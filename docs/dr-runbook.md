# ReciterAI disaster-recovery runbook (#223)

Operator-facing restore procedures for the ReciterAI persistence layer: the
`reciterai` DynamoDB table and the two S3 artifact buckets. Companion to
`infra/dynamodb_table.json`, `infra/s3_lifecycle_noncurrent.json`, and
`scripts/apply_backup_config.sh`. Style follows `docs/bucket-migration-runbook.md`.

## Why this runbook exists

The persistence layer had **no restore path** before #223. The hardening adds:

- **DynamoDB PITR** (35-day continuous restore) — the only protection against a
  bad pipeline run that `PutItem`-overwrites `TOPIC#`/`FACULTY#`/`SUBTOPIC_SCORE#`
  rows in place. S3 versioning does **not** help an in-place DynamoDB write.
- **DynamoDB deletion protection** — blocks an accidental `DeleteTable`.
- **S3 versioning + a 90-day noncurrent-version lifecycle** on both buckets — makes
  every `latest/*` pointer overwrite byte-recoverable, without letting dead
  versions accumulate forever.

This runbook turns those controls into concrete restore playbooks.

## Pre-conditions (verify before you need them)

```bash
scripts/apply_backup_config.sh --verify
```

Expect: PITR `ENABLED`, `DeletionProtectionEnabled True`, versioning `Enabled` on
both buckets, lifecycle rule `expire-noncurrent-90d` present on both. You also
need AWS credentials with `dynamodb:RestoreTableToPointInTime`,
`dynamodb:DescribeTable/UpdateTable/UpdateContinuousBackups`, and S3
read/write/`ListBucketVersions` on both buckets.

Capture a current inventory first so you can confirm the restore is structurally
complete:

```bash
aws dynamodb describe-table --table-name reciterai --region us-east-1 \
  --query 'Table.{Items:ItemCount,GSIs:GlobalSecondaryIndexes[].IndexName,Size:TableSizeBytes}'
```

---

## Scenario A — DynamoDB restore (PITR restore-to-new-table + swap)

Use when a bad run corrupted/over-deleted rows and you need to roll the whole
table back to a known-good instant within the last 35 days.

**A1 — pick the restore point.** Use the last-known-good timestamp (UTC, ISO-8601),
or `--use-latest-restorable-time` for "as fresh as possible."

**A2 — restore to a NEW table** (PITR can only restore to a new name):

```bash
aws dynamodb restore-table-to-point-in-time \
  --source-table-name reciterai \
  --target-table-name reciterai-restore-<YYYYMMDDhhmm> \
  --restore-date-time <ISO-8601-UTC> \
  --region us-east-1
# or: --use-latest-restorable-time   (instead of --restore-date-time)
```

**A3 — wait ACTIVE and spot-check.** Restore takes minutes-to-hours by size.

```bash
aws dynamodb describe-table --table-name reciterai-restore-<ts> --region us-east-1 \
  --query 'Table.{Status:TableStatus,Items:ItemCount,GSIs:GlobalSecondaryIndexes[].{N:IndexName,S:IndexStatus}}'
```

Confirm item count is in the expected range (~158k as of 2026-06) and all three
GSIs (`FacultyIndex`, `ProcessingByVersionIndex`, `PmidIndex`) are `ACTIVE`.

**A4 — atomic swap. There is no DynamoDB rename.** Two paths; **prefer the first.**

- **Primary — code-repoint (no destructive delete, no deletion-protection toggle):**
  1. Stop all writers first: disable the enrichment / hot / spotlight / onboarding
     crons (`scripts/deploy_cron.sh` rules, or disable in the EventBridge console) so
     nothing writes mid-swap.
  2. Point the app at the restore table by setting `TABLE_NAME`
     (`utils/dynamodb_helpers.py`) — via the `RECITERAI_TABLE_NAME` override if
     present, else a one-line constant change — and redeploy the Lambdas / Fargate
     task. **Coordinate with SPS**, which reads the same table.
  3. Smoke-test reads/writes against the restore table, then re-enable crons.

- **Fallback — delete-and-recreate the canonical name** (only if repointing consumers
  is infeasible). Requires toggling deletion protection OFF for exactly one delete:
  ```bash
  aws dynamodb update-table --table-name reciterai --no-deletion-protection-enabled --region us-east-1
  aws dynamodb delete-table --table-name reciterai --region us-east-1   # destructive
  # recreate from IaC, then copy data from the restore table back into `reciterai`
  ```
  Re-enable deletion protection immediately after recreate (step A5).

**A5 — re-harden the swapped-in table.** A PITR-restored table comes back with
**PITR OFF** and typically without deletion protection or tags. Re-run:

```bash
scripts/apply_backup_config.sh        # re-enables PITR + deletion protection (+ verifies)
```

Do not skip this — forgetting it silently re-enters the #223 unprotected state.

**A6 — re-enable crons and smoke.** Confirm a hot/enrichment tick writes cleanly,
then delete the now-unused restore table (if you used the code-repoint path and
have since copied forward, or are confident it is no longer the live table).

---

## Scenario B — hierarchy artifact rollback (re-point `latest/manifest.json`)

Use when a bad hierarchy publish made `latest/` point at a wrong version. Inverts
the forward write sequence in `pipeline_hierarchy/publish.py:309-359` (the version
copy is uploaded under `{version}/` and `latest/manifest.json` is overwritten LAST
with `Cache-Control: max-age=60, must-revalidate`).

**B1 — confirm the live key layout, then list prior versions.** Do not hard-code a
prefix; the bucket migration moved hierarchy under a `hierarchy/` prefix.

```bash
aws s3 ls s3://wcmc-reciterai-artifacts/hierarchy/
```

**B2 — inspect the target version's manifest** (sha256 you intend to restore to):

```bash
aws s3 cp s3://wcmc-reciterai-artifacts/hierarchy/<version>/manifest.json - | shasum -a 256
```

**B3 — re-point `latest/manifest.json`** to the prior version, preserving the
Cache-Control header the forward path uses:

```bash
aws s3 cp \
  s3://wcmc-reciterai-artifacts/hierarchy/<version>/manifest.json \
  s3://wcmc-reciterai-artifacts/hierarchy/latest/manifest.json \
  --cache-control "max-age=60, must-revalidate" --content-type application/json
```

For a full rollback also re-point the sibling `latest/` objects
(`hierarchy.json`, `hierarchy.schema.json`, and `aliases.json`/`membership.json`
if present) from the same `<version>/` prefix.

**B4 — verify.** `latest/manifest.json` sha256 must equal the target's. SPS polls
the manifest on its own cadence; the change is picked up on the next poll.

---

## Scenario C — spotlight rollback (re-push a prior `runs/{run_id}/` archive)

Use when a bad spotlight publish shipped wrong cards. Inverts
`spotlight/publish.py:196-223`: each run writes `spotlight/{version}/`,
overwrites `spotlight/latest/`, and **best-effort** archives an immutable
`spotlight/runs/{run_id}/` copy.

**C1 — list available run archives:**

```bash
aws s3 ls s3://wcmc-reciterai-artifacts/spotlight/runs/
```

**C2 — confirm the chosen archive is complete.** The `runs/` write is best-effort
(it only warns on failure), so a run may have **no** archive. It must contain all
three keys — `spotlight.json`, `spotlight.schema.json`, `manifest.json`:

```bash
aws s3 ls s3://wcmc-reciterai-artifacts/spotlight/runs/<run_id>/
```

If incomplete, fall back to a prior `spotlight/{version}/` prefix instead.

**C3 — re-push over `latest/`:**

```bash
for f in spotlight.json spotlight.schema.json manifest.json; do
  aws s3 cp \
    s3://wcmc-reciterai-artifacts/spotlight/runs/<run_id>/$f \
    s3://wcmc-reciterai-artifacts/spotlight/latest/$f \
    --content-type application/json
done
```

**C4 — verify.** Confirm `spotlight/latest/manifest.json` matches the chosen run;
SPS consumes `spotlight/latest/`.

---

## S3 version-level recovery (when a `latest/*` object itself was overwritten)

If versioning was on at the time, the prior bytes survive as a noncurrent version
(until the 90-day lifecycle expires them). Recover by VersionId:

```bash
aws s3api list-object-versions --bucket wcmc-reciterai-artifacts \
  --prefix spotlight/latest/spotlight.json \
  --query 'Versions[].{VersionId:VersionId,LastModified:LastModified,IsLatest:IsLatest}'

aws s3api copy-object --bucket wcmc-reciterai-artifacts \
  --copy-source "wcmc-reciterai-artifacts/spotlight/latest/spotlight.json?versionId=<PRIOR_VERSION_ID>" \
  --key spotlight/latest/spotlight.json
```

---

## Post-restore verification checklist

- `scripts/apply_backup_config.sh --verify` is all-green (PITR, deletion
  protection, versioning, lifecycle).
- DynamoDB item count + all 3 GSIs match the pre-incident inventory (A3).
- `latest/manifest.json` sha256 matches the intended version (B4 / C4).
- A fresh hot/enrichment tick writes without error and SPS renders the rolled-back
  artifacts.

## DR drill (run BEFORE you need it)

- **DynamoDB:** `restore-table-to-point-in-time` into a throwaway
  `reciterai-drtest-<ts>`, assert item count + 3 GSIs, then delete the drill table.
- **S3:** overwrite a scratch key in a versioned bucket and recover the prior bytes
  via `list-object-versions` + `copy-object`; `cp` a prior `hierarchy/<version>/manifest.json`
  over a scratch `latest/manifest.json` and diff the sha256.

## Cross-references

- `infra/dynamodb_table.json` — table rebuild spec + durability invariant.
- `infra/s3_lifecycle_noncurrent.json` — the 90-day noncurrent-version lifecycle.
- `scripts/apply_backup_config.sh` — applies/verifies every control above.
- `pipeline_hierarchy/publish.py:309-359` — the hierarchy write sequence Scenario B inverts.
- `spotlight/publish.py:196-223` — the spotlight write sequence Scenario C inverts.
- `docs/bucket-migration-runbook.md` — bucket layout / migration context.

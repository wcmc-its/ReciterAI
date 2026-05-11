# Bucket Migration Runbook: `wcmc-reciterai-hierarchy` -> `wcmc-reciterai-artifacts`

## Why this runbook exists

Phase 5 published `hierarchy.json` to `s3://wcmc-reciterai-hierarchy/`. Phase 6
introduces a second artifact (`spotlight.json`) co-located with the hierarchy,
and the bucket name is renamed to `wcmc-reciterai-artifacts` so it correctly
describes both artifact families. The Scholars @ WCM (SPS) ETL Lambda is the
sole external consumer today; cutover requires a coordinated dual-publish
window so SPS is never reading from a retired bucket. This document is the
operator-facing runbook executed manually before Plan 06-07's first
`--publish` against the new bucket.

The runbook mirrors Phase 5's D-09 30-day deprecation pattern (referenced in
`docs/hierarchy-contract.md` Breaking-Change Policy). It is executed once,
out-of-band, by the operator running shell commands against the WCM AWS
account.

## Pre-conditions

- `docs/aws-bucket-policy-artifacts.json` and
  `docs/aws-iam-pipeline-policy-artifacts.json` exist in this repo (Task 2 of
  Plan 06-01).
- `utils/s3_client.py` exports `ARTIFACTS_BUCKET = "wcmc-reciterai-artifacts"`
  alongside the existing `HIERARCHY_BUCKET` (Task 1 of Plan 06-01).
- The operator has AWS credentials (env vars exported by their shell profile)
  with `s3:CreateBucket`, `s3:PutBucketPolicy`, `s3:PutObject` and IAM-attach
  permissions in the WCM AWS account.
- The SPS coding-agent is reachable for cutover coordination -- the
  cross-repo handoff happens in Plan 06-07 via
  `docs/sps-spotlight-handoff.md`.
- A current-state inventory of `s3://wcmc-reciterai-hierarchy/` has been
  captured (`aws s3 ls s3://wcmc-reciterai-hierarchy/ --recursive`) so the
  post-sync diff can be verified.

## Step 1: Create the new bucket and apply policies

```bash
# Create the bucket. us-east-1 matches HIERARCHY_REGION in utils/s3_client.py.
aws s3 mb s3://wcmc-reciterai-artifacts --region us-east-1

# Apply the SPS-reader bucket policy. The template is checked into this
# repo; replace the <SPS_LAMBDA_ROLE_ARN> placeholder with the production
# SPS Lambda execution role ARN before applying.
aws s3api put-bucket-policy --bucket wcmc-reciterai-artifacts \
  --policy file://docs/aws-bucket-policy-artifacts.json

# The pipeline writer policy
# (docs/aws-iam-pipeline-policy-artifacts.json) is attached to the
# operator's IAM user/role via the AWS console (IAM > Roles > Add policy).
```

**Idempotency caveat:** S3 bucket names are globally unique. If
`aws s3 mb` returns `BucketAlreadyExists`, the name is taken in another AWS
account -- **ABORT** and coordinate with WCM IT before proceeding. Do not
silently re-name the bucket; downstream code references
`ARTIFACTS_BUCKET = "wcmc-reciterai-artifacts"` as the single source of
truth.

## Step 2: Sync existing hierarchy artifacts under the new prefix

The new bucket reorganizes artifacts under disjoint top-level prefixes:
`hierarchy/` for Phase 5 outputs, `spotlight/` for Phase 6 outputs. This
prevents prefix collisions when both pipelines publish concurrently.

```bash
# Mirror the full hierarchy artifact tree under the hierarchy/ prefix.
# Existing keys like v2026-05-06/hierarchy.json land at
# s3://wcmc-reciterai-artifacts/hierarchy/v2026-05-06/hierarchy.json.
aws s3 sync s3://wcmc-reciterai-hierarchy/ \
            s3://wcmc-reciterai-artifacts/hierarchy/

# Verify byte-equivalence with a recursive listing diff.
aws s3 ls s3://wcmc-reciterai-hierarchy/ --recursive > /tmp/old.txt
aws s3 ls s3://wcmc-reciterai-artifacts/hierarchy/ --recursive > /tmp/new.txt
# old.txt keys appear in new.txt with `hierarchy/` prefix; sizes match.
```

## Step 3: Re-publish hierarchy with updated `$id`

`docs/hierarchy.schema.json` `$id` currently embeds
`wcmc-reciterai-hierarchy.s3.amazonaws.com` (verified at
`docs/hierarchy.schema.json:3`). On bucket migration this `$id` becomes
stale. Per Phase 5 semver rules a bucket-name change is a PATCH bump (no
shape change); re-publish with the updated `$id` so SPS sees a manifest
`schema_version` increment.

**v1 path (recommended -- minimal code change):**

1. Branch off `main`.
2. Edit `utils/s3_client.py`: temporarily change the **default** of
   `S3HierarchyClient.__init__(bucket=HIERARCHY_BUCKET, ...)` to
   `bucket=ARTIFACTS_BUCKET` -- OR run `backfill_all.py --publish` from a
   shell with `HIERARCHY_BUCKET="wcmc-reciterai-artifacts"` exported (if the
   client honors env-var overrides). The choice is operator preference;
   either way, the change is throwaway and reverted in step 5.
3. Edit `docs/hierarchy.schema.json` `$id` to point at
   `wcmc-reciterai-artifacts.s3.amazonaws.com` and bump
   `$defs._meta.schema_version` PATCH.
4. Run `python3 backfill_all.py --publish`. Confirm three artifacts land in
   `s3://wcmc-reciterai-artifacts/hierarchy/v{ISO-date}/{hierarchy.json,
   hierarchy.schema.json, manifest.json}` plus the `hierarchy/latest/`
   overwrite.
5. Verify `manifest.sha256` and `schema_version` against the published file.
6. Revert the temporary `utils/s3_client.py` change. Commit only the
   `docs/hierarchy.schema.json` `$id` + version bump and the new
   `manifest.json` artifacts (if those are tracked).

**v2 path (deferred enhancement):** add a `--target-bucket` CLI flag to
`backfill_all.py` so the publish target is parameterizable without source
edits. Tracked as a Phase 7 cleanup; not required for the v1 migration.

## Step 4: Cross-repo handoff to SPS

Plan 06-07 generates `docs/sps-spotlight-handoff.md`. That handoff
includes the cutover instruction the SPS coding-agent executes:

> **SPS cutover step (from sps-spotlight-handoff.md):**
> Update the ETL Lambda's `BUCKET` environment variable from
> `wcmc-reciterai-hierarchy` to `wcmc-reciterai-artifacts`. Update fetch
> paths from `latest/manifest.json` to `hierarchy/latest/manifest.json`
> (and equivalent for the schema + hierarchy keys). Re-deploy the Lambda.
> Confirm the next scheduled fetch reads from the new bucket.

This runbook waits on SPS confirmation before Step 5.

## Step 5: Verify cutover

After SPS has redeployed:

- SPS coding-agent confirms the ETL fetch reads from the new bucket and
  renders correct subtopic counts in the Scholars surface.
- Operator inspects CloudWatch S3 metrics:
  `GetRequests` on `wcmc-reciterai-artifacts` is positive and
  `GetRequests` on `wcmc-reciterai-hierarchy` trends to zero over the
  next polling cycle (SPS poll interval is weekly per
  `docs/hierarchy-contract.md`).
- The first `spotlight.json` from Plan 06-07 publishes to
  `s3://wcmc-reciterai-artifacts/spotlight/v{ISO-date}/spotlight.json`.

If any of the above fails, follow the Rollback procedure below.

## Step 6: 30-day deprecation window

Mirror Phase 5 D-09. After SPS confirms the cutover:

- The old bucket `s3://wcmc-reciterai-hierarchy/` stays live but receives
  **no new writes**. (The pipeline writer IAM role still has access -- do
  not strip it; rollback depends on it.)
- Wait **30 calendar days** post-cutover-confirmation.
- After the 30-day window, retire the old bucket:
  ```bash
  # WARNING: destructive. Run only after the 30-day window AND a final
  # confirmation that no consumer references the old bucket.
  aws s3 rb s3://wcmc-reciterai-hierarchy --force
  ```

The 30-day delay is operator discipline -- it is **NOT** code-enforced.
Threat T-06-01-05 explicitly accepts this risk in Plan 06-01's threat
register; the runbook is the mitigation.

## Rollback procedure

If SPS reports problems after cutover:

1. SPS reverts its `BUCKET` env var back to `wcmc-reciterai-hierarchy`
   and re-deploys. The old bucket is still live (untouched in Step 6) and
   continues serving Phase 5 artifacts.
2. The pipeline operator stops further writes to
   `wcmc-reciterai-artifacts/hierarchy/` until the root cause is
   diagnosed.
3. The old bucket **MUST NOT** be deleted before SPS confirms a successful
   roll-forward. Step 6's 30-day window exists specifically to keep
   rollback symmetric and cheap.
4. Phase 6 spotlight publishes can proceed against
   `wcmc-reciterai-artifacts/spotlight/` independently of the rollback --
   `spotlight/` and `hierarchy/` prefixes are disjoint, so SPS can fetch
   hierarchy from the old bucket while the new bucket continues to host
   the spotlight artifact.

## Cross-references

- `.planning/phases/06-spotlight-pipeline/06-PATTERNS.md` Migration runbook
  section (lines 718-727) -- the 6-step lift this doc expands.
- `.planning/phases/06-spotlight-pipeline/06-RESEARCH.md` Bucket-migration
  risk surface (lines 582-585) and Pitfall 7 SPS ETL caches the OLD bucket
  name during migration (lines 904-913).
- `docs/hierarchy-contract.md` Breaking-Change Policy -- the 30-day
  deprecation window pattern this runbook mirrors.
- `docs/aws-bucket-policy-artifacts.json` and
  `docs/aws-iam-pipeline-policy-artifacts.json` -- the policy templates
  applied in Step 1.
- `docs/sps-spotlight-handoff.md` (generated by Plan 06-07) -- the
  cross-repo handoff that closes the cutover loop.
- `.planning/phases/05-hierarchy-publishing-contract/05-CONTEXT.md` D-09 --
  the Phase 5 origin of the 30-day deprecation pattern.


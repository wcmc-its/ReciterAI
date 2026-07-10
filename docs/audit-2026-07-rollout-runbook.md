# Audit 2026-07 rollout runbook

Operator procedure to make the merged audit fixes live. Run top-to-bottom.
Every step is idempotent or gated; nothing here is destructive except step 4,
which is isolated and read-first.

**Context.** Handoff §2.1 (`docs/audit-2026-07-handoff.md`) listed four rollout
steps written when only #301–306 had merged. Since then **#318–#323 also
merged** (audit issues #308–#312). They ride the same deploy surfaces, so the
one deploy pass below carries them too — this runbook supersedes §2.1's step
ordering.

## Ordering (why)

```
1. Deploy the merged code         ← must precede everything; the fixes aren't live until deployed
2. Run the spotlight history fold ← AFTER deploy, or the old code re-fragments partitions on next publish
3. Verify the spotlight cron      ← confirm it skips for the RIGHT reason now (#303 needs created_at)
4. Delete 98 junk STAGE# rows     ← independent; run anytime; destructive, so gated + read-first
```

---

## 1. Deploy the merged code (three surfaces)

All from the merged `main` commit. `config` and `requirements.txt` are
image-baked, so the intended state must be in what you deploy.

**Which merged PR lands on which surface:**

| Surface | Deploy with | Carries |
|---|---|---|
| **Docker image** (Fargate `reciterai-enrichment` + `reciterai-cold` tasks) | ECR build+push (infra/README.md §"Build + push the image", `--platform linux/amd64`) | #301 numpy/requests; and every fix that runs in the cold-run / enrichment pipelines: #319, #321, #322, #323, #320/#311 |
| **Lambda zips** (hot per-stage + `spotlight-orchestrator` + `drift-evaluator`) | `scripts/build_lambda_zips.sh` then `aws lambda update-function-code` per function | #319 (assign/score), #318 code half (`pipeline_hot/orchestrator.py` + `pipeline_enrichment/alerting.py`), #320/#311 (spotlight orchestrator) |
| **Step Functions** (`reciterai-hot-path` + `reciterai-onboarding`) | `scripts/deploy_state_machine.sh` and `scripts/deploy_onboarding_state_machine.sh` | #318 ASL Retry/Catch on every `dynamodb:putItem` state |

Each has a `--dry-run`. Run the dry-run first on all three.

> The exact ECR/ARN/env-var values are account-specific and already env-driven
> by the scripts — follow the per-surface runbooks (infra/README.md,
> docs/daily-enrichment.md §"Deploying the enrichment job"), don't hand-edit ARNs.

**Verify the image actually carries numpy/requests** (the #301 gap that only
showed up in the container):

```bash
docker run --rm --entrypoint python "$IMAGE_URI" -c "import numpy, requests; print('deps ok')"
```

---

## 2. Run the spotlight history fold (once, after deploy)

`scripts/fold_spotlight_history_versions.py` (#304) collapses the per-publish
`SPOTLIGHT_HISTORY#{date}#{subtopic}` partitions onto the durable
`SPOTLIGHT_HISTORY#{subtopic}` key so rotation decay starts from real history
instead of a permanent cold start. **Not yet run.** Must run after the fixed
code is deployed (step 1), or the old writer re-fragments on its next publish.

```bash
export RECITERAI_TABLE=reciterai AWS_REGION=us-east-1   # defaults; set explicitly

scripts/fold_spotlight_history_versions.py --dry-run
# Expected (from the handoff snapshot): 159 rows, 91 subtopics_to_fold,
# 38 recovered_repeat_features, 0 malformed.
```

- `malformed` **must be `[]`**. Any entry means an unexpected PK shape — stop and
  inspect; the script exits 1 and folds nothing further. (It flags empty /
  version-with-no-ident PKs; it does not treat an ordinary `#`-containing ident
  as malformed, but no such PK exists in prod.)
- If the dry-run numbers differ materially from 159/91/38/0, the table changed
  since the snapshot — eyeball the plan before proceeding, don't assume.

The script's fold logic (SUM `shown_count`, MAX `last_shown_at` → decay anchor,
two-phase crash-safe put-then-delete, idempotent re-run) was verified offline
against the "every row `shown_count==1`" fingerprint. Re-running after a real
fold is a no-op.

```bash
scripts/fold_spotlight_history_versions.py            # real: put folded rows, then delete sources
```

Then run the durable-id re-key **after** the fold (the fold guarantees one row
per subtopic, which its 1:1 collision guard assumes):

```bash
scripts/migrate_spotlight_history_pk.py --dry-run     # then without --dry-run
```

---

## 3. Verify the spotlight cron fires for the right reason

`reciterai-spotlight-monthly` (EventBridge, 1st of month 13:00 UTC → Lambda
`reciterai-spotlight-orchestrator`). #303 made the dirty gate able to see
`TOPIC#` rows, but only rows written *after* deploy carry `created_at`. Until
enough new activity lands, the gate correctly reports nothing to do.

On the next scheduled run (or a manual invoke), confirm it skips because the
in-window pub count is genuinely below threshold — **not** because it can't see
any rows. Check the Lambda log for the dirty-gate evaluation line (in-window
distinct-publication counts per subtopic), not a zero-rows-scanned line.

```bash
aws logs tail /aws/lambda/reciterai-spotlight-orchestrator --since 1h --follow
```

Note: #311 changed the gate to count **distinct publications**, not activity
rows — so the threshold now trips on real change, not co-author fan-out. That's
the expected new behavior.

---

## 4. Delete 98 junk `STAGE#` rows (destructive — gated, read-first)

Written by a test that called `run(dry_run=False)` without patching `get_table`
(fixed in #301). Inert — the skip cache keys on `input_hash`, and #312's new
scans filter by `run_id` (which these lack), so they affect no live path — but
they pollute the `STAGE#assign_subtopics#topic:cardiovascular_disease` partition
alongside the one genuine row.

**The `reciterai` table has PITR (35-day continuous restore)** — recovery exists
if a delete goes wrong. Still, read first:

```bash
# READ: confirm the shape before deleting anything.
aws dynamodb query --table-name reciterai --region us-east-1 \
  --key-condition-expression 'PK = :pk' \
  --expression-attribute-values '{":pk":{"S":"STAGE#assign_subtopics#topic:cardiovascular_disease"}}' \
  --projection-expression 'SK, records_written, completed_at' \
  --output json | python3 -c '
import json,sys
items=json.load(sys.stdin)["Items"]
junk=[i for i in items if i.get("records_written",{}).get("N")=="1"]
real=[i for i in items if i.get("records_written",{}).get("N")!="1"]
print(f"total={len(items)}  junk(records_written==1)={len(junk)}  keep={len(real)}")
for i in real: print("  KEEP:", i["SK"]["S"], "rw=", i.get("records_written",{}).get("N"))
'
# Expect: total=99  junk=98  keep=1  (the KEEP row is records_written=816).
```

Only if that prints `junk=98 keep=1`, delete the 98:

```bash
aws dynamodb query --table-name reciterai --region us-east-1 \
  --key-condition-expression 'PK = :pk' \
  --filter-expression 'records_written = :one' \
  --expression-attribute-values '{":pk":{"S":"STAGE#assign_subtopics#topic:cardiovascular_disease"},":one":{"N":"1"}}' \
  --projection-expression 'PK, SK' --output json \
| python3 -c '
import json,sys,subprocess
for it in json.load(sys.stdin)["Items"]:
    key=json.dumps({"PK":it["PK"],"SK":it["SK"]})
    subprocess.run(["aws","dynamodb","delete-item","--table-name","reciterai",
                    "--region","us-east-1","--key",key], check=True)
print("deleted junk rows")
'
```

Re-run the READ query; expect `total=1 keep=1`.

---

## 5. Post-rollout spot-checks (confirm the fixes are live)

- **#318:** `aws stepfunctions describe-state-machine --state-machine-arn …:reciterai-hot-path`
  → the definition's `dynamodb:putItem` states now carry `Retry`/`Catch`.
- **#309 alerts:** trigger nothing; just confirm the next real sweep alert lands
  in **Teams**, not the dead Slack path.
- **#312 invariant:** on the next cold-run, a topic with live rows but zero
  subtopic partitions now hard-fails (non-zero exit) instead of shipping an empty
  rollup — **confirm the cold-run orchestrator tolerates a non-zero topic exit**
  before the next scheduled run, or a still-unremediated topic (e.g.
  `aging_geroscience`) will block it.
- **#310:** a fully-failed assign/score batch now writes a `failed` STAGE# row
  (not `complete`), so `--resume` re-runs it. The drift evaluator will now WARN
  on nights with sporadic single-PMID failures — expected, not a regression.

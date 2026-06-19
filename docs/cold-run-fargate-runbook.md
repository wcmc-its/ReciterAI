# Cold-run on Fargate — operator runbook

The taxonomy **cold-run** (`python -m pipeline_cold.run`) is the full-corpus rebuild:
10 sequential stages (`score → assign → discover[no-op] → relabel → top_topic →
count → rollup → feedback_sweep → backfill_spotlight[--publish] → publish_hierarchy`,
`pipeline_cold/run.py`) ending in the two prod publishes — `spotlight.json` →
`wcmc-reciterai-artifacts` and `hierarchy.json` → `wcmc-reciterai-hierarchy`.

It is **on-demand, operator-triggered, annual cadence** — there is deliberately **no
EventBridge schedule** (it's ~$210 in Bedrock and overwrites the live hierarchy +
spotlight that SPS serves). This is the procedure to run it on AWS Fargate (the #240
launch path). Run it top-to-bottom; every account-specific value is *discovered* here
(nothing account-specific is committed — same convention as `infra/README.md`).

> **First use — the #204 flag flip.** The skip-relabel + skip-lede +
> propagate-durable-ids flags (PR #242 / issue #204) are flag-off in prod and must be
> validated on a real cold-run. See **Step 1**.

---

## When to run
- The annual taxonomy rebuild (cadence: #37 / #191), or any time a full re-cluster +
  republish is genuinely needed (rare).
- **Not** for incremental updates — the weekly hot path + monthly spotlight handle those.

## One-time infra (already provisioned — verify, don't recreate)
| Resource | Verify | If missing |
|---|---|---|
| IAM role `reciterai-cold-task` | `aws iam get-role --role-name reciterai-cold-task` | create from `infra/cold_run_task_iam_policy.json` (strip `$schema_note`) — see `infra/README.md` "Cold-run launch path" |
| Log group `/ecs/reciterai-cold` | `aws logs describe-log-groups --log-group-name-prefix /ecs/reciterai-cold` | `aws logs create-log-group --log-group-name /ecs/reciterai-cold` (must pre-exist — `ecsTaskExecutionRole` lacks `logs:CreateLogGroup`) |
| Task def template | `infra/cold_run_task_definition.json` (committed) | — |
| Launcher | `scripts/run_cold_run.sh` (committed) | — |

---

## Step 1 — decide the flag state (config is image-baked)
`config/thresholds.json` is baked into the image, so **the image you build determines the
flag state** — there is no runtime toggle.
- **#204 rollout:** merge PR #242 to `main` first (3 flags ON) and build off `main`; **or**
  re-flip the three flags on a branch and build off it. Flags:
  `subtopic_reconcile_skip_relabel_enabled`, `subtopic_reconcile_skip_lede_enabled`,
  `propagate_durable_ids` (leave `subtopic_reconcile_skip_relabel_overlap_min` at 0.70).
- **Otherwise:** build off current `main`.

## Step 2 — build + push the image (`linux/amd64`)
```bash
ACCT=$(aws sts get-caller-identity --query Account --output text)
ECR=$ACCT.dkr.ecr.us-east-1.amazonaws.com/reciterai-enrichment
TAG=cold-$(git rev-parse --short HEAD)
git checkout <the commit with the intended flag state>
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin "$ACCT.dkr.ecr.us-east-1.amazonaws.com"
docker build --platform linux/amd64 -t "$ECR:$TAG" .   # X86_64 task — a Mac defaults to arm64, which won't run on Fargate
docker push "$ECR:$TAG"
```
The cold-run shares the Dockerfile + ECR repo with the enrichment task but is generally a
**different tag** (different flag state). `out/` is excluded from the build (`.dockerignore`).

## Step 3 — register the task def
Discover the account-specific values and substitute the `{…}` placeholders in
`infra/cold_run_task_definition.json` (strip `$schema_note` + `deploy_notes` — ECS rejects
unknown keys; set `image` to `$ECR:$TAG`):
```bash
# Reuse the enrichment task's exec role + secret ARNs:
aws ecs describe-task-definition --task-definition reciterai-enrichment \
  --query 'taskDefinition.{exec:executionRoleArn,secrets:containerDefinitions[0].secrets}'
# Discover the VPC layout (same as enrichment):
aws events list-targets-by-rule --rule reciterai-enrichment-daily \
  --query 'Targets[0].EcsParameters.NetworkConfiguration.awsvpcConfiguration'
aws ecs register-task-definition --cli-input-json file:///tmp/reciterai-cold-taskdef.json
```
Cold-run task role = `reciterai-cold-task` (NOT the enrichment task role — it has no S3
grant). Secrets: DB + OpenAI + Bedrock bearer (no Teams).

## Step 4 — dry-run gate (safe, no publish, zero Bedrock cost)
```bash
AWS_REGION=us-east-1 ECS_CLUSTER_NAME=reciterai-cluster TASK_DEFINITION=reciterai-cold \
RECITERAI_COLD_SUBNETS=<from step 3> RECITERAI_COLD_SECURITY_GROUPS=<from step 3> \
COLD_MODE=dry-run scripts/run_cold_run.sh
```
Launches a Fargate task that runs `pipeline_cold.run --dry-run` (prints the stage plan,
exits 0). Proves the image pulls and the task def / role / secrets / logging are wired.
**Gate on exitCode 0 before the real run.**

## Step 5 — the real run ⚠️ ~$210, multi-hour, OVERWRITES PROD
```bash
... COLD_MODE=run scripts/run_cold_run.sh        # same env as Step 4
aws logs tail /ecs/reciterai-cold --follow        # monitor (the launcher does NOT block)
```
- **Overwrites** the live `hierarchy latest/` + `spotlight/latest/` that SPS serves.
- **First-Fargate caveat:** a fresh container starts with a *cold* `.planning/` (no warm
  workstation draft state); the `assign`/`relabel` stages regenerate the drafts. Watch them —
  `publish_hierarchy` refuses an incomplete hierarchy, so a failure there means the draft
  stages didn't fully rebuild. Watch the **first** real run closely.
- Expect a `STAGE#` row per stage in DynamoDB (`reciterai`).

## Step 6 — validate
```bash
PYTHONPATH=. python3 scripts/validate_durable_id_propagation.py   # exit 0; coverage > 0 on 3 surfaces; 0 mismatches
```
- Inspect the cold logs for the **relabel-skip / lede-skip** decisions — the skip set + the $
  saved (relabel is the ~$108 dominant line; a 0.70-overlap skip carries no quality risk).
- Spot-check the published `hierarchy.json` / `spotlight.json`.

## Step 7 — post-run
- **Merge PR #242** if it was the validation vehicle.
- Prompt **SPS** to build its `SubtopicAlias` table + repoint joins to durable IDs (their
  repo, default branch `master`) — durable_id is now stamped on the artifacts.

## Rollback
Flags are additive and gate-off byte-identical. To revert: build an image with the flags
OFF (or off the pre-flip `main`) and re-register; the next run reverts. `durable_id` is
additive and SPS-ignored — nothing to un-migrate.

## Disarm between runs (recommended)
A registered task def pins a specific image tag; a year later that's stale code. After a
run, deregister the task-def revision(s) and delete the image tag so nothing year-stale can
run; keep the role + log group (permanent). Re-register + rebuild next time.
```bash
aws ecs deregister-task-definition --task-definition reciterai-cold:<rev>
aws ecr batch-delete-image --repository-name reciterai-enrichment --image-ids imageTag=$TAG
```

## Gotchas (carry forward)
- **Never `--publish` the hierarchy from a local checkout** (Gotcha A — local `input_hash`
  ≠ live → overwrites prod `latest/` with stale data). Run on Fargate.
- **One container / one task** — stages hand off via local files (`assign` writes
  `hierarchy_augmented_*.json`; `publish_hierarchy` reads them); can't split across tasks.
- **Image-baked flags** — flipping a flag = rebuild + re-register.
- **Pre-create the log group** (exec role lacks `logs:CreateLogGroup`).
- **`linux/amd64`** — the task is X86_64.

## See also
- `infra/README.md` → "Cold-run launch path" — the file-level deploy reference (IAM policy,
  task-def template, one-time deploy).
- `docs/hot-cold-paths.md` — hot/cold/spotlight/drift/onboarding/enrichment invocation overview.
- Issues #204 (flag rollout) · #191 (durable IDs) · #240 (this launch path) · #37 (cadence).

# ReciterAI infrastructure (single-file IaC, D-10)

This directory is Phase 10's IaC v1. It is deliberately simple: a JSON
file of EventBridge rules + targets, JSON files of the minimum-
privilege IAM policies (one for Lambda execution roles, one for the
Fargate enrichment task role), and a thin bash deploy wrapper at
`scripts/deploy_cron.sh`. No Terraform, no CDK, no per-environment
overlays.

## Files

- **`eventbridge.json`** — five cron rules and their targets:
  - `reciterai-hot-weekly` → Step Functions state machine `reciterai-hot-path` (Mondays 12:00 UTC).
  - `reciterai-spotlight-monthly` → Lambda `reciterai-spotlight-orchestrator` (1st of month, 13:00 UTC).
  - `reciterai-drift-daily` → Lambda `reciterai-drift-evaluator` (daily 14:00 UTC).
  - `reciterai-onboarding-detector-daily` → Lambda `reciterai-onboarding-detector` (daily 13:00 UTC; #80 Phase 2).
  - `reciterai-enrichment-daily` → ECS Fargate `RunTask` on task definition `reciterai-enrichment` (daily 11:00 UTC; #37 PR 4). The first ECS target — adds a new compute substrate alongside Lambda + Step Functions.
- **`lambda_iam_policy.json`** — minimum permissions for every ReciterAI Lambda execution role.
- **`enrichment_task_iam_policy.json`** (#37 PR 4; #137 added `DeleteItem` + `Scan` for the quarantine module) — minimum permissions for the Fargate enrichment task role (DDB `Get/Put/Update/DeleteItem` + `Query`/`Scan` + `BatchWriteItem` + `DescribeTable` on the `reciterai` table, Secrets Manager read on the 4 enrichment secrets, CloudWatch Logs write). No `bedrock:InvokeModel` — Bedrock authenticates via the `AWS_BEARER_TOKEN_BEDROCK` bearer token (plan D8), and dropping the IAM grant gives the task loud-failure mode on a missing/stale token.
- **`ecs_task_definition.json`** (#37 PR 4) — the Fargate task definition for the daily enrichment job. Templated placeholders (`{IMAGE_URI}`, `{TASK_ROLE_ARN}`, etc.) are substituted manually at deploy time; see `docs/daily-enrichment.md` §"Deploying the enrichment job" for the runbook.
- **`cold_run_task_definition.json`** (#240) — the Fargate task definition for the on-demand **taxonomy cold-run** (`python -m pipeline_cold.run`). Reuses the same image as the enrichment task (the repo's single Dockerfile already contains `pipeline_cold`); it only overrides the default CMD. Sized 1 vCPU / 4 GB. See "Cold-run launch path" below.
- **`cold_run_task_iam_policy.json`** (#240) — minimum-privilege policy for the cold-run task role: the enrichment policy's DynamoDB CRUD on `reciterai` + **a new S3 read/write statement** on `wcmc-reciterai-hierarchy/*` and `wcmc-reciterai-artifacts/spotlight/*` (the cold-run is the only ReciterAI task that publishes those artifacts), Secrets Manager read on the DB + OpenAI secrets, and CloudWatch Logs on `/ecs/reciterai-cold`. No `bedrock:InvokeModel` (bearer-token auth, same as enrichment).
- **`dynamodb_table.json`** (#223) — declarative spec for the `reciterai` table: KeySchema + 3 GSIs + `BillingMode`, byte-faithful to `utils/dynamodb_helpers.py` `create_chatbot_table` (enforced by `tests/test_infra_dynamodb_table_parity.py`), plus the durability fields the bare creator historically omitted — `DeletionProtectionEnabled` + `Tags` — and a `_pitr` note (PITR is enabled out-of-band, not a CreateTable attribute). The table's rebuild spec **and** the documented #223 invariant.
- **`s3_lifecycle_noncurrent.json`** (#223) — noncurrent-version expiration lifecycle (90 days) applied to both artifact buckets so superseded `latest/*` pointers don't accumulate unbounded once versioning is on.
- **`../scripts/apply_backup_config.sh`** (#223) — idempotent wrapper (mirrors `deploy_cron.sh`: `--dry-run`, per-control verify) that enables DDB PITR + deletion protection and S3 versioning + the lifecycle on both buckets. One-time apply + drift recheck (`--verify`), not a cron.
- **`../scripts/deploy_cron.sh`** — `aws events put-rule` + `aws events put-targets` + `aws lambda add-permission` per rule. Supports `--dry-run` and `--rule <name>`. ECS target kind (#37 PR 4) needs `RECITERAI_ENRICHMENT_SUBNETS` + `RECITERAI_ENRICHMENT_SECURITY_GROUPS` env vars (comma-separated).

## Operator quickstart

```bash
# Required:
export AWS_ACCOUNT_ID=123456789012
export AWS_REGION=us-east-1

# 1. Preview what would happen:
scripts/deploy_cron.sh --dry-run

# 2. Apply:
scripts/deploy_cron.sh

# 3. Verify:
aws events list-rules --region "$AWS_REGION" \
  --query 'Rules[?starts_with(Name, `reciterai-`)].[Name,ScheduleExpression,State]' \
  --output table
```

The Lambda functions and the Step Functions state machine are deployed
separately. This script wires the cron triggers; it does not create the
target resources. The hot-path Step Functions deployment is in
`scripts/deploy_state_machine.sh` (T14); the onboarding state machine
(`reciterai-onboarding`, #80 Phase 2) has its own near-clone
`scripts/deploy_onboarding_state_machine.sh`. Lambda function deployment
stays manual `aws lambda create-function` / `update-function-code` — there
is no `deploy_lambda.sh` (current practice until a function boundary
changes frequently enough to justify automation).

## Cold-run launch path (#240)

The **taxonomy cold-run** (`python -m pipeline_cold.run`) is the full-corpus
rebuild — 10 sequential stages (`score → assign → discover[no-op] → relabel →
top_topic → count → rollup → feedback_sweep → backfill_spotlight[--publish] →
publish_hierarchy`, `pipeline_cold/run.py:94-185`) ending in the two prod
publishes (spotlight → `wcmc-reciterai-artifacts`, hierarchy →
`wcmc-reciterai-hierarchy`). Until #240 it had **no AWS launch path** — it had
only ever run from an operator workstation. This is the Fargate home for it.

It is **on-demand and operator-gated** — there is deliberately **no EventBridge
schedule** (the cold-run is annual/infrequent and ~$210 in Bedrock per run). The
schedule is future infra (see #191 rollout `0011`, the "Brick F EventBridge"
step); when it lands it adds one rule here, like the others.

### One-time deploy
1. **IAM role** `reciterai-cold-task` with `cold_run_task_iam_policy.json` attached
   (a NEW role — the enrichment task role has no S3 grant and cannot be reused).
   The execution role (`ecsTaskExecutionRole`) is reused; extend its inline
   Secrets grant to `reciterai/bedrock-api-key` if it is not already there.
2. **Register the task def:** substitute the `{…}` placeholders in
   `cold_run_task_definition.json` (use the SAME ECR image as the enrichment task —
   built from the commit whose `config/thresholds.json` carries the intended flag
   state, since the flags are baked into the image) and
   `aws ecs register-task-definition --cli-input-json file://…`.

### Launch (use `scripts/run_cold_run.sh`)
```bash
export AWS_REGION=us-east-1 ECS_CLUSTER_NAME=reciterai-cluster
export RECITERAI_COLD_SUBNETS=subnet-...,subnet-...        # same VPC layout as enrichment;
export RECITERAI_COLD_SECURITY_GROUPS=sg-...                # falls back to RECITERAI_ENRICHMENT_* if unset

# 1. dry-run plumbing gate — prints the stage plan, exits 0, zero Bedrock cost:
COLD_MODE=dry-run scripts/run_cold_run.sh

# 2. the real multi-hour publishing run — launches, prints monitoring commands, does NOT block:
COLD_MODE=run scripts/run_cold_run.sh
```
Monitor: `aws logs tail /ecs/reciterai-cold --follow`. Validate after the task
reaches `STOPPED` with `exitCode 0`: STAGE# rows in `reciterai` per stage, and the
refreshed `hierarchy latest/manifest.json` + `spotlight/latest/`.

### Gotchas
- **This IS the prod path now.** The cold-run overwrites the `latest/` pointers
  SPS consumes. It exists precisely so flag-on validation (e.g. #204) rides a real
  Fargate run instead of a `--publish` from a local checkout (Gotcha A).
- **Flags are image-baked.** `config/thresholds.json` is read from the repo at
  `REPO_ROOT` — there is no runtime override — so the intended flag state must be in
  the image you deploy (rebuild + push to flip a flag).
- **One container / one task.** Stages hand off via local files on a shared
  filesystem (`assign` writes `hierarchy_augmented_*.json`; `publish_hierarchy`
  reads them), so the whole run is a single task — stages cannot be split.
- **First Fargate run starts with a cold `.planning/`.** The pipeline has only ever
  run on a workstation with warm local draft state; a fresh container regenerates
  the drafts from `taxonomy_v2.json` + DynamoDB during `assign`/`relabel`. Validate
  the first real run closely (it should produce a complete hierarchy with every
  subtopic carrying `display_name`/`short_description`, which `publish_hierarchy`
  enforces). This makes `--from-stage` resume best-effort until the intermediates
  are externalized.

## D-10 migration trigger — when to adopt CDK

The single-file approach is correct only while ReciterAI's AWS surface
is small. The migration threshold below is set in plan-phase and is
binding on future operators:

> **Adopt CDK when ReciterAI adds any one of:**
> - **(a)** a second Step Function (e.g., a separate cold-path or
>   review-cycle state machine),
> - **(b)** a second cron rule beyond the three defined here, or
> - **(c)** a fifth managed AWS resource (Lambdas + Step Functions +
>   tables + buckets, excluding shared infra like CloudWatch log groups).
>
> Today ReciterAI manages: `reciterai` (DynamoDB),
> `wcmc-reciterai-hierarchy` (S3), `wcmc-reciterai-artifacts` (S3),
> `reciterai-hot-path` (Step Functions), plus Lambda functions per
> handler. The Lambda count alone will likely cross the threshold first.

**#80 Phase 2 update (2026-05) — triggers (a) and (b) have fired.**
Onboarding adds a second Step Function (`reciterai-onboarding`) and a fourth
cron rule (`reciterai-onboarding-detector-daily`). Per **D-INFRA** (the
onboarding PLAN), the CDK migration is *deliberately deferred*: onboarding
declares its infra in this directory alongside the hot path, and the two
migrate together in one batch. Revisit trigger: ReCiter-CDK#11 closing with
a named, deployable hosting path.

**#37 PR 4 update (2026-05) — the revisit trigger fired, and overshot
further.** ReCiter-CDK#11 closed on the operator's pivot decision, and the
named hosting path is this PR's ECS Fargate task definition. The CDK
migration revisit (per plan §4.5 / D6): the #37 deploy extends this
directory **once more**, exactly as #80 onboarding did — a fifth cron rule
(`reciterai-enrichment-daily`), a new compute substrate (ECS Fargate
alongside Lambda + Step Functions), and a second IAM policy file
(`enrichment_task_iam_policy.json`). That overshoots the D-10 threshold
further still; the overshoot is acknowledged, not ignored. A full CDK
migration of all ReciterAI infra remains worthwhile but is **its own
deferred Phase**, not a blocker for #37. With ReCiter-CDK#11 closed, the
closed-loop reference for the eventual revisit is this PR + the #80
onboarding deploy — both demonstrate a named, deployable hosting path
under the single-file IaC convention.

**#240 update (2026-06) — overshoots again, deliberately.** The cold-run Fargate
task (`cold_run_task_definition.json` + `cold_run_task_iam_policy.json`) adds a
second ECS task definition and a second IAM task-role policy — and is exactly the
"separate cold-path … compute" that trigger **(a)** named. Per the established
posture (#80, #37), the CDK migration stays a deferred Phase, not a blocker: #240
extends this directory once more under the single-file convention (no EventBridge
rule yet — the cold-run is on-demand). The acknowledged overshoot grows; a full
CDK migration of all ReciterAI infra remains the eventual target.

When the threshold fires, file the migration as its own Phase. Reciter-
CDK (the existing IaC repo for the Java retrieval services) is the
natural target if cross-repo coupling is acceptable; otherwise a new
`ReciterAI-CDK` repo. Either way, the migration should preserve the
JSON files in this directory until the CDK stack is verified in prod —
they are the rollback path.

## Constraints baked into the current shape

- **Account/region are templated**, not hard-coded. `{account_id}` and
  `{region}` placeholders in `eventbridge.json` are substituted by
  `deploy_cron.sh` using `AWS_ACCOUNT_ID` and `AWS_REGION` env vars.
- **IAM roles are not created here.** The script assumes the Lambda
  execution roles, the EventBridge → Step Functions invocation role
  (`reciterai-eventbridge-invoke-states`), the EventBridge → ECS
  RunTask role (`reciterai-eventbridge-invoke-ecs`), and the Fargate
  task role already exist with the policies from
  `lambda_iam_policy.json` and `enrichment_task_iam_policy.json`
  attached.
- **Cron expressions are tunable in this file.** No code change is
  required to change schedules; edit `schedule_expression` in
  `eventbridge.json` and re-run `deploy_cron.sh`.
- **Lambda permissions are idempotent on re-run.** `add-permission`
  uses a deterministic statement-id; re-running prints a notice but
  does not fail.
- **ECS target networking is env-var-driven.** Subnets and security
  groups for the `reciterai-enrichment-daily` rule come from
  `RECITERAI_ENRICHMENT_SUBNETS` and `RECITERAI_ENRICHMENT_SECURITY_GROUPS`
  at deploy time, not from `eventbridge.json` — VPC layout is
  account-specific and should not be committed.

## Onboarding state-machine role (#80 Phase 2)

`scripts/deploy_onboarding_state_machine.sh` takes a `STATE_MACHINE_ROLE_ARN`
— the IAM role **assumed by Step Functions itself** to run the
`reciterai-onboarding` workflow. Like the hot path's state-machine role and
the Lambda execution roles, it is created out-of-band (this directory does
not create IAM roles). Its minimum policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "InvokeOnboardingAndReusedHotLambdas",
      "Effect": "Allow",
      "Action": "lambda:InvokeFunction",
      "Resource": [
        "arn:aws:lambda:*:*:function:reciterai-onboarding-orchestrator",
        "arn:aws:lambda:*:*:function:reciterai-onboarding-finalize",
        "arn:aws:lambda:*:*:function:reciterai-onboarding-notify",
        "arn:aws:lambda:*:*:function:reciterai-onboarding-derive-topics",
        "arn:aws:lambda:*:*:function:reciterai-hot-score",
        "arn:aws:lambda:*:*:function:reciterai-hot-assign",
        "arn:aws:lambda:*:*:function:reciterai-hot-top-topic",
        "arn:aws:lambda:*:*:function:reciterai-hot-rollup"
      ]
    },
    {
      "Sid": "WriteStageRows",
      "Effect": "Allow",
      "Action": "dynamodb:PutItem",
      "Resource": "arn:aws:dynamodb:*:*:table/reciterai"
    }
  ]
}
```

`lambda:InvokeFunction` covers the 8 functions the ASL invokes — the four
onboarding Lambdas plus the four reused hot per-stage Lambdas.
`reciterai-onboarding-detector` is **not** in the list: EventBridge invokes
it, not the state machine. `dynamodb:PutItem` covers the ASL's inline
`arn:aws:states:::dynamodb:putItem` states (the terminal `STAGE#onboarding`
row and the per-stage `STAGE#` rows). This mirrors the hot path's
state-machine role.

## Backup / DR posture (#223)

The `reciterai` table and both artifact buckets are now durability-hardened:

- **DynamoDB `reciterai`**: PITR (35-day continuous restore) + deletion protection.
  PITR is the only protection against a bad pipeline run overwriting rows in place
  (S3-style versioning can't recover an in-place `UpdateItem`). Declared in
  `dynamodb_table.json`; `DeletionProtectionEnabled` is also set inline in
  `create_chatbot_table` so a recreate can't silently reintroduce the gap.
- **`wcmc-reciterai-artifacts` + `wcmc-reciterai-hierarchy`**: bucket versioning +
  a 90-day noncurrent-version expiration lifecycle (`s3_lifecycle_noncurrent.json`).
  Canonical history lives in `{version}/` and `spotlight/runs/{run_id}/` as
  **current** objects (never expired by the rule); the lifecycle only bounds
  superseded `latest/*` pointers.

Apply / re-check with `scripts/apply_backup_config.sh` (`--dry-run` / `--verify`).
Restore procedures are in `docs/dr-runbook.md`. This **declares** existing
resources and adds none, so it does not trip the D-10 CDK-migration threshold
above.

## Related

- `docs/data-model-and-queries.md` — DynamoDB record types these crons write.
- `docs/hot-cold-paths.md` (T13) — operator guide for hot/cold/spotlight/drift/onboarding/enrichment invocation.
- `docs/daily-enrichment.md` — operator guide for the daily enrichment job; §"Deploying the enrichment job" is the PR 4 deploy runbook (ECR push, secrets, task def register, cron apply, smoke gate, #112 backfill).
- `docs/severity.md` — alert severity table; conditions referenced by the drift evaluator.
- `pipeline_hot/state_machine.asl.json` — the Step Functions definition the hot-weekly rule starts.

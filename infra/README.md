# ReciterAI infrastructure (single-file IaC, D-10)

This directory is Phase 10's IaC v1. It is deliberately simple: a JSON
file of EventBridge rules + targets, a JSON file of the minimum-
privilege Lambda IAM policy, and a thin bash deploy wrapper at
`scripts/deploy_cron.sh`. No Terraform, no CDK, no per-environment
overlays.

## Files

- **`eventbridge.json`** — four cron rules and their targets:
  - `reciterai-hot-weekly` → Step Functions state machine `reciterai-hot-path` (Mondays 12:00 UTC).
  - `reciterai-spotlight-monthly` → Lambda `reciterai-spotlight-orchestrator` (1st of month, 13:00 UTC).
  - `reciterai-drift-daily` → Lambda `reciterai-drift-evaluator` (daily 14:00 UTC).
  - `reciterai-onboarding-detector-daily` → Lambda `reciterai-onboarding-detector` (daily 13:00 UTC; #80 Phase 2).
  - **Note:** the daily enrichment job (#37) is intentionally NOT scheduled here.
    It runs as an operator-typed CLI from the operator's laptop until an
    org-managed OpenAI API key replaces the current personal key. See
    `docs/daily-enrichment.md` for the operator guide and the trigger for
    revisiting automation.
- **`lambda_iam_policy.json`** — minimum permissions for every ReciterAI Lambda execution role.
- **`../scripts/deploy_cron.sh`** — `aws events put-rule` + `aws events put-targets` + `aws lambda add-permission` per rule. Supports `--dry-run` and `--rule <name>`.

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
  execution roles and the EventBridge → Step Functions invocation role
  (`reciterai-eventbridge-invoke-states`) already exist with the
  policy from `lambda_iam_policy.json` attached.
- **Cron expressions are tunable in this file.** No code change is
  required to change schedules; edit `schedule_expression` in
  `eventbridge.json` and re-run `deploy_cron.sh`.
- **Lambda permissions are idempotent on re-run.** `add-permission`
  uses a deterministic statement-id; re-running prints a notice but
  does not fail.

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

## Related

- `docs/data-model-and-queries.md` — DynamoDB record types these crons write.
- `docs/hot-cold-paths.md` (T13) — operator guide for hot/cold/spotlight/drift invocation.
- `docs/severity.md` — alert severity table; conditions referenced by the drift evaluator.
- `pipeline_hot/state_machine.asl.json` — the Step Functions definition the hot-weekly rule starts.

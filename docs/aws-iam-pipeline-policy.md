# Pipeline-runner IAM policy

Companion to [`aws-iam-pipeline-policy.json`](aws-iam-pipeline-policy.json). Each
statement maps to a specific class of operation the ReciterAI pipeline performs at
runtime, sourced from the actual call sites in the codebase. Operators auditing the
policy can use this document to verify nothing is over-broad and nothing is missing.

Before applying the JSON, substitute `ACCOUNT_ID` with the actual 12-digit AWS account
number. The bare-string `ACCOUNT_ID` placeholder is intentional — committing a real
account ID into source control adds no security but does propagate environment-specific
state through the repo.

## Statement-by-statement

### `BedrockInvokeModelDirect`

`bedrock:InvokeModel` on three foundation-model ARNs (Haiku 4.5, Sonnet 4.6, Opus 4.7).
Foundation-model ARNs use the empty-account-segment form
(`arn:aws:bedrock:us-east-1::foundation-model/...`) because foundation models are
account-agnostic.

Direct foundation-model invocation is used by callers that pin a specific model without
going through a cross-region inference profile.

### `BedrockInvokeModelViaInferenceProfile`

Same action, different resource type: `inference-profile/us.anthropic.claude-*`. The
`us.` prefix on the model IDs in `utils/bedrock_client.py` (e.g.
`us.anthropic.claude-haiku-4-5-20251001-v1:0`) names an inference profile, not the
foundation model directly — Bedrock requires both ARNs to be authorized when a profile
is used for cross-region inference.

Inference-profile ARNs are account-scoped, so the `ACCOUNT_ID` placeholder applies here.

### `DynamoDBReciterTable`

All read/write operations the pipeline performs against the single shared `reciterai`
table:

| Action | Used by |
|---|---|
| `Query` | TOPIC# scans (assign_subtopics, backfill_topic, score_publications, gates) |
| `GetItem` | stage record reads, history lookups |
| `PutItem` | stage record writes, manifest checkpoints |
| `UpdateItem` | SPOTLIGHT_HISTORY# upserts, REVIEW# state transitions |
| `BatchWriteItem` | bulk PutRequest from `load_dynamodb`, bulk DeleteRequest from `--reset-history` |
| `BatchGetItem` | rotation selector batched last-shown lookups |
| `Scan` | feedback sweep, enrichment imports, history truncation |
| `DescribeTable` | `load_dynamodb` table-existence probe |

Single-table resource — no table-name wildcard, no GSI ARNs (GSIs inherit the table-level
permissions for the read actions).

### `DynamoDBCreateReciterTable`

`dynamodb:CreateTable` is split into its own statement so the operational
read/write role can be granted without table-creation rights if desired. In practice the
pipeline-runner role has both; `load_dynamodb.py` auto-creates the table on first run if
it doesn't exist (the bootstrap path) — but a hardened deployment where the table is
pre-provisioned can drop this statement entirely without breaking steady-state pipeline
behavior.

### `HierarchyBucketReadWrite`

`s3:PutObject`, `s3:GetObject`, `s3:HeadObject` on `wcmc-reciterai-hierarchy/*`. Used by
`backfill_all.py --publish`, gate runners that fetch artifacts for validation
(`gates/cli.py` with `--from-s3`), and the SPS-bound publish step.

### `ArtifactsBucketReadWrite`

Same three actions on `wcmc-reciterai-artifacts/*`. Used by:
- `backfill_spotlight.py --publish` (PutObject)
- `backfill_spotlight.py --regen-only` / `--review-queue` (GetObject of `spotlight/latest/manifest.json` and `spotlight/latest/spotlight.json`)
- Idempotency checks before write (HeadObject)

The separate [`aws-iam-pipeline-policy-artifacts.json`](aws-iam-pipeline-policy-artifacts.json)
document exists for the consumer-side use case (SPS service-account read access to this
bucket). The pipeline-runner role does not need to use that separate policy — its
artifacts-bucket access is covered by this statement.

### `StepFunctionsConcurrentRunGuard`

`states:ListExecutions` on the hot-path state-machine ARN family
(`stateMachine:reciterai-hot-*`). `pipeline_hot/orchestrator.py:is_state_machine_running()`
lists in-flight executions to detect a concurrent run before kicking off a new one (per
RESEARCH.md Open Q5). No execution-start or stop permissions are granted; the orchestrator
only reads execution state.

The wildcard suffix on the state-machine name accommodates deployment-environment naming
(e.g. `reciterai-hot-prod`, `reciterai-hot-staging`) without re-issuing the policy. If
your deployment uses a single fixed name, narrow this to the exact ARN.

## What's intentionally NOT in this policy

- **EventBridge** (`events:*`): the EventBridge rule that targets the hot state machine
  on a cron schedule needs its own IAM role (the EventBridge → StepFunctions trust
  role), separate from the pipeline-runner role this document describes. Provisioned
  by `infra/eventbridge.json`, not by this policy.
- **StepFunctions `StartExecution` / `StopExecution`**: the pipeline-runner role does
  not start state-machine executions — EventBridge does. Granting StartExecution here
  would create a second path to kick off hot runs and weaken the concurrency guard.
- **S3 `ListBucket`**: the pipeline performs no bucket listings. Key-existence checks
  use `HeadObject`, which does not require `ListBucket`.
- **Bedrock model-discovery actions** (`bedrock:ListFoundationModels`,
  `GetFoundationModel`): the codebase pins model IDs as constants; no runtime discovery
  needed.
- **Wildcard resources** (`"Resource": "*"`): none. Every statement scopes to a specific
  table, bucket, or model ARN. If a stage fails with `AccessDenied` against a resource
  this policy doesn't cover, the right response is to update this JSON (and this
  document), not to relax the resource scope.

## Maintenance

When adding a new AWS-touching code path to the pipeline:

1. Identify the new IAM action(s) and resource(s).
2. Add a new statement (or extend an existing `Action`/`Resource` array) in
   `aws-iam-pipeline-policy.json`.
3. Add a row to the statement-by-statement section above.
4. Note the change in the PR description so the operator who re-applies the policy in
   AWS sees the new requirement.

The intent is: this `.json` is the live minimum policy, and this `.md` is the
human-readable rationale for every line in it. Either alone is incomplete.

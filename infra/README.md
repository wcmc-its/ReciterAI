# ReciterAI infrastructure (single-file IaC, D-10)

This directory is Phase 10's IaC v1. It is deliberately simple: a JSON
file of EventBridge rules + targets, JSON files of the minimum-
privilege IAM policies (one for Lambda execution roles, one for the
Fargate enrichment task role), and a thin bash deploy wrapper at
`scripts/deploy_cron.sh`. No Terraform, no CDK, no per-environment
overlays.

## Files

- **`eventbridge.json`** — eight cron rules and their targets:
  - `reciterai-hot-weekly` → Step Functions state machine `reciterai-hot-path` (Mondays 12:00 UTC).
  - `reciterai-spotlight-monthly` → ECS Fargate RunTask on task definition `reciterai-spotlight` (1st of month, 13:00 UTC; #329 shape a2 — the regen shells out to the full `cli.backfill_spotlight`, past Lambda's 15-minute ceiling; template in `spotlight_task_definition.json`).
  - `reciterai-drift-daily` → Lambda `reciterai-drift-evaluator` (daily 14:00 UTC).
  - `reciterai-onboarding-detector-daily` → Lambda `reciterai-onboarding-detector` (daily 13:00 UTC; #80 Phase 2).
  - `reciterai-enrichment-daily` → ECS Fargate `RunTask` on task definition `reciterai-enrichment` (daily 11:00 UTC; #37 PR 4). The first ECS target — adds a new compute substrate alongside Lambda + Step Functions.
  - `reciterai-taxonomy-drift-daily` → Lambda `reciterai-taxonomy-drift` (daily 15:00 UTC; ADR D5 layer 2).
  - `reciterai-grants-daily` → ECS Fargate `RunTask` on task definition `reciterai-grants` (daily 03:00 UTC; #269). Runs the grants.gov opportunity ingest then the SPS submission-queue drain; template in `grants_task_definition.json`. See "Grants ingest launch path" below.
  - `reciterai-cores-daily` → ECS Fargate `RunTask` on task definition `reciterai-cores` (daily 05:00 UTC). Re-scores core-facility usage for core 14 (Research Informatics) over the full ReciterDB corpus and writes the confirmed/candidate `(publication, core)` rows SPS's core claim queue reads; template in `cores_task_definition.json`. Makes ZERO Bedrock calls — `--with-llm` is deliberately absent. See "Cores daily run launch path" below.
- **`lambda_iam_policy.json`** — minimum permissions for every ReciterAI Lambda execution role. Changes here are applied to the LIVE role manually (`aws iam put-role-policy`) — and ordering can matter: the ADR-D3 `HotHandshakeReadPeerBundles` statement must reach the live role BEFORE the orchestrator zip that uses it is redeployed, or the next hot run fails closed on AccessDenied.
- **`enrichment_task_iam_policy.json`** (#37 PR 4; #137 added `DeleteItem` + `Scan` for the quarantine module) — minimum permissions for the Fargate enrichment task role (DDB `Get/Put/Update/DeleteItem` + `Query`/`Scan` + `BatchWriteItem` + `DescribeTable` on the `reciterai` table, Secrets Manager read on the 4 enrichment secrets, CloudWatch Logs write). No `bedrock:InvokeModel` — Bedrock authenticates via the `AWS_BEARER_TOKEN_BEDROCK` bearer token (plan D8), and dropping the IAM grant gives the task loud-failure mode on a missing/stale token.
- **`ecs_task_definition.json`** (#37 PR 4) — the Fargate task definition for the daily enrichment job. Templated placeholders (`{IMAGE_URI}`, `{TASK_ROLE_ARN}`, etc.) are substituted manually at deploy time; see `docs/daily-enrichment.md` §"Deploying the enrichment job" for the runbook.
- **`cold_run_task_definition.json`** (#240) — the Fargate task definition for the on-demand **taxonomy cold-run** (`python -m pipeline_cold.run`). Reuses the same image as the enrichment task (the repo's single Dockerfile already contains `pipeline_cold`); it only overrides the default CMD. Sized 1 vCPU / 4 GB. See "Cold-run launch path" below.
- **`cold_run_task_iam_policy.json`** (#240) — minimum-privilege policy for the cold-run task role: the enrichment policy's DynamoDB CRUD on `reciterai` + **a new S3 read/write statement** on `wcmc-reciterai-hierarchy/*` and `wcmc-reciterai-artifacts/spotlight/*` (the cold-run is the only ReciterAI task that publishes those artifacts), Secrets Manager read on the DB + OpenAI secrets, and CloudWatch Logs on `/ecs/reciterai-cold`. No `bedrock:InvokeModel` (bearer-token auth, same as enrichment).
- **`grants_task_definition.json`** (#269) — the Fargate task definition for the **daily grant-opportunity ingest** (`sh -c 'python -m pipeline_grants.ingest; python -m pipeline_grants.ingest_submissions'`). Reuses the same image as the other tasks; only overrides the default CMD. Sized 0.5 vCPU / 2 GB (serial Bedrock-I/O loops). See "Grants ingest launch path" below.
- **`grants_task_iam_policy.json`** (#269) — minimum-privilege policy for the `reciterai-grants-task` role: DynamoDB CRUD on `reciterai`, S3 read/write scoped to `wcmc-reciterai-artifacts/grants/*` + `s3:ListBucket` on the bucket (the shrink guard's `key_exists` probe needs it), CloudWatch Logs on `/ecs/reciterai-grants`. No `bedrock:InvokeModel` (bearer-token auth), no Secrets Manager statement (the one secret is execution-role-injected), no DB secrets (pipeline_grants reads no MariaDB).
- **`cores_task_definition.json`** — the Fargate task definition for the **daily core-facility usage inference** (`sh -c 'python -m pipeline_cores.run --core 14 --with-affinity --alias-search --llm-carry-forward --with-method-families --reconcile'`). Reuses the same image as the other tasks; only overrides the default CMD. Sized 1 vCPU / 4 GB — an unscoped run holds the whole 80,203-publication corpus (title + abstract) in memory from `ingest.fetch_publications`, plus the byline map and two paginated full-table DynamoDB Scans. `--with-llm` is deliberately ABSENT (a corpus-wide LLM pass is ~80k Haiku screens a night; the Bedrock budget for a nightly is an open decision in its own issue), and `--llm-carry-forward` is what stops `put_core_usage` from REMOVE-ing the stored LLM evidence on a run that does not produce it (#384). See "Cores daily run launch path" below.
- **`cores_task_iam_policy.json`** — minimum-privilege policy for the `reciterai-cores-task` role: DynamoDB CRUD on `reciterai` (+ `/index/*`) and CloudWatch Logs on `/ecs/reciterai-cores`. The smallest task-role policy in this directory, and the omissions are the content: NO S3 (pipeline_cores publishes no artifact — its output is the DynamoDB rows SPS projects, so there is no `latest/` pointer and no shrink guard), NO Secrets Manager (both secrets are execution-role-injected and nothing in the runtime path calls `GetSecretValue`), NO `bedrock:InvokeModel` (bearer-token auth, and the scheduled command invokes no model at all).
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

**Operator runbook (run it top-to-bottom): [`docs/cold-run-fargate-runbook.md`](../docs/cold-run-fargate-runbook.md).** The section below is the file-level deploy reference; the runbook is the end-to-end procedure (flag state → build → register → dry-run → run → validate → merge #242).

It is **on-demand and operator-gated** — there is deliberately **no EventBridge
schedule** (the cold-run is annual/infrequent and ~$210 in Bedrock per run). The
schedule is future infra (see #191 rollout `0011`, the "Brick F EventBridge"
step); when it lands it adds one rule here, like the others.

### One-time deploy
1. **IAM role** `reciterai-cold-task` with `cold_run_task_iam_policy.json` attached
   (a NEW role — the enrichment task role has no S3 grant and cannot be reused;
   strip the `$schema_note` key before `aws iam put-role-policy`). The execution role
   (`ecsTaskExecutionRole`) is reused; extend its inline Secrets grant to
   `reciterai/bedrock-api-key` if it is not already there.
2. **Pre-create the log group** — `aws logs create-log-group --log-group-name /ecs/reciterai-cold`.
   `ecsTaskExecutionRole` lacks `logs:CreateLogGroup`, so the task def omits
   `awslogs-create-group` and the group must exist first (otherwise the task fails at
   startup with `ResourceInitializationError … CreateLogGroup … AccessDenied`).
3. **Build + push the image** from the commit carrying the intended flag state (config
   is image-baked — see #204), **for `linux/amd64`** (the task is X86_64; a Mac defaults
   to arm64, which won't run on Fargate):
   ```bash
   aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin {ACCT}.dkr.ecr.us-east-1.amazonaws.com
   docker build --platform linux/amd64 -t {ACCT}.dkr.ecr.us-east-1.amazonaws.com/reciterai-enrichment:{TAG} .
   docker push {ACCT}.dkr.ecr.us-east-1.amazonaws.com/reciterai-enrichment:{TAG}
   ```
4. **Register the task def:** substitute the `{…}` placeholders in
   `cold_run_task_definition.json` (strip `$schema_note` + `deploy_notes` — ECS rejects
   unknown keys) and `aws ecs register-task-definition --cli-input-json file://…`.

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

## Grants ingest launch path (#269)

The **daily grant-opportunity ingest** runs two `pipeline_grants` entrypoints
in one Fargate task (`grants_task_definition.json`, family `reciterai-grants`),
scheduled by EventBridge rule `reciterai-grants-daily` (03:00 UTC):

1. `python -m pipeline_grants.ingest` — the grants.gov sweep (fetch →
   normalize → denoise → judge/score → persist `GRANT#` rows + the
   `grants/latest/` S3 artifact).
2. `python -m pipeline_grants.ingest_submissions` — the SPS submission-queue
   drain (development-office staff paste opportunity URLs into SPS's /edit
   intake; this processes the `SUBMISSION` items and writes the
   `processed`/`rejected` status SPS renders back). The drain writes **DynamoDB
   only** — it deliberately does NOT publish the `grants/latest/` artifact
   (SPS picks the produced `GRANT#` rows up via its nightly `etl:dynamodb`; a
   drain-side publish of only that run's items would trip the shrink guard
   against the sweep's same-night full-count manifest — see the comment in
   `pipeline_grants/ingest_submissions.py:drain`). Consequence: an
   `OpportunitiesPublishShrinkError` in this task's logs can only come from
   the sweep, where it is a real degraded-run signal.

**Why `;` and not `&&`:** the two ingests are independent sources sharing one
daily task. A grants.gov API failure must not strand the SPS submission queue —
staff are waiting on their pasted URLs — so `ingest_submissions` runs
regardless of `ingest`'s exit status. Consequence: the container's exit code is
`ingest_submissions`'s alone, and a grants.gov failure is visible **only in the
logs** — which is why the alerting step below is required, not optional.

**SPIN is deliberately excluded.** `pipeline_grants/ingest_spin.py`'s docstring
decision #5 is a written gate: manual CLI only, no scheduler/EventBridge until
the SPIN ToS check on scheduled bulk pulls clears. When it clears, adding SPIN
is a command edit + task-def re-register (a follow-up PR, not this one).

**Image:** the same `reciterai-enrichment` ECR image as every other task — the
single Dockerfile's `COPY . .` already contains `pipeline_grants`, so no new
build pipeline; the operator's normal `docker build --platform linux/amd64` +
push suffices.

### One-time deploy (in order)

1. **Pre-create the log group** — `aws logs create-log-group --log-group-name /ecs/reciterai-grants`.
   `ecsTaskExecutionRole` lacks `logs:CreateLogGroup`, so the task def omits
   `awslogs-create-group` and the group must exist first (otherwise the task
   fails at startup with `ResourceInitializationError … CreateLogGroup …
   AccessDenied` — the cold/spotlight gotcha).
2. **IAM role** `reciterai-grants-task` with `grants_task_iam_policy.json`
   attached (strip the `$schema_note` key before `aws iam put-role-policy`). A
   NEW role — the enrichment role has no S3 grant and the cold role's S3 write
   is scoped to spotlight/hierarchy, not `grants/*`. The execution role
   (`ecsTaskExecutionRole`) is reused as-is: its inline Secrets grant already
   covers `reciterai/bedrock-api-key` (the only secret this task injects).
3. **Extend `reciterai-eventbridge-invoke-ecs`** — add `ecs:RunTask` on the
   `reciterai-grants` task-definition family and `iam:PassRole` for
   `reciterai-grants-task` **and** `ecsTaskExecutionRole`. This per-family
   omission is exactly what bit the spotlight deploy (the enrichment-era policy
   predates each new family); do it before the first tick, not after.
4. **Register the task def:** substitute the `{…}` placeholders in
   `grants_task_definition.json` (strip `$schema_note` + `deploy_notes` — ECS
   rejects unknown keys) and `aws ecs register-task-definition --cli-input-json file://…`.
5. **Wire the schedule:** `scripts/deploy_cron.sh --rule reciterai-grants-daily`
   (run with `--dry-run` first; needs `AWS_ACCOUNT_ID`, `AWS_REGION`, and the
   ECS env vars `RECITERAI_ENRICHMENT_SUBNETS` + `RECITERAI_ENRICHMENT_SECURITY_GROUPS`).
6. **Watch the first ticks:** `aws logs tail /ecs/reciterai-grants --follow`.
   Expect the `ingest summary: …` line from the grants.gov sweep, then the
   drain's summary. **There is no Fargate task timeout — a hung run runs
   forever** (and a hung grants.gov or Bedrock socket read is the likeliest
   failure), so eyeball the first scheduled runs to calibrate normal wall-clock, and
   specifically check the FIRST FULL SWEEP finishes before 07:00 UTC — SPS's
   nightly projection reads the GRANT# rows then; an overrun defers that
   night's rows (and staff-visible submission statuses) to the next day.
   If it overruns, move the cron earlier or put the drain first in the command
   before trusting the schedule unattended.
7. **Alerting (REQUIRED before calling #269 closed)** — see below.

### Alerting gap — out-of-band CloudWatch alarm (required)

`pipeline_grants` has **zero alerting**: it never imports
`pipeline_common.alert` / `pipeline_enrichment.alerting` (the repo's Teams-card
paths), and the `;` chaining means a grants.gov failure doesn't even fail the
task. Until a code-level Teams path is added, a failed or shrink-guard-blocked
run is silent outside CloudWatch. This directory's convention creates no
CloudWatch resources, so the alarm is applied **out-of-band** (one-time, like
the IAM roles):

```bash
# Metric filter: any traceback, ERROR line, or shrink-guard refusal in the task logs
aws logs put-metric-filter \
  --log-group-name /ecs/reciterai-grants \
  --filter-name reciterai-grants-errors \
  --filter-pattern '?ERROR ?Traceback ?OpportunitiesPublishShrinkError' \
  --metric-transformations \
    metricName=GrantsIngestErrors,metricNamespace=ReciterAI,metricValue=1,defaultValue=0

# Alarm -> the operator's existing SNS topic ({SNS_TOPIC_ARN} — create one if none exists)
aws cloudwatch put-metric-alarm \
  --alarm-name reciterai-grants-ingest-errors \
  --namespace ReciterAI --metric-name GrantsIngestErrors \
  --statistic Sum --period 86400 --evaluation-periods 1 \
  --threshold 1 --comparison-operator GreaterThanOrEqualToThreshold \
  --treat-missing-data notBreaching \
  --alarm-actions {SNS_TOPIC_ARN}
```

`treat-missing-data notBreaching` keeps quiet days green; the 24h period spans
one daily tick. Note the alarm does **not** catch a run that never launched
(rule disabled, RunTask permission failure) — `deploy_cron.sh` will happily
bind a target that can't launch and exit 0 (the taxonomy-drift lesson), so the
first-tick eyeball in step 6 is the guard for that class. **#269 stays open
until this filter + alarm (or a code-level Teams alert in `pipeline_grants`)
is live.**

## Cores daily run launch path

The **daily core-facility usage inference** runs one `pipeline_cores` entrypoint in
one Fargate task (`cores_task_definition.json`, family `reciterai-cores`), scheduled
by EventBridge rule `reciterai-cores-daily` (05:00 UTC):

```bash
python -m pipeline_cores.run --core 14 --with-affinity --alias-search --llm-carry-forward --with-method-families --reconcile
```

It re-scores every `(publication, core)` pair for core 14 (Research Informatics)
against the whole scoreable ReciterDB corpus and writes the **surfaced** rows —
`confirmed` and `candidate` only; `below_threshold` is computed and dropped — as
`PK=PUB#{pmid}` / `SK=CORE#14` items in the shared `reciterai` table. SPS's own
nightly is what projects those into MySQL for the core claim queue; this task's
output is invisible to a reviewer until that runs, which is the whole reason for
the 05:00 slot (see the rule's `schedule_comment`).

**What each flag buys, and what is deliberately missing:**

- `--with-affinity` seeds the cross-run repeat-user prior from prior
  confirmed/claimed rows (one full-table Scan, gated to the scoreable corpus on
  *both* sides of the rate — `ingest.filter_corpus_pmids` exists because 19 of core
  14's 43 prior rows are outside the corpus and counting them inflated a cwid from
  `aff:regular` to `aff:core`).
- `--alias-search` is signal 3 **without** the corpus prefetch: three paged PMC
  `esearch` calls for core 14's three non-acronym aliases, then full text for the
  ~27 PMIDs those searches name — instead of 80,203 NCBI round trips for the same
  answer. NCBI is the only third-party endpoint the task talks to, and the reason
  its security group needs egress to `eutils.ncbi.nlm.nih.gov` +
  `www.ncbi.nlm.nih.gov` on top of the AWS APIs and ReciterDB.
- `--llm-carry-forward` re-emits the LLM evidence already stored on each row, and
  **it is not optional on a run without `--with-llm`.** `persist.put_core_usage`
  SETs what the run produced and REMOVEs every run.py-owned attribute it did not
  (`ack_alias`, `ack_snippet`, `llm_score`, `llm_rationale`, `author_affinity`) —
  deliberate, from #384, so a previous run's rationale cannot survive on a pair
  scored without the LLM and read as fresh evidence. Without carry-forward the
  first zero-Bedrock tick would strip `llm_score`/`llm_rationale` from every row it
  re-surfaces, and on core 14 that is the chip the review queue rests on (62 of 62
  open rows carry it). Dropping the flag does not fail the run; it empties the
  queue's strongest evidence overnight and looks like a working night.
- `--with-method-families` joins the A2 method-family taxonomy
  (`s3://<artifacts>/tools/latest/`) onto each publication and writes `method_tier`
  + `method_evidence`. It is a DETERMINISTIC join, not a model call: no Bedrock, no
  NCBI, one ~26 MB S3 read per run, loaded once and shared across the run. It was
  absent from the first shipped schedule only because the signal had not merged yet
  (#395); without it `method_tier` is never produced, and SPS's queue cannot show a
  method chip no matter what it plumbs.
  **Two things to know before removing it again.** First, `method_tier` and
  `method_evidence` are run.py-owned, so dropping the flag REMOVEs them from every
  row the next run re-surfaces — the same #384 trap `--llm-carry-forward` exists to
  close, and there is no carry-forward for these. Second, `load_family_index`
  validates core 14's seven curated labels against the artifact and RAISES on one
  the taxonomy has renamed away (#397), which aborts the whole run rather than
  scoring without the signal. That is deliberate — a silently-no-op curation is the
  failure it was written to prevent — but it does mean this flag can turn a taxonomy
  rebuild into a failed nightly. The artifact is frozen at v2026-06-23 with no
  schedule, and all seven labels were verified present against it when the flag was
  added, so the live risk today is nil; it stops being nil the day the tools
  pipeline gets a cadence.
- `--reconcile` demotes what the run did **not** re-surface. Without it a re-score
  cannot demote: a pair that falls below threshold (or leaves the corpus) is simply
  not written and keeps its old `candidate`/`confirmed` status forever — the gap the
  hand-run `demote_core14_stale.py` scripts closed. After `put_core_usage`, one
  paginated Scan finds core 14's `candidate`/`confirmed` rows with `scored_at` older
  than this run's single stamp, and each is demoted to `below_threshold` by a
  conditional UpdateItem (`status IN (candidate, confirmed) AND scored_at = :seen`),
  so a reviewer's `claimed`/`rejected` — even one written mid-sweep — and a row a
  concurrent run re-scored are never overwritten. Out-of-corpus pairs are demoted
  too (decided 2026-09-29; ~19 rows on core 14). SPS's `publication_core` prune then
  drops the demoted rows from MySQL on its next nightly. **Two safety guards, per
  core, both logged at ERROR (so the `?ERROR` metric filter below sees them) and
  neither failing the task:** a core this run surfaced **zero** rows for is never
  reconciled (indistinguishable from a run that silently scored nothing), and a core
  whose would-demote count exceeds half of (demoted + re-surfaced) is refused
  (`--reconcile-max-demote-fraction`, default 0.5 — raise it only for a deliberate
  operator re-score). run.py **refuses** `--reconcile` with `--test` or
  `--pmids-file` (exit 2): a partial-corpus run would read every row outside its
  slice as stale. Needs no IAM change — one more `Scan`, and `UpdateItem`s, both
  already granted.
- `--with-llm` is **deliberately absent.** `signals.llm_triage` makes one Haiku
  screen call per publication it is handed and a Sonnet dense-score call for each
  one at or above `SCREEN_CUTOFF`; with no `--pmids-file` the pool is the whole
  corpus, so an LLM-on nightly is ~80,203 Haiku calls a night plus the Sonnet tail.
  How much Bedrock a nightly may spend is an open decision tracked in its own
  issue, so the shipped schedule spends nothing. Turning it on later is a command
  edit + `register-task-definition` — no new infra, no IAM change (Bedrock
  authenticates on the bearer token the execution role already injects), and no
  security-group change if Bedrock egress is left reachable.
- `--all-cores-screen` is **deliberately absent** — experimental per its own
  `--help`: one Haiku call screens every core at once for ~13x fewer screen calls,
  but it lost 7-11% screen recall on the 237-pilot (borderline true positives
  collapse to score 1), and it is off until re-calibrated to per-core parity.
- `pipeline_cores.batch_screen` is **not in the command at all.** It MINTS
  candidate rows through `persist.put_candidate` and overwrites `likelihood` — a
  different, screen-priced product from run.py's evidence-weight scoring. The
  nightly runs run.py and only run.py.

**One core, on purpose.** `--core 14` is the entire scope: it is the core whose
claim queue is staffed, and the only core run.py has ever scored in production.
Every other core stays operator-run, and this rule cannot touch their rows — a run
only writes the pairs for the cores it scored. A second staffed core is a sibling
task definition + rule (its own schedule, its own log group), or dropping `--core`
to run the whole dictionary; run.py's `--core` takes a single id, so there is no
two-core middle ground in one command.

**Image:** the same `reciterai-enrichment` ECR image as every other task — the
single Dockerfile's `COPY . .` already contains `pipeline_cores`, so no new build
pipeline; the operator's normal `docker build --platform linux/amd64` + push
suffices. Config is image-baked (#204) and the surface is larger here than
elsewhere: `config/core_dictionary.yaml` (aliases, cached `alias_hits`, staff
CWIDs, per-core thresholds) and `combine.WEIGHTS` (the fitted evidence weights)
all ride the image, so a dictionary or weight change needs a rebuild before the
next tick sees it. The one exception is the curated **known-client** list — SPS's
"Known clients" panel writes it to DynamoDB (`CORE#14`/`CLIENTS`) and `run_core`
unions it with the YAML list at run time, so a client added in SPS today is in
scope tomorrow morning with no deploy.

### One-time deploy (in order)

0. **Build and push an image that actually contains the command.** There is no
   image-building CI in this repo — `.github/workflows/` is `pytest.yml` and
   `axis2-producer-gate.yml`, neither of which touches ECR — so **merging changes
   nothing in ECR**, and the newest tag can be months older than `main`. Check
   before anything else:

   ```bash
   aws ecr describe-images --repository-name reciterai-enrichment \
     --query 'reverse(sort_by(imageDetails,&imagePushedAt))[0].{tag:imageTags[0],pushed:imagePushedAt}'
   ```

   If that tag predates the commit carrying the flags in the task-def command,
   build and push first (from a checkout at the commit you intend to run):

   ```bash
   aws ecr get-login-password --region us-east-1 \
     | docker login --username AWS --password-stdin <acct>.dkr.ecr.us-east-1.amazonaws.com
   docker build --platform linux/amd64 -t <acct>.dkr.ecr.us-east-1.amazonaws.com/reciterai-enrichment:<sha> .
   docker push <acct>.dkr.ecr.us-east-1.amazonaws.com/reciterai-enrichment:<sha>
   ```

   `--platform linux/amd64` is not optional: the task is `X86_64` and a Mac
   defaults to arm64, which will not run on Fargate. Skipping this step produces
   the worst failure shape available — a green EventBridge rule firing a task that
   dies instantly every night on `error: unrecognized arguments:
   --llm-carry-forward`, with nothing but the log group to say so. The other
   families' launch paths bury this in an "Image:" paragraph; it is step 0 here
   because it is the step that was actually missed on the first cores deploy.
1. **Pre-create the log group** — `aws logs create-log-group --log-group-name /ecs/reciterai-cores`.
   `ecsTaskExecutionRole` lacks `logs:CreateLogGroup`, so the task def omits
   `awslogs-create-group` and the group must exist first (otherwise the task fails
   at startup with `ResourceInitializationError … CreateLogGroup … AccessDenied` —
   the cold/spotlight/grants gotcha, unchanged).
2. **IAM role** `reciterai-cores-task` with `cores_task_iam_policy.json` attached
   (strip the `$schema_note` key before `aws iam put-role-policy`). A NEW role, and
   a *smaller* one than any existing task role rather than a superset: enrichment
   and cold carry Secrets Manager grants this task does not need, and cold and
   grants carry S3 writes to buckets this pipeline never publishes to. The
   execution role (`ecsTaskExecutionRole`) is reused **as-is** — its inline Secrets
   grant already covers `reciterai/reciter-analysis-db` and
   `reciterai/bedrock-api-key`, the only two this task injects, so unusually for a
   new family there is no execution-role extension step here.
3. **Extend `reciterai-eventbridge-invoke-ecs`** — add `ecs:RunTask` on the
   `reciterai-cores` task-definition family and `iam:PassRole` for
   `reciterai-cores-task` **and** `ecsTaskExecutionRole`. **This is the step that
   has bitten on every single new family** (it bit spotlight, then grants; the
   enrichment-era policy predates each of them). Do it before the first tick, not
   after: `deploy_cron.sh` will happily bind a target that cannot launch and exit 0,
   so the symptom is a rule that fires nightly and produces nothing at all — no
   task, no log stream, no alarm.
4. **Register the task def:** substitute the `{…}` placeholders in
   `cores_task_definition.json` (strip `$schema_note` + `deploy_notes` — ECS
   rejects unknown keys) and `aws ecs register-task-definition --cli-input-json file://…`.
   Then run the two bounded gates in that file's `deploy_notes.plumbing_gate`
   before wiring the schedule: the `--test 500 --dry-run` one proves image,
   secrets, ReciterDB reachability, NCBI egress, `dynamodb:Scan` and logging while
   writing nothing; the same command **without** `--dry-run` is the only thing that
   proves `dynamodb:UpdateItem` and the curated-client `GetItem`, both of which
   `--dry-run` skips by design.
5. **Wire the schedule:** `scripts/deploy_cron.sh --rule reciterai-cores-daily`
   (run with `--dry-run` first; needs `AWS_ACCOUNT_ID`, `AWS_REGION`, and the ECS
   env vars `RECITERAI_ENRICHMENT_SUBNETS` + `RECITERAI_ENRICHMENT_SECURITY_GROUPS`
   — the cores task reuses the enrichment VPC layout, which is what reaches
   ReciterDB).
6. **Watch the first tick:** `aws logs tail /ecs/reciterai-cores --follow`. What to
   look for, in order:
   - `[14 Research Informatics] alias search: <n> PMC papers name an alias` —
     proves NCBI egress from the subnets. A zero here with no warning above it
     means the searches ran and found nothing, which for core 14's aliases would
     itself be the anomaly (their cached global PMC counts are 25 / 20 / 2).
   - `[14 Research Informatics] curated clients: <n> yaml + <m> dynamodb -> <k> union` —
     **`<m>` should be at least 1.** No core defines a `clients:` list in
     `config/core_dictionary.yaml` today, so `<n>` is 0 and the union is whatever
     SPS's "Known clients" panel has written. The `reciterai` DynamoDB table is
     **shared between SPS staging and prod**, so this number is the same in both
     environments — a difference would mean the item was written somewhere else,
     not that one environment is behind. `<m>` of 0 is either a genuinely empty
     item or a degraded read (`get_curated_clients` fail-softs to an empty set and
     logs a warning, never raises) — and since the panel's first curated add is
     already in the shared table, a 0 points at the read, not the data.
     **What a healthy `<m>` does NOT tell you.** It does not mean the asserted-client
     signal is moving anything. `WEIGHTS["client"]` is `0.00` — unfitted, pending
     ReciterAI #383 — and `client_cwids` is not in `build_core_item`, so a curated
     client on a byline is extracted, visible in `explain()`, persisted nowhere, and
     worth exactly zero nats. A publication whose ONLY evidence is a curated client
     scores P=0.02 and is never written. Until #383 fits the weight and SPS #2607
     projects the field, this line is a plumbing check on the read, not a measure of
     the signal, and the nightly's real value is the affinity refresh, the alias
     search and co-authorship.
   - `[14 Research Informatics] llm: <n> carried forward, <m> triaged` — on the
     scheduled command `<m>` must be **0** (no `--with-llm`, so no Bedrock call),
     and `<n>` is the number of TONIGHT'S CORPUS PUBLICATIONS that had a stored
     score — `len(pubs) - len(todo)`, i.e. the intersection, not `len(carry_forward)`.
     **Do not compare it to what SPS displays.** The `scan_core_llm_scores` line just
     above it prints the store's own total, and on 2026-09-05 that measured
     `347 stored LLM scores across 1 core(s)` for core 14 while SPS showed 195 rows
     (62 open + 133 confirmed). The gap is not a fault: the `publication_core` prune
     is MySQL-side and never deleted the DynamoDB `CORE#` rows, and
     `scan_core_llm_scores` filters only on `attribute_exists(llm_score)` with no
     status predicate, so claimed/rejected//superseded rows all count. `<n>` is then
     that total intersected with tonight's corpus, which trims it again (19 of core
     14's 43 prior affinity rows are outside the corpus, for scale). `<n>` of 0 on the first tick IS the alarm to stop on: it means
     carry-forward found nothing to re-emit and `put_core_usage` is about to strip
     the review queue's LLM chips. A 0 here is always a real 0 — unlike the other
     two reads, `scan_core_llm_scores` RAISES rather than degrading to empty, so a
     throttled Scan fails the task instead of printing `0 carried forward` and
     wiping the evidence.
   - `[14 Research Informatics] <n> pubs -> <c> confirmed, <k> candidates` — `<n>`
     should be the FULL corpus (~80,203). A materially smaller number means the
     corpus query was scoped, not that the core got smaller.
   - `wrote <n> (publication, core) records to DynamoDB` — `<n>` = `<c>` + `<k>`
     from the line above, because only surfaced rows are persisted. Pairs that fall
     below threshold are not written; `--reconcile` is what moves them.
   - `reconcile: core 14 demoted <x>, skipped <y> (condition failed)` — `<x>` is the
     rows the run stopped surfacing. Expect it LARGE on the first tick with the flag
     (the backlog of stale candidates since 09-15, plus ~19 out-of-corpus pairs) and
     small after. `<y>` is rows claimed/rejected or re-scored between the Scan and the
     write, normally 0. `reconcile: core 14 REFUSED` at ERROR means a safety guard
     fired and nothing was demoted — read its reason before re-running with a higher
     `--reconcile-max-demote-fraction`. SPS's next nightly should then log a
     `publication_core prune: removed N` close to `<x>`.
   - **Wall clock, measured against 07:00 UTC.** The 2h window before SPS's nightly
     is an assumption until this number exists. If the run overruns it, move THIS
     cron earlier — never the SPS nightly.
   **There is no Fargate task timeout — a hung run runs forever** (a stalled NCBI
   socket is the likeliest cause; `utils.db` already bounds a wedged ReciterDB
   connection per #224), so eyeball the first scheduled runs rather than trusting
   the schedule unattended.
7. **Alerting (same gap as grants)** — see below.

### Alerting gap — out-of-band CloudWatch alarm

`pipeline_cores` has **zero alerting**, exactly like `pipeline_grants`: it never
imports `pipeline_common.alert` / `pipeline_enrichment.alerting`. Two of its three
DynamoDB reads (`scan_prior_core_usage`, `get_curated_clients`) are deliberately
fail-soft in shape, but only ONE of them still is on this path. `get_curated_clients`
degrades to an empty set, logs a warning, and lets the run finish **green** — and
today that costs nothing measurable, because the `client` weight is 0.00 (#383), so
the alarm below catches it for the day the weight is fitted rather than for now.
`scan_prior_core_usage` is called `strict=True` from `run.py` precisely because this
run WRITES what it reads: an empty affinity prior is not a worse ranking, it is
`author_affinity = 0.0` swept into `put_core_usage`'s REMOVE clause and rows demoted
confirmed -> candidate, so it raises here and degrades only for the two analysis
callers (`batch_screen`, `suggest_aliases`) whose output nobody stores.
`scan_core_llm_scores` raises unconditionally for the same reason — an empty result
there would wipe the stored LLM evidence. Both of those you find out about from a
non-zero exit code; the metric filter is for the warning-level residue and for
Tracebacks.

**The remaining fail-soft that this run still writes back is the alias search.**
`pmc_search.pmids_naming` catches per-alias NCBI failures and returns whatever it has
(its docstring calls signal 3 "a bonus confirmer, never load-bearing" — for core 14 it
is not: all three aliases are non-acronym and `distinctive`, worth 6.37 + 4.35 + 3.47
nats together). An NCBI outage therefore yields `ack_matched=False` for the whole
night, `make_alias_loader` caches the empty result and never retries, and
`ack_alias`/`ack_snippet` land in `put_core_usage`'s REMOVE clause on every row that
still surfaces on other evidence. Rows that surfaced on the ack alone simply fall below
threshold and are not rewritten, so they survive stale rather than being deleted. This
is left fail-soft deliberately — "no PMC hits" is a legitimate result and cannot be
distinguished from an outage without counting failures per alias — which is why
`?"alias search failed"` is in the filter pattern below: on this cadence it is the
alarm, not the code, that has to notice. The
follow-up is the same metric-filter alarm the grants section documents, with the
log group swapped:

```bash
aws logs put-metric-filter \
  --log-group-name /ecs/reciterai-cores \
  --filter-name reciterai-cores-error-lines \
  --filter-pattern '?ERROR ?Traceback ?"affinity prior degraded" ?"treating as no curated clients" ?"unreadable shape" ?"alias search failed"' \
  --metric-transformations \
    metricName=CoresRunErrorLines,metricNamespace=ReciterAI/Cores,metricValue=1,defaultValue=0
```

…plus the twin `put-metric-alarm` from the grants section (`--period 86400`,
`--treat-missing-data notBreaching`, one daily tick per period). The filter
pattern is deliberately wider than grants': it catches the two fail-soft
degradations by their log text, because neither of them fails the task. As there,
the alarm cannot see a run that never launched — step 3's `iam:PassRole` omission
produces exactly that — so the first-tick eyeball in step 6 is the guard for that
class.

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

**#269 update (2026-08) — overshoots again, deliberately.** The grants ingest
adds a seventh cron rule (`reciterai-grants-daily`), a fourth ECS task
definition, and a third IAM task-role policy, all under the single-file
convention. Per the established posture (#80, #37, #240), the CDK migration
stays its own deferred Phase, not a blocker.

**Cores daily update (2026-09) — overshoots again, deliberately.** The cores
nightly adds an eighth cron rule (`reciterai-cores-daily`), a fifth ECS task
definition, and a fourth IAM task-role policy. Same posture as #80/#37/#240/#269:
the CDK migration stays its own deferred Phase, not a blocker. Worth recording
for whoever eventually does it — the five ECS task definitions now differ from
each other in exactly four dimensions (command, cpu/memory, which secrets are
injected, which log group), and the four task-role policies differ only in their
S3 statements, their Secrets Manager statements, and their log-group ARN. That is a construct with four parameters,
not five hand-maintained files, and it is the clearest argument the directory has
produced so far for the migration.

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
  groups for the `reciterai-enrichment-daily`,
  `reciterai-spotlight-monthly`, `reciterai-grants-daily`, and
  `reciterai-cores-daily` rules come
  from `RECITERAI_ENRICHMENT_SUBNETS` and
  `RECITERAI_ENRICHMENT_SECURITY_GROUPS` at deploy time, not from
  `eventbridge.json` — VPC layout is account-specific and should not be
  committed. The `reciterai-eventbridge-invoke-ecs` role must allow
  `ecs:RunTask` on ALL FOUR task-definition families
  (`reciterai-enrichment`, `reciterai-spotlight`, `reciterai-grants`,
  `reciterai-cores`)
  plus `iam:PassRole` for each task/execution role pair — the
  enrichment-era policy predates every later family, and the omission
  has bitten on each new one.

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

- `docs/adr-taxonomy-change-propagation.md` — how a taxonomy change reaches production. Directly relevant to the manual-deploy stance above: `taxonomy_v2.json` is baked into three Lambda zips plus the Docker image, and three `update-function-code` calls are never atomic, so a partial deploy splits the taxonomy across artifacts.
- `docs/data-model-and-queries.md` — DynamoDB record types these crons write.
- `docs/hot-cold-paths.md` (T13) — operator guide for hot/cold/spotlight/drift/onboarding/enrichment invocation.
- `docs/daily-enrichment.md` — operator guide for the daily enrichment job; §"Deploying the enrichment job" is the PR 4 deploy runbook (ECR push, secrets, task def register, cron apply, smoke gate, #112 backfill).
- `docs/severity.md` — alert severity table; conditions referenced by the drift evaluator.
- `pipeline_hot/state_machine.asl.json` — the Step Functions definition the hot-weekly rule starts.
- `pipeline_cores/README.md` — what the cores nightly actually computes: the five signals, the fitted evidence weights behind `combine()`, and why the LLM ranks the claim queue but never confirms a pair.

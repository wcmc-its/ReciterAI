# Daily enrichment job — operator guide

The daily enrichment job adds `reciterai_synopsis` + `reciterai_impact` rows
to MariaDB and an `IMPACT#` row to DynamoDB per PMID for new WCM-faculty
publications (#37). The Scholars Profile System reads those rows downstream.

**Operating mode (end state, post-#37 PR 4):** scheduled daily on AWS ECS
Fargate at 11:00 UTC / 07:00 EDT (EventBridge → ECS `RunTask`). The same
task definition runs the #112 backfill (~1,073 papers) and the annual
rescore on-demand via command overrides. The operator laptop is no longer
in the loop.

**Model:** synopsis + impact run on AWS Bedrock Claude Sonnet 4.6
(`us.anthropic.claude-sonnet-4-6`) on the happy path. On a Bedrock
content-filter block (Sonnet 4.6 reproducibly filters WCM biomedical
animal-model abstracts — see `sonnet-content-filter-on-dense-scoring.md`),
each call falls back once to OpenAI gpt-5.1 on the same prompt; only a
filtered-*and*-fallback-failed paper becomes a per-PMID failure.

## Prerequisites — Fargate task (production)

The task definition (`infra/ecs_task_definition.json`, added by #37 PR 4)
supplies these via the ECS-native `secrets` + `environment` blocks. No
operator action is needed once the task is registered.

| Env var | Source | Purpose |
|---|---|---|
| `DB_HOST`, `DB_USERNAME`, `DB_PASSWORD`, `DB_NAME` | Secrets Manager `reciterai/reciter-analysis-db` | MariaDB writes |
| `OPENAI_API_KEY` | Secrets Manager `reciterai/openai-api-key` | Bedrock→OpenAI content-filter fallback only |
| `AWS_BEARER_TOKEN_BEDROCK` | Secrets Manager (new, see §"Deploying the enrichment job") | Bedrock auth — long-term Bedrock API key (#37 D8) |
| `AWS_DEFAULT_REGION` | `environment` | DynamoDB region |
| `RECITERAI_TEAMS_WEBHOOK_URL` | Secrets Manager | Teams alerts on failure / cost-guard trip |
| `RECITERAI_ALERT_MENTION_UPN`, `RECITERAI_ALERT_MENTION_NAME` | `environment` | @-mention on actionable alerts |

The task role has `dynamodb:*Item` + `BatchWriteItem` on the `reciterai`
table, `secretsmanager:GetSecretValue` on the secrets listed above, and
CloudWatch Logs write. Bedrock authentication uses the bearer token
(`AWS_BEARER_TOKEN_BEDROCK`), so `bedrock:InvokeModel` on the task role is
not load-bearing for the happy path (#37 D8).

## Prerequisites — local dev (operator laptop)

Running the CLI locally (e.g., for one-shot diagnostic backfill) still
works against the production tables. Set these in `~/.zshrc`:

| Env var | Purpose | Notes |
|---|---|---|
| `DB_HOST`, `DB_USERNAME`, `DB_PASSWORD`, `DB_NAME` | MariaDB writes | |
| `AWS_BEARER_TOKEN_BEDROCK` | Bedrock auth (long-term Bedrock API key — #37 D8) | boto3 auto-detects this and uses it as a bearer token; no code path change. |
| `OPENAI_API_KEY` | Bedrock→OpenAI content-filter fallback only | The org-shared `reciterai/openai-api-key` secret. |
| `AWS_DEFAULT_REGION` | DynamoDB watermark | Defaults to `us-east-1` if unset. |
| AWS credentials | DynamoDB + Secrets Manager | Standard credential chain (env vars / `~/.aws/credentials` / SSO). The Bedrock API key authenticates Bedrock; everything else uses the default chain. |
| `RECITERAI_TEAMS_WEBHOOK_URL` | Teams alerts | Optional locally — absent = alerts silently skipped. |
| `RECITERAI_ALERT_MENTION_UPN`, `RECITERAI_ALERT_MENTION_NAME` | Teams @-mention | Optional locally. |

## Daily operation (steady state, Fargate)

EventBridge rule `reciterai-enrichment-daily` fires at 11:00 UTC and
invokes `RunTask` against the `reciterai-enrichment` task definition with
its default command (`python -m scripts.run_daily_enrichment`). The
schedule slot was picked to land after the overnight ReciterDB refresh and
before the onboarding detector (13:00 UTC) — so the detector's gap scan
sees the morning's freshly-enriched papers — and before drift (14:00 UTC).

Daily deltas are expected to be **5–15 papers**. At the spike-measured
Sonnet 4.6 rate (~$0.0141/paper combined), that's ~$0.07–$0.21 per run.

**Verifying a tick:**

```bash
# Most recent task run for the enrichment task family:
aws ecs list-tasks --cluster reciterai-cluster \
  --family reciterai-enrichment --desired-status STOPPED \
  --max-items 1

# CloudWatch logs for that task (substitute the task ARN's last segment):
aws logs tail /ecs/reciterai-enrichment --since 24h
```

A clean run logs the result JSON on the final line (`status: complete`,
`successes: N`, `new_watermark_pmid: …`, `cost_observed_usd: …`).

### What success looks like

```json
{
  "status": "complete",
  "delta_size": 7,
  "successes": 7,
  "new_watermark_pmid": 42199999,
  "cost_observed_usd": "0.10",
  "cost_summary": { "call_count": 14, "total_input_tokens": 13900, ... },
  ...
}
```

### What failure looks like

```json
{
  "status": "failed",
  "delta_size": 7,
  "failure_reason": "1/7 pmids failed",
  ...
}
```

The watermark does **not** advance on failure. The next tick retries the
same delta; idempotent writes (`pipeline_enrichment/mariadb_writer.py` +
`pipeline_enrichment/ddb_writer.py`) make that safe. If a particular PMID
persistently fails — content-filtered *and* fallback also failed, or a
transient Bedrock error — investigate manually before the daily delta
grows unmanageably (the cost guard catches the order-of-magnitude case;
the orchestrator is all-or-nothing per tick).

### Cost guard

`pipeline_enrichment/cost_guard.py` refuses a run whose preflight estimate
exceeds **$30** at a Sonnet-calibrated rate of **$0.018/paper** (the
spike's $0.0141/paper rounded up to cover the length-retry loops and the
content-filter fallback lane). At those defaults the guard trips above
~1,667 papers — far above any plausible daily delta. A trip means
something is structurally wrong (watermark hasn't advanced for weeks, a
corpus-filter regression, etc.), not "today's run is expensive."

The backfill and the annual rescore enforce no threshold (they bypass via
`--from-gap-scan` / `--full`).

## Quarantine + operator triage (#137)

The daily job's all-or-nothing watermark contract is correct for transient
flakes but pathological for content-specific failures: a PMID that
consistently breaks a Bedrock call (a structurally truncatable abstract,
content-filter-adjacent without the exact filter `stop_reason`, a model
regression on certain text shapes) would stall the entire delta forever,
every tick burning ~$0.55 of LLM cost without progress. The 2026-05-20
11:00 UTC tick was exactly that — PMID 42119587 (SURMOUNT-MAINTAIN
tirzepatide trial) hit `max_tokens=512` mid-JSON in the impact call and
killed a 36-PMID delta until PR #136 + manual recovery cleared it.

**Per-PMID quarantine** (`pipeline_enrichment/quarantine.py`) tracks
consecutive failure counts so that the *class* of failure auto-resolves.
After **3** consecutive failures (`config/thresholds.json` key
`enrichment_quarantine_threshold`), the threshold-crossing tick itself
treats the PMID as a write-off for the all-or-nothing gate, advances the
watermark past it, and fires an ERROR Teams card listing the quarantined
PMID for operator triage. Subsequent ticks skip the quarantined PMID
before any LLM call (defense in depth — the watermark has normally already
advanced past it).

Quarantine state is independent of the cost guard. Quarantined PMIDs still
count in the cost-guard preflight estimate (the guard fires on the
*original* delta size); the few cents of over-estimate keeps the contract
simple.

### Quarantine state in DynamoDB

```
PK = ENRICHMENT_QUARANTINE#pmid_{pmid}
SK = STATUS
attributes: pmid, consecutive_failures, last_failure_at,
            last_failure_reason, last_attempted_run_id, created_at
```

**Distinct from `QUARANTINE#pmid_{pmid}`** — the scoring pipeline
(`utils/dynamodb_helpers.py:quarantine_pmid` + `pipeline_hot/orchestrator.py`)
uses that other prefix for its own quarantine scheme (different schema,
companion `PROCESSING#` checkpoint row, `taxonomy_version`-bound). Two
pipelines, two quarantine schemes, one DDB table — the disambiguation
matters when scanning the table in the console.

### Operator workflow

When an ERROR card lands listing quarantined PMIDs:

1. **Inspect** the failure reason in the card or the DDB row:
   ```bash
   aws dynamodb get-item --table-name reciterai \
     --key '{"PK":{"S":"ENRICHMENT_QUARANTINE#pmid_42119587"},"SK":{"S":"STATUS"}}'
   ```
2. **Replay** every quarantined PMID in one batch (Fargate or local —
   bypasses watermark, no Teams alerts, prints result JSON):
   ```bash
   python -m scripts.run_daily_enrichment --retry-quarantine
   ```
   Successes clear their row; failures bump the counter (without a
   threshold check, since the operator is explicitly probing). Exit 0
   when every replayed PMID cleared, exit 1 when any row still failing.
3. **Manual clear** after an out-of-band code fix has made the PMID
   processable on the next daily tick:
   ```bash
   python -m scripts.run_daily_enrichment --clear-quarantine 42119587
   ```
   Idempotent — clearing an already-cleared row is a no-op.

`--retry-quarantine` and `--clear-quarantine` are mutually exclusive and
cannot combine with `--full` / `--pmids` / `--from-gap-scan` — they're
standalone operator modes.

## Backfill (#112 / onboarding)

The daily job is **watermark-forward-only**: it enriches `pmid >
last_max_pmid` and advances the watermark. It structurally cannot reach
*historical* publications — the ones a newly-onboarded researcher brings
from a prior institution (#80). Those PMIDs sit below the watermark
forever.

`run_daily_enrichment.py` has a second mode for exactly that set. With
`--pmids` or `--from-gap-scan` it invokes `run_enrichment_backfill`
instead of the daily job: synopsis + impact for an **explicit** PMID work
set, regardless of publication date. Same model path (Sonnet primary +
gpt-5.1 fallback), prompts, schema, and writes as the daily job —
`reciterai_synopsis` + `reciterai_impact` in MariaDB and an `IMPACT#` row
in DynamoDB per PMID — minus the watermark.

The Fargate task definition runs this mode via `command` override; the
operator can also run it locally for one-off ad-hoc sets.

### On Fargate (recommended for the #112 ~1,073-paper run)

```bash
# Preview: resolve the work set + cost estimate, no model calls.
aws ecs run-task --cluster reciterai-cluster \
  --task-definition reciterai-enrichment \
  --launch-type FARGATE \
  --network-configuration '<your subnets + SG>' \
  --overrides '{"containerOverrides":[{
    "name":"reciterai-enrichment",
    "command":["python","-m","scripts.run_daily_enrichment",
               "--from-gap-scan","--dry-run"]
  }]}'

# Then run it for real:
aws ecs run-task --cluster reciterai-cluster \
  --task-definition reciterai-enrichment \
  --launch-type FARGATE \
  --network-configuration '<your subnets + SG>' \
  --overrides '{"containerOverrides":[{
    "name":"reciterai-enrichment",
    "command":["python","-m","scripts.run_daily_enrichment",
               "--from-gap-scan","--verbose"]
  }]}'
```

At ~$0.0141/paper happy-path + a fallback lane, the #112 1,073-paper
backfill is ≈ $15 + the fallback share, and runs in a few hours on the
default Fargate sizing (0.25 vCPU / 0.5–1 GB — the job is I/O-bound).

### Locally (for ad-hoc PMID sets)

```bash
source ~/.zshrc

python3 -m scripts.run_daily_enrichment --from-gap-scan --dry-run
python3 -m scripts.run_daily_enrichment --from-gap-scan --verbose

# Or target an explicit PMID set:
python3 -m scripts.run_daily_enrichment --pmids 39001234,39005678
```

**Work-set sources** (mutually exclusive):

- `--from-gap-scan` — derive the set from the onboarding detector's
  faculty gap scan: every first/last-author full-time-faculty PMID with
  no synopsis. This is the #112 backfill entry point.
- `--pmids PMID,PMID,...` — an explicit comma-separated set.

**Idempotent.** Before generating anything, the backfill culls PMIDs that
already have *both* a synopsis and an impact row, so a re-run only does
the gaps. `--force` bypasses the cull and reprocesses every PMID — use it
to recover PMIDs left half-enriched by a failed run (every write is an
idempotent upsert, so reprocessing is safe).

**Cost.** The backfill is bootstrap-class: it surfaces a cost estimate
(`--dry-run` previews it) but enforces no threshold — an intentionally
large historical set is the point. `cost_observed_usd` in the result JSON
is the measured spend.

**Status.** The result JSON's `status` is `complete`, `partial` (some
PMIDs failed — the rest are committed; re-run to retry only the gaps),
`failed`, `no_op` (empty work set, or everything already enriched), or
`ddb_batch_failed` (MariaDB writes landed but the `IMPACT#` batch did not
— re-run with `--force`). Exit code is 0 for `complete` / `no_op`, 1
otherwise.

## Watermark + ops state

The watermark lives in DDB at `PK = WATERMARK#daily_enrichment` /
`SK = STATE`. Useful reads from `aws` CLI when debugging:

```bash
aws dynamodb get-item \
  --table-name reciterai \
  --key '{"PK":{"S":"WATERMARK#daily_enrichment"},"SK":{"S":"STATE"}}'
```

Fields of interest:
- `last_successful_max_pmid` — only advances on a clean run
- `last_run_status` — `complete | failed | in_progress`
- `last_run_started_at` — set every `mark_run_started` call
- `last_run_id` — UUID4, useful for cross-referencing in Teams alerts

## Deploying the enrichment job

Operator runbook for standing the Fargate deploy up (post-#37 PR 4
merge). Two cutovers are independent:

- **A**: redeploy the existing `reciterai-onboarding-enrich` Lambda so it
  picks up the Bedrock-rewired code path. Can run as soon as the rewire
  PRs (#129, #130) merge.
- **B**: bring up the Fargate task definition + cron rule. Requires PR 4.

### A. Cut the `reciterai-onboarding-enrich` Lambda over to Bedrock

The onboarding Enrich Lambda calls `run_enrichment_backfill` → the
synopsis/impact modules, so the Bedrock rewire reaches it transitively.
Until redeployed it runs its old zip — safe (Lambda execution is frozen
to its zip), but it should move to Bedrock for consistency.

1. Rebuild: `scripts/build_lambda_zips.sh reciterai-onboarding-enrich`.
   The zip still bundles `openai` — the content-filter fallback path
   needs it (#37 D3).
2. Redeploy:
   ```bash
   aws lambda update-function-code \
     --function-name reciterai-onboarding-enrich \
     --zip-file fileb://build/reciterai-onboarding-enrich.zip
   ```
3. **IAM:** no change needed — `infra/lambda_iam_policy.json` already
   grants `bedrock:InvokeModel` (the Lambda uses IAM-role auth for
   Bedrock, not the bearer token). The `reciterai-onboarding-enrich`
   Lambda keeps reading `reciterai/openai-api-key` via
   `utils.secrets_loader`'s auto-load — required for the content-filter
   fallback.
4. **Smoke:** an onboarding run on a small unscored CWID
   (`scripts/smoke_onboarding.sh SMOKE_EXPECT=complete`) exercises the
   Enrich stage on Bedrock.

### B. Deploy the daily enrichment job to Fargate

The infra lives in `infra/` alongside the hot path (#37 D6 — the
single-file IaC is extended once more; a full CDK migration remains its
own deferred Phase).

1. **ECR:** create the `reciterai-enrichment` repository; build the image
   from the repo's `Dockerfile`, tag, push:
   ```bash
   aws ecr create-repository --repository-name reciterai-enrichment
   docker build -t reciterai-enrichment:$(git rev-parse --short HEAD) .
   docker tag reciterai-enrichment:<sha> \
     <acct>.dkr.ecr.us-east-1.amazonaws.com/reciterai-enrichment:<sha>
   docker push <acct>.dkr.ecr.us-east-1.amazonaws.com/reciterai-enrichment:<sha>
   ```
2. **Bedrock API key secret (#37 D8):** create the Secrets Manager secret
   that the task definition's `secrets` block resolves to
   `AWS_BEARER_TOKEN_BEDROCK`. Must be a **long-term** Bedrock API key —
   short-term keys expire in ≤ 12 h and would break overnight ticks:
   ```bash
   aws secretsmanager create-secret \
     --name reciterai/bedrock-api-key \
     --secret-string "<long-term Bedrock API key>"
   ```
3. **Teams webhook secret (if it does not already exist):** the existing
   Lambda fleet reads `RECITERAI_TEAMS_WEBHOOK_URL` from env, not Secrets
   Manager (Phase 10 SECURITY note flagged this as a future migration).
   Fargate's `secrets` block does require a Secrets Manager source, so
   the deploy creates the secret if it does not already exist:
   ```bash
   aws secretsmanager describe-secret --secret-id reciterai/teams-webhook-url \
     >/dev/null 2>&1 \
     || aws secretsmanager create-secret \
          --name reciterai/teams-webhook-url \
          --secret-string "<RECITERAI_TEAMS_WEBHOOK_URL value>"
   ```
4. **IAM (out-of-band, the `infra/` directory creates no roles):** create
   the Fargate task role with `infra/enrichment_task_iam_policy.json`
   (covers DynamoDB write, Secrets Manager read on the four secrets, and
   CloudWatch Logs write — explicitly NO `bedrock:InvokeModel`, see plan
   D8 / PR-4 D-Q1), and the EventBridge→ECS `RunTask` invocation role.
5. **Networking:** confirm the subnets + security group reach ReciterDB
   — reuse the networking the hot-path Lambdas already use for MariaDB.
   Export them for the deploy script:
   ```bash
   export RECITERAI_ENRICHMENT_SUBNETS=subnet-...,subnet-...
   export RECITERAI_ENRICHMENT_SECURITY_GROUPS=sg-...
   ```
6. **Task definition:** register `infra/ecs_task_definition.json`
   (substitute the pushed image URI + role ARNs + secret ARNs in place
   of the `{...}` placeholders, then `aws ecs register-task-definition
   --cli-input-json file://infra/ecs_task_definition.rendered.json`).
7. **Cron:** `scripts/deploy_cron.sh --dry-run --rule
   reciterai-enrichment-daily`, inspect, then apply. The rule's target
   kind is `ecs`; `deploy_cron.sh` carries the new `ecs` branch (PR 4).
8. **Smoke:** run `scripts/smoke_enrichment.sh` — `SMOKE_EXPECT=dry-run`
   first (work-set + cost preview, no model calls), then
   `SMOKE_EXPECT=real ENRICHMENT_SMOKE_PMID=<a real unscored PMID>`.
   Confirm:
   - the task launches (image pull / IAM / network all wired),
   - the content-filter fallback fires cleanly when it triggers,
   - length enforcement on the synopsis 3-attempt loop holds,
   - MariaDB + `IMPACT#` writes land for the test PMID,
   - `cost_observed_usd` is in the spike-derived range — pin the
     cost-guard default down if measured spend differs materially.
9. **#112 backfill (#37 D5, forward-only):** run the task on-demand with
   `--from-gap-scan` — ~1,073 papers, Sonnet scores written for every
   `missing_either` PMID; existing gpt-5.1 scores from earlier POC runs
   stay in place untouched. `--dry-run` first to confirm the work-set
   size.
10. **Verify a scheduled tick:** confirm the first 11:00 UTC run writes
    a `complete`-status `RunResult` and advances the watermark.

The **annual rescore** (#37 D5, deferred) is enabled by this deploy — the
same task definition runs it on-demand via a different command override;
its scope and timing are a separate operator decision.

## Cost reconciliation

Done as of 2026-05-19 against the Bedrock spike
(`scripts/debug/bedrock_synopsis_impact_spike.py`, n=15 PMIDs, 30 calls).

| Metric | Sonnet 4.6 (Bedrock, current) | gpt-5.1 (OpenAI, prior) | Earlier modeling guess |
|---|---|---|---|
| Per-paper combined | **$0.0141** | $0.0094 (POC, n=100) | $0.060 (hand-wave) |
| 5–15 papers/day, daily | $0.07–$0.21/run | $0.05–$0.15/run | $0.30–$0.90/run |
| Annual at 5 papers/day × 250 days | **~$18/yr** | ~$12/yr | ~$430/yr |
| #112 backfill (~1,073 papers, forward-only) | **~$15** | n/a (POC) | n/a |
| Annual rescore (~6,200 papers, deferred) | **~$88** | ~$58 (Batch) / ~$117 (sync) | ~$110 (Batch) / ~$415 (sync) |

The Bedrock rate is ~50% higher than the gpt-5.1 measured rate — the
output-token differential (Sonnet writes terser justifications; both
input footprints are comparable) is dominated by the input price gap
($3/Mtok Sonnet vs. $1.25/Mtok gpt-5.1). At these prices the absolute
dollars are still small enough that Bedrock Batch's ~50% discount is not
worth the architectural overhead (two-process coordination, async
result-collection, in-flight state in DDB) — and Batch is incompatible
with the length-retry + content-filter-fallback loops anyway (#37 §4.2).

The fallback lane (Bedrock→gpt-5.1 on a content-filter block) adds a
gpt-5.1 call on top of the original Sonnet input-token spend, so a
filtered paper costs roughly $0.020 — pin this against measured spend
after PR 4's smoke run.

## History / closed follow-ups

The automation revisit (#46) was filed when this job ran from the
operator laptop and the trigger was "an org-managed OpenAI API key
becomes available." The Bedrock pivot (#37) supersedes that trigger —
Bedrock IAM auth + Fargate is the org-managed path. #46 closes on #37 PR
4's merge.

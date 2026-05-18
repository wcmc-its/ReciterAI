# Hot / Cold / Spotlight / Drift — operator guide

Phase 10 split the ReciterAI pipeline into four lanes with different
invocation models. This guide is the runbook: how each lane is
triggered, what to do when an alert fires, and how to recover from
common failures.

| Lane | Cadence | Trigger | Mints versions? | What it produces |
|---|---|---|---|---|
| **Hot path** | Mondays 12:00 UTC | EventBridge → Step Functions | No | Per-PMID topic scores, subtopic assignments, CWID rollups for new PMIDs since the last successful tick. |
| **Cold path** | Manual / on demand | Operator runs `python -m pipeline_cold.run` | **Yes** — taxonomy + hierarchy versions | Full pipeline mint: rescore, reassign, rediscover subtopics, relabel, full rollup, spotlight backfill, hierarchy publish. |
| **Spotlight refresh** | 1st of each month, 13:00 UTC | EventBridge → Lambda | No | Dirty-check gate; if dirty enough, regenerates the spotlight; otherwise writes a `skipped` STAGE# row. |
| **Drift evaluator** | Daily 14:00 UTC | EventBridge → Lambda | No | Writes one `DRIFT#evaluation` row; dispatches WARN/ERROR alerts. |
| **Onboarding** | Per-CWID, operator-triggered; detector daily 13:00 UTC | `aws stepfunctions start-execution` (workflow); EventBridge → Lambda (detector) | No | Full CWID-scoped backfill — score, subtopic-assign, top-topic, rollup for a researcher's whole accepted-publication set, regardless of publication date. The daily detector files GitHub issues for CWIDs needing a run. |

Cron schedules are defined in `infra/eventbridge.json` and applied by
`scripts/deploy_cron.sh`. All schedules are tunable in JSON without
touching code.

---

## Hot path — `pipeline_hot`

**Cadence**: weekly, Mondays 12:00 UTC (locked 2026-05-12 per Open Q
10.1; ReciterDB confirmed daily refresh).

**Invocation**: EventBridge rule `reciterai-hot-weekly` starts the
`reciterai-hot-path` Step Functions execution. No manual invocation is
required in normal operation.

**State machine** (`pipeline_hot/state_machine.asl.json`):

```
orchestrator → score → assign → rollup → write-hot-run-row → end
```

Each handler is an envelope-mode Lambda invocation of the matching
script (`score_publications.py`, `assign_subtopics.py`,
`rollup_by_cwid.py`). The state machine `Catch`-es every Task and
writes a `STAGE#…#failed` row plus a Notify state (alert dispatch).

**Locking** (Open Q 10.5): at start, the orchestrator does a
`ListExecutions` on the state machine. If any execution is `RUNNING`,
it writes a `STAGE#hot_run#GLOBAL` `skipped` row with
`skip_reason: "prior_run_in_progress"` and exits success. The next
weekly tick picks up where we left off; the WARN alert lets operators
notice if collisions are frequent.

**Manual invocation** (rarely needed; use cold path for systemic
issues):

```bash
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:us-east-1:$AWS_ACCOUNT_ID:stateMachine:reciterai-hot-path \
  --name "manual-$(date -u +%Y%m%dT%H%M%S)" \
  --input '{"initiated_by":"operator"}'
```

**Verifying a hot run**:

```bash
aws dynamodb query \
  --table-name reciterai \
  --key-condition-expression "PK = :pk" \
  --expression-attribute-values '{":pk":{"S":"STAGE#hot_run#GLOBAL"}}' \
  --no-scan-index-forward --limit 5
```

The top row is the latest tick; `status == complete` confirms success.

---

## Cold path — `pipeline_cold.run`

**Cadence**: on demand. Triggered manually by an operator when:

- A drift alert recommends it (`cold_run_recommended: true` in the
  latest `DRIFT#evaluation` row, **or** an ERROR-severity Slack/issue
  fires for `uncovered_rate_alert` or `low_confidence_topic_max`).
- The taxonomy is intentionally evolved (new topics added, splits/merges).
- A model upgrade (a Bedrock model ID changes in
  `MODEL_IDS_BY_STAGE`) requires a full rescore.

**Invocation**:

```bash
# Full mint:
python -m pipeline_cold.run --initiated-by drift_alert

# Dry-run (lists the seven stages without invoking them):
python -m pipeline_cold.run --dry-run

# Resume from a specific stage:
python -m pipeline_cold.run --from-stage rollup_by_cwid
```

**Stages** (sequential):

1. `score_publications` — full rescore of all in-scope PMIDs.
2. `assign_subtopics` — full subtopic reassignment.
3. `discover_subtopics` — propose new subtopics from clustering.
4. `relabel_subtopics` — refine subtopic labels.
5. `rollup_by_cwid` — full faculty rollup.
6. `backfill_spotlight` — regenerate spotlight from scratch.
7. `publish_hierarchy` — mint new hierarchy version + version-stamped artifacts.

Each stage writes its own STAGE# row via the direct-write path (no
envelope/handoff — cold path runs in a single Python process).

`--initiated-by` must be one of `operator`, `drift_alert`, `scheduled`
(spec §9 — the drift evaluator surfaces *recommended*, but the
operator decides whether to run).

**Important**: the cold path mints `hierarchy_version` and
`taxonomy_version`. The hot path explicitly does NOT. Downstream
consumers (SPS, dashboards) pin to version strings; the cold-path mint
is what gives them a new pin to adopt.

---

## Spotlight refresh — `pipeline_spotlight`

**Cadence**: monthly, 1st of the month at 13:00 UTC.

**Invocation**: EventBridge rule `reciterai-spotlight-monthly` invokes
the `reciterai-spotlight-orchestrator` Lambda.

**Gate** (per D-03): the orchestrator queries STAGE# rows since the
last `STAGE#spotlight_refresh#GLOBAL` `complete`, builds dirty-subtopic
counts by joining new-PMID classifications against the top-50 from
`spotlight/pool_ranker.py`, and applies the thresholds from
`config/thresholds.json`:

```
spotlight_dirty_subtopic_min       = 3   (D-03)
spotlight_dirty_pubs_per_subtopic_min = 5   (D-03)
```

If fewer than 3 subtopics each accumulate at least 5 new in-window
pubs, the orchestrator writes a `skipped` STAGE# row and exits. At or
above threshold, it invokes `backfill_spotlight.py --publish`.

**Manual invocation** (force a regen regardless of dirty-check):

```bash
aws lambda invoke \
  --function-name reciterai-spotlight-orchestrator \
  --payload '{"initiated_by":"operator","force":true}' \
  /tmp/spotlight-response.json
```

The `force: true` flag bypasses the dirty-check gate. Use sparingly —
the gate exists because Opus reads on the spotlight pipeline aren't
free.

---

## Drift evaluator — `pipeline_drift`

**Cadence**: daily, 14:00 UTC.

**Invocation**: EventBridge rule `reciterai-drift-daily` invokes the
`reciterai-drift-evaluator` Lambda.

**What it does**:

1. Reads UNCOVERED_PMID#, LOW_CONFIDENCE_ASSIGNMENT#, and
   STAGE#…failed rows over `config.drift_window_days` (default 14).
2. Computes `uncovered_rate`, `low_confidence_max_topic`,
   `low_confidence_max_count`, `triggered_thresholds[]`.
3. Writes one `DRIFT#evaluation` row keyed on the window-end day.
4. Dispatches a severity-tagged alert via `pipeline_common.alert.dispatch`:

   - **OK**: no alert.
   - **WARN**: Slack only (`#reciterai-pipeline`).
   - **ERROR**: Slack + GitHub issue (creates a new `drift-alert`
     issue, or comments on the existing open one to dedupe).

See `docs/severity.md` for the complete D-11 severity table.

### Drift-alert response runbook

When a Slack `[ERROR]` lands or a `drift-alert` GitHub issue is opened
or commented on:

1. **Read the DRIFT# row.** Pull the most recent `DRIFT#evaluation`:

   ```bash
   aws dynamodb query --table-name reciterai \
     --key-condition-expression "PK = :pk" \
     --expression-attribute-values '{":pk":{"S":"DRIFT#evaluation"}}' \
     --no-scan-index-forward --limit 1
   ```

   Inspect `triggered_thresholds`, `uncovered_rate`,
   `low_confidence_max_topic`, `cold_run_recommended`.

2. **Investigate the offending events.** For `uncovered_rate_alert`,
   look at the UNCOVERED_PMID# rows over the window:

   ```bash
   aws dynamodb scan --table-name reciterai \
     --filter-expression "begins_with(PK, :p) AND created_at >= :since" \
     --expression-attribute-values \
       '{":p":{"S":"UNCOVERED_PMID#"},":since":{"S":"2026-04-28T00:00:00Z"}}' \
     --projection-expression "pmid,top_topics,top_topic_score"
   ```

   Aggregate `top_topics[].topic_id` to find the topics most commonly
   "almost matching" — these are candidates for taxonomy expansion.

   For `low_confidence_topic_max`, focus on the topic named in
   `low_confidence_max_topic`. Likely needs a subtopic split or merge.

3. **Decide whether to cold-run.** ERROR with `cold_run_recommended:
   true` is the system's recommendation, not a directive. If the
   underlying corpus shift looks transient (one bad PubMed batch), it
   may resolve on its own next week. If it looks structural (taxonomy
   genuinely doesn't cover an emerging area), schedule the cold path.

4. **Cold-run**:

   ```bash
   python -m pipeline_cold.run --initiated-by drift_alert
   ```

   The `--initiated-by drift_alert` flag is the audit-trail link
   between the DRIFT# row and the cold-path STAGE# rows it produced.

5. **Close the GitHub issue** once the underlying cause is addressed
   (cold path completed, taxonomy bumped, or transient signal cleared).
   Daily evaluator runs will create a new issue if the condition fires
   again; until then, repeated ERROR-severity fires comment on the
   open issue rather than spawning duplicates.

### Threshold tuning

Tunable in `config/thresholds.json` (no redeploy needed; Lambda reads
on cold start):

```json
{
  "drift_uncovered_rate_alert": 0.05,       // ERROR floor
  "drift_low_confidence_topic_max": 50,     // ERROR floor
  "drift_window_days": 14
}
```

The WARN-band floors live in
`pipeline_drift.severity.classify_drift_evaluation` (defaults 3% and
30). Revisit after the first quarter of production drift data; the
defaults are conservative.

---

## Onboarding — `pipeline_onboarding`

**Cadence**: operator-triggered, per CWID, on demand. A daily detector
(13:00 UTC) finds CWIDs that need a run and files a GitHub issue for each.

**Why it exists**: the hot path is a *delta* over publication recency, so a
researcher who joins WCM with a substantial prior publication history has
most of those papers fall outside every weekly delta window. Onboarding is
the CWID-scoped backfill that closes the gap — it scores, subtopic-assigns,
top-topics, and rolls up one researcher's *whole* accepted-publication set
(Academic Article, `articleYear >= 2020`) regardless of date.

**Invocation**: start the `reciterai-onboarding` Step Functions execution
for one CWID (substitute the CWID):

```bash
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:us-east-1:$AWS_ACCOUNT_ID:stateMachine:reciterai-onboarding \
  --name "onboarding-abc1234-$(date -u +%Y%m%dT%H%M%SZ)" \
  --input '{"cwid":"abc1234","allow_cost_override":false}'
```

The daily detector embeds this command, pre-filled, in every issue it files.

**State machine** (`pipeline_onboarding/state_machine.asl.json`):

```
Orchestrate → CheckProceed ─(deferred|skipped|cost_exceeded)→ terminal row
                          └(ready)→ Score → DeriveDirtyTopics
                                  → AssignFanOut (Map, one iteration per topic)
                                  → TopTopic → Rollup → Finalize
```

`Orchestrate` scopes the CWID's accepted PMID set, checks the synopsis
precondition, culls the `PROCESSING#` checkpoint, and applies the cost
guard. The `ready` cascade reuses the four hot per-stage Lambdas
(`reciterai-hot-score / -assign / -top-topic / -rollup`); the Assign stage
fans `-assign` out per topic via a Step Functions `Map`. Every Lambda Task
`Catch`-es to a terminal `failed`-row writer (D-07 crash-safety).

**5-state terminal status** (R7): every run writes exactly one
`STAGE#onboarding#cwid:{cwid}` row whose `status` is one of —

| Status | Meaning | Operator action |
|---|---|---|
| `complete` | All PMIDs scored; the full cascade ran. | None. |
| `partial` | The cascade ran; some PMIDs did not reach a scored state. | Re-run — onboarding retries only the gaps (idempotent). A WARN Teams alert fires. |
| `deferred` | One or more PMIDs lack a synopsis; onboarding does not generate synopses. | Re-run after the enrichment job backfills synopses (#92). |
| `skipped` | Nothing to do — no accepted publications, or all already scored. | None. |
| `failed` | A Lambda/Task crashed, **or** the cost guard tripped (`error_code=CostGuardExceeded`). | Inspect; for a cost-guard trip, re-run with `allow_cost_override`. |

**Cost guard**: a run whose net scoring work exceeds
`onboarding_cost_guard_max_pmids` (300, in `config/thresholds.json`) with no
override terminates `failed` / `error_code=CostGuardExceeded` — nothing is
broken; re-run with `{"cwid":"...","allow_cost_override":true}`. The
orchestrator posts a Teams cost preview before any model call.

**Verifying a run**: query the workflow row, matched on `run_id` (the
execution name) — never the highest SK, since a `failed` row's
`RUN#FAILED#...` SK sorts above a clean `RUN#{iso}` row:

```bash
aws dynamodb query --table-name reciterai \
  --key-condition-expression "PK = :pk" \
  --filter-expression "run_id = :rid" \
  --expression-attribute-values \
    '{":pk":{"S":"STAGE#onboarding#cwid:abc1234"},":rid":{"S":"<run_id>"}}'
```

`scripts/smoke_onboarding.sh` is the scripted version of this check.

### Onboarding detector

**Cadence**: daily 13:00 UTC — EventBridge `reciterai-onboarding-detector-daily`
→ Lambda `reciterai-onboarding-detector`.

A faculty-scoped scan: for every full-time-faculty CWID it compares the
accepted publication set against what is synopsis-covered and scored, and
against the last CWID-scoped rollup's `input_pmid_set` (ReCiter attribution
churn, R9). Each flagged CWID gets a GitHub issue (created or refreshed,
labelled `onboarding`) carrying the gap detail + the pre-filled
`start-execution` command; a Teams digest summarises the run. It writes one
`STAGE#onboarding_detector#GLOBAL` row per run. It does **not** trigger the
state machine — issue-filing only (D1).

**Cold-start guard (#106).** When a run flags more CWIDs than the
`onboarding_detector_cold_start_threshold` in `config/thresholds.json`
(default 100), per-CWID issue filing is suppressed and a single
`[onboarding] Detector backlog digest` issue is filed instead — a cold-start
backlog (onboarding has not yet run for most faculty) cannot flood the repo
with hundreds of issues. Per-CWID filing resumes automatically once the
flagged count drops back below the threshold, at which point the digest issue
is marked cleared. The run's `STAGE#` row carries `cold_start_mode`.

A manual dry scan (scan + STAGE# row + Teams digest, no GitHub writes):

```bash
aws lambda invoke --function-name reciterai-onboarding-detector \
  --payload '{"file_issues":false}' /tmp/detector.json
```

### Deploying onboarding

Onboarding declares its infra in `infra/` alongside the hot path (D-INFRA).
The deploy is operator-run, in order.

**Gate 0 — verify the hot path is live *and* configured.** Onboarding
reuses the four hot per-stage Lambda ARNs; existence alone is not enough —
they are invoked by the onboarding state machine, so their env-var state
must be sound.

```bash
for fn in reciterai-hot-score reciterai-hot-assign reciterai-hot-top-topic reciterai-hot-rollup; do
  aws lambda get-function-configuration --function-name "$fn" \
    --query '[FunctionName,Runtime,Environment.Variables]'
done
aws stepfunctions describe-state-machine \
  --state-machine-arn arn:aws:states:us-east-1:$AWS_ACCOUNT_ID:stateMachine:reciterai-hot-path
```

Any miss → stop and resolve before proceeding.

1. **Build the zips** — `scripts/build_lambda_zips.sh` (builds all 10; note each `du -h`).
2. **Create the 5 onboarding Lambda functions** — `python3.12`, `x86_64`,
   the Lambda execution role with `lambda_iam_policy.json` attached:

   | Function | Zip | `--handler` |
   |---|---|---|
   | `reciterai-onboarding-orchestrator` | `reciterai-onboarding-orchestrator.zip` | `pipeline_onboarding.orchestrator.handler` |
   | `reciterai-onboarding-finalize` | `reciterai-onboarding-finalize.zip` | `pipeline_onboarding.finalize.handler` |
   | `reciterai-onboarding-notify` | `reciterai-onboarding-finalize.zip` *(same zip)* | `pipeline_onboarding.finalize.notify_handler` |
   | `reciterai-onboarding-detector` | `reciterai-onboarding-detector.zip` | `pipeline_onboarding.detector.handler` |
   | `reciterai-onboarding-derive-topics` | `reciterai-onboarding-derive-topics.zip` | `pipeline_onboarding.assign_fanout.handler` |

   Env vars: orchestrator / finalize / notify / detector each take
   `RECITERAI_TEAMS_WEBHOOK_URL` (best-effort alerting — absent = alerts
   silently skipped, not broken); notify + detector take
   `RECITERAI_ONBOARDING_STATE_MACHINE_ARN`; orchestrator + detector
   auto-load DB creds via `utils.secrets_loader`. `derive-topics` takes none.
3. **Provision the GitHub secret** — a fine-grained PAT scoped to
   `wcmc-its/ReciterAI`, Issues read+write only:
   `aws secretsmanager create-secret --name reciterai/github-token --secret-string "<PAT>"`.
4. **Redeploy `reciterai-hot-assign`** — PR 4 taught `build_lambda_zips.sh`
   to bundle the 66 hierarchy drafts; the live function predates that, and
   the onboarding Assign `Map` invokes it. Determine the live hierarchy
   version (the latest `STAGE#hierarchy_version_cutover` row, or
   `hierarchy_version` on a recent live `TOPIC#` row), confirm it matches
   the bundled drafts, then:
   ```bash
   aws lambda update-function-code --function-name reciterai-hot-assign \
     --zip-file fileb://build/reciterai-hot-assign.zip
   aws lambda update-function-configuration --function-name reciterai-hot-assign \
     --environment "Variables={RECITERAI_HIERARCHY_VERSION=<version>,...existing...}"
   ```
   Additive for the hot path (its `CheckAssignNeeded` keeps the always-empty
   `assign_topics` on the `AssignSkipped` branch — it never reaches the
   assign Lambda today). Re-run `scripts/smoke_hot_path.sh` after.
5. **Deploy the state machine** — export the 8 ARN env vars +
   `STATE_MACHINE_ROLE_ARN`, run `scripts/deploy_onboarding_state_machine.sh`
   (`--dry-run` first).
6. **Deploy the cron rule** —
   `scripts/deploy_cron.sh --rule reciterai-onboarding-detector-daily`.
7. **Smoke** — `scripts/smoke_onboarding.sh` with `SMOKE_EXPECT=skipped`
   (a zero-cost plumbing check on an already-scored CWID), then
   `SMOKE_EXPECT=complete` on a small unscored CWID — the real cascade, and
   the gate that closes Phase 2. Then a `file_issues:false` detector invoke.

**Rollback**: diagnose first (`get-execution-history` / CloudWatch logs).
Teardown order: disable `reciterai-onboarding-detector-daily` → delete the
state machine → delete the 5 Lambdas → delete the secret. The hot path needs
no rollback — onboarding is all-new resources except the additive
`reciterai-hot-assign` redeploy, which can be left in place.

---

## Related references

- `infra/eventbridge.json` — cron rules + targets.
- `infra/lambda_iam_policy.json` — minimum-privilege policy.
- `infra/README.md` — D-10 single-file IaC + CDK migration trigger.
- `docs/severity.md` — D-11 alert severity table.
- `docs/data-model-and-queries.md` — full DynamoDB record-type reference.
- `docs/stage-records-and-gates.md` — Phase 9 STAGE# substrate this phase builds on.
- `pipeline_hot/state_machine.asl.json` — the hot-path Step Functions definition.
- `pipeline_onboarding/state_machine.asl.json` — the onboarding Step Functions definition.

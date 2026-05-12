# Phase 10 — Hot/Cold Path Split: PLAN

**Milestone:** M2 — SPS-feeding service
**Spec source:** [docs/RECITERAI-SPEC.md §2 (Decision 1)](../../../docs/RECITERAI-SPEC.md#2-decision-1--two-paths-not-one)
**Context:** [10-CONTEXT.md](10-CONTEXT.md)
**Estimated effort:** 8–12 working days
**Status:** Plan — awaiting approval

## Goal

Split the conflated single pipeline into four independent jobs (hot, cold, spotlight, drift) per CONTEXT D-01 through D-04. Wire Phase 9's `STAGE#` substrate into the upstream pipeline stages with envelope-vs-direct write semantics per D-07. Ship the hot path on AWS Step Functions, the cold path as `python -m pipeline_cold.run`, spotlight as a standalone monthly Lambda with a dirty-check gate, and drift as a daily Lambda emitting severity-tagged alerts.

## Non-goals (per CONTEXT)

- `hierarchy_version` stamping on activity records — Phase 11
- `REVIEW#` records / cold-path human gates — Phase 11
- `diff.json` + write-order contract — Phase 11
- Critic-reject feedback events — Phase 12
- Both aggregations (exclusive + inclusive) — Phase 12
- Feedback-event *consumption* (UNCOVERED_PMID → Sonnet sweep) — Phase 12
- Full CDK adoption — triggered by D-10 threshold
- Dashboard for STAGE# / DRIFT# — deferred to operator load

## Open-question resolutions (provisional — flag in execution if you object)

| Q | CONTEXT § | Decision | Override-by-when |
|---|---|---|---|
| Hot-path cron expression | §Open 1 | **Locked 2026-05-12**: `cron(0 12 ? * MON *)` (Mondays 12:00 UTC = 08:00 EDT). ReciterDB confirmed daily-refresh; chose Monday 12:00 UTC to land after a typical overnight refresh window and inside US business hours for pager response. | n/a (locked) |
| Slack channel | §Open 2 | `#reciterai-pipeline` (dedicated) | Before T12 |
| Slack env var | §Open 2 | `RECITERAI_SLACK_WEBHOOK_URL` (disambiguated) | Before T12 |
| Bedrock Batch wait | §Open 3 | Wait + Choice poll loop (simpler; revisit on cost) | Before T14 |
| Bedrock Batch lock | §Open 5 | Prior-run-in-progress → write `STAGE#hot_run#GLOBAL` `skipped` (`skip_reason: "prior_run_in_progress"`) + WARN to Slack | Before T7 |
| Severity table (D-11) | §Open 4 | Drafted in T12 task body; reviewable file diff | During T12 |

## Approach

### Files (new)

```
pipeline_common/
├── __init__.py
├── dirty_set.py            # CWID + subtopic dirty-set computation from STAGE# queries
├── envelope.py             # Build STAGE# row envelopes for state-machine consumption
└── alert.py                # Slack + GitHub-issue dispatcher with WARN/ERROR severity

pipeline_hot/
├── __init__.py
├── orchestrator.py         # Entry: queries last STAGE#hot_run, builds delta PMID set
├── handlers/
│   ├── score.py            # Lambda handler: invokes score_publications in delta mode
│   ├── assign.py           # Lambda handler: invokes assign_subtopics in delta mode
│   └── rollup.py           # Lambda handler: invokes rollup_by_cwid in incremental mode
└── state_machine.asl.json  # Step Functions ASL definition (read by T13 deploy script)

pipeline_cold/
├── __init__.py
└── run.py                  # python -m pipeline_cold.run — operator CLI, mirrors backfill_all.py shape

pipeline_spotlight/
├── __init__.py
├── orchestrator.py         # Monthly Lambda handler: dirty-check gate → conditional regen
└── dirty_gate.py           # Reads STAGE# rows since last spotlight, applies D-03 threshold

pipeline_drift/
├── __init__.py
├── evaluator.py            # Daily Lambda handler: writes DRIFT#evaluation
├── severity.py             # WARN/ERROR condition → severity mapping (D-11)
└── severity.md             # Human-readable severity table (paired with severity.py)

infra/
├── eventbridge.json        # Cron rule defs: hot-weekly, spotlight-monthly, drift-daily
├── step_functions_hot.asl.json  # Pointer to pipeline_hot/state_machine.asl.json
├── lambda_iam_policy.json  # Min IAM for cron lambdas (DynamoDB, S3, Bedrock invoke)
└── README.md               # Includes the D-10 migration-to-CDK trigger

scripts/
└── deploy_cron.sh          # aws events put-rule + lambda update-function-code wrapper

tests/
├── test_stage_records_split.py      # build_complete_record purity, cost_observed_usd rename
├── test_dirty_set.py                # CWID + subtopic dirty-set computation
├── test_envelope.py                 # state-machine envelope shape
├── test_alert_dispatcher.py         # WARN→Slack, ERROR→Slack+GH issue
├── test_pipeline_hot_orchestrator.py
├── test_pipeline_cold_run.py
├── test_pipeline_spotlight_dirty_gate.py
├── test_pipeline_drift_evaluator.py
├── test_rollup_incremental_parity.py # Critical: parity vs full re-rollup
├── test_uncovered_pmid_event.py      # event record writes
└── test_low_confidence_event.py

docs/
├── hot-cold-paths.md       # Operator guide: invocation, cadence, alert response
└── data-model-and-queries.md # Extended with UNCOVERED_PMID#, LOW_CONFIDENCE_ASSIGNMENT#, DRIFT#, STAGE#hot_run
```

### Files (modified)

```
utils/stage_records.py
  - Split write_complete into build_complete_record (pure) + write_complete (writer)
    so state-machine SDK integration can write rows; cold-path Python still writes directly
  - Rename cost_estimate_usd → cost_observed_usd across all three write_* functions
    + SKIP_COST_USD constant (Decimal("0") for skips per D-09)
  - Same rename in write_skipped: cost_observed_usd: Decimal("0"), never omit

score_publications.py
  - Add --delta-since flag (timestamp); resolves new PMIDs since timestamp via ReciterDB query
  - Wrap main with stage_records.should_skip check; envelope-mode return when --emit-envelope
  - On UNCOVERED_PMID (top_topic_score < 0.4): write UNCOVERED_PMID# event record
  - On Bedrock parse-fail per PMID: write STAGE#score_publications# failed row with reason

assign_subtopics.py
  - Add --delta-pmids flag (comma-separated, or @file)
  - Wrap with should_skip + envelope-mode return
  - On LOW_CONFIDENCE_ASSIGNMENT (all candidate confidences < floor): write event record

rollup_by_cwid.py
  - Add --cwids flag (incremental mode) + --emit-envelope
  - Read existing cwid_rollup.csv when --cwids set; overwrite only the listed CWID rows;
    write merged output. Full re-run path unchanged when --cwids absent
  - Parity test (test_rollup_incremental_parity.py): full == incremental(dirty) merged with prior(undirty)

backfill_spotlight.py
  - Add --emit-envelope flag for state-machine consumption (no behavior change otherwise;
    spotlight runs in its own Lambda, not under Step Functions, but uniform envelope API
    keeps the substrate consistent)

config/thresholds.json (new file)
  - uncovered_score_floor: 0.4
  - low_confidence_floor: 0.35
  - drift_uncovered_rate_alert: 0.05
  - drift_low_confidence_topic_max: 50
  - drift_window_days: 14
  - spotlight_dirty_subtopic_min: 3
  - spotlight_dirty_pubs_per_subtopic_min: 5

docs/RECITERAI-SPEC.md §0 status table
  - Phase 10 row → complete on landing
```

## Task plan

11 tasks across 4 waves. One commit per task. Waves are dependency boundaries; within a wave tasks can be parallelized via `gsd-execute-phase` multi-plan mode if useful.

### Wave 1 — Substrate refactor (sequential; blocks everything)

**T1. Split `utils.stage_records` build-vs-write per D-07.**
Extract `build_complete_record(...) -> dict` (pure) from `write_complete`; same for `build_skipped_record`, `build_failed_record`. Existing `write_*` functions delegate to `build_* + dynamodb.put_item`. All Phase 9 callers continue to work unchanged.
*Tests*: `test_stage_records_split.py` — builder returns canonical dict matching DynamoDB shape; writer composes from builder; existing Phase 9 tests pass without modification.

**T2. Rename `cost_estimate_usd` → `cost_observed_usd` everywhere.**
Substrate (`utils/stage_records.py`), writers, tests, docs (`docs/data-model-and-queries.md`, `docs/stage-records-and-gates.md`). Skipped rows populate `cost_observed_usd: Decimal("0")` (was already 0 via `SKIP_COST_USD`; the rename + non-omission contract is the explicit change). One-off migration script `scripts/migrate_cost_field.py` rewrites existing prod rows; idempotent (skips already-renamed rows).
*Acceptance*: `grep -r cost_estimate_usd` → empty across code + docs; prod migration dry-run reports row count; existing Phase 9 `publish_hierarchy` STAGE# rows readable post-migration.

### Wave 2 — Stage instrumentation (parallel within wave)

**T3. Wire `STAGE#` substrate into `score_publications.py`.**
Add `--delta-since <iso8601>` flag and `--emit-envelope` mode. Compute `input_hash = (taxonomy_version, pmid_set_hash, MODEL_IDS_BY_STAGE["screening"], MODEL_IDS_BY_STAGE["scoring"])`. Skip behavior: when `should_skip` matches, emit skipped envelope and exit. Failed-row behavior: per-PMID Bedrock parse fail writes `STAGE#score_publications#pmid:{pmid}` `failed` row (scope = `pmid:*` lets the next run hash-match and skip the per-PMID record on retry).
*Tests*: mocked Bedrock; verify input_hash determinism; verify envelope shape matches `build_complete_record`.

**T4. Wire `STAGE#` substrate into `assign_subtopics.py`.**
Add `--delta-pmids` (comma-list or `@file`) and `--emit-envelope`. `input_hash = (hierarchy_version, pmid_set_hash, MODEL_IDS_BY_STAGE["subtopic_assignment"])`. Same envelope contract as T3. (Resume-via-`PROCESSING#pmid_*` sentinels remains the per-PMID retry mechanism; STAGE# is the stage-level memoization.)

**T5. Add incremental mode to `rollup_by_cwid.py` + parity test.**
`--cwids cwid1,cwid2,...` (or `@file`) reads existing `cwid_rollup.csv`, recomputes ONLY listed rows from the breakdown CSVs filtered to those CWIDs, merges into prior output. `input_hash = (score_version, hierarchy_version, sorted_cwid_tuple)`. Parity test (`test_rollup_incremental_parity.py`): for an N-CWID corpus, `full_rollup(N) == incremental_rollup(dirty_subset) ⊕ prior_rollup(N \ dirty_subset)` — byte-identical CSVs after sort.
*Acceptance*: parity test is GREEN; failing parity blocks the phase.

**T6. Add UNCOVERED_PMID# and LOW_CONFIDENCE_ASSIGNMENT# event writers.**
In `score_publications.py`: when top_topic_score < `config.uncovered_score_floor`, write `UNCOVERED_PMID#{pmid}` record with top-3 closest topics + scores (per spec §9). In `assign_subtopics.py`: when all candidate confidences < `config.low_confidence_floor`, write `LOW_CONFIDENCE_ASSIGNMENT#{pmid}` with the candidate confidences. Both writers are idempotent (PK = constant per PMID; overwrite OK).
*Tests*: `test_uncovered_pmid_event.py`, `test_low_confidence_event.py` — synthetic Bedrock outputs trigger the right event types.

### Wave 3 — Orchestrators (parallel within wave; depends on Wave 2)

**T7. `pipeline_hot/` package + state machine.**
`orchestrator.py` resolves `last_successful_hot_run_at` from `STAGE#hot_run#GLOBAL` per D-06, queries ReciterDB for new PMIDs, returns the PMID set as JSON for the state machine input. Handlers in `handlers/` are Lambda entry-points: each loads its envelope-mode upstream script (T3/T4/T5), invokes it, returns the envelope dict. `state_machine.asl.json` defines: Start → Score → Assign → Rollup → WriteHotRunRow → End, with Catch on each Task state writing a `failed` STAGE# row and routing to a Notify state (T12 dispatcher). Locking per Open Q5: ListExecutions check at Start; if any RUNNING, write `STAGE#hot_run#GLOBAL` `skipped` with `skip_reason: "prior_run_in_progress"` and exit success.
*Tests*: `test_pipeline_hot_orchestrator.py` — mocked ReciterDB + STAGE# queries; verify delta resolution + envelope handoff.

**T8. `pipeline_cold/run.py` operator CLI.**
Argparse mirrors `backfill_all.py` shape (see CONTEXT canonical refs). Subcommands: `--full` (default), `--from-stage <name>` (resume from a specific stage), `--dry-run`. Walks the cold stage list (per CONTEXT stage-assignment table): score → assign → discover → relabel → rollup → backfill_spotlight → publish_hierarchy. Each stage uses direct STAGE# write (no envelope mode — cold path runs in single Python process). `initiated_by ∈ {"operator","drift_alert","scheduled"}` populated from `--initiated-by` flag (default `"operator"`).
*Tests*: `test_pipeline_cold_run.py` — mocked stages; verify direct-write STAGE# rows and initiated_by propagation.

**T9. `pipeline_spotlight/` package + dirty-check gate.**
`orchestrator.py` is the monthly Lambda handler. `dirty_gate.py` queries STAGE# rows since last `STAGE#spotlight_refresh#GLOBAL` `complete`, builds dirty-subtopic counts by joining new-PMID classifications against `spotlight/pool_ranker.py`'s top-50, applies thresholds from `config.spotlight_dirty_subtopic_min` × `config.spotlight_dirty_pubs_per_subtopic_min` (D-03 = 3 × 5). Below threshold → write skipped row + exit. At/above → invoke `backfill_spotlight.py --publish`.
*Tests*: `test_pipeline_spotlight_dirty_gate.py` — synthetic STAGE# corpus; verify threshold gating both branches.

**T10. `pipeline_drift/` package + DRIFT# writer.**
`evaluator.py` is the daily Lambda handler. Queries `UNCOVERED_PMID#*` + `LOW_CONFIDENCE_ASSIGNMENT#*` + `STAGE#*#failed` rows over `config.drift_window_days` (14). Computes uncovered_rate, low_confidence_max_topic, low_confidence_max_count. Writes `DRIFT#evaluation` record with `triggered_thresholds[]`. On `cold_run_recommended: true`, calls `pipeline_common.alert.dispatch(severity="ERROR", message=..., open_issue=True)`.
*Tests*: `test_pipeline_drift_evaluator.py` — synthetic event corpus across all threshold conditions.

### Wave 4 — Alerting, IaC, docs (sequential at the end)

**T11. `pipeline_common.alert` dispatcher + severity table.**
`alert.py` exposes `dispatch(severity: Literal["WARN","ERROR"], message: str, context: dict, open_issue: bool = False)`. WARN → Slack only (POST to `RECITERAI_SLACK_WEBHOOK_URL`). ERROR → Slack + `gh issue create` (or comment on existing open issue with `drift-alert` label). `pipeline_drift/severity.py` maps each condition to a severity per the draft table below; `severity.md` is the human-readable companion.

Draft severity table (T12 finalizes via code review):

| Condition | Severity |
|---|---|
| Bedrock single-PMID parse fail | WARN (retried next run) |
| Bedrock throttle (recovered via retry) | WARN |
| uncovered_PMID 14d-rate 3–5% | WARN |
| uncovered_PMID 14d-rate >5% | ERROR (`cold_run_recommended: true`) |
| low_confidence count per topic 30–50 | WARN |
| low_confidence count per topic >50 | ERROR (`cold_run_recommended: true`) |
| Bedrock service outage halting hot path | ERROR |
| Schema-validation failure on publish | ERROR (paired with Phase 9 block gate) |
| STAGE# `failed` with retry exhausted | ERROR |
| Hot-path lock collision (prior run still running) | WARN |

*Tests*: `test_alert_dispatcher.py` — mocked Slack webhook + gh CLI; verify routing per severity.

**T12. IaC: EventBridge rules + Lambda deploy script per D-10.**
`infra/eventbridge.json` defines three cron rules (hot-weekly, spotlight-monthly, drift-daily). `infra/lambda_iam_policy.json` documents minimum permissions (DynamoDB RW on `reciterai-chatbot`, S3 RW on `wcmc-reciterai-*`, `bedrock:InvokeModel`, `bedrock:CreateModelInvocationJob`, `states:StartExecution`, `events:PutEvents`). `scripts/deploy_cron.sh` is a thin bash wrapper around `aws events put-rule` + `aws lambda update-function-code` + `aws iam attach-role-policy`. `infra/README.md` records the D-10 migration-to-CDK trigger explicitly: *"Adopt CDK when ReciterAI adds any of: second Step Function, second cron, fifth managed AWS resource."*

Hot-path cron resolution: if ReciterDB refresh-cadence answer arrives before T12, encode it. Otherwise default `cron(0 7 ? * MON *)` (Mondays 07:00 UTC). Comment in `infra/eventbridge.json` records the assumption + override location.

*Acceptance*: `scripts/deploy_cron.sh --dry-run` prints the AWS CLI calls without invoking them; smoke `aws events list-rules` post-deploy shows three rules.

**T13. Docs: `hot-cold-paths.md` + extend `data-model-and-queries.md`.**
`docs/hot-cold-paths.md`: operator guide covering hot-path invocation (auto), cold-path invocation (`python -m pipeline_cold.run`), spotlight manual trigger (`aws lambda invoke …`), drift alert response runbook (read DRIFT# row → decide cold-run schedule → invoke cold path with `--initiated-by drift_alert`). Extend `docs/data-model-and-queries.md` with the four new record types: `STAGE#hot_run#GLOBAL`, `UNCOVERED_PMID#{pmid}`, `LOW_CONFIDENCE_ASSIGNMENT#{pmid}`, `DRIFT#evaluation`.

**T14. Wire Step Functions ASL into deployment + smoke test.**
`scripts/deploy_state_machine.sh` registers `pipeline_hot/state_machine.asl.json` via `aws stepfunctions update-state-machine`. Smoke test invocation against a sandbox table: trigger via `aws stepfunctions start-execution` with a tiny synthetic PMID set; verify all three Task states succeed and a `STAGE#hot_run#GLOBAL` `complete` row appears.

## Verification (goal-backward)

Phase delivers what spec §2 promises iff:

1. **Hot path runs autonomously on a cron, processes deltas, never mints versions.** Verify: trigger an EventBridge run on the sandbox; observe `STAGE#hot_run#GLOBAL` `complete` row + `STAGE#{score,assign,rollup}#*` rows; verify no `hierarchy_version` or `taxonomy_version` changes occurred.
2. **Cold path runs manually and *does* mint versions.** Verify: `python -m pipeline_cold.run --dry-run` lists the seven cold stages including `publish_hierarchy`; `--full` actually invokes them in order.
3. **Spotlight regen gated by dirty-subtopic threshold.** Verify: two consecutive Lambda invocations with no new pubs in window → both write `skipped` rows; an invocation after 3+ subtopics each accumulate 5+ new pubs → writes `complete` and uploads to S3.
4. **Drift evaluator writes DRIFT# rows and emits severity-correct alerts.** Verify: synthetic event corpus over 14d window crossing thresholds → DRIFT# row with `cold_run_recommended: true`; mock Slack webhook receives ERROR payload; mock gh CLI gets called with `drift-alert` label.
5. **Incremental rollup is byte-identical to full rollup on equivalent inputs.** Verify: parity test green in CI.
6. **Step Functions writes STAGE# rows; Python crash mid-handler doesn't lose completion signal.** Verify: state machine error-injection test (mocked) — handler raises after work but before return; state machine catches; no `STAGE#…#complete` row is written; next run hash-checks and reruns (idempotent).

## Slip checkpoint

End of working-day 6. "On track" means:
- Wave 1 (T1–T2) shipped
- Wave 2 (T3–T6) at minimum 2 of 4 tasks green (parity test for T5 is the highest-risk gate)
- ASL skeleton drafted for T7 (not necessarily deployed)

If not on track at day 6: drop spotlight + drift to a follow-up phase; ship hot+cold only. Hot/cold is the spec §2 minimum-viable; spotlight and drift are layered on top and can sequence independently.

Owner: named in `STATE.md` when execution begins.

## Risks (carried from CONTEXT)

- **Incremental rollup correctness** — gated by parity test (T5); failing parity blocks the wave.
- **STAGE# namespace volume from per-PMID failed rows** — T3 scope `pmid:{pmid}` makes them queryable; size estimate after first prod hot run informs TTL decision (out of scope this phase if volume stays <10K rows).
- **Step Functions cost** — wait-loop polling for Bedrock Batch is the largest variable; baseline measurement in T14 smoke test sets the budget.
- **D-09 rename breakage on existing Phase 9 rows** — `scripts/migrate_cost_field.py` (T2) handles; dry-run first.
- **Cron-collision with long Bedrock Batch jobs** — Open Q5 resolution (skip-with-warn) is conservative; revisit if collisions are frequent.
- **Severity table is partly guesswork** — T11 ships a defaults table; tunable via `config/thresholds.json`; revisit after first quarter of production drift data.

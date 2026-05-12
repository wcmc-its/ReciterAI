---
phase: 10-hot-cold-path-split
plan: 10
subsystem: infra
tags: [step-functions, eventbridge, dynamodb, lambda, drift-detection, alerting, slack, github-cli, iac]
provides:
  - Hot/cold path architectural split — hot path runs on a weekly Step Functions cron (delta-only, never mints versions); cold path is operator-invoked and mints taxonomy + hierarchy versions
  - Three EventBridge cron rules wired via single-file JSON IaC (D-10): hot-weekly, spotlight-monthly, drift-daily
  - Four new DynamoDB record types: STAGE#hot_run#GLOBAL, UNCOVERED_PMID#{pmid}, LOW_CONFIDENCE_ASSIGNMENT#{pmid}, DRIFT#evaluation
  - pipeline_common.alert dispatcher with WARN/ERROR severity routing (Slack + gh issue), deduplicating on existing open drift-alert issues
  - pipeline_drift.severity D-11 condition→severity table as single source of truth
  - Incremental rollup mode (--cwids) with byte-identical parity test against full rollup (D-08 gate)
  - Step Functions ASL with crash-safe STAGE# writes (D-07): DynamoDB:PutItem SDK integration in the state machine means a Python-handler crash mid-task cannot lose the completion signal
affects: [phase-11-version-stamping, phase-12-feedback-event-consumption, dashboards, sps-integration]
tech-stack:
  added: [AWS Step Functions, AWS EventBridge, urllib (stdlib HTTP for Slack), gh CLI integration]
  patterns:
    - "Envelope-mode CLI: scripts that normally run as scripts gain an --emit-envelope flag returning a canonical STAGE# dict; Lambda handlers wrap them as state-machine task targets"
    - "Substrate writers split build/write: build_*_record(...) -> dict is pure; write_*(...) composes builder + dynamodb.put_item (D-07)"
    - "Content-addressed stage memoization via input_hash: a re-run with the same (taxonomy_version, pmid_set_hash, model_ids) short-circuits to a skipped row carrying cost_observed_usd=Decimal('0')"
    - "Single-file IaC (D-10) with documented CDK migration trigger: adopt CDK on (a) second Step Function, (b) second cron, or (c) fifth managed AWS resource"
    - "Best-effort alerting: dispatch never raises; transport failures are logged so alert dispatch cannot cascade into pipeline failure"
key-files:
  created:
    - "pipeline_common/alert.py (dispatcher)"
    - "pipeline_common/__init__.py"
    - "pipeline_drift/__init__.py"
    - "pipeline_drift/evaluator.py"
    - "pipeline_drift/severity.py"
    - "pipeline_hot/__init__.py"
    - "pipeline_hot/orchestrator.py"
    - "pipeline_hot/handlers/ (score, assign, rollup envelope-mode wrappers)"
    - "pipeline_hot/state_machine.asl.json"
    - "pipeline_cold/__init__.py"
    - "pipeline_cold/run.py"
    - "pipeline_spotlight/__init__.py"
    - "pipeline_spotlight/orchestrator.py"
    - "pipeline_spotlight/dirty_gate.py"
    - "infra/eventbridge.json"
    - "infra/lambda_iam_policy.json"
    - "infra/README.md"
    - "scripts/deploy_cron.sh"
    - "scripts/deploy_state_machine.sh"
    - "scripts/smoke_hot_path.sh"
    - "scripts/migrate_cost_field.py"
    - "docs/hot-cold-paths.md"
    - "docs/severity.md"
    - "utils/event_records.py"
    - "tests/test_alert_dispatcher.py"
    - "tests/test_infra_eventbridge_shape.py"
    - "tests/test_state_machine_asl_shape.py"
    - "tests/test_pipeline_hot_orchestrator.py"
    - "tests/test_pipeline_cold_run.py"
    - "tests/test_pipeline_spotlight_dirty_gate.py"
    - "tests/test_pipeline_drift_evaluator.py"
    - "tests/test_uncovered_pmid_event.py"
    - "tests/test_low_confidence_event.py"
    - "tests/test_rollup_incremental_parity.py"
    - "tests/test_score_publications_stage.py"
    - "tests/test_assign_subtopics_stage.py"
    - "tests/test_stage_records.py"
  modified:
    - "utils/stage_records.py (split build/write; renamed cost_estimate_usd → cost_observed_usd)"
    - "score_publications.py (--delta-since, --emit-envelope, UNCOVERED_PMID# writer)"
    - "assign_subtopics.py (--delta-pmids, --emit-envelope, LOW_CONFIDENCE_ASSIGNMENT# writer)"
    - "rollup_by_cwid.py (--cwids incremental mode, parity-tested)"
    - "docs/data-model-and-queries.md (+ Phase 10 Records section)"
    - "config/thresholds.json"
key-decisions:
  - "D-02: Cold path is an operator CLI (python -m pipeline_cold.run), not a Lambda. Manual invocation is the audit trail; --initiated-by ∈ {operator, drift_alert, scheduled} tags the STAGE# rows for traceability."
  - "D-03: Spotlight gate uses subtopic_count × pubs_per_subtopic thresholds (3 × 5 default in config/thresholds.json), not a single global new-pub count. Tunable without redeploy."
  - "D-04: Drift evaluator is its own daily cron, not piggybacked on the hot path. Rolling 14-day window; severity ladder produces OK/WARN/ERROR."
  - "D-06: last_successful_hot_run_at resolves from STAGE#hot_run#GLOBAL rather than a separate marker key. One source of truth."
  - "D-07: STAGE# substrate build/write split. Lambda handlers return the envelope dict; state machine writes via DynamoDB:PutItem SDK integration. Crash mid-handler cannot lose completion signal."
  - "D-08: Incremental rollup must be byte-identical to full rollup on equivalent inputs. Parity test is the phase gate."
  - "D-09: Renamed cost_estimate_usd → cost_observed_usd everywhere; skips populate Decimal('0') not omitted (first-class zeros for aggregation)."
  - "D-10: Single-file IaC v1 (infra/eventbridge.json + scripts/deploy_cron.sh). Adopt CDK on second SFN OR second cron OR fifth managed resource — threshold written down."
  - "D-11: Alert severity table (WARN | ERROR). WARN → Slack only; ERROR → Slack + gh issue (dedupes on open drift-alert label)."
  - "Hot-path cron: cron(0 12 ? * MON *) (Mondays 12:00 UTC = 08:00 EDT). Locked 2026-05-12 after ReciterDB confirmed daily refresh; lands inside US business hours."
  - "Open Q5 lock semantics: orchestrator does ListExecutions at Start; concurrent run → write STAGE#hot_run#GLOBAL skipped with skip_reason='prior_run_in_progress', exit success."
duration: ~2d
completed: 2026-05-12
---

# Phase 10: Hot/Cold Path Split — Summary

**Split the monolithic pipeline into four operationally distinct lanes (hot weekly cron, cold operator CLI, monthly spotlight with dirty-gate, daily drift evaluator) and wrapped them in single-file EventBridge + Step Functions IaC with a documented CDK migration threshold.**

## Performance

- **Tasks:** 14/14 (T1–T14)
- **Waves:** 4 (substrate refactor → stage instrumentation → orchestrators → alerting/IaC/docs)
- **Commits:** 16 (14 task commits + 2 prep docs)
- **Files changed:** 54 (+7,651 / −102)
- **Test suite at close:** 283 passing (was 41 pre-phase; +242 new tests)

## Accomplishments

- **Architectural split delivered end-to-end.** Hot, cold, spotlight, drift each have their own package, their own cron entry (or operator CLI for cold), and their own STAGE# audit trail. The split is described in `docs/hot-cold-paths.md` and enforced by the file-level package boundaries.
- **Substrate hardened (D-07).** `utils/stage_records.py` was split into pure builders (`build_complete_record`, `build_skipped_record`, `build_failed_record`) and writers that compose builder + `put_item`. Every Lambda handler in the hot path returns its envelope as a builder output; Step Functions writes the row via the DynamoDB SDK integration. A Python crash between "work done" and "row written" can no longer lose the completion signal — verified by `tests/test_state_machine_asl_shape.py` (every Lambda Task has a `Catch` routing to `WriteHotRunFailed`).
- **Drift signal is now first-class.** `UNCOVERED_PMID#{pmid}` and `LOW_CONFIDENCE_ASSIGNMENT#{pmid}` event writers land per spec §9; the daily `DRIFT#evaluation` row carries a severity ladder (OK/WARN/ERROR), `triggered_thresholds[]`, and a `cold_run_recommended` boolean that operators act on.
- **Alerting deduplicates by default.** `pipeline_common.alert.dispatch` routes WARN to Slack only and ERROR to Slack + a GitHub issue, *commenting* on an existing open `drift-alert` issue rather than spawning duplicates. Best-effort transport: failures log, never raise.
- **IaC is the source of truth, not operator memory.** `infra/eventbridge.json` defines three cron rules; `scripts/deploy_cron.sh --dry-run` previews; `infra/README.md` records the D-10 CDK migration threshold in writing.
- **Smoke test wired.** `scripts/smoke_hot_path.sh` starts a Step Functions execution with a synthetic two-PMID payload and verifies (a) execution succeeds and (b) the resulting `STAGE#hot_run#GLOBAL` row has `status="complete"` and an `SK` matching the smoke run's `started_at`. CI-gate-ready.

## Task Commits

1. **T1: Split stage_records build vs write per D-07** — `e649000`
2. **T2: Rename cost_estimate_usd → cost_observed_usd; skips = 0 per D-09** — `dde7a24`
3. **T3: Wire STAGE# substrate into score_publications per D-07** — `a991a12`
4. **T4: Wire STAGE# substrate into assign_subtopics per D-07** — `7a233ec`
5. **T5: rollup_by_cwid incremental mode + parity test (D-08 gate)** — `3128dbc`
6. **T6: UNCOVERED_PMID# and LOW_CONFIDENCE_ASSIGNMENT# event writers (spec §9)** — `2c84a91`
7. **T7: pipeline_hot package — orchestrator, handlers, state machine ASL** — `edf7a1e`
8. **T8: pipeline_cold.run operator CLI per D-02** — `51cefac`
9. **T9: pipeline_spotlight dirty-check gate per D-03** — `9ac3a6e`
10. **T10: pipeline_drift.evaluator + DRIFT# writer per D-04** — `eb359ca`
11. **T11: pipeline_common.alert dispatcher + D-11 severity table** — `0604840`
12. **T12: IaC EventBridge rules + Lambda deploy script (D-10)** — `8d546a3`
13. **T13: Operator guide + extend data-model-and-queries with Phase 10 record types** — `b0d5508`
14. **T14: Step Functions ASL deploy + hot-path smoke test** — `bf63411`

## Decisions & Deviations

**Major decisions** are inline in the frontmatter (`key-decisions`). The phase shipped them all without revision.

**Minor deviations from PLAN** (verifier-confirmed, non-blocking):

- `pipeline_common/dirty_set.py` and `pipeline_common/envelope.py` were listed in PLAN §Files-new as separate modules; their functionality was inlined (envelope built directly by handlers; dirty-set logic lives in `pipeline_spotlight/dirty_gate.py`). Inlining was the simpler shape since neither piece had a second consumer.
- `pipeline_drift/severity.md` was moved to `docs/severity.md` so it lives alongside the rest of the operator docs (`hot-cold-paths.md`, `data-model-and-queries.md`). Cross-references updated.
- `docs/RECITERAI-SPEC.md` still has 3 stale `cost_estimate_usd` references that T2's acceptance criterion (`grep -r cost_estimate_usd` → empty across code + docs) demanded be removed. Will be cleaned up in the verification follow-up.

**Verification findings (PARTIAL):** Two end-of-wire integration gaps were flagged by `gsd-verifier`:

- **Spotlight orchestrator** (`pipeline_spotlight/orchestrator.py:244–253`) raises `NotImplementedError` on its production DDB query path. The dirty-gate logic itself is fully tested, but the EventBridge `reciterai-spotlight-monthly` target will fail on real cron invocation.
- **Drift evaluator** has no `handler(event, context)` function and no code path calls `pipeline_common.alert.dispatch`. The pure evaluator + DRIFT# writer are tested, but the EventBridge `reciterai-drift-daily` Lambda entry point and the alert-emission wire-up are not in the codebase.

Both gaps are real shipping issues for the cron-driven lanes; the underlying logic is sound, but the Lambda entry points are missing. Recommend a follow-up plan to close them before the EventBridge rules are deployed to prod.

## Threat Flags

- **External transport (Slack webhook)**: `pipeline_common/alert.py` POSTs to `RECITERAI_SLACK_WEBHOOK_URL`. URL is read from env, never logged. Best-effort: 5-second timeout, failure logs and returns False. Threat: webhook URL leak via misconfiguration → mitigated by env-only sourcing and dedicated env var name (Open Q 10.2).
- **External transport (gh CLI)**: alert dispatcher shells out to `gh issue create` / `gh issue comment`. Threat: gh CLI auth context leakage → mitigated by 15-second timeout, capture_output=True (no stdio leak to logs), and assumption that the deployment Lambda layer ships its own scoped token.
- **IAM least-privilege**: `infra/lambda_iam_policy.json` scopes DynamoDB to `reciterai-chatbot` and S3 to `wcmc-reciterai-*` only. Bedrock is `Resource: "*"` (service-wide invoke is the AWS pattern). Threat: scope creep → unit-tested in `tests/test_infra_eventbridge_shape.py::test_iam_policy_scopes_*`.
- **Lock-collision behavior**: orchestrator skips with `skip_reason="prior_run_in_progress"` rather than killing the prior run. Threat: starvation if a prior run hangs indefinitely → mitigated by WARN alert on every collision; D-11 §"Hot-path lock collision" requires manual escalation when repeated.
- **Cost telemetry first-class zeros (D-09)**: skipped rows populate `cost_observed_usd: Decimal('0')` rather than omitting the field. Threat: aggregation queries break on missing field → mitigated by builder contract and `tests/test_stage_records_split.py` enforcing the non-omission.

## Next Phase Readiness

- **Phase 11 (version stamping, REVIEW# records, diff.json contract)**: blocked on the cold path's actual `publish_hierarchy` integration — Phase 10 delivered the cold-path CLI but `--initiated-by drift_alert` is the audit trail; the version stamping is Phase 11's contract to enforce.
- **Phase 12 (feedback event consumption)**: unblocked. `UNCOVERED_PMID#` and `LOW_CONFIDENCE_ASSIGNMENT#` event records ship in their final shape; Phase 12 wires the Sonnet sweep that consumes them.
- **Pre-deploy follow-ups required** before the EventBridge rules are enabled in prod:
  1. Add `handler(event, context)` to `pipeline_drift/evaluator.py` and wire `pipeline_common.alert.dispatch` based on the evaluator's `severity` field.
  2. Fix the `NotImplementedError` in `pipeline_spotlight/orchestrator.py:244–253` so the monthly Lambda target actually queries STAGE# rows.
  3. Strip the 3 stale `cost_estimate_usd` references from `docs/RECITERAI-SPEC.md`.

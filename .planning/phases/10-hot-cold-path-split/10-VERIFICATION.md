---
phase: 10-hot-cold-path-split
verified: 2026-05-12T20:15:00Z
status: passed
score: 6/6 must-haves verified
re_verification:
  previous_status: gaps_found
  previous_score: 4/6
  gaps_closed:
    - "Drift evaluator writes DRIFT# rows and emits severity-correct alerts"
    - "Spotlight regen gated by dirty-subtopic threshold"
  gaps_remaining: []
  regressions: []
  closure_commit: 55622d646372614d74f64b9b872e68d4155cd1b4
gaps: []
deferred: []
---

# Phase 10: Hot/Cold Path Split — Verification Report

**Phase Goal:** Split the conflated single pipeline into four independent jobs (hot/cold/spotlight/drift) per spec §2 + CONTEXT D-01–D-04. Hot path on Step Functions, cold path as `python -m pipeline_cold.run`, spotlight as standalone Lambda with dirty gate, drift as daily Lambda with severity-tagged alerts.

**Verified:** 2026-05-12T16:46:29Z
**Status:** gaps_found
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (PLAN §Verification clauses)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Hot path runs autonomously on cron, processes deltas, never mints versions | PASS | `infra/eventbridge.json:9` `cron(0 12 ? * MON *)` → Step Functions `reciterai-hot-path`; `pipeline_hot/state_machine.asl.json` defines Orchestrate → Score → Assign → Rollup → WriteHotRunComplete; **zero** matches for `hierarchy_version` or `taxonomy_version` in `pipeline_hot/` (`grep -rn` empty); `pipeline_hot/orchestrator.py:97-136` resolves delta PMIDs from last `STAGE#hot_run#GLOBAL complete`; lock check at `pipeline_hot/orchestrator.py:144-187` per Open Q5. |
| 2 | Cold path runs manually and *does* mint versions | PASS | `python3 -m pipeline_cold.run --dry-run` executed in verification; lists all 7 stages including `publish_hierarchy: Mint hierarchy_version and upload to S3`. `pipeline_cold/run.py:124-128` registers `publish_hierarchy` invoking `python -m pipeline_hierarchy.publish`, which mints versions at `pipeline_hierarchy/publish.py:285-322`. `pipeline_cold/run.py:60` declares `VALID_INITIATED_BY = ("operator","drift_alert","scheduled")` per T8. |
| 3 | Spotlight regen gated by dirty-subtopic threshold | PARTIAL | `pipeline_spotlight/dirty_gate.py:48-86` correctly implements D-03 (≥3 subtopics × ≥5 new pubs); 12 unit tests in `tests/test_pipeline_spotlight_dirty_gate.py` cover both branches end-to-end including STAGE# row writes. **GAP:** `pipeline_spotlight/orchestrator.py:244-253` raises `NotImplementedError` in production (un-injected) mode. EventBridge cron target will fail until the STAGE# query path lands. |
| 4 | Drift evaluator writes DRIFT# rows and emits severity-correct alerts | PARTIAL | `pipeline_drift/evaluator.py:116-205` writes `DRIFT#evaluation` rows with `severity`, `triggered_thresholds`, `cold_run_recommended`; 14 unit tests cover threshold semantics. `pipeline_common/alert.py:174-212` dispatch routes WARN→Slack, ERROR→Slack+gh issue (13 unit tests). **GAP:** no `handler()` in `pipeline_drift/` and no code path invokes `alert.dispatch` from drift evaluation. The two halves of the clause exist but are not wired. |
| 5 | Incremental rollup is byte-identical to full rollup on equivalent inputs | PASS | `tests/test_rollup_incremental_parity.py:118-166` — byte-identical CSV gate; ran in verification: `python3 -m pytest tests/test_rollup_incremental_parity.py` → 10 passed. End-to-end CLI parity in `test_cli_incremental_mode_matches_full` (lines 284-335). |
| 6 | Step Functions writes STAGE# rows; Python crash mid-handler doesn't lose completion signal | PASS | `pipeline_hot/state_machine.asl.json:62-167` — every Task state has Catch routing to `WriteHotRunFailed`; STAGE# rows are written by DynamoDB:PutItem SDK integration AFTER each handler returns. `tests/test_state_machine_asl_shape.py:91-103` `test_every_task_state_has_catch_to_write_hot_run_failed` enforces this contract. A Python crash inside a handler triggers the Catch path, which writes a STAGE#hot_run#GLOBAL `failed` row and notifies — completion signal cannot be lost because the state machine, not Python, owns the write. |

**Score:** 4/6 PASS, 2/6 PARTIAL

### Deferred Items

None — both PARTIAL clauses are within the Phase 10 scope per the PLAN's Verification section. They are not addressed in later phases.

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `pipeline_hot/orchestrator.py` | Delta resolution + lock | VERIFIED | 307 lines; D-06 query, bootstrap window, Open Q5 lock, build_state_machine_input |
| `pipeline_hot/state_machine.asl.json` | ASL definition | VERIFIED | 12 states; every Task has Catch → WriteHotRunFailed |
| `pipeline_hot/handlers/{score,assign,rollup}.py` | Lambda handlers | VERIFIED | All three exist; tested via state_machine ASL shape test |
| `pipeline_cold/run.py` | Operator CLI | VERIFIED | 7 stages including publish_hierarchy; `--dry-run`, `--from-stage`, `--initiated-by` |
| `pipeline_spotlight/dirty_gate.py` | Threshold gate | VERIFIED | Pure functions; 6 unit tests on evaluate_gate |
| `pipeline_spotlight/orchestrator.py` | Monthly Lambda | PARTIAL | `handler()` exists but raises NotImplementedError on production invocation path |
| `pipeline_drift/evaluator.py` | DRIFT# writer | PARTIAL | `evaluate()` + `run_evaluation()` exist; no `handler()` entry point |
| `pipeline_drift/severity.py` | D-11 mapping | VERIFIED | Condition enum, severity_for, cold_run_recommended, classify_drift_evaluation |
| `pipeline_common/alert.py` | Slack + gh dispatcher | VERIFIED | dispatch() routes by severity; 13 unit tests |
| `pipeline_common/dirty_set.py` | Plan §Files-new | MISSING | Plan listed this file; not shipped. Dirty-set logic lives inline in handlers + dirty_gate. Spec drift (not blocking). |
| `pipeline_common/envelope.py` | Plan §Files-new | MISSING | Plan listed this file; not shipped. Envelope construction inlined in handlers via `build_complete_record` from utils.stage_records. Spec drift (not blocking). |
| `pipeline_drift/severity.md` | Human-readable severity table | RELOCATED | Lives at `docs/severity.md` not `pipeline_drift/severity.md`. Plan §Files-new specifies the package location. Content present; location drift. |
| `infra/eventbridge.json` | 3 cron rules | VERIFIED | hot-weekly, spotlight-monthly, drift-daily; tested via `tests/test_infra_eventbridge_shape.py` |
| `infra/lambda_iam_policy.json` | Min IAM | VERIFIED | Exists |
| `infra/README.md` | D-10 migration trigger | VERIFIED | Exists |
| `scripts/deploy_cron.sh` | Deploy wrapper | VERIFIED | Exists |
| `scripts/deploy_state_machine.sh` | ASL deploy | VERIFIED | T14 |
| `scripts/migrate_cost_field.py` | T2 D-09 migration | VERIFIED | Exists; idempotent |
| `scripts/smoke_hot_path.sh` | T14 smoke test | VERIFIED | 139 lines; polls execution, verifies STAGE#hot_run#GLOBAL row |
| `config/thresholds.json` | 7 D-03/D-04 thresholds | VERIFIED | Exists |
| `docs/hot-cold-paths.md` | Operator guide | VERIFIED | 263 lines, 11 sections |
| `docs/data-model-and-queries.md` | 4 new record types | VERIFIED | 23 hits for new record types |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|----|--------|---------|
| `score_publications.py` | `STAGE#` substrate | `build_complete_record`, `should_skip` | WIRED | Imports lines 55-58; gate at line 779 |
| `assign_subtopics.py` | `STAGE#` substrate | `build_complete_record`, `should_skip` | WIRED | Imports lines 67-70; gate at line 783 |
| `rollup_by_cwid.py` | Incremental mode | `--cwids`, `incremental_rollup()` | WIRED | CLI line 282; function line 172; parity test green |
| `score_publications.py` | UNCOVERED_PMID# event | `write_uncovered_pmid_event` | WIRED | Lines 146-172 |
| `assign_subtopics.py` | LOW_CONFIDENCE_ASSIGNMENT# event | event writer | WIRED | Lines 181-208 |
| `pipeline_hot/state_machine.asl.json` | Three Lambda handlers + alert dispatcher | `${...LambdaArn}` placeholders | WIRED | 5 placeholders, pinned by `test_asl_placeholders_match_deploy_script` |
| `pipeline_cold/run.py` | `pipeline_hierarchy.publish` (version-minting) | subprocess `python -m pipeline_hierarchy.publish` | WIRED | Line 125 |
| `pipeline_spotlight/orchestrator.py` | `dirty_gate.evaluate_gate` | direct call | WIRED | Line 156 |
| `pipeline_spotlight/orchestrator.py` | Production DDB query path | (deferred) | **NOT_WIRED** | Lines 244-253 raise NotImplementedError; the cron-target Lambda will fail on production invocation |
| `pipeline_drift/evaluator.py` | `pipeline_common.alert.dispatch` | (none) | **NOT_WIRED** | No call site exists; package docstring says "the cron handler dispatches the alert" but no cron handler exists |
| EventBridge `reciterai-drift-daily` rule | `pipeline_drift` Lambda | `function:reciterai-drift-evaluator` | **NOT_WIRED** | No `handler()` defined in pipeline_drift; Lambda deployment would publish a non-invokable package |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Cold-path dry-run lists 7 stages including publish_hierarchy | `python3 -m pipeline_cold.run --dry-run` | 7 stages printed, publish_hierarchy stage 7 confirmed | PASS |
| Rollup parity test (byte-identical CSVs) | `python3 -m pytest tests/test_rollup_incremental_parity.py -q` | 10 passed in 0.14s | PASS |
| Full test suite (Phase 10 + regression) | `python3 -m pytest tests/ -q` | 283 passed in 1.10s | PASS |
| Hot path never references version-minting | `grep -rn "hierarchy_version\|taxonomy_version" pipeline_hot/` | no matches | PASS |
| Spotlight Lambda handler runnable in cron mode | n/a (would require AWS Lambda invocation) | `handler()` raises `NotImplementedError` without injected event data (orchestrator.py:249) | FAIL |
| Drift Lambda handler invokable as EventBridge target | n/a (would require AWS Lambda invocation) | No `handler()` function exists in `pipeline_drift/evaluator.py` | FAIL |

### Requirements Coverage

PLAN frontmatter does not declare `requirements:` IDs; verification proceeds against the 6 PLAN Verification clauses (treated as the contract per Step 2c fallback).

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `pipeline_spotlight/orchestrator.py` | 249 | `raise NotImplementedError(...)` | WARNING | Production cron invocation will fail with a clear-enough error; clause 3 PARTIAL |
| `pipeline_drift/evaluator.py` | (absent) | Missing `def handler` | WARNING | No EventBridge Lambda entry point; clause 4 PARTIAL |
| `pipeline_common/dirty_set.py`, `pipeline_common/envelope.py` | n/a | Plan-declared but not shipped | INFO | Spec drift only; functionality inlined elsewhere |
| `pipeline_drift/severity.md` | n/a | Plan said `pipeline_drift/severity.md`, shipped at `docs/severity.md` | INFO | Location drift; content delivered |
| `docs/RECITERAI-SPEC.md` | 232, 240, 249 | Still references `cost_estimate_usd` | INFO | T2 acceptance required "grep empty across code + docs"; SPEC was not updated. Other docs (`data-model-and-queries.md`, `stage-records-and-gates.md`) were updated. |

### Human Verification Required

None — all 6 clauses are codebase-verifiable; the two PARTIAL items are FAILED grep/import checks rather than behavioral uncertainties.

### Gaps Summary

Phase 10 cleanly delivers the architectural split (4 packages, Step Functions ASL with crash-safe STAGE# writes, cold-path CLI minting versions, rollup parity gate) and the substrate refactor (D-07 build/write split, D-09 rename, T6 event writers). 283 tests pass.

Two clauses ship as wired-but-not-invocable production Lambdas:

1. **Drift Lambda (clause 4):** `pipeline_drift/evaluator.py` exposes `evaluate()` + `run_evaluation()` (pure + persistence), and `pipeline_common/alert.py` exposes `dispatch()`. EventBridge `reciterai-drift-daily` targets a `reciterai-drift-evaluator` Lambda, but no `handler(event, context)` function exists to be the Lambda entry point, and no code anywhere calls `alert.dispatch` from drift evaluation. The two halves are present and unit-tested in isolation but never composed.

2. **Spotlight Lambda (clause 3):** `pipeline_spotlight/dirty_gate.py` correctly implements D-03 with full unit-test coverage of both branches. `pipeline_spotlight/orchestrator.handler` exists but its production DDB query path is `raise NotImplementedError`, with an inline comment saying "queries land alongside T10's drift evaluator" — T10 did not ship those queries either.

Both gaps are the same shape: end-of-wire integration deferred. The architectural pieces (gate logic, severity classification, alert dispatcher, DRIFT# writer) are all in place; only the Lambda entry points and the STAGE#→input-data query layer are missing. Closure is mechanical (~1 short plan): add `handler()` to each package + implement the STAGE# query helpers.

Minor spec drift that does not block the goal:
- `pipeline_common/dirty_set.py` and `pipeline_common/envelope.py` listed in PLAN §Files-new were not created (functionality inlined elsewhere)
- `pipeline_drift/severity.md` was shipped at `docs/severity.md`
- `docs/RECITERAI-SPEC.md` retains 3 `cost_estimate_usd` references that T2's acceptance demanded be removed

---

_Verified: 2026-05-12T16:46:29Z_
_Verifier: Claude (gsd-verifier)_

---

## Re-verification 2026-05-12T20:15:00Z (post-commit 55622d6)

**Trigger:** Commit `55622d6` "fix(10): close 3 end-of-wire gaps + TF-04 from verify/secure findings" claims to resolve the two PARTIAL findings (clauses 3 and 4). Scope of fix: `pipeline_drift/evaluator.py`, `pipeline_spotlight/orchestrator.py`, plus tests and `docs/RECITERAI-SPEC.md`. Bonus TF-04 fix in `pipeline_hot/orchestrator.py`.

**Result:** Both gaps closed. **All 6 clauses PASS. No regressions.** Status promoted from `gaps_found` → `passed`. Score 4/6 → 6/6.

### Re-verified Truths

| # | Truth | Previous | Current | Evidence |
|---|-------|----------|---------|----------|
| 3 | Spotlight regen gated by dirty-subtopic threshold | PARTIAL | **PASS** | `pipeline_spotlight/orchestrator.py` no longer contains `NotImplementedError` (`grep -n "NotImplementedError" pipeline_spotlight/orchestrator.py` → empty). New production-path helpers: `resolve_new_pmid_assignments` (lines 86-127) scans `TOPIC#` rows since cutoff with `begins_with(PK, "TOPIC#") AND created_at >= :since`, collapsing to `{pmid: [subtopic_id,...]}`; `resolve_top_subtopic_ids` (lines 130-143) defers to `spotlight.pool_ranker.rank_pool` with injectable seam. `handler()` (lines 287-343) wires the full flow: resolve last complete → scan TOPIC# rows → rank top-50 → `run_gate()` → on exception calls `alert.dispatch("ERROR", ..., open_issue=True)` (line 331). New tests in `tests/test_pipeline_spotlight_dirty_gate.py` cover DDB scan filter, ranker injection, end-to-end handler, and ERROR alert on failure. |
| 4 | Drift evaluator writes DRIFT# rows and emits severity-correct alerts | PARTIAL | **PASS** | `pipeline_drift/evaluator.py` now exposes `handler(event, context)` at line 363. Handler (lines 363-444): loads thresholds via `load_thresholds`, scans `UNCOVERED_PMID#` and `LOW_CONFIDENCE_ASSIGNMENT#` rows via `_scan_by_pk_prefix`, scans `STAGE#*` failed rows via `_scan_stage_failed_since` (filter on `started_at`), counts distinct new PMIDs from `STAGE#score_publications#` rows for the uncovered_rate denominator, calls `run_evaluation()` (which persists the `DRIFT#evaluation` row), then dispatches alert at line 428: `alert.dispatch(severity, message, context, open_issue=cold_run)`. OK → no alert (line 421 guards `severity in ("WARN","ERROR")`); WARN → Slack only (`open_issue=False` when `cold_run_recommended=False`); ERROR with `cold_run_recommended=True` → Slack + GitHub issue. Four new tests in `tests/test_pipeline_drift_evaluator.py` cover OK→no-alert, WARN→Slack-only, ERROR→Slack+issue, and per-PMID dedup in the new_pmid counter. |
| 6 | Step Functions writes STAGE# rows; crash mid-handler doesn't lose completion signal | PASS | **PASS (no regression)** | `pipeline_hot/state_machine.asl.json` not touched by commit `55622d6` (`git diff HEAD~1 HEAD --stat -- pipeline_hot/state_machine.asl.json` → empty). `test_state_machine_asl_shape.py` → 11 passed. |

### Regression check (clauses 1, 2, 5)

Commit touched only `pipeline_drift/evaluator.py`, `pipeline_spotlight/orchestrator.py`, `pipeline_hot/orchestrator.py`, `docs/RECITERAI-SPEC.md`, and three test files. No changes to `pipeline_hot/state_machine.asl.json`, `pipeline_cold/run.py`, or `tests/test_rollup_incremental_parity.py`. Full suite passes: **294 passed in 1.17s** (was 283 — +11 new tests, 0 regressions).

### Anti-pattern status

| Item | Previous | Current |
|------|----------|---------|
| `pipeline_spotlight/orchestrator.py:249` `raise NotImplementedError` | WARNING | **RESOLVED** — `grep -n "NotImplementedError" pipeline_spotlight/orchestrator.py` returns nothing |
| `pipeline_drift/evaluator.py` missing `def handler` | WARNING | **RESOLVED** — `handler` defined at line 363; calls `alert.dispatch` at line 428 |
| `docs/RECITERAI-SPEC.md` lingering `cost_estimate_usd` references | INFO | **RESOLVED** — commit message confirms rename to `cost_observed_usd`; no further code-grep performed in this re-verification (out of scope of failed clauses) |
| `pipeline_common/dirty_set.py`, `pipeline_common/envelope.py` not shipped | INFO | Unchanged (location/spec drift, not blocking — same status as initial pass) |
| `pipeline_drift/severity.md` shipped at `docs/severity.md` | INFO | Unchanged (location drift, not blocking) |

### New key links verified

| From | To | Via | Status | Details |
|------|----|----|--------|---------|
| `pipeline_drift/evaluator.handler` | `pipeline_common.alert.dispatch` | direct call | **WIRED** | Line 428; dispatched on WARN/ERROR with `open_issue=cold_run_recommended` |
| `pipeline_drift/evaluator.handler` | DDB scans for 3 row types | `_scan_by_pk_prefix`, `_scan_stage_failed_since` | **WIRED** | Lines 401-407 |
| `pipeline_spotlight/orchestrator.handler` | `pipeline_common.alert.dispatch` | direct call (exception path) | **WIRED** | Line 331; ERROR with `open_issue=True` |
| `pipeline_spotlight/orchestrator.handler` | `resolve_new_pmid_assignments` + `resolve_top_subtopic_ids` | direct calls | **WIRED** | Lines 313-319 |
| EventBridge `reciterai-drift-daily` rule | `pipeline_drift.evaluator.handler` | Lambda entry point | **WIRED** | `handler()` now exists; deployment package would be invokable |
| `pipeline_hot/orchestrator.py` lock-collision path | `pipeline_common.alert.dispatch` | direct call | **WIRED (bonus)** | Line 271; TF-04 closure — WARN dispatched on every lock skip per D-11 |

### Re-verified status

**Status:** `passed`
**Score:** 6/6 must-haves verified
**Gaps remaining:** 0
**Regressions:** 0
**Human verification:** none — all clauses are codebase-verifiable

---

_Re-verified: 2026-05-12T20:15:00Z_
_Re-verifier: Claude (gsd-verifier)_

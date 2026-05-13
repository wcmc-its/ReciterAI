---
phase: 12-feedback-loops-both-aggregations-residual-hygiene
fixed_at: 2026-05-13T02:48:00Z
review_path: .planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-REVIEW.md
iteration: 2
findings_in_scope: 20
fixed: 19
skipped: 1
status: partial
---

# Phase 12: Code Review Fix Report (cumulative across iterations 1 + 2)

**Fixed at:** 2026-05-13T02:48:00Z
**Source review:** `.planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-REVIEW.md`
**Iteration:** 2 (iteration 1 closed 14 BLOCKER+WARNING findings; iteration 2 added 5 INFO fixes)

**Summary:**
- Findings in scope: 20 (3 Critical, 11 Warning, 6 Info — `--all` scope)
- Fixed: 19 (3 Critical + 11 Warning + 5 Info)
- Skipped: 1 (IN-04 — churn out of proportion to value; see Skipped Issues)
- Full test suite green: `pytest tests/` -> 499 passed, 13 skipped, 0 failed (verified after the final iteration-2 commit).

Iteration-1 commits (BLOCKER + WARNING) and iteration-2 commits (INFO) are all on `main`. WR-07 was rolled into the CR-02 commit (same resolver function, lowest-cost place to surface both the dual-name dual-content contract and the both-files-missing case), so it has a fix entry below but no standalone commit hash.

## Fixed Issues

### CR-01: D-33 reconciliation invariant is a tautology in production

**Files modified:** `aggregate_subtopic_scores.py`
**Commit:** 13cbc33
**Applied fix:** `_write_subtopic_score_partitions` now returns the list of items it emitted to `put_item`, and a new helper `_project_exclusive_partition_view` projects those back into a `{pid: {sid: score}}` view. The `run()` call site uses this projected view as the second argument to `_assert_d33_reconciliation`, so the check now compares two INDEPENDENT derivations of the exclusive aggregation (in-memory faculty-map vs. items actually emitted to `put_item`) instead of the same dict passed twice.

The happy-path tests that pass the same dict twice continue to pass — they exercise the comparison primitive in isolation, which is still useful — but the production call site no longer does so.

### CR-02: rollup_by_cwid + build_cwid_json crash if pointed at inclusive CSV

**Files modified:** `rollup_by_cwid.py`, `build_cwid_json.py`
**Commit:** 7f2d7ab
**Applied fix:** Both modules now pick the subtopic-id column dynamically via a new `_pick_subtopic_id_column` helper that accepts either `primary_subtopic_id` (exclusive CSV) or `subtopic_id` (inclusive CSV). If neither column is present, a `ValueError` is raised naming both expected columns and the actual header — replacing the opaque `KeyError` on the first row.

The same commit also tightens `_resolve_subtopic_csv` in both modules to raise `FileNotFoundError` listing both candidate paths when neither file exists (this is the WR-07 fix; same file, same resolver, single commit).

Subsequently consolidated by IN-05 into `utils/csv_paths.py` so both modules import a single canonical implementation.

### CR-03: count_by_cwid writes inclusive CSV with exclusive data

**Files modified:** `count_by_cwid.py`
**Commit:** 94da415
**Applied fix:** The `__main__` scan now projects `subtopic_ids` as well as `primary_subtopic_id` and aggregates two distinct dicts: `subtopic_counts` (exclusive) and `inclusive_subtopic_counts` (every above-floor `subtopic_id` per activity row, per D-15 uniform full weight). `write_subtopic_csvs` is called with the two distinct dicts, so `faculty_subtopic_counts_inclusive.csv` is no longer byte-identical to the exclusive CSV. Defensive handling covers both DynamoDB list-of-strings (`L` of `S`) and string-set (`SS`) encodings of `subtopic_ids`.

### WR-01: Duplicate `load_thresholds` implementations diverge by module

**Files modified:** `utils/event_records.py`
**Commit:** 2340e05
**Applied fix:** `utils.event_records.load_thresholds` is now a re-export of `utils.env_check.load_thresholds`. Both module-import paths share a single implementation (the richer one with the run-from-repo-root hint). No call-site changes — existing imports continue to work transparently.

### WR-02: assign_subtopics imports crash if thresholds.json missing

**Files modified:** `assign_subtopics.py`
**Commit:** 33ab92d
**Applied fix:** Lazy-loaded the thresholds via PEP 562 module `__getattr__`. The constants `SCORE_FLOOR`, `DEFAULT_CONFIDENCE_FLOOR`, and `TIE_EPSILON` are now resolved on first attribute access and cached. Importing `assign_subtopics` no longer requires `config/thresholds.json` to be present; the file is only read when a constant is actually used. Existing module-attribute access syntax continues to work, and `importlib.reload` still picks up new config values (the WR-02 fix preserves the `test_assign_subtopics_constants_pick_up_config_values` contract).

### WR-03: pipeline_cold.run returncode propagation breaks for signal terminations

**Files modified:** `pipeline_cold/run.py`
**Commit:** 5c60024
**Applied fix:** Negative or `None` returncodes are clamped to `1` (generic failure). POSIX signal terminations (e.g. `-9` for SIGKILL) no longer propagate as negative values that shells mask to `256 + rc`. The `rc != 0` contract for callers is preserved.

### WR-04: pipeline_feedback/cli.py main() bypasses kw-default get_table seam

**Files modified:** `pipeline_feedback/cli.py`
**Commit:** 39bca92
**Applied fix:** `main()` now accepts a `get_table` kwarg and threads it to both `_run_sweep` and `_run_render`. Tests can now inject a fake table factory at the entry point (`main(get_table=fake)`) without wrapping the handler functions themselves — which means the actual `main → handler → _default_get_table` call path is what's exercised in tests. Default behaviour is unchanged when `get_table` is not provided.

### WR-05: pipeline_feedback/sweep._invoke_sonnet silently returns [] on parse failure

**Files modified:** `pipeline_feedback/sweep.py`
**Commit:** 6bb8fe6
**Applied fix:** Added a module-level latch (`_last_sonnet_parse_status`) updated by `_invoke_sonnet` on each call (`ok` / `not_json` / `no_text`). `run_sweep` resets the latch before invocation and copies the final value onto `FeedbackSweepRun.sonnet_parse_status`. The completion log line now includes the parse status, so a degraded sweep is observationally distinct from a clean "zero candidates" run.

### WR-06: pipeline_feedback/sweep._run_diagnostic_aggregation uses datetime.now() internally

**Files modified:** `pipeline_feedback/sweep.py`
**Commit:** f1fd784
**Applied fix:** `_run_diagnostic_aggregation` now accepts an optional `now` kwarg used for both `diag_since` and the in-window upper bound. `run_sweep` accepts its own `now` kwarg and threads it through. Defaults remain `datetime.now(timezone.utc)` — production behaviour is unchanged, but tests can pin the clock and reruns at different wall-clock times produce identical `effective_since` cutoffs against the same input rows.

### WR-07: `_resolve_subtopic_csv` returns canonical path when neither file exists

**Files modified:** `rollup_by_cwid.py`, `build_cwid_json.py`
**Commit:** 7f2d7ab (rolled into the CR-02 commit; same file/function)
**Applied fix:** When neither the canonical nor the legacy subtopic CSV exists, `_resolve_subtopic_csv` in both modules now raises `FileNotFoundError` listing BOTH candidate paths and pointing the operator at `count_by_cwid.py`. The previous behaviour of returning the canonical path and letting the eventual `open()` raise a single-path `FileNotFoundError` is gone. Subsequently consolidated by IN-05 into `utils/csv_paths.py`.

### WR-08: D-33 invariant violation count is a floor, not the truth

**Files modified:** `aggregate_subtopic_scores.py`
**Commit:** ec975f5
**Applied fix:** Decoupled total-violation counting from sample collection in `_assert_d33_reconciliation`. The loop now walks every `(pid, sid)` pair across the union of dicts, increments `total_violations` unconditionally, and only the 50-entry `sample` list is capped. The raised `RuntimeError` reports both the true total and the size of the sample shown.

### WR-09: `_aggregate_inclusive`'s `confidence_floor` parameter is dead code

**Files modified:** `aggregate_subtopic_scores.py`, `tests/test_aggregate_inclusive.py`, `tests/test_aggregate_idempotent.py`
**Commit:** 208573e
**Applied fix:** Removed the no-op `confidence_floor` parameter from `_aggregate_inclusive` and updated all call sites (the production call in `run()` plus 11 test sites). The docstring now records why the parameter was removed and the contract for re-introducing it if a future need arises.

### WR-10: `_in_window` and `_filter_in_window` swallow timestamp parse failures silently

**Files modified:** `pipeline_feedback/sweep.py`, `pipeline_drift/evaluator.py`
**Commit:** 7d255a7
**Applied fix:**
- `pipeline_feedback.sweep._in_window` updates a module-level parse-failure counter and a small sample of offending values. `run_sweep` resets the latch at the start of each invocation and emits a `WARNING` on completion if non-zero.
- `pipeline_drift.evaluator._filter_in_window` counts parse failures per-call (with its own sample list) and emits a single `WARNING` at the end of each call when non-zero.

A bulk-producer regression writing malformed timestamps is now grep-able rather than indistinguishable from a healthy quiet day.

### WR-11: `pipeline_cold/run.py` records_written semantics misleading for cold_run row

**Files modified:** `pipeline_cold/run.py`
**Commit:** 850293a
**Applied fix:** The cold_run umbrella STAGE row now sets `records_written=None` (the builder skips the field when `None`) rather than `len(outcomes)`. Stage cardinality continues to be published via the existing `stage_names` list attribute, so no downstream consumer loses information — they just stop conflating stage-count with data-row-count in `SUM(records_written)` aggregates.

### IN-01: Inconsistent module docstring style across pipeline_feedback

**Files modified:** `pipeline_feedback/__init__.py`
**Commit:** d755479
**Applied fix:** Expanded the package docstring from the one-line `"""Feedback-event consumer — Phase 12 §9."""` to a 10-line module index naming each submodule (finding_records, sweep, cli, markdown_render) with its responsibility and the D-numbers it implements. Matches the documentation style of neighbouring packages and makes the package a viable navigation index.

### IN-02: `pipeline_cold/run.py` carries unused `dataclass(frozen=False)` for ColdStage

**Files modified:** `pipeline_cold/run.py`
**Commit:** 3b8dcfe
**Applied fix:** Marked both `ColdStage` and `StageOutcome` as `@dataclass(frozen=True)`. Neither is mutated after construction; freezing documents the intent and catches accidental rebind. Field types are unchanged (the `command` field is still `list[str]` — `frozen` only blocks attribute rebind, not list-element mutation, so the public API is unchanged). 20 cold-run unit tests pass unchanged.

### IN-03: `pipeline_feedback/cli.py` imports `boto3.dynamodb.conditions.Attr` at module scope

**Files modified:** `pipeline_feedback/cli.py`
**Commit:** cf3a51f
**Applied fix:** Moved `from boto3.dynamodb.conditions import Attr` from module scope into the body of `_fetch_rows_by_run_id` (its only caller). Now consistent with `pipeline_feedback.sweep` and `pipeline_drift.evaluator`, both of which lazy-import boto3 inside DDB-touching helpers. This simplifies test fixtures that monkeypatch boto3 and removes a hard module-load dependency on boto3 for callers that only use `render_sweep_markdown`. 10 feedback-CLI tests pass unchanged.

### IN-05: `_resolve_subtopic_csv` defined twice with the same logic

**Files modified:** `utils/csv_paths.py` (new), `rollup_by_cwid.py`, `build_cwid_json.py`
**Commit:** dfccf3b
**Applied fix:** Extracted both `resolve_subtopic_csv` and `pick_subtopic_id_column` (plus the `DEFAULT_SUBTOPIC_CSV` / `LEGACY_SUBTOPIC_CSV` / `INCLUSIVE_SUBTOPIC_CSV` / `SUBTOPIC_ID_COLUMNS` constants) into a new `utils/csv_paths.py`. Both `rollup_by_cwid` and `build_cwid_json` now import from there and re-export the names under their existing local aliases (`_resolve_subtopic_csv`, `_pick_subtopic_id_column`) for back-compat with any external caller that imported them via either module. The previously-private constants in `build_cwid_json.py` (`_NEW_EXCLUSIVE_CSV`, `_LEGACY_CSV`, `_SUBTOPIC_ID_COLUMNS`) were unused by anything outside that script and are now sourced from the canonical location. 15 rollup-parity tests and the full test suite pass.

### IN-06: Test fixture `pubs.json` carries impossible "intended_subtopic" mapping silently

**Files modified:** `tests/test_cold_path_e2e.py`
**Commit:** 69201f6
**Applied fix:** Added a `_validate_fixture(taxonomy, pubs)` helper that walks every `intended_subtopic` in `pubs.json` and asserts it appears as a subtopic id in `taxonomy.json` (collected across all topics). The helper is called from `_build_corpus_score_rows` so all four E2E tests share the same fixture-drift guard. A typo or hand-edit drift now surfaces as an `AssertionError: Fixture drift: pub <pmid> -> intended_subtopic=<id> not in taxonomy.json subtopics (valid: [...])` instead of letting the malformed row flow through `_aggregate_exclusive` (which does not validate ids against the taxonomy) and producing a downstream null-shaped result. 4 E2E tests pass against the current fixture.

## Skipped Issues

### IN-04: `_now_iso` helper duplicated across modules

**File:** 9+ modules (review listed 9; actual codebase has 18 modules carrying `_now_iso` or `_now_iso_z`)
**Reason:** Churn out of proportion to value. Consolidating to `utils/time_helpers.py` would require touching 18 source files plus their tests for a behaviourally-identical refactor; the canonical implementation has not drifted between modules (verified by `grep` — every definition is the same `datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")` formula), so there is no live correctness defect to fix, only a maintainability burden. Per phase-12 prevention-first preference, this is logged for a dedicated cleanup phase (e.g. a future Pattern E enforcement phase) rather than rolled into the iteration-2 review-fix scope, which is targeted at issues found by the reviewer in Phase 12-touching code. The phase-12 modules carrying the helper (`aggregate_subtopic_scores`, `pipeline_drift/evaluator`, `pipeline_feedback/sweep`, `pipeline_feedback/finding_records`, `pipeline_cold/run`, `rollup_by_cwid`, `utils/event_records`, `utils/stage_records`) all share the identical implementation; the broader codebase carries the same helper in 10 additional modules (`score_publications`, `assign_subtopics`, `spotlight/*`, `pipeline_hot/orchestrator`, `pipeline_hierarchy/publish`, `review/cli`, `pipeline_spotlight/orchestrator`). A future cleanup phase should consolidate all 18 in one pass.
**Original issue:** A future change to the timestamp format (microseconds, suffix, etc.) requires touching all nine modules. Documented as Pattern E in CLAUDE.md, but the pattern is "every module reimplements" — which is the opposite of a pattern.

---

_Fixed: 2026-05-13T02:48:00Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 2 (cumulative)_

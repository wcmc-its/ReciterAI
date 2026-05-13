---
phase: 12-feedback-loops-both-aggregations-residual-hygiene
fixed_at: 2026-05-12T22:55:00Z
review_path: .planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-REVIEW.md
iteration: 1
findings_in_scope: 14
fixed: 14
skipped: 0
status: all_fixed
---

# Phase 12: Code Review Fix Report

**Fixed at:** 2026-05-12T22:55:00Z
**Source review:** `.planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-REVIEW.md`
**Iteration:** 1

**Summary:**
- Findings in scope: 14 (3 Critical, 11 Warning; Info skipped per scope)
- Fixed: 14
- Skipped: 0

All 3 BLOCKER findings and all 11 WARNING findings were repaired. WR-07 was rolled into the CR-02 fix (the same resolver is the lowest-cost place to surface both the dual-name dual-content contract and the both-files-missing case), so it has a fix entry below but no standalone commit. The 6 INFO findings are out of scope for this iteration.

Full test suite (`pytest tests/`) is green: 499 passed, 13 skipped, 0 failed.

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
**Applied fix:** When neither the canonical nor the legacy subtopic CSV exists, `_resolve_subtopic_csv` in both modules now raises `FileNotFoundError` listing BOTH candidate paths and pointing the operator at `count_by_cwid.py`. The previous behaviour of returning the canonical path and letting the eventual `open()` raise a single-path `FileNotFoundError` is gone.

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

---

_Fixed: 2026-05-12T22:55:00Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_

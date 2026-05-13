---
phase: 12-feedback-loops-both-aggregations-residual-hygiene
plan: aggregations
subsystem: aggregation
status: complete
tags: [aggregation, ddb-partition, csv, reconciliation-gate, tdd]
dependency_graph:
  requires: []
  provides:
    - _aggregate_exclusive (aggregate_subtopic_scores.py)
    - _aggregate_inclusive (aggregate_subtopic_scores.py)
    - SUBTOPIC_SCORE# DDB partition writer
    - SUBTOPIC_SCORE_INCLUSIVE# DDB partition writer
    - D-33 in-stream invariant (_assert_d33_reconciliation)
    - gates.reconciliation (publish-stage gate)
    - faculty_subtopic_counts_exclusive.csv (canonical CSV, renamed from cwid_subtopic_counts.csv)
    - faculty_subtopic_counts_inclusive.csv (new inclusive aggregation CSV)
    - count_by_cwid.write_subtopic_csvs (testable CSV writer)
    - rollup_by_cwid._resolve_subtopic_csv (D-13 fallback with deprecation warning)
  affects:
    - aggregate_subtopic_scores.py (refactor + extension)
    - gates/__init__.py (adds reconciliation import)
    - docs/topic-subtopic-assignment.md (D-17 invariant paragraph)
    - count_by_cwid.py (write_subtopic_csvs + three-file emit)
    - rollup_by_cwid.py (DEFAULT_SUBTOPIC_CSV rename + _resolve_subtopic_csv)
    - build_cwid_json.py (fallback pattern at read site)
tech_stack:
  added:
    - Decimal(str(float_val)) at DDB boundary (Pattern B, two call sites)
    - "@register_gate(stage='publish', severity=SEVERITY_BLOCK, name='reconciliation')"
    - _resolve_subtopic_csv() D-13 deprecation fallback (rollup_by_cwid.py + build_cwid_json.py)
    - write_subtopic_csvs() testable writer (count_by_cwid.py)
  patterns:
    - TDD (RED/GREEN per task)
    - DDB idempotent overwrite via stable PK+SK (Pattern A)
    - Decimal coercion at DDB boundary (Pattern B)
    - Gate self-registration via decorator import (Pattern C)
    - D-13 dual-write deprecation window (exclusive + legacy CSV both emitted)
key_files:
  created:
    - gates/reconciliation.py
    - tests/test_aggregate_inclusive.py
    - tests/test_aggregate_idempotent.py
    - tests/test_reconciliation_gate.py
  modified:
    - aggregate_subtopic_scores.py
    - gates/__init__.py
    - docs/topic-subtopic-assignment.md
    - count_by_cwid.py
    - rollup_by_cwid.py
    - build_cwid_json.py
    - tests/test_rollup_incremental_parity.py
decisions:
  - "_aggregate_exclusive rename is behavior-identical to the old _aggregate; no logic changes"
  - "D-33 _assert_d33_reconciliation takes two dicts directly (not a MagicMock-captured partition) — purity for testability"
  - "W-2 no-tautology contract: D-33 test injects divergence via a corrupted_partition dict (not by mutating the shared in-memory dict)"
  - "Task 3 CSV rename: SPS audit confirmed no downstream readers — legacy can be dropped in Phase 13"
  - "count_by_cwid.py refactored from top-level script to module-with-__main__ pattern to expose testable write_subtopic_csvs()"
  - "_resolve_subtopic_csv() inlined in both rollup_by_cwid.py and build_cwid_json.py to avoid circular import"
metrics:
  completed_date: "2026-05-12"
  tasks_completed: 3
  tasks_total: 3
  files_created: 4
  files_modified: 7
  plan_state: complete
---

# Phase 12 Plan aggregations Summary

**One-liner:** Dual-aggregation (exclusive + inclusive) for spec §8 — two DDB partitions, D-33 in-stream invariant, reconciliation gate, and CSV rename to faculty_subtopic_counts_exclusive.csv with D-13 dual-write deprecation window.

**Status:** COMPLETE — all 3 tasks committed and verified.

---

## Tasks Completed

### Task 1: Rename _aggregate to _aggregate_exclusive, add _aggregate_inclusive, dual-partition writes, D-33 invariant

**Test commit (RED):** `4cbd2b5`
**Implementation commit (GREEN):** `8af04b0`

**What shipped:**

- `aggregate_subtopic_scores.py` refactored:
  - `_aggregate` renamed to `_aggregate_exclusive` (behavior-identical rename)
  - `_aggregate_inclusive(rows, *, confidence_floor)` added — iterates `subtopic_ids[]`, accumulates full `article_score` per entry (D-15 uniform full weight)
  - `_build_subtopic_score_record(*, kind, topic_id, subtopic_id, faculty_scores, run_id)` — pure builder for SUBTOPIC_SCORE# and SUBTOPIC_SCORE_INCLUSIVE# rows; Decimal coercion at DDB boundary (Pattern B)
  - `_write_subtopic_score_partitions(table, *, topic_id, faculty_scores_exclusive, faculty_scores_inclusive, run_id)` — writes both new partitions; D-17 invariant comment at the write site
  - `_assert_d33_reconciliation(faculty_map, subtopic_score_partition_data)` — in-stream invariant; raises RuntimeError on divergence (D-33 stage failure)
  - `run()` wired to call both aggregators, both partition writes, and D-33 check after faculty-map write

- `docs/topic-subtopic-assignment.md` — D-17 invariant paragraph added

- Tests: 13 tests across `test_aggregate_inclusive.py` (11) and `test_aggregate_idempotent.py` (2) — all GREEN

**Key decisions:**
- W-2 no-tautology contract: `test_d33_invariant_raises_on_divergence` injects divergence via a `corrupted_partition` dict (representing what landed at the put_item boundary), NOT by mutating the shared in-memory `_aggregate_exclusive` output.
- `_assert_d33_reconciliation` takes two plain dicts (pure function, testable without mocks).

---

### Task 2: Register reconciliation_gate against publish stage

**Test commit (RED):** `7884d55`
**Implementation commit (GREEN):** `4aacd82`

**What shipped:**

- `gates/reconciliation.py` (new):
  - `@register_gate(stage="publish", severity=SEVERITY_BLOCK, name="reconciliation")`
  - D-17 arithmetic invariant: `incl_value < excl_value - 1e-9` is a violation
  - Violations capped at 50 entries; `truncated` flag; `violation_count` in details
  - `FLOAT_EPS = 1e-9` named constant at module level

- `gates/__init__.py` updated:
  - `from gates import reconciliation as _reconciliation_gate  # noqa: F401` added
  - Gate auto-registers on `import gates` via Pattern C decorator side effect

- Tests: 9 tests in `test_reconciliation_gate.py` — all GREEN

---

### Task 2.5 (W-3): SPS-Consumer Audit Checkpoint

**Status:** COMPLETE — audit performed by operator.

---

### Task 3: CSV rename with dual-write deprecation window (D-13 audit-safe path)

**Test commit (RED):** `54ade4f`
**Implementation commit (GREEN):** `9bf9e64`

**What shipped:**

- `count_by_cwid.py` refactored from top-level script to module-with-`__main__` pattern:
  - `write_subtopic_csvs(exclusive_counts, inclusive_counts, dest_dir)` — testable writer emitting `faculty_subtopic_counts_exclusive.csv` (canonical), `faculty_subtopic_counts_inclusive.csv`, and `cwid_subtopic_counts.csv` (legacy dual-write, byte-identical to exclusive per D-13)
  - `NEW_EXCLUSIVE_CSV`, `NEW_INCLUSIVE_CSV`, `LEGACY_CSV` constants at module level
  - `cwid_topic_counts.csv` emission unchanged (D-13 scope is subtopic-level only)

- `rollup_by_cwid.py` updated:
  - `DEFAULT_SUBTOPIC_CSV` changed to `faculty_subtopic_counts_exclusive.csv`
  - `LEGACY_SUBTOPIC_CSV = Path("cwid_subtopic_counts.csv")` added
  - `_resolve_subtopic_csv(provided=None)` added — prefers new name, falls back to legacy with `logger.warning(...)` deprecation warning
  - `--subtopic-csv` help text updated to document new default and legacy fallback
  - `main()` wired to use `_resolve_subtopic_csv(args.subtopic_csv)`

- `build_cwid_json.py` updated: same `_resolve_subtopic_csv` fallback pattern inlined at the read site

- `tests/test_rollup_incremental_parity.py` updated:
  - `_write_breakdown_csvs` fixture writes `faculty_subtopic_counts_exclusive.csv`
  - 5 new tests added — all GREEN:
    - `test_reader_falls_back_to_legacy_name`
    - `test_reader_prefers_new_name_when_both_exist`
    - `test_writer_emits_three_files`
    - `test_legacy_and_new_exclusive_have_identical_content`
    - `test_inclusive_csv_distinct_from_exclusive`

---

## W-3 Checkpoint: SPS-Consumer Audit Answer (Verbatim)

> audit performed — SPS confirms **no** downstream readers of `cwid_subtopic_counts.csv` (pinned commit `4e0e6b8`, 327 tracked files, zero hits across code / config / docs / other — see `.planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-aggregations-AUDIT-SPS-CSV.md` for the reproducible grep evidence). Phase 12 ships the rename + dual-write deprecation window per plan Task 3. Legacy `cwid_subtopic_counts.csv` can be dropped in Phase 13; no SPS-side coordination required.

**Audit evidence file:** `.planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-aggregations-AUDIT-SPS-CSV.md`

---

## Deviations from Plan

None — all three tasks executed as specified.

**count_by_cwid.py refactor (Task 3, Rule 3 — blocking fix):** The original `count_by_cwid.py` ran a DynamoDB scan at module import time, making it untestable. To satisfy `test_writer_emits_three_files` (which calls `cbc.write_subtopic_csvs(...)`), the file was refactored to the module-with-`__main__` pattern. CLI behavior is unchanged; this unblocked the test.

**`_resolve_subtopic_csv` inlined in build_cwid_json.py:** The plan said to import from `rollup_by_cwid` if it doesn't introduce a circular import. A check confirmed it would (shared `utils.dynamodb_helpers` chain). The helper was inlined as specified in the plan's conditional fallback.

---

## Known Stubs

`write_subtopic_csvs()` in `count_by_cwid.py` uses `exclusive_counts` for both the exclusive and inclusive arguments when called from `__main__` (the DynamoDB scan only projects `primary_subtopic_id`). This is documented with an inline comment pointing to `aggregate_subtopic_scores._aggregate_inclusive` as the correct inclusive data source for production use. `count_by_cwid.py` is a diagnostic script; the production inclusive aggregation runs in `aggregate_subtopic_scores.py`.

---

## Threat Flags

None — no new network endpoints, auth paths, or trust boundary crossings introduced.

---

## Self-Check

**Files verified present:**
- `aggregate_subtopic_scores.py` — modified (Tasks 1+2)
- `gates/reconciliation.py` — new (Task 2)
- `gates/__init__.py` — updated (Task 2)
- `docs/topic-subtopic-assignment.md` — updated (Task 1)
- `count_by_cwid.py` — refactored (Task 3)
- `rollup_by_cwid.py` — updated (Task 3)
- `build_cwid_json.py` — updated (Task 3)
- `tests/test_aggregate_inclusive.py` — new (Task 1)
- `tests/test_aggregate_idempotent.py` — new (Task 1)
- `tests/test_reconciliation_gate.py` — new (Task 2)
- `tests/test_rollup_incremental_parity.py` — updated (Task 3)

**Commits verified:**
- `4cbd2b5` — test(12-aggregations): RED Task 1
- `8af04b0` — feat(12-aggregations): GREEN Task 1
- `7884d55` — test(12-aggregations): RED Task 2
- `4aacd82` — feat(12-aggregations): GREEN Task 2
- `54ade4f` — test(12-aggregations): RED Task 3
- `9bf9e64` — feat(12-aggregations): GREEN Task 3

**Test run:** `pytest tests/test_rollup_incremental_parity.py -x` -> 15 passed

**Full related-suite:** `pytest tests/ -k "aggregate or reconciliation or rollup or gates" -x` -> 87 passed, 13 skipped

## Self-Check: PASSED

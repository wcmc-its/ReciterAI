---
phase: 12-feedback-loops-both-aggregations-residual-hygiene
plan: aggregations
subsystem: aggregation
status: checkpoint-paused
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
  affects:
    - aggregate_subtopic_scores.py (refactor + extension)
    - gates/__init__.py (adds reconciliation import)
    - docs/topic-subtopic-assignment.md (D-17 invariant paragraph)
tech_stack:
  added:
    - Decimal(str(float_val)) at DDB boundary (Pattern B, two call sites)
    - @register_gate(stage="publish", severity=SEVERITY_BLOCK, name="reconciliation")
  patterns:
    - TDD (RED/GREEN per task)
    - DDB idempotent overwrite via stable PK+SK (Pattern A)
    - Decimal coercion at DDB boundary (Pattern B)
    - Gate self-registration via decorator import (Pattern C)
key_files:
  created:
    - aggregate_subtopic_scores.py (refactored — new functions added)
    - gates/reconciliation.py
    - tests/test_aggregate_inclusive.py
    - tests/test_aggregate_idempotent.py
    - tests/test_reconciliation_gate.py
  modified:
    - aggregate_subtopic_scores.py
    - gates/__init__.py
    - docs/topic-subtopic-assignment.md
decisions:
  - "_aggregate_exclusive rename is behavior-identical to the old _aggregate; no logic changes"
  - "D-33 _assert_d33_reconciliation takes two dicts directly (not a MagicMock-captured partition) — purity for testability"
  - "W-2 no-tautology contract: D-33 test injects divergence via a corrupted_partition dict (not by mutating the shared in-memory dict)"
  - "Task 3 (CSV rename) gated by W-3 SPS-audit checkpoint per plan autonomous:false directive"
metrics:
  completed_date: "2026-05-13"
  tasks_completed: 2
  tasks_total: 3
  files_created: 5
  files_modified: 3
  plan_state: checkpoint-paused
---

# Phase 12 Plan aggregations Summary

**One-liner:** Dual-aggregation (exclusive + inclusive) implementation for spec §8 — two new DDB partitions (SUBTOPIC_SCORE# + SUBTOPIC_SCORE_INCLUSIVE#), D-33 in-stream invariant, and reconciliation gate registered against publish stage.

**Status:** CHECKPOINT-PAUSED — Tasks 1 and 2 committed; halted at W-3 SPS-audit operator checkpoint before Task 3 (CSV rename).

---

## Tasks Completed

### Task 1: Rename _aggregate → _aggregate_exclusive, add _aggregate_inclusive, dual-partition writes, D-33 invariant

**Commit:** `8af04b0`
**Test commit (RED):** `4cbd2b5`

**What shipped:**

- `aggregate_subtopic_scores.py` refactored:
  - `_aggregate` → `_aggregate_exclusive` (rename-only; semantics identical)
  - `_aggregate_inclusive(rows, *, confidence_floor)` added — iterates `subtopic_ids[]`, accumulates full `article_score` per entry (D-15 uniform full weight)
  - `_build_subtopic_score_record(*, kind, topic_id, subtopic_id, faculty_scores, run_id)` — pure builder for SUBTOPIC_SCORE# and SUBTOPIC_SCORE_INCLUSIVE# rows; Decimal coercion at DDB boundary (Pattern B)
  - `_write_subtopic_score_partitions(table, *, topic_id, faculty_scores_exclusive, faculty_scores_inclusive, run_id)` — writes both new partitions; D-17 invariant comment at the write site
  - `_assert_d33_reconciliation(faculty_map, subtopic_score_partition_data)` — in-stream invariant; raises RuntimeError on divergence (D-33 stage failure)
  - `run()` wired to call both aggregators, both partition writes, and D-33 check after faculty-map write

- `docs/topic-subtopic-assignment.md` — D-17 invariant paragraph added under "What gets written to the activity record"

- Tests: 13 tests across `test_aggregate_inclusive.py` (11) and `test_aggregate_idempotent.py` (2) — all GREEN

**Key decisions:**
- W-2 no-tautology contract: `test_d33_invariant_raises_on_divergence` injects divergence via a `corrupted_partition` dict (representing what landed at the put_item boundary), NOT by mutating the shared in-memory `_aggregate_exclusive` output. In-memory dicts remain equal by construction; the invariant must catch the split at the write site, not by comparing x == x.
- `_assert_d33_reconciliation` takes two plain dicts (pure function, testable without mocks) rather than capturing put_item call args. This is simpler and still catches the failure the D-33 spec describes.

### Task 2: Register reconciliation_gate against publish stage

**Commit:** `4aacd82`
**Test commit (RED):** `7884d55`

**What shipped:**

- `gates/reconciliation.py` (new):
  - `@register_gate(stage="publish", severity=SEVERITY_BLOCK, name="reconciliation")`
  - D-17 arithmetic invariant: `incl_value < excl_value - 1e-9` is a violation
  - Violations capped at 50 entries; `truncated` flag; `violation_count` in details
  - `FLOAT_EPS = 1e-9` named constant at module level

- `gates/__init__.py` updated:
  - `from gates import reconciliation as _reconciliation_gate  # noqa: F401` added
  - Gate auto-registers on `import gates` via Pattern C decorator side effect

- Tests: 9 tests in `test_reconciliation_gate.py` — all GREEN, including cross-gate regression with `test_gates_parent_prefix.py`

---

## W-3 Checkpoint: SPS-Consumer Audit (Task 2.5)

**Status:** HALTED — awaiting operator response.

**Context (from plan):**
Tasks 1+2 ship the new partitions and the reconciliation gate without touching the CSV filename contract. CONTEXT D-13 explicitly states: "do not commit the rename without that audit." The SPS-consumer audit is UNVERIFIABLE from this repo (separate codebase). Task 3 ships the dual-name safe-path default that CONTEXT D-13 explicitly authorizes, but the audit-then-rename is the long-term shape. This checkpoint records the operator's confirmation that the audit has been performed or is being explicitly deferred to a follow-up phase.

**Operator action required:**
1. Ping the Scholars Profile System (SPS) team or inspect the SPS codebase. Question: does anything in SPS read `cwid_subtopic_counts.csv` by that exact name today?
2. Record the answer in one of these forms:
   - "audit performed — SPS confirms <yes/no> downstream readers of `cwid_subtopic_counts.csv`. <yes → keep dual-name dual-write indefinitely until follow-up renames in SPS / no → can drop legacy name in next phase>."
   - "audit deferred — tracked in <follow-up issue path>. Phase 12 ships the dual-name safe-path default per CONTEXT D-13. Legacy `cwid_subtopic_counts.csv` continues to be emitted until the follow-up issue closes."

**Resume signal:** Paste the audit result (either "audit performed: ..." or "audit deferred: ..."). The continuation agent records it verbatim in this SUMMARY before proceeding to Task 3.

---

## Deviations from Plan

None — Tasks 1 and 2 executed exactly as specified. The W-2 no-tautology divergence test was refactored to use a `corrupted_partition` dict rather than a mutating put_item side_effect (both approaches satisfy the W-2 contract; the dict approach is cleaner and avoids dead code in the test body).

---

## Known Stubs

None — all new functions are fully implemented and write real DDB items. The `confidence_floor` parameter on `_aggregate_inclusive` is intentionally a no-op per D-16 (assign_subtopics.py already pre-filters); this is documented in the docstring, not a stub.

---

## Threat Flags

None — no new network endpoints, auth paths, or trust boundary crossings introduced. Both new DDB partitions use the same table/auth as existing FACULTY# partition writes.

## Self-Check: PENDING

This SUMMARY was written before Task 3 executes. The self-check will be completed by the continuation agent after Task 3 commits.

**Verified:**
- `aggregate_subtopic_scores.py` — modified with new functions (present in worktree)
- `gates/reconciliation.py` — new file (present in worktree)
- `gates/__init__.py` — updated with reconciliation import (present in worktree)
- `tests/test_aggregate_inclusive.py` — 11 tests, all GREEN
- `tests/test_aggregate_idempotent.py` — 2 tests, all GREEN
- `tests/test_reconciliation_gate.py` — 9 tests, all GREEN
- Commits: `4cbd2b5` (RED task1), `8af04b0` (GREEN task1), `7884d55` (RED task2), `4aacd82` (GREEN task2)

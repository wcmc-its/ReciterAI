---
phase: 06-spotlight-pipeline
plan: 03
subsystem: spotlight
tags: [dynamodb, boto3, pytest, dataclasses, decay, rotation, python]

# Dependency graph
requires:
  - phase: 06-spotlight-pipeline (Plan 06-01)
    provides: SPOTLIGHT_HISTORY# partition schema (docs/spotlight-dynamodb-schema.md)
  - phase: 06-spotlight-pipeline (Plan 06-02)
    provides: PoolEntry dataclass (spotlight/types.py), top-50 PoolEntry list from rank_pool()
provides:
  - "spotlight/rotation_selector.selection_score() — pool_score × (1 - exp(-weeks/12)) decay"
  - "spotlight/rotation_selector.fetch_history() — BatchGetItem on SPOTLIGHT_HISTORY# in chunks of 25, retries UnprocessedKeys once"
  - "spotlight/rotation_selector.select_with_diversity() — greedy one-per-parent-topic selection with deterministic tiebreaker; raises ValueError on selection floor failure"
  - "spotlight/rotation_selector.Selection (frozen dataclass: entry, sel_score, last_shown_at)"
  - "spotlight/history_writer.update_history() — UpdateItem per Selection writing shown_count + last_shown_at + last_shown_publish_id"
  - "16-test pytest suite (test_spotlight_rotation_selector.py) covering decay, diversity, floor, batching, retry, and write-path security"
affects: [06-05 lede-generator (consumes Selection list), 06-06 publish (calls update_history post-S3-PutObject)]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Constant UpdateExpression as module-level _UPDATE_EXPRESSION (T-06-03-01: no f-string interpolation in DynamoDB expressions)"
    - "BatchGetItem chunked at 25 with single UnprocessedKeys retry (T-06-03-03)"
    - "math.fromisoformat tolerance: malformed history values fall back to cold-start instead of raising (T-06-03-02)"
    - "ISO 8601 UTC Z-suffix timestamp via _now_iso_z() — matches Phase 5 convention (no microseconds)"
    - "Lazy boto3 singleton via _get_default_client() (mirrors pool_ranker.py)"

key-files:
  created:
    - "spotlight/rotation_selector.py (267 lines)"
    - "spotlight/history_writer.py (133 lines)"
    - "test_spotlight_rotation_selector.py (401 lines, 16 tests)"
  modified: []

key-decisions:
  - "selection_score returns pool_score directly on cold-start (last_shown_at=None) — explicit branch instead of relying on math.exp(-math.inf)=0 arithmetic. More readable, equivalent result."
  - "Malformed last_shown_at values are treated as cold-start (T-06-03-02 mitigation): a single corrupt history row cannot poison the rotation pipeline. Logged at WARNING level for operator visibility."
  - "UnprocessedKeys retry is single-shot with 100ms sleep; on second failure, affected subtopics silently fall back to cold-start (safe default)."
  - "select_with_diversity uses sorted() (not list.sort()) — defensive against caller aliasing; input pool is never mutated. Verified by test_07b."
  - "SELECTION_SIZE=10 is a FLOOR per RESEARCH §Open Q §8: insufficient distinct parent topics raises ValueError with operator-friendly message listing available parents."
  - "UpdateExpression is a module-level constant string. publish_id flows through ExpressionAttributeValues only; test_14 verifies a marker substring in publish_id never appears in the UpdateExpression."
  - "Wrote update_history to accept client=None and lazy-init via _get_default_client() so the publish CLI in 06-06 can omit the explicit client argument."

patterns-established:
  - "Module-level _UPDATE_EXPRESSION constant: keeps the literal away from any caller-controlled value, makes the security invariant grep-checkable in CI"
  - "ISO timestamp helper _now_iso_z() promoted to module scope — reusable by any future Phase 6 writer needing the same Phase-5-compatible format"

requirements-completed: [SPOT-03, SPOT-04]

# Metrics
duration: ~25min
completed: 2026-05-07
---

# Phase 6 Plan 03: Rotation Selector + History Writer Summary

**Exponential-decay rotation selector and DynamoDB history writer for the spotlight pipeline — the SPOT-03/04 layer that converts the top-50 PoolEntry list into 10 parent-diverse Selections and persists last-shown state for the next publish run.**

## Tasks Completed

| Task | Description | Commit |
|------|-------------|--------|
| 1 | TDD RED: 16 failing tests for rotation_selector + history_writer | `1209aaf` |
| 1 | TDD GREEN: rotation_selector.py — selection_score, fetch_history, select_with_diversity, Selection | `0588a82` |
| 2 | TDD GREEN: history_writer.py — update_history, _now_iso_z | `bfb6d84` |

All 16 tests pass; full spotlight test suite (48 tests across 4 modules) is green.

## Files

| File | Purpose | Key symbols |
|------|---------|-------------|
| `spotlight/rotation_selector.py` (267 lines) | SPOT-03 + SPOT-04 read | `selection_score` (line 96), `fetch_history` (line 135), `_ingest_responses` (line 188), `select_with_diversity` (line 203), `Selection` dataclass (line 60), `DECAY_TAU_WEEKS=12`, `SELECTION_SIZE=10`, `BATCH_GET_LIMIT=25` |
| `spotlight/history_writer.py` (133 lines) | SPOT-04 write | `update_history` (line 93), `_now_iso_z` (line 73), `_UPDATE_EXPRESSION` constant (line 44) |
| `test_spotlight_rotation_selector.py` (401 lines) | 16-test suite | tests 1-9b cover rotation_selector; tests 10-14 cover history_writer |

## Decay Curve Verification

| Weeks since last shown | Multiplier | Score (pool_score=100) | Test |
|------------------------|------------|------------------------|------|
| Cold-start (None) | 1.0 | 100.0 | test_01 |
| 1 week | 1 - exp(-1/12) ≈ 0.0800 | ~7.99 | test_02 |
| 12 weeks (τ) | 1 - exp(-1) ≈ 0.6321 | ~63.21 | test_03 |
| 39 weeks | 1 - exp(-39/12) ≈ 0.9613 | ~96.13 | test_04 |

Test fixtures use synthetic `datetime.now(timezone.utc) - timedelta(weeks=N)` offsets (not hard-coded real dates) so the suite is time-independent and CI-stable. Tolerance is ±0.5 for the 1-week test and ±1.0 for the 12-/39-week tests to absorb sub-second drift between fixture timestamp and `selection_score()` clock read.

## Threat Mitigations Applied

| Threat | Mitigation in code |
|--------|---------------------|
| T-06-03-01 (NoSQL injection via UpdateExpression) | `_UPDATE_EXPRESSION` is a module-level constant string; `publish_id` flows through `ExpressionAttributeValues[":pid"]` only. test_14 asserts a marker substring in publish_id never appears in the rendered UpdateExpression. Acceptance grep: `UpdateExpression=.*f"|UpdateExpression=.*format` returns 0. |
| T-06-03-02 (malformed last_shown_at) | `selection_score` wraps `datetime.fromisoformat` in try/except; parse failure → multiplier 1.0 (cold-start). Logged at WARNING for operator visibility. |
| T-06-03-03 (BatchGetItem UnprocessedKeys) | One retry with 100ms sleep; remaining unprocessed IDs surface as missing-from-result-dict (caller's `.get()` returns None → cold-start). test_09b verifies the retry path. |
| T-06-03-04 (info disclosure in logs) | `update_history` logs `len(selections)` and `publish_id` only — never subtopic_ids or lede text. |
| T-06-03-05 (selection floor bypass) | `select_with_diversity` raises `ValueError` containing "selection floor failure" when distinct parent topics < n. test_06 verifies. |

## Deviations from Plan

**None.** The plan was executed exactly as written. Two minor implementation choices fell within the latitude the plan granted:

1. The plan suggested `scored.sort(...)` in `select_with_diversity`; I used `sorted(scored, ...)` instead so a caller-aliased list is never mutated as a side effect. Behavior is identical; test_07b explicitly asserts the input is unchanged.

2. The plan suggested an explicit cold-start branch in `selection_score` (`if last_shown_at is None: return pool_score`). The plan also references RESEARCH §Pitfall 6 ("`math.exp(-math.inf) = 0.0` so the formula evaluates correctly without branching"). I kept the explicit branch — equivalent result, more readable, no special-case arithmetic.

## Self-Check: PASSED

- File exists: `spotlight/rotation_selector.py` — FOUND
- File exists: `spotlight/history_writer.py` — FOUND
- File exists: `test_spotlight_rotation_selector.py` — FOUND
- Commit `1209aaf` (RED test commit) — FOUND
- Commit `0588a82` (Task 1 GREEN: rotation_selector) — FOUND
- Commit `bfb6d84` (Task 2 GREEN: history_writer) — FOUND
- All 16 tests pass: `python3 -m pytest test_spotlight_rotation_selector.py -v` → 16 passed in 0.26s
- Full spotlight suite: 48 tests passed (no regression)
- Acceptance criteria (Task 1): all greps return expected counts; lazy init verified; cold-start invariant verified
- Acceptance criteria (Task 2): UpdateExpression literal contains all three required clauses; no f-string/format in UpdateExpression; ISO 8601 Z regex matches; no top-level boto3.client; no `cwid_` literal

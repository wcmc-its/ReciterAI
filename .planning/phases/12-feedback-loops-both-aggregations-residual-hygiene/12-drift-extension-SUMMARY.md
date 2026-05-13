---
phase: 12-feedback-loops-both-aggregations-residual-hygiene
plan: drift-extension
subsystem: pipeline_drift
tags: [drift, additive-schema, sparse, tdd]
requirements: [D-34, spec-§9-consumer-precondition]
dependency_graph:
  requires: []
  provides:
    - "DriftEvaluation.per_topic_low_confidence (dict[str,int]) on DRIFT#evaluation rows"
    - "Sparse-by-default field: absent key == zero (consumer reads via .get(key, {}))"
  affects:
    - "pipeline_feedback/sweep.py recluster trigger (wave-2 consumer reads this field)"
    - "DRIFT#evaluation DynamoDB rows (additive field; old rows remain readable)"
tech_stack:
  added: []
  patterns:
    - "Backwards-compatible additive dataclass field (Phase 11 D-13 run_id precedent)"
    - "Conditional-attach sparse DDB emit: `if self.per_topic_low_confidence: item[key] = ...`"
    - "TDD RED/GREEN cycle with behavior-contract non-regression tests"
key_files:
  created: []
  modified:
    - pipeline_drift/evaluator.py
    - tests/test_pipeline_drift_evaluator.py
decisions:
  - "Sparse representation: only topics with non-zero count appear as keys; absence == zero (D-34)"
  - "Counts stored as int (not Decimal) — counts are integer quantities, not floats"
  - "Filter expression `{k: v for k, v in per_topic.items() if v > 0}` is defensive; all entries are >= 1 by construction from the accumulator loop"
  - "Consumer safe-access pattern documented in test docstring: item.get('per_topic_low_confidence', {})"
metrics:
  duration: "< 15 minutes"
  completed: "2026-05-12"
  tasks_completed: 1
  tasks_total: 1
  files_modified: 2
  tests_added: 8
  tests_total: 26
---

# Phase 12 Plan drift-extension: DriftEvaluation per_topic_low_confidence Extension Summary

**One-liner:** Additive `per_topic_low_confidence: dict[str,int]` field on `DriftEvaluation` dataclass surfacing the per-topic count dict (previously computed and discarded) as a sparse DDB field for D-07's recluster trigger consumer.

## Tasks Completed

| # | Name | Commit | Files |
|---|------|--------|-------|
| RED | Failing tests for per_topic_low_confidence | 2bac179 | tests/test_pipeline_drift_evaluator.py |
| GREEN | Implement per_topic_low_confidence field + emit | 54514ea | pipeline_drift/evaluator.py |

## Acceptance Criteria Verification

- `grep -c "per_topic_low_confidence" pipeline_drift/evaluator.py` = **6** (>= 3 required)
- `grep -c "per_topic_low_confidence" tests/test_pipeline_drift_evaluator.py` = **29** (>= 5 required)
- `grep -nE "if self.per_topic_low_confidence" pipeline_drift/evaluator.py` = **line 106** (sparse-emit guard present)
- Empty dict → field absent from DDB item: **PASS**
- Non-empty dict → field present with correct int values: **PASS**
- `pytest tests/test_pipeline_drift_evaluator.py -x` = **26/26 passed**
- Pre-Phase-12 tests (21 tests, excluding new 8) all pass: **PASS**
- Git diff for `pipeline_drift/evaluator.py` touches only: dataclass header, `evaluate()` capture block, `to_dynamodb_item()` conditional-attach: **VERIFIED** (no edits to severity/alert/cold_run_recommended logic)

## Implementation Details

### Changes in `pipeline_drift/evaluator.py`

**Region 1: Dataclass field addition (after `severity`)**
```python
# Phase 12 D-34: per-topic LOW_CONFIDENCE# counts for this window.
# Sparse-by-default: only topics with non-zero count appear as keys.
# Absence == zero. Consumers MUST use .get(key, 0) to distinguish
# "key absent" from "row predates field" (treat both as zero).
per_topic_low_confidence: dict[str, int] = field(default_factory=dict)
```

**Region 2: `evaluate()` capture (after the existing per-topic max computation, before the `return`)**
```python
per_topic_low_confidence = {k: v for k, v in per_topic.items() if v > 0}
# ... passed to DriftEvaluation(..., per_topic_low_confidence=per_topic_low_confidence)
```

**Region 3: `to_dynamodb_item()` conditional-attach**
```python
if self.per_topic_low_confidence:
    item["per_topic_low_confidence"] = {
        str(k): int(v) for k, v in self.per_topic_low_confidence.items()
    }
```

### Tests added to `tests/test_pipeline_drift_evaluator.py`

Eight new tests in the `# Phase 12 D-34: per_topic_low_confidence` section:

1. `test_per_topic_low_confidence_populated_from_in_window_rows` — 2 rows for topic_a + 1 for topic_b → dict matches
2. `test_per_topic_low_confidence_sparse_zero_omitted` — only topic_a rows → topic_x absent from dict
3. `test_to_dynamodb_item_omits_field_when_dict_empty` — empty dict → field absent from DDB item
4. `test_to_dynamodb_item_includes_field_when_non_empty` — non-empty dict → present with int values
5. `test_low_confidence_max_topic_unchanged` — max_topic/max_count semantics preserved
6. `test_severity_ladder_unchanged` — severity computation unaffected by additive field
7. `test_cold_run_recommended_unchanged` — cold_run_recommended derivation unchanged
8. `test_consumer_reads_absent_field_as_absent` — documents safe `.get("per_topic_low_confidence", {})` consumer pattern for backward compatibility

## Deviations from Plan

None — plan executed exactly as written.

The `field(default_factory=dict)` import was already present in the module (`from dataclasses import dataclass, field` at line 28), so no import change was needed.

## Threat Flags

None. This change is purely additive to an existing DDB write path. No new network endpoints, auth paths, or trust boundaries introduced. The sparse emit pattern prevents O(taxonomy_size) row growth.

## Known Stubs

None. The field is wired end-to-end: `evaluate()` populates it from real in-window event rows; `to_dynamodb_item()` conditionally emits it to DynamoDB.

## Self-Check: PASSED

- `pipeline_drift/evaluator.py` modified: FOUND
- `tests/test_pipeline_drift_evaluator.py` modified: FOUND
- Commit 2bac179 exists: FOUND
- Commit 54514ea exists: FOUND
- All 26 tests pass: VERIFIED

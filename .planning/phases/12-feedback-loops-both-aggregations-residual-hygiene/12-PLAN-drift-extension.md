---
phase: 12-feedback-loops-both-aggregations-residual-hygiene
plan: drift-extension
type: execute
wave: 1
depends_on: []
files_modified:
  - pipeline_drift/evaluator.py
  - tests/test_pipeline_drift_evaluator.py
autonomous: true
requirements:
  - D-34
  - "spec-§9-consumer-precondition"
tags: [drift, additive-schema, sparse]
must_haves:
  truths:
    - "DriftEvaluation dataclass gains per_topic_low_confidence: dict[str,int]"
    - "DriftEvaluation.to_dynamodb_item() emits per_topic_low_confidence ONLY when non-empty (sparse-by-default)"
    - "Only topics whose LOW_CONFIDENCE_ASSIGNMENT# count is non-zero appear as keys (absence == zero)"
    - "Existing drift evaluator behavior unchanged: thresholds, severity logic, cold_run_recommended, alert ladder all preserved"
    - "Backwards-compatible: existing DRIFT#evaluation rows (without the field) remain readable; consumer presence-checks before reading"
  artifacts:
    - path: "pipeline_drift/evaluator.py"
      provides: "per_topic_low_confidence on DriftEvaluation + conditional emit in to_dynamodb_item"
      contains: "per_topic_low_confidence"
    - path: "tests/test_pipeline_drift_evaluator.py"
      provides: "Sparse-emit tests + non-regression tests for existing behavior"
      contains: "per_topic_low_confidence"
  key_links:
    - from: "pipeline_drift/evaluator.py DriftEvaluation.evaluate"
      to: "per-topic LOW_CONFIDENCE_ASSIGNMENT# counter (already computed internally; today the dict is discarded)"
      via: "expose via dataclass field"
      pattern: "per_topic_low_confidence"
    - from: "pipeline_drift/evaluator.py to_dynamodb_item"
      to: "DRIFT#evaluation DDB rows"
      via: "conditional attach pattern, identical to Phase 11 D-13 run_id precedent"
      pattern: "if self.per_topic_low_confidence:"
---

<objective>
Additive-only schema extension to `DriftEvaluation`: surface the per-topic low-confidence counter that the evaluator already computes internally (RESEARCH F-2 verified the value is computed, then discarded). The wave-2 feedback consumer's `RECLUSTER_RECOMMENDATION#` trigger needs materialized per-topic counts to apply D-07's "N consecutive days above the threshold" rule — re-scanning raw LOW_CONFIDENCE_ASSIGNMENT# rows is wasteful and risks divergence from what drift alerts already report.

Critical constraints (CONTEXT line 140, D-34):
- Sparse representation: only topics with non-zero count appear (key absent ≡ zero this day).
- Behavior contract preserved: thresholds, severity ladder, `cold_run_recommended` logic, alert dispatch — all UNCHANGED.
- Schema contract additive: existing DRIFT#evaluation rows without the field stay valid; consumers handle absence.

Output: pipeline_drift/evaluator.py with the additive field; tests cover sparse emit + non-regression.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@.planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-CONTEXT.md
@.planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-RESEARCH.md
@.planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-PATTERNS.md
@.planning/phases/10-hot-cold-path-split/10-SUMMARY.md

<interfaces>
<!-- DriftEvaluation existing shape (pipeline_drift/evaluator.py lines 73-96, verified RESEARCH F-2) -->

```python
@dataclass
class DriftEvaluation:
    window_start: str
    window_end: str
    drift_window_days: int
    uncovered_count: int
    low_confidence_count: int
    stage_failed_count: int
    new_pmid_count: int
    uncovered_rate: float
    low_confidence_max_topic: str        # single string: the worst topic this window
    low_confidence_max_count: int        # single int: count for that worst topic
    triggered_thresholds: list[str]
    cold_run_recommended: bool
    severity: str
    # NEW (Phase 12 D-34):
    per_topic_low_confidence: dict[str, int] = field(default_factory=dict)
```

Inside `evaluate()` (lines ~153-161 per PATTERNS.md), a per-topic dict is computed today to derive `low_confidence_max_topic` and `low_confidence_max_count`, then discarded. F-2 says: surface it instead.

Sparse rule (D-34): include only entries with `count > low_confidence_floor`-equivalent threshold. PATTERNS.md specifies the threshold is "the existing low_confidence_floor count threshold" — i.e. keep keys where count > 0 after the floor already applied during counting. Re-read evaluator.py to find the exact filter expression to mirror; do NOT introduce a NEW threshold.

Conditional-attach pattern (Pattern D from PATTERNS.md, Phase 11 D-13 run_id precedent):
```python
if self.per_topic_low_confidence:
    item["per_topic_low_confidence"] = {k: int(v) for k, v in self.per_topic_low_confidence.items()}
```

Behavior contract (CONTEXT line 140, NON-NEGOTIABLE):
- DO NOT change the threshold values used in the existing alert logic
- DO NOT change severity-tag computation
- DO NOT change `cold_run_recommended` calculation
- DO NOT change `low_confidence_max_topic` or `low_confidence_max_count` semantics
- DO NOT introduce CRITIC_REJECT# counts into drift (Deferred per CONTEXT, D-04 rationale)
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Add per_topic_low_confidence to DriftEvaluation, sparse-emit in to_dynamodb_item, non-regression tests</name>
  <files>pipeline_drift/evaluator.py, tests/test_pipeline_drift_evaluator.py</files>
  <read_first>
    - pipeline_drift/evaluator.py FULL file (need: DriftEvaluation dataclass header, to_dynamodb_item body, the evaluate() function's per-topic counting block ~lines 153-161, the alert/severity logic to confirm it stays untouched)
    - tests/test_pipeline_drift_evaluator.py FULL file (existing tests — extend; do NOT rewrite the file or refactor existing test names)
    - .planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-PATTERNS.md "pipeline_drift/evaluator.py per_topic_low_confidence extension" section
    - utils/stage_records.py lines 215-219 (the Phase 11 run_id additive precedent — same pattern, mirror it)
  </read_first>
  <behavior>
    - tests/test_pipeline_drift_evaluator.py::test_per_topic_low_confidence_populated_from_in_window_rows — feed evaluate() three LOW_CONFIDENCE_ASSIGNMENT# rows for topic_a (count=2) and one for topic_b (count=1); assert DriftEvaluation.per_topic_low_confidence == {"topic_a": 2, "topic_b": 1}
    - tests/test_pipeline_drift_evaluator.py::test_per_topic_low_confidence_sparse_zero_omitted — feed evaluate() rows that produce zero low-confidence events for topic_x but non-zero for topic_a; assert "topic_x" NOT in result.per_topic_low_confidence and "topic_a" in result.per_topic_low_confidence
    - tests/test_pipeline_drift_evaluator.py::test_to_dynamodb_item_omits_field_when_dict_empty — DriftEvaluation(per_topic_low_confidence={}); assert "per_topic_low_confidence" NOT in item produced by to_dynamodb_item()
    - tests/test_pipeline_drift_evaluator.py::test_to_dynamodb_item_includes_field_when_non_empty — DriftEvaluation(per_topic_low_confidence={"a":3,"b":1}); assert item["per_topic_low_confidence"] == {"a":3, "b":1} and all values are int (not Decimal — Decimal is for floats; counts are int)
    - tests/test_pipeline_drift_evaluator.py::test_low_confidence_max_topic_unchanged — same input as test_per_topic_low_confidence_populated; assert result.low_confidence_max_topic and result.low_confidence_max_count are computed the same way they were before (the worst-topic + worst-count semantics are preserved)
    - tests/test_pipeline_drift_evaluator.py::test_severity_ladder_unchanged — pick a known input that produces severity="warning" (or whatever pre-Phase-12 expected value the existing tests assert); assert it still produces that severity (behavior contract intact)
    - tests/test_pipeline_drift_evaluator.py::test_cold_run_recommended_unchanged — same: known input that produced cold_run_recommended=True before still produces True
    - tests/test_pipeline_drift_evaluator.py::test_consumer_reads_absent_field_as_absent — emulate a consumer reading an old DRIFT# row dict (no per_topic_low_confidence key); demonstrate the safe access pattern: `item.get("per_topic_low_confidence", {})` returns empty dict; document this in the test docstring as the consumer contract
  </behavior>
  <action>
    In `pipeline_drift/evaluator.py`:

    1. Add `from dataclasses import field` to the imports if not already imported (it should be — check the existing `from dataclasses import dataclass`).

    2. Extend the `DriftEvaluation` dataclass with the new field at the END of its current field list (before any methods):

    ```python
    @dataclass
    class DriftEvaluation:
        # ... all existing fields preserved in order ...
        severity: str
        # Phase 12 D-34: per-topic LOW_CONFIDENCE# counts for this window.
        # Sparse-by-default: only topics with non-zero count after the floor
        # appear as keys. Absence == zero. Read this via .get(key, 0) to
        # distinguish "key absent" from "row predates field" (treat both as zero).
        per_topic_low_confidence: dict[str, int] = field(default_factory=dict)
    ```

    3. In the `evaluate()` function, locate the per-topic counting block (~lines 153-161). Today it computes a `dict[str, int]` to derive `low_confidence_max_topic` and `low_confidence_max_count`, then the dict is discarded. **Capture the dict and pass it to the `DriftEvaluation(...)` constructor as `per_topic_low_confidence=` after filtering out zero-count entries.** Filter: `{k: v for k, v in per_topic_counts.items() if v > 0}`. Do NOT change the max-topic/max-count computation — leave those exactly as they are.

    4. Update `DriftEvaluation.to_dynamodb_item()` with the conditional-attach pattern:

    ```python
    def to_dynamodb_item(self) -> dict[str, Any]:
        item: dict[str, Any] = {
            # ... all existing fields unchanged ...
        }
        # Phase 12 D-34: sparse — emit only when populated. Same Phase 11 D-13
        # run_id precedent. Counts are int; no Decimal coercion needed.
        if self.per_topic_low_confidence:
            item["per_topic_low_confidence"] = {
                str(k): int(v) for k, v in self.per_topic_low_confidence.items()
            }
        return item
    ```

    5. **NEGATIVE-DELTA invariants** (do NOT touch):
       - `_severity_for_*` helpers (or whatever computes the severity tag)
       - The `cold_run_recommended` derivation
       - The alert-dispatch call site (if `pipeline_common.alert` is called from this module)
       - The `low_confidence_max_topic` and `low_confidence_max_count` assignments

    Extend `tests/test_pipeline_drift_evaluator.py` with the eight behaviors above. Mirror existing test fixture-construction style — do NOT introduce new fixture frameworks. The `test_severity_ladder_unchanged` and `test_cold_run_recommended_unchanged` tests are critical: they prove the additive change is in fact additive. If existing tests already cover these invariants, reference them in the new tests' docstrings instead of duplicating (e.g. "see test_severity_warning_when_uncovered_rate_high; this test asserts that invariant still holds after Phase 12 D-34's additive field").

    Commit message: `feat(12-drift): add sparse per_topic_low_confidence to DriftEvaluation (additive, behavior preserved)`.
  </action>
  <verify>
    <automated>cd /Users/paulalbert/Dropbox/GitHub/ReciterAI && pytest tests/test_pipeline_drift_evaluator.py -x</automated>
  </verify>
  <acceptance_criteria>
    - `grep -c "per_topic_low_confidence" pipeline_drift/evaluator.py` returns count >= 3 (dataclass field + evaluate() capture + to_dynamodb_item attach)
    - `grep -c "per_topic_low_confidence" tests/test_pipeline_drift_evaluator.py` returns count >= 5 (one per added test, minimum)
    - `grep -nE "if self.per_topic_low_confidence" pipeline_drift/evaluator.py` returns at least one match (sparse-emit guard present)
    - `python -c "from pipeline_drift.evaluator import DriftEvaluation; d=DriftEvaluation(window_start='', window_end='2026-05-12T00:00:00Z', drift_window_days=1, uncovered_count=0, low_confidence_count=0, stage_failed_count=0, new_pmid_count=0, uncovered_rate=0.0, low_confidence_max_topic='', low_confidence_max_count=0, triggered_thresholds=[], cold_run_recommended=False, severity='ok'); assert 'per_topic_low_confidence' not in d.to_dynamodb_item()"` exits 0 (sparse: empty dict not emitted)
    - `python -c "from pipeline_drift.evaluator import DriftEvaluation; d=DriftEvaluation(window_start='', window_end='2026-05-12T00:00:00Z', drift_window_days=1, uncovered_count=0, low_confidence_count=0, stage_failed_count=0, new_pmid_count=0, uncovered_rate=0.0, low_confidence_max_topic='', low_confidence_max_count=0, triggered_thresholds=[], cold_run_recommended=False, severity='ok', per_topic_low_confidence={'a':3}); item=d.to_dynamodb_item(); assert item['per_topic_low_confidence']=={'a':3}"` exits 0
    - `pytest tests/test_pipeline_drift_evaluator.py -x` exits 0
    - Pre-Phase-12 tests in `tests/test_pipeline_drift_evaluator.py` (anything not added by this plan) still pass: `pytest tests/test_pipeline_drift_evaluator.py -k "not per_topic_low_confidence and not consumer_reads_absent and not to_dynamodb_item_omits and not to_dynamodb_item_includes" -x` exits 0
    - No changes outside the dataclass + evaluate() capture + to_dynamodb_item: `git diff pipeline_drift/evaluator.py` shows hunks only in those three regions (no edits to severity/alert/cold_run_recommended logic)
  </acceptance_criteria>
  <done>Additive field exists, sparse-by-default, consumer can read absent-as-zero; existing evaluator behavior is provably unchanged via non-regression tests.</done>
</task>

</tasks>

<verification>
- Task 1 acceptance criteria all green
- Existing drift evaluator tests (everything not added by this plan) still pass
- `grep -c "per_topic_low_confidence" pipeline_drift/evaluator.py` >= 3
- The git diff for pipeline_drift/evaluator.py touches only the dataclass header, evaluate() per-topic capture, and to_dynamodb_item — confirms behavior-contract invariant
</verification>

<success_criteria>
The wave-2 feedback consumer can read materialized per-topic counts from DRIFT#evaluation rows without re-scanning raw LOW_CONFIDENCE_ASSIGNMENT# rows. Drift evaluator behavior is provably unchanged.
</success_criteria>

<output>
After completion, create `.planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-drift-extension-SUMMARY.md`.
</output>

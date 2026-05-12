---
phase: 12-feedback-loops-both-aggregations-residual-hygiene
plan: aggregations
type: execute
wave: 1
depends_on: []
files_modified:
  - aggregate_subtopic_scores.py
  - gates/reconciliation.py
  - gates/__init__.py
  - rollup_by_cwid.py
  - count_by_cwid.py
  - build_cwid_json.py
  - tests/test_aggregate_inclusive.py
  - tests/test_aggregate_idempotent.py
  - tests/test_reconciliation_gate.py
  - tests/test_rollup_incremental_parity.py
  - docs/topic-subtopic-assignment.md
autonomous: false  # has W-3 SPS-audit operator checkpoint between Task 2 and Task 3
requirements:
  - "spec-§8"
  - D-12
  - D-13
  - D-14
  - D-15
  - D-16
  - D-17
  - D-18
  - D-33
tags: [aggregation, ddb-partition, csv, reconciliation-gate]
must_haves:
  truths:
    - "Exclusive aggregation (primary subtopic only) AND inclusive aggregation (every above-floor subtopic_id) run side by side in aggregate_subtopic_scores.py"
    - "Two NEW DDB partitions exist: SUBTOPIC_SCORE#{topic}#{subtopic} and SUBTOPIC_SCORE_INCLUSIVE#{topic}#{subtopic}"
    - "Existing faculty.subtopic_scores.<topic_id> map is preserved as the SPS consumer contract (D-13 forbids SPS-side changes)"
    - "Two parallel CSVs: faculty_subtopic_counts_exclusive.csv (renamed from cwid_subtopic_counts.csv) and faculty_subtopic_counts_inclusive.csv"
    - "Reconciliation gate registered against the publish stage (NOT aggregation); on fail it blocks publish but leaves aggregation DDB rows readable"
    - "D-33 invariant lives in the aggregator code itself (immediately after both writes, before return): per-CWID per-subtopic equality between faculty-map and SUBTOPIC_SCORE# partition; divergence raises"
    - "D-17 arithmetic invariant documented at the write site AND in docs/topic-subtopic-assignment.md"
    - "Idempotent: re-run with same run_id cleanly overwrites both partitions; partition-level idempotency, not transactional (D-14)"
  artifacts:
    - path: "aggregate_subtopic_scores.py"
      provides: "_aggregate_exclusive (renamed) + _aggregate_inclusive (new) + dual-partition writers + D-33 reconciliation invariant in-stream"
      contains: "_aggregate_inclusive"
    - path: "gates/reconciliation.py"
      provides: "reconciliation_gate registered against publish stage; checks exclusive/inclusive arithmetic invariant (D-17) and per-CWID equality (D-33-derived)"
      contains: "@register_gate"
    - path: "gates/__init__.py"
      provides: "Import of gates.reconciliation so the decorator runs at process boot"
      contains: "reconciliation"
    - path: "rollup_by_cwid.py"
      provides: "Reads renamed CSV faculty_subtopic_counts_exclusive.csv; supports dual-name fallback for one deprecation window"
      contains: "faculty_subtopic_counts_exclusive"
    - path: "count_by_cwid.py"
      provides: "Writes BOTH new CSV names (faculty_subtopic_counts_exclusive.csv + faculty_subtopic_counts_inclusive.csv); legacy cwid_subtopic_counts.csv dual-write for one cycle"
      contains: "faculty_subtopic_counts_exclusive"
    - path: "docs/topic-subtopic-assignment.md"
      provides: "One-line D-17 invariant documented in prose"
      contains: "SUBTOPIC_SCORE_INCLUSIVE"
  key_links:
    - from: "aggregate_subtopic_scores.py _aggregate_inclusive"
      to: "DDB partition SUBTOPIC_SCORE_INCLUSIVE#{topic}#{subtopic}"
      via: "per-(topic,subtopic) put_item with Decimal-coerced article_score sum"
      pattern: "SUBTOPIC_SCORE_INCLUSIVE"
    - from: "gates/reconciliation.py"
      to: "publish stage"
      via: "@register_gate(stage='publish', severity=SEVERITY_BLOCK, name='reconciliation')"
      pattern: "@register_gate"
    - from: "aggregate_subtopic_scores.py end-of-stage"
      to: "D-33 invariant check (faculty-map ↔ SUBTOPIC_SCORE# equality)"
      via: "per-CWID per-subtopic equality assertion; divergence raises StageError"
      pattern: "reconciliation"
---

<objective>
Implement spec §8 both-aggregations work, the reconciliation gate, the in-stream D-33 invariant, and the CSV rename audit. After this plan, aggregation produces two parallel DDB partitions plus two parallel CSVs while preserving the existing SPS-facing faculty-map. A registered publish-stage gate enforces D-17's arithmetic invariant; a separate aggregator-internal check enforces D-33's per-CWID equality between the two derivations of the exclusive aggregation.

Purpose: the spec §8 "different populations, same keys" framing requires honest partition separation. Field collapse (one record with two `score` fields) reproduces G-19. Two partitions cost nothing at query time and guarantee the consumer's choice of column carries explicit semantics.

Output: aggregator refactor + new gate + CSV rename with dual-write deprecation window + tests + docs invariant line.
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
@.planning/phases/09-substrate-stages-and-gates/09-SUMMARY.md

<interfaces>
<!-- aggregate_subtopic_scores.py existing _aggregate signature (lines 108-167 — verified PATTERNS.md) -->

```python
def _aggregate(rows: list) -> tuple[dict, dict]:
    """Aggregate articleScore per (person_identifier, primary_subtopic_id)."""
    faculty_scores: dict = defaultdict(lambda: defaultdict(float))
    subtopic_total_weights: dict = defaultdict(float)
    # ... reads row.get("primary_subtopic_id") for exclusive aggregation
    # ... article_score = (impact_score / 100) ** 1.2 * relevance_score ** 1.4
    return ({pid: dict(m) for pid, m in faculty_scores.items()}, dict(subtopic_total_weights))
```

Phase 12 refactor:
- Rename `_aggregate` → `_aggregate_exclusive` (keep behavior identical)
- Add `_aggregate_inclusive(rows: list, *, confidence_floor: float) -> tuple[dict, dict]`:
  - Reads `row["subtopic_ids"]` (list of all above-floor assignments per D-16; writer already filtered)
  - Each subtopic_id in the list gets the FULL article_score (D-15 uniform full weight, NOT 1/N normalized)
  - confidence_floor on signature is documentation/type-safety; per D-16 do NOT re-filter (assign_subtopics.py:632 already filtered)

CSV consumers (verified via grep — Task 1 in this plan, F-1 deep-dive in RESEARCH):
- count_by_cwid.py:61 WRITES `cwid_subtopic_counts.csv`
- rollup_by_cwid.py:61 READS `cwid_subtopic_counts.csv` (DEFAULT_SUBTOPIC_CSV)
- build_cwid_json.py:12 READS `cwid_subtopic_counts.csv`
- tests/test_rollup_incremental_parity.py:68 READS `cwid_subtopic_counts.csv`

D-13 audit: SPS-side audit cannot be performed within this repo (separate repo). Take the safe path: emit BOTH `faculty_subtopic_counts_exclusive.csv` AND the legacy `cwid_subtopic_counts.csv` for ONE cold-run cycle, log a deprecation warning when any reader uses the legacy path. New CSV `faculty_subtopic_counts_inclusive.csv` is also emitted.

Reconciliation gate inputs (PATTERNS.md gates/reconciliation.py section):
```python
@register_gate(stage="publish", severity=SEVERITY_BLOCK, name="reconciliation")
def reconciliation_gate(*, exclusive_totals: dict, inclusive_totals: dict, **_: object) -> GateResult:
    """Check D-17 invariant on exclusive vs inclusive aggregation totals.
       - sum(inclusive[X][*]) >= sum(exclusive[X][*]) - FLOAT_EPS
       - delta = sum(inclusive) - sum(exclusive) >= 0
    Returns GateResult with violations list capped at 50.
    """
```

FLOAT_EPS = 1e-9 for the comparison.

D-33 in-stream check (different from D-18 gate — runs inside aggregator code, not at publish):
- After writing both faculty-map (existing SPS contract) AND SUBTOPIC_SCORE# (new partition), verify that for every (cwid, subtopic_id) the two derivations produce equal numbers (within 1e-9 tolerance)
- On divergence: RAISE — this is a stage failure, not a warning. Operators see the aggregator stage red in cold-path orchestrator output.
- D-33 applies to EXCLUSIVE pair only (the faculty-map has no parallel inclusive view today)

Gate registration auto-import: per PATTERNS.md Pattern C, the gate must be imported somewhere reachable at publish-time. Add `from gates import reconciliation as _reconciliation_gate` to `gates/__init__.py` so the decorator runs at process boot.

Existing article_score formula (verified line 565-567): `(impact_score / 100) ** 1.2 * relevance_score ** 1.4` — DO NOT modify; matches PM shared.ts byte-for-byte (P-10).
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Rename _aggregate → _aggregate_exclusive, add _aggregate_inclusive, write to two new DDB partitions, embed D-33 in-stream invariant</name>
  <files>aggregate_subtopic_scores.py, tests/test_aggregate_inclusive.py, tests/test_aggregate_idempotent.py, docs/topic-subtopic-assignment.md</files>
  <read_first>
    - aggregate_subtopic_scores.py FULL file (need: existing `_aggregate` body, the caller that writes faculty.subtopic_scores map, the DDB client/table object, the existing logger calls)
    - assign_subtopics.py lines 632-648 (where subtopic_ids[] is written — confirms D-16 confidence-floor filtering happens there, not in the aggregator)
    - utils/event_records.py (Decimal coercion idiom: Decimal(str(float_val)))
    - tests/test_event_records.py (analog for builder/aggregator shape tests with MagicMock table)
    - .planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-PATTERNS.md "aggregate_subtopic_scores.py inclusive aggregator" section
    - docs/topic-subtopic-assignment.md (find an appropriate section heading to attach the D-17 invariant line — likely near where existing aggregation is described)
  </read_first>
  <behavior>
    - tests/test_aggregate_inclusive.py::test_aggregate_exclusive_unchanged — feed three known rows (primary_subtopic_id="s1" for two, "s2" for one); assert `_aggregate_exclusive(rows)` returns the exact same shape as the pre-rename function would have (existing semantics preserved)
    - tests/test_aggregate_inclusive.py::test_aggregate_inclusive_uniform_full_weight — feed a row with subtopic_ids=["s1","s2","s3"] and article_score=10.0; assert faculty_scores[pid] for all three subtopics each gets 10.0 (D-15 uniform full weight, NOT 1/3 each)
    - tests/test_aggregate_inclusive.py::test_aggregate_inclusive_uses_subtopic_ids_not_primary — feed a row with primary_subtopic_id="primary" AND subtopic_ids=["s_a","s_b"] (where primary is NOT in subtopic_ids — edge case); assert inclusive aggregation uses subtopic_ids list verbatim (does NOT auto-add primary)
    - tests/test_aggregate_inclusive.py::test_aggregate_inclusive_invariant_holds — for any input set, assert sum(inclusive_totals.values()) >= sum(exclusive_totals.values()) - 1e-9
    - tests/test_aggregate_inclusive.py::test_aggregate_inclusive_invariant_equality_case — feed rows where every row has exactly one subtopic_id (singleton list); assert sum(inclusive) == sum(exclusive) within 1e-9 (D-17 equality clause)
    - tests/test_aggregate_inclusive.py::test_aggregate_inclusive_invariant_delta_case — feed rows where each row has ≥2 subtopic_ids; assert sum(inclusive) > sum(exclusive) (the delta IS the secondary contribution per D-17 second invariant)
    - tests/test_aggregate_inclusive.py::test_subtopic_score_partition_writes — call the dual-write function with a MagicMock table; assert at least one put_item call uses PK starting with "SUBTOPIC_SCORE#" (exclusive partition) and at least one with PK starting with "SUBTOPIC_SCORE_INCLUSIVE#"; assert all numeric values are Decimal (not float) at put_item boundary
    - tests/test_aggregate_inclusive.py::test_subtopic_score_partition_pk_format — captured PKs follow exactly the pattern `SUBTOPIC_SCORE#{topic}#{subtopic}` and `SUBTOPIC_SCORE_INCLUSIVE#{topic}#{subtopic}` with SK="GLOBAL"
    - tests/test_aggregate_inclusive.py::test_d33_invariant_raises_on_divergence — W-2 hardening: divergence MUST be injected at the **put_item boundary** of one writer (e.g. patch `mock_table.put_item` for SUBTOPIC_SCORE# calls to mutate the Item dict in flight, or wrap the faculty-map writer with a post-mutation shim), NOT in the shared in-memory `_aggregate_exclusive` output dict that both writers read. Reason: if both derivations read the same in-memory dict, they will always agree — `x == x` is a tautology and the invariant looks like it works when it actually catches nothing. Injecting at the put_item boundary forces the two derivations to disagree at the point where they actually leave the process, which is what the D-33 invariant is supposed to catch. Acceptance: assert the aggregator raises an exception (D-33: divergence is a stage failure, not a warning); assert exception message mentions "reconciliation" and the offending cwid+subtopic; **assert the in-memory aggregator output dicts were NOT mutated by the test fixture** (test pins the no-tautology contract)
    - tests/test_aggregate_inclusive.py::test_d33_invariant_passes_on_aligned_writes — happy path: faculty-map values and SUBTOPIC_SCORE# values are equal by construction; aggregator completes without raising
    - tests/test_aggregate_idempotent.py::test_rerun_same_rows_produces_identical_put_item_args — call the dual-write function twice with the same input rows + same run_id; assert the captured put_item call args (Item dicts) for run 2 are equal to run 1 (idempotent at partition level per D-14)
    - tests/test_aggregate_idempotent.py::test_rerun_overwrites_with_new_data — run 1 with rows_a, run 2 with rows_b (different); assert run 2's put_item calls reflect rows_b (DDB partition-level overwrite semantics — same PK, different value)
  </behavior>
  <action>
    In `aggregate_subtopic_scores.py`:

    1. **Rename**: change `def _aggregate(` to `def _aggregate_exclusive(` everywhere in the file (definition + all internal references). Update the docstring header to: "Aggregate articleScore per (person_identifier, primary_subtopic_id) — EXCLUSIVE aggregation. D-17 invariant: sum(SUBTOPIC_SCORE#X#*) ≤ sum(SUBTOPIC_SCORE_INCLUSIVE#X#*); equality iff every paper in topic X has exactly one above-floor subtopic assignment."

    2. **Add `_aggregate_inclusive`** immediately after `_aggregate_exclusive`. Mirror the exclusive function's structure precisely (same skip-counters, same float coercion, same logger.info shape, same returned dict-of-dict shape) but the inner loop iterates `for sid in (row.get("subtopic_ids") or []):` and accumulates the full `article_score` to each `sid`. Docstring: "Aggregate articleScore per (person_identifier, subtopic_id) for EVERY above-floor subtopic assignment — INCLUSIVE aggregation per D-15 (uniform full weight). Confidence floor is NOT re-applied here; assign_subtopics.py:632 already filters subtopic_ids[] to above-floor (D-16). The confidence_floor parameter is on the signature for type safety and future-proofing; today it is documentation, not behavior."

    3. **Add `_write_subtopic_score_partitions(table, *, faculty_scores_exclusive, faculty_scores_inclusive, run_id)`** as a new helper that, for every (cwid, subtopic_id, score) in each aggregation, computes the `topic_id` for that subtopic (via existing taxonomy lookup) and writes:
       - PK=`SUBTOPIC_SCORE#{topic_id}#{subtopic_id}`, SK="GLOBAL", record_type="SUBTOPIC_SCORE", topic_id, subtopic_id, faculty_scores={cwid: Decimal(str(score)), ...}, run_id, source_stage="aggregate_subtopic_scores", created_at=_now_iso()
       - PK=`SUBTOPIC_SCORE_INCLUSIVE#{topic_id}#{subtopic_id}`, SK="GLOBAL", record_type="SUBTOPIC_SCORE_INCLUSIVE", same other fields, faculty_scores comes from the inclusive map.
       Wrap as `_build_subtopic_score_record(*, kind, topic_id, subtopic_id, faculty_scores, run_id)` returning the dict (pure builder, testable). Use `Decimal(str(float_val))` for every numeric value (Pattern B).

       Inline comment at the function header: `# D-17 invariant: sum(SUBTOPIC_SCORE_INCLUSIVE#X#*) >= sum(SUBTOPIC_SCORE#X#*); difference == secondary contribution in topic X. Documented at the write site per D-17 — search this comment if you wonder why two partitions exist.`

    4. **Add D-33 in-stream invariant** as `_assert_d33_reconciliation(faculty_map, subtopic_score_partition_data)` called immediately after both writes complete and BEFORE returning from the aggregator's main stage function. Body: for every (cwid, subtopic_id) in either source, verify the two derivations equal within 1e-9. On mismatch, build a violation list capped at 50 entries, then `raise RuntimeError(f"D-33 reconciliation invariant violated: {len(violations)} (cwid, subtopic) pairs disagree. First: {violations[0]}. Stage aborts; faculty-map and SUBTOPIC_SCORE# partition disagree.")` — this is a stage failure per D-33 ("divergence is a stage failure, not a warning").

    5. **Update the main aggregator entry point** (whatever the caller is — the existing function that orchestrates the read → aggregate → write flow). Wire: read rows once → call `_aggregate_exclusive(rows)` AND `_aggregate_inclusive(rows, confidence_floor=DEFAULT_CONFIDENCE_FLOOR)` → write the existing faculty.subtopic_scores map (unchanged path, SPS contract) → write `SUBTOPIC_SCORE#` partition → write `SUBTOPIC_SCORE_INCLUSIVE#` partition → call `_assert_d33_reconciliation(...)`. The run_id is threaded from the cold-run env var (`RECITERAI_COLD_RUN_ID`) per Phase 11 D-13 precedent; if absent, generate one for the stage.

    In `docs/topic-subtopic-assignment.md`: add a paragraph somewhere logical (probably immediately after the existing description of how subtopic_ids[] is computed). Exact text:

    > **Both aggregations (Phase 12 §8):** Exclusive aggregation rolls up each activity row's `primary_subtopic_id` only; inclusive aggregation rolls up every entry in `subtopic_ids[]` at full `article_score` per entry. Invariant (D-17): `sum(SUBTOPIC_SCORE_INCLUSIVE#X#*) ≥ sum(SUBTOPIC_SCORE#X#*)`; equality iff every paper in topic X has exactly one above-floor subtopic assignment. The difference equals the total `article_score` contributed by secondary assignments in topic X. The reconciliation gate (`gates/reconciliation.py`) verifies this invariant before publish; a separate aggregator-internal invariant (D-33) verifies per-CWID per-subtopic equality between the legacy `faculty.subtopic_scores` map and the new `SUBTOPIC_SCORE#` partition.

    Write the two test files. Use MagicMock for the DDB table; capture `put_item.call_args_list` to assert PK formats and Decimal types. Module docstrings list every test case.

    Commit message: `feat(12-aggregations): add _aggregate_inclusive + dual-partition writes + D-33 in-stream invariant`.
  </action>
  <verify>
    <automated>cd /Users/paulalbert/Dropbox/GitHub/ReciterAI && pytest tests/test_aggregate_inclusive.py tests/test_aggregate_idempotent.py -x</automated>
  </verify>
  <acceptance_criteria>
    - `grep -c "def _aggregate_exclusive" aggregate_subtopic_scores.py` returns 1
    - `grep -c "def _aggregate_inclusive" aggregate_subtopic_scores.py` returns 1
    - `grep -cE "^def _aggregate\\(" aggregate_subtopic_scores.py` returns 0 (old name fully removed)
    - `grep -c "SUBTOPIC_SCORE_INCLUSIVE" aggregate_subtopic_scores.py` returns count >= 2
    - `grep -c "SUBTOPIC_SCORE#" aggregate_subtopic_scores.py` returns count >= 1
    - `grep -c "D-17 invariant" aggregate_subtopic_scores.py` returns count >= 1 (comment present at write site)
    - `grep -c "D-33" aggregate_subtopic_scores.py` returns count >= 1 (in-stream invariant referenced)
    - `grep -c "Decimal(str(" aggregate_subtopic_scores.py` returns count >= 2 (Pattern B coercion at DDB boundary)
    - `grep -c "SUBTOPIC_SCORE_INCLUSIVE" docs/topic-subtopic-assignment.md` returns count >= 1 (D-17 invariant in prose)
    - `grep -c "D-17" docs/topic-subtopic-assignment.md` returns count >= 1
    - `pytest tests/test_aggregate_inclusive.py tests/test_aggregate_idempotent.py -x` exits 0
    - No regression: `pytest tests/ -k "aggregate" -x` exits 0
    - **W-2 contract pinned**: `test_d33_invariant_raises_on_divergence` injects divergence at the put_item boundary of one writer (NOT in the shared in-memory `_aggregate_exclusive` dict). Verify by inspecting the test body — it must patch a writer call (e.g. `mock_table.put_item` side_effect that mutates Item for SUBTOPIC_SCORE# calls, OR a wrapper around the faculty-map writer) rather than mutating the dict returned by `_aggregate_exclusive`. The test asserts the in-memory aggregator output is unchanged after the divergence injection.
  </acceptance_criteria>
  <done>Both aggregators exist side by side; two new DDB partitions are written; D-33 in-stream invariant catches split-brain immediately at the put_item boundary (not in the shared in-memory dict — the W-2 no-tautology contract); D-17 invariant documented at the write site and in docs.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Register reconciliation_gate against publish stage</name>
  <files>gates/reconciliation.py, gates/__init__.py, tests/test_reconciliation_gate.py</files>
  <read_first>
    - gates/registry.py (full file — GateResult dataclass, register_gate decorator signature, SEVERITY_BLOCK constant)
    - gates/parent_prefix.py (full file — model gate to mirror; lines 38-94 are the gate body shape)
    - gates/schema_validation.py (alternative gate analog; also registered against publish stage)
    - gates/__init__.py (check current state — may already import other gates; mirror that pattern)
    - tests/test_gates_parent_prefix.py (analog test file for the new gate's tests)
    - .planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-PATTERNS.md "gates/reconciliation.py" section + Pattern C
    - Task 1's aggregate_subtopic_scores.py (so the gate receives the same data shape the aggregator produces)
  </read_first>
  <behavior>
    - tests/test_reconciliation_gate.py::test_passes_on_aligned_totals — exclusive_totals={"topic_a":{"s1":1.0,"s2":2.0}}, inclusive_totals={"topic_a":{"s1":1.0,"s2":2.5}}; assert result.passed is True, result.summary mentions "invariant holds"
    - tests/test_reconciliation_gate.py::test_fails_when_inclusive_lt_exclusive — exclusive_totals={"topic_a":{"s1":5.0}}, inclusive_totals={"topic_a":{"s1":3.0}} (impossible state; inclusive < exclusive by 2.0); assert result.passed is False, result.severity == SEVERITY_BLOCK, result.details["violations"] is non-empty
    - tests/test_reconciliation_gate.py::test_passes_within_float_tolerance — exclusive_totals={"topic_a":{"s1":1.0}}, inclusive_totals={"topic_a":{"s1":1.0 - 1e-12}}; assert result.passed is True (1e-9 epsilon absorbs IEEE 754 noise)
    - tests/test_reconciliation_gate.py::test_fails_outside_float_tolerance — exclusive_totals={"topic_a":{"s1":1.0}}, inclusive_totals={"topic_a":{"s1":1.0 - 1e-6}}; assert result.passed is False
    - tests/test_reconciliation_gate.py::test_violations_capped_at_50 — feed 100 mismatched (topic, subtopic) pairs; assert len(result.details["violations"]) <= 50 and result.details.get("truncated") is True and result.details["violation_count"] == 100
    - tests/test_reconciliation_gate.py::test_gate_registered_against_publish_stage — `from gates.registry import _REGISTRY` (or whatever the registry accessor is); assert a gate with name="reconciliation" is registered for stage="publish" with severity=SEVERITY_BLOCK
    - tests/test_reconciliation_gate.py::test_decorator_import_runs_on_package_load — `import gates`; assert the reconciliation gate is registered (because gates/__init__.py imports gates.reconciliation, triggering the decorator)
    - tests/test_reconciliation_gate.py::test_passes_when_empty_inputs — exclusive_totals={}, inclusive_totals={}; assert result.passed is True (empty corpus is not a failure mode)
  </behavior>
  <action>
    Create `gates/reconciliation.py` mirroring `gates/parent_prefix.py` structure:

    ```python
    """reconciliation gate — Phase 12 D-18 + D-17.

    Verifies the arithmetic invariant between the exclusive (primary-subtopic)
    and inclusive (all-above-floor) aggregations before the hierarchy is
    published:

        sum(SUBTOPIC_SCORE_INCLUSIVE#X#*) >= sum(SUBTOPIC_SCORE#X#*) - 1e-9

    Equality iff every paper in topic X has exactly one above-floor subtopic
    assignment (D-17). The gate fails the *publish* stage — not aggregation
    — so operators retain readable aggregation rows for diagnosis (D-18).

    A separate in-stream invariant (D-33) inside aggregate_subtopic_scores.py
    enforces per-CWID per-subtopic equality between the legacy faculty.map
    derivation and the new SUBTOPIC_SCORE# partition. That invariant runs
    earlier (in the aggregator stage) and raises rather than returning a
    GateResult.
    """

    from __future__ import annotations
    from typing import Any
    from gates.registry import GateResult, SEVERITY_BLOCK, register_gate

    FLOAT_EPS = 1e-9


    @register_gate(stage="publish", severity=SEVERITY_BLOCK, name="reconciliation")
    def reconciliation_gate(
        *,
        exclusive_totals: dict[str, dict[str, float]] | None = None,
        inclusive_totals: dict[str, dict[str, float]] | None = None,
        **_: object,
    ) -> GateResult:
        """Verify exclusive ≤ inclusive across every (topic, subtopic) pair.

        Inputs: two nested dicts shaped `{topic_id: {subtopic_id: float}}`.
        Missing pair on inclusive side is treated as 0.0 (and is a violation
        if exclusive has a non-zero value there). Missing pair on exclusive
        is fine (inclusive can have above-floor secondaries without primaries).
        """
        exclusive_totals = exclusive_totals or {}
        inclusive_totals = inclusive_totals or {}

        violations: list[dict[str, Any]] = []
        for topic, sub_map in exclusive_totals.items():
            incl_sub_map = inclusive_totals.get(topic, {})
            for subtopic, excl_value in sub_map.items():
                incl_value = float(incl_sub_map.get(subtopic, 0.0))
                excl_value = float(excl_value)
                if incl_value < excl_value - FLOAT_EPS:
                    violations.append({
                        "topic_id": topic,
                        "subtopic_id": subtopic,
                        "exclusive": excl_value,
                        "inclusive": incl_value,
                        "delta": incl_value - excl_value,
                    })

        if violations:
            return GateResult(
                name="reconciliation",
                passed=False,
                severity=SEVERITY_BLOCK,
                summary=(
                    f"{len(violations)} (topic, subtopic) pair(s) violate D-17 "
                    f"invariant (inclusive < exclusive within 1e-9 tolerance)"
                ),
                details={
                    "violation_count": len(violations),
                    "violations": violations[:50],
                    "truncated": len(violations) > 50,
                    "float_eps": FLOAT_EPS,
                },
            )

        return GateResult(
            name="reconciliation",
            passed=True,
            severity=SEVERITY_BLOCK,
            summary="D-17 reconciliation invariant holds across all (topic, subtopic) pairs",
        )
    ```

    In `gates/__init__.py`: read the file first to see how existing gates are imported. Add a line registering reconciliation: `from gates import reconciliation as _reconciliation_gate  # noqa: F401  # Phase 12 D-18: decorator-import side effect registers the gate`. Order it alongside the existing gate imports (parent_prefix, schema_validation, pii_scan, schema_roundtrip). If gates/__init__.py is currently empty or does not import gates by side effect, the canonical place to put this import is here so `import gates` at process boot is sufficient to register all gates.

    Write `tests/test_reconciliation_gate.py` mirroring `tests/test_gates_parent_prefix.py` structure. Imports: `import pytest`, `from gates.reconciliation import reconciliation_gate`, `from gates.registry import GateResult, SEVERITY_BLOCK, _REGISTRY` (or the registry-inspection helper, whichever exists). The registration test asserts `("publish", "reconciliation")` appears in `_REGISTRY` keys (adapt to the actual registry data structure).

    Commit message: `feat(12-aggregations): register reconciliation gate against publish stage`.
  </action>
  <verify>
    <automated>cd /Users/paulalbert/Dropbox/GitHub/ReciterAI && pytest tests/test_reconciliation_gate.py -x</automated>
  </verify>
  <acceptance_criteria>
    - File `gates/reconciliation.py` exists
    - `grep -c "@register_gate" gates/reconciliation.py` returns 1
    - `grep -E 'stage="publish".*name="reconciliation"' gates/reconciliation.py` returns count >= 1
    - `grep -E 'SEVERITY_BLOCK' gates/reconciliation.py` returns count >= 2 (decorator arg + GateResult body)
    - `grep -c "reconciliation" gates/__init__.py` returns count >= 1
    - `python -c "import gates; from gates.registry import _REGISTRY; print(_REGISTRY)" 2>&1 | grep -q "reconciliation"` exits 0 (gate auto-registers on import; adapt the inspection if the registry uses a different accessor)
    - `pytest tests/test_reconciliation_gate.py -x` exits 0
    - `pytest tests/test_gates_parent_prefix.py tests/test_reconciliation_gate.py -x` exits 0 (no cross-gate regression)
  </acceptance_criteria>
  <done>Reconciliation gate auto-registers on `import gates`; passes on aligned totals, fails with structured details on injected mismatch, capped at 50 violations.</done>
</task>

<task type="checkpoint:human-verify" gate="blocking">
  <name>Task 2.5 (W-3): Operator confirms SPS-consumer audit performed (or explicitly deferred)</name>
  <what-built>
    Tasks 1+2 ship the new partitions and the reconciliation gate without touching the CSV filename contract. CONTEXT D-13 explicitly states: "do not commit the rename without that audit." The SPS-consumer audit is UNVERIFIABLE from this repo (separate codebase). Task 3 ships the dual-name safe-path default that CONTEXT D-13 explicitly authorizes, but the audit-then-rename is the long-term shape. This checkpoint records the operator's confirmation that the audit has been performed or is being explicitly deferred to a follow-up phase.
  </what-built>
  <how-to-verify>
    1. Operator pings the Scholars Profile System (SPS) team or otherwise inspects the SPS codebase. Question to answer: does anything in SPS read `cwid_subtopic_counts.csv` by that exact name today?
    2. Record the answer in one of these two forms:
       - "audit performed — SPS confirms <yes/no> downstream readers of `cwid_subtopic_counts.csv`. <yes → keep dual-name dual-write indefinitely until a follow-up renames in SPS / no → can drop legacy name in the next phase>."
       - "audit deferred — tracked in <new follow-up issue path>. Phase 12 ships the dual-name safe-path default per CONTEXT D-13. Legacy `cwid_subtopic_counts.csv` continues to be emitted until the follow-up issue closes."
    3. This answer is the load-bearing artifact: the Phase 12 SUMMARY MUST quote it verbatim so future phases have provenance for the decision.
  </how-to-verify>
  <resume-signal>Paste the audit result (either "audit performed: ..." or "audit deferred: ..."). The executor records it verbatim in the Phase 12 SUMMARY before proceeding to Task 3.</resume-signal>
</task>

<task type="auto" tdd="true">
  <name>Task 3: CSV rename with dual-write deprecation window (D-13 audit-safe path)</name>
  <files>count_by_cwid.py, rollup_by_cwid.py, build_cwid_json.py, tests/test_rollup_incremental_parity.py</files>
  <read_first>
    - count_by_cwid.py FULL file (writer: line 61 writes cwid_subtopic_counts.csv; understand what columns it emits, what the inclusive companion needs to emit)
    - rollup_by_cwid.py FULL file (reader: DEFAULT_SUBTOPIC_CSV at line 61, --help text at line 275)
    - build_cwid_json.py (reader: line 12 reads cwid_subtopic_counts.csv)
    - tests/test_rollup_incremental_parity.py:68 (test that reads cwid_subtopic_counts.csv — update path-handling but preserve the parity invariant the test enforces)
    - pipeline_common/alert.py (the warning-emission channel; we may use logger warnings instead — see the action note)
    - .planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-CONTEXT.md D-13 (CSV rename is a contract change — dual-name for one cycle since SPS-side audit cannot happen from this repo)
  </read_first>
  <behavior>
    - tests/test_rollup_incremental_parity.py (existing tests preserved) — UPDATE: subtopic_csv fixture file path becomes faculty_subtopic_counts_exclusive.csv; assert rollup_by_cwid.py reads it
    - New test in tests/test_rollup_incremental_parity.py::test_reader_falls_back_to_legacy_name — write only the old-name file (cwid_subtopic_counts.csv); assert rollup_by_cwid.py still loads it AND emits a deprecation warning (logging.warning captured via caplog)
    - New test in tests/test_rollup_incremental_parity.py::test_reader_prefers_new_name_when_both_exist — write both old + new files with different contents; assert rollup_by_cwid.py reads the NEW name (faculty_subtopic_counts_exclusive.csv) and does NOT emit deprecation warning
    - New test in tests/test_rollup_incremental_parity.py::test_writer_emits_three_files — invoke count_by_cwid.py's writer; assert faculty_subtopic_counts_exclusive.csv, faculty_subtopic_counts_inclusive.csv, AND legacy cwid_subtopic_counts.csv (dual-write for one cycle per D-13) all exist
    - New test in tests/test_rollup_incremental_parity.py::test_legacy_and_new_exclusive_have_identical_content — open both files, parse, assert row-for-row identical (the legacy file is literally a copy for the deprecation window)
    - New test in tests/test_rollup_incremental_parity.py::test_inclusive_csv_distinct_from_exclusive — open faculty_subtopic_counts_inclusive.csv and faculty_subtopic_counts_exclusive.csv; if any row has subtopic_ids count >= 2 in the source data, inclusive totals MUST be > exclusive totals for at least one (cwid, subtopic) pair
  </behavior>
  <action>
    In `count_by_cwid.py`:

    1. Change line 61 area to write all three files. The two new files (exclusive + inclusive) are the canonical artifacts; the legacy name is a dual-write for one cycle per D-13.

       ```python
       NEW_EXCLUSIVE_CSV = "faculty_subtopic_counts_exclusive.csv"
       NEW_INCLUSIVE_CSV = "faculty_subtopic_counts_inclusive.csv"
       LEGACY_CSV = "cwid_subtopic_counts.csv"   # Phase 12 D-13 dual-write; remove in a later phase
       ```

       Write the SAME row content to NEW_EXCLUSIVE_CSV and LEGACY_CSV (byte-identical — the legacy file is a copy). Compute the inclusive aggregation rows separately (use aggregate_subtopic_scores._aggregate_inclusive output if accessible, or re-aggregate from the source data) and write to NEW_INCLUSIVE_CSV. Update the final print statement at line 67 to mention all three filenames AND a deprecation note for the legacy.

    2. **DO NOT** delete the cwid_topic_counts.csv emission — only the subtopic-level rename applies (D-13).

    In `rollup_by_cwid.py`:

    1. Change `DEFAULT_SUBTOPIC_CSV = Path("cwid_subtopic_counts.csv")` to `DEFAULT_SUBTOPIC_CSV = Path("faculty_subtopic_counts_exclusive.csv")`.

    2. Add a fallback layer at the read site:

       ```python
       LEGACY_SUBTOPIC_CSV = Path("cwid_subtopic_counts.csv")

       def _resolve_subtopic_csv(provided: Path | None = None) -> Path:
           """Prefer the Phase 12 D-13 new name; fall back to legacy with a deprecation warning."""
           if provided is not None:
               return provided
           if DEFAULT_SUBTOPIC_CSV.exists():
               return DEFAULT_SUBTOPIC_CSV
           if LEGACY_SUBTOPIC_CSV.exists():
               logger.warning(
                   "Reading legacy CSV name '%s'. Phase 12 D-13 renamed this to '%s'. "
                   "Update producers to write the new name; this fallback will be removed in a later phase.",
                   LEGACY_SUBTOPIC_CSV, DEFAULT_SUBTOPIC_CSV,
               )
               return LEGACY_SUBTOPIC_CSV
           return DEFAULT_SUBTOPIC_CSV  # will fail downstream with a clear "file not found"
       ```

    3. Update the --help text at line 275 to mention the new default and the legacy fallback.

    In `build_cwid_json.py`:

    1. Apply the same fallback pattern as rollup_by_cwid.py at line 12. If you want a tighter cut, just import `_resolve_subtopic_csv` from rollup_by_cwid.py — but only if doing so doesn't introduce a circular import. If it does, inline the helper.

    In `tests/test_rollup_incremental_parity.py`:

    1. Update the existing test at line 68 to write `faculty_subtopic_counts_exclusive.csv` instead of `cwid_subtopic_counts.csv`. Preserve every existing assertion — the parity test is load-bearing.

    2. Add the four new tests listed in `<behavior>`. Use pytest's `caplog` fixture to capture the deprecation warning. Use `tmp_path` fixture and `monkeypatch.chdir(tmp_path)` to isolate filesystem state.

    Commit message: `feat(12-aggregations): rename CSV to faculty_subtopic_counts_exclusive + add inclusive emit + D-13 dual-write deprecation window`.
  </action>
  <verify>
    <automated>cd /Users/paulalbert/Dropbox/GitHub/ReciterAI && pytest tests/test_rollup_incremental_parity.py -x</automated>
  </verify>
  <acceptance_criteria>
    - `grep -c "faculty_subtopic_counts_exclusive" count_by_cwid.py` returns count >= 1
    - `grep -c "faculty_subtopic_counts_inclusive" count_by_cwid.py` returns count >= 1
    - `grep -c "cwid_subtopic_counts.csv" count_by_cwid.py` returns count >= 1 (legacy dual-write preserved for one cycle per D-13)
    - `grep -c "faculty_subtopic_counts_exclusive" rollup_by_cwid.py` returns count >= 2 (DEFAULT_SUBTOPIC_CSV + fallback logic)
    - `grep -c "deprecat" rollup_by_cwid.py` returns count >= 1 (deprecation warning text)
    - `grep -c "logger.warning\|logging.warning" rollup_by_cwid.py` returns count >= 1 (warning channel)
    - `pytest tests/test_rollup_incremental_parity.py -x` exits 0
    - `grep -c "test_reader_falls_back_to_legacy_name\|test_reader_prefers_new_name_when_both_exist\|test_writer_emits_three_files" tests/test_rollup_incremental_parity.py` returns count >= 3
  </acceptance_criteria>
  <done>Producer writes new exclusive + new inclusive + legacy CSVs; readers prefer new name, fall back to legacy with deprecation warning; parity test green; all four new tests green.</done>
</task>

</tasks>

<verification>
- All three tasks' acceptance criteria green
- Full file diffs: aggregate_subtopic_scores.py touched in three regions (rename, new function, new helper/writer, D-33 invariant); gates/__init__.py adds one import line; gates/reconciliation.py is new; CSV producers/readers updated
- No regression: `pytest tests/ -k "aggregate or reconciliation or rollup or gates" -x` exits 0
- Reconciliation gate auto-registers on `import gates`
</verification>

<success_criteria>
spec §8 ships: two parallel DDB partitions, two parallel CSVs (plus legacy for one cycle), D-17 invariant gate, D-33 in-stream invariant. Field collapse (G-19 pattern) is avoided.
</success_criteria>

<output>
After completion, create `.planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-aggregations-SUMMARY.md`.
</output>

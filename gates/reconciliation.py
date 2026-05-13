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
    """Verify exclusive <= inclusive across every (topic, subtopic) pair.

    Inputs: two nested dicts shaped `{topic_id: {subtopic_id: float}}`.
    Missing pair on inclusive side is treated as 0.0 (and is a violation
    if exclusive has a non-zero value there). Missing pair on exclusive
    is fine (inclusive can have above-floor secondaries without primaries).

    Returns GateResult(passed=True) when D-17 invariant holds for all pairs.
    Returns GateResult(passed=False, severity=SEVERITY_BLOCK) with structured
    violations (capped at 50) when any pair violates the invariant.
    """
    exclusive_totals = exclusive_totals or {}
    inclusive_totals = inclusive_totals or {}

    violations: list[dict[str, Any]] = []
    for topic, sub_map in exclusive_totals.items():
        incl_sub_map = inclusive_totals.get(topic, {})
        for subtopic, excl_value in sub_map.items():
            incl_value = float(incl_sub_map.get(subtopic, 0.0))
            excl_value_f = float(excl_value)
            if incl_value < excl_value_f - FLOAT_EPS:
                violations.append(
                    {
                        "topic_id": topic,
                        "subtopic_id": subtopic,
                        "exclusive": excl_value_f,
                        "inclusive": incl_value,
                        "delta": incl_value - excl_value_f,
                    }
                )

    total_violations = len(violations)

    if violations:
        return GateResult(
            name="reconciliation",
            passed=False,
            severity=SEVERITY_BLOCK,
            summary=(
                f"{total_violations} (topic, subtopic) pair(s) violate D-17 "
                f"invariant (inclusive < exclusive within 1e-9 tolerance)"
            ),
            details={
                "violation_count": total_violations,
                "violations": violations[:50],
                "truncated": total_violations > 50,
                "float_eps": FLOAT_EPS,
            },
        )

    return GateResult(
        name="reconciliation",
        passed=True,
        severity=SEVERITY_BLOCK,
        summary="D-17 reconciliation invariant holds across all (topic, subtopic) pairs",
    )

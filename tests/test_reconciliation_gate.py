"""Tests for gates.reconciliation — Phase 12 D-18 + D-17.

Covers:
- Passes on aligned totals (inclusive >= exclusive - 1e-9)
- Fails when inclusive < exclusive (D-17 violated)
- Passes within float tolerance (1e-12 difference absorbed by 1e-9 epsilon)
- Fails outside float tolerance (1e-6 difference exceeds epsilon)
- Violation list capped at 50 entries; truncated flag set; violation_count accurate
- Gate registered against publish stage with SEVERITY_BLOCK and name='reconciliation'
- Package-level import (gates/__init__.py) triggers registration on `import gates`
- Empty inputs pass (empty corpus is not a failure mode)
"""

from __future__ import annotations

import pytest

from gates.reconciliation import reconciliation_gate
from gates.registry import GateResult, SEVERITY_BLOCK, _REGISTRY


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_passes_on_aligned_totals():
    """Inclusive totals >= exclusive totals — invariant holds."""
    exclusive_totals = {"topic_a": {"s1": 1.0, "s2": 2.0}}
    inclusive_totals = {"topic_a": {"s1": 1.0, "s2": 2.5}}

    result = reconciliation_gate(
        exclusive_totals=exclusive_totals,
        inclusive_totals=inclusive_totals,
    )

    assert result.passed is True
    assert result.name == "reconciliation"
    assert "invariant holds" in result.summary.lower()


def test_passes_when_empty_inputs():
    """Empty corpus is not a failure mode."""
    result = reconciliation_gate(exclusive_totals={}, inclusive_totals={})
    assert result.passed is True


def test_passes_within_float_tolerance():
    """1e-12 below exclusive is within the 1e-9 epsilon — absorbed as IEEE 754 noise."""
    exclusive_totals = {"topic_a": {"s1": 1.0}}
    inclusive_totals = {"topic_a": {"s1": 1.0 - 1e-12}}

    result = reconciliation_gate(
        exclusive_totals=exclusive_totals,
        inclusive_totals=inclusive_totals,
    )

    assert result.passed is True


def test_passes_on_none_inputs():
    """None inputs default to empty dicts — treated same as empty corpus."""
    result = reconciliation_gate(exclusive_totals=None, inclusive_totals=None)
    assert result.passed is True


# ---------------------------------------------------------------------------
# Failure cases
# ---------------------------------------------------------------------------


def test_fails_when_inclusive_lt_exclusive():
    """Impossible state: inclusive < exclusive by 2.0 — gate blocks with structured details."""
    exclusive_totals = {"topic_a": {"s1": 5.0}}
    inclusive_totals = {"topic_a": {"s1": 3.0}}  # 3.0 < 5.0 → violation

    result = reconciliation_gate(
        exclusive_totals=exclusive_totals,
        inclusive_totals=inclusive_totals,
    )

    assert result.passed is False
    assert result.severity == SEVERITY_BLOCK
    assert result.blocked is True
    assert "violations" in result.details
    assert len(result.details["violations"]) >= 1

    violation = result.details["violations"][0]
    assert violation["topic_id"] == "topic_a"
    assert violation["subtopic_id"] == "s1"
    assert violation["exclusive"] == 5.0
    assert violation["inclusive"] == 3.0
    assert violation["delta"] == pytest.approx(3.0 - 5.0)


def test_fails_outside_float_tolerance():
    """1e-6 below exclusive exceeds the 1e-9 epsilon — triggers failure."""
    exclusive_totals = {"topic_a": {"s1": 1.0}}
    inclusive_totals = {"topic_a": {"s1": 1.0 - 1e-6}}

    result = reconciliation_gate(
        exclusive_totals=exclusive_totals,
        inclusive_totals=inclusive_totals,
    )

    assert result.passed is False
    assert result.blocked is True


def test_violations_capped_at_50():
    """100 mismatched (topic, subtopic) pairs — list capped at 50, truncated=True, count=100."""
    exclusive_totals = {
        f"topic_{i}": {f"s{i}": 5.0} for i in range(100)
    }
    inclusive_totals = {
        f"topic_{i}": {f"s{i}": 1.0} for i in range(100)  # all below exclusive
    }

    result = reconciliation_gate(
        exclusive_totals=exclusive_totals,
        inclusive_totals=inclusive_totals,
    )

    assert result.passed is False
    assert len(result.details["violations"]) <= 50
    assert result.details.get("truncated") is True
    assert result.details["violation_count"] == 100


# ---------------------------------------------------------------------------
# Gate registry
# ---------------------------------------------------------------------------


def test_gate_registered_against_publish_stage():
    """The gate must be registered for stage='publish', name='reconciliation', severity=SEVERITY_BLOCK."""
    matching = [
        g for g in _REGISTRY
        if g.name == "reconciliation" and g.stage == "publish"
    ]
    assert len(matching) >= 1, (
        "No gate with name='reconciliation' registered for stage='publish'. "
        "Check that gates/reconciliation.py is imported somewhere at process boot."
    )
    assert matching[0].severity == SEVERITY_BLOCK


def test_decorator_import_runs_on_package_load():
    """import gates triggers gates/__init__.py which imports gates.reconciliation,
    causing the @register_gate decorator to run and register the gate."""
    import importlib
    import gates

    importlib.reload(gates)

    matching = [
        g for g in _REGISTRY
        if g.name == "reconciliation" and g.stage == "publish"
    ]
    assert len(matching) >= 1, (
        "After `import gates`, reconciliation gate must be registered. "
        "Add `from gates import reconciliation as _reconciliation_gate` to gates/__init__.py."
    )

"""Unit tests for pipeline_enrichment.cost_guard."""
from __future__ import annotations

from decimal import Decimal

import pytest

from pipeline_enrichment.cost_guard import (
    DEFAULT_PER_PAPER_USD,
    DEFAULT_THRESHOLD_USD,
    CostEstimate,
    CostGuardTripped,
    check_guard,
    estimate_run_cost,
)


# ---------------------------------------------------------------------------
# estimate_run_cost
# ---------------------------------------------------------------------------

def test_estimate_scales_linearly_with_delta_size():
    assert estimate_run_cost(0) == Decimal("0.00")
    assert estimate_run_cost(1) == Decimal("0.06")
    assert estimate_run_cost(10) == Decimal("0.60")
    assert estimate_run_cost(100) == Decimal("6.00")


def test_estimate_respects_per_paper_override():
    """Caller can override per_paper_usd (e.g., when switching to Batch)."""
    batch_rate = Decimal("0.035")
    assert estimate_run_cost(10, per_paper_usd=batch_rate) == Decimal("0.35")
    assert estimate_run_cost(100, per_paper_usd=batch_rate) == Decimal("3.50")


def test_estimate_quantizes_to_two_decimals():
    odd_rate = Decimal("0.0671")
    result = estimate_run_cost(13, per_paper_usd=odd_rate)
    # 13 * 0.0671 = 0.8723 → rounds to 0.87
    assert result == Decimal("0.87")
    # Always exactly 2dp.
    assert result.as_tuple().exponent == -2


def test_estimate_rejects_negative_delta():
    with pytest.raises(ValueError, match="non-negative"):
        estimate_run_cost(-1)


# ---------------------------------------------------------------------------
# check_guard
# ---------------------------------------------------------------------------

def test_check_guard_passes_under_threshold_and_returns_estimate():
    est = check_guard(delta_size=10)
    assert isinstance(est, CostEstimate)
    assert est.delta_size == 10
    assert est.estimated_usd == Decimal("0.60")
    assert est.threshold_usd == DEFAULT_THRESHOLD_USD
    assert est.per_paper_usd == DEFAULT_PER_PAPER_USD


def test_check_guard_trips_above_threshold():
    """At default rates ($0.06/paper, $30 threshold), guard trips above ~500."""
    with pytest.raises(CostGuardTripped) as exc_info:
        check_guard(delta_size=501)
    err = exc_info.value
    assert err.estimate.delta_size == 501
    assert err.estimate.estimated_usd == Decimal("30.06")
    # Exception message must include diagnostic context for the Teams alert.
    msg = str(err)
    assert "501" in msg
    assert "30.06" in msg
    assert "30.00" in msg


def test_check_guard_at_threshold_does_not_trip():
    """Equal-to is not greater-than. 500 × 0.06 = 30.00 exactly."""
    est = check_guard(delta_size=500)
    assert est.estimated_usd == Decimal("30.00")


def test_check_guard_respects_custom_threshold():
    """Operator override (e.g., higher threshold for a planned catch-up)."""
    est = check_guard(delta_size=600, threshold_usd=Decimal("50.00"))
    assert est.estimated_usd == Decimal("36.00")  # 600 × 0.06
    # And trips when the new threshold is itself exceeded.
    with pytest.raises(CostGuardTripped):
        check_guard(delta_size=900, threshold_usd=Decimal("50.00"))


def test_check_guard_respects_custom_per_paper_rate():
    """If we ever switch back to Batch, per_paper drops to ~$0.035 and the
    guard trips at a higher paper count for the same threshold."""
    est = check_guard(delta_size=800, per_paper_usd=Decimal("0.035"))
    assert est.estimated_usd == Decimal("28.00")  # 800 × 0.035
    with pytest.raises(CostGuardTripped):
        check_guard(delta_size=900, per_paper_usd=Decimal("0.035"))


def test_check_guard_typical_daily_delta_passes_far_under_threshold():
    """5–15 papers per day per #37 — the routine path must never trip."""
    for delta in (5, 10, 15, 20):
        est = check_guard(delta_size=delta)
        assert est.estimated_usd < Decimal("2.00")


def test_cost_guard_tripped_carries_full_estimate_for_alerting():
    """Teams-alert handler reads .estimate to format the alert payload."""
    try:
        check_guard(delta_size=1000)
    except CostGuardTripped as e:
        assert e.estimate.delta_size == 1000
        assert e.estimate.per_paper_usd == DEFAULT_PER_PAPER_USD
        assert e.estimate.threshold_usd == DEFAULT_THRESHOLD_USD
        assert e.estimate.estimated_usd == Decimal("60.00")
    else:
        pytest.fail("expected CostGuardTripped")

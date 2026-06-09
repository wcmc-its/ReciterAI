"""Unit tests for pipeline_tools.cost_guard (A2 extraction cost guard)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from pipeline_tools.cost_guard import (
    CAP_SAFETY_FACTOR,
    HARD_CAP_FLOOR_USD,
    DEFAULT_PER_PMID_USD,
    DEFAULT_THRESHOLD_USD,
    CostCeiling,
    CostCeilingExceeded,
    ExtractionCostEstimate,
    ExtractionCostGuardTripped,
    check_extraction_guard,
    derive_hard_cap,
    estimate_extraction_cost,
    recompute_per_pmid_usd,
)
from utils.bedrock_client import HAIKU_MODEL
from utils.llm_cost import cost_for

# cost_for(HAIKU, 1_000_000, 0) == $1.00 exactly — a clean unit for ceiling tests.
ONE_DOLLAR_TOKENS = dict(model=HAIKU_MODEL, input_tokens=1_000_000, output_tokens=0)


# ---------------------------------------------------------------------------
# preflight estimate
# ---------------------------------------------------------------------------

def test_estimate_scales_linearly():
    assert estimate_extraction_cost(0) == Decimal("0.00")
    assert estimate_extraction_cost(1000) == Decimal("6.00")   # 1000 × 0.006
    assert estimate_extraction_cost(8146) == Decimal("48.88")  # the corpus


def test_estimate_rejects_negative():
    with pytest.raises(ValueError, match="non-negative"):
        estimate_extraction_cost(-1)


def test_estimate_respects_override():
    assert estimate_extraction_cost(1000, per_pmid_usd=Decimal("0.01")) == Decimal("10.00")


def test_corpus_passes_default_threshold():
    """≈8,146-paper corpus must PASS the default preflight (it's the intended run)."""
    est = check_extraction_guard(8146)
    assert isinstance(est, ExtractionCostEstimate)
    assert est.estimated_usd == Decimal("48.88")
    assert est.threshold_usd == DEFAULT_THRESHOLD_USD
    assert est.per_pmid_usd == DEFAULT_PER_PMID_USD


def test_guard_trips_on_order_of_magnitude_anomaly():
    with pytest.raises(ExtractionCostGuardTripped) as exc:
        check_extraction_guard(20000)   # 20000 × 0.006 = $120 > $75
    err = exc.value
    assert err.estimate.n_pmids == 20000
    assert err.estimate.estimated_usd == Decimal("120.00")
    msg = str(err)
    assert "20000" in msg and "120.00" in msg and "--full" in msg


def test_guard_respects_custom_threshold():
    with pytest.raises(ExtractionCostGuardTripped):
        check_extraction_guard(8146, threshold_usd=Decimal("40.00"))
    # and passes under a raised threshold
    assert check_extraction_guard(8146, threshold_usd=Decimal("60.00")).estimated_usd == Decimal("48.88")


def test_recompute_per_pmid_applies_headroom():
    """Recomputed rate = price-table base × fallback headroom (not hand-waved)."""
    base = cost_for(HAIKU_MODEL, 1300, 350)
    assert recompute_per_pmid_usd() == (base * Decimal("2.0")).quantize(Decimal("0.000001"))
    # And the pinned default is a sane round-up of that base.
    assert DEFAULT_PER_PMID_USD >= base


# ---------------------------------------------------------------------------
# derive_hard_cap
# ---------------------------------------------------------------------------

def test_hard_cap_floors_small_runs():
    """A --limit 100 probe (~$0.60 × 1.75 ≈ $1.05) gets the floor cap, not a near-zero one."""
    est = check_extraction_guard(100)
    assert derive_hard_cap(est) == HARD_CAP_FLOOR_USD


def test_hard_cap_scales_large_runs():
    est = check_extraction_guard(8146)   # $48.88
    cap = derive_hard_cap(est)
    expected = (Decimal("48.88") * CAP_SAFETY_FACTOR).quantize(Decimal("0.01"))
    assert cap == expected
    assert cap > est.estimated_usd        # always headroom over the estimate


# ---------------------------------------------------------------------------
# CostCeiling — runtime hard ceiling on measured spend
# ---------------------------------------------------------------------------

def test_ceiling_accumulates_and_reports_remaining():
    c = CostCeiling(cap_usd=Decimal("10.00"))
    c.record(**ONE_DOLLAR_TOKENS)
    c.record(**ONE_DOLLAR_TOKENS)
    assert c.observed_usd == Decimal("2.00")
    assert c.calls == 2
    assert c.remaining_usd == Decimal("8.00")


def test_ceiling_trips_on_cost_after_recording_breach():
    c = CostCeiling(cap_usd=Decimal("2.50"))
    c.record(**ONE_DOLLAR_TOKENS)   # $1
    c.record(**ONE_DOLLAR_TOKENS)   # $2  (≤ cap, no trip)
    with pytest.raises(CostCeilingExceeded) as exc:
        c.record(**ONE_DOLLAR_TOKENS)   # $3 > $2.50 → trip
    err = exc.value
    assert err.reason == "cost"
    assert err.observed_usd == Decimal("3.00")   # breaching call IS counted (honest total)
    assert err.cap_usd == Decimal("2.50")
    assert c.observed_usd == Decimal("3.00")


def test_ceiling_trips_on_call_count():
    c = CostCeiling(cap_usd=Decimal("1000.00"), max_calls=2)
    c.record(model=HAIKU_MODEL, input_tokens=1, output_tokens=1)
    c.record(model=HAIKU_MODEL, input_tokens=1, output_tokens=1)
    with pytest.raises(CostCeilingExceeded) as exc:
        c.record(model=HAIKU_MODEL, input_tokens=1, output_tokens=1)
    assert exc.value.reason == "calls"
    assert exc.value.calls == 3


def test_ceiling_includes_prior_run_on_resume():
    """A resumed run seeds prior spend so cap_usd bounds the WHOLE run."""
    c = CostCeiling(cap_usd=Decimal("2.50"), prior_usd=Decimal("2.00"), prior_calls=2)
    assert c.observed_usd == Decimal("2.00")
    assert c.calls == 2
    with pytest.raises(CostCeilingExceeded):
        c.record(**ONE_DOLLAR_TOKENS)   # prior $2 + $1 = $3 > $2.50
    assert c.observed_usd == Decimal("3.00")


def test_ceiling_summary_carries_totals():
    c = CostCeiling(cap_usd=Decimal("5.00"), max_calls=10, prior_usd=Decimal("1.00"), prior_calls=1)
    c.record(**ONE_DOLLAR_TOKENS)
    s = c.summary()
    assert Decimal(s["observed_usd_total"]) == Decimal("2.00")   # prior $1 + $1 this run
    assert s["cap_usd"] == "5.00"
    assert s["calls_total"] == 2
    assert s["max_calls"] == 10

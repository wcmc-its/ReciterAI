"""Unit tests for utils.llm_cost."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from utils.llm_cost import (
    CostAccumulator,
    UnknownModelError,
    cost_for,
    estimate_cost,
    load_prices,
)


@pytest.fixture
def fake_prices_path(tmp_path: Path) -> Path:
    """Minimal valid YAML price table written to a tmp path."""
    p = tmp_path / "prices.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "models": {
                    "test-cheap": {"input_per_mtok": 1.0, "output_per_mtok": 4.0},
                    "test-pricey": {"input_per_mtok": 15.0, "output_per_mtok": 75.0},
                },
            }
        )
    )
    return p


@pytest.fixture
def fake_prices(fake_prices_path: Path) -> dict:
    return load_prices(fake_prices_path, reload=True)


# ---------- cost_for ----------


def test_cost_for_known_model_returns_decimal(fake_prices):
    # 1M input @ $1/MTok + 1M output @ $4/MTok = $5
    cost = cost_for("test-cheap", 1_000_000, 1_000_000, prices=fake_prices)
    assert cost == Decimal("5.000000")


def test_cost_for_fractional_tokens_is_decimal_clean(fake_prices):
    # 500 input @ $1/MTok = $0.0005
    # 100 output @ $4/MTok = $0.0004
    # total = $0.0009
    cost = cost_for("test-cheap", 500, 100, prices=fake_prices)
    assert cost == Decimal("0.000900")


def test_cost_for_pricey_model_charges_pricey_rates(fake_prices):
    # 1000 input @ $15/MTok + 200 output @ $75/MTok
    # = 0.015 + 0.015 = 0.030
    cost = cost_for("test-pricey", 1000, 200, prices=fake_prices)
    assert cost == Decimal("0.030000")


def test_cost_for_unknown_model_raises(fake_prices):
    with pytest.raises(UnknownModelError) as excinfo:
        cost_for("nonexistent", 100, 100, prices=fake_prices)
    assert "nonexistent" in str(excinfo.value)
    assert "config/llm_prices.yaml" in str(excinfo.value)


def test_cost_for_zero_tokens_is_zero(fake_prices):
    assert cost_for("test-cheap", 0, 0, prices=fake_prices) == Decimal("0E-6")


# ---------- CostAccumulator ----------


def test_accumulator_sums_across_calls(fake_prices):
    acc = CostAccumulator()
    acc.record(model="test-cheap", input_tokens=1000, output_tokens=200, prices=fake_prices)
    acc.record(model="test-cheap", input_tokens=500, output_tokens=100, prices=fake_prices)
    # First: 1000*1/M + 200*4/M = 0.001 + 0.0008 = 0.0018
    # Second: 500*1/M + 100*4/M = 0.0005 + 0.0004 = 0.0009
    # Total: 0.0027
    assert acc.total_usd == Decimal("0.002700")
    assert acc.call_count == 2
    assert acc.total_input_tokens == 1500
    assert acc.total_output_tokens == 300


def test_accumulator_tracks_per_model(fake_prices):
    acc = CostAccumulator()
    acc.record(model="test-cheap", input_tokens=1000, output_tokens=0, prices=fake_prices)
    acc.record(model="test-pricey", input_tokens=1000, output_tokens=0, prices=fake_prices)
    assert acc.by_model["test-cheap"] == Decimal("0.001000")
    assert acc.by_model["test-pricey"] == Decimal("0.015000")
    assert acc.total_usd == Decimal("0.016000")


def test_accumulator_summary_is_string_serializable(fake_prices):
    acc = CostAccumulator()
    acc.record(model="test-cheap", input_tokens=100, output_tokens=50, prices=fake_prices)
    summary = acc.summary()
    # All Decimal values must be strings so json.dumps doesn't choke.
    assert isinstance(summary["total_usd"], str)
    assert all(isinstance(v, str) for v in summary["by_model"].values())
    assert summary["call_count"] == 1


# ---------- estimate_cost ----------


def test_estimate_cost_scales_linearly(fake_prices):
    """Sized so per-call cost is above the cents-quantization noise floor.
    Estimator output is rounded to cents (cost-guard preflight) so the
    linearity property only holds once per-call cost exceeds $0.01."""
    one = estimate_cost("test-pricey", n_calls=1, avg_input_tokens=100_000,
                       avg_output_tokens=10_000, prices=fake_prices)
    # 100K * $15/M + 10K * $75/M = $1.50 + $0.75 = $2.25
    assert one == Decimal("2.25")
    ten = estimate_cost("test-pricey", n_calls=10, avg_input_tokens=100_000,
                       avg_output_tokens=10_000, prices=fake_prices)
    assert ten == Decimal("22.50")
    assert ten == one * 10


def test_estimate_cost_quantizes_to_cents(fake_prices):
    """estimate_cost is the cost-guard preflight; it should round to cents."""
    cost = estimate_cost(
        "test-pricey", n_calls=1000,
        avg_input_tokens=1000, avg_output_tokens=200,
        prices=fake_prices,
    )
    # 1000 calls × ($0.015 + $0.015) = $30.00
    assert cost == Decimal("30.00")


# ---------- price-table loading ----------


def test_load_prices_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_prices(tmp_path / "nope.yaml", reload=True)


def test_load_prices_missing_models_key_raises(tmp_path: Path):
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump({"version": 1}))
    with pytest.raises(ValueError, match="top-level 'models'"):
        load_prices(p, reload=True)


def test_load_prices_missing_input_per_mtok_raises(tmp_path: Path):
    p = tmp_path / "bad.yaml"
    p.write_text(
        yaml.safe_dump({"models": {"bad-model": {"output_per_mtok": 5}}})
    )
    with pytest.raises(ValueError, match="input_per_mtok"):
        load_prices(p, reload=True)


def test_real_price_table_loads_and_covers_daily_job_models():
    """The shipped config/llm_prices.yaml must cover every model the daily
    enrichment job calls. Catches drift between MODEL_IDS_BY_STAGE and the
    price table."""
    prices = load_prices(reload=True)
    # Models the daily enrichment job (#37) actually calls:
    required = {
        "gpt-5.1",
        "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "us.anthropic.claude-sonnet-4-6",
    }
    missing = required - set(prices.keys())
    assert not missing, f"daily-job models missing from price table: {missing}"
    # cost_for must not raise on any required model.
    for m in required:
        c = cost_for(m, 100, 100, prices=prices)
        assert c > 0, f"price for {m} should be positive"

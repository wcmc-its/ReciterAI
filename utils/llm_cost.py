"""LLM cost attribution helper.

Reads `config/llm_prices.yaml`, exposes `cost_for(model, in, out) -> Decimal`,
and provides a per-run accumulator for stages that want to report measured
spend through the `STAGE#.cost_observed_usd` substrate slot.

Closes the #35 scope for the daily enrichment job (#37). The broader
retrofit into ReciterAI's other Bedrock stages (score_publications,
assign_subtopics, etc.) is a follow-up issue against this repo.

Design:
- Prices in YAML, not Python. Provider price changes are a one-file PR.
- Decimal math, not float, so summed costs stay deterministic.
- Unknown models raise — silent misattribution is worse than failing the run.
- The accumulator is a small dataclass; callers create one per run, add per
  call, hand the final total to the STAGE# writer.

Security: never logs token counts or per-call cost at INFO; that's
operational detail that belongs in the STAGE# row, not in CloudWatch logs
where it can leak budget into log aggregators.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRICES_PATH = REPO_ROOT / "config" / "llm_prices.yaml"

# Per-million-token denominator.
_MILLION = Decimal("1000000")


class UnknownModelError(KeyError):
    """Raised when cost_for() is asked about a model not in the price table."""


def _load_prices(path: Path = DEFAULT_PRICES_PATH) -> dict[str, dict[str, Decimal]]:
    """Parse the YAML price table into a {model_id: {input, output}} dict."""
    if not path.exists():
        raise FileNotFoundError(
            f"LLM price table not found at {path}. "
            "Run from repo root or check config/llm_prices.yaml exists."
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "models" not in raw:
        raise ValueError(
            f"{path}: expected top-level 'models' mapping; got {type(raw).__name__}"
        )
    out: dict[str, dict[str, Decimal]] = {}
    for model_id, entry in raw["models"].items():
        try:
            out[model_id] = {
                "input": Decimal(str(entry["input_per_mtok"])),
                "output": Decimal(str(entry["output_per_mtok"])),
            }
        except (KeyError, TypeError) as e:
            raise ValueError(
                f"{path}: model {model_id!r} missing required input_per_mtok / "
                f"output_per_mtok: {e}"
            ) from e
    return out


# Module-level cache; loaded on first call. Tests inject via load_prices(path=...).
_prices_cache: dict[str, dict[str, Decimal]] | None = None


def load_prices(path: Path | None = None, *, reload: bool = False) -> dict[str, dict[str, Decimal]]:
    """Return the price table dict. Caches by default; pass reload=True to refresh."""
    global _prices_cache
    if _prices_cache is None or reload or path is not None:
        _prices_cache = _load_prices(path or DEFAULT_PRICES_PATH)
    return _prices_cache


def cost_for(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    prices: dict[str, dict[str, Decimal]] | None = None,
) -> Decimal:
    """Compute USD cost for a single LLM call.

    Args:
        model: model ID as it appears in `config/llm_prices.yaml` (must match
            exactly; Bedrock inference-profile IDs are not normalized).
        input_tokens: prompt + cached-read tokens.
        output_tokens: completion + reasoning tokens.
        prices: optional injected price table (for tests).

    Returns:
        Decimal cost in USD, quantized to 6 decimal places.

    Raises:
        UnknownModelError: if the model isn't in the price table.
    """
    table = prices if prices is not None else load_prices()
    if model not in table:
        raise UnknownModelError(
            f"No price entry for model {model!r}. "
            f"Add it to config/llm_prices.yaml. "
            f"Known models: {sorted(table.keys())}"
        )
    entry = table[model]
    cost = (
        Decimal(input_tokens) * entry["input"] / _MILLION
        + Decimal(output_tokens) * entry["output"] / _MILLION
    )
    return cost.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


@dataclass
class CostAccumulator:
    """Per-run accumulator. One per stage invocation; sum cost across calls.

    Usage:
        acc = CostAccumulator()
        for pmid in pmids:
            response = client.chat.completions.create(...)
            acc.record(
                model=response.model,
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
            )
        write_stage_complete(..., cost_observed_usd=acc.total_usd)

    Per-call costs are stored on the accumulator so they can be logged into
    the STAGE# row's failure_details if a run failed partway through.
    """

    total_usd: Decimal = field(default_factory=lambda: Decimal("0"))
    call_count: int = 0
    by_model: dict[str, Decimal] = field(default_factory=dict)
    total_input_tokens: int = 0
    total_output_tokens: int = 0

    def record(self, *, model: str, input_tokens: int, output_tokens: int,
               prices: dict[str, dict[str, Decimal]] | None = None) -> Decimal:
        """Compute + accumulate the cost of one call. Returns this call's cost."""
        cost = cost_for(model, input_tokens, output_tokens, prices=prices)
        self.total_usd += cost
        self.call_count += 1
        self.by_model[model] = self.by_model.get(model, Decimal("0")) + cost
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        return cost

    def summary(self) -> dict[str, Any]:
        """Compact summary dict suitable for logging / STAGE# `failure_details`."""
        return {
            "total_usd": str(self.total_usd),
            "call_count": self.call_count,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "by_model": {m: str(c) for m, c in self.by_model.items()},
        }


def estimate_cost(
    model: str,
    *,
    n_calls: int,
    avg_input_tokens: int,
    avg_output_tokens: int,
    prices: dict[str, dict[str, Decimal]] | None = None,
) -> Decimal:
    """Cost-guard preflight estimator.

    The daily job uses this before invoking Bedrock/OpenAI to decide
    whether the delta is large enough to refuse + alert (per #37's
    $30 cost guard).

    `avg_input_tokens` and `avg_output_tokens` should be the conservative
    per-call estimate for the stage in question. The estimator deliberately
    does not handle multi-model deltas; callers sum across stages.
    """
    per_call = cost_for(model, avg_input_tokens, avg_output_tokens, prices=prices)
    return (per_call * Decimal(n_calls)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

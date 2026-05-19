"""Cost-guard preflight for the daily enrichment job (#37).

Before invoking Bedrock + OpenAI on a delta of N PMIDs, the daily handler
estimates the run cost and refuses to proceed if it exceeds a threshold.
The intent is to catch anomalies — a watermark that hasn't advanced for
weeks, a corpus-filter regression that suddenly sweeps in thousands of
papers, etc. — before they spend real money.

Measured (Bedrock spike, 2026-05-19, n=15 PMIDs / 30 calls):
    Per-paper combined ≈ $0.0141 ($0.21222 / 15 papers,
    Sonnet 4.6 synopsis + impact, plain Converse).

Default per-paper here is set to $0.018 — a conservative round-up of the
measured Sonnet rate that carries headroom for two real-world lanes:
- The synopsis 3-attempt and impact length-retry loops (#37 D4), which
  can spend Sonnet input tokens more than once per paper.
- The Bedrock→OpenAI gpt-5.1 content-filter fallback (#37 D3): a
  filtered paper pays a Sonnet input-token cost plus a full gpt-5.1
  call. With Sonnet 4.6 reproducibly content-filtering WCM biomedical
  animal-model abstracts, the fallback lane is the baseline, not an
  edge case.

At the $30 default threshold this trips at ~1,667 papers — well above
any plausible daily delta (5–15 PMIDs) and any plausible PTO catch-up.
The backfill (#112) and annual rescore enforce no threshold anyway; they
bypass the guard via the orchestrator's --full / --from-gap-scan paths.

Earlier defaults: $0.060 (#37 modeling estimate, hand-wave) → $0.010
(gpt-5.1 measured) → $0.018 (Sonnet measured + fallback headroom). The
Fargate smoke run (#37 PR 4) pins this against measured Sonnet spend
before the 1,073-paper backfill.

The accurate, measured cost lives in `utils/llm_cost.CostAccumulator`
and is reported per-run via the STAGE# row; this module just raises on
threshold breach.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

DEFAULT_PER_PAPER_USD = Decimal("0.018")
DEFAULT_THRESHOLD_USD = Decimal("30.00")


@dataclass(frozen=True)
class CostEstimate:
    delta_size: int
    per_paper_usd: Decimal
    estimated_usd: Decimal
    threshold_usd: Decimal


class CostGuardTripped(Exception):
    """Raised when the preflight estimate exceeds the threshold.

    Carries the estimate so the orchestrator (and downstream Teams alert)
    can surface enough context to triage the anomaly.
    """

    def __init__(self, estimate: CostEstimate):
        self.estimate = estimate
        super().__init__(
            f"Cost guard tripped: delta={estimate.delta_size} papers × "
            f"${estimate.per_paper_usd}/paper = ${estimate.estimated_usd} "
            f"(threshold ${estimate.threshold_usd})."
        )


def estimate_run_cost(
    delta_size: int,
    per_paper_usd: Decimal = DEFAULT_PER_PAPER_USD,
) -> Decimal:
    """Return the estimated USD cost for a daily delta of `delta_size` papers.

    Linear in delta_size by design — the guard catches order-of-magnitude
    anomalies, not 10% drift.
    """
    if delta_size < 0:
        raise ValueError(f"delta_size must be non-negative, got {delta_size}")
    raw = Decimal(delta_size) * per_paper_usd
    return raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def check_guard(
    delta_size: int,
    threshold_usd: Decimal = DEFAULT_THRESHOLD_USD,
    per_paper_usd: Decimal = DEFAULT_PER_PAPER_USD,
) -> CostEstimate:
    """Preflight a daily run. Returns the estimate on pass; raises on trip.

    Args:
        delta_size: number of new PMIDs to enrich this run.
        threshold_usd: refuse-and-alert ceiling.
        per_paper_usd: estimator's per-paper assumption. Override if the
            stage mix changes (e.g., a model swap or a different fallback mix).

    Returns:
        CostEstimate with the inputs + computed estimate.

    Raises:
        CostGuardTripped: estimate > threshold. Run must not proceed
            until an operator inspects the delta.
    """
    estimated = estimate_run_cost(delta_size, per_paper_usd)
    estimate = CostEstimate(
        delta_size=delta_size,
        per_paper_usd=per_paper_usd,
        estimated_usd=estimated,
        threshold_usd=threshold_usd,
    )
    if estimated > threshold_usd:
        raise CostGuardTripped(estimate)
    return estimate

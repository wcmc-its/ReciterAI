"""Cost-guard preflight for the daily enrichment job (#37 step 2).

Before invoking Bedrock + OpenAI on a delta of N PMIDs, the daily handler
estimates the run cost and refuses to proceed if it exceeds a threshold.
The intent is to catch anomalies — a watermark that hasn't advanced for
weeks, a corpus-filter regression that suddenly sweeps in thousands of
papers, etc. — before they spend real money.

Measured (50-paper run, 2026-05-14, n=100 calls):
    Per-paper sync ≈ $0.0094 ($0.471495 / 50 papers)
        ≈ 1,649 input + 265 output tokens per call × 2 calls/paper

Default per-paper here is set to $0.010 — a slight conservative overestimate
of the measured rate so the guard remains protective if reasoning-token usage
trends upward on harder abstracts. At the $30 default threshold this trips
at ~3,000 papers, which is well above any plausible non-anomalous workload
(daily delta 5–15, bootstrap ~1,900, annual rescore ~6,200 — only the
rescore would trip).

Earlier versions of this module used $0.060 per-paper, derived from #37's
modeling estimate. That number was a hand-wave that didn't survive contact
with measured data; the audit committed to in `docs/daily-enrichment.md` is
the source for the current default.

Annual rescore bypasses the guard via the orchestrator's --full flag;
this module just raises on threshold breach, doesn't know about --full.
The accurate, measured cost lives in `utils/llm_cost.CostAccumulator` and
is reported via the per-run STAGE# row.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

DEFAULT_PER_PAPER_USD = Decimal("0.010")
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
            stage mix changes (e.g., switching synopsis+impact to Batch).

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

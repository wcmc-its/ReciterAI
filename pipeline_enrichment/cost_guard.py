"""Cost-guard preflight for the daily enrichment job (#37 step 2).

Before invoking Bedrock + OpenAI on a delta of N PMIDs, the daily handler
estimates the run cost and refuses to proceed if it exceeds a threshold.
The intent is to catch anomalies — a watermark that hasn't advanced for
weeks, a corpus-filter regression that suddenly sweeps in thousands of
papers, etc. — before they spend real money.

Math (per #37 cost model):
    Per-paper all-in ≈ $0.035 (with OpenAI Batch API, 50% off)
    Per-paper sync   ≈ $0.060 (Batch deferred per design decision —
                                ~2× on the OpenAI portion only)

Step 2 commits to sync, so the default per-paper estimate here is $0.060.
At the $30 default threshold the guard trips at ~500 papers, well above
the 5–15 daily norm and below the issue's "anomalous" benchmark of ~850.

Annual rescore bypasses the guard via the orchestrator's --full flag;
this module just raises on threshold breach, doesn't know about --full.
The accurate, measured cost lives in `utils/llm_cost.CostAccumulator` and
is reported via the per-run STAGE# row.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

DEFAULT_PER_PAPER_USD = Decimal("0.060")
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

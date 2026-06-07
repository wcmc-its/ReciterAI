"""Cost guard for the A2 corpus-wide tool/method extraction (docs/tool-classifier-spec.md).

The A1 seed (230 names) is bounded — a handful of Sonnet calls + ~230 embeds —
and needs no guard. A2 is the expensive fan-out: one Bedrock Haiku extraction
call per faculty paper over the ≈8,146 lead/senior-authored Academic Articles
≥2020. That is bounded but real spend, so it runs behind this guard.

Two layers, because the seed's single preflight check is not enough for a
multi-hour fan-out:

1. **Preflight estimate** (mirrors ``pipeline_enrichment.cost_guard``): before
   the loop, estimate ``n_pmids × per_pmid_usd`` and refuse if it exceeds a
   threshold — the order-of-magnitude anomaly catch (a corpus filter that
   suddenly sweeps in 100k rows). Bypassable with ``--full`` for an intentional
   large run, exactly as the daily job's guard is.

2. **Runtime ceiling** (``CostCeiling``, new vs the seed/enrichment guard): a
   HARD cap on *measured* spend, enforced after every call. The preflight is an
   estimate; if the real per-PMID cost runs higher than modeled (more
   content-filter fallbacks to gpt-5.x than expected, longer abstracts), the
   estimate is wrong but the ceiling still stops the run before it overspends.
   This is the "cap total calls/tokens" requirement: a runaway loop or a
   token-explosion cannot blow past ``cap_usd`` / ``max_calls``.

The ceiling reuses ``utils.llm_cost.CostAccumulator`` for the per-call cost math
(prices in ``config/llm_prices.yaml``), so cost attribution here and the measured
spend reported by the harness are one number, not two estimates that can drift.

Resumability: a crashed run resumes from the checkpoint (``pipeline_tools.checkpoint``);
the ceiling is seeded with the prior run's observed spend (``prior_usd`` / ``prior_calls``)
so ``cap_usd`` bounds the WHOLE run across resumes, not just the resumed segment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

from utils.bedrock_client import HAIKU_MODEL
from utils.llm_cost import CostAccumulator, cost_for

# ---------------------------------------------------------------------------
# Per-PMID cost model — derived from the price table, not hand-waved.
# ---------------------------------------------------------------------------
#
# One extraction call per PMID: a Haiku Converse call over the system prompt
# (~750 tok) + the paper's title + abstract (~550 tok) ≈ 1,300 input tokens,
# producing a short JSON mention list (~350 output tokens). At the Haiku
# 4.5 rate ($1.00 in / $5.00 out per Mtok, config/llm_prices.yaml):
#
#     cost_for(HAIKU, 1300, 350) = 1300·1.00/1e6 + 350·5.00/1e6 ≈ $0.00305
#
# Rounded up to $0.006/PMID — a ~2× headroom that carries two real lanes,
# the same way the enrichment guard rounds its measured Sonnet rate up for
# headroom:
#   - the Bedrock single-retry on a transient empty return, and
#   - the gpt-5.1 content-filter fallback (Bedrock reproducibly filters WCM
#     biomedical animal-model abstracts; a filtered paper pays a Haiku input
#     cost PLUS a full gpt-5.1 call, which is pricier per paper).
#
# RECALIBRATE this against the --limit probe's measured CostAccumulator before
# the full ≈8,146-paper fan-out — same cheap-probe-first discipline as the seed.
AVG_INPUT_TOKENS = 1300
AVG_OUTPUT_TOKENS = 350
FALLBACK_HEADROOM = Decimal("2.0")

DEFAULT_PER_PMID_USD = Decimal("0.006")

# Preflight anomaly threshold. The intended corpus (≈8,146 papers × $0.006 ≈
# $48.9) must PASS; the guard trips on an order-of-magnitude miss. $75 trips at
# ~12,500 PMIDs (≈1.5× the corpus) — well above any plausible faculty-paper
# set, well below a runaway. Operator overrides via --threshold-usd; --full
# bypasses entirely for an intentional large run.
DEFAULT_THRESHOLD_USD = Decimal("75.00")

# Floor for the run-scoped hard cap. derive_hard_cap() returns
# max(estimate × SAFETY_FACTOR, this floor), so a tiny --limit probe still gets
# a usable cap (one long abstract can't trip it) while the full corpus run gets
# the scaled value (≈$85.54 for 8,146 papers). Also the fallback cap if a caller
# constructs a CostCeiling without deriving one. Kept low so scaling — not the
# floor — governs any run past a few hundred papers.
HARD_CAP_FLOOR_USD = Decimal("10.00")
CAP_SAFETY_FACTOR = Decimal("1.75")
# Per-PMID call budget: 1 Bedrock happy-path + 1 transient-empty retry + 1
# gpt-5.1 fallback = 3 calls worst case. A run making far more calls than
# n_pmids × this is looping — stop it.
MAX_CALLS_PER_PMID = 3


def recompute_per_pmid_usd(
    *,
    avg_input_tokens: int = AVG_INPUT_TOKENS,
    avg_output_tokens: int = AVG_OUTPUT_TOKENS,
    headroom: Decimal = FALLBACK_HEADROOM,
    model: str = HAIKU_MODEL,
) -> Decimal:
    """Recompute the per-PMID rate from the live price table × fallback headroom.

    Lets the operator re-derive ``DEFAULT_PER_PMID_USD`` after a price change or
    after the --limit probe measures the real token profile, instead of trusting
    the pinned constant. Quantized to 6dp like ``utils.llm_cost.cost_for``.
    """
    base = cost_for(model, avg_input_tokens, avg_output_tokens)
    return (base * headroom).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Layer 1 — preflight estimate (mirrors pipeline_enrichment.cost_guard)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExtractionCostEstimate:
    n_pmids: int
    per_pmid_usd: Decimal
    estimated_usd: Decimal
    threshold_usd: Decimal


class ExtractionCostGuardTripped(Exception):
    """Raised when the preflight estimate exceeds the threshold.

    Carries the estimate so the CLI (and any operator alert) can surface enough
    context to triage the anomaly before any spend.
    """

    def __init__(self, estimate: ExtractionCostEstimate):
        self.estimate = estimate
        super().__init__(
            f"Extraction cost guard tripped: n_pmids={estimate.n_pmids} × "
            f"${estimate.per_pmid_usd}/PMID = ${estimate.estimated_usd} "
            f"(threshold ${estimate.threshold_usd}). Re-run with --full to "
            f"bypass if this corpus size is intentional."
        )


def estimate_extraction_cost(
    n_pmids: int,
    per_pmid_usd: Decimal = DEFAULT_PER_PMID_USD,
) -> Decimal:
    """Estimated USD cost to extract over ``n_pmids`` papers (linear in n_pmids)."""
    if n_pmids < 0:
        raise ValueError(f"n_pmids must be non-negative, got {n_pmids}")
    raw = Decimal(n_pmids) * per_pmid_usd
    return raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def check_extraction_guard(
    n_pmids: int,
    *,
    threshold_usd: Decimal = DEFAULT_THRESHOLD_USD,
    per_pmid_usd: Decimal = DEFAULT_PER_PMID_USD,
) -> ExtractionCostEstimate:
    """Preflight the extraction run. Returns the estimate on pass; raises on trip.

    Raises:
        ExtractionCostGuardTripped: estimate > threshold. The run must not
            proceed until an operator confirms the corpus size (or passes
            --full).
    """
    estimated = estimate_extraction_cost(n_pmids, per_pmid_usd)
    estimate = ExtractionCostEstimate(
        n_pmids=n_pmids,
        per_pmid_usd=per_pmid_usd,
        estimated_usd=estimated,
        threshold_usd=threshold_usd,
    )
    if estimated > threshold_usd:
        raise ExtractionCostGuardTripped(estimate)
    return estimate


def derive_hard_cap(
    estimate: ExtractionCostEstimate,
    *,
    safety_factor: Decimal = CAP_SAFETY_FACTOR,
    floor_usd: Decimal = HARD_CAP_FLOOR_USD,
) -> Decimal:
    """Run-scoped hard cap = max(estimate × safety_factor, floor).

    Auto-scales the ceiling to the run: a --limit 100 probe (~$0.60 estimate)
    still gets the floor cap (so the probe can't be the thing that overspends),
    while a full-corpus run gets estimate × 1.75. The floor keeps a tiny probe
    from setting a near-zero cap that a single long abstract would trip.
    """
    scaled = (estimate.estimated_usd * safety_factor).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return max(scaled, floor_usd)


# ---------------------------------------------------------------------------
# Layer 2 — runtime hard ceiling on MEASURED spend ("cap total calls/tokens")
# ---------------------------------------------------------------------------


class CostCeilingExceeded(Exception):
    """Raised the moment measured spend (or call count) crosses the hard cap.

    The crossing call is fully recorded before this fires, so ``observed_usd``
    is the true post-crossing total; the harness halts before the NEXT call.
    """

    def __init__(
        self,
        *,
        reason: str,
        observed_usd: Decimal,
        cap_usd: Decimal,
        calls: int,
        max_calls: int | None,
    ):
        self.reason = reason  # "cost" | "calls"
        self.observed_usd = observed_usd
        self.cap_usd = cap_usd
        self.calls = calls
        self.max_calls = max_calls
        if reason == "cost":
            detail = f"observed ${observed_usd} > cap ${cap_usd}"
        else:
            detail = f"calls {calls} > max_calls {max_calls}"
        super().__init__(
            f"Extraction cost ceiling exceeded ({reason}): {detail}. "
            f"Run halted; checkpointed PMIDs are durable — resume after raising "
            f"the cap or narrowing the corpus."
        )


@dataclass
class CostCeiling:
    """Hard, measured-spend ceiling for the extraction loop.

    Wraps a ``CostAccumulator``. Call ``record`` after every LLM call; it
    accumulates the call's cost and raises ``CostCeilingExceeded`` once the
    running total (including any ``prior_usd`` carried from a resumed run)
    crosses ``cap_usd``, or once the call count crosses ``max_calls``.

    Enforce-after-record is deliberate: the breaching call is counted (so the
    reported total is honest) and the run stops before spending the next one.
    """

    cap_usd: Decimal
    max_calls: int | None = None
    prior_usd: Decimal = Decimal("0")
    prior_calls: int = 0
    accumulator: CostAccumulator = field(default_factory=CostAccumulator)

    def record(self, *, model: str, input_tokens: int, output_tokens: int) -> Decimal:
        """Accumulate one call's cost, then enforce the ceiling. Returns the call cost."""
        cost = self.accumulator.record(
            model=model, input_tokens=input_tokens, output_tokens=output_tokens
        )
        self._enforce()
        return cost

    def _enforce(self) -> None:
        if self.observed_usd > self.cap_usd:
            raise CostCeilingExceeded(
                reason="cost",
                observed_usd=self.observed_usd,
                cap_usd=self.cap_usd,
                calls=self.calls,
                max_calls=self.max_calls,
            )
        if self.max_calls is not None and self.calls > self.max_calls:
            raise CostCeilingExceeded(
                reason="calls",
                observed_usd=self.observed_usd,
                cap_usd=self.cap_usd,
                calls=self.calls,
                max_calls=self.max_calls,
            )

    @property
    def observed_usd(self) -> Decimal:
        """Measured spend across the whole run (prior resumed segment + this one)."""
        return self.prior_usd + self.accumulator.total_usd

    @property
    def calls(self) -> int:
        return self.prior_calls + self.accumulator.call_count

    @property
    def remaining_usd(self) -> Decimal:
        return self.cap_usd - self.observed_usd

    def summary(self) -> dict:
        """Compact run-cost summary for telemetry / the operator report."""
        s = self.accumulator.summary()
        s["observed_usd_total"] = str(self.observed_usd)
        s["cap_usd"] = str(self.cap_usd)
        s["calls_total"] = self.calls
        s["max_calls"] = self.max_calls
        return s

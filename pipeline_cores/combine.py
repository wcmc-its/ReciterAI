"""Merge signal layers into one (publication, core) likelihood + status.

Precedence:
  * deterministic confirmers (core named in text, OR a core-staff co-author) ->
    auto-CONFIRMED, high likelihood. Both were 100%-precision in validation.
  * otherwise probabilistic: noisy-OR of the LLM triage score and the author
    affinity prior -> CANDIDATE if it clears the triage threshold, else dropped.

Confirmers never come from the LLM. The LLM only ranks the candidate queue.
"""
from __future__ import annotations

from pipeline_cores.models import (
    STATUS_BELOW,
    STATUS_CANDIDATE,
    STATUS_CONFIRMED,
    CoreUsageRecord,
    SignalResult,
)

# Likelihoods for the deterministic confirmers (validation: both 100% precision).
_ACK_LIKELIHOOD = 0.98
_STAFF_COAUTHOR_LIKELIHOOD = 0.95
# A candidate must clear this to be surfaced to the claim queue.
DEFAULT_TRIAGE_THRESHOLD = 0.30


def combine(
    pmid: str,
    core_id: str,
    signals: SignalResult,
    *,
    scored_at: str = "",
    triage_threshold: float = DEFAULT_TRIAGE_THRESHOLD,
) -> CoreUsageRecord:
    if signals.ack_matched:
        return CoreUsageRecord(pmid, core_id, _ACK_LIKELIHOOD, STATUS_CONFIRMED, signals, scored_at)
    if signals.coauthor_cwids:
        return CoreUsageRecord(pmid, core_id, _STAFF_COAUTHOR_LIKELIHOOD, STATUS_CONFIRMED, signals, scored_at)

    llm_norm = (signals.llm_score / 10.0) if signals.llm_score else 0.0
    affinity = max(0.0, min(1.0, signals.author_affinity))
    likelihood = 1.0 - (1.0 - llm_norm) * (1.0 - affinity)  # noisy-OR
    status = STATUS_CANDIDATE if likelihood >= triage_threshold else STATUS_BELOW
    return CoreUsageRecord(pmid, core_id, round(likelihood, 4), status, signals, scored_at)

"""Phase 12 §9 feedback finding-record builders.

Produces three typed finding records for the feedback sweep consumer:

- ``CANDIDATE_TOPIC#{slug}`` — Sonnet-identified candidate new topic from
  uncovered-PMID analysis.
- ``RECLUSTER_RECOMMENDATION#{topic_id}`` — Drift signal: a topic has
  accumulated low-confidence assignments for recluster_persistence_days
  consecutive days above the drift_low_confidence_topic_max threshold.
- ``SPOTLIGHT_DIAGNOSTIC#{subtopic_id}`` — D-08 (CR-2026-05-12 re-keyed
  per-subtopic): repeated CRITIC_REJECT# events for a subtopic surface
  structural spotlight failure. Per-faculty drill-down available via
  author_cwids on underlying CRITIC_REJECT# rows.

All builders follow Pattern A (pure builder + thin writer). See
pipeline_feedback.sweep for the aggregation logic that feeds these builders.
"""

from __future__ import annotations

from typing import Any
from utils.iso_clock import now_iso




# ---------------------------------------------------------------------------
# CANDIDATE_TOPIC#{slug}
# ---------------------------------------------------------------------------


def build_candidate_topic_record(
    *,
    slug: str,
    proposed_label: str,
    source_pmids: list[str],
    sonnet_rationale: str,
    source_sweep_run_id: str,
    triggered_by: str,
    truncated: bool = False,
    total_unprocessed_remaining: int | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Pure builder for a CANDIDATE_TOPIC# row — Phase 12 D-04.

    Idempotent PK; source_sweep_run_id lives in body, not key (D-05).
    `truncated` is True when the uncovered-PMID input was capped at
    feedback_sweep_max_pmids; total_unprocessed_remaining carries
    the count of PMIDs beyond the cap when set.
    """
    item: dict[str, Any] = {
        "PK": f"CANDIDATE_TOPIC#{slug}",
        "SK": "GLOBAL",
        "record_type": "CANDIDATE_TOPIC",
        "slug": str(slug),
        "proposed_label": str(proposed_label),
        "source_pmids": [str(p) for p in source_pmids],
        "sonnet_rationale": str(sonnet_rationale),
        "source_sweep_run_id": str(source_sweep_run_id),
        "triggered_by": str(triggered_by),
        "truncated": bool(truncated),
        "created_at": created_at or now_iso(),
        "source_stage": "feedback.sweep",
    }
    if total_unprocessed_remaining is not None:
        item["total_unprocessed_remaining"] = int(total_unprocessed_remaining)
    return item


def write_candidate_topic(table: Any, **kwargs: Any) -> dict[str, Any]:
    """Build a CANDIDATE_TOPIC# row and persist via table.put_item."""
    item = build_candidate_topic_record(**kwargs)
    table.put_item(Item=item)
    return item


# ---------------------------------------------------------------------------
# RECLUSTER_RECOMMENDATION#{topic_id}
# ---------------------------------------------------------------------------


def build_recluster_recommendation_record(
    *,
    topic_id: str,
    evaluation_history: list[dict[str, Any]],
    source_sweep_run_id: str,
    triggered_by: str,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Pure builder for a RECLUSTER_RECOMMENDATION# row — Phase 12 D-04 + D-07.

    evaluation_history is a list of {"date": "YYYY-MM-DD", "count": int}
    for the recluster_persistence_days days that tripped the
    drift_low_confidence_topic_max threshold under this topic.
    """
    return {
        "PK": f"RECLUSTER_RECOMMENDATION#{topic_id}",
        "SK": "GLOBAL",
        "record_type": "RECLUSTER_RECOMMENDATION",
        "topic_id": str(topic_id),
        "evaluation_history": [
            {"date": str(e["date"]), "count": int(e["count"])}
            for e in evaluation_history
        ],
        "source_sweep_run_id": str(source_sweep_run_id),
        "triggered_by": str(triggered_by),
        "created_at": created_at or now_iso(),
        "source_stage": "feedback.sweep",
    }


def write_recluster_recommendation(table: Any, **kwargs: Any) -> dict[str, Any]:
    """Build a RECLUSTER_RECOMMENDATION# row and persist via table.put_item."""
    item = build_recluster_recommendation_record(**kwargs)
    table.put_item(Item=item)
    return item


# ---------------------------------------------------------------------------
# SPOTLIGHT_DIAGNOSTIC#{subtopic_id}
# ---------------------------------------------------------------------------


def build_spotlight_diagnostic_record(
    *,
    subtopic_id: str,
    reason_code_distribution: dict[str, int],
    distinct_pmid_set_count: int,
    underlying_rejects: list[str],
    max_underlying: int,
    window_days: int,
    source_sweep_run_id: str,
    triggered_by: str,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Pure builder for a SPOTLIGHT_DIAGNOSTIC# row — Phase 12 D-04 + D-08 + D-09 + D-32.

    Phase 12 D-08 (CR-2026-05-12 re-framing): keying is by subtopic_id,
    NOT cwid. Spotlight rejections are per-(publish, subtopic, pmid_set)
    events; rolling them up by subtopic surfaces 'this subtopic is
    structurally failing across publish cycles' as the diagnostic signal.

    distinct_pmid_set_count carries D-09 semantics: 'distinct (publish_id,
    pmid_set_hash) pairs over the window for this subtopic' — NOT 'total
    rejection events.' Document this explicitly so consumers don't conflate
    'ranking is stuck on the same papers across publishes' with 'ranking
    is broken across different papers.'

    underlying_rejects carries {publish_id}#{subtopic_id}#{pmid_set_hash}
    suffixes (D-08 four-segment PK shape) for drill-down (D-32); capped
    at max_underlying; overflow surfaces as underlying_rejects_truncated=true
    + total_underlying=N.

    Per-faculty drill-down is available by reading the underlying
    CRITIC_REJECT# rows' body author_cwids field (D-08 body field).
    The diagnostic row deliberately does NOT carry author_cwids
    directly — the per-(publish, subtopic, pmid_set) rejection row is
    the source of truth.

    DOES NOT carry lede_text — that's a SPOTLIGHT_REVIEW# concern only
    (Pattern H PII boundary).
    """
    total = len(underlying_rejects)
    capped = underlying_rejects[:max_underlying]
    item: dict[str, Any] = {
        "PK": f"SPOTLIGHT_DIAGNOSTIC#{subtopic_id}",
        "SK": "GLOBAL",
        "record_type": "SPOTLIGHT_DIAGNOSTIC",
        "subtopic_id": str(subtopic_id),
        "reason_code_distribution": {str(k): int(v) for k, v in reason_code_distribution.items()},
        "distinct_pmid_set_count": int(distinct_pmid_set_count),
        "underlying_rejects": [str(s) for s in capped],
        "window_days": int(window_days),
        "source_sweep_run_id": str(source_sweep_run_id),
        "triggered_by": str(triggered_by),
        "created_at": created_at or now_iso(),
        "source_stage": "feedback.sweep",
    }
    if total > max_underlying:
        item["underlying_rejects_truncated"] = True
        item["total_underlying"] = total
    return item


def write_spotlight_diagnostic(table: Any, **kwargs: Any) -> dict[str, Any]:
    """Build a SPOTLIGHT_DIAGNOSTIC# row and persist via table.put_item."""
    item = build_spotlight_diagnostic_record(**kwargs)
    table.put_item(Item=item)
    return item

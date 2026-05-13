"""Typed event records for Phase 10 hot-path feedback signals.

Spec §9 captures quality signals as DynamoDB records instead of letting
them leak into logs:

- `UNCOVERED_PMID#{pmid}`: a publication's top topic score is below
  `uncovered_score_floor` (0.4 default). Carries the top-3 closest
  topics + scores so a follow-up Sonnet pass can surface candidate new
  topics (consumption is Phase 12).
- `LOW_CONFIDENCE_ASSIGNMENT#{pmid}`: subtopic-assignment confidence is
  below `low_confidence_floor` (0.35 default) across all candidate
  subtopics under a PMID's top topic. Carries the candidate
  confidences so persistent low-confidence under a topic flags that
  subtopic hierarchy for re-clustering (consumption is Phase 12).

Both record types are idempotent: PK is per-PMID and SK is the constant
`GLOBAL`, so a re-run with the same finding overwrites in place rather
than appending duplicates. The `created_at` attribute carries the
timestamp the drift evaluator (T10) uses to bucket events into its
rolling 14-day window.

The substrate table is the existing `reciterai-chatbot` single-table
design (PK/SK schema).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any


# Default thresholds file lives at <repo>/config/thresholds.json.
# WR-01: kept as a module-level alias for backwards compatibility; the canonical
# path constant now lives in utils.env_check.THRESHOLDS_FILE.
DEFAULT_THRESHOLDS_PATH = (
    Path(__file__).parent.parent / "config" / "thresholds.json"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# WR-01: load_thresholds is canonical in utils.env_check. Re-exported here so
# existing importers (`from utils.event_records import load_thresholds`) keep
# working while the implementation lives in one place.
from utils.env_check import load_thresholds  # noqa: E402, F401


# ---------------------------------------------------------------------------
# UNCOVERED_PMID#{pmid}
# ---------------------------------------------------------------------------


def build_uncovered_pmid_record(
    *,
    pmid: str,
    taxonomy_version: str,
    top_topics: list[tuple[str, float]],
    created_at: str | None = None,
) -> dict[str, Any]:
    """Pure builder for an UNCOVERED_PMID# row.

    `top_topics` is the top-3 (topic_id, score) tuples ordered from
    highest to lowest score. Float scores are converted to Decimal to
    satisfy DynamoDB's numeric type rule.
    """
    top_sorted = sorted(top_topics, key=lambda t: (-float(t[1]), t[0]))[:3]
    top_topic_score = (
        Decimal(str(top_sorted[0][1])) if top_sorted else Decimal("0")
    )
    return {
        "PK": f"UNCOVERED_PMID#{pmid}",
        "SK": "GLOBAL",
        "record_type": "UNCOVERED_PMID",
        "pmid": str(pmid),
        "taxonomy_version": taxonomy_version,
        "top_topic_score": top_topic_score,
        "top_topics": [
            {"topic_id": tid, "score": Decimal(str(score))}
            for tid, score in top_sorted
        ],
        "created_at": created_at or _now_iso(),
        "source_stage": "score_publications",
    }


def write_uncovered_pmid(table: Any, **kwargs: Any) -> dict[str, Any]:
    """Build an UNCOVERED_PMID# row and persist via table.put_item."""
    item = build_uncovered_pmid_record(**kwargs)
    table.put_item(Item=item)
    return item


# ---------------------------------------------------------------------------
# LOW_CONFIDENCE_ASSIGNMENT#{pmid}
# ---------------------------------------------------------------------------


def build_low_confidence_assignment_record(
    *,
    pmid: str,
    topic_id: str,
    candidate_confidences: dict[str, float],
    created_at: str | None = None,
) -> dict[str, Any]:
    """Pure builder for a LOW_CONFIDENCE_ASSIGNMENT# row.

    `candidate_confidences` is {subtopic_id: confidence}; all of the
    candidates the classifier considered under the PMID's top topic.
    `topic_id` is the top topic so the drift evaluator can roll up
    counts per-topic (spec §9's "any single topic accumulates >50"
    threshold).
    """
    confidences_decimal = {
        sid: Decimal(str(c)) for sid, c in candidate_confidences.items()
    }
    max_confidence = (
        max(confidences_decimal.values()) if confidences_decimal else Decimal("0")
    )
    return {
        "PK": f"LOW_CONFIDENCE_ASSIGNMENT#{pmid}",
        "SK": "GLOBAL",
        "record_type": "LOW_CONFIDENCE_ASSIGNMENT",
        "pmid": str(pmid),
        "topic_id": topic_id,
        "max_confidence": max_confidence,
        "candidate_confidences": confidences_decimal,
        "created_at": created_at or _now_iso(),
        "source_stage": "assign_subtopics",
    }


def write_low_confidence_assignment(table: Any, **kwargs: Any) -> dict[str, Any]:
    """Build a LOW_CONFIDENCE_ASSIGNMENT# row and persist via table.put_item."""
    item = build_low_confidence_assignment_record(**kwargs)
    table.put_item(Item=item)
    return item


# ---------------------------------------------------------------------------
# CRITIC_REJECT#{publish_id}#{subtopic_id}#{pmid_set_hash}
# ---------------------------------------------------------------------------


def _compute_pmid_set_hash(pmids: list[str]) -> str:
    """Stable, order-invariant hash for a PMID set.

    Sort first, comma-join, sha256, take 16 hex chars. Same set → same hash
    regardless of input ordering, giving the CRITIC_REJECT# producer natural
    dedup across critic retries within a single spotlight regen run (D-09).
    """
    joined = ",".join(sorted(str(p) for p in pmids))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def build_critic_reject_record(
    *,
    publish_id: str,
    subtopic_id: str,
    pmids: list[str],
    author_cwids: list[str],
    reason_code: str,
    regen_count: int,
    reason: str = "",
    raw_failed_constraint: str | None = None,
    pre_llm_constraint: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Pure builder for a CRITIC_REJECT# row.

    Phase 12 D-08 (CR-2026-05-12 re-framing): keying is per-(publish_id,
    subtopic_id, pmid_set), NOT per-cwid. Spotlight artifacts have
    authorship spanning many CWIDs; there is no single 'spotlight cwid'.
    Per-faculty drill-down is preserved via the author_cwids body field
    (distinct first/last-author person_identifiers across the rejected
    pmid_set), allowing the wave-2 consumer to surface affected faculty
    without forcing per-CWID aggregation.

    Idempotent via pmid_set_hash → same (publish_id, subtopic_id,
    pmid_set) overwrites within a regen run (D-09).

    PII boundary (Pattern H): carries reason_code, pmid_set, author_cwids,
    regen_count, reason only. SPOTLIGHT_REVIEW# is the artifact for human
    reviewers and is the only place the generated text lives.

    reason_code is one of CritReasonCode (defined in spotlight/critic.py):
    - "active_verb" | "anchored_in_synopses" | "no_faculty_named"
    - "institutional_voice"     (post-LLM rejections, verbatim from LLM)
    - "pre_llm_gate"            (deterministic gate failed; pre_llm_constraint
                                 carries the specific code)
    - "unknown"                 (vocabulary drift; raw_failed_constraint
                                 preserves the raw LLM string)
    """
    sorted_pmids = sorted(str(p) for p in pmids)
    pmid_set_hash = _compute_pmid_set_hash(sorted_pmids)
    # author_cwids: sort+dedup at the builder boundary so callers can pass raw lists
    sorted_cwids = sorted({str(c) for c in (author_cwids or []) if c})
    item: dict[str, Any] = {
        "PK": f"CRITIC_REJECT#{publish_id}#{subtopic_id}#{pmid_set_hash}",
        "SK": "GLOBAL",
        "record_type": "CRITIC_REJECT",
        "publish_id": str(publish_id),
        "subtopic_id": str(subtopic_id),
        "pmid_set": sorted_pmids,
        "pmid_set_hash": pmid_set_hash,
        "author_cwids": sorted_cwids,
        "reason_code": str(reason_code),
        "regen_count": int(regen_count),
        "reason": str(reason),
        "created_at": created_at or _now_iso(),
        "source_stage": "spotlight.critic",
    }
    if raw_failed_constraint is not None:
        item["raw_failed_constraint"] = str(raw_failed_constraint)
    if pre_llm_constraint is not None:
        item["pre_llm_constraint"] = str(pre_llm_constraint)
    return item


def write_critic_reject(table: Any, **kwargs: Any) -> dict[str, Any]:
    """Build a CRITIC_REJECT# row and persist via table.put_item."""
    item = build_critic_reject_record(**kwargs)
    table.put_item(Item=item)
    return item

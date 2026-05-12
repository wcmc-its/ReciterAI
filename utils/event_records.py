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

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any


# Default thresholds file lives at <repo>/config/thresholds.json.
DEFAULT_THRESHOLDS_PATH = (
    Path(__file__).parent.parent / "config" / "thresholds.json"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_thresholds(path: Path | None = None) -> dict[str, Any]:
    """Load the Phase 10 thresholds config (cached caller-side if needed).

    `path` overrides the default location for test ergonomics. Returns a
    plain dict — callers index by key.
    """
    target = Path(path) if path else DEFAULT_THRESHOLDS_PATH
    with open(target) as f:
        return json.load(f)


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

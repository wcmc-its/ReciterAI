"""Daily drift evaluator (D-04).

Reads three event-record types within a rolling N-day window:

- `UNCOVERED_PMID#{pmid}` — produced by score_publications (T6).
- `LOW_CONFIDENCE_ASSIGNMENT#{pmid}` — produced by assign_subtopics (T6).
- `STAGE#{stage}#…` `failed` rows across all stages — produced by Wave 2.

Computes:

- uncovered_rate = uncovered_count / (uncovered_count + assignment_count_in_window)
- low_confidence_max_topic = the topic_id with the most events
- low_confidence_max_count = the count for that topic
- triggered_thresholds = list of which D-11 thresholds tripped

Writes a single `DRIFT#evaluation` row (idempotent overwrite per run
date) and returns a structured result for the cron handler to alert on.

Severity mapping per draft D-11 table:
- uncovered_rate >= drift_uncovered_rate_alert  → ERROR (cold_run_recommended=true)
- low_confidence_max_count > drift_low_confidence_topic_max → ERROR
- Anything below alert threshold but non-zero → WARN
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable

logger = logging.getLogger(__name__)

DRIFT_PK = "DRIFT#evaluation"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_iso(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


# ---------------------------------------------------------------------------
# Pure evaluation
# ---------------------------------------------------------------------------


@dataclass
class DriftEvaluation:
    window_start: str
    window_end: str
    drift_window_days: int

    uncovered_count: int
    low_confidence_count: int
    stage_failed_count: int

    new_pmid_count: int  # denominator for uncovered_rate
    uncovered_rate: float

    low_confidence_max_topic: str | None
    low_confidence_max_count: int

    triggered_thresholds: list[str] = field(default_factory=list)
    cold_run_recommended: bool = False
    severity: str = "OK"  # OK | WARN | ERROR

    def to_dynamodb_item(self) -> dict[str, Any]:
        """Render as the DynamoDB item the evaluator persists.

        SK is the window end date so a daily cron produces one row per
        evaluation day. Floats are coerced to Decimal per the DDB rule.
        """
        return {
            "PK": DRIFT_PK,
            "SK": f"DAY#{self.window_end[:10]}",
            "record_type": "DRIFT_EVALUATION",
            "window_start": self.window_start,
            "window_end": self.window_end,
            "drift_window_days": self.drift_window_days,
            "uncovered_count": self.uncovered_count,
            "low_confidence_count": self.low_confidence_count,
            "stage_failed_count": self.stage_failed_count,
            "new_pmid_count": self.new_pmid_count,
            "uncovered_rate": Decimal(str(round(self.uncovered_rate, 6))),
            "low_confidence_max_topic": self.low_confidence_max_topic,
            "low_confidence_max_count": self.low_confidence_max_count,
            "triggered_thresholds": list(self.triggered_thresholds),
            "cold_run_recommended": self.cold_run_recommended,
            "severity": self.severity,
        }


def _filter_in_window(
    items: Iterable[dict], *, since: datetime, until: datetime, ts_field: str
) -> list[dict]:
    out = []
    for item in items:
        raw = item.get(ts_field)
        if not raw:
            continue
        try:
            ts = _parse_iso(str(raw))
        except ValueError:
            continue
        if since <= ts <= until:
            out.append(item)
    return out


def evaluate(
    *,
    uncovered_rows: Iterable[dict],
    low_confidence_rows: Iterable[dict],
    stage_failed_rows: Iterable[dict],
    new_pmid_count: int,
    thresholds: dict[str, Any],
    now: datetime | None = None,
) -> DriftEvaluation:
    """Pure evaluator.

    `new_pmid_count` is the denominator for uncovered_rate — the total
    number of PMIDs landed in the window from any source. The caller
    resolves this (counting distinct PMIDs across score STAGE# rows).

    Severity mapping (D-11 draft):
    - uncovered_rate >= drift_uncovered_rate_alert (0.05)         → ERROR
    - low_confidence_max_count > drift_low_confidence_topic_max  → ERROR
    - any nonzero uncovered or low-confidence under those floors → WARN
    - else                                                       → OK
    """
    now = now or datetime.now(timezone.utc)
    window_days = int(thresholds.get("drift_window_days", 14))
    since = now - timedelta(days=window_days)

    uncovered = _filter_in_window(
        uncovered_rows, since=since, until=now, ts_field="created_at"
    )
    low_conf = _filter_in_window(
        low_confidence_rows, since=since, until=now, ts_field="created_at"
    )
    stage_failed = _filter_in_window(
        stage_failed_rows, since=since, until=now, ts_field="started_at"
    )

    # Per-topic count for low-confidence (drift §9: "any single topic
    # accumulates >X" — count by topic_id).
    per_topic: dict[str, int] = {}
    for row in low_conf:
        tid = row.get("topic_id") or "<unknown>"
        per_topic[tid] = per_topic.get(tid, 0) + 1

    if per_topic:
        max_topic, max_count = max(per_topic.items(), key=lambda kv: (kv[1], kv[0]))
    else:
        max_topic, max_count = (None, 0)

    uncovered_rate = (
        len(uncovered) / new_pmid_count if new_pmid_count > 0 else 0.0
    )

    triggered: list[str] = []
    severity = "OK"

    rate_alert = float(thresholds.get("drift_uncovered_rate_alert", 0.05))
    if uncovered_rate >= rate_alert:
        triggered.append("uncovered_rate_alert")
        severity = "ERROR"
    elif len(uncovered) > 0:
        # Non-zero but below alert floor → WARN.
        if severity == "OK":
            severity = "WARN"

    topic_max = int(thresholds.get("drift_low_confidence_topic_max", 50))
    if max_count > topic_max:
        triggered.append("low_confidence_topic_max")
        severity = "ERROR"
    elif max_count > 0 and severity == "OK":
        severity = "WARN"

    if stage_failed:
        triggered.append("stage_failures_in_window")
        if severity == "OK":
            severity = "WARN"

    return DriftEvaluation(
        window_start=since.isoformat(timespec="seconds").replace("+00:00", "Z"),
        window_end=now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        drift_window_days=window_days,
        uncovered_count=len(uncovered),
        low_confidence_count=len(low_conf),
        stage_failed_count=len(stage_failed),
        new_pmid_count=new_pmid_count,
        uncovered_rate=uncovered_rate,
        low_confidence_max_topic=max_topic,
        low_confidence_max_count=max_count,
        triggered_thresholds=triggered,
        cold_run_recommended=severity == "ERROR",
        severity=severity,
    )


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------


def write_drift_row(table: Any, evaluation: DriftEvaluation) -> dict[str, Any]:
    item = evaluation.to_dynamodb_item()
    table.put_item(Item=item)
    return item


def run_evaluation(
    *,
    table: Any,
    uncovered_rows: Iterable[dict],
    low_confidence_rows: Iterable[dict],
    stage_failed_rows: Iterable[dict],
    new_pmid_count: int,
    thresholds: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate + persist; returns the DriftEvaluation as a dict the cron
    handler can dispatch alerts from (T11)."""
    eval_ = evaluate(
        uncovered_rows=uncovered_rows,
        low_confidence_rows=low_confidence_rows,
        stage_failed_rows=stage_failed_rows,
        new_pmid_count=new_pmid_count,
        thresholds=thresholds,
        now=now,
    )
    write_drift_row(table, eval_)
    return {
        "severity": eval_.severity,
        "cold_run_recommended": eval_.cold_run_recommended,
        "triggered_thresholds": eval_.triggered_thresholds,
        "uncovered_rate": eval_.uncovered_rate,
        "low_confidence_max_topic": eval_.low_confidence_max_topic,
        "low_confidence_max_count": eval_.low_confidence_max_count,
    }

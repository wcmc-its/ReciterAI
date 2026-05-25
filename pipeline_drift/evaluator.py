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
from utils.iso_clock import now_iso

logger = logging.getLogger(__name__)

DRIFT_PK = "DRIFT#evaluation"




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

    # Phase 12 D-34: per-topic LOW_CONFIDENCE# counts for this window.
    # Sparse-by-default: only topics with non-zero count appear as keys.
    # Absence == zero. Consumers MUST use .get(key, 0) to distinguish
    # "key absent" from "row predates field" (treat both as zero).
    per_topic_low_confidence: dict[str, int] = field(default_factory=dict)

    def to_dynamodb_item(self) -> dict[str, Any]:
        """Render as the DynamoDB item the evaluator persists.

        SK is the window end date so a daily cron produces one row per
        evaluation day. Floats are coerced to Decimal per the DDB rule.
        """
        item: dict[str, Any] = {
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
        # Phase 12 D-34: sparse — emit only when populated. Same Phase 11
        # D-13 run_id precedent (utils/stage_records.py:217-219). Counts are
        # int; no Decimal coercion needed (counts are not floats).
        if self.per_topic_low_confidence:
            item["per_topic_low_confidence"] = {
                str(k): int(v) for k, v in self.per_topic_low_confidence.items()
            }
        return item


def _filter_in_window(
    items: Iterable[dict], *, since: datetime, until: datetime, ts_field: str
) -> list[dict]:
    """Filter items by timestamp window.

    WR-10: counts timestamp parse failures and logs a single WARNING per call
    if any rows had unparseable timestamps. Previously the failure was silent —
    a bulk producer regression writing slightly-off timestamps would drop every
    row from drift evaluation with no signal beyond "zero rows".
    """
    out: list[dict] = []
    parse_failures = 0
    parse_sample: list[str] = []
    for item in items:
        raw = item.get(ts_field)
        if not raw:
            continue
        try:
            ts = _parse_iso(str(raw))
        except ValueError:
            parse_failures += 1
            if len(parse_sample) < 5:
                parse_sample.append(repr(raw))
            continue
        if since <= ts <= until:
            out.append(item)
    if parse_failures:
        logger.warning(
            "drift evaluator: %d timestamp(s) failed to parse for ts_field=%r; "
            "first %d sample(s): %s",
            parse_failures,
            ts_field,
            len(parse_sample),
            parse_sample,
        )
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

    # Phase 12 D-34: surface the per-topic dict computed above (previously
    # computed to derive max_topic/max_count and then discarded). Sparse:
    # filter out zero-count entries (any key with count > 0 is kept; the
    # per_topic dict only accumulates positive counts from the loop above,
    # so this filter is `v > 0`, but all entries in `per_topic` are already
    # >= 1 by construction — the filter is defensive).
    per_topic_low_confidence = {k: v for k, v in per_topic.items() if v > 0}

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
        per_topic_low_confidence=per_topic_low_confidence,
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


# ---------------------------------------------------------------------------
# Production DDB query seams
# ---------------------------------------------------------------------------


def _scan_by_pk_prefix(
    table: Any, *, pk_prefix: str, since_iso: str
) -> list[dict]:
    """Scan a single-table item type filtered by PK prefix + created_at.

    Used to collect UNCOVERED_PMID# and LOW_CONFIDENCE_ASSIGNMENT# rows
    within the rolling drift window. STAGE#…failed uses a different
    timestamp field so it has its own helper below.
    """
    kwargs = {
        "FilterExpression": "begins_with(#pk, :p) AND created_at >= :since",
        "ExpressionAttributeNames": {"#pk": "PK"},
        "ExpressionAttributeValues": {":p": pk_prefix, ":since": since_iso},
    }
    out: list[dict] = []
    last_key = None
    while True:
        if last_key is not None:
            kwargs["ExclusiveStartKey"] = last_key
        resp = table.scan(**kwargs)
        out.extend(resp.get("Items", []))
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
    return out


def _scan_stage_failed_since(table: Any, *, since_iso: str) -> list[dict]:
    """Collect STAGE#*#failed rows in window.

    Filters on started_at (STAGE# rows use started_at, not created_at).
    """
    kwargs = {
        "FilterExpression": (
            "begins_with(#pk, :stage) AND #status = :failed AND started_at >= :since"
        ),
        "ExpressionAttributeNames": {"#pk": "PK", "#status": "status"},
        "ExpressionAttributeValues": {
            ":stage": "STAGE#",
            ":failed": "failed",
            ":since": since_iso,
        },
    }
    out: list[dict] = []
    last_key = None
    while True:
        if last_key is not None:
            kwargs["ExclusiveStartKey"] = last_key
        resp = table.scan(**kwargs)
        out.extend(resp.get("Items", []))
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
    return out


def _count_new_pmids_since(table: Any, *, since_iso: str) -> int:
    """Denominator for uncovered_rate: distinct PMIDs that landed via
    score_publications STAGE# rows in window.

    Counts distinct values of the `pmid` field from
    STAGE#score_publications#pmid:* rows. The score writer emits one
    failed row per failing PMID and one aggregated complete row per
    run — both carry a `pmid` attribute we can dedupe on. When the
    score writer emits only a global aggregate row (no per-PMID rows),
    we fall back to counting UNCOVERED_PMID# rows plus the
    `records_written` aggregate from the latest complete row.
    """
    kwargs = {
        "FilterExpression": (
            "begins_with(#pk, :stage) AND started_at >= :since"
        ),
        "ExpressionAttributeNames": {"#pk": "PK"},
        "ExpressionAttributeValues": {
            ":stage": "STAGE#score_publications#",
            ":since": since_iso,
        },
    }
    seen: set[str] = set()
    aggregate_count = 0
    last_key = None
    while True:
        if last_key is not None:
            kwargs["ExclusiveStartKey"] = last_key
        resp = table.scan(**kwargs)
        for item in resp.get("Items", []):
            pmid = item.get("pmid")
            if pmid:
                seen.add(str(pmid))
            else:
                # Whole-corpus aggregate row: use records_written.
                rw = item.get("records_written")
                if rw and item.get("status") == "complete":
                    try:
                        aggregate_count = max(aggregate_count, int(rw))
                    except (TypeError, ValueError):
                        pass
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
    return max(len(seen), aggregate_count)


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------


def handler(event: dict | None = None, context: Any = None) -> dict[str, Any]:
    """EventBridge daily-cron entry point.

    Event fields (all optional):
        thresholds_path: override default config/thresholds.json
        now:             ISO-8601 override for `now` (test injection)

    Production flow:
      1. Load thresholds from config/thresholds.json.
      2. Scan three event-row types within drift_window_days.
      3. Count new PMIDs in window for the uncovered_rate denominator.
      4. Run evaluator + persist DRIFT# row.
      5. Dispatch a severity-tagged alert via pipeline_enrichment.alerting (Teams).

    OK → no alert. WARN → Teams card, no @mention (informational). ERROR
    (cold run recommended) → Teams card with an operator @mention. The
    Teams transport is the same one the hot path + enrichment use; it
    replaces the retired pipeline_common.alert Slack/`gh` path.
    """
    # Local imports keep cold-start fast and let tests avoid pulling boto3.
    from utils.dynamodb_helpers import get_table, TABLE_NAME
    from utils.event_records import load_thresholds
    from pipeline_enrichment import alerting

    event = event or {}
    thresholds_path = event.get("thresholds_path")
    now = (
        _parse_iso(event["now"])
        if event.get("now")
        else datetime.now(timezone.utc)
    )

    thresholds = load_thresholds(thresholds_path)
    window_days = int(thresholds.get("drift_window_days", 14))
    since = now - timedelta(days=window_days)
    since_iso = since.isoformat(timespec="seconds").replace("+00:00", "Z")

    table = get_table(TABLE_NAME)

    uncovered_rows = _scan_by_pk_prefix(
        table, pk_prefix="UNCOVERED_PMID#", since_iso=since_iso
    )
    low_confidence_rows = _scan_by_pk_prefix(
        table, pk_prefix="LOW_CONFIDENCE_ASSIGNMENT#", since_iso=since_iso
    )
    stage_failed_rows = _scan_stage_failed_since(table, since_iso=since_iso)
    new_pmid_count = _count_new_pmids_since(table, since_iso=since_iso)

    result = run_evaluation(
        table=table,
        uncovered_rows=uncovered_rows,
        low_confidence_rows=low_confidence_rows,
        stage_failed_rows=stage_failed_rows,
        new_pmid_count=new_pmid_count,
        thresholds=thresholds,
        now=now,
    )

    severity = result["severity"]
    if severity in ("WARN", "ERROR"):
        cold_run = result["cold_run_recommended"]
        title = "Drift threshold tripped" + (
            " — cold run recommended" if cold_run else ""
        )
        message = "Triggered: " + (
            ", ".join(result["triggered_thresholds"]) or "no specific threshold"
        )
        # @mention only when a cold run is recommended (actionable); WARN-band
        # drift is informational, so it posts a card without paging the operator.
        alerting.alert(
            severity,  # type: ignore[arg-type]
            title,
            message,
            {
                "source": "pipeline_drift.evaluator",
                "window_days": window_days,
                "window_start": since_iso,
                "uncovered_rate": result["uncovered_rate"],
                "low_confidence_max_topic": result["low_confidence_max_topic"],
                "low_confidence_max_count": result["low_confidence_max_count"],
                "triggered_thresholds": result["triggered_thresholds"],
                "cold_run_recommended": cold_run,
            },
            mention=cold_run,
        )

    return result

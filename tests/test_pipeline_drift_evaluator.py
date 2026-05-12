"""Phase 10 T10 — pipeline_drift.evaluator.

Covers:
- Window filtering: events outside drift_window_days are excluded.
- uncovered_rate: numerator / new_pmid_count denominator handling
  (zero-denominator edge case).
- per-topic low-confidence aggregation: max topic + count.
- D-11 severity ladder: ERROR thresholds drive cold_run_recommended;
  WARN for non-zero-but-below; OK for clean window.
- DRIFT# record shape: PK constant, SK=DAY#YYYY-MM-DD for daily idempotency.
- triggered_thresholds list populates with the right alert names.
- run_evaluation persists exactly one row via table.put_item.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from pipeline_drift.evaluator import (
    DRIFT_PK,
    DriftEvaluation,
    evaluate,
    run_evaluation,
    write_drift_row,
)


NOW = datetime(2026, 5, 12, 12, 0, 0, tzinfo=timezone.utc)
DAY = timedelta(days=1)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _uncov(pmid: str, created_at: datetime):
    return {"PK": f"UNCOVERED_PMID#{pmid}", "created_at": _iso(created_at)}


def _low_conf(pmid: str, topic_id: str, created_at: datetime):
    return {
        "PK": f"LOW_CONFIDENCE_ASSIGNMENT#{pmid}",
        "topic_id": topic_id,
        "created_at": _iso(created_at),
    }


def _stage_fail(stage: str, started_at: datetime):
    return {
        "PK": f"STAGE#{stage}#GLOBAL",
        "status": "failed",
        "started_at": _iso(started_at),
    }


THRESHOLDS = {
    "drift_window_days": 14,
    "drift_uncovered_rate_alert": 0.05,
    "drift_low_confidence_topic_max": 50,
}


# ---------- window filtering ----------


def test_evaluator_excludes_events_older_than_window():
    """20-day-old events are out of window; only the recent one counts."""
    result = evaluate(
        uncovered_rows=[
            _uncov("old1", NOW - 20 * DAY),
            _uncov("recent1", NOW - 3 * DAY),
        ],
        low_confidence_rows=[],
        stage_failed_rows=[],
        new_pmid_count=10,
        thresholds=THRESHOLDS,
        now=NOW,
    )
    assert result.uncovered_count == 1


def test_evaluator_includes_events_at_window_edge():
    """Events exactly at window_start ARE included (inclusive bound)."""
    result = evaluate(
        uncovered_rows=[_uncov("p", NOW - 14 * DAY)],
        low_confidence_rows=[],
        stage_failed_rows=[],
        new_pmid_count=10,
        thresholds=THRESHOLDS,
        now=NOW,
    )
    assert result.uncovered_count == 1


def test_evaluator_skips_rows_with_unparseable_timestamps():
    bad = {"PK": "UNCOVERED_PMID#x", "created_at": "not-a-date"}
    result = evaluate(
        uncovered_rows=[bad, _uncov("p", NOW - 1 * DAY)],
        low_confidence_rows=[],
        stage_failed_rows=[],
        new_pmid_count=10,
        thresholds=THRESHOLDS,
        now=NOW,
    )
    assert result.uncovered_count == 1


# ---------- severity ladder ----------


def test_severity_ok_on_empty_window():
    result = evaluate(
        uncovered_rows=[], low_confidence_rows=[], stage_failed_rows=[],
        new_pmid_count=100, thresholds=THRESHOLDS, now=NOW,
    )
    assert result.severity == "OK"
    assert result.cold_run_recommended is False
    assert result.triggered_thresholds == []


def test_severity_warn_when_uncovered_below_alert_rate():
    # 1 uncovered out of 100 new = 1% < 5% alert
    result = evaluate(
        uncovered_rows=[_uncov("p", NOW - 1 * DAY)],
        low_confidence_rows=[],
        stage_failed_rows=[],
        new_pmid_count=100,
        thresholds=THRESHOLDS,
        now=NOW,
    )
    assert result.severity == "WARN"
    assert result.cold_run_recommended is False


def test_severity_error_when_uncovered_at_or_above_alert_rate():
    # 5 uncovered out of 100 new = 5% == alert floor
    rows = [_uncov(f"p{i}", NOW - 1 * DAY) for i in range(5)]
    result = evaluate(
        uncovered_rows=rows,
        low_confidence_rows=[],
        stage_failed_rows=[],
        new_pmid_count=100,
        thresholds=THRESHOLDS,
        now=NOW,
    )
    assert result.severity == "ERROR"
    assert result.cold_run_recommended is True
    assert "uncovered_rate_alert" in result.triggered_thresholds


def test_severity_error_when_low_conf_topic_exceeds_max():
    # 51 low-confidence in topic "cardio" > drift_low_confidence_topic_max=50
    rows = [_low_conf(f"p{i}", "cardio", NOW - 1 * DAY) for i in range(51)]
    result = evaluate(
        uncovered_rows=[], low_confidence_rows=rows, stage_failed_rows=[],
        new_pmid_count=1000, thresholds=THRESHOLDS, now=NOW,
    )
    assert result.severity == "ERROR"
    assert result.cold_run_recommended is True
    assert "low_confidence_topic_max" in result.triggered_thresholds
    assert result.low_confidence_max_topic == "cardio"
    assert result.low_confidence_max_count == 51


def test_severity_warn_for_stage_failures_in_window():
    result = evaluate(
        uncovered_rows=[], low_confidence_rows=[],
        stage_failed_rows=[_stage_fail("score_publications", NOW - 1 * DAY)],
        new_pmid_count=10, thresholds=THRESHOLDS, now=NOW,
    )
    assert result.severity == "WARN"
    assert "stage_failures_in_window" in result.triggered_thresholds


# ---------- per-topic max ----------


def test_low_conf_per_topic_aggregation():
    rows = (
        [_low_conf(f"p{i}", "cardio", NOW - 1 * DAY) for i in range(10)] +
        [_low_conf(f"q{i}", "neuro", NOW - 1 * DAY) for i in range(5)]
    )
    result = evaluate(
        uncovered_rows=[], low_confidence_rows=rows, stage_failed_rows=[],
        new_pmid_count=100, thresholds=THRESHOLDS, now=NOW,
    )
    assert result.low_confidence_max_topic == "cardio"
    assert result.low_confidence_max_count == 10


def test_low_conf_no_rows_returns_none_topic_zero_count():
    result = evaluate(
        uncovered_rows=[], low_confidence_rows=[], stage_failed_rows=[],
        new_pmid_count=10, thresholds=THRESHOLDS, now=NOW,
    )
    assert result.low_confidence_max_topic is None
    assert result.low_confidence_max_count == 0


# ---------- uncovered_rate denominator ----------


def test_uncovered_rate_zero_when_no_new_pmids():
    """Zero denominator → zero rate (avoid divide-by-zero, treat as quiet)."""
    result = evaluate(
        uncovered_rows=[], low_confidence_rows=[], stage_failed_rows=[],
        new_pmid_count=0, thresholds=THRESHOLDS, now=NOW,
    )
    assert result.uncovered_rate == 0.0
    assert result.severity == "OK"


# ---------- DynamoDB shape ----------


def test_to_dynamodb_item_pk_and_sk():
    result = evaluate(
        uncovered_rows=[], low_confidence_rows=[], stage_failed_rows=[],
        new_pmid_count=10, thresholds=THRESHOLDS, now=NOW,
    )
    item = result.to_dynamodb_item()
    assert item["PK"] == DRIFT_PK == "DRIFT#evaluation"
    assert item["SK"] == "DAY#2026-05-12"
    assert item["record_type"] == "DRIFT_EVALUATION"
    assert isinstance(item["uncovered_rate"], Decimal)


def test_to_dynamodb_item_carries_triggered_thresholds_list():
    rows = [_uncov(f"p{i}", NOW - 1 * DAY) for i in range(10)]
    result = evaluate(
        uncovered_rows=rows, low_confidence_rows=[], stage_failed_rows=[],
        new_pmid_count=100, thresholds=THRESHOLDS, now=NOW,
    )
    item = result.to_dynamodb_item()
    assert "uncovered_rate_alert" in item["triggered_thresholds"]
    assert item["cold_run_recommended"] is True
    assert item["severity"] == "ERROR"


# ---------- run_evaluation persistence ----------


def test_run_evaluation_writes_one_drift_row():
    captured: list = []
    table = MagicMock()
    table.put_item.side_effect = lambda Item: captured.append(Item) or {}

    # 1 uncovered / 200 new = 0.5%, well under the 5% alert.
    out = run_evaluation(
        table=table,
        uncovered_rows=[_uncov("p", NOW - 1 * DAY)],
        low_confidence_rows=[],
        stage_failed_rows=[],
        new_pmid_count=200,
        thresholds=THRESHOLDS,
        now=NOW,
    )

    assert len(captured) == 1
    row = captured[0]
    assert row["PK"] == DRIFT_PK
    assert row["SK"] == "DAY#2026-05-12"
    assert out["severity"] == "WARN"
    assert out["cold_run_recommended"] is False

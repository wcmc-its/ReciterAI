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


# ---------- handler: Lambda entry point + alert wire-up (Clause 4 fix) ----------


def _make_paged_scan(items_by_call: list[list[dict]]):
    """Return a side_effect that yields successive scan responses."""
    calls = iter(items_by_call)

    def _scan(**kwargs):
        try:
            return {"Items": next(calls), "LastEvaluatedKey": None}
        except StopIteration:
            return {"Items": [], "LastEvaluatedKey": None}

    return _scan


def test_handler_dispatches_no_alert_on_ok_severity(monkeypatch):
    """Empty window → severity=OK → no Teams alert call."""
    from pipeline_drift import evaluator

    table = MagicMock()
    table.scan.return_value = {"Items": [], "LastEvaluatedKey": None}
    table.put_item.return_value = {}

    monkeypatch.setattr(
        "utils.dynamodb_helpers.get_table", lambda *a, **kw: table
    )
    monkeypatch.setattr(
        "utils.event_records.load_thresholds",
        lambda *a, **kw: {
            "drift_window_days": 14,
            "drift_uncovered_rate_alert": 0.05,
            "drift_low_confidence_topic_max": 50,
        },
    )

    dispatched: list = []
    monkeypatch.setattr(
        "pipeline_enrichment.alerting.alert",
        lambda *a, **kw: dispatched.append((a, kw)) or False,
    )

    result = evaluator.handler({"now": "2026-05-12T12:00:00Z"})
    assert result["severity"] == "OK"
    assert dispatched == []


def test_handler_dispatches_warn_when_low_band_events_exist(monkeypatch):
    """One uncovered PMID in window with 100 new PMIDs → 1% rate → WARN."""
    from pipeline_drift import evaluator

    table = MagicMock()

    def scan(**kwargs):
        fexp = kwargs["FilterExpression"]
        eav = kwargs["ExpressionAttributeValues"]
        if eav.get(":p") == "UNCOVERED_PMID#":
            return {"Items": [_uncov("p1", NOW - 1 * DAY)], "LastEvaluatedKey": None}
        if eav.get(":p") == "LOW_CONFIDENCE_ASSIGNMENT#":
            return {"Items": [], "LastEvaluatedKey": None}
        if ":failed" in eav:
            return {"Items": [], "LastEvaluatedKey": None}
        if ":stage" in eav and eav.get(":stage") == "STAGE#score_publications#":
            # 100 new PMIDs aggregate
            return {
                "Items": [
                    {
                        "PK": "STAGE#score_publications#GLOBAL",
                        "status": "complete",
                        "records_written": 100,
                    }
                ],
                "LastEvaluatedKey": None,
            }
        return {"Items": [], "LastEvaluatedKey": None}

    table.scan.side_effect = scan
    table.put_item.return_value = {}

    monkeypatch.setattr(
        "utils.dynamodb_helpers.get_table", lambda *a, **kw: table
    )
    monkeypatch.setattr(
        "utils.event_records.load_thresholds",
        lambda *a, **kw: {
            "drift_window_days": 14,
            "drift_uncovered_rate_alert": 0.05,
            "drift_low_confidence_topic_max": 50,
        },
    )

    dispatched: list = []
    monkeypatch.setattr(
        "pipeline_enrichment.alerting.alert",
        lambda *a, **kw: dispatched.append((a, kw)) or True,
    )

    result = evaluator.handler({"now": "2026-05-12T12:00:00Z"})
    assert result["severity"] == "WARN"
    assert len(dispatched) == 1
    args, kwargs = dispatched[0]
    assert args[0] == "WARN"
    # WARN band is informational → no operator @mention.
    assert kwargs.get("mention") is False


def test_handler_dispatches_error_with_open_issue_on_cold_run_recommended(monkeypatch):
    """Uncovered rate ≥ 5% → ERROR + open_issue=True."""
    from pipeline_drift import evaluator

    table = MagicMock()

    def scan(**kwargs):
        eav = kwargs["ExpressionAttributeValues"]
        if eav.get(":p") == "UNCOVERED_PMID#":
            # 6 uncovered events in window
            return {
                "Items": [_uncov(f"p{i}", NOW - 1 * DAY) for i in range(6)],
                "LastEvaluatedKey": None,
            }
        if eav.get(":p") == "LOW_CONFIDENCE_ASSIGNMENT#":
            return {"Items": [], "LastEvaluatedKey": None}
        if ":failed" in eav:
            return {"Items": [], "LastEvaluatedKey": None}
        if eav.get(":stage") == "STAGE#score_publications#":
            # 100 new PMIDs aggregate → 6/100 = 6% > 5%
            return {
                "Items": [
                    {
                        "PK": "STAGE#score_publications#GLOBAL",
                        "status": "complete",
                        "records_written": 100,
                    }
                ],
                "LastEvaluatedKey": None,
            }
        return {"Items": [], "LastEvaluatedKey": None}

    table.scan.side_effect = scan
    table.put_item.return_value = {}

    monkeypatch.setattr(
        "utils.dynamodb_helpers.get_table", lambda *a, **kw: table
    )
    monkeypatch.setattr(
        "utils.event_records.load_thresholds",
        lambda *a, **kw: {
            "drift_window_days": 14,
            "drift_uncovered_rate_alert": 0.05,
            "drift_low_confidence_topic_max": 50,
        },
    )

    dispatched: list = []
    monkeypatch.setattr(
        "pipeline_enrichment.alerting.alert",
        lambda *a, **kw: dispatched.append((a, kw)) or True,
    )

    result = evaluator.handler({"now": "2026-05-12T12:00:00Z"})
    assert result["severity"] == "ERROR"
    assert result["cold_run_recommended"] is True
    assert len(dispatched) == 1
    args, kwargs = dispatched[0]
    # Teams signature: alert(severity, title, message, context, mention=)
    assert args[0] == "ERROR"
    assert "cold run recommended" in args[1].lower()  # title carries the cold-run note
    assert kwargs.get("mention") is True  # cold run → page the operator
    # Context payload carries triage info
    ctx = args[3]
    assert "triggered_thresholds" in ctx
    assert ctx["cold_run_recommended"] is True


def test_count_new_pmids_dedupes_per_pmid_rows():
    """Per-PMID STAGE#score_publications#pmid:* rows count distinct pmid."""
    from pipeline_drift.evaluator import _count_new_pmids_since

    table = MagicMock()
    table.scan.return_value = {
        "Items": [
            {"PK": "STAGE#score_publications#pmid:1", "pmid": "1", "status": "complete"},
            {"PK": "STAGE#score_publications#pmid:1", "pmid": "1", "status": "complete"},
            {"PK": "STAGE#score_publications#pmid:2", "pmid": "2", "status": "failed"},
            {"PK": "STAGE#score_publications#GLOBAL", "status": "complete", "records_written": 100},
        ],
        "LastEvaluatedKey": None,
    }
    # max(distinct pmid count=2, aggregate=100) = 100
    assert _count_new_pmids_since(table, since_iso="2026-04-28T00:00:00Z") == 100


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


# ---------- duration_ms ----------


def test_run_evaluation_stamps_an_int_duration_on_the_row():
    """SPS's producer board has a run-duration column that every STAGE# row fills and
    this DRIFT# row did not."""
    captured: list = []
    table = MagicMock()
    table.put_item.side_effect = lambda Item: captured.append(Item) or {}

    run_evaluation(
        table=table,
        uncovered_rows=[],
        low_confidence_rows=[],
        stage_failed_rows=[],
        new_pmid_count=0,
        thresholds=THRESHOLDS,
        now=NOW,
    )

    duration = captured[0]["duration_ms"]
    # >= 0, not > 0: an evaluation of zero rows can round to a whole millisecond of
    # nothing. The assertion that matters is that the field is there and is an int
    # (DynamoDB has no float, and the board formats it as a number).
    assert isinstance(duration, int) and duration >= 0


def test_an_untimed_evaluation_omits_duration_ms():
    """Optional-shaped, like per_topic_low_confidence: `evaluate` is pure and times
    nothing, so a row built straight off it carries no duration rather than a zero
    that would read as an instantaneous run. Rows predating the field behave the same
    way, and consumers must survive both."""
    item = evaluate(
        uncovered_rows=[],
        low_confidence_rows=[],
        stage_failed_rows=[],
        new_pmid_count=0,
        thresholds=THRESHOLDS,
        now=NOW,
    ).to_dynamodb_item()

    assert "duration_ms" not in item


# ---------- Phase 12 D-34: per_topic_low_confidence ----------


def test_per_topic_low_confidence_populated_from_in_window_rows():
    """evaluate() returns per_topic_low_confidence dict with counts per topic_id.

    Feed 2 rows for topic_a and 1 for topic_b; assert the new field
    maps topic_a → 2 and topic_b → 1.
    """
    rows = (
        [_low_conf(f"a{i}", "topic_a", NOW - 1 * DAY) for i in range(2)] +
        [_low_conf("b0", "topic_b", NOW - 1 * DAY)]
    )
    result = evaluate(
        uncovered_rows=[],
        low_confidence_rows=rows,
        stage_failed_rows=[],
        new_pmid_count=100,
        thresholds=THRESHOLDS,
        now=NOW,
    )
    assert result.per_topic_low_confidence == {"topic_a": 2, "topic_b": 1}


def test_per_topic_low_confidence_sparse_zero_omitted():
    """Topics with zero in-window count are absent from per_topic_low_confidence.

    Feed rows only for topic_a; topic_x has no events in window.
    Absence of topic_x key == zero (sparse-by-default contract, D-34).
    """
    rows = [_low_conf("a0", "topic_a", NOW - 1 * DAY)]
    result = evaluate(
        uncovered_rows=[],
        low_confidence_rows=rows,
        stage_failed_rows=[],
        new_pmid_count=100,
        thresholds=THRESHOLDS,
        now=NOW,
    )
    assert "topic_x" not in result.per_topic_low_confidence
    assert "topic_a" in result.per_topic_low_confidence


def test_to_dynamodb_item_omits_field_when_dict_empty():
    """Sparse-by-default: when per_topic_low_confidence is empty, field absent from DDB item.

    Consumers of old DRIFT#evaluation rows (without the field) read absence
    as zero via item.get("per_topic_low_confidence", {}).
    """
    d = DriftEvaluation(
        window_start="",
        window_end="2026-05-12T00:00:00Z",
        drift_window_days=1,
        uncovered_count=0,
        low_confidence_count=0,
        stage_failed_count=0,
        new_pmid_count=0,
        uncovered_rate=0.0,
        low_confidence_max_topic=None,
        low_confidence_max_count=0,
        triggered_thresholds=[],
        cold_run_recommended=False,
        severity="OK",
        per_topic_low_confidence={},
    )
    item = d.to_dynamodb_item()
    assert "per_topic_low_confidence" not in item


def test_to_dynamodb_item_includes_field_when_non_empty():
    """When per_topic_low_confidence is non-empty, field is present in DDB item.

    Values must be int (not Decimal) — counts are int, not float.
    """
    d = DriftEvaluation(
        window_start="",
        window_end="2026-05-12T00:00:00Z",
        drift_window_days=1,
        uncovered_count=0,
        low_confidence_count=0,
        stage_failed_count=0,
        new_pmid_count=0,
        uncovered_rate=0.0,
        low_confidence_max_topic=None,
        low_confidence_max_count=0,
        triggered_thresholds=[],
        cold_run_recommended=False,
        severity="OK",
        per_topic_low_confidence={"a": 3, "b": 1},
    )
    item = d.to_dynamodb_item()
    assert "per_topic_low_confidence" in item
    assert item["per_topic_low_confidence"] == {"a": 3, "b": 1}
    # All values must be int, not Decimal or float.
    for v in item["per_topic_low_confidence"].values():
        assert isinstance(v, int), f"expected int, got {type(v)}"


def test_low_confidence_max_topic_unchanged():
    """Behavior contract: low_confidence_max_topic and low_confidence_max_count
    are computed exactly as before Phase 12 D-34's additive field.

    Uses the same input as test_per_topic_low_confidence_populated_from_in_window_rows.
    After adding per_topic_low_confidence, max_topic=topic_a (count=2) is preserved.
    """
    rows = (
        [_low_conf(f"a{i}", "topic_a", NOW - 1 * DAY) for i in range(2)] +
        [_low_conf("b0", "topic_b", NOW - 1 * DAY)]
    )
    result = evaluate(
        uncovered_rows=[],
        low_confidence_rows=rows,
        stage_failed_rows=[],
        new_pmid_count=100,
        thresholds=THRESHOLDS,
        now=NOW,
    )
    # The existing max-topic semantics must be preserved.
    assert result.low_confidence_max_topic == "topic_a"
    assert result.low_confidence_max_count == 2


def test_severity_ladder_unchanged():
    """Behavior contract: severity ladder is unaffected by D-34's additive field.

    See test_severity_warn_when_uncovered_below_alert_rate for the existing
    assertion; this test verifies the same invariant holds after Phase 12
    D-34's additive field addition.
    """
    # 1 uncovered / 100 new = 1% < 5% alert → WARN (below ERROR threshold).
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
    # per_topic_low_confidence does not affect severity.
    assert result.per_topic_low_confidence == {}


def test_cold_run_recommended_unchanged():
    """Behavior contract: cold_run_recommended derivation is unaffected by D-34.

    A known input that produced cold_run_recommended=True before Phase 12
    (uncovered rate >= 5%) still produces True.
    """
    rows = [_uncov(f"p{i}", NOW - 1 * DAY) for i in range(5)]
    result = evaluate(
        uncovered_rows=rows,
        low_confidence_rows=[],
        stage_failed_rows=[],
        new_pmid_count=100,  # 5/100 = 5% == alert floor → ERROR
        thresholds=THRESHOLDS,
        now=NOW,
    )
    assert result.cold_run_recommended is True
    assert result.severity == "ERROR"
    # per_topic_low_confidence for this input is empty (no low-conf rows).
    assert result.per_topic_low_confidence == {}


def test_consumer_reads_absent_field_as_absent():
    """Consumer contract for backward compatibility (D-34 sparse semantics).

    An old DRIFT#evaluation DDB row (predating Phase 12) won't have the
    per_topic_low_confidence key. Consumers MUST use:
        item.get("per_topic_low_confidence", {})
    This returns an empty dict for old rows and the dict for new rows.
    Both cases: treat absent key as "zero counts for all topics."

    This test emulates a consumer reading an old row dict and demonstrates
    the safe access pattern.
    """
    # Simulate an old DRIFT#evaluation row (no per_topic_low_confidence key).
    old_row: dict = {
        "PK": "DRIFT#evaluation",
        "SK": "DAY#2025-01-01",
        "record_type": "DRIFT_EVALUATION",
        "severity": "OK",
    }
    # Safe consumer pattern — returns {} for missing key.
    per_topic = old_row.get("per_topic_low_confidence", {})
    assert per_topic == {}
    # For a new row with data, same pattern returns the dict.
    new_row = {**old_row, "per_topic_low_confidence": {"cardio": 5}}
    per_topic_new = new_row.get("per_topic_low_confidence", {})
    assert per_topic_new == {"cardio": 5}

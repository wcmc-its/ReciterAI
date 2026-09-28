"""#406 — per-topic TOPIC# field coverage, measured in the taxonomy drift Scan."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pipeline_taxonomy_drift.checker import (
    FIELDS_PK,
    evaluate_field_coverage,
    run_check,
    scan_topic_partitions,
)

_T = {
    "topic_field_author_position_min_coverage": 0.99,
    "topic_field_subtopic_min_coverage": 0.75,
    "topic_field_subtopic_exempt_topics": ["no_hierarchy"],
}


def _rows(topic, n, *, ap, sub):
    return [
        {"PK": f"TOPIC#{topic}",
         **({"author_position": "first"} if i < ap else {}),
         **({"primary_subtopic_id": "s1"} if i < sub else {})}
        for i in range(n)
    ]


def _eval(counts):
    return evaluate_field_coverage(
        counts, author_position_min=0.99, subtopic_min=0.75, subtopic_exempt=["no_hierarchy"])


def test_scan_counts_fields_in_the_same_pass():
    table = MagicMock()
    table.scan.side_effect = [
        {"Items": _rows("a", 3, ap=3, sub=2) + [{"PK": "TOPIC#a", "author_position": ""}]},
    ]
    counts = {}
    scan_topic_partitions(table, counts)
    assert counts == {"a": {"rows": 4, "author_position": 3, "primary_subtopic_id": 2}}
    assert table.scan.call_count == 1
    assert "author_position" in table.scan.call_args.kwargs["ProjectionExpression"]


def test_healthy_topics_are_ok_including_steady_state_subtopic_gaps():
    # ~9% of rows carry no subtopic in steady state; that must not alert.
    r = _eval({"a": {"rows": 100, "author_position": 100, "primary_subtopic_id": 80}})
    assert r["severity"] == "OK" and r["violations"] == []


def test_a_few_stale_author_position_blanks_are_tolerated():
    r = _eval({"a": {"rows": 404, "author_position": 402, "primary_subtopic_id": 404}})
    assert r["severity"] == "OK"


def test_the_405_shape_is_an_error():
    # 69% of rows blank, as SPS measured before #405.
    r = _eval({"a": {"rows": 1000, "author_position": 310, "primary_subtopic_id": 900}})
    assert r["severity"] == "ERROR"
    assert r["violations"] == [
        {"topic": "a", "field": "author_position", "covered": 310, "rows": 1000, "level": "ERROR"}]


def test_subtopic_collapse_is_a_warn_and_exempt_topics_are_skipped():
    r = _eval({
        "a": {"rows": 100, "author_position": 100, "primary_subtopic_id": 10},
        "no_hierarchy": {"rows": 94, "author_position": 94, "primary_subtopic_id": 0},
    })
    assert r["severity"] == "WARN"
    assert [(v["topic"], v["field"]) for v in r["violations"]] == [("a", "primary_subtopic_id")]


def test_error_outranks_warn():
    r = _eval({
        "a": {"rows": 100, "author_position": 0, "primary_subtopic_id": 100},
        "b": {"rows": 100, "author_position": 100, "primary_subtopic_id": 0},
    })
    assert r["severity"] == "ERROR"


def _table(items):
    table = MagicMock()
    table.scan.side_effect = [{"Items": items}]
    table.get_item.return_value = {"Item": {"topics": [{"id": "a"}]}}
    return table


def _field_puts(table):
    return [c.kwargs["Item"] for c in table.put_item.call_args_list
            if c.kwargs["Item"]["PK"] == FIELDS_PK]


def test_run_check_writes_its_own_row_and_leaves_the_taxonomy_row_alone(monkeypatch):
    import pipeline_enrichment.alerting as alerting

    monkeypatch.setattr(alerting, "alert", lambda *a, **k: pytest.fail("clean run alerted"))
    table = _table(_rows("a", 10, ap=10, sub=10))
    result = run_check(table, ["a"], taxonomy_hash="h", day="2026-09-28", thresholds=_T)

    assert result["severity"] == "OK" and result["topic_fields"]["severity"] == "OK"
    (row,) = _field_puts(table)
    assert row["SK"] == "DAY#2026-09-28"
    assert row["coverage"] == {"a": {"rows": 10, "author_position": 10, "primary_subtopic_id": 10}}
    taxonomy_rows = [c.kwargs["Item"] for c in table.put_item.call_args_list
                     if c.kwargs["Item"]["PK"] == "DRIFT#taxonomy"]
    assert len(taxonomy_rows) == 1 and "coverage" not in taxonomy_rows[0]


@pytest.mark.parametrize("dispatched", [True, False])
def test_violation_alerts_with_mention_and_records_delivery(monkeypatch, dispatched):
    import pipeline_enrichment.alerting as alerting

    calls = []
    monkeypatch.setattr(alerting, "alert", lambda *a, **k: calls.append((a, k)) or dispatched)
    table = _table(_rows("a", 10, ap=0, sub=10))
    result = run_check(table, ["a"], taxonomy_hash="h", day="2026-09-28", thresholds=_T)

    assert result["topic_fields"]["severity"] == "ERROR"
    ((severity, title, _msg, ctx), kwargs), = calls
    assert severity == "ERROR" and "author_position" in title
    assert ctx["author_position"] == ["a (0/10)"] and kwargs["mention"] is True
    puts = _field_puts(table)
    assert len(puts) == 2 and puts[-1]["alert_sent"] is dispatched


def test_alert_still_fires_when_the_row_write_fails(monkeypatch):
    import pipeline_enrichment.alerting as alerting

    calls = []
    monkeypatch.setattr(alerting, "alert", lambda *a, **k: calls.append(a) or True)
    table = _table(_rows("a", 10, ap=10, sub=0))
    table.put_item.side_effect = RuntimeError("ValidationException")
    result = run_check(table, ["a"], taxonomy_hash="h", day="2026-09-28", thresholds=_T)

    assert result["topic_fields"]["row_persisted"] is False
    assert len(calls) == 1 and calls[0][0] == "WARN"


def test_shipped_thresholds_carry_the_keys():
    from utils.env_check import load_thresholds

    t = load_thresholds()
    assert 0 < t["topic_field_subtopic_min_coverage"] < t["topic_field_author_position_min_coverage"] <= 1
    assert "oral_craniofacial_health" in t["topic_field_subtopic_exempt_topics"]


def test_a_coverage_failure_alerts_without_failing_the_run(monkeypatch):
    import pipeline_enrichment.alerting as alerting

    calls = []
    monkeypatch.setattr(alerting, "alert", lambda *a, **k: calls.append(a) or True)
    table = _table(_rows("a", 10, ap=10, sub=10))
    result = run_check(table, ["a"], taxonomy_hash="h", day="2026-09-28", thresholds={})

    assert result["severity"] == "OK"  # taxonomy half unaffected
    assert result["topic_fields"]["severity"] == "ERROR"
    assert "KeyError" in result["topic_fields"]["error"]
    assert [c[1] for c in calls] == ["TOPIC# field coverage check failed"]

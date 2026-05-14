"""Unit tests for pipeline_enrichment.watermark."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pipeline_enrichment.watermark import (
    PK,
    SK,
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_IN_PROGRESS,
    Watermark,
    mark_run_complete,
    mark_run_failed,
    mark_run_started,
    read_watermark,
)


@pytest.fixture
def mock_table():
    return MagicMock()


def test_read_watermark_returns_none_when_item_missing(mock_table):
    mock_table.get_item.return_value = {}
    assert read_watermark(table=mock_table) is None
    mock_table.get_item.assert_called_once_with(Key={"PK": PK, "SK": SK})


def test_read_watermark_returns_populated_dataclass(mock_table):
    mock_table.get_item.return_value = {
        "Item": {
            "PK": PK,
            "SK": SK,
            "last_successful_run_at": "2026-05-13T14:00:00Z",
            "last_successful_max_pmid": 40927852,
            "last_run_started_at": "2026-05-13T14:00:00Z",
            "last_run_status": STATUS_COMPLETE,
        }
    }
    wm = read_watermark(table=mock_table)
    assert wm == Watermark(
        last_successful_run_at="2026-05-13T14:00:00Z",
        last_successful_max_pmid=40927852,
        last_run_started_at="2026-05-13T14:00:00Z",
        last_run_status=STATUS_COMPLETE,
    )


def test_read_watermark_coerces_max_pmid_to_int(mock_table):
    """DDB returns Decimal for numbers; the dataclass must surface int."""
    from decimal import Decimal
    mock_table.get_item.return_value = {
        "Item": {
            "last_successful_max_pmid": Decimal("40927852"),
        }
    }
    wm = read_watermark(table=mock_table)
    assert wm.last_successful_max_pmid == 40927852
    assert isinstance(wm.last_successful_max_pmid, int)


def test_read_watermark_handles_partial_item(mock_table):
    """First run after a failure may have status but no successful_* fields."""
    mock_table.get_item.return_value = {
        "Item": {"last_run_status": STATUS_FAILED}
    }
    wm = read_watermark(table=mock_table)
    assert wm.last_run_status == STATUS_FAILED
    assert wm.last_successful_max_pmid is None
    assert wm.last_successful_run_at is None


def test_mark_run_started_writes_started_at_and_in_progress(mock_table):
    ts = mark_run_started(table=mock_table)
    assert ts.endswith("Z")
    assert "T" in ts
    call = mock_table.update_item.call_args
    assert call.kwargs["Key"] == {"PK": PK, "SK": SK}
    assert call.kwargs["ExpressionAttributeValues"][":status"] == STATUS_IN_PROGRESS
    assert call.kwargs["ExpressionAttributeValues"][":started"] == ts
    # Must NOT touch last_successful_*
    expr = call.kwargs["UpdateExpression"]
    assert "last_successful" not in expr


def test_mark_run_complete_advances_max_pmid_and_run_at(mock_table):
    ts = mark_run_complete(max_pmid=40999999, table=mock_table)
    assert ts.endswith("Z")
    call = mock_table.update_item.call_args
    vals = call.kwargs["ExpressionAttributeValues"]
    assert vals[":status"] == STATUS_COMPLETE
    assert vals[":max_pmid"] == 40999999
    assert vals[":run_at"] == ts
    expr = call.kwargs["UpdateExpression"]
    assert "last_successful_run_at" in expr
    assert "last_successful_max_pmid" in expr


def test_mark_run_complete_coerces_max_pmid_to_int(mock_table):
    mark_run_complete(max_pmid="40999999", table=mock_table)
    vals = mock_table.update_item.call_args.kwargs["ExpressionAttributeValues"]
    assert vals[":max_pmid"] == 40999999
    assert isinstance(vals[":max_pmid"], int)


def test_mark_run_failed_does_not_touch_successful_fields(mock_table):
    mark_run_failed(table=mock_table)
    call = mock_table.update_item.call_args
    expr = call.kwargs["UpdateExpression"]
    vals = call.kwargs["ExpressionAttributeValues"]
    assert vals[":status"] == STATUS_FAILED
    assert "last_successful" not in expr
    assert "last_run_started_at" not in expr


def test_failure_then_retry_preserves_delta_starting_point(mock_table):
    """Failed run must leave last_successful_max_pmid intact so next run
    re-queries the same delta."""
    # Simulate: previous successful run wrote max_pmid=100.
    mock_table.get_item.return_value = {
        "Item": {
            "last_successful_max_pmid": 100,
            "last_run_status": STATUS_COMPLETE,
        }
    }
    wm_before = read_watermark(table=mock_table)
    assert wm_before.last_successful_max_pmid == 100

    # Today's run starts...
    mark_run_started(table=mock_table)
    # ...and fails before completing.
    mark_run_failed(table=mock_table)

    # Neither mark_run_started nor mark_run_failed should have touched
    # last_successful_max_pmid in their UpdateExpressions.
    for call in mock_table.update_item.call_args_list:
        assert "last_successful_max_pmid" not in call.kwargs["UpdateExpression"]

"""Unit tests for pipeline_enrichment.quarantine (#137)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from pipeline_enrichment import quarantine
from pipeline_enrichment.quarantine import (
    PK_PREFIX,
    SK,
    clear,
    is_quarantined,
    list_quarantined,
    record_failure,
)


@pytest.fixture
def mock_table():
    return MagicMock()


def _ccf_error():
    """Build a ConditionalCheckFailedException that boto3 would raise."""
    return ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "ccf"}},
        "UpdateItem",
    )


# ---------------------------------------------------------------------------
# record_failure
# ---------------------------------------------------------------------------

def test_record_failure_first_call_returns_one(mock_table):
    mock_table.update_item.return_value = {
        "Attributes": {"consecutive_failures": 1}
    }
    count = record_failure(
        "42119587", reason="bedrock truncation", run_id="run-1",
        table=mock_table,
    )
    assert count == 1


def test_record_failure_writes_full_row_attributes(mock_table):
    mock_table.update_item.return_value = {
        "Attributes": {"consecutive_failures": 1}
    }
    record_failure(
        "42119587", reason="bedrock truncation", run_id="run-1",
        table=mock_table,
    )
    call = mock_table.update_item.call_args
    assert call.kwargs["Key"] == {
        "PK": f"{PK_PREFIX}42119587", "SK": SK,
    }
    expr = call.kwargs["UpdateExpression"]
    # ADD on the counter (atomic increment); SET on the metadata.
    assert "ADD consecutive_failures :one" in expr
    assert "SET pmid = :pmid" in expr
    assert "last_failure_at = :ts" in expr
    assert "last_failure_reason = :reason" in expr
    assert "last_attempted_run_id = :run_id" in expr
    # created_at uses if_not_exists so it survives subsequent increments.
    assert "created_at = if_not_exists(created_at, :ts)" in expr
    vals = call.kwargs["ExpressionAttributeValues"]
    assert vals[":one"] == 1
    assert vals[":pmid"] == "42119587"
    assert vals[":reason"] == "bedrock truncation"
    assert vals[":run_id"] == "run-1"
    assert vals[":ts"].endswith("Z") and "T" in vals[":ts"]


def test_record_failure_pmid_coerced_to_string(mock_table):
    """Caller may pass pmid as int from a DB row; the DDB attribute must be str."""
    mock_table.update_item.return_value = {
        "Attributes": {"consecutive_failures": 1}
    }
    record_failure(
        42119587, reason="x", run_id="run-1", table=mock_table,
    )
    call = mock_table.update_item.call_args
    assert call.kwargs["Key"]["PK"] == f"{PK_PREFIX}42119587"
    assert call.kwargs["ExpressionAttributeValues"][":pmid"] == "42119587"


def test_record_failure_subsequent_call_returns_incremented_count(mock_table):
    """Two failures across different runs → counter reaches 2."""
    mock_table.update_item.return_value = {
        "Attributes": {"consecutive_failures": 2}
    }
    count = record_failure(
        "42119587", reason="bedrock truncation again", run_id="run-2",
        table=mock_table,
    )
    assert count == 2


def test_record_failure_uses_condition_to_block_same_run_id_double_count(mock_table):
    mock_table.update_item.return_value = {
        "Attributes": {"consecutive_failures": 1}
    }
    record_failure(
        "42119587", reason="x", run_id="run-1", table=mock_table,
    )
    cond = mock_table.update_item.call_args.kwargs["ConditionExpression"]
    assert "attribute_not_exists(last_attempted_run_id)" in cond
    assert "last_attempted_run_id <> :run_id" in cond


def test_record_failure_idempotent_on_same_run_id(mock_table):
    """If the same (pmid, run_id) record_failure is called twice — e.g. a retried
    subprocess — the second call must be a no-op returning the existing count."""
    mock_table.update_item.side_effect = _ccf_error()
    mock_table.get_item.return_value = {
        "Item": {
            "consecutive_failures": 2,
            "last_attempted_run_id": "run-1",
        }
    }
    count = record_failure(
        "42119587", reason="x", run_id="run-1", table=mock_table,
    )
    assert count == 2
    # We did try the UpdateItem; we fell back to GetItem on the CCF.
    mock_table.update_item.assert_called_once()
    mock_table.get_item.assert_called_once_with(
        Key={"PK": f"{PK_PREFIX}42119587", "SK": SK}
    )


def test_record_failure_non_ccf_client_error_propagates(mock_table):
    """A ProvisionedThroughputExceededException (or any non-CCF) must surface."""
    mock_table.update_item.side_effect = ClientError(
        {"Error": {"Code": "ProvisionedThroughputExceededException",
                   "Message": "boom"}},
        "UpdateItem",
    )
    with pytest.raises(ClientError):
        record_failure(
            "42119587", reason="x", run_id="run-1", table=mock_table,
        )


def test_record_failure_truncates_long_reason(mock_table):
    """last_failure_reason caps at 1000 chars to match the scoring-quarantine
    helper in utils/dynamodb_helpers.py — same DDB table, same write-amplification
    budget."""
    mock_table.update_item.return_value = {
        "Attributes": {"consecutive_failures": 1}
    }
    long_reason = "x" * 5000
    record_failure(
        "42119587", reason=long_reason, run_id="run-1", table=mock_table,
    )
    vals = mock_table.update_item.call_args.kwargs["ExpressionAttributeValues"]
    assert len(vals[":reason"]) == 1000


@pytest.mark.parametrize("bad_pmid", ["20", "2001", "3001", "4001", "5001", 999_999])
def test_record_failure_rejects_pmid_below_real_floor(mock_table, bad_pmid):
    """PMIDs below the 1M floor are almost certainly test fixtures — refuse
    at the write boundary. The literal values here are the exact 5 stale rows
    the 2026-05-20 #112 backfill cleanup found in prod DDB, plus a near-miss."""
    with pytest.raises(ValueError, match="real-PMID floor"):
        record_failure(
            bad_pmid, reason="boom", run_id="run-xyz", table=mock_table,
        )
    mock_table.update_item.assert_not_called()


def test_record_failure_rejects_non_numeric_pmid(mock_table):
    with pytest.raises(ValueError, match="non-numeric"):
        record_failure(
            "not-a-pmid", reason="boom", run_id="run-xyz", table=mock_table,
        )
    mock_table.update_item.assert_not_called()


def test_record_failure_accepts_pmid_at_floor(mock_table):
    """The floor itself is accepted — only strictly-below is rejected."""
    mock_table.update_item.return_value = {
        "Attributes": {"consecutive_failures": 1}
    }
    record_failure(
        "1000000", reason="hypothetical floor case", run_id="run-1",
        table=mock_table,
    )
    mock_table.update_item.assert_called_once()


# ---------------------------------------------------------------------------
# is_quarantined
# ---------------------------------------------------------------------------

def test_is_quarantined_false_when_row_missing(mock_table):
    mock_table.get_item.return_value = {}
    assert is_quarantined("42119587", threshold=3, table=mock_table) is False


def test_is_quarantined_false_when_below_threshold(mock_table):
    mock_table.get_item.return_value = {
        "Item": {"consecutive_failures": 2}
    }
    assert is_quarantined("42119587", threshold=3, table=mock_table) is False


def test_is_quarantined_true_at_threshold(mock_table):
    mock_table.get_item.return_value = {
        "Item": {"consecutive_failures": 3}
    }
    assert is_quarantined("42119587", threshold=3, table=mock_table) is True


def test_is_quarantined_true_above_threshold(mock_table):
    mock_table.get_item.return_value = {
        "Item": {"consecutive_failures": 7}
    }
    assert is_quarantined("42119587", threshold=3, table=mock_table) is True


def test_is_quarantined_coerces_decimal_count(mock_table):
    """DDB returns numbers as Decimal — must coerce."""
    from decimal import Decimal
    mock_table.get_item.return_value = {
        "Item": {"consecutive_failures": Decimal("3")}
    }
    assert is_quarantined("42119587", threshold=3, table=mock_table) is True


def test_is_quarantined_uses_correct_key(mock_table):
    mock_table.get_item.return_value = {}
    is_quarantined("42119587", threshold=3, table=mock_table)
    mock_table.get_item.assert_called_once_with(
        Key={"PK": f"{PK_PREFIX}42119587", "SK": SK}
    )


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------

def test_clear_calls_delete_item(mock_table):
    clear("42119587", table=mock_table)
    mock_table.delete_item.assert_called_once_with(
        Key={"PK": f"{PK_PREFIX}42119587", "SK": SK}
    )


def test_clear_is_idempotent_on_absent_row(mock_table):
    """DeleteItem on a missing key is a no-op in DDB — the module relies on
    that, never checks existence first."""
    # No mock side_effect → MagicMock returns a MagicMock for delete_item.
    # The function should not raise.
    clear("42119587", table=mock_table)
    mock_table.delete_item.assert_called_once()


# ---------------------------------------------------------------------------
# list_quarantined
# ---------------------------------------------------------------------------

def test_list_quarantined_returns_empty_when_no_rows(mock_table):
    mock_table.scan.return_value = {"Items": []}
    assert list_quarantined(table=mock_table) == []


def test_list_quarantined_returns_normalized_rows_sorted_by_pmid(mock_table):
    mock_table.scan.return_value = {
        "Items": [
            {
                "PK": f"{PK_PREFIX}42119587",
                "SK": SK,
                "pmid": "42119587",
                "consecutive_failures": 3,
                "last_failure_at": "2026-05-21T11:00:00Z",
                "last_failure_reason": "bedrock truncation",
                "last_attempted_run_id": "run-3",
                "created_at": "2026-05-19T11:00:00Z",
            },
            {
                "PK": f"{PK_PREFIX}40000000",
                "SK": SK,
                "pmid": "40000000",
                "consecutive_failures": 5,
                "last_failure_at": "2026-05-20T11:00:00Z",
                "last_failure_reason": "json parse",
                "last_attempted_run_id": "run-5",
                "created_at": "2026-05-15T11:00:00Z",
            },
        ]
    }
    rows = list_quarantined(table=mock_table)
    # Sorted by pmid ascending.
    assert [r["pmid"] for r in rows] == ["40000000", "42119587"]
    assert rows[0]["consecutive_failures"] == 5
    assert rows[1]["consecutive_failures"] == 3
    assert rows[1]["last_failure_reason"] == "bedrock truncation"


def test_list_quarantined_applies_threshold_filter(mock_table):
    """With threshold filter, only rows AT OR ABOVE threshold are returned —
    used by --retry-quarantine to skip in-progress counts below threshold."""
    mock_table.scan.return_value = {
        "Items": [
            {"PK": f"{PK_PREFIX}A", "pmid": "A", "consecutive_failures": 2},
            {"PK": f"{PK_PREFIX}B", "pmid": "B", "consecutive_failures": 3},
            {"PK": f"{PK_PREFIX}C", "pmid": "C", "consecutive_failures": 7},
        ]
    }
    rows = list_quarantined(threshold=3, table=mock_table)
    assert [r["pmid"] for r in rows] == ["B", "C"]


def test_list_quarantined_paginates_on_last_evaluated_key(mock_table):
    """Scan paginates via LastEvaluatedKey → ExclusiveStartKey."""
    mock_table.scan.side_effect = [
        {
            "Items": [
                {"PK": f"{PK_PREFIX}A", "pmid": "A", "consecutive_failures": 3},
            ],
            "LastEvaluatedKey": {"PK": f"{PK_PREFIX}A", "SK": SK},
        },
        {
            "Items": [
                {"PK": f"{PK_PREFIX}B", "pmid": "B", "consecutive_failures": 4},
            ],
        },
    ]
    rows = list_quarantined(table=mock_table)
    assert [r["pmid"] for r in rows] == ["A", "B"]
    # Second call carried ExclusiveStartKey from first response.
    second_call = mock_table.scan.call_args_list[1]
    assert second_call.kwargs.get("ExclusiveStartKey") == {
        "PK": f"{PK_PREFIX}A", "SK": SK,
    }


def test_list_quarantined_filter_expression_targets_pk_prefix(mock_table):
    mock_table.scan.return_value = {"Items": []}
    list_quarantined(table=mock_table)
    call = mock_table.scan.call_args
    assert call.kwargs["FilterExpression"] == "begins_with(PK, :p)"
    assert call.kwargs["ExpressionAttributeValues"] == {":p": PK_PREFIX}


# ---------------------------------------------------------------------------
# Default table injection
# ---------------------------------------------------------------------------

def test_record_failure_uses_default_table_when_none(monkeypatch):
    """When table is None (the production call site default), the module pulls
    from utils.dynamodb_helpers.get_table()."""
    default_table = MagicMock()
    default_table.update_item.return_value = {
        "Attributes": {"consecutive_failures": 1}
    }
    monkeypatch.setattr(quarantine, "get_table", lambda: default_table)
    record_failure("42119587", reason="r", run_id="rid")
    default_table.update_item.assert_called_once()


def test_is_quarantined_uses_default_table_when_none(monkeypatch):
    default_table = MagicMock()
    default_table.get_item.return_value = {}
    monkeypatch.setattr(quarantine, "get_table", lambda: default_table)
    is_quarantined("X", threshold=3)
    default_table.get_item.assert_called_once()


def test_clear_uses_default_table_when_none(monkeypatch):
    default_table = MagicMock()
    monkeypatch.setattr(quarantine, "get_table", lambda: default_table)
    clear("X")
    default_table.delete_item.assert_called_once()


def test_list_quarantined_uses_default_table_when_none(monkeypatch):
    default_table = MagicMock()
    default_table.scan.return_value = {"Items": []}
    monkeypatch.setattr(quarantine, "get_table", lambda: default_table)
    list_quarantined()
    default_table.scan.assert_called_once()

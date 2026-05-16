"""Tests for the retry-sweep DynamoDB helpers.

Covers the four helpers added for the hot-path state-based retry sweep:

- ``mark_processing_failed`` — UpdateItem that *increments* retry_count
  (so the counter survives across re-score attempts) and stamps failed_at.
- ``query_failed_pmids`` — GSI query for every status='failed' PROCESSING#
  row under a taxonomy version, with pagination.
- ``get_processing_rows`` — BatchGet of full PROCESSING# rows.
- ``quarantine_pmid`` — writes a QUARANTINE# row and flips the PROCESSING#
  row's status to 'quarantined'.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from utils.dynamodb_helpers import (
    get_processing_rows,
    mark_processing_failed,
    quarantine_pmid,
    query_failed_pmids,
)


# ---------------------------------------------------------------------------
# mark_processing_failed
# ---------------------------------------------------------------------------


def test_mark_processing_failed_increments_retry_count():
    """The failure path must ADD to retry_count, not reset it — otherwise
    the sweep could never reach the quarantine threshold."""
    client = MagicMock()
    mark_processing_failed(
        client, "reciterai", "123",
        error="boom", taxonomy_version="taxonomy_v2",
    )
    kwargs = client.update_item.call_args.kwargs
    assert kwargs["Key"]["PK"] == {"S": "PROCESSING#pmid_123"}
    assert kwargs["Key"]["SK"] == {"S": "STATUS"}
    assert "ADD retry_count :one" in kwargs["UpdateExpression"]
    vals = kwargs["ExpressionAttributeValues"]
    assert vals[":one"] == {"N": "1"}
    assert vals[":failed"] == {"S": "failed"}
    assert vals[":tv"] == {"S": "taxonomy_v2"}
    assert vals[":err"]["S"] == "boom"
    # failed_at is stamped (ISO8601, Z-suffixed) for the sweep age filter.
    assert vals[":ts"]["S"].endswith("Z")


def test_mark_processing_failed_truncates_long_error():
    client = MagicMock()
    mark_processing_failed(
        client, "reciterai", "1",
        error="x" * 5000, taxonomy_version="taxonomy_v2",
    )
    vals = client.update_item.call_args.kwargs["ExpressionAttributeValues"]
    assert len(vals[":err"]["S"]) == 1000


# ---------------------------------------------------------------------------
# query_failed_pmids
# ---------------------------------------------------------------------------


def test_query_failed_pmids_queries_gsi_and_paginates():
    client = MagicMock()
    client.query.side_effect = [
        {
            "Items": [{"PK": {"S": "PROCESSING#pmid_111"}}],
            "LastEvaluatedKey": {"PK": {"S": "PROCESSING#pmid_111"}},
        },
        {"Items": [{"PK": {"S": "PROCESSING#pmid_222"}}]},
    ]
    pmids = query_failed_pmids(client, "reciterai", "taxonomy_v2")
    assert pmids == ["111", "222"]

    first = client.query.call_args_list[0].kwargs
    assert first["IndexName"] == "ProcessingByVersionIndex"
    assert first["ExpressionAttributeValues"][":failed"] == {"S": "failed"}
    assert first["ExpressionAttributeValues"][":tv"] == {"S": "taxonomy_v2"}
    # Second query carried the pagination cursor.
    assert "ExclusiveStartKey" in client.query.call_args_list[1].kwargs


def test_query_failed_pmids_empty_result():
    client = MagicMock()
    client.query.return_value = {"Items": []}
    assert query_failed_pmids(client, "reciterai", "taxonomy_v2") == []


# ---------------------------------------------------------------------------
# get_processing_rows
# ---------------------------------------------------------------------------


def test_get_processing_rows_parses_typed_attributes():
    client = MagicMock()
    client.batch_get_item.return_value = {
        "Responses": {
            "reciterai": [
                {
                    "PK": {"S": "PROCESSING#pmid_5"},
                    "status": {"S": "failed"},
                    "retry_count": {"N": "2"},
                    "failed_at": {"S": "2026-05-01T00:00:00Z"},
                    "error": {"S": "x"},
                },
            ]
        },
    }
    rows = get_processing_rows(client, "reciterai", ["5"])
    assert rows == {
        "5": {
            "status": "failed",
            "retry_count": 2,
            "failed_at": "2026-05-01T00:00:00Z",
            "error": "x",
        }
    }


def test_get_processing_rows_defaults_missing_attrs():
    """A row with no retry_count / failed_at (legacy failure) parses to
    retry_count 0 and failed_at None."""
    client = MagicMock()
    client.batch_get_item.return_value = {
        "Responses": {
            "reciterai": [
                {"PK": {"S": "PROCESSING#pmid_9"}, "status": {"S": "failed"}},
            ]
        },
    }
    rows = get_processing_rows(client, "reciterai", ["9"])
    assert rows["9"]["retry_count"] == 0
    assert rows["9"]["failed_at"] is None


def test_get_processing_rows_empty_input_skips_db():
    client = MagicMock()
    assert get_processing_rows(client, "reciterai", []) == {}
    client.batch_get_item.assert_not_called()


# ---------------------------------------------------------------------------
# quarantine_pmid
# ---------------------------------------------------------------------------


def test_quarantine_pmid_writes_row_and_flips_processing_status():
    client = MagicMock()
    quarantine_pmid(
        client, "reciterai", "999",
        retry_count=3, last_error="content-filtered",
        taxonomy_version="taxonomy_v2",
    )
    # 1. QUARANTINE# row written for operator review.
    put = client.put_item.call_args.kwargs
    assert put["Item"]["PK"] == {"S": "QUARANTINE#pmid_999"}
    assert put["Item"]["SK"] == {"S": "STATUS"}
    assert put["Item"]["retry_count"] == {"N": "3"}
    assert put["Item"]["last_error"] == {"S": "content-filtered"}
    assert put["Item"]["quarantined_at"]["S"].endswith("Z")

    # 2. PROCESSING# row flipped to 'quarantined' so it leaves the
    #    status='failed' GSI partition and no future sweep sees it.
    upd = client.update_item.call_args.kwargs
    assert upd["Key"]["PK"] == {"S": "PROCESSING#pmid_999"}
    assert upd["ExpressionAttributeValues"][":q"] == {"S": "quarantined"}

"""Tests for the retry-sweep DynamoDB helpers.

Covers the helpers behind the hot-path state-based retry sweep (#84) and
the operator quarantine-release path (#86):

- ``mark_processing_failed`` — UpdateItem that *increments* retry_count
  (so the counter survives across re-score attempts) and stamps failed_at.
- ``query_failed_pmids`` — GSI query for every status='failed' PROCESSING#
  row under a taxonomy version, with pagination.
- ``get_processing_rows`` — BatchGet of full PROCESSING# rows.
- ``quarantine_pmid`` — writes a QUARANTINE# row and flips the PROCESSING#
  row's status to 'quarantined'.
- ``release_quarantine`` — the inverse of quarantine_pmid: deletes the
  QUARANTINE# row and the PROCESSING# checkpoint so a reviewed PMID is
  re-scored fresh.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from utils.dynamodb_helpers import (
    fetch_scored_provenance,
    get_processing_rows,
    invalidate_stale_score,
    mark_processing_failed,
    quarantine_pmid,
    query_failed_pmids,
    release_quarantine,
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
    # query_failed_pmids delegates to the generalized query_pmids_by_status,
    # which binds the status under :st (was :failed before #150 1b).
    assert first["ExpressionAttributeValues"][":st"] == {"S": "failed"}
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


# ---------------------------------------------------------------------------
# release_quarantine
# ---------------------------------------------------------------------------


def test_release_quarantine_deletes_quarantine_and_processing_rows():
    """A quarantined PMID: both the QUARANTINE# review row and the
    PROCESSING# checkpoint are deleted, and the cleared retry_count is
    reported back."""
    client = MagicMock()
    client.get_item.side_effect = [
        {"Item": {  # QUARANTINE# row
            "PK": {"S": "QUARANTINE#pmid_999"},
            "status": {"S": "quarantined"},
            "retry_count": {"N": "3"},
            "last_error": {"S": "content-filtered"},
        }},
        {"Item": {  # PROCESSING# row, stuck at 'quarantined'
            "PK": {"S": "PROCESSING#pmid_999"},
            "status": {"S": "quarantined"},
            "retry_count": {"N": "3"},
        }},
    ]
    result = release_quarantine(client, "reciterai", "999")

    assert result == {
        "pmid": "999",
        "was_quarantined": True,
        "retry_count": 3,
        "last_error": "content-filtered",
    }
    # Both rows deleted, on the right PK/SK.
    deleted = [c.kwargs["Key"]["PK"]["S"] for c in client.delete_item.call_args_list]
    assert deleted == ["QUARANTINE#pmid_999", "PROCESSING#pmid_999"]
    for c in client.delete_item.call_args_list:
        assert c.kwargs["Key"]["SK"] == {"S": "STATUS"}


def test_release_quarantine_recovers_stuck_processing_row():
    """The QUARANTINE# row was hand-deleted but the PROCESSING# row is
    stuck at status='quarantined'. Release must still clear it — otherwise
    the PMID can never re-enter processing."""
    client = MagicMock()
    client.get_item.side_effect = [
        {},  # no QUARANTINE# row
        {"Item": {
            "PK": {"S": "PROCESSING#pmid_777"},
            "status": {"S": "quarantined"},
            "retry_count": {"N": "5"},
            "error": {"S": "boom"},
        }},
    ]
    result = release_quarantine(client, "reciterai", "777")

    assert result["was_quarantined"] is True
    assert result["retry_count"] == 5
    assert result["last_error"] == "boom"
    deleted = [c.kwargs["Key"]["PK"]["S"] for c in client.delete_item.call_args_list]
    assert deleted == ["QUARANTINE#pmid_777", "PROCESSING#pmid_777"]


def test_release_quarantine_leaves_non_quarantined_pmid_untouched():
    """A PMID with no QUARANTINE# row and a non-quarantined PROCESSING#
    status (an operator typo) is reported, never mutated — releasing it
    would needlessly drop a healthy checkpoint."""
    client = MagicMock()
    client.get_item.side_effect = [
        {},  # no QUARANTINE# row
        {"Item": {"PK": {"S": "PROCESSING#pmid_5"}, "status": {"S": "complete"}}},
    ]
    result = release_quarantine(client, "reciterai", "5")

    assert result["was_quarantined"] is False
    client.delete_item.assert_not_called()


def test_release_quarantine_idempotent_when_no_state_exists():
    """Releasing a PMID with no quarantine state at all — already released,
    or never quarantined — deletes nothing and reports was_quarantined
    False."""
    client = MagicMock()
    client.get_item.side_effect = [{}, {}]  # neither row exists
    result = release_quarantine(client, "reciterai", "123")

    assert result == {
        "pmid": "123",
        "was_quarantined": False,
        "retry_count": 0,
        "last_error": "",
    }
    client.delete_item.assert_not_called()


# ---------------------------------------------------------------------------
# fetch_scored_provenance / invalidate_stale_score — #150 item 2 drift sweep
# ---------------------------------------------------------------------------


def test_fetch_scored_provenance_parses_stamps_and_defaults():
    client = MagicMock()
    client.batch_get_item.return_value = {
        "Responses": {"reciterai": [
            {"PK": {"S": "PROCESSING#pmid_5"},
             "scored_enriched_at": {"S": "2026-05-20T11:00:00Z"},
             "scored_synopsis_model": {"S": "claude-sonnet-4-6"}},
            {"PK": {"S": "PROCESSING#pmid_6"}},  # un-stamped → empty strings
        ]},
        "UnprocessedKeys": {},
    }
    out = fetch_scored_provenance(client, "reciterai", ["5", "6"])
    assert out["5"] == {"scored_enriched_at": "2026-05-20T11:00:00Z",
                        "scored_synopsis_model": "claude-sonnet-4-6"}
    assert out["6"] == {"scored_enriched_at": "", "scored_synopsis_model": ""}


def test_fetch_scored_provenance_empty_input_skips_db():
    client = MagicMock()
    assert fetch_scored_provenance(client, "reciterai", []) == {}
    client.batch_get_item.assert_not_called()


def test_invalidate_stale_score_flips_complete_to_stale():
    client = MagicMock()
    assert invalidate_stale_score(client, "reciterai", "999") is True
    kwargs = client.update_item.call_args.kwargs
    assert kwargs["Key"]["PK"] == {"S": "PROCESSING#pmid_999"}
    assert kwargs["ExpressionAttributeValues"][":stale"] == {"S": "stale"}
    assert kwargs["ExpressionAttributeValues"][":complete"] == {"S": "complete"}
    assert kwargs["ConditionExpression"] == "#s = :complete"


def test_invalidate_stale_score_noop_when_not_complete():
    """The complete-guard makes a concurrent status change a no-op (False),
    not an error."""
    from botocore.exceptions import ClientError
    client = MagicMock()
    client.update_item.side_effect = ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem",
    )
    assert invalidate_stale_score(client, "reciterai", "999") is False

"""Tests for ``scripts/backfill_legacy_synopsis_to_ddb.py`` (#138).

Unit-level coverage for the classify/apply seams. The DDB and MariaDB
calls are stubbed via MagicMock — behavioral validation against real
infrastructure happens in the local ``--dry-run`` smoke before the apply run.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

# Load the script as a module without invoking the CLI.
_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts" / "backfill_legacy_synopsis_to_ddb.py"
)
_spec = importlib.util.spec_from_file_location("backfill_legacy_synopsis", _SCRIPT)
backfill = importlib.util.module_from_spec(_spec)
sys.modules["backfill_legacy_synopsis"] = backfill
_spec.loader.exec_module(backfill)


# ---------------------------------------------------------------------------
# classify_pmids
# ---------------------------------------------------------------------------

def _make_batch_get_response(items: list[dict]) -> dict:
    return {
        "Responses": {backfill.TABLE_NAME: items},
        "UnprocessedKeys": {},
    }


def test_classify_partitions_skip_update_create():
    """One PMID from each branch in a single BatchGetItem response."""
    pmids = ["100", "200", "300"]
    client = MagicMock()
    client.batch_get_item.return_value = _make_batch_get_response([
        {  # 100 — row exists, has synopsis → skip
            "PK": {"S": f"{backfill.IMPACT_PK_PREFIX}100"},
            "synopsis": {"S": "an existing synopsis"},
        },
        {  # 200 — row exists, no synopsis → update
            "PK": {"S": f"{backfill.IMPACT_PK_PREFIX}200"},
        },
        # 300 — absent from Responses → create
    ])

    skip, update, create = backfill.classify_pmids(pmids, dynamo_client=client)

    assert skip == {"100"}
    assert update == {"200"}
    assert create == {"300"}


def test_classify_treats_empty_synopsis_as_update_not_skip():
    """A row with ``synopsis = ''`` is incomplete — write the real text."""
    client = MagicMock()
    client.batch_get_item.return_value = _make_batch_get_response([
        {
            "PK": {"S": f"{backfill.IMPACT_PK_PREFIX}42"},
            "synopsis": {"S": ""},
        },
    ])

    skip, update, create = backfill.classify_pmids(["42"], dynamo_client=client)

    assert skip == set()
    assert update == {"42"}
    assert create == set()


def test_classify_chunks_into_groups_of_100():
    """DDB BatchGetItem caps at 100 keys per request — must paginate."""
    pmids = [str(i) for i in range(250)]
    client = MagicMock()
    client.batch_get_item.return_value = _make_batch_get_response([])

    backfill.classify_pmids(pmids, dynamo_client=client)

    assert client.batch_get_item.call_count == 3
    sent = [
        len(call.kwargs["RequestItems"][backfill.TABLE_NAME]["Keys"])
        for call in client.batch_get_item.call_args_list
    ]
    assert sent == [100, 100, 50]


def test_classify_retries_unprocessed_keys():
    """``UnprocessedKeys`` round-trips back into the same loop iteration."""
    client = MagicMock()
    pk = f"{backfill.IMPACT_PK_PREFIX}77"
    sk = backfill.IMPACT_SK
    client.batch_get_item.side_effect = [
        # First call: nothing returned, one key unprocessed.
        {
            "Responses": {backfill.TABLE_NAME: []},
            "UnprocessedKeys": {
                backfill.TABLE_NAME: {
                    "Keys": [{"PK": {"S": pk}, "SK": {"S": sk}}],
                    "ProjectionExpression": "PK, synopsis",
                },
            },
        },
        # Retry: the row exists with no synopsis → update.
        _make_batch_get_response([{"PK": {"S": pk}}]),
    ]

    skip, update, create = backfill.classify_pmids(["77"], dynamo_client=client)

    assert client.batch_get_item.call_count == 2
    assert update == {"77"}
    assert skip == set() and create == set()


# ---------------------------------------------------------------------------
# apply_updates
# ---------------------------------------------------------------------------

def _conditional_failed_error() -> ClientError:
    return ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "..."}},
        "UpdateItem",
    )


def test_apply_updates_writes_three_attributes_and_uses_condition():
    table = MagicMock()
    backfill.apply_updates(
        {"123": "synopsis text"},
        enriched_at="2026-05-20T00:00:00Z",
        table=table,
    )

    call = table.update_item.call_args
    assert call.kwargs["Key"] == {
        "PK": f"{backfill.IMPACT_PK_PREFIX}123", "SK": backfill.IMPACT_SK,
    }
    assert call.kwargs["ConditionExpression"] == "attribute_not_exists(synopsis)"
    assert (
        call.kwargs["ExpressionAttributeValues"][":m"]
        == backfill.LEGACY_SYNOPSIS_MODEL
    )
    assert call.kwargs["ExpressionAttributeValues"][":s"] == "synopsis text"
    assert (
        call.kwargs["ExpressionAttributeValues"][":t"] == "2026-05-20T00:00:00Z"
    )
    assert "synopsis" in call.kwargs["UpdateExpression"]
    assert "synopsis_model" in call.kwargs["UpdateExpression"]
    assert "enriched_at" in call.kwargs["UpdateExpression"]


def test_apply_updates_counts_conditional_failure_separately():
    table = MagicMock()
    table.update_item.side_effect = [
        None,
        _conditional_failed_error(),
        None,
    ]
    written, skipped = backfill.apply_updates(
        {"a": "1", "b": "2", "c": "3"},
        enriched_at="2026-05-20T00:00:00Z",
        table=table,
    )
    assert written == 2
    assert skipped == 1


def test_apply_updates_propagates_non_conditional_client_errors():
    table = MagicMock()
    table.update_item.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "nope"}},
        "UpdateItem",
    )
    with pytest.raises(ClientError):
        backfill.apply_updates(
            {"x": "y"},
            enriched_at="2026-05-20T00:00:00Z",
            table=table,
        )


# ---------------------------------------------------------------------------
# apply_puts
# ---------------------------------------------------------------------------

def test_apply_puts_writes_synopsis_only_item_with_pk_guard():
    table = MagicMock()
    backfill.apply_puts(
        {"999": "fresh synopsis"},
        enriched_at="2026-05-20T00:00:00Z",
        table=table,
    )
    call = table.put_item.call_args
    item = call.kwargs["Item"]
    assert item["PK"] == f"{backfill.IMPACT_PK_PREFIX}999"
    assert item["SK"] == backfill.IMPACT_SK
    assert item["pmid"] == "999"
    assert item["synopsis"] == "fresh synopsis"
    assert item["synopsis_model"] == backfill.LEGACY_SYNOPSIS_MODEL
    assert item["enriched_at"] == "2026-05-20T00:00:00Z"
    # No impact_score / justification — out of scope for #138.
    assert "impact_score" not in item
    assert "justification" not in item

    assert call.kwargs["ConditionExpression"] == "attribute_not_exists(PK)"


def test_apply_puts_counts_conditional_failure_separately():
    table = MagicMock()
    table.put_item.side_effect = [
        None,
        ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "..."}},
            "PutItem",
        ),
    ]
    written, skipped = backfill.apply_puts(
        {"a": "1", "b": "2"},
        enriched_at="2026-05-20T00:00:00Z",
        table=table,
    )
    assert written == 1
    assert skipped == 1


# ---------------------------------------------------------------------------
# load_mariadb_synopses limit / shape
# ---------------------------------------------------------------------------

def test_load_mariadb_synopses_respects_limit(monkeypatch):
    fake_conn = MagicMock()
    fake_conn.execute.return_value.fetchall.return_value = [
        ("100", "a"), ("200", "b"), ("300", "c"),
    ]
    monkeypatch.setattr(backfill, "get_db_connection", lambda: fake_conn)

    out = backfill.load_mariadb_synopses(limit=2)

    assert len(out) == 2
    assert list(out.keys()) == ["100", "200"]
    fake_conn.close.assert_called_once()


def test_load_mariadb_synopses_stringifies_pmids(monkeypatch):
    fake_conn = MagicMock()
    fake_conn.execute.return_value.fetchall.return_value = [
        (12345, "synopsis A"),
    ]
    monkeypatch.setattr(backfill, "get_db_connection", lambda: fake_conn)

    out = backfill.load_mariadb_synopses()

    assert out == {"12345": "synopsis A"}

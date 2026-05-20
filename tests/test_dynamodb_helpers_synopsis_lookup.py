"""Tests for ``fetch_synopses_for_pmids`` + ``scan_all_synopses`` (#38).

The DDB-side replacement for the old MariaDB synopsis joins. Unit-level
coverage with MagicMock'd boto3 client — behavioural coverage against real
DDB is covered by the cold-run smoke tests downstream.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from utils.dynamodb_helpers import (
    IMPACT_PK_PREFIX,
    IMPACT_SK,
    TABLE_NAME,
    fetch_synopses_for_pmids,
    scan_all_synopses,
)


# ---------------------------------------------------------------------------
# fetch_synopses_for_pmids
# ---------------------------------------------------------------------------

def _make_batch_get(items: list[dict]) -> dict:
    return {"Responses": {TABLE_NAME: items}, "UnprocessedKeys": {}}


def test_fetch_returns_only_pmids_with_non_empty_synopsis():
    """Mirrors the legacy `INNER JOIN reciterai_synopsis` filter — a row
    with no `synopsis` attribute or an empty string is dropped."""
    client = MagicMock()
    client.batch_get_item.return_value = _make_batch_get([
        {  # has synopsis → included
            "PK": {"S": f"{IMPACT_PK_PREFIX}100"},
            "synopsis": {"S": "synopsis text"},
        },
        {  # row exists but synopsis is empty → dropped
            "PK": {"S": f"{IMPACT_PK_PREFIX}200"},
            "synopsis": {"S": ""},
        },
        {  # row exists with no synopsis attr → dropped
            "PK": {"S": f"{IMPACT_PK_PREFIX}300"},
        },
        # 400 absent from Responses → dropped
    ])
    out = fetch_synopses_for_pmids(client, ["100", "200", "300", "400"])
    assert out == {"100": "synopsis text"}


def test_fetch_empty_list_returns_empty_without_ddb_call():
    client = MagicMock()
    assert fetch_synopses_for_pmids(client, []) == {}
    client.batch_get_item.assert_not_called()


def test_fetch_chunks_into_groups_of_100():
    """DDB BatchGetItem max is 100 keys per request — must paginate."""
    pmids = [str(i) for i in range(250)]
    client = MagicMock()
    client.batch_get_item.return_value = _make_batch_get([])

    fetch_synopses_for_pmids(client, pmids)

    assert client.batch_get_item.call_count == 3
    chunk_sizes = [
        len(call.kwargs["RequestItems"][TABLE_NAME]["Keys"])
        for call in client.batch_get_item.call_args_list
    ]
    assert chunk_sizes == [100, 100, 50]


def test_fetch_retries_unprocessed_keys_in_same_request():
    client = MagicMock()
    pk = f"{IMPACT_PK_PREFIX}77"
    client.batch_get_item.side_effect = [
        # First call: one key unprocessed.
        {
            "Responses": {TABLE_NAME: []},
            "UnprocessedKeys": {
                TABLE_NAME: {
                    "Keys": [{"PK": {"S": pk}, "SK": {"S": IMPACT_SK}}],
                    "ProjectionExpression": "PK, synopsis",
                },
            },
        },
        # Retry resolves to a row with synopsis.
        _make_batch_get([{"PK": {"S": pk}, "synopsis": {"S": "syn"}}]),
    ]
    out = fetch_synopses_for_pmids(client, ["77"])
    assert client.batch_get_item.call_count == 2
    assert out == {"77": "syn"}


def test_fetch_dedupes_and_strips_input():
    """Duplicates and whitespace-only PMIDs are normalised away before the call."""
    client = MagicMock()
    client.batch_get_item.return_value = _make_batch_get([])
    fetch_synopses_for_pmids(client, ["1", "1", "  ", "", "2"])
    keys_sent = client.batch_get_item.call_args.kwargs["RequestItems"][TABLE_NAME]["Keys"]
    pmids_sent = sorted(k["PK"]["S"] for k in keys_sent)
    assert pmids_sent == [f"{IMPACT_PK_PREFIX}1", f"{IMPACT_PK_PREFIX}2"]


def test_fetch_uses_projection_expression_to_minimise_payload():
    client = MagicMock()
    client.batch_get_item.return_value = _make_batch_get([])
    fetch_synopses_for_pmids(client, ["1"])
    projection = (
        client.batch_get_item.call_args.kwargs["RequestItems"][TABLE_NAME]
        ["ProjectionExpression"]
    )
    assert projection == "PK, synopsis"


# ---------------------------------------------------------------------------
# scan_all_synopses
# ---------------------------------------------------------------------------

def test_scan_paginates_on_last_evaluated_key():
    client = MagicMock()
    client.scan.side_effect = [
        {
            "Items": [
                {"PK": {"S": f"{IMPACT_PK_PREFIX}1"},
                 "synopsis": {"S": "s1"}},
            ],
            "LastEvaluatedKey": {"PK": {"S": f"{IMPACT_PK_PREFIX}1"}},
        },
        {
            "Items": [
                {"PK": {"S": f"{IMPACT_PK_PREFIX}2"},
                 "synopsis": {"S": "s2"}},
            ],
        },
    ]
    out = scan_all_synopses(client)
    assert out == {"1": "s1", "2": "s2"}
    assert client.scan.call_count == 2
    # Second call must carry the ExclusiveStartKey forward.
    assert "ExclusiveStartKey" in client.scan.call_args_list[1].kwargs


def test_scan_filters_to_impact_rows_with_synopsis():
    client = MagicMock()
    client.scan.return_value = {"Items": []}
    scan_all_synopses(client)
    kwargs = client.scan.call_args.kwargs
    assert kwargs["FilterExpression"] == (
        "begins_with(PK, :p) AND attribute_exists(synopsis)"
    )
    assert kwargs["ExpressionAttributeValues"][":p"]["S"] == "IMPACT#"
    assert kwargs["ProjectionExpression"] == "PK, synopsis"


def test_scan_drops_empty_synopses_defensively():
    """A row that somehow has `synopsis = ''` (e.g. a partial write race)
    is filtered out at the application layer too."""
    client = MagicMock()
    client.scan.return_value = {
        "Items": [
            {"PK": {"S": f"{IMPACT_PK_PREFIX}1"}, "synopsis": {"S": ""}},
            {"PK": {"S": f"{IMPACT_PK_PREFIX}2"}, "synopsis": {"S": "real"}},
        ],
    }
    out = scan_all_synopses(client)
    assert out == {"2": "real"}

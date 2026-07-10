"""Tests for DDB-side IMPACT# row reads (#38, #141).

``fetch_synopses_for_pmids`` + ``scan_all_synopses`` (#38) — the DDB-side
replacement for the old MariaDB synopsis joins.
``check_enrichment_coverage`` (#141) — the DDB-side replacement for the
MariaDB synopsis+impact idempotency cull. Unit-level coverage with
MagicMock'd boto3 client — behavioural coverage against real DDB is
covered by the cold-run smoke tests downstream.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from utils.dynamodb_helpers import (
    IMPACT_PK_PREFIX,
    IMPACT_SK,
    TABLE_NAME,
    check_enrichment_coverage,
    fetch_synopses_for_pmids,
    fetch_synopsis_records,
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
# fetch_synopsis_records — #150 item 2 (synopsis + provenance)
# ---------------------------------------------------------------------------


def test_fetch_records_returns_synopsis_and_provenance():
    client = MagicMock()
    client.batch_get_item.return_value = _make_batch_get([
        {
            "PK": {"S": f"{IMPACT_PK_PREFIX}100"},
            "synopsis": {"S": "syn text"},
            "synopsis_model": {"S": "claude-sonnet-4-6"},
            "enriched_at": {"S": "2026-05-20T11:00:00Z"},
        },
        {  # empty synopsis → dropped (same inner-join filter as fetch_synopses)
            "PK": {"S": f"{IMPACT_PK_PREFIX}200"},
            "synopsis": {"S": ""},
            "synopsis_model": {"S": "m"},
        },
    ])
    out = fetch_synopsis_records(client, ["100", "200"])
    assert out == {
        "100": {
            "synopsis": "syn text",
            "synopsis_model": "claude-sonnet-4-6",
            "enriched_at": "2026-05-20T11:00:00Z",
            # #212 Part A: the IMPACT# row carried no enriched impact yet.
            "impact_score": None,
            "impact_justification": "",
        },
    }


def test_fetch_records_missing_provenance_defaults_to_empty_strings():
    """A synopsis row with no model/enriched_at (legacy enrichment) yields
    empty-string provenance, not a KeyError — the scorer then writes no stamp."""
    client = MagicMock()
    client.batch_get_item.return_value = _make_batch_get([
        {"PK": {"S": f"{IMPACT_PK_PREFIX}300"}, "synopsis": {"S": "s"}},
    ])
    out = fetch_synopsis_records(client, ["300"])
    assert out == {
        "300": {
            "synopsis": "s",
            "synopsis_model": "",
            "enriched_at": "",
            "impact_score": None,
            "impact_justification": "",
        }
    }


def test_fetch_records_empty_list_skips_ddb():
    client = MagicMock()
    assert fetch_synopsis_records(client, []) == {}
    client.batch_get_item.assert_not_called()


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


# ---------------------------------------------------------------------------
# check_enrichment_coverage (#141) — synopsis ∩ impact_score partition
# ---------------------------------------------------------------------------

def test_coverage_empty_input_skips_ddb():
    client = MagicMock()
    result = check_enrichment_coverage(client, [])
    assert result == {"complete": [], "incomplete": []}
    client.batch_get_item.assert_not_called()


def test_coverage_complete_requires_both_synopsis_and_impact_score():
    """100 has both → complete; 200 synopsis-only → incomplete;
    300 impact-only → incomplete; 400 row missing entirely → incomplete."""
    client = MagicMock()
    client.batch_get_item.return_value = _make_batch_get([
        {
            "PK": {"S": f"{IMPACT_PK_PREFIX}100"},
            "synopsis": {"S": "syn"},
            "impact_score": {"N": "75"},
        },
        {
            "PK": {"S": f"{IMPACT_PK_PREFIX}200"},
            "synopsis": {"S": "syn"},
            # no impact_score
        },
        {
            "PK": {"S": f"{IMPACT_PK_PREFIX}300"},
            "impact_score": {"N": "60"},
            # no synopsis
        },
        # 400 absent
    ])
    result = check_enrichment_coverage(client, ["100", "200", "300", "400"])
    assert result["complete"] == ["100"]
    assert result["incomplete"] == ["200", "300", "400"]


def test_coverage_treats_empty_synopsis_as_incomplete():
    """A row whose synopsis attribute is the empty string is incomplete
    (mirrors the legacy MariaDB ``synopsis != ''`` filter)."""
    client = MagicMock()
    client.batch_get_item.return_value = _make_batch_get([
        {
            "PK": {"S": f"{IMPACT_PK_PREFIX}1"},
            "synopsis": {"S": ""},
            "impact_score": {"N": "10"},
        },
    ])
    result = check_enrichment_coverage(client, ["1"])
    assert result == {"complete": [], "incomplete": ["1"]}


def test_coverage_dedupes_and_stringifies_input():
    client = MagicMock()
    client.batch_get_item.return_value = _make_batch_get([
        {
            "PK": {"S": f"{IMPACT_PK_PREFIX}100"},
            "synopsis": {"S": "syn"},
            "impact_score": {"N": "50"},
        },
    ])
    result = check_enrichment_coverage(client, [100, "100", 100, "  ", ""])
    assert result == {"complete": ["100"], "incomplete": []}
    keys_sent = client.batch_get_item.call_args.kwargs["RequestItems"][TABLE_NAME]["Keys"]
    assert keys_sent == [{"PK": {"S": f"{IMPACT_PK_PREFIX}100"}, "SK": {"S": IMPACT_SK}}]


def test_coverage_all_incomplete_when_nothing_present():
    client = MagicMock()
    client.batch_get_item.return_value = _make_batch_get([])
    result = check_enrichment_coverage(client, ["1", "2"])
    assert result == {"complete": [], "incomplete": ["1", "2"]}


def test_coverage_chunks_into_groups_of_100():
    """DDB BatchGetItem max is 100 keys per request — must paginate."""
    pmids = [str(i) for i in range(250)]
    client = MagicMock()
    client.batch_get_item.return_value = _make_batch_get([])

    check_enrichment_coverage(client, pmids)

    assert client.batch_get_item.call_count == 3
    chunk_sizes = [
        len(call.kwargs["RequestItems"][TABLE_NAME]["Keys"])
        for call in client.batch_get_item.call_args_list
    ]
    assert chunk_sizes == [100, 100, 50]


def test_coverage_retries_unprocessed_keys_in_same_request():
    client = MagicMock()
    pk = f"{IMPACT_PK_PREFIX}77"
    client.batch_get_item.side_effect = [
        {
            "Responses": {TABLE_NAME: []},
            "UnprocessedKeys": {
                TABLE_NAME: {
                    "Keys": [{"PK": {"S": pk}, "SK": {"S": IMPACT_SK}}],
                    "ProjectionExpression": "PK, synopsis, impact_score",
                },
            },
        },
        _make_batch_get([
            {"PK": {"S": pk}, "synopsis": {"S": "syn"},
             "impact_score": {"N": "70"}},
        ]),
    ]
    result = check_enrichment_coverage(client, ["77"])
    assert client.batch_get_item.call_count == 2
    assert result == {"complete": ["77"], "incomplete": []}


def test_coverage_uses_projection_with_both_attributes():
    client = MagicMock()
    client.batch_get_item.return_value = _make_batch_get([])
    check_enrichment_coverage(client, ["1"])
    projection = (
        client.batch_get_item.call_args.kwargs["RequestItems"][TABLE_NAME]
        ["ProjectionExpression"]
    )
    assert projection == "PK, synopsis, impact_score"

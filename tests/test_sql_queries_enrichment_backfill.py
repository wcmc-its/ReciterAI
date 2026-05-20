"""Tests for the enrichment-backfill SQL layer (#112).

`PUBLICATIONS_BY_PMIDS_FOR_ENRICHMENT_SQL` is the explicit-PMID variant of
the daily delta query. The synopsis+impact idempotency cull
(`check_enrichment_coverage`) moved to DynamoDB in #141 — see
`tests/test_dynamodb_helpers_synopsis_lookup.py`. SQL-shape + helper-contract
tests; behavioral correctness against live MariaDB is covered by the
enrichment integration tests.
"""
from __future__ import annotations

import re
from unittest.mock import MagicMock

from utils.sql_queries import (
    PUBLICATIONS_BY_PMIDS_FOR_ENRICHMENT_SQL,
    fetch_publications_for_enrichment,
)


# ---------------------------------------------------------------------------
# PUBLICATIONS_BY_PMIDS_FOR_ENRICHMENT_SQL — column list + filter clauses
# ---------------------------------------------------------------------------

# The backfill reuses _process_one_pmid, so the row must carry every column
# the synopsis AND impact prompts read — identical to the daily delta query.
REQUIRED_COLUMNS = [
    "pmid",
    "articleTitle",
    "journalTitleVerbose",
    "articleYear",
    "datePublicationAddedToEntrez",
    "citationCountNIH",
    "percentileNIH",
    "relativeCitationRatioNIH",
    "abstractVarchar",
]


def test_sql_selects_all_columns_the_prompts_consume():
    for col in REQUIRED_COLUMNS:
        assert col in PUBLICATIONS_BY_PMIDS_FOR_ENRICHMENT_SQL, (
            f"missing column {col!r} — impact/synopsis prompts will fail"
        )


def test_sql_scopes_to_an_explicit_pmid_list_not_a_watermark():
    """The whole point of the backfill: an explicit IN-list, NOT the
    watermark window the daily delta uses (the watermark cannot reach
    publications below itself)."""
    assert "a.pmid IN :pmid_list" in PUBLICATIONS_BY_PMIDS_FOR_ENRICHMENT_SQL
    assert "last_max_pmid" not in PUBLICATIONS_BY_PMIDS_FOR_ENRICHMENT_SQL


def test_sql_keeps_the_corpus_filter():
    """Academic-Article + articleYear >= 2020 stay as the D4 invariant."""
    assert (
        "publicationTypeCanonical = 'Academic Article'"
        in PUBLICATIONS_BY_PMIDS_FOR_ENRICHMENT_SQL
    )
    assert "articleYear >= 2020" in PUBLICATIONS_BY_PMIDS_FOR_ENRICHMENT_SQL


def test_sql_drops_the_faculty_exists_filter():
    """The work set is already faculty-scoped (it comes from the gap scan
    or an operator list), so the query trusts its input."""
    assert "fullTimeFaculty" not in PUBLICATIONS_BY_PMIDS_FOR_ENRICHMENT_SQL
    assert "EXISTS" not in PUBLICATIONS_BY_PMIDS_FOR_ENRICHMENT_SQL


def test_sql_uses_left_join_for_abstract():
    """A pub may have no reporting_abstracts row — LEFT JOIN keeps it."""
    assert re.search(
        r"LEFT JOIN reporting_abstracts r ON r\.pmid = a\.pmid",
        PUBLICATIONS_BY_PMIDS_FOR_ENRICHMENT_SQL,
    )


# ---------------------------------------------------------------------------
# fetch_publications_for_enrichment helper
# ---------------------------------------------------------------------------

def _make_engine_returning(rows):
    """Engine mock whose connection.execute().mappings().all() returns rows."""
    conn = MagicMock()
    result = MagicMock()
    result.mappings.return_value.all.return_value = rows
    conn.execute.return_value = result
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    engine.connect.return_value.__exit__.return_value = False
    return engine, conn


def test_fetch_empty_pmid_list_short_circuits_without_a_db_call():
    engine = MagicMock()
    assert fetch_publications_for_enrichment(engine, []) == []
    engine.connect.assert_not_called()


def test_fetch_passes_stringified_pmids_as_the_bind_parameter():
    engine, conn = _make_engine_returning([])
    fetch_publications_for_enrichment(engine, [123, "456"])
    params = conn.execute.call_args.args[1]
    assert params == {"pmid_list": ["123", "456"]}


def test_fetch_returns_plain_dicts():
    fake_row = {"pmid": 40927852, "articleTitle": "t", "articleYear": 2025}
    engine, _ = _make_engine_returning([fake_row])
    out = fetch_publications_for_enrichment(engine, ["40927852"])
    assert out == [fake_row]
    assert isinstance(out[0], dict)

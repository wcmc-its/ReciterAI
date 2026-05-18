"""Tests for the enrichment-backfill SQL layer (#112).

`PUBLICATIONS_BY_PMIDS_FOR_ENRICHMENT_SQL` is the explicit-PMID variant of
the daily delta query; `check_enrichment_coverage` is the synopsis+impact
idempotency cull. SQL-shape + helper-contract tests — behavioral correctness
against live MariaDB is covered by the enrichment integration tests.
"""
from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

from utils.sql_queries import (
    IMPACT_COVERAGE_SQL,
    PUBLICATIONS_BY_PMIDS_FOR_ENRICHMENT_SQL,
    check_enrichment_coverage,
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


def test_impact_coverage_sql_requires_a_non_null_score():
    assert "reciterai_impact" in IMPACT_COVERAGE_SQL
    assert "entity_type = 'publication'" in IMPACT_COVERAGE_SQL
    assert "impactScore IS NOT NULL" in IMPACT_COVERAGE_SQL
    assert "external_id IN :pmid_list" in IMPACT_COVERAGE_SQL


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


# ---------------------------------------------------------------------------
# check_enrichment_coverage — synopsis ∩ impact partition
# ---------------------------------------------------------------------------

def _patch_coverage(synopsis_present, impact_present):
    """Patch get_db_connection so the two execute() calls return, in order,
    the synopsis-present then impact-present external_id rows."""
    conn = MagicMock()
    conn.execute.side_effect = [
        [(p,) for p in synopsis_present],
        [(p,) for p in impact_present],
    ]
    return patch("utils.sql_queries.get_db_connection", return_value=conn), conn


def test_check_enrichment_coverage_empty_input_skips_the_db():
    with patch("utils.sql_queries.get_db_connection") as gdc:
        result = check_enrichment_coverage([])
    assert result == {"complete": [], "incomplete": []}
    gdc.assert_not_called()


def test_check_enrichment_coverage_complete_requires_synopsis_and_impact():
    # 100 has both; 200 synopsis-only; 300 impact-only; 400 neither.
    cm, conn = _patch_coverage(
        synopsis_present=["100", "200"], impact_present=["100", "300"]
    )
    with cm:
        result = check_enrichment_coverage(["100", "200", "300", "400"])
    assert result["complete"] == ["100"]
    assert result["incomplete"] == ["200", "300", "400"]
    conn.close.assert_called_once()


def test_check_enrichment_coverage_dedupes_and_stringifies_input():
    cm, _ = _patch_coverage(synopsis_present=["100"], impact_present=["100"])
    with cm:
        result = check_enrichment_coverage([100, "100", 100])
    assert result == {"complete": ["100"], "incomplete": []}


def test_check_enrichment_coverage_all_incomplete_when_nothing_covered():
    cm, _ = _patch_coverage(synopsis_present=[], impact_present=[])
    with cm:
        result = check_enrichment_coverage(["1", "2"])
    assert result == {"complete": [], "incomplete": ["1", "2"]}

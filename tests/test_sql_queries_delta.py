"""Tests for the daily-enrichment delta query (#37 step 2).

The SQL contract is a load-bearing piece — downstream prompts read keys
out of these rows, so column-name parity matters. These tests verify the
shape of the SQL constant and the helper. Behavioral correctness against
live MariaDB is covered by a follow-up integration test (#43).
"""
from __future__ import annotations

import re
from unittest.mock import MagicMock

from utils.sql_queries import (
    NEW_PUBLICATIONS_FOR_ENRICHMENT_SQL,
    fetch_new_publications,
)


# ---------------------------------------------------------------------------
# SQL constant — column list + filter clauses
# ---------------------------------------------------------------------------

# The impact prompt's input contract (per pipeline_enrichment/impact.py:9-13).
# The synopsis prompt is a subset (title + journal + year + abstract).
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
        assert col in NEW_PUBLICATIONS_FOR_ENRICHMENT_SQL, (
            f"missing column {col!r} — impact/synopsis prompts will fail"
        )


def test_sql_applies_corpus_filter():
    """publicationTypeCanonical and articleYear filter must be present."""
    assert "publicationTypeCanonical = 'Academic Article'" in NEW_PUBLICATIONS_FOR_ENRICHMENT_SQL
    assert "articleYear >= 2020" in NEW_PUBLICATIONS_FOR_ENRICHMENT_SQL


def test_sql_applies_watermark_filter():
    assert "pmid > :last_max_pmid" in NEW_PUBLICATIONS_FOR_ENRICHMENT_SQL


def test_sql_applies_wcm_faculty_filter():
    """EXISTS subquery against identity.fullTimeFaculty = 'yes'."""
    assert "EXISTS" in NEW_PUBLICATIONS_FOR_ENRICHMENT_SQL
    assert "analysis_summary_author" in NEW_PUBLICATIONS_FOR_ENRICHMENT_SQL
    assert "identity" in NEW_PUBLICATIONS_FOR_ENRICHMENT_SQL
    assert "fullTimeFaculty = 'yes'" in NEW_PUBLICATIONS_FOR_ENRICHMENT_SQL


def test_sql_orders_by_pmid_ascending():
    """Required so the orchestrator can advance the watermark to MAX(pmid)
    of the returned set. ASC, not DESC."""
    assert re.search(r"ORDER BY\s+a\.pmid\s+ASC", NEW_PUBLICATIONS_FOR_ENRICHMENT_SQL)


def test_sql_supports_limit_parameter():
    assert "LIMIT :limit_n" in NEW_PUBLICATIONS_FOR_ENRICHMENT_SQL


def test_sql_uses_left_join_for_abstract():
    """A pub may not have an abstract row in reporting_abstracts — must not
    filter those out. LEFT JOIN keeps the row with abstractVarchar=NULL."""
    assert re.search(
        r"LEFT JOIN reporting_abstracts r ON r\.pmid = a\.pmid",
        NEW_PUBLICATIONS_FOR_ENRICHMENT_SQL,
    )


# ---------------------------------------------------------------------------
# fetch_new_publications helper
# ---------------------------------------------------------------------------

def _make_engine_returning(rows):
    """Build an engine mock whose connection.execute().mappings().all() returns rows."""
    conn = MagicMock()
    result = MagicMock()
    result.mappings.return_value.all.return_value = rows
    conn.execute.return_value = result
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    engine.connect.return_value.__exit__.return_value = False
    return engine, conn


def test_fetch_new_publications_passes_watermark_and_limit_as_parameters():
    engine, conn = _make_engine_returning([])
    fetch_new_publications(engine, last_max_pmid=12345, limit=500)
    params = conn.execute.call_args.args[1]
    assert params == {"last_max_pmid": 12345, "limit_n": 500}


def test_fetch_new_publications_coerces_int_args():
    engine, conn = _make_engine_returning([])
    fetch_new_publications(engine, last_max_pmid="12345", limit="500")
    params = conn.execute.call_args.args[1]
    assert params == {"last_max_pmid": 12345, "limit_n": 500}
    assert isinstance(params["last_max_pmid"], int)
    assert isinstance(params["limit_n"], int)


def test_fetch_new_publications_default_limit_is_well_above_cost_guard_trip_count():
    """Cost guard trips around 500 papers at default rates; the SQL limit
    should be a safety valve above that, not below."""
    engine, conn = _make_engine_returning([])
    fetch_new_publications(engine, last_max_pmid=0)
    params = conn.execute.call_args.args[1]
    # Cost guard trips at 500; sentinel value here is "at least 1000"
    assert params["limit_n"] >= 1000


def test_fetch_new_publications_returns_plain_dicts():
    """RowMapping → dict so callers don't accidentally couple to sqlalchemy
    return types."""
    fake_row = {"pmid": 40927852, "articleTitle": "test", "articleYear": 2025}
    engine, _ = _make_engine_returning([fake_row])
    out = fetch_new_publications(engine, last_max_pmid=0)
    assert out == [fake_row]
    assert isinstance(out[0], dict)


def test_fetch_new_publications_returns_empty_list_for_empty_result():
    engine, _ = _make_engine_returning([])
    assert fetch_new_publications(engine, last_max_pmid=10**9) == []

"""Tests for the hot-path PMID-set -> dirty-CWID SQL layer (#119).

`CWIDS_BY_PMIDS_SQL` is the inverse of `PMIDS_BY_CWID_SQL`: given a
publication set, it returns the full-time-faculty first/last-author CWIDs.
The hot orchestrator uses `get_cwids_for_pmids` to derive the per-run
dirty-CWID set for the weekly Rollup fan-out. SQL-shape + helper-contract
tests; behavioral correctness against live MariaDB is an integration concern.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from utils.sql_queries import CWIDS_BY_PMIDS_SQL, get_cwids_for_pmids


# ---------------------------------------------------------------------------
# CWIDS_BY_PMIDS_SQL — join shape + filter clauses
# ---------------------------------------------------------------------------

def test_sql_selects_distinct_cwid():
    assert "SELECT DISTINCT au.personIdentifier AS cwid" in CWIDS_BY_PMIDS_SQL


def test_sql_scopes_to_an_explicit_pmid_list():
    assert "au.pmid IN :pmid_list" in CWIDS_BY_PMIDS_SQL


def test_sql_filters_to_full_time_faculty():
    """Rollup is faculty-scoped — the identity join + fullTimeFaculty filter
    mirror AUTHOR_MAPPING_SQL."""
    assert "JOIN identity id ON id.cwid = au.personIdentifier" in CWIDS_BY_PMIDS_SQL
    assert "id.fullTimeFaculty = 'yes'" in CWIDS_BY_PMIDS_SQL


def test_sql_filters_to_first_last_author_position():
    """v1 faculty-facing scope — matches PMIDS_BY_CWID_SQL / FACULTY_GAP_SCAN_SQL."""
    assert "au.authorPosition IN ('first', 'last')" in CWIDS_BY_PMIDS_SQL


def test_sql_omits_the_article_join():
    """The caller passes PMIDs already scoped to Academic Articles /
    articleYear >= 2020, so no analysis_summary_article join is needed."""
    assert "analysis_summary_article" not in CWIDS_BY_PMIDS_SQL
    assert "articleYear" not in CWIDS_BY_PMIDS_SQL


# ---------------------------------------------------------------------------
# get_cwids_for_pmids helper
# ---------------------------------------------------------------------------

def _patch_conn(cwid_rows):
    """Patch get_db_connection so execute() returns the given CWID rows."""
    conn = MagicMock()
    conn.execute.return_value = [(c,) for c in cwid_rows]
    return patch("utils.sql_queries.get_db_connection", return_value=conn), conn


def test_empty_input_short_circuits_without_a_db_call():
    with patch("utils.sql_queries.get_db_connection") as gdc:
        assert get_cwids_for_pmids([]) == []
    gdc.assert_not_called()


def test_blank_only_input_short_circuits_without_a_db_call():
    with patch("utils.sql_queries.get_db_connection") as gdc:
        assert get_cwids_for_pmids(["", "   "]) == []
    gdc.assert_not_called()


def test_returns_sorted_deduped_cwids_and_closes_the_connection():
    # A CWID first/last-authoring two of the input PMIDs appears twice.
    cm, conn = _patch_conn(["xyz1003", "abc1001", "abc1001"])
    with cm:
        result = get_cwids_for_pmids(["111", "222"])
    assert result == ["abc1001", "xyz1003"]
    conn.close.assert_called_once()


def test_stringifies_and_dedupes_pmids_into_the_bind_parameter():
    cm, conn = _patch_conn([])
    with cm:
        get_cwids_for_pmids([123, "456", 123])
    params = conn.execute.call_args.args[1]
    assert params == {"pmid_list": ["123", "456"]}

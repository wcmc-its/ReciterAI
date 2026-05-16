"""Tests for the --rescore-pmids flag on score_publications.py.

Provides a CLI handle for targeted operator recovery of PMIDs outside
the weekly hot-path delta window (e.g. content-filter survivors after
a fallback-path change, or PMIDs that ReCiter newly attributed to a
CWID). Bypasses both --delta-since and the PROCESSING# checkpoint
cache.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import score_publications as sp


# ---------------------------------------------------------------------------
# extract_publications_by_pmids — SQL shape + behavior
# ---------------------------------------------------------------------------


def _stub_db_with_rows(rows: list[dict]):
    """Build a fake SQLAlchemy connection that yields the given rows on execute()."""
    mappings_result = MagicMock()
    mappings_result.all.return_value = rows
    exec_result = MagicMock()
    exec_result.mappings.return_value = mappings_result
    conn = MagicMock()
    conn.execute.return_value = exec_result
    return conn


def test_empty_pmid_list_returns_empty_without_db_call():
    """No PMIDs supplied → return immediately, no DB connection opened."""
    with patch.object(sp, "get_db_connection") as get_conn:
        assert sp.extract_publications_by_pmids([]) == []
        get_conn.assert_not_called()


def test_extracts_supplied_pmids_with_in_clause():
    """The IN-clause variant of the extraction SQL is invoked with the given list."""
    conn = _stub_db_with_rows([
        {"pmid": "41198049", "synopsis": "s1", "abstract": "a1"},
        {"pmid": "41485218", "synopsis": "s2", "abstract": "a2"},
    ])
    with patch.object(sp, "get_db_connection", return_value=conn):
        pubs = sp.extract_publications_by_pmids(["41198049", "41485218"])

    assert len(pubs) == 2
    assert pubs[0]["pmid"] == "41198049"
    assert pubs[1]["pmid"] == "41485218"

    # Verify the SQL was the IN-clause variant, not the base query
    call_args = conn.execute.call_args
    sql_text = str(call_args.args[0])
    assert "a1.pmid IN" in sql_text or ":pmid_list" in sql_text
    assert "datePublicationAddedToEntrez" not in sql_text  # No date filter
    # Verify the PMID list was passed as the bound param
    params = call_args.args[1]
    assert params == {"pmid_list": ["41198049", "41485218"]}


def test_pmids_not_found_in_db_are_silently_excluded():
    """Returns the subset of requested PMIDs that exist in analysis_summary_article.
    Caller compares lengths to detect missing PMIDs."""
    conn = _stub_db_with_rows([
        {"pmid": "41198049", "synopsis": "s1", "abstract": "a1"},
    ])
    with patch.object(sp, "get_db_connection", return_value=conn):
        pubs = sp.extract_publications_by_pmids(["41198049", "99999999", "88888888"])

    # 3 requested, 1 returned — caller can detect the gap
    assert len(pubs) == 1
    assert pubs[0]["pmid"] == "41198049"


def test_db_connection_closed_even_on_exception():
    """Verify the finally block closes the connection on error."""
    conn = MagicMock()
    conn.execute.side_effect = RuntimeError("simulated DB error")
    with patch.object(sp, "get_db_connection", return_value=conn):
        with pytest.raises(RuntimeError, match="simulated DB error"):
            sp.extract_publications_by_pmids(["1", "2"])
    conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# CLI argument behavior
# ---------------------------------------------------------------------------


def test_cli_rejects_mutually_exclusive_flags(monkeypatch):
    """--rescore-pmids and --delta-since cannot be used together."""
    import asyncio
    import sys

    monkeypatch.setattr(sys, "argv", [
        "score_publications.py",
        "--rescore-pmids", "1,2,3",
        "--delta-since", "2026-05-01T00:00:00Z",
    ])
    # parser.error raises SystemExit
    with pytest.raises(SystemExit):
        asyncio.run(sp.main())


# ---------------------------------------------------------------------------
# STAGE# substrate skip-cache bypass when --rescore-pmids is set
# ---------------------------------------------------------------------------


def test_rescore_pmids_bypasses_stage_skip_cache(monkeypatch):
    """When --rescore-pmids is set, the run-level should_skip check must
    NOT be consulted. A prior `records_written=0` complete row for the
    same PMID set would otherwise collide on input_hash and block recovery
    via the substrate cache, forcing operator DDB hand-surgery.
    """
    import asyncio
    import sys

    monkeypatch.setattr(sys, "argv", [
        "score_publications.py",
        "--rescore-pmids", "41198049",
    ])

    # Patch out everything the main() loop needs except should_skip. We
    # want to assert should_skip is NOT called, then short-circuit before
    # any real scoring fires.
    fake_table = MagicMock()
    fake_table.put_item = MagicMock(return_value={})
    monkeypatch.setattr(sp, "get_table", lambda: fake_table)
    monkeypatch.setattr(sp, "load_thresholds", lambda: {
        "score_floor": 0.3,
        "target_failure_rate": 0.01,
        "uncovered_score_floor": 0.5,
    })
    monkeypatch.setattr(sp, "get_dynamo_client", lambda: MagicMock())
    monkeypatch.setattr(sp, "extract_publications_by_pmids", lambda pmids: [])
    monkeypatch.setattr(sp, "extract_author_mapping", lambda: {})
    monkeypatch.setattr(sp, "extract_faculty_metadata", lambda: {})

    should_skip_mock = MagicMock(return_value=(False, None))
    monkeypatch.setattr(sp, "should_skip", should_skip_mock)

    asyncio.run(sp.main())

    # The contract: should_skip is bypassed entirely under --rescore-pmids,
    # not consulted-and-overridden. Asserts the run-level cache cannot
    # block a rescore even when the input_hash would match a prior row.
    should_skip_mock.assert_not_called()


def test_normal_run_still_consults_should_skip(monkeypatch):
    """Sanity check: without --rescore-pmids, should_skip is still
    consulted (we did not accidentally turn it off for everyone)."""
    import asyncio
    import sys

    monkeypatch.setattr(sys, "argv", ["score_publications.py"])

    fake_table = MagicMock()
    fake_table.put_item = MagicMock(return_value={})
    monkeypatch.setattr(sp, "get_table", lambda: fake_table)
    monkeypatch.setattr(sp, "load_thresholds", lambda: {
        "score_floor": 0.3,
        "target_failure_rate": 0.01,
        "uncovered_score_floor": 0.5,
    })
    monkeypatch.setattr(sp, "get_dynamo_client", lambda: MagicMock())
    monkeypatch.setattr(sp, "extract_publications", lambda delta_since=None: [])
    monkeypatch.setattr(sp, "extract_author_mapping", lambda: {})
    monkeypatch.setattr(sp, "extract_faculty_metadata", lambda: {})
    monkeypatch.setattr(sp, "get_unscored_publications", lambda *a, **k: [])

    should_skip_mock = MagicMock(return_value=(False, None))
    monkeypatch.setattr(sp, "should_skip", should_skip_mock)

    asyncio.run(sp.main())

    should_skip_mock.assert_called_once()


# ---------------------------------------------------------------------------
# --retry-pmids — hot-path retry sweep ingestion
# ---------------------------------------------------------------------------


def test_cli_rejects_retry_and_rescore_together(monkeypatch):
    """--retry-pmids (additive sweep) and --rescore-pmids (operator force
    recovery) are competing mechanisms and cannot be combined."""
    import asyncio
    import sys

    monkeypatch.setattr(sys, "argv", [
        "score_publications.py",
        "--rescore-pmids", "1",
        "--retry-pmids", "2",
    ])
    with pytest.raises(SystemExit):
        asyncio.run(sp.main())


def test_retry_pmids_unioned_onto_date_delta(monkeypatch):
    """--retry-pmids extracts the failed PMIDs via extract_publications_by_pmids
    and unions them onto the --delta-since date delta, deduplicating against
    PMIDs already present in the delta."""
    import asyncio
    import sys

    monkeypatch.setattr(sys, "argv", [
        "score_publications.py",
        "--delta-since", "2026-05-01T00:00:00Z",
        "--retry-pmids", "111,222",
    ])

    fake_table = MagicMock()
    fake_table.put_item = MagicMock(return_value={})
    monkeypatch.setattr(sp, "get_table", lambda: fake_table)
    monkeypatch.setattr(sp, "load_thresholds", lambda: {
        "score_floor": 0.3,
        "target_failure_rate": 0.01,
        "uncovered_score_floor": 0.5,
    })
    monkeypatch.setattr(sp, "get_dynamo_client", lambda: MagicMock())
    # Date delta carries 999 and 111 — 111 is also a retry PMID.
    monkeypatch.setattr(
        sp, "extract_publications",
        lambda delta_since=None: [{"pmid": "999"}, {"pmid": "111"}],
    )
    by_pmids_calls: list = []

    def fake_by_pmids(pmids):
        by_pmids_calls.append(list(pmids))
        return [{"pmid": "111"}, {"pmid": "222"}]

    monkeypatch.setattr(sp, "extract_publications_by_pmids", fake_by_pmids)
    monkeypatch.setattr(sp, "extract_author_mapping", lambda: {})
    monkeypatch.setattr(sp, "extract_faculty_metadata", lambda: {})

    captured: dict = {}

    def fake_unscored(pubs, *a, **k):
        captured["pmids"] = sorted(str(p["pmid"]) for p in pubs)
        return []

    monkeypatch.setattr(sp, "get_unscored_publications", fake_unscored)
    monkeypatch.setattr(sp, "should_skip", MagicMock(return_value=(False, None)))

    asyncio.run(sp.main())

    # extract_publications_by_pmids received exactly the retry list.
    assert by_pmids_calls == [["111", "222"]]
    # 999 + 111 from the delta, 222 unioned from the retry sweep;
    # 111 is NOT double-counted.
    assert captured["pmids"] == ["111", "222", "999"]

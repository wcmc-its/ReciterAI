"""Tests for the --pmids work-set flags on score_publications.py.

`--pmids` is the explicit PMID work set — an alternative to the
`--delta-since` date delta. Two orthogonal modifiers compose with it (#89,
consolidating the former --rescore-pmids / --retry-pmids / --pmids flags):

- ``--additive`` — union the work set onto the --delta-since delta instead
  of replacing it (the hot-path retry sweep); caches respected.
- ``--force`` — bypass the PROCESSING# checkpoint and the STAGE# skip
  cache (operator force-recovery).

Also covers ``extract_publications_by_pmids``, the explicit-list extraction
SQL that backs every --pmids variant.
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


def test_extracts_supplied_pmids_with_in_clause(monkeypatch):
    """The IN-clause variant of the extraction SQL is invoked with the given list.

    Post-#38: synopsis is no longer joined by the SQL; it's attached
    afterward from DDB via ``fetch_synopsis_records`` (synopsis + #150 item 2
    provenance).
    """
    conn = _stub_db_with_rows([
        {"pmid": "41198049", "title": "T1", "abstract": "a1"},
        {"pmid": "41485218", "title": "T2", "abstract": "a2"},
    ])
    monkeypatch.setattr(
        sp, "fetch_synopsis_records",
        lambda client, pmids: {
            "41198049": {"synopsis": "s1", "synopsis_model": "m", "enriched_at": "t1"},
            "41485218": {"synopsis": "s2", "synopsis_model": "m", "enriched_at": "t2"},
        },
    )
    monkeypatch.setattr(sp, "get_dynamo_client", lambda: MagicMock())

    with patch.object(sp, "get_db_connection", return_value=conn):
        pubs = sp.extract_publications_by_pmids(["41198049", "41485218"])

    assert len(pubs) == 2
    assert pubs[0]["pmid"] == "41198049"
    assert pubs[0]["synopsis"] == "s1"
    assert pubs[1]["pmid"] == "41485218"
    assert pubs[1]["synopsis"] == "s2"

    # Verify the SQL was the IN-clause variant, not the base query
    call_args = conn.execute.call_args
    sql_text = str(call_args.args[0])
    assert "a1.pmid IN" in sql_text or ":pmid_list" in sql_text
    assert "datePublicationAddedToEntrez" not in sql_text  # No date filter
    # The SQL must no longer join reciterai_synopsis (#38).
    assert "reciterai_synopsis" not in sql_text
    # Verify the PMID list was passed as the bound param
    params = call_args.args[1]
    assert params == {"pmid_list": ["41198049", "41485218"]}


def test_pmids_not_found_in_db_are_silently_excluded(monkeypatch):
    """Returns the subset of requested PMIDs that exist in analysis_summary_article.
    Caller compares lengths to detect missing PMIDs."""
    conn = _stub_db_with_rows([
        {"pmid": "41198049", "title": "T1", "abstract": "a1"},
    ])
    monkeypatch.setattr(
        sp, "fetch_synopsis_records",
        lambda client, pmids: {
            "41198049": {"synopsis": "s1", "synopsis_model": "m", "enriched_at": "t"},
        },
    )
    monkeypatch.setattr(sp, "get_dynamo_client", lambda: MagicMock())
    with patch.object(sp, "get_db_connection", return_value=conn):
        pubs = sp.extract_publications_by_pmids(["41198049", "99999999", "88888888"])

    # 3 requested, 1 returned — caller can detect the gap
    assert len(pubs) == 1
    assert pubs[0]["pmid"] == "41198049"


def test_pmids_without_ddb_synopsis_are_dropped(monkeypatch):
    """The #38 read-switch must preserve the legacy INNER-JOIN semantics:
    a PMID whose IMPACT# row has no `synopsis` attribute is silently dropped."""
    conn = _stub_db_with_rows([
        {"pmid": "100", "title": "T1", "abstract": "a1"},
        {"pmid": "200", "title": "T2", "abstract": "a2"},
        {"pmid": "300", "title": "T3", "abstract": "a3"},
    ])
    # DDB has synopsis for 100 and 300, none for 200.
    monkeypatch.setattr(
        sp, "fetch_synopsis_records",
        lambda client, pmids: {
            "100": {"synopsis": "s1", "synopsis_model": "m", "enriched_at": "t"},
            "300": {"synopsis": "s3", "synopsis_model": "m", "enriched_at": "t"},
        },
    )
    monkeypatch.setattr(sp, "get_dynamo_client", lambda: MagicMock())
    with patch.object(sp, "get_db_connection", return_value=conn):
        pubs = sp.extract_publications_by_pmids(["100", "200", "300"])
    assert [p["pmid"] for p in pubs] == ["100", "300"]


def test_db_connection_closed_even_on_exception():
    """Verify the finally block closes the connection on error."""
    conn = MagicMock()
    conn.execute.side_effect = RuntimeError("simulated DB error")
    with patch.object(sp, "get_db_connection", return_value=conn):
        with pytest.raises(RuntimeError, match="simulated DB error"):
            sp.extract_publications_by_pmids(["1", "2"])
    conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# CLI — scoping-flag validation (#89)
# ---------------------------------------------------------------------------


def test_cli_rejects_pmids_with_delta_since(monkeypatch):
    """--pmids replaces --delta-since; combining them without --additive is
    rejected — they are competing work sets."""
    import asyncio
    import sys

    monkeypatch.setattr(sys, "argv", [
        "score_publications.py",
        "--pmids", "1,2,3",
        "--delta-since", "2026-05-01T00:00:00Z",
    ])
    with pytest.raises(SystemExit):
        asyncio.run(sp.main())


def test_cli_rejects_force_with_additive(monkeypatch):
    """--force (operator force-recovery) and --additive (orchestrator sweep)
    are competing modifiers and cannot be combined."""
    import asyncio
    import sys

    monkeypatch.setattr(sys, "argv", [
        "score_publications.py",
        "--pmids", "1",
        "--force", "--additive",
    ])
    with pytest.raises(SystemExit):
        asyncio.run(sp.main())


def test_cli_rejects_additive_without_pmids(monkeypatch):
    """--additive is a --pmids modifier; on its own it has nothing to union."""
    import asyncio
    import sys

    monkeypatch.setattr(sys, "argv", ["score_publications.py", "--additive"])
    with pytest.raises(SystemExit):
        asyncio.run(sp.main())


def test_cli_rejects_force_without_pmids(monkeypatch):
    """--force is a --pmids modifier; on its own it has no work set to force."""
    import asyncio
    import sys

    monkeypatch.setattr(sys, "argv", ["score_publications.py", "--force"])
    with pytest.raises(SystemExit):
        asyncio.run(sp.main())


# ---------------------------------------------------------------------------
# CLI — --force bypasses the STAGE# substrate skip cache
# ---------------------------------------------------------------------------


def test_force_bypasses_stage_skip_cache(monkeypatch):
    """With --pmids ... --force, the run-level should_skip check must NOT be
    consulted. A prior `records_written=0` complete row for the same PMID
    set would otherwise collide on input_hash and block recovery via the
    substrate cache, forcing operator DDB hand-surgery.
    """
    import asyncio
    import sys

    monkeypatch.setattr(sys, "argv", [
        "score_publications.py",
        "--pmids", "41198049", "--force",
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

    # The contract: should_skip is bypassed entirely under --force, not
    # consulted-and-overridden. The run-level cache cannot block a forced
    # rescore even when the input_hash would match a prior row.
    should_skip_mock.assert_not_called()


def test_normal_run_still_consults_should_skip(monkeypatch):
    """Sanity check: a plain --delta-since run (no --force) still consults
    should_skip — the bypass did not leak to everyone."""
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
# CLI — --additive unions the work set onto the date delta
# ---------------------------------------------------------------------------


def test_additive_unions_onto_date_delta(monkeypatch):
    """--pmids ... --additive extracts the explicit set via
    extract_publications_by_pmids and unions it onto the --delta-since date
    delta, deduplicating against PMIDs already present in the delta."""
    import asyncio
    import sys

    monkeypatch.setattr(sys, "argv", [
        "score_publications.py",
        "--delta-since", "2026-05-01T00:00:00Z",
        "--pmids", "111,222", "--additive",
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
    # Date delta carries 999 and 111 — 111 is also an additive PMID.
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

    # extract_publications_by_pmids received exactly the --pmids list.
    assert by_pmids_calls == [["111", "222"]]
    # 999 + 111 from the delta, 222 unioned from the additive set;
    # 111 is NOT double-counted.
    assert captured["pmids"] == ["111", "222", "999"]


# ---------------------------------------------------------------------------
# cull_invalid_publications — #150 item 3 consume-time invalid exclude
# ---------------------------------------------------------------------------


def test_cull_invalid_drops_flagged_pmids_and_reports_them():
    pubs = [{"pmid": "111"}, {"pmid": "222"}, {"pmid": "333"}]
    kept, culled = sp.cull_invalid_publications(pubs, {"222"})
    assert [p["pmid"] for p in kept] == ["111", "333"]
    assert culled == ["222"]


def test_cull_invalid_empty_set_is_noop_same_object():
    pubs = [{"pmid": "111"}, {"pmid": "222"}]
    kept, culled = sp.cull_invalid_publications(pubs, set())
    assert kept is pubs           # untouched — no scan hit, no copy
    assert culled == []


def test_cull_invalid_no_overlap_is_noop():
    pubs = [{"pmid": "111"}, {"pmid": "222"}]
    kept, culled = sp.cull_invalid_publications(pubs, {"999"})
    assert kept is pubs
    assert culled == []


def test_cull_invalid_normalizes_pmid_types():
    """Work-set PMIDs are strings; the invalid set may arrive as ints — both
    sides are normalized so the membership test still matches."""
    pubs = [{"pmid": 111}, {"pmid": "222"}]
    kept, culled = sp.cull_invalid_publications(pubs, {111})
    assert [p["pmid"] for p in kept] == ["222"]
    assert culled == ["111"]

"""Tests for #80 new-researcher onboarding — Phase 1 backend primitives.

Covers the primitives still delivered in Phase 1 (post-#141):

- `get_pmids_for_cwid` — CWID-scoped accepted-publication PMID set.
- `score_publications.py --pmids` — explicit work set, idempotent (R2).
- `onboarding_cost_guard_tripped` + the `--pmids` cost guard (R5).

`check_synopsis_coverage` was the R3-step-1 synopsis-precondition partition;
it was retired in #141 once the Enrich stage moved to inline
`run_enrichment_backfill`. The fifth original checklist item
(`STAGE#rollup_by_cwid` `input_pmid_set`) is a verified deferral —
`rollup_by_cwid.py` is CSV-count-driven and writes a GLOBAL-scoped row, so
it has no PMID set to record; that rework belongs to Phase 2's CWID-scoped
rollup stage.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import score_publications as sp
import utils.sql_queries as sq


# ---------------------------------------------------------------------------
# get_pmids_for_cwid — CWID-scoped extraction (#80 R2)
# ---------------------------------------------------------------------------


def _fake_conn(rows):
    """Fake SQLAlchemy connection whose execute() yields `rows`."""
    conn = MagicMock()
    conn.execute.return_value = rows
    return conn


def test_get_pmids_for_cwid_returns_stringified_pmids(monkeypatch):
    conn = _fake_conn([(41198049,), (36853799,)])
    monkeypatch.setattr(sq, "get_db_connection", lambda: conn)

    pmids = sq.get_pmids_for_cwid("abc1234")

    assert pmids == ["41198049", "36853799"]
    # CWID bound into the query params; query targets the author table.
    call = conn.execute.call_args
    assert call.args[1] == {"cwid": "abc1234"}
    sql_text = str(call.args[0])
    assert "analysis_summary_author" in sql_text
    assert "personIdentifier" in sql_text
    # The CWID query must NOT inherit the synopsis-required inner join —
    # synopsis-less PMIDs must survive for the precondition check.
    assert "reciterai_synopsis" not in sql_text
    conn.close.assert_called_once()


def test_get_pmids_for_cwid_strips_whitespace(monkeypatch):
    conn = _fake_conn([])
    monkeypatch.setattr(sq, "get_db_connection", lambda: conn)
    sq.get_pmids_for_cwid("  xyz9999  ")
    assert conn.execute.call_args.args[1] == {"cwid": "xyz9999"}


def test_get_pmids_for_cwid_blank_cwid_skips_db(monkeypatch):
    get_conn = MagicMock()
    monkeypatch.setattr(sq, "get_db_connection", get_conn)
    assert sq.get_pmids_for_cwid("") == []
    assert sq.get_pmids_for_cwid("   ") == []
    get_conn.assert_not_called()


def test_pmids_by_cwid_sql_scopes_to_first_last_author():
    """First/last is the v1 faculty-facing author scope (matches
    spotlight/author_resolver.py). Shape test — the get_pmids_for_cwid
    tests mock the connection, so nothing else guards the SQL filter."""
    assert "authorPosition IN ('first', 'last')" in sq.PMIDS_BY_CWID_SQL


# ---------------------------------------------------------------------------
# onboarding_cost_guard_tripped — pure decision (#80 R5)
# ---------------------------------------------------------------------------


def test_cost_guard_passes_at_or_below_threshold():
    assert sp.onboarding_cost_guard_tripped(300, threshold=300, override=False) is False
    assert sp.onboarding_cost_guard_tripped(0, threshold=300, override=False) is False


def test_cost_guard_trips_above_threshold():
    assert sp.onboarding_cost_guard_tripped(301, threshold=300, override=False) is True


def test_cost_guard_override_bypasses_any_size():
    assert sp.onboarding_cost_guard_tripped(10_000, threshold=300, override=True) is False


# ---------------------------------------------------------------------------
# score_publications.py --pmids flag (#80 R2)
# ---------------------------------------------------------------------------


def test_pmids_rejects_combination_with_delta_since(monkeypatch):
    import asyncio
    import sys

    monkeypatch.setattr(sys, "argv", [
        "score_publications.py",
        "--pmids", "1,2",
        "--delta-since", "2026-05-01T00:00:00Z",
    ])
    with pytest.raises(SystemExit):
        asyncio.run(sp.main())


def _stub_score_main_env(monkeypatch):
    """Patch out the I/O `score_publications.main` needs so a --pmids run
    can be driven to (but not through) the scoring loop."""
    fake_table = MagicMock()
    fake_table.put_item = MagicMock(return_value={})
    monkeypatch.setattr(sp, "get_table", lambda: fake_table)
    monkeypatch.setattr(sp, "load_thresholds", lambda: {
        "score_floor": 0.3,
        "target_failure_rate": 0.01,
        "uncovered_score_floor": 0.5,
    })
    monkeypatch.setattr(sp, "get_dynamo_client", lambda: MagicMock())
    monkeypatch.setattr(sp, "extract_author_mapping", lambda: {})
    monkeypatch.setattr(sp, "extract_faculty_metadata", lambda: {})


def test_pmids_mode_extracts_explicit_set_and_respects_idempotency(monkeypatch):
    """--pmids extracts exactly the supplied list AND, unlike --pmids --force,
    still consults the STAGE# skip cache (idempotent re-runs)."""
    import asyncio
    import sys

    monkeypatch.setattr(sys, "argv", ["score_publications.py", "--pmids", "111,222"])
    _stub_score_main_env(monkeypatch)

    by_pmids_calls: list = []

    def fake_by_pmids(pmids):
        by_pmids_calls.append(list(pmids))
        return [{"pmid": "111"}, {"pmid": "222"}]

    monkeypatch.setattr(sp, "extract_publications_by_pmids", fake_by_pmids)
    monkeypatch.setattr(sp, "get_unscored_publications", lambda pubs, *a, **k: [])
    should_skip_mock = MagicMock(return_value=(False, None))
    monkeypatch.setattr(sp, "should_skip", should_skip_mock)

    asyncio.run(sp.main())

    # Extracted exactly the explicit list — no date delta.
    assert by_pmids_calls == [["111", "222"]]
    # Idempotency preserved: should_skip IS consulted (contrast --force).
    should_skip_mock.assert_called_once()


def test_pmids_cost_guard_trips_and_exits_before_scoring(monkeypatch):
    """A --pmids run whose net work exceeds the threshold exits non-zero
    before the skip-cache check or any scoring."""
    import asyncio
    import sys

    monkeypatch.setattr(sys, "argv", ["score_publications.py", "--pmids", "1,2,3"])
    _stub_score_main_env(monkeypatch)
    monkeypatch.setattr(sp, "ONBOARDING_COST_GUARD_MAX_PMIDS", 300)

    # 500 publications, none previously scored → net work = 500 > 300.
    big_set = [{"pmid": str(i)} for i in range(500)]
    monkeypatch.setattr(sp, "extract_publications_by_pmids", lambda pmids: big_set)
    monkeypatch.setattr(sp, "get_unscored_publications", lambda pubs, *a, **k: pubs)
    should_skip_mock = MagicMock(return_value=(False, None))
    monkeypatch.setattr(sp, "should_skip", should_skip_mock)

    with pytest.raises(SystemExit) as exc:
        asyncio.run(sp.main())

    assert exc.value.code == 1
    # Guard fires before the skip-cache gate — no scoring work attempted.
    should_skip_mock.assert_not_called()


def test_pmids_cost_guard_override_allows_large_run(monkeypatch):
    """--allow-cost-override lets an above-threshold --pmids run proceed
    past the guard (into the normal skip-cache path)."""
    import asyncio
    import sys

    monkeypatch.setattr(sys, "argv", [
        "score_publications.py", "--pmids", "1,2,3", "--allow-cost-override",
    ])
    _stub_score_main_env(monkeypatch)
    monkeypatch.setattr(sp, "ONBOARDING_COST_GUARD_MAX_PMIDS", 300)

    big_set = [{"pmid": str(i)} for i in range(500)]
    monkeypatch.setattr(sp, "extract_publications_by_pmids", lambda pmids: big_set)
    monkeypatch.setattr(sp, "get_unscored_publications", lambda pubs, *a, **k: pubs)
    # Skip-cache hit so the run short-circuits before real scoring.
    monkeypatch.setattr(
        sp, "should_skip",
        MagicMock(return_value=(True, {"started_at": "2026-05-16T00:00:00Z"})),
    )

    # Override → guard does not exit; run reaches the skip-cache gate.
    asyncio.run(sp.main())

"""Tests for TOPIC# row materialization in score_publications.

#80 PR 4a — the _materialize_topic_rows / _delete_topic_rows_for_pmid
helpers and score_one_publication's persist_topic_rows gating +
rows-before-marker ordering.

#98 — main()'s persist_topic_rows gate: ON for every --emit-envelope run
(the hot path + onboarding), OFF for the cold path."""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import MagicMock

import pytest

import score_publications as sp
from utils.bedrock_client import HAIKU_MODEL

_TAXONOMY = {
    "taxonomy_version": "taxonomy_v2",
    "topics": [{"id": "t1", "label": "T1", "description": "d1"}],
}
_INT_TO_ID = {"0": "t1"}
_ID_TO_INT = {"t1": "0"}
_PUB = {"pmid": "12345", "synopsis": "syn", "abstract": "abs", "title": "A Title"}
_AUTHORS = [{"cwid": "abc1234", "position": "first"}]


class _FakeBedrockScoring:
    """Bedrock stub: topic int 0 passes screening and dense scoring."""

    def call_json(self, *, model, messages, **kwargs):
        if model == HAIKU_MODEL:
            return {"0": 0.9}
        return {"0": {"score": 0.9, "rationale": "r"}}


class _FakeBedrockNoPass:
    """Bedrock stub: topic int 0 scores below SCREENING_THRESHOLD (0.3),
    so no topic clears screening — score_one_publication takes the
    no-relevant-topics completion path."""

    def call_json(self, *, model, messages, **kwargs):
        if model == HAIKU_MODEL:
            return {"0": 0.0}
        return {"0": {"score": 0.0, "rationale": ""}}


def _ddb_client(query_items=None):
    """Low-level DynamoDB client mock with real-dict returns so the
    helpers' .get(...) calls behave."""
    client = MagicMock()
    client.query.return_value = {"Items": query_items or []}
    client.batch_write_item.return_value = {}
    client.delete_item.return_value = {}
    return client


def _topic_item(pmid, cwid, topic="cardio", score="0900"):
    """A raw TOPIC# item in the low-level typed shape PmidIndex returns."""
    return {
        "PK": {"S": f"TOPIC#{topic}"},
        "SK": {"S": f"SCORE#{score}#ACTIVITY#pmid_{pmid}#cwid_{cwid}"},
        "faculty_uid": {"S": f"cwid_{cwid}"},
        "pmid": {"S": pmid},
    }


# --- _materialize_topic_rows -------------------------------------------------


def test_materialize_writes_topic_rows_with_title_and_synopsis():
    client = _ddb_client()
    sp._materialize_topic_rows(
        client, "reciterai",
        pmid="12345",
        dense_scores={"cardio": {"score": 0.9, "rationale": "r"}},
        authors=_AUTHORS,
        taxonomy_version="taxonomy_v2",
        synopsis="a synopsis",
        title="A Title",
    )
    assert client.batch_write_item.called
    request = client.batch_write_item.call_args.kwargs["RequestItems"]["reciterai"]
    items = [r["PutRequest"]["Item"] for r in request]
    assert len(items) == 1
    item = items[0]
    assert item["PK"] == {"S": "TOPIC#cardio"}
    assert item["faculty_uid"] == {"S": "cwid_abc1234"}
    assert item["synopsis"] == {"S": "a synopsis"}
    assert item["title"] == {"S": "A Title"}
    assert item["author_position"] == {"S": "first"}


def test_materialize_no_authors_is_noop():
    client = _ddb_client()
    sp._materialize_topic_rows(
        client, "reciterai",
        pmid="12345",
        dense_scores={"cardio": {"score": 0.9}},
        authors=[],
        taxonomy_version="taxonomy_v2",
        synopsis="s", title="t",
    )
    client.query.assert_not_called()
    client.batch_write_item.assert_not_called()
    client.delete_item.assert_not_called()


def test_materialize_deletes_prior_rows_before_writing():
    """A prior TOPIC# row for this (pmid, cwid) is deleted before the
    fresh rows are written — delete-then-write (D-B)."""
    prior = _topic_item("12345", "abc1234", topic="oldtopic", score="0500")
    client = _ddb_client(query_items=[prior])
    sp._materialize_topic_rows(
        client, "reciterai",
        pmid="12345",
        dense_scores={"cardio": {"score": 0.9}},
        authors=_AUTHORS,
        taxonomy_version="taxonomy_v2",
        synopsis="s", title="t",
    )
    client.delete_item.assert_called_once_with(
        TableName="reciterai",
        Key={"PK": prior["PK"], "SK": prior["SK"]},
    )
    assert client.batch_write_item.called


# --- _delete_topic_rows_for_pmid --------------------------------------------


def test_delete_is_scoped_to_given_faculty_uids():
    """A co-authored PMID: only the target CWID's rows are deleted; a
    co-author's TOPIC# rows are left untouched."""
    mine = _topic_item("12345", "abc1234")
    coauthor = _topic_item("12345", "xyz9999")
    client = _ddb_client(query_items=[mine, coauthor])
    sp._delete_topic_rows_for_pmid(
        client, "reciterai", "12345", {"cwid_abc1234"},
    )
    client.delete_item.assert_called_once_with(
        TableName="reciterai",
        Key={"PK": mine["PK"], "SK": mine["SK"]},
    )


def test_delete_paginates_query():
    page1 = {
        "Items": [_topic_item("12345", "abc1234", topic="t1")],
        "LastEvaluatedKey": {"k": "v"},
    }
    page2 = {"Items": [_topic_item("12345", "abc1234", topic="t2")]}
    client = MagicMock()
    client.query.side_effect = [page1, page2]
    client.delete_item.return_value = {}
    sp._delete_topic_rows_for_pmid(
        client, "reciterai", "12345", {"cwid_abc1234"},
    )
    assert client.query.call_count == 2
    assert client.delete_item.call_count == 2


# --- score_one_publication: --pmids gating + ordering ------------------------


def test_persist_off_by_default_skips_materialization(monkeypatch):
    monkeypatch.setattr(sp, "mark_processing", lambda *a, **k: None)
    spy = MagicMock()
    monkeypatch.setattr(sp, "_materialize_topic_rows", spy)

    result = sp.score_one_publication(
        _PUB, _FakeBedrockScoring(), _TAXONOMY, MagicMock(), "reciterai",
        _INT_TO_ID, _ID_TO_INT,
    )
    assert result.status == "complete"
    spy.assert_not_called()


def test_persist_on_invokes_materialization(monkeypatch):
    monkeypatch.setattr(sp, "mark_processing", lambda *a, **k: None)
    spy = MagicMock()
    monkeypatch.setattr(sp, "_materialize_topic_rows", spy)

    result = sp.score_one_publication(
        _PUB, _FakeBedrockScoring(), _TAXONOMY, MagicMock(), "reciterai",
        _INT_TO_ID, _ID_TO_INT,
        authors=_AUTHORS, persist_topic_rows=True,
    )
    assert result.status == "complete"
    spy.assert_called_once()
    assert spy.call_args.kwargs["pmid"] == "12345"
    assert spy.call_args.kwargs["authors"] == _AUTHORS
    assert "t1" in spy.call_args.kwargs["dense_scores"]


# --- score_one_publication: #150 item 2 synopsis-provenance stamp ------------


def _capture_mark_processing(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        sp, "mark_processing",
        lambda client, table, pmid, status, tax, **kw: calls.append((status, kw)),
    )
    return calls


def test_complete_marker_stamps_synopsis_provenance(monkeypatch):
    """The 'complete' PROCESSING# write records the synopsis provenance the
    score was based on (scored_enriched_at / scored_synopsis_model), so a later
    drift sweep can detect a regenerated synopsis."""
    calls = _capture_mark_processing(monkeypatch)
    pub = {**_PUB, "synopsis_model": "claude-sonnet-4-6",
           "enriched_at": "2026-05-20T11:00:00Z"}
    result = sp.score_one_publication(
        pub, _FakeBedrockScoring(), _TAXONOMY, MagicMock(), "reciterai",
        _INT_TO_ID, _ID_TO_INT,
    )
    assert result.status == "complete"
    complete = next(kw for status, kw in calls if status == "complete")
    assert complete["scored_enriched_at"] == "2026-05-20T11:00:00Z"
    assert complete["scored_synopsis_model"] == "claude-sonnet-4-6"


def test_complete_marker_omits_provenance_when_absent(monkeypatch):
    """A pub with no IMPACT# provenance marks complete without the stamp — the
    drift sweep treats an un-stamped score as baseline, not drifted."""
    calls = _capture_mark_processing(monkeypatch)
    result = sp.score_one_publication(
        _PUB, _FakeBedrockScoring(), _TAXONOMY, MagicMock(), "reciterai",
        _INT_TO_ID, _ID_TO_INT,
    )
    assert result.status == "complete"
    complete = next(kw for status, kw in calls if status == "complete")
    assert "scored_enriched_at" not in complete
    assert "scored_synopsis_model" not in complete


def test_topic_rows_written_before_complete_marker(monkeypatch):
    """Crash-safety: TOPIC# rows must be materialized before the
    'complete' PROCESSING# marker, so a crash between the two leaves the
    PMID re-scoreable (the checkpoint only skips 'complete' PMIDs)."""
    calls = []
    monkeypatch.setattr(
        sp, "_materialize_topic_rows",
        lambda *a, **k: calls.append("materialize"),
    )
    monkeypatch.setattr(
        sp, "mark_processing",
        lambda client, table, pmid, status, tv, **k: calls.append(f"mark:{status}"),
    )
    sp.score_one_publication(
        _PUB, _FakeBedrockScoring(), _TAXONOMY, MagicMock(), "reciterai",
        _INT_TO_ID, _ID_TO_INT,
        authors=_AUTHORS, persist_topic_rows=True,
    )
    assert "materialize" in calls
    assert "mark:complete" in calls
    assert calls.index("materialize") < calls.index("mark:complete")


def test_no_passing_topics_branch_materializes_before_marker(monkeypatch):
    """The no-relevant-topics completion path also materializes TOPIC#
    rows — with empty dense_scores, so a re-score that drops a PMID below
    threshold clears its now-stale rows — and does so before the
    'complete' PROCESSING# marker, like the scored path."""
    calls = []
    monkeypatch.setattr(
        sp, "_materialize_topic_rows",
        lambda *a, **k: calls.append(("materialize", k.get("dense_scores"))),
    )
    monkeypatch.setattr(
        sp, "mark_processing",
        lambda client, table, pmid, status, tv, **k: calls.append(("mark", status)),
    )
    monkeypatch.setattr(sp, "_maybe_write_uncovered_event", lambda *a, **k: None)

    result = sp.score_one_publication(
        _PUB, _FakeBedrockNoPass(), _TAXONOMY, MagicMock(), "reciterai",
        _INT_TO_ID, _ID_TO_INT,
        authors=_AUTHORS, persist_topic_rows=True,
    )
    assert result.status == "complete"

    materialized = [i for i, c in enumerate(calls) if c[0] == "materialize"]
    completed = [i for i, c in enumerate(calls) if c == ("mark", "complete")]
    assert materialized, "no-passing-topics branch did not materialize TOPIC# rows"
    assert completed, "no 'complete' marker was written"
    # materialized with empty dense_scores — clears stale rows on a re-score
    assert calls[materialized[0]][1] == {}
    # ...strictly before the 'complete' PROCESSING# marker (crash-safety)
    assert materialized[0] < completed[0]


# --- main(): the persist_topic_rows gate (#98) -------------------------------
#
# PR 4a gated TOPIC# materialization on --pmids; #98 widens the gate to
# args.emit_envelope so the hot path materializes rows too. These tests drive
# score_publications.main() with stubbed I/O far enough to capture the
# persist_topic_rows kwarg it hands score_batch_async, then abort before any
# real scoring runs.


class _GateProbe(Exception):
    """Raised by the score_batch_async stub once it has captured the
    persist_topic_rows gate value — aborts main() before real scoring."""


def _persist_topic_rows_for_argv(monkeypatch, argv):
    """Run score_publications.main() with the given argv and stubbed I/O;
    return the persist_topic_rows value main() passed to score_batch_async.

    extract_* are stubbed to a one-PMID work set so main() reaches
    score_batch_async rather than the 'nothing to score' short-circuit.
    """
    monkeypatch.setattr(sys, "argv", argv)

    work_set = [{"pmid": "111"}]
    fake_table = MagicMock()
    fake_table.put_item = MagicMock(return_value={})
    monkeypatch.setattr(sp, "get_table", lambda: fake_table)
    monkeypatch.setattr(sp, "load_thresholds", lambda: {
        "score_floor": 0.3,
        "target_failure_rate": 0.01,
        "uncovered_score_floor": 0.5,
    })
    monkeypatch.setattr(sp, "get_dynamo_client", lambda: MagicMock())
    monkeypatch.setattr(
        sp, "extract_publications", lambda delta_since=None: list(work_set)
    )
    monkeypatch.setattr(
        sp, "extract_publications_by_pmids", lambda pmids: list(work_set)
    )
    monkeypatch.setattr(sp, "extract_author_mapping", lambda: {})
    monkeypatch.setattr(sp, "extract_faculty_metadata", lambda: {})
    monkeypatch.setattr(
        sp, "get_unscored_publications", lambda pubs, *a, **k: list(pubs)
    )
    monkeypatch.setattr(sp, "should_skip", MagicMock(return_value=(False, None)))

    captured = {}

    async def _fake_score_batch_async(*args, **kwargs):
        captured["persist_topic_rows"] = kwargs.get("persist_topic_rows")
        raise _GateProbe

    monkeypatch.setattr(sp, "score_batch_async", _fake_score_batch_async)

    with pytest.raises(_GateProbe):
        asyncio.run(sp.main())
    return captured["persist_topic_rows"]


def test_hot_delta_run_enables_persist_topic_rows(monkeypatch):
    """The hot path's normal weekly invocation
    (`--emit-envelope --delta-since ...`) turns persist_topic_rows ON, so
    Score materializes TOPIC# rows for the delta PMIDs (#98)."""
    gate = _persist_topic_rows_for_argv(
        monkeypatch,
        ["score_publications.py", "--emit-envelope",
         "--delta-since", "2026-05-01T00:00:00Z"],
    )
    assert gate is True


def test_hot_envelope_run_without_delta_since_enables_persist_topic_rows(monkeypatch):
    """A hot Score invocation with no --delta-since — bare --emit-envelope,
    the first-ever run or a post-state-loss retry sweep — still materializes
    TOPIC# rows. Gating on args.emit_envelope rather than --delta-since is
    what closes this residual gap (#98)."""
    gate = _persist_topic_rows_for_argv(
        monkeypatch, ["score_publications.py", "--emit-envelope"],
    )
    assert gate is True


def test_onboarding_pmids_run_still_enables_persist_topic_rows(monkeypatch):
    """Regression guard: the onboarding path (`--emit-envelope --pmids ...`)
    keeps persist_topic_rows ON. #98 widened the gate — it must not narrow
    PR 4a's onboarding behavior."""
    gate = _persist_topic_rows_for_argv(
        monkeypatch,
        ["score_publications.py", "--emit-envelope", "--pmids", "111"],
    )
    assert gate is True


def test_full_cold_load_keeps_persist_topic_rows_off(monkeypatch):
    """The full cold load (no --emit-envelope AND no --pmids — score the
    whole corpus) leaves persist_topic_rows OFF: its TOPIC# rows are written
    by the cold loader (load_dynamodb) from the JSON artifact, always paired
    with that load step. PR 4a's cold-path byte-unchanged guarantee holds."""
    gate = _persist_topic_rows_for_argv(
        monkeypatch, ["score_publications.py"],
    )
    assert gate is False


def test_cli_pmids_run_enables_persist_topic_rows(monkeypatch):
    """#150 regression guard: a plain CLI `--pmids` run (no --emit-envelope)
    must persist TOPIC# rows INLINE. #98 narrowed the gate to --emit-envelope,
    which left `score_publications.py --pmids …` runs marking
    PROCESSING#=complete with no TOPIC# — the "zombie" footgun that produced
    the 2,606 complete-but-no-TOPIC# PMIDs. Restoring PR-4a's --pmids
    persistence makes targeted runs atomic (complete <=> TOPIC# persisted)."""
    gate = _persist_topic_rows_for_argv(
        monkeypatch, ["score_publications.py", "--pmids", "111"],
    )
    assert gate is True


def test_cli_pmids_force_run_enables_persist_topic_rows(monkeypatch):
    """The operator recovery shape (`--pmids … --force`, no --emit-envelope)
    also persists inline — this is exactly the invocation that must never
    again leave a zombie."""
    gate = _persist_topic_rows_for_argv(
        monkeypatch,
        ["score_publications.py", "--pmids", "111", "--force", "--allow-cost-override"],
    )
    assert gate is True

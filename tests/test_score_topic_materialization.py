"""Tests for #80 PR 4a — TOPIC# row materialization in score_publications:
the _materialize_topic_rows / _delete_topic_rows_for_pmid helpers and the
score_one_publication --pmids gating + rows-before-marker ordering."""

from __future__ import annotations

from unittest.mock import MagicMock

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

    def call_json(self, *, model, messages):
        if model == HAIKU_MODEL:
            return {"0": 0.9}
        return {"0": {"score": 0.9, "rationale": "r"}}


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

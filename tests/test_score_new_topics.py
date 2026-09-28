"""Tests for the additive new-topic scorer (cli/score_new_topics.py).

Covers the bug-prone pure logic — additive TOPIC# rows, the DDB-sourced
FACULTY# merge that must preserve every existing topic, the affected-faculty
write filter, the FacultyIndex parse, the guards, and the two-pass parse/floor
in score_one — without touching Bedrock or a live DynamoDB.
"""

from __future__ import annotations

import pytest

from cli import score_new_topics as snt

NEW = "heme_onc"
TAX = {
    "taxonomy_version": "taxonomy_v2",
    "topics": [
        {"id": "cardio", "label": "Cardio", "description": "heart"},
        {"id": "neuro", "label": "Neuro", "description": "brain"},
        {"id": NEW, "label": "Heme/Onc", "description": "blood cancer"},
    ],
}


def _faculty_meta(*cwids):
    return {c: {"name": c.upper(), "department": "Dept", "h_index": 1,
               "article_count": 1, "first_author_count": 1, "last_author_count": 0}
            for c in cwids}


# --------------------------------------------------------------------------- guards

def test_assert_topics_in_taxonomy_passes_for_present_topic():
    snt.assert_topics_in_taxonomy([NEW], TAX)  # no raise


def test_assert_topics_in_taxonomy_rejects_missing_topic():
    with pytest.raises(AssertionError, match="not in taxonomy"):
        snt.assert_topics_in_taxonomy(["nonexistent"], TAX)


def test_single_topic_view_restricts_to_targets():
    view = snt.single_topic_view(TAX, [NEW])
    assert [t["id"] for t in view["topics"]] == [NEW]
    assert view["taxonomy_version"] == "taxonomy_v2"


class _FakeQueryDDB:
    """Minimal DDB stub whose query() returns a fixed Items page (no paging)."""
    def __init__(self, items):
        self._items = items

    def query(self, **kwargs):
        return {"Items": self._items}


def test_assert_no_existing_ddb_rows_passes_when_empty():
    snt.assert_no_existing_ddb_rows(_FakeQueryDDB([]), "reciterai", [NEW])  # no raise


def test_assert_no_existing_ddb_rows_rejects_when_rows_exist():
    rows = [{"PK": {"S": f"TOPIC#{NEW}"}, "SK": {"S": "x"}}]
    with pytest.raises(AssertionError, match="already has rows"):
        snt.assert_no_existing_ddb_rows(_FakeQueryDDB(rows), "reciterai", [NEW])


# --------------------------------------------------------------------------- TOPIC#

def test_additive_topic_rows_only_for_new_topic():
    scored = {"1": {NEW: {"score": 0.8, "rationale": "r"}}}
    corpus_by_pmid = {"1": {"pmid": "1", "synopsis": "s", "title": "t"}}
    author_mapping = {"1": [{"cwid": "aaa", "position": "first"}]}
    rows = snt.build_additive_topic_rows(scored, corpus_by_pmid, author_mapping, "taxonomy_v2")
    assert len(rows) == 1
    assert rows[0]["PK"]["S"] == f"TOPIC#{NEW}"
    assert rows[0]["faculty_uid"]["S"] == "cwid_aaa"
    assert rows[0]["author_position"] == {"S": "first"}


def test_additive_topic_rows_stamp_minted_by_score_new_topics(monkeypatch):
    """#407 — rows minted by the additive path name it."""
    from utils import build_info
    monkeypatch.setenv("RECITERAI_BUILD_SHA", "cafe123")
    build_info.build_id.cache_clear()
    try:
        rows = snt.build_additive_topic_rows(
            {"1": {NEW: {"score": 0.8, "rationale": "r"}}},
            {"1": {"pmid": "1"}},
            {"1": [{"cwid": "aaa", "position": "first"}]},
            "taxonomy_v2",
        )
    finally:
        build_info.build_id.cache_clear()
    assert rows[0]["minted_by"] == {"S": "score_new_topics@cafe123"}


def test_additive_topic_rows_skip_pub_with_no_authors():
    scored = {"1": {NEW: {"score": 0.8}}}
    rows = snt.build_additive_topic_rows(scored, {"1": {}}, {"1": []}, "taxonomy_v2")
    assert rows == []


# --------------------------------------------------------- FacultyIndex parse

def test_fetch_faculty_existing_parses_topic_max_and_pmids():
    items = [
        {"PK": {"S": "TOPIC#cardio"},
         "SK": {"S": "SCORE#0900#ACTIVITY#pmid_100#cwid_aaa"}, "score": {"N": "0.9"}},
        {"PK": {"S": "TOPIC#cardio"},
         "SK": {"S": "SCORE#0700#ACTIVITY#pmid_101#cwid_aaa"}, "score": {"N": "0.7"}},
        {"PK": {"S": "TOPIC#neuro"},
         "SK": {"S": "SCORE#0600#ACTIVITY#pmid_100#cwid_aaa"}, "score": {"N": "0.6"}},
    ]
    topic_max, pmids = snt.fetch_faculty_existing(_FakeQueryDDB(items), "reciterai", "aaa")
    assert topic_max == {"cardio": 0.9, "neuro": 0.6}  # max across pubs
    assert pmids == {"100", "101"}


# --------------------------------------------------------------------------- FACULTY#

def _fetcher(existing):
    """existing: {cwid: (topic_max, pmids)} -> callable used by build_merged_faculty_rows."""
    return lambda cwid: existing.get(cwid, ({}, set()))


def test_faculty_merge_preserves_existing_topics():
    """The crux: merging the new topic must NOT drop a faculty's pre-existing
    topics, and must recompute the top-10 with the new topic joining."""
    scored = {"1": {NEW: {"score": 0.8, "rationale": "r"}}}
    author_mapping = {"1": [{"cwid": "aaa", "position": "first"}]}
    fetch = _fetcher({"aaa": ({"cardio": 0.9}, {"100"})})
    rows = snt.build_merged_faculty_rows(
        scored, author_mapping, _faculty_meta("aaa"), "taxonomy_v2", fetch
    )
    assert len(rows) == 1
    top = {m["M"]["topic_id"]["S"]: float(m["M"]["max_score"]["N"])
           for m in rows[0]["top_topics"]["L"]}
    assert top == {"cardio": 0.9, NEW: 0.8}                 # existing preserved + new
    assert rows[0]["scored_pub_count"]["N"] == "2"          # {100} ∪ {1}


def test_faculty_merge_creates_record_for_faculty_with_no_existing_rows():
    """A faculty whose only scored pub is the new-topic one (no existing
    TOPIC# rows) still yields a FACULTY# record."""
    scored = {"2": {NEW: {"score": 0.95, "rationale": "r"}}}
    author_mapping = {"2": [{"cwid": "bbb", "position": "last"}]}
    fetch = _fetcher({})  # bbb has nothing yet
    rows = snt.build_merged_faculty_rows(
        scored, author_mapping, _faculty_meta("bbb"), "taxonomy_v2", fetch
    )
    assert len(rows) == 1
    assert rows[0]["PK"]["S"] == "FACULTY#cwid_bbb"
    assert rows[0]["scored_pub_count"]["N"] == "1"


def test_faculty_merge_skips_faculty_without_metadata():
    """No faculty_metadata entry → no FACULTY# record (matches the canonical
    builder); the additive TOPIC# rows are still written elsewhere."""
    scored = {"1": {NEW: {"score": 0.8}}}
    author_mapping = {"1": [{"cwid": "ghost", "position": "first"}]}
    rows = snt.build_merged_faculty_rows(
        scored, author_mapping, _faculty_meta(), "taxonomy_v2", _fetcher({})
    )
    assert rows == []


def test_faculty_merge_only_emits_affected_faculty():
    """Only faculty who co-authored a qualifying new-topic pub are emitted."""
    scored = {"1": {NEW: {"score": 0.8}}}
    author_mapping = {
        "1": [{"cwid": "aaa", "position": "first"}],
        "3": [{"cwid": "ccc", "position": "first"}],  # ccc not in scored → untouched
    }
    fetch = _fetcher({"aaa": ({"cardio": 0.9}, {"100"})})
    rows = snt.build_merged_faculty_rows(
        scored, author_mapping, _faculty_meta("aaa", "ccc"), "taxonomy_v2", fetch
    )
    assert {r["PK"]["S"] for r in rows} == {"FACULTY#cwid_aaa"}


# --------------------------------------------------------------------------- score_one

class _FakeBedrock:
    def __init__(self, screening):
        self._screening = screening

    def call_json(self, model, messages):
        return self._screening


def test_score_one_below_floor_skips_dense(monkeypatch):
    view = snt.single_topic_view(TAX, [NEW])
    int_to_id, id_to_int = snt.sp.build_topic_index(view)
    called = {"dense": False}

    def _no_dense(*a, **k):
        called["dense"] = True
        return {}, None
    monkeypatch.setattr(snt.sp, "_dense_score", _no_dense)

    _screening, dense = snt.score_one(
        {"pmid": "1", "synopsis": "x", "abstract": ""},
        _FakeBedrock({"0": 0.1}), view, int_to_id, id_to_int,
    )
    assert dense == {}
    assert called["dense"] is False  # never enters the expensive Sonnet pass


def test_score_one_above_floor_returns_dense(monkeypatch):
    view = snt.single_topic_view(TAX, [NEW])
    int_to_id, id_to_int = snt.sp.build_topic_index(view)
    monkeypatch.setattr(
        snt.sp, "_dense_score",
        lambda *a, **k: ({"0": {"score": 0.7, "rationale": "r"}}, None),
    )
    _screening, dense = snt.score_one(
        {"pmid": "1", "synopsis": "x", "abstract": ""},
        _FakeBedrock({"0": 0.6}), view, int_to_id, id_to_int,
    )
    assert dense == {NEW: {"score": 0.7, "rationale": "r"}}

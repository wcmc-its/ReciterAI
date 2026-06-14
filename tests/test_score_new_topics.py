"""Tests for the additive new-topic scorer (cli/score_new_topics.py).

Covers the bug-prone pure logic — additive TOPIC# rows, the FACULTY# merge
that must preserve every existing topic, the affected-faculty write filter,
the genuinely-new guards, and the two-pass parse/floor in score_one — without
touching Bedrock or DynamoDB.
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


# --------------------------------------------------------------------------- guards

def test_assert_topics_are_new_passes_for_unscored_topic():
    scoring_results = [{"pmid": "1", "dense_scores": {"cardio": {"score": 0.9}}}]
    snt.assert_topics_are_new([NEW], TAX, scoring_results)  # no raise


def test_assert_topics_are_new_rejects_topic_missing_from_taxonomy():
    with pytest.raises(AssertionError, match="not in taxonomy"):
        snt.assert_topics_are_new(["nonexistent"], TAX, [])


def test_assert_topics_are_new_rejects_already_scored_topic():
    scoring_results = [{"pmid": "1", "dense_scores": {NEW: {"score": 0.7}}}]
    with pytest.raises(AssertionError, match="already have scores"):
        snt.assert_topics_are_new([NEW], TAX, scoring_results)


def test_single_topic_view_restricts_to_targets():
    view = snt.single_topic_view(TAX, [NEW])
    assert [t["id"] for t in view["topics"]] == [NEW]
    assert view["taxonomy_version"] == "taxonomy_v2"


# --------------------------------------------------------------------------- TOPIC#

def test_additive_topic_rows_only_for_new_topic():
    scored = {"1": {NEW: {"score": 0.8, "rationale": "r"}}}
    corpus_by_pmid = {"1": {"pmid": "1", "synopsis": "s", "title": "t"}}
    author_mapping = {"1": [{"cwid": "aaa", "position": "first"}]}
    rows = snt.build_additive_topic_rows(scored, corpus_by_pmid, author_mapping, "taxonomy_v2")
    assert len(rows) == 1
    assert rows[0]["PK"]["S"] == f"TOPIC#{NEW}"
    assert rows[0]["faculty_uid"]["S"] == "cwid_aaa"


def test_additive_topic_rows_skip_pub_with_no_authors():
    scored = {"1": {NEW: {"score": 0.8}}}
    rows = snt.build_additive_topic_rows(scored, {"1": {}}, {"1": []}, "taxonomy_v2")
    assert rows == []


# --------------------------------------------------------------------------- FACULTY#

def _faculty_meta(*cwids):
    return {c: {"name": c.upper(), "department": "Dept", "h_index": 1,
               "article_count": 1, "first_author_count": 1, "last_author_count": 0}
            for c in cwids}


def test_faculty_merge_preserves_existing_topics():
    """The crux: rebuilding FACULTY# after the merge must NOT drop a faculty's
    pre-existing topics — the new topic just joins the top-10 contest."""
    scoring_results = [{"pmid": "1", "dense_scores": {"cardio": {"score": 0.9}}}]
    scored = {"1": {NEW: {"score": 0.8, "rationale": "r"}}}
    author_mapping = {"1": [{"cwid": "aaa", "position": "first"}]}
    rows = snt.build_merged_faculty_rows(
        scored, scoring_results, author_mapping, _faculty_meta("aaa"), "taxonomy_v2"
    )
    assert len(rows) == 1
    top = {m["M"]["topic_id"]["S"]: float(m["M"]["max_score"]["N"])
           for m in rows[0]["top_topics"]["L"]}
    assert top == {"cardio": 0.9, NEW: 0.8}  # both present


def test_faculty_merge_creates_record_for_pub_absent_from_scoring_results():
    """A pure new-topic paper missing from scoring_results.json still yields a
    FACULTY# record for its author."""
    scoring_results = []  # pmid 2 never scored on any old topic
    scored = {"2": {NEW: {"score": 0.95, "rationale": "r"}}}
    author_mapping = {"2": [{"cwid": "bbb", "position": "last"}]}
    rows = snt.build_merged_faculty_rows(
        scored, scoring_results, author_mapping, _faculty_meta("bbb"), "taxonomy_v2"
    )
    assert len(rows) == 1
    assert rows[0]["PK"]["S"] == "FACULTY#cwid_bbb"
    assert rows[0]["scored_pub_count"]["N"] == "1"


def test_faculty_merge_filters_out_unaffected_faculty():
    """Faculty with no qualifying new-topic pub must not be rewritten."""
    scoring_results = [
        {"pmid": "1", "dense_scores": {"cardio": {"score": 0.9}}},   # aaa, affected
        {"pmid": "3", "dense_scores": {"neuro": {"score": 0.7}}},    # ccc, untouched
    ]
    scored = {"1": {NEW: {"score": 0.8}}}
    author_mapping = {
        "1": [{"cwid": "aaa", "position": "first"}],
        "3": [{"cwid": "ccc", "position": "first"}],
    }
    rows = snt.build_merged_faculty_rows(
        scored, scoring_results, author_mapping, _faculty_meta("aaa", "ccc"), "taxonomy_v2"
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

    screening, dense = snt.score_one(
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
    screening, dense = snt.score_one(
        {"pmid": "1", "synopsis": "x", "abstract": ""},
        _FakeBedrock({"0": 0.6}), view, int_to_id, id_to_int,
    )
    assert dense == {NEW: {"score": 0.7, "rationale": "r"}}

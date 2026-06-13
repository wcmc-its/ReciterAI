"""Tests for the shared TOPIC# DynamoDB row builders (#80 PR 4a):
utils.topic_records.build_topic_rows_for_pmid and load_dynamodb's
cold-path wrapper build_topic_records that delegates to it."""

from __future__ import annotations

from cli import load_dynamodb
from utils.dynamodb_helpers import make_score_sk
from utils.topic_records import build_topic_rows_for_pmid

_AUTHORS = [{"cwid": "abc1234", "position": "first"}]


def test_topic_below_min_score_is_dropped():
    rows = build_topic_rows_for_pmid(
        pmid="111",
        dense_scores={"cardio": {"score": 0.9}, "neuro": {"score": 0.2}},
        authors=_AUTHORS,
        taxonomy_version="taxonomy_v2",
        min_score=0.3,
    )
    assert {r["PK"]["S"] for r in rows} == {"TOPIC#cardio"}


def test_one_row_per_qualifying_topic_per_author():
    rows = build_topic_rows_for_pmid(
        pmid="111",
        dense_scores={"cardio": {"score": 0.9}, "neuro": {"score": 0.8}},
        authors=[
            {"cwid": "abc1234", "position": "first"},
            {"cwid": "xyz9999", "position": "last"},
        ],
        taxonomy_version="taxonomy_v2",
        min_score=0.3,
    )
    assert len(rows) == 4
    assert {(r["PK"]["S"], r["faculty_uid"]["S"]) for r in rows} == {
        ("TOPIC#cardio", "cwid_abc1234"),
        ("TOPIC#cardio", "cwid_xyz9999"),
        ("TOPIC#neuro", "cwid_abc1234"),
        ("TOPIC#neuro", "cwid_xyz9999"),
    }


def test_sk_format_and_base_attributes():
    rows = build_topic_rows_for_pmid(
        pmid="12345",
        dense_scores={"cardio": {"score": 0.85, "rationale": "RCT on PAD"}},
        authors=_AUTHORS,
        taxonomy_version="taxonomy_v2",
        min_score=0.3,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["PK"] == {"S": "TOPIC#cardio"}
    # SK derived via make_score_sk so the test does not pin float-rounding.
    assert row["SK"] == {"S": f"{make_score_sk(0.85, '12345')}#cwid_abc1234"}
    assert row["faculty_uid"] == {"S": "cwid_abc1234"}
    assert row["score"] == {"N": "0.85"}
    assert row["rationale"] == {"S": "RCT on PAD"}
    assert row["topic_scores_version"] == {"S": "taxonomy_v2"}
    assert row["pmid"] == {"S": "12345"}


def test_synopsis_and_title_written_when_provided():
    rows = build_topic_rows_for_pmid(
        pmid="111",
        dense_scores={"cardio": {"score": 0.9}},
        authors=_AUTHORS,
        taxonomy_version="taxonomy_v2",
        min_score=0.3,
        synopsis="A study of X.",
        title="Study of X",
    )
    assert rows[0]["synopsis"] == {"S": "A study of X."}
    assert rows[0]["title"] == {"S": "Study of X"}


def test_synopsis_and_title_omitted_when_absent():
    """Cold-path parity: load_dynamodb passes neither, so the row keeps
    its historical 7-attribute shape."""
    rows = build_topic_rows_for_pmid(
        pmid="111",
        dense_scores={"cardio": {"score": 0.9}},
        authors=_AUTHORS,
        taxonomy_version="taxonomy_v2",
        min_score=0.3,
    )
    assert set(rows[0]) == {
        "PK", "SK", "faculty_uid", "score",
        "rationale", "topic_scores_version", "pmid",
    }


def test_plain_float_dense_score_supported():
    rows = build_topic_rows_for_pmid(
        pmid="111",
        dense_scores={"cardio": 0.9},
        authors=_AUTHORS,
        taxonomy_version="taxonomy_v2",
        min_score=0.3,
    )
    assert len(rows) == 1
    assert rows[0]["score"] == {"N": "0.9"}
    assert rows[0]["rationale"] == {"S": ""}


def test_no_authors_yields_no_rows():
    assert build_topic_rows_for_pmid(
        pmid="111",
        dense_scores={"cardio": {"score": 0.9}},
        authors=[],
        taxonomy_version="taxonomy_v2",
        min_score=0.3,
    ) == []


def test_empty_dense_scores_yields_no_rows():
    assert build_topic_rows_for_pmid(
        pmid="111",
        dense_scores={},
        authors=_AUTHORS,
        taxonomy_version="taxonomy_v2",
        min_score=0.3,
    ) == []


def test_duplicate_author_deduped():
    rows = build_topic_rows_for_pmid(
        pmid="111",
        dense_scores={"cardio": {"score": 0.9}},
        authors=[
            {"cwid": "abc1234", "position": "first"},
            {"cwid": "abc1234", "position": "middle"},
        ],
        taxonomy_version="taxonomy_v2",
        min_score=0.3,
    )
    assert len(rows) == 1


# --- build_topic_records: load_dynamodb's cold-path wrapper over the builder ---


def test_build_topic_records_aggregates_pmid_rows():
    """The cold-path wrapper aggregates per-PMID rows and keeps the
    historical 7-attribute row shape (no synopsis/title)."""
    scoring_results = [
        {"pmid": "111", "dense_scores": {"cardio": {"score": 0.9}}},
        {"pmid": "222", "dense_scores": {"neuro": {"score": 0.8, "rationale": "r"}}},
    ]
    author_mapping = {
        "111": [{"cwid": "abc1234", "position": "first"}],
        "222": [
            {"cwid": "abc1234", "position": "first"},
            {"cwid": "xyz9999", "position": "last"},
        ],
    }
    records = load_dynamodb.build_topic_records(
        scoring_results, author_mapping, "taxonomy_v2", min_score=0.3
    )
    # pmid 111: 1 topic x 1 author; pmid 222: 1 topic x 2 authors
    assert len(records) == 3
    for row in records:
        assert set(row) == {
            "PK", "SK", "faculty_uid", "score",
            "rationale", "topic_scores_version", "pmid",
        }


def test_build_topic_records_skips_pubs_without_faculty_authors():
    """A publication absent from author_mapping contributes no rows."""
    scoring_results = [
        {"pmid": "111", "dense_scores": {"cardio": {"score": 0.9}}},
        {"pmid": "999", "dense_scores": {"cardio": {"score": 0.9}}},
    ]
    author_mapping = {"111": [{"cwid": "abc1234", "position": "first"}]}
    records = load_dynamodb.build_topic_records(
        scoring_results, author_mapping, "taxonomy_v2", min_score=0.3
    )
    assert len(records) == 1
    assert records[0]["pmid"] == {"S": "111"}


# --- #212 Part A: build-time impact_score join -----------------------------


def test_impact_score_and_justification_written_when_provided():
    rows = build_topic_rows_for_pmid(
        pmid="111",
        dense_scores={"cardio": {"score": 0.9}},
        authors=_AUTHORS,
        taxonomy_version="taxonomy_v2",
        min_score=0.3,
        impact_score="55",
        impact_justification="cited widely",
    )
    assert rows[0]["impact_score"] == {"N": "55"}
    assert rows[0]["impact_justification"] == {"S": "cited widely"}


def test_impact_score_accepts_int_and_float():
    for val, expected in ((72, "72"), (88.0, "88.0")):
        rows = build_topic_rows_for_pmid(
            pmid="111",
            dense_scores={"cardio": {"score": 0.9}},
            authors=_AUTHORS,
            taxonomy_version="taxonomy_v2",
            min_score=0.3,
            impact_score=val,
        )
        assert rows[0]["impact_score"] == {"N": expected}


def test_impact_justification_omitted_when_blank_even_if_score_present():
    """Mirrors the stopgap backfill: justification only when present."""
    rows = build_topic_rows_for_pmid(
        pmid="111",
        dense_scores={"cardio": {"score": 0.9}},
        authors=_AUTHORS,
        taxonomy_version="taxonomy_v2",
        min_score=0.3,
        impact_score=72,
        impact_justification="",
    )
    assert rows[0]["impact_score"] == {"N": "72"}
    assert "impact_justification" not in rows[0]


def test_impact_keys_omitted_when_score_absent_preserves_historical_shape():
    """TOPIC#-before-enrichment ordering: no impact yet -> historical
    7-attribute shape, Part B back-propagates later."""
    rows = build_topic_rows_for_pmid(
        pmid="111",
        dense_scores={"cardio": {"score": 0.9}},
        authors=_AUTHORS,
        taxonomy_version="taxonomy_v2",
        min_score=0.3,
        impact_score=None,
        impact_justification="ignored when score is None",
    )
    assert "impact_score" not in rows[0]
    assert "impact_justification" not in rows[0]
    assert set(rows[0]) == {
        "PK", "SK", "faculty_uid", "score",
        "rationale", "topic_scores_version", "pmid",
    }


def test_impact_score_blank_string_treated_as_absent():
    rows = build_topic_rows_for_pmid(
        pmid="111",
        dense_scores={"cardio": {"score": 0.9}},
        authors=_AUTHORS,
        taxonomy_version="taxonomy_v2",
        min_score=0.3,
        impact_score="   ",
        impact_justification="x",
    )
    assert "impact_score" not in rows[0]
    assert "impact_justification" not in rows[0]


def test_build_topic_records_joins_impact_from_scoring_results():
    """Cold path: serialize_results stamps impact onto scoring_results.json
    and build_topic_records joins it onto the TOPIC# rows."""
    scoring_results = [
        {
            "pmid": "111",
            "dense_scores": {"cardio": {"score": 0.9}},
            "impact_score": "88",
            "impact_justification": "j",
        },
    ]
    author_mapping = {"111": [{"cwid": "abc1234", "position": "first"}]}
    records = load_dynamodb.build_topic_records(
        scoring_results, author_mapping, "taxonomy_v2", min_score=0.3
    )
    assert records[0]["impact_score"] == {"N": "88"}
    assert records[0]["impact_justification"] == {"S": "j"}

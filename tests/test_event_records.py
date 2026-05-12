"""Tests for utils.event_records — Phase 10 T6 substrate.

Covers:
- UNCOVERED_PMID# record shape, top-3 cap, sort order, Decimal coercion
- LOW_CONFIDENCE_ASSIGNMENT# record shape, max-confidence derivation
- load_thresholds happy path and missing-file behavior
- idempotency: PK constant per PMID + SK constant means rerun overwrites
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from utils import event_records as er


# ---------- load_thresholds ----------


def test_load_thresholds_default_path():
    """The repo's config/thresholds.json must exist and parse."""
    t = er.load_thresholds()
    assert t["uncovered_score_floor"] == 0.4
    assert t["low_confidence_floor"] == 0.35
    assert t["drift_window_days"] == 14


def test_load_thresholds_custom_path(tmp_path: Path):
    p = tmp_path / "thresholds.json"
    p.write_text(json.dumps({"uncovered_score_floor": 0.55}))
    t = er.load_thresholds(p)
    assert t == {"uncovered_score_floor": 0.55}


def test_load_thresholds_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        er.load_thresholds(tmp_path / "nope.json")


# ---------- UNCOVERED_PMID# ----------


def test_uncovered_pmid_record_shape():
    record = er.build_uncovered_pmid_record(
        pmid="12345",
        taxonomy_version="taxonomy_v2",
        top_topics=[("cardio", 0.32), ("neuro", 0.28), ("onco", 0.15)],
        created_at="2026-05-12T12:00:00Z",
    )
    assert record["PK"] == "UNCOVERED_PMID#12345"
    assert record["SK"] == "GLOBAL"
    assert record["record_type"] == "UNCOVERED_PMID"
    assert record["pmid"] == "12345"
    assert record["taxonomy_version"] == "taxonomy_v2"
    assert record["top_topic_score"] == Decimal("0.32")
    assert record["source_stage"] == "score_publications"
    assert record["created_at"] == "2026-05-12T12:00:00Z"


def test_uncovered_pmid_caps_at_top_3_and_sorts_desc():
    record = er.build_uncovered_pmid_record(
        pmid="x",
        taxonomy_version="v",
        top_topics=[
            ("a", 0.1), ("b", 0.3), ("c", 0.2), ("d", 0.05), ("e", 0.4),
        ],
    )
    ids = [t["topic_id"] for t in record["top_topics"]]
    scores = [t["score"] for t in record["top_topics"]]
    assert ids == ["e", "b", "c"]
    assert scores == [Decimal("0.4"), Decimal("0.3"), Decimal("0.2")]


def test_uncovered_pmid_handles_empty_top_topics():
    record = er.build_uncovered_pmid_record(
        pmid="x", taxonomy_version="v", top_topics=[]
    )
    assert record["top_topic_score"] == Decimal("0")
    assert record["top_topics"] == []


def test_uncovered_pmid_idempotent_pk_per_pmid():
    """PK+SK must be constant per PMID so a rerun overwrites in place."""
    a = er.build_uncovered_pmid_record(
        pmid="x", taxonomy_version="v", top_topics=[("a", 0.1)],
        created_at="2026-05-01T00:00:00Z",
    )
    b = er.build_uncovered_pmid_record(
        pmid="x", taxonomy_version="v", top_topics=[("a", 0.1)],
        created_at="2026-05-12T00:00:00Z",
    )
    assert a["PK"] == b["PK"]
    assert a["SK"] == b["SK"]


def test_uncovered_pmid_writer_calls_put_item():
    table = MagicMock()
    er.write_uncovered_pmid(
        table,
        pmid="x",
        taxonomy_version="v",
        top_topics=[("a", 0.1)],
        created_at="2026-05-12T00:00:00Z",
    )
    table.put_item.assert_called_once()
    item = table.put_item.call_args.kwargs["Item"]
    assert item["PK"] == "UNCOVERED_PMID#x"
    # Numerics must be Decimal (DynamoDB rule).
    assert isinstance(item["top_topic_score"], Decimal)


# ---------- LOW_CONFIDENCE_ASSIGNMENT# ----------


def test_low_confidence_record_shape():
    record = er.build_low_confidence_assignment_record(
        pmid="55555",
        topic_id="cardiovascular_disease",
        candidate_confidences={"afib": 0.22, "stroke": 0.18, "hf": 0.30},
        created_at="2026-05-12T12:00:00Z",
    )
    assert record["PK"] == "LOW_CONFIDENCE_ASSIGNMENT#55555"
    assert record["SK"] == "GLOBAL"
    assert record["record_type"] == "LOW_CONFIDENCE_ASSIGNMENT"
    assert record["pmid"] == "55555"
    assert record["topic_id"] == "cardiovascular_disease"
    assert record["max_confidence"] == Decimal("0.30")
    assert record["source_stage"] == "assign_subtopics"
    # All confidences coerced to Decimal
    assert all(
        isinstance(v, Decimal) for v in record["candidate_confidences"].values()
    )


def test_low_confidence_empty_candidates():
    record = er.build_low_confidence_assignment_record(
        pmid="x", topic_id="t", candidate_confidences={}
    )
    assert record["max_confidence"] == Decimal("0")
    assert record["candidate_confidences"] == {}


def test_low_confidence_writer_calls_put_item():
    table = MagicMock()
    er.write_low_confidence_assignment(
        table,
        pmid="x",
        topic_id="t",
        candidate_confidences={"s1": 0.2, "s2": 0.1},
        created_at="2026-05-12T00:00:00Z",
    )
    table.put_item.assert_called_once()
    item = table.put_item.call_args.kwargs["Item"]
    assert item["PK"] == "LOW_CONFIDENCE_ASSIGNMENT#x"
    assert item["max_confidence"] == Decimal("0.2")

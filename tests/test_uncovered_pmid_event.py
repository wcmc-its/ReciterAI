"""Phase 10 T6 — UNCOVERED_PMID# triggered from score_publications.

Verifies that synthetic Bedrock outputs whose top topic score is below the
configured floor produce an UNCOVERED_PMID# row, and that ones above the
floor do not.

Covers both completion paths in score_one_publication:
1. No topics passed the 0.3 screening threshold (early return).
2. Some topics passed; dense scoring still produces a top score below the
   uncovered floor.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

import score_publications as sp


TAXONOMY = {
    "taxonomy_version": "taxonomy_v2",
    "topics": [
        {"id": "cardio", "label": "Cardio", "description": "..."},
        {"id": "neuro",  "label": "Neuro",  "description": "..."},
        {"id": "onco",   "label": "Onco",   "description": "..."},
    ],
}


def _int_id_maps():
    int_to_id = {"0": "cardio", "1": "neuro", "2": "onco"}
    id_to_int = {v: k for k, v in int_to_id.items()}
    return int_to_id, id_to_int


class _FakeBedrock:
    """Returns predetermined JSON for screening + dense calls."""

    def __init__(self, screening, dense=None):
        self._screening = screening
        self._dense = dense or {}
        self.calls = 0

    def call_json(self, *, model, messages, **kwargs):
        self.calls += 1
        # Pass 1 is the screening call (no nested dicts).
        if self.calls == 1:
            return self._screening
        return self._dense


def _captured_uncovered(captured):
    return [
        item for item in captured
        if str(item.get("PK", "")).startswith("UNCOVERED_PMID#")
    ]


@pytest.fixture
def stage_table_capture():
    captured: list = []
    table = MagicMock()
    table.put_item.side_effect = lambda Item: captured.append(Item) or {}
    return table, captured


def test_uncovered_event_written_when_no_topics_pass_screening(
    stage_table_capture, monkeypatch
):
    """Early-return path: max screening score is below 0.3 — and well below the
    0.4 uncovered floor, so an event must be written."""
    monkeypatch.setattr(sp, "mark_processing", lambda *a, **k: None)
    table, captured = stage_table_capture
    int_to_id, id_to_int = _int_id_maps()

    bedrock = _FakeBedrock(screening={"0": 0.20, "1": 0.10, "2": 0.05})
    pub = {"pmid": "12345", "synopsis": "s", "abstract": "a"}

    result = sp.score_one_publication(
        pub, bedrock, TAXONOMY, MagicMock(), "reciterai",
        int_to_id, id_to_int,
        stage_table=table,
        thresholds={"uncovered_score_floor": 0.4},
    )

    assert result.status == "complete"
    uncov = _captured_uncovered(captured)
    assert len(uncov) == 1
    row = uncov[0]
    assert row["PK"] == "UNCOVERED_PMID#12345"
    # Top topic was cardio at 0.20 — top-3 in descending order.
    assert row["top_topic_score"] == Decimal("0.2")
    top_ids = [t["topic_id"] for t in row["top_topics"]]
    assert top_ids == ["cardio", "neuro", "onco"]


def test_uncovered_event_written_when_dense_top_below_floor(
    stage_table_capture, monkeypatch
):
    """Topics passed screening but Sonnet refined them all below 0.4."""
    monkeypatch.setattr(sp, "mark_processing", lambda *a, **k: None)
    table, captured = stage_table_capture
    int_to_id, id_to_int = _int_id_maps()

    bedrock = _FakeBedrock(
        screening={"0": 0.55, "1": 0.40},  # both pass 0.3 floor
        dense={
            "0": {"score": 0.30, "rationale": "tangential"},
            "1": {"score": 0.25, "rationale": "weak"},
        },
    )
    pub = {"pmid": "77777", "synopsis": "s", "abstract": "a"}
    result = sp.score_one_publication(
        pub, bedrock, TAXONOMY, MagicMock(), "reciterai",
        int_to_id, id_to_int,
        stage_table=table,
        thresholds={"uncovered_score_floor": 0.4},
    )

    assert result.status == "complete"
    uncov = _captured_uncovered(captured)
    assert len(uncov) == 1
    row = uncov[0]
    assert row["PK"] == "UNCOVERED_PMID#77777"
    assert row["top_topic_score"] == Decimal("0.3")


def test_no_event_when_dense_top_above_floor(stage_table_capture, monkeypatch):
    monkeypatch.setattr(sp, "mark_processing", lambda *a, **k: None)
    table, captured = stage_table_capture
    int_to_id, id_to_int = _int_id_maps()

    bedrock = _FakeBedrock(
        screening={"0": 0.65, "1": 0.40},
        dense={
            "0": {"score": 0.85, "rationale": "primary"},
            "1": {"score": 0.30, "rationale": "secondary"},
        },
    )
    pub = {"pmid": "88888", "synopsis": "s", "abstract": "a"}
    sp.score_one_publication(
        pub, bedrock, TAXONOMY, MagicMock(), "reciterai",
        int_to_id, id_to_int,
        stage_table=table,
        thresholds={"uncovered_score_floor": 0.4},
    )
    assert _captured_uncovered(captured) == []


def test_no_event_when_thresholds_none(stage_table_capture, monkeypatch):
    """thresholds=None must short-circuit the event write (legacy callers)."""
    monkeypatch.setattr(sp, "mark_processing", lambda *a, **k: None)
    table, captured = stage_table_capture
    int_to_id, id_to_int = _int_id_maps()
    bedrock = _FakeBedrock(screening={"0": 0.05, "1": 0.05, "2": 0.05})
    pub = {"pmid": "00000", "synopsis": "s", "abstract": "a"}
    sp.score_one_publication(
        pub, bedrock, TAXONOMY, MagicMock(), "reciterai",
        int_to_id, id_to_int,
        stage_table=table,
        thresholds=None,
    )
    assert _captured_uncovered(captured) == []


def test_no_event_when_stage_table_none(monkeypatch):
    """stage_table=None must short-circuit the event write."""
    monkeypatch.setattr(sp, "mark_processing", lambda *a, **k: None)
    int_to_id, id_to_int = _int_id_maps()
    bedrock = _FakeBedrock(screening={"0": 0.05, "1": 0.05, "2": 0.05})
    pub = {"pmid": "00001", "synopsis": "s", "abstract": "a"}
    sp.score_one_publication(
        pub, bedrock, TAXONOMY, MagicMock(), "reciterai",
        int_to_id, id_to_int,
        stage_table=None,
        thresholds={"uncovered_score_floor": 0.4},
    )


# ---------- pure helper ----------


def test_evaluate_uncovered_prefers_dense_over_screening():
    top_score, top3 = sp._evaluate_uncovered(
        screening_scores={"cardio": 0.9, "neuro": 0.8},
        dense_scores={"cardio": {"score": 0.3}, "neuro": {"score": 0.25}},
    )
    assert top_score == 0.3
    assert [t[0] for t in top3] == ["cardio", "neuro"]


def test_evaluate_uncovered_falls_back_to_screening_when_dense_empty():
    top_score, top3 = sp._evaluate_uncovered(
        screening_scores={"cardio": 0.22, "neuro": 0.18},
        dense_scores={},
    )
    assert top_score == 0.22
    assert [t[0] for t in top3] == ["cardio", "neuro"]


def test_evaluate_uncovered_returns_zero_when_both_empty():
    assert sp._evaluate_uncovered({}, {}) == (0.0, [])

"""Phase 10 T6 — LOW_CONFIDENCE_ASSIGNMENT# triggered from assign_subtopics.

Verifies that synthetic Bedrock outputs with all valid candidate confidences
below the floor produce a LOW_CONFIDENCE_ASSIGNMENT# row, and that ones with
any candidate at/above the floor do not.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

import assign_subtopics as ast_mod


HIERARCHY_DRAFT = {
    "topic_id": "cardiovascular_disease",
    "subtopics": [
        {"id": "afib",   "label": "AFib",   "description": "..."},
        {"id": "stroke", "label": "Stroke", "description": "..."},
        {"id": "hf",     "label": "HF",     "description": "..."},
    ],
    "review_status": "approved",
}


def _captured_low_conf(captured):
    return [
        item for item in captured
        if str(item.get("PK", "")).startswith("LOW_CONFIDENCE_ASSIGNMENT#")
    ]


def _make_kwargs(*, stage_table, raw_assignments, thresholds, monkeypatch):
    """Patch _classify_activity to return the given assignments and build the
    kwargs dict for _process_pmid."""
    monkeypatch.setattr(
        ast_mod, "_classify_activity",
        lambda **kwargs: (raw_assignments, {"inputTokens": 1, "outputTokens": 1}),
    )
    return dict(
        pmid="42",
        group={
            "activity": {"pmid": "42", "title": "t", "synopsis": "s"},
            "rows": [],
            "has_primary": False,
        },
        client=MagicMock(),
        topic_meta={"id": "cardiovascular_disease", "label": "Cardio"},
        subtopic_defs=[
            {"id": s["id"], "label": s["label"], "description": ""}
            for s in HIERARCHY_DRAFT["subtopics"]
        ],
        valid_subtopic_ids={s["id"] for s in HIERARCHY_DRAFT["subtopics"]},
        hierarchy_draft=HIERARCHY_DRAFT,
        confidence_floor=0.3,
        dry_run=True,  # keep update_activity_subtopics off
        stage_table=stage_table,
        thresholds=thresholds,
    )


@pytest.fixture
def stage_table_capture():
    captured: list = []
    table = MagicMock()
    table.put_item.side_effect = lambda Item: captured.append(Item) or {}
    return table, captured


def test_event_written_when_all_candidate_confidences_below_floor(
    stage_table_capture, monkeypatch
):
    table, captured = stage_table_capture
    # All three candidates are below the 0.35 low_confidence_floor.
    raw = [
        {"subtopic_id": "afib",   "confidence": 0.22},
        {"subtopic_id": "stroke", "confidence": 0.18},
        {"subtopic_id": "hf",     "confidence": 0.30},
    ]
    kwargs = _make_kwargs(
        stage_table=table,
        raw_assignments=raw,
        thresholds={"low_confidence_floor": 0.35},
        monkeypatch=monkeypatch,
    )

    ast_mod._process_pmid(**kwargs)

    rows = _captured_low_conf(captured)
    assert len(rows) == 1
    row = rows[0]
    assert row["PK"] == "LOW_CONFIDENCE_ASSIGNMENT#42"
    assert row["topic_id"] == "cardiovascular_disease"
    assert row["max_confidence"] == Decimal("0.30")
    # All three candidates captured
    assert set(row["candidate_confidences"].keys()) == {"afib", "stroke", "hf"}


def test_no_event_when_any_candidate_at_or_above_floor(
    stage_table_capture, monkeypatch
):
    table, captured = stage_table_capture
    raw = [
        {"subtopic_id": "afib",   "confidence": 0.22},
        {"subtopic_id": "stroke", "confidence": 0.42},  # above 0.35 floor
        {"subtopic_id": "hf",     "confidence": 0.10},
    ]
    kwargs = _make_kwargs(
        stage_table=table,
        raw_assignments=raw,
        thresholds={"low_confidence_floor": 0.35},
        monkeypatch=monkeypatch,
    )
    ast_mod._process_pmid(**kwargs)
    assert _captured_low_conf(captured) == []


def test_no_event_when_no_valid_candidates(stage_table_capture, monkeypatch):
    """Empty/invalid candidates is a model issue, not a confidence signal."""
    table, captured = stage_table_capture
    raw = [
        {"subtopic_id": "not_in_hierarchy", "confidence": 0.1},
    ]
    kwargs = _make_kwargs(
        stage_table=table,
        raw_assignments=raw,
        thresholds={"low_confidence_floor": 0.35},
        monkeypatch=monkeypatch,
    )
    ast_mod._process_pmid(**kwargs)
    assert _captured_low_conf(captured) == []


def test_no_event_when_thresholds_none(stage_table_capture, monkeypatch):
    table, captured = stage_table_capture
    raw = [{"subtopic_id": "afib", "confidence": 0.1}]
    kwargs = _make_kwargs(
        stage_table=table,
        raw_assignments=raw,
        thresholds=None,
        monkeypatch=monkeypatch,
    )
    ast_mod._process_pmid(**kwargs)
    assert _captured_low_conf(captured) == []


def test_no_event_when_stage_table_none(monkeypatch):
    """stage_table=None must short-circuit cleanly without raising."""
    raw = [{"subtopic_id": "afib", "confidence": 0.1}]
    kwargs = _make_kwargs(
        stage_table=None,
        raw_assignments=raw,
        thresholds={"low_confidence_floor": 0.35},
        monkeypatch=monkeypatch,
    )
    result = ast_mod._process_pmid(**kwargs)
    # Should not crash; the unassigned-status path handles "no confidences"
    assert result["status"] in ("ok", "partial")

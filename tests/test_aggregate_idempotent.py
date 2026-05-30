"""Tests for aggregate_subtopic_scores — Phase 12 D-14 idempotent DDB writes.

Covers:
- Re-run with same input rows + same run_id produces identical put_item args (D-14)
- Re-run with different input rows produces updated put_item args (partition-level overwrite)
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

import cli.aggregate_subtopic_scores as agg


def _make_row(
    *,
    faculty_uid: str,
    primary_subtopic_id: str,
    subtopic_ids: list[str],
    score: float = 0.8,
    impact_score: float = 50.0,
) -> dict:
    return {
        "faculty_uid": f"cwid_{faculty_uid}",
        "primary_subtopic_id": primary_subtopic_id,
        "subtopic_ids": subtopic_ids,
        "score": score,
        "impact_score": impact_score,
    }


# ---------------------------------------------------------------------------
# D-14 idempotent partition-level overwrite
# ---------------------------------------------------------------------------


def test_rerun_same_rows_produces_identical_put_item_args():
    """Re-run with the same input rows + same run_id produces byte-identical put_item args (D-14)."""
    rows = [
        _make_row(faculty_uid="alice", primary_subtopic_id="s1", subtopic_ids=["s1", "s2"]),
        _make_row(faculty_uid="bob",   primary_subtopic_id="s2", subtopic_ids=["s2"]),
    ]
    faculty_scores_exclusive, _ = agg._aggregate_exclusive(rows)
    faculty_scores_inclusive, _ = agg._aggregate_inclusive(rows)

    topic_id = "cardio"
    run_id = "same-run-id-001"

    # Run 1
    table1 = MagicMock()
    agg._write_subtopic_score_partitions(
        table1,
        topic_id=topic_id,
        faculty_scores_exclusive=faculty_scores_exclusive,
        faculty_scores_inclusive=faculty_scores_inclusive,
        run_id=run_id,
    )
    calls1 = [c.kwargs["Item"] for c in table1.put_item.call_args_list]

    # Run 2 — same input
    table2 = MagicMock()
    agg._write_subtopic_score_partitions(
        table2,
        topic_id=topic_id,
        faculty_scores_exclusive=faculty_scores_exclusive,
        faculty_scores_inclusive=faculty_scores_inclusive,
        run_id=run_id,
    )
    calls2 = [c.kwargs["Item"] for c in table2.put_item.call_args_list]

    # Same number of writes
    assert len(calls1) == len(calls2), (
        f"Run 1 had {len(calls1)} puts, run 2 had {len(calls2)}"
    )

    # Sort by PK for deterministic comparison
    calls1_sorted = sorted(calls1, key=lambda x: x["PK"])
    calls2_sorted = sorted(calls2, key=lambda x: x["PK"])

    for item1, item2 in zip(calls1_sorted, calls2_sorted):
        assert item1["PK"] == item2["PK"]
        assert item1["SK"] == item2["SK"]
        assert item1["faculty_scores"] == item2["faculty_scores"], (
            f"faculty_scores differ for PK={item1['PK']}"
        )
        assert item1["run_id"] == item2["run_id"]


def test_rerun_overwrites_with_new_data():
    """Re-run with different rows produces different put_item args (partition-level overwrite semantics)."""
    rows_a = [
        _make_row(faculty_uid="alice", primary_subtopic_id="s1", subtopic_ids=["s1"],
                  score=0.8, impact_score=50.0),
    ]
    rows_b = [
        _make_row(faculty_uid="alice", primary_subtopic_id="s1", subtopic_ids=["s1"],
                  score=0.9, impact_score=80.0),  # different scores → different article_score
    ]

    topic_id = "cardio"
    run_id = "overwrite-run-001"

    # Run with rows_a
    table_a = MagicMock()
    fse_a, _ = agg._aggregate_exclusive(rows_a)
    fsi_a, _ = agg._aggregate_inclusive(rows_a)
    agg._write_subtopic_score_partitions(
        table_a,
        topic_id=topic_id,
        faculty_scores_exclusive=fse_a,
        faculty_scores_inclusive=fsi_a,
        run_id=run_id,
    )
    calls_a = {c.kwargs["Item"]["PK"]: c.kwargs["Item"] for c in table_a.put_item.call_args_list}

    # Run with rows_b
    table_b = MagicMock()
    fse_b, _ = agg._aggregate_exclusive(rows_b)
    fsi_b, _ = agg._aggregate_inclusive(rows_b)
    agg._write_subtopic_score_partitions(
        table_b,
        topic_id=topic_id,
        faculty_scores_exclusive=fse_b,
        faculty_scores_inclusive=fsi_b,
        run_id=run_id,
    )
    calls_b = {c.kwargs["Item"]["PK"]: c.kwargs["Item"] for c in table_b.put_item.call_args_list}

    # Both runs touch the same PKs
    assert set(calls_a.keys()) == set(calls_b.keys())

    # But the faculty_scores values differ (rows_b has a different article_score)
    pk = "SUBTOPIC_SCORE#cardio#s1"
    assert pk in calls_a and pk in calls_b
    score_a = float(calls_a[pk]["faculty_scores"]["alice"])
    score_b = float(calls_b[pk]["faculty_scores"]["alice"])
    assert abs(score_a - score_b) > 1e-6, (
        "Expected different scores for rows_a vs rows_b but got the same value"
    )

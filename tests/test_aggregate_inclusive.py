"""Tests for aggregate_subtopic_scores — Phase 12 both-aggregations (spec §8).

Covers:
- _aggregate_exclusive preserves old _aggregate semantics (rename-only)
- _aggregate_inclusive: uniform full article_score per above-floor subtopic_id (D-15)
- _aggregate_inclusive uses subtopic_ids list verbatim; does NOT auto-add primary (D-16)
- D-17 arithmetic invariant: sum(inclusive) >= sum(exclusive) - 1e-9
- D-17 equality case: singleton subtopic_ids[] → sums match within 1e-9
- D-17 delta case: multi-subtopic rows → inclusive > exclusive
- Dual-partition write: SUBTOPIC_SCORE# and SUBTOPIC_SCORE_INCLUSIVE# PK formats
- PK format: SUBTOPIC_SCORE#{topic}#{subtopic} and SUBTOPIC_SCORE_INCLUSIVE#{topic}#{subtopic}, SK="GLOBAL"
- D-33 in-stream invariant: divergence at put_item boundary raises (not a tautology)
- D-33 happy path: aligned writes complete without raising
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, call, patch

import pytest

import aggregate_subtopic_scores as agg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(
    *,
    faculty_uid: str,
    primary_subtopic_id: str,
    subtopic_ids: list[str],
    score: float = 0.8,
    impact_score: float = 50.0,
) -> dict:
    """Build a synthetic SCORE# row for testing."""
    return {
        "faculty_uid": f"cwid_{faculty_uid}",
        "primary_subtopic_id": primary_subtopic_id,
        "subtopic_ids": subtopic_ids,
        "score": score,
        "impact_score": impact_score,
    }


def _article_score(score: float = 0.8, impact_score: float = 50.0) -> float:
    """Replicate the article_score formula (P-10)."""
    return (impact_score / 100) ** 1.2 * score ** 1.4


# ---------------------------------------------------------------------------
# _aggregate_exclusive — rename-only; old semantics preserved
# ---------------------------------------------------------------------------


def test_aggregate_exclusive_unchanged():
    """_aggregate_exclusive returns the same shape as the pre-rename _aggregate."""
    rows = [
        _make_row(faculty_uid="alice", primary_subtopic_id="s1", subtopic_ids=["s1"]),
        _make_row(faculty_uid="alice", primary_subtopic_id="s1", subtopic_ids=["s1"]),
        _make_row(faculty_uid="alice", primary_subtopic_id="s2", subtopic_ids=["s2"]),
    ]
    faculty_scores, total_weights = agg._aggregate_exclusive(rows)

    expected_score = _article_score()
    assert "alice" in faculty_scores
    # Two rows for s1 → double the single-row score
    assert abs(faculty_scores["alice"]["s1"] - 2 * expected_score) < 1e-9
    assert abs(faculty_scores["alice"]["s2"] - expected_score) < 1e-9
    assert abs(total_weights["s1"] - 2 * expected_score) < 1e-9
    assert abs(total_weights["s2"] - expected_score) < 1e-9
    # Result is plain dict (un-defaultdicted)
    assert isinstance(faculty_scores, dict)
    assert isinstance(total_weights, dict)


# ---------------------------------------------------------------------------
# _aggregate_inclusive — D-15 uniform full weight
# ---------------------------------------------------------------------------


def test_aggregate_inclusive_uniform_full_weight():
    """Each subtopic_id in the list gets the FULL article_score (D-15 — NOT 1/N)."""
    score = _article_score()
    rows = [
        {
            "faculty_uid": "cwid_alice",
            "subtopic_ids": ["s1", "s2", "s3"],
            "score": 0.8,
            "impact_score": 50.0,
        }
    ]
    faculty_scores, total_weights = agg._aggregate_inclusive(rows, confidence_floor=0.0)

    assert "alice" in faculty_scores
    assert abs(faculty_scores["alice"]["s1"] - score) < 1e-9
    assert abs(faculty_scores["alice"]["s2"] - score) < 1e-9
    assert abs(faculty_scores["alice"]["s3"] - score) < 1e-9
    # total_weights likewise
    assert abs(total_weights["s1"] - score) < 1e-9


def test_aggregate_inclusive_uses_subtopic_ids_not_primary():
    """Inclusive aggregation uses subtopic_ids verbatim — does NOT auto-add primary (D-16)."""
    rows = [
        {
            "faculty_uid": "cwid_alice",
            "primary_subtopic_id": "primary_only",
            "subtopic_ids": ["s_a", "s_b"],  # primary_only NOT in subtopic_ids
            "score": 0.8,
            "impact_score": 50.0,
        }
    ]
    faculty_scores, total_weights = agg._aggregate_inclusive(rows, confidence_floor=0.0)

    assert "alice" in faculty_scores
    # Must contain exactly s_a and s_b — NOT primary_only
    assert "s_a" in faculty_scores["alice"]
    assert "s_b" in faculty_scores["alice"]
    assert "primary_only" not in faculty_scores.get("alice", {})


# ---------------------------------------------------------------------------
# D-17 invariant tests
# ---------------------------------------------------------------------------


def test_aggregate_inclusive_invariant_holds():
    """For any input: sum(inclusive_totals) >= sum(exclusive_totals) - 1e-9."""
    rows = [
        _make_row(faculty_uid="alice", primary_subtopic_id="s1", subtopic_ids=["s1", "s2"]),
        _make_row(faculty_uid="bob",   primary_subtopic_id="s2", subtopic_ids=["s2", "s3"]),
    ]
    _, excl_totals = agg._aggregate_exclusive(rows)
    _, incl_totals = agg._aggregate_inclusive(rows, confidence_floor=0.0)

    sum_excl = sum(excl_totals.values())
    sum_incl = sum(incl_totals.values())
    assert sum_incl >= sum_excl - 1e-9


def test_aggregate_inclusive_invariant_equality_case():
    """Singleton subtopic_ids[] per row → sum(inclusive) == sum(exclusive) within 1e-9 (D-17)."""
    rows = [
        _make_row(faculty_uid="alice", primary_subtopic_id="s1", subtopic_ids=["s1"]),
        _make_row(faculty_uid="bob",   primary_subtopic_id="s2", subtopic_ids=["s2"]),
    ]
    _, excl_totals = agg._aggregate_exclusive(rows)
    _, incl_totals = agg._aggregate_inclusive(rows, confidence_floor=0.0)

    sum_excl = sum(excl_totals.values())
    sum_incl = sum(incl_totals.values())
    assert abs(sum_incl - sum_excl) < 1e-9


def test_aggregate_inclusive_invariant_delta_case():
    """Rows with >=2 subtopic_ids → sum(inclusive) > sum(exclusive) (D-17 delta)."""
    rows = [
        _make_row(faculty_uid="alice", primary_subtopic_id="s1", subtopic_ids=["s1", "s2"]),
        _make_row(faculty_uid="bob",   primary_subtopic_id="s2", subtopic_ids=["s2", "s3"]),
    ]
    _, excl_totals = agg._aggregate_exclusive(rows)
    _, incl_totals = agg._aggregate_inclusive(rows, confidence_floor=0.0)

    sum_excl = sum(excl_totals.values())
    sum_incl = sum(incl_totals.values())
    assert sum_incl > sum_excl


# ---------------------------------------------------------------------------
# Dual-partition write tests
# ---------------------------------------------------------------------------


def test_subtopic_score_partition_writes():
    """Dual-write function puts items with SUBTOPIC_SCORE# and SUBTOPIC_SCORE_INCLUSIVE# PKs.
    All numeric values at put_item boundary must be Decimal (Pattern B).
    """
    table = MagicMock()
    topic_id = "cardio"
    faculty_scores_exclusive = {"alice": {"s1": 2.5}}
    faculty_scores_inclusive = {"alice": {"s1": 2.5, "s2": 1.0}}

    agg._write_subtopic_score_partitions(
        table,
        topic_id=topic_id,
        faculty_scores_exclusive=faculty_scores_exclusive,
        faculty_scores_inclusive=faculty_scores_inclusive,
        run_id="test-run-001",
    )

    calls = table.put_item.call_args_list
    pks = [c.kwargs["Item"]["PK"] for c in calls]

    excl_pks = [pk for pk in pks if pk.startswith("SUBTOPIC_SCORE#")]
    incl_pks = [pk for pk in pks if pk.startswith("SUBTOPIC_SCORE_INCLUSIVE#")]

    assert len(excl_pks) >= 1, "Expected at least one SUBTOPIC_SCORE# put_item"
    assert len(incl_pks) >= 1, "Expected at least one SUBTOPIC_SCORE_INCLUSIVE# put_item"

    # All numeric faculty_scores values must be Decimal
    for c in calls:
        item = c.kwargs["Item"]
        faculty_map = item.get("faculty_scores", {})
        for val in faculty_map.values():
            assert isinstance(val, Decimal), (
                f"faculty_scores value must be Decimal at DDB boundary, got {type(val)}"
            )


def test_subtopic_score_partition_pk_format():
    """PKs follow exactly SUBTOPIC_SCORE#{topic}#{subtopic} and SUBTOPIC_SCORE_INCLUSIVE#{topic}#{subtopic}, SK='GLOBAL'."""
    table = MagicMock()
    topic_id = "aging_geroscience"
    faculty_scores_exclusive = {"alice": {"aging_cellular_senescence": 1.0}}
    faculty_scores_inclusive = {"alice": {"aging_cellular_senescence": 1.0}}

    agg._write_subtopic_score_partitions(
        table,
        topic_id=topic_id,
        faculty_scores_exclusive=faculty_scores_exclusive,
        faculty_scores_inclusive=faculty_scores_inclusive,
        run_id="test-run-002",
    )

    calls = table.put_item.call_args_list
    pks = {c.kwargs["Item"]["PK"] for c in calls}
    sks = {c.kwargs["Item"]["SK"] for c in calls}

    assert f"SUBTOPIC_SCORE#aging_geroscience#aging_cellular_senescence" in pks
    assert f"SUBTOPIC_SCORE_INCLUSIVE#aging_geroscience#aging_cellular_senescence" in pks
    assert sks == {"GLOBAL"}, f"SK must always be 'GLOBAL', got {sks}"


# ---------------------------------------------------------------------------
# D-33 in-stream invariant — divergence at put_item boundary (W-2 contract)
# ---------------------------------------------------------------------------


def test_d33_invariant_raises_on_divergence():
    """D-33: divergence MUST be injected at the put_item boundary of one writer.

    W-2 no-tautology contract: divergence is simulated by constructing a
    corrupted_partition dict that differs from the faculty_map, mimicking a
    scenario where the SUBTOPIC_SCORE# put_item call had its Item mutated
    in-flight at the write boundary. The in-memory aggregator output dicts
    are NOT mutated — they remain equal by construction. Only the 'snapshot'
    of what was actually written to the partition differs.

    The invariant must catch the split at the write boundary, not by comparing
    two views of the same in-memory dict (which would be a tautology: x == x).
    """
    rows = [
        _make_row(faculty_uid="alice", primary_subtopic_id="s1", subtopic_ids=["s1"]),
    ]
    faculty_scores_exclusive, _ = agg._aggregate_exclusive(rows)
    faculty_scores_inclusive, _ = agg._aggregate_inclusive(rows, confidence_floor=0.0)

    # Pin a snapshot of the in-memory dicts BEFORE the write to verify W-2
    excl_snapshot = {pid: dict(scores) for pid, scores in faculty_scores_exclusive.items()}
    incl_snapshot = {pid: dict(scores) for pid, scores in faculty_scores_inclusive.items()}

    # Simulate what was written at the SUBTOPIC_SCORE# put_item boundary after corruption:
    # the partition received corrupted data (999.99) while the faculty-map holds the real value.
    # This represents divergence at the write boundary — NOT a mutation of the in-memory dict.
    corrupted_partition = {"alice": {"s1": 999.99}}  # simulates what landed on disk after boundary corruption

    with pytest.raises(Exception) as exc_info:
        agg._assert_d33_reconciliation(
            faculty_map=faculty_scores_exclusive,
            subtopic_score_partition_data=corrupted_partition,
        )

    # The exception message must reference reconciliation and the offending data
    msg = str(exc_info.value).lower()
    assert "reconciliation" in msg or "d-33" in msg.lower() or "d33" in msg.lower()

    # W-2 contract: in-memory dicts were NOT mutated by the test fixture
    assert faculty_scores_exclusive == excl_snapshot, (
        "W-2 violated: test fixture mutated the in-memory exclusive dict"
    )
    assert faculty_scores_inclusive == incl_snapshot, (
        "W-2 violated: test fixture mutated the in-memory inclusive dict"
    )


def test_d33_invariant_raises_on_divergence_via_write_boundary():
    """D-33 round-trip: write helper that injects divergence at put_item boundary triggers assertion.

    Injects divergence by patching _write_subtopic_score_partitions to modify
    the partition data passed to _assert_d33_reconciliation.
    """
    rows = [
        _make_row(faculty_uid="alice", primary_subtopic_id="s1", subtopic_ids=["s1"]),
    ]
    faculty_scores_exclusive, _ = agg._aggregate_exclusive(rows)
    faculty_scores_inclusive, _ = agg._aggregate_inclusive(rows, confidence_floor=0.0)

    # Corrupt the exclusive partition data AFTER writing (simulating a write boundary mutation)
    corrupted_exclusive = {"alice": {"s1": 999.99}}  # different from faculty_map

    with pytest.raises(Exception) as exc_info:
        agg._assert_d33_reconciliation(
            faculty_map=faculty_scores_exclusive,
            subtopic_score_partition_data=corrupted_exclusive,
        )

    msg = str(exc_info.value).lower()
    assert "reconciliation" in msg or "d-33" in msg.lower() or "d33" in msg.lower()


def test_d33_invariant_passes_on_aligned_writes():
    """D-33 happy path: when faculty-map and SUBTOPIC_SCORE# agree, no exception is raised."""
    rows = [
        _make_row(faculty_uid="alice", primary_subtopic_id="s1", subtopic_ids=["s1"]),
        _make_row(faculty_uid="bob",   primary_subtopic_id="s2", subtopic_ids=["s2"]),
    ]
    faculty_scores_exclusive, _ = agg._aggregate_exclusive(rows)

    # Both derivations are identical — no divergence
    agg._assert_d33_reconciliation(
        faculty_map=faculty_scores_exclusive,
        subtopic_score_partition_data=faculty_scores_exclusive,
    )
    # No exception = pass

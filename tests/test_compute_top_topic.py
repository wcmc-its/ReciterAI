"""Tests for compute_top_topic.py (#68).

Covers:
- Pure tiebreak (`resolve_top_topic`): standard argmax, no-above-floor,
  tie broken by subtopic-confidence sum, tie broken alphabetically,
  empty input, single-topic input.
- Activity-row reducer (`derive_top_topic_inputs`): groups multi-author
  rows per topic; ignores non-TOPIC# PKs.
- Per-PMID end-to-end (`process_pmid`): writes top_topic_id to every
  activity row; emits REMOVE when no topic clears the floor.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import compute_top_topic as ct


# ---------- pure tiebreak ----------

def test_standard_argmax_picks_top_score():
    assert ct.resolve_top_topic(
        {"a": 0.85, "b": 0.50, "c": 0.30},
        score_floor=0.3,
        tie_epsilon=0.001,
    ) == "a"


def test_no_above_floor_returns_none():
    assert ct.resolve_top_topic(
        {"a": 0.10, "b": 0.20},
        score_floor=0.3,
        tie_epsilon=0.001,
    ) is None


def test_empty_topic_scores_returns_none():
    assert ct.resolve_top_topic(
        {}, score_floor=0.3, tie_epsilon=0.001,
    ) is None


def test_single_topic_returned_without_tiebreak():
    assert ct.resolve_top_topic(
        {"only_one": 0.55},
        score_floor=0.3,
        tie_epsilon=0.001,
    ) == "only_one"


def test_tie_broken_by_higher_subtopic_confidence_sum():
    """Two topics within tie_epsilon → higher sum(subtopic_confidences) wins."""
    chosen = ct.resolve_top_topic(
        {"z_topic": 0.500, "a_topic": 0.5005},
        subtopic_confidence_sums={"z_topic": 5.0, "a_topic": 1.0},
        score_floor=0.3,
        tie_epsilon=0.001,
    )
    assert chosen == "z_topic", "denser signal should beat alphabetical when within tie_epsilon"


def test_tie_broken_alphabetically_when_confidence_sums_equal():
    chosen = ct.resolve_top_topic(
        {"z_topic": 0.5, "a_topic": 0.5},
        subtopic_confidence_sums={"z_topic": 1.0, "a_topic": 1.0},
        score_floor=0.3,
        tie_epsilon=0.001,
    )
    assert chosen == "a_topic"


def test_tie_with_no_confidence_data_falls_back_alphabetical():
    """When subtopic_confidence_sums is empty/None, alphabetical wins."""
    chosen = ct.resolve_top_topic(
        {"z_topic": 0.5, "a_topic": 0.5},
        score_floor=0.3,
        tie_epsilon=0.001,
    )
    assert chosen == "a_topic"


def test_below_floor_excluded_even_if_argmax_otherwise():
    """A topic below floor is invisible to argmax."""
    chosen = ct.resolve_top_topic(
        {"a": 0.29, "b": 0.31},
        score_floor=0.3,
        tie_epsilon=0.001,
    )
    assert chosen == "b"


def test_tie_epsilon_zero_treats_only_exact_ties_as_tied():
    """With tie_epsilon=0, a 0.001 gap is a clear winner, not a tie."""
    chosen = ct.resolve_top_topic(
        {"z_topic": 0.501, "a_topic": 0.500},
        subtopic_confidence_sums={"z_topic": 0.1, "a_topic": 9.9},
        score_floor=0.3,
        tie_epsilon=0.0,
    )
    assert chosen == "z_topic"


def test_decimal_scores_supported():
    """Scores arriving from DDB are Decimals; tiebreak must coerce."""
    chosen = ct.resolve_top_topic(
        {"a": Decimal("0.85"), "b": Decimal("0.55")},
        score_floor=0.3,
        tie_epsilon=0.001,
    )
    assert chosen == "a"


# ---------- activity-row reducer ----------

def _row(topic_id: str, score, *, sk_suffix: str = "cwid_x", confs: dict | None = None) -> dict:
    return {
        "PK": f"TOPIC#{topic_id}",
        "SK": f"SCORE#0{int(score * 1000):04d}#ACTIVITY#pmid_X#{sk_suffix}",
        "score": Decimal(str(score)),
        "subtopic_confidences": confs or {},
    }


def test_derive_dedupes_multi_author_rows_per_topic():
    rows = [
        _row("topic_a", 0.85, sk_suffix="cwid_one"),
        _row("topic_a", 0.85, sk_suffix="cwid_two"),
        _row("topic_b", 0.50, sk_suffix="cwid_one"),
    ]
    scores, sums = ct.derive_top_topic_inputs(rows)
    assert scores == {"topic_a": 0.85, "topic_b": 0.50}
    assert sums == {"topic_a": 0.0, "topic_b": 0.0}


def test_derive_sums_subtopic_confidences_per_topic():
    rows = [
        _row("topic_a", 0.85, confs={"sub_1": Decimal("0.6"), "sub_2": Decimal("0.4")}),
        _row("topic_b", 0.50, confs={"sub_3": Decimal("0.9")}),
    ]
    _, sums = ct.derive_top_topic_inputs(rows)
    assert sums["topic_a"] == 1.0
    assert sums["topic_b"] == 0.9


def test_derive_ignores_non_topic_pk():
    """Faculty / processing rows surfaced by PmidIndex must be dropped."""
    rows = [
        {"PK": "FACULTY#cwid_jsmith", "SK": "PROFILE", "score": Decimal("0.99")},
        _row("topic_a", 0.85),
    ]
    scores, _ = ct.derive_top_topic_inputs(rows)
    assert scores == {"topic_a": 0.85}


# ---------- per-PMID end-to-end ----------

def test_process_pmid_writes_top_topic_to_every_row(monkeypatch):
    rows = [
        _row("topic_a", 0.85, sk_suffix="cwid_one"),
        _row("topic_a", 0.85, sk_suffix="cwid_two"),
        _row("topic_b", 0.50, sk_suffix="cwid_one"),
    ]
    monkeypatch.setattr(ct, "fetch_activity_rows_for_pmid", lambda table, pmid: rows)

    table = MagicMock()
    top, written = ct.process_pmid(table, "X", score_floor=0.3, tie_epsilon=0.001)

    assert top == "topic_a"
    assert written == 3
    assert table.update_item.call_count == 3
    for call in table.update_item.call_args_list:
        kw = call.kwargs
        assert kw["UpdateExpression"] == "SET top_topic_id = :tid"
        assert kw["ExpressionAttributeValues"] == {":tid": "topic_a"}


def test_process_pmid_removes_top_topic_when_no_topic_clears_floor(monkeypatch):
    """A paper with persisted rows but none above the (current) floor must
    have any stale top_topic_id REMOVED."""
    rows = [
        _row("topic_a", 0.10),
        _row("topic_b", 0.20),
    ]
    monkeypatch.setattr(ct, "fetch_activity_rows_for_pmid", lambda table, pmid: rows)

    table = MagicMock()
    top, written = ct.process_pmid(table, "X", score_floor=0.3, tie_epsilon=0.001)

    assert top is None
    assert written == 2
    for call in table.update_item.call_args_list:
        assert call.kwargs["UpdateExpression"] == "REMOVE top_topic_id"


def test_process_pmid_empty_rows_writes_nothing(monkeypatch):
    monkeypatch.setattr(ct, "fetch_activity_rows_for_pmid", lambda table, pmid: [])
    table = MagicMock()
    top, written = ct.process_pmid(table, "X", score_floor=0.3, tie_epsilon=0.001)
    assert top is None
    assert written == 0
    table.update_item.assert_not_called()


def test_process_pmid_tiebreak_picks_higher_confidence_sum(monkeypatch):
    """Within tie_epsilon, the topic with denser subtopic signal wins."""
    rows = [
        _row("z_topic", 0.500, confs={"s1": Decimal("4.0"), "s2": Decimal("1.0")}),
        _row("a_topic", 0.5005, confs={"s3": Decimal("0.5")}),
    ]
    monkeypatch.setattr(ct, "fetch_activity_rows_for_pmid", lambda table, pmid: rows)

    table = MagicMock()
    top, _ = ct.process_pmid(table, "X", score_floor=0.3, tie_epsilon=0.001)
    assert top == "z_topic"

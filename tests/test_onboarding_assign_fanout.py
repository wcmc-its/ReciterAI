"""#80 Phase 2 (PR 4) — pipeline_onboarding.assign_fanout tests.

Covers the pure dirty-topic derivation (`derive_dirty_topics`) — group-by,
the run-PMID restriction, the draft-coverage filter, hash determinism — and
the Lambda handler's I/O wiring (the FacultyIndex query + manifest read).
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

import pipeline_onboarding.assign_fanout as af

# A representative draft-coverage set for the pure tests. `dermatology` is
# deliberately excluded to exercise the draft-less-topic drop.
_COVERED = {"cardiovascular_disease", "hematology", "neuroscience_neurology"}


def _row(topic_id, pmid, subtopic=None):
    """A projected activity row as `fetch_cwid_topic_activity` returns it."""
    return {"topic_id": topic_id, "pmid": pmid, "primary_subtopic_id": subtopic}


# ---------------------------------------------------------------------------
# derive_dirty_topics — pure group-by
# ---------------------------------------------------------------------------


def test_groups_pmids_by_topic():
    result = af.derive_dirty_topics(
        cwid="abc1234",
        pmids=["1", "2", "3"],
        topic_activity_rows=[
            _row("cardiovascular_disease", "1"),
            _row("cardiovascular_disease", "2"),
            _row("hematology", "3"),
        ],
        draft_covered_topics=_COVERED,
    )
    assert result["assign_topics"] == [
        {"topic_id": "cardiovascular_disease", "delta_pmids": ["1", "2"]},
        {"topic_id": "hematology", "delta_pmids": ["3"]},
    ]
    assert result["dropped_topics"] == []


def test_assign_topics_sorted_by_topic_and_pmids_sorted():
    result = af.derive_dirty_topics(
        cwid="abc1234",
        pmids=["9", "3", "5"],
        topic_activity_rows=[
            _row("hematology", "9"),
            _row("cardiovascular_disease", "5"),
            _row("cardiovascular_disease", "3"),
        ],
        draft_covered_topics=_COVERED,
    )
    assert [t["topic_id"] for t in result["assign_topics"]] == [
        "cardiovascular_disease",
        "hematology",
    ]
    assert result["assign_topics"][0]["delta_pmids"] == ["3", "5"]


def test_restricts_to_run_pmid_set():
    """A TOPIC# row for a PMID outside the run's accepted set is dropped —
    a CWID can carry rows for de-attributed / out-of-run publications."""
    result = af.derive_dirty_topics(
        cwid="abc1234",
        pmids=["1", "2"],
        topic_activity_rows=[
            _row("cardiovascular_disease", "1"),
            _row("cardiovascular_disease", "999"),  # not in run set
        ],
        draft_covered_topics=_COVERED,
    )
    assert result["assign_topics"] == [
        {"topic_id": "cardiovascular_disease", "delta_pmids": ["1"]},
    ]


def test_topic_with_no_in_run_pmids_is_absent():
    """A topic whose every activity row is out-of-run yields no entry —
    no empty-delta Map iteration."""
    result = af.derive_dirty_topics(
        cwid="abc1234",
        pmids=["1"],
        topic_activity_rows=[
            _row("cardiovascular_disease", "1"),
            _row("hematology", "999"),
        ],
        draft_covered_topics=_COVERED,
    )
    assert [t["topic_id"] for t in result["assign_topics"]] == [
        "cardiovascular_disease"
    ]


def test_dedupes_multi_author_rows_for_same_topic_pmid():
    """One PMID yields one TOPIC# row per co-authoring CWID; the fan-out
    assigns per (topic, pmid), so duplicate (topic, pmid) collapse."""
    result = af.derive_dirty_topics(
        cwid="abc1234",
        pmids=["1"],
        topic_activity_rows=[
            _row("cardiovascular_disease", "1"),
            _row("cardiovascular_disease", "1"),  # same pmid, another author
        ],
        draft_covered_topics=_COVERED,
    )
    assert result["assign_topics"] == [
        {"topic_id": "cardiovascular_disease", "delta_pmids": ["1"]},
    ]


def test_integer_pmids_coerced_to_str():
    result = af.derive_dirty_topics(
        cwid="abc1234",
        pmids=[1, 2],
        topic_activity_rows=[_row("hematology", 1), _row("hematology", 2)],
        draft_covered_topics=_COVERED,
    )
    assert result["assign_topics"] == [
        {"topic_id": "hematology", "delta_pmids": ["1", "2"]},
    ]


# ---------------------------------------------------------------------------
# derive_dirty_topics — draft-coverage filter (D-DRAFT-GAP)
# ---------------------------------------------------------------------------


def test_drops_topics_with_no_approved_draft():
    """A topic absent from the coverage manifest is dropped, not fanned out
    — assign_subtopics sys.exit(2)s on a missing draft and fails the Map."""
    result = af.derive_dirty_topics(
        cwid="abc1234",
        pmids=["1", "2"],
        topic_activity_rows=[
            _row("cardiovascular_disease", "1"),
            _row("dermatology_skin_disease", "2"),  # not in _COVERED
        ],
        draft_covered_topics=_COVERED,
    )
    assert [t["topic_id"] for t in result["assign_topics"]] == [
        "cardiovascular_disease"
    ]
    assert result["dropped_topics"] == ["dermatology_skin_disease"]


def test_dropped_topics_sorted():
    result = af.derive_dirty_topics(
        cwid="abc1234",
        pmids=["1", "2"],
        topic_activity_rows=[
            _row("zzz_topic", "1"),
            _row("aaa_topic", "2"),
        ],
        draft_covered_topics=_COVERED,
    )
    assert result["assign_topics"] == []
    assert result["dropped_topics"] == ["aaa_topic", "zzz_topic"]


# ---------------------------------------------------------------------------
# derive_dirty_topics — empty / degenerate inputs
# ---------------------------------------------------------------------------


def test_no_activity_rows_yields_empty_fan_out():
    result = af.derive_dirty_topics(
        cwid="abc1234",
        pmids=["1", "2"],
        topic_activity_rows=[],
        draft_covered_topics=_COVERED,
    )
    assert result["assign_topics"] == []
    assert result["dropped_topics"] == []
    assert result["assign_input_hash"]  # still a stable hash over the empty set


def test_rows_with_missing_topic_or_pmid_skipped():
    result = af.derive_dirty_topics(
        cwid="abc1234",
        pmids=["1"],
        topic_activity_rows=[
            _row("cardiovascular_disease", "1"),
            _row(None, "1"),
            _row("hematology", None),
            {"primary_subtopic_id": "x"},  # neither key present
        ],
        draft_covered_topics=_COVERED,
    )
    assert result["assign_topics"] == [
        {"topic_id": "cardiovascular_disease", "delta_pmids": ["1"]},
    ]


# ---------------------------------------------------------------------------
# derive_dirty_topics — assign_input_hash
# ---------------------------------------------------------------------------


def test_input_hash_is_deterministic_and_row_order_invariant():
    rows_a = [_row("cardiovascular_disease", "2"), _row("hematology", "1")]
    rows_b = list(reversed(rows_a))
    h_a = af.derive_dirty_topics(
        cwid="abc1234", pmids=["1", "2"],
        topic_activity_rows=rows_a, draft_covered_topics=_COVERED,
    )["assign_input_hash"]
    h_b = af.derive_dirty_topics(
        cwid="abc1234", pmids=["2", "1"],
        topic_activity_rows=rows_b, draft_covered_topics=_COVERED,
    )["assign_input_hash"]
    assert h_a == h_b


def test_input_hash_sensitive_to_pmid_set():
    base = dict(
        cwid="abc1234",
        topic_activity_rows=[_row("hematology", "1"), _row("hematology", "2")],
        draft_covered_topics=_COVERED,
    )
    h_one = af.derive_dirty_topics(**base, pmids=["1"])["assign_input_hash"]
    h_two = af.derive_dirty_topics(**base, pmids=["1", "2"])["assign_input_hash"]
    assert h_one != h_two


def test_input_hash_sensitive_to_cwid():
    rows = [_row("hematology", "1")]
    h_1 = af.derive_dirty_topics(
        cwid="abc1234", pmids=["1"],
        topic_activity_rows=rows, draft_covered_topics=_COVERED,
    )["assign_input_hash"]
    h_2 = af.derive_dirty_topics(
        cwid="xyz9999", pmids=["1"],
        topic_activity_rows=rows, draft_covered_topics=_COVERED,
    )["assign_input_hash"]
    assert h_1 != h_2


def test_input_hash_pmid_scoped_when_no_cwid():
    """With cwid omitted (the hot path), the hash is scoped by the PMID set:
    two different PMID sets differ, and a hot-path hash never collides with
    an onboarding cwid-scoped hash over the same topic work."""
    rows_one = [_row("hematology", "1")]
    h_hot = af.derive_dirty_topics(
        pmids=["1"], topic_activity_rows=rows_one, draft_covered_topics=_COVERED,
    )["assign_input_hash"]
    h_hot_other = af.derive_dirty_topics(
        pmids=["1", "2"],
        topic_activity_rows=[_row("hematology", "1"), _row("hematology", "2")],
        draft_covered_topics=_COVERED,
    )["assign_input_hash"]
    h_cwid = af.derive_dirty_topics(
        cwid="abc1234", pmids=["1"],
        topic_activity_rows=rows_one, draft_covered_topics=_COVERED,
    )["assign_input_hash"]
    assert h_hot != h_hot_other
    assert h_hot != h_cwid


# ---------------------------------------------------------------------------
# handler — I/O wiring
# ---------------------------------------------------------------------------


def _wire(monkeypatch, *, rows, covered=_COVERED):
    """Stub the handler's I/O. Returns the captured fetch-call cwid list."""
    captured: dict = {}
    monkeypatch.setattr(af, "get_table", lambda *a, **k: MagicMock())

    def _fetch(table, cwid):
        captured["cwid"] = cwid
        return rows

    monkeypatch.setattr(af, "fetch_cwid_topic_activity", _fetch)
    monkeypatch.setattr(af, "_load_draft_coverage", lambda: set(covered))
    return captured


def _wire_pmids(monkeypatch, *, rows, covered=_COVERED):
    """Stub the hot-path handler's I/O. Returns the captured pmids list."""
    captured: dict = {}
    monkeypatch.setattr(af, "get_table", lambda *a, **k: MagicMock())

    def _fetch(table, pmids):
        captured["pmids"] = list(pmids)
        return rows

    monkeypatch.setattr(af, "fetch_topic_activity_for_pmids", _fetch)
    monkeypatch.setattr(af, "_load_draft_coverage", lambda: set(covered))
    return captured


def test_handler_blank_cwid_raises(monkeypatch):
    """An onboarding event (the cwid key is present) with a blank value is
    an operator error — not a silent fall-through to the hot PMID path."""
    monkeypatch.setattr(af, "get_table", lambda *a, **k: MagicMock())
    with pytest.raises(ValueError, match="blank 'cwid'"):
        af.handler({"cwid": "   ", "pmids": ["1"]})


def test_handler_strips_cwid_whitespace(monkeypatch):
    captured = _wire(monkeypatch, rows=[])
    af.handler({"cwid": "  abc1234  ", "pmids": []})
    assert captured["cwid"] == "abc1234"


def test_handler_returns_derive_result(monkeypatch):
    _wire(
        monkeypatch,
        rows=[
            _row("cardiovascular_disease", "1"),
            _row("hematology", "2"),
        ],
    )
    result = af.handler({"cwid": "abc1234", "pmids": ["1", "2"]})
    assert [t["topic_id"] for t in result["assign_topics"]] == [
        "cardiovascular_disease",
        "hematology",
    ]
    assert result["assign_input_hash"]
    assert result["dropped_topics"] == []


def test_handler_logs_dropped_topics(monkeypatch, caplog):
    _wire(
        monkeypatch,
        rows=[_row("dermatology_skin_disease", "1")],  # not in _COVERED
    )
    with caplog.at_level(logging.WARNING, logger=af.logger.name):
        result = af.handler({"cwid": "abc1234", "pmids": ["1"]})
    assert result["assign_topics"] == []
    assert result["dropped_topics"] == ["dermatology_skin_disease"]
    assert any(
        "dermatology_skin_disease" in r.message and "no approved" in r.message
        for r in caplog.records
    )


def test_handler_coerces_event_pmids_to_str(monkeypatch):
    _wire(monkeypatch, rows=[_row("hematology", "5")])
    result = af.handler({"cwid": "abc1234", "pmids": [5]})
    assert result["assign_topics"] == [
        {"topic_id": "hematology", "delta_pmids": ["5"]},
    ]


# ---------------------------------------------------------------------------
# handler — hot-path {pmids} event (#119)
# ---------------------------------------------------------------------------


def test_handler_pmids_event_routes_to_pmid_index(monkeypatch):
    """A {pmids} event (no cwid key) is the hot path: it reads TOPIC#
    activity by PMID and derives the fan-out."""
    captured = _wire_pmids(
        monkeypatch,
        rows=[
            _row("cardiovascular_disease", "1"),
            _row("hematology", "2"),
        ],
    )
    result = af.handler({"pmids": ["1", "2"]})
    assert captured["pmids"] == ["1", "2"]
    assert [t["topic_id"] for t in result["assign_topics"]] == [
        "cardiovascular_disease",
        "hematology",
    ]
    assert result["assign_input_hash"]
    assert result["dropped_topics"] == []


def test_handler_pmids_event_empty_is_a_valid_empty_fan_out(monkeypatch):
    """An empty delta (empty pmids) is legitimate — an empty fan-out, not a
    raise. CheckAssignNeeded routes the empty assign_topics past the Map."""
    _wire_pmids(monkeypatch, rows=[])
    result = af.handler({"pmids": []})
    assert result["assign_topics"] == []
    assert result["dropped_topics"] == []
    assert result["assign_input_hash"]


def test_handler_pmids_event_coerces_pmids_to_str(monkeypatch):
    captured = _wire_pmids(monkeypatch, rows=[_row("hematology", "5")])
    af.handler({"pmids": [5]})
    assert captured["pmids"] == ["5"]

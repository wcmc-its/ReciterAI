"""#80 Phase 2 / #90 — CWID-scoped, PMID-aware rollup.

Covers the per-CWID DynamoDB rollup path added to `rollup_by_cwid.py`:

- `compute_cwid_rollup` — the four counts over the PMID-aware intersection
  of a CWID's TOPIC# activity rows and its live accepted PMID set.
- `compute_cwid_rollup_input_hash` — the content-addressed skip key.
- `fetch_cwid_topic_activity` — the FacultyIndex GSI query + projection.
- `run_cwid_rollup` — end to end: SQL + DDB read, the skip gate, and the
  `STAGE#rollup_by_cwid#cwid:{cwid}` row carrying `input_pmid_set` +
  `rollup_counts`.
- the `--cwid` CLI surface and its mutual-exclusion guards.

The GLOBAL/CSV path and its byte-identical parity gate (Phase 10 D-08)
live in `test_rollup_incremental_parity.py` and are untouched by this PR.
"""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

import rollup_by_cwid as rbc
import utils.sql_queries as sq


# --- helpers ---------------------------------------------------------------


def _activity(topic_id, pmid, subtopic=None):
    """A projected activity row as `fetch_cwid_topic_activity` returns it."""
    return {"topic_id": topic_id, "pmid": pmid, "primary_subtopic_id": subtopic}


def _topic_item(topic_id, pmid, subtopic=None, sk=None):
    """A raw TOPIC# DynamoDB item as the FacultyIndex GSI returns it."""
    item = {
        "PK": f"TOPIC#{topic_id}",
        "SK": sk or f"SCORE#0900#ACTIVITY#pmid_{pmid}#cwid_x",
        "pmid": pmid,
        "faculty_uid": "cwid_x",
    }
    if subtopic is not None:
        item["primary_subtopic_id"] = subtopic
    return item


def _condition_values(cond):
    """Collect the literal string values referenced in a boto3 condition tree.

    Lets a test assert which `faculty_uid` / `PK` prefix a query targets
    without hard-coding boto3 ConditionBase internals fragilely.
    """
    vals: list[str] = []
    for v in cond.get_expression()["values"]:
        if hasattr(v, "get_expression"):
            vals.extend(_condition_values(v))
        elif isinstance(v, str):
            vals.append(v)
    return vals


class _FakeTable:
    """Minimal DynamoDB Table double for the CWID-scoped rollup path.

    `query` dispatches on IndexName: the FacultyIndex query returns the
    seeded TOPIC# activity items; the un-indexed query (should_skip's
    find_existing_complete) returns the seeded prior STAGE# rows.
    `put_item` records every write.
    """

    def __init__(self, *, activity_items=None, prior_stage_items=None):
        self._activity_items = list(activity_items or [])
        self._prior_stage_items = list(prior_stage_items or [])
        self.put_items: list[dict] = []

    def query(self, **kwargs):
        if kwargs.get("IndexName") == "FacultyIndex":
            return {"Items": list(self._activity_items)}
        return {"Items": list(self._prior_stage_items)}

    def put_item(self, Item):
        self.put_items.append(Item)
        return {}


# --- compute_cwid_rollup ---------------------------------------------------


def test_compute_cwid_rollup_basic_counts():
    rows = [
        _activity("cardio", "111", "afib"),
        _activity("neuro", "111", "stroke"),
        _activity("cardio", "222", "afib"),
    ]
    counts = rbc.compute_cwid_rollup(rows, input_pmid_set=["111", "222"])
    assert counts == {
        "n_activities": 3,            # three TOPIC# rows
        "n_distinct_topics": 2,       # cardio, neuro
        "n_subtopic_activities": 3,   # all three carry a primary_subtopic_id
        "n_distinct_subtopics": 2,    # afib, stroke
    }


def test_compute_cwid_rollup_is_pmid_aware_and_excludes_stale_rows():
    """A TOPIC# row whose PMID is not in the live accepted set (a paper
    ReCiter has since de-attributed) must not enter any count."""
    rows = [
        _activity("cardio", "111", "afib"),
        _activity("onco", "999", "melanoma"),  # 999 is stale — de-attributed
    ]
    counts = rbc.compute_cwid_rollup(rows, input_pmid_set=["111"])
    assert counts == {
        "n_activities": 1,
        "n_distinct_topics": 1,
        "n_subtopic_activities": 1,
        "n_distinct_subtopics": 1,
    }


def test_compute_cwid_rollup_distinct_vs_total():
    """Two rows under the same topic count twice for n_activities but once
    for n_distinct_topics."""
    rows = [
        _activity("cardio", "111", "afib"),
        _activity("cardio", "222", "afib"),
    ]
    counts = rbc.compute_cwid_rollup(rows, input_pmid_set=["111", "222"])
    assert counts["n_activities"] == 2
    assert counts["n_distinct_topics"] == 1
    assert counts["n_subtopic_activities"] == 2
    assert counts["n_distinct_subtopics"] == 1


def test_compute_cwid_rollup_rows_without_subtopic():
    """Rows lacking a primary_subtopic_id count toward n_activities /
    n_distinct_topics but not toward the subtopic tallies."""
    rows = [
        _activity("cardio", "111", "afib"),
        _activity("neuro", "222", None),       # no primary_subtopic_id
        _activity("onco", "333", ""),          # empty primary_subtopic_id
    ]
    counts = rbc.compute_cwid_rollup(rows, input_pmid_set=["111", "222", "333"])
    assert counts["n_activities"] == 3
    assert counts["n_distinct_topics"] == 3
    assert counts["n_subtopic_activities"] == 1
    assert counts["n_distinct_subtopics"] == 1


def test_compute_cwid_rollup_empty_input_pmid_set_is_all_zero():
    rows = [_activity("cardio", "111", "afib")]
    counts = rbc.compute_cwid_rollup(rows, input_pmid_set=[])
    assert counts == {
        "n_activities": 0,
        "n_distinct_topics": 0,
        "n_subtopic_activities": 0,
        "n_distinct_subtopics": 0,
    }


def test_compute_cwid_rollup_no_activity_rows_is_all_zero():
    counts = rbc.compute_cwid_rollup([], input_pmid_set=["111", "222"])
    assert counts == {
        "n_activities": 0,
        "n_distinct_topics": 0,
        "n_subtopic_activities": 0,
        "n_distinct_subtopics": 0,
    }


# --- compute_cwid_rollup_input_hash ----------------------------------------


_HASH_ROWS = [_activity("cardio", "111", "afib"), _activity("neuro", "222", "stroke")]


def test_cwid_input_hash_is_deterministic():
    h1 = rbc.compute_cwid_rollup_input_hash(
        cwid="abc", activity_rows=_HASH_ROWS, input_pmid_set=["111", "222"]
    )
    h2 = rbc.compute_cwid_rollup_input_hash(
        cwid="abc", activity_rows=list(reversed(_HASH_ROWS)),
        input_pmid_set=["222", "111"],
    )
    assert h1 == h2  # order of rows / pmids does not matter


def test_cwid_input_hash_differs_by_cwid():
    h_abc = rbc.compute_cwid_rollup_input_hash(
        cwid="abc", activity_rows=_HASH_ROWS, input_pmid_set=["111", "222"]
    )
    h_xyz = rbc.compute_cwid_rollup_input_hash(
        cwid="xyz", activity_rows=_HASH_ROWS, input_pmid_set=["111", "222"]
    )
    assert h_abc != h_xyz


def test_cwid_input_hash_changes_with_input_pmid_set():
    h1 = rbc.compute_cwid_rollup_input_hash(
        cwid="abc", activity_rows=_HASH_ROWS, input_pmid_set=["111", "222"]
    )
    h2 = rbc.compute_cwid_rollup_input_hash(
        cwid="abc", activity_rows=_HASH_ROWS, input_pmid_set=["111", "222", "333"]
    )
    assert h1 != h2


def test_cwid_input_hash_changes_when_relevant_activity_changes():
    """A topic/subtopic change on an in-set PMID re-triggers the rollup."""
    h1 = rbc.compute_cwid_rollup_input_hash(
        cwid="abc", activity_rows=[_activity("cardio", "111", "afib")],
        input_pmid_set=["111"],
    )
    h2 = rbc.compute_cwid_rollup_input_hash(
        cwid="abc", activity_rows=[_activity("cardio", "111", "stroke")],
        input_pmid_set=["111"],
    )
    assert h1 != h2


def test_cwid_input_hash_ignores_stale_activity():
    """A change confined to a TOPIC# row outside input_pmid_set cannot
    affect the rollup result, so it must not change the input_hash."""
    base = [_activity("cardio", "111", "afib")]
    h_with_stale_a = rbc.compute_cwid_rollup_input_hash(
        cwid="abc",
        activity_rows=base + [_activity("onco", "999", "melanoma")],
        input_pmid_set=["111"],
    )
    h_with_stale_b = rbc.compute_cwid_rollup_input_hash(
        cwid="abc",
        activity_rows=base + [_activity("neuro", "999", "stroke")],
        input_pmid_set=["111"],
    )
    assert h_with_stale_a == h_with_stale_b


# --- fetch_cwid_topic_activity ---------------------------------------------


def test_fetch_queries_faculty_index_for_cwid_topic_rows():
    table = MagicMock()
    table.query.return_value = {"Items": []}
    rbc.fetch_cwid_topic_activity(table, "abc1234")
    kwargs = table.query.call_args.kwargs
    assert kwargs["IndexName"] == "FacultyIndex"
    values = _condition_values(kwargs["KeyConditionExpression"])
    # The GSI hash key is faculty_uid = cwid_{cwid}; the range key narrows
    # to the TOPIC# partition.
    assert "cwid_abc1234" in values
    assert "TOPIC#" in values


def test_fetch_projects_to_three_rollup_fields():
    table = MagicMock()
    table.query.return_value = {
        "Items": [_topic_item("cardio", "111", "afib")]
    }
    rows = rbc.fetch_cwid_topic_activity(table, "abc")
    assert rows == [
        {"topic_id": "cardio", "pmid": "111", "primary_subtopic_id": "afib"}
    ]


def test_fetch_drops_non_activity_sk_rows():
    """The FacultyIndex query is bounded to TOPIC# PKs, but a non-activity
    SK (e.g. a stray metadata row) is dropped defensively."""
    table = MagicMock()
    table.query.return_value = {
        "Items": [
            _topic_item("cardio", "111", "afib"),
            _topic_item("cardio", "222", "afib", sk="META#something"),
        ]
    }
    rows = rbc.fetch_cwid_topic_activity(table, "abc")
    assert rows == [
        {"topic_id": "cardio", "pmid": "111", "primary_subtopic_id": "afib"}
    ]


def test_fetch_paginates_on_last_evaluated_key():
    table = MagicMock()
    table.query.side_effect = [
        {"Items": [_topic_item("cardio", "111", "afib")],
         "LastEvaluatedKey": {"cursor": "page2"}},
        {"Items": [_topic_item("neuro", "222", "stroke")]},
    ]
    rows = rbc.fetch_cwid_topic_activity(table, "abc")
    assert len(rows) == 2
    assert table.query.call_count == 2
    assert table.query.call_args_list[1].kwargs["ExclusiveStartKey"] == {
        "cursor": "page2"
    }


def test_fetch_normalizes_missing_pmid_and_subtopic():
    table = MagicMock()
    table.query.return_value = {
        "Items": [{"PK": "TOPIC#cardio", "SK": "SCORE#0900#ACTIVITY#x"}]
    }
    rows = rbc.fetch_cwid_topic_activity(table, "abc")
    assert rows == [
        {"topic_id": "cardio", "pmid": None, "primary_subtopic_id": None}
    ]


# --- run_cwid_rollup -------------------------------------------------------


def test_run_cwid_rollup_writes_complete_row_with_input_pmid_set_and_counts(
    monkeypatch,
):
    monkeypatch.setattr(sq, "get_pmids_for_cwid", lambda cwid: ["222", "111"])
    table = _FakeTable(activity_items=[
        _topic_item("cardio", "111", "afib"),
        _topic_item("neuro", "222", "stroke"),
    ])

    rc = rbc.run_cwid_rollup("abc123", stage_table=table, emit_envelope=False)

    assert rc == 0
    assert len(table.put_items) == 1
    row = table.put_items[0]
    assert row["PK"] == "STAGE#rollup_by_cwid#cwid:abc123"
    assert row["SK"].startswith("RUN#")
    assert row["stage"] == "rollup_by_cwid"
    assert row["scope"] == "cwid:abc123"
    assert row["status"] == "complete"
    assert row["cost_observed_usd"] == Decimal("0")
    assert row["records_written"] == 1
    # input_pmid_set is the live accepted snapshot — sorted + deduped.
    assert row["input_pmid_set"] == ["111", "222"]
    assert row["rollup_counts"] == {
        "n_activities": 2,
        "n_distinct_topics": 2,
        "n_subtopic_activities": 2,
        "n_distinct_subtopics": 2,
    }


def test_run_cwid_rollup_is_pmid_aware_against_stale_activity(monkeypatch):
    """A TOPIC# row for a de-attributed PMID is excluded from the row's
    rollup_counts, but the accepted PMID set still drives input_pmid_set."""
    monkeypatch.setattr(sq, "get_pmids_for_cwid", lambda cwid: ["111"])
    table = _FakeTable(activity_items=[
        _topic_item("cardio", "111", "afib"),
        _topic_item("onco", "999", "melanoma"),  # stale: 999 not accepted
    ])

    rbc.run_cwid_rollup("abc123", stage_table=table, emit_envelope=False)

    row = table.put_items[0]
    assert row["input_pmid_set"] == ["111"]
    assert row["rollup_counts"]["n_activities"] == 1
    assert row["rollup_counts"]["n_distinct_topics"] == 1


def test_run_cwid_rollup_skips_on_matching_prior_run(monkeypatch):
    monkeypatch.setattr(sq, "get_pmids_for_cwid", lambda cwid: ["111"])
    activity_items = [_topic_item("cardio", "111", "afib")]
    # Pre-compute the input_hash this run will produce and seed a prior
    # complete row carrying it, so should_skip fires.
    prior_hash = rbc.compute_cwid_rollup_input_hash(
        cwid="abc123",
        activity_rows=[_activity("cardio", "111", "afib")],
        input_pmid_set=["111"],
    )
    table = _FakeTable(
        activity_items=activity_items,
        prior_stage_items=[{
            "status": "complete",
            "input_hash": prior_hash,
            "started_at": "2026-05-15T00:00:00Z",
        }],
    )

    rc = rbc.run_cwid_rollup("abc123", stage_table=table, emit_envelope=False)

    assert rc == 0
    assert len(table.put_items) == 1
    row = table.put_items[0]
    assert row["status"] == "skipped"
    assert row["PK"] == "STAGE#rollup_by_cwid#cwid:abc123"
    assert "input_hash unchanged" in row["skip_reason"]
    # A skipped run did no rollup work — no counts on the row.
    assert "rollup_counts" not in row


def test_run_cwid_rollup_emit_envelope_prints_and_skips_write(
    monkeypatch, capsys,
):
    monkeypatch.setattr(sq, "get_pmids_for_cwid", lambda cwid: ["111"])
    table = _FakeTable(activity_items=[_topic_item("cardio", "111", "afib")])

    rc = rbc.run_cwid_rollup("abc123", stage_table=table, emit_envelope=True)

    assert rc == 0
    # Envelope mode emits to stdout instead of writing to DynamoDB.
    assert table.put_items == []
    envelope_line = [
        ln for ln in capsys.readouterr().out.splitlines()
        if ln.strip().startswith("{")
    ][-1]
    env = json.loads(envelope_line)
    assert env["PK"] == "STAGE#rollup_by_cwid#cwid:abc123"
    assert env["status"] == "complete"
    assert env["input_pmid_set"] == ["111"]
    assert env["rollup_counts"]["n_activities"] == 1


def test_run_cwid_rollup_empty_cwid_raises(monkeypatch):
    monkeypatch.setattr(sq, "get_pmids_for_cwid", lambda cwid: [])
    with pytest.raises(SystemExit):
        rbc.run_cwid_rollup("   ", stage_table=_FakeTable(), emit_envelope=False)


def test_run_cwid_rollup_handles_cwid_with_no_accepted_pmids(monkeypatch):
    """A CWID with zero accepted publications still writes a coherent
    complete row — empty input_pmid_set, all-zero counts."""
    monkeypatch.setattr(sq, "get_pmids_for_cwid", lambda cwid: [])
    table = _FakeTable(activity_items=[])

    rbc.run_cwid_rollup("abc123", stage_table=table, emit_envelope=False)

    row = table.put_items[0]
    assert row["status"] == "complete"
    assert row["input_pmid_set"] == []
    assert row["rollup_counts"]["n_activities"] == 0


# --- CLI surface -----------------------------------------------------------


def test_cli_cwid_routes_to_run_cwid_rollup(monkeypatch):
    monkeypatch.setattr(sq, "get_pmids_for_cwid", lambda cwid: ["111"])
    table = _FakeTable(activity_items=[_topic_item("cardio", "111", "afib")])
    monkeypatch.setattr(rbc, "get_table", lambda *a, **k: table)

    rc = rbc.main(["--cwid", "abc123"])

    assert rc == 0
    assert table.put_items[0]["PK"] == "STAGE#rollup_by_cwid#cwid:abc123"


def test_cli_cwid_rejects_skip_stage_write():
    with pytest.raises(SystemExit):
        rbc.main(["--cwid", "abc", "--skip-stage-write"])


# --- fetch_topic_activity_for_pmids (#119) ---------------------------------


def test_fetch_for_pmids_queries_pmid_index():
    table = MagicMock()
    table.query.return_value = {"Items": []}
    rbc.fetch_topic_activity_for_pmids(table, ["111"])
    kwargs = table.query.call_args.kwargs
    assert kwargs["IndexName"] == "PmidIndex"
    values = _condition_values(kwargs["KeyConditionExpression"])
    # The GSI hash key is the PMID; the range key narrows to the TOPIC# partition.
    assert "111" in values
    assert "TOPIC#" in values


def test_fetch_for_pmids_projects_to_three_rollup_fields():
    table = MagicMock()
    table.query.return_value = {"Items": [_topic_item("cardio", "111", "afib")]}
    rows = rbc.fetch_topic_activity_for_pmids(table, ["111"])
    assert rows == [
        {"topic_id": "cardio", "pmid": "111", "primary_subtopic_id": "afib"}
    ]


def test_fetch_for_pmids_drops_non_activity_sk_rows():
    table = MagicMock()
    table.query.return_value = {
        "Items": [
            _topic_item("cardio", "111", "afib"),
            _topic_item("cardio", "111", "afib", sk="META#something"),
        ]
    }
    rows = rbc.fetch_topic_activity_for_pmids(table, ["111"])
    assert rows == [
        {"topic_id": "cardio", "pmid": "111", "primary_subtopic_id": "afib"}
    ]


def test_fetch_for_pmids_paginates_on_last_evaluated_key():
    table = MagicMock()
    table.query.side_effect = [
        {"Items": [_topic_item("cardio", "111", "afib")],
         "LastEvaluatedKey": {"cursor": "page2"}},
        {"Items": [_topic_item("neuro", "111", "stroke")]},
    ]
    rows = rbc.fetch_topic_activity_for_pmids(table, ["111"])
    assert len(rows) == 2
    assert table.query.call_count == 2
    assert table.query.call_args_list[1].kwargs["ExclusiveStartKey"] == {
        "cursor": "page2"
    }


def test_fetch_for_pmids_queries_each_pmid_once_sorted_and_deduped():
    table = MagicMock()
    table.query.return_value = {"Items": []}
    rbc.fetch_topic_activity_for_pmids(table, ["222", "111", "222"])
    pmids_queried = [
        next(
            v for v in _condition_values(c.kwargs["KeyConditionExpression"])
            if not v.startswith("TOPIC#")
        )
        for c in table.query.call_args_list
    ]
    assert pmids_queried == ["111", "222"]


def test_fetch_for_pmids_empty_input_makes_no_query():
    table = MagicMock()
    assert rbc.fetch_topic_activity_for_pmids(table, []) == []
    assert rbc.fetch_topic_activity_for_pmids(table, ["", "  "]) == []
    table.query.assert_not_called()


def test_fetch_for_pmids_normalizes_missing_pmid_and_subtopic():
    table = MagicMock()
    table.query.return_value = {
        "Items": [{"PK": "TOPIC#cardio", "SK": "SCORE#0900#ACTIVITY#x"}]
    }
    rows = rbc.fetch_topic_activity_for_pmids(table, ["111"])
    assert rows == [
        {"topic_id": "cardio", "pmid": None, "primary_subtopic_id": None}
    ]

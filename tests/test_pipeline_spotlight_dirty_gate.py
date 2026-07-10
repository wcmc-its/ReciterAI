"""Phase 10 T9 — pipeline_spotlight dirty-check gate (D-03).

Covers:
- evaluate_gate threshold semantics across both branches with the
  D-03 default (≥3 subtopics × ≥5 new pubs).
- counts ignore non-top-50 subtopics (long-tail coverage does not
  justify monthly Opus spend).
- multi-assignment PMIDs contribute to each of their subtopics.
- run_gate writes a STAGE#spotlight_refresh#GLOBAL skipped row when
  the gate holds, and a complete row + invokes backfill_spotlight
  when it triggers.
- a backfill_spotlight failure produces a failed STAGE# row.
- resolve_last_spotlight_complete returns the newest complete row's
  started_at; ignores failed/skipped.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import pytest

from pipeline_spotlight import dirty_gate
from pipeline_spotlight import orchestrator as orch


TOP_50 = [f"sub_{i:03d}" for i in range(50)]


# ---------- evaluate_gate semantics ----------


def test_gate_holds_when_no_new_pmids():
    result = dirty_gate.evaluate_gate(
        new_pmid_assignments={},
        top_subtopic_ids=TOP_50,
        min_dirty_subtopics=3,
        min_pubs_per_subtopic=5,
    )
    assert result.should_regen is False
    assert result.new_pmid_count == 0
    assert result.dirty_subtopics == []


def test_gate_holds_below_subtopic_count_threshold():
    """Two subtopics hit 5+ new pubs, but D-03 needs 3."""
    assignments = {f"pmid_{i}": ["sub_000"] for i in range(5)}
    assignments.update({f"pmid_{20 + i}": ["sub_001"] for i in range(5)})
    result = dirty_gate.evaluate_gate(
        new_pmid_assignments=assignments,
        top_subtopic_ids=TOP_50,
        min_dirty_subtopics=3,
        min_pubs_per_subtopic=5,
    )
    assert result.should_regen is False
    assert set(result.dirty_subtopics) == {"sub_000", "sub_001"}


def test_gate_holds_below_per_subtopic_floor():
    """Three subtopics each have 4 new pubs; floor is 5."""
    assignments = {}
    for sid in ("sub_000", "sub_001", "sub_002"):
        for i in range(4):
            assignments[f"pmid_{sid}_{i}"] = [sid]
    result = dirty_gate.evaluate_gate(
        new_pmid_assignments=assignments,
        top_subtopic_ids=TOP_50,
        min_dirty_subtopics=3,
        min_pubs_per_subtopic=5,
    )
    assert result.should_regen is False
    assert result.dirty_subtopics == []  # none crossed the floor


def test_gate_triggers_at_d03_default():
    """Three top-50 subtopics each at ≥5 new pubs → regen."""
    assignments = {}
    for sid in ("sub_000", "sub_001", "sub_002"):
        for i in range(5):
            assignments[f"pmid_{sid}_{i}"] = [sid]
    result = dirty_gate.evaluate_gate(
        new_pmid_assignments=assignments,
        top_subtopic_ids=TOP_50,
        min_dirty_subtopics=3,
        min_pubs_per_subtopic=5,
    )
    assert result.should_regen is True
    assert sorted(result.dirty_subtopics) == ["sub_000", "sub_001", "sub_002"]


def test_gate_ignores_non_top50_subtopics():
    """Many new PMIDs landed in long-tail subtopics — gate stays cold."""
    assignments = {f"pmid_{i}": ["sub_999"] for i in range(50)}
    result = dirty_gate.evaluate_gate(
        new_pmid_assignments=assignments,
        top_subtopic_ids=TOP_50,
        min_dirty_subtopics=3,
        min_pubs_per_subtopic=5,
    )
    assert result.should_regen is False
    assert result.counts_per_top_subtopic == {}


def test_gate_multi_assignment_pmid_contributes_to_each_subtopic():
    """A PMID with multiple subtopic assignments counts once per subtopic."""
    assignments = {
        f"pmid_{i}": ["sub_000", "sub_001", "sub_002"] for i in range(5)
    }
    result = dirty_gate.evaluate_gate(
        new_pmid_assignments=assignments,
        top_subtopic_ids=TOP_50,
        min_dirty_subtopics=3,
        min_pubs_per_subtopic=5,
    )
    assert result.should_regen is True
    assert result.counts_per_top_subtopic == {
        "sub_000": 5, "sub_001": 5, "sub_002": 5,
    }


def test_gate_counts_distinct_publications_not_activity_rows():
    """A single new paper with several co-author TOPIC# rows sharing one
    primary_subtopic_id is one dirty publication, not several (#311). Three such
    papers in three subtopics stay below the 5-pub floor and must not regen."""
    assignments = {
        "pmid_a": ["sub_000"] * 5,  # one paper, five co-author rows
        "pmid_b": ["sub_001"] * 5,
        "pmid_c": ["sub_002"] * 5,
    }
    result = dirty_gate.evaluate_gate(
        new_pmid_assignments=assignments,
        top_subtopic_ids=TOP_50,
        min_dirty_subtopics=3,
        min_pubs_per_subtopic=5,
    )
    assert result.counts_per_top_subtopic == {
        "sub_000": 1, "sub_001": 1, "sub_002": 1,
    }
    assert result.dirty_subtopics == []
    assert result.should_regen is False


def test_gate_reason_strings_describe_branch():
    """The reason() string is human-readable and indicates which branch."""
    triggered = dirty_gate.evaluate_gate(
        new_pmid_assignments={f"p_{i}": ["sub_000", "sub_001", "sub_002"]
                              for i in range(5)},
        top_subtopic_ids=TOP_50,
        min_dirty_subtopics=3, min_pubs_per_subtopic=5,
    )
    assert "triggered" in triggered.reason(min_dirty=3, min_pubs_per=5)
    held = dirty_gate.evaluate_gate(
        new_pmid_assignments={},
        top_subtopic_ids=TOP_50,
        min_dirty_subtopics=3, min_pubs_per_subtopic=5,
    )
    assert "held" in held.reason(min_dirty=3, min_pubs_per=5)


# ---------- resolve_last_spotlight_complete ----------


def test_resolve_last_spotlight_returns_newest_complete():
    table = MagicMock()
    table.query.return_value = {
        "Items": [
            {"status": "skipped",  "started_at": "2026-05-12T00:00:00Z"},
            {"status": "complete", "started_at": "2026-04-01T00:00:00Z"},
        ]
    }
    assert orch.resolve_last_spotlight_complete(table) == "2026-04-01T00:00:00Z"


def test_resolve_last_spotlight_returns_none_when_empty():
    table = MagicMock()
    table.query.return_value = {"Items": []}
    assert orch.resolve_last_spotlight_complete(table) is None


# ---------- run_gate integration ----------


def _capture_table():
    captured: list = []
    table = MagicMock()
    table.put_item.side_effect = lambda Item: captured.append(Item) or {}
    table.query.return_value = {"Items": []}
    return table, captured


def _thresholds():
    return {
        "spotlight_dirty_subtopic_min": 3,
        "spotlight_dirty_pubs_per_subtopic_min": 5,
    }


def test_run_gate_writes_skipped_row_when_below_threshold():
    table, captured = _capture_table()
    result = orch.run_gate(
        table=table,
        new_pmid_assignments={"pmid_1": ["sub_000"]},
        top_subtopic_ids=TOP_50,
        thresholds=_thresholds(),
        spotlight_runner=MagicMock(),  # not invoked when skipped
    )
    assert result["status"] == "skipped"
    rows = [it for it in captured if it.get("PK") == "STAGE#spotlight_refresh#GLOBAL"]
    assert len(rows) == 1
    assert rows[0]["status"] == "skipped"
    assert "held" in rows[0]["skip_reason"]


def test_run_gate_invokes_backfill_and_writes_complete_when_dirty():
    table, captured = _capture_table()
    assignments = {}
    for sid in ("sub_000", "sub_001", "sub_002"):
        for i in range(5):
            assignments[f"pmid_{sid}_{i}"] = [sid]

    runner_calls = []

    def fake_runner(cmd, **kw):
        runner_calls.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0,
                                            stdout="ok", stderr="")

    result = orch.run_gate(
        table=table,
        new_pmid_assignments=assignments,
        top_subtopic_ids=TOP_50,
        thresholds=_thresholds(),
        spotlight_runner=fake_runner,
    )
    assert result["status"] == "complete"
    assert sorted(result["dirty_subtopics"]) == ["sub_000", "sub_001", "sub_002"]

    # backfill_spotlight was invoked exactly once.
    assert len(runner_calls) == 1
    cmd = runner_calls[0]
    assert any("cli.backfill_spotlight" in part for part in cmd)
    assert "--publish" in cmd

    rows = [it for it in captured if it.get("PK") == "STAGE#spotlight_refresh#GLOBAL"]
    assert len(rows) == 1
    assert rows[0]["status"] == "complete"


# ---------- handler: production DDB wiring (Clause 3 fix) ----------


def test_resolve_new_pmid_assignments_groups_subtopics_by_pmid():
    """Scan results returning (pmid, primary_subtopic_id) pairs collapse
    into a {pmid: [subtopic, ...]} mapping. Multiple TOPIC# rows for the
    same PMID (one per co-author) contribute distinct subtopic entries."""
    table = MagicMock()
    table.scan.return_value = {
        "Items": [
            {"pmid": "p1", "primary_subtopic_id": "sub_000"},
            {"pmid": "p1", "primary_subtopic_id": "sub_000"},
            {"pmid": "p2", "primary_subtopic_id": "sub_001"},
            {"pmid": "p3", "primary_subtopic_id": None},  # skipped
            {"pmid": None, "primary_subtopic_id": "sub_002"},  # skipped
        ],
        "LastEvaluatedKey": None,
    }
    out = orch.resolve_new_pmid_assignments(table, since_iso="2026-05-01T00:00:00Z")
    assert out == {"p1": ["sub_000", "sub_000"], "p2": ["sub_001"]}
    # Scan was filtered by since_iso (created_at >=)
    kwargs = table.scan.call_args.kwargs
    assert "created_at >= :since" in kwargs["FilterExpression"]
    assert kwargs["ExpressionAttributeValues"][":since"] == "2026-05-01T00:00:00Z"


def test_resolve_new_pmid_assignments_no_cutoff_scans_all():
    """since_iso=None means 'no prior spotlight' → no created_at filter."""
    table = MagicMock()
    table.scan.return_value = {"Items": [], "LastEvaluatedKey": None}
    orch.resolve_new_pmid_assignments(table, since_iso=None)
    kwargs = table.scan.call_args.kwargs
    assert "created_at" not in kwargs["FilterExpression"]


def test_resolve_top_subtopic_ids_uses_injected_ranker():
    fake_entries = [
        type("E", (), {"subtopic_id": "sub_000"})(),
        type("E", (), {"subtopic_id": "sub_001"})(),
    ]
    out = orch.resolve_top_subtopic_ids(
        pool_size=50, rank_pool_fn=lambda **kw: fake_entries
    )
    assert out == ["sub_000", "sub_001"]


def test_handler_runs_end_to_end_with_injected_data(monkeypatch):
    """handler() with new_pmid_assignments + top_subtopic_ids supplied
    bypasses the DDB scans and just runs the gate."""
    table, captured = _capture_table()
    monkeypatch.setattr(orch, "get_table", lambda *a, **kw: table)
    monkeypatch.setattr(orch, "load_thresholds", lambda: _thresholds())

    # Below-threshold input → skipped row
    out = orch.handler({
        "new_pmid_assignments": {},
        "top_subtopic_ids": TOP_50,
    })
    assert out["status"] == "skipped"
    rows = [it for it in captured if it.get("PK") == "STAGE#spotlight_refresh#GLOBAL"]
    assert len(rows) == 1
    assert rows[0]["status"] == "skipped"


def test_handler_queries_ddb_when_event_omits_fields(monkeypatch):
    """No NotImplementedError: production path queries DDB + pool_ranker."""
    table, captured = _capture_table()
    monkeypatch.setattr(orch, "get_table", lambda *a, **kw: table)
    monkeypatch.setattr(orch, "load_thresholds", lambda: _thresholds())
    monkeypatch.setattr(orch, "resolve_last_spotlight_complete", lambda t: "2026-04-01T00:00:00Z")

    called = {"scan": False, "rank": False}

    def fake_scan(table, *, since_iso):
        called["scan"] = True
        return {}
    monkeypatch.setattr(orch, "resolve_new_pmid_assignments", fake_scan)

    def fake_rank():
        called["rank"] = True
        return [f"sub_{i:03d}" for i in range(50)]
    monkeypatch.setattr(orch, "resolve_top_subtopic_ids", fake_rank)

    out = orch.handler({})
    assert called["scan"] and called["rank"]
    assert out["status"] == "skipped"  # empty new_pmid_assignments → no regen


def test_handler_dispatches_error_alert_on_failure(monkeypatch):
    """run_gate raise → handler dispatches ERROR alert before re-raising."""
    monkeypatch.setattr(orch, "get_table", lambda *a, **kw: MagicMock())
    monkeypatch.setattr(orch, "load_thresholds", lambda: _thresholds())

    def boom(**kw):
        raise RuntimeError("backfill_spotlight blew up")
    monkeypatch.setattr(orch, "run_gate", boom)

    dispatched = []
    monkeypatch.setattr(
        orch.alert, "dispatch",
        lambda *a, **kw: dispatched.append((a, kw)) or {"slack": True, "issue": True},
    )

    with pytest.raises(RuntimeError, match="blew up"):
        orch.handler({"new_pmid_assignments": {}, "top_subtopic_ids": []})

    assert len(dispatched) == 1
    args, kwargs = dispatched[0]
    assert args[0] == "ERROR"
    assert "Spotlight" in args[1]
    assert kwargs.get("open_issue") is True


def test_run_gate_writes_failed_row_on_backfill_error():
    table, captured = _capture_table()
    assignments = {}
    for sid in ("sub_000", "sub_001", "sub_002"):
        for i in range(5):
            assignments[f"pmid_{sid}_{i}"] = [sid]

    def bad_runner(cmd, **kw):
        return subprocess.CompletedProcess(
            args=cmd, returncode=2, stdout="", stderr="missing input"
        )

    with pytest.raises(RuntimeError, match="exited 2"):
        orch.run_gate(
            table=table,
            new_pmid_assignments=assignments,
            top_subtopic_ids=TOP_50,
            thresholds=_thresholds(),
            spotlight_runner=bad_runner,
        )

    rows = [it for it in captured if it.get("PK") == "STAGE#spotlight_refresh#GLOBAL"]
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert rows[0]["error_code"] == "RuntimeError"
    assert rows[0]["failure_details"]["dirty_subtopics"] == [
        "sub_000", "sub_001", "sub_002"
    ]

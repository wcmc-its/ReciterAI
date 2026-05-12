"""Phase 10 T7 — pipeline_hot.orchestrator + handlers.

Covers:
- resolve_last_successful_hot_run finds the newest complete row and
  skips failed/skipped rows.
- resolve_delta_pmids returns a sorted, deduped string PMID list and
  bootstraps when no prior run exists.
- is_state_machine_running excludes the orchestrator's own execution.
- write_skipped_hot_run_locked writes the right STAGE# shape.
- build_state_machine_input is small and JSONPath-friendly.
- The three handlers parse the upstream JSON envelope from stdout and
  raise on non-zero exit codes.
- state_machine.asl.json is valid JSON with the expected state names.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pipeline_hot import orchestrator as orch
from pipeline_hot.handlers import assign as assign_handler
from pipeline_hot.handlers import rollup as rollup_handler
from pipeline_hot.handlers import score as score_handler


# ---------- resolve_last_successful_hot_run ----------


def test_resolve_last_returns_started_at_of_newest_complete():
    table = MagicMock()
    table.query.return_value = {
        "Items": [
            {"status": "failed",   "started_at": "2026-05-12T12:00:00Z"},
            {"status": "complete", "started_at": "2026-05-05T12:00:00Z"},
            {"status": "complete", "started_at": "2026-04-28T12:00:00Z"},
        ]
    }
    assert orch.resolve_last_successful_hot_run(table) == "2026-05-05T12:00:00Z"


def test_resolve_last_returns_none_when_no_complete_rows():
    table = MagicMock()
    table.query.return_value = {
        "Items": [
            {"status": "failed",  "started_at": "2026-05-12T12:00:00Z"},
            {"status": "skipped", "started_at": "2026-05-05T12:00:00Z"},
        ]
    }
    assert orch.resolve_last_successful_hot_run(table) is None


def test_resolve_last_returns_none_on_empty_table():
    table = MagicMock()
    table.query.return_value = {"Items": []}
    assert orch.resolve_last_successful_hot_run(table) is None


def test_resolve_last_queries_correct_pk():
    table = MagicMock()
    table.query.return_value = {"Items": []}
    orch.resolve_last_successful_hot_run(table)
    kwargs = table.query.call_args.kwargs
    assert kwargs["ExpressionAttributeValues"][":pk"] == "STAGE#hot_run#GLOBAL"
    assert kwargs["ScanIndexForward"] is False


# ---------- resolve_delta_pmids ----------


def test_resolve_delta_pmids_passes_since_to_query_fn():
    seen_since = []

    def stub(since):
        seen_since.append(since)
        return ["3", "1", "2", "2"]  # unsorted, with duplicate

    pmids = orch.resolve_delta_pmids("2026-05-05T12:00:00Z", query_fn=stub)
    assert seen_since == ["2026-05-05T12:00:00Z"]
    # Sorted, deduped, stringified
    assert pmids == ["1", "2", "3"]


def test_resolve_delta_pmids_bootstraps_when_last_run_none():
    captured = []

    def stub(since):
        captured.append(since)
        return ["10", "20"]

    pmids = orch.resolve_delta_pmids(None, query_fn=stub)
    assert pmids == ["10", "20"]
    # Bootstrap uses a recent date (not the epoch).
    assert captured[0].startswith("2")  # at least starts with a year


def test_resolve_delta_pmids_requires_query_fn():
    with pytest.raises(ValueError, match="requires a query_fn"):
        orch.resolve_delta_pmids("2026-05-05T12:00:00Z", query_fn=None)


# ---------- is_state_machine_running ----------


def test_lock_detects_other_running_execution():
    sfn = MagicMock()
    sfn.list_executions.return_value = {
        "executions": [
            {"executionArn": "arn:other-execution"},
        ]
    }
    assert orch.is_state_machine_running(
        sfn,
        state_machine_arn="arn:state-machine",
        self_execution_arn="arn:self-execution",
    ) is True


def test_lock_excludes_self_execution():
    sfn = MagicMock()
    sfn.list_executions.return_value = {
        "executions": [{"executionArn": "arn:self-execution"}]
    }
    assert orch.is_state_machine_running(
        sfn,
        state_machine_arn="arn:state-machine",
        self_execution_arn="arn:self-execution",
    ) is False


def test_lock_returns_false_when_no_running_executions():
    sfn = MagicMock()
    sfn.list_executions.return_value = {"executions": []}
    assert orch.is_state_machine_running(
        sfn,
        state_machine_arn="arn:state-machine",
        self_execution_arn="arn:self-execution",
    ) is False


# ---------- write_skipped_hot_run_locked ----------


def test_write_skipped_for_lock_carries_correct_skip_reason():
    captured = []
    table = MagicMock()
    table.put_item.side_effect = lambda Item: captured.append(Item) or {}

    row = orch.write_skipped_hot_run_locked(
        table, started_at="2026-05-12T12:00:00Z", duration_ms=0
    )

    assert row["PK"] == "STAGE#hot_run#GLOBAL"
    assert row["status"] == "skipped"
    assert row["skip_reason"] == orch.SKIP_REASON_LOCKED == "prior_run_in_progress"
    # Persisted
    assert len(captured) == 1
    assert captured[0]["PK"] == "STAGE#hot_run#GLOBAL"


# ---------- build_state_machine_input ----------


def test_build_state_machine_input_shape():
    sm_input = orch.build_state_machine_input(
        pmids=["1", "2", "3"],
        last_run_at="2026-05-05T12:00:00Z",
        started_at="2026-05-12T12:00:00Z",
        run_id="run-abc",
    )
    assert sm_input["run_id"] == "run-abc"
    assert sm_input["started_at"] == "2026-05-12T12:00:00Z"
    assert sm_input["last_successful_hot_run_at"] == "2026-05-05T12:00:00Z"
    assert sm_input["delta"]["pmids"] == ["1", "2", "3"]
    assert sm_input["delta"]["size"] == 3


# ---------- handler stdout parser ----------


def test_parse_envelope_picks_last_json_object_on_stdout():
    stdout = (
        "Loaded taxonomy: taxonomy_v2 (50 topics)\n"
        "Some log line\n"
        '{"PK": "STAGE#score_publications#GLOBAL", "status": "complete"}\n'
    )
    env = score_handler._parse_envelope_from_stdout(stdout)
    assert env["PK"] == "STAGE#score_publications#GLOBAL"
    assert env["status"] == "complete"


def test_parse_envelope_raises_when_no_json_found():
    with pytest.raises(RuntimeError, match="no JSON envelope"):
        score_handler._parse_envelope_from_stdout("just some text\nmore text\n")


# ---------- score handler ----------


def test_score_handler_invokes_subprocess_and_returns_envelope(monkeypatch):
    completed = subprocess.CompletedProcess(
        args=["python", "..."],
        returncode=0,
        stdout='{"PK":"STAGE#score_publications#GLOBAL","status":"complete","input_hash":"abc"}\n',
        stderr="",
    )
    captured_cmds = []

    def fake_run(cmd, **kwargs):
        captured_cmds.append(cmd)
        return completed

    monkeypatch.setattr(subprocess, "run", fake_run)

    env = score_handler.handler({
        "delta": {"pmids": ["1", "2"], "size": 2},
        "last_successful_hot_run_at": "2026-05-05T12:00:00Z",
    })
    assert env["input_hash"] == "abc"
    cmd = captured_cmds[0]
    assert "--emit-envelope" in cmd
    assert "--delta-since" in cmd
    assert "2026-05-05T12:00:00Z" in cmd


def test_score_handler_raises_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(
            args=cmd, returncode=42, stdout="", stderr="boom",
        ),
    )
    with pytest.raises(RuntimeError, match="exited 42"):
        score_handler.handler({"delta": {"pmids": []}})


# ---------- assign handler ----------


def test_assign_handler_requires_topic_id():
    with pytest.raises(ValueError, match="missing 'topic_id'"):
        assign_handler.handler({"delta_pmids": ["1"]})


def test_assign_handler_passes_topic_and_pmids(monkeypatch):
    captured = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        return subprocess.CompletedProcess(
            args=cmd, returncode=0,
            stdout='{"PK":"STAGE#assign_subtopics#topic:cardio","status":"complete"}\n',
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    env = assign_handler.handler({
        "topic_id": "cardio",
        "delta_pmids": ["1", "2"],
    })
    assert env["PK"] == "STAGE#assign_subtopics#topic:cardio"
    cmd = captured[0]
    assert "--topic" in cmd and "cardio" in cmd
    assert "--delta-pmids" in cmd and "1,2" in cmd
    assert "--emit-envelope" in cmd


# ---------- rollup handler ----------


def test_rollup_handler_passes_cwids(monkeypatch):
    captured = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        return subprocess.CompletedProcess(
            args=cmd, returncode=0,
            stdout='{"PK":"STAGE#rollup_by_cwid#GLOBAL","status":"complete"}\n',
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    env = rollup_handler.handler({"dirty_cwids": ["alice", "bob"]})
    assert env["PK"] == "STAGE#rollup_by_cwid#GLOBAL"
    cmd = captured[0]
    assert "--cwids" in cmd and "alice,bob" in cmd
    assert "--emit-envelope" in cmd


def test_rollup_handler_omits_cwids_when_empty(monkeypatch):
    captured = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        return subprocess.CompletedProcess(
            args=cmd, returncode=0,
            stdout='{"PK":"STAGE#rollup_by_cwid#GLOBAL","status":"complete"}\n',
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    rollup_handler.handler({"dirty_cwids": []})
    assert "--cwids" not in captured[0]


# ---------- ASL static validation ----------


def test_state_machine_asl_is_valid_json():
    asl_path = Path(__file__).parent.parent / "pipeline_hot" / "state_machine.asl.json"
    asl = json.loads(asl_path.read_text())
    assert asl["StartAt"] == "Orchestrate"
    states = asl["States"]
    for name in [
        "Orchestrate",
        "CheckLockOrProceed",
        "Score", "WriteScoreStageRow",
        "Assign", "WriteAssignStageRow",
        "Rollup", "WriteRollupStageRow",
        "WriteHotRunComplete", "WriteHotRunFailed", "NotifyError", "End",
    ]:
        assert name in states, f"missing state: {name}"


def test_state_machine_asl_has_catch_on_every_task():
    """Every Task state (other than the StageRow writers and Notify) must
    route failures to WriteHotRunFailed so completion never silently drops."""
    asl_path = Path(__file__).parent.parent / "pipeline_hot" / "state_machine.asl.json"
    asl = json.loads(asl_path.read_text())
    for name in ("Orchestrate", "Score", "Assign", "Rollup"):
        state = asl["States"][name]
        assert state["Type"] == "Task"
        catches = state.get("Catch") or []
        assert any(
            c.get("Next") == "WriteHotRunFailed" for c in catches
        ), f"{name} missing Catch → WriteHotRunFailed"

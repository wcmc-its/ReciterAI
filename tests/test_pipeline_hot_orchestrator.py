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
from datetime import datetime, timezone
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


# ---------- lock-collision: handler emits WARN alert (TF-04) ----------


def test_handler_lock_collision_dispatches_warn_alert(monkeypatch):
    """D-11: lock collision must emit a WARN alert (Slack) in addition to
    writing the STAGE# skipped row. logger.warning alone is not enough —
    the severity table promises a Slack notification on every collision.
    """
    # Stub the boto3 SFN client so is_state_machine_running returns True.
    fake_sfn = MagicMock()
    fake_sfn.list_executions.return_value = {
        "executions": [
            {"executionArn": "arn:aws:states:::execution:other-run"},
        ],
    }
    fake_boto3 = MagicMock()
    fake_boto3.client.return_value = fake_sfn
    monkeypatch.setitem(__import__("sys").modules, "boto3", fake_boto3)

    # Stub the DDB table so the skipped row write doesn't blow up.
    fake_table = MagicMock()
    monkeypatch.setattr(orch, "get_table", lambda *a, **kw: fake_table)

    # Capture the dispatch call.
    dispatch_calls: list = []
    monkeypatch.setattr(
        orch.alert,
        "dispatch",
        lambda *a, **kw: dispatch_calls.append((a, kw)) or {"slack": True, "issue": False},
    )

    result = orch.handler(
        {
            "state_machine_arn": "arn:aws:states:::stateMachine:reciterai-hot-path",
            "execution_arn": "arn:aws:states:::execution:self-run",
            "run_id": "smoke-1",
        }
    )

    assert result["status"] == "skipped"
    assert result["skip_reason"] == orch.SKIP_REASON_LOCKED
    assert len(dispatch_calls) == 1, "expected exactly one alert.dispatch call"
    args, kwargs = dispatch_calls[0]
    # Positional: severity, message, context
    assert args[0] == "WARN"
    assert "prior execution" in args[1].lower() or "running" in args[1].lower()
    ctx = args[2]
    assert ctx["skip_reason"] == orch.SKIP_REASON_LOCKED


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
    # dirty_cwids defaults empty; CheckRollupNeeded routes that past Rollup.
    assert sm_input["delta"]["dirty_cwids"] == []
    # rollup_input_hash is always present (BuildRollupSummary stamps it).
    assert isinstance(sm_input["delta"]["rollup_input_hash"], str)
    assert sm_input["delta"]["rollup_input_hash"]
    # assign_topics is NOT orchestrator-produced — the post-Score
    # DeriveDirtyTopics Task computes it (#119).
    assert "assign_topics" not in sm_input["delta"]


def test_build_state_machine_input_populates_dirty_cwids():
    sm_input = orch.build_state_machine_input(
        pmids=["1", "2"],
        last_run_at=None,
        started_at="2026-05-12T12:00:00Z",
        run_id="run-abc",
        dirty_cwids=["abc1001", "xyz1003"],
    )
    assert sm_input["delta"]["dirty_cwids"] == ["abc1001", "xyz1003"]


def test_rollup_input_hash_is_content_addressed_to_the_dirty_set():
    """Same dirty set -> same hash (order-independent); a different set ->
    a different hash. BuildRollupSummary stamps this on the hot_run row."""
    def _hash(cwids):
        return orch.build_state_machine_input(
            pmids=["1"], last_run_at=None, started_at="t", run_id="r",
            dirty_cwids=cwids,
        )["delta"]["rollup_input_hash"]

    assert _hash(["a", "b"]) == _hash(["b", "a"])
    assert _hash(["a", "b"]) != _hash(["a", "c"])
    assert _hash([]) != _hash(["a"])


def test_build_state_machine_input_folds_eligibility_into_additive():
    """#150 1b: eligibility PMIDs join the retry sweep in the cache-respecting
    additive set, are unioned into all_pmids (so Assign/TopTopic/Rollup cover
    them), and the run_kind records both recovery sources."""
    sm = orch.build_state_machine_input(
        pmids=["1", "2"],
        last_run_at="t", started_at="t", run_id="r",
        retry_pmids=["9"],
        eligibility_pmids=["8", "2"],  # "2" overlaps the date delta
    )
    d = sm["delta"]
    assert d["pmids"] == ["1", "2"]
    assert d["retry_pmids"] == ["9"]
    assert d["eligibility_pmids"] == ["8", "2"]
    assert d["additive_pmids"] == ["2", "8", "9"]  # sorted(retry ∪ eligibility)
    assert d["force_pmids"] == []
    assert d["all_pmids"] == ["1", "2", "8", "9"]
    assert sm["run_kind"] == "delta+retry+eligibility"


def test_build_state_machine_input_override_uses_force_and_bypasses_delta():
    """#150 1a: an override REPLACES the date delta — date pmids empty,
    force_pmids carries the explicit set, additive empty, run_kind=override."""
    sm = orch.build_state_machine_input(
        pmids=["1", "2"],           # ignored under override
        last_run_at="t", started_at="t", run_id="r",
        retry_pmids=["9"],          # ignored under override
        override_pmids=["77", "88"],
        dirty_cwids=["abc1001"],
    )
    d = sm["delta"]
    assert d["pmids"] == []
    assert d["size"] == 0
    assert d["force_pmids"] == ["77", "88"]
    assert d["additive_pmids"] == []
    assert d["all_pmids"] == ["77", "88"]
    assert d["dirty_cwids"] == ["abc1001"]
    assert sm["run_kind"] == "override"


# ---------- #150 1b: resolve_eligibility_sweep ----------


def _patch_sweep_sets(monkeypatch, *, enriched, scored, failed, quarantined,
                      invalid=()):
    monkeypatch.setattr(orch, "scan_impact_pmids_with_synopsis",
                        lambda *a, **k: list(enriched))
    monkeypatch.setattr(orch, "scan_invalid_pmids", lambda *a, **k: list(invalid))

    def _by_status(client, table, tv, status):
        return {"complete": scored, "failed": failed, "quarantined": quarantined}[status]

    monkeypatch.setattr(orch, "query_pmids_by_status", _by_status)


def test_eligibility_sweep_excludes_scored_failed_quarantined(monkeypatch):
    _patch_sweep_sets(
        monkeypatch,
        enriched=["1", "2", "3", "4", "5"],
        scored=["1"],        # already has TOPIC#/verdict → skip
        failed=["2"],        # retry sweep owns it → skip
        quarantined=["3"],   # parked → skip
    )
    eligible = orch.resolve_eligibility_sweep(
        MagicMock(), table_name="reciterai", taxonomy_version="taxonomy_v2",
        thresholds={"eligibility_sweep_enabled": True, "eligibility_sweep_max_pmids": 100},
    )
    assert eligible == ["4", "5"]


def test_eligibility_sweep_respects_cap(monkeypatch):
    _patch_sweep_sets(
        monkeypatch,
        enriched=[str(i) for i in range(10)],
        scored=[], failed=[], quarantined=[],
    )
    eligible = orch.resolve_eligibility_sweep(
        MagicMock(), table_name="reciterai", taxonomy_version="taxonomy_v2",
        thresholds={"eligibility_sweep_enabled": True, "eligibility_sweep_max_pmids": 3},
    )
    assert len(eligible) == 3
    # deterministic (sorted) so the same backlog drains the same prefix each run
    assert eligible == sorted([str(i) for i in range(10)])[:3]


def test_eligibility_sweep_disabled_returns_empty(monkeypatch):
    called = {"scan": False}
    monkeypatch.setattr(
        orch, "scan_impact_pmids_with_synopsis",
        lambda *a, **k: called.__setitem__("scan", True) or ["1"],
    )
    eligible = orch.resolve_eligibility_sweep(
        MagicMock(), table_name="reciterai", taxonomy_version="taxonomy_v2",
        thresholds={"eligibility_sweep_enabled": False},
    )
    assert eligible == []
    assert called["scan"] is False, "disabled sweep must not scan"


def test_eligibility_sweep_excludes_invalid(monkeypatch):
    """#150 item 3: ReciterDB-flagged invalid PMIDs (INVALID#pmid_ rows) are
    culled from the eligible set even when they carry a synopsis IMPACT# row —
    the enrichment cron synopsized them before the upstream DELETE, and that
    SQL DELETE never cleans the DDB IMPACT# rows the sweep scans."""
    _patch_sweep_sets(
        monkeypatch,
        enriched=["1", "2", "3", "4"],
        scored=["1"],
        failed=[], quarantined=[],
        invalid=["3"],   # flagged invalid → cull despite the synopsis
    )
    eligible = orch.resolve_eligibility_sweep(
        MagicMock(), table_name="reciterai", taxonomy_version="taxonomy_v2",
        thresholds={"eligibility_sweep_enabled": True, "eligibility_sweep_max_pmids": 100},
    )
    assert eligible == ["2", "4"]


# ---------- #150 1a: handler operator override ----------


def test_handler_override_bypasses_delta_and_sweeps(monkeypatch):
    """A manual_catchup SFn input with explicit pmids skips the date-delta,
    retry sweep, and eligibility sweep, and routes the set as force_pmids."""
    fake_table = MagicMock()
    fake_table.query.return_value = {"Items": []}  # resolve_last_successful_hot_run
    monkeypatch.setattr(orch, "get_table", lambda *a, **kw: fake_table)

    # None of these may run under override — make them explode if called.
    monkeypatch.setattr(orch, "resolve_delta_pmids",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("delta ran")))
    monkeypatch.setattr(orch, "resolve_retry_sweep",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("retry ran")))
    monkeypatch.setattr(orch, "resolve_eligibility_sweep",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("eligibility ran")))

    import utils.sql_queries as sq
    monkeypatch.setattr(sq, "get_cwids_for_pmids", lambda pmids: ["abc1001"])

    result = orch.handler({
        "run_id": "override-1",
        "sfn_input": {"initiated_by": "manual_catchup", "pmids": ["111", "222"]},
    })

    assert result["status"] == "ready"
    d = result["input"]["delta"]
    assert d["force_pmids"] == ["111", "222"]
    assert d["pmids"] == []
    assert d["all_pmids"] == ["111", "222"]
    assert d["dirty_cwids"] == ["abc1001"]
    assert result["input"]["run_kind"] == "override"


def test_handler_scheduled_input_is_not_an_override(monkeypatch):
    """The weekly cron input (`initiated_by:scheduled`, no pmids) must take
    the normal delta path, not the override branch."""
    fake_table = MagicMock()
    fake_table.query.return_value = {"Items": []}
    monkeypatch.setattr(orch, "get_table", lambda *a, **kw: fake_table)
    monkeypatch.setattr(orch, "resolve_delta_pmids", lambda *a, **k: ["5"])
    monkeypatch.setattr(orch, "resolve_retry_sweep",
                        lambda *a, **k: {"retry_pmids": [], "quarantined_pmids": []})
    monkeypatch.setattr(orch, "resolve_eligibility_sweep", lambda *a, **k: [])
    import utils.sql_queries as sq
    monkeypatch.setattr(sq, "get_cwids_for_pmids", lambda pmids: [])

    result = orch.handler({
        "run_id": "sched-1",
        "sfn_input": {"initiated_by": "scheduled", "trigger": "eventbridge:..."},
    })
    assert result["input"]["delta"]["force_pmids"] == []
    assert result["input"]["delta"]["pmids"] == ["5"]
    assert result["input"]["run_kind"] == "delta"


# ---------- score handler ----------


class _FakePopen:
    """Minimal stand-in for subprocess.Popen returned by tests.

    The Score handler streams subprocess output line-by-line then waits
    for the process to exit. Tests stub Popen with this class so they
    don't actually spawn anything.
    """

    def __init__(self, stdout: str, returncode: int = 0):
        self.stdout = iter(stdout.splitlines(keepends=True))
        self._rc = returncode

    def wait(self) -> int:
        return self._rc


def test_score_handler_invokes_subprocess_and_returns_envelope(monkeypatch):
    captured_cmds: list = []

    def fake_popen(cmd, **kwargs):
        captured_cmds.append(cmd)
        return _FakePopen(
            stdout='{"PK":"STAGE#score_publications#GLOBAL","status":"complete","input_hash":"abc","duration_ms":42,"cost_observed_usd":"0"}\n',
            returncode=0,
        )

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    env = score_handler.handler({
        "delta": {"pmids": ["1", "2"], "size": 2},
        "last_successful_hot_run_at": "2026-05-05T12:00:00Z",
    })
    # Returned envelope is DDB attribute-typed so the state machine's
    # WriteScoreStageRow can consume it directly via Item.$.
    assert env["PK"] == {"S": "STAGE#score_publications#GLOBAL"}
    assert env["status"] == {"S": "complete"}
    assert env["input_hash"] == {"S": "abc"}
    assert env["duration_ms"] == {"N": "42"}
    # cost_observed_usd arrives as a JSON string (Decimal serialized
    # via default=str), but the typed envelope must carry N not S.
    assert env["cost_observed_usd"] == {"N": "0"}
    cmd = captured_cmds[0]
    assert "--emit-envelope" in cmd
    assert "--delta-since" in cmd
    assert "2026-05-05T12:00:00Z" in cmd


def test_score_handler_raises_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        subprocess, "Popen",
        lambda cmd, **kw: _FakePopen(stdout="boom\n", returncode=42),
    )
    with pytest.raises(RuntimeError, match="exited 42"):
        score_handler.handler({"delta": {"pmids": []}})


def _capture_score_cmd(monkeypatch, event):
    captured: list = []
    envelope = '{"PK":"STAGE#score_publications#GLOBAL","status":"complete","input_hash":"a","duration_ms":1,"cost_observed_usd":"0"}\n'
    monkeypatch.setattr(
        subprocess, "Popen",
        lambda cmd, **kw: captured.append(cmd) or _FakePopen(stdout=envelope),
    )
    score_handler.handler(event)
    return captured[0]


def test_score_handler_additive_pmids_use_additive(monkeypatch):
    """#150 1b: additive_pmids (retry ∪ eligibility) are passed cache-respecting
    (--additive) on top of the date delta — never --force."""
    cmd = _capture_score_cmd(monkeypatch, {
        "delta": {"pmids": ["1"], "size": 1, "additive_pmids": ["8", "9"]},
        "last_successful_hot_run_at": "2026-05-05T12:00:00Z",
    })
    assert "--delta-since" in cmd
    assert "--additive" in cmd
    assert "--force" not in cmd
    assert "8,9" in cmd


def test_score_handler_force_pmids_use_force_and_no_delta_since(monkeypatch):
    """#150 1a: force_pmids (operator override) are scored with --force and the
    date delta is suppressed (no --delta-since) — an override REPLACES it."""
    cmd = _capture_score_cmd(monkeypatch, {
        "delta": {"pmids": [], "size": 0, "force_pmids": ["77", "88"],
                  "additive_pmids": []},
        "last_successful_hot_run_at": "2026-05-05T12:00:00Z",
    })
    assert "--force" in cmd
    assert "77,88" in cmd
    assert "--delta-since" not in cmd
    assert "--additive" not in cmd


def test_score_handler_additive_falls_back_to_retry_pmids(monkeypatch):
    """Backward-compat: a pre-1b envelope with only retry_pmids (no
    additive_pmids) still scores them additively."""
    cmd = _capture_score_cmd(monkeypatch, {
        "delta": {"pmids": ["1"], "size": 1, "retry_pmids": ["9"]},
        "last_successful_hot_run_at": "2026-05-05T12:00:00Z",
    })
    assert "--additive" in cmd
    assert "9" in cmd


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
    # DDB attribute-typed return so WriteAssignStageRow can consume it.
    assert env["PK"] == {"S": "STAGE#assign_subtopics#topic:cardio"}
    cmd = captured[0]
    assert "--topic" in cmd and "cardio" in cmd
    assert "--delta-pmids" in cmd and "1,2" in cmd
    assert "--emit-envelope" in cmd


# ---------- rollup handler ----------


def test_rollup_handler_routes_cwid_event(monkeypatch):
    captured = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        return subprocess.CompletedProcess(
            args=cmd, returncode=0,
            stdout='{"PK":"STAGE#rollup_by_cwid#cwid:abc1234","status":"complete"}\n',
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    env = rollup_handler.handler({"cwid": "abc1234"})
    # DDB attribute-typed return so WriteRollupStageRow can consume it.
    assert env["PK"] == {"S": "STAGE#rollup_by_cwid#cwid:abc1234"}
    cmd = captured[0]
    assert "--cwid" in cmd and "abc1234" in cmd
    assert "--emit-envelope" in cmd


def test_rollup_handler_cwid_event_strips_whitespace(monkeypatch):
    captured = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        return subprocess.CompletedProcess(
            args=cmd, returncode=0,
            stdout='{"PK":"STAGE#rollup_by_cwid#cwid:abc","status":"complete"}\n',
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    rollup_handler.handler({"cwid": "  abc  "})
    cmd = captured[0]
    assert "abc" in cmd
    assert "  abc  " not in cmd


def test_rollup_handler_empty_cwid_raises(monkeypatch):
    # The onboarding state machine must invoke Rollup with the workflow's
    # CWID; an empty 'cwid' is a contract violation, not a no-op.
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: pytest.fail("subprocess must not run for empty cwid"),
    )
    with pytest.raises(ValueError, match="empty 'cwid'"):
        rollup_handler.handler({"cwid": ""})
    with pytest.raises(ValueError, match="empty 'cwid'"):
        rollup_handler.handler({"cwid": "   "})


def test_rollup_handler_missing_cwid_raises(monkeypatch):
    """An event with no 'cwid' key is a contract violation — both callers
    (the hot RollupFanOut Map and the onboarding Rollup stage) send
    {cwid}. subprocess must not run."""
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: pytest.fail("subprocess must not run for missing cwid"),
    )
    with pytest.raises(ValueError, match="no 'cwid'"):
        rollup_handler.handler({})
    with pytest.raises(ValueError, match="no 'cwid'"):
        rollup_handler.handler({"dirty_cwids": ["alice"]})


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
        "DeriveDirtyTopics", "CheckAssignNeeded",
        "AssignSkipped", "AssignFanOut", "BuildAssignSummary",
        "TopTopic", "WriteTopTopicStageRow",
        "CheckRollupNeeded",
        "RollupSkipped", "RollupFanOut", "BuildRollupSummary",
        "WriteHotRunComplete", "WriteHotRunFailed", "NotifyError", "End",
    ]:
        assert name in states, f"missing state: {name}"


def test_state_machine_asl_has_catch_on_every_task():
    """Every Task / Map state that does real work must route failures to
    WriteHotRunFailed so completion never silently drops."""
    asl_path = Path(__file__).parent.parent / "pipeline_hot" / "state_machine.asl.json"
    asl = json.loads(asl_path.read_text())
    for name, expected_type in (
        ("Orchestrate", "Task"),
        ("Score", "Task"),
        ("DeriveDirtyTopics", "Task"),
        ("TopTopic", "Task"),
        ("AssignFanOut", "Map"),
        ("RollupFanOut", "Map"),
    ):
        state = asl["States"][name]
        assert state["Type"] == expected_type
        catches = state.get("Catch") or []
        assert any(
            c.get("Next") == "WriteHotRunFailed" for c in catches
        ), f"{name} missing Catch -> WriteHotRunFailed"


def test_state_machine_top_topic_consumes_all_pmids():
    """TopTopic must score the union of date-delta + retry PMIDs so a retry
    PMID that scores successfully also gets its top topic recomputed."""
    asl_path = Path(__file__).parent.parent / "pipeline_hot" / "state_machine.asl.json"
    asl = json.loads(asl_path.read_text())
    params = asl["States"]["TopTopic"]["Parameters"]
    assert params["delta_pmids.$"] == "$.orchestrate.input.delta.all_pmids"


def test_state_machine_hot_run_row_records_retry_metadata():
    """The hot_run STAGE# row distinguishes a retry-sweep run from a pure
    delta run via retry_size + run_kind."""
    asl_path = Path(__file__).parent.parent / "pipeline_hot" / "state_machine.asl.json"
    asl = json.loads(asl_path.read_text())
    item = asl["States"]["WriteHotRunComplete"]["Parameters"]["Item"]
    assert "retry_size" in item
    assert item["run_kind"]["S.$"] == "$.orchestrate.input.run_kind"


# ---------- ASL: DeriveDirtyTopics + the Assign / Rollup Maps (#119) ----------


def test_derive_dirty_topics_feeds_the_assign_fanout():
    """DeriveDirtyTopics runs post-Score and writes $.assign; CheckAssignNeeded
    and AssignFanOut both read that array, not the orchestrator's input."""
    asl_path = Path(__file__).parent.parent / "pipeline_hot" / "state_machine.asl.json"
    states = json.loads(asl_path.read_text())["States"]
    assert states["WriteScoreStageRow"]["Next"] == "DeriveDirtyTopics"
    ddt = states["DeriveDirtyTopics"]
    assert ddt["Type"] == "Task"
    assert ddt["ResultPath"] == "$.assign"
    assert ddt["Parameters"]["pmids.$"] == "$.orchestrate.input.delta.all_pmids"
    assert (
        states["CheckAssignNeeded"]["Choices"][0]["Variable"]
        == "$.assign.assign_topics[0]"
    )
    fan = states["AssignFanOut"]
    assert fan["Type"] == "Map"
    assert fan["ItemsPath"] == "$.assign.assign_topics"


def test_rollup_fanout_maps_over_dirty_cwids():
    """RollupFanOut iterates the orchestrator's dirty_cwids (bare CWID strings).
    ItemSelector wraps each as {cwid: <value>} so RollupOne's ResultPath has an
    object to merge into — a ReferencePath on a scalar item is a
    States.ReferencePathConflict (#150/#153). RollupOne then invokes the rollup
    Lambda's per-CWID {cwid} mode reading the wrapped field."""
    asl_path = Path(__file__).parent.parent / "pipeline_hot" / "state_machine.asl.json"
    states = json.loads(asl_path.read_text())["States"]
    fan = states["RollupFanOut"]
    assert fan["Type"] == "Map"
    assert fan["ItemsPath"] == "$.orchestrate.input.delta.dirty_cwids"
    assert fan["ItemSelector"] == {"cwid.$": "$$.Map.Item.Value"}
    assert fan["ItemProcessor"]["States"]["RollupOne"]["Parameters"] == {
        "cwid.$": "$.cwid"
    }


def test_build_summary_states_feed_hot_run_complete():
    """The Map outputs are arrays; BuildAssignSummary / BuildRollupSummary
    collapse each to the single envelope WriteHotRunComplete reads."""
    asl_path = Path(__file__).parent.parent / "pipeline_hot" / "state_machine.asl.json"
    states = json.loads(asl_path.read_text())["States"]
    assert states["BuildAssignSummary"]["ResultPath"] == "$.assign_envelope"
    assert states["BuildRollupSummary"]["ResultPath"] == "$.rollup_envelope"
    item = states["WriteHotRunComplete"]["Parameters"]["Item"]
    assert item["assign_input_hash"]["S.$"] == "$.assign_envelope.input_hash.S"
    assert item["rollup_input_hash"]["S.$"] == "$.rollup_envelope.input_hash.S"


# ---------- retry sweep: state-machine input shape ----------


def test_build_state_machine_input_includes_retry_fields():
    sm_input = orch.build_state_machine_input(
        pmids=["1", "2"],
        last_run_at=None,
        started_at="2026-05-16T12:00:00Z",
        run_id="run-x",
        retry_pmids=["2", "9"],
    )
    assert sm_input["run_kind"] == "delta+retry"
    assert sm_input["delta"]["retry_pmids"] == ["2", "9"]
    assert sm_input["delta"]["retry_size"] == 2
    # all_pmids is the deduplicated union — "2" is in both lists.
    assert sm_input["delta"]["all_pmids"] == ["1", "2", "9"]
    # delta.size stays a pure date-delta count (the hot_run row reads it).
    assert sm_input["delta"]["size"] == 2


def test_build_state_machine_input_run_kind_delta_when_no_retry():
    sm_input = orch.build_state_machine_input(
        pmids=["1"],
        last_run_at=None,
        started_at="2026-05-16T12:00:00Z",
        run_id="run-x",
    )
    assert sm_input["run_kind"] == "delta"
    assert sm_input["delta"]["retry_pmids"] == []
    assert sm_input["delta"]["retry_size"] == 0
    assert sm_input["delta"]["all_pmids"] == ["1"]


def test_current_taxonomy_version_reads_taxonomy_file():
    assert orch.current_taxonomy_version() == "taxonomy_v2"


# ---------- resolve_retry_sweep ----------

_SWEEP_THRESHOLDS = {
    "retry_sweep_max_pmids": 2,
    "retry_sweep_quarantine_after": 3,
    "retry_sweep_min_age_days": 7,
}
_SWEEP_NOW = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def test_retry_sweep_no_failed_rows_is_a_noop(monkeypatch):
    monkeypatch.setattr(orch, "query_failed_pmids", lambda *a, **k: [])
    rows_mock = MagicMock()
    monkeypatch.setattr(orch, "get_processing_rows", rows_mock)
    result = orch.resolve_retry_sweep(
        MagicMock(), table_name="reciterai",
        taxonomy_version="taxonomy_v2", thresholds=_SWEEP_THRESHOLDS,
        now_dt=_SWEEP_NOW,
    )
    assert result == {"retry_pmids": [], "quarantined_pmids": []}
    # No failed rows → no second round-trip for full row attributes.
    rows_mock.assert_not_called()


def test_retry_sweep_partitions_candidate_fresh_and_quarantine(monkeypatch):
    monkeypatch.setattr(
        orch, "query_failed_pmids",
        lambda *a, **k: ["old1", "fresh1", "exhausted1"],
    )
    monkeypatch.setattr(orch, "get_processing_rows", lambda *a, **k: {
        # 15 days old, under retry threshold → retry candidate
        "old1": {"retry_count": 1,
                 "failed_at": _iso(datetime(2026, 5, 1, tzinfo=timezone.utc)),
                 "error": "transient"},
        # 1 day old → too fresh, skipped this week
        "fresh1": {"retry_count": 1,
                   "failed_at": _iso(datetime(2026, 5, 15, tzinfo=timezone.utc)),
                   "error": "transient"},
        # retry_count == quarantine_after → quarantined
        "exhausted1": {"retry_count": 3,
                       "failed_at": _iso(datetime(2026, 5, 1, tzinfo=timezone.utc)),
                       "error": "content-filtered"},
    })
    q_calls: list = []
    monkeypatch.setattr(
        orch, "quarantine_pmid",
        lambda c, t, pmid, **kw: q_calls.append((pmid, kw)),
    )
    result = orch.resolve_retry_sweep(
        MagicMock(), table_name="reciterai",
        taxonomy_version="taxonomy_v2", thresholds=_SWEEP_THRESHOLDS,
        now_dt=_SWEEP_NOW,
    )
    assert result["retry_pmids"] == ["old1"]
    assert result["quarantined_pmids"] == ["exhausted1"]
    # quarantine_pmid received the row's retry_count + last error.
    assert q_calls == [("exhausted1", {
        "retry_count": 3,
        "last_error": "content-filtered",
        "taxonomy_version": "taxonomy_v2",
    })]


def test_retry_sweep_caps_candidates_oldest_first(monkeypatch):
    monkeypatch.setattr(
        orch, "query_failed_pmids",
        lambda *a, **k: ["p_new", "p_oldest", "p_mid"],
    )
    monkeypatch.setattr(orch, "get_processing_rows", lambda *a, **k: {
        "p_new": {"retry_count": 0,
                  "failed_at": _iso(datetime(2026, 5, 8, tzinfo=timezone.utc))},
        "p_oldest": {"retry_count": 0,
                     "failed_at": _iso(datetime(2026, 4, 1, tzinfo=timezone.utc))},
        "p_mid": {"retry_count": 0,
                  "failed_at": _iso(datetime(2026, 4, 20, tzinfo=timezone.utc))},
    })
    monkeypatch.setattr(orch, "quarantine_pmid", lambda *a, **k: None)
    result = orch.resolve_retry_sweep(
        MagicMock(), table_name="reciterai",
        taxonomy_version="taxonomy_v2", thresholds=_SWEEP_THRESHOLDS,  # cap = 2
        now_dt=_SWEEP_NOW,
    )
    # All three are >7d old; cap=2 keeps the two oldest, oldest first.
    assert result["retry_pmids"] == ["p_oldest", "p_mid"]


def test_retry_sweep_legacy_row_without_failed_at_is_eligible(monkeypatch):
    monkeypatch.setattr(orch, "query_failed_pmids", lambda *a, **k: ["legacy"])
    monkeypatch.setattr(orch, "get_processing_rows", lambda *a, **k: {
        # No failed_at — the row predates mark_processing_failed.
        "legacy": {"retry_count": 0},
    })
    monkeypatch.setattr(orch, "quarantine_pmid", lambda *a, **k: None)
    result = orch.resolve_retry_sweep(
        MagicMock(), table_name="reciterai",
        taxonomy_version="taxonomy_v2", thresholds=_SWEEP_THRESHOLDS,
        now_dt=_SWEEP_NOW,
    )
    assert result["retry_pmids"] == ["legacy"]


# ---------- score handler: additive retry passthrough ----------


def test_score_handler_passes_retry_pmids(monkeypatch):
    captured_cmds: list = []

    def fake_popen(cmd, **kwargs):
        captured_cmds.append(cmd)
        return _FakePopen(
            stdout='{"PK":"STAGE#score_publications#GLOBAL","status":"complete","input_hash":"abc","duration_ms":1,"cost_observed_usd":"0"}\n',
            returncode=0,
        )

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    score_handler.handler({
        "delta": {"pmids": ["1"], "size": 1,
                  "retry_pmids": ["77", "88"], "retry_size": 2},
        "last_successful_hot_run_at": "2026-05-05T12:00:00Z",
    })
    cmd = captured_cmds[0]
    # The retry sweep is passed as --pmids ... --additive (#89).
    assert "--pmids" in cmd
    assert "--additive" in cmd
    assert "77,88" in cmd
    # The sweep is additive — --delta-since is still passed.
    assert "--delta-since" in cmd


def test_score_handler_omits_retry_pmids_when_empty(monkeypatch):
    captured_cmds: list = []

    def fake_popen(cmd, **kwargs):
        captured_cmds.append(cmd)
        return _FakePopen(
            stdout='{"PK":"x","status":"complete","input_hash":"a","duration_ms":1,"cost_observed_usd":"0"}\n',
            returncode=0,
        )

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    score_handler.handler({"delta": {"pmids": ["1"], "size": 1}})
    assert "--additive" not in captured_cmds[0]


# ---------- score handler: onboarding {pmids} event (#80) ----------


def test_score_handler_pmids_event_routes_to_pmids_flag(monkeypatch):
    captured_cmds: list = []

    def fake_popen(cmd, **kwargs):
        captured_cmds.append(cmd)
        return _FakePopen(
            stdout='{"PK":"STAGE#score_publications#GLOBAL","status":"complete","input_hash":"abc","duration_ms":1,"cost_observed_usd":"0"}\n',
            returncode=0,
        )

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    env = score_handler.handler({"pmids": ["111", "222", "333"]})
    # DDB attribute-typed return so WriteScoreStageRow can consume it.
    assert env["PK"] == {"S": "STAGE#score_publications#GLOBAL"}
    cmd = captured_cmds[0]
    assert "--pmids" in cmd
    assert "111,222,333" in cmd
    assert "--emit-envelope" in cmd
    # An onboarding event must NOT carry the hot-path delta flags or the
    # --pmids modifiers — plain --pmids only.
    assert "--delta-since" not in cmd
    assert "--additive" not in cmd
    assert "--force" not in cmd
    assert "--allow-cost-override" not in cmd


def test_score_handler_pmids_event_passes_cost_override(monkeypatch):
    captured_cmds: list = []

    def fake_popen(cmd, **kwargs):
        captured_cmds.append(cmd)
        return _FakePopen(
            stdout='{"PK":"x","status":"complete","input_hash":"a","duration_ms":1,"cost_observed_usd":"0"}\n',
            returncode=0,
        )

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    score_handler.handler({"pmids": ["1"], "allow_cost_override": True})
    assert "--allow-cost-override" in captured_cmds[0]


def test_score_handler_empty_pmids_event_raises(monkeypatch):
    # The onboarding orchestrator only invokes Score with a non-empty work
    # set; an empty 'pmids' list is a contract violation, not a no-op.
    monkeypatch.setattr(
        subprocess, "Popen",
        lambda *a, **kw: pytest.fail("subprocess must not run for empty pmids"),
    )
    with pytest.raises(ValueError, match="empty 'pmids'"):
        score_handler.handler({"pmids": []})


# ---------- handler: ready path derives dirty_cwids (#119) ----------


def test_handler_ready_path_derives_and_threads_dirty_cwids(monkeypatch):
    """The ready path runs get_cwids_for_pmids over the delta+retry union
    and threads the result into delta.dirty_cwids (#119)."""
    monkeypatch.delenv("RECITERAI_HOT_STATE_MACHINE_ARN", raising=False)
    monkeypatch.setattr(orch, "get_table", lambda *a, **k: MagicMock())
    monkeypatch.setattr(
        orch, "resolve_last_successful_hot_run", lambda t: "2026-05-05T12:00:00Z"
    )
    monkeypatch.setattr(orch, "resolve_delta_pmids", lambda *a, **k: ["1", "2"])
    monkeypatch.setattr(orch, "get_dynamo_client", lambda: MagicMock())
    monkeypatch.setattr(
        orch, "resolve_retry_sweep",
        lambda *a, **k: {"retry_pmids": ["9"], "quarantined_pmids": []},
    )
    # The ready path also runs the 1b eligibility sweep (#150); stub it so the
    # handler test does not drive the real full-table DDB scans against the
    # MagicMock client. Empty → dirty_cwids derive over the delta+retry union.
    monkeypatch.setattr(orch, "resolve_eligibility_sweep", lambda *a, **k: [])

    seen: list = []

    def fake_cwids(pmids):
        seen.append(list(pmids))
        return ["cwidX", "cwidY"]

    monkeypatch.setattr("utils.sql_queries.get_cwids_for_pmids", fake_cwids)

    result = orch.handler({"run_id": "run-x"})  # no state_machine_arn -> no lock check

    assert result["status"] == "ready"
    # Derived over the sorted delta+retry union.
    assert seen == [["1", "2", "9"]]
    assert result["input"]["delta"]["dirty_cwids"] == ["cwidX", "cwidY"]

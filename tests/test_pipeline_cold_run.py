"""Phase 10 T8 — pipeline_cold.run operator CLI.

Covers:
- default_cold_stages encodes the canonical seven stages in the order
  the CONTEXT stage-assignment table prescribes.
- select_stages handles --from-stage and rejects unknown names.
- run_stage maps subprocess returncodes to StageOutcome status.
- main() short-circuits on the first failure and writes a STAGE#cold_run
  failed row carrying the failed_stage + completed_stages + initiated_by.
- main() writes a STAGE#cold_run complete row when all stages succeed,
  with initiated_by propagated through and stage_names captured.
- --dry-run prints the plan and exits without invoking stages.
- --skip-stage-write keeps the STAGE#cold_run DynamoDB write off.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pipeline_cold import run as cold


# ---------- default_cold_stages ----------


def test_default_cold_stages_in_canonical_order():
    names = [s.name for s in cold.default_cold_stages()]
    assert names == [
        "score",
        "assign",
        "discover",
        "relabel",
        "rollup",
        "backfill_spotlight",
        "publish_hierarchy",
    ]


def test_default_cold_stages_all_have_commands():
    for s in cold.default_cold_stages():
        assert s.command, f"stage {s.name} has empty command"


# ---------- select_stages ----------


def test_select_stages_full_when_from_none():
    all_stages = cold.default_cold_stages()
    assert cold.select_stages(all_stages, from_stage=None) == all_stages


def test_select_stages_resumes_from_named_stage():
    all_stages = cold.default_cold_stages()
    selected = cold.select_stages(all_stages, from_stage="rollup")
    assert [s.name for s in selected] == [
        "rollup", "backfill_spotlight", "publish_hierarchy"
    ]


def test_select_stages_raises_on_unknown_stage():
    with pytest.raises(ValueError, match="not a known cold stage"):
        cold.select_stages(cold.default_cold_stages(), from_stage="bogus")


# ---------- run_stage ----------


def test_run_stage_returns_complete_on_rc0(tmp_path: Path):
    stage = cold.ColdStage("noop", ["true"])
    runner = MagicMock(return_value=subprocess.CompletedProcess(
        args=stage.command, returncode=0, stdout="", stderr="",
    ))
    outcome = cold.run_stage(stage, repo_root=tmp_path, runner=runner)
    assert outcome.status == "complete"
    assert outcome.returncode == 0


def test_run_stage_returns_failed_with_stderr_tail(tmp_path: Path):
    stage = cold.ColdStage("boom", ["false"])
    runner = MagicMock(return_value=subprocess.CompletedProcess(
        args=stage.command, returncode=1, stdout="",
        stderr="error: something bad",
    ))
    outcome = cold.run_stage(stage, repo_root=tmp_path, runner=runner)
    assert outcome.status == "failed"
    assert outcome.returncode == 1
    assert "something bad" in outcome.stderr_tail


# ---------- compute_cold_run_input_hash ----------


def test_input_hash_depends_on_initiated_by():
    stages = cold.default_cold_stages()
    h_op = cold.compute_cold_run_input_hash(
        started_at="2026-05-12T12:00:00Z", stages=stages, initiated_by="operator"
    )
    h_drift = cold.compute_cold_run_input_hash(
        started_at="2026-05-12T12:00:00Z", stages=stages, initiated_by="drift_alert"
    )
    assert h_op != h_drift


# ---------- main() end-to-end (mocked subprocess + DDB) ----------


def _ok_proc(cmd):
    return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")


def _bad_proc(cmd):
    return subprocess.CompletedProcess(
        args=cmd, returncode=7, stdout="", stderr="boom in stage",
    )


def test_main_writes_complete_row_with_initiated_by(monkeypatch):
    """All stages green → STAGE#cold_run#GLOBAL complete row carries
    initiated_by + stage_names."""
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _ok_proc(cmd))

    captured: list = []
    fake_table = MagicMock()
    fake_table.put_item.side_effect = lambda Item: captured.append(Item) or {}
    monkeypatch.setattr(cold, "get_table", lambda *a, **k: fake_table)

    rc = cold.main(["--initiated-by", "drift_alert"])

    assert rc == 0
    cold_run_rows = [
        it for it in captured if it.get("PK") == "STAGE#cold_run#GLOBAL"
    ]
    assert len(cold_run_rows) == 1
    row = cold_run_rows[0]
    assert row["status"] == "complete"
    assert row["initiated_by"] == "drift_alert"
    assert row["stage_names"] == [
        "score", "assign", "discover", "relabel", "rollup",
        "backfill_spotlight", "publish_hierarchy",
    ]


def test_main_short_circuits_on_first_failure(monkeypatch):
    """Second stage fails; cold_run failed row carries failed_stage +
    completed_stages list (preceding stages only)."""
    call_count = {"n": 0}

    def fake_run(cmd, **kw):
        call_count["n"] += 1
        if call_count["n"] == 2:
            return _bad_proc(cmd)
        return _ok_proc(cmd)

    monkeypatch.setattr(subprocess, "run", fake_run)

    captured: list = []
    fake_table = MagicMock()
    fake_table.put_item.side_effect = lambda Item: captured.append(Item) or {}
    monkeypatch.setattr(cold, "get_table", lambda *a, **k: fake_table)

    rc = cold.main(["--initiated-by", "operator"])
    assert rc == 7

    rows = [it for it in captured if it.get("PK") == "STAGE#cold_run#GLOBAL"]
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "failed"
    assert row["failure_details"]["failed_stage"] == "assign"
    assert row["failure_details"]["completed_stages"] == ["score"]
    assert row["failure_details"]["initiated_by"] == "operator"
    # No subsequent stages were invoked.
    assert call_count["n"] == 2


def test_main_dry_run_invokes_nothing(monkeypatch):
    called = MagicMock()
    monkeypatch.setattr(subprocess, "run", called)
    # Even if get_table is called, no writes should happen in dry-run.
    fake_table = MagicMock()
    monkeypatch.setattr(cold, "get_table", lambda *a, **k: fake_table)

    rc = cold.main(["--dry-run"])
    assert rc == 0
    called.assert_not_called()
    fake_table.put_item.assert_not_called()


def test_main_from_stage_skips_earlier_stages(monkeypatch):
    invoked_commands: list = []

    def fake_run(cmd, **kw):
        invoked_commands.append(cmd)
        return _ok_proc(cmd)

    monkeypatch.setattr(subprocess, "run", fake_run)
    fake_table = MagicMock()
    monkeypatch.setattr(cold, "get_table", lambda *a, **k: fake_table)

    rc = cold.main(["--from-stage", "rollup"])
    assert rc == 0
    # rollup + backfill_spotlight + publish_hierarchy = 3 invocations
    assert len(invoked_commands) == 3
    # Confirm by inspecting one command string
    flat = [" ".join(c) for c in invoked_commands]
    assert any("rollup_by_cwid" in s for s in flat)
    assert any("backfill_spotlight" in s for s in flat)
    assert any("pipeline_hierarchy.publish" in s for s in flat)


def test_main_unknown_from_stage_exits_2(monkeypatch):
    monkeypatch.setattr(subprocess, "run", MagicMock())
    rc = cold.main(["--from-stage", "nonsense"])
    assert rc == 2


def test_main_skip_stage_write_does_not_touch_dynamodb(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _ok_proc(cmd))
    table_factory = MagicMock()
    monkeypatch.setattr(cold, "get_table", table_factory)
    rc = cold.main(["--skip-stage-write"])
    assert rc == 0
    table_factory.assert_not_called()


def test_main_rejects_invalid_initiated_by():
    # argparse rejects with SystemExit before any work happens.
    with pytest.raises(SystemExit):
        cold.main(["--initiated-by", "rogue"])

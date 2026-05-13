"""Tests for pipeline_feedback.cli — sweep + render subcommands, pagination, cold-stage registration.

Phase 12 Task 3 — feedback consumer CLI.
"""
from __future__ import annotations

import sys
import subprocess
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sweep_result(run_id: str = "test-run-id", triggered_by: str = "operator"):
    from pipeline_feedback.sweep import FeedbackSweepRun
    return FeedbackSweepRun(
        source_sweep_run_id=run_id,
        triggered_by=triggered_by,
        started_at="2026-01-01T00:00:00Z",
        since="2026-01-01T00:00:00Z",
        candidate_topics=[],
        recluster_recommendations=[],
        spotlight_diagnostics=[],
    )


def _mock_table():
    t = MagicMock()
    t.scan.return_value = {"Items": []}
    return t


# ---------------------------------------------------------------------------
# test_sweep_subcommand_dispatches_to_run_sweep
# ---------------------------------------------------------------------------

def test_sweep_subcommand_dispatches_to_run_sweep():
    from pipeline_feedback.cli import main
    mock_result = _make_sweep_result()

    with patch("pipeline_feedback.cli.run_sweep", return_value=mock_result) as mock_sweep, \
         patch("pipeline_feedback.cli._default_get_table", return_value=_mock_table()):
        rc = main(["sweep", "--since", "7"])

    assert rc == 0
    mock_sweep.assert_called_once()
    call_kwargs = mock_sweep.call_args[1]
    assert call_kwargs["triggered_by"] == "operator"
    # since should be ~7 days ago
    since_arg: datetime = call_kwargs["since"]
    expected = datetime.now(timezone.utc) - timedelta(days=7)
    diff = abs((since_arg - expected).total_seconds())
    assert diff < 5, f"since was {since_arg}, expected ~7 days ago"


# ---------------------------------------------------------------------------
# test_sweep_subcommand_default_run_id_minted
# ---------------------------------------------------------------------------

def test_sweep_subcommand_default_run_id_minted():
    import re
    from pipeline_feedback.cli import main
    mock_result = _make_sweep_result()

    with patch("pipeline_feedback.cli.run_sweep", return_value=mock_result) as mock_sweep, \
         patch("pipeline_feedback.cli._default_get_table", return_value=_mock_table()):
        main(["sweep", "--since", "7"])

    call_kwargs = mock_sweep.call_args[1]
    run_id = call_kwargs.get("run_id")
    assert run_id is not None
    assert re.match(r"^[0-9a-f-]{36}$", run_id), f"run_id={run_id!r} is not a UUID"


# ---------------------------------------------------------------------------
# test_sweep_subcommand_explicit_run_id_threaded
# ---------------------------------------------------------------------------

def test_sweep_subcommand_explicit_run_id_threaded():
    from pipeline_feedback.cli import main
    mock_result = _make_sweep_result(run_id="specific-id")

    with patch("pipeline_feedback.cli.run_sweep", return_value=mock_result) as mock_sweep, \
         patch("pipeline_feedback.cli._default_get_table", return_value=_mock_table()):
        main(["sweep", "--since", "7", "--run-id", "specific-id"])

    call_kwargs = mock_sweep.call_args[1]
    assert call_kwargs["run_id"] == "specific-id"


# ---------------------------------------------------------------------------
# test_render_subcommand_reads_run_id_rows
# ---------------------------------------------------------------------------

def test_render_subcommand_reads_run_id_rows(capsys):
    from pipeline_feedback.cli import main

    rows = [
        {
            "PK": "CANDIDATE_TOPIC#test_topic",
            "SK": "GLOBAL",
            "record_type": "CANDIDATE_TOPIC",
            "slug": "test_topic",
            "proposed_label": "Test Topic",
            "source_pmids": ["12345"],
            "sonnet_rationale": "Strong signal",
            "source_sweep_run_id": "run-abc",
            "triggered_by": "operator",
            "truncated": False,
            "created_at": "2026-01-01T00:00:00Z",
            "source_stage": "feedback.sweep",
        }
    ]
    mock_table = _mock_table()
    mock_table.scan.return_value = {"Items": rows}

    with patch("pipeline_feedback.cli._default_get_table", return_value=mock_table):
        rc = main(["render", "run-abc"])

    assert rc == 0
    mock_table.scan.assert_called_once()
    # Verify the FilterExpression was set (checking the call used Attr filter)
    scan_call_kwargs = mock_table.scan.call_args[1]
    assert "FilterExpression" in scan_call_kwargs


# ---------------------------------------------------------------------------
# test_fetch_rows_by_run_id_paginates  (W-5 fix)
# ---------------------------------------------------------------------------

def test_fetch_rows_by_run_id_paginates():
    from pipeline_feedback.cli import _fetch_rows_by_run_id

    table = MagicMock()
    page1_items = [{"PK": "CANDIDATE_TOPIC#a", "source_sweep_run_id": "r1"}]
    page2_items = [{"PK": "CANDIDATE_TOPIC#b", "source_sweep_run_id": "r1"}]

    table.scan.side_effect = [
        {"Items": page1_items, "LastEvaluatedKey": {"PK": "CANDIDATE_TOPIC#a"}},
        {"Items": page2_items},
    ]

    results = _fetch_rows_by_run_id(table, "r1")

    assert len(results) == 2
    assert table.scan.call_count == 2

    # Second call must have ExclusiveStartKey set
    second_call_kwargs = table.scan.call_args_list[1][1]
    assert "ExclusiveStartKey" in second_call_kwargs
    assert second_call_kwargs["ExclusiveStartKey"] == {"PK": "CANDIDATE_TOPIC#a"}


# ---------------------------------------------------------------------------
# test_fetch_rows_by_run_id_no_pagination_when_single_page
# ---------------------------------------------------------------------------

def test_fetch_rows_by_run_id_no_pagination_when_single_page():
    from pipeline_feedback.cli import _fetch_rows_by_run_id

    table = MagicMock()
    table.scan.return_value = {"Items": [{"PK": "CANDIDATE_TOPIC#a"}]}

    results = _fetch_rows_by_run_id(table, "r1")

    assert len(results) == 1
    assert table.scan.call_count == 1
    # No ExclusiveStartKey on first (and only) call
    call_kwargs = table.scan.call_args[1]
    assert "ExclusiveStartKey" not in call_kwargs


# ---------------------------------------------------------------------------
# test_exit_codes_documented_in_module_docstring
# ---------------------------------------------------------------------------

def test_exit_codes_documented_in_module_docstring():
    import importlib
    import pipeline_feedback.cli as cli_mod
    doc = cli_mod.__doc__ or ""
    assert "Exit codes:" in doc, "Module docstring must contain 'Exit codes:'"
    # Must document at least 0, 2, 3
    for code in ("0", "2", "3"):
        assert code in doc, f"Exit code {code} must be documented in module docstring"


# ---------------------------------------------------------------------------
# test_argparse_errors_return_exit_code_2
# ---------------------------------------------------------------------------

def test_argparse_errors_return_exit_code_2():
    from pipeline_feedback.cli import main

    with pytest.raises(SystemExit) as exc_info:
        main(["badsubcmd"])

    assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# test_main_invokable_via_dunder_main
# ---------------------------------------------------------------------------

def test_main_invokable_via_dunder_main():
    result = subprocess.run(
        [sys.executable, "-m", "pipeline_feedback", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    combined = result.stdout + result.stderr
    assert "sweep" in combined, f"'sweep' not in help: {combined}"
    assert "render" in combined, f"'render' not in help: {combined}"


# ---------------------------------------------------------------------------
# test_cold_stage_registration
# ---------------------------------------------------------------------------

def test_cold_stage_registration():
    from pipeline_cold.run import default_cold_stages

    stages = default_cold_stages()
    names = [s.name for s in stages]

    assert "feedback_sweep" in names, f"feedback_sweep not in stages: {names}"

    idx_rollup = names.index("rollup")
    idx_feedback = names.index("feedback_sweep")
    idx_backfill = names.index("backfill_spotlight")

    assert idx_rollup < idx_feedback < idx_backfill, (
        f"placement wrong: rollup={idx_rollup} feedback={idx_feedback} backfill={idx_backfill}"
    )

    # command must include pipeline_feedback and sweep
    feedback_stage = stages[idx_feedback]
    cmd_str = " ".join(feedback_stage.command)
    assert "pipeline_feedback" in cmd_str
    assert "sweep" in cmd_str

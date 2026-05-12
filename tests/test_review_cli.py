"""Integration tests for review.cli (approve + validate subcommands).

Tests bypass argparse by calling _run_approve/_run_validate directly with
argparse.Namespace instances, except for the smoke test for `main` routing.

Editor seam: tests inject a fake_editor function instead of opening $EDITOR.
DDB/S3 seam: get_table and get_s3_client are replaced with MagicMock factories.
RunSignals injection: monkeypatch review.cli.read_run_signals to return
a controlled RunSignals without touching DDB or S3.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from review.cli import _run_approve, _run_validate, main
from review.config import ReviewerCwidUnresolvable
from review.validator import RunSignals


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_editor(yaml_content: str):
    """Returns a fake editor function that writes yaml_content and exits 0."""
    def _invoke(path: Path) -> int:
        path.write_text(yaml_content)
        return 0
    return _invoke


def _valid_yaml_str(reviewer_cwid: str = "cwid_jsmith1") -> str:
    data = {
        "artifact_type": "hierarchy",
        "version": "v2026-06-01",
        "proposed_artifact_uri": "s3://wcmc-reciterai-hierarchy/v2026-06-01/hierarchy.json",
        "summary_stats": {"topics_added": 2, "subtopics_renamed": 14, "pmids_reassigned": 312},
        "reviewer_cwid": reviewer_cwid,
        "rationale": "A" * 40,
        "decision": "approve",
    }
    return yaml.safe_dump(data)


def _approve_args(artifact: str = "hierarchy", version: str = "v2026-06-01") -> argparse.Namespace:
    return argparse.Namespace(artifact=artifact, version=version)


def _validate_args(path: str) -> argparse.Namespace:
    return argparse.Namespace(path=path)


def _ok_signals(**overrides) -> RunSignals:
    kwargs = dict(
        failed_stages=(),
        gate_block_errors=(),
        artifact_uri_exists=True,
    )
    kwargs.update(overrides)
    return RunSignals(**kwargs)


# ---------------------------------------------------------------------------
# Test 1: validate subcommand — happy path
# ---------------------------------------------------------------------------

def test_validate_happy_path(tmp_path, monkeypatch):
    """Valid YAML file → exit 0, 'validation passed' on stdout."""
    f = tmp_path / "review.yaml"
    f.write_text(_valid_yaml_str())

    mock_table = MagicMock()
    mock_s3 = MagicMock()
    ok_signals = _ok_signals()

    with patch("review.cli.read_run_signals", return_value=ok_signals), \
         patch("builtins.print") as mock_print:
        rc = _run_validate(
            _validate_args(str(f)),
            get_table=lambda: mock_table,
            get_s3_client=lambda: mock_s3,
        )

    assert rc == 0
    # "validation passed" must appear in stdout output
    calls = [str(c) for c in mock_print.call_args_list]
    assert any("validation passed" in c for c in calls)
    # No PutItem should have been called
    mock_table.put_item.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2: validate subcommand — YAML validation failure
# ---------------------------------------------------------------------------

def test_validate_failure_exits_3(tmp_path, monkeypatch):
    """YAML with decision=yes → exit 3, error mentions decision rule."""
    bad = yaml.safe_dump({
        "artifact_type": "hierarchy",
        "version": "v2026-06-01",
        "proposed_artifact_uri": "s3://bucket/key",
        "reviewer_cwid": "cwid_jsmith1",
        "rationale": "A" * 40,
        "decision": "yes",  # invalid
    })
    f = tmp_path / "bad.yaml"
    f.write_text(bad)

    ok_signals = _ok_signals()
    stderr_output: list[str] = []

    with patch("review.cli.read_run_signals", return_value=ok_signals), \
         patch("sys.stderr") as mock_stderr:
        mock_stderr.write = lambda s: stderr_output.append(s)
        rc = _run_validate(
            _validate_args(str(f)),
            get_table=lambda: MagicMock(),
            get_s3_client=lambda: MagicMock(),
        )

    assert rc == 3


def test_validate_failure_error_mentions_decision(tmp_path, capsys):
    """Validation error output mentions the decision rule."""
    bad = yaml.safe_dump({
        "artifact_type": "hierarchy",
        "version": "v2026-06-01",
        "proposed_artifact_uri": "s3://bucket/key",
        "reviewer_cwid": "cwid_jsmith1",
        "rationale": "A" * 40,
        "decision": "yes",
    })
    f = tmp_path / "bad.yaml"
    f.write_text(bad)

    ok_signals = _ok_signals()
    with patch("review.cli.read_run_signals", return_value=ok_signals):
        rc = _run_validate(
            _validate_args(str(f)),
            get_table=lambda: MagicMock(),
            get_s3_client=lambda: MagicMock(),
        )
    assert rc == 3
    captured = capsys.readouterr()
    assert "decision" in captured.err


# ---------------------------------------------------------------------------
# Test 3: approve — happy path, PutItem fires exactly once
# ---------------------------------------------------------------------------

def test_approve_happy_path_puts_item(monkeypatch):
    """Valid YAML from fake editor → exit 0, exactly one PutItem to REVIEW#."""
    mock_table = MagicMock()
    mock_s3 = MagicMock()
    ok_signals = _ok_signals()

    with patch("review.cli.read_run_signals", return_value=ok_signals), \
         patch("review.cli.load_reviewer_cwid", return_value="cwid_jsmith1"):
        rc = _run_approve(
            _approve_args(),
            invoke_editor=_fake_editor(_valid_yaml_str()),
            get_table=lambda: mock_table,
            get_s3_client=lambda: mock_s3,
        )

    assert rc == 0
    mock_table.put_item.assert_called_once()
    call_args = mock_table.put_item.call_args[1]["Item"]
    assert call_args["PK"] == "REVIEW#hierarchy#v2026-06-01"
    assert call_args["SK"] == "GLOBAL"


# ---------------------------------------------------------------------------
# Test 4: approve — gate failure → exit 3, NO PutItem
# ---------------------------------------------------------------------------

def test_approve_gate_failure_exits_3_no_put(monkeypatch):
    """RunSignals with failed_stages → exit 3; PutItem must NOT fire."""
    mock_table = MagicMock()
    bad_signals = _ok_signals(failed_stages=("publish_hierarchy",))

    with patch("review.cli.read_run_signals", return_value=bad_signals), \
         patch("review.cli.load_reviewer_cwid", return_value="cwid_jsmith1"):
        rc = _run_approve(
            _approve_args(),
            invoke_editor=_fake_editor(_valid_yaml_str()),
            get_table=lambda: mock_table,
            get_s3_client=lambda: MagicMock(),
        )

    assert rc == 3
    mock_table.put_item.assert_not_called()


def test_approve_gate_failure_prints_error(monkeypatch, capsys):
    """Gate failure output mentions the failing stage."""
    bad_signals = _ok_signals(failed_stages=("publish_hierarchy",))

    with patch("review.cli.read_run_signals", return_value=bad_signals), \
         patch("review.cli.load_reviewer_cwid", return_value="cwid_jsmith1"):
        _run_approve(
            _approve_args(),
            invoke_editor=_fake_editor(_valid_yaml_str()),
            get_table=lambda: MagicMock(),
            get_s3_client=lambda: MagicMock(),
        )
    captured = capsys.readouterr()
    assert "publish_hierarchy" in captured.err


# ---------------------------------------------------------------------------
# Test 5: approve — no reviewer_cwid → exit 4 with actionable message
# ---------------------------------------------------------------------------

def test_approve_no_cwid_exits_4(monkeypatch, capsys):
    """load_reviewer_cwid raises → CLI exits 4 with actionable message."""
    exc = ReviewerCwidUnresolvable(
        "Could not resolve reviewer_cwid.\n\n"
        "Option 1 — create ~/.reciterai/config.yaml:\n"
        "    reviewer_cwid: cwid_jsmith1\n\n"
        "Option 2 — export RECITERAI_REVIEWER_CWID:\n"
        "    export RECITERAI_REVIEWER_CWID=cwid_jsmith1\n"
    )
    with patch("review.cli.load_reviewer_cwid", side_effect=exc):
        rc = _run_approve(
            _approve_args(),
            get_table=lambda: MagicMock(),
            get_s3_client=lambda: MagicMock(),
        )
    assert rc == 4
    captured = capsys.readouterr()
    assert "reviewer_cwid" in captured.err or "RECITERAI_REVIEWER_CWID" in captured.err


# ---------------------------------------------------------------------------
# Test 6: template.build_template — parses to valid YAML with reviewer_cwid key
# ---------------------------------------------------------------------------

def test_build_template_produces_valid_yaml():
    """build_template output is parseable and contains reviewer_cwid key."""
    from review.template import build_template
    tpl = build_template(
        artifact_type="hierarchy",
        version="v2026-06-01",
        proposed_artifact_uri="s3://wcmc-reciterai-hierarchy/v2026-06-01/hierarchy.json",
        summary_stats={"topics_added": 2, "subtopics_renamed": 14, "pmids_reassigned": 312},
    )
    # Must parse without error
    data = yaml.safe_load(tpl)
    assert isinstance(data, dict)
    # Must contain reviewer_cwid field (for operator to fill)
    assert "reviewer_cwid" in data


def test_build_template_prepopulates_cwid():
    """When reviewer_cwid is provided, it is pre-populated in the template."""
    from review.template import build_template
    tpl = build_template(
        artifact_type="hierarchy",
        version="v2026-06-01",
        proposed_artifact_uri="s3://bucket/key",
        summary_stats={},
        reviewer_cwid="cwid_jsmith1",
    )
    data = yaml.safe_load(tpl)
    assert data["reviewer_cwid"] == "cwid_jsmith1"


def test_build_template_no_cwid_leaves_empty():
    """When reviewer_cwid not provided, field is empty string."""
    from review.template import build_template
    tpl = build_template(
        artifact_type="hierarchy",
        version="v2026-06-01",
        proposed_artifact_uri="s3://bucket/key",
        summary_stats={},
    )
    data = yaml.safe_load(tpl)
    assert data.get("reviewer_cwid") is None or data.get("reviewer_cwid") == ""


# ---------------------------------------------------------------------------
# Test 7: $EDITOR seam — fake editor writes YAML, CLI proceeds to validation
# ---------------------------------------------------------------------------

def test_editor_seam_accepts_injected_function(monkeypatch):
    """Fake editor that writes valid YAML causes approve to proceed."""
    mock_table = MagicMock()
    ok_signals = _ok_signals()

    with patch("review.cli.read_run_signals", return_value=ok_signals), \
         patch("review.cli.load_reviewer_cwid", return_value="cwid_jsmith1"):
        rc = _run_approve(
            _approve_args(),
            invoke_editor=_fake_editor(_valid_yaml_str()),
            get_table=lambda: mock_table,
            get_s3_client=lambda: MagicMock(),
        )

    assert rc == 0
    mock_table.put_item.assert_called_once()


def test_editor_nonzero_exit_returns_3(monkeypatch):
    """Editor that exits non-zero → CLI returns 3 (no DDB write)."""
    mock_table = MagicMock()

    def bad_editor(path: Path) -> int:
        return 1  # editor quit without saving

    with patch("review.cli.load_reviewer_cwid", return_value="cwid_jsmith1"):
        rc = _run_approve(
            _approve_args(),
            invoke_editor=bad_editor,
            get_table=lambda: mock_table,
            get_s3_client=lambda: MagicMock(),
        )

    assert rc == 3
    mock_table.put_item.assert_not_called()


# ---------------------------------------------------------------------------
# Test 8: reviewed_at timestamp format
# ---------------------------------------------------------------------------

def test_approved_row_has_reviewed_at_timestamp(monkeypatch):
    """Successful approve writes reviewed_at in ISO 8601 UTC format."""
    mock_table = MagicMock()
    ok_signals = _ok_signals()

    with patch("review.cli.read_run_signals", return_value=ok_signals), \
         patch("review.cli.load_reviewer_cwid", return_value="cwid_jsmith1"):
        rc = _run_approve(
            _approve_args(),
            invoke_editor=_fake_editor(_valid_yaml_str()),
            get_table=lambda: mock_table,
            get_s3_client=lambda: MagicMock(),
        )

    assert rc == 0
    written_item = mock_table.put_item.call_args[1]["Item"]
    reviewed_at = written_item.get("reviewed_at", "")
    pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
    assert re.match(pattern, reviewed_at), (
        f"reviewed_at {reviewed_at!r} does not match ISO 8601 UTC pattern"
    )


# ---------------------------------------------------------------------------
# Smoke test: main() argparse routing
# ---------------------------------------------------------------------------

def test_main_validate_routing(tmp_path):
    """main() with 'validate' subcommand routes to _run_validate."""
    f = tmp_path / "review.yaml"
    f.write_text(_valid_yaml_str())
    ok_signals = _ok_signals()
    with patch("review.cli.read_run_signals", return_value=ok_signals), \
         patch("builtins.print"):
        rc = main(["validate", str(f)])
    assert rc == 0


def test_main_help_exits_0():
    """main() --help exits 0."""
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0

"""Tests for gates.cli — ad-hoc gate runner."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gates import cli

REPO_ROOT = Path(__file__).resolve().parents[1]
LIVE_HIERARCHY_DIR = REPO_ROOT / "out/hierarchy/v2026-05-12"


def test_list_emits_one_line_per_gate(capsys):
    rc = cli.main(["--list", "--stage", "publish"])
    out = capsys.readouterr().out.strip().splitlines()
    assert rc == 0
    assert len(out) >= 3  # schema_validation, parent_prefix, pii_scan at minimum
    parsed = [json.loads(line) for line in out]
    names = {g["name"] for g in parsed}
    assert {"schema_validation", "parent_prefix", "pii_scan"}.issubset(names)
    assert all(g["stage"] == "publish" for g in parsed)


def test_list_without_stage_returns_all_stages(capsys):
    rc = cli.main(["--list"])
    out = capsys.readouterr().out.strip().splitlines()
    assert rc == 0
    stages = {json.loads(line)["stage"] for line in out}
    assert {"publish", "publish_post"}.issubset(stages)


def test_missing_stage_errors(capsys):
    rc = cli.main([])
    err = capsys.readouterr().err
    assert rc == 2
    assert "--stage is required" in err


def test_force_without_reason_errors(capsys):
    rc = cli.main(["--stage", "publish", "--force"])
    err = capsys.readouterr().err
    assert rc == 4
    assert "--force-reason" in err


def test_publish_stage_requires_version(capsys):
    rc = cli.main(["--stage", "publish"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "--hierarchy" in err or "version" in err


def test_publish_stage_local_load_runs_gates(capsys):
    if not LIVE_HIERARCHY_DIR.exists():
        pytest.skip("v2026-05-12 local artifact not on disk")

    rc = cli.main(["--stage", "publish", "--hierarchy", "v2026-05-12"])
    out = capsys.readouterr().out
    report = json.loads(out)
    assert rc == 0  # all gates pass on the real artifact
    assert report["stage"] == "publish"
    assert report["version"] == "v2026-05-12"
    names = {g["name"] for g in report["gates"]}
    assert {"schema_validation", "parent_prefix", "pii_scan"}.issubset(names)
    assert all(g["passed"] for g in report["gates"])


def test_publish_stage_from_s3_uses_s3_client(capsys):
    """--from-s3 must fetch via S3HierarchyClient.get_object_bytes."""
    if not LIVE_HIERARCHY_DIR.exists():
        pytest.skip("v2026-05-12 local artifact not on disk")

    body = (LIVE_HIERARCHY_DIR / "hierarchy.json").read_bytes()
    fake_s3 = MagicMock()
    fake_s3.get_object_bytes.return_value = body

    with patch.object(cli, "S3HierarchyClient", return_value=fake_s3):
        rc = cli.main(
            ["--stage", "publish", "--hierarchy", "v2026-05-12", "--from-s3"]
        )

    capsys.readouterr()  # drain
    fake_s3.get_object_bytes.assert_called_once_with("v2026-05-12/hierarchy.json")
    assert rc == 0


def test_publish_post_runs_roundtrip(capsys):
    if not LIVE_HIERARCHY_DIR.exists():
        pytest.skip("v2026-05-12 local artifact not on disk")

    h_bytes = (LIVE_HIERARCHY_DIR / "hierarchy.json").read_bytes()
    s_bytes = (REPO_ROOT / "docs/hierarchy.schema.json").read_bytes()

    fake_s3 = MagicMock()
    def fake_get(key):
        return s_bytes if key.endswith("schema.json") else h_bytes
    fake_s3.get_object_bytes.side_effect = fake_get

    with patch.object(cli, "S3HierarchyClient", return_value=fake_s3):
        rc = cli.main(["--stage", "publish_post", "--version", "v2026-05-12"])

    out = capsys.readouterr().out
    report = json.loads(out)
    assert rc == 0
    assert report["stage"] == "publish_post"
    assert any(g["name"] == "schema_roundtrip" and g["passed"] for g in report["gates"])


def test_block_severity_failure_exits_3(capsys):
    """Inject a violation; expect exit 3 without --force."""
    if not LIVE_HIERARCHY_DIR.exists():
        pytest.skip("v2026-05-12 local artifact not on disk")

    hierarchy = json.loads((LIVE_HIERARCHY_DIR / "hierarchy.json").read_text())
    # Inject a PII violation that's guaranteed to trip the pii_scan gate.
    hierarchy["topics"]["aging_geroscience"]["subtopics"][0]["short_description"] = (
        "Maintained by cwid_jsmith1234"
    )

    with patch.object(cli, "_load_hierarchy", return_value=hierarchy):
        rc = cli.main(["--stage", "publish", "--hierarchy", "v2026-05-12"])

    out = capsys.readouterr().out
    report = json.loads(out)
    assert rc == 3
    failed_gates = [g for g in report["gates"] if not g["passed"]]
    assert any(g["name"] == "pii_scan" for g in failed_gates)


def test_force_overrides_block_severity(capsys):
    """--force --force-reason exits 0 even with a block-severity failure,
    and logs the override to stderr."""
    if not LIVE_HIERARCHY_DIR.exists():
        pytest.skip("v2026-05-12 local artifact not on disk")

    hierarchy = json.loads((LIVE_HIERARCHY_DIR / "hierarchy.json").read_text())
    hierarchy["topics"]["aging_geroscience"]["subtopics"][0]["short_description"] = (
        "Maintained by cwid_jsmith1234"
    )

    with patch.object(cli, "_load_hierarchy", return_value=hierarchy):
        rc = cli.main([
            "--stage", "publish",
            "--hierarchy", "v2026-05-12",
            "--force",
            "--force-reason", "intentional injection during testing",
        ])

    captured = capsys.readouterr()
    assert rc == 0
    assert "force_override" in captured.err
    assert "intentional injection" in captured.err

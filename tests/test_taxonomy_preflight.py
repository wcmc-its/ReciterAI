"""ADR D3 cold-run taxonomy preflight (utils/taxonomy_preflight.py)."""
from __future__ import annotations

import json
import subprocess

import pytest

from utils import taxonomy_preflight as tp
from utils.taxonomy import content_hash, current_content_hash


def test_env_match_passes(monkeypatch):
    monkeypatch.setenv(tp.EXPECTED_HASH_ENV, current_content_hash())
    ok, msg = tp.taxonomy_preflight()
    assert ok
    assert "OK" in msg


def test_env_mismatch_blocks_and_names_both_hashes(monkeypatch):
    monkeypatch.setenv(tp.EXPECTED_HASH_ENV, "0" * 64)
    ok, msg = tp.taxonomy_preflight()
    assert not ok
    assert "0" * 64 in msg
    assert current_content_hash() in msg


def test_blank_env_is_not_a_baseline(monkeypatch, tmp_path):
    monkeypatch.setenv(tp.EXPECTED_HASH_ENV, "   ")
    ok, msg = tp.taxonomy_preflight(repo_root=tmp_path)  # no .git either
    assert not ok
    assert "no baseline" in msg


def test_no_env_no_git_fails_closed(monkeypatch, tmp_path):
    monkeypatch.delenv(tp.EXPECTED_HASH_ENV, raising=False)
    ok, msg = tp.taxonomy_preflight(repo_root=tmp_path)
    assert not ok
    assert "no baseline" in msg


def _git(*args, cwd):
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd, check=True, capture_output=True,
    )


def test_git_fallback_hashes_origin_main(monkeypatch, tmp_path):
    monkeypatch.delenv(tp.EXPECTED_HASH_ENV, raising=False)
    remote = tmp_path / "remote"
    remote.mkdir()
    _git("init", "-b", "main", cwd=remote)
    taxonomy = {
        "taxonomy_version": "taxonomy_v2",
        "topics": [{"id": "cardiology", "label": "Cardiology"}],
    }
    (remote / "taxonomy_v2.json").write_text(json.dumps(taxonomy))
    _git("add", "taxonomy_v2.json", cwd=remote)
    _git("commit", "-m", "seed", cwd=remote)
    clone = tmp_path / "clone"
    _git("clone", "--quiet", str(remote), str(clone), cwd=tmp_path)

    assert tp._expected_hash_from_git(clone) == content_hash(taxonomy)


def test_git_fetch_failure_returns_none_not_stale_ref(tmp_path):
    work = tmp_path / "w"
    work.mkdir()
    _git("init", "-b", "main", cwd=work)
    _git("remote", "add", "origin", str(tmp_path / "does-not-exist"), cwd=work)
    assert tp._expected_hash_from_git(work) is None


def test_no_git_dir_returns_none(tmp_path):
    assert tp._expected_hash_from_git(tmp_path) is None


def test_cold_run_dry_run_blocks_on_mismatch(monkeypatch):
    from pipeline_cold import run as cold

    monkeypatch.setenv(tp.EXPECTED_HASH_ENV, "0" * 64)
    assert cold.main(["--dry-run"]) == 3


def test_cold_run_dry_run_passes_with_matching_baseline(monkeypatch):
    from pipeline_cold import run as cold

    monkeypatch.setenv(tp.EXPECTED_HASH_ENV, current_content_hash())
    assert cold.main(["--dry-run"]) == 0


def test_publish_dry_run_blocks_on_mismatch(monkeypatch):
    from pipeline_hierarchy import publish

    monkeypatch.setenv(tp.EXPECTED_HASH_ENV, "0" * 64)
    assert publish.main(["--dry-run"]) == publish.EXIT_PREFLIGHT_BLOCKED


def test_publish_force_does_not_override_preflight(monkeypatch):
    from pipeline_hierarchy import publish

    monkeypatch.setenv(tp.EXPECTED_HASH_ENV, "0" * 64)
    rc = publish.main(["--dry-run", "--force", "--force-reason", "test"])
    assert rc == publish.EXIT_PREFLIGHT_BLOCKED

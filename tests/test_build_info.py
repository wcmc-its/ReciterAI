"""utils.build_info.build_id precedence (#407): env var > BUILD_SHA file >
git > "unknown"."""

from __future__ import annotations

import subprocess

import pytest

from utils import build_info


@pytest.fixture(autouse=True)
def _fresh_cache():
    build_info.build_id.cache_clear()
    yield
    build_info.build_id.cache_clear()


@pytest.fixture
def sha_file(tmp_path, monkeypatch):
    f = tmp_path / "BUILD_SHA"
    monkeypatch.setattr(build_info, "BUILD_SHA_FILE", f)
    return f


def _fake_git(monkeypatch, out="", exc=None):
    def run(*a, **k):
        if exc:
            raise exc
        return subprocess.CompletedProcess(a, 0, stdout=out, stderr="")
    monkeypatch.setattr(build_info.subprocess, "run", run)


def test_env_var_wins(monkeypatch, sha_file):
    monkeypatch.setenv("RECITERAI_BUILD_SHA", "envsha")
    sha_file.write_text("filesha\n")
    _fake_git(monkeypatch, "gitsha\n")
    assert build_info.build_id() == "envsha"


def test_file_when_env_unset_or_blank(monkeypatch, sha_file):
    monkeypatch.setenv("RECITERAI_BUILD_SHA", "  ")  # Dockerfile default: empty
    sha_file.write_text("filesha\n")
    _fake_git(monkeypatch, "gitsha\n")
    assert build_info.build_id() == "filesha"


def test_git_when_no_env_or_file(monkeypatch, sha_file):
    monkeypatch.delenv("RECITERAI_BUILD_SHA", raising=False)
    _fake_git(monkeypatch, "gitsha\n")
    assert build_info.build_id() == "gitsha"


def test_unknown_when_git_fails(monkeypatch, sha_file):
    monkeypatch.delenv("RECITERAI_BUILD_SHA", raising=False)
    _fake_git(monkeypatch, exc=FileNotFoundError("git"))
    assert build_info.build_id() == "unknown"


def test_unknown_when_git_prints_nothing(monkeypatch, sha_file):
    monkeypatch.delenv("RECITERAI_BUILD_SHA", raising=False)
    _fake_git(monkeypatch, "")
    assert build_info.build_id() == "unknown"


def test_cached(monkeypatch, sha_file):
    monkeypatch.setenv("RECITERAI_BUILD_SHA", "first")
    assert build_info.build_id() == "first"
    monkeypatch.setenv("RECITERAI_BUILD_SHA", "second")
    assert build_info.build_id() == "first"


def test_minted_by_format(monkeypatch):
    monkeypatch.setenv("RECITERAI_BUILD_SHA", "abc1234")
    assert build_info.minted_by("load_dynamodb") == "load_dynamodb@abc1234"

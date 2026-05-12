"""Tests for review.config — operator config loader.

Covers:
  Test 7: ~/.reciterai/config.yaml exists with reviewer_cwid → returned.
  Test 8: No config file, RECITERAI_REVIEWER_CWID env set → returned.
  Test 9: Neither → ReviewerCwidUnresolvable with actionable message.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from review.config import ReviewerCwidUnresolvable, load_reviewer_cwid


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_config(tmp_path: Path, data: dict) -> Path:
    """Write a config.yaml at <tmp_path>/.reciterai/config.yaml."""
    config_dir = tmp_path / ".reciterai"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.yaml"
    config_file.write_text(yaml.safe_dump(data))
    return config_file


# ---------------------------------------------------------------------------
# Test 7: config file present
# ---------------------------------------------------------------------------

def test_load_from_config_file(tmp_path, monkeypatch):
    """~/.reciterai/config.yaml with reviewer_cwid → load_reviewer_cwid() returns it."""
    _write_config(tmp_path, {"reviewer_cwid": "cwid_jsmith1"})
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    result = load_reviewer_cwid()
    assert result == "cwid_jsmith1"


def test_config_file_strips_whitespace(tmp_path, monkeypatch):
    """Leading/trailing whitespace in config value is stripped."""
    _write_config(tmp_path, {"reviewer_cwid": "  cwid_jsmith1  "})
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    result = load_reviewer_cwid()
    assert result == "cwid_jsmith1"


# ---------------------------------------------------------------------------
# Test 8: env var fallback
# ---------------------------------------------------------------------------

def test_load_from_env_var(tmp_path, monkeypatch):
    """No config file, RECITERAI_REVIEWER_CWID set → returned."""
    # Point home to empty tmp_path so no config file exists
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setenv("RECITERAI_REVIEWER_CWID", "cwid_jsmith1")
    result = load_reviewer_cwid()
    assert result == "cwid_jsmith1"


def test_env_var_takes_precedence_after_missing_config(tmp_path, monkeypatch):
    """Config file absent + env set → env wins (not an error)."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setenv("RECITERAI_REVIEWER_CWID", "cwid_envuser1")
    result = load_reviewer_cwid()
    assert result == "cwid_envuser1"


# ---------------------------------------------------------------------------
# Test 9: neither config nor env → actionable error
# ---------------------------------------------------------------------------

def test_neither_config_nor_env_raises(tmp_path, monkeypatch):
    """No config, no env → ReviewerCwidUnresolvable with actionable message."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.delenv("RECITERAI_REVIEWER_CWID", raising=False)
    with pytest.raises(ReviewerCwidUnresolvable) as exc_info:
        load_reviewer_cwid()
    msg = str(exc_info.value)
    # Actionable message must contain all three elements
    assert "~/.reciterai/config.yaml" in msg, "Must mention config file path"
    assert "RECITERAI_REVIEWER_CWID" in msg, "Must mention env var name"
    assert "reviewer_cwid:" in msg, "Must include a YAML example"


def test_error_message_is_actionable(tmp_path, monkeypatch):
    """Error message gives concrete instructions, not just 'error occurred'."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.delenv("RECITERAI_REVIEWER_CWID", raising=False)
    with pytest.raises(ReviewerCwidUnresolvable) as exc_info:
        load_reviewer_cwid()
    msg = str(exc_info.value)
    # Should include at least one of: "create", "export", "Option"
    assert any(kw in msg for kw in ("create", "export", "Option")), (
        f"Error message not actionable enough: {msg!r}"
    )

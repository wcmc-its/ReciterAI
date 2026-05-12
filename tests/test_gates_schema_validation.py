"""Tests for gates.schema_validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gates import registry
from gates.schema_validation import schema_validation_gate

REPO_ROOT = Path(__file__).resolve().parents[1]
LIVE_HIERARCHY = REPO_ROOT / "out/hierarchy/v2026-05-12/hierarchy.json"
SCHEMA_PATH = REPO_ROOT / "docs/hierarchy.schema.json"


def test_schema_validation_passes_on_live_artifact():
    """Today's published v2026-05-12 hierarchy must pass the gate."""
    if not LIVE_HIERARCHY.exists():
        pytest.skip("v2026-05-12 dry-run artifact not on disk")
    hierarchy = json.loads(LIVE_HIERARCHY.read_text())
    result = schema_validation_gate(hierarchy=hierarchy)
    assert result.passed is True
    assert result.name == "schema_validation"
    assert "schema-valid" in result.summary


def test_schema_validation_fails_on_missing_required_field():
    """Strip `taxonomy_version` (a required top-level field) and expect a block."""
    if not LIVE_HIERARCHY.exists():
        pytest.skip("v2026-05-12 dry-run artifact not on disk")
    hierarchy = json.loads(LIVE_HIERARCHY.read_text())
    hierarchy.pop("taxonomy_version", None)

    result = schema_validation_gate(hierarchy=hierarchy)
    assert result.passed is False
    assert result.blocked is True
    assert "taxonomy_version" in result.details.get("message", "") or "taxonomy_version" in result.summary


def test_schema_validation_does_not_raise_on_failure():
    """Gates return GateResult on failure; they MUST NOT propagate ValidationError."""
    bad_hierarchy: dict = {"this": "is not a hierarchy"}
    # Should return, not raise.
    result = schema_validation_gate(hierarchy=bad_hierarchy)
    assert isinstance(result.passed, bool)
    assert result.passed is False


def test_schema_validation_registered_against_publish_stage():
    """Importing the module triggers @register_gate. Confirm registration shape."""
    # Filter just our gate from any others that may have been registered.
    publish_gates = [g for g in registry.list_gates(stage="publish") if g["name"] == "schema_validation"]
    assert len(publish_gates) == 1
    assert publish_gates[0]["severity"] == "block"


def test_schema_validation_failure_includes_json_pointer():
    """Failed validation must surface the JSON Pointer for operator debugging."""
    bad_hierarchy = {"taxonomy_version": "x", "topics": "not-an-object"}
    result = schema_validation_gate(hierarchy=bad_hierarchy)
    assert result.passed is False
    assert "json_pointer" in result.details
    assert "validator" in result.details

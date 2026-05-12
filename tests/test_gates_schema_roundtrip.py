"""Tests for gates.schema_roundtrip — uses a mock S3 client (no network)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gates import registry
from gates.schema_roundtrip import schema_roundtrip_gate

REPO_ROOT = Path(__file__).resolve().parents[1]
LIVE_HIERARCHY = REPO_ROOT / "out/hierarchy/v2026-05-12/hierarchy.json"
LIVE_SCHEMA = REPO_ROOT / "docs/hierarchy.schema.json"


def _mock_s3_with(hierarchy_bytes: bytes, schema_bytes: bytes) -> MagicMock:
    """Mock S3 client returning the given bytes for the two well-known keys."""
    client = MagicMock()

    def fake_get(key: str) -> bytes:
        if key.endswith("hierarchy.json"):
            return hierarchy_bytes
        if key.endswith("hierarchy.schema.json"):
            return schema_bytes
        raise KeyError(key)

    client.get_object_bytes.side_effect = fake_get
    return client


# ---------- happy path ----------


def test_passes_when_published_artifact_validates_against_published_schema():
    if not LIVE_HIERARCHY.exists() or not LIVE_SCHEMA.exists():
        pytest.skip("v2026-05-12 dry-run artifact not on disk")
    h_bytes = LIVE_HIERARCHY.read_bytes()
    s_bytes = LIVE_SCHEMA.read_bytes()
    client = _mock_s3_with(h_bytes, s_bytes)

    result = schema_roundtrip_gate(version="v2026-05-12", s3_client=client)
    assert result.passed is True
    assert result.severity == "warn"
    assert "v2026-05-12" in result.summary


def test_fetches_from_versioned_prefix_not_latest():
    """Defense against the contract D-02 invariant — must NOT trust latest/."""
    if not LIVE_HIERARCHY.exists() or not LIVE_SCHEMA.exists():
        pytest.skip("v2026-05-12 dry-run artifact not on disk")
    client = _mock_s3_with(LIVE_HIERARCHY.read_bytes(), LIVE_SCHEMA.read_bytes())

    schema_roundtrip_gate(version="v2026-05-12", s3_client=client)
    fetched_keys = [call.args[0] for call in client.get_object_bytes.call_args_list]
    assert "v2026-05-12/hierarchy.json" in fetched_keys
    assert "v2026-05-12/hierarchy.schema.json" in fetched_keys
    assert all("latest/" not in k for k in fetched_keys)


# ---------- failure modes ----------


def test_fails_when_hierarchy_object_missing():
    client = MagicMock()
    client.get_object_bytes.side_effect = FileNotFoundError("404 NoSuchKey")

    result = schema_roundtrip_gate(version="v2026-05-12", s3_client=client)
    assert result.passed is False
    assert result.severity == "warn"
    assert "hierarchy.json" in result.summary or "hierarchy.json" in result.details.get("s3_key", "")


def test_fails_when_schema_object_missing():
    if not LIVE_HIERARCHY.exists():
        pytest.skip("v2026-05-12 dry-run artifact not on disk")
    client = MagicMock()

    def fake_get(key: str) -> bytes:
        if key.endswith("hierarchy.json"):
            return LIVE_HIERARCHY.read_bytes()
        raise FileNotFoundError("404 NoSuchKey")

    client.get_object_bytes.side_effect = fake_get
    result = schema_roundtrip_gate(version="v2026-05-12", s3_client=client)
    assert result.passed is False
    assert "schema.json" in result.summary or result.details.get("s3_key", "").endswith("schema.json")


def test_fails_on_invalid_json():
    """A torn upload could leave a half-written hierarchy.json."""
    client = _mock_s3_with(b"{ not valid json", b"{}")
    result = schema_roundtrip_gate(version="v2026-05-12", s3_client=client)
    assert result.passed is False
    assert "JSON" in result.summary or "not valid JSON" in result.summary


def test_fails_on_schema_validation_failure():
    """Catches the case where bytes are JSON but don't satisfy the schema —
    a real hazard if a publish-step bug uploaded an old hierarchy.json against
    a newer schema.json."""
    bogus_hierarchy = json.dumps({"this": "is not a hierarchy"}).encode()
    if not LIVE_SCHEMA.exists():
        pytest.skip("schema not on disk")
    client = _mock_s3_with(bogus_hierarchy, LIVE_SCHEMA.read_bytes())

    result = schema_roundtrip_gate(version="v2026-05-12", s3_client=client)
    assert result.passed is False
    assert "fails validation" in result.summary


# ---------- registration ----------


def test_registered_against_publish_post_stage_with_warn_severity():
    """Distinct stage name `publish_post` (NOT `publish`) because this runs
    after upload. Distinct severity `warn` because the bytes are already live."""
    matches = [
        g for g in registry.list_gates(stage="publish_post")
        if g["name"] == "schema_roundtrip"
    ]
    assert len(matches) == 1
    assert matches[0]["severity"] == "warn"


def test_not_registered_against_publish_stage():
    """Regression guard: must NOT show up on the pre-upload gate list."""
    publish_names = {g["name"] for g in registry.list_gates(stage="publish")}
    assert "schema_roundtrip" not in publish_names

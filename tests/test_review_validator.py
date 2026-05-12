"""Tests for review.validator — pure validation logic.

These tests require no I/O and no mocks. All inputs are constructed
in-process; review.validator has no boto3 or filesystem imports.
"""
from __future__ import annotations

import pytest

from review.validator import RunSignals, ValidationResult, validate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_dict(**overrides) -> dict:
    base = {
        "artifact_type": "hierarchy",
        "version": "v2026-06-01",
        "proposed_artifact_uri": "s3://wcmc-reciterai-hierarchy/v2026-06-01/hierarchy.json",
        "summary_stats": {"topics_added": 2, "subtopics_renamed": 14, "pmids_reassigned": 312},
        "reviewer_cwid": "cwid_jsmith1",
        "rationale": "A" * 40,
        "decision": "approve",
    }
    base.update(overrides)
    return base


def _ok_signals(**overrides) -> RunSignals:
    kwargs = dict(
        failed_stages=(),
        gate_block_errors=(),
        artifact_uri_exists=True,
        low_confidence_count=0,
        uncovered_pmid_count=0,
    )
    kwargs.update(overrides)
    return RunSignals(**kwargs)


# ---------------------------------------------------------------------------
# Test 1: happy path
# ---------------------------------------------------------------------------

def test_valid_yaml_all_fields_passes():
    result = validate(_valid_dict(), _ok_signals())
    assert isinstance(result, ValidationResult)
    assert result.ok is True
    assert result.errors == ()


# ---------------------------------------------------------------------------
# Test 2: reviewer_cwid regex failures
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_cwid", [
    "jsmith",         # missing cwid_ prefix
    "cwid_ABC123",    # uppercase letters not allowed
    "",               # empty
    None,             # missing entirely
])
def test_bad_reviewer_cwid_fails(bad_cwid):
    d = _valid_dict(reviewer_cwid=bad_cwid)
    result = validate(d, _ok_signals())
    assert result.ok is False
    assert any("reviewer_cwid" in e for e in result.errors)


def test_valid_reviewer_cwid_passes():
    d = _valid_dict(reviewer_cwid="cwid_abc123")
    result = validate(d, _ok_signals())
    # Only check cwid; other fields are valid
    assert not any("reviewer_cwid" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Test 3: rationale length
# ---------------------------------------------------------------------------

def test_rationale_too_short_fails():
    result = validate(_valid_dict(rationale="too short"), _ok_signals())
    assert result.ok is False
    assert any("rationale" in e for e in result.errors)


def test_rationale_all_whitespace_fails():
    result = validate(_valid_dict(rationale=" " * 50), _ok_signals())
    assert result.ok is False
    assert any("rationale" in e for e in result.errors)


def test_rationale_exactly_40_chars_passes():
    result = validate(_valid_dict(rationale="A" * 40), _ok_signals())
    assert not any("rationale" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Test 4: decision field
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_decision", ["yes", "no", "maybe", "", None, 1])
def test_bad_decision_fails(bad_decision):
    result = validate(_valid_dict(decision=bad_decision), _ok_signals())
    assert result.ok is False
    assert any("decision" in e for e in result.errors)


def test_decision_reject_passes():
    result = validate(_valid_dict(decision="reject"), _ok_signals())
    # Only decision-related errors, no "decision" error expected
    assert not any("decision" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Test 5: artifact_uri_exists == False
# ---------------------------------------------------------------------------

def test_artifact_uri_missing_fails():
    result = validate(_valid_dict(), _ok_signals(artifact_uri_exists=False))
    assert result.ok is False
    assert any("proposed_artifact_uri" in e or "S3" in e or "404" in e for e in result.errors)


def test_artifact_uri_exists_passes():
    result = validate(_valid_dict(), _ok_signals(artifact_uri_exists=True))
    assert not any("proposed_artifact_uri" in e or "S3" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Test 6: gate-failure refusal on approve; allow reject
# ---------------------------------------------------------------------------

def test_approve_with_failed_stages_rejected():
    signals = _ok_signals(failed_stages=("publish_hierarchy",))
    result = validate(_valid_dict(decision="approve"), signals)
    assert result.ok is False
    # Error must mention "cannot approve" and the failed stage name
    combined = " ".join(result.errors)
    assert "cannot approve" in combined
    assert "publish_hierarchy" in combined


def test_approve_with_gate_block_errors_rejected():
    signals = _ok_signals(gate_block_errors=("GATE_BLOCK_pii_check",))
    result = validate(_valid_dict(decision="approve"), signals)
    assert result.ok is False
    combined = " ".join(result.errors)
    assert "cannot approve" in combined
    assert "GATE_BLOCK_pii_check" in combined


def test_reject_with_failed_stages_allowed():
    """You can always reject, even when quality gates failed."""
    signals = _ok_signals(failed_stages=("publish_hierarchy",))
    result = validate(_valid_dict(decision="reject"), signals)
    # Gate failure should not produce a "cannot approve" error on reject
    assert not any("cannot approve" in e for e in result.errors)
    # The result itself may still pass (rationale etc. are valid in _valid_dict)
    assert result.ok is True


# ---------------------------------------------------------------------------
# Structural: validator.py must have no I/O imports
# ---------------------------------------------------------------------------

def test_validator_has_no_io_imports():
    """Pure-function contract: validator module must not import boto3, os, or
    pathlib at module level (except as typing). This is enforced by inspecting
    the module's __dict__ after import."""
    import review.validator as mod
    banned = {"boto3", "os", "pathlib"}
    imported_names = set(mod.__dict__.keys())
    for name in banned:
        assert name not in imported_names, (
            f"review.validator imported '{name}' — must remain pure (no I/O)"
        )

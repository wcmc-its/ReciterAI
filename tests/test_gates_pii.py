"""Tests for gates.pii — PII scan against the v2026-05-12 artifact.

False-positive guard is critical here: a regex that flags
`cardiovascular_disease` as a cwid match would block every publish.
The first test runs the gate against the live artifact and asserts
zero hits.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gates import registry
from gates.pii import pii_scan_gate

REPO_ROOT = Path(__file__).resolve().parents[1]
LIVE_HIERARCHY = REPO_ROOT / "out/hierarchy/v2026-05-12/hierarchy.json"


# ---------- false-positive guard (the load-bearing test) ----------


def test_no_false_positives_on_live_v2026_05_12_artifact():
    """The hierarchy contract asserts no PII. The gate must agree against
    the actually-published v2026-05-12 bytes — any false positive here
    would block legitimate future publishes."""
    if not LIVE_HIERARCHY.exists():
        pytest.skip("v2026-05-12 dry-run artifact not on disk")
    hierarchy = json.loads(LIVE_HIERARCHY.read_text())
    result = pii_scan_gate(hierarchy=hierarchy)
    assert result.passed is True, (
        f"PII scan false-positive on v2026-05-12 artifact: {result.details}"
    )


def test_no_false_positives_on_topic_ids_alone():
    """Topic IDs use snake_case ending in letters; cwid regex must not match.

    Regression test for: `cwid_[a-z]+\\d+` correctly requires a trailing
    digit, so `cardiovascular_disease` and similar should not match."""
    hierarchy = {
        "topics": {
            "cardiovascular_disease": {"subtopics": []},
            "aging_geroscience": {"subtopics": []},
            "neuroscience_neurology": {"subtopics": []},
            "biomedical_informatics": {"subtopics": []},
        }
    }
    result = pii_scan_gate(hierarchy=hierarchy)
    assert result.passed is True


# ---------- positive detections ----------


def test_detects_cwid_in_string_value():
    hierarchy = {"topics": {"x": {"subtopics": []}, "note": "owner is cwid_jsmith1234"}}
    result = pii_scan_gate(hierarchy=hierarchy)
    assert result.passed is False
    assert result.details["total_string_hits"] >= 1
    matches = [h["match"] for h in result.details["string_hits"]]
    assert "cwid_jsmith1234" in matches


def test_detects_email_in_string_value():
    hierarchy = {
        "topics": {
            "x": {
                "subtopics": [
                    {"id": "x_y", "description": "contact: foo.bar@med.cornell.edu"}
                ]
            }
        }
    }
    result = pii_scan_gate(hierarchy=hierarchy)
    assert result.passed is False
    matches = [h["match"] for h in result.details["string_hits"]]
    assert "foo.bar@med.cornell.edu" in matches


def test_detects_forbidden_personidentifier_key():
    hierarchy = {
        "topics": {
            "x": {"subtopics": [{"id": "x_y", "personIdentifier": "redacted"}]}
        }
    }
    result = pii_scan_gate(hierarchy=hierarchy)
    assert result.passed is False
    assert result.details["total_key_hits"] >= 1
    assert any(h["key"] == "personIdentifier" for h in result.details["key_hits"])


def test_detects_personidentifier_even_with_empty_value():
    """The forbidden-key check applies regardless of value (contract is
    about presence of the field, not its content)."""
    hierarchy = {"topics": {}, "personIdentifier": ""}
    result = pii_scan_gate(hierarchy=hierarchy)
    assert result.passed is False


def test_reports_json_pointer_paths():
    hierarchy = {
        "topics": {
            "x": {"subtopics": [{"id": "x_y", "note": "see cwid_abc1234"}]}
        }
    }
    result = pii_scan_gate(hierarchy=hierarchy)
    hit = result.details["string_hits"][0]
    # Path should be informative enough to locate the violation.
    assert "topics" in hit["path"]
    assert "subtopics" in hit["path"]


def test_ignores_integer_pmids_and_other_non_strings():
    """seed_pmids lists carry integers; PMIDs are public; nothing to scan."""
    hierarchy = {
        "topics": {
            "x": {
                "subtopics": [
                    {
                        "id": "x_y",
                        "display_name": "Foo",
                        "seed_pmids": [12345678, 23456789, 34567890],
                        "activity_count": 42,
                    }
                ]
            }
        }
    }
    result = pii_scan_gate(hierarchy=hierarchy)
    assert result.passed is True


def test_truncates_at_50_hits():
    hierarchy = {
        "topics": {
            f"t_{i}": {"subtopics": [{"id": f"t_{i}_x", "note": f"cwid_user{i}9999"}]}
            for i in range(75)
        }
    }
    result = pii_scan_gate(hierarchy=hierarchy)
    assert result.passed is False
    assert result.details["total_string_hits"] == 75
    assert len(result.details["string_hits"]) == 50
    assert result.details["truncated"] is True


# ---------- registration ----------


def test_pii_scan_registered_against_publish_stage():
    publish_gates = [g for g in registry.list_gates(stage="publish") if g["name"] == "pii_scan"]
    assert len(publish_gates) == 1
    assert publish_gates[0]["severity"] == "block"

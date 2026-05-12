"""Tests for gates.parent_prefix."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gates import registry
from gates.parent_prefix import parent_prefix_gate

REPO_ROOT = Path(__file__).resolve().parents[1]
LIVE_HIERARCHY = REPO_ROOT / "out/hierarchy/v2026-05-12/hierarchy.json"


def test_parent_prefix_passes_on_live_artifact():
    """The v2026-05-12 publish was verified to have zero violations
    (SPS etl:hierarchy reported warnings: 0). The gate must agree."""
    if not LIVE_HIERARCHY.exists():
        pytest.skip("v2026-05-12 dry-run artifact not on disk")
    hierarchy = json.loads(LIVE_HIERARCHY.read_text())
    result = parent_prefix_gate(hierarchy=hierarchy)
    assert result.passed is True
    assert result.name == "parent_prefix"


def test_parent_prefix_fails_on_injected_violation():
    """Hand-inject a violation and confirm the gate blocks with structured details."""
    if not LIVE_HIERARCHY.exists():
        pytest.skip("v2026-05-12 dry-run artifact not on disk")
    hierarchy = json.loads(LIVE_HIERARCHY.read_text())

    # Pick a known topic and inject a violating display_name on its first subtopic.
    target_topic = "microbiome_research"
    assert target_topic in hierarchy["topics"], "fixture assumes microbiome_research present"
    hierarchy["topics"][target_topic]["subtopics"][0]["display_name"] = (
        "Microbiome & Cancer Immunotherapy Response"
    )

    result = parent_prefix_gate(hierarchy=hierarchy)
    assert result.passed is False
    assert result.blocked is True
    assert result.details["violation_count"] == 1
    v = result.details["violations"][0]
    assert v["topic_id"] == target_topic
    assert v["display_name"] == "Microbiome & Cancer Immunotherapy Response"


def test_parent_prefix_accepts_explicit_topic_labels():
    """Tests can pass topic_labels inline without reading taxonomy_v2.json."""
    hierarchy = {
        "topics": {
            "fake_topic": {
                "subtopics": [
                    {"id": "fake_topic_x", "display_name": "Fake Methods"},
                    {"id": "fake_topic_y", "display_name": "Fake & Bar"},  # violation: first word = 'Fake'
                ]
            }
        }
    }
    topic_labels = {"fake_topic": "Fake Topic"}
    result = parent_prefix_gate(hierarchy=hierarchy, topic_labels=topic_labels)
    assert result.passed is False
    assert result.details["violation_count"] == 2  # both start with 'Fake'


def test_parent_prefix_truncates_violation_list_at_50():
    """Defense against runaway details payload size."""
    subtopics = [
        {"id": f"fake_topic_x{i}", "display_name": f"Fake number {i}"} for i in range(75)
    ]
    hierarchy = {"topics": {"fake_topic": {"subtopics": subtopics}}}
    topic_labels = {"fake_topic": "Fake Topic"}
    result = parent_prefix_gate(hierarchy=hierarchy, topic_labels=topic_labels)
    assert result.passed is False
    assert result.details["violation_count"] == 75
    assert len(result.details["violations"]) == 50
    assert result.details["truncated"] is True


def test_parent_prefix_passes_empty_hierarchy():
    """Empty topics dict is structurally valid for the gate's contract."""
    result = parent_prefix_gate(hierarchy={"topics": {}}, topic_labels={})
    assert result.passed is True


def test_parent_prefix_registered_against_publish_stage():
    publish_gates = [g for g in registry.list_gates(stage="publish") if g["name"] == "parent_prefix"]
    assert len(publish_gates) == 1
    assert publish_gates[0]["severity"] == "block"

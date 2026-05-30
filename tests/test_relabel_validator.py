"""
Unit tests for the parent-prefix validator in relabel_subtopics.

The validator rejects subtopic display_names whose first word repeats a word
from the parent topic id or label. This is the prevention-at-source half of
issue #2 (the 87 SPS editorial warnings).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from cli.relabel_subtopics import (
    _collect_parent_prefix_violations,
    _retry_parent_prefix_violations,
    find_parent_prefix_violation,
)


def test_clean_display_name_passes():
    assert find_parent_prefix_violation(
        "microbiome_research", "Microbiome Research", "Cancer Immunotherapy Response"
    ) is None


def test_topic_id_first_word_match_is_violation():
    reason = find_parent_prefix_violation(
        "microbiome_research",
        "Microbiome Research",
        "Microbiome & Cancer Immunotherapy Response",
    )
    assert reason is not None
    assert "Microbiome" in reason


def test_topic_id_second_word_match_is_violation():
    # "research" is in topic_id `microbiome_research` — a subtopic starting with
    # "Research" repeats the parent context just as much.
    reason = find_parent_prefix_violation(
        "microbiome_research", "Microbiome Research", "Research Methods & Tools"
    )
    assert reason is not None


def test_topic_label_word_match_when_label_diverges_from_id():
    # If the label contains a content word that's not in the id, it still counts.
    reason = find_parent_prefix_violation(
        "geriatrics_aging", "Aging Geroscience", "Geroscience Biomarker Studies"
    )
    assert reason is not None


def test_hyphenated_first_word_normalized():
    # "Pre-Clinical" -> "preclinical"; parent contains "preclinical"
    reason = find_parent_prefix_violation(
        "preclinical_models", "Preclinical Models", "Pre-Clinical Imaging"
    )
    assert reason is not None


def test_case_insensitive():
    reason = find_parent_prefix_violation(
        "PULMONARY_critical_care",
        "Pulmonary Critical Care",
        "pulmonary hypertension",
    )
    assert reason is not None


def test_stopword_in_parent_does_not_match():
    # Parent "Aging and Geroscience" has "and" — a subtopic starting with "And"
    # would be malformed, but ensure stopwords don't seed false positives.
    assert find_parent_prefix_violation(
        "aging_and_geroscience", "Aging and Geroscience", "Frailty Biomarkers"
    ) is None


def test_empty_display_name_passes():
    assert find_parent_prefix_violation(
        "microbiome_research", "Microbiome Research", ""
    ) is None


def test_collect_aggregates_violations():
    subtopics = [
        {"id": "a", "display_name": "Microbiome & Cancer"},
        {"id": "b", "display_name": "Cancer Immunotherapy"},
        {"id": "c", "display_name": "Microbiome Methods"},
    ]
    out = _collect_parent_prefix_violations(
        subtopics, "microbiome_research", "Microbiome Research"
    )
    assert {v["id"] for v in out} == {"a", "c"}
    assert all("reason" in v for v in out)


def test_retry_replaces_violating_display_names():
    """Mock Bedrock returns clean labels; retry should mutate subtopics in place."""
    subtopics = [
        {"id": "a", "display_name": "Microbiome & Cancer", "short_description": "x"},
        {"id": "b", "display_name": "Cancer Methods", "short_description": "y"},
    ]
    violations = [
        {"id": "a", "display_name": "Microbiome & Cancer", "reason": "..."},
    ]
    mock_client = MagicMock()
    mock_client.call_json.return_value = {
        "relabels": [
            {"id": "a", "display_name": "Cancer Immunotherapy", "short_description": "x2"},
        ]
    }
    remaining = _retry_parent_prefix_violations(
        client=mock_client,
        topic_id="microbiome_research",
        topic_label="Microbiome Research",
        topic_description="...",
        subtopics=subtopics,
        violations=violations,
    )
    assert remaining == []
    assert subtopics[0]["display_name"] == "Cancer Immunotherapy"
    # Untouched
    assert subtopics[1]["display_name"] == "Cancer Methods"


def test_retry_reports_persistent_violations():
    """If Sonnet returns another violating label, the retry should surface it."""
    subtopics = [
        {"id": "a", "display_name": "Microbiome & Cancer", "short_description": "x"},
    ]
    violations = [
        {"id": "a", "display_name": "Microbiome & Cancer", "reason": "..."},
    ]
    mock_client = MagicMock()
    mock_client.call_json.return_value = {
        "relabels": [
            {"id": "a", "display_name": "Microbiome Dysbiosis", "short_description": "x2"},
        ]
    }
    remaining = _retry_parent_prefix_violations(
        client=mock_client,
        topic_id="microbiome_research",
        topic_label="Microbiome Research",
        topic_description="...",
        subtopics=subtopics,
        violations=violations,
    )
    assert len(remaining) == 1
    assert remaining[0]["id"] == "a"

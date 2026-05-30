"""
Tests for generate_see_also.py — bidirectionality filter and end-to-end pipeline.

Covers:
  - Test 1 (unit): bidirectionality filter drops asymmetric links
  - Test 2 (unit): self-loops dropped unconditionally
  - Test 3 (unit): duplicate (from, to) pairs deduplicated (first occurrence wins)
  - Test 4 (integration, mocked Sonnet): full pipeline writes correct output JSON
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from generate_see_also import _apply_bidirectionality_filter, generate_see_also


# ---------------------------------------------------------------------------
# Test 1: Bidirectionality filter drops asymmetric links
# ---------------------------------------------------------------------------

def test_bidirectionality_filter_drops_asymmetric():
    """(A,B) and (B,A) retained; (C,D) dropped because (D,C) absent."""
    links = [
        {"from": "A", "to": "B", "reason": "r1"},
        {"from": "B", "to": "A", "reason": "r2"},
        {"from": "C", "to": "D", "reason": "r3"},
    ]
    kept, stats = _apply_bidirectionality_filter(links)

    kept_pairs = {(l["from"], l["to"]) for l in kept}
    assert kept_pairs == {("A", "B"), ("B", "A")}
    assert stats["dropped_non_bidirectional"] == 1
    assert stats["dropped_self_loop"] == 0
    assert stats["dropped_duplicate"] == 0


# ---------------------------------------------------------------------------
# Test 2: Self-loops dropped unconditionally
# ---------------------------------------------------------------------------

def test_bidirectionality_filter_drops_self_loops():
    """(A,A) dropped even if present."""
    links = [
        {"from": "A", "to": "A", "reason": "self"},
        {"from": "X", "to": "Y", "reason": "r1"},
        {"from": "Y", "to": "X", "reason": "r2"},
    ]
    kept, stats = _apply_bidirectionality_filter(links)

    kept_pairs = {(l["from"], l["to"]) for l in kept}
    assert ("A", "A") not in kept_pairs
    assert kept_pairs == {("X", "Y"), ("Y", "X")}
    assert stats["dropped_self_loop"] == 1


# ---------------------------------------------------------------------------
# Test 3: Duplicate (from, to) pairs deduplicated (keep first occurrence)
# ---------------------------------------------------------------------------

def test_bidirectionality_filter_dedupes_duplicates():
    """Duplicate (A,B) entries collapse to one; first reason wins."""
    links = [
        {"from": "A", "to": "B", "reason": "first"},
        {"from": "B", "to": "A", "reason": "r2"},
        {"from": "A", "to": "B", "reason": "second (duplicate)"},
    ]
    kept, stats = _apply_bidirectionality_filter(links)

    ab = [l for l in kept if l["from"] == "A" and l["to"] == "B"]
    assert len(ab) == 1
    assert ab[0]["reason"] == "first"
    assert stats["dropped_duplicate"] == 1


# ---------------------------------------------------------------------------
# Test 4 (integration, mocked Sonnet): full pipeline writes expected output
# ---------------------------------------------------------------------------

def test_generate_see_also_end_to_end_mocked(tmp_path):
    """Full pipeline: fake hierarchy with 2 parent topics -> mocked Sonnet ->
    filtered output JSON with pre_filter_count / post_filter_count."""
    input_path = tmp_path / "hierarchy.json"
    output_path = tmp_path / "see_also.json"

    hierarchy = {
        "topics": {
            "aging_geroscience": {
                "subtopics": [
                    {"id": "aging_cardiovascular_disease", "label": "Cardiovascular Aging",
                     "description": "Age-related cardiac changes."},
                    {"id": "aging_cellular_senescence", "label": "Cellular Senescence",
                     "description": "Senescent cells in aging."},
                ]
            },
            "cardiovascular_disease": {
                "subtopics": [
                    {"id": "cvd_heart_failure", "label": "Heart Failure",
                     "description": "HF across clinical spectrum."},
                ]
            },
        }
    }
    input_path.write_text(json.dumps(hierarchy))

    # Mock Sonnet to return 3 proposed links:
    #   (aging_cardiovascular_disease, cvd_heart_failure)  — has reverse => kept
    #   (cvd_heart_failure, aging_cardiovascular_disease)  — has forward => kept
    #   (aging_cellular_senescence, cvd_heart_failure)     — no reverse  => dropped
    mocked_sonnet_response = {
        "links": [
            {"from": "aging_cardiovascular_disease", "to": "cvd_heart_failure",
             "reason": "Both address cardiac decline in older adults."},
            {"from": "cvd_heart_failure", "to": "aging_cardiovascular_disease",
             "reason": "Aging is a methodological lens for HF cohorts."},
            {"from": "aging_cellular_senescence", "to": "cvd_heart_failure",
             "reason": "Weak link, no reverse proposed."},
        ]
    }

    with patch("generate_see_also._call_sonnet_for_see_also",
               return_value=mocked_sonnet_response):
        result = generate_see_also(
            input_path=str(input_path),
            output_path=str(output_path),
            temperature=0.0,
        )

    assert output_path.exists()
    out = json.loads(output_path.read_text())

    assert "see_also" in out
    assert out["temperature"] == 0.0
    assert out["pre_filter_count"] == 3
    assert out["post_filter_count"] == 2
    assert "generated_at" in out
    assert "sonnet_model_id" in out

    kept_pairs = {(l["from"], l["to"]) for l in out["see_also"]}
    assert kept_pairs == {
        ("aging_cardiovascular_disease", "cvd_heart_failure"),
        ("cvd_heart_failure", "aging_cardiovascular_disease"),
    }

    # Return value matches what was written
    assert result["post_filter_count"] == 2

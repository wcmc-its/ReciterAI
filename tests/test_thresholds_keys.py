"""Tests for config/thresholds.json key presence and value correctness — Phase 12 D-23/D-24/D-25.

Covers:
- confidence_floor is distinct from low_confidence_floor (D-25 anti-collapse)
- All Phase 12 keys present with documented defaults (D-23)
- No confidence_floor_default key exists (D-24)
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
THRESHOLDS_PATH = REPO_ROOT / "config/thresholds.json"


def _load() -> dict:
    return json.loads(THRESHOLDS_PATH.read_text(encoding="utf-8"))


def test_confidence_floor_distinct_from_low_confidence_floor():
    """D-25 anti-collapse: confidence_floor and low_confidence_floor are separate keys.

    If this test fails, Phase 12 D-25 has been regressed: confidence_floor and
    low_confidence_floor were collapsed into a single key. They represent different
    decisions (assignment-time membership vs event-emission threshold). Restore both
    keys with distinct values.
    """
    cfg = _load()
    assert "confidence_floor" in cfg, "confidence_floor key missing"
    assert "low_confidence_floor" in cfg, "low_confidence_floor key missing"
    assert cfg["confidence_floor"] == 0.3, (
        f"confidence_floor must be 0.3 (assignment-time floor per D-23), got {cfg['confidence_floor']}"
    )
    assert cfg["low_confidence_floor"] == 0.35, (
        f"low_confidence_floor must be 0.35 (event-emission threshold), got {cfg['low_confidence_floor']}"
    )
    assert cfg["confidence_floor"] != cfg["low_confidence_floor"], (
        "confidence_floor and low_confidence_floor must differ — they are different decisions "
        "(assignment-time membership vs event-emission threshold per D-25)"
    )


def test_phase_12_keys_present():
    """All Phase 12 G-18 keys must be present with their documented defaults (D-23)."""
    cfg = _load()
    expected = {
        "tie_epsilon": 0.001,
        "confidence_floor": 0.3,
        "score_floor": 0.3,
        "feedback_sweep_max_pmids": 200,
        "recluster_persistence_days": 7,
        "critic_reject_persistence_days": 90,
        "critic_reject_subtopic_max": 2,
        "feedback_diagnostic_max_underlying": 20,
    }
    for key, value in expected.items():
        assert key in cfg, f"Phase 12 key '{key}' missing from thresholds.json"
        assert cfg[key] == value, (
            f"thresholds.json['{key}'] expected {value!r}, got {cfg[key]!r}"
        )


def test_no_default_suffix_on_confidence_floor():
    """D-24: confidence_floor_default must NOT exist.

    A _default suffix would falsely promise a per-topic override mechanism
    that doesn't exist. The key is simply 'confidence_floor'.
    """
    cfg = _load()
    assert "confidence_floor_default" not in cfg, (
        "confidence_floor_default found — D-24 violated. "
        "No per-topic override exists; use plain 'confidence_floor'."
    )

"""Tests for config/thresholds.json schema validation — Phase 12 D-27.

Covers:
- Well-formed thresholds.json passes jsonschema validation
- Unknown key raises jsonschema.ValidationError (additionalProperties=false)
- Wrong type raises jsonschema.ValidationError (e.g., string where float expected)
- Negative value outside minimum raises jsonschema.ValidationError (minimum: 1)
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
THRESHOLDS_PATH = REPO_ROOT / "config/thresholds.json"
SCHEMA_PATH = REPO_ROOT / "config/thresholds.schema.json"


def _load() -> tuple[dict, dict]:
    cfg = json.loads(THRESHOLDS_PATH.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return cfg, schema


def test_well_formed_thresholds_passes_validation():
    """The actual config/thresholds.json passes schema validation."""
    cfg, schema = _load()
    # Should not raise.
    jsonschema.validate(instance=cfg, schema=schema)


def test_unknown_key_raises_validation_error():
    """additionalProperties=false means any unknown key is rejected."""
    cfg, schema = _load()
    mutated = dict(cfg)
    mutated["unknown_key"] = 1
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=mutated, schema=schema)


def test_wrong_type_raises_validation_error():
    """confidence_floor must be a number; a string value must fail."""
    cfg, schema = _load()
    mutated = dict(cfg)
    mutated["confidence_floor"] = "0.3"  # string, not float
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=mutated, schema=schema)


def test_negative_value_outside_minimum_raises():
    """feedback_sweep_max_pmids declares minimum: 1; -1 must fail."""
    cfg, schema = _load()
    mutated = dict(cfg)
    mutated["feedback_sweep_max_pmids"] = -1
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=mutated, schema=schema)

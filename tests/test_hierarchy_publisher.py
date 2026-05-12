"""
Reproducibility + schema tests for pipeline_hierarchy.

Same inputs + pinned generated_at -> stable sha256. This is the canonical
invariant the publisher must hold: serialization is deterministic so the
SPS ETL can short-circuit on unchanged sha256.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from pipeline_hierarchy.generator import (
    SCHEMA_PATH,
    SOURCE_HIERARCHY,
    build_hierarchy,
    generate,
    validate,
)


PINNED_GENERATED_AT = "2026-05-11T00:00:00Z"


def test_build_hierarchy_pins_generated_at_and_sets_see_also():
    h = build_hierarchy(generated_at=PINNED_GENERATED_AT)
    assert h["generated_at"] == PINNED_GENERATED_AT
    assert h["see_also"] == []
    assert h["taxonomy_version"] == "taxonomy_v2"
    assert h["version"] == "subtopic_v1"


def test_topics_are_sorted():
    h = build_hierarchy(generated_at=PINNED_GENERATED_AT)
    keys = list(h["topics"].keys())
    assert keys == sorted(keys)


def test_schema_validation_passes():
    h = build_hierarchy(generated_at=PINNED_GENERATED_AT)
    validate(h)  # raises on failure


def test_reproducibility_same_pin_same_sha256():
    h_bytes_1, _, manifest_1 = generate(generated_at=PINNED_GENERATED_AT, version="vtest")
    h_bytes_2, _, manifest_2 = generate(generated_at=PINNED_GENERATED_AT, version="vtest")
    assert manifest_1["sha256"] == manifest_2["sha256"]
    assert h_bytes_1 == h_bytes_2
    assert hashlib.sha256(h_bytes_1).hexdigest() == manifest_1["sha256"]


def test_default_version_derived_from_generated_at():
    _, _, manifest = generate(generated_at="2026-12-31T23:59:59Z")
    assert manifest["version"] == "v2026-12-31"


def test_topics_count_matches_source():
    raw = json.loads(SOURCE_HIERARCHY.read_text())
    h = build_hierarchy(generated_at=PINNED_GENERATED_AT)
    assert set(h["topics"].keys()) == set(raw["topics"].keys())


def test_all_subtopics_have_required_fields():
    h = build_hierarchy(generated_at=PINNED_GENERATED_AT)
    required = {"id", "label", "description", "display_name", "short_description", "activity_count", "total_weight"}
    for topic_id, topic in h["topics"].items():
        for sub in topic["subtopics"]:
            missing = required - set(sub.keys())
            assert not missing, f"{topic_id}/{sub.get('id')} missing {missing}"

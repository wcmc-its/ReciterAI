"""
Reproducibility + schema tests for pipeline_hierarchy.

Same inputs + pinned generated_at -> stable sha256. This is the canonical
invariant the publisher must hold: serialization is deterministic so the
SPS ETL can short-circuit on unchanged sha256.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from pipeline_hierarchy.bundler import bundle
from pipeline_hierarchy.generator import (
    SCHEMA_PATH,
    build_hierarchy,
    generate,
    validate,
)


PINNED_GENERATED_AT = "2026-05-11T00:00:00Z"

# Two real per-topic augmented files, trimmed to 3 subtopics each. Committed so
# the publish-contract tests run on any clone and in CI: bundle()'s default dir
# lives under the gitignored .planning/ tree that only the operator's machine
# has, and these are the tests guarding the SPS-facing hierarchy.json.
FIXTURE_AUGMENTED_DIR = Path(__file__).parent / "fixtures" / "hierarchy_augmented"


@pytest.fixture(scope="module")
def live_bundle() -> dict:
    """Bundle the fixture per-topic augmented files once per test module."""
    return bundle(augmented_dir=FIXTURE_AUGMENTED_DIR)


def test_build_hierarchy_pins_generated_at_and_sets_see_also(live_bundle):
    h = build_hierarchy(hierarchy=live_bundle, generated_at=PINNED_GENERATED_AT)
    # D-14: generated_at must NOT be in the hierarchy dict
    assert "generated_at" not in h, (
        "D-14: build_hierarchy() must not embed generated_at into the hierarchy dict"
    )
    assert h["see_also"] == []
    assert h["taxonomy_version"] == "taxonomy_v2"
    assert h["version"] == "subtopic_v1"


def test_manifest_includes_generated_at(live_bundle):
    """D-14: manifest.json continues to carry generated_at; hierarchy.json does not."""
    _, _, manifest = generate(hierarchy=live_bundle, generated_at=PINNED_GENERATED_AT, version="vtest")
    assert "generated_at" in manifest, "D-14: manifest must carry generated_at"


def test_manifest_generated_at_matches_iso_regex(live_bundle):
    """D-14: manifest generated_at must be a valid ISO8601 UTC timestamp."""
    _, _, manifest = generate(hierarchy=live_bundle, generated_at=PINNED_GENERATED_AT, version="vtest")
    pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
    assert re.match(pattern, manifest["generated_at"]), (
        f"manifest.generated_at {manifest['generated_at']!r} doesn't match ISO pattern"
    )


def test_topics_are_sorted(live_bundle):
    h = build_hierarchy(hierarchy=live_bundle, generated_at=PINNED_GENERATED_AT)
    keys = list(h["topics"].keys())
    assert keys == sorted(keys)


def test_schema_validation_passes(live_bundle):
    h = build_hierarchy(hierarchy=live_bundle, generated_at=PINNED_GENERATED_AT)
    validate(h)  # raises on failure


def test_reproducibility_same_pin_same_sha256(live_bundle):
    h_bytes_1, _, manifest_1 = generate(hierarchy=live_bundle, generated_at=PINNED_GENERATED_AT, version="vtest")
    h_bytes_2, _, manifest_2 = generate(hierarchy=live_bundle, generated_at=PINNED_GENERATED_AT, version="vtest")
    assert manifest_1["sha256"] == manifest_2["sha256"]
    assert h_bytes_1 == h_bytes_2
    assert hashlib.sha256(h_bytes_1).hexdigest() == manifest_1["sha256"]


def test_default_version_derived_from_generated_at(live_bundle):
    _, _, manifest = generate(hierarchy=live_bundle, generated_at="2026-12-31T23:59:59Z")
    assert manifest["version"] == "v2026-12-31"


def test_topic_keys_match_bundler_output(live_bundle):
    """Structural invariant (formerly compared against checked-in hierarchy_full.json).
    build_hierarchy() must preserve the topic set produced by the bundler."""
    h = build_hierarchy(hierarchy=live_bundle, generated_at=PINNED_GENERATED_AT)
    assert set(h["topics"].keys()) == set(live_bundle["topics"].keys())


def test_all_subtopics_have_required_fields(live_bundle):
    h = build_hierarchy(hierarchy=live_bundle, generated_at=PINNED_GENERATED_AT)
    required = {"id", "label", "description", "display_name", "short_description", "activity_count", "total_weight"}
    for topic_id, topic in h["topics"].items():
        for sub in topic["subtopics"]:
            missing = required - set(sub.keys())
            assert not missing, f"{topic_id}/{sub.get('id')} missing {missing}"

"""Phase 11 D-14 / G-36 prerequisite: reproducibility tests.

hierarchy.json is bit-stable across content-identical reruns because
generated_at is no longer embedded (D-14). Two-pass bundle + build must
produce byte-identical json.dumps output with the same inputs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline_hierarchy.bundler import (
    DEFAULT_AUGMENTED_DIR,
    bundle,
)
from pipeline_hierarchy.generator import (
    SOURCE_HIERARCHY,
    build_hierarchy,
    generate,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------- fixture helpers (shared with test_hierarchy_bundler) ----------


def _write_augmented(dir_path: Path, topic_id: str, subtopics: list[dict]) -> None:
    payload = {
        "topic_id": topic_id,
        "topic_label": topic_id.replace("_", " ").title(),
        "subtopics": subtopics,
    }
    (dir_path / f"hierarchy_augmented_{topic_id}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _minimal_sub(sid: str) -> dict:
    return {
        "id": sid,
        "label": f"Label for {sid}",
        "description": f"Description for {sid}.",
        "total_weight": 12.5,
        "activity_count": 7,
        "display_name": f"{sid.title()} UI",
        "short_description": f"Tagline for {sid}.",
    }


def _make_taxonomy(tmp_path: Path) -> Path:
    p = tmp_path / "taxonomy.json"
    p.write_text(json.dumps({"taxonomy_version": "taxonomy_v_test", "topics": []}))
    return p


def _make_excluded(tmp_path: Path) -> Path:
    p = tmp_path / "excluded.json"
    p.write_text(json.dumps({"excluded_topics": []}))
    return p


# ---------- reproducibility tests ----------


def test_bundle_is_byte_stable_across_two_passes(tmp_path):
    """D-14 / G-36: Two calls to bundle() with same inputs → byte-identical
    json.dumps output. Since generated_at is removed from the hierarchy dict,
    no timestamp drift can differentiate two passes."""
    aug_dir = tmp_path / "aug"
    aug_dir.mkdir()
    _write_augmented(aug_dir, "topic_a", [_minimal_sub("topic_a_one")])
    _write_augmented(aug_dir, "topic_b", [_minimal_sub("topic_b_one")])

    kwargs = dict(
        augmented_dir=aug_dir,
        taxonomy_path=_make_taxonomy(tmp_path),
        excluded_topics_path=_make_excluded(tmp_path),
    )

    result1 = bundle(**kwargs)
    result2 = bundle(**kwargs)

    serialized1 = json.dumps(result1, sort_keys=True)
    serialized2 = json.dumps(result2, sort_keys=True)
    assert serialized1 == serialized2, (
        "D-14: bundle() output must be byte-stable across two passes with same inputs"
    )


def test_build_hierarchy_is_byte_stable_across_two_passes(tmp_path):
    """D-14 / G-36: Two calls to build_hierarchy() with the same in-memory hierarchy
    produce byte-identical json.dumps output."""
    aug_dir = tmp_path / "aug"
    aug_dir.mkdir()
    _write_augmented(aug_dir, "topic_a", [_minimal_sub("topic_a_one")])
    _write_augmented(aug_dir, "topic_b", [_minimal_sub("topic_b_one")])

    bundled = bundle(
        augmented_dir=aug_dir,
        taxonomy_path=_make_taxonomy(tmp_path),
        excluded_topics_path=_make_excluded(tmp_path),
    )

    # Pass a fixed generated_at to get deterministic manifest for both passes.
    h1 = build_hierarchy(hierarchy=bundled, generated_at="2026-06-01T00:00:00Z")
    h2 = build_hierarchy(hierarchy=bundled, generated_at="2026-06-01T00:00:00Z")

    s1 = json.dumps(h1, sort_keys=True)
    s2 = json.dumps(h2, sort_keys=True)
    assert s1 == s2, (
        "D-14: build_hierarchy() must produce byte-identical output across passes with same inputs"
    )


def test_generate_is_byte_stable_with_pinned_generated_at():
    """D-14 / G-36: generate() with the same generated_at produces byte-identical
    hierarchy bytes. Uses the live source hierarchy."""
    pinned = "2026-06-01T00:00:00Z"

    h_bytes1, _, manifest1 = generate(generated_at=pinned, version="v2026-06-01")
    h_bytes2, _, manifest2 = generate(generated_at=pinned, version="v2026-06-01")

    assert h_bytes1 == h_bytes2, (
        "generate() must produce byte-identical hierarchy bytes with the same generated_at"
    )
    assert manifest1["sha256"] == manifest2["sha256"], (
        "sha256 must match across two passes with the same generated_at"
    )


def test_hierarchy_dict_has_no_generated_at_after_build(tmp_path):
    """D-14: build_hierarchy() must not embed generated_at into the hierarchy dict
    even when a generated_at kwarg is passed for manifest derivation."""
    aug_dir = tmp_path / "aug"
    aug_dir.mkdir()
    _write_augmented(aug_dir, "topic_a", [_minimal_sub("topic_a_one")])

    bundled = bundle(
        augmented_dir=aug_dir,
        taxonomy_path=_make_taxonomy(tmp_path),
        excluded_topics_path=_make_excluded(tmp_path),
    )
    h = build_hierarchy(hierarchy=bundled, generated_at="2026-06-01T00:00:00Z")
    assert "generated_at" not in h, (
        "D-14: hierarchy dict must not contain generated_at after build_hierarchy()"
    )

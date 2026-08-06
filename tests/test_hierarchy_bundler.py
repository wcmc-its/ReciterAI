"""
Tests for `pipeline_hierarchy.bundler` — the per-topic → full hierarchy bundler
that closes the publisher input gap (issue #4).

Two layers of coverage:

1. Fixture-based: hand-crafted minimal augmented files exercise the strict
   contract (required UI fields, schema-shape output, excluded_topics
   propagation, end-to-end pass through generator.validate).
2. Live structural: run the bundler against the real
   `.planning/.../hierarchy_augmented_*.json` files and assert it produces
   the expected topic / subtopic counts (1526 published subtopics across 65
   published topics; 66/1541 on disk, less the excluded `implementation_science`)
   with the D-19 UI fields populated on every subtopic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline_hierarchy.bundler import (
    DEFAULT_AUGMENTED_DIR,
    MissingUIFieldsError,
    bundle,
    write_bundle,
)
from pipeline_hierarchy.generator import SCHEMA_PATH, build_hierarchy, validate


# ---------- fixture helpers ----------

def _write_augmented(dir_path: Path, topic_id: str, subtopics: list[dict]) -> None:
    payload = {
        "topic_id": topic_id,
        "topic_label": topic_id.replace("_", " ").title(),
        "subtopics": subtopics,
    }
    (dir_path / f"hierarchy_augmented_{topic_id}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _minimal_sub(sid: str, *, with_ui: bool = True) -> dict:
    s = {
        "id": sid,
        "label": f"Label for {sid}",
        "description": f"Description for {sid}.",
        "seed_pmids": [123, 456],  # extraneous; bundler must strip
        "coverage_estimate": 0.5,  # extraneous
        "total_weight": 12.5,
        "activity_count": 7,
    }
    if with_ui:
        s["display_name"] = f"{sid.title()} UI"
        s["short_description"] = f"Tagline for {sid}."
    return s


def _make_taxonomy(tmp_path: Path) -> Path:
    p = tmp_path / "taxonomy.json"
    p.write_text(json.dumps({"taxonomy_version": "taxonomy_v_test", "topics": []}))
    return p


def _make_excluded(tmp_path: Path) -> Path:
    p = tmp_path / "excluded.json"
    p.write_text(
        json.dumps(
            {
                "excluded_topics": [
                    {"id": "implementation_science", "reason": "fail", "activity_count": 1314}
                ]
            }
        )
    )
    return p


# ---------- fixture-based tests ----------

def test_bundles_minimal_two_topic_input(tmp_path):
    aug_dir = tmp_path / "aug"
    aug_dir.mkdir()
    _write_augmented(aug_dir, "topic_a", [_minimal_sub("topic_a_one"), _minimal_sub("topic_a_two")])
    _write_augmented(aug_dir, "topic_b", [_minimal_sub("topic_b_one")])

    result = bundle(
        augmented_dir=aug_dir,
        taxonomy_path=_make_taxonomy(tmp_path),
        excluded_topics_path=_make_excluded(tmp_path),
    )

    assert result["version"] == "subtopic_v1"
    assert result["taxonomy_version"] == "taxonomy_v_test"
    # D-14: hierarchy dict no longer contains generated_at
    assert "generated_at" not in result
    assert result["see_also"] == []
    assert set(result["topics"].keys()) == {"topic_a", "topic_b"}
    assert len(result["topics"]["topic_a"]["subtopics"]) == 2
    assert len(result["topics"]["topic_b"]["subtopics"]) == 1


def test_strips_non_schema_subtopic_fields(tmp_path):
    aug_dir = tmp_path / "aug"
    aug_dir.mkdir()
    _write_augmented(aug_dir, "topic_a", [_minimal_sub("topic_a_one")])

    result = bundle(
        augmented_dir=aug_dir,
        taxonomy_path=_make_taxonomy(tmp_path),
        excluded_topics_path=_make_excluded(tmp_path),
    )
    sub = result["topics"]["topic_a"]["subtopics"][0]
    assert "seed_pmids" not in sub
    assert "coverage_estimate" not in sub
    assert set(sub.keys()) == {
        "id", "label", "description", "display_name", "short_description",
        "activity_count", "total_weight",
    }


def test_excluded_topics_carry_through(tmp_path):
    aug_dir = tmp_path / "aug"
    aug_dir.mkdir()
    _write_augmented(aug_dir, "topic_a", [_minimal_sub("topic_a_one")])

    result = bundle(
        augmented_dir=aug_dir,
        taxonomy_path=_make_taxonomy(tmp_path),
        excluded_topics_path=_make_excluded(tmp_path),
    )
    assert result["excluded_topics"] == [
        {"id": "implementation_science", "reason": "fail", "activity_count": 1314}
    ]


def test_excluded_topic_is_not_published_and_does_not_break_strict(tmp_path):
    """#344 / ADR D7: excluded == scored but not published.

    The excluded topic's augmented file is deliberately missing the UI fields.
    A post-hoc delete from `topics` would let it accumulate into `missing` and
    raise MissingUIFieldsError for a topic that is not being published, so this
    also pins the skip's placement, not just its effect.
    """
    aug_dir = tmp_path / "aug"
    aug_dir.mkdir()
    _write_augmented(aug_dir, "topic_a", [_minimal_sub("topic_a_one")])
    _write_augmented(
        aug_dir, "implementation_science", [_minimal_sub("is_one", with_ui=False)]
    )

    result = bundle(
        augmented_dir=aug_dir,
        taxonomy_path=_make_taxonomy(tmp_path),
        excluded_topics_path=_make_excluded(tmp_path),
        strict=True,
    )

    assert set(result["topics"]) == {"topic_a"}
    # still declared in the metadata — that half already worked
    assert [e["id"] for e in result["excluded_topics"]] == ["implementation_science"]


def test_strict_raises_on_missing_display_name(tmp_path):
    aug_dir = tmp_path / "aug"
    aug_dir.mkdir()
    _write_augmented(
        aug_dir, "topic_a",
        [_minimal_sub("topic_a_one", with_ui=False), _minimal_sub("topic_a_two")],
    )

    with pytest.raises(MissingUIFieldsError) as exc:
        bundle(
            augmented_dir=aug_dir,
            taxonomy_path=_make_taxonomy(tmp_path),
            excluded_topics_path=_make_excluded(tmp_path),
        )
    assert "topic_a_one" in str(exc.value)


def test_non_strict_passes_missing_ui_fields_as_empty(tmp_path):
    aug_dir = tmp_path / "aug"
    aug_dir.mkdir()
    _write_augmented(
        aug_dir, "topic_a", [_minimal_sub("topic_a_one", with_ui=False)]
    )
    result = bundle(
        augmented_dir=aug_dir,
        taxonomy_path=_make_taxonomy(tmp_path),
        excluded_topics_path=_make_excluded(tmp_path),
        strict=False,
    )
    sub = result["topics"]["topic_a"]["subtopics"][0]
    assert sub["display_name"] == ""
    assert sub["short_description"] == ""


def test_empty_augmented_dir_raises(tmp_path):
    (tmp_path / "aug").mkdir()
    with pytest.raises(FileNotFoundError):
        bundle(
            augmented_dir=tmp_path / "aug",
            taxonomy_path=_make_taxonomy(tmp_path),
            excluded_topics_path=_make_excluded(tmp_path),
        )


def test_bundle_then_validate_passes_schema(tmp_path):
    """End-to-end: bundle output should pass the published schema after
    generator.build_hierarchy() sorts it. D-14: hierarchy dict has no generated_at."""
    aug_dir = tmp_path / "aug"
    aug_dir.mkdir()
    _write_augmented(aug_dir, "topic_a", [_minimal_sub("topic_a_one")])
    _write_augmented(aug_dir, "topic_b", [_minimal_sub("topic_b_one")])

    h = bundle(
        augmented_dir=aug_dir,
        taxonomy_path=_make_taxonomy(tmp_path),
        excluded_topics_path=_make_excluded(tmp_path),
    )
    built = build_hierarchy(hierarchy=h, generated_at="2026-05-11T00:00:00Z")
    validate(built, schema_path=SCHEMA_PATH)


# ---------- D-14 / G-29 fix tests ----------


def test_bundle_does_not_include_generated_at(tmp_path):
    """D-14: bundle() output MUST NOT contain 'generated_at'.
    hierarchy.json is timestamp-free; manifest.json carries the timestamp."""
    aug_dir = tmp_path / "aug"
    aug_dir.mkdir()
    _write_augmented(aug_dir, "topic_a", [_minimal_sub("topic_a_one")])
    result = bundle(
        augmented_dir=aug_dir,
        taxonomy_path=_make_taxonomy(tmp_path),
        excluded_topics_path=_make_excluded(tmp_path),
    )
    assert "generated_at" not in result, (
        "D-14: bundle() must not embed generated_at into the hierarchy dict"
    )


def test_bundle_raises_typeerror_if_generated_at_kwarg_passed(tmp_path):
    """D-14 / W2: bundle() signature no longer accepts generated_at kwarg."""
    aug_dir = tmp_path / "aug"
    aug_dir.mkdir()
    _write_augmented(aug_dir, "topic_a", [_minimal_sub("topic_a_one")])
    with pytest.raises(TypeError, match="generated_at"):
        bundle(
            augmented_dir=aug_dir,
            taxonomy_path=_make_taxonomy(tmp_path),
            excluded_topics_path=_make_excluded(tmp_path),
            generated_at="2026-05-11T00:00:00Z",  # must raise
        )


def test_write_bundle_raises_typeerror_if_generated_at_kwarg_passed(tmp_path):
    """D-14 / W2: write_bundle() signature also no longer accepts generated_at."""
    aug_dir = tmp_path / "aug"
    aug_dir.mkdir()
    _write_augmented(aug_dir, "topic_a", [_minimal_sub("topic_a_one")])
    out = tmp_path / "out.json"
    with pytest.raises(TypeError, match="generated_at"):
        write_bundle(
            out_path=out,
            augmented_dir=aug_dir,
            taxonomy_path=_make_taxonomy(tmp_path),
            excluded_topics_path=_make_excluded(tmp_path),
            generated_at="2026-05-11T00:00:00Z",  # must raise
        )


# ---------- live structural tests ----------


@pytest.mark.skipif(
    not any(DEFAULT_AUGMENTED_DIR.glob("hierarchy_augmented_*.json")),
    reason="needs the live per-topic augmented corpus under gitignored .planning/",
)
def test_live_bundler_strict_mode_succeeds_after_relabel():
    """Post-2026-05-12 relabel pass: every augmented file has display_name +
    short_description populated, so strict-mode bundle succeeds. This is the
    flip of the prior `_fails_until_relabel_repopulates` test, which pinned
    the pre-relabel failure mode.

    Asserts against the real corpus, so it only runs where that corpus exists.
    The publish contract itself is covered on every clone by
    tests/test_hierarchy_publisher.py's committed fixtures.

    Counts are post-#344: the corpus holds 66 augmented files / 1541 subtopics,
    and `implementation_science` (15 subtopics) is now withheld from `topics`
    by the D7 exclusion enforcement. `oral_craniofacial_health`, the other
    excluded entry, has no augmented file at all, so it does not move these."""
    rebuilt = bundle(
        augmented_dir=DEFAULT_AUGMENTED_DIR,
    )
    assert len(rebuilt["topics"]) == 65
    total = sum(len(t["subtopics"]) for t in rebuilt["topics"].values())
    assert total == 1526
    assert "implementation_science" not in rebuilt["topics"]
    # Every subtopic carries both D-19 fields.
    for topic in rebuilt["topics"].values():
        for sub in topic["subtopics"]:
            assert sub.get("display_name"), f"missing display_name on {sub.get('id')}"
            assert sub.get("short_description"), f"missing short_description on {sub.get('id')}"

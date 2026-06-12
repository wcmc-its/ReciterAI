"""Tests for the durable_id cross-artifact shadow validator (#191 brick D3, §7).

Covers the pure core (no I/O):
- extractors pull {slug -> durable_id} from each artifact/shape, skipping absent companions;
- intra-artifact conflict detection (same slug, two durables in one artifact);
- reconcile: all-agree, cross-surface disagreement, store disagreement, absent-from-store,
  the inert empty-store/gate-off case, and the --no-store (cross-artifact only) path;
- coverage counts and the ok/exit semantics.
"""

from __future__ import annotations

from scripts.validate_durable_id_propagation import (
    SURFACE_HIERARCHY,
    SURFACE_SPOTLIGHT,
    SURFACE_TOPIC,
    extract_hierarchy_durable_map,
    extract_spotlight_durable_map,
    extract_topic_durable_map,
    reconcile_durable_ids,
)


# --------------------------------------------------------------------------- #
# extractors
# --------------------------------------------------------------------------- #


def test_extract_hierarchy_skips_subtopics_without_durable():
    hierarchy = {
        "topics": {
            "aging": {
                "subtopics": [
                    {"id": "aging_one", "durable_id": "st_aaa"},
                    {"id": "aging_two"},  # no durable yet (one-run lag)
                    {"id": "aging_three", "durable_id": ""},  # empty -> omitted
                ]
            },
            "cardio": {"subtopics": [{"id": "cardio_one", "durable_id": "st_bbb"}]},
        }
    }
    assert extract_hierarchy_durable_map(hierarchy) == {
        "aging_one": "st_aaa",
        "cardio_one": "st_bbb",
    }


def test_extract_hierarchy_empty_and_missing_topics():
    assert extract_hierarchy_durable_map({}) == {}
    assert extract_hierarchy_durable_map({"topics": {}}) == {}


def test_extract_spotlight_from_both_sections():
    spotlight = {
        "spotlights": [
            {"subtopic_id": "a", "durable_id": "st_a"},
            {"subtopic_id": "b"},  # absent companion
        ],
        "pool_snapshot": [
            {"subtopic_id": "a", "durable_id": "st_a"},  # agrees -> fine
            {"subtopic_id": "c", "durable_id": "st_c"},
        ],
    }
    out, conflicts = extract_spotlight_durable_map(spotlight)
    assert out == {"a": "st_a", "c": "st_c"}
    assert conflicts == []


def test_extract_spotlight_intra_artifact_conflict():
    spotlight = {
        "spotlights": [{"subtopic_id": "a", "durable_id": "st_a"}],
        "pool_snapshot": [{"subtopic_id": "a", "durable_id": "st_DIFFERENT"}],
    }
    out, conflicts = extract_spotlight_durable_map(spotlight)
    assert out == {"a": "st_a"}
    assert conflicts == [{"subtopic_id": "a", "first": "st_a", "second": "st_DIFFERENT"}]


def test_extract_topic_rows_and_conflict():
    rows = [
        {"primary_subtopic_id": "a", "primary_subtopic_durable_id": "st_a"},
        {"primary_subtopic_id": "a", "primary_subtopic_durable_id": "st_a"},  # dup ok
        {"primary_subtopic_id": "b"},  # no durable
        {"primary_subtopic_id": "c", "primary_subtopic_durable_id": "st_c"},
    ]
    out, conflicts = extract_topic_durable_map(rows)
    assert out == {"a": "st_a", "c": "st_c"}
    assert conflicts == []

    bad = [
        {"primary_subtopic_id": "a", "primary_subtopic_durable_id": "st_a"},
        {"primary_subtopic_id": "a", "primary_subtopic_durable_id": "st_other"},
    ]
    _, conflicts = extract_topic_durable_map(bad)
    assert conflicts == [{"primary_subtopic_id": "a", "first": "st_a", "second": "st_other"}]


# --------------------------------------------------------------------------- #
# reconcile
# --------------------------------------------------------------------------- #


def test_reconcile_all_agree_across_surfaces_and_store():
    maps = {
        SURFACE_HIERARCHY: {"a": "st_a", "b": "st_b"},
        SURFACE_SPOTLIGHT: {"a": "st_a"},
        SURFACE_TOPIC: {"a": "st_a", "b": "st_b"},
    }
    store = {"a": "st_a", "b": "st_b", "c": "st_c"}
    report = reconcile_durable_ids(maps, store)
    assert report.ok
    assert report.slugs_checked == 2
    assert report.consistent == 2
    assert report.mismatches == []
    assert report.coverage == {SURFACE_HIERARCHY: 2, SURFACE_SPOTLIGHT: 1, SURFACE_TOPIC: 2}
    assert set(report.surfaces_present) == {SURFACE_HIERARCHY, SURFACE_SPOTLIGHT, SURFACE_TOPIC}
    assert report.store_compared is True
    assert report.store_size == 3


def test_reconcile_cross_surface_disagreement():
    maps = {
        SURFACE_HIERARCHY: {"a": "st_a"},
        SURFACE_TOPIC: {"a": "st_DIFFERENT"},
    }
    report = reconcile_durable_ids(maps, store_map=None)
    assert not report.ok
    assert report.consistent == 0
    assert len(report.mismatches) == 1
    m = report.mismatches[0]
    assert m["slug"] == "a"
    assert "cross_surface_disagreement" in m["reasons"]
    assert m["by_surface"] == {SURFACE_HIERARCHY: "st_a", SURFACE_TOPIC: "st_DIFFERENT"}
    assert report.store_compared is False


def test_reconcile_store_disagreement():
    maps = {SURFACE_HIERARCHY: {"a": "st_a"}, SURFACE_SPOTLIGHT: {"a": "st_a"}}
    store = {"a": "st_TRUTH"}
    report = reconcile_durable_ids(maps, store)
    assert not report.ok
    m = report.mismatches[0]
    assert "store_disagrees" in m["reasons"]
    assert m["store"] == "st_TRUTH"


def test_reconcile_absent_from_nonempty_store():
    # A stamped slug missing from a populated store is a fidelity gap.
    maps = {SURFACE_HIERARCHY: {"orphan": "st_x"}}
    store = {"a": "st_a", "b": "st_b"}
    report = reconcile_durable_ids(maps, store)
    assert not report.ok
    m = report.mismatches[0]
    assert m["slug"] == "orphan"
    assert "absent_from_store" in m["reasons"]
    assert m["store"] is None


def test_reconcile_empty_store_is_inert_not_a_wall_of_warnings():
    # Empty store + no stamps anywhere (gate off / pre-population): nothing to reconcile.
    maps = {SURFACE_HIERARCHY: {}, SURFACE_SPOTLIGHT: {}, SURFACE_TOPIC: {}}
    report = reconcile_durable_ids(maps, store_map={})
    assert report.ok
    assert report.slugs_checked == 0
    assert report.consistent == 0
    assert report.mismatches == []
    assert report.surfaces_present == []


def test_reconcile_no_store_skips_store_checks():
    # --no-store: only cross-artifact agreement matters; a slug on one surface is fine.
    maps = {SURFACE_HIERARCHY: {"a": "st_a"}, SURFACE_SPOTLIGHT: {"a": "st_a"}}
    report = reconcile_durable_ids(maps, store_map=None)
    assert report.ok
    assert report.consistent == 1
    assert report.store_compared is False
    assert report.store_size == 0


def test_reconcile_intra_artifact_conflict_makes_not_ok():
    maps = {SURFACE_SPOTLIGHT: {"a": "st_a"}}
    store = {"a": "st_a"}
    conflicts = {SURFACE_SPOTLIGHT: [{"subtopic_id": "a", "first": "st_a", "second": "st_b"}]}
    report = reconcile_durable_ids(maps, store, intra_conflicts=conflicts)
    # the slug itself reconciles, but the intra-artifact conflict still fails the run
    assert report.mismatches == []
    assert not report.ok
    assert report.to_dict()["intra_artifact_conflicts"] == conflicts


def test_reconcile_one_run_lag_partial_coverage_is_consistent():
    # b exists in the store but is not yet stamped on any surface (one-run lag) -> fine.
    maps = {
        SURFACE_HIERARCHY: {"a": "st_a"},
        SURFACE_SPOTLIGHT: {"a": "st_a"},
        SURFACE_TOPIC: {"a": "st_a"},
    }
    store = {"a": "st_a", "b": "st_b"}
    report = reconcile_durable_ids(maps, store)
    assert report.ok
    assert report.slugs_checked == 1
    assert report.consistent == 1


def test_to_dict_shape_and_ok_flag():
    maps = {SURFACE_HIERARCHY: {"a": "st_a"}, SURFACE_TOPIC: {"a": "st_bad"}}
    d = reconcile_durable_ids(maps, store_map=None).to_dict()
    assert d["ok"] is False
    assert d["mismatch_count"] == 1
    assert d["slugs_checked"] == 1
    assert set(d) == {
        "ok",
        "surfaces_present",
        "coverage",
        "slugs_checked",
        "consistent",
        "mismatch_count",
        "mismatches",
        "intra_artifact_conflicts",
        "store_compared",
        "store_size",
    }

"""Brick C (#191): the durable-id lineage overlay on compute_structural_diff.

Two invariants:
  - WITHOUT lineage the result is byte-identical to the pre-brick-C 4-key shape (the
    published diff.json calls compute_structural_diff without lineage until brick D
    resolves the store-derived lineage and wires it in), so no consumer-facing byte
    changes here.
  - WITH lineage, splits and merges surface as additive, distinct events while
    added/removed stay authoritative (pure enrichment overlay).
"""

from __future__ import annotations

from pipeline_hierarchy.diff_stats import (
    compute_structural_diff,
    derive_editorial_only,
)

_FOUR_KEYS = {
    "taxonomy_version_changed",
    "added_subtopics",
    "removed_subtopics",
    "renamed_subtopics",
}


def _h(taxonomy_version: str, subs: list[tuple[str, str]]) -> dict:
    """subs: list of (subtopic_id, display_name)."""
    return {
        "taxonomy_version": taxonomy_version,
        "topics": {"t": {"subtopics": [{"id": sid, "display_name": dn} for sid, dn in subs]}},
    }


# ---------- no lineage: byte-identical to the pre-brick-C shape ----------


def test_without_lineage_is_unchanged_four_key_shape():
    prev = _h("v1", [("a", "A"), ("b", "B")])
    new = _h("v1", [("a", "A"), ("c", "C")])
    out = compute_structural_diff(prev, new)
    assert set(out) == _FOUR_KEYS  # no split_subtopics / merged_subtopics keys
    assert out["added_subtopics"] == ["c"]
    assert out["removed_subtopics"] == ["b"]


def test_first_publish_without_lineage_is_unchanged():
    out = compute_structural_diff(None, _h("v1", [("a", "A")]))
    assert set(out) == _FOUR_KEYS


# ---------- with lineage: additive split/merge overlay ----------


def test_split_event_overlay_is_additive():
    prev = _h("v1", [("parent", "Parent")])
    new = _h("v1", [("parent", "Parent"), ("child", "Child")])
    lineage = {"split": [{"id": "child", "split_from": "parent"}], "merged": []}
    out = compute_structural_diff(prev, new, lineage=lineage)
    assert out["split_subtopics"] == [{"id": "child", "split_from": "parent"}]
    assert out["merged_subtopics"] == []
    # added stays authoritative — a 1.0.0-shaped reader is unaffected
    assert "child" in out["added_subtopics"]


def test_merge_event_overlay_is_additive():
    prev = _h("v1", [("keep", "Keep"), ("absorbed", "Absorbed")])
    new = _h("v1", [("keep", "Keep")])
    lineage = {"split": [], "merged": [{"id": "absorbed", "merged_into": "keep"}]}
    out = compute_structural_diff(prev, new, lineage=lineage)
    assert out["merged_subtopics"] == [{"id": "absorbed", "merged_into": "keep"}]
    assert "absorbed" in out["removed_subtopics"]


def test_first_publish_with_lineage_has_empty_event_lists():
    out = compute_structural_diff(None, _h("v1", [("a", "A")]), lineage={"split": [], "merged": []})
    assert out["split_subtopics"] == [] and out["merged_subtopics"] == []
    assert set(out) == _FOUR_KEYS | {"split_subtopics", "merged_subtopics"}


def test_lineage_missing_keys_default_to_empty():
    # a lineage dict that omits 'split'/'merged' must not raise
    out = compute_structural_diff(_h("v1", [("a", "A")]), _h("v1", [("a", "A")]), lineage={})
    assert out["split_subtopics"] == [] and out["merged_subtopics"] == []


# ---------- editorial_only must never swallow a split/merge ----------


def test_editorial_only_false_when_split_present():
    # adds/removes empty + a rename, but a split overlay present -> NOT editorial-only
    structural = {
        "taxonomy_version_changed": False,
        "added_subtopics": [],
        "removed_subtopics": [],
        "renamed_subtopics": [{"id": "x", "old_display_name": "A", "new_display_name": "B"}],
        "split_subtopics": [{"id": "c", "split_from": "p"}],
        "merged_subtopics": [],
    }
    assert derive_editorial_only(structural, 0) is False


def test_editorial_only_true_for_renames_only_without_lineage():
    out = compute_structural_diff(_h("v1", [("a", "Old")]), _h("v1", [("a", "New")]))
    assert derive_editorial_only(out, 0) is True

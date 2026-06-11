"""Phase 11 D-09 — hybrid diff-stats compute.

Two authorities:
  - STAGE# rows (filtered by run_id): "How many PMIDs got reassigned?"
  - Byte-comparison of prev vs new hierarchy.json: "What subtopics changed?"

Pure functions over inputs; caller fetches both halves and merges.
Used by the diff.json producer in pipeline_hierarchy/publish.py::compute_diff.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

# 1.1.0 (#191 brick D): additive split_subtopics / merged_subtopics keys. Minor bump —
# a 1.0.0 consumer ignores the new optional keys; added/removed stay authoritative.
DIFF_SCHEMA_VERSION = "1.1.0"


def compute_structural_diff(
    prev_hierarchy: Optional[dict],
    new_hierarchy: dict,
    *,
    lineage: Optional[dict] = None,
) -> dict:
    """Byte-comparison of prev vs new hierarchy structure.

    If prev_hierarchy is None (first-ever-publish per O-01), returns empty
    diffs and from_version remains None at the caller's discretion.

    Brick C (#191) — durable-id lineage overlay. ``lineage`` is the optional
    slug-space lineage resolved from the durable-id store:
        {"split":  [{"id": <child slug>,  "split_from":  <parent slug>}, ...],
         "merged": [{"id": <prior slug>,  "merged_into": <successor slug>}, ...]}
    When supplied, the result gains additive ``split_subtopics`` / ``merged_subtopics``
    keys so a re-cluster reports splits and merges as DISTINCT events instead of
    add/remove churn. ``added_subtopics`` / ``removed_subtopics`` stay authoritative
    (a split child is also in added; a merged prior is also in removed), so a reader
    of the original shape is unaffected — the overlay is pure enrichment.

    When ``lineage`` is None the result is byte-identical to the pre-brick-C 4-key
    shape (no new keys) — the path the unit tests exercise directly. Brick D's
    ``compute_diff`` now always passes a (possibly empty) store-derived overlay, so the
    published ``diff.json`` carries the split/merged keys at ``diff_schema_version``
    1.1.0; SPS consumes the additive keys (a 1.0.0 reader ignores them).
    """
    if prev_hierarchy is None:
        base = {
            "taxonomy_version_changed": False,
            "added_subtopics": [],
            "removed_subtopics": [],
            "renamed_subtopics": [],
        }
        return _overlay_lineage(base, lineage)

    prev_tv = prev_hierarchy.get("taxonomy_version")
    new_tv = new_hierarchy.get("taxonomy_version")
    taxonomy_changed = prev_tv != new_tv

    prev_subs = _index_subtopics(prev_hierarchy)
    new_subs = _index_subtopics(new_hierarchy)

    added = sorted(set(new_subs) - set(prev_subs))
    removed = sorted(set(prev_subs) - set(new_subs))
    renamed: list[dict] = []
    for sid in sorted(set(prev_subs) & set(new_subs)):
        prev_label = prev_subs[sid].get("display_name")
        new_label = new_subs[sid].get("display_name")
        if prev_label != new_label:
            renamed.append(
                {
                    "id": sid,
                    "old_display_name": prev_label,
                    "new_display_name": new_label,
                }
            )

    base = {
        "taxonomy_version_changed": taxonomy_changed,
        "added_subtopics": added,
        "removed_subtopics": removed,
        "renamed_subtopics": renamed,
    }
    return _overlay_lineage(base, lineage)


def _overlay_lineage(base: dict, lineage: Optional[dict]) -> dict:
    """Brick C: enrich the structural diff with split/merge events when durable-id
    lineage is supplied. Omits the new keys entirely when ``lineage`` is None (the
    pre-brick-C 4-key shape, still used by the unit tests); brick D's ``compute_diff``
    supplies a non-None overlay so the published ``diff.json`` carries them."""
    if lineage is None:
        return base
    return {
        **base,
        "split_subtopics": list(lineage.get("split", [])),
        "merged_subtopics": list(lineage.get("merged", [])),
    }


def _index_subtopics(hierarchy: dict) -> dict[str, dict]:
    """Build {subtopic_id: subtopic_dict} index from a hierarchy dict.

    Walks `topics[].subtopics[]`. Subtopic identifier key is `id` (per schema).
    """
    out: dict[str, dict] = {}
    for topic in hierarchy.get("topics", {}).values():
        for sub in topic.get("subtopics", []):
            sid = sub.get("id") or sub.get("subtopic_id")
            if sid:
                out[sid] = sub
    return out


def compute_reassigned_pmid_count(stage_rows: Iterable[dict]) -> int:
    """Sum `records_written` across the provided assign-stage STAGE# rows.

    Per D-18: rows-touched semantics, NOT primary-changed semantics. The
    field name `reassigned_pmid_count` is a verbatim representation of
    records_written from assign-stage rows filtered by run_id at the
    caller.
    """
    return sum(int(r.get("records_written", 0) or 0) for r in stage_rows)


def derive_editorial_only(structural: dict, reassigned_pmid_count: int) -> bool:
    """True iff only display-name renames occurred AND no PMID reassignment.

    Renames-only + no adds/removes + no taxonomy change + reassigned == 0.

    Brick C: a split/merge must never read as editorial-only. Under the enrichment
    overlay a split child is also in added_subtopics and a merged prior also in
    removed_subtopics, so the existing add/remove guards already force False; the
    ``.get()`` split/merged checks are defense-in-depth for any future caller that
    surfaces those events without the add/remove overlap.
    """
    return (
        not structural["taxonomy_version_changed"]
        and not structural["added_subtopics"]
        and not structural["removed_subtopics"]
        and not structural.get("split_subtopics")
        and not structural.get("merged_subtopics")
        and bool(structural["renamed_subtopics"])
        and reassigned_pmid_count == 0
    )

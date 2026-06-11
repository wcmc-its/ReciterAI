"""
Bundle per-topic `hierarchy_augmented_*.json` files into the canonical
`hierarchy_full.json` shape consumed by `pipeline_hierarchy.generator`.

Phase 5 produced today's `hierarchy_full.json` as a hand-stitched artifact
with no upstream regenerator. That meant subtopic edits (relabel runs,
re-augmentation, taxonomy_v3) never reached SPS through the publisher.
This module closes that gap: it reads the 65 per-topic augmented files,
strips them to schema-required fields, attaches the frozen
`excluded_topics` list from `config/excluded_topics.json`, and returns a
dict ready for `generator.build_hierarchy()`.

Strict mode (default) fails loudly when any subtopic is missing
`display_name` or `short_description` — the bundler will not silently
publish data that would trip SPS's hierarchy ETL.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUGMENTED_DIR = REPO_ROOT / ".planning/phases/04-subtopic-system"
DEFAULT_TAXONOMY_PATH = REPO_ROOT / "taxonomy_v2.json"
DEFAULT_EXCLUDED_PATH = REPO_ROOT / "config/excluded_topics.json"
DEFAULT_THRESHOLDS_PATH = REPO_ROOT / "config/thresholds.json"
DEFAULT_OUT_PATH = REPO_ROOT / "out/hierarchy_full.json"

# Fields copied from each augmented subtopic into the bundle.
_SUBTOPIC_REQUIRED = (
    "id",
    "label",
    "description",
    "display_name",
    "short_description",
    "activity_count",
    "total_weight",
)
_SUBTOPIC_UI_FIELDS = ("display_name", "short_description")

# Membership sidecar (#191). The bundle() output deliberately strips per-subtopic
# `seed_pmids` (SPS consumers don't need them), but the durable-ID reconcile stage
# and the taxonomy-jitter measurement both require membership persisted across runs.
# build_membership() recovers it into a co-located `membership.json` artifact.
MEMBERSHIP_ARTIFACT_VERSION = "membership_v1"
# The captured set is the discovery *seed* set, not the full assignment set.
# Stamped explicitly so downstream code never mistakes seed overlap for
# full-assignment overlap. See docs/subtopic-lifecycle-and-evolution.md.
MEMBERSHIP_KIND = "discovery_seed_pmids"


class MissingUIFieldsError(ValueError):
    """One or more subtopics lack display_name or short_description in strict mode."""


def _iter_augmented_files(augmented_dir: Path) -> list[Path]:
    return sorted(augmented_dir.glob("hierarchy_augmented_*.json"))


def _read_taxonomy_version(taxonomy_path: Path) -> str:
    with taxonomy_path.open() as f:
        data = json.load(f)
    tv = data.get("taxonomy_version")
    if not tv:
        raise ValueError(
            f"{taxonomy_path}: missing `taxonomy_version` (looked up to stamp "
            f"the bundled hierarchy)"
        )
    return tv


def _read_topic_display_thresholds(
    taxonomy_path: Path, thresholds_path: Path
) -> tuple[dict[str, float], float]:
    """Return ({topic_id: display_threshold}, global_default).

    Per #69: each topic in `taxonomy_v2.json` carries an optional
    `display_threshold`. Topics without an explicit value inherit the
    global `display_threshold_default` from `config/thresholds.json`.
    The returned dict carries only topics with an explicit, in-range
    per-topic value; callers apply the default to absent topics.
    """
    with taxonomy_path.open() as f:
        taxonomy = json.load(f)
    with thresholds_path.open() as f:
        thresholds = json.load(f)
    default = thresholds.get("display_threshold_default")
    if default is None:
        raise ValueError(
            f"{thresholds_path}: missing `display_threshold_default` (required for #69)"
        )
    default = float(default)
    if not 0.0 <= default <= 1.0:
        raise ValueError(
            f"{thresholds_path}: display_threshold_default={default} outside [0, 1]"
        )
    per_topic: dict[str, float] = {}
    for topic in taxonomy.get("topics", []):
        tid = topic.get("id")
        if not tid or "display_threshold" not in topic:
            continue
        v = float(topic["display_threshold"])
        if not 0.0 <= v <= 1.0:
            raise ValueError(
                f"{taxonomy_path}: topic {tid!r} display_threshold={v} outside [0, 1]"
            )
        per_topic[tid] = v
    return per_topic, default


def _read_excluded_topics(excluded_path: Path) -> list[dict[str, Any]]:
    with excluded_path.open() as f:
        data = json.load(f)
    entries = data.get("excluded_topics", [])
    out: list[dict[str, Any]] = []
    for entry in entries:
        out.append(
            {
                "id": entry["id"],
                "reason": entry["reason"],
                "activity_count": int(entry["activity_count"]),
            }
        )
    return out


def _build_subtopic(
    subtopic: dict[str, Any], topic_id: str, strict: bool, missing: list
) -> dict[str, Any] | None:
    """
    Build a schema-shaped subtopic dict from an augmented entry.

    On missing UI fields: in strict mode append to `missing` and return None;
    in non-strict mode emit the subtopic with empty strings (used by the
    structural-only tests, not by the publisher).
    """
    out: dict[str, Any] = {}
    missing_here: list[str] = []
    for field in _SUBTOPIC_REQUIRED:
        if field in subtopic and subtopic[field] not in (None, ""):
            out[field] = subtopic[field]
        elif field in _SUBTOPIC_UI_FIELDS:
            missing_here.append(field)
            out[field] = ""
        else:
            raise ValueError(
                f"{topic_id} :: {subtopic.get('id')!r}: missing required field "
                f"{field!r} (augmented files must carry this)"
            )
    if missing_here:
        missing.append(
            {
                "topic_id": topic_id,
                "subtopic_id": subtopic.get("id"),
                "missing_fields": missing_here,
            }
        )
        if strict:
            return None
    return out


def _build_topics(
    augmented_files: list[Path],
    strict: bool,
    *,
    display_thresholds: dict[str, float],
    display_threshold_default: float,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    topics: dict[str, dict[str, Any]] = {}
    missing: list[dict[str, Any]] = []
    for path in augmented_files:
        with path.open() as f:
            data = json.load(f)
        topic_id = data.get("topic_id")
        if not topic_id:
            raise ValueError(f"{path}: missing `topic_id`")
        if topic_id in topics:
            raise ValueError(
                f"duplicate topic_id {topic_id!r}: seen in {path} but already in bundle"
            )
        subs: list[dict[str, Any]] = []
        for s in data.get("subtopics", []):
            built = _build_subtopic(s, topic_id, strict, missing)
            if built is not None:
                subs.append(built)
        topics[topic_id] = {
            "subtopics": subs,
            "display_threshold": display_thresholds.get(
                topic_id, display_threshold_default
            ),
        }
    return topics, missing


def bundle(
    *,
    augmented_dir: Path = DEFAULT_AUGMENTED_DIR,
    taxonomy_path: Path = DEFAULT_TAXONOMY_PATH,
    excluded_topics_path: Path = DEFAULT_EXCLUDED_PATH,
    thresholds_path: Path = DEFAULT_THRESHOLDS_PATH,
    strict: bool = True,
) -> dict[str, Any]:
    """
    Bundle per-topic augmented files into a hierarchy dict.

    Args:
        augmented_dir: directory containing `hierarchy_augmented_*.json` files.
        taxonomy_path: source for the top-level `taxonomy_version` stamp.
        excluded_topics_path: frozen config listing topics excluded from publication.
        strict: when True (default), raise `MissingUIFieldsError` if any subtopic
            lacks display_name or short_description. Set False only for
            structural/development checks.

    Returns:
        The hierarchy dict, schema-compatible after generator.build_hierarchy()
        re-sorts topic keys.
    """
    files = _iter_augmented_files(augmented_dir)
    if not files:
        raise FileNotFoundError(
            f"no hierarchy_augmented_*.json files in {augmented_dir}"
        )
    taxonomy_version = _read_taxonomy_version(taxonomy_path)
    excluded = _read_excluded_topics(excluded_topics_path)
    display_thresholds, display_threshold_default = _read_topic_display_thresholds(
        taxonomy_path, thresholds_path
    )
    topics, missing = _build_topics(
        files,
        strict=strict,
        display_thresholds=display_thresholds,
        display_threshold_default=display_threshold_default,
    )

    if strict and missing:
        sample = ", ".join(
            f"{m['topic_id']}::{m['subtopic_id']}({'+'.join(m['missing_fields'])})"
            for m in missing[:5]
        )
        raise MissingUIFieldsError(
            f"{len(missing)} subtopic(s) missing display_name/short_description; "
            f"re-run relabel_subtopics.py before bundling. Sample: {sample}"
        )

    return {
        "version": "subtopic_v1",
        "taxonomy_version": taxonomy_version,
        "excluded_topics": excluded,
        "topics": topics,
        "see_also": [],
    }


def _index_seed_pmids(augmented_dir: Path) -> dict[str, list[int]]:
    """Map subtopic_id -> sorted unique seed PMIDs across the augmented files.

    Fails loud if the same subtopic id appears with conflicting seed sets in two
    files (mirrors bundle()'s duplicate-topic guard). A subtopic with absent or
    empty `seed_pmids` maps to an empty list.
    """
    index: dict[str, list[int]] = {}
    for path in _iter_augmented_files(augmented_dir):
        with path.open() as f:
            data = json.load(f)
        for s in data.get("subtopics", []):
            sid = s.get("id")
            if not sid:
                continue
            pmids = sorted({int(p) for p in (s.get("seed_pmids") or [])})
            if sid in index and index[sid] != pmids:
                raise ValueError(
                    f"subtopic id {sid!r} appears with conflicting seed_pmids "
                    f"across augmented files (cannot build membership)"
                )
            index[sid] = pmids
    return index


def build_membership(
    hierarchy: dict[str, Any],
    *,
    hierarchy_version: str,
    augmented_dir: Path = DEFAULT_AUGMENTED_DIR,
) -> dict[str, Any]:
    """Build the per-subtopic membership sidecar artifact (#191).

    The sidecar records, for every subtopic in the *published* hierarchy, the
    discovery `seed_pmids` that the bundler strips out of hierarchy.json. It is
    the data the durable-ID reconcile stage and the jitter measurement both need
    but which is otherwise discarded after a cold run.

    The subtopic id set is taken from `hierarchy` (the bundled dict), so the
    sidecar stays exactly 1:1 with the published artifact: a successful strict
    bundle never drops subtopics, so every published subtopic gets an entry.

    Args:
        hierarchy: the bundled hierarchy dict from bundle().
        hierarchy_version: the artifact version label (e.g. "v2026-06-10"),
            matching the co-located hierarchy.json's manifest version.
        augmented_dir: directory of hierarchy_augmented_*.json files (seed source).

    Returns:
        A deterministic, JSON-serializable dict. Membership is the discovery
        seed set, recorded in `membership_kind` (NOT the full assignment set).
    """
    seed_index = _index_seed_pmids(augmented_dir)
    subtopics: dict[str, dict[str, Any]] = {}
    for topic_id, tval in (hierarchy.get("topics") or {}).items():
        for s in tval.get("subtopics", []):
            sid = s.get("id")
            if not sid:
                continue
            subtopics[sid] = {
                "topic_id": topic_id,
                "seed_pmids": seed_index.get(sid, []),
            }
    return {
        "version": MEMBERSHIP_ARTIFACT_VERSION,
        "membership_kind": MEMBERSHIP_KIND,
        "taxonomy_version": hierarchy.get("taxonomy_version"),
        "hierarchy_version": hierarchy_version,
        "subtopic_count": len(subtopics),
        "subtopics": subtopics,
    }


def write_bundle(
    *,
    out_path: Path = DEFAULT_OUT_PATH,
    augmented_dir: Path = DEFAULT_AUGMENTED_DIR,
    taxonomy_path: Path = DEFAULT_TAXONOMY_PATH,
    excluded_topics_path: Path = DEFAULT_EXCLUDED_PATH,
    thresholds_path: Path = DEFAULT_THRESHOLDS_PATH,
) -> Path:
    """Run bundle() in strict mode and write the result to `out_path`."""
    hierarchy = bundle(
        augmented_dir=augmented_dir,
        taxonomy_path=taxonomy_path,
        excluded_topics_path=excluded_topics_path,
        thresholds_path=thresholds_path,
        strict=True,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(hierarchy, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return out_path

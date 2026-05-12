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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUGMENTED_DIR = REPO_ROOT / ".planning/phases/04-subtopic-system"
DEFAULT_TAXONOMY_PATH = REPO_ROOT / "taxonomy_v2.json"
DEFAULT_EXCLUDED_PATH = REPO_ROOT / "config/excluded_topics.json"
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
    augmented_files: list[Path], strict: bool
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
        topics[topic_id] = {"subtopics": subs}
    return topics, missing


def bundle(
    *,
    augmented_dir: Path = DEFAULT_AUGMENTED_DIR,
    taxonomy_path: Path = DEFAULT_TAXONOMY_PATH,
    excluded_topics_path: Path = DEFAULT_EXCLUDED_PATH,
    generated_at: str | None = None,
    strict: bool = True,
) -> dict[str, Any]:
    """
    Bundle per-topic augmented files into a hierarchy dict.

    Args:
        augmented_dir: directory containing `hierarchy_augmented_*.json` files.
        taxonomy_path: source for the top-level `taxonomy_version` stamp.
        excluded_topics_path: frozen config listing topics excluded from publication.
        generated_at: ISO8601 timestamp to stamp; defaults to now() in UTC.
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
    topics, missing = _build_topics(files, strict=strict)

    if strict and missing:
        sample = ", ".join(
            f"{m['topic_id']}::{m['subtopic_id']}({'+'.join(m['missing_fields'])})"
            for m in missing[:5]
        )
        raise MissingUIFieldsError(
            f"{len(missing)} subtopic(s) missing display_name/short_description; "
            f"re-run relabel_subtopics.py before bundling. Sample: {sample}"
        )

    if generated_at is None:
        generated_at = (
            datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        )

    return {
        "version": "subtopic_v1",
        "generated_at": generated_at,
        "taxonomy_version": taxonomy_version,
        "excluded_topics": excluded,
        "topics": topics,
        "see_also": [],
    }


def write_bundle(
    *,
    out_path: Path = DEFAULT_OUT_PATH,
    augmented_dir: Path = DEFAULT_AUGMENTED_DIR,
    taxonomy_path: Path = DEFAULT_TAXONOMY_PATH,
    excluded_topics_path: Path = DEFAULT_EXCLUDED_PATH,
    generated_at: str | None = None,
) -> Path:
    """Run bundle() in strict mode and write the result to `out_path`."""
    hierarchy = bundle(
        augmented_dir=augmented_dir,
        taxonomy_path=taxonomy_path,
        excluded_topics_path=excluded_topics_path,
        generated_at=generated_at,
        strict=True,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(hierarchy, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return out_path

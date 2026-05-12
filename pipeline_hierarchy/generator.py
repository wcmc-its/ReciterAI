"""
Canonical hierarchy artifact generator.

Reads the pre-bundled hierarchy from
`.planning/phases/04-subtopic-system/hierarchy_full.json`, re-stamps the
`generated_at` field, ensures `see_also` is present, sorts topic keys for
sha256 stability, validates against `docs/hierarchy.schema.json`, and
returns the canonical bytes + manifest dict.

Does not touch S3 — see publish.py for the upload side.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_HIERARCHY = REPO_ROOT / ".planning/phases/04-subtopic-system/hierarchy_full.json"
SCHEMA_PATH = REPO_ROOT / "docs/hierarchy.schema.json"


def _canonical_serialize(obj: dict[str, Any]) -> bytes:
    return json.dumps(obj, indent=2, ensure_ascii=False).encode("utf-8")


def _sort_topics(hierarchy: dict[str, Any]) -> dict[str, Any]:
    topics = hierarchy.get("topics", {})
    sorted_topics = {k: topics[k] for k in sorted(topics.keys())}
    return {**hierarchy, "topics": sorted_topics}


def build_hierarchy(
    *,
    source_path: Path = SOURCE_HIERARCHY,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """
    Load hierarchy_full.json, re-stamp generated_at, ensure see_also, sort topics.

    Args:
        source_path: path to the pre-bundled hierarchy_full.json.
        generated_at: ISO8601 timestamp to stamp; if None, uses now() in UTC.
                      Pinning this is the only way to get a reproducible sha256
                      across runs (used by the reproducibility test).

    Returns the in-memory hierarchy dict, ready for serialization.
    """
    raw = json.loads(source_path.read_text(encoding="utf-8"))
    if generated_at is None:
        generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    raw["generated_at"] = generated_at
    raw.setdefault("see_also", [])
    return _sort_topics(raw)


def validate(hierarchy: dict[str, Any], schema_path: Path = SCHEMA_PATH) -> None:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.validate(instance=hierarchy, schema=schema)


def generate(
    *,
    source_path: Path = SOURCE_HIERARCHY,
    schema_path: Path = SCHEMA_PATH,
    version: str | None = None,
    generated_at: str | None = None,
) -> tuple[bytes, bytes, dict[str, Any]]:
    """
    Produce the three artifact components (hierarchy bytes, schema bytes, manifest).

    Args:
        version: artifact version label (e.g. "v2026-05-11"); defaults to
                 "v{ISO-date}" based on the resolved generated_at.
        generated_at: see build_hierarchy().

    Returns:
        (hierarchy_bytes, schema_bytes, manifest_dict)
    """
    hierarchy = build_hierarchy(source_path=source_path, generated_at=generated_at)
    validate(hierarchy, schema_path=schema_path)

    hierarchy_bytes = _canonical_serialize(hierarchy)
    schema_bytes = schema_path.read_bytes()
    sha256 = hashlib.sha256(hierarchy_bytes).hexdigest()

    resolved_generated_at = hierarchy["generated_at"]
    if version is None:
        version = f"v{resolved_generated_at[:10]}"

    manifest = {
        "schema_version": "1.0.0",
        "taxonomy_version": hierarchy["taxonomy_version"],
        "version": version,
        "generated_at": resolved_generated_at,
        "sha256": sha256,
        "artifact_bytes": len(hierarchy_bytes),
    }
    return hierarchy_bytes, schema_bytes, manifest

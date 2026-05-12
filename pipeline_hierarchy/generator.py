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
    hierarchy: dict[str, Any] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """
    Build the canonical in-memory hierarchy: re-stamp generated_at, ensure
    see_also, sort topic keys.

    Args:
        source_path: path to a pre-bundled hierarchy_full.json. Ignored when
                     `hierarchy` is provided.
        hierarchy: in-memory hierarchy dict (e.g. from
                   `pipeline_hierarchy.bundler.bundle`). Preferred call shape now
                   that the bundler regenerates this on every publish.
        generated_at: ISO8601 timestamp to stamp; if None, uses now() in UTC.
                      Pinning this is the only way to get a reproducible sha256
                      across runs (used by the reproducibility test).

    Returns the in-memory hierarchy dict, ready for serialization.
    """
    if hierarchy is None:
        hierarchy = json.loads(source_path.read_text(encoding="utf-8"))
    else:
        hierarchy = dict(hierarchy)  # shallow copy so re-stamping doesn't mutate caller
    if generated_at is None:
        generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    hierarchy["generated_at"] = generated_at
    hierarchy.setdefault("see_also", [])
    return _sort_topics(hierarchy)


def validate(hierarchy: dict[str, Any], schema_path: Path = SCHEMA_PATH) -> None:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.validate(instance=hierarchy, schema=schema)


def generate(
    *,
    source_path: Path = SOURCE_HIERARCHY,
    hierarchy: dict[str, Any] | None = None,
    schema_path: Path = SCHEMA_PATH,
    version: str | None = None,
    generated_at: str | None = None,
) -> tuple[bytes, bytes, dict[str, Any]]:
    """
    Produce the three artifact components (hierarchy bytes, schema bytes, manifest).

    Args:
        source_path: pre-bundled file to read when `hierarchy` is not provided.
        hierarchy: in-memory hierarchy dict (e.g. from
                   `pipeline_hierarchy.bundler.bundle`). When set, `source_path`
                   is ignored.
        version: artifact version label (e.g. "v2026-05-11"); defaults to
                 "v{ISO-date}" based on the resolved generated_at.
        generated_at: see build_hierarchy().

    Returns:
        (hierarchy_bytes, schema_bytes, manifest_dict)
    """
    hierarchy = build_hierarchy(
        source_path=source_path,
        hierarchy=hierarchy,
        generated_at=generated_at,
    )
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

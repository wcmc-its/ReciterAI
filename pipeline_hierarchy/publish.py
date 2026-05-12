"""
Hierarchy artifact publisher.

Generates the canonical artifact via pipeline_hierarchy.generator, writes
it to out/hierarchy/<version>/ locally, and uploads to S3 at
s3://wcmc-reciterai-hierarchy/<version>/{hierarchy.json,hierarchy.schema.json}
plus s3://wcmc-reciterai-hierarchy/latest/manifest.json.

Upload order matters: version-pinned objects FIRST, then latest/manifest.json
LAST, so a concurrent SPS ETL run never reads a manifest pointing at objects
that haven't landed yet (D-02 invariant in docs/hierarchy-contract.md).

Usage:
    python -m pipeline_hierarchy.publish [--dry-run] [--version LABEL]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pipeline_hierarchy.bundler import MissingUIFieldsError, bundle
from pipeline_hierarchy.generator import REPO_ROOT, generate
from utils.s3_client import S3HierarchyClient


def write_local(out_dir: Path, hierarchy: bytes, schema: bytes, manifest: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "hierarchy.json").write_bytes(hierarchy)
    (out_dir / "hierarchy.schema.json").write_bytes(schema)
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def upload_to_s3(version: str, hierarchy: bytes, schema: bytes, manifest: dict) -> None:
    s3 = S3HierarchyClient()
    # Version-pinned objects FIRST.
    s3.put_object(f"{version}/hierarchy.json", hierarchy)
    s3.put_object(f"{version}/hierarchy.schema.json", schema)
    # latest/manifest.json LAST.
    manifest_bytes = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
    s3.put_object("latest/manifest.json", manifest_bytes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pipeline_hierarchy.publish")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate locally and validate, but skip S3 upload.",
    )
    parser.add_argument(
        "--version",
        default=None,
        help="Artifact version label (default: v{ISO-date} from generated_at).",
    )
    parser.add_argument(
        "--no-rebuild",
        action="store_true",
        help=(
            "Skip the per-topic bundler and read the checked-in "
            "hierarchy_full.json directly. Escape hatch for debugging only — "
            "the default rebuilds so a stale bundle can never reach SPS."
        ),
    )
    args = parser.parse_args(argv)

    if args.no_rebuild:
        print("[publish] --no-rebuild set; reading checked-in hierarchy_full.json")
        hierarchy_bytes, schema_bytes, manifest = generate(version=args.version)
    else:
        try:
            hierarchy_dict = bundle()
        except MissingUIFieldsError as exc:
            print(f"[publish] bundler refused to ship stale data: {exc}", file=sys.stderr)
            return 2
        hierarchy_bytes, schema_bytes, manifest = generate(
            hierarchy=hierarchy_dict, version=args.version
        )
    version = manifest["version"]

    out_dir = REPO_ROOT / "out" / "hierarchy" / version
    write_local(out_dir, hierarchy_bytes, schema_bytes, manifest)

    summary = {
        "event": "artifact_written",
        "out_dir": str(out_dir),
        "version": version,
        "taxonomy_version": manifest["taxonomy_version"],
        "sha256_prefix": manifest["sha256"][:12],
        "artifact_bytes": manifest["artifact_bytes"],
    }
    print(json.dumps(summary, indent=2))

    if args.dry_run:
        print("[dry-run] skipping S3 upload")
        return 0

    upload_to_s3(version, hierarchy_bytes, schema_bytes, manifest)
    print(json.dumps(
        {
            "event": "upload_complete",
            "bucket": "wcmc-reciterai-hierarchy",
            "version": version,
            "keys": [
                f"{version}/hierarchy.json",
                f"{version}/hierarchy.schema.json",
                "latest/manifest.json",
            ],
        },
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())

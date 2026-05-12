"""
Hierarchy artifact publisher.

Generates the canonical artifact via pipeline_hierarchy.generator, writes
it to out/hierarchy/<version>/ locally, and uploads to S3 at
s3://wcmc-reciterai-hierarchy/<version>/{hierarchy.json,hierarchy.schema.json}
plus s3://wcmc-reciterai-hierarchy/latest/manifest.json.

Upload order matters: version-pinned objects FIRST, then latest/manifest.json
LAST, so a concurrent SPS ETL run never reads a manifest pointing at objects
that haven't landed yet (D-02 invariant in docs/hierarchy-contract.md).

Phase 9 substrate integration:
- Computes a content-addressed `input_hash` from a generated_at-free
  representation of the bundled hierarchy (spec G-29). A prior `complete`
  STAGE# row with the same hash short-circuits the run — emitting a
  `skipped` row that still carries duration_ms and the pinned skip cost.
- Runs registered `publish`-stage gates pre-upload (schema_validation,
  parent_prefix, pii_scan — all `block` severity). Any failure writes
  a `failed` STAGE# row and exits non-zero, unless `--force --force-reason "..."`
  is set.
- Runs `publish_post`-stage gates post-upload (schema_roundtrip, `warn`
  severity). Failures here are surfaced in the STAGE# row but do not
  fail the publish — by the time they run, bytes are live.

Usage:
    python -m pipeline_hierarchy.publish [--dry-run] [--version LABEL]
                                          [--force --force-reason "..."]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from decimal import Decimal
from pathlib import Path

# Import gate modules so their @register_gate decorators run at import time.
import gates.parent_prefix       # noqa: F401  (registers parent_prefix gate)
import gates.pii                 # noqa: F401  (registers pii_scan gate)
import gates.schema_roundtrip    # noqa: F401  (registers schema_roundtrip gate)
import gates.schema_validation   # noqa: F401  (registers schema_validation gate)
import pipeline_hierarchy
from gates.registry import any_blocked, run_gates
from pipeline_hierarchy.bundler import (
    DEFAULT_EXCLUDED_PATH,
    MissingUIFieldsError,
    bundle,
)
from pipeline_hierarchy.generator import REPO_ROOT, SCHEMA_PATH, generate
from utils.bedrock_client import MODEL_IDS_BY_STAGE
from utils.dynamodb_helpers import get_table
from utils.s3_client import S3HierarchyClient
from utils.stage_records import (
    compute_input_hash,
    should_skip,
    write_complete,
    write_failed,
    write_skipped,
)

STAGE_NAME = "publish_hierarchy"
STAGE_SCOPE = "GLOBAL"
PUBLISH_COST_USD = Decimal("0")  # the publish stage makes no Bedrock calls


# ---------- pure helpers ----------


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _sha256_path(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def compute_publish_input_hash(
    hierarchy_dict: dict,
    *,
    schema_path: Path = SCHEMA_PATH,
    excluded_topics_path: Path = DEFAULT_EXCLUDED_PATH,
) -> str:
    """Compute the publish stage's input_hash.

    `hierarchy_dict` is hashed *without* its `generated_at` field — that
    re-stamps on every run and would otherwise make the hash unstable
    across content-identical publishes (spec G-29). Phase 11 may move
    `generated_at` out of the hierarchy bytes entirely; until then this
    is the local mitigation.

    Model IDs are included even though publish itself doesn't invoke
    models, so any model swap anywhere in the pipeline invalidates
    skip cache uniformly.

    Bundler version (`pipeline_hierarchy.__version__`) is included so a
    code change to the bundler invalidates the skip cache without
    relying on file-mtime heuristics.
    """
    h_for_hash = {k: v for k, v in hierarchy_dict.items() if k != "generated_at"}
    h_canonical = json.dumps(
        h_for_hash, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return compute_input_hash(
        STAGE_NAME,
        {
            "hierarchy_canonical_sha256_no_generated_at": _sha256_bytes(h_canonical),
            "schema_sha256": _sha256_path(schema_path),
            "excluded_topics_sha256": _sha256_path(excluded_topics_path),
            "model_ids": sorted(set(MODEL_IDS_BY_STAGE.values())),
            "bundler_version": pipeline_hierarchy.__version__,
        },
    )


# ---------- I/O helpers (unchanged in shape) ----------


def write_local(out_dir: Path, hierarchy: bytes, schema: bytes, manifest: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "hierarchy.json").write_bytes(hierarchy)
    (out_dir / "hierarchy.schema.json").write_bytes(schema)
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def upload_to_s3(version: str, hierarchy: bytes, schema: bytes, manifest: dict) -> None:
    s3 = S3HierarchyClient()
    # Version-pinned objects FIRST (D-02).
    s3.put_object(f"{version}/hierarchy.json", hierarchy)
    s3.put_object(f"{version}/hierarchy.schema.json", schema)
    # latest/manifest.json LAST.
    manifest_bytes = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
    s3.put_object("latest/manifest.json", manifest_bytes)


# ---------- exit codes ----------

EXIT_OK = 0
EXIT_BUNDLER_MISSING_FIELDS = 2
EXIT_GATE_BLOCKED = 3
EXIT_FORCE_WITHOUT_REASON = 4


# ---------- main flow ----------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pipeline_hierarchy.publish")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Generate locally and validate, but skip S3 upload AND skip "
            "all STAGE# writes. Useful for inspecting bundler output."
        ),
    )
    parser.add_argument(
        "--version",
        default=None,
        help="Artifact version label (default: v{ISO-date} from generated_at).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Override block-severity gate failures. REQUIRES --force-reason. "
            "The reason is persisted into the STAGE# row's force_reason field."
        ),
    )
    parser.add_argument(
        "--force-reason",
        default=None,
        help="Required when --force is set; a one-line audit note.",
    )
    args = parser.parse_args(argv)

    if args.force and not args.force_reason:
        print(
            "[publish] --force requires --force-reason '<audit note>'",
            file=sys.stderr,
        )
        return EXIT_FORCE_WITHOUT_REASON

    started_at = _now_iso()
    t_start = time.monotonic()

    # 1. Bundle augmented files into the in-memory hierarchy.
    try:
        hierarchy_dict = bundle()
    except MissingUIFieldsError as exc:
        print(f"[publish] bundler refused to ship stale data: {exc}", file=sys.stderr)
        return EXIT_BUNDLER_MISSING_FIELDS

    # 2. Compute input_hash and (unless --dry-run) check skip cache.
    input_hash = compute_publish_input_hash(hierarchy_dict)
    table = None
    if not args.dry_run:
        table = get_table()
        skip, prior = should_skip(
            table,
            stage=STAGE_NAME,
            scope=STAGE_SCOPE,
            input_hash=input_hash,
        )
        if skip:
            completed_at = _now_iso()
            duration_ms = int((time.monotonic() - t_start) * 1000)
            write_skipped(
                table,
                stage=STAGE_NAME,
                scope=STAGE_SCOPE,
                input_hash=input_hash,
                skip_reason=(
                    f"input_hash unchanged since prior complete run at "
                    f"{prior.get('started_at', '?')}"
                ),
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
                model_ids_snapshot=sorted(set(MODEL_IDS_BY_STAGE.values())),
            )
            print(json.dumps(
                {
                    "event": "publish_skipped",
                    "input_hash_prefix": input_hash[:12],
                    "prior_output_pointer": prior.get("output_pointer"),
                    "prior_started_at": prior.get("started_at"),
                    "duration_ms": duration_ms,
                },
                indent=2,
            ))
            return EXIT_OK

    # 3. Run pre-upload gates against the bundled hierarchy.
    gate_results = run_gates(stage="publish", hierarchy=hierarchy_dict)
    blocked = any_blocked(gate_results)

    if blocked and not args.force:
        completed_at = _now_iso()
        duration_ms = int((time.monotonic() - t_start) * 1000)
        failing = [r for r in gate_results if r.blocked]
        failure_details = {
            "failing_gates": [
                {"name": r.name, "summary": r.summary, "details": r.details}
                for r in failing
            ],
        }
        if not args.dry_run:
            write_failed(
                table,
                stage=STAGE_NAME,
                scope=STAGE_SCOPE,
                input_hash=input_hash,
                error_code=f"GATE_BLOCK_{','.join(r.name for r in failing)}",
                error_message=f"{len(failing)} block-severity gate(s) failed",
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
                cost_observed_usd=PUBLISH_COST_USD,
                failure_details=failure_details,
                model_ids_snapshot=sorted(set(MODEL_IDS_BY_STAGE.values())),
            )
        print(json.dumps(
            {
                "event": "publish_blocked_by_gates",
                "failing_gates": [r.name for r in failing],
                "summaries": [r.summary for r in failing],
            },
            indent=2,
        ), file=sys.stderr)
        return EXIT_GATE_BLOCKED

    # 4. Generate canonical bytes + manifest.
    hierarchy_bytes, schema_bytes, manifest = generate(
        hierarchy=hierarchy_dict, version=args.version
    )
    version = manifest["version"]

    out_dir = REPO_ROOT / "out" / "hierarchy" / version
    write_local(out_dir, hierarchy_bytes, schema_bytes, manifest)

    print(json.dumps(
        {
            "event": "artifact_written",
            "out_dir": str(out_dir),
            "version": version,
            "taxonomy_version": manifest["taxonomy_version"],
            "sha256_prefix": manifest["sha256"][:12],
            "artifact_bytes": manifest["artifact_bytes"],
            "input_hash_prefix": input_hash[:12],
            "gate_summaries": [
                {"name": r.name, "passed": r.passed, "severity": r.severity}
                for r in gate_results
            ],
        },
        indent=2,
    ))

    if args.dry_run:
        print("[dry-run] skipping S3 upload AND STAGE# write")
        return EXIT_OK

    # 5. S3 upload.
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

    # 6. Run post-upload (warn) gates against the just-published artifact.
    post_results = run_gates(
        stage="publish_post",
        version=version,
        s3_client=S3HierarchyClient(),
    )
    post_warnings = [r for r in post_results if not r.passed]
    if post_warnings:
        print(json.dumps(
            {
                "event": "publish_post_warnings",
                "warnings": [
                    {"name": r.name, "summary": r.summary}
                    for r in post_warnings
                ],
            },
            indent=2,
        ), file=sys.stderr)

    # 7. Write STAGE# complete row.
    completed_at = _now_iso()
    duration_ms = int((time.monotonic() - t_start) * 1000)
    write_complete(
        table,
        stage=STAGE_NAME,
        scope=STAGE_SCOPE,
        input_hash=input_hash,
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=duration_ms,
        cost_observed_usd=PUBLISH_COST_USD,
        output_pointer=f"s3://wcmc-reciterai-hierarchy/{version}/",
        records_written=manifest["artifact_bytes"],
        model_ids_snapshot=sorted(set(MODEL_IDS_BY_STAGE.values())),
        force_reason=args.force_reason if (blocked and args.force) else None,
    )

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

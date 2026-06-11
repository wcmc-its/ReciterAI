"""
Hierarchy artifact publisher.

Generates the canonical artifact via pipeline_hierarchy.generator, writes
it to out/hierarchy/<version>/ locally, and uploads to S3 at
s3://wcmc-reciterai-hierarchy/<version>/{hierarchy.json,hierarchy.schema.json,
diff.json,manifest.json} plus s3://wcmc-reciterai-hierarchy/latest/manifest.json.

Phase 11 D-11 upload order (5 PutObjects):
    1. {version}/hierarchy.json
    2. {version}/hierarchy.schema.json
    3. {version}/diff.json         ← new; before manifest so consumers can GET diff
    4. {version}/manifest.json     ← new; version-pinned copy was previously missing
    5. latest/manifest.json        ← with Cache-Control: max-age=60, must-revalidate

Phase 9 substrate integration:
- Computes a content-addressed `input_hash` from a generated_at-free
  representation of the bundled hierarchy (spec G-29 — now fixed by D-14). A prior
  `complete` STAGE# row with the same hash short-circuits the run — emitting a
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
                                          [--g29-cutover]
                                          [--run-id <cold-run-id>]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Optional

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
    build_membership,
    bundle,
)
from pipeline_hierarchy.diff_stats import (
    DIFF_SCHEMA_VERSION,
    compute_reassigned_pmid_count,
    compute_structural_diff,
    derive_editorial_only,
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
from utils.iso_clock import now_iso

STAGE_NAME = "publish_hierarchy"
STAGE_SCOPE = "GLOBAL"
PUBLISH_COST_USD = Decimal("0")  # the publish stage makes no Bedrock calls


# ---------- pure helpers ----------




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

    `hierarchy_dict` is hashed directly — Phase 11 D-14 removed `generated_at`
    from the hierarchy dict entirely (G-29 fix). The field no longer appears
    in bundle() output so no filtering is required; the hash is stable across
    content-identical publishes by construction.

    Model IDs are included even though publish itself doesn't invoke
    models, so any model swap anywhere in the pipeline invalidates
    skip cache uniformly.

    Bundler version (`pipeline_hierarchy.__version__`) is included so a
    code change to the bundler invalidates the skip cache without
    relying on file-mtime heuristics.
    """
    # G-29 fixed upstream per D-14; no embedded timestamp remains in the hierarchy dict.
    # The hierarchy dict from bundle() is already free of generated_at. Computing the
    # hash directly without filtering is now equivalent but we keep a clean canonical form.
    h_canonical = json.dumps(
        hierarchy_dict, sort_keys=True, separators=(",", ":"), ensure_ascii=False
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


# ---------- diff.json producer (Phase 11 D-09, D-10, D-12, D-13, D-18) ----------


def _scan_assign_rows_by_run_id(table: object, run_id: str) -> list[dict]:
    """Fetch assign STAGE# rows and filter by run_id in Python.

    Phase 9 convention: Python-side filtering is acceptable at Phase 11 scale.
    A GSI on run_id is deferred per Phase 9 plan (D-13 note).

    Uses table.query on each known assign-subtopics PK prefix pattern, then
    filters in Python by run_id. Alternatively, scans all STAGE# rows and
    filters. Either way, run_id matching happens in Python.

    For testability, this method calls table.query (consistent with the rest
    of the stage_records pattern). The mock in tests sets query.return_value.
    """
    # Query the table for all assign-subtopics rows.
    # Since DynamoDB requires exact PK match in query, we scan and filter.
    # In production, rows per cold run are bounded (one per topic ~65 rows).
    resp = table.query(
        KeyConditionExpression="#pk = :pk",
        ExpressionAttributeNames={"#pk": "PK"},
        ExpressionAttributeValues={":pk": "STAGE#assign_subtopics#GLOBAL"},
    )
    all_rows = resp.get("Items", [])
    # Python-side filter by run_id (D-13)
    return [row for row in all_rows if row.get("run_id") == run_id]


def compute_diff(
    *,
    prev_version: Optional[str],
    new_hierarchy: dict,
    to_version: str,
    run_id: Optional[str],
    table: object,
    s3_client: object,
) -> dict:
    """Phase 11 D-09, D-10, D-12, D-13, D-18.

    Produces the diff.json dict. Hybrid:
      - Structural: byte-compare prev vs new hierarchy.json (S3 GET via W1).
      - PMID-count: query STAGE# assign rows filtered by run_id.

    First-ever-publish (O-01): prev_version is None → from_version: null
    in output; structural diffs are empty (no prev to compare).

    W1: Reuses the existing S3HierarchyClient.get_object_bytes(key) method
    (utils/s3_client.py:132). No new S3 GET accessor introduced.
    """
    # 1. Fetch prev hierarchy via existing get_object_bytes (W1).
    prev_hierarchy: Optional[dict] = None
    if prev_version is not None:
        try:
            body = s3_client.get_object_bytes(f"{prev_version}/hierarchy.json")
            prev_hierarchy = json.loads(body)
        except Exception as exc:
            # Treat as first-ever-publish per O-01 (explicit null signal)
            import logging
            logging.getLogger(__name__).warning(
                f"prev hierarchy GET failed for {prev_version}: {exc}; "
                "treating as first-publish (from_version: null)"
            )
            prev_hierarchy = None

    # 2. Structural diff (taxonomy, added/removed/renamed subtopics)
    structural = compute_structural_diff(prev_hierarchy, new_hierarchy)

    # 3. STAGE# query for reassignment count (D-13: filtered by run_id)
    if run_id is not None:
        stage_rows = _scan_assign_rows_by_run_id(table, run_id)
    else:
        stage_rows = []
    reassigned = compute_reassigned_pmid_count(stage_rows)

    # 4. Assemble diff.json
    return {
        "diff_schema_version": DIFF_SCHEMA_VERSION,
        "from_version": prev_version,   # None → JSON null per O-01
        "to_version": to_version,
        "taxonomy_version_changed": structural["taxonomy_version_changed"],
        "added_subtopics": structural["added_subtopics"],
        "removed_subtopics": structural["removed_subtopics"],
        "renamed_subtopics": structural["renamed_subtopics"],
        "reassigned_pmid_count": reassigned,
        "editorial_only": derive_editorial_only(structural, reassigned),
    }


# ---------- I/O helpers ----------


def write_local(
    out_dir: Path,
    hierarchy: bytes,
    schema: bytes,
    manifest: dict,
    membership: bytes | None = None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "hierarchy.json").write_bytes(hierarchy)
    (out_dir / "hierarchy.schema.json").write_bytes(schema)
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    if membership is not None:
        (out_dir / "membership.json").write_bytes(membership)


def upload_to_s3(
    version: str,
    hierarchy: bytes,
    schema: bytes,
    manifest: dict,
    diff_bytes: bytes,
    s3_client: Optional[object] = None,
    membership_bytes: Optional[bytes] = None,
) -> None:
    """Phase 11 D-11: 5-step PutObject sequence (+ optional membership sidecar).

    Order matters for consumer safety:
    0. {version}/membership.json    — #191 sidecar; consumer-irrelevant, so it is
                                      uploaded FIRST (present before anything keys
                                      off the manifest). Omitted when None.
    1. {version}/hierarchy.json     — the artifact itself
    2. {version}/hierarchy.schema.json — validator for the artifact
    3. {version}/diff.json          — BEFORE manifest so consumers polling
                                      manifest.sha256 can immediately GET diff
                                      without eventually-consistent race
    4. {version}/manifest.json      — version-pinned copy (was missing pre-Phase-11)
    5. latest/manifest.json         — last, with Cache-Control header
    """
    s3 = s3_client if s3_client is not None else S3HierarchyClient()
    manifest_bytes = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")

    # 0. {version}/membership.json — sidecar, before the consumer-facing artifact.
    if membership_bytes is not None:
        s3.put_object(f"{version}/membership.json", membership_bytes)
    # 1. {version}/hierarchy.json
    s3.put_object(f"{version}/hierarchy.json", hierarchy)
    # 2. {version}/hierarchy.schema.json
    s3.put_object(f"{version}/hierarchy.schema.json", schema)
    # 3. {version}/diff.json — before manifest (D-11 race guard)
    s3.put_object(f"{version}/diff.json", diff_bytes)
    # 4. {version}/manifest.json — version-pinned copy (Phase 11: was missing)
    s3.put_object(f"{version}/manifest.json", manifest_bytes)
    # 5. latest/manifest.json — LAST, with Cache-Control
    s3.put_object(
        "latest/manifest.json",
        manifest_bytes,
        cache_control="max-age=60, must-revalidate",
    )


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
    parser.add_argument(
        "--g29-cutover",
        action="store_true",
        help=(
            "One-time: write STAGE#g29_cutover#GLOBAL audit row recording the "
            "previous_publish_sha, new_publish_sha, hierarchy_version_at_cutover, "
            "run_id, started_at, completed_at. Idempotent — writes one row per run. "
            "D-16 / D-15 operator coordination: schedule this with SPS team."
        ),
    )
    parser.add_argument(
        "--run-id",
        default=os.environ.get("RECITERAI_COLD_RUN_ID"),
        help=(
            "Cold-run identifier threaded into STAGE# rows and diff.json "
            "for run-scoped correlation. Defaults to RECITERAI_COLD_RUN_ID env var. "
            "D-13: used by diff producer to filter assign-stage STAGE# rows."
        ),
    )
    args = parser.parse_args(argv)

    if args.force and not args.force_reason:
        print(
            "[publish] --force requires --force-reason '<audit note>'",
            file=sys.stderr,
        )
        return EXIT_FORCE_WITHOUT_REASON

    run_id: Optional[str] = args.run_id

    started_at = now_iso()
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
            completed_at = now_iso()
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
        completed_at = now_iso()
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

    # 4b. Build the per-subtopic membership sidecar (#191). Deterministic
    # (sort_keys + sorted seed lists) so it is byte-stable across content-identical
    # reruns, like hierarchy.json. Consumer-irrelevant; recovers the seed_pmids the
    # bundler strips so the durable-ID reconcile stage and jitter measurement have
    # the data they need.
    membership = build_membership(hierarchy_dict, hierarchy_version=version)
    membership_bytes = (
        json.dumps(membership, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")

    out_dir = REPO_ROOT / "out" / "hierarchy" / version
    write_local(out_dir, hierarchy_bytes, schema_bytes, manifest, membership_bytes)

    print(json.dumps(
        {
            "event": "artifact_written",
            "out_dir": str(out_dir),
            "version": version,
            "taxonomy_version": manifest["taxonomy_version"],
            "sha256_prefix": manifest["sha256"][:12],
            "artifact_bytes": manifest["artifact_bytes"],
            "membership_subtopics": membership["subtopic_count"],
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

    # 5. Compute diff.json (Phase 11 D-09, D-10).
    s3 = S3HierarchyClient()

    # Resolve prev_version from latest/manifest.json (W1: reuse get_object_bytes).
    prev_version: Optional[str] = None
    try:
        from botocore.exceptions import ClientError
        prev_manifest_bytes = s3.get_object_bytes("latest/manifest.json")
        prev_manifest = json.loads(prev_manifest_bytes)
        prev_version = prev_manifest.get("version")
    except Exception:
        prev_version = None  # first-ever-publish (O-01)

    diff = compute_diff(
        prev_version=prev_version,
        new_hierarchy=hierarchy_dict,
        to_version=version,
        run_id=run_id,
        table=table,
        s3_client=s3,
    )
    diff_bytes = (json.dumps(diff, indent=2) + "\n").encode("utf-8")

    # 6. S3 upload (5-step D-11 order + #191 membership sidecar).
    upload_to_s3(
        version,
        hierarchy_bytes,
        schema_bytes,
        manifest,
        diff_bytes,
        s3_client=s3,
        membership_bytes=membership_bytes,
    )
    print(json.dumps(
        {
            "event": "upload_complete",
            "bucket": "wcmc-reciterai-hierarchy",
            "version": version,
            "keys": [
                f"{version}/membership.json",
                f"{version}/hierarchy.json",
                f"{version}/hierarchy.schema.json",
                f"{version}/diff.json",
                f"{version}/manifest.json",
                "latest/manifest.json",
            ],
        },
        indent=2,
    ))

    # 7. Run post-upload (warn) gates against the just-published artifact.
    post_results = run_gates(
        stage="publish_post",
        version=version,
        s3_client=s3,
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

    # 8. Write STAGE#g29_cutover audit row if requested (D-16).
    if args.g29_cutover:
        previous_publish_sha: Optional[str] = None
        try:
            previous_publish_sha = prev_manifest.get("sha256") if prev_version else None
        except Exception:
            previous_publish_sha = None

        cutover_item = {
            "PK": "STAGE#g29_cutover#GLOBAL",
            "SK": f"RUN#{started_at}",
            "stage": "g29_cutover",
            "scope": "GLOBAL",
            "status": "complete",
            "previous_publish_sha": previous_publish_sha,
            "new_publish_sha": manifest["sha256"],
            "hierarchy_version_at_cutover": version,
            "run_id": run_id,
            "started_at": started_at,
            "completed_at": now_iso(),
        }
        table.put_item(Item=cutover_item)

    # 9. Write STAGE# complete row (D-13: include run_id).
    completed_at = now_iso()
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
        run_id=run_id,
    )

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

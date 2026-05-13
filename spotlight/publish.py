"""S3 publish path for spotlight artifact.

Mirrors backfill_all.py:_run_publish line-for-line; Phase 6 deltas:
bucket=ARTIFACTS_BUCKET, prefix=spotlight/, no PM worktree copy,
post-publish history writeback. Implements SPOT-11 + SPOT-12.

Behavioral contract (Plan 06-07):
  1. Schema validation precedes any S3 PutObject (D-16 fail-fast). Errors
     short-circuit with exit code 1; zero side effects on the bucket.
  2. Six PutObjects in this exact order:
       spotlight/v{date}/spotlight.json
       spotlight/v{date}/spotlight.schema.json
       spotlight/v{date}/manifest.json
       spotlight/latest/spotlight.json
       spotlight/latest/spotlight.schema.json
       spotlight/latest/manifest.json
  3. manifest.json has 7 keys in LOCKED insertion order:
       schema_version, spotlight_version, taxonomy_version, version,
       generated_at, sha256, artifact_bytes
     The Phase 6 addition (relative to Phase 5) is `spotlight_version`,
     slotted between `schema_version` and `taxonomy_version`.
  4. manifest.sha256 is computed over the in-memory json.dumps bytes that
     are uploaded as spotlight.json — NOT over a file-on-disk read. This
     guarantees the consumer integrity check matches whatever bytes
     actually landed in S3 (Pitfall 2).
  5. json.dumps must NEVER force a sorted-keys serialization in this
     module — insertion order is the canonical representation (Pitfall 4).
  6. Same-day re-publish is allowed and emits a logger.warning before
     overwriting (operator-discipline mitigation, T-06-07-05).
  7. After all 6 PutObjects succeed, history_writer.update_history is
     called for every Selection so SPOTLIGHT_HISTORY# state advances.
  8. NoCredentialsError surfaces an operator-friendly hint pointing at
     ~/.zshrc env vars; the hint is INSTRUCTIONAL and never displays
     credential VALUES (T-06-07-04).

Phase 5 deltas (intentionally NOT carried over from backfill_all.py):
  - No PM worktree copy (the Phase 5 PM-copy helpers do not exist in this
    module). Spotlight is SPS-only.
  - Bucket is `ARTIFACTS_BUCKET` (`wcmc-reciterai-artifacts`), not
    `HIERARCHY_BUCKET`.
  - Top-level prefix is `spotlight/`, not bare `v{date}/`.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date

from botocore.exceptions import NoCredentialsError
from jsonschema import Draft202012Validator

from spotlight.history_writer import update_history
from spotlight.rotation_selector import Selection
from utils.s3_client import ARTIFACTS_BUCKET, S3HierarchyClient
from utils.iso_clock import now_iso

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

PREFIX = "spotlight"
SPOTLIGHT_VERSION = "spotlight_v1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def publish_artifact(
    artifact: dict,
    schema: dict,
    selections: list[Selection],
    dry_run: bool = False,
    s3_client=None,
    dynamo_client=None,
) -> int:
    """Validate + upload the spotlight artifact to S3, then write history.

    Args:
        artifact: spotlight.json dict produced by
            ``spotlight.assembler.build_artifact``.
        schema: spotlight.schema.json dict (loaded from
            ``docs/spotlight.schema.json``).
        selections: list of ``Selection`` objects from
            ``rotation_selector.select_with_diversity``. After upload
            succeeds, ``history_writer.update_history`` is called with this
            list to advance ``SPOTLIGHT_HISTORY#`` state.
        dry_run: when True, run validation + manifest preview only; no
            PutObject calls and no history writeback. Returns 0 on success.
        s3_client: optional injected S3 client (test seam). Defaults to
            ``S3HierarchyClient(bucket=ARTIFACTS_BUCKET)`` when None.
        dynamo_client: optional injected DynamoDB client passed to
            ``update_history`` (test seam). Defaults to lazy-init in
            history_writer when None.

    Returns:
        0 on successful publish (or successful dry-run); 1 on schema
        validation failure or NoCredentialsError.
    """
    # Step 1: schema validation gate (D-16 fail-fast, T-06-07-01).
    # Validation precedes upload; errors short-circuit with no PutObject.
    validator = Draft202012Validator(schema)
    errors = sorted(
        validator.iter_errors(artifact), key=lambda e: list(e.absolute_path)
    )
    if errors:
        print("\nSCHEMA VALIDATION FAILED — spotlight.json does not match schema:\n")
        for e in errors:
            path = " > ".join(str(p) for p in e.absolute_path) or "<root>"
            print(f"  [{path}] {e.message}")
        print(
            "\nFix the artifact (or update docs/spotlight.schema.json) "
            "before publishing."
        )
        return 1
    logger.info("Schema validation: PASS")

    # Step 2: compute bytes + sha256 over in-memory bytes (T-06-07-02).
    # Do NOT force sorted-keys serialization (T-06-07-03 / Pitfall 4).
    artifact_bytes = json.dumps(artifact, indent=2, ensure_ascii=False).encode(
        "utf-8"
    )
    schema_bytes = json.dumps(schema, indent=2, ensure_ascii=False).encode("utf-8")
    sha256 = hashlib.sha256(artifact_bytes).hexdigest()
    version = f"v{date.today().isoformat()}"
    schema_version = (
        schema.get("$defs", {}).get("_meta", {}).get("schema_version", "1.0.0")
    )

    # Step 3: build manifest in LOCKED insertion order (T-06-07-03).
    # Manifest field order is LOCKED — do NOT sort_keys.
    # Order: schema_version, spotlight_version, taxonomy_version, version,
    #        generated_at, sha256, artifact_bytes (RESEARCH §Pattern 6).
    manifest = {
        "schema_version":   schema_version,
        "spotlight_version": SPOTLIGHT_VERSION,
        "taxonomy_version": artifact.get("taxonomy_version", "unknown"),
        "version":          version,
        "generated_at":     now_iso(),
        "sha256":           sha256,
        "artifact_bytes":   len(artifact_bytes),
    }
    manifest_bytes = json.dumps(manifest, indent=2, ensure_ascii=False).encode(
        "utf-8"
    )

    # Step 4: dry-run short-circuit (no PutObject, no history writeback).
    if dry_run:
        print("\nSpotlight publish — DRY RUN preview")
        print(f"  Version: {version}")
        print(f"  Schema version: {schema_version}")
        print(f"  Spotlight version: {SPOTLIGHT_VERSION}")
        print(f"  Taxonomy version: {manifest['taxonomy_version']}")
        print(f"  sha256: {sha256}")
        print(f"  Artifact bytes: {len(artifact_bytes):,}")
        print(f"  Would upload to: s3://{ARTIFACTS_BUCKET}/{PREFIX}/{version}/")
        print(f"  Would also publish to: s3://{ARTIFACTS_BUCKET}/{PREFIX}/latest/")
        print("\nManifest preview:\n" + json.dumps(manifest, indent=2))
        return 0

    # Step 5: S3 upload — validation gate has already passed (T-06-07-01).
    try:
        s3 = s3_client or S3HierarchyClient(bucket=ARTIFACTS_BUCKET)

        # Idempotency: warn (don't skip) on same-day re-publish (T-06-07-05).
        if s3.key_exists(f"{PREFIX}/{version}/manifest.json"):
            logger.warning(
                f"{PREFIX}/{version}/ already exists in s3://{ARTIFACTS_BUCKET} "
                f"— overwriting (same-day re-publish)."
            )

        # Versioned prefix (CONTEXT decision Q2.2).
        s3.put_object(f"{PREFIX}/{version}/spotlight.json",        artifact_bytes)
        s3.put_object(f"{PREFIX}/{version}/spotlight.schema.json", schema_bytes)
        s3.put_object(f"{PREFIX}/{version}/manifest.json",         manifest_bytes)

        # latest/ overwrite (CONTEXT decision Q2.2).
        s3.put_object(f"{PREFIX}/latest/spotlight.json",        artifact_bytes)
        s3.put_object(f"{PREFIX}/latest/spotlight.schema.json", schema_bytes)
        s3.put_object(f"{PREFIX}/latest/manifest.json",         manifest_bytes)

    except NoCredentialsError:
        # Operator hint — INSTRUCTIONAL string; never displays values
        # (T-06-07-04). The literal phrase is asserted by tests.
        print(
            "\nABORT: AWS credentials not found. Ensure AWS_ACCESS_KEY_ID and "
            "AWS_SECRET_ACCESS_KEY are exported in your shell (from ~/.zshrc). "
            "Run: source ~/.zshrc && python backfill_spotlight.py --publish"
        )
        return 1

    # Step 6: post-publish SPOTLIGHT_HISTORY# writeback. publish_id == version
    # so the rotation selector's decay calculation can correlate runs.
    # Phase 11 D-04: hierarchy_version == version in spotlight publish context.
    # The spotlight publisher reads `version` from the active manifest
    # (f"v{date.today().isoformat()}"); this is also the hierarchy_version
    # that governs which subtopic IDs are valid for this publish run.
    update_history(dynamo_client, selections, publish_id=version, hierarchy_version=version)

    # Step 7: operational summary (no lede text, no credentials, T-06-07-11).
    logger.info(
        f"Published spotlight {version} to s3://{ARTIFACTS_BUCKET}/{PREFIX}/{version}/ "
        f"(sha256={sha256[:12]}..., {len(artifact_bytes):,} bytes, "
        f"{len(selections)} selections)"
    )
    return 0


__all__ = [
    "PREFIX",
    "SPOTLIGHT_VERSION",
    "publish_artifact",
]

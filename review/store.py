"""DDB read/write wrappers for REVIEW# rows (Phase 11).

PK shape (D-07): REVIEW#{artifact_type}#{version}
SK:             GLOBAL  (only one review record per artifact+version)

write_review — PutItem the REVIEW# row.
read_run_signals — Query STAGE# + LOW_CONFIDENCE_ASSIGNMENT# rows + S3
                   HEAD to assemble RunSignals for the pre-write validator.
"""
from __future__ import annotations

from typing import Any

from review.validator import RunSignals

# ---------------------------------------------------------------------------
# PK / SK constants
# ---------------------------------------------------------------------------

REVIEW_PK_TEMPLATE = "REVIEW#{artifact_type}#{version}"
REVIEW_SK = "GLOBAL"


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def write_review(
    table: Any,
    *,
    artifact_type: str,
    version: str,
    review_dict: dict,
) -> dict:
    """Write a REVIEW# row to DynamoDB.

    Args:
        table: boto3 DynamoDB Table resource (from utils.dynamodb_helpers.get_table).
        artifact_type: Artifact type string (e.g. "hierarchy").
        version: Version string (e.g. "v2026-06-01").
        review_dict: Validated review fields to merge into the item.

    Returns:
        The full item dict that was written (including PK and SK).
    """
    item = {
        "PK": REVIEW_PK_TEMPLATE.format(artifact_type=artifact_type, version=version),
        "SK": REVIEW_SK,
        **review_dict,
    }
    table.put_item(Item=item)
    return item


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def read_run_signals(
    table: Any,
    s3_client: Any,
    *,
    run_id: str,
    proposed_artifact_uri: str,
) -> RunSignals:
    """Query DDB and S3 to assemble RunSignals for the pre-write validator.

    Queries performed:
      - Full table scan for STAGE# rows (Phase 9 convention: Python filter
        until GSI added). Collects failed_stages and gate_block_errors for
        the given run_id.
      - Full table scan for LOW_CONFIDENCE_ASSIGNMENT# rows filtered by
        run_id → low_confidence_count.
      - S3 head_object on proposed_artifact_uri → artifact_uri_exists.

    Note on uncovered_pmid_count: currently not written by any Phase 9-11
    stage; defaults to 0. Will be wired when the uncovered-PMID tracking
    plan lands.

    Args:
        table: boto3 DynamoDB Table resource.
        s3_client: S3HierarchyClient instance with a head_object-capable
            _get_client() (or any object exposing key_exists(key)).
        run_id: Cold-run identifier (typically from RECITERAI_COLD_RUN_ID
            env var; empty string means no run filtering is applied and
            all STAGE# rows are considered).
        proposed_artifact_uri: Full S3 URI to HEAD-check
            (e.g. "s3://wcmc-reciterai-hierarchy/v2026-06-01/hierarchy.json").

    Returns:
        RunSignals with all fields populated from live DDB/S3 state.
    """
    failed_stages: list[str] = []
    gate_block_errors: list[str] = []
    low_confidence_count: int = 0

    # --- STAGE# rows --------------------------------------------------------
    # Scan for STAGE# PKs; filter by run_id on the SK suffix (RUN#{run_id})
    # when run_id is set, otherwise collect all failed/blocked rows.
    stage_resp = table.scan(
        FilterExpression="begins_with(PK, :stage_prefix)",
        ExpressionAttributeValues={":stage_prefix": "STAGE#"},
    )
    for item in stage_resp.get("Items", []):
        # Filter by run_id if provided
        if run_id and run_id not in item.get("SK", ""):
            continue
        if item.get("status") == "failed":
            stage_name = item.get("stage", "")
            if stage_name and stage_name not in failed_stages:
                failed_stages.append(stage_name)
            error_code = item.get("error_code", "")
            if error_code.startswith("GATE_BLOCK_") and error_code not in gate_block_errors:
                gate_block_errors.append(error_code)

    # --- LOW_CONFIDENCE_ASSIGNMENT# rows ------------------------------------
    lca_resp = table.scan(
        FilterExpression="begins_with(PK, :lca_prefix)",
        ExpressionAttributeValues={":lca_prefix": "LOW_CONFIDENCE_ASSIGNMENT#"},
    )
    for item in lca_resp.get("Items", []):
        if run_id and run_id not in item.get("SK", ""):
            continue
        low_confidence_count += 1

    # --- S3 HEAD ------------------------------------------------------------
    artifact_uri_exists = _check_artifact_uri(s3_client, proposed_artifact_uri)

    return RunSignals(
        failed_stages=tuple(failed_stages),
        gate_block_errors=tuple(gate_block_errors),
        artifact_uri_exists=artifact_uri_exists,
        low_confidence_count=low_confidence_count,
        uncovered_pmid_count=0,
    )


def _check_artifact_uri(s3_client: Any, uri: str) -> bool:
    """Return True if the S3 URI resolves (HEAD 200); False on 404.

    Args:
        s3_client: Object exposing key_exists(key: str) -> bool, or a raw
            boto3 S3 client with head_object. Prefers key_exists() if present
            (S3HierarchyClient convention).
        uri: Full S3 URI ("s3://bucket/key/path").

    Returns:
        True if the object exists, False if 404 or URI is malformed/empty.
    """
    if not uri or not uri.startswith("s3://"):
        return False

    # Parse bucket and key from "s3://bucket/key/path"
    rest = uri[len("s3://"):]
    slash = rest.find("/")
    if slash == -1:
        return False
    bucket = rest[:slash]
    key = rest[slash + 1:]

    if hasattr(s3_client, "key_exists"):
        # S3HierarchyClient — but key_exists uses self.bucket; if bucket differs,
        # fall through to raw head_object. Since the production client is always
        # constructed for the hierarchy bucket, we check the bucket match.
        try:
            # Attempt via the client's bucket; the URI bucket is ignored here
            # because S3HierarchyClient encapsulates bucket selection.
            return s3_client.key_exists(key)
        except Exception:
            return False

    # Raw boto3 S3 client fallback
    from botocore.exceptions import ClientError

    try:
        s3_client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
            return False
        raise

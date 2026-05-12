"""
S3 client for ReCiter AI artifact publishing pipelines (Phases 5 + 6).

Mirrors the BedrockClient lazy-init convention from utils/bedrock_client.py:
no boto3 calls at import time; client created lazily on first method call.

Well-known buckets exposed as module constants:

- HIERARCHY_BUCKET (Phase 5, "wcmc-reciterai-hierarchy") -- the original
  hierarchy artifact target. Default for S3HierarchyClient() so existing
  Phase 5 callers are unaffected.
- ARTIFACTS_BUCKET (Phase 6, "wcmc-reciterai-artifacts") -- the consolidated
  artifact bucket introduced for the spotlight pipeline. Both hierarchy.json
  and spotlight.json live here under disjoint top-level prefixes
  (hierarchy/ vs spotlight/) once the bucket-migration runbook
  (docs/bucket-migration-runbook.md) has been executed by the operator.

Both bucket names map to the same S3HierarchyClient class via the
``bucket=`` constructor kwarg -- no class rename, no behavior change.
Plans 06-02..06-07 instantiate ``S3HierarchyClient(bucket=ARTIFACTS_BUCKET)``
to target the new bucket.

Provides S3HierarchyClient with:
- Lazy boto3 S3 client initialization (no AWS calls at import time)
- put_object(key, body) -- PutObject with content-type header
- key_exists(key) -- HeadObject existence check; re-raises non-404 errors

Usage:
    from utils.s3_client import S3HierarchyClient, ARTIFACTS_BUCKET

    # Phase 5 default (hierarchy bucket)
    s3 = S3HierarchyClient()
    s3.put_object("latest/hierarchy.json", hierarchy_bytes)

    # Phase 6 publish target
    s3 = S3HierarchyClient(bucket=ARTIFACTS_BUCKET)
    s3.put_object("spotlight/latest/spotlight.json", spotlight_bytes)

Security:
    AWS credentials via the default credential chain (env vars exported by
    the operator shell profile, IAM role, or ~/.aws/credentials). Never
    passed as parameters or logged. This module never reads, displays, or
    logs credential values.
"""
import os
import boto3
import logging

logger = logging.getLogger(__name__)

HIERARCHY_BUCKET = "wcmc-reciterai-hierarchy"
ARTIFACTS_BUCKET = "wcmc-reciterai-artifacts"
HIERARCHY_REGION = "us-east-1"


class S3HierarchyClient:
    """
    Lazy-init S3 client for hierarchy artifact publishing.

    Design follows BedrockClient convention (utils/bedrock_client.py):
    - No boto3 calls at import time. Client created on first method call
      and cached for subsequent calls.
    - AWS credentials via default credential chain (env vars exported from
      ~/.zshrc, IAM role, or ~/.aws/credentials).
    - Credentials are never passed as constructor parameters or logged.
    - NoCredentialsError surfaces immediately so the caller can emit a
      human-readable message (see backfill_all.py _run_publish).

    Security (T-05-02-01):
    - All log lines emit operational metadata only (S3 key, byte count).
    - No aws_access_key_id, aws_secret_access_key, or session token values
      are ever logged or printed.
    """

    def __init__(self, bucket: str = HIERARCHY_BUCKET, region: str = HIERARCHY_REGION) -> None:
        """
        Initialize the S3HierarchyClient.

        Args:
            bucket: S3 bucket name (default: wcmc-reciterai-hierarchy per D-01).
            region: AWS region (default: us-east-1 per D-01).

        Note: Does NOT create the boto3 client here. Client is created lazily
        on the first API call to avoid import-time side effects.
        """
        self.bucket = bucket
        self.region = region
        self._client = None  # Lazy init — set on first call to _get_client()

    def _get_client(self):
        """Get or create the S3 boto3 client (lazy initialization).

        Creates the client only once and caches it on self._client.
        Uses default AWS credential chain (env vars, ~/.aws/credentials, IAM role).
        """
        if self._client is None:
            self._client = boto3.client("s3", region_name=self.region)
        return self._client

    def put_object(self, key: str, body: bytes, content_type: str = "application/json") -> None:
        """Upload bytes to S3 at the given key.

        Args:
            key: S3 object key (e.g. "v2026-05-06/hierarchy.json").
            body: Raw bytes to upload.
            content_type: HTTP Content-Type header (default: application/json).

        Raises:
            botocore.exceptions.ClientError: On S3 API errors (permissions, etc.).
            botocore.exceptions.NoCredentialsError: If no AWS credentials found.
        """
        self._get_client().put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
        )
        logger.info(f"Uploaded s3://{self.bucket}/{key} ({len(body):,} bytes)")

    def get_object_bytes(self, key: str) -> bytes:
        """Fetch raw bytes for an S3 object.

        Args:
            key: S3 object key (e.g. "v2026-05-12/hierarchy.json").

        Returns:
            Raw object body as bytes.

        Raises:
            botocore.exceptions.ClientError: On 404 (NoSuchKey) or other errors.
              Callers that want to treat 404 specially should catch ClientError
              and inspect e.response["Error"]["Code"].
        """
        resp = self._get_client().get_object(Bucket=self.bucket, Key=key)
        body = resp["Body"].read()
        logger.info(f"Fetched s3://{self.bucket}/{key} ({len(body):,} bytes)")
        return body

    def key_exists(self, key: str) -> bool:
        """Check whether an S3 key exists via HeadObject.

        Args:
            key: S3 object key to check.

        Returns:
            True if the key exists, False if it does not (404 / NoSuchKey).

        Raises:
            botocore.exceptions.ClientError: On non-404 errors (e.g. AccessDenied).
              Non-404 errors are re-raised so the caller sees the auth issue.
        """
        from botocore.exceptions import ClientError
        try:
            self._get_client().head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
                return False
            raise

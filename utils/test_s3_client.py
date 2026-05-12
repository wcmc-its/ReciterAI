"""Phase 11: Tests for S3HierarchyClient.put_object cache_control kwarg (D-11).

Uses MagicMock for _get_client() — no real AWS calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from utils.s3_client import S3HierarchyClient


def _make_client_with_mock() -> tuple[S3HierarchyClient, MagicMock]:
    """Return (client, mock_boto3_client) where mock replaces the lazy-init."""
    client = S3HierarchyClient(bucket="test-bucket", region="us-east-1")
    mock_boto3 = MagicMock()
    client._client = mock_boto3
    return client, mock_boto3


# ---------- cache_control kwarg tests ----------


def test_put_object_without_cache_control_does_not_include_CacheControl():
    """Default call (no cache_control) must NOT pass CacheControl to boto3."""
    client, mock_boto3 = _make_client_with_mock()
    client.put_object("latest/manifest.json", b"data")
    call_kwargs = mock_boto3.put_object.call_args.kwargs
    assert "CacheControl" not in call_kwargs, (
        "put_object() without cache_control must not pass CacheControl to boto3"
    )


def test_put_object_with_cache_control_includes_CacheControl():
    """cache_control kwarg must flow through to boto3 as CacheControl."""
    client, mock_boto3 = _make_client_with_mock()
    client.put_object(
        "latest/manifest.json",
        b"data",
        cache_control="max-age=60, must-revalidate",
    )
    call_kwargs = mock_boto3.put_object.call_args.kwargs
    assert "CacheControl" in call_kwargs, (
        "put_object() with cache_control must pass CacheControl to boto3"
    )
    assert call_kwargs["CacheControl"] == "max-age=60, must-revalidate"


def test_put_object_with_cache_control_none_does_not_include_CacheControl():
    """cache_control=None (explicit default) must not include CacheControl."""
    client, mock_boto3 = _make_client_with_mock()
    client.put_object("v2026-06-01/hierarchy.json", b"data", cache_control=None)
    call_kwargs = mock_boto3.put_object.call_args.kwargs
    assert "CacheControl" not in call_kwargs, (
        "put_object(cache_control=None) must not pass CacheControl to boto3"
    )


def test_put_object_passes_required_fields_to_boto3():
    """Bucket, Key, Body, ContentType must always be present."""
    client, mock_boto3 = _make_client_with_mock()
    client.put_object("v2026-06-01/hierarchy.json", b"hello")
    call_kwargs = mock_boto3.put_object.call_args.kwargs
    assert call_kwargs["Bucket"] == "test-bucket"
    assert call_kwargs["Key"] == "v2026-06-01/hierarchy.json"
    assert call_kwargs["Body"] == b"hello"
    assert call_kwargs["ContentType"] == "application/json"


def test_put_object_custom_content_type_passes_through():
    """content_type kwarg must override the default."""
    client, mock_boto3 = _make_client_with_mock()
    client.put_object("test.txt", b"data", content_type="text/plain")
    call_kwargs = mock_boto3.put_object.call_args.kwargs
    assert call_kwargs["ContentType"] == "text/plain"


def test_get_object_bytes_returns_body():
    """get_object_bytes() uses the existing method — not a new accessor."""
    client, mock_boto3 = _make_client_with_mock()
    mock_resp = {"Body": MagicMock()}
    mock_resp["Body"].read.return_value = b"hierarchy-bytes"
    mock_boto3.get_object.return_value = mock_resp

    result = client.get_object_bytes("v2026-06-01/hierarchy.json")
    assert result == b"hierarchy-bytes"
    mock_boto3.get_object.assert_called_once_with(
        Bucket="test-bucket", Key="v2026-06-01/hierarchy.json"
    )

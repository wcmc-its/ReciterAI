import hashlib
import json
from unittest.mock import MagicMock

import pytest

from pipeline_grants.persist import (
    OpportunitiesPublishShrinkError,
    publish_opportunities_artifact,
)


def _s3_with_prior(count):
    """MagicMock S3 whose latest/manifest.json reports `count` opportunities."""
    s3 = MagicMock()
    s3.key_exists.return_value = True
    s3.get_object_bytes.return_value = json.dumps({"count": count}).encode("utf-8")
    return s3


_ONE = [{"opportunity_id": "grants_gov:1", "title": "T"}]


def test_shrink_guard_blocks_a_collapse_before_any_put():
    s3 = _s3_with_prior(100)  # 100 -> 1 is a >99% shrink
    with pytest.raises(OpportunitiesPublishShrinkError):
        publish_opportunities_artifact(_ONE, s3=s3, version="v2026-06-19")
    assert s3.put_object.call_count == 0


def test_force_overrides_the_shrink_guard():
    s3 = _s3_with_prior(100)
    publish_opportunities_artifact(_ONE, s3=s3, version="v2026-06-19", force=True)
    assert s3.put_object.call_count == 3


def test_no_prior_fails_open_and_publishes():
    s3 = MagicMock()
    s3.key_exists.return_value = False
    publish_opportunities_artifact(_ONE, s3=s3, version="v2026-06-19")
    assert s3.put_object.call_count == 3


def test_publish_writes_versioned_artifact_and_manifest():
    s3 = MagicMock()
    puts = {}
    s3.put_object.side_effect = lambda key, body, **kw: puts.__setitem__(key, body)
    artifact = [{"opportunity_id": "grants_gov:1", "title": "T"}]

    manifest = publish_opportunities_artifact(artifact, s3=s3, version="v2026-06-19")

    assert "grants/v2026-06-19/opportunities.json" in puts
    assert "grants/v2026-06-19/manifest.json" in puts
    assert "grants/latest/manifest.json" in puts
    body = puts["grants/v2026-06-19/opportunities.json"]
    assert manifest["sha256"] == hashlib.sha256(body).hexdigest()
    assert manifest["count"] == 1
    assert json.loads(body)[0]["opportunity_id"] == "grants_gov:1"

import hashlib
import json
from unittest.mock import MagicMock
from pipeline_grants.persist import publish_opportunities_artifact


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

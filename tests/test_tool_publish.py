"""Tests for Step G — tools.json payload assembly + S3 publish (dry-run default)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hashlib

from pipeline_tools import salience as sal
from pipeline_tools.publish import build_publish_payload, publish_artifacts

# The full published key set: 3 flat (transition) + 3 latest/ + the latest/ manifest.
_FLAT_KEYS = {"tools/tools.json", "tools/families.json", "tools/faculty.json"}
_LATEST_KEYS = {"tools/latest/tools.json", "tools/latest/families.json", "tools/latest/faculty.json"}
_MANIFEST_KEY = {"tools/latest/manifest.json"}
_ALL_KEYS = _FLAT_KEYS | _LATEST_KEYS | _MANIFEST_KEY


class _FakeFamilyRegistry:
    def __init__(self, fams):
        self._fams = fams

    def records(self):
        return self._fams


def _result():
    th = sal.GroundedThresholds(s_spread_min=4, a_pub_floor=3, a_spread_floor=2, percentile=0.88)
    fams = [{"family_id": "fam_0001", "label": "molecular imaging", "supercategory": "imaging_image_analysis",
             "dominant_kind": "instrument", "status": "active",
             "member_tool_ids": ["tool_000001"], "exemplar_tool_ids": ["tool_000001"]}]
    return SimpleNamespace(
        thresholds=th,
        records=[{"canonical_tool_id": "tool_000001", "display_name": "MRI", "salience_tier": "S"}],
        family_registry=_FakeFamilyRegistry(fams),
        hierarchy={"imaging_image_analysis": [{"family_id": "fam_0001"}]},
        faculty_rollup={"facA": {"cwid": "facA", "tools": [], "families": []}},
        grant_signal={"tool_000002": {"appl_ids": ["g1"], "investigator_cwids": ["facC"]}},
        telemetry={"canonical_tools": 1, "exceptions_by_type": {"minted_family": 1}},
    )


def test_payload_structure_and_thresholds():
    p = build_publish_payload(_result(), provenance={"raw_mentions": 10})
    assert p["schema_version"] == "tools-a2-v1"
    assert p["provenance"]["raw_mentions"] == 10
    assert p["salience_thresholds"]["s_spread_min"] == 4
    assert p["tools"][0]["canonical_tool_id"] == "tool_000001"
    assert p["families"][0]["label"] == "molecular imaging"
    assert "facA" in p["faculty"]
    assert "tool_000002" in p["grant_signal"]
    # payload is fully JSON-serializable
    json.loads(json.dumps(p))


def test_publish_dry_run_uploads_nothing():
    p = build_publish_payload(_result(), provenance={})

    class _BoomS3:
        def put_object(self, *a, **k):
            raise AssertionError("dry-run must not upload")

    report = publish_artifacts(p, s3_client=_BoomS3(), dry_run=True)
    assert all(r["uploaded"] is False for r in report)
    # Manifest is reported (keys + bytes) but its sha/bytes are computed from
    # in-memory bytes, so a dry-run still touches no AWS.
    assert {r["key"] for r in report} == _ALL_KEYS


def test_publish_real_uploads_each_object():
    p = build_publish_payload(_result(), provenance={})
    calls = []

    class _RecS3:
        def put_object(self, key, body, content_type="application/json", cache_control=None):
            calls.append((key, len(body), content_type, cache_control))

    report = publish_artifacts(p, s3_client=_RecS3(), dry_run=False)
    assert all(r["uploaded"] for r in report)
    assert {c[0] for c in calls} == _ALL_KEYS
    assert all(c[2] == "application/json" for c in calls)
    # Every latest/* object (incl. manifest) carries the short cache; flat
    # families/faculty keep their existing no-cache posture.
    cc = {c[0]: c[3] for c in calls}
    for k in _LATEST_KEYS | _MANIFEST_KEY | {"tools/tools.json"}:
        assert cc[k] == "max-age=60, must-revalidate", k
    assert cc["tools/families.json"] is None
    assert cc["tools/faculty.json"] is None


def test_publish_manifest_integrity_and_latest_mirror():
    """latest/ bytes mirror flat bytes; manifest sha256/bytes match what was uploaded."""
    p = build_publish_payload(_result(), provenance={})
    bodies = {}

    class _CapS3:
        def put_object(self, key, body, content_type="application/json", cache_control=None):
            bodies[key] = body

    publish_artifacts(p, s3_client=_CapS3(), dry_run=False)

    # latest/ copies are byte-identical to the flat copies.
    for name in ("tools.json", "families.json", "faculty.json"):
        assert bodies[f"tools/latest/{name}"] == bodies[f"tools/{name}"], name

    manifest = json.loads(bodies["tools/latest/manifest.json"])
    assert manifest["schema_version"] == "tools-a2-v1"
    assert "taxonomy_version" not in manifest  # deliberately omitted
    # objects{} integrity: sha256 + bytes match the exact uploaded latest/ bytes.
    for name in ("tools.json", "families.json", "faculty.json"):
        body = bodies[f"tools/latest/{name}"]
        obj = manifest["objects"][name]
        assert obj["key"] == f"tools/latest/{name}"
        assert obj["bytes"] == len(body)
        assert obj["sha256"] == hashlib.sha256(body).hexdigest()
    # Top-level sha256/artifact_bytes point at latest/tools.json (the bundle).
    tools_body = bodies["tools/latest/tools.json"]
    assert manifest["sha256"] == hashlib.sha256(tools_body).hexdigest()
    assert manifest["artifact_bytes"] == len(tools_body)
    assert manifest["counts"] == {"tools": 1, "families": 1, "faculty": 1}

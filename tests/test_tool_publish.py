"""Tests for Step G — tools.json payload assembly + S3 publish (dry-run default)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline_tools import salience as sal
from pipeline_tools.publish import build_publish_payload, publish_artifacts


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
    assert {r["key"] for r in report} == {"tools/tools.json", "tools/families.json", "tools/faculty.json"}


def test_publish_real_uploads_each_object():
    p = build_publish_payload(_result(), provenance={})
    calls = []

    class _RecS3:
        def put_object(self, key, body, content_type="application/json", cache_control=None):
            calls.append((key, len(body), content_type))

    report = publish_artifacts(p, s3_client=_RecS3(), dry_run=False)
    assert all(r["uploaded"] for r in report)
    assert {c[0] for c in calls} == {"tools/tools.json", "tools/families.json", "tools/faculty.json"}
    assert all(c[2] == "application/json" for c in calls)

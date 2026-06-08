"""Per-inference model provenance — classify + relabel stamp which model answered."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline_tools.classify import classify_batch
from pipeline_tools.registry import FamilyRegistry
from pipeline_tools.relabel import relabel_families


def _classification(raw):
    return {"raw_name": raw, "disposition": "method_tool", "kind": "method",
            "supercategory": "computational_statistical", "attributes": {}, "confidence": "high"}


def test_classify_batch_stamps_model_from_response():
    def call_json(system, user):
        return {"_model": "us.anthropic.claude-sonnet-4-6", "classifications": [_classification("Tool X")]}

    recs = classify_batch([{"raw_name": "Tool X"}], call_json=call_json)
    assert recs[0]["model"] == "us.anthropic.claude-sonnet-4-6"


def test_classify_batch_stamps_fallback_model():
    def call_json(system, user):  # simulates the gpt-5.x fallback response
        return {"_model": "gpt-5.x", "classifications": [_classification("Tool Y")]}

    recs = classify_batch([{"raw_name": "Tool Y"}], call_json=call_json)
    assert recs[0]["model"] == "gpt-5.x"


def test_classify_batch_no_model_key_when_absent():
    def call_json(system, user):  # a stub that doesn't report a model
        return {"classifications": [_classification("Tool Z")]}

    recs = classify_batch([{"raw_name": "Tool Z"}], call_json=call_json)
    assert "model" not in recs[0]  # no provenance reported -> no stamp (not a false value)


def test_relabel_delta_carries_model():
    fams = FamilyRegistry([{
        "family_id": "fam_0001", "label": "X", "supercategory": "computational_statistical",
        "dominant_kind": "method", "member_tool_ids": ["t1"], "exemplar_tool_ids": ["t1"],
        "status": "provisional", "member_display_names": ["n"],
    }])

    def call_json(system, user):
        return {"_model": "us.anthropic.claude-sonnet-4-6",
                "families": [{"family_id": "fam_0001", "label": "risk prediction", "confidence": "high"}]}

    deltas = relabel_families(fams, call_json=call_json)
    assert deltas[0]["model"] == "us.anthropic.claude-sonnet-4-6"

"""Tests for §7.2 family relabel + §7 exact-label dedup sweep."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline_tools.registry import FamilyRegistry
from pipeline_tools.relabel import dedup_families, relabel_families


def _fam(fid, label, supercat, members, kind="method"):
    return {
        "family_id": fid, "label": label, "supercategory": supercat, "dominant_kind": kind,
        "member_tool_ids": members, "exemplar_tool_ids": members[:1],
        "status": "provisional", "member_display_names": [f"name-{m}" for m in members],
    }


# --- relabel ---------------------------------------------------------------

def test_relabel_sets_controlled_label_and_promotes_confident_to_active():
    fams = FamilyRegistry([_fam("fam_0001", "PET scanner", "imaging_image_analysis", ["tool_1"])])

    def call_json(system, user):
        return {"families": [{"family_id": "fam_0001", "label": "molecular imaging", "confidence": "high"}]}

    deltas = relabel_families(fams, call_json=call_json)
    fam = fams.get("fam_0001")
    assert fam["label"] == "molecular imaging"
    assert fam["status"] == "active"
    assert deltas == [{"family_id": "fam_0001", "old_label": "PET scanner",
                       "new_label": "molecular imaging", "status": "active", "confidence": "high"}]


def test_relabel_keeps_low_confidence_provisional_for_review():
    fams = FamilyRegistry([_fam("fam_0001", "X", "computational_statistical", ["t1"])])

    def call_json(system, user):
        return {"families": [{"family_id": "fam_0001", "label": "risk prediction", "confidence": "low"}]}

    relabel_families(fams, call_json=call_json)
    assert fams.get("fam_0001")["status"] == "provisional"


def test_relabel_partial_failure_keeps_placeholder():
    fams = FamilyRegistry([_fam("fam_0001", "placeholder", "computational_statistical", ["t1"])])

    def call_json(system, user):
        raise RuntimeError("bedrock down")

    deltas = relabel_families(fams, call_json=call_json)
    assert deltas == []
    assert fams.get("fam_0001")["label"] == "placeholder"  # untouched, still provisional
    assert fams.get("fam_0001")["status"] == "provisional"


# --- dedup -----------------------------------------------------------------

def test_dedup_merges_identical_label_same_supercategory_onto_older_id():
    fams = FamilyRegistry([
        _fam("fam_0002", "antiretrovirals", "therapeutics_interventions", ["tool_9"]),
        _fam("fam_0005", "antiretrovirals", "therapeutics_interventions", ["tool_12", "tool_13"]),
    ])
    deltas = dedup_families(fams)
    assert len(deltas) == 1
    d = deltas[0]
    assert d["keep_id"] == "fam_0002"  # older (lower) id survives — D-06
    assert d["drop_id"] == "fam_0005"
    assert set(d["moved_tool_ids"]) == {"tool_12", "tool_13"}
    assert fams.get("fam_0005") is None  # forked family removed
    assert set(fams.get("fam_0002")["member_tool_ids"]) == {"tool_9", "tool_12", "tool_13"}


def test_dedup_does_not_merge_across_supercategories():
    fams = FamilyRegistry([
        _fam("fam_0001", "imaging", "imaging_image_analysis", ["a"]),
        _fam("fam_0002", "imaging", "microscopy_histology", ["b"]),
    ])
    deltas = dedup_families(fams)
    assert deltas == []
    assert len(fams) == 2  # cross-supercategory near-dupes stay for review, not auto-merged


def test_dedup_noop_when_all_distinct():
    fams = FamilyRegistry([
        _fam("fam_0001", "macromolecular crystallography", "structural_biophysical", ["a"]),
        _fam("fam_0002", "binding thermodynamics", "structural_biophysical", ["b"]),
    ])
    assert dedup_families(fams) == []


# --- merge_into edge cases -------------------------------------------------

def test_merge_into_noop_on_self_or_missing():
    fams = FamilyRegistry([_fam("fam_0001", "x", "computational_statistical", ["a"])])
    assert fams.merge_into(keep_id="fam_0001", drop_id="fam_0001") == []
    assert fams.merge_into(keep_id="fam_0001", drop_id="fam_9999") == []
    assert len(fams) == 1


def test_merge_into_inherits_exemplars_only_when_keep_has_none():
    fams = FamilyRegistry([
        {"family_id": "fam_0001", "label": "x", "supercategory": "s", "member_tool_ids": [],
         "exemplar_tool_ids": [], "status": "provisional", "member_display_names": []},
        _fam("fam_0002", "x", "s", ["t9"]),
    ])
    moved = fams.merge_into(keep_id="fam_0001", drop_id="fam_0002")
    assert moved == ["t9"]
    assert fams.get("fam_0001")["exemplar_tool_ids"] == ["t9"]  # inherited (keep had none)

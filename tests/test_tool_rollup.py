"""Tests for the faculty rollup — per-(scholar,tool) count + §8 C-reconciliation."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline_tools.rollup import build_faculty_rollup


def _occ(cid, pmid, cwid, role="lead", source="publication"):
    return {"canonical_tool_id": cid, "pmid": pmid, "cwid": cwid, "author_role": role, "source_kind": source}


TOOL_INDEX = {
    "tool_A": {"display_name": "A", "disposition": "method_tool", "salience_tier": "A",
               "member_of_family": "fam_1", "supercategory": "computational_statistical"},
    "tool_B": {"display_name": "B", "disposition": "method_tool", "salience_tier": "C",  # demoted
               "member_of_family": "fam_1", "supercategory": "computational_statistical"},
    "tool_C": {"display_name": "C", "disposition": "method_tool", "salience_tier": "B",
               "member_of_family": "fam_2", "supercategory": "imaging_image_analysis"},
    "tool_D": {"display_name": "D", "disposition": "infrastructure", "salience_tier": None,
               "member_of_family": None, "supercategory": None},
}
FAMILY_INDEX = {
    "fam_1": {"label": "regression methods", "supercategory": "computational_statistical"},
    "fam_2": {"label": "molecular imaging", "supercategory": "imaging_image_analysis"},
}


def _rollup(occs):
    return build_faculty_rollup(occs, tool_index=TOOL_INDEX, family_index=FAMILY_INDEX)


def test_per_scholar_tool_pub_count_is_distinct_pmids():
    occs = [_occ("tool_A", "p1", "fac1"), _occ("tool_A", "p2", "fac1"), _occ("tool_A", "p2", "fac1")]
    r = _rollup(occs)["fac1"]
    a = next(t for t in r["tools"] if t["canonical_tool_id"] == "tool_A")
    assert a["pub_count"] == 2  # p2 de-duplicated


def test_grant_occurrences_excluded_from_pub_rollup():
    occs = [_occ("tool_A", "p1", "fac1"), _occ("tool_A", "grant:g9", "fac1", role="investigator", source="grant")]
    r = _rollup(occs)["fac1"]
    a = next(t for t in r["tools"] if t["canonical_tool_id"] == "tool_A")
    assert a["pub_count"] == 1  # grant hit not counted in the pub-filter


def test_infrastructure_tools_excluded():
    occs = [_occ("tool_D", "p1", "fac1")]
    assert "fac1" not in _rollup(occs)  # no method_tool touched -> no profile row


def test_family_count_uses_C_reconciliation():
    # fac1 touches tool_A (tier A, p1+p2) and tool_B (tier C, p3) — both in fam_1.
    occs = [_occ("tool_A", "p1", "fac1"), _occ("tool_A", "p2", "fac1"), _occ("tool_B", "p3", "fac1")]
    r = _rollup(occs)["fac1"]
    fam1 = next(f for f in r["families"] if f["family_id"] == "fam_1")
    # C-tier member tool_B's p3 does NOT count; only non-C tool_A's {p1,p2}.
    assert fam1["pub_count"] == 2
    assert fam1["exemplar_tool_ids"] == ["tool_A"]  # C member never an exemplar


def test_family_with_only_C_member_does_not_surface():
    occs = [_occ("tool_B", "p3", "fac1")]  # only a C-tier family hit
    r = _rollup(occs).get("fac1")
    # the scholar's tool row still exists (tool_B touched), but no family row.
    assert r is not None
    assert all(f["family_id"] != "fam_1" for f in r["families"])


def test_exemplars_ranked_by_scholar_own_usage():
    # tool_C (fam_2) twice, plus a hypothetical second non-C member would rank by count.
    occs = [_occ("tool_C", "p1", "fac1"), _occ("tool_C", "p2", "fac1"), _occ("tool_A", "p1", "fac1")]
    r = _rollup(occs)["fac1"]
    tools_sorted = [t["canonical_tool_id"] for t in r["tools"]]
    assert tools_sorted[0] == "tool_C"  # 2 pubs ranks above tool_A's 1

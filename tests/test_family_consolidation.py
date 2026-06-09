"""Tests for the 820-family consolidation batch (pipeline_tools.family_consolidation).

Mechanics on synthetic registries via the parametric ``_apply``; the bundled
``config/family_consolidation_v820.json`` is checked for well-formedness and (when the
v6 artifact is present locally) that every referenced id exists in the v6 registry.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline_tools.embeddings import EmbeddingCache
from pipeline_tools.family_consolidation import _DATA_PATH, _apply, _load
from pipeline_tools.registry import FamilyRegistry, ToolRegistry

_STUB = lambda: EmbeddingCache(embed=lambda ts: [[0.5, 0.5] for _ in ts])  # noqa: E731
_SC_A = "computational_statistical"
_SC_B = "molecular_biochem_reagents"


def _tool(cid, sc, fam):
    return {"canonical_tool_id": cid, "display_name": cid, "disposition": "method_tool",
            "kind": "method", "supercategory": sc, "member_of_family": fam, "pub_ids": ["p1"]}


def _fam(fid, label, sc, members):
    return {"family_id": fid, "label": label, "supercategory": sc, "dominant_kind": "method",
            "member_tool_ids": list(members), "exemplar_tool_ids": list(members)[:3],
            "status": "active", "member_display_names": list(members)}


def _fixture():
    tools = [_tool("t1", _SC_A, "fam_a"), _tool("t2", _SC_A, "fam_b"),
             _tool("t3", _SC_B, "fam_c"), _tool("t4", _SC_B, "fam_d")]
    fams = [_fam("fam_a", "Alpha", _SC_A, ["t1"]), _fam("fam_b", "Beta", _SC_A, ["t2"]),
            _fam("fam_c", "Gamma", _SC_B, ["t3"]), _fam("fam_d", "Delta", _SC_B, ["t4"])]
    return FamilyRegistry(fams, cache=_STUB()), ToolRegistry(tools, cache=_STUB())


def test_apply_merges_relabels_and_sets_tiers():
    fr, tr = _fixture()
    # merge fam_b -> fam_a (both #A); relabel fam_a; tier every survivor.
    tiers = {"fam_a": "feature", "fam_c": "standard", "fam_d": "suppressed"}
    deltas = _apply(fr, tr, (("fam_a", "Renamed", "why"),), (("fam_b", "fam_a", "why"),), tiers)
    assert fr.get("fam_b") is None                              # absorbed
    assert fr.get("fam_a")["label"] == "Renamed"               # relabeled
    assert set(fr.get("fam_a")["member_tool_ids"]) == {"t1", "t2"}
    assert tr.get("t2")["member_of_family"] == "fam_a"          # tool repointed
    assert {f["family_id"]: f["display"] for f in fr.records()} == tiers
    assert any(d["op"] == "tiers" and d["families_tiered"] == 3 for d in deltas)


def test_invalid_tier_value_raises():
    fr, tr = _fixture()
    bad = {"fam_a": "feature", "fam_b": "BOGUS", "fam_c": "standard", "fam_d": "suppressed"}
    with pytest.raises(ValueError, match="invalid display tier"):
        _apply(fr, tr, (), (), bad)


def test_surviving_family_without_tier_raises():
    fr, tr = _fixture()
    with pytest.raises(ValueError, match="no display tier"):
        _apply(fr, tr, (), (), {"fam_a": "feature"})  # fam_b/c/d untiered


def test_tier_on_nonsurviving_family_raises():
    fr, tr = _fixture()
    # merge fam_b away, then tier it — it no longer survives.
    tiers = {"fam_a": "feature", "fam_b": "standard", "fam_c": "standard", "fam_d": "suppressed"}
    with pytest.raises(ValueError, match="non-surviving"):
        _apply(fr, tr, (), (("fam_b", "fam_a", "why"),), tiers)


# --- the bundled consolidation data --------------------------------------------

def test_bundled_data_is_wellformed():
    relabels, merges, tiers = _load()
    meta = json.loads(_DATA_PATH.read_text())["_meta"]
    assert len(merges) == meta["merge_absorbs"] == 52
    assert len(tiers) == 820 == meta["surviving"]
    assert {t for _, t in [(0, v) for v in tiers.values()]} == {"feature", "standard", "suppressed"}
    # a merge is (absorb, keep, why); no family is both absorbed and a keep (no chains).
    absorbs = {a for a, _, _ in merges}
    keeps = {k for _, k, _ in merges}
    assert not (absorbs & keeps), "a family is both absorbed and a merge keep (chain)"
    assert len(absorbs) == len(merges), "a family is absorbed more than once"


def test_bundled_ids_exist_in_v6_snapshot_if_present():
    path = Path("out/tools/a2/_v6_backup/family_registry.json")
    if not path.exists():
        pytest.skip("v6 family_registry.json snapshot not present (gitignored artifact)")
    v6 = {f["family_id"] for f in json.loads(path.read_text())["families"]}
    relabels, merges, tiers = _load()
    referenced = {a for a, _, _ in merges} | {k for _, k, _ in merges} \
        | {fid for fid, _, _ in relabels} | set(tiers)
    missing = sorted(referenced - v6)
    assert not missing, f"consolidation references ids absent from v6: {missing[:10]}"

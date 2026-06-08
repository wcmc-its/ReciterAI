"""Tests for the D-07 family-override fix batch (pipeline_tools.family_overrides).

Mechanics are tested with synthetic tables via the parametric core ``_apply_tables``;
the real frozen tables are checked for structural well-formedness and (when the v6
artifact is present locally) that they reference no stale family_id.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline_tools.embeddings import EmbeddingCache
from pipeline_tools.family_overrides import (
    MERGES,
    RELABELS,
    REROUTES,
    _apply_tables,
    _validate,
    apply_overrides,
)
from pipeline_tools.registry import FamilyRegistry, ToolRegistry

_STUB_CACHE = lambda: EmbeddingCache(embed=lambda ts: [[0.5, 0.5] for _ in ts])  # noqa: E731


def _tool(cid, sc, fam):
    return {"canonical_tool_id": cid, "display_name": cid, "disposition": "method_tool",
            "kind": "method", "supercategory": sc, "member_of_family": fam,
            "pub_ids": ["p1"]}


def _fam(fid, label, sc, members):
    return {"family_id": fid, "label": label, "supercategory": sc, "dominant_kind": "method",
            "member_tool_ids": list(members), "exemplar_tool_ids": list(members)[:3],
            "status": "active", "member_display_names": list(members)}


def _fixture():
    """A tiny registry: fam_a/#sc1 (t1,t2), fam_b/#sc2 (t3), fam_c/#sc1 (t4) survivor."""
    tools = [
        _tool("t1", "sc1", "fam_a"), _tool("t2", "sc1", "fam_a"),
        _tool("t3", "sc2", "fam_b"),
        _tool("t4", "sc1", "fam_c"), _tool("t5", "sc1", "fam_c"),
    ]
    fams = [
        _fam("fam_a", "Alpha", "sc1", ["t1", "t2"]),
        _fam("fam_b", "Beta", "sc2", ["t3"]),
        _fam("fam_c", "Gamma", "sc1", ["t4", "t5"]),
    ]
    return FamilyRegistry(fams, cache=_STUB_CACHE()), ToolRegistry(tools, cache=_STUB_CACHE())


# Valid supercategory ids for synthetic tables (sc1/sc2 are not real); patch the validator
# by using REAL supercategory ids in mechanic tests.
_SC_A = "computational_statistical"
_SC_B = "molecular_biochem_reagents"


def _real_fixture():
    tools = [
        _tool("t1", _SC_A, "fam_a"), _tool("t2", _SC_A, "fam_a"),
        _tool("t3", _SC_B, "fam_b"),
        _tool("t4", _SC_B, "fam_c"), _tool("t5", _SC_B, "fam_c"),
    ]
    fams = [
        _fam("fam_a", "Alpha", _SC_A, ["t1", "t2"]),
        _fam("fam_b", "Beta", _SC_B, ["t3"]),
        _fam("fam_c", "Gamma", _SC_B, ["t4", "t5"]),
    ]
    return FamilyRegistry(fams, cache=_STUB_CACHE()), ToolRegistry(tools, cache=_STUB_CACHE())


def test_reroute_moves_family_and_every_member_tool():
    fams, tools = _real_fixture()
    _apply_tables(fams, tools, [("fam_a", _SC_B, "why")], [], [])
    assert fams.get("fam_a")["supercategory"] == _SC_B
    assert tools.get("t1")["supercategory"] == _SC_B
    assert tools.get("t2")["supercategory"] == _SC_B


def test_relabel_changes_label_only():
    fams, tools = _real_fixture()
    _apply_tables(fams, tools, [], [("fam_a", "Renamed", "why")], [])
    assert fams.get("fam_a")["label"] == "Renamed"
    assert fams.get("fam_a")["supercategory"] == _SC_A  # unchanged


def test_merge_drops_family_repoints_tools_keeps_survivor():
    fams, tools = _real_fixture()
    _apply_tables(fams, tools, [], [], [("fam_c", "fam_b", "why")])  # both _SC_B
    assert fams.get("fam_c") is None
    keep = fams.get("fam_b")
    assert set(keep["member_tool_ids"]) == {"t3", "t4", "t5"}
    assert tools.get("t4")["member_of_family"] == "fam_b"
    assert tools.get("t5")["member_of_family"] == "fam_b"


def test_merge_after_reroute_lands_same_supercat():
    fams, tools = _real_fixture()
    # fam_a (_SC_A) → _SC_B, then merge into fam_b (_SC_B): must succeed in one pass.
    deltas = _apply_tables(fams, tools, [("fam_a", _SC_B, "why")], [], [("fam_a", "fam_b", "why")])
    assert fams.get("fam_a") is None
    assert set(fams.get("fam_b")["member_tool_ids"]) == {"t1", "t2", "t3"}
    assert [d["op"] for d in deltas] == ["reroute", "merge"]


def test_validate_raises_on_stale_id():
    fams, tools = _real_fixture()
    with pytest.raises(ValueError, match="unknown family_id"):
        _apply_tables(fams, tools, [("fam_missing", _SC_B, "why")], [], [])


def test_validate_raises_on_bad_supercategory_target():
    fams, tools = _real_fixture()
    with pytest.raises(ValueError, match="not valid supercategor"):
        _apply_tables(fams, tools, [("fam_a", "not_a_bucket", "why")], [], [])


def test_merge_cross_supercategory_without_reroute_raises():
    fams, tools = _real_fixture()
    # fam_a is _SC_A, fam_b is _SC_B; merging without a reroute must fail loud.
    with pytest.raises(ValueError, match="crosses supercategory"):
        _apply_tables(fams, tools, [], [], [("fam_a", "fam_b", "why")])


def test_double_apply_raises_because_drops_are_gone():
    fams, tools = _real_fixture()
    _apply_tables(fams, tools, [], [], [("fam_c", "fam_b", "why")])
    with pytest.raises(ValueError, match="unknown family_id"):
        _apply_tables(fams, tools, [], [], [("fam_c", "fam_b", "why")])


# --- the real frozen tables -------------------------------------------------

def test_real_tables_are_the_documented_size():
    assert len(REROUTES) == 14
    assert len(RELABELS) == 4
    assert len(MERGES) == 16


def test_real_merge_drops_are_unique_and_not_also_keeps():
    drops = [d for d, _, _ in MERGES]
    keeps = {k for _, k, _ in MERGES}
    assert len(drops) == len(set(drops)), "a family is dropped twice"
    assert not (set(drops) & keeps), "a merge keep is also a merge drop (chained merge)"


def test_real_relabel_targets_are_not_merged_away():
    # A relabeled family must survive (not be a merge drop) — else the relabel is moot.
    relabeled = {fid for fid, _, _ in RELABELS}
    drops = {d for d, _, _ in MERGES}
    assert not (relabeled & drops)


def test_real_tables_validate_against_v6_registry_snapshot_if_present():
    # The override table targets the PRE-override (v6) family set, so validate against
    # the v6 backup snapshot — NOT the live registry, which becomes v7 (drops gone) once
    # the batch has run. Drift detector: fails if a table edit names a stale v6 id.
    path = Path("out/tools/a2/_v6_backup/family_registry.json")
    if not path.exists():
        pytest.skip("v6 family_registry.json snapshot not present (gitignored artifact)")
    fams = FamilyRegistry(json.loads(path.read_text())["families"], cache=_STUB_CACHE())
    _validate(fams, REROUTES, RELABELS, MERGES)  # raises if any id drifted

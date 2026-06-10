"""Tests for the accreting ad-hoc dedup batch (pipeline_tools.family_adhoc_dedup).

Mechanics on synthetic registries via ``apply_adhoc_dedup`` pointed at a temp config
(it carries reroutes, unlike v820); the bundled ``config/family_adhoc_dedup.json`` is
checked for well-formedness and (when the live registry is present) that every
referenced id exists in the post-v820 family set.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline_tools.embeddings import EmbeddingCache
from pipeline_tools.family_adhoc_dedup import _DATA_PATH, _load, apply_adhoc_dedup
from pipeline_tools.registry import FamilyRegistry, ToolRegistry

_STUB = lambda: EmbeddingCache(embed=lambda ts: [[0.5, 0.5] for _ in ts])  # noqa: E731
_SC_A = "computational_statistical"
_SC_B = "clinical_instruments_assays"


def _tool(cid, sc, fam):
    return {"canonical_tool_id": cid, "display_name": cid, "disposition": "method_tool",
            "kind": "method", "supercategory": sc, "member_of_family": fam, "pub_ids": ["p1"]}


def _fam(fid, label, sc, members, display="standard"):
    return {"family_id": fid, "label": label, "supercategory": sc, "dominant_kind": "method",
            "member_tool_ids": list(members), "exemplar_tool_ids": list(members)[:3],
            "status": "active", "member_display_names": list(members), "display": display}


def _fixture(**fam_kwargs):
    tools = [_tool("t1", _SC_A, "fam_a"), _tool("t2", _SC_B, "fam_b"),
             _tool("t3", _SC_B, "fam_c")]
    fams = [_fam("fam_a", "Survey instruments", _SC_A, ["t1"], **fam_kwargs.get("fam_a", {})),
            _fam("fam_b", "Survey instruments", _SC_B, ["t2"], **fam_kwargs.get("fam_b", {})),
            _fam("fam_c", "Other", _SC_B, ["t3"], **fam_kwargs.get("fam_c", {}))]
    return FamilyRegistry(fams, cache=_STUB()), ToolRegistry(tools, cache=_STUB())


def _write(tmp_path, *, reroutes=(), relabels=(), merges=(), tiers=None):
    cfg = {"_meta": {}, "reroutes": list(reroutes), "relabels": list(relabels),
           "merges": list(merges), "tiers": dict(tiers or {})}
    p = tmp_path / "adhoc.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p


def test_cross_supercategory_dedup_via_reroute(tmp_path):
    """The defining capability: reroute the loser into the keep's bucket, then merge."""
    # fam_a (#A) is the identical-label cross-SC twin of fam_b (#B). Reroute a -> #B, merge a -> b.
    cfg = _write(tmp_path,
                 reroutes=[["fam_a", _SC_B, "land in the survivor's bucket"]],
                 relabels=[["fam_b", "Survey & questionnaire instruments", "umbrella"]],
                 merges=[["fam_a", "fam_b", "high", "identical-label cross-SC dup"]])
    fr, tr = _fixture()
    deltas = apply_adhoc_dedup(fr, tr, data_path=cfg)
    assert fr.get("fam_a") is None                                   # absorbed
    assert fr.get("fam_b")["supercategory"] == _SC_B
    assert fr.get("fam_b")["label"] == "Survey & questionnaire instruments"
    assert set(fr.get("fam_b")["member_tool_ids"]) == {"t1", "t2"}   # members moved
    assert tr.get("t1")["member_of_family"] == "fam_b"               # tool repointed
    assert tr.get("t1")["supercategory"] == _SC_B                    # reroute moved tool bucket
    assert {d["op"] for d in deltas} == {"reroute", "relabel", "merge"}


def test_relabel_only_disambiguation(tmp_path):
    """Relabel-to-disambiguate verdict: change labels, merge nothing (genuine siblings)."""
    cfg = _write(tmp_path, relabels=[["fam_b", "Diagnostic", "split"], ["fam_c", "Therapeutic", "split"]])
    fr, tr = _fixture()
    apply_adhoc_dedup(fr, tr, data_path=cfg)
    assert len(fr) == 3                                              # nothing merged
    assert fr.get("fam_b")["label"] == "Diagnostic"
    assert fr.get("fam_c")["label"] == "Therapeutic"


def test_stale_id_raises(tmp_path):
    cfg = _write(tmp_path, merges=[["fam_zzz", "fam_b", "high", "stale"]])
    fr, tr = _fixture()
    with pytest.raises(ValueError, match="unknown family_id"):
        apply_adhoc_dedup(fr, tr, data_path=cfg)


def test_cross_supercategory_merge_without_reroute_raises(tmp_path):
    """A merge whose pair still crosses supercategories (missing reroute) fails loud."""
    cfg = _write(tmp_path, merges=[["fam_a", "fam_b", "high", "no reroute -> still #A vs #B"]])
    fr, tr = _fixture()
    with pytest.raises(ValueError, match="crosses supercategory"):
        apply_adhoc_dedup(fr, tr, data_path=cfg)


def test_bad_reroute_target_raises(tmp_path):
    cfg = _write(tmp_path, reroutes=[["fam_a", "not_a_supercategory", "bad"]])
    fr, tr = _fixture()
    with pytest.raises(ValueError, match="not valid supercategories"):
        apply_adhoc_dedup(fr, tr, data_path=cfg)


def test_double_apply_raises(tmp_path):
    cfg = _write(tmp_path, merges=[["fam_b", "fam_c", "high", "same #B"]])
    fr, tr = _fixture()
    apply_adhoc_dedup(fr, tr, data_path=cfg)
    with pytest.raises(ValueError, match="unknown family_id"):  # drop id is gone on 2nd apply
        apply_adhoc_dedup(fr, tr, data_path=cfg)


def test_tier_override_bumps_survivor(tmp_path):
    """A post-merge tier correction sets display on the surviving family."""
    cfg = _write(tmp_path, merges=[["fam_b", "fam_c", "high", "same #B"]],
                 tiers={"fam_c": "feature"})
    fr, tr = _fixture()
    deltas = apply_adhoc_dedup(fr, tr, data_path=cfg)
    assert fr.get("fam_c")["display"] == "feature"          # bumped
    assert fr.get("fam_a")["display"] == "standard"         # untouched
    assert any(d["op"] == "tier" and d["family_id"] == "fam_c" for d in deltas)


def test_tier_override_on_nonsurvivor_raises(tmp_path):
    """Tiering a merge drop (gone after the merge) fails loud."""
    cfg = _write(tmp_path, merges=[["fam_b", "fam_c", "high", "same #B"]],
                 tiers={"fam_b": "feature"})  # fam_b is absorbed
    fr, tr = _fixture()
    with pytest.raises(ValueError, match="non-surviving family"):
        apply_adhoc_dedup(fr, tr, data_path=cfg)


def test_invalid_tier_value_raises(tmp_path):
    cfg = _write(tmp_path, tiers={"fam_a": "BOGUS"})
    fr, tr = _fixture()
    with pytest.raises(ValueError, match="invalid display tier"):
        apply_adhoc_dedup(fr, tr, data_path=cfg)


def test_survivor_without_display_tier_raises(tmp_path):
    """If the set was tiered upstream, a survivor missing a display tier fails loud."""
    cfg = _write(tmp_path, relabels=[["fam_b", "X", "y"]])
    # fam_c survives but carries no display tier while a/b do -> half-tiered set.
    fr, tr = _fixture(fam_c={"display": None})
    fr.get("fam_c").pop("display")
    with pytest.raises(ValueError, match="lost their display tier"):
        apply_adhoc_dedup(fr, tr, data_path=cfg)


# --- the bundled batch-1 data --------------------------------------------------

def test_bundled_data_wellformed():
    reroutes, relabels, merges, tiers = _load()
    meta = json.loads(_DATA_PATH.read_text())["_meta"]["counts"]
    assert len(reroutes) == meta["reroutes"]
    assert len(relabels) == meta["relabels"]
    assert len(merges) == meta["merges"]
    assert len(tiers) == meta.get("tiers", 0)
    assert all(t in {"feature", "standard", "suppressed"} for t in tiers.values())
    absorbs = {d for d, _, _ in merges}
    keeps = {k for _, k, _ in merges}
    assert not (absorbs & keeps), "a family is both absorbed and a merge keep (chain)"
    assert len(absorbs) == len(merges), "a family is absorbed more than once"
    # every cross-SC merge has a preceding reroute landing the drop in the keep's bucket.
    rerouted = {fid for fid, _, _ in reroutes}
    # batch-1 invariant: the two reroutes are exactly the two cross-SC merge drops.
    assert rerouted <= absorbs, "a reroute targets a family that is not a merge drop"


def test_bundled_ids_exist_in_live_registry_if_present():
    """Drift check, robust to whether the registry is pre- or post-adhoc.

    Once the layer has run (e.g. after a corpus re-run with apply_adhoc_dedup),
    the merge DROP ids are legitimately absent from the saved registry. Any OTHER
    referenced id missing is real drift (a renamed/removed survivor) — that fails.
    """
    path = Path("out/tools/a2/family_registry.json")
    if not path.exists():
        pytest.skip("live family_registry.json not present")
    live = {f["family_id"] for f in json.loads(path.read_text())["families"]}
    reroutes, relabels, merges, tiers = _load()
    drops = {d for d, _, _ in merges}
    referenced = ({fid for fid, _, _ in reroutes} | {fid for fid, _, _ in relabels}
                  | drops | {k for _, k, _ in merges} | set(tiers))
    missing = referenced - live
    assert not (missing - drops), \
        f"adhoc batch references non-drop ids absent from the live registry: {sorted(missing - drops)[:10]}"
    # tier targets must be survivors (never a merge drop).
    assert not (set(tiers) & drops), "a tier override targets a merge drop"

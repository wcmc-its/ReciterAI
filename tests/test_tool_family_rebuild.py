"""Tests for capability-class family formation (broaden -> consolidate -> group)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline_tools.embeddings import EmbeddingCache
from pipeline_tools.family_rebuild import (
    broaden_with_cache, consolidate_classes, form_families, reconcile_classes,
)


def _tool(cid, name, sc="therapeutics_interventions", kind="reagent", pubs=1):
    return {"canonical_tool_id": cid, "display_name": name, "disposition": "method_tool",
            "supercategory": sc, "kind": kind, "pub_ids": [f"p{i}" for i in range(pubs)]}


def _last_array(user):
    return json.loads(user[user.rindex("["):user.rindex("]") + 1])


def _stub(broaden_map=None, reconcile_map=None):
    """Combined LLM stub: broaden assigns family_class; reconcile maps label->canonical.

    Branches on the system prompt (only the reconcile prompt mentions "canonical"),
    so one call_json serves both passes form_families makes.
    """
    broaden_map, reconcile_map = broaden_map or {}, reconcile_map or {}

    def call_json(system, user):
        labels = _last_array(user)
        if "canonical" in system:  # reconcile pass
            return {"assignments": [{"label": n, "canonical": reconcile_map.get(n, n)} for n in labels]}
        return {"assignments": [{"label": n, "family_class": broaden_map.get(n, n)} for n in labels]}
    return call_json


def test_form_families_groups_same_class_different_name():
    # the core fix: nivolumab + pembrolizumab (dissimilar names) share a class -> one family.
    tools = [_tool("t1", "Nivolumab", pubs=5), _tool("t2", "Pembrolizumab", pubs=3),
             _tool("t3", "Metformin", pubs=2)]
    broaden = {"Nivolumab": "anti-PD-1 immunotherapy", "Pembrolizumab": "anti-PD-1 immunotherapy",
               "Metformin": "biguanide antidiabetics"}
    vecs = {"anti-PD-1 immunotherapy": [1.0, 0.0], "biguanide antidiabetics": [0.0, 1.0]}
    cache = EmbeddingCache(embed=lambda ts: [vecs.get(t, [0.5, 0.5]) for t in ts])

    reg, mapping = form_families(tools, call_json=_stub(broaden_map=broaden), embed_cache=cache)
    assert len(reg) == 2
    assert mapping["t1"] == mapping["t2"] != mapping["t3"]
    pd1 = reg.get(mapping["t1"])
    assert pd1["label"] == "anti-PD-1 immunotherapy"
    assert pd1["exemplar_tool_ids"][0] == "t1"  # ranked by pub_ids (5 > 3)
    assert pd1["dominant_kind"] == "reagent"


def test_reconcile_merges_wording_variants_the_cosine_missed():
    # the residual A2 problem: broaden minted variant labels for the SAME class across
    # batches; reconcile (LLM) collapses them where a fixed cosine could not.
    tools = [_tool("t1", "A", sc="animal_cell_models"), _tool("t2", "B", sc="animal_cell_models")]
    raw = {"t1": "transgenic mouse models", "t2": "genetically engineered mouse models"}
    reconcile_map = {"genetically engineered mouse models": "transgenic mouse models"}
    canon = reconcile_classes(raw, tools, call_json=_stub(reconcile_map=reconcile_map), batch_size=10)
    assert canon["t1"] == canon["t2"] == "transgenic mouse models"


def test_reconcile_keeps_distinct_targets_separate():
    # the over-merge guard: sibling targets differ by one token but must NOT merge.
    tools = [_tool("t1", "A"), _tool("t2", "B")]
    raw = {"t1": "anti-PD-1 immunotherapy", "t2": "anti-PD-L1 immunotherapy"}
    canon = reconcile_classes(raw, tools, call_json=_stub(), batch_size=10)  # stub maps to self
    assert canon["t1"] != canon["t2"]


def test_reconcile_accretion_passes_prior_vocab_across_batches():
    # batch_size=1 forces three sequential calls within one supercategory; a variant
    # in a later batch must reconcile against a canonical established earlier.
    tools = [_tool(f"t{i}", n, sc="animal_cell_models")
             for i, n in enumerate(["A", "B", "C"])]
    raw = {"t0": "transgenic mouse models", "t1": "knockout mouse models",
           "t2": "genetically engineered mouse models"}
    seen_vocabs = []

    def call_json(system, user):
        vocab_arr = json.loads(user[user.index("["):user.index("]") + 1]) if "VERBATIM" in user else []
        labels = _last_array(user)
        seen_vocabs.append(vocab_arr)
        # collapse the third variant onto the first canonical once it's in the vocab
        out = {}
        for n in labels:
            out[n] = "transgenic mouse models" if "engineered" in n else n
        return {"assignments": [{"label": n, "canonical": out[n]} for n in labels]}

    canon = reconcile_classes(raw, tools, call_json=call_json, batch_size=1)
    assert canon["t0"] == canon["t2"] == "transgenic mouse models"  # variant folded in
    assert canon["t1"] == "knockout mouse models"  # distinct class preserved
    # by the final batch the accreted vocabulary carried the earlier canonicals
    assert "transgenic mouse models" in seen_vocabs[-1]


def test_broaden_with_cache_roundtrip(tmp_path):
    tools = [_tool("t1", "Nivolumab"), _tool("t2", "Pembrolizumab")]
    broaden = {"Nivolumab": "anti-PD-1 immunotherapy", "Pembrolizumab": "anti-PD-1 immunotherapy"}
    first = broaden_with_cache(tools, call_json=_stub(broaden_map=broaden),
                               batch_size=10, checkpoint_dir=tmp_path)
    assert first == {"t1": "anti-PD-1 immunotherapy", "t2": "anti-PD-1 immunotherapy"}
    assert (tmp_path / "broaden_cache.json").exists()

    def boom(system, user):
        raise AssertionError("must not re-broaden when cached")

    second = broaden_with_cache(tools, call_json=boom, batch_size=10, checkpoint_dir=tmp_path)
    assert second == first  # served entirely from cache, $0


def test_consolidate_merges_near_duplicate_class_wording():
    tools = [_tool("t1", "A"), _tool("t2", "B")]
    raw = {"t1": "kinase inhibitors", "t2": "kinase inhibitor therapeutics"}
    vecs = {"kinase inhibitors": [1.0, 0.0, 0.0], "kinase inhibitor therapeutics": [0.985, 0.02, 0.0]}
    cache = EmbeddingCache(embed=lambda ts: [vecs[t] for t in ts])
    canon = consolidate_classes(raw, tools, embed_cache=cache, cosine=0.92)
    assert canon["t1"] == canon["t2"] == "kinase inhibitors"  # shorter canonical wins the tie


def test_consolidate_keeps_distinct_classes_separate():
    tools = [_tool("t1", "A"), _tool("t2", "B")]
    raw = {"t1": "anti-PD-1 immunotherapy", "t2": "anti-IL-6 anti-inflammatory therapy"}
    vecs = {"anti-PD-1 immunotherapy": [1.0, 0.0], "anti-IL-6 anti-inflammatory therapy": [0.0, 1.0]}
    cache = EmbeddingCache(embed=lambda ts: [vecs[t] for t in ts])
    canon = consolidate_classes(raw, tools, embed_cache=cache, cosine=0.92)
    assert canon["t1"] != canon["t2"]  # orthogonal classes never merge


def test_broaden_failure_keeps_specific_name():
    tools = [_tool("t1", "ExoticTool")]

    def boom(system, user):
        raise RuntimeError("LLM down")

    cache = EmbeddingCache(embed=lambda ts: [[1.0, 0.0] for _ in ts])
    reg, mapping = form_families(tools, call_json=boom, embed_cache=cache)
    assert reg.get(mapping["t1"])["label"] == "ExoticTool"  # falls back to the name, not dropped

"""Tests for capability-class family formation (broaden -> consolidate -> group)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline_tools.embeddings import EmbeddingCache
from pipeline_tools.family_rebuild import consolidate_classes, form_families


def _tool(cid, name, sc="therapeutics_interventions", kind="reagent", pubs=1):
    return {"canonical_tool_id": cid, "display_name": name, "disposition": "method_tool",
            "supercategory": sc, "kind": kind, "pub_ids": [f"p{i}" for i in range(pubs)]}


def _broaden_stub(classmap):
    def call_json(system, user):
        names = json.loads(user[user.index("["):])
        return {"assignments": [{"label": n, "family_class": classmap.get(n, n)} for n in names]}
    return call_json


def test_form_families_groups_same_class_different_name():
    # the core fix: nivolumab + pembrolizumab (dissimilar names) share a class -> one family.
    tools = [_tool("t1", "Nivolumab", pubs=5), _tool("t2", "Pembrolizumab", pubs=3),
             _tool("t3", "Metformin", pubs=2)]
    classmap = {"Nivolumab": "anti-PD-1 immunotherapy", "Pembrolizumab": "anti-PD-1 immunotherapy",
                "Metformin": "biguanide antidiabetics"}
    vecs = {"anti-PD-1 immunotherapy": [1.0, 0.0], "biguanide antidiabetics": [0.0, 1.0]}
    cache = EmbeddingCache(embed=lambda ts: [vecs.get(t, [0.5, 0.5]) for t in ts])

    reg, mapping = form_families(tools, call_json=_broaden_stub(classmap), embed_cache=cache)
    assert len(reg) == 2
    assert mapping["t1"] == mapping["t2"] != mapping["t3"]
    pd1 = reg.get(mapping["t1"])
    assert pd1["label"] == "anti-PD-1 immunotherapy"
    assert pd1["exemplar_tool_ids"][0] == "t1"  # ranked by pub_ids (5 > 3)
    assert pd1["dominant_kind"] == "reagent"


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

"""Tests for the A2 corpus orchestrator — grouping, grant separation, D-06, rollup."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline_tools import vocab
from pipeline_tools.corpus_run import group_mentions, run_corpus
from pipeline_tools.embeddings import EmbeddingCache
from pipeline_tools.registry import FamilyRegistry, ToolRegistry


def _zero_embed(texts):
    """No-NN embed: zero vectors -> cosine 0 -> matching falls to surface keys only."""
    return [[0.0, 0.0] for _ in texts]


def _mention(raw, pmid, cwid, role="lead", source="publication", cat=None, ctx="ctx"):
    return {"raw_name": raw, "tool_category": cat, "context": ctx, "confidence": "high",
            "pmid": pmid, "source_kind": source, "authors": [{"cwid": cwid, "author_role": role}]}


# --- group_mentions --------------------------------------------------------

def test_group_dedups_by_norm_and_explodes_occurrences():
    ms = [
        _mention("scRNA-seq", "p1", "facA"),
        _mention("scRNA-seq", "p2", "facB", role="senior"),
        _mention("grant tool", "grant:g1", "facC", role="investigator", source="grant"),
    ]
    uniques = {u.norm_key: u for u in group_mentions(ms)}
    sc = uniques["scrna seq"]
    assert sc.pub_count == 2  # two distinct publication pmids
    assert len(sc.occurrences) == 2
    grant = uniques["grant tool"]
    assert grant.pub_count == 0  # grant pmid excluded from the pub-filter prominence


# --- run_corpus ------------------------------------------------------------

def _stub_classify(dispmap):
    """call_json that classifies by raw_name via dispmap; relabel branch returns labels."""
    import json as _json

    def call_json(system, user):
        items = _json.loads(user[user.index("["):])
        if "method FAMILIES" in system:
            return {"families": [{"family_id": it["family_id"],
                                  "label": f"fam-{it['family_id'][-4:]}", "confidence": "high"}
                                 for it in items]}
        out = []
        for it in items:
            spec = dispmap.get(it["raw_name"], {"disposition": "method_tool", "kind": "method",
                                                "supercategory": "computational_statistical"})
            out.append({"raw_name": it["raw_name"], "attributes": {"rrid_candidate": False},
                        "confidence": "high", **spec})
        return {"classifications": out}

    return call_json


def _seed_registries():
    cache = EmbeddingCache(embed=_zero_embed)
    seed_tool = {
        "canonical_tool_id": "tool_000001", "display_name": "MRI", "aliases": ["MRI"],
        "disposition": "method_tool", "kind": "instrument", "supercategory": "imaging_image_analysis",
        "attributes": vocab.default_attributes(), "salience_tier": "A",
        "salience_tier_basis": "llm_provisional", "member_of_family": "fam_0001",
        "pub_ids": [], "pub_count": 5, "context_evidence": [],
    }
    tools = ToolRegistry([seed_tool], cache=cache)
    fams = FamilyRegistry([{"family_id": "fam_0001", "label": "MRI", "supercategory": "imaging_image_analysis",
                            "dominant_kind": "instrument", "member_tool_ids": ["tool_000001"],
                            "exemplar_tool_ids": ["tool_000001"], "status": "provisional",
                            "member_display_names": ["MRI"]}], cache=cache)
    return tools, fams


def test_run_corpus_preserves_seed_ids_and_grounds_real_pub_signal():
    tools, fams = _seed_registries()
    dispmap = {
        "MRI": {"disposition": "method_tool", "kind": "instrument", "supercategory": "imaging_image_analysis"},
        "scRNA-seq": {"disposition": "method_tool", "kind": "method", "supercategory": "genomics_sequencing"},
        "GrantOnlyTool": {"disposition": "method_tool", "kind": "method", "supercategory": "computational_statistical"},
        "junk": {"disposition": "excluded", "kind": None, "supercategory": None},
    }
    ms = [
        _mention("MRI", "p1", "facA"),                                   # attaches to seed tool_000001
        _mention("scRNA-seq", "p1", "facA"),
        _mention("scRNA-seq", "p2", "facB", role="senior"),             # spread 2
        _mention("GrantOnlyTool", "grant:g1", "facC", role="investigator", source="grant"),
        _mention("junk", "p3", "facA"),                                  # excluded -> denylist
    ]
    res = run_corpus(ms, call_json=_stub_classify(dispmap), tool_registry=tools, family_registry=fams,
                     force_c_terms=[], relabel=True)

    by_name = {r["display_name"]: r for r in res.records}
    # D-06: MRI stayed tool_000001 and got real A2 pub evidence.
    mri = tools.get("tool_000001")
    assert mri["pub_ids"] == ["p1"]
    assert mri["salience_tier_basis"] == "grounded"
    # scRNA-seq minted fresh, real pub_ids accreted, grounded.
    scrna = by_name["scRNA-seq"]
    assert scrna["pub_count"] == 2
    assert scrna["salience_tier_basis"] == "grounded"
    # junk excluded — denied, never minted.
    assert tools.is_denied("junk")
    assert "junk" not in {r["display_name"] for r in res.records}


def test_grants_stay_out_of_pub_filter_but_feed_grant_signal():
    tools, fams = _seed_registries()
    dispmap = {"GrantOnlyTool": {"disposition": "method_tool", "kind": "method",
                                 "supercategory": "computational_statistical"}}
    ms = [_mention("GrantOnlyTool", "grant:g1", "facC", role="investigator", source="grant"),
          _mention("GrantOnlyTool", "grant:g2", "facD", role="investigator", source="grant")]
    res = run_corpus(ms, call_json=_stub_classify(dispmap), tool_registry=tools, family_registry=fams,
                     force_c_terms=[], relabel=False)
    grant_rec = next(r for r in res.records if r["display_name"] == "GrantOnlyTool")
    cid = grant_rec["canonical_tool_id"]
    assert grant_rec["pub_count"] == 0                       # no pub_ids from grants
    assert cid in res.grant_signal
    assert set(res.grant_signal[cid]["appl_ids"]) == {"g1", "g2"}
    assert set(res.grant_signal[cid]["investigator_cwids"]) == {"facC", "facD"}
    # grant-only faculty never appear in the publication rollup.
    assert "facC" not in res.faculty_rollup and "facD" not in res.faculty_rollup


def test_faculty_rollup_built_from_publication_occurrences():
    tools, fams = _seed_registries()
    dispmap = {"MRI": {"disposition": "method_tool", "kind": "instrument",
                       "supercategory": "imaging_image_analysis"}}
    ms = [_mention("MRI", "p1", "facA"), _mention("MRI", "p2", "facA")]
    res = run_corpus(ms, call_json=_stub_classify(dispmap), tool_registry=tools, family_registry=fams,
                     force_c_terms=[], relabel=False)
    assert "facA" in res.faculty_rollup
    fac = res.faculty_rollup["facA"]
    mri_row = next(t for t in fac["tools"] if t["canonical_tool_id"] == "tool_000001")
    assert mri_row["pub_count"] == 2

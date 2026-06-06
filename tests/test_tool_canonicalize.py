"""Unit tests for pipeline_tools.canonicalize pure logic (no Bedrock, no I/O).

Covers slugging, alias matching, entry shaping into the #8 TOOL# schema,
cross-batch merge, suppress-list enforcement, coverage QC, and finalize sorting.
The LLM passes are not exercised here (they need Bedrock); these tests pin the
deterministic post-processing that guards the artifact quality.
"""

from __future__ import annotations

import pipeline_tools.canonicalize as c
from utils.bedrock_client import MODEL_IDS_BY_STAGE


RAW = [
    {"raw_name": "Magnetic resonance imaging (MRI) scanner", "tool_category": "instrument", "pub_count": 191},
    {"raw_name": "3 Tesla MRI scanner", "tool_category": "instrument", "pub_count": 9},
    {"raw_name": "CRISPR-Cas9", "tool_category": "reagent", "pub_count": 25},
    {"raw_name": "PubMed", "tool_category": "dataset_public", "pub_count": 50},
]
LOOKUP = c.build_raw_lookup(RAW)


# ---------- slug / normalize ----------

def test_slugify():
    assert c.slugify("MRI Scanner") == "mri_scanner"
    assert c.slugify("LC–MS/MS") == "lc_ms_ms"
    assert c.slugify("   ") == "unknown"


def test_norm_name_is_punct_and_case_insensitive():
    assert c.norm_name("Magnetic resonance imaging (MRI) scanner") == "magnetic resonance imaging mri scanner"
    assert c.norm_name("MRI-scanner") == c.norm_name("MRI scanner")


def test_build_raw_lookup_keys_on_normalized_name():
    assert c.norm_name("3 Tesla MRI scanner") in LOOKUP


# ---------- suppress matching ----------

def test_matches_suppress_whole_word():
    terms = ["PubMed", "t-test"]
    assert c.matches_suppress("PubMed", terms)
    assert c.matches_suppress("searched in PubMed database", terms)
    # substring inside another word must NOT match
    assert not c.matches_suppress("pubmedex special tool", terms)


# ---------- normalize_entry ----------

def test_normalize_entry_shapes_schema_and_rolls_up_pub_count():
    e = c.normalize_entry(
        {
            "canonical_tool_id": "mri_scanner",
            "display_name": "MRI Scanner",
            "kind": "instrument",
            "functional_category": "Imaging",
            "supercategory": "imaging_image_analysis",
            "salience_tier": "A",
            "aliases": ["Magnetic resonance imaging (MRI) scanner", "3 Tesla MRI scanner"],
            "source_confidence": 0.92,
            "description": "MRI imaging instrument.",
        },
        LOOKUP,
    )
    assert e["canonical_tool_id"] == "wcm_tool_mri_scanner"
    assert e["kind"] == "instrument"
    assert e["functional_category"] == "imaging"
    assert e["salience_tier"] == "A"
    assert e["salience_tier_basis"] == "llm_provisional"
    assert e["parent_tool_ids"] == []
    assert e["source"] == "wcm_curated" and e["source_uri"] is None
    assert e["pub_count"] == 191 + 9  # both aliases resolved against the seed
    assert e["rrid_candidate"] is False  # instrument is not an RRID-candidate kind


def test_normalize_entry_flags_rrid_candidate_for_reagents():
    e = c.normalize_entry(
        {"canonical_tool_id": "crispr_cas9", "display_name": "CRISPR-Cas9", "kind": "reagent",
         "aliases": ["CRISPR-Cas9"], "salience_tier": "S", "supercategory": "molecular_biochem_reagents"},
        LOOKUP,
    )
    assert e["rrid_candidate"] is True


def test_normalize_entry_coerces_bad_enums():
    e = c.normalize_entry(
        {"canonical_tool_id": "x", "display_name": "X tool", "kind": "bogus",
         "salience_tier": "Z", "supercategory": "nope", "aliases": ["x tool"], "source_confidence": "high"},
        LOOKUP,
    )
    assert e["kind"] == "method"          # invalid kind -> default
    assert e["salience_tier"] == "B"       # invalid tier -> default
    assert e["supercategory"] == "other"   # invalid supercat -> other
    assert e["source_confidence"] == 0.5   # unparseable confidence -> default


def test_normalize_entry_drops_unusable():
    assert c.normalize_entry({"display_name": "", "aliases": []}, LOOKUP) is None
    assert c.normalize_entry({"display_name": "No aliases", "aliases": []}, LOOKUP) is None


# ---------- merge_by_id ----------

def test_merge_by_id_unions_aliases_and_keeps_signature_tier():
    e1 = c.normalize_entry({"canonical_tool_id": "mri_scanner", "display_name": "MRI Scanner",
                            "kind": "instrument", "salience_tier": "B",
                            "aliases": ["Magnetic resonance imaging (MRI) scanner"]}, LOOKUP)
    e2 = c.normalize_entry({"canonical_tool_id": "mri_scanner", "display_name": "MRI Scanner",
                            "kind": "instrument", "salience_tier": "A",
                            "aliases": ["3 Tesla MRI scanner"]}, LOOKUP)
    merged = c.merge_by_id([e1, e2], LOOKUP)
    assert len(merged) == 1
    m = merged[0]
    assert set(m["aliases"]) == {"Magnetic resonance imaging (MRI) scanner", "3 Tesla MRI scanner"}
    assert m["salience_tier"] == "A"        # A is more signature than B
    assert m["pub_count"] == 191 + 9        # recomputed from unioned aliases, no double-count


# ---------- apply_suppress ----------

def test_apply_suppress_forces_tier_c():
    e = c.normalize_entry({"canonical_tool_id": "pubmed", "display_name": "PubMed", "kind": "dataset",
                           "salience_tier": "A", "aliases": ["PubMed"]}, LOOKUP)
    out = c.apply_suppress([e], ["PubMed"])
    assert out[0]["salience_tier"] == "C"
    assert out[0]["salience_tier_basis"] == "suppress_list"


def test_apply_suppress_matches_display_name_only_not_aliases():
    """A distinctive instrument whose verbose ALIAS contains a generic word must
    NOT be suppressed — only the clean display_name is matched."""
    e = c.normalize_entry({"canonical_tool_id": "mri_scanner", "display_name": "MRI Scanner",
                           "kind": "instrument", "salience_tier": "A",
                           "aliases": ["Magnetic resonance imaging (MRI) scanner"]}, LOOKUP)
    out = c.apply_suppress([e], ["imaging"])
    assert out[0]["salience_tier"] == "A"  # alias contains "imaging" but display_name does not


def test_normalize_entry_id_is_idempotent():
    """Re-normalizing an already-prefixed id (consolidation pass) must not double it."""
    e = c.normalize_entry({"canonical_tool_id": "wcm_tool_scrna_seq", "display_name": "scRNA-seq",
                           "kind": "method", "salience_tier": "S", "aliases": ["scRNA-seq"]}, LOOKUP)
    assert e["canonical_tool_id"] == "wcm_tool_scrna_seq"


# ---------- coverage + finalize ----------

def test_coverage_stats_counts_unmatched():
    e = c.normalize_entry({"canonical_tool_id": "mri_scanner", "display_name": "MRI Scanner",
                           "kind": "instrument", "salience_tier": "A",
                           "aliases": ["Magnetic resonance imaging (MRI) scanner", "3 Tesla MRI scanner"]}, LOOKUP)
    cov = c.coverage_stats([e], LOOKUP)
    assert cov["raw_total"] == 4
    assert cov["raw_matched"] == 2          # CRISPR + PubMed unmatched
    assert cov["raw_unmatched"] == 2


def test_finalize_strips_internal_fields_and_sorts_by_tier():
    eS = c.normalize_entry({"canonical_tool_id": "a", "display_name": "A", "kind": "method",
                            "salience_tier": "C", "aliases": ["a"]}, LOOKUP)
    eA = c.normalize_entry({"canonical_tool_id": "b", "display_name": "B", "kind": "method",
                            "salience_tier": "S", "aliases": ["b"]}, LOOKUP)
    out = c.finalize([eS, eA])
    assert out[0]["salience_tier"] == "S"   # S sorts first
    assert all(not k.startswith("_") for e in out for k in e)


# ---------- stage wiring ----------

def test_model_stage_keys_present():
    assert MODEL_IDS_BY_STAGE.get("tool_canonicalize")
    assert MODEL_IDS_BY_STAGE.get("tool_extraction")

"""Unit tests for the match-or-mint registries (pipeline_tools.registry).

Canned ``embed`` only. Exercises identity resolution, accretion, the denylist,
durable opaque ids across save/load, and the §7 cross-supercategory guard.
"""

import json

import pytest

from pipeline_tools.embeddings import EmbeddingCache
from pipeline_tools.registry import FamilyRegistry, ToolRegistry, surface_keys


def _no_embed(texts):
    raise AssertionError("embedding must not be called — surface key should match first")

VECTORS = {
    "MRI scanner": [1.0, 0.0, 0.0],
    "MRI Scanner": [1.0, 0.0, 0.0],
    "magnetic resonance imaging": [0.99, 0.141, 0.0],   # ~0.99 cos with MRI scanner
    "flow cytometer": [0.0, 1.0, 0.0],                  # orthogonal to MRI
    "brand new tool": [0.0, 0.0, 1.0],                  # orthogonal to both -> mints
}


def _embed(texts):
    return [VECTORS[t] for t in texts]


def _cache():
    return EmbeddingCache(embed=_embed)


# --- ToolRegistry (§8) ------------------------------------------------------


def test_mint_creates_opaque_id_not_derived_from_name():
    reg = ToolRegistry(cache=_cache())
    rec, action = reg.match_or_mint(raw_name="MRI scanner", pub_ids=["PMID1"])
    assert action == "minted"
    assert rec["canonical_tool_id"] == "tool_000001"   # opaque, NOT "mri_scanner"
    assert rec["pub_ids"] == ["PMID1"]


def test_exact_normalized_name_attaches_without_minting():
    reg = ToolRegistry(cache=_cache())
    reg.match_or_mint(raw_name="MRI scanner", pub_ids=["PMID1"])
    rec, action = reg.match_or_mint(raw_name="MRI Scanner", pub_ids=["PMID1"])  # case variant
    assert action == "attached"
    assert len(reg) == 1
    assert rec["pub_ids"] == ["PMID1"]                 # same pub does not double-count


def test_pub_ids_accrete_as_a_set():
    reg = ToolRegistry(cache=_cache())
    reg.match_or_mint(raw_name="MRI scanner", pub_ids=["PMID1"])
    rec, _ = reg.match_or_mint(raw_name="MRI scanner", pub_ids=["PMID2", "PMID1"])
    assert rec["pub_ids"] == ["PMID1", "PMID2"]
    assert reg._serialize(rec)["pub_count"] == 2


def test_embedding_near_match_attaches_and_accretes_alias():
    reg = ToolRegistry(cache=_cache())
    reg.match_or_mint(raw_name="MRI scanner", pub_ids=["PMID1"])
    rec, action = reg.match_or_mint(raw_name="magnetic resonance imaging", pub_ids=["PMID9"])
    assert action == "attached"                        # resolved by embedding NN
    assert len(reg) == 1
    assert "magnetic resonance imaging" in rec["aliases"]
    assert rec["pub_ids"] == ["PMID1", "PMID9"]


# --- #193 per-publication context (context_by_pub) -------------------------


def test_context_by_pub_minted_then_longer_snippet_wins_on_pmid_collision():
    reg = ToolRegistry(cache=_cache())
    reg.match_or_mint(raw_name="MRI scanner", pub_ids=["P1"],
                      context_by_pub={"P1": "for imaging"})
    # Same canonical tool, same pmid re-seen with a longer (more specific) snippet,
    # plus a new pmid. Longer wins on collision; new pmid accretes.
    rec, action = reg.match_or_mint(
        raw_name="MRI scanner", pub_ids=["P1", "P2"],
        context_by_pub={"P1": "for imaging tumor margins intraoperatively", "P2": "for staging"},
    )
    assert action == "attached"
    assert rec["context_by_pub"] == {
        "P1": "for imaging tumor margins intraoperatively",  # longer snippet retained
        "P2": "for staging",
    }
    # A shorter re-sighting never displaces the longer snippet.
    rec, _ = reg.match_or_mint(raw_name="MRI scanner", pub_ids=["P1"],
                               context_by_pub={"P1": "scan"})
    assert rec["context_by_pub"]["P1"] == "for imaging tumor margins intraoperatively"


def test_context_by_pub_unions_across_surface_forms():
    # MRI scanner and "magnetic resonance imaging" resolve to ONE canonical tool
    # (embedding NN); their per-pmid context must union onto that record.
    reg = ToolRegistry(cache=_cache())
    reg.match_or_mint(raw_name="MRI scanner", pub_ids=["P1"],
                      context_by_pub={"P1": "for imaging"})
    rec, action = reg.match_or_mint(
        raw_name="magnetic resonance imaging", pub_ids=["P1", "P9"],
        context_by_pub={"P1": "for high-resolution imaging of cortical thickness", "P9": "for angiography"},
    )
    assert action == "attached" and len(reg) == 1
    assert rec["context_by_pub"] == {
        "P1": "for high-resolution imaging of cortical thickness",  # the longer of the two forms
        "P9": "for angiography",
    }


def test_context_by_pub_survives_save_load_and_is_key_sorted(tmp_path):
    reg = ToolRegistry(cache=_cache())
    reg.match_or_mint(raw_name="MRI scanner", pub_ids=["P2", "P1"],
                      context_by_pub={"P2": "for staging", "P1": "for imaging"})
    rpath = tmp_path / "tool_registry.json"
    reg.save(rpath)
    # Persisted form is key-sorted (byte-stable across content-identical reruns).
    saved = json.loads(rpath.read_text())
    assert list(saved["tools"][0]["context_by_pub"]) == ["P1", "P2"]
    reloaded = ToolRegistry.load(rpath, cache=_cache())
    assert reloaded.get("tool_000001")["context_by_pub"] == {"P1": "for imaging", "P2": "for staging"}


def test_orthogonal_name_mints_a_distinct_record():
    reg = ToolRegistry(cache=_cache())
    reg.match_or_mint(raw_name="MRI scanner")
    rec, action = reg.match_or_mint(raw_name="flow cytometer")
    assert action == "minted"
    assert rec["canonical_tool_id"] == "tool_000002"
    assert len(reg) == 2


def test_denylist_blocks_minting_excluded_mentions():
    reg = ToolRegistry(cache=_cache())
    reg.deny("Amazon Fresh")
    rec, action = reg.match_or_mint(raw_name="amazon  fresh")  # normalized match
    assert action == "denied" and rec is None
    assert len(reg) == 0


def test_save_load_round_trip_preserves_ids_and_seeds_minter(tmp_path):
    reg = ToolRegistry(cache=_cache())
    reg.match_or_mint(raw_name="MRI scanner", pub_ids=["PMID1"])
    reg.match_or_mint(raw_name="flow cytometer", pub_ids=["PMID2"])
    reg.deny("SEO tool")
    rpath = tmp_path / "tool_registry.json"
    dpath = tmp_path / "tool_denylist.json"
    reg.save(rpath, dpath)

    reloaded = ToolRegistry.load(rpath, dpath, cache=_cache())
    assert len(reloaded) == 2
    assert reloaded.get("tool_000001")["display_name"] == "MRI scanner"
    assert reloaded.get("tool_000001")["pub_ids"] == ["PMID1"]
    assert reloaded.is_denied("seo  tool")
    # Durable: the next mint continues past the reloaded high-water mark.
    rec, action = reloaded.match_or_mint(raw_name="brand new tool")
    assert action == "minted" and rec["canonical_tool_id"] == "tool_000003"


def test_serialized_record_reports_pub_count():
    reg = ToolRegistry(cache=_cache())
    rec, _ = reg.match_or_mint(raw_name="MRI scanner", pub_ids=["PMID1", "PMID2", "PMID2"])
    assert reg._serialize(rec)["pub_count"] == 2


# --- surface_keys / §8 dedup under-merge fix --------------------------------


def test_surface_keys_acronym_and_expansion():
    keys = surface_keys("Magnetic resonance imaging (MRI) scanner")
    assert "mri" in keys                          # parenthetical acronym
    assert "magnetic resonance imaging" in keys   # expansion, scanner stripped


def test_surface_keys_database_and_service_suffix():
    assert "pubmed" in surface_keys("PubMed bibliographic database")
    assert "embase" in surface_keys("Embase bibliographic database")
    assert "rna seq" in surface_keys("RNA sequencing (RNA-seq) service")  # acronym
    assert "rna sequencing" in surface_keys("RNA sequencing (RNA-seq) service")


def test_surface_keys_do_not_overmerge_distinct_tools():
    # PET vs PET/CT must NOT share a key.
    pet = surface_keys("Positron emission tomography (PET) scanner")
    petct = surface_keys("Positron emission tomography/computed tomography (PET/CT) scanner")
    assert pet.isdisjoint(petct)
    # Domain instrument head-nouns are NOT stripped -> distinct microscopes stay distinct.
    assert surface_keys("Confocal microscope").isdisjoint(surface_keys("Fluorescence microscope"))


def test_acronym_expansion_attaches_without_embedding():
    reg = ToolRegistry(cache=EmbeddingCache(embed=_no_embed))
    reg.match_or_mint(raw_name="Magnetic resonance imaging (MRI) scanner", pub_count=191)
    rec, action = reg.match_or_mint(raw_name="MRI", pub_count=5)            # bare acronym
    assert action == "attached" and len(reg) == 1
    rec2, action2 = reg.match_or_mint(raw_name="magnetic resonance imaging", pub_count=3)  # expansion
    assert action2 == "attached" and len(reg) == 1


def test_database_and_service_suffix_attach():
    # Each first mint is into an EMPTY registry (no embedding); the variant then
    # attaches via surface key (returns before any embedding call).
    reg = ToolRegistry(cache=EmbeddingCache(embed=_no_embed))
    reg.match_or_mint(raw_name="PubMed bibliographic database", pub_count=27)
    _, a = reg.match_or_mint(raw_name="PubMed", pub_count=4)
    assert a == "attached" and len(reg) == 1

    reg2 = ToolRegistry(cache=EmbeddingCache(embed=_no_embed))
    reg2.match_or_mint(raw_name="RNA sequencing (RNA-seq) service", pub_count=10)
    _, a2 = reg2.match_or_mint(raw_name="RNA-seq", pub_count=2)
    assert a2 == "attached" and len(reg2) == 1


def test_distinct_modalities_still_mint_separately():
    # PET vs PET/CT share NO surface key (asserted above); the mint then correctly
    # falls through to embedding NN, which (orthogonal here) keeps them separate.
    vecs = {"Positron emission tomography (PET) scanner": [1.0, 0.0, 0.0],
            "Positron emission tomography/computed tomography (PET/CT) scanner": [0.0, 1.0, 0.0]}
    reg = ToolRegistry(cache=EmbeddingCache(embed=lambda ts: [vecs[t] for t in ts]))
    reg.match_or_mint(raw_name="Positron emission tomography (PET) scanner", pub_count=36)
    rec, action = reg.match_or_mint(
        raw_name="Positron emission tomography/computed tomography (PET/CT) scanner", pub_count=11)
    assert action == "minted" and len(reg) == 2   # PET and PET/CT stay distinct


# --- FamilyRegistry (§7) ----------------------------------------------------


def test_family_mints_provisional_when_empty():
    fam_reg = FamilyRegistry(cache=_cache())
    fam, action, cross = fam_reg.match_or_mint(
        tool_id="tool_000001", tool_text="MRI scanner",
        supercategory="imaging_image_analysis", dominant_kind="instrument",
    )
    assert action == "minted" and cross is None
    assert fam["family_id"] == "fam_0001"
    assert fam["status"] == "provisional"
    assert fam["member_tool_ids"] == ["tool_000001"]


def test_family_attaches_same_supercategory_near_member():
    fam_reg = FamilyRegistry(cache=_cache())
    fam_reg.match_or_mint(tool_id="tool_000001", tool_text="MRI scanner",
                          supercategory="imaging_image_analysis")
    fam, action, cross = fam_reg.match_or_mint(
        tool_id="tool_000002", tool_text="magnetic resonance imaging",
        supercategory="imaging_image_analysis",
    )
    assert action == "attached" and cross is None
    assert len(fam_reg) == 1
    assert fam["member_tool_ids"] == ["tool_000001", "tool_000002"]


def test_family_cross_supercategory_match_is_flagged_not_forked():
    fam_reg = FamilyRegistry(cache=_cache())
    fam_reg.match_or_mint(tool_id="tool_000001", tool_text="MRI scanner",
                          supercategory="imaging_image_analysis")
    # Same tool concept arriving under the WRONG supercategory: strong cross match.
    fam, action, cross = fam_reg.match_or_mint(
        tool_id="tool_000002", tool_text="magnetic resonance imaging",
        supercategory="computational_statistical",
    )
    assert action == "flagged"
    assert cross is not None and cross.key == "fam_0001"   # the suspected-error match
    assert fam["supercategory"] == "computational_statistical"  # still minted in its own bucket
    assert len(fam_reg) == 2                               # a new family, not a silent cross-fork


def test_family_unrelated_tool_mints_without_flag():
    fam_reg = FamilyRegistry(cache=_cache())
    fam_reg.match_or_mint(tool_id="tool_000001", tool_text="MRI scanner",
                          supercategory="imaging_image_analysis")
    fam, action, cross = fam_reg.match_or_mint(
        tool_id="tool_000002", tool_text="flow cytometer",
        supercategory="clinical_instruments_assays",
    )
    assert action == "minted" and cross is None


def test_family_id_durable_across_save_load(tmp_path):
    fam_reg = FamilyRegistry(cache=_cache())
    fam_reg.match_or_mint(tool_id="tool_000001", tool_text="MRI scanner",
                          supercategory="imaging_image_analysis")
    path = tmp_path / "family_registry.json"
    fam_reg.save(path)
    reloaded = FamilyRegistry.load(path, cache=_cache())
    assert reloaded.get("fam_0001")["member_tool_ids"] == ["tool_000001"]
    fam, action, _ = reloaded.match_or_mint(
        tool_id="tool_000003", tool_text="flow cytometer",
        supercategory="clinical_instruments_assays",
    )
    assert action == "minted" and fam["family_id"] == "fam_0002"  # durable continuation

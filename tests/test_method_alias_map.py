"""Unit tests for the #252 curated surface-form alias map + keep-separate guard.

Canned embeddings only. Verifies that exact curated variants fold onto ONE
canonical record order-independently (forward prevention), that the never-merge
guard blocks an embedding fold and is genuinely load-bearing, and that
contradictory curation fails loud.
"""
import itertools

import pytest

from pipeline_tools.embeddings import EmbeddingCache
from pipeline_tools.registry import ToolRegistry, load_method_alias_map

HMC1 = [{
    "class": "hmc-1",
    "canonical": "HMC-1 cells",
    "variants": ["HMC-1 cells", "human mast cell line HMC-1", "cultured human mast cells (HMC-1)"],
}]

# Two distinct lines whose embeddings are very close (cos ~0.985 >= the 0.86 match
# floor) — without a guard the registry folds them; HEK293T carries SV40 large-T.
HEK_VEC = {"HEK293 cells": [1.0, 0.0], "HEK293T cells": [0.985, 0.1726]}


def _no_embed(texts):
    raise AssertionError("surface/alias key should match first — embedding must not be called")


def _embed_hek(texts):
    return [HEK_VEC[t] for t in texts]


# --- curated merge (forward prevention) ------------------------------------

def test_alias_map_merges_variants_order_independently():
    variants = ["HMC-1 cells", "human mast cell line HMC-1", "cultured human mast cells (HMC-1)"]
    for order in itertools.permutations(variants):
        reg = ToolRegistry(cache=EmbeddingCache(embed=_no_embed), alias_merges=HMC1)
        for i, v in enumerate(order):
            reg.match_or_mint(raw_name=v, pub_ids=[f"PMID{i}"])
        assert len(reg) == 1, f"order {order} failed to merge the 3 HMC-1 forms"
        rec = reg.records()[0]
        assert rec["display_name"] == "HMC-1 cells"                  # canonical display wins
        assert reg._serialize(rec)["pub_count"] == 3                 # all 3 pmids accrete onto one
        for v in variants:
            assert v in rec["aliases"]                               # every form kept as an alias


def test_non_variant_name_is_not_merged():
    # Orthogonal vectors so the non-variant falls through to a mint (no fuzzy fold).
    vec = {"HMC-1 cells": [1.0, 0.0], "HMC-1.2 subclone": [0.0, 1.0]}
    reg = ToolRegistry(cache=EmbeddingCache(embed=lambda ts: [vec[t] for t in ts]), alias_merges=HMC1)
    reg.match_or_mint(raw_name="HMC-1 cells", pub_ids=["P1"])
    # A different subline that is NOT a curated variant must stay separate.
    _, action = reg.match_or_mint(raw_name="HMC-1.2 subclone", pub_ids=["P2"])
    assert action == "minted" and len(reg) == 2


# --- keep-separate guard (never silently fold) -----------------------------

def test_keep_separate_blocks_embedding_merge_and_records_it():
    reg = ToolRegistry(cache=EmbeddingCache(embed=_embed_hek), keep_separate=[["HEK293", "HEK293T"]])
    reg.match_or_mint(raw_name="HEK293 cells", pub_ids=["P1"])
    rec, action = reg.match_or_mint(raw_name="HEK293T cells", pub_ids=["P2"])
    assert action == "minted" and len(reg) == 2                      # forced a distinct record
    assert reg.blocked_merges and reg.blocked_merges[0]["raw_name"] == "HEK293T cells"
    assert reg.blocked_merges[0]["via"].startswith("embedding@")


def test_guard_is_load_bearing_absent_it_they_fold():
    reg = ToolRegistry(cache=EmbeddingCache(embed=_embed_hek))       # no keep_separate
    reg.match_or_mint(raw_name="HEK293 cells", pub_ids=["P1"])
    _, action = reg.match_or_mint(raw_name="HEK293T cells", pub_ids=["P2"])
    assert action == "attached" and len(reg) == 1                    # the close vectors WOULD fold


def test_keep_separate_is_whole_word_not_substring():
    # The guard keys on whole words: a HEK293 self-attach must NOT be blocked just
    # because "HEK293T" is on the never-merge list (hek293 != hek293t as tokens).
    reg = ToolRegistry(cache=EmbeddingCache(embed=_embed_hek), keep_separate=[["HEK293", "HEK293T"]])
    reg.match_or_mint(raw_name="HEK293 cells", pub_ids=["P1"])
    _, action = reg.match_or_mint(raw_name="HEK293 cells", pub_ids=["P2"])
    assert action == "attached" and len(reg) == 1 and not reg.blocked_merges


def test_contradictory_curation_fails_loud():
    with pytest.raises(ValueError):
        ToolRegistry(
            alias_merges=[{"class": "x", "canonical": "X", "variants": ["HEK293", "HEK293T"]}],
            keep_separate=[["HEK293", "HEK293T"]],
        )


# --- config loading --------------------------------------------------------

def test_load_real_alias_map_config_and_is_consistent():
    merges, keep = load_method_alias_map()
    assert {m["class"] for m in merges} >= {"hmc-1", "sh-sy5y", "mcf-7", "mda-mb-231"}
    assert ["HEK293", "HEK293T"] in keep
    # The shipped config must not contradict itself (loads without raising).
    ToolRegistry(alias_merges=merges, keep_separate=keep)


def test_load_alias_map_missing_is_fail_open(tmp_path):
    assert load_method_alias_map(tmp_path / "nope.json") == ([], [])

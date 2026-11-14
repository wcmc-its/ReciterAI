"""#1166-B — multi-sentence entity-context augmentation: merge + collect logic."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cli.extract_entity_augment import collect_sentences
from pipeline_tools.entities import MAX_USAGE_PER_PAIR, build_entity_layer


def _artifact():
    tools = [
        {"canonical_tool_id": "tool_1", "display_name": "3T3-L1 adipocytes",
         "method_family_id": "fam_cl", "pub_count": 4, "aliases": ["3T3-L1"]},
    ]
    families = [
        {"family_id": "fam_cl", "label": "Immortalized cell lines", "supercategory": "animal_cell_models",
         "dominant_kind": "organism_or_cells", "status": "active", "member_tool_ids": ["tool_1"]},
    ]
    tool_context = {
        "tool_1": {"P1": "SILAC-based proteomic profiling of 3T3-L1 adipocytes was performed."},
    }
    return tools, families, tool_context


# --------------------------------------------------------------------------- #
# collect_sentences — validation + dedup (the CLI's pure helper)
# --------------------------------------------------------------------------- #

def test_collect_sentences_validates_and_dedups():
    abstract = (
        "We profiled 3T3-L1 adipocytes by SILAC. "
        "The 3T3-L1 adipocytes were then differentiated for eight days. "
        "Glucose uptake was measured in the cells."
    )
    mentions = [{
        "raw_name": "3T3-L1 adipocytes",
        "contexts": [
            "We profiled 3T3-L1 adipocytes by SILAC.",                       # kept
            "The 3T3-L1 adipocytes were then differentiated for eight days.",  # kept
            "We profiled 3T3-L1 adipocytes by SILAC.",                       # dup -> dropped
            "Glucose uptake was measured in the cells.",                     # doesn't name tool -> dropped
            "3T3-L1 adipocytes cure everything overnight magically forever.",  # not verbatim -> dropped
        ],
    }]
    got = collect_sentences(mentions, abstract)
    assert got == [
        "We profiled 3T3-L1 adipocytes by SILAC.",
        "The 3T3-L1 adipocytes were then differentiated for eight days.",
    ]


def test_collect_sentences_tolerates_string_context_and_empty():
    abstract = "The HeLa cells were imaged on a confocal microscope."
    assert collect_sentences([], abstract) == []
    assert collect_sentences([{"raw_name": "HeLa", "contexts": []}], abstract) == []
    # a bare-string contexts (model slip) is coerced to a one-element list
    got = collect_sentences([{"raw_name": "HeLa", "contexts": "The HeLa cells were imaged on a confocal microscope."}], abstract)
    assert got == ["The HeLa cells were imaged on a confocal microscope."]


# --------------------------------------------------------------------------- #
# build_entity_layer(context_augment=...) — additive multi-sentence merge
# --------------------------------------------------------------------------- #

def test_no_augment_is_byte_identical():
    base = build_entity_layer(*_artifact())[1]
    assert build_entity_layer(*_artifact(), context_augment=None)[1] == base
    assert build_entity_layer(*_artifact(), context_augment={})[1] == base
    # the single live snippet stays a one-element list
    assert len(base["tool_1"]["P1"]) == 1


def test_augment_merges_name_gated_deduped():
    aug = {"P1": [
        "3T3-L1 adipocytes were cultured in DMEM for the lipolysis assay.",       # names tool -> merged
        "Downstream signalling was quantified by western blot in these samples.",  # no tool name -> dropped
        "SILAC-based proteomic profiling of 3T3-L1 adipocytes was performed.",     # dup of live -> dropped
    ]}
    ectx = build_entity_layer(*_artifact(), context_augment=aug)[1]
    facts = ectx["tool_1"]["P1"]
    sentences = {f["usage_sentence"] for f in facts}
    assert len(facts) == 2  # live + the one name-gated, non-dup augment sentence
    assert "3T3-L1 adipocytes were cultured in DMEM for the lipolysis assay." in sentences
    assert all("western blot" not in s for s in sentences)  # name-gate dropped it


def test_augment_caps_per_pair():
    aug = {"P1": [f"3T3-L1 adipocytes were assayed in replicate number {n} of the study." for n in range(10)]}
    facts = build_entity_layer(*_artifact(), context_augment=aug)[1]["tool_1"]["P1"]
    assert len(facts) == MAX_USAGE_PER_PAIR  # capped (live + augment, ranked, truncated)


def test_max_per_pair_one_keeps_single_best():
    # while the SPS feed renders one sentence/pair, publish cap=1: keep only the best
    aug = {"P1": [
        "3T3-L1 adipocytes were cultured in DMEM for the lipolysis assay.",
        "3T3-L1 adipocytes were assayed for glucose uptake after insulin treatment.",
    ]}
    facts = build_entity_layer(*_artifact(), context_augment=aug, max_per_pair=1)[1]["tool_1"]["P1"]
    assert len(facts) == 1


def test_augment_never_adds_new_pairs():
    # an augment sentence for a pmid the entity is NOT already in must not create a pair
    aug = {"P_NEW": ["3T3-L1 adipocytes were profiled in a separate unrelated cohort."]}
    ectx = build_entity_layer(*_artifact(), context_augment=aug)[1]
    assert set(ectx["tool_1"].keys()) == {"P1"}  # depth only, not coverage

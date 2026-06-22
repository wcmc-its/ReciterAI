"""Tests for the #1166 specific-entity (cell-line) resolution stage."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline_tools.context_quality import (
    centrality_score,
    compute_matched_span,
    name_first_fraction,
    salient_name_forms,
)
from pipeline_tools.entities import (
    apply_parent_descriptors,
    build_entity_layer,
    define_entity_parents,
    is_cell_line_family,
    parent_core,
)


# --------------------------------------------------------------------------- #
# context_quality entity helpers (span + centrality + forms)
# --------------------------------------------------------------------------- #

def test_salient_name_forms_longest_first_with_aliases():
    forms = salient_name_forms("HEK293T cells", ["HEK293T", "human HEK293T cells"])
    assert forms[0] == "human hek293t cells"  # longest first
    assert "hek293t cells" in forms
    assert "hek293t" in forms  # alias picked up


def test_compute_matched_span_prefers_longest_form():
    s = "we cultured 3T3-L1 adipocytes for 8 days"
    forms = salient_name_forms("3T3-L1 adipocytes", ["3T3-L1"])
    span = compute_matched_span(s, forms)
    assert span is not None
    assert s[span[0]:span[1]] == "3T3-L1 adipocytes"  # the longer form, not bare 3T3-L1


def test_compute_matched_span_falls_back_to_core_when_full_absent():
    s = "UHRF1 positively regulates 3T3-L1 adipogenesis and limits fibrosis"
    forms = salient_name_forms("3T3-L1 adipocytes", ["3T3-L1"])
    span = compute_matched_span(s, forms)
    assert span is not None and s[span[0]:span[1]] == "3T3-L1"


def test_compute_matched_span_none_when_absent():
    assert compute_matched_span("no cell line here", salient_name_forms("HeLa cells")) is None


def test_centrality_high_when_named_early():
    early = "3T3-L1 adipocytes were treated with metformin to study lipolysis"
    late = "lipolysis was measured after a long treatment course in 3T3-L1 adipocytes"
    forms = salient_name_forms("3T3-L1 adipocytes")
    assert centrality_score(early, forms) > centrality_score(late, forms)
    assert name_first_fraction("nothing here", forms) == 1.0


# --------------------------------------------------------------------------- #
# parent_core — the deterministic nesting key
# --------------------------------------------------------------------------- #

def test_parent_core_cases():
    assert parent_core("3T3-L1 adipocytes") == "3T3-L1"
    assert parent_core("3T3-L1 preadipocytes") == "3T3-L1"
    assert parent_core("NIH 3T3 cells") == "NIH 3T3"      # a DIFFERENT line, not merged with 3T3-L1
    assert parent_core("MS1 VEGF angiosarcoma cells") == "MS1"
    assert parent_core("human hepatocyte cell line") is None  # no digit token -> not nestable
    assert parent_core("HEK293T") is None                 # core == whole name (no trailing form)
    assert parent_core("HeLa") is None                    # single token


# --------------------------------------------------------------------------- #
# build_entity_layer — projection + nesting + facts
# --------------------------------------------------------------------------- #

def _artifact():
    tools = [
        {"canonical_tool_id": "tool_1", "display_name": "3T3-L1 adipocytes",
         "method_family_id": "fam_cl", "pub_count": 4, "aliases": ["3T3-L1 adipocyte"]},
        {"canonical_tool_id": "tool_2", "display_name": "3T3-L1 preadipocytes",
         "method_family_id": "fam_cl", "pub_count": 3, "aliases": []},
        {"canonical_tool_id": "tool_3", "display_name": "NIH 3T3 cells",
         "method_family_id": "fam_cl", "pub_count": 1, "aliases": []},
        {"canonical_tool_id": "tool_4", "display_name": "HEK293T cells",
         "method_family_id": "fam_cl", "pub_count": 2, "aliases": []},
        # a tool in a NON-cell-line family — must be excluded by scope
        {"canonical_tool_id": "tool_9", "display_name": "CRISPR-Cas9",
         "method_family_id": "fam_ge", "pub_count": 9, "aliases": []},
    ]
    families = [
        {"family_id": "fam_cl", "label": "Immortalized cell lines", "supercategory": "animal_cell_models",
         "dominant_kind": "organism_or_cells", "status": "active",
         "member_tool_ids": ["tool_1", "tool_2", "tool_3", "tool_4"]},
        {"family_id": "fam_ge", "label": "Gene editing systems", "supercategory": "molecular_methods",
         "dominant_kind": "method", "status": "active", "member_tool_ids": ["tool_9"]},
    ]
    tool_context = {
        "tool_1": {
            "32991178": "SILAC-based proteomic profiling of 3T3-L1 adipocyte differentiation",
            "38789534": "UHRF1 positively regulates 3T3-L1 adipogenesis and limits fibrosis",
        },
        "tool_2": {"33672392": "a robust in vitro system to trigger senescence in 3T3-L1 preadipocytes"},
        # tool_3 / tool_4 have no usable sentence -> evidenced False but still ranked
    }
    return tools, families, tool_context


def test_build_entity_layer_scope_and_ranking():
    ents, ectx, parents = build_entity_layer(*_artifact())
    # Only cell-line-family members; the gene-editing tool is excluded by scope.
    assert {e["normalized_entity_id"] for e in ents} == {"tool_1", "tool_2", "tool_3", "tool_4"}
    # Ranked by usage_count desc within the family.
    counts = [(e["entity_label"], e["usage_count"]) for e in ents]
    assert counts[0] == ("3T3-L1 adipocytes", 4)
    assert counts[1] == ("3T3-L1 preadipocytes", 3)
    # usage_count is pub_count (institution-wide), NOT len(facts).
    e1 = next(e for e in ents if e["normalized_entity_id"] == "tool_1")
    assert e1["usage_count"] == 4 and len(ectx["tool_1"]) == 2


def test_build_entity_layer_parent_nesting():
    ents, _, parents = build_entity_layer(*_artifact())
    by_id = {e["normalized_entity_id"]: e for e in ents}
    # The two 3T3-L1 forms share ONE parent; NIH 3T3 and HEK293T are top-level.
    assert by_id["tool_1"]["parent_entity_id"] == by_id["tool_2"]["parent_entity_id"]
    assert by_id["tool_1"]["parent_entity_id"] is not None
    assert by_id["tool_3"]["parent_entity_id"] is None  # NIH 3T3 — different line
    assert by_id["tool_4"]["parent_entity_id"] is None
    # the parent label (the line core) rides each grouped child for the directory header
    assert by_id["tool_1"]["parent_label"] == "3T3-L1"
    assert by_id["tool_2"]["parent_label"] == "3T3-L1"
    assert by_id["tool_3"]["parent_label"] is None  # top-level → no parent label
    assert len(parents) == 1
    p = parents[0]
    assert p["parent_label"] == "3T3-L1" and p["form_count"] == 2


def test_build_entity_layer_facts_have_span_and_centrality():
    _, ectx, _ = build_entity_layer(*_artifact())
    fact = ectx["tool_1"]["32991178"][0]
    s = fact["usage_sentence"]
    assert s[fact["span"][0]:fact["span"][1]].lower().startswith("3t3-l1")
    assert 0.0 <= fact["centrality_score"] <= 1.0
    assert fact["role"] is None  # #1166-B
    assert "sentence_complete" in fact  # #254 additive field present


def test_build_entity_layer_marks_sentence_complete():
    tools = [{"canonical_tool_id": "tool_1", "display_name": "HEK293 cells",
              "method_family_id": "fam_cl", "pub_count": 5, "aliases": []}]
    families = [{"family_id": "fam_cl", "label": "Immortalized cell lines",
                 "supercategory": "animal_cell_models", "dominant_kind": "organism_or_cells",
                 "status": "active", "member_tool_ids": ["tool_1"]}]
    tool_context = {"tool_1": {
        "111": "The F220C opsin was expressed in HEK293 cells.",                # complete sentence
        "222": "they both dimerize in the plasma membrane of HEK293 cells",     # #254 fragment
    }}
    _, ectx, _ = build_entity_layer(tools, families, tool_context)
    assert ectx["tool_1"]["111"][0]["sentence_complete"] is True
    assert ectx["tool_1"]["222"][0]["sentence_complete"] is False


def test_build_entity_layer_evidenced_flag():
    ents, _, _ = build_entity_layer(*_artifact())
    by_id = {e["normalized_entity_id"]: e for e in ents}
    assert by_id["tool_1"]["evidenced"] is True
    assert by_id["tool_3"]["evidenced"] is False  # ranked, but no usage sentence


def test_parent_id_stable_across_runs():
    a = build_entity_layer(*_artifact())[2][0]["parent_entity_id"]
    b = build_entity_layer(*_artifact())[2][0]["parent_entity_id"]
    assert a == b and a.startswith("ent_")  # content-derived, not run-dependent


def test_is_cell_line_family_predicate():
    assert is_cell_line_family({"label": "Cancer cell lines", "dominant_kind": "organism_or_cells", "status": "active"})
    assert not is_cell_line_family({"label": "Gene editing", "dominant_kind": "method", "status": "active"})
    assert not is_cell_line_family({"label": "Retired lines", "dominant_kind": "organism_or_cells", "status": "merged"})


# --------------------------------------------------------------------------- #
# define_entity_parents — descriptor define-pass via injected call_json seam
# --------------------------------------------------------------------------- #

def test_define_entity_parents_happy_path_and_apply():
    parents = [{"parent_entity_id": "ent_abc", "parent_label": "3T3-L1",
                "member_display_names": ["3T3-L1 adipocytes", "3T3-L1 preadipocytes"], "form_count": 2}]

    def call_json(system, user):
        return {"parents": [{"parent_entity_id": "ent_abc", "descriptor": "mouse fibroblast line",
                             "confidence": "high"}]}

    descriptors = define_entity_parents(parents, call_json=call_json)
    assert descriptors == {"ent_abc": "mouse fibroblast line"}

    ents = [{"normalized_entity_id": "tool_1", "parent_entity_id": "ent_abc", "parent_descriptor": None}]
    apply_parent_descriptors(ents, descriptors)
    assert ents[0]["parent_descriptor"] == "mouse fibroblast line"


def test_define_entity_parents_rejects_invalid_and_tolerates_errors():
    parents = [
        {"parent_entity_id": "ent_bad", "parent_label": "X", "member_display_names": ["X cells"], "form_count": 2},
        {"parent_entity_id": "ent_boom", "parent_label": "Y", "member_display_names": ["Y cells"], "form_count": 2},
    ]
    calls = {"n": 0}

    def call_json(system, user):
        calls["n"] += 1
        # Reject ent_bad (echoes the banned "cell line"); never returns ent_boom.
        return {"parents": [{"parent_entity_id": "ent_bad", "descriptor": "a human cell line", "confidence": "high"}]}

    descriptors = define_entity_parents(parents, call_json=call_json)
    # Neither survives validation/return -> empty; the run does not raise.
    assert descriptors == {}
    assert calls["n"] >= 2  # initial batch + one bounded re-prompt


def test_define_entity_parents_survives_llm_exception():
    parents = [{"parent_entity_id": "ent_x", "parent_label": "Z", "member_display_names": ["Z cells"], "form_count": 2}]

    def call_json(system, user):
        raise RuntimeError("bedrock down")

    assert define_entity_parents(parents, call_json=call_json) == {}  # tolerated, no raise

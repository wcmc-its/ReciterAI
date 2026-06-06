"""End-to-end wiring test for the seed orchestrator (pipeline_tools.seed).

Stub classifier + canned embed — no AWS/OpenAI. Drives one small batch through
identity -> classify -> salience -> family and asserts the §9 outputs + telemetry.
"""

import json

from pipeline_tools.seed import (
    EXC_INFRASTRUCTURE,
    EXC_MINTED_FAMILY,
    EXC_UNCLASSIFIED,
    run_seed,
    write_outputs,
)

FORCE_C = ["Western blot", "placebo", "centrifuge"]

MENTIONS = [
    {"raw_name": "MRI scanner", "tool_category": "instrument", "pub_count": 191},
    {"raw_name": "magnetic resonance imaging", "tool_category": "instrument", "pub_count": 10},  # near-dup -> attach
    {"raw_name": "Amazon Fresh", "tool_category": "service", "pub_count": 1},                    # excluded
    {"raw_name": "Stroke Trials Network RCC", "tool_category": "core_facility", "pub_count": 5}, # infrastructure
    {"raw_name": "Western blot", "tool_category": "assay_kit", "pub_count": 40},                 # method_tool, force-C
    {"raw_name": "ghost tool", "tool_category": "instrument", "pub_count": 2},                   # LLM omits -> unclassified
]

VECTORS = {
    "MRI scanner": [1.0, 0.0, 0.0],
    "magnetic resonance imaging": [0.99, 0.141, 0.0],
    "Western blot": [0.0, 1.0, 0.0],
    "Stroke Trials Network RCC": [0.0, 0.0, 1.0],
}


def _embed(texts):
    return [VECTORS[t] for t in texts]


def _stub_classifier(system, user):
    # Returns canned classifications for the whole batch; omits "ghost tool".
    return {"classifications": [
        {"raw_name": "MRI scanner", "disposition": "method_tool", "kind": "instrument",
         "supercategory": "imaging_image_analysis", "confidence": "high",
         "attributes": {"rrid_candidate": False}},
        {"raw_name": "magnetic resonance imaging", "disposition": "method_tool", "kind": "instrument",
         "supercategory": "imaging_image_analysis", "confidence": "high", "attributes": {}},
        {"raw_name": "Amazon Fresh", "disposition": "excluded", "kind": None,
         "supercategory": None, "attributes": {}},
        {"raw_name": "Stroke Trials Network RCC", "disposition": "infrastructure", "kind": None,
         "supercategory": None, "attributes": {}},
        {"raw_name": "Western blot", "disposition": "method_tool", "kind": "assay",
         "supercategory": "clinical_instruments_assays", "confidence": "high",
         "attributes": {"rrid_candidate": True}},
    ]}


def _run():
    return run_seed(MENTIONS, call_json=_stub_classifier, embed=_embed,
                    force_c_terms=FORCE_C, batch_size=50)


def test_identity_dedups_and_denies_and_keeps_infrastructure():
    res = _run()
    tools = res.tool_registry
    assert len(tools) == 3                               # MRI(+alias), Western blot, Stroke Trials
    assert tools.is_denied("Amazon Fresh")               # excluded -> denylist, never minted

    mri = tools.get("tool_000001")
    assert mri["display_name"] == "MRI scanner"          # prominent form mints the canonical
    assert "magnetic resonance imaging" in mri["aliases"]  # near-dup accreted
    assert tools._serialize(mri)["pub_count"] == 201      # 191 + 10 summed (seed approx)


def test_salience_and_disposition():
    res = _run()
    tools = res.tool_registry
    mri = tools.get("tool_000001")
    assert mri["disposition"] == "method_tool"
    assert mri["salience_tier"] == "A"                   # high pub_count, no spread -> at most A at seed
    assert mri["salience_tier"] != "S"                   # S withheld at seed

    western = next(r for r in tools.records() if r["display_name"] == "Western blot")
    assert western["salience_tier"] == "C"              # force-C floor
    assert western["salience_tier_basis"] == "suppress_list"

    stroke = next(r for r in tools.records() if "Stroke" in r["display_name"])
    assert stroke["disposition"] == "infrastructure"
    assert stroke["salience_tier"] is None              # no salience for infrastructure
    assert stroke["member_of_family"] is None           # no family for infrastructure


def test_families_minted_for_method_tools_only():
    res = _run()
    fams = res.family_registry
    assert len(fams) == 2                                # MRI family + Western blot family; Stroke excluded
    supercats = {f["supercategory"] for f in fams.records()}
    assert supercats == {"imaging_image_analysis", "clinical_instruments_assays"}
    assert all(f["status"] == "provisional" for f in fams.records())


def test_hierarchy_excludes_c_tier_from_exemplars():
    res = _run()
    h = res.hierarchy
    assert "imaging_image_analysis" in h
    imaging_fam = h["imaging_image_analysis"][0]
    assert imaging_fam["exemplars"] == ["MRI scanner"]
    # Western blot is C -> its family surfaces but with no exemplars / 0 non-C count.
    clinical_fam = h["clinical_instruments_assays"][0]
    assert clinical_fam["exemplars"] == []
    assert clinical_fam["n_members_non_c"] == 0


def test_exceptions_queue_is_bounded_and_typed():
    res = _run()
    types = {e["type"] for e in res.exceptions}
    assert EXC_UNCLASSIFIED in types                     # ghost tool
    assert EXC_INFRASTRUCTURE in types                   # Stroke Trials spot-audit
    assert EXC_MINTED_FAMILY in types                    # both provisional families
    # awaiting-A2 is a count, not 230 rows — the queue stays actionable.
    assert sum(1 for e in res.exceptions if e["type"] == EXC_MINTED_FAMILY) == 2


def test_telemetry_counts():
    res = _run()
    t = res.telemetry
    assert t["tool_minted"] == 3
    assert t["tool_attached"] == 1
    assert t["tool_denied"] == 1
    assert t["tool_unclassified"] == 1
    assert t["families"] == 2
    assert t["family_minted"] == 2
    assert t["awaiting_a2_regrounding"] == 2             # two method_tools
    assert t["salience_distribution"].get("A") == 1
    assert t["salience_distribution"].get("C") == 1


def test_write_outputs_emits_parseable_artifacts(tmp_path):
    res = _run()
    paths = write_outputs(res, tmp_path)
    names = {p.name for p in paths}
    assert {"tool_registry.json", "tool_denylist.json", "family_registry.json",
            "tool_taxonomy_seed.json", "tool_hierarchy_seed.json",
            "tool_exceptions_seed.json", "tool_telemetry_seed.json"} <= names
    # Every artifact is valid JSON and the registry round-trips its records.
    for p in paths:
        json.loads(p.read_text())
    reg = json.loads((tmp_path / "tool_registry.json").read_text())
    assert len(reg["tools"]) == 3
    deny = json.loads((tmp_path / "tool_denylist.json").read_text())
    assert any("amazon" in a for a in deny["aliases"])

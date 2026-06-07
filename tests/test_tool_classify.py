"""Unit tests for the disposition gate + classifier (pipeline_tools.classify).

Stub ``call_json`` only — no Bedrock, no OpenAI. Verifies validation/coercion
against the frozen vocab and the partial-failure batching contract.
"""

from pipeline_tools import classify, vocab


# --- normalize_classification ----------------------------------------------


def test_valid_method_tool_passes_through():
    entry = {
        "raw_name": "MRI scanner", "disposition": "method_tool", "kind": "instrument",
        "supercategory": "imaging_image_analysis", "confidence": "high",
        "attributes": {"rrid_candidate": False, "consumable": False},
    }
    rec = classify.normalize_classification(entry, {"raw_name": "MRI scanner"})
    assert rec["disposition"] == "method_tool"
    assert rec["supercategory"] == "imaging_image_analysis"
    assert rec["kind"] == "instrument"
    assert rec["flags"] == []


def test_invalid_supercategory_routes_to_other_and_flags():
    entry = {"raw_name": "x", "disposition": "method_tool", "kind": "method",
             "supercategory": "made_up", "confidence": "high", "attributes": {}}
    rec = classify.normalize_classification(entry, {"raw_name": "x"})
    assert rec["supercategory"] == vocab.OTHER_SUPERCATEGORY
    assert classify.FLAG_INVALID_SUPERCATEGORY in rec["flags"]


def test_invalid_kind_nulled_and_flagged():
    entry = {"raw_name": "x", "disposition": "method_tool", "kind": "model_system",
             "supercategory": "animal_cell_models", "confidence": "high", "attributes": {}}
    rec = classify.normalize_classification(entry, {"raw_name": "x"})
    assert rec["kind"] is None
    assert classify.FLAG_INVALID_KIND in rec["flags"]


def test_invalid_disposition_defaults_to_method_tool_and_flags():
    entry = {"raw_name": "x", "disposition": "is_tool", "kind": "software",
             "supercategory": "software_informatics", "confidence": "high", "attributes": {}}
    rec = classify.normalize_classification(entry, {"raw_name": "x"})
    assert rec["disposition"] == "method_tool"
    assert classify.FLAG_INVALID_DISPOSITION in rec["flags"]


def test_infrastructure_strips_capability_fields_and_flags_for_audit():
    entry = {"raw_name": "Stroke Trials Network RCC", "disposition": "infrastructure",
             "kind": "software", "supercategory": "software_informatics", "attributes": {}}
    rec = classify.normalize_classification(entry, {"raw_name": "Stroke Trials Network RCC"})
    assert rec["disposition"] == "infrastructure"
    assert rec["kind"] is None and rec["supercategory"] is None
    assert classify.FLAG_INFRASTRUCTURE in rec["flags"]


def test_excluded_strips_capability_fields():
    entry = {"raw_name": "Amazon Fresh", "disposition": "excluded",
             "kind": "software", "supercategory": "software_informatics", "attributes": {}}
    rec = classify.normalize_classification(entry, {"raw_name": "Amazon Fresh"})
    assert rec["disposition"] == "excluded"
    assert rec["kind"] is None and rec["supercategory"] is None


def test_low_confidence_is_flagged():
    entry = {"raw_name": "x", "disposition": "method_tool", "kind": "method",
             "supercategory": "computational_statistical", "confidence": "low", "attributes": {}}
    rec = classify.normalize_classification(entry, {"raw_name": "x"})
    assert classify.FLAG_LOW_CONFIDENCE in rec["flags"]


def test_attributes_coerced_and_legacy_prior_backfills_nulls():
    entry = {"raw_name": "biopsy needle", "disposition": "method_tool", "kind": "instrument",
             "supercategory": "clinical_instruments_assays", "confidence": "high",
             "attributes": {"license": "MIT", "consumable": "yes"}}  # bad license enum, string bool
    rec = classify.normalize_classification(entry, {"raw_name": "biopsy needle",
                                                    "tool_category": "instrument_consumable"})
    attrs = rec["attributes"]
    assert attrs["license"] is None             # invalid enum coerced to null
    assert attrs["consumable"] is True          # "yes" -> True (and the legacy prior agrees)


def test_legacy_prior_fills_provenance_when_llm_leaves_null():
    entry = {"raw_name": "MarketScan", "disposition": "method_tool", "kind": "dataset",
             "supercategory": "datasets_cohorts", "confidence": "high", "attributes": {}}
    rec = classify.normalize_classification(entry, {"raw_name": "MarketScan",
                                                    "tool_category": "dataset_proprietary"})
    assert rec["attributes"]["provenance"] == "proprietary"


# --- classify_mentions (batching) ------------------------------------------


def test_classify_mentions_aligns_by_raw_name():
    mentions = [{"raw_name": "MRI scanner", "tool_category": "instrument"},
                {"raw_name": "flow cytometer", "tool_category": "instrument"}]

    def stub(system, user):
        return {"classifications": [
            {"raw_name": "flow cytometer", "disposition": "method_tool", "kind": "instrument",
             "supercategory": "clinical_instruments_assays", "confidence": "high", "attributes": {}},
            {"raw_name": "MRI scanner", "disposition": "method_tool", "kind": "instrument",
             "supercategory": "imaging_image_analysis", "confidence": "high", "attributes": {}},
        ]}

    out = classify.classify_mentions(mentions, call_json=stub, batch_size=10)
    assert [r["raw_name"] for r in out] == ["MRI scanner", "flow cytometer"]  # input order preserved
    assert out[0]["supercategory"] == "imaging_image_analysis"


def test_classify_mentions_flags_missing_entries():
    mentions = [{"raw_name": "MRI scanner"}, {"raw_name": "ghost tool"}]

    def stub(system, user):
        return {"classifications": [
            {"raw_name": "MRI scanner", "disposition": "method_tool", "kind": "instrument",
             "supercategory": "imaging_image_analysis", "confidence": "high", "attributes": {}},
        ]}

    out = classify.classify_mentions(mentions, call_json=stub, batch_size=10)
    ghost = next(r for r in out if r["raw_name"] == "ghost tool")
    assert ghost["disposition"] is None
    assert classify.FLAG_MISSING in ghost["flags"]


def test_classify_mentions_tolerates_batch_failure():
    mentions = [{"raw_name": "a"}, {"raw_name": "b"}]

    def boom(system, user):
        raise RuntimeError("bedrock down")

    out = classify.classify_mentions(mentions, call_json=boom, batch_size=10)
    assert len(out) == 2
    assert all(r["disposition"] is None and classify.FLAG_LLM_ERROR in r["flags"] for r in out)


def test_classify_mentions_batches_respect_size():
    mentions = [{"raw_name": f"t{i}"} for i in range(5)]
    seen_batch_sizes = []

    def stub(system, user):
        import json as _json
        # Count how many mentions this batch's user message carried.
        payload = user[user.index("["):]
        seen_batch_sizes.append(len(_json.loads(payload)))
        return {"classifications": []}

    classify.classify_mentions(mentions, call_json=stub, batch_size=2)
    assert seen_batch_sizes == [2, 2, 1]  # 5 mentions -> batches of 2,2,1

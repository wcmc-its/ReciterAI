"""Unit tests for the frozen tool/method vocabulary (pipeline_tools.vocab).

Pure — no AWS, no I/O. Verifies the closed sets match the spec's cardinalities
and that the validators reject out-of-vocabulary values and capability fields on
non-method_tool records (§0.5/§1/§2/§3/§9).
"""

from pipeline_tools import vocab


def test_closed_set_cardinalities_match_spec():
    assert len(vocab.DISPOSITIONS) == 3                 # §0.5
    assert len(vocab.SUPERCATEGORIES) == 14             # §1 (12 + functional/metabolic #14 + Other)
    assert len(vocab.KINDS) == 8                        # §2
    assert len(vocab.SALIENCE_TIERS) == 4               # §5  S/A/B/C


def test_v3_supercategory_12_present_and_other_last():
    ids = [s["id"] for s in vocab.SUPERCATEGORIES]
    assert "structural_biophysical" in ids              # the v3 addition (#12)
    assert "functional_metabolic_cellular_assays" in ids  # the #14 spine-rule mint
    assert ids[-1] == vocab.OTHER_SUPERCATEGORY         # gated remainder stays last
    assert "service" not in vocab.KINDS                 # §6.2 — service is a delivery attribute, not a kind


def test_computational_axis_relabeled_to_research_and_analysis():
    # id is kept stable (cache/records), only the display label broadens to the real axis
    assert vocab.is_valid_supercategory("computational_statistical")
    assert vocab.SUPERCATEGORY_LABELS["computational_statistical"] == "Research & analysis methods"


def test_validators_accept_in_vocab_reject_out():
    assert vocab.is_valid_disposition("method_tool")
    assert not vocab.is_valid_disposition("is_tool")
    assert vocab.is_valid_supercategory("structural_biophysical")
    assert not vocab.is_valid_supercategory("coordination")  # removed in v2
    assert vocab.is_valid_kind("model")
    assert not vocab.is_valid_kind("model_system")           # #169's kind, retired
    assert vocab.is_valid_tier("S") and not vocab.is_valid_tier("D")


def test_default_attributes_shape():
    attrs = vocab.default_attributes()
    assert attrs == {
        "delivery": None, "provenance": None, "license": None,
        "consumable": False, "rrid_candidate": False,
    }
    assert vocab.validate_attributes(attrs) == []


def test_validate_attributes_flags_bad_enums_and_types():
    problems = vocab.validate_attributes(
        {"delivery": "outsourced", "provenance": "secret", "license": "MIT",
         "consumable": "yes", "rrid_candidate": 1}
    )
    assert len(problems) == 5  # all five fields invalid


def test_method_tool_record_requires_capability_fields():
    good = {
        "disposition": "method_tool", "kind": "instrument",
        "supercategory": "imaging_image_analysis", "salience_tier": "A",
        "attributes": vocab.default_attributes(),
    }
    assert vocab.validate_method_tool_record(good) == []
    bad = {**good, "supercategory": "nope"}
    assert vocab.validate_method_tool_record(bad)


def test_infrastructure_record_must_not_carry_capability_fields():
    # §9: infrastructure/excluded carry no supercategory/kind/salience.
    rec = {"disposition": "infrastructure", "supercategory": "datasets_cohorts"}
    problems = vocab.validate_method_tool_record(rec)
    assert any("must not carry supercategory" in p for p in problems)
    clean = {"disposition": "infrastructure", "supercategory": None, "kind": None, "salience_tier": None}
    assert vocab.validate_method_tool_record(clean) == []


def test_legacy_prior_is_weak_and_demixes():
    # §4 — known tags give a kind prior + attribute hints.
    assert vocab.legacy_prior("instrument_consumable")["attrs"]["consumable"] is True
    assert vocab.legacy_prior("dataset_proprietary")["attrs"]["provenance"] == "proprietary"
    # de-mix / strip-wrapper tags carry NO hard kind (LLM decides).
    assert vocab.legacy_prior("computational_method")["kind"] is None
    assert vocab.legacy_prior("service")["kind"] is None
    # unknown / blank tags -> empty prior, decide from name+context (cardinal rule).
    assert vocab.legacy_prior("")["kind"] is None
    assert vocab.legacy_prior("totally_made_up")["kind"] is None

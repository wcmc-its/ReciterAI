"""Unit tests for the routing-sanity net (pipeline_tools.sanity).

Flag-only, deterministic. Each check must fire on a likely-misroute and stay
QUIET on a correct assignment (the net must not nag about good classifications).
"""

from pipeline_tools import sanity


def _rec(**kw):
    base = {"disposition": "method_tool", "display_name": "x", "kind": "method",
            "supercategory": "computational_statistical", "salience_tier": "A"}
    base.update(kw)
    return base


def _checks(rec):
    return {f["check"] for f in sanity.routing_sanity_flags(rec)}


def test_index_outside_allowed_supercats_is_flagged():
    r = _rec(display_name="Reversed Bice-Boxerman Index", kind="method",
             supercategory="imaging_image_analysis")
    assert sanity.SANITY_INDEX_MISROUTE in _checks(r)


def test_index_in_allowed_supercats_is_quiet():
    # Distributed lookup index in datasets, clinical scale in #6, computed index in #5 — all fine.
    assert sanity.SANITY_INDEX_MISROUTE not in _checks(
        _rec(display_name="Social Vulnerability Index (SVI)", kind="dataset",
             supercategory="datasets_cohorts"))
    assert sanity.SANITY_INDEX_MISROUTE not in _checks(
        _rec(display_name="Modified Rankin Scale", kind="method",
             supercategory="clinical_instruments_assays"))
    assert sanity.SANITY_INDEX_MISROUTE not in _checks(
        _rec(display_name="Reversed Bice-Boxerman Index", kind="method",
             supercategory="computational_statistical"))  # the corrected placement


def test_dataset_named_nondataset_kind_is_flagged():
    r = _rec(display_name="Premier Healthcare Database", kind="software",
             supercategory="software_informatics")
    assert sanity.SANITY_DATASET_NAME in _checks(r)
    # A true dataset is quiet.
    assert sanity.SANITY_DATASET_NAME not in _checks(
        _rec(display_name="SEER registry", kind="dataset", supercategory="datasets_cohorts"))


def test_pathogen_unexpected_kind_is_flagged_but_reagent_organism_quiet():
    assert sanity.SANITY_PATHOGEN in _checks(
        _rec(display_name="Influenza A virus", kind="instrument",
             supercategory="clinical_instruments_assays"))
    # reagent / organism are the legitimate homes -> quiet.
    assert sanity.SANITY_PATHOGEN not in _checks(
        _rec(display_name="Severe acute respiratory syndrome coronavirus 2", kind="reagent",
             supercategory="molecular_biochem_reagents"))


def test_kind_supercategory_incoherence_is_flagged():
    assert sanity.SANITY_KIND_SUPERCAT in _checks(
        _rec(display_name="C57BL/6J mouse", kind="organism_or_cells",
             supercategory="therapeutics_interventions"))   # organism in the wrong bucket
    assert sanity.SANITY_KIND_SUPERCAT not in _checks(
        _rec(display_name="C57BL/6J mouse", kind="organism_or_cells",
             supercategory="animal_cell_models"))


def test_other_distinctive_is_a_taxonomy_gap_candidate():
    # A non-C tool in 'other' = genuine homeless capability -> gap candidate (the #14 trigger).
    assert sanity.SANITY_OTHER_DISTINCTIVE in _checks(
        _rec(display_name="Seahorse XF extracellular flux analyzer", kind="instrument",
             supercategory="other", salience_tier="A"))
    # A C-tier commodity in 'other' is NOT a gap candidate (correctly demoted, never surfaces).
    assert sanity.SANITY_OTHER_DISTINCTIVE not in _checks(
        _rec(display_name="Ultracentrifuge", kind="instrument",
             supercategory="other", salience_tier="C"))


def test_non_method_tool_records_are_never_flagged():
    assert sanity.routing_sanity_flags(
        {"disposition": "infrastructure", "display_name": "Stroke Trials Network RCC"}) == []
    assert sanity.routing_sanity_flags(
        {"disposition": "excluded", "display_name": "Amazon Fresh"}) == []


def test_clean_record_produces_no_flags():
    assert sanity.routing_sanity_flags(
        _rec(display_name="Magnetic resonance imaging (MRI) scanner", kind="instrument",
             supercategory="imaging_image_analysis", salience_tier="A")) == []

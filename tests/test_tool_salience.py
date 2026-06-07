"""Unit tests for seed-mode salience (pipeline_tools.salience, spec §5).

Pure — no AWS. Verifies the seed-time signal policy: pub_count + rrid_candidate
only, S withheld, force-to-C deterministic floor, everything queued for A2.
"""

from pipeline_tools import salience, vocab

TERMS = ["placebo", "centrifuge", "Western blot", "PCR", "linear regression"]


# --- matches_force_c --------------------------------------------------------


def test_force_c_whole_word_match_is_case_and_punct_insensitive():
    assert salience.matches_force_c("Benchtop centrifuge", TERMS)
    assert salience.matches_force_c("Digital PCR", TERMS)
    assert salience.matches_force_c("western-blot apparatus", TERMS)


def test_force_c_does_not_substring_overmatch():
    # "PCR" must not fire inside an unrelated token.
    assert not salience.matches_force_c("encryption", ["PCR"])
    assert not salience.matches_force_c("MRI scanner", TERMS)


# --- assign_seed_salience ---------------------------------------------------


def test_force_c_term_demotes_to_C_with_suppress_basis():
    out = salience.assign_seed_salience(
        display_name="Western blot", pub_count=50, rrid_candidate=True, force_c_terms=TERMS,
    )
    assert out["salience_tier"] == "C"
    assert out["salience_tier_basis"] == "suppress_list"
    assert salience.FLAG_AWAITING_A2 in out["flags"]


def test_rrid_candidate_earns_provisional_A():
    out = salience.assign_seed_salience(
        display_name="anti-CD3 antibody", pub_count=1, rrid_candidate=True, force_c_terms=TERMS,
    )
    assert out["salience_tier"] == "A"
    assert out["salience_tier_basis"] == "llm_provisional"


def test_pub_count_floor_earns_provisional_A():
    out = salience.assign_seed_salience(
        display_name="some platform", pub_count=3, rrid_candidate=False, force_c_terms=TERMS,
    )
    assert out["salience_tier"] == "A"


def test_low_signal_lands_in_B():
    out = salience.assign_seed_salience(
        display_name="some niche method", pub_count=1, rrid_candidate=False, force_c_terms=TERMS,
    )
    assert out["salience_tier"] == "B"


def test_S_is_never_assigned_at_seed():
    # Even a very high pub_count cannot earn S without cross-faculty spread.
    out = salience.assign_seed_salience(
        display_name="MRI scanner", pub_count=191, rrid_candidate=True, force_c_terms=TERMS,
    )
    assert out["salience_tier"] != "S"
    assert out["salience_tier"] == "A"
    assert salience.FLAG_THRESHOLDS_PROVISIONAL in out["flags"]
    assert salience.FLAG_AWAITING_A2 in out["flags"]


# --- apply_seed_salience ----------------------------------------------------


def test_apply_only_tiers_method_tool_records():
    records = [
        {"disposition": "method_tool", "display_name": "MRI scanner", "pub_count": 191,
         "attributes": {"rrid_candidate": False}},
        {"disposition": "infrastructure", "display_name": "Stroke Trials Network RCC",
         "pub_count": 5, "attributes": {}},
        {"disposition": "method_tool", "display_name": "Western blot", "pub_count": 40,
         "attributes": {"rrid_candidate": True}},
    ]
    salience.apply_seed_salience(records, force_c_terms=TERMS)
    assert records[0]["salience_tier"] == "A"
    assert "salience_tier" not in records[1] or records[1].get("salience_tier") is None  # infra untouched
    assert records[2]["salience_tier"] == "C"  # force-C wins over its rrid/pub_count
    assert vocab.is_valid_tier(records[0]["salience_tier"])

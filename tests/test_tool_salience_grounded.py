"""Tests for §5 grounded (A2) salience — spread-driven, RRID gates A only."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline_tools import salience as sal
from pipeline_tools import vocab


def _signal(pub_count=0, spread=0, grant=False, rrid=False, has=None):
    if has is None:
        has = pub_count > 0 or grant
    return {"pub_count": pub_count, "spread": spread, "grant_spread": 1 if grant else 0,
            "rrid_candidate": rrid, "has_a2_signal": has}


# --- calibration -----------------------------------------------------------

def test_calibrate_withholds_S_when_no_cross_faculty_spread():
    # every tool is single-lab -> no record clears the S spread floor -> S withheld.
    th = sal.calibrate_grounded_thresholds([1, 1, 1, 1])
    assert th.s_spread_min is None


def test_calibrate_cuts_S_at_percentile_of_qualifying_spreads():
    spreads = [2, 2, 3, 3, 4, 5, 8, 20]  # all >= floor(2)
    th = sal.calibrate_grounded_thresholds(spreads, percentile=0.88)
    # idx = int(8*0.88)=7 -> spreads_sorted[7]=20
    assert th.s_spread_min == 20
    assert th.a_pub_floor == sal.A_PUB_FLOOR


def test_calibrate_floors_S_cutoff_at_spread_floor():
    th = sal.calibrate_grounded_thresholds([2, 2, 2], percentile=0.0)
    assert th.s_spread_min == sal.S_SPREAD_FLOOR  # never below the marquee floor


# --- per-record assignment -------------------------------------------------

def test_force_c_is_deterministic_floor_with_suppress_basis():
    th = sal.GroundedThresholds(s_spread_min=2, a_pub_floor=3, a_spread_floor=2, percentile=0.88)
    out = sal.assign_grounded_salience(display_name="generic centrifuge", pub_count=99, spread=99,
                                       rrid_candidate=True, thresholds=th, force_c_terms=["centrifuge"])
    assert out["salience_tier"] == vocab.DEMOTED_TIER
    assert out["salience_tier_basis"] == "suppress_list"


def test_spread_drives_S_not_rrid():
    th = sal.GroundedThresholds(s_spread_min=5, a_pub_floor=3, a_spread_floor=2, percentile=0.88)
    # high RRID + pub_count but low spread -> A, never S (spread is the discriminator).
    a = sal.assign_grounded_salience(display_name="x", pub_count=50, spread=2, rrid_candidate=True,
                                     thresholds=th, force_c_terms=[])
    assert a["salience_tier"] == "A"
    # broad spread -> S.
    s = sal.assign_grounded_salience(display_name="y", pub_count=3, spread=5, rrid_candidate=False,
                                     thresholds=th, force_c_terms=[])
    assert s["salience_tier"] == "S"
    assert s["salience_tier_basis"] == sal.GROUNDED_BASIS


def test_b_when_undistinctive():
    th = sal.GroundedThresholds(s_spread_min=5, a_pub_floor=3, a_spread_floor=2, percentile=0.88)
    b = sal.assign_grounded_salience(display_name="z", pub_count=1, spread=1, rrid_candidate=False,
                                     thresholds=th, force_c_terms=[])
    assert b["salience_tier"] == "B"


def test_no_S_assignable_keeps_top_tools_at_A():
    th = sal.GroundedThresholds(s_spread_min=None, a_pub_floor=3, a_spread_floor=2, percentile=0.88)
    out = sal.assign_grounded_salience(display_name="x", pub_count=10, spread=1, rrid_candidate=True,
                                       thresholds=th, force_c_terms=[])
    assert out["salience_tier"] == "A"  # not S — S withheld this run


# --- apply over a record list ----------------------------------------------

def test_apply_skips_records_without_a2_signal():
    method_tools = [
        {"canonical_tool_id": "tool_1", "display_name": "Seen", "disposition": "method_tool",
         "salience_tier": "A", "salience_tier_basis": "llm_provisional",
         "flags": ["awaiting_a2_regrounding", "thresholds_provisional"], "attributes": {}},
        {"canonical_tool_id": "tool_2", "display_name": "Unseen", "disposition": "method_tool",
         "salience_tier": "A", "salience_tier_basis": "llm_provisional",
         "flags": ["awaiting_a2_regrounding"], "attributes": {}},
    ]
    sig = {"tool_1": _signal(pub_count=5, spread=4), "tool_2": _signal(has=False)}
    _, th = sal.apply_grounded_salience(method_tools, signal_of=lambda r: sig[r["canonical_tool_id"]],
                                        force_c_terms=[])
    seen, unseen = method_tools
    assert seen["salience_tier_basis"] == sal.GROUNDED_BASIS
    assert "awaiting_a2_regrounding" not in seen["flags"]  # cleared on grounding
    # untouched tool keeps its honest provisional posture (no silent demotion).
    assert unseen["salience_tier_basis"] == "llm_provisional"
    assert "awaiting_a2_regrounding" in unseen["flags"]

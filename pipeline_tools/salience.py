"""Seed-mode salience tiering (docs/tool-classifier-spec.md §5).

Salience is an INSTITUTION-level measure (it gates page-worthiness and noise; it
does NOT order any one scholar's profile — that is §7.1, per-profile). At seed
time the discriminating signal — cross-faculty spread — does not exist yet (it
needs the A2 corpus extraction + the per-faculty join). So this module scores
from ``pub_count`` + ``rrid_candidate`` ONLY, marks the basis ``llm_provisional``,
and queues every record for A2 regrounding. Seed tiers are explicitly NOT final
ranking input.

Consequences of the seed-time signal gap, made explicit:
  - **S is withheld at seed.** S is "marquee by cross-faculty spread" (§5); with
    no spread signal, no record can earn S yet. The marquee tier is assigned at
    A2 when spread is computed. A high-``pub_count`` seed tool (MRI) is at most a
    provisional A here and is expected to promote to S at A2.
  - Thresholds are provisional placeholders (§5 ``THRESHOLDS_PROVISIONAL``),
    recalibrated against the real post-A2 distribution — not this 230-record seed.

Force-to-C (§5) is a deterministic floor over the canonical display_name —
commodity/universal-infrastructure tools & comparators that carry no
scholar-specific signal. Only ``method_tool`` records are tiered; infrastructure
/ excluded records carry no salience.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from pipeline_tools import vocab
from pipeline_tools.registry import norm_name

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FORCE_C_PATH = REPO_ROOT / "config" / "tool_force_c_list.json"

# §5 provisional placeholder: A requires pub_count >= this floor OR rrid_candidate.
# THRESHOLDS_PROVISIONAL — recalibrate against the post-A2 distribution.
A_PUB_FLOOR = 3

FLAG_THRESHOLDS_PROVISIONAL = "thresholds_provisional"
FLAG_AWAITING_A2 = "awaiting_a2_regrounding"

# §5 grounded (A2) — cross-faculty spread is the discriminator; RRID only gates A.
GROUNDED_BASIS = "grounded"
# S ≈ top ~12% by spread (the §5 "top 10–15%" placeholder), calibrated against
# the REAL post-A2 distribution at run time — not this constant. The constant is
# only the percentile to cut at; the cutoff spread value is data-derived.
DEFAULT_S_SPREAD_PERCENTILE = 0.88
# Marquee (S) requires genuine cross-faculty breadth — a single lab, however
# deep, is A (depth), not S (spread). A two-faculty floor keeps S from collapsing
# onto single-lab tools when the corpus is small.
S_SPREAD_FLOOR = 2
# Below S, spread of >= this is still "distinctive" (A) even without RRID/pub depth.
A_SPREAD_FLOOR = 2


def load_force_c_terms(path: Path = DEFAULT_FORCE_C_PATH) -> list[str]:
    if not path.exists():
        logger.warning("force-to-C list not found at %s; proceeding with none.", path)
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("force_c_terms", []))


def matches_force_c(display_name: str, terms: list[str]) -> bool:
    """True if ``display_name`` contains any force-C term as a whole word (case/punct-insensitive)."""
    hay = f" {norm_name(display_name)} "
    for term in terms:
        needle = norm_name(term)
        if needle and f" {needle} " in hay:
            return True
    return False


def assign_seed_salience(
    *,
    display_name: str,
    pub_count: int,
    rrid_candidate: bool,
    force_c_terms: list[str],
) -> dict:
    """Provisional seed tier for one method_tool record.

    Returns ``{"salience_tier", "salience_tier_basis", "flags"}``. S is never
    assigned at seed (no spread signal). Force-C matches get basis
    ``suppress_list``; everything else is ``llm_provisional`` and queued for A2.
    """
    if matches_force_c(display_name, force_c_terms):
        return {
            "salience_tier": vocab.DEMOTED_TIER,        # C
            "salience_tier_basis": "suppress_list",
            "flags": [FLAG_AWAITING_A2],
        }

    tier = "A" if (rrid_candidate or (pub_count or 0) >= A_PUB_FLOOR) else "B"
    return {
        "salience_tier": tier,
        "salience_tier_basis": "llm_provisional",
        "flags": [FLAG_THRESHOLDS_PROVISIONAL, FLAG_AWAITING_A2],
    }


def apply_seed_salience(records: list[dict], *, force_c_terms: list[str]) -> list[dict]:
    """Tier a list of classified records in place (method_tool only; others untouched).

    Each record is expected to carry ``disposition``, ``display_name``,
    ``pub_count``, and ``attributes.rrid_candidate``. The salience fields and any
    new review flags are merged onto the record.
    """
    for rec in records:
        if rec.get("disposition") != vocab.CAPABILITY_DISPOSITION:
            continue
        result = assign_seed_salience(
            display_name=rec.get("display_name") or rec.get("raw_name", ""),
            pub_count=rec.get("pub_count", 0),
            rrid_candidate=bool((rec.get("attributes") or {}).get("rrid_candidate")),
            force_c_terms=force_c_terms,
        )
        rec["salience_tier"] = result["salience_tier"]
        rec["salience_tier_basis"] = result["salience_tier_basis"]
        existing = rec.setdefault("flags", [])
        for flag in result["flags"]:
            if flag not in existing:
                existing.append(flag)
    return records


# ---------------------------------------------------------------------------
# §5 GROUNDED salience (A2) — spread is the discriminator, RRID gates A only.
# Replaces the seed's pub_count+RRID guess once the per-faculty join exists.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GroundedThresholds:
    """The A2-calibrated salience cutoffs (§5), derived from the real distribution."""

    s_spread_min: int | None  # spread >= this -> S; None when no record qualifies for S
    a_pub_floor: int          # pub_count >= this -> at least A (single-lab depth)
    a_spread_floor: int       # spread >= this -> at least A (broad but sub-marquee)
    percentile: float         # the spread percentile s_spread_min was cut at


def calibrate_grounded_thresholds(
    spreads: list[int],
    *,
    percentile: float = DEFAULT_S_SPREAD_PERCENTILE,
    a_pub_floor: int = A_PUB_FLOOR,
    s_spread_floor: int = S_SPREAD_FLOOR,
    a_spread_floor: int = A_SPREAD_FLOOR,
) -> GroundedThresholds:
    """Derive the S-tier spread cutoff from the REAL post-A2 spread distribution.

    ``spreads`` is the cross-faculty spread of every method_tool that has A2
    signal. S is the top ``1 - percentile`` of tools whose spread clears
    ``s_spread_floor`` (marquee = genuine cross-faculty breadth). When no tool
    clears the floor (e.g. a tiny probe where every tool is single-lab), S is
    withheld (``s_spread_min=None``) rather than guessed — exactly the seed-time
    posture, but now because the data, not the missing signal, says so.
    """
    qualifying = sorted(s for s in spreads if s >= s_spread_floor)
    if not qualifying:
        return GroundedThresholds(
            s_spread_min=None, a_pub_floor=a_pub_floor,
            a_spread_floor=a_spread_floor, percentile=percentile,
        )
    idx = min(int(len(qualifying) * percentile), len(qualifying) - 1)
    s_min = max(qualifying[idx], s_spread_floor)
    return GroundedThresholds(
        s_spread_min=s_min, a_pub_floor=a_pub_floor,
        a_spread_floor=a_spread_floor, percentile=percentile,
    )


def assign_grounded_salience(
    *,
    display_name: str,
    pub_count: int,
    spread: int,
    rrid_candidate: bool,
    thresholds: GroundedThresholds,
    force_c_terms: list[str],
) -> dict:
    """Grounded tier for one method_tool from real A2 signals (§5).

    Returns ``{"salience_tier", "salience_tier_basis", "clear_flags"}``.
    ``clear_flags`` are the seed-era flags to drop now that the record is
    grounded. Force-C is the deterministic floor and applies regardless of
    signal; otherwise spread drives S, with RRID / pub-depth / sub-marquee
    spread earning A.
    """
    if matches_force_c(display_name, force_c_terms):
        return {
            "salience_tier": vocab.DEMOTED_TIER,  # C
            "salience_tier_basis": "suppress_list",
            "clear_flags": [FLAG_THRESHOLDS_PROVISIONAL, FLAG_AWAITING_A2],
        }
    if thresholds.s_spread_min is not None and (spread or 0) >= thresholds.s_spread_min:
        tier = "S"
    elif (
        rrid_candidate
        or (pub_count or 0) >= thresholds.a_pub_floor
        or (spread or 0) >= thresholds.a_spread_floor
    ):
        tier = "A"
    else:
        tier = "B"
    return {
        "salience_tier": tier,
        "salience_tier_basis": GROUNDED_BASIS,
        "clear_flags": [FLAG_THRESHOLDS_PROVISIONAL, FLAG_AWAITING_A2],
    }


def apply_grounded_salience(
    method_tools: list[dict],
    *,
    signal_of,
    force_c_terms: list[str],
    thresholds: GroundedThresholds | None = None,
) -> tuple[list[dict], GroundedThresholds]:
    """Reground every method_tool with A2 signal in place; return ``(records, thresholds)``.

    ``signal_of(rec) -> dict`` supplies the per-tool A2 aggregates:
    ``pub_count`` (= |pub_ids|), ``spread`` (distinct lead/senior faculty whose
    pubs touch the tool), ``rrid_candidate``, and ``has_a2_signal`` (any real
    publication/grant corroboration this run).

    A tool with **no** A2 signal is left untouched — it keeps its seed
    ``llm_provisional`` tier and ``awaiting_a2_regrounding`` flag, because A2 gave
    us nothing new to ground it on (honest, not a silent demotion). Force-C still
    applies to it via the seed pass. Thresholds are calibrated from the grounded
    population when not supplied, so S is data-cut, not guessed.
    """
    grounded = [r for r in method_tools if signal_of(r).get("has_a2_signal")]
    if thresholds is None:
        thresholds = calibrate_grounded_thresholds(
            [int(signal_of(r).get("spread") or 0) for r in grounded]
        )
    for rec in grounded:
        sig = signal_of(rec)
        result = assign_grounded_salience(
            display_name=rec.get("display_name") or rec.get("raw_name", ""),
            pub_count=int(sig.get("pub_count") or 0),
            spread=int(sig.get("spread") or 0),
            rrid_candidate=bool(sig.get("rrid_candidate")),
            thresholds=thresholds,
            force_c_terms=force_c_terms,
        )
        rec["salience_tier"] = result["salience_tier"]
        rec["salience_tier_basis"] = result["salience_tier_basis"]
        if rec.get("flags"):
            rec["flags"] = [f for f in rec["flags"] if f not in result["clear_flags"]]
    logger.info(
        "grounded salience: regrounded %d/%d method_tool(s); S cutoff spread=%s (p%.0f), A pub floor=%d",
        len(grounded), len(method_tools), thresholds.s_spread_min,
        thresholds.percentile * 100, thresholds.a_pub_floor,
    )
    return method_tools, thresholds

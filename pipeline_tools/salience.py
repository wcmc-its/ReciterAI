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

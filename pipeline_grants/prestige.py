"""Opportunity prestige signal (producer side) — see docs/funding-opportunity-prestige-spec.md.

v1 scope (validation-pruned): mechanism_tier + size_bucket -> score -> label, plus an
``is_honorific`` flag. ``sponsor_tier`` (curated table) and ``selectivity`` (no source)
are DEFERRED — emitted as null and excluded from the blend, which renormalizes over the
present signals (so a missing input is no-signal, never a 0 that drags the score down).
Pure + side-effect-free so it unit-tests without network/AWS, mirroring ``normalize`` /
``wcm_curated``. The DynamoDB attribute conversion lives here too (``prestige_item_attrs``)
so ``persist.build_grant_item`` just ``item.update(...)``.
"""
import math
import re

from utils.dynamodb_helpers import to_decimal

# --- mechanism tier (§3.1) -------------------------------------------------
# NIH-ish ordering. Keyed by activity code (R01, K23, DP2 …); '' -> default.
_PROGRAM_PREFIXES = {"DP", "RM", "UM", "UG", "UH", "UC", "UF", "UE", "UT", "PN", "PM", "PL"}
_R_FLAGSHIP = {"01", "35", "37", "61"}   # R01/R35/R37/R61 — major independent research
_R_SMALL = {"21", "03", "34", "36", "56"}  # exploratory / pilot / small


def mechanism_tier(mechanism: str) -> float:
    """Map an activity code to [0,1]. Unknown/curated ('' ) -> 0.3 (neutral-low)."""
    m = (mechanism or "").upper().strip()
    if not m:
        return 0.3
    if m[:2] in _PROGRAM_PREFIXES:        # DP2 New Innovator, UM1 …  — flagship/center
        return 1.0
    c = m[0]
    if c in ("P", "U"):                    # P30/P50 centers, U01/U54 cooperative
        return 1.0
    if c == "R":
        num = m[1:3]
        if num in _R_FLAGSHIP:
            return 0.85
        if num in _R_SMALL:
            return 0.4
        return 0.6
    if c == "K":                           # career development
        return 0.7
    if c in ("F", "T"):                    # fellowship / training
        return 0.5
    return 0.3


# --- size bucket (§3.2) ----------------------------------------------------
# Fixed-anchor log scale (NOT corpus min-max — that would re-scale every score
# whenever a new large opp lands, breaking cross-ingest stability). Unknown ->
# None (no-signal, renormalized away), never 0.
_LO, _HI = 1e4, 1e7  # $10k .. $10M
_SPAN = math.log10(_HI) - math.log10(_LO)


def size_bucket(opp) -> float | None:
    amt = opp.award_ceiling or opp.estimated_funding   # curated carries estimated_funding only
    if not amt or amt <= 0:
        return None
    x = (math.log10(amt) - math.log10(_LO)) / _SPAN
    return max(0.0, min(1.0, x))


# --- honorific flag (the prereq) -------------------------------------------
# Nomination-based recognition (unwinnable prize/medal/lectureship, or a recognition
# "Award") that the prestige sort must NOT float to the top of the reverse RD browse
# list (find-researchers bypasses the matcher, so it inherits no matcher-side filter).
# ponytail: title-regex heuristic calibrated on the 199-row curated set -> ~93% honorific,
# which fits a source that is curated *awards/prizes* with a "Nomination Deadline" column;
# the 7% spared are genuinely applyable (fellowships/scholarships/travel/career-dev).
# Mechanism-gated: an applyable NIH award carries an activity code (R35 "Outstanding
# Investigator Award", DP2), honorific recognition does not — so the broad "Award" tier
# only fires when mechanism is empty. SPS #1296 remains the authoritative gate; this is
# the upstream default so every consumer inherits it. Upgrade path: replace with the
# #1296 regex (or a curated is_honorific column) if precision matters.
_HONORIFIC_RE = re.compile(r"\b(prizes?|prix|medals?|lectureships?|laureate)\b", re.I)
_AWARD_RE = re.compile(r"\bawards?\b", re.I)
_APPLYABLE_RE = re.compile(
    r"\b(fellowships?|scholarships?|travel|pilot|seed|career[ -]development|"
    r"postbac\w*|seminar|residency|internship|traineeship|sabbatical)\b", re.I)


def is_honorific(opp) -> bool:
    t = opp.title or ""
    if _HONORIFIC_RE.search(t):
        return True
    if _AWARD_RE.search(t) and not opp.mechanism and not _APPLYABLE_RE.search(t):
        return True
    return False


# --- composite (§3) --------------------------------------------------------
_W_MECH, _W_SIZE = 0.4, 0.2   # sponsor_tier/selectivity deferred -> excluded from blend
_FLAGSHIP, _MAJOR = 0.8, 0.55  # label thresholds (§3.5; open decision §7 — provisional)


def label_for(score: float) -> str:
    if score >= _FLAGSHIP:
        return "Flagship"
    if score >= _MAJOR:
        return "Major"
    return "Standard"


def _fmt_amt(n) -> str:
    if not n:
        return ""
    if n >= 1_000_000:
        return f"${n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"${round(n / 1000)}K"
    return f"${n}"


def _rationale(opp, amt) -> str:
    bits = []
    if opp.mechanism:
        bits.append(opp.mechanism)
    elif opp.program_type:
        bits.append(opp.program_type)
    if amt:
        bits.append(_fmt_amt(amt) + (" ceiling" if opp.award_ceiling else ""))
    if opp.sponsor:
        bits.append(opp.sponsor)
    return " · ".join(bits)


def compute_prestige(opp) -> dict:
    """Per-opportunity prestige block. Renormalizes over present signals (§3)."""
    mech = mechanism_tier(opp.mechanism)
    size = size_bucket(opp)
    parts = [(_W_MECH, mech)]
    if size is not None:
        parts.append((_W_SIZE, size))
    wsum = sum(w for w, _ in parts)
    score = max(0.0, min(1.0, sum(w * v for w, v in parts) / wsum))
    amt = opp.award_ceiling or opp.estimated_funding
    return {
        "score": round(score, 4),
        "mechanism_tier": round(mech, 4),
        "size_bucket": round(size, 4) if size is not None else None,
        "sponsor_tier": None,   # deferred (no curated table in v1)
        "selectivity": None,    # deferred (no award-rate source)
        "label": label_for(score),
        "rationale": _rationale(opp, amt),
    }


def _n(value) -> dict:
    return {"N": str(to_decimal(value))}


def prestige_item_attrs(opp) -> dict:
    """DynamoDB attribute-format additions for build_grant_item: prestige (M) + is_honorific."""
    p = compute_prestige(opp)
    m = {
        "score": _n(p["score"]),
        "mechanism_tier": _n(p["mechanism_tier"]),
        "size_bucket": _n(p["size_bucket"]) if p["size_bucket"] is not None else {"NULL": True},
        "sponsor_tier": {"NULL": True},
        "selectivity": {"NULL": True},
        "label": {"S": p["label"]},
        "rationale": {"S": p["rationale"]},
    }
    return {"prestige": {"M": m}, "is_honorific": {"BOOL": is_honorific(opp)}}

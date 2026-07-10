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


def mechanism_tier(mechanism: str) -> float | None:
    """Map an activity code to [0,1]. No code at all (NSF/foundation/curated) -> None,
    so it ABSTAINS from the blend (renormalized away) instead of injecting a 0.3
    constant that compresses every non-NIH score into a flat low band. A present-
    but-untiered NIH code (Z99/X01) still returns 0.3 — it IS a real mechanism, just niche."""
    m = (mechanism or "").upper().strip()
    if not m:
        return None
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
# list (find-researchers bypasses the matcher, so it inherits no matcher-side filter),
# and that the SPS forward matcher hard-excludes via `must_not term isHonorific:true`.
# This flag is the SOLE honorific gate for both consumers — SPS #1628 removed its
# redundant title regex — so precision here matters directly.
#
# Two tiers:
#   1. explicit prize/medal/lectureship/laureate wording -> always honorific.
#   2. a bare "Award" with no NIH activity code -> honorific ONLY when it is neither a
#      known applyable *type* (fellowship/career-dev/…) NOR from a known open-competition
#      *grantmaker*. The sponsor veto (#314) is what keeps the tier-2 net from swallowing
#      real foundation/agency grants titled "…Award" (DoD CDMRP, Hartwell, Damon Runyon,
#      Komen, disease-foundation research awards): those are open competitions a PI applies
#      to, not honors. Verified against the live corpus — the allowlist below vetoes 41
#      award-tier items and every one is a real grant (zero true honors).
# ponytail: sponsor allowlist, not a typed field — the corpus carries no opportunity_type.
# Ceiling: it can't help rows with a BLANK sponsor (a wcm_curated ingest gap; ~9 real
# grants like Hirschl/Komen stay mis-flagged) and won't auto-cover a new grantmaker. The
# durable fix is a typed opportunity_type at ingest (ReciterAI #290). Add funders HERE as
# they recur; do NOT reintroduce a title-text applyable heuristic (that is what #1628 cut).
_HONORIFIC_RE = re.compile(r"\b(prizes?|prix|medals?|lectureships?|laureate)\b", re.I)
_AWARD_RE = re.compile(r"\bawards?\b", re.I)
_APPLYABLE_RE = re.compile(
    r"\b(fellowships?|scholarships?|travel|pilot|seed|career[ -]development|"
    r"postbac\w*|seminar|residency|internship|traineeship|sabbatical)\b", re.I)
# Open-competition grantmakers whose "…Award" listings are applyable grants, not honors.
# Matched as case-folded substrings of the sponsor field. Kept deliberately tight to
# disease foundations + agencies that run open competitions; professional societies and
# academies (AACR/ASBMB/NAS/AAAS/SfN/ICIS/AAMC) are excluded because they confer honors
# titled "Award".
_APPLYABLE_SPONSORS = frozenset({
    "department of defense", "hartwell", "damon runyon", "burroughs wellcome",
    "research to prevent blindness", "rheumatology research foundation",
    "cystic fibrosis foundation", "crohn's & colitis", "multiple sclerosis society",
    "children's cancer research fund", "colorectal cancer alliance", "curing kids cancer",
    "dermatology foundation", "emerald foundation", "harrington discovery",
    "immunodeficiency canada", "lung cancer research foundation", "mark foundation for cancer",
    "musculoskeletal tumor society", "patient-centered outcomes research", "st. baldrick",
    "breakthrough t1d", "american sleep medicine foundation", "american cancer society",
    "simons foundation", "american heart association",
})


def _is_applyable_sponsor(sponsor: str) -> bool:
    s = (sponsor or "").lower()
    return any(k in s for k in _APPLYABLE_SPONSORS)


def is_honorific(opp) -> bool:
    t = opp.title or ""
    if _HONORIFIC_RE.search(t):
        return True
    if (
        _AWARD_RE.search(t)
        and not opp.mechanism
        and not _APPLYABLE_RE.search(t)
        and not _is_applyable_sponsor(opp.sponsor)
    ):
        return True
    return False


# --- sponsor tier (§3.3) ---------------------------------------------------
# Curated funder-prestige map — the signal mechanism_tier CAN'T see (it's NIH
# activity-code only). Covers the non-NIH federal agencies + recurring prize/
# foundation bodies actually in the corpus. NIH is intentionally ABSENT: its
# prestige is already carried by mechanism_tier, so listing it here would
# double-count (spec §3.3/§7). Unknown funder -> None (abstain), so the long tail
# of one-off agencies and person-name noise in the grants_gov feed falls back to
# size. ponytail: ~dozen substring keys cover the corpus; tiers are provisional —
# tune them HERE (producer), never in the consumer. Extend as new funders recur.
# Values are a per-funder BASELINE, deliberately kept in the Major band (0.55–0.8)
# so sponsor-alone (no award size) reads as "Major", and size lifts the larger
# grants into Flagship — sponsor_tier is a constant per funder, so size is what
# must differentiate WITHIN a funder (unlike NIH, where mechanism already varies
# per grant). Above-0.8 baselines would flood Flagship.
_SPONSOR_TIER = {
    "national science foundation": 0.7,        # NSF (incl. "U.S. National Science Foundation")
    "darpa": 0.75,
    "aeronautics and space": 0.7,              # NASA
    "national academ": 0.8,                    # NAS / NAM / National Academies (elite; mostly honorific-gated)
    "association for cancer research": 0.8,    # AACR
    "advancement of science": 0.75,            # AAAS
    "society for neuroscience": 0.7,
    "brain & behavior research": 0.65,
    "centers for disease control": 0.6,
    "health resources and services": 0.55,
    "institute of food and agriculture": 0.55,  # USDA NIFA
}


def sponsor_tier(opp) -> float | None:
    s = (opp.sponsor or "").lower()
    for key, tier in _SPONSOR_TIER.items():
        if key in s:
            return tier
    return None


# --- composite (§3) --------------------------------------------------------
_W_MECH, _W_SIZE, _W_SPONSOR = 0.4, 0.2, 0.4  # renormalized over PRESENT signals only
_NO_SIGNAL = 0.3   # score when nothing is known (untiered funder, no code, no amount)
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
    """Per-opportunity prestige block. Renormalizes over whichever signals are
    PRESENT (§3) — a missing input abstains, never injects a constant. NIH opps
    score on mechanism+size; non-NIH on sponsor_tier+size; nothing known -> floor."""
    mech = mechanism_tier(opp.mechanism)
    size = size_bucket(opp)
    spon = sponsor_tier(opp)
    parts = [(w, v) for w, v in ((_W_MECH, mech), (_W_SIZE, size), (_W_SPONSOR, spon)) if v is not None]
    if parts:
        wsum = sum(w for w, _ in parts)
        score = max(0.0, min(1.0, sum(w * v for w, v in parts) / wsum))
    else:
        score = _NO_SIGNAL
    amt = opp.award_ceiling or opp.estimated_funding
    return {
        "score": round(score, 4),
        "mechanism_tier": round(mech, 4) if mech is not None else None,
        "size_bucket": round(size, 4) if size is not None else None,
        "sponsor_tier": round(spon, 4) if spon is not None else None,
        "selectivity": None,    # deferred (no award-rate source)
        "label": label_for(score),
        "rationale": _rationale(opp, amt),
    }


def _n(value) -> dict:
    return {"N": str(to_decimal(value))}


def prestige_item_attrs(opp) -> dict:
    """DynamoDB attribute-format additions for build_grant_item: prestige (M) + is_honorific."""
    p = compute_prestige(opp)
    _opt = lambda v: _n(v) if v is not None else {"NULL": True}
    m = {
        "score": _n(p["score"]),
        "mechanism_tier": _opt(p["mechanism_tier"]),
        "size_bucket": _opt(p["size_bucket"]),
        "sponsor_tier": _opt(p["sponsor_tier"]),
        "selectivity": {"NULL": True},
        "label": {"S": p["label"]},
        "rationale": {"S": p["rationale"]},
    }
    return {"prestige": {"M": m}, "is_honorific": {"BOOL": is_honorific(opp)}}

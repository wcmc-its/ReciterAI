"""Two-stage denoise: Stage A = deterministic regex/rules gate (this file, regex_gate).
Stage B (LLM judge) is added in a later task."""
import re

from pipeline_grants.models import Opportunity
from utils.iso_clock import now_iso

# Non-research opportunity types we never surface (matched against title).
_EXCLUDE_TYPE_RE = re.compile(
    r"\b(travel|conference|symposium|workshop|registration|poster|"
    r"prize|medal|lectureship|equipment|instrumentation|membership|"
    r"subscription|publication fee|page charge|open access fee)\b",
    re.IGNORECASE,
)


def regex_gate(opp: Opportunity):
    """Return (kept, reason). reason is '' when kept."""
    if _EXCLUDE_TYPE_RE.search(opp.title or ""):
        return False, "excluded type (regex)"
    if opp.due_date and opp.status != "continuous":
        # ISO dates compare lexicographically; now_iso() is "YYYY-MM-DD...Z".
        if opp.due_date < now_iso()[:10]:
            return False, "expired deadline"
    return True, ""


from utils.bedrock_client import HAIKU_MODEL

_STAGES = ("grad", "postdoc", "early", "mid", "senior")

_JUDGE_SYSTEM = (
    "You triage research funding opportunities for a medical college. "
    "Decide if an opportunity is a substantive research opportunity (not a travel/conference/"
    "prize/equipment award), and rate how appealing it is to each career stage. "
    "Respond ONLY with JSON: "
    '{"is_research": bool, "reason": str, '
    '"appeal_by_stage": {"grad": 0-1, "postdoc": 0-1, "early": 0-1, "mid": 0-1, "senior": 0-1}}. '
    "Small awards should score high for trainees and low for senior PIs."
)


def judge_opportunity(opp: Opportunity, bedrock) -> dict:
    """Stage B: LLM relevance + career-stage-relative appeal verdict."""
    user = (
        f"Title: {opp.title}\n"
        f"Sponsor: {opp.sponsor}\n"
        f"Award ceiling: {opp.award_ceiling}\n"
        f"Eligibility: {opp.eligibility_raw}\n"
        f"Synopsis: {opp.synopsis[:4000]}"
    )
    raw = bedrock.call_json(
        model=HAIKU_MODEL,
        messages=[{"role": "user", "content": user}],
        system=_JUDGE_SYSTEM,
    )
    appeal_in = raw.get("appeal_by_stage") or {}
    return {
        "is_research": bool(raw.get("is_research", False)),
        "reason": raw.get("reason", "") or "",
        "appeal_by_stage": {s: float(appeal_in.get(s, 0.0) or 0.0) for s in _STAGES},
    }

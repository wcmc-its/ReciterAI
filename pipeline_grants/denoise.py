"""Two-stage denoise: Stage A = deterministic regex/rules gate (regex_gate);
Stage B = LLM relevance + career-stage appeal judge (judge_opportunity)."""
import re

from pipeline_grants.models import Opportunity
from utils.bedrock_client import HAIKU_MODEL
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


_STAGES = ("grad", "postdoc", "early", "mid", "senior")

_JUDGE_SYSTEM = (
    "You triage research funding opportunities for a medical college. "
    "Decide if an opportunity is a substantive research opportunity (not a travel/conference/"
    "prize/equipment award), rate how appealing it is to each career stage, and extract "
    "structured eligibility facts FROM THE ELIGIBILITY TEXT (title/synopsis are context only; "
    "when the text does not state a fact, use the empty list or 'not_stated' — never guess). "
    "Respond ONLY with JSON:\n"
    "{\n"
    '  "is_research": bool,\n'
    '  "reason": str,\n'
    '  "appeal_by_stage": {"grad": 0-1, "postdoc": 0-1, "early": 0-1, "mid": 0-1, "senior": 0-1},\n'
    '  "eligibility": {\n'
    '    "applicant_org_types": [subset of: "higher_ed","nonprofit","for_profit","small_business",'
    '"state_government","local_government","tribal_government","federal_agency","hospital",'
    '"foreign_org","individual","other","unrestricted"],  // who may APPLY; [] = not described;'
    ' ["unrestricted"] = explicitly open to all\n'
    '    "career_stages": [subset of: "undergraduate","graduate_student","postdoc",'
    '"early_career_faculty","mid_career_faculty","senior_faculty","any_faculty","clinician"],'
    '  // stages the funded PERSON must be in; [] = no person-level restriction\n'
    '    "degree_required": [subset of: "phd","md","md_or_phd_either","other_doctoral",'
    '"nursing_degree","other_clinical_doctorate"],  // [] = none stated\n'
    '    "citizenship_requirement": "us_citizen_or_permanent_resident_required" | '
    '"visa_holders_eligible" | "foreign_institutions_eligible" | '
    '"foreign_institutions_ineligible" | "not_stated",\n'
    '    "esi_targeted": bool,        // ESI/new investigators explicitly targeted or required\n'
    '    "limited_submission": bool,  // institution capped at N applications / internal competition\n'
    '    "cost_sharing_required": bool,  // required, not merely encouraged\n'
    '    "individual_award": bool     // award follows a named person (fellowship/career award)\n'
    "  }\n"
    "}\n"
    "Small awards should score high for trainees and low for senior PIs. "
    "A stated PRIORITY for a group (e.g. ESI) is esi_targeted=true but is NOT a career_stages "
    "restriction; only hard requirements restrict career_stages."
)

# Structured eligibility enum sets (#290) — the judge extracts these from eligibility prose so
# the SPS mapper can derive UI filter flags from structured fields instead of the prose regexes
# that fire on 2.8% of items. Full schema + audit in docs/grant-matching-measurements-runbook.md §2.
_ORG_TYPES = frozenset({
    "higher_ed", "nonprofit", "for_profit", "small_business", "state_government",
    "local_government", "tribal_government", "federal_agency", "hospital", "foreign_org",
    "individual", "other", "unrestricted",
})
_CAREER_STAGES = frozenset({
    "undergraduate", "graduate_student", "postdoc", "early_career_faculty",
    "mid_career_faculty", "senior_faculty", "any_faculty", "clinician",
})
_DEGREES = frozenset({
    "phd", "md", "md_or_phd_either", "other_doctoral", "nursing_degree", "other_clinical_doctorate",
})
_CITIZENSHIP = frozenset({
    "us_citizen_or_permanent_resident_required", "visa_holders_eligible",
    "foreign_institutions_eligible", "foreign_institutions_ineligible", "not_stated",
})
_ELIGIBILITY_LISTS = (
    ("applicant_org_types", _ORG_TYPES),
    ("career_stages", _CAREER_STAGES),
    ("degree_required", _DEGREES),
)
_ELIGIBILITY_BOOLS = ("esi_targeted", "limited_submission", "cost_sharing_required", "individual_award")


def _clean_eligibility(raw, model_id: str):
    """Validate the judge's structured eligibility block; return a persist-ready dict or ``None``.

    Fail-open (mirrors the ``match_attrs`` contract): an absent/non-dict block or ANY enum
    violation returns ``None`` so persist omits the field and SPS falls back to the legacy prose
    regexes — a low-confidence extraction never hard-drops an opportunity. List fields are
    deduped+sorted for byte-stable persistence; provenance is stamped in code, never asked of
    the model."""
    if not isinstance(raw, dict):
        return None
    clean: dict = {}
    for field, allowed in _ELIGIBILITY_LISTS:
        values = raw.get(field) or []
        if not isinstance(values, list) or any(v not in allowed for v in values):
            return None
        clean[field] = sorted(set(values))
    citizenship = raw.get("citizenship_requirement") or "not_stated"
    if citizenship not in _CITIZENSHIP:
        return None
    clean["citizenship_requirement"] = citizenship
    for field in _ELIGIBILITY_BOOLS:
        clean[field] = bool(raw.get(field, False))
    clean["extracted_by"] = model_id
    clean["extracted_at"] = now_iso()
    return clean


def judge_opportunity(opp: Opportunity, bedrock) -> dict:
    """Stage B: LLM relevance + career-stage-relative appeal verdict + structured eligibility."""
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
        "eligibility": _clean_eligibility(raw.get("eligibility"), HAIKU_MODEL),
    }

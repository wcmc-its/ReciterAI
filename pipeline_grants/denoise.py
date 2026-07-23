"""Two-stage denoise: Stage A = deterministic regex/rules gate (regex_gate);
Stage B = LLM relevance + career-stage appeal judge (judge_opportunity)."""
import re

from pipeline_grants.models import Opportunity
from utils.bedrock_client import SONNET_MODEL
from utils.iso_clock import now_iso

# Eligibility-map schema version. Bump on any facet/enum change so the backfill can targeted-re-run
# only stale-version items (keyed on this) instead of a from-scratch redo. v1 = the shipped 8-facet
# block (#290); v2 = the data-sized person-level + institutional facets (2026-07 sizing passes).
ELIGIBILITY_SCHEMA_VERSION = "2.0.0"

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
    "Decide (a) if an opportunity is a substantive research opportunity (not a travel/conference/"
    "prize/equipment award); (b) if its subject is biomedical, clinical, public-health or "
    "life-sciences relevant to a medical college — NOT off-domain research such as construction/"
    "occupational safety, agriculture, defense/aerospace engineering, or physical sciences with no "
    "health application; (c) rate how appealing it is to each career stage; and (d) extract "
    "structured eligibility facts FROM THE ELIGIBILITY TEXT (title/synopsis are context only; "
    "when the text does not state a fact, use the empty list or 'not_stated' — never guess). "
    "Respond ONLY with JSON:\n"
    "{\n"
    '  "is_research": bool,\n'
    '  "is_biomedical_relevant": bool,\n'
    '  "reason": str,\n'
    '  "appeal_by_stage": {"grad": 0-1, "postdoc": 0-1, "early": 0-1, "mid": 0-1, "senior": 0-1},\n'
    '  "eligibility": {\n'
    '    "applicant_org_types": [subset of: "higher_ed","nonprofit","for_profit","small_business",'
    '"state_government","local_government","tribal_government","federal_agency","hospital",'
    '"foreign_org","individual","other","unrestricted"],  // who may APPLY; [] = not described;'
    ' ["unrestricted"] = explicitly open to all\n'
    '    "career_stages": [subset of: "undergraduate","graduate_student","postdoc",'
    '"early_career_faculty","mid_career_faculty","senior_faculty","any_faculty","clinician",'
    '"resident","clinical_fellow","late_stage_postdoc"],'
    '  // stages the funded PERSON must be in; [] = no person-level restriction\n'
    '    "degree_required": [subset of: "phd","md","md_or_phd_either","other_doctoral",'
    '"nursing_degree","other_clinical_doctorate","do","pharmd","dds","dvm"],  // [] = none stated\n'
    '    "citizenship_requirement": "us_citizen_or_permanent_resident_required" | '
    '"visa_holders_eligible" | "foreign_institutions_eligible" | '
    '"foreign_institutions_ineligible" | "not_stated",\n'
    '    "esi_targeted": bool,        // ESI/new investigators explicitly targeted or required\n'
    '    "limited_submission": bool,  // institution capped at N applications / internal competition\n'
    '    "cost_sharing_required": bool,  // required, not merely encouraged\n'
    '    "individual_award": bool,    // award follows a named person (fellowship/career award)\n'
    '    "nomination_gated": bool,    // applicant must be institution-NOMINATED or INVITED (not open application)\n'
    '    "nominee_cap": int|null,     // per-institution nominee limit if stated, else null\n'
    '    "career_window": {"anchor": "degree"|"first_faculty_appt"|"residency_completion"|"postdoc_start",'
    ' "max_years": int|null, "min_years": int|null} | null,  // eligibility window vs a career milestone; null if none\n'
    '    "mentorship": "mentored_required" | "independent_required" | "not_stated",'
    '  // mentored (named mentor) vs must-be-independent; not_stated if neither\n'
    '    "mentor_requirements": str,  // criteria the MENTOR must meet (e.g. active R01), else ""\n'
    '    "funding_history_restriction": [subset of: "no_concurrent_career_award","no_concurrent_major_award",'
    '"no_prior_independent_pi","new_investigator_only","esi_only","requires_prior_k_award"],  // [] if none\n'
    '    "faculty_track_required": [subset of: "tenure_track","research_track","clinical_track",'
    '"independent_position","any"],  // track(s) the applicant must hold; [] if not stated\n'
    '    "tracks_excluded": [subset of: "tenure_track","research_track","clinical_track","independent_position"],'
    '  // tracks explicitly EXCLUDED (e.g. "assistant prof but NOT research/adjunct"); [] if none\n'
    '    "min_research_effort_pct": int|null,  // protected-research-effort floor % if stated (e.g. 75), else null\n'
    '    "institutional_eligibility": [ {"value": "research_funding_capped"|"research_funding_floor_required"|'
    '"idea_state_only"|"epscor_jurisdiction_only"|"minority_serving_only"|"ctsa_required"|'
    '"nci_designated_required"|"accredited_degree_granting_required", "polarity": "exclusion"|"prerequisite"} ]'
    '  // restrictions on the APPLICANT INSTITUTION\'s characteristics; [] if none\n'
    "  }\n"
    "}\n"
    "Small awards should score high for trainees and low for senior PIs. "
    "A stated PRIORITY for a group (e.g. ESI) is esi_targeted=true but is NOT a career_stages "
    "restriction; only hard requirements restrict career_stages. "
    "nomination_gated=true ONLY when internal nomination/invitation is REQUIRED to apply. "
    "career_window ONLY when a hard time limit vs a milestone is stated (e.g. 'within 5 years of first "
    "faculty appointment', 'no more than 2 years postdoc'). institutional_eligibility is for the APPLICANT "
    "INSTITUTION's characteristics ONLY (research intensity, IDeA/EPSCoR jurisdiction, minority-serving, "
    "CTSA/NCI/accreditation) — a foreign/domestic restriction goes in citizenship_requirement, NOT here."
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
    "resident", "clinical_fellow", "late_stage_postdoc",
})
_DEGREES = frozenset({
    "phd", "md", "md_or_phd_either", "other_doctoral", "nursing_degree", "other_clinical_doctorate",
    "do", "pharmd", "dds", "dvm",
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

# v2 facets (2026-07 sizing passes). Persisted alongside the core-8; validated FAIL-SOFT (a bad value
# drops only that field, never the whole map) so richer extraction can't enlarge the whole-block fail-open.
_MENTORSHIP = frozenset({"mentored_required", "independent_required", "not_stated"})
_FUNDING_HISTORY = frozenset({
    "no_concurrent_career_award", "no_concurrent_major_award", "no_prior_independent_pi",
    "new_investigator_only", "esi_only", "requires_prior_k_award",
})
_FACULTY_TRACKS = frozenset({"tenure_track", "research_track", "clinical_track", "independent_position", "any"})
_TRACKS_EXCLUDED = frozenset({"tenure_track", "research_track", "clinical_track", "independent_position"})
_CAREER_ANCHORS = frozenset({"degree", "first_faculty_appt", "residency_completion", "postdoc_start"})
_INSTITUTIONAL_VALUES = frozenset({
    "research_funding_capped", "research_funding_floor_required", "idea_state_only",
    "epscor_jurisdiction_only", "minority_serving_only", "ctsa_required",
    "nci_designated_required", "accredited_degree_granting_required",
})
_POLARITY = frozenset({"exclusion", "prerequisite"})
# v2 list facets validated fail-soft against a single enum (name, allowed).
_V2_ENUM_LISTS = (
    ("funding_history_restriction", _FUNDING_HISTORY),
    ("faculty_track_required", _FACULTY_TRACKS),
    ("tracks_excluded", _TRACKS_EXCLUDED),
)


def _clean_enum_subset(raw, field, allowed):
    """Fail-soft: sorted valid subset of a list field (non-list / invalid values -> dropped)."""
    values = raw.get(field)
    if not isinstance(values, list):
        return []
    return sorted({v for v in values if v in allowed})


def _clean_nonneg_int(value):
    """A non-negative int, or ``None`` (fail-soft). ``bool`` is not an int here."""
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) and value >= 0 else None


def _clean_career_window(raw):
    """`{anchor, max_years?, min_years?}` or ``None`` — a bad anchor drops the whole window."""
    if not isinstance(raw, dict) or raw.get("anchor") not in _CAREER_ANCHORS:
        return None
    window = {"anchor": raw["anchor"]}
    for key in ("max_years", "min_years"):
        value = _clean_nonneg_int(raw.get(key))
        if value is not None:
            window[key] = value
    return window


def _clean_institutional(raw):
    """List of `{value, polarity}` — invalid entries dropped, deduped, stable-sorted."""
    if not isinstance(raw, list):
        return []
    valid = {
        (e["value"], e["polarity"])
        for e in raw
        if isinstance(e, dict) and e.get("value") in _INSTITUTIONAL_VALUES and e.get("polarity") in _POLARITY
    }
    return [{"value": v, "polarity": p} for v, p in sorted(valid)]


def _clean_eligibility(raw, model_id: str):
    """Validate the judge's structured eligibility block; return a persist-ready dict or ``None``.

    The **core-8** facets keep the original whole-block fail-open contract (mirrors ``match_attrs``):
    an absent/non-dict block or ANY core enum violation returns ``None`` so persist omits the field and
    SPS falls back to the legacy prose regexes — a low-confidence core extraction never hard-drops an
    opportunity. The **v2** facets (2026-07 sizing) are FAIL-SOFT: a bad value drops only that field, so
    richer extraction can't enlarge the whole-block blast radius. List fields are deduped+sorted for
    byte-stable persistence; ``schema_version``/provenance are stamped in code, never asked of the model."""
    if not isinstance(raw, dict):
        return None
    clean: dict = {}
    # --- core-8: whole-block fail-open (unchanged contract) ---
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
    # --- v2 facets: per-field fail-soft ---
    clean["nomination_gated"] = bool(raw.get("nomination_gated", False))
    mentorship = raw.get("mentorship")
    clean["mentorship"] = mentorship if mentorship in _MENTORSHIP else "not_stated"
    mentor_req = raw.get("mentor_requirements")
    clean["mentor_requirements"] = mentor_req if isinstance(mentor_req, str) else ""
    for field, allowed in _V2_ENUM_LISTS:
        clean[field] = _clean_enum_subset(raw, field, allowed)
    clean["institutional_eligibility"] = _clean_institutional(raw.get("institutional_eligibility"))
    nominee_cap = _clean_nonneg_int(raw.get("nominee_cap"))
    if nominee_cap is not None:
        clean["nominee_cap"] = nominee_cap
    effort = _clean_nonneg_int(raw.get("min_research_effort_pct"))
    if effort is not None:
        clean["min_research_effort_pct"] = effort
    window = _clean_career_window(raw.get("career_window"))
    if window is not None:
        clean["career_window"] = window
    clean["schema_version"] = ELIGIBILITY_SCHEMA_VERSION
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
        model=SONNET_MODEL,
        messages=[{"role": "user", "content": user}],
        system=_JUDGE_SYSTEM,
    )
    appeal_in = raw.get("appeal_by_stage") or {}
    return {
        "is_research": bool(raw.get("is_research", False)),
        # Fail-open: only an explicit False drops the grant (see ingest gate). A missing
        # field keeps it, so a judge reply that omits the key never nukes recall.
        "is_biomedical_relevant": bool(raw.get("is_biomedical_relevant", True)),
        "reason": raw.get("reason", "") or "",
        "appeal_by_stage": {s: float(appeal_in.get(s, 0.0) or 0.0) for s in _STAGES},
        "eligibility": _clean_eligibility(raw.get("eligibility"), SONNET_MODEL),
    }

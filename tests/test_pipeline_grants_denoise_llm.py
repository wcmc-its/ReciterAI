from pipeline_grants.models import Opportunity
from pipeline_grants.denoise import ELIGIBILITY_SCHEMA_VERSION, judge_opportunity
from utils.bedrock_client import SONNET_MODEL


class _FakeBedrock:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def call_json(self, model, messages, **kwargs):
        self.calls.append((model, messages))
        return self.payload


def _opp():
    return Opportunity(opportunity_id="grants_gov:1", source="grants_gov", source_id="1",
                       source_url="http://x", sponsor="NIH", title="R01 Research",
                       synopsis="A substantive research program.", award_ceiling=600000)


def test_judge_returns_structured_verdict():
    fake = _FakeBedrock({"is_research": True, "is_biomedical_relevant": True, "reason": "substantive",
                         "appeal_by_stage": {"grad": 0.2, "postdoc": 0.6, "early": 0.9, "mid": 0.8, "senior": 0.7}})
    verdict = judge_opportunity(_opp(), fake)
    assert verdict["is_research"] is True
    assert verdict["is_biomedical_relevant"] is True
    assert verdict["appeal_by_stage"]["early"] == 0.9
    assert fake.calls, "bedrock should be called"


def test_judge_flags_off_domain_grant():
    # An occupational-safety NOFO is real research but off-domain for a medical college (#293).
    verdict = judge_opportunity(_opp(), _FakeBedrock({"is_research": True, "is_biomedical_relevant": False}))
    assert verdict["is_research"] is True
    assert verdict["is_biomedical_relevant"] is False


def test_judge_coerces_missing_fields():
    verdict = judge_opportunity(_opp(), _FakeBedrock({"is_research": False}))
    assert verdict["is_research"] is False
    # Fail-open: a reply omitting is_biomedical_relevant keeps the grant, never drops it.
    assert verdict["is_biomedical_relevant"] is True
    assert verdict["reason"] == ""
    assert verdict["appeal_by_stage"] == {"grad": 0.0, "postdoc": 0.0, "early": 0.0, "mid": 0.0, "senior": 0.0}
    assert verdict["eligibility"] is None  # no block -> fail open (SPS falls back to prose regexes)


# --- structured eligibility extraction (#290) -----------------------------------------------

def test_judge_extracts_and_normalizes_eligibility():
    fake = _FakeBedrock({"is_research": True, "eligibility": {
        # unsorted + duplicated -> normalized to sorted/deduped for byte-stable persistence
        "applicant_org_types": ["small_business", "higher_ed", "small_business"],
        "career_stages": ["early_career_faculty"],
        "degree_required": [],
        "citizenship_requirement": "us_citizen_or_permanent_resident_required",
        "esi_targeted": True, "limited_submission": False,
        "cost_sharing_required": False, "individual_award": True,
    }})
    elig = judge_opportunity(_opp(), fake)["eligibility"]
    assert elig["applicant_org_types"] == ["higher_ed", "small_business"]
    assert elig["career_stages"] == ["early_career_faculty"]
    assert elig["degree_required"] == []
    assert elig["citizenship_requirement"] == "us_citizen_or_permanent_resident_required"
    assert elig["esi_targeted"] is True and elig["individual_award"] is True
    assert elig["extracted_by"] == SONNET_MODEL      # provenance stamped in code, not the prompt
    assert elig["schema_version"] == ELIGIBILITY_SCHEMA_VERSION
    assert isinstance(elig["extracted_at"], str) and elig["extracted_at"]
    # v2 facets default cleanly when the judge omits them (no-op for the shipped core-8 payload).
    assert elig["nomination_gated"] is False and elig["mentorship"] == "not_stated"
    assert elig["faculty_track_required"] == [] and elig["institutional_eligibility"] == []
    assert "career_window" not in elig and "nominee_cap" not in elig  # optional scalars omitted when null


def test_judge_extracts_v2_facets_and_fails_soft():
    # A career-award payload: v2 facets extract; a bad faculty_track / institutional entry / anchor
    # drops ONLY that field (fail-soft) while the rest of the map — incl. the core-8 — persists.
    fake = _FakeBedrock({"is_research": True, "eligibility": {
        "career_stages": ["early_career_faculty"],
        "citizenship_requirement": "not_stated",
        "nomination_gated": True, "nominee_cap": 1,
        "mentorship": "independent_required",
        "career_window": {"anchor": "first_faculty_appt", "max_years": 5},
        "funding_history_restriction": ["new_investigator_only", "bogus_rule"],  # bad value dropped
        "faculty_track_required": ["tenure_track", "professor"],                 # bad value dropped
        "min_research_effort_pct": 75,
        "institutional_eligibility": [
            {"value": "idea_state_only", "polarity": "exclusion"},
            {"value": "not_an_enum", "polarity": "exclusion"},                   # dropped
            {"value": "epscor_jurisdiction_only", "polarity": "bogus"},          # dropped (bad polarity)
        ],
    }})
    elig = judge_opportunity(_opp(), fake)["eligibility"]
    assert elig is not None                                   # fail-SOFT: map survives the bad values
    assert elig["career_stages"] == ["early_career_faculty"]  # core-8 intact
    assert elig["nomination_gated"] is True and elig["nominee_cap"] == 1
    assert elig["mentorship"] == "independent_required"
    assert elig["career_window"] == {"anchor": "first_faculty_appt", "max_years": 5}
    assert elig["funding_history_restriction"] == ["new_investigator_only"]
    assert elig["faculty_track_required"] == ["tenure_track"]
    assert elig["min_research_effort_pct"] == 75
    assert elig["institutional_eligibility"] == [{"value": "idea_state_only", "polarity": "exclusion"}]


def test_judge_bad_career_window_anchor_drops_only_the_window():
    fake = _FakeBedrock({"is_research": True, "eligibility": {
        "citizenship_requirement": "not_stated",
        "career_window": {"anchor": "invented_anchor", "max_years": 3},
    }})
    elig = judge_opportunity(_opp(), fake)["eligibility"]
    assert elig is not None and "career_window" not in elig  # bad anchor -> window omitted, map kept


def test_judge_drops_eligibility_on_enum_violation():
    # A value outside the enum set fails the whole block -> None (never persist a bad extraction).
    fake = _FakeBedrock({"is_research": True, "eligibility": {
        "applicant_org_types": ["university"],  # not a valid enum member
        "citizenship_requirement": "not_stated",
    }})
    assert judge_opportunity(_opp(), fake)["eligibility"] is None


def test_judge_defaults_citizenship_when_absent():
    fake = _FakeBedrock({"is_research": True, "eligibility": {"career_stages": ["postdoc"]}})
    elig = judge_opportunity(_opp(), fake)["eligibility"]
    assert elig["citizenship_requirement"] == "not_stated"
    assert elig["applicant_org_types"] == [] and elig["career_stages"] == ["postdoc"]

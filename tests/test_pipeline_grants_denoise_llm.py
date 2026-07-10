from pipeline_grants.models import Opportunity
from pipeline_grants.denoise import judge_opportunity
from utils.bedrock_client import HAIKU_MODEL


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
    fake = _FakeBedrock({"is_research": True, "reason": "substantive",
                         "appeal_by_stage": {"grad": 0.2, "postdoc": 0.6, "early": 0.9, "mid": 0.8, "senior": 0.7}})
    verdict = judge_opportunity(_opp(), fake)
    assert verdict["is_research"] is True
    assert verdict["appeal_by_stage"]["early"] == 0.9
    assert fake.calls, "bedrock should be called"


def test_judge_coerces_missing_fields():
    verdict = judge_opportunity(_opp(), _FakeBedrock({"is_research": False}))
    assert verdict["is_research"] is False
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
    assert elig["extracted_by"] == HAIKU_MODEL       # provenance stamped in code, not the prompt
    assert isinstance(elig["extracted_at"], str) and elig["extracted_at"]


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

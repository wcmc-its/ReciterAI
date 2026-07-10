from pipeline_grants.models import Opportunity
from pipeline_grants.denoise import judge_opportunity


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

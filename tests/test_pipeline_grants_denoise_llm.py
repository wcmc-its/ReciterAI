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

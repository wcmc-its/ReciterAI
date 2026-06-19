from decimal import Decimal
from pipeline_grants.models import Opportunity
from pipeline_grants.persist import build_grant_item


def _opp():
    return Opportunity(opportunity_id="grants_gov:359855", source="grants_gov", source_id="359855",
                       source_url="http://x", sponsor="NIH", title="T", synopsis="S",
                       award_ceiling=600000, due_date="2026-10-19", status="posted",
                       ingested_at="2026-06-19T00:00:00Z")


def test_build_grant_item_keys_and_types():
    dense = {"breast_cancer": {"score": 0.95, "rationale": "rb"},
             "biomedical_informatics": {"score": 0.6, "rationale": "ri"}}
    item = build_grant_item(_opp(), dense, taxonomy_version="taxonomy_v2",
                            judge={"is_research": True, "appeal_by_stage": {"early": 0.9}})
    assert item["PK"] == {"S": "GRANT#grants_gov:359855"}
    assert item["SK"] == {"S": "META"}
    assert item["sponsor"] == {"S": "NIH"}
    assert item["award_ceiling"] == {"N": "600000"}
    assert item["primary_topic_id"] == {"S": "breast_cancer"}  # highest score
    tv = item["topic_vector"]["L"]
    assert tv[0]["M"]["topic_id"]["S"] == "breast_cancer"
    assert tv[0]["M"]["score"]["N"] == str(Decimal("0.95"))


def test_build_grant_item_handles_none_award():
    opp = _opp()
    opp.award_ceiling = None
    item = build_grant_item(opp, {}, taxonomy_version="taxonomy_v2", judge={})
    assert "award_ceiling" not in item  # omit nulls rather than write empty
    assert item["primary_topic_id"] == {"S": ""}

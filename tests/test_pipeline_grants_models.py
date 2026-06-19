from pipeline_grants.models import Opportunity, make_opportunity_id


def test_make_opportunity_id():
    assert make_opportunity_id("grants_gov", "359855") == "grants_gov:359855"


def test_opportunity_defaults():
    opp = Opportunity(
        opportunity_id="grants_gov:1", source="grants_gov", source_id="1",
        source_url="http://x", sponsor="NIH", title="T", synopsis="S",
    )
    assert opp.award_ceiling is None
    assert opp.cfda_list == []
    assert opp.status == ""

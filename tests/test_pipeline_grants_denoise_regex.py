from pipeline_grants.models import Opportunity
from pipeline_grants.denoise import regex_gate


def _opp(**kw):
    base = dict(opportunity_id="grants_gov:1", source="grants_gov", source_id="1",
               source_url="http://x", sponsor="NIH", title="Research Project Grant",
               synopsis="study of disease", status="posted", due_date="2099-01-01")
    base.update(kw)
    return Opportunity(**base)


def test_keeps_research_grant():
    kept, reason = regex_gate(_opp())
    assert kept is True and reason == ""


def test_excludes_travel_award_by_title():
    kept, reason = regex_gate(_opp(title="Conference Travel Award"))
    assert kept is False and "type" in reason


def test_excludes_expired_due_date():
    kept, reason = regex_gate(_opp(due_date="2000-01-01"))
    assert kept is False and "expired" in reason


def test_continuous_with_no_due_date_is_kept():
    kept, reason = regex_gate(_opp(due_date="", status="continuous"))
    assert kept is True

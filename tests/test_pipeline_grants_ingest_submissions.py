from pipeline_grants import ingest_submissions as mod
from pipeline_grants.safe_fetch import FetchRejected
from utils.bedrock_client import HAIKU_MODEL, SONNET_MODEL

URL = "https://www.skincancer.org/about-us/research-grants"

EXTRACTION = {
    "programs": [
        {"title": "Todd Nagel Memorial Research Grant Award",
         "sponsor": "The Skin Cancer Foundation",
         "synopsis": "One-year research grant for early-career dermatology investigators.",
         "eligibility_raw": "dermatology residents, fellows and investigators within 10 years",
         "estimated_funding": "$50,000", "due_date": None},
        {"title": "Annual Melanoma Symposium Travel Award",
         "sponsor": "The Skin Cancer Foundation",
         "synopsis": "Travel support to attend the symposium.",
         "eligibility_raw": "", "estimated_funding": 2000, "due_date": None},
        {"title": "Expired Pilot Award",
         "sponsor": "The Skin Cancer Foundation",
         "synopsis": "A pilot award whose deadline has passed.",
         "eligibility_raw": "", "due_date": "2020-01-01"},
        {"title": "Hartwell Individual Biomedical Research Award",
         "sponsor": "The Hartwell Foundation",
         "synopsis": "Already in the corpus under another source.",
         "eligibility_raw": "", "due_date": None},
    ],
}


class _FakeBedrock:
    """Routes by model: Sonnet -> extraction, Haiku -> judge (travel award fails)."""

    def call_json(self, model, messages, **kwargs):
        if model == SONNET_MODEL:
            return EXTRACTION
        assert model == HAIKU_MODEL
        content = messages[0]["content"]
        if "Travel Award" in content:
            return {"is_research": False, "reason": "travel support, not research funding"}
        return {"is_research": True, "reason": "substantive",
                "appeal_by_stage": {"grad": 0.2, "postdoc": 0.6, "early": 0.9, "mid": 0.5, "senior": 0.3}}


def _scoring_stub(**kwargs):
    return {"skin_cancer": {"score": 0.9, "rationale": "dermatology research"}}


def _process(monkeypatch, corpus_titles):
    monkeypatch.setattr(mod, "fetch_page_text", lambda url: "Research grants page text " * 40)
    monkeypatch.setattr(mod.scoring, "score_grant_text", lambda **kw: _scoring_stub(**kw))
    return mod.process_submission(
        {"sk": "2026-07-06T12:00:00.000Z#ab12cd34", "url": URL},
        bedrock=_FakeBedrock(), taxonomy={}, taxonomy_version="taxonomy_v2",
        int_to_id={}, id_to_int={}, corpus_titles=corpus_titles,
    )


def test_source_id_is_deterministic_and_url_scoped():
    a = mod.make_submission_source_id("Todd Nagel Award", URL)
    b = mod.make_submission_source_id("Todd Nagel Award", URL)
    c = mod.make_submission_source_id("Todd Nagel Award", "https://other.org/page")
    assert a == b
    assert a != c  # same award name on a different page never collides
    assert a.startswith("todd-nagel-award-")


def test_build_opportunity_coerces_amounts_and_dates():
    opp = mod.build_opportunity(
        {"title": "X Award", "sponsor": "", "synopsis": "s",
         "estimated_funding": "$50,000", "award_ceiling": "junk", "due_date": "next spring"},
        url=URL, ingested_at="2026-07-06T00:00:00Z",
    )
    assert opp.estimated_funding == 50000
    assert opp.award_ceiling is None
    assert opp.due_date == ""            # non-ISO date degrades to unknown, not garbage
    assert opp.sponsor == "www.skincancer.org"  # sponsor falls back to the page host
    assert opp.source == "manual_url"
    assert opp.opportunity_id.startswith("manual_url:x-award-")


def test_process_submission_keeps_research_drops_travel_expired_and_dup(monkeypatch):
    corpus = {mod.title_tokens("The Hartwell Individual Biomedical Research Award"):
              "wcm_curated:hartwell-abc123"}
    outcome = _process(monkeypatch, corpus)
    assert outcome["status"] == "processed"
    # 4 extracted: travel (judge), expired (gate), Hartwell (token-identical dup) drop
    assert len(outcome["produced"]) == 1
    assert outcome["produced"][0].startswith("manual_url:todd-nagel-memorial")
    assert len(outcome["items"]) == 1
    item = outcome["items"][0]
    assert item["source"]["S"] == "manual_url"
    assert item["is_research"]["BOOL"] is True
    # the kept program now guards the rest of the run against re-submission
    assert corpus[mod.title_tokens("Todd Nagel Memorial Research Grant Award")] == outcome["produced"][0]


def test_process_submission_rejects_when_nothing_survives(monkeypatch):
    monkeypatch.setattr(mod, "fetch_page_text", lambda url: "text " * 200)
    monkeypatch.setattr(
        mod, "extract_programs",
        lambda text, url, bedrock: [{"title": "Old Award", "due_date": "2020-01-01", "synopsis": "s"}],
    )
    outcome = mod.process_submission(
        {"sk": "s", "url": URL}, bedrock=None, taxonomy={}, taxonomy_version="v",
        int_to_id={}, id_to_int={}, corpus_titles={},
    )
    assert outcome["status"] == "rejected"
    assert "expired deadline" in outcome["reject_reason"]
    assert outcome["items"] == []


def test_process_submission_rejects_on_guarded_fetch_failure(monkeypatch):
    def blocked(url):
        raise FetchRejected("ssrf_blocked", "resolves to non-public 10.0.0.5")
    monkeypatch.setattr(mod, "fetch_page_text", blocked)
    outcome = mod.process_submission(
        {"sk": "s", "url": "https://internal.example.org"}, bedrock=None, taxonomy={},
        taxonomy_version="v", int_to_id={}, id_to_int={}, corpus_titles={},
    )
    assert outcome["status"] == "rejected"
    assert "ssrf_blocked" in outcome["reject_reason"]


def test_mark_submission_update_shape():
    calls = {}

    class _FakeClient:
        def update_item(self, **kwargs):
            calls.update(kwargs)

    mod.mark_submission(_FakeClient(), "2026-07-06T12:00:00.000Z#ab12cd34",
                        status="processed", produced=["manual_url:x-award-abc123"])
    assert calls["Key"] == {"PK": {"S": "SUBMISSION"}, "SK": {"S": "2026-07-06T12:00:00.000Z#ab12cd34"}}
    assert ":ids" in calls["ExpressionAttributeValues"]
    assert calls["ExpressionAttributeNames"] == {"#st": "status"}
    assert "reject_reason" not in calls["UpdateExpression"]

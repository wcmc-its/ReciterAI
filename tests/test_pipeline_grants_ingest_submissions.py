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


# --- suppressed submissions (status recheck in the drain + produced-row cleanup) ---
def test_get_submission_status_reads_item():
    class _FakeClient:
        def get_item(self, **kw):
            assert kw["Key"] == {"PK": {"S": "SUBMISSION"}, "SK": {"S": "sk1"}}
            return {"Item": {"status": {"S": "suppressed"}}}

    assert mod.get_submission_status(_FakeClient(), "sk1") == "suppressed"


def test_get_submission_status_missing_row_is_empty():
    class _FakeClient:
        def get_item(self, **kw):
            return {}

    assert mod.get_submission_status(_FakeClient(), "gone") == ""


def test_drain_skips_submission_suppressed_after_listing(monkeypatch):
    monkeypatch.setattr(mod.scoring, "load_taxonomy", lambda: {"taxonomy_version": "taxonomy_v2"})
    monkeypatch.setattr(mod.scoring, "build_index", lambda tax: ({}, {}))
    monkeypatch.setattr(mod, "BedrockClient", lambda *a, **k: object())
    monkeypatch.setattr(mod, "get_dynamo_client", lambda region=None: object())
    monkeypatch.setattr(mod, "list_pending", lambda client: [
        {"sk": "sk-suppressed", "url": "https://a.org", "note": "", "submitted_by": ""},
        {"sk": "sk-live", "url": "https://b.org", "note": "", "submitted_by": ""},
    ])
    monkeypatch.setattr(mod, "load_corpus_title_index", lambda client: {})
    statuses = {"sk-suppressed": "suppressed", "sk-live": "pending"}
    monkeypatch.setattr(mod, "get_submission_status", lambda client, sk: statuses[sk])

    processed_sks = []

    def _process(sub, **kw):
        processed_sks.append(sub["sk"])
        return {"status": "processed", "produced": ["manual_url:x"], "reject_reason": None,
                "items": [{"PK": {"S": "GRANT#manual_url:x"}}], "artifact": []}

    monkeypatch.setattr(mod, "process_submission", _process)
    marked = []
    monkeypatch.setattr(mod, "mark_submission", lambda client, sk, **kw: marked.append(sk))
    monkeypatch.setattr(mod, "put_grants", lambda client, items, **kw: len(items))
    monkeypatch.setattr(mod, "publish_opportunities_artifact", lambda arts, **kw: {})

    summary = mod.drain()
    assert processed_sks == ["sk-live"]       # suppressed one never fetched/scored
    assert marked == ["sk-live"]              # ...and its status row is left alone
    assert summary["skipped_not_pending"] == 1 and summary["processed"] == 1


class _CleanupClient:
    """Query pages of suppressed SUBMISSION rows; records deletes + updates."""

    def __init__(self, items):
        self._items = items
        self.deleted, self.updated = [], []

    def get_paginator(self, _op):
        items = self._items

        class _P:
            def paginate(self, **_kw):
                return [{"Items": items}]

        return _P()

    def delete_item(self, **kw):
        self.deleted.append(kw)

    def update_item(self, **kw):
        self.updated.append(kw)


def _suppressed_item(sk, produced, deleted_at=""):
    item = {"PK": {"S": "SUBMISSION"}, "SK": {"S": sk}, "status": {"S": "suppressed"},
            "produced_opportunity_ids": {"L": [{"S": p} for p in produced]}}
    if deleted_at:
        item["produced_deleted_at"] = {"S": deleted_at}
    return item


def test_cleanup_suppressed_dry_run_is_default_and_deletes_nothing():
    client = _CleanupClient([_suppressed_item("sk1", ["manual_url:a", "manual_url:b"])])
    summary = mod.cleanup_suppressed(client)
    assert summary == {"suppressed": 1, "with_produced": 1,
                       "grant_items_deleted": 0, "dry_run": True}
    assert client.deleted == [] and client.updated == []


def test_cleanup_suppressed_apply_deletes_produced_and_stamps():
    client = _CleanupClient([
        _suppressed_item("sk1", ["manual_url:a", "manual_url:b"]),
        _suppressed_item("sk2", []),               # suppressed before it ever produced
    ])
    summary = mod.cleanup_suppressed(client, apply=True)
    assert summary["grant_items_deleted"] == 2 and summary["with_produced"] == 1
    assert [d["Key"]["PK"]["S"] for d in client.deleted] == ["GRANT#manual_url:a", "GRANT#manual_url:b"]
    assert all(d["Key"]["SK"] == {"S": "META"} for d in client.deleted)
    # the drained submission is stamped so re-runs skip it; the empty one untouched
    assert len(client.updated) == 1
    upd = client.updated[0]
    assert upd["Key"] == {"PK": {"S": "SUBMISSION"}, "SK": {"S": "sk1"}}
    assert "produced_deleted_at" in upd["UpdateExpression"]


def test_cleanup_suppressed_skips_already_cleaned():
    client = _CleanupClient(
        [_suppressed_item("sk1", ["manual_url:a"], deleted_at="2026-07-01T00:00:00Z")])
    summary = mod.cleanup_suppressed(client, apply=True)
    assert summary["grant_items_deleted"] == 0 and summary["with_produced"] == 0
    assert client.deleted == [] and client.updated == []

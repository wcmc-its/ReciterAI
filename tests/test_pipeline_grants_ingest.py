from unittest.mock import MagicMock
import pipeline_grants.ingest as ingest


def test_run_ingest_filters_and_persists(monkeypatch):
    # Two search hits; one is a travel award (regex-dropped), one is a real research grant.
    monkeypatch.setattr(ingest.grants_gov, "search_opportunities",
                        lambda **kw: {"oppHits": [{"id": "1"}, {"id": "2"}]})

    details = {
        "1": {"data": {"id": "1", "opportunityTitle": "Conference Travel Award",
                       "synopsis": {"synopsisDesc": "travel"}}},
        "2": {"data": {"id": "2", "opportunityTitle": "Cancer Research Project",
                       "synopsis": {"synopsisDesc": "research", "responseDate": "Oct 19, 2099 12:00:00 AM EDT"}}},
    }
    monkeypatch.setattr(ingest.grants_gov, "fetch_opportunity", lambda oid: details[oid]["data"])
    monkeypatch.setattr(ingest.scoring, "load_taxonomy", lambda: {"taxonomy_version": "taxonomy_v2", "topics": []})
    monkeypatch.setattr(ingest.scoring, "build_index", lambda tax: ({}, {}))
    monkeypatch.setattr(ingest.scoring, "score_grant_text",
                        lambda **kw: {"breast_cancer": {"score": 0.9, "rationale": "r"}})
    monkeypatch.setattr(ingest, "judge_opportunity",
                        lambda opp, bedrock: {"is_research": True, "reason": "", "appeal_by_stage": {}})
    monkeypatch.setattr(ingest, "BedrockClient", lambda *a, **k: object())

    captured = {}
    monkeypatch.setattr(ingest, "get_dynamo_client", lambda region=None: MagicMock())
    def _fake_put(client, items, **kw):
        captured["items"] = items
        return len(items)
    monkeypatch.setattr(ingest, "put_grants", _fake_put)
    monkeypatch.setattr(ingest, "publish_opportunities_artifact", lambda arts, **kw: {"count": len(arts)})

    summary = ingest.run(rows=10, keyword="")
    assert summary["fetched"] == 2
    assert summary["kept"] == 1          # travel award dropped by regex_gate
    assert summary["persisted"] == 1
    assert captured["items"][0]["PK"]["S"] == "GRANT#grants_gov:2"


def test_run_ingest_skips_failed_item_and_persists_the_rest(monkeypatch):
    # Middle opportunity's scoring raises (e.g. a non-JSON Bedrock reply); the run must
    # skip it and still persist the two good ones, not abort the whole batch.
    monkeypatch.setattr(ingest.grants_gov, "search_opportunities",
                        lambda **kw: {"oppHits": [{"id": "1"}, {"id": "2"}, {"id": "3"}]})
    titles = {"1": "Cancer Research Project", "2": "Diabetes Research Project",
              "3": "Heart Research Project"}
    monkeypatch.setattr(ingest.grants_gov, "fetch_opportunity",
                        lambda oid: {"id": oid, "opportunityTitle": titles[oid],
                                     "synopsis": {"synopsisDesc": "research",
                                                  "responseDate": "Oct 19, 2099 12:00:00 AM EDT"}})
    monkeypatch.setattr(ingest.scoring, "load_taxonomy", lambda: {"taxonomy_version": "taxonomy_v2", "topics": []})
    monkeypatch.setattr(ingest.scoring, "build_index", lambda tax: ({}, {}))

    def _score(**kw):
        if kw["opportunity_id"] == "grants_gov:2":
            raise RuntimeError("grant scoring failed for grants_gov:2: Expecting value")
        return {"breast_cancer": {"score": 0.9, "rationale": "r"}}
    monkeypatch.setattr(ingest.scoring, "score_grant_text", _score)
    monkeypatch.setattr(ingest, "judge_opportunity",
                        lambda opp, bedrock: {"is_research": True, "reason": "", "appeal_by_stage": {}})
    monkeypatch.setattr(ingest, "BedrockClient", lambda *a, **k: object())
    monkeypatch.setattr(ingest, "get_dynamo_client", lambda region=None: MagicMock())

    persisted_items = []
    monkeypatch.setattr(ingest, "put_grants",
                        lambda client, items, **kw: (persisted_items.extend(items), len(items))[1])
    monkeypatch.setattr(ingest, "publish_opportunities_artifact", lambda arts, **kw: {"count": len(arts)})

    summary = ingest.run(rows=10, keyword="")
    assert summary["fetched"] == 3
    assert summary["kept"] == 2
    assert summary["failed"] == 1
    assert summary["persisted"] == 2
    assert {i["PK"]["S"] for i in persisted_items} == {"GRANT#grants_gov:1", "GRANT#grants_gov:3"}


def test_run_ingest_skips_key_already_held_by_corpus(monkeypatch):
    # The corpus already holds this normalized key under an equal-priority source
    # (a different grants.gov listing) -> the incoming hit is skipped BEFORE any
    # Bedrock spend (judge would explode if reached).
    from pipeline_grants import dedupe

    monkeypatch.setattr(ingest.grants_gov, "search_opportunities",
                        lambda **kw: {"oppHits": [{"id": "2"}]})
    monkeypatch.setattr(ingest.grants_gov, "fetch_opportunity",
                        lambda oid: {"id": oid, "opportunityTitle": "Cancer Research Project",
                                     "synopsis": {"synopsisDesc": "research"}})
    monkeypatch.setattr(ingest.scoring, "load_taxonomy", lambda: {"taxonomy_version": "taxonomy_v2", "topics": []})
    monkeypatch.setattr(ingest.scoring, "build_index", lambda tax: ({}, {}))
    monkeypatch.setattr(ingest, "BedrockClient", lambda *a, **k: object())
    monkeypatch.setattr(ingest, "get_dynamo_client", lambda region=None: MagicMock())

    index = dedupe.CorpusKeyIndex()
    index.add(opportunity_id="grants_gov:999", source="grants_gov",
              title="The Cancer Research Project", sponsor="")
    monkeypatch.setattr(ingest, "load_corpus_key_index", lambda client: index)

    def _boom(*_a, **_k):
        raise AssertionError("judge called for a corpus-duplicate")
    monkeypatch.setattr(ingest, "judge_opportunity", _boom)

    persisted = []
    monkeypatch.setattr(ingest, "put_grants",
                        lambda client, items, **kw: (persisted.extend(items), len(items))[1])
    monkeypatch.setattr(ingest, "publish_opportunities_artifact", lambda arts, **kw: {"count": len(arts)})

    summary = ingest.run(rows=10, keyword="")
    assert summary["kept"] == 0 and summary["persisted"] == 0
    assert persisted == []

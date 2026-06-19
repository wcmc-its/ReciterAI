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

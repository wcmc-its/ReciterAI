import json
from unittest.mock import MagicMock

import pipeline_grants.exclusions as exclusions
import pipeline_grants.ingest as ingest


def test_load_excluded_ids(tmp_path):
    p = tmp_path / "excluded_opportunities.json"
    p.write_text(json.dumps({"excluded_opportunities": [
        {"opportunity_id": "wcm_curated:a", "title": "A", "reason": "non-topical"},
        {"opportunity_id": "grants_gov:99", "title": "B", "reason": "non-topical"},
        {"title": "no id - ignored"},
    ]}))
    assert exclusions.load_excluded_ids(str(p)) == {"wcm_curated:a", "grants_gov:99"}


def test_load_excluded_ids_missing_file_is_empty(tmp_path):
    assert exclusions.load_excluded_ids(str(tmp_path / "nope.json")) == set()


def test_ingest_skips_excluded_id_before_persist_and_before_scoring(monkeypatch):
    # Two research hits; id "2" is on the exclusion list -> never scored, never persisted.
    monkeypatch.setattr(ingest.grants_gov, "search_opportunities",
                        lambda **kw: {"oppHits": [{"id": "1"}, {"id": "2"}]})
    monkeypatch.setattr(ingest.grants_gov, "fetch_opportunity",
                        lambda oid: {"id": oid, "opportunityTitle": f"Cancer Research {oid}",
                                     "synopsis": {"synopsisDesc": "research",
                                                  "responseDate": "Oct 19, 2099 12:00:00 AM EDT"}})
    monkeypatch.setattr(ingest.scoring, "load_taxonomy", lambda: {"taxonomy_version": "taxonomy_v2", "topics": []})
    monkeypatch.setattr(ingest.scoring, "build_index", lambda tax: ({}, {}))
    monkeypatch.setattr(ingest, "load_excluded_ids", lambda: {"grants_gov:2"})

    scored = []
    def _score(**kw):
        scored.append(kw["opportunity_id"])
        return {"breast_cancer": {"score": 0.9, "rationale": "r"}}
    monkeypatch.setattr(ingest.scoring, "score_grant_text", _score)
    monkeypatch.setattr(ingest, "judge_opportunity",
                        lambda opp, bedrock: {"is_research": True, "reason": "", "appeal_by_stage": {}})
    monkeypatch.setattr(ingest, "BedrockClient", lambda *a, **k: object())
    monkeypatch.setattr(ingest, "get_dynamo_client", lambda region=None: MagicMock())
    persisted = []
    monkeypatch.setattr(ingest, "put_grants",
                        lambda client, items, **kw: (persisted.extend(items), len(items))[1])
    monkeypatch.setattr(ingest, "publish_opportunities_artifact", lambda arts, **kw: {"count": len(arts)})

    summary = ingest.run(rows=10, keyword="")
    assert summary["kept"] == 1
    assert summary["persisted"] == 1
    assert scored == ["grants_gov:1"]                       # excluded id never reached the scorer
    assert {i["PK"]["S"] for i in persisted} == {"GRANT#grants_gov:1"}

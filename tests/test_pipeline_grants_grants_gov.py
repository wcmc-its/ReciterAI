import pipeline_grants.grants_gov as gg


def test_search_opportunities_builds_payload_and_returns_data(monkeypatch):
    captured = {}

    def fake_post(url, payload, timeout=30):
        captured["url"] = url
        captured["payload"] = payload
        return {"errorcode": 0, "msg": "ok", "data": {"hitCount": 1, "oppHits": [{"id": "359855"}]}}

    monkeypatch.setattr(gg, "_post_json", fake_post)
    data = gg.search_opportunities(keyword="cancer", statuses="posted", rows=2, start=0)
    assert captured["url"].endswith("/v1/api/search2")
    assert captured["payload"] == {"keyword": "cancer", "oppStatuses": "posted", "rows": 2, "startRecordNum": 0}
    assert data["oppHits"][0]["id"] == "359855"


def test_fetch_opportunity_unwraps_data(monkeypatch):
    monkeypatch.setattr(gg, "_post_json", lambda url, payload, timeout=30: {"errorcode": 0, "data": {"id": "359855"}})
    assert gg.fetch_opportunity("359855")["id"] == "359855"


def test_raises_on_grants_gov_errorcode(monkeypatch):
    monkeypatch.setattr(gg, "_post_json", lambda url, payload, timeout=30: {"errorcode": 1, "msg": "boom", "data": {}})
    try:
        gg.search_opportunities(keyword="x")
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "boom" in str(e)

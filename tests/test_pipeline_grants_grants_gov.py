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


def test_search_all_opportunities_paginates_and_includes_forecasted(monkeypatch):
    # 5 hits across pages of 2 -> requests at startRecordNum 0,2,4 then stops at hitCount
    # (no extra empty page). Default status set must include forecasted NOFOs.
    pages = {0: [{"id": "a"}, {"id": "b"}], 2: [{"id": "c"}, {"id": "d"}], 4: [{"id": "e"}]}
    starts = []

    def fake_post(url, payload, timeout=30):
        starts.append(payload["startRecordNum"])
        assert payload["oppStatuses"] == "posted|forecasted"
        return {"errorcode": 0, "data": {"hitCount": 5, "oppHits": pages[payload["startRecordNum"]]}}

    monkeypatch.setattr(gg, "_post_json", fake_post)
    ids = [h["id"] for h in gg.search_all_opportunities(rows=2)]
    assert ids == ["a", "b", "c", "d", "e"]
    assert starts == [0, 2, 4]


def test_raises_on_grants_gov_errorcode(monkeypatch):
    monkeypatch.setattr(gg, "_post_json", lambda url, payload, timeout=30: {"errorcode": 1, "msg": "boom", "data": {}})
    try:
        gg.search_opportunities(keyword="x")
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "boom" in str(e)

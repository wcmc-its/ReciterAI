"""Thin client for the public Grants.gov REST API (no API key required)."""
import json
import urllib.request

SEARCH_URL = "https://api.grants.gov/v1/api/search2"
FETCH_URL = "https://api.grants.gov/v1/api/fetchOpportunity"


def _post_json(url: str, payload: dict, timeout: int = 30) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _check(resp: dict) -> dict:
    if resp.get("errorcode") not in (0, "0"):
        raise RuntimeError(f"Grants.gov error: {resp.get('msg')!r} (errorcode={resp.get('errorcode')})")
    return resp.get("data", {})


def search_opportunities(*, keyword: str = "", statuses: str = "posted|forecasted",
                         rows: int = 25, start: int = 0, agencies: str = "") -> dict:
    """One page of search results. Returns the `data` object (data.oppHits[], data.hitCount)."""
    payload = {"keyword": keyword, "oppStatuses": statuses, "rows": rows, "startRecordNum": start}
    if agencies:
        payload["agencies"] = agencies
    return _check(_post_json(SEARCH_URL, payload))


def fetch_opportunity(opportunity_id: str) -> dict:
    """Full detail for one opportunity. Returns the `data` object (data.synopsis, data.cfdas, ...)."""
    return _check(_post_json(FETCH_URL, {"opportunityId": opportunity_id}))

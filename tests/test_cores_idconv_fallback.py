"""PMC->PMID mapping falls back to esummary(db=pmc) when idconv fails.

idconv's old URL 301s to pmc.ncbi.nlm.nih.gov, which on 2026-10-07 429'd even a
single request; every core with an alias hit then failed #415's guard. The guard
is right for a TOTAL failure, so these pin both halves: a dead idconv no longer
fails the core, and a dead idconv AND esummary still raises.
"""
from __future__ import annotations

import io
import json
import urllib.error

import pytest

from pipeline_cores import pmc_search
from pipeline_cores.models import CoreDefinition


def _http_error(url, code):
    return urllib.error.HTTPError(url, code, "err", {}, io.BytesIO(b""))


def _esummary(mapping):
    """An esummary db=pmc body: uid -> pmid ('' / None means no pmid articleid)."""
    result = {"uids": list(mapping)}
    for uid, pmid in mapping.items():
        aids = [{"idtype": "pmcid", "value": f"PMC{uid}"}]
        if pmid is not None:
            aids.insert(0, {"idtype": "pmid", "value": pmid})
        result[uid] = {"uid": uid, "articleids": aids}
    return {"result": result}


@pytest.fixture
def calls(monkeypatch):
    log = []
    monkeypatch.setattr(pmc_search.time, "sleep", lambda s: None)
    return log


def test_idconv_ok_path_is_unchanged(monkeypatch, calls):
    def fake_get(url, params, timeout, **kw):
        calls.append((url, kw.get("waits")))
        assert url == pmc_search.IDCONV
        return {"records": [{"pmcid": p, "pmid": "9" + p[3:]} for p in params["ids"].split(",")]}

    monkeypatch.setattr(pmc_search, "_get_json", fake_get)
    assert pmc_search.pmcids_to_pmids(["1", "2"]) == {"91", "92"}
    assert calls == [(pmc_search.IDCONV, pmc_search.IDCONV_WAITS)]


def test_idconv_429_falls_back_to_esummary(monkeypatch, calls):
    def fake_get(url, params, timeout, **kw):
        calls.append(url)
        if url == pmc_search.IDCONV:
            raise _http_error(url, 429)
        assert params["db"] == "pmc" and params["id"] == "7495155,111"
        return _esummary({"7495155": "32963523", "111": "222"})

    monkeypatch.setattr(pmc_search, "_get_json", fake_get)
    assert pmc_search.pmcids_to_pmids(["7495155", "PMC111"]) == {"32963523", "222"}
    assert calls == [pmc_search.IDCONV, pmc_search.ESUMMARY]


@pytest.mark.parametrize("err", [
    urllib.error.URLError("timed out"),
    TimeoutError("read timed out"),
    json.JSONDecodeError("Expecting value", "<html>", 0),
])
def test_other_idconv_failures_fall_back(monkeypatch, calls, err):
    def fake_get(url, params, timeout, **kw):
        if url == pmc_search.IDCONV:
            raise err
        return _esummary({"1": "11"})

    monkeypatch.setattr(pmc_search, "_get_json", fake_get)
    assert pmc_search.pmcids_to_pmids(["1"]) == {"11"}


def test_esummary_entry_without_pmid_is_skipped(monkeypatch, calls):
    def fake_get(url, params, timeout, **kw):
        if url == pmc_search.IDCONV:
            raise _http_error(url, 503)
        return _esummary({"1": "11", "2": None, "3": "", "4": "0"})

    monkeypatch.setattr(pmc_search, "_get_json", fake_get)
    assert pmc_search.pmcids_to_pmids(["1", "2", "3", "4"]) == {"11"}


def test_both_failing_raises_and_the_guard_refuses(monkeypatch, calls):
    def fake_get(url, params, timeout, **kw):
        raise _http_error(url, 429)

    monkeypatch.setattr(pmc_search, "_get_json", fake_get)
    with pytest.raises(urllib.error.HTTPError):
        pmc_search.pmcids_to_pmids(["1"])

    monkeypatch.setattr(pmc_search, "esearch_pmc", lambda alias, **kw: ["1"])
    core = CoreDefinition(core_id="14", name="X", aliases=["Some Core Name"])
    with pytest.raises(RuntimeError, match="refusing to score core 14"):
        pmc_search.pmids_naming(core)


def test_esummary_error_body_raises_not_empty(monkeypatch, calls):
    """An E-utilities error body must not read as 'these PMC ids have no PMID'."""
    def fake_get(url, params, timeout, **kw):
        if url == pmc_search.IDCONV:
            raise _http_error(url, 429)
        return {"error": "API rate limit exceeded"}

    monkeypatch.setattr(pmc_search, "_get_json", fake_get)
    with pytest.raises(RuntimeError, match="no result"):
        pmc_search.pmcids_to_pmids(["1"])


def test_fallback_is_per_chunk_and_chunked(monkeypatch, calls):
    """Chunks stay at IDCONV_CHUNK on both paths; a chunk idconv serves never hits esummary."""
    sizes = []

    def fake_get(url, params, timeout, **kw):
        if url == pmc_search.IDCONV:
            ids = params["ids"].split(",")
            sizes.append(("idconv", len(ids)))
            if ids[0] == "PMC200":  # only the second chunk fails
                raise _http_error(url, 429)
            return {"records": [{"pmid": "9" + i[3:]} for i in ids]}
        ids = params["id"].split(",")
        sizes.append(("esummary", len(ids)))
        return _esummary({u: "9" + u for u in ids})

    monkeypatch.setattr(pmc_search, "_get_json", fake_get)
    out = pmc_search.pmcids_to_pmids([str(i) for i in range(450)])
    assert sizes == [("idconv", 200), ("idconv", 200), ("esummary", 200), ("idconv", 50)]
    assert out == {"9" + str(i) for i in range(450)}


def test_idconv_gets_a_short_retry_budget(monkeypatch):
    """With a fallback waiting, idconv must not burn the ~1-minute RETRY_WAITS."""
    import urllib.request

    slept, hosts = [], []

    def fake_urlopen(url, timeout=None):
        hosts.append(url.split("?")[0])
        if url.startswith(pmc_search.IDCONV):
            raise _http_error(url, 429)
        return io.BytesIO(json.dumps(_esummary({"1": "11"})).encode())

    monkeypatch.delenv("PUBMED_API_KEY", raising=False)
    monkeypatch.delenv("NCBI_API_KEY", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(pmc_search.time, "sleep", lambda s: slept.append(s))
    assert pmc_search.pmcids_to_pmids(["1"]) == {"11"}
    assert hosts == [pmc_search.IDCONV] * (len(pmc_search.IDCONV_WAITS) + 1) + [pmc_search.ESUMMARY]
    backoff = [s for s in slept if s >= 1]
    assert sum(backoff) <= 5, backoff

"""PMC full-text loader for the acknowledgement signal (signal 3).

Dependency-free (urllib, matching the repo's dominant HTTP convention) with a
disk cache so a PMID is fetched at most once. pmid -> PMCID (elink) ->
PMC XML (efetch) -> plain text. Returns "" when no PMC full text exists
(the ~10-20% of papers outside the OA/author-manuscript subset), which simply
leaves signal 3 silent for that paper.

Reads PUBMED_API_KEY / NCBI_API_KEY from the env when present to lift the NCBI
rate limit (3->10 req/s); never required, never logged.

Scale note: fetching inline for the whole ~10K corpus is slow. In production,
pre-populate the cache (or back it with S3 via utils.s3_client) out of band and
run the pipeline against the warm cache.
"""
from __future__ import annotations

import html
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
_DEFAULT_CACHE = Path(__file__).resolve().parent.parent / "out" / "fulltext_cache"
_NO_PMC = "\x00NO_PMC\x00"  # sentinel cached when a PMID has no PMC record


def _api_key() -> str:
    return os.getenv("PUBMED_API_KEY") or os.getenv("NCBI_API_KEY") or ""


def to_plain_text(xml: str) -> str:
    """Strip PMC XML to whitespace-collapsed plain text."""
    if not xml:
        return ""
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", xml))).strip()


class PmcFullTextClient:
    def __init__(self, cache_dir: Path = None, min_interval: float = None, timeout: int = 60):
        self.cache_dir = Path(cache_dir) if cache_dir else _DEFAULT_CACHE
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # NCBI: 10 req/s with a key, 3 without. elink+efetch = 2 calls per miss.
        self.min_interval = min_interval if min_interval is not None else (0.11 if _api_key() else 0.34)
        self.timeout = timeout
        self._last = 0.0

    # -- network ----------------------------------------------------------
    def _get(self, path: str, params: dict) -> str:
        key = _api_key()
        if key:
            params = {**params, "api_key": key}
        url = _BASE + path + "?" + urllib.parse.urlencode(params)
        for attempt in range(5):
            gap = self.min_interval - (time.monotonic() - self._last)
            if gap > 0:
                time.sleep(gap)
            self._last = time.monotonic()
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "reciterai-cores/1.0"})
                return urllib.request.urlopen(req, timeout=self.timeout).read().decode("utf-8", "ignore")
            except Exception:
                time.sleep(1.5 * (attempt + 1))
        return ""

    def _pmid_to_pmcid(self, pmid: str) -> str:
        body = self._get("elink.fcgi", {"dbfrom": "pubmed", "db": "pmc", "retmode": "json", "id": str(pmid)})
        m = re.search(r'"pubmed_pmc"\s*,\s*"links"\s*:\s*\[\s*"?(\d+)', body) or \
            re.search(r'"links":\["?(\d+)', body)
        return "PMC" + m.group(1) if m else ""

    # -- public -----------------------------------------------------------
    def get(self, pmid: str) -> str:
        """Plain-text body for `pmid` (cached). "" when no PMC full text."""
        cache = self.cache_dir / f"{pmid}.xml"
        if cache.exists():
            raw = cache.read_text(encoding="utf-8", errors="ignore")
            return "" if raw == _NO_PMC else to_plain_text(raw)
        pmcid = self._pmid_to_pmcid(pmid)
        if not pmcid:
            cache.write_text(_NO_PMC, encoding="utf-8")
            return ""
        xml = self._get("efetch.fcgi", {"db": "pmc", "id": pmcid[3:], "rettype": "xml", "retmode": "xml"})
        cache.write_text(xml or _NO_PMC, encoding="utf-8")
        return to_plain_text(xml)

    def prefetch(self, pmids: list) -> int:
        """Warm the cache for a batch; returns how many have full text."""
        return sum(1 for p in pmids if self.get(p))

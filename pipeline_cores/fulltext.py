"""PMC full-text loader for the acknowledgement signal (signal 3).

Dependency-free at import (urllib, matching the repo's dominant HTTP convention)
with a two-tier cache so a PMID is fetched from NCBI at most once across the
whole fleet. pmid -> PMCID (elink) -> PMC XML (efetch) -> plain text. Returns ""
when no PMC full text exists (the ~10-20% of papers outside the OA/author-
manuscript subset), which simply leaves signal 3 silent for that paper.

Cache tiers, checked in order on ``get()``:
  1. local disk   (out/fulltext_cache/{pmid}.xml) — fastest, per-host.
  2. S3           (s3://<artifacts bucket>/cores/fulltext/{pmid}.xml) — shared,
                  durable, survives a cold host. Optional: only consulted when a
                  client is constructed with an S3 backend (``with_s3()`` or the
                  ``s3=`` kwarg). boto3 is imported lazily so the disk-only path
                  and the pure unit tests stay dependency-free.
  3. NCBI         (elink + efetch) — the origin. Results (including the "no PMC"
                  sentinel) are written back through to BOTH disk and S3 so the
                  next cold run never re-fetches them.

S3 is best-effort: a read or write failure (missing creds, AccessDenied, network)
degrades to the origin and is logged once per client — it never raises into the
pipeline. Warm the S3 cache out of band with ``python -m
pipeline_cores.prefetch_fulltext`` before a full-corpus run.

Reads PUBMED_API_KEY / NCBI_API_KEY from the env when present to lift the NCBI
rate limit (3->10 req/s); never required, never logged.
"""
from __future__ import annotations

import html
import logging
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
_DEFAULT_CACHE = Path(__file__).resolve().parent.parent / "out" / "fulltext_cache"
_NO_PMC = "\x00NO_PMC\x00"  # sentinel cached when a PMID has no PMC record
_S3_PREFIX = "cores/fulltext/"  # under the artifacts bucket
# The `<ref-list\b[^>]*/>` alternative is load-bearing: without it `[^>]*` eats the
# `/` of a self-closing <ref-list/> and the body is swallowed to the next close tag.
_REF_LIST_RE = re.compile(r"<ref-list\b[^>]*/>|<ref-list\b[^>]*>.*?</ref-list\s*>", re.I | re.S)


def _api_key() -> str:
    return os.getenv("PUBMED_API_KEY") or os.getenv("NCBI_API_KEY") or ""


def to_plain_text(xml: str) -> str:
    """Strip PMC XML to whitespace-collapsed plain text, <ref-list> dropped first.

    The reference list is cited-work text rather than this paper's own, and
    signal 3 weighs an alias found anywhere in what we return here, heavily enough
    to confirm a pair on its own.
    """
    if not xml:
        return ""
    body = _REF_LIST_RE.sub(" ", xml)
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))).strip()


def make_s3_backend(bucket: str = None):
    """Construct the shared S3 cache backend (lazy boto3, default credential chain).

    Imported lazily by callers so ``import pipeline_cores.fulltext`` stays
    boto3-free. Returns an ``S3HierarchyClient`` targeting the artifacts bucket;
    its key_exists/get_object_bytes/put_object are the only methods this module
    uses, so any duck-typed stand-in works in tests.
    """
    from utils.s3_client import ARTIFACTS_BUCKET, S3HierarchyClient  # lazy: keeps this module dep-free

    return S3HierarchyClient(bucket=bucket or ARTIFACTS_BUCKET)


class PmcFullTextClient:
    def __init__(
        self,
        cache_dir: Path = None,
        min_interval: float = None,
        timeout: int = 60,
        s3=None,
        s3_prefix: str = _S3_PREFIX,
    ):
        self.cache_dir = Path(cache_dir) if cache_dir else _DEFAULT_CACHE
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # NCBI: 10 req/s with a key, 3 without. elink+efetch = 2 calls per miss.
        self.min_interval = min_interval if min_interval is not None else (0.11 if _api_key() else 0.34)
        self.timeout = timeout
        # Optional shared cache tier. Duck-typed: key_exists/get_object_bytes/put_object.
        self.s3 = s3
        self.s3_prefix = s3_prefix
        self._last = 0.0
        self._s3_read_warned = False
        self._s3_write_warned = False
        self.s3_read_failures = 0
        self.s3_write_failures = 0

    @classmethod
    def with_s3(cls, *, cache_dir: Path = None, bucket: str = None, **kwargs) -> "PmcFullTextClient":
        """Build a client backed by the shared S3 cache (artifacts bucket)."""
        return cls(cache_dir=cache_dir, s3=make_s3_backend(bucket), **kwargs)

    # -- S3 tier (best-effort; never raises into the pipeline) ------------
    def _s3_key(self, pmid: str) -> str:
        return f"{self.s3_prefix}{pmid}.xml"

    def _s3_read(self, pmid: str):
        """Cache content (raw xml or the _NO_PMC sentinel) from S3, or None on miss/error."""
        if not self.s3:
            return None
        key = self._s3_key(pmid)
        try:
            if not self.s3.key_exists(key):
                return None
            return self.s3.get_object_bytes(key).decode("utf-8", "ignore")
        except Exception as exc:
            self.s3_read_failures += 1
            if not self._s3_read_warned:
                logger.warning("S3 full-text read failed (degrading to NCBI origin): %s", exc)
                self._s3_read_warned = True
            else:
                logger.debug("S3 full-text read failed for %s: %s", pmid, exc)
            return None

    def _s3_write(self, pmid: str, content: str) -> None:
        if not self.s3:
            return
        try:
            self.s3.put_object(self._s3_key(pmid), content.encode("utf-8"), content_type="application/xml")
        except Exception as exc:
            self.s3_write_failures += 1
            if not self._s3_write_warned:
                logger.warning("S3 full-text write failed (cache stays local-only): %s", exc)
                self._s3_write_warned = True
            else:
                logger.debug("S3 full-text write failed for %s: %s", pmid, exc)

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
    def get_xml(self, pmid: str) -> str:
        """Raw PMC XML for `pmid` (cached). "" when no PMC full text.

        disk -> S3 (read-through, populates disk on hit) -> NCBI (write-through to
        both disk and S3). The _NO_PMC sentinel is cached at every tier so the
        ~10-20% of papers with no PMC record are never re-fetched.

        Callers wanting the body text want `get()`. This raw form exists for
        consumers that need the JATS STRUCTURE the tag-strip throws away — e.g.
        `suggest_aliases`, which reads the <ack> element specifically.
        """
        cache = self.cache_dir / f"{pmid}.xml"
        if cache.exists():
            raw = cache.read_text(encoding="utf-8", errors="ignore")
            return "" if raw == _NO_PMC else raw

        # S3 second tier: on a hit, populate the local disk so later reads on this
        # host are free.
        raw = self._s3_read(pmid)
        if raw is not None:
            cache.write_text(raw, encoding="utf-8")
            return "" if raw == _NO_PMC else raw

        # Origin (NCBI) — write the result through to both tiers.
        pmcid = self._pmid_to_pmcid(pmid)
        if not pmcid:
            cache.write_text(_NO_PMC, encoding="utf-8")
            self._s3_write(pmid, _NO_PMC)
            return ""
        xml = self._get("efetch.fcgi", {"db": "pmc", "id": pmcid[3:], "rettype": "xml", "retmode": "xml"})
        content = xml or _NO_PMC
        cache.write_text(content, encoding="utf-8")
        self._s3_write(pmid, content)
        return xml or ""

    def get(self, pmid: str) -> str:
        """Plain-text body for `pmid` (cached). "" when no PMC full text."""
        return to_plain_text(self.get_xml(pmid))

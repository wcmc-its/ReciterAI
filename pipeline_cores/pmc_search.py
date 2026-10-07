"""Find the papers that NAME a core by asking PMC, instead of downloading the
corpus and grepping it.

`--with-fulltext` fetches full text for every scored publication so signal 3 can
look for an alias in it. At WCM scale that is ~80k NCBI round trips (~12h on a
cold cache) to find, typically, a couple of dozen papers — PMC has already
indexed that text, so the same answer is a handful of searches per alias:

    esearch(db=pmc, term='"Architecture for Research Computing"')  ->  25 PMC ids
    idconv (esummary db=pmc when idconv fails)                     ->  25 PMIDs

Full text is then fetched for those PMIDs ONLY, which keeps the ack_snippet the
claim queue shows as evidence. 25 fetches instead of 80,203.

SEARCHES PAGINATE. esearch returns at most RETMAX ids per call but reports the
true total in `count`, so `esearch_pmc` walks retstart until it has them all. A
single page silently dropped 98% of a generic alias ("Flow Cytometry Core":
count=23544, idlist=500) in NCBI's default sort order — not even the same 500
twice. MAX_IDS caps a pathological alias, and tripping it logs a warning naming
the alias and its count, so a truncated set is never silent again.

REQUESTS ARE PACED and carry PUBMED_API_KEY / NCBI_API_KEY when set, exactly as
fulltext.py does. Unpaced, the 2 requests per alias across 57 aliases ran past
NCBI's 3-req/s anonymous limit and 18 of them came back "HTTP Error 400", each
swallowed by the then-fail-soft per-alias catch as "no hits" (it raises now, #415).

Coverage is identical to `--with-fulltext`: both see the PMC subset and nothing
else, so this trades no recall for the speedup.

ACRONYM ALIASES ARE SKIPPED. `signals.acknowledgement_signal` matches a short
all-caps alias case-sensitively on a word boundary, precisely because "ARCH"
would otherwise hit "arch", "Arch." and ARCH econometric models. esearch has no
case-sensitive mode, so searching one would return exactly the noise that rule
exists to prevent. A core whose aliases are ALL acronyms gets no hits from this
path and needs `--with-fulltext`.
"""
from __future__ import annotations

import http.client
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from pipeline_cores.signals import _ACRONYM, alias_variants

logger = logging.getLogger(__name__)

ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
# 301s to https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/ (urllib follows
# it). Not pointed there directly: on 2026-10-07 the new URL 429'd even a single
# unkeyed request, so it could not be verified to serve.
IDCONV = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"
DEFAULT_TIMEOUT = 45
RETMAX = 500        # ids per esearch page
MAX_IDS = 9999      # NCBI's OWN ceiling: retstart must be <= 9998, and a request past it
                    # returns a 200 whose JSON carries a raw newline, so json.load raises
                    # and pmids_naming's per-alias catch would discard every id collected.
IDCONV_CHUNK = 200  # idconv's documented per-request id limit (a bigger GET is a 414)

RETRY_WAITS = (2, 4, 8, 16, 32)  # seconds between attempts on a 429/5xx/network error
                                # (~1 min); Retry-After wins when NCBI sends one
IDCONV_WAITS = (2,)  # idconv has a fallback (esummary), so ONE retry, not the full minute

_last_request = 0.0


def _api_key() -> str:
    """Same env vars fulltext.py reads; never required, never logged."""
    return os.getenv("PUBMED_API_KEY") or os.getenv("NCBI_API_KEY") or ""


def _get_json(url: str, params: dict, timeout: int, waits: tuple = RETRY_WAITS) -> dict:
    """One paced, keyed NCBI request. Every call in this module goes through here.

    `waits` is the retry schedule; a caller with a fallback passes a shorter one."""
    global _last_request
    key = _api_key()
    if key:
        params = {**params, "api_key": key}
    # NCBI: 10 req/s with a key, 3 without — same cadence as PmcFullTextClient.
    gap = (0.11 if key else 0.34) - (time.monotonic() - _last_request)
    if gap > 0:
        time.sleep(gap)
    _last_request = time.monotonic()
    q = urllib.parse.urlencode(params)
    # Retried, because one 429 used to cost the alias (#415): unkeyed Fargate egress
    # shares NCBI's 3 req/s per-IP budget with whoever else is on that IP.
    for wait in (*waits, None):
        try:
            with urllib.request.urlopen(f"{url}?{q}", timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as err:
            if wait is None or not (err.code == 429 or err.code >= 500):
                raise
            try:
                wait = max(wait, float(err.headers.get("Retry-After") or 0))
            except ValueError:
                pass  # an HTTP-date Retry-After; the backoff is close enough
            logger.info("NCBI HTTP %d — retrying in %ss", err.code, wait)
        except (urllib.error.URLError, TimeoutError) as err:
            if wait is None:
                raise
            logger.info("NCBI %s — retrying in %ss", err, wait)
        time.sleep(wait)


def alias_term(phrase: str) -> str:
    """The esearch term for one alias: each spelling quoted, OR-ed.

    PMC DROPS "&" from a phrase, so '"Microscopy & Image Analysis Core"' runs as
    "microscopy image analysis core" and finds a DIFFERENT paper set from the "and"
    spelling — probed 2026-10-06: 207 vs 88 PMC ids with 5 in common, 290 for the
    OR. Searching only the dictionary's spelling left the other set unfetched, so
    the matcher never saw those papers whatever it would have said about them.

    One OR request, not one per spelling, for a second reason: inside an OR an
    unrunnable phrase contributes 0 hits, where on its own PMC silently falls back to
    a term-ANDed query (7,321 ids for "Davis Cancer Immune Monitoring Core").

    An alias with no connector gives exactly '"phrase"', the term it always sent.
    """
    return " OR ".join(f'"{v}"' for v in alias_variants(phrase))


def esearch_pmc(phrase: str, *, timeout: int = DEFAULT_TIMEOUT) -> list:
    """Every PMC id whose full text contains `phrase` (quoted as one phrase), in any
    of its "and"/"&" spellings (alias_term).

    Pages on retstart until `count` ids are in hand or MAX_IDS trips; a short
    set is always warned about, never returned silently.
    """
    ids: list = []
    count = 0
    while True:
        body = _get_json(ESEARCH, {"db": "pmc", "term": alias_term(phrase), "retmode": "json",
                                   "retmax": RETMAX, "retstart": len(ids)}, timeout)
        result = body.get("esearchresult", {})
        count = int(result.get("count") or 0)
        page = list(result.get("idlist", []))
        ids += page
        if not page or len(ids) >= count or len(ids) >= MAX_IDS:
            break
    if count > len(ids):
        logger.warning("alias %r has %d PMC hits but only %d were retrieved (MAX_IDS=%d) — "
                       "signal 3 sees a TRUNCATED set for this alias", phrase, count, len(ids), MAX_IDS)
    return ids


def esearch_count(phrase: str, *, timeout: int = DEFAULT_TIMEOUT):
    """How many PMC papers name `phrase`, in ONE request (retmax=0, no id list).

    The alias-SPECIFICITY feature: esearch reports the true total in `count` whether
    or not it returns any ids, so the number that predicts an alias's precision
    (r=-0.852 vs home%) costs one paced request per alias. Cached into the dictionary
    by `python3 -m pipeline_cores.refresh_alias_hits`, never fetched during a run.

    Returns None when PMC could not run the phrase at all. esearch does NOT error on
    an unfindable phrase — it silently falls back to a term-ANDed/MeSH-expanded query
    and reports THAT count, so "Davis Cancer Immune Monitoring Core" comes back as
    ("davies" OR "davis" ...) AND ("cancer s" OR ...) = 7221. Scoring that number as
    specificity pushed 4 genuine WCM aliases into the `generic` bucket (-5.07) and 5
    into `distinctive` (+4.35) on a quantity that measures nothing. The warning list
    is the only signal that it happened; None routes them to `ack.spec:unknown` (0.00),
    which is the honest answer.

    AN ALIAS WITH AN "and"/"&" CONNECTOR is counted as the UNION of its spellings,
    because that is what the matcher now accepts (signals._alias_pattern): the count
    has to describe the set of papers a match can come from, or specificity measures
    a different alias from the one that matched. Each spelling is first run ALONE to
    keep the None rule — an OR never raises quotedphrasesnotfound, it just scores the
    unrunnable spelling 0 — and if NO spelling runs the alias stays None. Otherwise
    the OR's count is the union (3 requests for such an alias, 1 for any other).
    """
    variants = alias_variants(phrase)
    singles = [_count(f'"{v}"', timeout) for v in variants]
    if all(n is None for n in singles):
        logger.warning("PMC could not run %r as a phrase — no specificity count", phrase)
        return None
    if len(variants) == 1:
        return singles[0]
    return _count(alias_term(phrase), timeout)


def _count(term: str, timeout: int):
    """esearch's `count` for `term`, or None when PMC could not run a quoted phrase."""
    body = _get_json(ESEARCH, {"db": "pmc", "term": term, "retmode": "json",
                               "retmax": 0}, timeout)
    result = body.get("esearchresult", {})
    if result.get("warninglist", {}).get("quotedphrasesnotfound"):
        return None
    return int(result.get("count") or 0)


# What an idconv chunk can fail with before esummary takes over: HTTPError (429/5xx
# after its short retry, or any 4xx), URLError / timeouts / resets (all OSError), a
# truncated body (HTTPException), or a 200 that is not JSON (ValueError).
_IDCONV_FAILURES = (OSError, ValueError, http.client.HTTPException)


def pmcids_to_pmids(pmcids: list, *, timeout: int = DEFAULT_TIMEOUT) -> set:
    """Map PMC ids to PMIDs. Records without a PMID (rare) are dropped.

    Each chunk asks idconv first; if idconv fails for that chunk the same ids go to
    esummary(db=pmc), whose `articleids` carry the PMID. idconv 301s to a new host
    that, since 2026-10-07, 429s even a single request, which made every core with an
    alias hit fail (#415's guard, correctly). esummary is E-utilities, the host esearch
    already uses, so it shares the key and pacing. If esummary ALSO fails it raises,
    and pmids_naming refuses the core exactly as before.
    """
    pmcids = [str(p).removeprefix("PMC") for p in pmcids]
    out: set = set()
    for i in range(0, len(pmcids), IDCONV_CHUNK):
        chunk = pmcids[i:i + IDCONV_CHUNK]
        try:
            body = _get_json(IDCONV, {"ids": ",".join("PMC" + p for p in chunk),
                                      "format": "json", "tool": "pipeline_cores"},
                             timeout, waits=IDCONV_WAITS)
            got = {str(r["pmid"]) for r in body.get("records", []) if r.get("pmid")}
            logger.info("PMC->PMID: idconv mapped %d of %d PMC ids", len(got), len(chunk))
        except _IDCONV_FAILURES as err:
            logger.info("PMC->PMID: idconv failed (%s) — falling back to esummary for %d PMC ids",
                        type(err).__name__ + (f" {err.code}" if hasattr(err, "code") else ""),
                        len(chunk))
            got = esummary_pmids(chunk, timeout=timeout)
            logger.info("PMC->PMID: esummary mapped %d of %d PMC ids", len(got), len(chunk))
        out |= got
    return out


def esummary_pmids(pmcids: list, *, timeout: int = DEFAULT_TIMEOUT) -> set:
    """PMIDs for numeric PMC uids via esummary(db=pmc). Entries with no PMID are skipped.

    Callers keep the list at or under IDCONV_CHUNK (200), which fits a GET. A response
    with no `result` (an E-utilities error body) raises rather than reading as "no
    PMIDs", so a failure here can never pass for an empty answer.
    """
    body = _get_json(ESUMMARY, {"db": "pmc", "id": ",".join(str(p) for p in pmcids),
                                "retmode": "json", "tool": "pipeline_cores"}, timeout)
    result = body.get("result")
    if not isinstance(result, dict):
        raise RuntimeError(f"esummary db=pmc returned no result: {str(body)[:200]}")
    out: set = set()
    for uid in result.get("uids", []):
        for aid in (result.get(uid) or {}).get("articleids", []):
            if aid.get("idtype") == "pmid" and str(aid.get("value") or "0") != "0":
                out.add(str(aid["value"]))
                break
    return out


def pmids_naming(core, *, timeout: int = DEFAULT_TIMEOUT) -> set:
    """PMIDs whose PMC full text names one of `core`'s non-acronym aliases.

    RAISES when an alias search still fails after _get_json's retries (#415). It
    used to log and carry on, and that is a wipe, not a degradation: ack_alias /
    ack_snippet are owned attributes, so a run that "found no alias" REMOVEs them
    from every row it rewrites, and the pairs resting on them drop a band. A failed
    nightly writes nothing (run.py persists only after every core finishes), so the
    stored evidence waits for the next night intact.
    """
    out: set = set()
    for alias in core.aliases:
        if _ACRONYM.match(alias):
            logger.info("alias %r is an acronym — skipped (esearch is case-insensitive)", alias)
            continue
        try:
            out |= pmcids_to_pmids(esearch_pmc(alias, timeout=timeout), timeout=timeout)
        except Exception as err:
            raise RuntimeError(f"alias search failed for {alias!r} after retries — "
                               f"refusing to score core {core.core_id} without it "
                               f"(it would strip stored ack evidence, #415)") from err
    return out


def make_alias_loader(core, *, use_s3: bool = False, client=None, hits=None):
    """A `full_text(pmid)` callable for `run_core` that only fetches alias hits.

    Returns "" for everything else, so `acknowledgement_signal` sees exactly the
    papers PMC says name the core — same result as scanning the whole corpus,
    at the cost of one search per alias. The search runs once, lazily, on the
    first call. Pass `hits` to skip the network entirely (tests, or a
    pre-computed PMID list).
    """
    from pipeline_cores.fulltext import PmcFullTextClient  # lazy: keeps import cheap

    if client is None:
        client = PmcFullTextClient.with_s3() if use_s3 else PmcFullTextClient()
    state = {"hits": set(hits) if hits is not None else None}

    def loader(pmid) -> str:
        if state["hits"] is None:
            state["hits"] = pmids_naming(core)
            print(f"[{core.core_id} {core.name}] alias search: "
                  f"{len(state['hits'])} PMC papers name an alias")
        return client.get(str(pmid)) if str(pmid) in state["hits"] else ""

    return loader

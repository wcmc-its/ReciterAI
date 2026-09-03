"""Find the papers that NAME a core by asking PMC, instead of downloading the
corpus and grepping it.

`--with-fulltext` fetches full text for every scored publication so signal 3 can
look for an alias in it. At WCM scale that is ~80k NCBI round trips (~12h on a
cold cache) to find, typically, a couple of dozen papers — PMC has already
indexed that text, so the same answer is one esearch per alias:

    esearch(db=pmc, term='"Architecture for Research Computing"')  ->  25 PMC ids
    idconv                                                         ->  25 PMIDs

Full text is then fetched for those PMIDs ONLY, which keeps the ack_snippet the
claim queue shows as evidence. 25 fetches instead of 80,203.

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

import json
import logging
import urllib.parse
import urllib.request

from pipeline_cores.signals import _ACRONYM

logger = logging.getLogger(__name__)

ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
IDCONV = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"
DEFAULT_TIMEOUT = 45
RETMAX = 500


def _get_json(url: str, params: dict, timeout: int) -> dict:
    q = urllib.parse.urlencode(params)
    with urllib.request.urlopen(f"{url}?{q}", timeout=timeout) as resp:
        return json.load(resp)


def esearch_pmc(phrase: str, *, timeout: int = DEFAULT_TIMEOUT) -> list:
    """PMC ids whose full text contains `phrase` (quoted as one phrase)."""
    body = _get_json(ESEARCH, {"db": "pmc", "term": f'"{phrase}"',
                               "retmode": "json", "retmax": RETMAX}, timeout)
    return list(body.get("esearchresult", {}).get("idlist", []))


def pmcids_to_pmids(pmcids: list, *, timeout: int = DEFAULT_TIMEOUT) -> set:
    """Map PMC ids to PMIDs. Records without a PMID (rare) are dropped."""
    if not pmcids:
        return set()
    body = _get_json(IDCONV, {"ids": ",".join("PMC" + str(i) for i in pmcids),
                              "format": "json", "tool": "pipeline_cores"}, timeout)
    return {str(r["pmid"]) for r in body.get("records", []) if r.get("pmid")}


def pmids_naming(core, *, timeout: int = DEFAULT_TIMEOUT) -> set:
    """PMIDs whose PMC full text names one of `core`'s non-acronym aliases.

    Fail-soft per alias: an NCBI hiccup on one alias logs and is skipped rather
    than failing the run — signal 3 is a bonus confirmer, never load-bearing.
    """
    out: set = set()
    for alias in core.aliases:
        if _ACRONYM.match(alias):
            logger.info("alias %r is an acronym — skipped (esearch is case-insensitive)", alias)
            continue
        try:
            out |= pmcids_to_pmids(esearch_pmc(alias, timeout=timeout), timeout=timeout)
        except Exception as err:  # noqa: BLE001 — any NCBI failure degrades to "no hits"
            logger.warning("alias search failed for %r: %s", alias, err)
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

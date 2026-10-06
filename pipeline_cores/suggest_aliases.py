"""Discover a core's acknowledgement aliases from its own confirmed papers —
the inverse of signal 3, and the mirror of `suggest_staff`.

`suggest_staff` bootstraps the PEOPLE from the papers. This bootstraps the
STRINGS: a core with confirmed usage but no alias list (a newly-added core, or
one whose owner simply doesn't know how the facility is acknowledged) can read
its aliases straight off the acknowledgement sections of the papers humans have
already claimed.

  confirmed/claimed papers  ->  PMC <ack> text  ->  facility-shaped phrases
                            ->  ranked by how many of those papers name them

The gate is document frequency, not one lucky hit: a phrase on 1 paper is that
paper's own funder boilerplate, a phrase on 8 of 40 is the core's name. Phrases
already claimed as an alias by a DIFFERENT core in the dictionary are dropped —
they belong to that core, not this one.

This SUGGESTS, never auto-adds: output is a review list an owner confirms, the
same posture as the claim queue and `suggest_staff`. Read-only (no DB writes, no
dictionary writes). Re-run it after each batch of confirmations and the alias
list sharpens as the claims accumulate — the discovery loop the cores design
otherwise leaves to a human guess.

Usage:
  python3 -m pipeline_cores.suggest_aliases --core 14                 # from DynamoDB
  python3 -m pipeline_cores.suggest_aliases --core 14 --pmids-file p  # offline
  python3 -m pipeline_cores.suggest_aliases --core 14 --min-docs 3 --top 25
"""
from __future__ import annotations

import argparse
import re
from collections import defaultdict

from pipeline_cores.dictionary import load_cores
from pipeline_cores.fulltext import PmcFullTextClient, to_plain_text

DEFAULT_MIN_DOCS = 2
DEFAULT_TOP = 25
# A facility name is a handful of words. The cap is not cosmetic: an <ack> that
# swallows the affiliations block yields one 200-token capitalised run, and the
# window enumeration below is O(n^2) in the run length — uncapped it emitted
# tens of thousands of junk phrases per paper.
MAX_PHRASE_WORDS = 8

# A phrase only counts as a facility name if it carries one of these. Without the
# gate every grant number and university name in the acknowledgements outranks the
# core itself.
FACILITY_WORDS = (
    "core", "facility", "center", "centre", "shared resource", "resource",
    "service", "services", "platform", "program", "laboratory", "lab", "unit",
    "institute", "consortium", "initiative", "office",
)

# Boilerplate that appears in nearly every acknowledgements section. Not a
# correctness gate — it just keeps the top of the report readable.
# ponytail: a fixed stoplist, not a background corpus. If it stops earning its
# keep, pass --background-pmids and rank by (positive DF - background DF).
GENERIC = re.compile(
    r"^(the )?(national (institutes? of health|cancer institute|science foundation)"
    r"|nih|nci|nsf|clinical and translational science center"
    r"|weill cornell medic(ine|al college)|cornell university|memorial sloan kettering"
    r"|howard hughes medical institute|department of (defense|veterans affairs))$",
    re.IGNORECASE,
)

_ACK_TAG = re.compile(r"<ack\b.*?</ack>", re.IGNORECASE | re.DOTALL)
# A <sec> whose <title> starts with Acknowledg… / Funding — some publishers use a
# plain section instead of the <ack> element.
_ACK_SEC = re.compile(
    r"<sec\b[^>]*>\s*<title>\s*(?:acknowledg|funding|financial support)[^<]*</title>.*?</sec>",
    re.IGNORECASE | re.DOTALL,
)

# A facility name reads as Title Case with lowercase connectors: "Citigroup
# Biomedical Imaging Center", "Center for Advanced Digital Applications".
_CONNECT = {"of", "for", "and", "the", "at", "in", "on", "de", "&"}
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9'\-]*")
_PAREN_ACRONYM = re.compile(r"\(([A-Z][A-Z0-9]{1,5})\)")


def ack_text(xml: str) -> str:
    """The acknowledgements/funding prose of a PMC article, as plain text."""
    if not xml:
        return ""
    blocks = _ACK_TAG.findall(xml) + _ACK_SEC.findall(xml)
    return " ".join(to_plain_text(b) for b in blocks).strip()


def phrases(text: str) -> set:
    """Title-cased facility-shaped phrases in `text`, plus parenthesised acronyms.

    Every contiguous run of capitalised words (lowercase connectors allowed
    inside, never at an edge) is emitted at each length from 2 up to the whole
    run, so "Citigroup Biomedical Imaging Center" also yields "Biomedical
    Imaging Center" — the alias the next paper may use instead.
    """
    out = set()
    for acro in _PAREN_ACRONYM.findall(text):
        out.add(acro)
    tokens = [(m.group(0), m.start(), m.end()) for m in _WORD.finditer(text)]
    run: list = []
    for tok, _s, _e in tokens + [("", 0, 0)]:
        capitalised = bool(tok) and tok[0].isupper()
        connector = tok.lower() in _CONNECT
        if capitalised or (connector and run):
            run.append(tok)
            continue
        for start in range(len(run)):
            if run[start].lower() in _CONNECT:
                continue
            for end in range(start + 2, min(start + MAX_PHRASE_WORDS, len(run)) + 1):
                if run[end - 1].lower() in _CONNECT:
                    continue
                cand = " ".join(run[start:end])
                low = cand.lower()
                if any(w in low for w in FACILITY_WORDS) and not GENERIC.match(cand):
                    out.add(cand)
        run = []
    return out


def rank(docs: dict, min_docs: int, foreign_aliases: set) -> list:
    """(phrase, n_docs, example_pmid) ranked by document frequency then length.

    Sub-spans are collapsed: the miner emits every window of a capitalised run,
    so one facility name arrives as a dozen nested phrases. A shorter phrase
    that appears on exactly the same papers as a longer one it sits inside adds
    nothing — only the longest form survives. A shorter phrase on MORE papers
    does survive: that is the short form other papers actually use.
    """
    scored = []
    for phrase, pmids in docs.items():
        if len(pmids) < min_docs or phrase.lower() in foreign_aliases:
            continue
        scored.append((phrase, len(pmids), sorted(pmids)[0]))
    scored.sort(key=lambda r: (-r[1], -len(r[0].split()), r[0].lower()))

    kept: list = []
    for phrase, n, example in scored:
        low = phrase.lower()
        if any(n == kn and low in kp.lower() for kp, kn, _ in kept):
            continue
        kept.append((phrase, n, example))
    return kept


def suggest_for_core(core, pmids: list, client, min_docs: int, top: int, foreign_aliases: set):
    docs: dict = defaultdict(set)
    with_ack = 0
    for pmid in pmids:
        text = ack_text(client.get_xml(str(pmid)))
        if not text:
            continue
        with_ack += 1
        for ph in phrases(text):
            docs[ph].add(str(pmid))

    print(f"\n=== core {core.core_id}  {core.name} ===")
    print(f"  {len(pmids)} confirmed papers, {with_ack} with a PMC acknowledgements section")
    if not with_ack:
        print("  (no acknowledgements text — nothing to mine; signal 3 cannot fire for this core)")
        return []
    known = {a.lower() for a in core.aliases}
    rows = rank(docs, min_docs, foreign_aliases)
    if not rows:
        print(f"  (no phrase on >= {min_docs} papers)")
        return []
    print(f"  {'docs':>4}  {'phrase':<58} status")
    for phrase, n, example in rows[:top]:
        status = "ALREADY AN ALIAS" if phrase.lower() in known else f"pmid {example}"
        print(f"  {n:>4}  {phrase:<58} {status}")
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--core", required=True, help="core_id, e.g. 14")
    ap.add_argument("--pmids-file", help="newline-separated PMIDs; default reads DynamoDB")
    ap.add_argument("--min-docs", type=int, default=DEFAULT_MIN_DOCS)
    ap.add_argument("--top", type=int, default=DEFAULT_TOP)
    ap.add_argument("--cache-dir", default=None)
    args = ap.parse_args(argv)

    cores = load_cores()
    core = next((c for c in cores if str(c.core_id) == str(args.core)), None)
    if core is None:
        # A core can be live in SPS before it has a dictionary entry — that is
        # exactly the case this tool serves, so report against a stub.
        from pipeline_cores.models import CoreDefinition

        core = CoreDefinition(core_id=str(args.core), name="(not in dictionary)", facility="",
                              aliases=[], staff=[])
    foreign = {a.lower() for c in cores if str(c.core_id) != str(args.core) for a in c.aliases}

    if args.pmids_file:
        with open(args.pmids_file) as fh:
            pmids = [ln.strip() for ln in fh if ln.strip()]
    else:
        from pipeline_cores.persist import scan_prior_core_usage

        # strict: an empty scan must fail loudly, not report "0 confirmed papers".
        # The rows are DICTS — attribute access here read every status as "" and
        # silently surveyed nothing.
        recs = scan_prior_core_usage(str(args.core), strict=True)
        pmids = sorted({r["pmid"] for r in recs if r.get("status") in ("confirmed", "claimed")})

    client = PmcFullTextClient(cache_dir=args.cache_dir) if args.cache_dir else PmcFullTextClient()
    suggest_for_core(core, pmids, client, args.min_docs, args.top, foreign)
    print("\nReview these, then add the survivors to config/core_dictionary.yaml "
          "under this core's `aliases:`. Nothing was written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

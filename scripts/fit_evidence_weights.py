#!/usr/bin/env python3
"""Fit combine()'s weights of evidence from the ground truth that actually exists.

    w_e = log( P(e | used the core) / P(e | did not) )

Laplace-smoothed, printed with the n behind every cell, so a weight nobody can
support shows up as unsupported instead of being quietly invented. Output is a
ready-to-paste `WEIGHTS` block for pipeline_cores/combine.py — this script is the
provenance of those numbers, and re-running it is how they get refreshed.

THREE PANELS, because ONE ground truth cannot fit every feature without circularity:

  A. `ack` — did an alias match at all?
     positives: analysis/confirmed_pmids_by_core.json, the per-core SIGNAL-2
       confirms (a core staff member on the byline). Independent of signal 3,
       which is exactly what makes them usable here.
     negatives: a random sample of the 80,203-paper WCM corpus scored against the
       SAME cores. Any one core is used by ~1-5% of papers, so a random paper is a
       negative for it (a ~2% contamination that biases weights DOWN, i.e. safely).

  C. the terms CONDITIONAL on a match — alias specificity, institution, section.
     Panel A's random negatives produce only a handful of alias matches, which
     cannot price an institution, so the negatives here are papers that match an
     alias but have NO WCM author (PMC-wide alias hits minus the corpus), sampled
     in proportion to each alias's global hit count. A paper with no WCM author did
     not use a WCM core, and that label never consults the institution regex the
     feature is built from — so this is not circular.
     Conditional terms sum with the marginal `ack` by the chain rule, so nothing is
     double counted.

  B. `staff`, and the LLM / affinity slopes.
     analysis/labeled_set.csv — 237 human yes/no rows for the Biomedical Imaging
     core, plus the pipeline's own two-pass triage scores for the same 237.
     Panel A's positives ARE signal-2 confirms, so fitting signal 2's own weight
     from them would be circular and would manufacture a huge bogus number. This
     panel never touches them.

The likelihood RATIO is prevalence-invariant, which is what lets an enriched
58%-positive label set fit weights that are then applied under a ~2% corpus prior.
Only the prior carries the base rate.

NOT FITTED, on purpose:
  * match section (<ack> / methods / body) — counted and printed, weight forced to
    0.0. Guessing it is forbidden (SPEC decision 3), and the positives cannot
    separate it from the alias features it co-occurs with.
  * core-grant acknowledgement — grant_ids is empty for all 14 cores. Nothing to
    count, so no dead feature is declared for it.

    python3 scripts/fit_evidence_weights.py
    python3 scripts/fit_evidence_weights.py --negatives 2000 --alias-negatives 600
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import math
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from pipeline_cores import ingest, pmc_search, signals  # noqa: E402
from pipeline_cores.combine import evidence_features  # noqa: E402
from pipeline_cores.dictionary import load_core, load_cores  # noqa: E402
from pipeline_cores.fulltext import PmcFullTextClient, to_plain_text  # noqa: E402
from pipeline_cores.models import SignalResult  # noqa: E402

GROUND_TRUTH = Path("/Users/paulalbert/Dropbox/Projects/Inferring Cores and Services/analysis")
CONFIRMED = GROUND_TRUTH / "confirmed_pmids_by_core.json"
LABELS = GROUND_TRUTH / "labeled_set.csv"
LLM_SCORES = GROUND_TRUTH / "calibration_llm_results.json"   # the pipeline's own two-pass triage
LABEL_CORE = "2"                                             # labeled_set.csv is the imaging core

# The disk cache the earlier cores runs filled. Shared, and read-through to S3.
CACHE_DIR = Path("/Users/paulalbert/worktrees/reciterai-run/out/fulltext_cache")

ALPHA = 0.5        # Laplace (Jeffreys) smoothing on both cells of every ratio
MIN_PANEL = 30     # below this a panel cannot estimate a rate at all -> refuse the weight

# Threaded full-text fetch. Each worker gets its OWN paced client, so pacing is
# per-client: 5 workers x 2 NCBI requests per 1.0s = NCBI's keyed 10 req/s limit.
WORKERS = 5
WORKER_INTERVAL = 1.0


# ---------------------------------------------------------------------------
# full text
# ---------------------------------------------------------------------------
def fetch_xml(pmids) -> dict:
    """pmid -> raw PMC XML ("" when the paper has no PMC record), threaded."""
    pmids = sorted({str(p) for p in pmids})
    out: dict = {}

    def worker(chunk):
        client = PmcFullTextClient.with_s3(cache_dir=CACHE_DIR, min_interval=WORKER_INTERVAL)
        return {p: client.get_xml(p) for p in chunk}

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for part in ex.map(worker, [pmids[i::WORKERS] for i in range(WORKERS)]):
            out.update(part)
    return out


def ack_signal(xml: str, core) -> SignalResult:
    """Exactly what production computes, via the same function."""
    return signals.acknowledgement_signal(to_plain_text(xml), core, xml=xml)


# ---------------------------------------------------------------------------
# the estimator
# ---------------------------------------------------------------------------
def weight(pos_hits: int, pos_n: int, neg_hits: int, neg_n: int) -> float:
    """log( P(e|positive) / P(e|negative) ), Laplace-smoothed on both sides.

    Each key is scored as evidence that is PRESENT — the log( P(not-e|+)/P(not-e|-) )
    term full naive Bayes would add for every ABSENT feature is dropped, which is
    the standard weight-of-evidence approximation and is exactly what
    `logit = prior + sum(w_i)` computes in combine(). For rare evidence that term
    is ~0; for common evidence it shrinks the weight slightly, so this over-states
    nothing that matters.
    """
    p = (pos_hits + ALPHA) / (pos_n + 2 * ALPHA)
    q = (neg_hits + ALPHA) / (neg_n + 2 * ALPHA)
    return math.log(p / q)


def tally(rows) -> collections.Counter:
    c: collections.Counter = collections.Counter()
    for sig in rows:
        c.update(set(evidence_features(sig)))
    return c


def fit(pos, neg, keys, label: str, forced=()) -> dict:
    """Print one panel's table and return {key: (weight, note)}.

    A cell is refused when its panel is too small to estimate a rate at all, and
    flagged `bound` when a numerator is 0 — there the smoothing, not the data, sets
    the magnitude, so the number is a floor on |w| rather than a point estimate.
    """
    pc, nc = tally(pos), tally(neg)
    print(f"\n=== {label} ===")
    print(f"  positives n={len(pos)}   negatives n={len(neg)}")
    print(f"  {'evidence':<24}{'pos':>6}{'neg':>7}{'P(e|+)':>9}{'P(e|-)':>9}{'w':>8}   note")
    out = {}
    for k in keys:
        ph, nh = pc[k], nc[k]
        w, note = weight(ph, len(pos), nh, len(neg)), ""
        if k in forced:
            w, note = 0.0, "held at 0: unmeasured, and guessing it is forbidden"
        elif len(pos) < MIN_PANEL or len(neg) < MIN_PANEL:
            w, note = 0.0, f"REFUSED: panel too small ({len(pos)}/{len(neg)})"
        elif ph == 0 and nh == 0:
            w, note = 0.0, "REFUSED: never observed on either side"
        elif ph == 0 or nh == 0:
            note = f"bound only ({ph} vs {nh}); smoothing sets |w|, treat as a floor"
        out[k] = (round(w, 2), note)
        print(f"  {k:<24}{ph:>6}{nh:>7}{ph / max(len(pos), 1):>9.4f}"
              f"{nh / max(len(neg), 1):>9.4f}{w:>8.2f}   {note}")
    return out


def fit_line(bins, pos_n: int, neg_n: int, label: str, through_origin: bool = False):
    """Weighted least squares of the binned log-LRs against the bin's x value.

    `bins` is [(x, pos_hits, neg_hits)] over the FULL panel (so each bin's weight is
    marginal, comparable with every other feature's). The two natively-graded signals
    (LLM 1-10, affinity 0-1) do not bucket without throwing resolution away, so their
    weight is a line through the same smoothed bin log-LRs. Inverse-variance weighted
    (1/pos + 1/neg is the variance of a log odds-ratio), which is what keeps a
    5-paper bin from out-voting a 35-paper one. Residuals are printed so the
    linearity claim is auditable rather than asserted.
    """
    pts = [(x, weight(ph, pos_n, nh, neg_n), 1.0 / (1.0 / (ph + ALPHA) + 1.0 / (nh + ALPHA)))
           for x, ph, nh in bins]
    sw = sum(w for _, _, w in pts)
    if through_origin:
        slope = sum(w * x * y for x, y, w in pts) / sum(w * x * x for x, y, w in pts)
        intercept = 0.0
    else:
        mx = sum(w * x for x, _, w in pts) / sw
        my = sum(w * y for _, y, w in pts) / sw
        slope = (sum(w * (x - mx) * (y - my) for x, y, w in pts)
                 / sum(w * (x - mx) ** 2 for x, _, w in pts))
        intercept = my - slope * mx
    ss_res = sum(w * (y - (intercept + slope * x)) ** 2 for x, y, w in pts)
    ss_tot = sum(w * (y - sum(w2 * y2 for _, y2, w2 in pts) / sw) ** 2 for _, y, w in pts)

    print(f"\n=== {label} ===")
    print(f"  positives n={pos_n}   negatives n={neg_n}"
          + ("   (line forced through the origin: no evidence -> no weight)" if through_origin else ""))
    print(f"  {'x':>7}{'pos':>6}{'neg':>6}{'bin w':>9}{'fitted':>9}{'resid':>8}")
    for (x, ph, nh), (_, y, _) in zip(bins, pts):
        f = intercept + slope * x
        print(f"  {x:>7.2f}{ph:>6}{nh:>6}{y:>9.2f}{f:>9.2f}{y - f:>8.2f}")
    print(f"  -> w({label.split()[0]}) = {intercept:.2f} + {slope:.2f} * x     "
          f"weighted R2 = {1 - ss_res / ss_tot:.3f}")
    return round(intercept, 2), round(slope, 2)


# ---------------------------------------------------------------------------
# panel A — did an alias match at all?
# ---------------------------------------------------------------------------
def confirms() -> dict:
    return {k: [str(p) for p in v] for k, v in json.loads(CONFIRMED.read_text()).items()}


def panel_a(corpus, neg_pmids):
    conf = confirms()
    cores = {cid: load_core(cid) for cid in conf}
    n_negatives = len(neg_pmids)

    print(f"panel A: {sum(len(v) for v in conf.values())} signal-2 confirms over cores "
          f"{sorted(conf, key=int)}; {n_negatives} random corpus papers x {len(cores)} "
          f"cores = {n_negatives * len(cores)} negative pairs")
    xml = fetch_xml([p for v in conf.values() for p in v] + neg_pmids)
    print(f"  full text: {sum(1 for v in xml.values() if v)}/{len(xml)} pmids have a PMC record")

    pos = [ack_signal(xml.get(p, ""), cores[cid]) for cid, v in conf.items() for p in v]
    neg = [ack_signal(xml.get(p, ""), core)
           for cid, core in cores.items() for p in neg_pmids if p not in set(conf[cid])]
    return pos, neg


# ---------------------------------------------------------------------------
# panel C — given a match, whose core is it?
# ---------------------------------------------------------------------------
def _esearch_one_page(alias: str) -> list:
    """ONE esearch page (<=500 PMC ids) for `alias` — a sample, not the census.

    `pmc_search.esearch_pmc` pages to NCBI's 9,999 ceiling, which for the generic
    aliases here is ~175 requests to build a pool this only ever samples from.
    CAVEAT: a page is NCBI's own ordering, not a random draw, so panel C's papers
    are a convenience sample within each alias. The proportional draw ACROSS aliases
    is the part that carries the shape, and that is exact.
    """
    body = pmc_search._get_json(pmc_search.ESEARCH,
                                {"db": "pmc", "term": f'"{alias}"', "retmode": "json",
                                 "retmax": pmc_search.RETMAX}, pmc_search.DEFAULT_TIMEOUT)
    return list(body.get("esearchresult", {}).get("idlist", []))


def alias_negatives(corpus_set, n_sample: int, seed: int):
    """Alias matches in papers with NO WCM author, sampled in proportion to hits.

    One esearch page per alias (500 ids) is a sample, not a census — which is all
    this needs, because the SAMPLING is what carries the shape: an alias is drawn in
    proportion to its global PMC hit count, so `Flow Cytometry Core` (23,544 hits)
    contributes the flood of other-institution matches it really does produce and
    `Architecture for Research Computing in Health` (20) contributes almost none.
    That proportionality IS measured fact 1 (r=-0.852) entering the fit.
    """
    pools, weights_, owners = [], [], []
    for core in load_cores():
        for alias, hits in core.alias_hits.items():
            if hits <= 0:
                continue
            try:
                ids = pmc_search.pmcids_to_pmids(_esearch_one_page(alias))
            except Exception as err:                   # noqa: BLE001 — one bad alias must not kill the fit
                print(f"  ! esearch failed for {alias!r}: {err}")
                continue
            outside = sorted(ids - corpus_set)
            if outside:
                pools.append(outside)
                weights_.append(hits)
                owners.append(core)
    rng = random.Random(seed)
    picks = collections.defaultdict(set)               # core -> pmids
    for _ in range(n_sample):
        i = rng.choices(range(len(pools)), weights=weights_)[0]
        picks[owners[i].core_id].add(rng.choice(pools[i]))
    by_core = {c.core_id: c for c in load_cores()}
    print(f"panel C: {sum(len(v) for v in picks.values())} alias-matching papers with no WCM "
          f"author, drawn from {len(pools)} alias pools in proportion to global PMC hits")
    xml = fetch_xml([p for v in picks.values() for p in v])
    return [ack_signal(xml.get(p, ""), by_core[cid]) for cid, v in picks.items() for p in v]


# ---------------------------------------------------------------------------
# panel B — staff / LLM / affinity: human labels vs the random corpus
# ---------------------------------------------------------------------------
def panel_b(engine, neg_pmids, n_llm_negatives: int, seed: int):
    """(labelled yes, labelled no, random-corpus negatives) as SignalResults.

    The labelled NO rows are the obvious negative panel and are fitted too — but
    only as a foil. They are EASY negatives ("clearly non-imaging papers", as the
    2026-06 head-to-head says in as many words), so they understate how often a
    random WCM paper trips the LLM or the affinity prior, and every weight fitted
    against them comes out inflated. The random corpus is the population the
    pipeline actually scores, so it is the panel the shipped weights use.
    """
    labels = {r["pmid"]: r["label"] for r in csv.DictReader(LABELS.open())}
    stored_llm = json.loads(LLM_SCORES.read_text())
    core = load_core(LABEL_CORE)
    everyone = sorted(labels) + list(neg_pmids)

    coauthors = signals.coauthorship_index(engine, core, everyone)
    bylines = ingest.fetch_author_bylines(engine, everyone)

    # The affinity index is built from the core's signal-2 confirms MINUS anything in
    # the label set, so a labelled paper never contributes to its own prior.
    outside = [p for p in confirms()[LABEL_CORE] if p not in labels]
    counts: dict = collections.defaultdict(lambda: collections.defaultdict(int))
    for _pmid, cwids in ingest.fetch_author_bylines(engine, outside).items():
        for cwid in cwids:
            counts[cwid][LABEL_CORE] += 1
    index = signals.build_affinity_index(counts)

    # The negatives need LLM scores from the SAME two-pass triage that produced the
    # positives' stored ones, so they are scored live — on a subset, because this is
    # the only part of the fit that costs Bedrock calls.
    rng = random.Random(seed)
    llm_negs = rng.sample(list(neg_pmids), min(n_llm_negatives, len(neg_pmids)))
    from utils.bedrock_client import BedrockClient  # lazy
    print(f"\npanel B: {len(labels)} labelled imaging-core papers "
          f"({sum(1 for v in labels.values() if v == 'yes')} yes / "
          f"{sum(1 for v in labels.values() if v == 'no')} no); affinity index from "
          f"{len(outside)} confirms outside the label set, {len(index)} authors; "
          f"triaging {len(llm_negs)} random corpus papers through Bedrock")
    live_llm = signals.llm_triage(BedrockClient(read_timeout=120), core,
                                  ingest.fetch_publications(engine, pmids=llm_negs))

    def sig(pmid, llm_source):
        return SignalResult(
            coauthor_cwids=coauthors.get(pmid, []),
            llm_score=llm_source.get(pmid, {}).get("score"),
            author_affinity=signals.author_affinity(index, bylines.get(pmid, []), LABEL_CORE),
        )

    rows = {"yes": [], "no": []}
    for pmid in sorted(labels):
        rows[labels[pmid]].append(sig(pmid, stored_llm))
    return rows["yes"], rows["no"], [sig(p, live_llm) for p in neg_pmids]


def graded_bins(pos, neg, value, edges):
    """[(mean x, n_pos, n_neg)] for a graded signal — one row per [edge, next edge)."""
    rows = []
    for lo, hi in zip(edges, list(edges[1:]) + [float("inf")]):
        p = [value(s) for s in pos if value(s) and lo <= value(s) < hi]
        n = [value(s) for s in neg if value(s) and lo <= value(s) < hi]
        if p or n:
            rows.append((sum(p + n) / len(p + n), len(p), len(n)))
    return rows


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--negatives", type=int, default=1200,
                    help="random corpus papers in panel A's negative panel (default 1200)")
    ap.add_argument("--alias-negatives", type=int, default=400,
                    help="alias-matching non-WCM papers in panel C (default 400)")
    ap.add_argument("--llm-negatives", type=int, default=400,
                    help="random corpus papers put through live Bedrock triage (default 400)")
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args(argv)

    from utils.db import get_engine  # lazy
    engine = get_engine()
    corpus = [p["pmid"] for p in ingest.fetch_publications(engine)]
    random.seed(args.seed)
    neg_pmids = random.sample(corpus, args.negatives)
    print(f"corpus: {len(corpus)} WCM publications")

    buckets = ("distinctive", "moderate", "generic", "unknown")
    sections = [f"sec:{s}" for s in ("ack", "methods", "body")]
    conditional = ([f"ack.spec:{b}" for b in buckets]
                   + ["inst:home", "inst:other"] + [f"inst:none@{b}" for b in buckets]
                   + sections)

    pos_a, neg_a = panel_a(corpus, neg_pmids)
    table = fit(pos_a, neg_a, ["ack"], "PANEL A — an alias matched at all (confirms vs random corpus)")

    # Panel C conditions on a match: only the rows where one fired can speak.
    neg_c = alias_negatives(set(corpus), args.alias_negatives, args.seed)
    table.update(fit([s for s in pos_a if s.ack_matched], [s for s in neg_c if s.ack_matched],
                     conditional, "PANEL C — given a match, whose core is it? (CONDITIONAL on ack)",
                     forced=sections))

    pos_b, easy_b, hard_b = panel_b(engine, neg_pmids, args.llm_negatives, args.seed)
    fit(pos_b, easy_b, ["staff"], "PANEL B foil — vs the label set's own EASY negatives (NOT SHIPPED)")
    table.update(fit(pos_b, hard_b, ["staff"],
                     "PANEL B — core-staff co-authorship (labelled yes vs random corpus)"))

    scored = [s for s in hard_b if s.llm_score]
    fit_line(graded_bins(pos_b, easy_b, lambda s: s.llm_score, list(range(1, 11))),
             len(pos_b), len(easy_b), "llm foil — vs EASY negatives (NOT SHIPPED)")
    llm_fit = fit_line(graded_bins(pos_b, scored, lambda s: s.llm_score, list(range(1, 11))),
                       len(pos_b), len(scored), "llm 1-10 dense triage score")
    aff_fit = fit_line(graded_bins(pos_b, hard_b, lambda s: s.author_affinity,
                                   [0.0001, 0.45, 0.6, 0.75]),
                       len(pos_b), len(hard_b), "affinity repeat-user prior", through_origin=True)

    print("\n\n# --- paste into pipeline_cores/combine.py ---")
    print("WEIGHTS = {")
    for k, (w, note) in table.items():
        print(f'    "{k}": {w:.2f},' + (f"  # {note}" if note else ""))
    print("}")
    print(f"LLM_INTERCEPT = {llm_fit[0]:.2f}\nLLM_PER_POINT = {llm_fit[1]:.2f}")
    print(f"AFFINITY_PER_UNIT = {aff_fit[1]:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""EXPERIMENT: does TF-IDF text similarity add anything to combine()'s score?

Two candidate signals, both measured on panel B (analysis/labeled_set.csv, the 237
human yes/no imaging-core labels) with LEAVE-ONE-OUT profiles, so a labelled paper
never scores against a profile that contains itself:

  1. WITHIN-AUTHOR. For each byline author who has core papers OTHER than this one,
     cos(paper, centroid of that author's core papers) - cos(paper, centroid of that
     author's other corpus papers); MAX over those authors (the same MAX-over-byline
     rule as signals.author_affinity). The aim is ranking WITHIN one repeat user's
     papers, where the affinity rate is constant across all of them and so cannot.
     There are no `rejected` rows for the panel core, so "their other papers" is the
     contrast set. `within_core_only` is the same without the contrast term.
  2. CORE-WIDE. cos(paper, centroid of all of the core's confirmed/claimed papers
     except this one) - cos(paper, centroid of a random corpus sample).

"Core papers" = the core's signal-2 confirms (analysis/confirmed_pmids_by_core.json)
UNION its DynamoDB confirmed/claimed rows, corpus-gated exactly as run.py gates them.

The BASELINE is combine.score() on the panel-B SignalResult the fitting script builds
(staff, affinity, stored LLM score) plus `ack` where a cached full text exists, with the
affinity index built from confirms outside the label set exactly as
scripts/fit_evidence_weights.py builds it, i.e. the score the shipped weights describe.
The labelled NO rows are easy and that baseline separates them at ~0.999, so two HARD
panels are reported too: labelled yes vs the fit's own random-corpus draw, with a
byline-only baseline, and vs the LLM-scored subset of it with staff + affinity + LLM.

Results and the decision not to wire either feature: docs/cores-tfidf-similarity-experiment.md

Reported for each feature: marginal AUC; AUC of `baseline_logit + w*feature` against
`baseline_logit`, w fitted leave-one-out (the paper being scored never fits its own w,
so the lift is out-of-sample); the same restricted to the
repeat-user subset (author_affinity > 0); a paired bootstrap CI on the lift; and
redundancy with the LLM (Spearman rho, and LOO-CV AUC lift over llm_score alone).

TF-IDF is a minimal pure-Python implementation: scikit-learn is not a dependency of
this repo, and the experiment needs nothing it would add.

READ-ONLY: SELECTs against ReciterDB, a filtered Scan of the core's DynamoDB rows, and
Bedrock triage calls for --llm-negatives. Nothing is written anywhere except the local
--out / --llm-cache JSON files.

    python3 scripts/experiment_tfidf_similarity.py
    python3 scripts/experiment_tfidf_similarity.py --corpus-sample 4000 --out /tmp/tfidf.json
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import math
import random
import re
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from pipeline_cores import combine, ingest, signals  # noqa: E402
from pipeline_cores.dictionary import load_core  # noqa: E402
from pipeline_cores.fulltext import to_plain_text  # noqa: E402
from pipeline_cores.models import SignalResult  # noqa: E402

GROUND_TRUTH = Path("/Users/paulalbert/Dropbox/Projects/Inferring Cores and Services/analysis")
LABELS = GROUND_TRUTH / "labeled_set.csv"
LLM_SCORES = GROUND_TRUTH / "calibration_llm_results.json"
CONFIRMED = GROUND_TRUTH / "confirmed_pmids_by_core.json"
PANEL_TEXT = GROUND_TRUTH / "pubmed_data.json"        # title+abstract for all 237
FULLTEXT_DIR = GROUND_TRUTH / "fulltext"               # cached PMC XML, <pmid>.xml
LABEL_CORE = "2"

# ---------------------------------------------------------------------------
# minimal TF-IDF
# ---------------------------------------------------------------------------
_STOP = set("""
a about above after again against all also am an and any are as at be because been
before being below between both but by can could did do does doing down during each
few for from further had has have having he her here hers him his how i if in into is
it its itself just me more most my no nor not of off on once only or other our out
over own same she should so some such than that the their them then there these they
this those through to too under until up very was we were what when where which while
who whom why will with would you your yours however therefore thus using used use
study studies patients patient results result methods method conclusion conclusions
background objective objectives aim aims we our among within whether may also two one
three new based compared significant significantly associated including
""".split())
_TOKEN = re.compile(r"[a-z][a-z0-9\-]{2,}")


def tokens(text: str) -> list:
    return [t for t in _TOKEN.findall((text or "").lower()) if t not in _STOP]


class Tfidf:
    """Sublinear tf, smoothed idf, L2-normalised sparse dict vectors."""

    def __init__(self, docs: dict):
        df = collections.Counter()
        for toks in docs.values():
            df.update(set(toks))
        n = len(docs)
        self.idf = {t: math.log((1 + n) / (1 + c)) + 1.0 for t, c in df.items() if c >= 2}
        self.vec = {k: self._vectorise(v) for k, v in docs.items()}

    def _vectorise(self, toks) -> dict:
        tf = collections.Counter(t for t in toks if t in self.idf)
        v = {t: (1 + math.log(c)) * self.idf[t] for t, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {t: x / norm for t, x in v.items()}


def vsum(vecs) -> dict:
    out: dict = collections.defaultdict(float)
    for v in vecs:
        for t, x in v.items():
            out[t] += x
    return out


def cos_with_sum(v: dict, s: dict, minus: dict | None = None) -> float:
    """cos(v, s - minus): a centroid with one member removed, without rebuilding it."""
    if minus is not None:
        s = {t: s.get(t, 0.0) - minus.get(t, 0.0) for t in s}
    norm = math.sqrt(sum(x * x for x in s.values()))
    if norm < 1e-9:
        return float("nan")
    return sum(x * s.get(t, 0.0) for t, x in v.items()) / norm


# ---------------------------------------------------------------------------
# evaluation primitives
# ---------------------------------------------------------------------------
def auc(y, s) -> float:
    """Mann-Whitney AUC with tie correction."""
    y, s = np.asarray(y), np.asarray(s, dtype=float)
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(len(s))
    sv = s[order]
    i = 0
    while i < len(sv):
        j = i
        while j + 1 < len(sv) and sv[j + 1] == sv[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2 + 1
        i = j + 1
    npos, nneg = int(y.sum()), int(len(y) - y.sum())
    if not npos or not nneg:
        return float("nan")
    return (ranks[y == 1].sum() - npos * (npos + 1) / 2) / (npos * nneg)


def fit_w(offset, feat, y, l2: float = 1e-3, iters: int = 100) -> float:
    """w in  logit = a + offset + w * feat  (offset's coefficient FIXED at 1).

    Exactly the deployment form: combine() would ADD w * feature to the logit it
    already computes. The free intercept `a` absorbs the panel's prevalence (25-58%
    positive here vs the ~2% corpus prior the logit is built on), which a likelihood
    ratio must not be asked to carry; it is fitted and then DROPPED from the score,
    because a shared constant cannot change an AUC and a per-fold one only breaks
    ties against the held-out label (which is what manufactured a fake lift when the
    baseline's own slope and intercept were re-fitted per fold).
    """
    X = np.column_stack([np.ones(len(feat)), feat])
    b = np.zeros(2)
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(offset + X @ b, -30, 30)))
        g = X.T @ (y - p) - l2 * np.r_[0.0, b[1]]
        H = (X * (p * (1 - p))[:, None]).T @ X + l2 * np.diag([0.0, 1.0]) + 1e-9 * np.eye(2)
        step = np.linalg.solve(H, g)
        b += step
        if np.abs(step).max() < 1e-9:
            break
    return float(b[1])


def lift(base, feat, y, rng, reps: int = 2000) -> dict:
    """AUC of `base` vs leave-one-out `base + w*feat` (w fitted without the held-out
    paper); paired bootstrap 95% CI on the difference."""
    y, base, feat = (np.asarray(v, dtype=float) for v in (y, base, feat))
    s1 = np.empty(len(y))
    for i in range(len(y)):
        m = np.arange(len(y)) != i
        s1[i] = base[i] + fit_w(base[m], feat[m], y[m]) * feat[i]
    a0, a1 = auc(y, base), auc(y, s1)
    diffs = []
    for _ in range(reps):
        idx = rng.integers(0, len(y), len(y))
        if 0 < y[idx].sum() < len(idx):
            diffs.append(auc(y[idx], s1[idx]) - auc(y[idx], base[idx]))
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    return {"n": len(y), "pos": int(y.sum()), "auc_base": a0, "auc_with": a1,
            "lift": a1 - a0, "ci95": (lo, hi), "w_full": fit_w(base, feat, y)}


def spearman(a, b) -> float:
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    return float(np.corrcoef(ra, rb)[0, 1])


# ---------------------------------------------------------------------------
def build(args):
    from sqlalchemy import bindparam, text  # lazy

    from pipeline_cores.persist import scan_prior_core_usage  # lazy
    from utils.db import get_engine  # lazy

    engine = get_engine()
    labels = {r["pmid"]: (1 if r["label"] == "yes" else 0) for r in csv.DictReader(LABELS.open())}
    stored_llm = json.loads(LLM_SCORES.read_text())
    panel_text = json.loads(PANEL_TEXT.read_text())
    core = load_core(LABEL_CORE)

    # --- core papers: signal-2 confirms UNION DynamoDB confirmed/claimed, corpus-gated
    confirms = [str(p) for p in json.loads(CONFIRMED.read_text())[LABEL_CORE]]
    ddb = [r["pmid"] for r in scan_prior_core_usage(LABEL_CORE, strict=True)]
    core_papers = ingest.filter_corpus_pmids(engine, set(confirms) | set(ddb))
    print(f"core {LABEL_CORE} papers: {len(set(confirms))} signal-2 confirms, {len(set(ddb))} "
          f"DynamoDB confirmed/claimed, {len(core_papers)} in corpus after union; "
          f"{len(core_papers & set(labels))} of them are in the label set")

    # --- the HARD negatives: the same random corpus draw the fitting script prices
    #     panel B against (random.seed(11); random.sample(corpus, 1200)), minus any core
    #     paper or labelled paper. The labelled NO rows are easy negatives and the
    #     baseline separates them almost perfectly, so they cannot show a lift.
    corpus = [r["pmid"] for r in ingest.fetch_publications(engine)]
    random.seed(args.seed)
    drawn = random.sample(corpus, args.negatives)
    hard = [p for p in drawn if p not in core_papers and p not in labels]
    contrast = random.Random(args.seed + 1).sample(corpus, args.corpus_sample)
    pmids = sorted(labels) + hard
    panel = {p: "label" for p in labels} | {p: "random" for p in hard}

    # --- the baseline, exactly as fit_evidence_weights.panel_b builds it
    bylines = ingest.fetch_author_bylines(engine, pmids)
    coauthors = signals.coauthorship_index(engine, core, pmids)
    outside = sorted(ingest.filter_corpus_pmids(engine, [p for p in confirms if p not in labels]))
    counts: dict = collections.defaultdict(lambda: collections.defaultdict(int))
    for cwids in ingest.fetch_author_bylines(engine, outside).values():
        for c in cwids:
            counts[c][LABEL_CORE] += 1
    index = signals.build_affinity_index(counts, ingest.fetch_author_totals(engine, list(counts)))

    def ack(pmid):
        f = FULLTEXT_DIR / f"{pmid}.xml"
        if not f.exists():
            return SignalResult()
        xml = f.read_text(errors="ignore")
        return signals.acknowledgement_signal(to_plain_text(xml), core, xml=xml)

    # Live two-pass LLM triage for a subset of the random negatives — the same subset
    # rule the fitting script uses (random.Random(seed).sample(negatives, 400)) PLUS
    # every random negative with affinity > 0, so the repeat-user subset is complete.
    # Bedrock reads only; cached to --llm-cache so a re-run costs nothing.
    live_llm: dict = {}
    if args.llm_negatives:
        cache = Path(args.llm_cache) if args.llm_cache else None
        if cache and cache.exists():
            live_llm = json.loads(cache.read_text())
        rep_neg = [p for p in hard
                   if signals.author_affinity(index, bylines.get(p, []), LABEL_CORE) > 0]
        pick = set(random.Random(args.seed).sample(drawn, min(args.llm_negatives, len(drawn))))
        todo = sorted((pick & set(hard)) | set(rep_neg))
        need = [p for p in todo if p not in live_llm]
        if need:
            from utils.bedrock_client import BedrockClient  # lazy
            print(f"llm: triaging {len(need)} random negatives live through Bedrock")
            live_llm.update(signals.llm_triage(BedrockClient(read_timeout=120), core,
                                               ingest.fetch_publications(engine, pmids=need)))
            if cache:
                cache.write_text(json.dumps(live_llm))
        live_llm = {p: live_llm[p] for p in todo if p in live_llm}
        print(f"llm: {len(live_llm)} random negatives carry a live LLM score "
              f"({len(rep_neg)} of them repeat-user)")

    base, byline_base, aff, llm = {}, {}, {}, {}
    nolack = {}
    for p in pmids:
        a = ack(p) if panel[p] == "label" else SignalResult()
        sig = SignalResult(
            ack_matched=a.ack_matched, ack_alias=a.ack_alias, ack_alias_hits=a.ack_alias_hits,
            ack_institution=a.ack_institution, ack_section=a.ack_section,
            coauthor_cwids=coauthors.get(p, []),
            llm_score=(stored_llm if panel[p] == "label" else live_llm).get(p, {}).get("score"),
            author_affinity=signals.author_affinity(index, bylines.get(p, []), LABEL_CORE))
        pr = combine.score(sig)
        base[p] = math.log(pr / (1 - pr))
        # The byline-only score (staff + affinity): the one baseline computable on the
        # random negatives too, which have no stored LLM score and no cached full text.
        pr = combine.score(SignalResult(coauthor_cwids=sig.coauthor_cwids,
                                        author_affinity=sig.author_affinity))
        byline_base[p] = math.log(pr / (1 - pr))
        # staff + affinity + LLM, no `ack`: computable on both sides wherever an LLM
        # score exists, so it is the like-for-like full baseline on the hard panel.
        pr = combine.score(SignalResult(coauthor_cwids=sig.coauthor_cwids,
                                        author_affinity=sig.author_affinity,
                                        llm_score=sig.llm_score))
        nolack[p] = math.log(pr / (1 - pr))
        aff[p] = sig.author_affinity
        llm[p] = sig.llm_score
    n_ft = sum((FULLTEXT_DIR / f"{p}.xml").exists() for p in labels)
    print(f"baseline: {n_ft}/{len(labels)} labelled papers have cached full text for `ack`; "
          f"{len(hard)} random-corpus hard negatives")

    # --- texts: every paper by every byline author on the panel, the core papers, and a
    #     random corpus sample (the core-wide contrast)
    authors = sorted({c for p in pmids for c in bylines.get(p, [])})
    stmt = text("SELECT DISTINCT pmid, personIdentifier FROM analysis_summary_author "
                "WHERE personIdentifier IN :c").bindparams(bindparam("c", expanding=True))
    author_pmids: dict = collections.defaultdict(set)
    with engine.connect() as conn:
        for i in range(0, len(authors), 1000):
            for row in conn.execute(stmt, {"c": authors[i:i + 1000]}):
                author_pmids[row.personIdentifier].add(str(row.pmid))
    want = set().union(*author_pmids.values()) | core_papers | set(contrast) | set(hard)
    texts = {}
    want_l = sorted(want - set(labels))
    for i in range(0, len(want_l), 2000):
        for r in ingest.fetch_publications(engine, pmids=want_l[i:i + 2000]):
            texts[r["pmid"]] = f'{r["title"]} {r["abstract"]}'
    for p in labels:                                   # labelled text from the panel itself
        d = panel_text.get(p, {})
        texts[p] = f'{d.get("title", "")} {d.get("abstract", "")}'
    for c in author_pmids:
        author_pmids[c] &= set(texts)                  # corpus papers only, like the rate
    print(f"texts: {len(texts)} docs ({len(authors)} byline authors, "
          f"{len(contrast)} random corpus contrast sample)")

    tf = Tfidf({k: tokens(v) for k, v in texts.items()})
    V = tf.vec
    core_sum = vsum(V[p] for p in core_papers if p in V)
    contrast_set = {p for p in contrast if p in V}
    corpus_sum = vsum(V[p] for p in contrast_set)

    rows = []
    for p in pmids:
        v = V[p]
        # leave-one-out on BOTH centroids: a paper never scores against itself
        core_wide = (cos_with_sum(v, core_sum, V[p] if p in core_papers else None)
                     - cos_with_sum(v, corpus_sum, V[p] if p in contrast_set else None))
        within, within_core = None, None
        for c in bylines.get(p, []):
            mine = author_pmids.get(c, set()) - {p}
            cp = mine & core_papers
            op = mine - core_papers
            if not cp:
                continue                               # no OTHER core paper -> no profile
            s_core = cos_with_sum(v, vsum(V[q] for q in cp))
            s_other = cos_with_sum(v, vsum(V[q] for q in op)) if op else 0.0
            d = s_core - s_other
            within = d if within is None else max(within, d)
            within_core = s_core if within_core is None else max(within_core, s_core)
        top = max(bylines.get(p, []), key=lambda c: index.get(c, {}).get(LABEL_CORE, 0.0),
                  default=None)
        if top is not None and not index.get(top, {}).get(LABEL_CORE):
            top = None
        rows.append({"pmid": p, "panel": panel[p], "top_author": top, "y": 1 if labels.get(p) else 0,
                     "base_logit": base[p], "byline_logit": byline_base[p],
                     "noack_logit": nolack[p], "aff": aff[p],
                     "llm": llm[p], "core_wide": core_wide,
                     "within": within, "within_core_only": within_core})
    return rows


def _feat(rows, key):
    has = np.array([r[key] is not None and not math.isnan(r[key]) for r in rows])
    return has, np.array([r[key] if h else 0.0 for r, h in zip(rows, has)])


def _show(name, y, base, feat, rng, llm=None):
    if len(y) < 10 or y.sum() in (0, len(y)):
        print(f"  {name}: n={len(y)} (yes {int(y.sum())}) -- one-class or too small, NOT MEASURABLE")
        return
    L = lift(base, feat, y, rng)
    print(f"  {name}: n={L['n']} (yes {L['pos']})  marginal AUC={auc(y, feat):.4f}  "
          f"base AUC={L['auc_base']:.4f} -> +feat {L['auc_with']:.4f}  "
          f"lift={L['lift']:+.4f} CI95=[{L['ci95'][0]:+.4f},{L['ci95'][1]:+.4f}]  "
          f"w={L['w_full']:.2f}")
    if llm is not None:
        Ll = lift(llm, feat, y, rng)
        print(f"      vs llm: spearman rho={spearman(feat, llm):.3f}  llm AUC="
              f"{Ll['auc_base']:.4f} -> +feat {Ll['auc_with']:.4f} lift={Ll['lift']:+.4f} "
              f"CI95=[{Ll['ci95'][0]:+.4f},{Ll['ci95'][1]:+.4f}]")


def same_author_auc(rows, key):
    """AUC over yes/no PAIRS that share their top-affinity author (affinity is constant
    within such a pair, so this is exactly the ranking the rate cannot do)."""
    groups = collections.defaultdict(list)
    for r in rows:
        if r.get("top_author") and r[key] is not None and not math.isnan(r[key]):
            groups[r["top_author"]].append(r)
    conc = ties = n = 0
    authors = 0
    for g in groups.values():
        pos = [r[key] for r in g if r["y"]]
        neg = [r[key] for r in g if not r["y"]]
        if pos and neg:
            authors += 1
        for x in pos:
            for z in neg:
                n += 1
                conc += x > z
                ties += x == z
    return (conc + ties / 2) / n if n else float("nan"), n, authors


def llm_logit(score) -> float:
    """The LLM's own contribution to combine()'s logit (prior included)."""
    if not score:
        return combine.PRIOR_LOGIT
    return (combine.PRIOR_LOGIT + combine.LLM_INTERCEPT
            + combine.LLM_PER_POINT * min(score, combine.LLM_MAX_FITTED))


def report(rows, seed: int):
    rng = np.random.default_rng(seed)
    keys = ("core_wide", "within", "within_core_only")

    lab = [r for r in rows if r["panel"] == "label"]
    y = np.array([r["y"] for r in lab])
    base = np.array([r["base_logit"] for r in lab])
    llm = np.array([llm_logit(r["llm"]) for r in lab])
    rep = np.array([r["aff"] > 0 for r in lab])
    print(f"\n##### PANEL B as labelled: n={len(y)}  yes={int(y.sum())}  no={int(len(y) - y.sum())}")
    print("  baseline = full combine() logit (staff + affinity + stored LLM + ack where cached)")
    print(f"  baseline AUC = {auc(y, base):.4f}   llm_score alone AUC = {auc(y, llm):.4f}")
    print(f"  repeat-user subset (author_affinity > 0): n={rep.sum()} yes={int(y[rep].sum())}")
    for key in keys:
        has, f = _feat(lab, key)
        print(f"\n=== {key} ===  defined on {has.sum()}/{len(lab)} (yes {int(y[has].sum())})")
        _show("all (0 where undefined)", y, base, f, rng, llm)
        _show("where defined", y[has], base[has], f[has], rng, llm[has])
        _show("repeat-user subset", y[rep & has], base[rep & has], f[rep & has], rng, llm[rep & has])

    hard = [r for r in rows if r["panel"] == "random" or r["y"] == 1]
    y = np.array([r["y"] for r in hard])
    base = np.array([r["byline_logit"] for r in hard])
    rep = np.array([r["aff"] > 0 for r in hard])
    print(f"\n##### PANEL B HARD: labelled yes vs random corpus  n={len(y)}  yes={int(y.sum())}  "
          f"random={int(len(y) - y.sum())}")
    print("  baseline = byline-only combine() logit (staff + affinity); no LLM/full text on "
          "the random side")
    print(f"  baseline AUC = {auc(y, base):.4f}")
    print(f"  repeat-user subset (author_affinity > 0): n={rep.sum()} yes={int(y[rep].sum())} "
          f"random={int(rep.sum() - y[rep].sum())}; baseline AUC there = "
          f"{auc(y[rep], base[rep]):.4f}")
    for key in keys:
        has, f = _feat(hard, key)
        print(f"\n=== {key} ===  defined on {has.sum()}/{len(hard)} (yes {int(y[has].sum())})")
        _show("all (0 where undefined)", y, base, f, rng)
        _show("where defined", y[has], base[has], f[has], rng)
        _show("repeat-user subset", y[rep & has], base[rep & has], f[rep & has], rng)
        a, n, k = same_author_auc(hard, key)
        print(f"  same-top-author pairs: AUC={a:.4f} over {n} yes/random pairs from {k} authors")

    full = [r for r in hard if r["llm"]]
    if not any(r["y"] == 0 for r in full):
        print("\n(no random negative carries an LLM score -- rerun with --llm-negatives)")
        return
    y = np.array([r["y"] for r in full])
    base = np.array([r["noack_logit"] for r in full])
    llm = np.array([llm_logit(r["llm"]) for r in full])
    rep = np.array([r["aff"] > 0 for r in full])
    print(f"\n##### PANEL B HARD + LLM: labelled yes vs LLM-scored random corpus  n={len(y)}  "
          f"yes={int(y.sum())}  random={int(len(y) - y.sum())}")
    print("  baseline = combine() logit on staff + affinity + LLM (no `ack`: no full text on "
          "the random side)")
    print(f"  baseline AUC = {auc(y, base):.4f}   llm alone AUC = {auc(y, llm):.4f}")
    print(f"  repeat-user subset: n={rep.sum()} yes={int(y[rep].sum())} "
          f"random={int(rep.sum() - y[rep].sum())}; baseline AUC there = "
          f"{auc(y[rep], base[rep]):.4f}")
    for key in keys:
        has, f = _feat(full, key)
        print(f"\n=== {key} ===  defined on {has.sum()}/{len(full)} (yes {int(y[has].sum())})")
        _show("all (0 where undefined)", y, base, f, rng, llm)
        _show("where defined", y[has], base[has], f[has], rng, llm[has])
        _show("repeat-user subset", y[rep & has], base[rep & has], f[rep & has], rng, llm[rep & has])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--corpus-sample", type=int, default=3000,
                    help="random corpus papers for the core-wide contrast + IDF (default 3000)")
    ap.add_argument("--negatives", type=int, default=1200,
                    help="random corpus draw for the hard negatives (default 1200, as the fit)")
    ap.add_argument("--llm-negatives", type=int, default=400,
                    help="random negatives put through live Bedrock triage (default 400, as "
                         "the fit; 0 = none). Repeat-user negatives are always added.")
    ap.add_argument("--llm-cache", help="local JSON cache of those live LLM scores")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--out", help="write the per-paper features as JSON here (local only)")
    ap.add_argument("--from-json", help="skip the DB/DynamoDB reads; re-report a saved --out")
    args = ap.parse_args(argv)
    if args.from_json:
        rows = json.loads(Path(args.from_json).read_text())
    else:
        rows = build(args)
        if args.out:
            Path(args.out).write_text(json.dumps(rows))
    report(rows, args.seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

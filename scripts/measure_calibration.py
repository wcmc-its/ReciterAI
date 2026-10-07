#!/usr/bin/env python3
"""Is combine.score()'s probability ACCURATE, and would a calibration step fix it?

combine.score() fits every weight MARGINALLY and sums the log-odds (naive Bayes). A
signal that is redundant with another one can still sharpen the probability, but
only a JOINT fit can use that: summed marginal weights double-count correlated
evidence, which shows up as an OVERCONFIDENT probability (a Platt slope below 1)
even when the ranking (AUC) is fine. This script measures that, held out, on the two
labelled panels that exist, for four ways of turning the evidence into a probability:

  (1) combine.score() as shipped (no refit at all)
  (2) (1) + Platt recalibration: logistic(intercept + slope * logit(score))
  (3) a joint L2-regularised logistic on the existing evidence (the ack block, staff,
      the three aff:* buckets, the LLM score)
  (4) (3) + candidate REDUNDANT features: a WCM author first / last on the byline
      (PubMed affiliations), the INSIGHT CRN alias (feat/insight-alias, #422; not in
      this branch's dictionary) and the A2 method tier (core 14 only)

Panels:
  B   core 2 (Biomedical Imaging): the 137 human "yes" labels vs the random-corpus
      negatives that carry a live LLM score (the fit script's own panel B), with the
      100 labelled "no" reported as a separate foil. A CASE-CONTROL panel: every
      metric is importance-weighted so positives carry PRIOR (= combine's 2%) of the
      weight, i.e. scored at the prevalence combine.PRIOR_LOGIT assumes; models (2)-(4)
      are fitted with the same weights. The Platt SLOPE is prevalence-invariant and is
      reported unweighted too.
  14  core 14 (Research Informatics): every claimed (1) / rejected (0) row, unweighted.
      14w adds engine-confirmed rows carrying ack or staff evidence as WEAK positives.

Held-out predictions are leave-one-out (every model, including the Platt fit, is
refitted without the scored row; the L2 strength is chosen by an inner CV on the
training fold only). CIs are a paired bootstrap over rows of those held-out
predictions (they do not re-run the refit, so they understate model variance).

TWO STEPS, so the analysis is re-runnable offline:

    # READ-ONLY: one DynamoDB Scan of CORE#14, reciterdb SELECTs, S3 GETs (full-text
    # cache read through a wrapper whose put_object refuses, A2 tools artifact),
    # PubMed efetch, and Bedrock triage ONLY for papers with no stored/cached score.
    python3 scripts/measure_calibration.py collect --out inputs.json \
        --llm-cache panelb_llm.json --c14-llm-cache c14_llm.json --cache-dir ftcache

    # Pure: numpy + scikit-learn on inputs.json.
    python3 scripts/measure_calibration.py analyze --inputs inputs.json

Results 2026-10-06: docs/experiments/core14-calibration-2026-10-06.md.
"""
from __future__ import annotations

import argparse
import collections
import csv
import dataclasses
import json
import logging
import math
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from pipeline_cores import combine as C  # noqa: E402
from pipeline_cores.models import SignalResult  # noqa: E402

PRIOR = 0.02                                   # combine.PRIOR_LOGIT's base rate
INSIGHT_ALIASES = {"INSIGHT Clinical Research Network": 76, "INSIGHT CRN": 33}   # #422
CUTS = {"triage": C.DEFAULT_TRIAGE_THRESHOLD, "sps_floor": 0.40,
        "confirm": C.DEFAULT_CONFIRM_THRESHOLD}
_HOME = re.compile(r"Weill|Cornell|WCM|NewYork-Presbyterian|New York-Presbyterian|NYP", re.I)


# ===========================================================================
# collect (network, read-only)
# ===========================================================================
class ReadOnlyS3:
    """The full-text cache's S3 tier for READS ONLY: PmcFullTextClient writes a miss
    back through put_object, and this experiment must not write anything."""

    def __init__(self):
        from pipeline_cores.fulltext import make_s3_backend
        self._s3 = make_s3_backend()

    def key_exists(self, key):
        return self._s3.key_exists(key)

    def get_object_bytes(self, key):
        return self._s3.get_object_bytes(key)

    def put_object(self, *_a, **_k):
        raise PermissionError("read-only experiment: S3 write refused")


def fulltext(pmids, cache_dir) -> dict:
    from pipeline_cores.fulltext import PmcFullTextClient
    client = PmcFullTextClient(cache_dir=Path(cache_dir), s3=ReadOnlyS3(), min_interval=0.4)
    return {p: client.get_xml(p) or "" for p in sorted(set(pmids))}


def pubmed_authorship(pmids, cache: Path) -> dict:
    """pmid -> {first, last}: does the first / last author carry a WCM affiliation
    (PubMed efetch; the same regex as exp/core-deposit-signal #428)."""
    import urllib.parse
    import urllib.request
    import xml.etree.ElementTree as ET
    out = json.loads(cache.read_text()) if cache.exists() else {}
    todo = sorted(set(pmids) - set(out))
    for b in range(0, len(todo), 150):
        q = urllib.parse.urlencode({"db": "pubmed", "id": ",".join(todo[b:b + 150]),
                                    "retmode": "xml"}).encode()
        root = ET.fromstring(urllib.request.urlopen(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi", q, timeout=120).read())
        for art in root.iter("PubmedArticle"):
            pm = art.find(".//PMID").text
            h = [bool(_HOME.search(" ".join(a.text or "" for a in au.findall(".//Affiliation"))))
                 for au in art.findall(".//AuthorList/Author")]
            out[pm] = {"first": bool(h and h[0]), "last": bool(h and h[-1])}
        time.sleep(0.4)
    cache.write_text(json.dumps(out))
    return out


def pubmed_text(pmids) -> list:
    """[{pmid, title, abstract}] from PubMed efetch, for papers reciterdb does not carry."""
    import urllib.parse
    import urllib.request
    import xml.etree.ElementTree as ET
    out = []
    for b in range(0, len(pmids), 150):
        q = urllib.parse.urlencode({"db": "pubmed", "id": ",".join(pmids[b:b + 150]),
                                    "retmode": "xml"}).encode()
        root = ET.fromstring(urllib.request.urlopen(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi", q, timeout=120).read())
        for art in root.iter("PubmedArticle"):
            out.append({"pmid": art.find(".//PMID").text,
                        "title": "".join(art.find(".//ArticleTitle").itertext()),
                        "abstract": " ".join("".join(a.itertext())
                                             for a in art.findall(".//Abstract/AbstractText"))})
        time.sleep(0.4)
    return out


def ack_features(xml: str, core) -> tuple:
    """(production ack SignalResult, INSIGHT alias matched?) from one paper's XML."""
    from pipeline_cores import signals
    from pipeline_cores.fulltext import to_plain_text
    text = to_plain_text(xml) if xml else ""
    sig = signals.acknowledgement_signal(text, core, xml=xml)
    ins = dataclasses.replace(core, aliases=list(INSIGHT_ALIASES), alias_hits=dict(INSIGHT_ALIASES))
    return sig, bool(text) and signals.acknowledgement_signal(text, ins, xml=xml).ack_matched


def sig_dict(s: SignalResult) -> dict:
    return {k: getattr(s, k) for k in ("coauthor_cwids", "ack_matched", "ack_alias", "llm_score",
                                       "author_affinity", "ack_alias_hits", "ack_institution",
                                       "ack_section", "method_tier")}


def triage(core, pmids, cache: Path, engine, live: bool) -> dict:
    """pmid -> llm score from the cache, else (with --live-llm) signals.llm_triage."""
    from pipeline_cores import ingest, signals
    got = json.loads(cache.read_text()) if cache and cache.exists() else {}
    todo = [p for p in pmids if p not in got]
    if todo and live:
        from utils.bedrock_client import BedrockClient
        pubs = ingest.fetch_publications(engine, pmids=todo)
        # A claimed paper outside the WCM corpus has no reciterdb title/abstract (and
        # production never scores it); its text comes from PubMed instead, so the score
        # function can still be asked what it would say about it.
        pubs += pubmed_text(sorted(set(todo) - {p["pmid"] for p in pubs}))
        print(f"  triaging {len(pubs)} papers through Bedrock (signals.llm_triage)")
        got.update(signals.llm_triage(BedrockClient(read_timeout=120), core, pubs))
        if cache:
            cache.write_text(json.dumps(got))
    return {p: got[p]["score"] for p in pmids if p in got}


def collect_panel_b(engine, args, family_index, auth_cache) -> list:
    import fit_evidence_weights as F
    from pipeline_cores import ingest, signals
    from pipeline_cores.dictionary import load_core
    labels = {r["pmid"]: r["label"] for r in csv.DictReader(F.LABELS.open())}
    stored = json.loads(F.LLM_SCORES.read_text())
    # The negatives are the papers in the live-LLM cache: the fit script's rule (a
    # seed-11 draw of 1,200 corpus papers, 400 of them triaged, plus every repeat-user
    # paper among the 1,200), drawn from the corpus as it stood when the cache was built.
    # Re-drawing today gives a different 1,200 (the corpus has grown), which is why the
    # cache's own keys are the panel rather than a fresh draw.
    live = json.loads(Path(args.llm_cache).read_text())
    corpus = ingest.filter_corpus_pmids(engine, list(live))
    neg = sorted(p for p in live if p not in labels and p in corpus)
    core = load_core(F.LABEL_CORE)
    everyone = sorted(labels) + neg
    print(f"panel B: {len(labels)} labelled; {len(neg)} of {len(live)} cached live-LLM random "
          f"negatives still in the corpus ({args.llm_cache})")
    coauthors = signals.coauthorship_index(engine, core, everyone)
    _b, _y, aff = F.affinity_panel(engine, labels, neg, core)
    xml = fulltext(everyone, args.cache_dir)
    auth = pubmed_authorship(everyone, auth_cache)
    rows = []
    for p in everyone:
        s, insight = ack_features(xml.get(p, ""), core)
        s.coauthor_cwids = coauthors.get(p, [])
        s.author_affinity = aff(p)
        s.llm_score = (stored if p in labels else live).get(p, {}).get("score")
        s.method_evidence, s.method_tier = signals.method_family_signal(family_index, p, core)
        rows.append(dict(pmid=p, panel="B" if labels.get(p) != "no" else "B_easy",
                         y=1 if labels.get(p) == "yes" else 0, has_ft=bool(xml.get(p)),
                         sig=sig_dict(s), insight=insight, **auth.get(p, {"first": None, "last": None})))
    print(f"  full text for {sum(r['has_ft'] for r in rows)}/{len(rows)}; ack on "
          f"{sum(r['sig']['ack_matched'] for r in rows)}; staff on "
          f"{sum(bool(r['sig']['coauthor_cwids']) for r in rows)}; PubMed authorship for "
          f"{sum(r['first'] is not None for r in rows)}")
    return rows


def scan_core14() -> list:
    from utils.dynamodb_helpers import TABLE_NAME, get_dynamo_client
    client = get_dynamo_client()
    kw = dict(TableName=TABLE_NAME, FilterExpression="SK = :sk",
              ExpressionAttributeValues={":sk": {"S": "CORE#14"}})
    rows = []
    while True:
        page = client.scan(**kw)
        for it in page.get("Items", []):
            llm = it.get("llm_score", {}).get("N")
            rows.append(dict(pmid=it["pmid"]["S"], status=it["status"]["S"],
                             likelihood=float(it.get("likelihood", {}).get("N", 0)),
                             stored_affinity=float(it.get("author_affinity", {}).get("N", 0)),
                             llm=int(float(llm)) if llm else None,
                             ack=bool(it.get("signal_ack", {}).get("BOOL")),
                             coauthors=bool(it.get("signal_coauthors", {}).get("L")),
                             method_tier=it.get("method_tier", {}).get("S", "")))
        if not page.get("LastEvaluatedKey"):
            return rows
        kw["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def collect_core14(engine, args, family_index, auth_cache) -> tuple:
    from pipeline_cores import ingest, signals
    from pipeline_cores.dictionary import load_core
    from pipeline_cores.persist import get_curated_staff
    core = load_core("14")
    rows = scan_core14()
    pmids = sorted({r["pmid"] for r in rows})
    in_corpus = ingest.filter_corpus_pmids(engine, pmids)
    bylines = ingest.fetch_author_bylines(engine, pmids)
    dyn_staff = get_curated_staff("14")
    staff = {c.lower() for c in core.staff_cwids} | {c.lower() for c in dyn_staff}
    # The affinity index exactly as measure_affinity_prior_strength.py / run_core build it.
    papers = collections.defaultdict(lambda: collections.defaultdict(set))
    core_pmids = set()
    for r in rows:
        if r["status"] in ("confirmed", "claimed") and r["pmid"] in in_corpus:
            core_pmids.add(r["pmid"])
            for cwid in bylines.get(r["pmid"], []):
                if cwid.lower() not in staff:
                    papers[cwid]["14"].add(r["pmid"])
    counts, totals, tenure, _ = ingest.affinity_inputs(engine, papers)
    corpus_size = ingest.fetch_corpus_size(engine)
    p0 = signals.affinity_base_rate(len(core_pmids), corpus_size)
    idx = signals.build_affinity_index(counts, totals, tenure=tenure, members=papers,
                                       prior_strength={"14": signals.affinity_prior_strength(core)},
                                       base_rate={"14": p0})
    years = ingest.fetch_pub_years(engine, pmids)
    for r in rows:
        r["affinity"] = signals.author_affinity(idx, bylines.get(r["pmid"], []), "14",
                                                years.get(r["pmid"]), pmid=r["pmid"])
    print(f"core 14: {len(rows)} rows {dict(collections.Counter(r['status'] for r in rows))}; "
          f"s={signals.affinity_prior_strength(core):g} p0={len(core_pmids)}/{corpus_size}")

    weak = [r for r in rows if r["status"] == "confirmed" and (r["ack"] or r["coauthors"])]
    panel = [r for r in rows if r["status"] in ("claimed", "rejected")] + weak
    ppm = [r["pmid"] for r in panel]
    coauthors = signals.coauthorship_index(engine, core, ppm, extra_cwids=dyn_staff)
    xml = fulltext(ppm, args.cache_dir)
    auth = pubmed_authorship(ppm, auth_cache)
    missing = [r["pmid"] for r in panel if r["llm"] is None]
    llm = triage(core, missing, Path(args.c14_llm_cache), engine, args.live_llm)
    out = []
    for r in panel:
        s, insight = ack_features(xml.get(r["pmid"], ""), core)
        s.coauthor_cwids = coauthors.get(r["pmid"], [])
        s.author_affinity = r["affinity"]
        s.llm_score = r["llm"] if r["llm"] is not None else llm.get(r["pmid"])
        s.method_evidence, s.method_tier = signals.method_family_signal(
            family_index, r["pmid"], core)
        out.append(dict(pmid=r["pmid"], panel="14" if r["status"] != "confirmed" else "14_weak",
                        status=r["status"], y=0 if r["status"] == "rejected" else 1,
                        has_ft=bool(xml.get(r["pmid"])), in_corpus=r["pmid"] in in_corpus,
                        llm_source="stored" if r["llm"] is not None else
                        ("live" if r["pmid"] in llm else "none"),
                        engine_scored=r["likelihood"] > 0, sig=sig_dict(s), insight=insight,
                        stored_ack=r["ack"], stored_method_tier=r["method_tier"],
                        **auth.get(r["pmid"], {"first": None, "last": None})))
    print(f"  panel: {len(out)} rows; full text {sum(o['has_ft'] for o in out)}; llm sources "
          f"{dict(collections.Counter(o['llm_source'] for o in out))}; ack recomputed "
          f"{sum(o['sig']['ack_matched'] for o in out)} (stored {sum(o['stored_ack'] for o in out)})")
    # Every row, for the status-move count: stored likelihood + recomputed affinity.
    allrows = [dict(pmid=r["pmid"], status=r["status"], likelihood=r["likelihood"],
                    stored_affinity=r["stored_affinity"], affinity=r["affinity"], llm=r["llm"],
                    ack=r["ack"], coauthors=r["coauthors"]) for r in rows]
    return out, allrows


def cmd_collect(args) -> int:
    logging.disable(logging.WARNING)
    from pipeline_cores import method_families
    from pipeline_cores.dictionary import load_cores
    from utils.db import get_engine
    engine = get_engine()
    family_index = method_families.load_family_index(cores=load_cores())
    auth_cache = Path(args.cache_dir) / "pubmed_authorship.json"
    Path(args.cache_dir).mkdir(parents=True, exist_ok=True)
    b = collect_panel_b(engine, args, family_index, auth_cache)
    c14, allrows = collect_core14(engine, args, family_index, auth_cache)
    Path(args.out).write_text(json.dumps(dict(panel=b + c14, core14_all=allrows), default=str))
    print(f"wrote {args.out}")
    return 0


# ===========================================================================
# analyze (pure)
# ===========================================================================
def _sig(d: dict) -> SignalResult:
    return SignalResult(**{k: v for k, v in d.items()})


def logit_of(s: SignalResult) -> float:
    return C.PRIOR_LOGIT + sum(w for _, w in C.explain(s))


def features(row: dict, extended: bool) -> list:
    """(3)'s features are combine's own evidence, one column per independently weighted
    piece; the ack chain-rule block stays one column (its sum), because its parts are
    conditional on each other by construction and cannot be refitted on a panel with a
    handful of matches. (4) appends the candidate redundant features."""
    s = _sig(row["sig"])
    keys = set(C.evidence_features(s))
    ack_block = sum(C.WEIGHTS.get(k, 0.0) for k in keys
                    if k == "ack" or k.startswith(("ack.", "inst:", "sec:")))
    llm = min(s.llm_score, C.LLM_MAX_FITTED) if s.llm_score else 0
    x = [ack_block, float("staff" in keys), float("aff:trace" in keys),
         float("aff:regular" in keys), float("aff:core" in keys), float(llm),
         float(s.llm_score is None)]
    if extended:
        x += [float(bool(row.get("first"))), float(bool(row.get("last"))),
              float(bool(row.get("insight"))),
              float(s.method_tier == "strong"), float(s.method_tier == "moderate"),
              float(s.method_tier == "weak")]
    return x


FEATURE_NAMES = ["ack_block", "staff", "aff:trace", "aff:regular", "aff:core", "llm",
                 "llm_missing", "wcm_first", "wcm_last", "insight", "method:strong",
                 "method:moderate", "method:weak"]


def _clip(p):
    import numpy as np
    return np.clip(p, 1e-6, 1 - 1e-6)


def metrics(y, p, w) -> dict:
    import numpy as np
    p = _clip(p)
    w = w / w.sum()
    ll = -(w * (y * np.log(p) + (1 - y) * np.log(1 - p))).sum()
    brier = (w * (p - y) ** 2).sum()
    return dict(logloss=ll, brier=brier, ece=ece(y, p, w))


def ece(y, p, w, bins=(0, .1, .3, .4, .65, .9, 1.0001)) -> float:
    """Weighted expected calibration error over fixed bins that include the decision
    cuts (0.30 triage, 0.40 SPS floor, 0.65 confirm), so the table reads at the edges
    production acts on."""
    import numpy as np
    tot = 0.0
    for lo, hi in zip(bins, bins[1:]):
        m = (p >= lo) & (p < hi)
        if m.any():
            tot += w[m].sum() / w.sum() * abs((w[m] * y[m]).sum() / w[m].sum()
                                              - (w[m] * p[m]).sum() / w[m].sum())
    return tot


def reliability(y, p, w, bins=(0, .1, .3, .4, .65, .9, 1.0001)) -> list:
    import numpy as np
    out = []
    for lo, hi in zip(bins, bins[1:]):
        m = (p >= lo) & (p < hi)
        if m.any():
            out.append((f"[{lo:.2f},{min(hi, 1):.2f})", int(m.sum()), int(y[m].sum()),
                        (w[m] * p[m]).sum() / w[m].sum(), (w[m] * y[m]).sum() / w[m].sum()))
    return out


def _expit(v):
    from scipy.special import expit
    return expit(v)


def _lr(C_=1.0):
    from sklearn.linear_model import LogisticRegression
    return LogisticRegression(C=C_, max_iter=5000)


def fit_platt(z, y, w):
    """(intercept, slope) of logistic(a + b z); effectively unregularised."""
    m = _lr(1e6).fit(z.reshape(-1, 1), y, sample_weight=w)
    return float(m.intercept_[0]), float(m.coef_[0][0])


def fit_offset(z, y, w) -> float:
    """Intercept-only recalibration (slope fixed at 1): the logit shift that best fits,
    i.e. what a per-core PRIOR would be. Weighted Newton steps on one parameter."""
    import numpy as np
    a = 0.0
    for _ in range(100):
        p = _expit(a + z)
        g = (w * (p - y)).sum()
        h = (w * p * (1 - p)).sum()
        if h <= 0:
            break
        step = g / h
        a -= np.clip(step, -5, 5)
        if abs(step) < 1e-9:
            break
    return float(a)


def fit_joint(X, y, w, grid=(0.01, 0.03, 0.1, 0.3, 1.0, 3.0, 10.0)):
    """L2 logistic on standardised X; C chosen by an inner 5-fold weighted log-loss on the
    TRAINING rows only (3-fold when a class has < 10 rows)."""
    import numpy as np
    from sklearn.model_selection import StratifiedKFold
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    k = 5 if min(y.sum(), len(y) - y.sum()) >= 10 else 3
    best, bestC = None, grid[0]
    for c in grid:
        loss = 0.0
        for tr, te in StratifiedKFold(k, shuffle=True, random_state=0).split(X, y):
            m = make_pipeline(StandardScaler(), _lr(c))
            m.fit(X[tr], y[tr], logisticregression__sample_weight=w[tr])
            p = _clip(m.predict_proba(X[te])[:, 1])
            loss += -(w[te] * (y[te] * np.log(p) + (1 - y[te]) * np.log(1 - p))).sum()
        if best is None or loss < best:
            best, bestC = loss, c
    m = make_pipeline(StandardScaler(), _lr(bestC))
    m.fit(X, y, logisticregression__sample_weight=w)
    return m, bestC


def loo(rows, w):
    """Held-out P for (1)-(4), leave-one-out; plus the chosen C per fold."""
    import numpy as np
    y = np.array([r["y"] for r in rows])
    z = np.array([logit_of(_sig(r["sig"])) for r in rows])
    X3 = np.array([features(r, False) for r in rows])
    X4 = np.array([features(r, True) for r in rows])
    keep3 = X3.std(0) > 0
    keep4 = X4.std(0) > 0
    out = {k: np.zeros(len(rows)) for k in ("score", "offset", "platt", "joint", "joint+")}
    out["score"] = _expit(z)
    cs = collections.Counter()
    for i in range(len(rows)):
        tr = np.arange(len(rows)) != i
        a, b = fit_platt(z[tr], y[tr], w[tr])
        out["platt"][i] = _expit(a + b * z[i])
        out["offset"][i] = _expit(fit_offset(z[tr], y[tr], w[tr]) + z[i])
        for name, X, keep in (("joint", X3, keep3), ("joint+", X4, keep4)):
            m, c = fit_joint(X[tr][:, keep], y[tr], w[tr])
            out[name][i] = m.predict_proba(X[i:i + 1, keep])[0, 1]
            cs[(name, c)] += 1
    return y, z, out, cs, (X3, keep3), (X4, keep4)


PAIRS = (("joint", "platt"), ("joint+", "joint"), ("platt", "offset"))


def boot_ci(y, preds, w, base="score", n=2000, seed=0):
    """Paired bootstrap (rows resampled with their weights) of each metric and of each
    model's difference from `base`."""
    import numpy as np
    rng = np.random.default_rng(seed)
    res = collections.defaultdict(list)
    for _ in range(n):
        i = rng.integers(0, len(y), len(y))
        if y[i].min() == y[i].max():
            continue
        mb = {k: metrics(y[i], p[i], w[i]) for k, p in preds.items()}
        for k in preds:
            for m in ("logloss", "brier", "ece"):
                res[(k, m)].append(mb[k][m])
                res[(k, m, "d")].append(mb[k][m] - mb[base][m])
        for a_, b_ in PAIRS:
            for m in ("logloss", "brier", "ece"):
                res[(a_, b_, m)].append(mb[a_][m] - mb[b_][m])
    return {k: (np.percentile(v, 2.5), np.percentile(v, 97.5)) for k, v in res.items()}


def report(label, rows, weighted: bool, out_lines: list):
    import numpy as np
    y0 = np.array([r["y"] for r in rows])
    if weighted:
        npos, nneg = y0.sum(), len(y0) - y0.sum()
        w = np.where(y0 == 1, PRIOR / npos, (1 - PRIOR) / nneg) * len(y0)
    else:
        w = np.ones(len(rows))
    y, z, preds, cs, (X3, k3), (X4, k4) = loo(rows, w)
    P = lambda *a: out_lines.append(" ".join(str(x) for x in a))  # noqa: E731
    P(f"\n##### {label}: n={len(y)} pos={int(y.sum())} neg={int(len(y) - y.sum())}"
      + (f"  (weighted to prevalence {PRIOR})" if weighted else "  (unweighted)"))
    a, b = fit_platt(z, y, w)
    P(f"  intercept-only shift on all rows: {fit_offset(z, y, w):+.3f}")
    rng = np.random.default_rng(1)
    bs, a_s = [], []
    for _ in range(2000):
        i = rng.integers(0, len(y), len(y))
        if y[i].min() != y[i].max():
            ai, bi = fit_platt(z[i], y[i], w[i])
            bs.append(bi)
            a_s.append(ai)
    P(f"  Platt on all rows: intercept {a:+.3f} [{np.percentile(a_s, 2.5):+.3f},"
      f"{np.percentile(a_s, 97.5):+.3f}]  slope {b:.3f} [{np.percentile(bs, 2.5):.3f},"
      f"{np.percentile(bs, 97.5):.3f}]   (slope < 1 = overconfident)")
    if weighted:
        au, bu = fit_platt(z, y, np.ones(len(y)))
        P(f"  Platt slope unweighted (prevalence-invariant check): {bu:.3f}")
    P(f"  joint-model C chosen per LOO fold: {dict(cs)}")
    ci = boot_ci(y, preds, w)
    P(f"  {'model':<8}{'logloss':>9}{'  95% CI':<18}{'d vs score':>11}{'  95% CI':<20}"
      f"{'brier':>8}{'d brier CI':>22}{'ECE':>8}")
    for k, p in preds.items():
        m = metrics(y, p, w)
        d = m["logloss"] - metrics(y, preds["score"], w)["logloss"]
        db = m["brier"] - metrics(y, preds["score"], w)["brier"]
        lo, hi = ci[(k, "logloss")]
        dlo, dhi = ci[(k, "logloss", "d")]
        blo, bhi = ci[(k, "brier", "d")]
        P(f"  {k:<8}{m['logloss']:>9.4f}  [{lo:.4f},{hi:.4f}] {d:>+10.4f}  [{dlo:+.4f},{dhi:+.4f}]"
          f"{m['brier']:>9.4f} {db:+.4f} [{blo:+.4f},{bhi:+.4f}]{m['ece']:>8.4f}")
    for a_, b_ in PAIRS:
        d = metrics(y, preds[a_], w)["logloss"] - metrics(y, preds[b_], w)["logloss"]
        lo, hi = ci[(a_, b_, "logloss")]
        P(f"  logloss {a_} - {b_}: {d:+.4f} [{lo:+.4f},{hi:+.4f}]")
    try:
        from sklearn.metrics import roc_auc_score
        P("  AUC (held-out): " + "  ".join(f"{k} {roc_auc_score(y, p, sample_weight=w):.4f}"
                                          for k, p in preds.items()))
    except ValueError:
        pass
    P("  reliability (bin, n, positives, mean predicted, observed rate):")
    for k, p in preds.items():
        P(f"    {k}: " + "; ".join(f"{b_} n={n} pos={pos} pred={mp:.3f} obs={ob:.3f}"
                                    for b_, n, pos, mp, ob in reliability(y, p, w)))
    P("  decisions (held-out P): rows at/above each cut, claimed/rejected split")
    for k, p in preds.items():
        P(f"    {k}: " + "  ".join(f"{c}>={t:.2f}: {int((p >= t).sum())} "
                                    f"({int(((p >= t) & (y == 1)).sum())}+/"
                                    f"{int(((p >= t) & (y == 0)).sum())}-)"
                                    for c, t in CUTS.items()))
    # full-data joint coefficients, for reading only
    for name, X, keep in (("joint", X3, k3), ("joint+", X4, k4)):
        m, c = fit_joint(X[:, keep], y, w)
        coef = m.named_steps["logisticregression"].coef_[0] / m.named_steps["standardscaler"].scale_
        names = [n for n, kk in zip(FEATURE_NAMES, keep) if kk]
        P(f"  {name} (all rows, C={c}) per-unit coefs: "
          + ", ".join(f"{n} {v:+.2f}" for n, v in zip(names, coef)))
    return dict(y=y, z=z, preds=preds, w=w, platt=(a, b), ci=ci)


def status_of(p, held_p):
    st = ("confirmed" if p >= C.DEFAULT_CONFIRM_THRESHOLD else
          "candidate" if p >= C.DEFAULT_TRIAGE_THRESHOLD else "below_threshold")
    if st == "confirmed" and held_p < C.DEFAULT_CONFIRM_THRESHOLD:
        st = "candidate"
    return st


def status_moves(allrows, a, b, out_lines):
    """Every core-14 row re-scored on this branch's affinity (the stored logit with its
    aff:* term swapped, as measure_affinity_prior_strength.rebanded does), then banded
    with identity vs the Platt map (a, b). Claimed/rejected keep their human status;
    the count is what the ENGINE band would be."""
    import measure_affinity_prior_strength as M
    stored_w = M.PRE_REFIT
    moves = collections.Counter()
    vis = collections.Counter()
    for r in allrows:
        if r["likelihood"] <= 0:
            moves[("never engine-scored", "-", "-")] += 1
            continue
        lik = min(max(r["likelihood"], 1e-6), 1 - 1e-6)
        z = (math.log(lik / (1 - lik)) - stored_w.get(M.key(r["stored_affinity"]), 0.0)
             + C.WEIGHTS.get(M.key(r["affinity"]), 0.0))
        held = z
        if r["llm"]:
            held -= C.LLM_INTERCEPT + C.LLM_PER_POINT * min(r["llm"], C.LLM_MAX_FITTED)
        if r["coauthors"] and not r["ack"]:
            held -= C.WEIGHTS["staff"]
        sg = _expit
        s0 = status_of(sg(z), sg(held))
        s1 = status_of(sg(a + b * z), sg(a + b * held))
        moves[(r["status"], s0, s1)] += 1
        vis[(sg(z) >= 0.40, sg(a + b * z) >= 0.40)] += 1
    out_lines.append(f"\n  core-14 engine bands under Platt (a={a:+.3f}, b={b:.3f}) vs identity, "
                     f"all {len(allrows)} rows (stored status, band now, band calibrated):")
    for k, v in sorted(moves.items(), key=lambda kv: -kv[1]):
        out_lines.append(f"    {k}: {v}" + ("   <- moves" if k[1] != k[2] else ""))
    out_lines.append(f"    SPS display floor 0.40 (P>=0.40 now, calibrated): "
                     f"{ {f'{bool(k0)}->{bool(k1)}': v for (k0, k1), v in vis.items()} }")
    for i, name in ((1, "band now"), (2, "band calibrated")):
        tot = collections.Counter()
        for k, v in moves.items():
            tot[k[i]] += v
        out_lines.append(f"    {name}: {dict(tot)}")
    out_lines.append("  what the map does to single pieces of evidence (P now -> calibrated):")
    for label, s in (
            ("distinctive alias + home inst", SignalResult(ack_matched=True, ack_alias_hits=20,
                                                           ack_institution="home")),
            ("GENERIC alias + home inst", SignalResult(ack_matched=True, ack_alias_hits=5000,
                                                       ack_institution="home")),
            ("staff alone", SignalResult(coauthor_cwids=["x"])),
            ("llm 9 alone", SignalResult(llm_score=9)),
            ("llm 8 + aff:regular", SignalResult(llm_score=8, author_affinity=0.2)),
            ("aff:regular alone", SignalResult(author_affinity=0.2)),
            ("nothing (prior)", SignalResult())):
        z = logit_of(s)
        out_lines.append(f"    {label:<32} {_expit(z):.3f} -> {_expit(a + b * z):.3f}")


def cmd_analyze(args) -> int:
    d = json.loads(Path(args.inputs).read_text())
    rows = d["panel"]
    out: list = []
    pb = [r for r in rows if r["panel"] == "B"]
    pb_easy = [r for r in rows if r["panel"] in ("B", "B_easy")]
    c14 = [r for r in rows if r["panel"] == "14"]
    c14e = [r for r in c14 if r["engine_scored"]]
    c14w = [r for r in rows if r["panel"] in ("14", "14_weak")]
    out.append("panel sizes: " + str(dict(collections.Counter(r["panel"] for r in rows))))
    report("PANEL B (core 2): labelled yes vs random-corpus negatives with a live LLM score",
           pb, True, out)
    report("PANEL B + easy 'no' labels as extra negatives (foil)", pb_easy, True, out)
    # 133 of the 137 "yes" papers name the imaging core in their full text, so with `ack`
    # in, panel B is close to separable and says little about the byline/LLM evidence it
    # was actually used to price. The same panel with the ack evidence blanked out is the
    # test of THAT evidence (staff, aff:*, llm) and of what summing it does.
    report("PANEL B, ack evidence removed (staff + aff:* + llm only)",
           [dict(r, sig=dict(r["sig"], ack_matched=False, ack_alias="", ack_alias_hits=None,
                             ack_institution="", ack_section=""), insight=False)
            for r in pb], True, out)
    r14 = report("CORE 14: claimed vs rejected", c14, False, out)
    report("CORE 14, in-corpus rows only (the rows production can score)",
           [r for r in c14 if r["in_corpus"]], False, out)
    report("CORE 14, engine-scored rows only (rows the queue actually showed)", c14e, False, out)
    report("CORE 14 + WEAK positives (engine-confirmed rows with ack or staff)", c14w, False, out)
    status_moves(d["core14_all"], *r14["platt"], out)
    text = "\n".join(out)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--out", required=True)
    c.add_argument("--llm-cache", required=True,
                   help="{pmid: {score}} live signals.llm_triage scores for random corpus "
                        "papers: panel B's negatives")
    c.add_argument("--c14-llm-cache", required=True,
                   help="{pmid: {score}} for core-14 rows with no stored llm_score")
    c.add_argument("--live-llm", action="store_true",
                   help="triage core-14 rows missing from --c14-llm-cache through Bedrock")
    c.add_argument("--cache-dir", required=True, help="local full-text + PubMed cache")
    a = sub.add_parser("analyze")
    a.add_argument("--inputs", required=True)
    a.add_argument("--out")
    args = ap.parse_args(argv)
    return cmd_collect(args) if args.cmd == "collect" else cmd_analyze(args)


if __name__ == "__main__":
    raise SystemExit(main())

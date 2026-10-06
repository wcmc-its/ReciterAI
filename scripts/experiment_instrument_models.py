#!/usr/bin/env python3
"""EXPERIMENT: is a SPECIFIC INSTRUMENT MODEL named in a paper's full text evidence that
the paper used the WCM core that owns that instrument?

The question behind it: every byline-based signal (staff, aff:*) is entangled with the
repeat-user loop, and every title/abstract signal (TF-IDF, journal subfield, mesh:tree,
method:*) turned out to be a second copy of the LLM. An instrument model string lives in
the METHODS of the full text, which the LLM never reads, and it is a property of the
paper rather than of its authors. If "Siemens Inveon" or "FACSymphony S6" names a scanner
or sorter only one place on campus owns, it might separate "used OUR scanner" from
"did imaging" without touching the byline.

Pipeline (all READ-ONLY):
  1. INSTRUMENTS below: per core, model strings taken from the core's own public web
     pages (fetched 2026-10-06) and its dictionary llm_description. Each carries a
     PMC phrase (what esearch is asked) and a regex (what must actually appear in the
     full text, so a tokenised esearch hit that is not the model is dropped).
  2. SPECIFICITY: global PMC hit count per phrase (like alias_hits), plus the count
     restricted to papers that name Weill Cornell anywhere (affiliations included).
  3. FIRING SET: the WCM-restricted PMC ids -> PMIDs -> gated to the scoreable corpus
     (analysis_summary_article, Academic Article, 2020+), then confirmed by the regex in
     the cached PMC XML.
  4. LABELS per (paper, core):
       gold  : DynamoDB human `claimed` / `rejected`; panel B yes/no (core 2 only)
       silver: DynamoDB engine `confirmed`; the core's alias named in the full text
               beside a HOME institution (signals.acknowledgement_signal)
     and for each fired paper: the existing row's status + stored LLM score, staff on
     byline, and author affinity RECOMPUTED with the paper's own confirmation removed
     from its numerator (the self-confirmation loop the task is about).
  5. CONTEXT READ: the ~300 chars around each model mention, classified as naming
     WCM / the core, naming another institution, or neither; plus a sample printed for
     a human read.
  6. PANEL B (core 2 only): leave-one-out AUC lift of `baseline_logit + w*feature`
     over the full combine() logit with the stored LLM score, same form as
     scripts/experiment_tfidf_similarity.py.

Nothing is written anywhere except --out (JSON) and the local full-text cache dir.

    python3 scripts/experiment_instrument_models.py --out /tmp/inst.json
    python3 scripts/experiment_instrument_models.py --from-json /tmp/inst.json   # re-report
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

GROUND_TRUTH = Path("/Users/paulalbert/Dropbox/Projects/Inferring Cores and Services/analysis")
LABELS = GROUND_TRUTH / "labeled_set.csv"
LLM_SCORES = GROUND_TRUTH / "calibration_llm_results.json"
CONFIRMED = GROUND_TRUTH / "confirmed_pmids_by_core.json"
WCM_CLAUSE = '("Weill Cornell" OR "Weill Medical College")'

# (core_id, label, PMC phrase, full-text regex). Sources: the core's public pages,
# fetched 2026-10-06 (cbic.weill.cornell.edu/facilities/*, research.weill.cornell.edu
# core pages for NMR/flow/microscopy/HIMC/ABAC, mpc.weill.cornell.edu) and the
# dictionary llm_description. Generic, campus-wide platforms (NovaSeq 6000, Q Exactive,
# LSM 880, IVIS Spectrum) are KEPT as controls: they show what a non-unique model buys.
INSTRUMENTS = [
    ("2", "Siemens Biograph Vision Quadra", "Biograph Vision Quadra", r"vision\s+quadra"),
    ("2", "Bruker BioSpec 70/30 (7T)", "BioSpec 70/30", r"biospec\s*70\s*/\s*30"),
    ("2", "Siemens Inveon PET/SPECT/CT", "Inveon", r"\binveon\b"),
    ("2", "VisualSonics Vevo 3100", "Vevo 3100", r"vevo\s*3100"),
    ("2", "ACSI TR-19 cyclotron", "TR-19 cyclotron", r"\btr[\s-]?19\b"),
    ("2", "Siemens Prisma Fit 3T", "Prisma Fit", r"prisma\s*fit"),
    ("2", "GE Discovery MR750 3T", "Discovery MR750", r"(discovery\s*)?mr\s?750\b"),
    ("2", "IVIS Spectrum (control)", "IVIS Spectrum", r"ivis\s*spectrum"),
    ("4", "BD FACSymphony S6", "FACSymphony S6", r"symphony\s*s6"),
    ("4", "BD Influx sorter", "BD Influx", r"\binflux\b.{0,40}(sort|cytomet)|bd\s+influx"),
    ("4", "Sony MA900", "Sony MA900", r"\bma900\b"),
    ("4", "BD FACSAria II SORP", "FACSAria II SORP", r"aria\s*ii\s*sorp"),
    ("4", "BD FACSymphony A5", "FACSymphony A5", r"symphony\s*a5"),
    ("4", "BD FACSCelesta", "FACSCelesta", r"facs\s*celesta"),
    ("4", "BD LSRFortessa (control)", "LSRFortessa", r"lsr\s*fortessa"),
    # Case-sensitive: "PromethION" is an Oxford Nanopore sequencer (5 of the first 30
    # sample-read hits were nanopore runs before this guard).
    ("7", "Promethion metabolic cages", "Promethion", r"(?-i:Promethion)(?!\s*(flow|sequenc|device|platform|R10|\(Oxford))"),
    ("7", "EchoMRI", "EchoMRI", r"echo\s?-?mri"),
    ("7", "FLIR T430sc", "FLIR T430sc", r"t430sc"),
    ("7", "Seahorse XFe24", "Seahorse XFe24", r"xfe\s?24"),
    ("9", "Bruker timsTOF Pro 2", "timsTOF Pro 2", r"timstof\s*pro\s*2"),
    ("13", "Bruker timsTOF Pro 2", "timsTOF Pro 2", r"timstof\s*pro\s*2"),
    ("13", "Thermo Orbitrap Fusion Lumos", "Orbitrap Fusion Lumos", r"fusion\s*lumos"),
    ("13", "Agilent RapidFire", "RapidFire", r"rapidfire"),
    ("13", "Thermo Q Exactive (control)", "Q Exactive", r"q[\s-]?exactive"),
    ("10", "Cytek Aurora", "Cytek Aurora", r"cytek\s*aurora|aurora\s*(spectral|cytometer|\(cytek)"),
    ("10", "Fluidigm Hyperion IMC", "Hyperion Imaging System", r"hyperion\s*(imaging|tissue|mass|\(fluidigm|system)|fluidigm\s*hyperion|standard\s+biotools\s+hyperion"),
    ("10", "Fluidigm Helios CyTOF", "Helios mass cytometer", r"helios\s*(cytof|mass\s*cytomet|\(fluidigm)|cytof\s*helios|fluidigm\s*helios"),
    ("11", "Sartorius Incucyte SX5", "Incucyte SX5", r"incucyte\s*sx5"),
    ("11", "LaVision/Miltenyi UltraMicroscope II", "Ultramicroscope II", r"ultra\s?microscope\s*ii\b"),
    ("11", "Leica Stellaris 8", "Stellaris 8", r"stellaris\s*8"),
    ("11", "Zeiss AxioScan 7", "Axioscan 7", r"axio\s?scan\s*7"),
    ("11", "Zeiss Axio Observer 7", "Axio Observer 7", r"axio\s?observer\s*7"),
    ("11", "Zeiss LSM 880 (control)", "LSM 880", r"lsm\s*880"),
    ("12", "Bruker Avance III HD 600", "Avance III HD 600", r"avance\s*iii\s*hd\s*600|600\s*mhz.{0,40}avance\s*iii\s*hd"),
    ("12", "Bruker Avance III HD 500", "Avance III HD 500", r"avance\s*iii\s*hd\s*500|500\s*mhz.{0,40}avance\s*iii\s*hd"),
    ("12", "Agilent/Varian Inova 600", "Inova 600", r"inova\s*600|600\s*mhz.{0,30}inova"),
    ("1", "10x Xenium", "Xenium", r"\bxenium\b"),
    ("1", "10x Visium HD", "Visium HD", r"visium\s*hd"),
    ("1", "NanoString CosMx", "CosMx", r"\bcosmx\b"),
    ("5", "Illumina NovaSeq 6000 (control)", "NovaSeq 6000", r"novaseq\s*6000"),
]

_WCM = re.compile(r"weill|cornell|citigroup|\bwcm\b|newyork[\s-]*presbyterian|"
                  r"\bnyp\b|meyer cancer center|belfer", re.I)
_OTHER = re.compile(r"memorial sloan|\bmskcc\b|\bmsk\b|rockefeller|columbia|\bnyu\b|"
                    r"new york university|mount sinai|icahn|harvard|stanford|yale|"
                    r"university of [a-z]+|universit[eéa]|institut|hospital(?! for special)|"
                    r"\bnih\b|national institutes", re.I)
_CORE_WORD = re.compile(r"\bcore\b|facility|shared resource|imaging center", re.I)


# ---------------------------------------------------------------------------
# evaluation primitives (same forms as experiment_tfidf_similarity.py)
# ---------------------------------------------------------------------------
def auc(y, s) -> float:
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
    """w in logit = a + offset + w*feat, offset's coefficient FIXED at 1; a dropped."""
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
    y, base, feat = (np.asarray(v, dtype=float) for v in (y, base, feat))
    s1 = np.empty(len(y))
    for i in range(len(y)):
        m = np.arange(len(y)) != i
        s1[i] = base[i] + fit_w(base[m], feat[m], y[m]) * feat[i]
    diffs = []
    for _ in range(reps):
        idx = rng.integers(0, len(y), len(y))
        if 0 < y[idx].sum() < len(idx):
            diffs.append(auc(y[idx], s1[idx]) - auc(y[idx], base[idx]))
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    return {"n": len(y), "pos": int(y.sum()), "auc_base": auc(y, base), "auc_with": auc(y, s1),
            "lift": auc(y, s1) - auc(y, base), "ci95": [float(lo), float(hi)],
            "w_full": fit_w(base, feat, y)}


def wilson(k: int, n: int, z: float = 1.96):
    if not n:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def esearch_term(term: str, get_json, url: str) -> list:
    """Every PMC id for an arbitrary esearch term (pmc_search.esearch_pmc quotes its
    argument as one phrase, so a phrase AND an affiliation clause needs this)."""
    ids: list = []
    while True:
        res = get_json(url, {"db": "pmc", "term": term, "retmode": "json", "retmax": 500,
                             "retstart": len(ids)}, 45).get("esearchresult", {})
        page = list(res.get("idlist", []))
        ids += page
        if not page or len(ids) >= int(res.get("count") or 0) or len(ids) >= 9999:
            break
    return ids


# ---------------------------------------------------------------------------
def collect(args) -> dict:
    from pipeline_cores import combine, ingest, signals  # noqa: F401
    from pipeline_cores.dictionary import load_cores
    from pipeline_cores.fulltext import PmcFullTextClient, to_plain_text
    from pipeline_cores.models import SignalResult
    from pipeline_cores.pmc_search import ESEARCH, _get_json, esearch_count, esearch_pmc, pmcids_to_pmids
    from utils.db import get_engine

    engine = get_engine()
    cores = {c.core_id: c for c in load_cores()}
    rows = json.loads(Path(args.rows).read_text())
    row = {(r["pmid"], r["core_id"]): r for r in rows}

    # --- 1-3: specificity + firing sets
    phrases = sorted({p for _, _, p, _ in INSTRUMENTS})
    spec, fired_by_phrase = {}, {}
    for ph in phrases:
        g = esearch_count(ph)
        w = int(_get_json(ESEARCH, {"db": "pmc", "term": f'"{ph}" AND {WCM_CLAUSE}',
                                    "retmode": "json", "retmax": 0}, 45)["esearchresult"]["count"])
        pm_w = pmcids_to_pmids(esearch_term(f'"{ph}" AND {WCM_CLAUSE}', _get_json, ESEARCH))
        spec[ph] = {"global": g, "wcm": w}
        fired_by_phrase[ph] = ingest.filter_corpus_pmids(engine, pm_w)
        # Coverage check: is the WCM clause dropping corpus papers? For phrases small
        # enough to pull in full, compare against the unrestricted set.
        if g is not None and g <= args.coverage_max:
            full = ingest.filter_corpus_pmids(engine, pmcids_to_pmids(esearch_pmc(ph)))
            spec[ph]["corpus_unrestricted"] = len(full)
            spec[ph]["corpus_wcm_clause"] = len(fired_by_phrase[ph])
            fired_by_phrase[ph] |= full
        print(f"  {ph:28s} global={g} wcm={w} corpus={len(fired_by_phrase[ph])}", flush=True)

    # --- 4: labels
    labels = {r["pmid"]: (1 if r["label"] == "yes" else 0) for r in csv.DictReader(LABELS.open())}
    stored_llm = json.loads(LLM_SCORES.read_text())
    all_fired = sorted(set().union(*fired_by_phrase.values()))
    # every paper we will read: fired ones + panel B (for the lift test)
    to_read = sorted(set(all_fired) | set(labels))
    client = PmcFullTextClient(cache_dir=Path(args.cache))
    texts = {}
    for i, p in enumerate(to_read):
        xml = client.get_xml(p)
        texts[p] = (xml, to_plain_text(xml) if xml else "")
        if i % 200 == 0:
            print(f"  full text {i}/{len(to_read)}", flush=True)

    # self-excluded affinity: counts of confirmed/claimed in-corpus papers per (cwid, core)
    users = collections.defaultdict(set)
    for r in rows:
        if r.get("status") in ("confirmed", "claimed"):
            users[r["core_id"]].add(r["pmid"])
    core2_confirms = {str(p) for p in json.loads(CONFIRMED.read_text())["2"]}
    users["2"] |= core2_confirms
    corpus_users = {c: ingest.filter_corpus_pmids(engine, ps) for c, ps in users.items()}
    by_paper = ingest.fetch_author_bylines(engine, sorted(set().union(*corpus_users.values())
                                                         | set(all_fired) | set(labels)))
    cnt = collections.defaultdict(collections.Counter)
    for c, ps in corpus_users.items():
        for p in ps:
            for a in by_paper.get(p, []):
                cnt[c][a] += 1
    totals = ingest.fetch_author_totals(engine, sorted({a for c in cnt for a in cnt[c]}))

    def aff_excl(p, c):
        """MAX over the byline of (author's confirmed core-c papers EXCLUDING p) /
        (author's corpus total) -- the paper never feeds its own numerator."""
        best = 0.0
        own = 1 if p in corpus_users.get(c, ()) else 0
        for a in by_paper.get(p, []):
            k, t = cnt[c].get(a, 0) - own, totals.get(a, 0)
            if k > 0 and t > 0:
                best = max(best, k / t)
        return best

    staff = {c: {s.cwid for s in cores[c].staff} for c in cores}
    papers = []
    for cid, lab, ph, rx in INSTRUMENTS:
        pat = re.compile(rx, re.I)
        for p in sorted(fired_by_phrase[ph]):
            xml, txt = texts.get(p, ("", ""))
            m = pat.search(txt)
            if not m:
                papers.append({"core": cid, "inst": lab, "pmid": p, "regex": False})
                continue
            win = txt[max(0, m.start() - 300): m.end() + 300]
            ack = signals.acknowledgement_signal(txt, cores[cid], xml=xml)
            r = row.get((p, cid), {})
            papers.append({
                "core": cid, "inst": lab, "pmid": p, "regex": True,
                "snippet": " ".join(win.split()),
                "ctx_wcm": bool(_WCM.search(win)), "ctx_other": bool(_OTHER.search(win)),
                "ctx_core": bool(_CORE_WORD.search(win)),
                "ack": bool(ack.ack_matched), "ack_inst": ack.ack_institution, "ack_alias": ack.ack_alias,
                "status": r.get("status", "none"),
                "llm": float(r["llm_score"]) if r.get("llm_score") is not None else None,
                "staff": bool(staff[cid] & set(by_paper.get(p, []))),
                "aff_excl": aff_excl(p, cid),
                "panelB": labels.get(p) if cid == "2" else None,
                "method_tier": r.get("method_tier"),
            })

    # --- 6: panel B features + baseline (core 2), same SignalResult build as the fit
    core2 = cores["2"]
    pb_byl = ingest.fetch_author_bylines(engine, sorted(labels))
    outside = sorted(ingest.filter_corpus_pmids(engine, [p for p in core2_confirms if p not in labels]))
    cnt2: dict = collections.defaultdict(lambda: collections.defaultdict(int))
    for cw in ingest.fetch_author_bylines(engine, outside).values():
        for c in cw:
            cnt2[c]["2"] += 1
    index = signals.build_affinity_index(cnt2, ingest.fetch_author_totals(engine, list(cnt2)))
    coauth = signals.coauthorship_index(engine, core2, sorted(labels))
    pb = []
    for p, y in labels.items():
        xml, txt = texts.get(p, ("", ""))
        a = signals.acknowledgement_signal(txt, core2, xml=xml) if txt else SignalResult()
        sig = SignalResult(ack_matched=a.ack_matched, ack_alias=a.ack_alias,
                           ack_alias_hits=a.ack_alias_hits, ack_institution=a.ack_institution,
                           ack_section=a.ack_section, coauthor_cwids=coauth.get(p, []),
                           llm_score=stored_llm.get(p, {}).get("score"),
                           author_affinity=signals.author_affinity(index, pb_byl.get(p, []), "2"))
        pr = combine.score(sig)
        nolack = combine.score(SignalResult(coauthor_cwids=sig.coauthor_cwids,
                                            author_affinity=sig.author_affinity,
                                            llm_score=sig.llm_score))
        feats = {lab: bool(re.search(rx, txt, re.I)) for c, lab, _, rx in INSTRUMENTS if c == "2"}
        pb.append({"pmid": p, "y": y, "has_text": bool(txt), "base": math.log(pr / (1 - pr)),
                   "base_noack": math.log(nolack / (1 - nolack)),
                   "llm": sig.llm_score, "aff": sig.author_affinity, "feats": feats})
    return {"spec": spec, "papers": papers, "panelB": pb}


# ---------------------------------------------------------------------------
def report(d: dict, args) -> None:
    rng = np.random.default_rng(7)
    spec, papers, pb = d["spec"], d["papers"], d["panelB"]
    print("\n== SPECIFICITY + FIRING (corpus = Academic Article 2020+, PMC full text) ==")
    print(f"{'core':>4} {'instrument':38s} {'globalPMC':>9} {'wcmPMC':>6} {'fired':>5} {'regex':>5}")
    ph_of = {(c, l): p for c, l, p, _ in INSTRUMENTS}
    by = collections.defaultdict(list)
    for x in papers:
        by[(x["core"], x["inst"])].append(x)
    for c, l, ph, _ in INSTRUMENTS:
        xs = by[(c, l)]
        print(f"{c:>4} {l:38s} {spec[ph]['global']!s:>9} {spec[ph]['wcm']:>6} {len(xs):>5} "
              f"{sum(x['regex'] for x in xs):>5}")
    cov = [(ph, s) for ph, s in spec.items() if "corpus_unrestricted" in s]
    u = sum(s["corpus_unrestricted"] for _, s in cov)
    w = sum(s["corpus_wcm_clause"] for _, s in cov)
    print(f"coverage of the WCM clause (phrases pulled in full): {w}/{u} corpus papers "
          f"the unrestricted search found were also found WITH the clause")

    print("\n== PER INSTRUMENT: what is already known about the papers it fires on ==")
    hdr = ("core instrument                               n  gold+ gold-  conf  ackHome  "
           "cand(llm>=6) cand(llm<6) noRow  ctxWCM ctxOther  INCR  INCR_ctxWCM")
    print(hdr)
    agg = collections.Counter()
    incr_rows = []
    for c, l, _, _ in INSTRUMENTS:
        xs = [x for x in by[(c, l)] if x["regex"]]
        g_pos = sum(1 for x in xs if x["status"] == "claimed" or x["panelB"] == 1)
        g_neg = sum(1 for x in xs if x["status"] == "rejected" or x["panelB"] == 0)
        conf = sum(1 for x in xs if x["status"] == "confirmed")
        ackh = sum(1 for x in xs if x["ack"] and x["ack_inst"] == "home")
        c_hi = sum(1 for x in xs if x["status"] == "candidate" and (x["llm"] or 0) >= 6)
        c_lo = sum(1 for x in xs if x["status"] in ("candidate", "below_threshold") and (x["llm"] or 0) < 6)
        none = sum(1 for x in xs if x["status"] == "none")
        cw = sum(x["ctx_wcm"] for x in xs)
        co = sum(x["ctx_other"] and not x["ctx_wcm"] for x in xs)
        # INCREMENTAL: not already confirmed/claimed/panel-yes, no home alias, no staff,
        # and not already carried by a high LLM score.
        inc = [x for x in xs if x["status"] not in ("confirmed", "claimed") and x["panelB"] != 1
               and not (x["ack"] and x["ack_inst"] == "home") and not x["staff"]
               and (x["llm"] or 0) < 6]
        incr_rows += inc
        print(f"{c:>4} {l:38s} {len(xs):3d} {g_pos:5d} {g_neg:5d} {conf:5d} {ackh:8d} "
              f"{c_hi:12d} {c_lo:11d} {none:5d} {cw:7d} {co:8d} {len(inc):5d} "
              f"{sum(x['ctx_wcm'] for x in inc):11d}")
        for k, v in (("n", len(xs)), ("gpos", g_pos), ("gneg", g_neg), ("conf", conf), ("ack", ackh),
                     ("inc", len(inc)), ("inc_wcm", sum(x["ctx_wcm"] for x in inc))):
            agg[k] += v
    print("TOTAL", dict(agg))

    # WITHIN-MODALITY CONTRAST. The outcome is "the paper names this core's alias beside
    # a HOME institution" -- a proxy for usage that is independent of the instrument
    # string. If a specific model does not raise that rate above a generic instrument of
    # the SAME modality (the control row), the model string is measuring "did flow /
    # did PET / did MS", which is topicality, not this core.
    print("\n== WITHIN-MODALITY CONTRAST: home-alias rate, specific model vs the modality control ==")
    from math import comb

    def fisher_greater(a, n1, c, n2):
        """one-sided P(X >= a), hypergeometric, a of n1 vs c of n2"""
        K, N = a + c, n1 + n2
        return sum(comb(K, k) * comb(N - K, n1 - k) for k in range(a, min(K, n1) + 1)) / comb(N, n1)

    controls = {c: l for c, l, _, _ in INSTRUMENTS if "control" in l}
    for c, l, _, _ in INSTRUMENTS:
        xs = [x for x in by[(c, l)] if x["regex"]]
        k = sum(1 for x in xs if x["ack"] and x["ack_inst"] == "home")
        lo, hi = wilson(k, len(xs))
        line = f"{c:>4} {l:38s} {k:3d}/{len(xs):<4d} {k/max(1,len(xs)):.3f} [{lo:.3f},{hi:.3f}]"
        ctl = controls.get(c)
        if ctl and ctl != l and len(xs):
            cx = [x for x in by[(c, ctl)] if x["regex"]]
            ck = sum(1 for x in cx if x["ack"] and x["ack_inst"] == "home")
            line += f"   control {ck}/{len(cx)} = {ck/max(1,len(cx)):.3f}  one-sided Fisher p={fisher_greater(k, len(xs), ck, len(cx)):.3f}"
        print(line)

    # silver precision: of fired papers, share that carry ANY independent usage evidence
    print("\n== SILVER PRECISION per core (fired papers with known usage / fired papers) ==")
    for c in sorted({x["core"] for x in papers}, key=int):
        xs = [x for x in papers if x["core"] == c and x["regex"]]
        k = sum(1 for x in xs if x["status"] in ("confirmed", "claimed") or x["panelB"] == 1
                or (x["ack"] and x["ack_inst"] == "home"))
        lo, hi = wilson(k, len(xs))
        neg = sum(1 for x in xs if x["status"] == "rejected" or x["panelB"] == 0)
        print(f"  core {c:>2}: {k}/{len(xs)} = {k/max(1,len(xs)):.3f}  CI95 [{lo:.3f}, {hi:.3f}]   "
              f"gold negatives among fired: {neg}")

    print("\n== AFFINITY (self-excluded) among fired papers: does the signal reach non-repeat users? ==")
    for c in sorted({x["core"] for x in papers}, key=int):
        xs = [x for x in papers if x["core"] == c and x["regex"]]
        rep = sum(1 for x in xs if x["aff_excl"] > 0)
        print(f"  core {c:>2}: {rep}/{len(xs)} fired papers have a byline author with prior "
              f"confirmations (self excluded)")

    print("\n== PANEL B (core 2) ==")
    yes = [r for r in pb if r["y"] == 1]
    no = [r for r in pb if r["y"] == 0]
    print(f"panel: {len(yes)} yes / {len(no)} no; with PMC full text: "
          f"{sum(r['has_text'] for r in yes)} yes / {sum(r['has_text'] for r in no)} no")
    labs2 = [l for c, l, _, _ in INSTRUMENTS if c == "2"]
    for l in labs2:
        print(f"  {l:38s} yes={sum(r['feats'][l] for r in yes):3d} no={sum(r['feats'][l] for r in no):3d}")
    anyf = lambda r, ls: float(any(r["feats"][l] for l in ls))  # noqa: E731
    specific = [l for l in labs2 if "control" not in l]
    y = [r["y"] for r in pb]
    feat = [anyf(r, specific) for r in pb]
    print(f"  ANY core-2 model (excl. controls): yes={sum(f for f, r in zip(feat, pb) if r['y'])} "
          f"no={sum(f for f, r in zip(feat, pb) if not r['y'])}")
    for name, key in (("full combine() logit incl. ack + LLM", "base"),
                      ("staff + affinity + LLM (no ack)", "base_noack")):
        base = [r[key] for r in pb]
        L = lift(base, feat, y, rng)
        print(f"  over {name}: AUC {L['auc_base']:.4f} -> {L['auc_with']:.4f}  "
              f"lift {L['lift']:+.4f} CI95 [{L['ci95'][0]:+.4f}, {L['ci95'][1]:+.4f}]  w={L['w_full']:.2f}")
    llm = [r for r in pb if r["llm"] is not None]
    L = lift([r["llm"] for r in llm], [anyf(r, specific) for r in llm], [r["y"] for r in llm], rng)
    print(f"  over the LLM score alone (n={L['n']}): AUC {L['auc_base']:.4f} -> {L['auc_with']:.4f} "
          f"lift {L['lift']:+.4f} CI95 [{L['ci95'][0]:+.4f}, {L['ci95'][1]:+.4f}]")
    rep = [r for r in pb if r["aff"] > 0]
    print(f"  repeat-user subset (aff>0): {sum(r['y'] for r in rep)} yes / "
          f"{len(rep) - sum(r['y'] for r in rep)} no -> lift not estimable with <5 negatives"
          if len(rep) - sum(r['y'] for r in rep) < 5 else "")
    # Within the hard cell: labelled papers the LLM rates highly but that are NO
    hard = [r for r in pb if (r["llm"] or 0) >= 6]
    print(f"  LLM>=6 cell: {sum(r['y'] for r in hard)} yes / {len(hard)-sum(r['y'] for r in hard)} no; "
          f"model fires on {sum(anyf(r, specific) for r in hard if r['y'])} yes / "
          f"{sum(anyf(r, specific) for r in hard if not r['y'])} no there")

    print("\n== SAMPLE READ: incremental fired papers (not confirmed/claimed, no alias, no staff, llm<6) ==")
    random.Random(3).shuffle(incr_rows)
    for x in incr_rows[: args.sample]:
        tag = "WCM" if x["ctx_wcm"] else ("OTHER" if x["ctx_other"] else "-")
        print(f"- core {x['core']} {x['inst']} pmid {x['pmid']} status={x['status']} llm={x['llm']} "
              f"ctx={tag}\n    ...{x['snippet'][150:520]}...")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--rows", help="JSON dump of every CORE# DynamoDB row (read-only scan)")
    ap.add_argument("--cache", default=str(REPO / "out" / "fulltext_cache"))
    ap.add_argument("--coverage-max", type=int, default=2500,
                    help="also pull the unrestricted PMC set for phrases with <= this many hits")
    ap.add_argument("--out")
    ap.add_argument("--from-json")
    ap.add_argument("--sample", type=int, default=40)
    args = ap.parse_args(argv)
    if args.from_json:
        d = json.loads(Path(args.from_json).read_text())
    else:
        d = collect(args)
        if args.out:
            Path(args.out).write_text(json.dumps(d, default=list))
    report(d, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

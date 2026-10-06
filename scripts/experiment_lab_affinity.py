#!/usr/bin/env python3
"""EXPERIMENT: LAB-level core usage as a signal, instead of (or on top of) AUTHOR affinity.

Cores bill labs, and trainees rotate, so the last WCM author on a byline is a proxy
for the lab that paid. For a scored paper p with lab head h:

    lab_n    = core papers whose lab head is h, EXCLUDING p
    lab_N    = corpus papers whose lab head is h, EXCLUDING p
    lab_rate = lab_n / (lab_N + 1)
    lab_any  = lab_n > 0
    lab_win  = core papers of lab h published in [year(p) - 3, year(p) - 1]  (strictly
               before p; any year, not corpus-gated, because the window looks back past 2020)

"Lab head" = the LAST resolved (personIdentifier set) author on the byline who is not
the core's own staff (the #418 rule: staff lend no usage prior to their own core).
`analysis_summary_author_list.rank` gives the order. Corresponding author is not in
reciterdb, so it is not used. `senior` variants count only papers where h is the true
last author (rank == the paper's max rank), i.e. the PI really is the senior author.

The BASELINE always carries the author-affinity prior with the scored paper EXCLUDED
from its own numerator (and denominator): `aff_se`. A paper's own confirmation never
feeds the rate it is scored with. The fit-script affinity (confirms outside the whole
label set) is also reported, as `aff_fit`, for comparability with #421/#423.

Panels:
  B   — core 2 (Biomedical Imaging). analysis/labeled_set.csv yes (137) vs the fit's
        random-corpus draw (seed 11, 1,200), restricted to the 421 random papers that
        carry a live two-pass LLM score (#423's cache). Baseline = staff + aff_se + LLM.
  B14 — core 14 (Research Informatics) human decisions in DynamoDB: claimed vs rejected.
        Baseline = staff + aff_se + stored LLM score.

Lift = AUC of `base + w*feature` with w fitted leave-one-out (deployment form, the
baseline's coefficient fixed at 1), paired bootstrap CI95. Same machinery as #423.

READ-ONLY: SELECTs on reciterdb, one filtered DynamoDB Scan, local JSON caches.

    python3 scripts/experiment_lab_affinity.py --llm-cache <#423 llm_cache.json> --out rows.json
    python3 scripts/experiment_lab_affinity.py --from-json rows.json
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import math
import random
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
FULLTEXT_DIR = GROUND_TRUTH / "fulltext"
WINDOW = 3


# ---------------------------------------------------------------------------
# evaluation primitives (identical to scripts/experiment_tfidf_similarity.py, #423)
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
    a0, a1 = auc(y, base), auc(y, s1)
    diffs = []
    for _ in range(reps):
        idx = rng.integers(0, len(y), len(y))
        if 0 < y[idx].sum() < len(idx):
            diffs.append(auc(y[idx], s1[idx]) - auc(y[idx], base[idx]))
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    return {"n": len(y), "pos": int(y.sum()), "auc_base": a0, "auc_with": a1,
            "lift": a1 - a0, "ci95": (lo, hi), "w_full": fit_w(base, feat, y)}


def wilson(k: int, n: int, z: float = 1.96) -> tuple:
    if not n:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def logit(p: float) -> float:
    return math.log(p / (1 - p))


# ---------------------------------------------------------------------------
# reciterdb reads
# ---------------------------------------------------------------------------
def _chunks(xs, n=2000):
    xs = list(xs)
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def author_lists(engine, pmids) -> dict:
    """pmid -> (max rank over ALL authors, [(rank, cwid) resolved authors])."""
    from sqlalchemy import bindparam, text  # lazy
    stmt = text("SELECT pmid, `rank` AS rk, personIdentifier AS c FROM analysis_summary_author_list "
                "WHERE pmid IN :p").bindparams(bindparam("p", expanding=True))
    mx: dict = collections.defaultdict(int)
    res: dict = collections.defaultdict(list)
    with engine.connect() as conn:
        for ch in _chunks(pmids):
            for r in conn.execute(stmt, {"p": [int(x) for x in ch]}):
                p = str(r.pmid)
                mx[p] = max(mx[p], int(r.rk or 0))
                if r.c:
                    res[p].append((int(r.rk or 0), r.c))
    return {p: (mx[p], sorted(res.get(p, []))) for p in mx}


def pmids_of(engine, cwids) -> dict:
    from sqlalchemy import bindparam, text  # lazy
    stmt = text("SELECT DISTINCT pmid, personIdentifier AS c FROM analysis_summary_author_list "
                "WHERE personIdentifier IN :c").bindparams(bindparam("c", expanding=True))
    out: dict = collections.defaultdict(set)
    with engine.connect() as conn:
        for ch in _chunks(cwids, 1000):
            for r in conn.execute(stmt, {"c": list(ch)}):
                out[r.c].add(str(r.pmid))
    return out


def years(engine, pmids) -> dict:
    from sqlalchemy import bindparam, text  # lazy
    stmt = text("SELECT pmid, articleYear AS y FROM analysis_summary_article WHERE pmid IN :p"
                ).bindparams(bindparam("p", expanding=True))
    out = {}
    with engine.connect() as conn:
        for ch in _chunks(pmids):
            for r in conn.execute(stmt, {"p": [int(x) for x in ch]}):
                if r.y:
                    out[str(r.pmid)] = int(r.y)
    return out


def head_of(al, staff) -> tuple:
    """(lab head cwid, is_true_last_author) — the last resolved non-staff author."""
    if not al:
        return None, False
    mx, resolved = al
    cand = [(rk, c) for rk, c in resolved if c not in staff]
    if not cand:
        return None, False
    rk, c = max(cand)
    return c, rk == mx


class Labs:
    """Lab tables for a set of heads: which papers each head leads, and when."""

    def __init__(self, engine, heads, staff, corpus):
        self.staff, self.corpus = staff, corpus
        own = pmids_of(engine, heads)
        allp = set().union(*own.values()) if own else set()
        al = author_lists(engine, allp)
        self.year = years(engine, allp)
        self.led = collections.defaultdict(set)         # head -> papers they lead (any year)
        self.led_senior = collections.defaultdict(set)  # ... as the TRUE last author
        for p in allp:
            h, senior = head_of(al.get(p), staff)
            if h in heads:
                self.led[h].add(p)
                if senior:
                    self.led_senior[h].add(p)

    def features(self, p, h, year, core_gated, core_any, senior=False) -> dict:
        if h is None:
            return {"lab_n": 0, "lab_N": 0, "lab_rate": 0.0, "lab_any": 0, "lab_win": 0}
        led = (self.led_senior if senior else self.led)[h] - {p}
        L = led & self.corpus
        n = len(L & core_gated)
        win = 0
        if year:
            win = sum(1 for q in led & core_any
                      if q in self.year and year - WINDOW <= self.year[q] <= year - 1)
        return {"lab_n": n, "lab_N": len(L), "lab_rate": n / (len(L) + 1),
                "lab_any": int(n > 0), "lab_win": win}


def affinity_se(p, byline, core_gated, author_core, totals, staff, in_corpus) -> float:
    """MAX over the byline of (author's core papers - p) / (author's corpus papers - p)."""
    best = 0.0
    for c in byline:
        if c in staff:
            continue
        n = author_core.get(c, 0) - (1 if p in core_gated else 0)
        t = totals.get(c, 0) - (1 if in_corpus else 0)
        if n > 0 and t > 0:
            best = max(best, min(n / t, 1.0))
    return best


def affinity_with_self(byline, author_core, totals, staff) -> float:
    best = 0.0
    for c in byline:
        if c in staff:
            continue
        n, t = author_core.get(c, 0), totals.get(c, 0)
        if n > 0 and t > 0:
            best = max(best, min(n / t, 1.0))
    return best


# ---------------------------------------------------------------------------
def build_panel(engine, core_id, panel_pmids, ylab, llm, staff_hits, core_gated, core_any,
                corpus, extra_ack=None, aff_fit=None):
    core = load_core(core_id)
    staff = set(core.staff_cwids)
    bylines = ingest.fetch_author_bylines(engine, panel_pmids)
    al = author_lists(engine, panel_pmids)
    pyear = years(engine, panel_pmids)
    heads = {p: head_of(al.get(p), staff) for p in panel_pmids}

    # author-level numerator from the SAME core set, staff out (#418)
    core_bylines = ingest.fetch_author_bylines(engine, sorted(core_gated))
    author_core: dict = collections.Counter()
    for cw in core_bylines.values():
        for c in set(cw):
            if c not in staff:
                author_core[c] += 1
    authors = sorted({c for p in panel_pmids for c in bylines.get(p, [])})
    totals = ingest.fetch_author_totals(engine, authors)

    hs = {h for h, _ in heads.values() if h}
    labs = Labs(engine, hs, staff, corpus)
    rows = []
    for p in panel_pmids:
        h, senior = heads[p]
        bl = bylines.get(p, [])
        in_c = p in corpus
        a_se = affinity_se(p, bl, core_gated, author_core, totals, staff, in_c)
        a_self = affinity_with_self(bl, author_core, totals, staff)
        f = labs.features(p, h, pyear.get(p), core_gated, core_any)
        fs = labs.features(p, h if senior else None, pyear.get(p), core_gated, core_any, senior=True)
        # lab rate WITH the paper itself, to measure self-reference
        f_self_n = len(labs.led[h] & corpus & core_gated) if h else 0
        f_self_N = len(labs.led[h] & corpus) if h else 0
        top_total = max((totals.get(c, 0) for c in bl if c not in staff), default=0)
        ack = (extra_ack or {}).get(p, SignalResult())
        sig = SignalResult(ack_matched=ack.ack_matched, ack_alias=ack.ack_alias,
                           ack_alias_hits=ack.ack_alias_hits, ack_institution=ack.ack_institution,
                           ack_section=ack.ack_section, coauthor_cwids=staff_hits.get(p, []),
                           llm_score=llm.get(p), author_affinity=a_se)
        # `ack` exists only where full text was cached (the labelled side), so it is kept
        # OUT of `base`: on a yes-vs-random panel it would separate by data availability.
        base_ack = logit(combine.score(sig))
        sig = SignalResult(coauthor_cwids=staff_hits.get(p, []), llm_score=llm.get(p),
                           author_affinity=a_se)
        base = logit(combine.score(sig))
        sig.author_affinity = 0.0
        base_noaff = logit(combine.score(sig))
        row = {"pmid": p, "core": core_id, "y": ylab[p], "year": pyear.get(p), "in_corpus": in_c,
               "head": h, "head_is_last": senior, "llm": llm.get(p),
               "staff": bool(staff_hits.get(p)), "aff_se": a_se, "aff_self": a_self,
               "top_author_total": top_total, "base": base, "base_ack": base_ack, "base_noaff": base_noaff,
               "lab_rate_self": f_self_n / (f_self_N + 1) if h else 0.0, "in_core": p in core_gated,
               **f, **{f"sen_{k}": v for k, v in fs.items()}}
        if aff_fit is not None:
            sig.author_affinity = aff_fit.get(p, 0.0)
            row["base_fit"] = logit(combine.score(sig))
        rows.append(row)
    print(f"core {core_id}: panel {len(panel_pmids)} ({sum(ylab.values())} yes); "
          f"{sum(1 for h, _ in heads.values() if h)} have a non-staff lab head "
          f"({sum(1 for h, s in heads.values() if h and s)} as true last author); "
          f"{len(hs)} distinct heads; core set {len(core_gated)} in-corpus / {len(core_any)} any-year")
    return rows


def build(args):
    from pipeline_cores.persist import scan_prior_core_usage  # lazy
    from utils.db import get_engine  # lazy

    engine = get_engine()
    corpus_rows = ingest.fetch_publications(engine)
    corpus = {r["pmid"] for r in corpus_rows}
    print(f"corpus: {len(corpus)} pmids")

    # ---------------- panel B (core 2) ----------------
    labels = {r["pmid"]: (1 if r["label"] == "yes" else 0) for r in csv.DictReader(LABELS.open())}
    stored = json.loads(LLM_SCORES.read_text())
    confirms2 = {str(p) for p in json.loads(CONFIRMED.read_text())["2"]}
    ddb2 = {r["pmid"] for r in scan_prior_core_usage("2", strict=True)}
    core_any2 = confirms2 | ddb2
    core_gated2 = ingest.filter_corpus_pmids(engine, core_any2)
    random.seed(args.seed)
    drawn = random.sample([r["pmid"] for r in corpus_rows], args.negatives)  # the fit's draw
    hard = [p for p in drawn if p not in core_any2 and p not in labels]
    live = json.loads(Path(args.llm_cache).read_text()) if args.llm_cache else {}
    neg = [p for p in hard if p in live]
    yes = [p for p, v in labels.items() if v == 1]
    no = [p for p, v in labels.items() if v == 0]
    panel = yes + no + neg
    ylab = {p: labels.get(p, 0) for p in panel}
    llm = {p: stored.get(p, {}).get("score") for p in labels} | {p: live[p].get("score") for p in neg}
    print(f"core 2: {len(confirms2)} signal-2 confirms, {len(ddb2)} DynamoDB confirmed/claimed; "
          f"{len(hard)} random hard negatives, {len(neg)} with a live LLM score")
    core2 = load_core("2")
    staff_hits = signals.coauthorship_index(engine, core2, panel)

    def ack(p):
        f = FULLTEXT_DIR / f"{p}.xml"
        if not f.exists():
            return SignalResult()
        x = f.read_text(errors="ignore")
        return signals.acknowledgement_signal(to_plain_text(x), core2, xml=x)
    acks = {p: ack(p) for p in labels}
    # fit-script affinity: confirms outside the WHOLE label set (#421/#423 baseline)
    outside = sorted(ingest.filter_corpus_pmids(engine, [p for p in confirms2 if p not in labels]))
    cnt: dict = collections.defaultdict(lambda: collections.defaultdict(int))
    for cw in ingest.fetch_author_bylines(engine, outside).values():
        for c in cw:
            if c not in set(core2.staff_cwids):
                cnt[c]["2"] += 1
    idx = signals.build_affinity_index(cnt, ingest.fetch_author_totals(engine, list(cnt)))
    bl = ingest.fetch_author_bylines(engine, panel)
    aff_fit = {p: signals.author_affinity(idx, bl.get(p, []), "2") for p in panel}
    rows = build_panel(engine, "2", panel, ylab, llm, staff_hits, core_gated2, core_any2, corpus,
                       extra_ack=acks, aff_fit=aff_fit)
    for r in rows:
        r["panel"] = "label" if r["pmid"] in labels else "random"

    # ---------------- panel B14 (core 14 human decisions) ----------------
    ddb = json.loads(Path(args.ddb_rows).read_text())
    c14 = [d for d in ddb if d["core_id"] == "14"]
    human = {d["pmid"]: d for d in c14 if d["status"] in ("claimed", "rejected")}
    # core set variants: ALL confirmed+claimed (what production feeds affinity), and STRICT
    # (claimed + confirmed with ack or staff evidence: no LLM/affinity-only confirmations)
    any_all = {d["pmid"] for d in c14 if d["status"] in ("claimed", "confirmed")}
    any_strict = {d["pmid"] for d in c14 if d["status"] == "claimed"
                  or (d["status"] == "confirmed" and (d["ack"] or d["staff"]))}
    y14 = {p: int(d["status"] == "claimed") for p, d in human.items()}
    llm14 = {p: d["llm"] for p, d in human.items()}
    staff14 = {p: d["staff"] for p, d in human.items()}
    rows14 = []
    for name, cset in (("all", any_all), ("strict", any_strict)):
        rr = build_panel(engine, "14", sorted(human), y14, llm14, staff14,
                         ingest.filter_corpus_pmids(engine, cset), cset, corpus)
        for r in rr:
            r["panel"] = f"c14_{name}"
        rows14 += rr
    return rows + rows14


# ---------------------------------------------------------------------------
FEATS = ("lab_any", "lab_rate", "lab_win_any", "sen_lab_any", "sen_lab_rate", "sen_lab_win_any")


def _feat(r, k):
    if k.endswith("win_any"):
        return float(r[k[:-4]] > 0)
    return float(r[k])


def show_lifts(rows, base_key, rng, title):
    y = np.array([r["y"] for r in rows])
    base = np.array([r[base_key] for r in rows])
    print(f"\n  -- {title}: n={len(y)} yes={int(y.sum())} no={int(len(y) - y.sum())}  "
          f"base({base_key}) AUC={auc(y, base):.4f}")
    if len(y) < 10 or y.sum() in (0, len(y)):
        print("     NOT MEASURABLE (one-class or <10)")
        return
    for k in FEATS:
        f = np.array([_feat(r, k) for r in rows])
        if f.std() == 0:
            print(f"     {k:<16} constant on this slice -- no lift possible")
            continue
        L = lift(base, f, y, rng)
        print(f"     {k:<16} fires {int((f > 0).sum()):>4} (yes {int(y[f > 0].sum())})  "
              f"marg AUC={auc(y, f):.4f}  {L['auc_base']:.4f} -> {L['auc_with']:.4f}  "
              f"lift={L['lift']:+.4f} CI95=[{L['ci95'][0]:+.4f},{L['ci95'][1]:+.4f}]  w={L['w_full']:.2f}")


def precision_table(rows, title):
    print(f"\n  -- precision where the signal fires ({title})")
    for k in ("aff_se", "lab_any", "lab_win_any", "sen_lab_any"):
        fire = [r for r in rows if _feat(r, k) > 0] if k != "aff_se" else [r for r in rows if r["aff_se"] > 0]
        kk = sum(r["y"] for r in fire)
        lo, hi = wilson(kk, len(fire))
        print(f"     {k:<14} fires {len(fire):>4}: yes {kk:>4} / no {len(fire) - kk:>4}  "
              f"precision {kk / max(len(fire), 1):.3f} [{lo:.3f},{hi:.3f}]")
    # lab fires where author affinity is silent: the 'new postdoc in a heavy-user lab' case
    for k in ("lab_any", "lab_win_any"):
        fire = [r for r in rows if _feat(r, k) > 0 and r["aff_se"] == 0]
        kk = sum(r["y"] for r in fire)
        lo, hi = wilson(kk, len(fire))
        print(f"     {k} & aff_se==0  fires {len(fire):>4}: yes {kk} / no {len(fire) - kk}  "
              f"precision {kk / max(len(fire), 1):.3f} [{lo:.3f},{hi:.3f}]")
    small = [r for r in rows if 0 < r["top_author_total"] < 5]
    fire = [r for r in small if r["lab_any"]]
    print(f"     small-denominator rows (top non-staff author has 1-4 corpus papers): {len(small)} "
          f"(yes {sum(r['y'] for r in small)}); lab_any fires on {len(fire)} "
          f"(yes {sum(r['y'] for r in fire)}), aff_se fires on {sum(1 for r in small if r['aff_se'] > 0)}")


def self_reference(rows, title):
    """Of the panel papers that are themselves in the core set, how many keep a non-zero
    signal once they are taken out of their own numerator."""
    inc = [r for r in rows if r["in_core"]]
    if not inc:
        print(f"\n  -- self-reference ({title}): no panel paper is in the core set")
        return
    a_s = sum(1 for r in inc if r["aff_self"] > 0)
    a_x = sum(1 for r in inc if r["aff_se"] > 0)
    l_s = sum(1 for r in inc if r["lab_rate_self"] > 0)
    l_x = sum(1 for r in inc if r["lab_any"])
    print(f"\n  -- self-reference ({title}): {len(inc)} panel papers are in the core set")
    print(f"     author affinity: non-zero WITH self {a_s}, after excluding self {a_x} "
          f"({a_s - a_x} were self-only)")
    print(f"     lab rate:        non-zero WITH self {l_s}, after excluding self {l_x} "
          f"({l_s - l_x} were self-only)")


def report(rows, seed):
    rng = np.random.default_rng(seed)
    b = [r for r in rows if r["core"] == "2"]
    hard = [r for r in b if r["y"] == 1 or r["panel"] == "random"]
    hard_llm = [r for r in hard if r["llm"]]
    print("\n########## CORE 2 (Biomedical Imaging) ##########")
    print(f"  head coverage: yes {sum(1 for r in hard if r['y'] and r['head'])}/"
          f"{sum(r['y'] for r in hard)}, random {sum(1 for r in hard if not r['y'] and r['head'])}/"
          f"{sum(1 for r in hard if not r['y'])}")
    self_reference(hard, "core 2 hard panel")
    show_lifts(hard_llm, "base", rng, "HARD+LLM: yes vs LLM-scored random; base = staff + aff_se + LLM")
    show_lifts(hard_llm, "base_fit", rng, "HARD+LLM, base = staff + aff_fit (#423 affinity) + LLM")
    rep = [r for r in hard_llm if r["aff_se"] > 0]
    show_lifts(rep, "base", rng, "HARD+LLM repeat users (aff_se > 0)")
    nor = [r for r in hard_llm if r["aff_se"] == 0]
    show_lifts(nor, "base", rng, "HARD+LLM affinity-silent (aff_se == 0)")
    d20 = [r for r in hard_llm if (r["year"] or 0) >= 2020]
    show_lifts(d20, "base", rng, "HARD+LLM date-matched (yes papers 2020+)")
    precision_table(hard_llm, "core 2 HARD+LLM")

    # replacement: lab instead of author affinity, both LOO-fitted on staff+LLM
    y = np.array([r["y"] for r in hard_llm])
    bn = np.array([r["base_noaff"] for r in hard_llm])
    for k in ("aff_se", "lab_rate", "lab_any"):
        f = np.array([float(r[k]) for r in hard_llm])
        L = lift(bn, f, y, rng)
        print(f"     REPLACE: staff+LLM + w*{k:<9} {L['auc_base']:.4f} -> {L['auc_with']:.4f} "
              f"lift={L['lift']:+.4f} CI95=[{L['ci95'][0]:+.4f},{L['ci95'][1]:+.4f}]")

    lab = [r for r in b if r["panel"] == "label"]
    show_lifts(lab, "base_ack", rng, "LABELLED yes vs no (easy negatives); base = staff + aff_se + LLM + ack")

    for name in ("c14_all", "c14_strict"):
        rr = [r for r in rows if r["panel"] == name]
        print(f"\n########## CORE 14 human decisions, core set = {name} ##########")
        self_reference(rr, name)
        show_lifts(rr, "base", rng, "claimed vs rejected; base = staff + aff_se + stored LLM")
        precision_table(rr, name)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--negatives", type=int, default=1200)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--llm-cache", help="#423's live LLM cache for the random negatives")
    ap.add_argument("--ddb-rows", help="local JSON of a read-only Scan of all CORE# rows")
    ap.add_argument("--out")
    ap.add_argument("--from-json")
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

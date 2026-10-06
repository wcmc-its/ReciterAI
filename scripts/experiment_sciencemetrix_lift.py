#!/usr/bin/env python3
"""EXPERIMENT: does the publishing journal's Science-Metrix subfield predict core usage?

    lift(core, subfield) = share of the core's confirmed/claimed papers in that subfield
                           / share of the scoreable corpus in that subfield

A paper's feature is log(lift) for its journal's subfield (0 when the journal has no
Science-Metrix row, i.e. absent evidence). The question is not "is it predictive" — the
journal surely is, a little — but whether it adds anything combine() does not already
know. So every number here is reported twice: alone (marginal AUC), and ON TOP OF the
current score (incremental AUC of base + beta * feature, beta fitted leave-one-out).

READ-ONLY. reciterdb (journal_science_metrix, analysis_summary_article/_author) via
SELECTs, DynamoDB via a Scan + GetItem. Nothing is written anywhere. No Bedrock calls.

DATA
  * journal -> subfield: reciterdb.journal_science_metrix (19,866 journals), joined on
    analysis_summary_article.issn = sm.issn, falling back to sm.eissn.
  * core profile: the core's confirmed/claimed CORE# rows in DynamoDB (what production's
    affinity prior reads), gated to the corpus. LEAVE-ONE-OUT: a paper in the profile is
    scored against the profile minus itself. `--profile-excludes-labels` instead drops
    every labelled paper from the profile up front (no label leaks into the feature at
    all), which is the stricter of the two.
  * panel: analysis/labeled_set.csv (the imaging core, LABEL_CORE "2") exactly as
    scripts/fit_evidence_weights.py panel B builds it — 137 yes / 100 no — plus the same
    seeded random-corpus negatives (seed 11, n 1200).

THE BASE SCORE, and what it leaves out. combine.score() over the evidence panel B can
reproduce: staff co-authorship, the curated client list and the author x core affinity
rate (built as production builds it since #418 — the core's own staff lend no affinity
to it). Two panels, because the LLM score exists only for labelled rows:
  P1  labelled yes vs random corpus — base = staff + affinity, NO llm on either side
      (scoring the negatives would cost Bedrock calls; leaving it out of both sides keeps
      the comparison symmetric). This is the population the pipeline scores.
  P2  labelled yes vs labelled no — base = staff + affinity + stored llm. Easy negatives
      (see fit_evidence_weights.panel_b), but the only panel with the full score.
No acknowledgement signal on either: panel B has never carried full text.

    python3 scripts/experiment_sciencemetrix_lift.py
    python3 scripts/experiment_sciencemetrix_lift.py --profile-excludes-labels
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

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from pipeline_cores import combine, ingest, signals  # noqa: E402
from pipeline_cores.dictionary import load_core  # noqa: E402
from pipeline_cores.models import SignalResult  # noqa: E402

GROUND_TRUTH = Path("/Users/paulalbert/Dropbox/Projects/Inferring Cores and Services/analysis")
LABELS = GROUND_TRUTH / "labeled_set.csv"
LLM_SCORES = GROUND_TRUTH / "calibration_llm_results.json"
LABEL_CORE = "2"

# Pseudo-count shrinking every subfield's core share toward its corpus share (lift 1).
# A 69-paper profile cannot estimate a 1-paper subfield's share, so the prior carries it.
SHRINK = 10.0
# The doctrine-shaped candidate key: one binary "the journal sits in a subfield this
# core over-indexes on". Edge chosen before looking at the panel.
ENRICHED_MIN_LIFT = 2.0
BOOTSTRAP = 1000


# ---------------------------------------------------------------------------
# pure pieces (tested in tests/test_cores_sciencemetrix_experiment.py)
# ---------------------------------------------------------------------------
def subfield_lift(core_counts: collections.Counter, core_n: int, corpus_share: dict,
                  subfield: str, *, shrink: float = SHRINK) -> float:
    """Shrunk lift of `subfield` for a core with `core_counts` over `core_n` papers.
    1.0 (no evidence) for an unknown subfield or an empty profile."""
    q = corpus_share.get(subfield)
    if not subfield or not q:
        return 1.0
    return ((core_counts.get(subfield, 0) + shrink * q) / (core_n + shrink)) / q


def loo_lift(pmid: str, subfield: str, profile: dict, core_counts, core_n, corpus_share):
    """Lift with `pmid` removed from the profile when it is in it."""
    if pmid in profile and profile[pmid] == subfield and subfield:
        counts = collections.Counter(core_counts)
        counts[subfield] -= 1
        return subfield_lift(counts, core_n - 1, corpus_share, subfield)
    if pmid in profile:
        return subfield_lift(core_counts, core_n - 1, corpus_share, subfield)
    return subfield_lift(core_counts, core_n, corpus_share, subfield)


def auc(scores, labels) -> float:
    """Mann-Whitney AUC with ties counted half."""
    pairs = sorted(zip(scores, labels))
    rank_sum, i, n = 0.0, 0, len(pairs)
    while i < n:
        j = i
        while j < n and pairs[j][0] == pairs[i][0]:
            j += 1
        avg = (i + j + 1) / 2.0                     # 1-based average rank of the tie block
        rank_sum += avg * sum(1 for k in range(i, j) if pairs[k][1])
        i = j
    npos = sum(1 for _, y in pairs if y)
    nneg = n - npos
    if not npos or not nneg:
        return float("nan")
    return (rank_sum - npos * (npos + 1) / 2.0) / (npos * nneg)


def fit_beta(offsets, xs, ys, iters: int = 25) -> tuple:
    """Logistic regression logit = a + offset + beta * x (offset fixed). Newton on (a, beta).

    The intercept `a` absorbs the panel's prevalence (P1/P2 are enriched far above the
    2% prior), so beta is the only thing that can move a ranking.
    """
    a = b = 0.0
    for _ in range(iters):
        g0 = g1 = h00 = h01 = h11 = 0.0
        for o, x, y in zip(offsets, xs, ys):
            z = max(-30.0, min(30.0, a + o + b * x))
            p = 1.0 / (1.0 + math.exp(-z))
            r, w = y - p, p * (1 - p)
            g0 += r
            g1 += r * x
            h00 += w
            h01 += w * x
            h11 += w * x * x
        h00 += 1e-6
        h11 += 1e-6
        det = h00 * h11 - h01 * h01
        if det <= 0:
            break
        da = (h11 * g0 - h01 * g1) / det
        db = (h00 * g1 - h01 * g0) / det
        a, b = a + da, b + db
        if abs(da) + abs(db) < 1e-9:
            break
    return a, b


def loo_incremental(offsets, xs, ys) -> tuple:
    """(full-panel beta, LOO-scored offsets + beta_-i * x_i) — each row scored by a beta
    fitted without it, so the incremental AUC is out-of-sample."""
    _, beta = fit_beta(offsets, xs, ys)
    out = []
    for i in range(len(ys)):
        _, b = fit_beta(offsets[:i] + offsets[i + 1:], xs[:i] + xs[i + 1:], ys[:i] + ys[i + 1:])
        out.append(offsets[i] + b * xs[i])
    return beta, out


def woe(pos_hits, pos_n, neg_hits, neg_n, alpha=0.5) -> float:
    """Same estimator as fit_evidence_weights.weight()."""
    return math.log(((pos_hits + alpha) / (pos_n + 2 * alpha))
                    / ((neg_hits + alpha) / (neg_n + 2 * alpha)))


def loo_woe_scores(offsets, flags, ys) -> tuple:
    """Binary key priced by WoE on the panel, LOO: row i's weight is fitted without row i."""
    pos_n = sum(ys)
    neg_n = len(ys) - pos_n
    ph = sum(1 for f, y in zip(flags, ys) if f and y)
    nh = sum(1 for f, y in zip(flags, ys) if f and not y)
    full = woe(ph, pos_n, nh, neg_n)
    out = []
    for o, f, y in zip(offsets, flags, ys):
        if not f:
            out.append(o)
            continue
        w = woe(ph - y, pos_n - y, nh - (1 - y), neg_n - (1 - y))
        out.append(o + w)
    return (ph, pos_n, nh, neg_n, full), out


def bootstrap_delta(a_scores, b_scores, ys, reps: int, seed: int) -> tuple:
    """Paired bootstrap 95% interval on AUC(b) - AUC(a)."""
    rng = random.Random(seed)
    n = len(ys)
    deltas = []
    for _ in range(reps):
        idx = [rng.randrange(n) for _ in range(n)]
        yy = [ys[i] for i in idx]
        if not any(yy) or all(yy):
            continue
        deltas.append(auc([b_scores[i] for i in idx], yy) - auc([a_scores[i] for i in idx], yy))
    deltas.sort()
    return deltas[int(0.025 * len(deltas))], deltas[int(0.975 * len(deltas)) - 1]


# ---------------------------------------------------------------------------
# data (read-only)
# ---------------------------------------------------------------------------
def corpus_subfields(engine, extra_pmids=()) -> tuple:
    """(corpus {pmid: subfield}, extra {pmid: subfield}); "" when the journal has no row.

    `extra_pmids` are looked up WITHOUT the corpus predicate. Needed because 100 of the
    137 labelled-yes papers are pre-2020 (outside the corpus): scoring them as "no
    subfield" (log-lift 0) against corpus negatives that almost all have one made
    MISSINGNESS look like signal in the first run of this script.
    """
    from sqlalchemy import bindparam, text  # lazy

    with engine.connect() as conn:
        sm = conn.execute(text(
            "SELECT smsid, issn, eissn, subfield FROM journal_science_metrix ORDER BY smsid"
        )).all()
        rows = conn.execute(text(
            "SELECT DISTINCT pmid, issn FROM analysis_summary_article "
            "WHERE publicationTypeCanonical = 'Academic Article' AND articleYear >= 2020"
        )).all()
        extra_rows = conn.execute(text(
            "SELECT DISTINCT pmid, issn FROM analysis_summary_article WHERE pmid IN :p"
        ).bindparams(bindparam("p", expanding=True)),
            {"p": [int(x) for x in extra_pmids] or [0]}).all()
    by_issn, by_eissn = {}, {}
    for _sid, issn, eissn, sub in sm:
        if issn:
            by_issn.setdefault(issn.strip(), sub)
        if eissn:
            by_eissn.setdefault(eissn.strip(), sub)
    def resolve(rs):
        out = {}
        for pmid, issn in rs:
            key = (issn or "").strip()
            sub = by_issn.get(key) or by_eissn.get(key) or ""
            p = str(pmid)
            if not out.get(p):
                out[p] = sub
        return out
    return resolve(rows), resolve(extra_rows)


def logit(p: float) -> float:
    p = min(max(p, 1e-12), 1 - 1e-12)
    return math.log(p / (1 - p))


def report(name, ys, base, feat, flags, seed):
    """Print one panel's marginal / incremental numbers."""
    npos = sum(ys)
    print(f"\n--- {name}: n={len(ys)} ({npos} pos / {len(ys) - npos} neg) ---")
    a_base = auc(base, ys)
    a_feat = auc(feat, ys)
    beta, inc = loo_incremental(base, feat, ys)
    a_inc = auc(inc, ys)
    (ph, pn, nh, nn, w_full), inc_key = loo_woe_scores(base, flags, ys)
    a_key = auc(inc_key, ys)
    lo, hi = bootstrap_delta(base, inc, ys, BOOTSTRAP, seed)
    klo, khi = bootstrap_delta(base, inc_key, ys, BOOTSTRAP, seed)
    print(f"  marginal AUC, log-lift alone           {a_feat:.4f}")
    print(f"  base AUC, combine() logit              {a_base:.4f}")
    print(f"  base + beta*log-lift (LOO beta)        {a_inc:.4f}   delta {a_inc - a_base:+.4f}"
          f"   95% boot [{lo:+.4f}, {hi:+.4f}]   full-panel beta {beta:.3f}")
    print(f"  base + sm:enriched key (LOO WoE)       {a_key:.4f}   delta {a_key - a_base:+.4f}"
          f"   95% boot [{klo:+.4f}, {khi:+.4f}]")
    print(f"    sm:enriched (lift>={ENRICHED_MIN_LIFT}) fires {ph}/{pn} pos vs {nh}/{nn} neg"
          f"  -> full-panel WoE {w_full:+.2f} nats")
    return {"n": len(ys), "pos": npos, "auc_feature": a_feat, "auc_base": a_base,
            "auc_base_plus_loglift": a_inc, "beta": beta, "delta_ci": [lo, hi],
            "auc_base_plus_key": a_key, "key_counts": [ph, pn, nh, nn], "key_woe": w_full,
            "key_delta_ci": [klo, khi]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--negatives", type=int, default=1200)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--profile-excludes-labels", action="store_true",
                    help="drop every labelled paper from the core profile (stricter than LOO)")
    ap.add_argument("--json-out", help="write the numbers here as JSON")
    args = ap.parse_args(argv)

    from utils.db import get_engine  # lazy
    from pipeline_cores.persist import get_curated_staff, scan_prior_core_usage  # lazy

    engine = get_engine()
    labels = {r["pmid"]: r["label"] for r in csv.DictReader(LABELS.open())}
    sub_of, sub_extra = corpus_subfields(engine, labels)
    covered = sum(1 for v in sub_of.values() if v)
    print(f"corpus: {len(sub_of)} pmids, {covered} ({covered / len(sub_of):.1%}) with a "
          f"Science-Metrix subfield")
    corpus_counts = collections.Counter(v for v in sub_of.values() if v)
    corpus_share = {k: v / covered for k, v in corpus_counts.items()}
    in_corpus = set(sub_of)
    sub_of = {**sub_extra, **sub_of}
    stored_llm = json.loads(LLM_SCORES.read_text())
    core = load_core(LABEL_CORE)

    # Core profile: production's confirmed/claimed rows, corpus-gated.
    recs = scan_prior_core_usage(LABEL_CORE, strict=True)
    prof_pmids = sorted(ingest.filter_corpus_pmids(engine, {r["pmid"] for r in recs}))
    if args.profile_excludes_labels:
        prof_pmids = [p for p in prof_pmids if p not in labels]
    profile = {p: sub_of.get(p, "") for p in prof_pmids}
    core_counts = collections.Counter(v for v in profile.values() if v)
    core_n = sum(core_counts.values())
    in_labels = sum(1 for p in profile if p in labels)
    print(f"core {LABEL_CORE} profile: {len(recs)} confirmed/claimed rows, {len(profile)} "
          f"in corpus, {core_n} with a subfield; {in_labels} of them are labelled papers "
          f"({'excluded' if args.profile_excludes_labels else 'scored leave-one-out'})")
    print(f"  top subfields (core n / core share / corpus share / shrunk lift):")
    for sub, n in core_counts.most_common(10):
        print(f"    {sub:<44}{n:>4}{n / core_n:>8.1%}{corpus_share[sub]:>8.2%}"
              f"{subfield_lift(core_counts, core_n, corpus_share, sub):>8.2f}")

    # Panel exactly as fit_evidence_weights.panel_b + main() draw it.
    corpus = [p["pmid"] for p in ingest.fetch_publications(engine)]
    random.seed(args.seed)
    neg_pmids = random.sample(corpus, args.negatives)
    everyone = sorted(labels) + list(neg_pmids)

    dyn_staff = get_curated_staff(LABEL_CORE)
    staff = {c.lower() for c in core.staff_cwids} | set(dyn_staff)
    coauthors = signals.coauthorship_index(engine, core, everyone, extra_cwids=dyn_staff)
    bylines = ingest.fetch_author_bylines(engine, everyone)
    # Affinity as production builds it (#418: staff lend no affinity to their own core),
    # from the same profile rows minus the label set so no labelled paper feeds its own
    # prior — the fit script's rule.
    aff_src = [p for p in prof_pmids if p not in labels]
    counts: dict = collections.defaultdict(lambda: collections.defaultdict(int))
    for _pmid, cwids in ingest.fetch_author_bylines(engine, aff_src).items():
        for cwid in cwids:
            if cwid.lower() not in staff:
                counts[cwid][LABEL_CORE] += 1
    index = signals.build_affinity_index(counts, ingest.fetch_author_totals(engine, list(counts)))
    clients = set(core.clients)

    def sig(pmid, with_llm):
        return SignalResult(
            coauthor_cwids=coauthors.get(pmid, []),
            client_cwids=[c for c in bylines.get(pmid, []) if c in clients],
            llm_score=stored_llm.get(pmid, {}).get("score") if with_llm else None,
            author_affinity=signals.author_affinity(index, bylines.get(pmid, []), LABEL_CORE),
        )

    def feature(pmid):
        lift = loo_lift(pmid, sub_of.get(pmid, ""), profile, core_counts, core_n, corpus_share)
        return math.log(lift), lift >= ENRICHED_MIN_LIFT

    def build(rows, with_llm):
        ys, base, feat, flags, aff = [], [], [], [], []
        for pmid, y in rows:
            s = sig(pmid, with_llm)
            f, fl = feature(pmid)
            ys.append(y)
            base.append(logit(combine.score(s)))
            feat.append(f)
            flags.append(fl)
            aff.append(s.author_affinity > 0)
        return ys, base, feat, flags, aff

    yes = [p for p in sorted(labels) if labels[p] == "yes"]
    no = [p for p in sorted(labels) if labels[p] == "no"]
    lab_cov = sum(1 for p in labels if sub_of.get(p))
    print(f"\npanel: {len(yes)} yes / {len(no)} no labelled, {len(neg_pmids)} random corpus; "
          f"{lab_cov}/{len(labels)} labelled papers have a subfield")

    results = {}
    for name, rows, with_llm in (
        ("P1 yes vs random corpus (base = staff + affinity)",
         [(p, 1) for p in yes] + [(p, 0) for p in neg_pmids], False),
        # Date-matched: 100 of the 137 yes are pre-2020 while every random negative is a
        # 2020+ corpus paper, so P1 also measures era. Same panel, positives restricted
        # to the corpus the negatives are drawn from.
        ("P1c in-corpus yes vs random corpus (base = staff + affinity)",
         [(p, 1) for p in yes if p in in_corpus] + [(p, 0) for p in neg_pmids], False),
        ("P2 yes vs labelled no (base = staff + affinity + llm)",
         [(p, 1) for p in yes] + [(p, 0) for p in no], True),
    ):
        ys, base, feat, flags, aff = build(rows, with_llm)
        results[name] = {"all": report(name, ys, base, feat, flags, args.seed)}
        sel = [i for i, a in enumerate(aff) if a]
        pick = lambda xs: [xs[i] for i in sel]  # noqa: E731
        if sum(pick(ys)) >= 5 and len(sel) - sum(pick(ys)) >= 5:
            results[name]["repeat_users"] = report(
                name + " | REPEAT-USER subset (affinity > 0)",
                pick(ys), pick(base), pick(feat), pick(flags), args.seed)
        else:
            print(f"\n--- {name} | REPEAT-USER subset: {sum(pick(ys))} pos / "
                  f"{len(sel) - sum(pick(ys))} neg — too few to measure ---")
            results[name]["repeat_users"] = {"n": len(sel), "pos": sum(pick(ys)),
                                             "skipped": "too few on one side"}

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

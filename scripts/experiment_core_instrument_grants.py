#!/usr/bin/env python3
"""EXPERIMENT: do papers that cite a WCM NIH S10 instrument award (or the CTSC UL1)
use the core facility that houses that instrument?

The idea: an S10 Shared Instrumentation award buys ONE instrument for a shared
facility, and NIH asks every user to cite it. So a paper citing the S10 is a
paper that used the instrument, and the instrument sits in a known core. That is
evidence about the PAPER (its funding statement), not about who wrote it, so it
is independent of the author-affinity prior by construction.

Two questions, answered separately:
  1. COVERAGE: how many corpus papers does the signal fire on at all?
  2. PRECISION: of the papers it fires on, how many show INDEPENDENT evidence of
     the core (an alias match in PMC full text, a core staff member on the byline,
     an engine confirmation, a human claim) vs a human rejection?
Plus, where a labelled panel exists (panel B, the imaging core), the incremental
leave-one-out lift over a baseline that includes the LLM score.

READ-ONLY. NIH RePORTER (projects + publications search), NCBI E-utilities
(esearch [gr]; efetch PMC XML through pipeline_cores.fulltext with a DISK-only
cache, never the S3 tier), reciterdb SELECTs, one DynamoDB Scan. Nothing written
anywhere except the local cache directory. No Bedrock calls.

    python3 scripts/experiment_core_instrument_grants.py --cache-dir out/core_grants
    python3 scripts/experiment_core_instrument_grants.py --cache-dir out/core_grants --json-out r.json
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import math
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

GROUND_TRUTH = Path("/Users/paulalbert/Dropbox/Projects/Inferring Cores and Services/analysis")
LABELS = GROUND_TRUTH / "labeled_set.csv"
LLM_SCORES = GROUND_TRUTH / "calibration_llm_results.json"

REPORTER = "https://api.reporter.nih.gov/v2/"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
WCM_ORG = "WEILL MEDICAL COLL OF CORNELL UNIV"   # the only WCM org name RePORTER returns S10s under
CTSC_UL1 = ("UL1RR024996", "UL1TR000457", "UL1TR002384")

# S10 -> core. Two rules, reported separately so the weaker one can be discounted:
#   A  the award's PI is a staff CWID in config/core_dictionary.yaml for that core.
#   B  the PI is NOT on the core's staff list, but the instrument is one the core's
#      llm_description names (MRI, MALDI imaging, EM / multiphoton). An assertion,
#      not a measurement: rule-B rows are where a mapping error would hide.
# Every other WCM S10 (36 in RePORTER) maps to no dictionary core and is listed
# by the script as unmapped.
S10_CORE = {
    # core 2 Biomedical Imaging (Ballon = djb2001)
    "S10RR023020": ("2", "A"),   # small-animal 7T MRI/MRS, 2007
    "S10OD030447": ("2", "A"),   # cyclotron upgrade, 2021
    "S10OD034271": ("2", "A"),   # PET/CT, 2023
    "S10OD021782": ("2", "B"),   # 3T MRI, PI Yi Wang (Radiology), 2016
    "S10OD027039": ("2", "B"),   # 7T MRI, PI Yi Wang, 2021
    # core 12 NMR (Bracken = wcb2001)
    "S10RR023694": ("12", "A"),  # 500 MHz NMR, 2007
    "S10OD016320": ("12", "A"),  # 600 MHz cold probe + console, 2013
    "S10OD028556": ("12", "A"),  # RF console + probes, 2020
    # core 5 Genomics Resources (Xiang = jzx2002)
    "S10RR022370": ("5", "A"),   # 7900HT real-time PCR, 2006
    # core 10 Human Immune Monitoring (Anandasabapathy = nia9069)
    "S10OD032397": ("10", "A"),  # high-parameter flow sorter, 2023
    # core 9 Advanced Biomolecular Analysis (mass spec; PI Steven Gross, Pharmacology)
    "S10RR011360": ("9", "B"),
    "S10RR015909": ("9", "B"),
    "S10RR019355": ("9", "B"),
    "S10RR022615": ("9", "B"),
    "S10RR027305": ("9", "B"),
    "S10OD023652": ("9", "B"),   # MALDI-FT-ICR imaging, 2017
    # core 11 Microscopy and Image Analysis (optical/EM; PI Frederick Maxfield, Biochemistry)
    "S10RR004718": ("11", "B"),
    "S10RR011886": ("11", "B"),
    "S10RR027699": ("11", "B"),  # JEM 1400 EM, 2010
    "S10RR029663": ("11", "B"),  # multiphoton, 2010
}

# A WIDER "does the text name the core" pattern than the dictionary aliases, used ONLY
# to estimate precision here. Written after reading the core-11 acknowledgements, which
# spell it "Microscopy & Image Analysis Core" — an ampersand the dictionary aliases
# ("... and Image Analysis ...") do not match, so signal 3 misses every one of them.
WIDE_NAME = {
    "2": r"Citigroup|\bCBIC\b|Biomedical Imaging (Center|Core)",
    "5": r"Genomics (Resources )?Core|Genomics Resources",
    "9": r"Advanced Biomolecular|\bABAC\b|Mass Spectrometry (Core|Facility)",
    "10": r"Immune Monitoring|Flow Cytometry Core",
    "11": r"Microscopy (&|and) Image Analysis|Optical Microscopy Core|"
          r"Electron Microscopy (&|and) Histology",
    "12": r"NMR (Core|[Ff]acilit)|Nuclear Magnetic Resonance (Core|Facility)",
}


# ---------------------------------------------------------------------------
# pure pieces
# ---------------------------------------------------------------------------
def wilson(k: int, n: int, z: float = 1.96) -> tuple:
    """95% Wilson interval for k/n; (nan, nan) when n is 0."""
    if not n:
        return float("nan"), float("nan")
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def woe(pos_hits, pos_n, neg_hits, neg_n, alpha=0.5) -> float:
    """Same estimator as fit_evidence_weights.weight()."""
    return math.log(((pos_hits + alpha) / (pos_n + 2 * alpha))
                    / ((neg_hits + alpha) / (neg_n + 2 * alpha)))


def auc(scores, labels) -> float:
    """Mann-Whitney AUC with ties counted half."""
    pairs = sorted(zip(scores, labels))
    rank_sum, i, n = 0.0, 0, len(pairs)
    while i < n:
        j = i
        while j < n and pairs[j][0] == pairs[i][0]:
            j += 1
        rank_sum += (i + j + 1) / 2.0 * sum(1 for k in range(i, j) if pairs[k][1])
        i = j
    npos = sum(1 for _, y in pairs if y)
    nneg = n - npos
    if not npos or not nneg:
        return float("nan")
    return (rank_sum - npos * (npos + 1) / 2.0) / (npos * nneg)


def loo_woe_scores(offsets, flags, ys):
    """Binary key priced by WoE, LOO: row i's weight is fitted without row i."""
    pos_n = sum(ys)
    neg_n = len(ys) - pos_n
    ph = sum(1 for f, y in zip(flags, ys) if f and y)
    nh = sum(1 for f, y in zip(flags, ys) if f and not y)
    out = []
    for o, f, y in zip(offsets, flags, ys):
        out.append(o + woe(ph - y, pos_n - y, nh - (1 - y), neg_n - (1 - y)) if f else o)
    return (ph, pos_n, nh, neg_n, woe(ph, pos_n, nh, neg_n)), out


def bootstrap_delta(a, b, ys, reps=1000, seed=11) -> tuple:
    rng = random.Random(seed)
    n, deltas = len(ys), []
    for _ in range(reps):
        idx = [rng.randrange(n) for _ in range(n)]
        yy = [ys[i] for i in idx]
        if any(yy) and not all(yy):
            deltas.append(auc([b[i] for i in idx], yy) - auc([a[i] for i in idx], yy))
    deltas.sort()
    return deltas[int(0.025 * len(deltas))], deltas[int(0.975 * len(deltas)) - 1]


# ---------------------------------------------------------------------------
# external fetches (cached)
# ---------------------------------------------------------------------------
def _post(path: str, body: dict) -> dict:
    req = urllib.request.Request(REPORTER + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    for attempt in range(4):
        try:
            return json.load(urllib.request.urlopen(req, timeout=90))
        except Exception:
            if attempt == 3:
                raise
            time.sleep(3 * (attempt + 1))


def _paged(path: str, criteria: dict, **extra) -> list:
    out, off = [], 0
    while True:
        r = _post(path, {"criteria": criteria, "offset": off, "limit": 500, **extra})
        out += r.get("results", [])
        off += 500
        time.sleep(1.0)                      # RePORTER asks for <= 1 req/s
        if off >= r["meta"]["total"] or not r.get("results") or off >= 9500:
            return out


def _cached(cache: Path, name: str, fn):
    p = cache / name
    if p.exists():
        return json.loads(p.read_text())
    v = fn()
    p.write_text(json.dumps(v))
    return v


def wcm_s10_projects(cache: Path) -> list:
    return _cached(cache, "s10_projects.json", lambda: _paged(
        "projects/search", {"org_names": [WCM_ORG], "activity_codes": ["S10"]},
        include_fields=["CoreProjectNum", "ProjectTitle", "FiscalYear", "PrincipalInvestigators"]))


def reporter_links(cache: Path, name: str, nums: list) -> dict:
    """{core_project_num: [pmid]} from RePORTER's publication links (PubMed-derived)."""
    def go():
        out = collections.defaultdict(list)
        for n in nums:
            for x in _paged("publications/search", {"core_project_nums": [n]}):
                out[x["coreproject"]].append(str(x["pmid"]))
        return out
    return _cached(cache, name, go)


def pubmed_gr(cache: Path, nums: list) -> dict:
    """{num: [pmid]} from PubMed's grant field, several spellings of the serial."""
    key = os.getenv("NCBI_API_KEY") or os.getenv("PUBMED_API_KEY") or ""

    def go():
        out = {}
        for n in nums:
            serial = n[3:]
            term = (f'"{n}"[gr] OR "{serial}"[gr] OR "S10 {serial}"[gr] OR '
                    f'"S10-{serial}"[gr] OR "S10{serial[2:]}"[gr]')
            params = {"db": "pubmed", "term": term, "retmax": "10000", "retmode": "json"}
            if key:
                params["api_key"] = key
            for attempt in range(4):
                try:
                    r = json.load(urllib.request.urlopen(
                        EUTILS + "?" + urllib.parse.urlencode(params), timeout=60))
                    break
                except Exception:
                    if attempt == 3:
                        raise
                    time.sleep(2 * (attempt + 1))
            out[n] = r["esearchresult"].get("idlist", [])
            time.sleep(0.12 if key else 0.35)
        return out
    return _cached(cache, "pubmed_gr.json", go)


# ---------------------------------------------------------------------------
# internal reads
# ---------------------------------------------------------------------------
def scan_core_rows() -> dict:
    """{(pmid, core_id): row} over every CORE# row, all statuses. Read-only Scan."""
    from utils.dynamodb_helpers import TABLE_NAME, get_dynamo_client  # lazy

    c = get_dynamo_client()
    kw = {"TableName": TABLE_NAME, "FilterExpression": "begins_with(SK, :sk)",
          "ExpressionAttributeValues": {":sk": {"S": "CORE#"}},
          "ExpressionAttributeNames": {"#st": "status"},
          "ProjectionExpression": "pmid, core_id, #st, signal_ack, signal_coauthors, "
                                  "llm_score, author_affinity"}
    out = {}
    while True:
        r = c.scan(**kw)
        for it in r["Items"]:
            k = (it["pmid"]["S"], it["core_id"]["S"])
            out[k] = {"status": it.get("status", {}).get("S", ""),
                      "ack": it.get("signal_ack", {}).get("BOOL", False),
                      "staff": [x["S"] for x in it.get("signal_coauthors", {}).get("L", [])],
                      "llm": float(it["llm_score"]["N"]) if "llm_score" in it else None,
                      "aff": float(it["author_affinity"]["N"]) if "author_affinity" in it else 0.0}
        if "LastEvaluatedKey" not in r:
            return out
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]


def titles(engine, pmids) -> dict:
    from sqlalchemy import bindparam, text  # lazy
    if not pmids:
        return {}
    with engine.connect() as c:
        return {str(p): (t or "", y) for p, t, y in c.execute(text(
            "SELECT pmid, articleTitle, articleYear FROM analysis_summary_article WHERE pmid IN :p"
        ).bindparams(bindparam("p", expanding=True)), {"p": [int(x) for x in pmids]})}


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache-dir", default="out/core_grants")
    ap.add_argument("--negatives", type=int, default=1200)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--json-out")
    args = ap.parse_args(argv)
    cache = Path(args.cache_dir)
    cache.mkdir(parents=True, exist_ok=True)

    from utils.db import get_engine  # lazy
    from pipeline_cores import combine, ingest, signals
    from pipeline_cores.dictionary import load_core
    from pipeline_cores.fulltext import PmcFullTextClient, to_plain_text
    from pipeline_cores.models import SignalResult
    from pipeline_cores.persist import get_curated_staff, scan_prior_core_usage

    engine = get_engine()
    results: dict = {}

    # --- 1. the awards --------------------------------------------------------
    projects = wcm_s10_projects(cache)
    by_num = collections.defaultdict(list)
    for x in projects:
        by_num[x["core_project_num"]].append(x)
    nums = sorted(by_num)
    print(f"RePORTER: {len(projects)} WCM S10 project-years, {len(nums)} distinct awards "
          f"(org '{WCM_ORG}')")
    rep = reporter_links(cache, "s10_reporter_links.json", nums)
    gr = pubmed_gr(cache, nums)
    cites = {n: sorted(set(rep.get(n, [])) | set(gr.get(n, []))) for n in nums}
    print(f"  citing pmids: RePORTER links {sum(len(v) for v in rep.values())}, PubMed [gr] "
          f"{sum(len(v) for v in gr.values())}, union {len(set().union(*map(set, cites.values())))}")
    print(f"  awards cited by >=1 paper: {sum(1 for v in cites.values() if v)}/{len(nums)}")
    unmapped = [n for n in nums if n not in S10_CORE]
    print(f"  mapped to a dictionary core: {len(S10_CORE)} (rule A {sum(1 for v in S10_CORE.values() if v[1] == 'A')}"
          f", rule B {sum(1 for v in S10_CORE.values() if v[1] == 'B')}); unmapped {len(unmapped)} "
          f"citing {sum(len(cites[n]) for n in unmapped)} papers")

    corpus = {p["pmid"] for p in ingest.fetch_publications(engine)}
    rows = scan_core_rows()
    print(f"corpus {len(corpus)} pmids; DynamoDB CORE# rows {len(rows)}")

    # --- 2. per-core coverage + precision -----------------------------------
    ft = PmcFullTextClient(cache_dir=cache / "fulltext")   # disk-only: never the S3 tier
    core_cites = collections.defaultdict(dict)              # core -> pmid -> (award, rule)
    for n, (cid, rule) in S10_CORE.items():
        for p in cites.get(n, []):
            prev = core_cites[cid].get(p)
            if not prev or (prev[1] == "B" and rule == "A"):
                core_cites[cid][p] = (n, rule)
    print("\n=== per-core precision: S10-citing papers vs independent evidence of the core ===")
    print("  independent = full-text alias match for the core OR core staff on the byline OR")
    print("  engine 'confirmed' with ack/staff OR human 'claimed'; negative = human 'rejected'")
    prec: dict = {}
    for cid in sorted(core_cites, key=int):
        core = load_core(cid)
        ps = sorted(core_cites[cid])
        staff_ix = signals.coauthorship_index(engine, core, ps, extra_cwids=get_curated_staff(cid))
        t = titles(engine, ps)
        recs = []
        for p in ps:
            xml = ft.get_xml(p) if hasattr(ft, "get_xml") else ""
            text = to_plain_text(xml)
            ack = signals.acknowledgement_signal(text, core, xml) if text else SignalResult()
            r = rows.get((p, cid), {})
            rule = core_cites[cid][p][1]
            ind = bool(ack.ack_matched or staff_ix.get(p) or r.get("status") == "claimed"
                       or (r.get("status") == "confirmed" and (r.get("ack") or r.get("staff"))))
            named = bool(text and re.search(WIDE_NAME[cid], text))
            recs.append({"pmid": p, "award": core_cites[cid][p][0], "rule": rule,
                         "in_corpus": p in corpus, "has_fulltext": bool(text),
                         "ft_alias": ack.ack_alias if ack.ack_matched else "",
                         "staff": staff_ix.get(p, []), "status": r.get("status", "none"),
                         "llm": r.get("llm"), "aff": r.get("aff", 0.0), "independent": ind,
                         "named_wide": named,
                         "award_in_text": bool(text and core_cites[cid][p][0][3:] in text),
                         "title": t.get(p, ("", None))[0][:110], "year": t.get(p, ("", None))[1]})
        prec[cid] = recs
        for rule in ("A", "B"):
            sub = [x for x in recs if x["rule"] == rule]
            if not sub:
                continue
            k = sum(x["independent"] for x in sub)
            lo, hi = wilson(k, len(sub))
            inc = [x for x in sub if x["in_corpus"]]
            st = collections.Counter(x["status"] for x in inc)
            print(f"  core {cid:>2} {core.name[:28]:<28} rule {rule}: fires {len(sub):>3} "
                  f"(full text {sum(x['has_fulltext'] for x in sub)}), independent evidence "
                  f"{k}/{len(sub)} = {k / len(sub):.0%} [{lo:.0%}, {hi:.0%}]; in corpus "
                  f"{len(inc)}: {dict(st)}; ft-alias {sum(bool(x['ft_alias']) for x in sub)}, "
                  f"staff {sum(bool(x['staff']) for x in sub)}, rejected "
                  f"{sum(x['status'] == 'rejected' for x in sub)}")
            kw = sum(x["independent"] or x["named_wide"] for x in sub)
            lo, hi = wilson(kw, len(sub))
            print(f"      + core named under the WIDENED pattern: {kw}/{len(sub)} = "
                  f"{kw / len(sub):.0%} [{lo:.0%}, {hi:.0%}]; award number in the PMC text "
                  f"itself (not only PubMed metadata) {sum(x['award_in_text'] for x in sub)}")
    results["precision"] = prec

    # --- 3. what it adds where it fires: in-corpus, not already independently known
    print("\n=== in-corpus rows the signal fires on that carry NO other independent evidence ===")
    for cid, recs in sorted(prec.items(), key=lambda kv: int(kv[0])):
        new = [x for x in recs if x["in_corpus"] and not x["independent"]]
        print(f"  core {cid}: {len(new)} rows (engine status: "
              f"{dict(collections.Counter(x['status'] for x in new))}; "
              f"llm>=6: {sum(1 for x in new if (x['llm'] or 0) >= 6)}, aff>0: "
              f"{sum(1 for x in new if x['aff'] > 0)})")
        for x in new:
            print(f"      {x['pmid']} {x['year']} {x['award']}/{x['rule']} {x['status']:<10} "
                  f"llm={x['llm']} aff={x['aff']:.2f}  {x['title'][:80]}")

    # --- 4. background firing rate (likelihood-ratio denominator) ----------
    print("\n=== background: share of the 82k corpus citing each core's mapped S10s ===")
    for cid, recs in sorted(prec.items(), key=lambda kv: int(kv[0])):
        k = sum(1 for x in recs if x["in_corpus"])
        print(f"  core {cid}: {k}/{len(corpus)} = {k / len(corpus):.4%}")

    # --- 5. panel B (imaging core) LOO lift over a baseline WITH the LLM ----
    labels = {r["pmid"]: r["label"] for r in csv.DictReader(LABELS.open())}
    stored_llm = json.loads(LLM_SCORES.read_text())
    core2 = load_core("2")
    fired2 = set(core_cites.get("2", {}))
    recs2 = scan_prior_core_usage("2", strict=True)
    prof = sorted(ingest.filter_corpus_pmids(engine, {r["pmid"] for r in recs2}))
    aff_src = [p for p in prof if p not in labels]     # no labelled paper feeds its own prior
    dyn_staff = get_curated_staff("2")
    staff = {c.lower() for c in core2.staff_cwids} | set(dyn_staff)
    counts: dict = collections.defaultdict(lambda: collections.defaultdict(int))
    for _p, cw in ingest.fetch_author_bylines(engine, aff_src).items():
        for c in cw:
            if c.lower() not in staff:
                counts[c]["2"] += 1
    index = signals.build_affinity_index(counts, ingest.fetch_author_totals(engine, list(counts)))
    corpus_list = sorted(corpus, key=int, reverse=True)
    random.seed(args.seed)
    negs = random.sample(corpus_list, args.negatives)
    everyone = sorted(labels) + negs
    coauth = signals.coauthorship_index(engine, core2, everyone, extra_cwids=dyn_staff)
    bylines = ingest.fetch_author_bylines(engine, everyone)

    def base(p, with_llm):
        s = SignalResult(coauthor_cwids=coauth.get(p, []),
                         llm_score=stored_llm.get(p, {}).get("score") if with_llm else None,
                         author_affinity=signals.author_affinity(index, bylines.get(p, []), "2"))
        pr = min(max(combine.score(s), 1e-12), 1 - 1e-12)
        return math.log(pr / (1 - pr)), s.author_affinity > 0

    yes = [p for p in sorted(labels) if labels[p] == "yes"]
    no = [p for p in sorted(labels) if labels[p] == "no"]
    print(f"\n=== panel B (core 2): {len(yes)} yes / {len(no)} no labelled, {len(negs)} random corpus ===")
    results["panel_b"] = {}
    for name, rws, llm in (("P2 yes vs labelled no (base = staff+affinity+LLM)",
                            [(p, 1) for p in yes] + [(p, 0) for p in no], True),
                           ("P1 yes vs random corpus (base = staff+affinity, no LLM)",
                            [(p, 1) for p in yes] + [(p, 0) for p in negs], False)):
        ys, b, fl, af = [], [], [], []
        for p, y in rws:
            o, a = base(p, llm)
            ys.append(y); b.append(o); fl.append(p in fired2); af.append(a)
        (ph, pn, nh, nn, w), inc = loo_woe_scores(b, fl, ys)
        a0, a1 = auc(b, ys), auc(inc, ys)
        lo, hi = bootstrap_delta(b, inc, ys, seed=args.seed)
        print(f"  {name}: fires {ph}/{pn} pos vs {nh}/{nn} neg, full-panel WoE {w:+.2f}; "
              f"AUC {a0:.4f} -> {a1:.4f} (delta {a1 - a0:+.4f}, 95% boot [{lo:+.4f}, {hi:+.4f}])")
        sel = [i for i, a in enumerate(af) if a]
        rp = sum(ys[i] for i in sel)
        rf = [(ys[i], fl[i]) for i in sel]
        print(f"    repeat-user subset (aff>0): {rp} pos / {len(sel) - rp} neg; fires "
              f"{sum(1 for y, f in rf if f and y)} pos / {sum(1 for y, f in rf if f and not y)} neg")
        results["panel_b"][name] = {"fires": [ph, pn, nh, nn], "woe": w, "auc": [a0, a1],
                                    "delta_ci": [lo, hi]}

    # --- 6. core 14 x CTSC UL1 ---------------------------------------------
    ul1 = reporter_links(cache, "ctsc_ul1_links.json", list(CTSC_UL1))
    ul1p = set().union(*map(set, ul1.values()))
    k = len(ul1p & corpus)
    print(f"\n=== core 14 (Research Informatics) x CTSC UL1 {', '.join(CTSC_UL1)} ===")
    print(f"  UL1-citing pmids {len(ul1p)}; corpus background {k}/{len(corpus)} = {k / len(corpus):.2%}")
    grp = collections.defaultdict(list)
    for (p, cid), r in rows.items():
        if cid != "14":
            continue
        g = r["status"]
        if g == "confirmed":
            g += "/ack" if r["ack"] else ("/staff" if r["staff"] else "/prior-only")
        grp[g + (" (in corpus)" if p in corpus else " (out of corpus)")].append(p)
    out14 = {}
    for g in sorted(grp):
        ps = grp[g]
        h = sum(p in ul1p for p in ps)
        lo, hi = wilson(h, len(ps))
        out14[g] = [h, len(ps)]
        print(f"  {g:<36} {h:>4}/{len(ps):<5} = {h / len(ps):6.1%} [{lo:.1%}, {hi:.1%}]")
    results["core14_ul1"] = out14

    # Lift on the only core-14 panel with independent labels AND an LLM score. Positives:
    # in-corpus human 'claimed' + engine 'confirmed' on an alias match (signal 3, which
    # is independent of both the LLM and the affinity prior). Negatives: human
    # 'rejected'. Base = combine.score(staff + stored LLM). Affinity is LEFT OUT: the
    # stored value on a confirmed row includes that row's own confirmation (the
    # self-confirmation loop this experiment is trying to get away from).
    lab14 = []
    for (p, cid), r in rows.items():
        if cid != "14" or p not in corpus:
            continue
        if r["status"] == "claimed" or (r["status"] == "confirmed" and r["ack"]):
            lab14.append((p, 1, r))
        elif r["status"] == "rejected":
            lab14.append((p, 0, r))
    ys = [y for _, y, _ in lab14]
    b = []
    for _, _, r in lab14:
        pr = min(max(combine.score(SignalResult(coauthor_cwids=r["staff"],
                                                llm_score=r["llm"])), 1e-12), 1 - 1e-12)
        b.append(math.log(pr / (1 - pr)))
    fl = [p in ul1p for p, _, _ in lab14]
    (ph, pn, nh, nn, w), inc = loo_woe_scores(b, fl, ys)
    a0, a1 = auc(b, ys), auc(inc, ys)
    lo, hi = bootstrap_delta(b, inc, ys, seed=args.seed)
    print(f"  core-14 labelled panel ({pn} pos = claimed + confirmed/ack, {nn} neg = rejected; "
          f"base = staff + LLM): UL1 fires {ph}/{pn} pos vs {nh}/{nn} neg, WoE {w:+.2f}; AUC "
          f"{a0:.4f} -> {a1:.4f} (delta {a1 - a0:+.4f}, 95% boot [{lo:+.4f}, {hi:+.4f}])")
    results["core14_panel"] = {"fires": [ph, pn, nh, nn], "woe": w, "auc": [a0, a1],
                               "delta_ci": [lo, hi]}

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(results, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

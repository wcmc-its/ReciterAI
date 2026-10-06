#!/usr/bin/env python3
"""What a core's affinity prior strength s does to its live queue and its human labels.

READ-ONLY: one paginated DynamoDB Scan of the core's CORE#<id> rows plus reciterdb
SELECTs (bylines, corpus filter, per-year corpus totals, identity tenure, pub years).
Nothing is written, no Bedrock, no full text.

For each s in --strengths it rebuilds the affinity index the way run_core builds it
(non-staff authors of the core's confirmed + claimed in-corpus papers, through
ingest.affinity_inputs and signals.build_affinity_index, numerator-only self-exclusion,
tenure gate, shrinkage rate = (n + s*p0) / (total + s) with p0 = the core's confirmed +
claimed in-corpus papers / the corpus size, shipped weights) and reports:

  * status moves: each stored row's logit with its stored `aff:*` term swapped for the
    recomputed one, re-banded with combine()'s thresholds and the LLM / staff holds.
    `--stored-weights` gives the aff:* weights the STORED rows were scored with (the
    default is the pre-2026-10-06 set, 0.79 / 3.43 / 4.93, which is what the live table
    holds until the next run rescored it).
  * claimed vs rejected affinity AUC (rate and bucket) on the core's human-decided rows,
    the panel that says whether THIS core wants a different s from the global default.

Written for core 14 (Research Informatics); the numbers it printed on 2026-10-06 are in
config/core_dictionary.yaml beside core 14.

    python3 scripts/measure_affinity_prior_strength.py --core 14 --strengths 0 1 2 5 10 20
"""
from __future__ import annotations

import argparse
import collections
import logging
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline_cores import combine as C  # noqa: E402
from pipeline_cores import ingest, signals  # noqa: E402
from pipeline_cores.dictionary import load_core  # noqa: E402

PRE_REFIT = {"aff:trace": 0.79, "aff:regular": 3.43, "aff:core": 4.93}
RANK = {None: 0, "aff:trace": 1, "aff:regular": 2, "aff:core": 3}


def scan_core_rows(core_id: str) -> list:
    """Every CORE#<id> row (all statuses), the fields the re-banding needs."""
    from utils.dynamodb_helpers import TABLE_NAME, get_dynamo_client  # lazy
    client = get_dynamo_client()
    kw = dict(TableName=TABLE_NAME, FilterExpression="SK = :sk",
              ExpressionAttributeValues={":sk": {"S": f"CORE#{core_id}"}})
    rows = []
    while True:
        page = client.scan(**kw)
        for it in page.get("Items", []):
            llm = it.get("llm_score", {}).get("N")
            rows.append(dict(
                pmid=it["pmid"]["S"], status=it["status"]["S"],
                likelihood=float(it.get("likelihood", {}).get("N", 0)),
                affinity=float(it.get("author_affinity", {}).get("N", 0)),
                llm=int(float(llm)) if llm else None,
                ack=bool(it.get("signal_ack", {}).get("BOOL")),
                coauthors=bool(it.get("signal_coauthors", {}).get("L"))))
        if not page.get("LastEvaluatedKey"):
            return rows
        kw["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def key(rate):
    return None if rate <= 0 else f"aff:{C._affinity_bucket(rate)}"


def rebanded(row, new_rate, stored_w) -> tuple:
    """(status, P) with the stored aff:* term swapped for the recomputed one."""
    lik = min(max(row["likelihood"], 1e-6), 1 - 1e-6)
    logit = (math.log(lik / (1 - lik)) - stored_w.get(key(row["affinity"]), 0.0)
             + C.WEIGHTS.get(key(new_rate), 0.0))
    p = 1 / (1 + math.exp(-logit))
    status = ("confirmed" if p >= C.DEFAULT_CONFIRM_THRESHOLD else
              "candidate" if p >= C.DEFAULT_TRIAGE_THRESHOLD else "below_threshold")
    if status == "confirmed":      # the LLM / staff holds: never the deciding vote
        held = logit
        if row["llm"]:
            held -= C.LLM_INTERCEPT + C.LLM_PER_POINT * min(row["llm"], C.LLM_MAX_FITTED)
        if row["coauthors"] and not row["ack"]:
            held -= C.WEIGHTS["staff"]
        if 1 / (1 + math.exp(-held)) < C.DEFAULT_CONFIRM_THRESHOLD:
            status = "candidate"
    return status, p


def auc(pos, neg) -> float:
    wins = sum(1.0 if a > b else 0.5 if a == b else 0.0 for a in pos for b in neg)
    return wins / max(len(pos) * len(neg), 1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--core", default="14")
    ap.add_argument("--strengths", type=float, nargs="+", default=[0, 1, 2, 5, 10, 20])
    ap.add_argument("--stored-weights", type=float, nargs=3, default=None,
                    metavar=("TRACE", "REGULAR", "CORE"),
                    help="aff:* weights the stored rows were scored with (default 0.79 3.43 4.93)")
    args = ap.parse_args(argv)
    logging.disable(logging.WARNING)
    stored_w = (dict(zip(PRE_REFIT, args.stored_weights)) if args.stored_weights
                else PRE_REFIT)

    from utils.db import get_engine  # lazy
    from pipeline_cores.persist import get_curated_staff  # lazy
    engine = get_engine()
    core = load_core(args.core)
    rows = scan_core_rows(core.core_id)
    pmids = sorted({r["pmid"] for r in rows})
    in_corpus = ingest.filter_corpus_pmids(engine, pmids)
    bylines = ingest.fetch_author_bylines(engine, pmids)
    staff = {c.lower() for c in core.staff_cwids} | {c.lower() for c in get_curated_staff(core.core_id)}

    papers = collections.defaultdict(lambda: collections.defaultdict(set))
    for r in rows:
        if r["status"] in ("confirmed", "claimed") and r["pmid"] in in_corpus:
            for cwid in bylines.get(r["pmid"], []):
                if cwid.lower() not in staff:
                    papers[cwid][core.core_id].add(r["pmid"])
    counts, totals, tenure, _ = ingest.affinity_inputs(engine, papers)
    core_pmids = {r["pmid"] for r in rows
                  if r["status"] in ("confirmed", "claimed") and r["pmid"] in in_corpus}
    corpus_size = ingest.fetch_corpus_size(engine)
    p0 = signals.affinity_base_rate(len(core_pmids), corpus_size)
    years = ingest.fetch_pub_years(engine, pmids)
    status_n = collections.Counter(r["status"] for r in rows)
    print(f"core {core.core_id} {core.name}: {len(rows)} rows {dict(status_n)}; "
          f"{len(papers)} non-staff authors in the numerator; shipped "
          f"{ {k: C.WEIGHTS[k] for k in PRE_REFIT} }, stored {stored_w}; "
          f"dictionary affinity_prior_strength={core.affinity_prior_strength} "
          f"(global {signals.AFFINITY_PRIOR_STRENGTH:g}); p0 = {len(core_pmids)}/{corpus_size} "
          f"= {p0:.5f}")

    groups = [(s, [r for r in rows if r["status"] == s])
              for s in ("candidate", "confirmed", "below_threshold")]
    decided = [r for r in rows if r["status"] in ("claimed", "rejected")]
    for strength in args.strengths:
        idx = signals.build_affinity_index(counts, totals, tenure=tenure, members=papers,
                                           prior_strength={core.core_id: strength},
                                           base_rate={core.core_id: p0})

        def rate(p):
            return signals.author_affinity(idx, bylines.get(p, []), core.core_id,
                                           years.get(p), pmid=p)
        print(f"\n=== prior_strength s={strength:g} (p0={p0:.5f}): "
              f"{len(idx.papers)} measurable authors")
        for name, group in groups:
            moves, visible = collections.Counter(), 0
            for r in group:
                st, p = rebanded(r, rate(r["pmid"]), stored_w)
                moves[f"{r['status']}->{st}"] += 1
                visible += st == "candidate" and r["status"] != "candidate" and p >= 0.40
            print(f"  {name:<16} n={len(group):<5} {dict(sorted(moves.items()))}"
                  f"  (new candidates >= 0.40: {visible})")
        pos = [rate(r["pmid"]) for r in decided if r["status"] == "claimed"]
        neg = [rate(r["pmid"]) for r in decided if r["status"] == "rejected"]
        print(f"  claimed({len(pos)}) vs rejected({len(neg)}): AUC rate {auc(pos, neg):.4f}, "
              f"bucket {auc([RANK[key(x)] for x in pos], [RANK[key(x)] for x in neg]):.4f}; "
              f"affinity > 0 on {sum(1 for x in pos if x)}/{len(pos)} claimed, "
              f"{sum(1 for x in neg if x)}/{len(neg)} rejected")
    return 0


if __name__ == "__main__":
    sys.exit(main())

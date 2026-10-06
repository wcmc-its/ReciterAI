#!/usr/bin/env python3
"""What a core's affinity knobs (prior strength s, minimum, soft threshold) do to its
live queue and its human labels.

READ-ONLY: one paginated DynamoDB Scan of the core's CORE#<id> rows plus reciterdb
SELECTs (bylines, corpus filter, per-year corpus totals, identity tenure, pub years).
Nothing is written, no Bedrock, no full text.

For each configuration — every combination of --strengths x --min-confirms x
--soft-threshold (each defaults to the core's current setting) — it rebuilds the affinity
index the way run_core builds it (non-staff authors of the core's confirmed + claimed
in-corpus papers, through ingest.affinity_inputs and signals.build_affinity_index,
numerator-only self-exclusion, tenure gate, shrinkage (n + s*p0) / (total + s) with p0 =
the core's confirmed + claimed in-corpus papers / the corpus size, then the gate: 0 below
the minimum, else x g(n) = n^h / (n^h + c^h)) and reports:

  * claimed vs rejected affinity AUC (rate and bucket) on the core's human-decided rows,
    the panel that says whether THIS core wants different knobs from the global default,
    and with --bootstrap N a paired, stratified bootstrap 95% CI of each configuration's
    AUC minus the FIRST configuration's (same resampled rows for both).
  * aff:* bucket membership of the claimed / rejected / open-candidate rows.
  * status moves: each stored row's logit with its stored `aff:*` term swapped for the
    recomputed one, re-banded with combine()'s thresholds and the LLM / staff holds.
    `--stored-weights` gives the aff:* weights the STORED rows were scored with (default
    0.79 / 3.43 / 4.93, what the live table holds until the next run rescores it);
    `--new-weights` the aff:* weights to re-band with (default combine.WEIGHTS — pass a
    configuration's own panel-B refit when it changes the global feature).
  * where an affinity-only paper lands: P(aff:<bucket> alone) against SPS's 0.40 display
    floor.

    python3 scripts/measure_affinity_gates.py --core 14 --min-confirms 1 3 \
        --soft-threshold 3 2 --soft-threshold off --bootstrap 2000

The numbers it printed for core 14 on 2026-10-06 are in config/core_dictionary.yaml there.
"""
from __future__ import annotations

import argparse
import collections
import logging
import math
import random
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


def soft_arg(values):
    """--soft-threshold C H | off -> (c, h) or None."""
    if values == ["off"]:
        return None
    if len(values) != 2:
        raise argparse.ArgumentTypeError("--soft-threshold takes C H, or off")
    return (float(values[0]), float(values[1]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--core", default="14")
    ap.add_argument("--strengths", type=float, nargs="+", default=None,
                    help="prior strengths s (default: the core's)")
    ap.add_argument("--min-confirms", type=int, nargs="+", default=None,
                    help="repeat-user minimums (default: the core's)")
    ap.add_argument("--soft-threshold", nargs="+", action="append", default=None,
                    metavar="C H | off", help="repeatable; default: the core's")
    ap.add_argument("--stored-weights", type=float, nargs=3, default=None,
                    metavar=("TRACE", "REGULAR", "CORE"),
                    help="aff:* weights the stored rows were scored with (default 0.79 3.43 4.93)")
    ap.add_argument("--new-weights", type=float, nargs=3, default=None,
                    metavar=("TRACE", "REGULAR", "CORE"),
                    help="aff:* weights to re-band with (default combine.WEIGHTS)")
    ap.add_argument("--bootstrap", type=int, default=0,
                    help="paired stratified bootstrap resamples for AUC CIs vs the first config")
    ap.add_argument("--seed", type=int, default=20261006)
    args = ap.parse_args(argv)
    logging.disable(logging.WARNING)
    stored_w = (dict(zip(PRE_REFIT, args.stored_weights)) if args.stored_weights
                else PRE_REFIT)
    if args.new_weights:
        C.WEIGHTS.update(zip(PRE_REFIT, args.new_weights))

    from utils.db import get_engine  # lazy
    from pipeline_cores.persist import get_curated_staff  # lazy
    engine = get_engine()
    core = load_core(args.core)
    strengths = args.strengths or [signals.affinity_prior_strength(core)]
    mins = args.min_confirms or [signals.affinity_min_confirms(core)]
    softs = ([soft_arg(v) for v in args.soft_threshold] if args.soft_threshold
             else [signals.affinity_soft_threshold(core)])
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
    new_w = {k: C.WEIGHTS[k] for k in PRE_REFIT}
    print(f"core {core.core_id} {core.name}: {len(rows)} rows {dict(status_n)}; "
          f"{len(papers)} non-staff authors in the numerator; re-band weights {new_w}, "
          f"stored {stored_w}; dictionary s={core.affinity_prior_strength} "
          f"min={core.affinity_min_confirms} soft={core.affinity_soft_threshold}; "
          f"p0 = {len(core_pmids)}/{corpus_size} = {p0:.5f}")
    alone = {k: 1 / (1 + math.exp(-(C.PRIOR_LOGIT + w))) for k, w in new_w.items()}
    print("  affinity-only P (prior + aff:* alone): "
          + ", ".join(f"{k} {v:.3f}{' (>= 0.40 SPS floor)' if v >= 0.40 else ''}"
                      for k, v in alone.items()))

    groups = [(s, [r for r in rows if r["status"] == s])
              for s in ("candidate", "confirmed", "below_threshold")]
    decided = [r for r in rows if r["status"] in ("claimed", "rejected")]
    is_pos = [r["status"] == "claimed" for r in decided]
    per_config = []
    for strength in strengths:
        for minimum in mins:
            for soft in softs:
                idx = signals.build_affinity_index(
                    counts, totals, tenure=tenure, members=papers,
                    prior_strength={core.core_id: strength}, base_rate={core.core_id: p0},
                    min_confirms={core.core_id: minimum}, soft_threshold={core.core_id: soft})
                rate = {r["pmid"]: signals.author_affinity(idx, bylines.get(r["pmid"], []),
                                                           core.core_id, years.get(r["pmid"]),
                                                           pmid=r["pmid"]) for r in rows}
                name = (f"s={strength:g} min={minimum} soft="
                        + (f"c{soft[0]:g}/h{soft[1]:g}" if soft else "off"))
                print(f"\n=== {name} (p0={p0:.5f}): {len(idx.papers)} measurable authors")
                for gname, group in groups:
                    moves, visible = collections.Counter(), 0
                    for r in group:
                        st, p = rebanded(r, rate[r["pmid"]], stored_w)
                        moves[f"{r['status']}->{st}"] += 1
                        visible += st == "candidate" and r["status"] != "candidate" and p >= 0.40
                    print(f"  {gname:<16} n={len(group):<5} {dict(sorted(moves.items()))}"
                          f"  (new candidates >= 0.40: {visible})")
                for gname in ("claimed", "rejected", "candidate"):
                    mem = collections.Counter(key(rate[r["pmid"]]) or "none"
                                              for r in rows if r["status"] == gname)
                    print(f"  buckets {gname:<10} {dict(sorted(mem.items()))}")
                vals = [rate[r["pmid"]] for r in decided]
                buck = [RANK[key(x)] for x in vals]
                per_config.append((name, vals, buck))
                pos = [x for x, t in zip(vals, is_pos) if t]
                neg = [x for x, t in zip(vals, is_pos) if not t]
                print(f"  claimed({len(pos)}) vs rejected({len(neg)}): AUC rate "
                      f"{auc(pos, neg):.4f}, bucket "
                      f"{auc([RANK[key(x)] for x in pos], [RANK[key(x)] for x in neg]):.4f}; "
                      f"affinity > 0 on {sum(1 for x in pos if x)}/{len(pos)} claimed, "
                      f"{sum(1 for x in neg if x)}/{len(neg)} rejected")
    if args.bootstrap and len(per_config) > 1:
        rng = random.Random(args.seed)
        P = [i for i, t in enumerate(is_pos) if t]
        N = [i for i, t in enumerate(is_pos) if not t]
        draws = [([rng.choice(P) for _ in P], [rng.choice(N) for _ in N])
                 for _ in range(args.bootstrap)]

        def sub(vals, pi, ni):
            return auc([vals[i] for i in pi], [vals[i] for i in ni])
        base = per_config[0]
        print(f"\npaired stratified bootstrap ({args.bootstrap} resamples of "
              f"{len(P)} claimed / {len(N)} rejected, seed {args.seed}) vs {base[0]}:")
        for name, vals, buck in per_config[1:]:
            for label, a, b in (("rate", vals, base[1]), ("bucket", buck, base[2])):
                d = sorted(sub(a, pi, ni) - sub(b, pi, ni) for pi, ni in draws)
                pt = sub(a, P, N) - sub(b, P, N)
                lo, hi = d[int(0.025 * len(d))], d[int(0.975 * len(d)) - 1]
                print(f"  {name} minus base [{label}]: {pt:+.4f}  95% CI [{lo:+.4f}, {hi:+.4f}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Did replacing the constants with log-odds actually buy spread, and did it cost accuracy?

The whole point of the evidence-weight change is that a ~3,000-paper pool per core
should occupy a continuum instead of clustering, so this measures the continuum:
distinct scores, decile histogram, min/median/max, and status mix — BEFORE (the
combine() on origin/main) against AFTER (this branch), on the same signals.

BEFORE is not paraphrased. It is `git show origin/main:pipeline_cores/combine.py`
exec'd into a throwaway module, so the comparison is against the real old code.

Two populations:
  --core 14   the live 347-candidate pool, read from DynamoDB. Its stored
              `likelihood` is the actual production number, so the reconstruction
              is checked against it rather than trusted.
  --labels    the 237 human-labelled imaging-core papers, which also carry a
              yes/no, so this is where AUC before vs after is measured. Spread is
              worthless if it came out of accuracy.

    python3 scripts/measure_evidence_spread.py
    python3 scripts/measure_evidence_spread.py --core 14
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import statistics
import subprocess
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from pipeline_cores import combine as new_combine  # noqa: E402
from pipeline_cores import ingest, signals  # noqa: E402
from pipeline_cores.dictionary import load_core  # noqa: E402
from pipeline_cores.fulltext import to_plain_text  # noqa: E402
from pipeline_cores.models import SignalResult  # noqa: E402
from scripts.fit_evidence_weights import (  # noqa: E402
    CONFIRMED, GROUND_TRUTH, LABELS, LABEL_CORE, LLM_SCORES, fetch_xml,
)


def origin_main_combine():
    """origin/main's combine(), exec'd from git — BEFORE is the real old code."""
    src = subprocess.check_output(
        ["git", "-C", str(REPO), "show", "origin/main:pipeline_cores/combine.py"], text=True)
    mod = types.ModuleType("combine_origin_main")
    exec(compile(src, "origin/main:pipeline_cores/combine.py", "exec"), mod.__dict__)  # noqa: S102
    return mod


# ---------------------------------------------------------------------------
def auc(scores: list, labels: list) -> float:
    """Rank-based AUC (Mann-Whitney), ties averaged. No dependency, 8 lines."""
    order = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks, i = [0.0] * len(scores), 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2.0 + 1.0
        i = j + 1
    pos = [r for r, y in zip(ranks, labels) if y]
    n_pos, n_neg = len(pos), len(labels) - len(pos)
    return (sum(pos) - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def report(name: str, before: list, after: list, statuses=None) -> None:
    print(f"\n{'=' * 74}\n{name}   n={len(before)}\n{'=' * 74}")
    print(f"  {'':<10}{'distinct':>10}{'min':>9}{'median':>9}{'max':>9}{'range':>9}")
    for label, xs in (("BEFORE", before), ("AFTER", after)):
        print(f"  {label:<10}{len(set(xs)):>10}{min(xs):>9.4f}{statistics.median(xs):>9.4f}"
              f"{max(xs):>9.4f}{max(xs) - min(xs):>9.4f}")
    print(f"\n  decile{'':<8}{'BEFORE':>10}{'AFTER':>10}")
    b = collections.Counter(min(int(x * 10), 9) for x in before)
    a = collections.Counter(min(int(x * 10), 9) for x in after)
    for d in range(10):
        bar_b, bar_a = "#" * round(30 * b[d] / len(before)), "#" * round(30 * a[d] / len(after))
        print(f"  {d / 10:.1f}-{(d + 1) / 10:.1f}{'':<5}{b[d]:>10}{a[d]:>10}   {bar_b:<31}|{bar_a}")
    if statuses:
        print(f"\n  status{'':<8}{'BEFORE':>10}{'AFTER':>10}")
        for key in ("confirmed", "candidate", "below_threshold"):
            print(f"  {key:<14}{statuses[0][key]:>10}{statuses[1][key]:>10}")


def status_counts(records) -> dict:
    c = collections.Counter(r.status for r in records)
    return {k: c[k] for k in ("confirmed", "candidate", "below_threshold")}


# ---------------------------------------------------------------------------
def core_pool(core_id: str, old):
    """The live candidate pool for one core, straight out of DynamoDB."""
    from utils.dynamodb_helpers import TABLE_NAME, get_dynamo_client  # lazy

    client = get_dynamo_client()
    kwargs = dict(TableName=TABLE_NAME, FilterExpression="SK = :sk",
                  ExpressionAttributeValues={":sk": {"S": f"CORE#{core_id}"}})
    items = []
    while True:
        resp = client.scan(**kwargs)
        items += resp.get("Items", [])
        if not resp.get("LastEvaluatedKey"):
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    pool = [i for i in items if i.get("status", {}).get("S") == "candidate"]
    print(f"core {core_id}: {len(items)} scored pairs in DynamoDB, {len(pool)} of them candidates")

    core = load_core(core_id)
    sigs = [SignalResult(
        coauthor_cwids=[c["S"] for c in i.get("signal_coauthors", {}).get("L", [])],
        ack_matched=bool(i.get("signal_ack", {}).get("BOOL")),
        ack_alias=i.get("ack_alias", {}).get("S", ""),
        ack_snippet=i.get("ack_snippet", {}).get("S", ""),
        llm_score=int(i["llm_score"]["N"]) if "llm_score" in i else None,
        author_affinity=float(i.get("author_affinity", {}).get("N", 0)),
    ) for i in pool]
    stored = [float(i["likelihood"]["N"]) for i in pool]

    # The stored likelihood is the production number; recomputing it from the
    # reconstructed signals is how we prove the reconstruction is faithful before
    # trusting the AFTER column.
    before = [old.combine(str(n), core_id, s).likelihood for n, s in enumerate(sigs)]
    drift = max(abs(a - b) for a, b in zip(stored, before))
    off = sum(1 for a, b in zip(stored, before) if abs(a - b) > 1e-9)
    print(f"  reconstruction check: max |stored - recomputed BEFORE| = {drift:.5f} "
          f"on {off}/{len(pool)} rows"
          + ("  (within the stored value's 4-dp rounding)" if drift <= 2e-4
             else "  <-- INVESTIGATE, the signals do not reproduce the stored score"))
    after = [new_combine.combine(str(n), core_id, s, core=core) for n, s in enumerate(sigs)]
    ack = sum(1 for s in sigs if s.ack_matched)
    print(f"  evidence present in this pool: ack {ack}, staff "
          f"{sum(1 for s in sigs if s.coauthor_cwids)}, llm {sum(1 for s in sigs if s.llm_score)}, "
          f"affinity {sum(1 for s in sigs if s.author_affinity)}")
    report(f"core {core_id} candidate pool", stored, [r.likelihood for r in after],
           statuses=({"confirmed": 0, "candidate": len(pool), "below_threshold": 0},
                     status_counts(after)))


# ---------------------------------------------------------------------------
def label_set(engine, old):
    """The 237 human-labelled imaging-core papers: spread AND accuracy."""
    labels = {r["pmid"]: r["label"] == "yes" for r in csv.DictReader(LABELS.open())}
    llm = json.loads(LLM_SCORES.read_text())
    core = load_core(LABEL_CORE)
    pmids = sorted(labels)

    coauthors = signals.coauthorship_index(engine, core, pmids)
    bylines = ingest.fetch_author_bylines(engine, pmids)
    outside = [str(p) for p in json.loads(CONFIRMED.read_text())[LABEL_CORE] if str(p) not in labels]
    outside = sorted(ingest.filter_corpus_pmids(engine, outside))   # same gate production uses
    counts: dict = collections.defaultdict(lambda: collections.defaultdict(int))
    for _pmid, cwids in ingest.fetch_author_bylines(engine, outside).items():
        for cwid in cwids:
            counts[cwid][LABEL_CORE] += 1
    # Same rate denominator production uses — an author's own corpus output — so the
    # AFTER column is scored on the feature the shipped weights were fitted against.
    index = signals.build_affinity_index(counts, ingest.fetch_author_totals(engine, list(counts)))
    xml = fetch_xml(pmids)

    sigs = []
    for pmid in pmids:
        sig = signals.acknowledgement_signal(to_plain_text(xml.get(pmid, "")), core,
                                             xml=xml.get(pmid, ""))
        sig.coauthor_cwids = coauthors.get(pmid, [])
        sig.llm_score = llm.get(pmid, {}).get("score")
        sig.author_affinity = signals.author_affinity(index, bylines.get(pmid, []), LABEL_CORE)
        sigs.append(sig)

    truth = [labels[p] for p in pmids]
    before = [old.combine(p, LABEL_CORE, s) for p, s in zip(pmids, sigs)]
    after = [new_combine.combine(p, LABEL_CORE, s, core=core) for p, s in zip(pmids, sigs)]
    print(f"  full text: {sum(1 for p in pmids if xml.get(p))}/{len(pmids)}; "
          f"ack matched {sum(1 for s in sigs if s.ack_matched)}, staff "
          f"{sum(1 for s in sigs if s.coauthor_cwids)}, affinity "
          f"{sum(1 for s in sigs if s.author_affinity)}")
    report("237 labelled imaging-core papers",
           [r.likelihood for r in before], [r.likelihood for r in after],
           statuses=(status_counts(before), status_counts(after)))
    print(f"\n  AUC vs the human labels   BEFORE {auc([r.likelihood for r in before], truth):.4f}"
          f"   AFTER {auc([r.likelihood for r in after], truth):.4f}")
    print(f"  (the LLM score alone, for reference: "
          f"{auc([s.llm_score or 0 for s in sigs], truth):.4f})")


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--core", help="also measure this core's live candidate pool (e.g. 14)")
    ap.add_argument("--skip-labels", action="store_true")
    args = ap.parse_args(argv)

    old = origin_main_combine()
    print(f"BEFORE = origin/main combine(): ack={old._ACK_LIKELIHOOD}, "
          f"staff={old._STAFF_COAUTHOR_LIKELIHOOD}, else noisy_or, threshold "
          f"{old.DEFAULT_TRIAGE_THRESHOLD}")
    print(f"AFTER  = this branch: prior {new_combine.PRIOR_LOGIT:.2f} + sum(weights), confirm "
          f">= {new_combine.DEFAULT_CONFIRM_THRESHOLD}, candidate >= "
          f"{new_combine.DEFAULT_TRIAGE_THRESHOLD}\n  ground truth: {GROUND_TRUTH}")

    if args.core:
        core_pool(args.core, old)
    if not args.skip_labels:
        from utils.db import get_engine  # lazy
        label_set(get_engine(), old)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

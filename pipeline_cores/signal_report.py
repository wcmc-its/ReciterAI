"""Per-signal breakdown of a run's surfaced (publication, core) pairs (`run.py --signal-report`).

For each core: how often each signal family FIRES on its confirmed/candidate pairs, and
how often it is DECISIVE, i.e. knocking that one family out (and re-scoring through the
real `combine`, so per-core thresholds and the staff/LLM confirmation hold all apply)
drops the pair a status. "sole" counts confirmed pairs where it is the only decisive
family. A family can fire everywhere and decide nothing (redundant evidence) or fire
rarely and carry the core; that difference is what tuning aliases and synopses needs.

Read-only: it re-scores in memory and never touches DynamoDB. Rows go to stdout as a
fixed-width table; `path` additionally gets one CSV row per surfaced pair.
"""
from __future__ import annotations

import csv
from collections import Counter, defaultdict
from dataclasses import replace

from pipeline_cores import combine as _combine
from pipeline_cores.models import STATUS_BELOW, STATUS_CANDIDATE, STATUS_CONFIRMED

# family -> (fires?, knock-out). Mirrors combine.evidence_features plus the LLM line.
FAMILIES = {
    "ack": (lambda s: s.ack_matched, lambda s: replace(s, ack_matched=False)),
    "staff": (lambda s: bool(s.coauthor_cwids), lambda s: replace(s, coauthor_cwids=[])),
    "affinity": (lambda s: s.author_affinity > 0, lambda s: replace(s, author_affinity=0.0)),
    "llm": (lambda s: bool(s.llm_score), lambda s: replace(s, llm_score=None)),
    "method": (lambda s: bool(s.method_tier), lambda s: replace(s, method_tier="")),
    "mesh": (lambda s: bool(s.mesh_evidence), lambda s: replace(s, mesh_evidence=[])),
    "client": (lambda s: bool(s.client_cwids), lambda s: replace(s, client_cwids=[])),
}
_RANK = {STATUS_BELOW: 0, STATUS_CANDIDATE: 1, STATUS_CONFIRMED: 2}


def analyse(rec, core, threshold=None) -> dict:
    """{"fires": [...], "decisive": [...]} for one surfaced record."""
    fires, decisive = [], []
    for name, (fired, knock) in FAMILIES.items():
        if not fired(rec.signals):
            continue
        fires.append(name)
        alt = _combine.combine(rec.pmid, rec.core_id, knock(rec.signals), core=core,
                               triage_threshold=threshold)
        if _RANK[alt.status] < _RANK[rec.status]:
            decisive.append(name)
    return {"fires": fires, "decisive": decisive}


def report(records, cores, *, threshold=None, path: str = None) -> dict:
    """Print the per-core table and return {core_id: {family: Counter}} for tests."""
    by_id = {c.core_id: c for c in cores}
    out = defaultdict(lambda: defaultdict(Counter))
    rows = []
    for rec in records:
        if rec.status not in (STATUS_CONFIRMED, STATUS_CANDIDATE):
            continue
        a = analyse(rec, by_id.get(rec.core_id), threshold)
        st = rec.status
        out[rec.core_id]["_n"][st] += 1
        for f in a["fires"]:
            out[rec.core_id][f][f"{st}.fires"] += 1
        for f in a["decisive"]:
            out[rec.core_id][f][f"{st}.decisive"] += 1
        if st == STATUS_CONFIRMED and len(a["decisive"]) == 1:
            out[rec.core_id][a["decisive"][0]]["confirmed.sole"] += 1
        if st == STATUS_CONFIRMED and not a["decisive"]:
            out[rec.core_id]["_n"]["confirmed.redundant"] += 1
        rows.append((rec.core_id, rec.pmid, st, rec.likelihood, ";".join(a["fires"]),
                     ";".join(a["decisive"]), rec.signals.ack_alias))

    print("SIGNAL REPORT  conf/cand = surfaced pairs; per family: fires / decisive (sole) "
          "on confirmed | fires / decisive on candidates")
    for core_id in sorted(out, key=lambda k: (len(k), k)):
        n = out[core_id]["_n"]
        name = getattr(by_id.get(core_id), "name", "")
        print(f"[{core_id} {name}] confirmed={n['confirmed']} candidate={n['candidate']} "
              f"confirmed-with-no-single-decisive-family={n['confirmed.redundant']}")
        for fam in FAMILIES:
            c = out[core_id].get(fam)
            if not c:
                continue
            print(f"    {fam:<9} conf {c['confirmed.fires']:>5} / {c['confirmed.decisive']:>5} "
                  f"({c['confirmed.sole']:>5})  |  cand {c['candidate.fires']:>5} / "
                  f"{c['candidate.decisive']:>5}")
    if path:
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["core_id", "pmid", "status", "likelihood", "fires", "decisive", "ack_alias"])
            w.writerows(rows)
        print(f"signal report: {len(rows)} rows -> {path}")
    return out

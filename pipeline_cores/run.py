"""Cores inference entry point: ingest -> signals -> combine -> persist.

    python3 -m pipeline_cores.run --core 2 --test 200 --dry-run
    python3 -m pipeline_cores.run --core 2 --with-llm        # full run (Bedrock + DynamoDB)

Signals layer in by cost/precision:
  coauthorship (free, deterministic) + acknowledgement (full text, deterministic)
  confirm; author-affinity + LLM triage only rank the candidate queue.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collections import defaultdict

from pipeline_cores import combine as _combine
from pipeline_cores import ingest, signals
from pipeline_cores.dictionary import load_core, load_cores
from pipeline_cores.models import STATUS_CONFIRMED


def _make_fulltext_loader(enabled: bool, *, use_s3: bool = False):
    """Return a pmid -> plain-text body callable.

    With --with-fulltext, backs onto the cached PMC client (signal 3 live);
    --fulltext-s3 additionally consults/fills the shared S3 cache so the run
    rides a warm cache instead of re-fetching from NCBI. Otherwise returns ""
    for every pmid, leaving the acknowledgement signal silent while the rest of
    the pipeline runs.
    """
    if not enabled:
        return lambda _pmid: ""
    from pipeline_cores.fulltext import PmcFullTextClient  # lazy
    client = PmcFullTextClient.with_s3() if use_s3 else PmcFullTextClient()
    return client.get


def load_prior_user_counts(core_id: str, bylines: dict, *, enabled: bool) -> dict:
    """cwid -> {core_id: n_confirmed_papers} from prior confirmed/claimed records.

    Scans DynamoDB for prior confirmed/claimed (pub, core) rows and attributes
    each to its byline authors (the cross-run repeat-user prior). `bylines` maps
    pmid -> [cwid]; a prior pmid outside this run's byline set contributes nothing
    (its authors aren't in scope this run). Empty when disabled or on first run.
    """
    counts: dict = defaultdict(lambda: defaultdict(int))
    if not enabled:
        return counts
    from pipeline_cores.persist import scan_prior_core_usage  # lazy
    for rec in scan_prior_core_usage(core_id):
        for cwid in bylines.get(rec["pmid"], []):
            counts[cwid][rec["core_id"]] += 1
    return counts


def run_core(core, pubs, *, bedrock=None, full_text=None, threshold, scored_at,
             engine, prior_user_counts=None):
    """Two-phase: deterministic+LLM signals, then the repeat-user affinity prior.

    Phase 1 builds each record from acknowledgement / co-authorship / LLM. Phase 2
    aggregates this run's confirmations (+ prior_user_counts) to author level and
    re-scores the not-yet-confirmed records with the affinity prior. Confirmed
    records are untouched (already at ceiling)."""
    full_text = full_text or (lambda _pmid: "")
    pmids = [p["pmid"] for p in pubs]
    coauthors = signals.coauthorship_index(engine, core, pmids)
    bylines = ingest.fetch_author_bylines(engine, pmids)
    llm_scores = signals.llm_triage(bedrock, core, pubs) if bedrock else {}

    # Phase 1 — deterministic + LLM signals.
    sigs, records = {}, []
    for pub in pubs:
        pmid = pub["pmid"]
        sig = signals.acknowledgement_signal(full_text(pmid), core)
        sig.coauthor_cwids = coauthors.get(pmid, [])
        tri = llm_scores.get(pmid)
        if tri:
            sig.llm_score = tri["score"]
            sig.llm_rationale = tri.get("rationale", "")
        sigs[pmid] = sig
        records.append(_combine.combine(pmid, core.core_id, sig, scored_at=scored_at, triage_threshold=threshold))

    # Phase 2 — repeat-user affinity. Count confirmed papers per author (this run
    # + prior), build the affinity index, and re-score non-confirmed records.
    counts = defaultdict(lambda: defaultdict(int))
    for cwid, by_core in (prior_user_counts or {}).items():
        for cid, n in by_core.items():
            counts[cwid][cid] += n
    for rec in records:
        if rec.status == STATUS_CONFIRMED:
            for cwid in bylines.get(rec.pmid, []):
                counts[cwid][core.core_id] += 1
    affinity_index = signals.build_affinity_index(counts)

    out = []
    for rec in records:
        if rec.status == STATUS_CONFIRMED:
            out.append(rec)
            continue
        sig = sigs[rec.pmid]
        sig.author_affinity = signals.author_affinity(affinity_index, bylines.get(rec.pmid, []), core.core_id)
        out.append(_combine.combine(rec.pmid, core.core_id, sig, scored_at=scored_at, triage_threshold=threshold))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="WCM core-facility usage inference")
    ap.add_argument("--core", help="core_id to run (default: all in dictionary)")
    ap.add_argument("--test", type=int, help="limit to N publications")
    ap.add_argument("--with-llm", action="store_true", help="enable Bedrock triage (signal 4)")
    ap.add_argument("--with-fulltext", action="store_true", help="enable PMC acknowledgement match (signal 3)")
    ap.add_argument("--fulltext-s3", action="store_true",
                    help="back the full-text cache with the shared S3 cache (warm with -m pipeline_cores.prefetch_fulltext)")
    ap.add_argument("--with-affinity", action="store_true",
                    help="seed the repeat-user prior from prior DynamoDB confirmations (signal 1 cross-run)")
    ap.add_argument("--dry-run", action="store_true", help="do not write to DynamoDB")
    ap.add_argument("--threshold", type=float, default=_combine.DEFAULT_TRIAGE_THRESHOLD)
    args = ap.parse_args(argv)

    from utils.db import get_engine  # lazy
    from utils.iso_clock import now_iso  # lazy

    cores = [load_core(args.core)] if args.core else load_cores()
    engine = get_engine()
    pubs = ingest.fetch_publications(engine, limit=args.test)
    bedrock = None
    if args.with_llm:
        from utils.bedrock_client import BedrockClient  # lazy
        bedrock = BedrockClient()

    full_text = _make_fulltext_loader(args.with_fulltext, use_s3=args.fulltext_s3)
    scored_at = now_iso()
    # Bylines once per run (shared across cores) to attribute prior confirmations.
    bylines = ingest.fetch_author_bylines(engine, [p["pmid"] for p in pubs])
    all_records = []
    for core in cores:
        prior_counts = load_prior_user_counts(core.core_id, bylines, enabled=args.with_affinity)
        recs = run_core(core, pubs, bedrock=bedrock, full_text=full_text,
                        threshold=args.threshold, scored_at=scored_at, engine=engine,
                        prior_user_counts=prior_counts)
        confirmed = sum(1 for r in recs if r.status == "confirmed")
        candidates = sum(1 for r in recs if r.status == "candidate")
        print(f"[{core.core_id} {core.name}] {len(recs)} pubs -> {confirmed} confirmed, {candidates} candidates")
        all_records.extend(recs)

    surfaced = [r for r in all_records if r.status in ("confirmed", "candidate")]
    if args.dry_run:
        print(f"DRY RUN — {len(surfaced)} records would be written (PUB#/CORE# in '{__doc__ and 'reciterai'}').")
    else:
        from pipeline_cores.persist import put_core_usage  # lazy
        print(f"wrote {put_core_usage(surfaced)} (publication, core) records to DynamoDB")


if __name__ == "__main__":
    main()

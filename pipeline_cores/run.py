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

from pipeline_cores import combine as _combine
from pipeline_cores import ingest, signals
from pipeline_cores.dictionary import load_core, load_cores
from pipeline_cores.models import SignalResult


def _make_fulltext_loader(enabled: bool):
    """Return a pmid -> plain-text body callable.

    With --with-fulltext, backs onto the cached PMC client (signal 3 live).
    Otherwise returns "" for every pmid, leaving the acknowledgement signal
    silent while the rest of the pipeline runs.
    """
    if not enabled:
        return lambda _pmid: ""
    from pipeline_cores.fulltext import PmcFullTextClient  # lazy
    return PmcFullTextClient().get


def load_confirmed_pairs(core_id: str) -> dict:
    """cwid -> {core_id: affinity} from prior confirmed runs + SPS claims.

    TODO(affinity): read back confirmed/claimed (pub, core) records and aggregate
    to author level (the repeat-user prior). Empty until wired -> affinity 0.
    """
    return {}


def run_core(core, pubs, *, bedrock=None, full_text=None, threshold, scored_at, engine):
    full_text = full_text or (lambda _pmid: "")
    pmids = [p["pmid"] for p in pubs]
    coauthors = signals.coauthorship_index(engine, core, pmids)
    confirmed = load_confirmed_pairs(core.core_id)
    llm_scores = signals.llm_triage(bedrock, core, pubs) if bedrock else {}

    records = []
    for pub in pubs:
        pmid = pub["pmid"]
        sig = signals.acknowledgement_signal(full_text(pmid), core)
        sig.coauthor_cwids = coauthors.get(pmid, [])
        tri = llm_scores.get(pmid)
        if tri:
            sig.llm_score = tri["score"]
            sig.llm_rationale = tri.get("rationale", "")
        # byline CWIDs for affinity would come from the author mapping; reuse the
        # staff hits as a stand-in until the full byline read is wired.
        sig.author_affinity = signals.author_affinity(confirmed, sig.coauthor_cwids, core.core_id)
        records.append(_combine.combine(pmid, core.core_id, sig, scored_at=scored_at, triage_threshold=threshold))
    return records


def main(argv=None):
    ap = argparse.ArgumentParser(description="WCM core-facility usage inference")
    ap.add_argument("--core", help="core_id to run (default: all in dictionary)")
    ap.add_argument("--test", type=int, help="limit to N publications")
    ap.add_argument("--with-llm", action="store_true", help="enable Bedrock triage (signal 4)")
    ap.add_argument("--with-fulltext", action="store_true", help="enable PMC acknowledgement match (signal 3)")
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

    full_text = _make_fulltext_loader(args.with_fulltext)
    scored_at = now_iso()
    all_records = []
    for core in cores:
        recs = run_core(core, pubs, bedrock=bedrock, full_text=full_text,
                        threshold=args.threshold, scored_at=scored_at, engine=engine)
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

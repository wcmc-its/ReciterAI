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
from pipeline_cores import ingest, pmc_search, signals
from pipeline_cores.dictionary import load_core, load_cores
from pipeline_cores.models import STATUS_CONFIRMED

# Triage Bedrock calls are tiny generations (a screen int / a short JSON), so a
# 120s read timeout is ample headroom while bounding a hung connection far below
# the BedrockClient 900s default that suits long overview/biosketch generations.
_TRIAGE_READ_TIMEOUT = 120


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


def load_prior_user_counts(core_id: str, bylines: dict, *, enabled: bool, engine=None) -> dict:
    """cwid -> {core_id: n_confirmed_papers} from prior confirmed/claimed records.

    Scans DynamoDB for prior confirmed/claimed (pub, core) rows and attributes
    each to its byline authors (the cross-run repeat-user prior).

    A prior paper is NOT necessarily in this run's `bylines` — always so for a
    `--pmids-file` run, and possible in a full run for a paper that has since
    left the corpus. Its bylines are fetched on demand rather than treated as
    empty; skipping them silently zeroes the whole prior, which is a wrong
    answer that looks like a working run (a scoped core-14 pool scored 0
    candidates instead of 404 before this).
    """
    counts: dict = defaultdict(lambda: defaultdict(int))
    if not enabled:
        return counts
    from pipeline_cores.persist import scan_prior_core_usage  # lazy
    recs = list(scan_prior_core_usage(core_id))
    # Gate the NUMERATOR to the same corpus as the denominator. DynamoDB holds
    # confirmed/claimed rows for papers this pipeline does not score, and counting
    # those against a corpus-restricted total inflates the rate into the strongest
    # bucket — 19 of core 14's 43 prior rows are outside the corpus.
    if engine is not None:
        in_corpus = ingest.filter_corpus_pmids(engine, {r["pmid"] for r in recs})
        recs = [r for r in recs if r["pmid"] in in_corpus]
    missing = sorted({r["pmid"] for r in recs} - set(bylines))
    if missing and engine is not None:
        bylines = {**bylines, **ingest.fetch_author_bylines(engine, missing)}
    for rec in recs:
        for cwid in bylines.get(rec["pmid"], []):
            counts[cwid][rec["core_id"]] += 1
    return counts


def run_core(core, pubs, *, bedrock=None, full_text=None, threshold, scored_at,
             engine, prior_user_counts=None, screen_map=None, llm_workers=8):
    """Two-phase: deterministic+LLM signals, then the repeat-user affinity prior.

    Phase 1 builds each record from acknowledgement / co-authorship / LLM. Phase 2
    aggregates this run's confirmations (+ prior_user_counts) to author level, divides
    each author's count by their TOTAL corpus output to get the affinity rate, and
    re-scores the not-yet-confirmed records with it. Confirmed records are untouched
    (already at ceiling)."""
    full_text = full_text or (lambda _pmid: "")
    pmids = [p["pmid"] for p in pubs]
    coauthors = signals.coauthorship_index(engine, core, pmids)
    bylines = ingest.fetch_author_bylines(engine, pmids)
    llm_scores = signals.llm_triage(bedrock, core, pubs, screen_map=screen_map,
                                    max_workers=llm_workers) if bedrock else {}

    # Curated clients, intersected with each byline below. Asserted rather than
    # inferred, so this fires on a core with zero prior confirmations — the case the
    # phase-2 affinity prior cannot reach.
    clients = set(core.clients)  # already lowercased/stripped by load_cores

    # Phase 1 — deterministic + LLM signals.
    sigs, records = {}, []
    for pub in pubs:
        pmid = pub["pmid"]
        sig = signals.acknowledgement_signal(full_text(pmid), core)
        sig.coauthor_cwids = coauthors.get(pmid, [])
        # Lowercased on both sides: CWID casing is not stable across sources (SPS
        # compares them case-insensitively throughout for the same reason), and the
        # curated list is hand-typed.
        sig.client_cwids = [cwid for cwid in bylines.get(pmid, []) if cwid.lower() in clients]
        tri = llm_scores.get(pmid)
        if tri:
            sig.llm_score = tri["score"]
            sig.llm_rationale = tri.get("rationale", "")
        sigs[pmid] = sig
        records.append(_combine.combine(pmid, core.core_id, sig, scored_at=scored_at,
                                        core=core, triage_threshold=threshold))

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
    # The rate's denominator, for the authors that actually have confirmations. Scoped
    # to those cwids rather than the whole corpus: it is the same number either way,
    # and this run may only be scoring a pool.
    author_totals = ingest.fetch_author_totals(engine, list(counts))
    affinity_index = signals.build_affinity_index(counts, author_totals)

    out = []
    for rec in records:
        if rec.status == STATUS_CONFIRMED:
            out.append(rec)
            continue
        sig = sigs[rec.pmid]
        sig.author_affinity = signals.author_affinity(affinity_index, bylines.get(rec.pmid, []), core.core_id)
        out.append(_combine.combine(rec.pmid, core.core_id, sig, scored_at=scored_at,
                                    core=core, triage_threshold=threshold))
    return out


def read_pmids_file(path: str) -> list:
    """PMIDs from a newline-separated file; blanks and `#` comments ignored.

    Exists so an expensive signal can re-score an EXISTING candidate pool
    instead of the whole corpus: running the LLM over 80k publications to rank
    404 of them is the wrong trade. (The cheap signals no longer leave the pool
    unrankable on their own — the affinity RATE took core 14's 347 rows from 6
    distinct values to 39 — but the LLM is still the resolution that ranks it.)
    """
    out = []
    with open(path) as fh:
        for line in fh:
            tok = line.split("#", 1)[0].strip()
            if tok:
                out.append(tok)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="WCM core-facility usage inference")
    ap.add_argument("--core", help="core_id to run (default: all in dictionary)")
    ap.add_argument("--test", type=int, help="limit to N publications")
    ap.add_argument("--pmids-file",
                    help="restrict scoring to these PMIDs (one per line, # comments ok). "
                         "For RE-SCORING an existing candidate pool with an expensive signal "
                         "(e.g. --with-llm over a core's candidates) rather than the corpus. "
                         "NOTE: a scoped run writes only these rows, so it can neither surface "
                         "a pair outside the set nor demote one — never use it to GENERATE a pool")
    ap.add_argument("--with-llm", action="store_true", help="enable Bedrock triage (signal 4)")
    ap.add_argument("--llm-workers", type=int, default=8,
                    help="concurrent Bedrock triage workers (default 8; lower if Bedrock throttles)")
    ap.add_argument("--with-fulltext", action="store_true", help="enable PMC acknowledgement match (signal 3)")
    ap.add_argument("--alias-search", action="store_true",
                    help="signal 3 WITHOUT the corpus prefetch: ask PMC which papers name each "
                         "alias (one esearch per alias), then fetch full text for those PMIDs "
                         "only. Same coverage as --with-fulltext, ~25 fetches instead of 80k. "
                         "Acronym aliases are skipped (esearch has no case-sensitive mode)")
    ap.add_argument("--fulltext-s3", action="store_true",
                    help="back the full-text cache with the shared S3 cache (warm with -m pipeline_cores.prefetch_fulltext)")
    ap.add_argument("--with-affinity", action="store_true",
                    help="seed the repeat-user prior from prior DynamoDB confirmations (signal 1 cross-run)")
    ap.add_argument("--all-cores-screen", action="store_true",
                    help="EXPERIMENTAL: one Haiku call per pub screens all cores at once (13x fewer "
                         "screen calls) but lost screen recall on the 237-pilot (TP regressions collapse "
                         "to score 1) — OFF by default until re-calibrated to per-core parity")
    ap.add_argument("--dry-run", action="store_true", help="do not write to DynamoDB")
    ap.add_argument("--threshold", type=float, default=None,
                    help="override the candidate threshold for every core (default: the "
                         "core's own triage_threshold, else combine.DEFAULT_TRIAGE_THRESHOLD)")
    args = ap.parse_args(argv)

    from utils.db import get_engine  # lazy
    from utils.iso_clock import now_iso  # lazy

    cores = [load_core(args.core)] if args.core else load_cores()
    engine = get_engine()
    pool = read_pmids_file(args.pmids_file) if args.pmids_file else None
    pubs = ingest.fetch_publications(engine, pmids=pool, limit=args.test)
    if pool is not None:
        print(f"scoped run: {len(pubs)} of {len(pool)} requested PMIDs are in the corpus")
    bedrock = None
    if args.with_llm:
        from utils.bedrock_client import BedrockClient  # lazy
        bedrock = BedrockClient(read_timeout=_TRIAGE_READ_TIMEOUT)

    full_text = _make_fulltext_loader(args.with_fulltext, use_s3=args.fulltext_s3)
    scored_at = now_iso()
    # Bylines once per run (shared across cores) to attribute prior confirmations.
    bylines = ingest.fetch_author_bylines(engine, [p["pmid"] for p in pubs])
    # Default: the per-core Haiku screen inside each run_core (calibration-validated
    # 100% recall@SCREEN_CUTOFF on the 237-pilot). The all-cores screen (one call per
    # pub) cuts screen calls ~13x but lost 7-11% screen recall on that pilot — its
    # borderline true positives collapse to score 1 — so it is opt-in (--all-cores-screen)
    # until re-calibrated to parity. See pipeline_cores/README.md.
    screen_map = signals.screen_all_cores(bedrock, cores, pubs) if (bedrock and args.all_cores_screen) else None
    # Affinity prior: one Scan grouped by core_id in memory, not one Scan per
    # core. The byline attribution keys on each row's own core_id, and each
    # run_core reads only its own core's slice — so passing the whole prior is
    # identical to a per-core filtered scan, at 1/len(cores) the table reads.
    prior_counts = load_prior_user_counts(args.core, bylines, enabled=args.with_affinity, engine=engine)
    all_records = []
    for core in cores:
        # --alias-search is per-core (each core has its own aliases), unlike the
        # shared corpus loader above.
        core_full_text = (
            pmc_search.make_alias_loader(core, use_s3=args.fulltext_s3)
            if args.alias_search else full_text
        )
        recs = run_core(core, pubs, bedrock=bedrock, full_text=core_full_text,
                        threshold=args.threshold, scored_at=scored_at, engine=engine,
                        prior_user_counts=prior_counts, screen_map=screen_map,
                        llm_workers=args.llm_workers)
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

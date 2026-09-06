"""Cores inference entry point: ingest -> signals -> combine -> persist.

    python3 -m pipeline_cores.run --core 2 --test 200 --dry-run
    python3 -m pipeline_cores.run --core 2 --with-llm        # full run (Bedrock + DynamoDB)
    python3 -m pipeline_cores.run --core 14 --llm-carry-forward   # nightly: ZERO Bedrock calls,
                                                                  # stored LLM evidence preserved

Signals layer in by cost/precision:
  coauthorship (free, deterministic) + acknowledgement (full text, deterministic)
  confirm; author-affinity + LLM triage only rank the candidate queue.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collections import Counter, defaultdict

from pipeline_cores import combine as _combine
from pipeline_cores import ingest, method_families, pmc_search, prefilter, signals
from pipeline_cores.dictionary import load_core, load_cores
from pipeline_cores.models import METHOD_FAMILY_TIERS, STATUS_CONFIRMED

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


def load_prior_user_pmids(core_id: str, bylines: dict, *, enabled: bool, engine=None) -> dict:
    """cwid -> {core_id: {pmid, ...}} from prior confirmed/claimed records.

    Scans DynamoDB for prior confirmed/claimed (pub, core) rows and attributes
    each to its byline authors (the cross-run repeat-user prior).

    SETS OF PMIDS, not counts, and that is the point (#391). This used to return
    `{cwid: {core_id: n}}`, which `run_core` phase 2 then ADDED this run's confirmations
    to — and on a corpus-wide run those are largely the SAME PAPERS, since a pair
    confirmed by a previous run is confirmed again by this one. Every such paper was
    credited twice to every author on its byline, doubling the numerator of a rate whose
    denominator is a real paper count. `build_affinity_index` only warns when the
    doubled value exceeds the author's corpus total (2 authors on the 2026-09-05
    full-corpus run); everyone still under their total was inflated SILENTLY, and
    `aff:core` (rate >= 0.70, +4.93 nats) clears DEFAULT_CONFIRM_THRESHOLD on its own —
    so an inflated author's next paper auto-confirms with no acknowledgement, no staff
    co-author and no LLM score behind it. A set unions instead of adding, so the same
    paper arriving from both sources counts once by construction rather than by a
    subtraction someone has to remember to keep correct.

    A prior paper is NOT necessarily in this run's `bylines` — always so for a
    `--pmids-file` run, and possible in a full run for a paper that has since
    left the corpus. Its bylines are fetched on demand rather than treated as
    empty; skipping them silently zeroes the whole prior, which is a wrong
    answer that looks like a working run (a scoped core-14 pool scored 0
    candidates instead of 404 before this).
    """
    pmids: dict = defaultdict(lambda: defaultdict(set))
    if not enabled:
        return pmids
    from pipeline_cores.persist import scan_prior_core_usage  # lazy
    # strict=True because THIS caller writes. scan_prior_core_usage degrades to [] for
    # its two analysis callers, where a missing prior only costs a worse report — but
    # here an empty prior is persisted: author_affinity becomes 0.0, build_core_item
    # omits it, put_core_usage sweeps it into REMOVE, and the row's status demotes
    # confirmed -> candidate. A throttled Scan would quietly rewrite the queue. On a
    # nightly that is not a degraded run, it is a wrong one; fail and let it re-run.
    recs = list(scan_prior_core_usage(core_id, strict=True))
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
            pmids[cwid][rec["core_id"]].add(rec["pmid"])
    return pmids


def run_core(core, pubs, *, bedrock=None, full_text=None, threshold, scored_at,
             engine, prior_user_pmids=None, screen_map=None, llm_workers=8,
             dry_run: bool = False, carry_forward: dict = None, family_index: dict = None,
             # TRAP: mesh_index=None records NO mesh_evidence, and mesh_evidence is in
             # _OWNED_ATTRS — a second caller that forgets this kwarg makes put_core_usage
             # REMOVE it from every row an earlier run wrote. One caller today: main().
             mesh_index: dict = None):
    """Two-phase: deterministic+LLM signals, then the repeat-user affinity prior.

    Phase 1 builds each record from acknowledgement / co-authorship / LLM. Phase 2
    aggregates this run's confirmations (+ prior_user_pmids) to author level, divides
    each author's count by their TOTAL corpus output to get the affinity rate, and
    re-scores the not-yet-confirmed records with it. Confirmed records are untouched
    (already at ceiling).

    dry_run mirrors main()'s --dry-run (README: "no AWS needed" for a dry run) —
    so the curated-client read, like the final write, is skipped under
    --dry-run (note --with-affinity still scans DynamoDB for the prior).

    carry_forward is THIS core's slice of the stored LLM evidence
    ({pmid: {"score", "rationale"}}, from persist.scan_core_llm_scores) and None means
    --llm-carry-forward is off. None and {} are different answers and must not be
    conflated: {} is the flag ON for a core that simply has nothing stored yet, and it
    still takes the carry-forward path — with bedrock that triages every pub (correctly,
    there is nothing to reuse), without bedrock it prints "0 carried forward" so an
    operator can see the core is empty rather than guess the flag failed.

    family_index is main()'s once-per-run A2 method-family index (26 MB of JSON) or
    None when --with-method-families is off. Looking a pmid up in it is a dict access,
    so phase 1 asks for every pub either way and an absent index simply answers with
    nothing.

    mesh_index is the same shape for MeSH descriptors
    (prefilter.core_mesh_tree_descriptors), but built PER CORE — the E-tree prefixes are
    per-core, so there is nothing to share across cores — and there is deliberately NO
    FLAG guarding it: main() always builds it. That is the answer to the trap the method
    families had to document instead: put_core_usage REMOVEs every _OWNED_ATTRS
    attribute a run did not produce, so an optional-by-flag attribute strips itself off
    rows a previous run wrote. Always-on cannot do that, and it costs nothing to be
    always-on here — one indexed reciterdb query per core with mapped prefixes, zero
    queries for the 5 cores without, no S3 and no Bedrock. A caller that passes nothing
    (the tests, today) simply records no descriptors."""
    full_text = full_text or (lambda _pmid: "")
    pmids = [p["pmid"] for p in pubs]
    coauthors = signals.coauthorship_index(engine, core, pmids)
    bylines = ingest.fetch_author_bylines(engine, pmids)
    if carry_forward is None:
        llm_scores = signals.llm_triage(bedrock, core, pubs, screen_map=screen_map,
                                        max_workers=llm_workers) if bedrock else {}
        # Printed on BOTH branches on purpose. The failure the runbook has to catch is
        # --llm-carry-forward going missing from the task-def command, and if this line
        # only existed on the other branch that failure would print nothing at all — a
        # missing line in `logs tail --follow` is far easier to miss than a 0.
        print(f"[{core.core_id} {core.name}] llm: carry-forward OFF, "
              f"{len(pubs) if bedrock else 0} triaged")
    else:
        # Triage only what has no stored score, then merge the stored entries back in.
        # Without bedrock this is the zero-Bedrock nightly: nothing is triaged and the
        # merge alone is what keeps llm_score/llm_rationale on the row, because
        # put_core_usage REMOVES every owned optional the run did not produce (#384).
        # str() on both sides: the lookup at the bottom of phase 1 keys on the raw
        # pub["pmid"], so a caller passing int pmids would exclude every pub from `todo`
        # AND miss every carry-forward lookup — nothing triaged, nothing carried, and
        # put_core_usage REMOVEs the evidence while this line still reports it carried.
        # ingest.fetch_publications str()s them today; this keeps that from being load-bearing.
        carry_forward = {str(k): v for k, v in carry_forward.items()}
        todo = [p for p in pubs if str(p["pmid"]) not in carry_forward]
        llm_scores = signals.llm_triage(bedrock, core, todo, screen_map=screen_map,
                                        max_workers=llm_workers) if bedrock else {}
        llm_scores.update(carry_forward)
        print(f"[{core.core_id} {core.name}] llm: {len(pubs) - len(todo)} carried forward, "
              f"{len(todo) if bedrock else 0} triaged")

    # Curated clients, intersected with each byline below. Asserted rather than
    # inferred, so this fires on a core with zero prior confirmations — the case the
    # phase-2 affinity prior cannot reach. Union of the YAML-curated list (already
    # lowercased/stripped by load_cores) and whatever SPS's "Known clients" panel has
    # written to DynamoDB for this core (#383's engine half).
    yaml_clients = set(core.clients)
    if dry_run:
        print(f"[{core.core_id} {core.name}] dry-run: skipping DynamoDB curated-client read")
        dynamo_clients = set()
    else:
        from pipeline_cores.persist import get_curated_clients  # lazy
        dynamo_clients = get_curated_clients(core.core_id)
    clients = yaml_clients | dynamo_clients
    print(f"[{core.core_id} {core.name}] curated clients: {len(yaml_clients)} yaml + "
          f"{len(dynamo_clients)} dynamodb -> {len(clients)} union")

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
        sig.method_evidence, sig.method_tier = signals.method_family_signal(
            family_index or {}, pmid, core)
        # Already joined and prefix-attributed by the per-core query; a dict access, and
        # no per-core matching step, because the core's prefixes ARE the curation and the
        # SQL applied them. str() on the key: the index is built from DB pmids.
        sig.mesh_evidence = (mesh_index or {}).get(str(pmid)) or []
        tri = llm_scores.get(str(pmid))
        if tri:
            sig.llm_score = tri["score"]
            sig.llm_rationale = tri.get("rationale", "")
        sigs[pmid] = sig
        records.append(_combine.combine(pmid, core.core_id, sig, scored_at=scored_at,
                                        core=core, triage_threshold=threshold))
    if family_index is not None:
        fired = Counter(s.method_tier for s in sigs.values() if s.method_tier)
        print(f"[{core.core_id} {core.name}] method families: {len(family_index)} pmids "
              f"indexed + {sum(len(v) for v in core.method_families.values())} curated "
              f"labels -> {sum(fired.values())} of {len(pubs)} pubs "
              f"({', '.join(f'{t} {fired[t]}' for t in METHOD_FAMILY_TIERS)})")
    if mesh_index is not None:
        prefixes = prefilter.CORE_MESH_TREE_PREFIXES.get(str(core.core_id), [])
        print(f"[{core.core_id} {core.name}] mesh descriptors: {len(prefixes)} E-tree "
              f"prefix(es) -> {sum(1 for s in sigs.values() if s.mesh_evidence)} of "
              f"{len(pubs)} pubs")

    # Phase 2 — repeat-user affinity. Collect the confirmed PAPERS per author (this run
    # + prior), build the affinity index, and re-score non-confirmed records.
    #
    # Sets, not counters (#391). A pair confirmed by an earlier run arrives from BOTH
    # prior_user_pmids and this run's records on any corpus-wide run; `add` makes that
    # one paper, `+= 1` made it two. The numerator has to mean the same thing as the
    # denominator — a count of that author's papers — or the rate is not a rate.
    papers = defaultdict(lambda: defaultdict(set))
    for cwid, by_core in (prior_user_pmids or {}).items():
        for cid, pmid_set in by_core.items():
            papers[cwid][cid] |= set(pmid_set)
    for rec in records:
        if rec.status == STATUS_CONFIRMED:
            for cwid in bylines.get(rec.pmid, []):
                papers[cwid][core.core_id].add(rec.pmid)
    # len() at the boundary: build_affinity_index takes counts, and keeping the sets on
    # this side of it means the dedupe cannot be undone by a caller that builds its own.
    counts = {cwid: {cid: len(p) for cid, p in by_core.items()} for cwid, by_core in papers.items()}
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
    ap.add_argument("--llm-carry-forward", action="store_true",
                    help="reuse the llm_score / llm_rationale already stored in DynamoDB for "
                         "this core instead of re-scoring it. WITHOUT --with-llm this is a "
                         "ZERO-Bedrock run that still keeps the stored LLM evidence alive — "
                         "put_core_usage would otherwise REMOVE it as an optional this run did "
                         "not produce (#384), stripping the chip the review queue rests on. "
                         "WITH --with-llm it triages only the pubs that have no stored score, "
                         "so the same flag covers both the nightly and an incremental top-up")
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
    ap.add_argument("--with-method-families", action="store_true",
                    help="join the A2 method-family taxonomy (s3://<artifacts>/tools/latest/) "
                         "onto each pub and match it against the core's curated "
                         "`method_families:`. One 26 MB read per RUN, no per-pub cost, no "
                         "Bedrock. Every weight is 0.00 today, so this changes no score and "
                         "no status — it only writes the method_* attributes. "
                         "CARRY IT ONCE IT IS ENABLED: exactly like --llm-carry-forward's "
                         "evidence, these four attributes are in _OWNED_ATTRS, so a run "
                         "WITHOUT this flag REMOVEs them from every row a run WITH it wrote "
                         "(#384). There is deliberately no carry-forward analogue — nothing "
                         "reads these attributes yet, so re-deriving them from the artifact "
                         "is free and a second read-back path is not worth owning")
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

    # Without this the module loggers are silent: the root logger defaults to WARNING
    # with only lastResort, so persist.py's logger.info lines — the #386 guard's count
    # and the carry-forward row count, the two numbers the first-tick runbook tells an
    # operator to read — never reach the log group. Same line every pipeline_grants
    # entrypoint carries, and it matters more here: this one runs unattended nightly.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")

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
    # ONCE per run, before any per-publication work: 26 MB of JSON off S3, shared by
    # every core. load_family_index RAISES rather than degrading to {} — an empty index
    # here would be WRITTEN as a REMOVE of the method_* attributes on every row.
    family_index = method_families.load_family_index() if args.with_method_families else None
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
    prior_pmids = load_prior_user_pmids(args.core, bylines, enabled=args.with_affinity, engine=engine)
    # Stored LLM evidence: the same trade as the affinity prior above — ONE Scan
    # grouped by core_id in memory rather than one Scan per core, since each run_core
    # reads only its own core's slice.
    #
    # Read under --dry-run too, unlike the curated-client GetItem. --with-affinity
    # already scans DynamoDB on a dry run and says so, and skipping this one would make
    # a dry run measure the wrong thing in the expensive direction: with --with-llm it
    # would report triaging the WHOLE corpus where the real run triages a few hundred.
    # A dry run that under-reports Bedrock spend by two orders of magnitude is a footgun,
    # not a saving.
    carry_forward = None
    if args.llm_carry_forward:
        from pipeline_cores.persist import scan_core_llm_scores  # lazy
        carry_forward = scan_core_llm_scores(args.core)
    all_records = []
    for core in cores:
        # --alias-search is per-core (each core has its own aliases), unlike the
        # shared corpus loader above.
        core_full_text = (
            pmc_search.make_alias_loader(core, use_s3=args.fulltext_s3)
            if args.alias_search else full_text
        )
        # Per-core, always on, free: the E-tree prefixes differ per core so there is
        # nothing to hoist, and a core with no mapped prefix returns {} without touching
        # the DB. No flag on purpose — see run_core's docstring: an attribute that is
        # only sometimes produced strips itself out of DynamoDB on the runs that skip it.
        # `if pubs` because an EMPTY pmid list means "scan all of person_article_keyword"
        # to the query builder, and a --pmids-file whose PMIDs are all out of corpus
        # lands exactly there — an unbounded join for a run with nothing to score.
        mesh_index = prefilter.core_mesh_tree_descriptors(
            engine, core.core_id, [p["pmid"] for p in pubs]) if pubs else {}
        recs = run_core(core, pubs, bedrock=bedrock, full_text=core_full_text,
                        threshold=args.threshold, scored_at=scored_at, engine=engine,
                        prior_user_pmids=prior_pmids, screen_map=screen_map,
                        llm_workers=args.llm_workers, dry_run=args.dry_run,
                        # None (flag off) and {} (flag on, nothing stored for this
                        # core) mean different things to run_core — .get's default
                        # keeps them apart.
                        carry_forward=(None if carry_forward is None
                                       else carry_forward.get(core.core_id, {})),
                        family_index=family_index, mesh_index=mesh_index)
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

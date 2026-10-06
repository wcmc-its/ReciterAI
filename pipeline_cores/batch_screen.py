"""batch_screen run-mode: pre-filter -> one-core Sonnet title screen -> bands -> candidates.

    python3 -m pipeline_cores.batch_screen --core 2 --test 200            # dry-run (default)
    python3 -m pipeline_cores.batch_screen --core 2 --with-llm --write    # screen + write

The deterministic run (`pipeline_cores.run`) lands the confirmed substrate; this mode
generates the CANDIDATE queue for everything not deterministically confirmed:

  pool = corpus pubs (minus this core's already-confirmed pubs)
   -> prefilter soft prior (author-affinity OR bare-descriptor MeSH E-tree; recorded,
      NOT dropped in v1 — `--drop-threshold` defaults to 0.0)
   -> Sonnet one-core title screen -> confidence 1-10
   -> bands: high=candidate, mid=curator (both written status=candidate), low=drop (skipped)
   -> idempotent, never-downgrade conditional writes to the `reciterai` DynamoDB table.

Writes are opt-in (`--write`); without it the run is a dry-run. The band thresholds were set
by the Option-3 Sonnet calibration (analysis/calibrate_batch_screen.py + RESULTS doc); the
production full-corpus `--write` run is the next gated step. Pre-filter dropping stays OFF
(`--drop-threshold` 0.0) — the calibration confirmed the cheap signals can't gate without
losing recall (MeSH-tree covers ~21%/1% of confirmed imaging/genomics pubs; author is circular).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline_cores import prefilter, signals
from pipeline_cores.combine import noisy_or
from pipeline_cores.dictionary import load_core, load_cores

# --- versions (stamped on every written row; bump when the screen/prefilter change) ---
SCREEN_VERSION = "batch_screen-v1-sonnet-title"
PREFILTER_VERSION = "prefilter-v1-author+mesh-etree"

# --- Bands (confidence 1-10), CALIBRATED 2026-06-21 by the Option-3 Sonnet pass
#     (analysis/calibrate_batch_screen.py): the 237-paper core-2 pilot + held-out recall on
#     the deterministic-confirmed set across cores. high >= CANDIDATE_BAND_MIN (auto-surface);
#     mid in [CURATOR_BAND_MIN, CANDIDATE_BAND_MIN) (curator review); low < CURATOR_BAND_MIN
#     -> drop (not written).
#   * curator-min = 2 is the recall-safe DROP FLOOR: held-out recall at >=2 is 91-100% across
#     the well-powered cores (limited by core 2 at 91% — confirmed imaging pubs whose TITLE
#     doesn't reveal imaging; an intrinsic screen limit, not threshold-tunable). >=3 drops to
#     85%, >=4 to 72% — too lossy.
#   * candidate-min = 5 auto-surfaces at ~91% pilot precision (>=6 reaches 95% but costs recall).
#     This is the tunable precision/volume knob (raise for fewer, cleaner auto-candidates).
CANDIDATE_BAND_MIN = 5
CURATOR_BAND_MIN = 2

# Triage read-timeout: batched screen generations are small JSON, so bound a hung call
# well below the BedrockClient 900s default (same rationale as pipeline_cores.run).
_TRIAGE_READ_TIMEOUT = 120


def band_for(confidence: int, *, candidate_min: int = CANDIDATE_BAND_MIN,
             curator_min: int = CURATOR_BAND_MIN) -> str:
    """confidence 1-10 -> 'candidate' (high) | 'curator' (mid) | 'drop' (low). Pure."""
    if confidence >= candidate_min:
        return "candidate"
    if confidence >= curator_min:
        return "curator"
    return "drop"


def screen_likelihood(confidence: int, prior: float) -> float:
    """Noisy-OR of the screen confidence (conf/10) and the cheap prefilter prior -> [0,1].

    Consistent with combine.combine's noisy-OR; the screen carries the decision, the prior
    lifts it. Pure (no I/O).
    """
    llm_norm = max(0.0, min(1.0, confidence / 10.0))
    prior = max(0.0, min(1.0, prior))
    return round(noisy_or(llm_norm, prior), 4)


def screen_core(core, pubs, *, bedrock, mesh_pmids: set, author_pmids: set,
                confirmed_pmids: set = None, batch_size: int = 40,
                candidate_min: int = CANDIDATE_BAND_MIN, curator_min: int = CURATOR_BAND_MIN,
                drop_threshold: float = 0.0, max_workers: int = 4) -> list:
    """Screen one core's candidate pool -> list of per-(pub,core) result dicts.

    Pure given injected signal sets + a (possibly fake) bedrock client — no DB/AWS — so
    the band/likelihood/threshold logic is unit-testable. Excludes already-confirmed pubs
    (no wasted screen call), applies the soft prior, screens the survivors, and bands them.
    `write` on each result is True iff the band is candidate/curator (drop is skipped).
    Each result: {pmid, confidence, prior, band, likelihood, write}.
    """
    confirmed_pmids = confirmed_pmids or set()
    priors = prefilter.compute_priors([p["pmid"] for p in pubs],
                                      mesh_pmids=mesh_pmids, author_pmids=author_pmids)
    # Pool = not-yet-confirmed pubs whose prior clears the drop threshold (0.0 -> all).
    pool = [p for p in pubs
            if str(p["pmid"]) not in confirmed_pmids and priors[str(p["pmid"])] >= drop_threshold]
    scores = signals.batched_one_core_screen(bedrock, core, pool, batch_size=batch_size,
                                             max_workers=max_workers) if pool else {}
    out = []
    for p in pool:
        pmid = str(p["pmid"])
        conf = scores.get(pmid, 1)
        prior = priors[pmid]
        band = band_for(conf, candidate_min=candidate_min, curator_min=curator_min)
        out.append({
            "pmid": pmid,
            "confidence": conf,
            "prior": prior,
            "band": band,
            "likelihood": screen_likelihood(conf, prior),
            "write": band in ("candidate", "curator"),
        })
    return out


def _prior_signals(core, pmids: list, bylines: dict, engine, *, with_affinity: bool = True) -> tuple:
    """One scan of this core's prior confirmed/claimed rows -> (confirmed_pmids, author_pmids).

    A single `scan_prior_core_usage(core_id)` feeds BOTH the confirmed-set (pubs to skip
    re-screening) and the repeat-user affinity prior (pubs whose byline has a prior user of
    this core), rather than scanning the table twice per core. `bylines` is hoisted by the
    caller (shared across cores); `engine` is only used to read the affinity rate's
    denominator (each author's total corpus output). author_pmids is empty when
    with_affinity is False or on the first run / a scan error (degrades to the MeSH-only
    prior).

    The set is unchanged by the count -> rate switch: this consumer asks only whether the
    prior fires at all, and any positive count still gives a positive rate. It is the
    WEIGHT that the rate changed, in combine().

    No self-exclusion here (signals.author_affinity's `pmid`), deliberately: screen_core
    drops `confirmed` — every pmid this numerator was built from — from the pool before
    the prior is consulted, so no screened paper can be in its own numerator. Taking it
    out of the denominator cannot change whether the prior FIRES either: a positive
    numerator is some OTHER corpus paper, so total - 1 stays positive.

    The prior strength and base rate (signals.AFFINITY_PRIOR_STRENGTH, affinity_base_rate)
    are not passed either: they shrink a positive rate, they never zero one or make a
    zero positive, so they cannot change which papers this set holds.
    """
    from collections import defaultdict
    from pipeline_cores import ingest  # lazy
    from pipeline_cores.persist import scan_prior_core_usage  # lazy

    prior = scan_prior_core_usage(core.core_id)
    confirmed = {str(r["pmid"]) for r in prior}
    if not with_affinity:
        return confirmed, set()
    counts = defaultdict(lambda: defaultdict(int))
    in_corpus = ingest.filter_corpus_pmids(engine, {str(r["pmid"]) for r in prior})
    for rec in prior:
        if str(rec["pmid"]) not in in_corpus:      # numerator on the same corpus as the total
            continue
        for cwid in bylines.get(str(rec["pmid"]), []):
            counts[cwid][rec["core_id"]] += 1
    index = signals.build_affinity_index(counts, ingest.fetch_author_totals(engine, list(counts)))
    author = {str(p) for p in pmids
              if signals.author_affinity(index, bylines.get(str(p), []), core.core_id) > 0.0}
    return confirmed, author


def main(argv=None):
    ap = argparse.ArgumentParser(description="cores batch_screen candidate generation")
    ap.add_argument("--core", help="core_id to run (default: all in dictionary)")
    ap.add_argument("--test", type=int, help="limit to N publications")
    ap.add_argument("--with-llm", action="store_true", help="enable the Bedrock Sonnet screen (required to write)")
    ap.add_argument("--write", action="store_true", help="write candidates to DynamoDB (default: dry-run)")
    ap.add_argument("--no-affinity", action="store_true", help="skip the author-affinity prior signal")
    ap.add_argument("--batch-size", type=int, default=40, help="papers per Sonnet screen call (default 40)")
    ap.add_argument("--workers", type=int, default=4, help="concurrent screen-batch workers (default 4)")
    ap.add_argument("--drop-threshold", type=float, default=0.0,
                    help="drop (pub,core) pairs with prefilter prior below this BEFORE the screen "
                         "(default 0.0 = soft prioritizer, drop nothing; raise only post-calibration)")
    ap.add_argument("--candidate-min", type=int, default=CANDIDATE_BAND_MIN)
    ap.add_argument("--curator-min", type=int, default=CURATOR_BAND_MIN)
    args = ap.parse_args(argv)

    from utils.db import get_engine  # lazy
    from utils.iso_clock import now_iso  # lazy

    if args.write and not args.with_llm:
        ap.error("--write requires --with-llm (cannot write without screening)")

    from pipeline_cores import ingest  # lazy

    cores = [load_core(args.core)] if args.core else load_cores()
    engine = get_engine()
    pubs = ingest.fetch_publications(engine, limit=args.test)
    pmids = [p["pmid"] for p in pubs]
    # Bylines once per run (shared across cores) — mirrors run.py:150, attributes prior
    # confirmations to authors for the affinity prior without a per-core re-fetch.
    bylines = ingest.fetch_author_bylines(engine, pmids)

    bedrock = None
    if args.with_llm:
        from utils.bedrock_client import BedrockClient  # lazy
        bedrock = BedrockClient(read_timeout=_TRIAGE_READ_TIMEOUT)

    scored_at = now_iso()
    total_written = total_candidate = total_curator = total_drop = 0
    for core in cores:
        mesh_pmids = prefilter.core_mesh_tree_pmids(engine, core.core_id, pmids)
        confirmed, author_pmids = _prior_signals(core, pmids, bylines, engine,
                                                 with_affinity=not args.no_affinity)
        results = screen_core(
            core, pubs, bedrock=bedrock, mesh_pmids=mesh_pmids, author_pmids=author_pmids,
            confirmed_pmids=confirmed, batch_size=args.batch_size,
            candidate_min=args.candidate_min, curator_min=args.curator_min,
            drop_threshold=args.drop_threshold, max_workers=args.workers,
        )
        c = sum(1 for r in results if r["band"] == "candidate")
        cu = sum(1 for r in results if r["band"] == "curator")
        d = sum(1 for r in results if r["band"] == "drop")
        total_candidate += c; total_curator += cu; total_drop += d
        if args.write:
            from pipeline_cores.persist import put_candidate  # lazy
            for r in results:
                if r["write"]:
                    total_written += int(put_candidate(
                        r["pmid"], core.core_id, confidence=r["confidence"], band=r["band"],
                        prior=r["prior"], likelihood=r["likelihood"], scored_at=scored_at,
                        screen_version=SCREEN_VERSION, prefilter_version=PREFILTER_VERSION))
        print(f"[{core.core_id} {core.name}] pool={len(results)} "
              f"-> candidate {c}, curator {cu}, drop {d} "
              f"(mesh-signal {len(mesh_pmids)}, author-signal {len(author_pmids)})")

    mode = "WROTE" if args.write else "DRY RUN — would write"
    print(f"\n{mode} {total_written if args.write else total_candidate + total_curator} "
          f"candidate rows ({total_candidate} candidate-band + {total_curator} curator-band; "
          f"{total_drop} dropped). screen={SCREEN_VERSION} prefilter={PREFILTER_VERSION}")
    if not args.with_llm:
        print("NOTE: no --with-llm -> every pub screened low (confidence 1). Add --with-llm to score.")


if __name__ == "__main__":
    main()

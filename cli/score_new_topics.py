#!/usr/bin/env python3
"""Additive scorer for NEW taxonomy topics — mint scores without a full re-score.

The stock pipeline (``score_publications.py --force``) re-screens every
publication against *all* topics and re-materializes every ``TOPIC#`` row.
Adding a single curated topic that way costs a full ~$65 run because the
delete-then-write materialization (``_materialize_topic_rows``) rewrites the
whole corpus. This tool does the cheap, surgical equivalent: it screens the
corpus against ONLY the new topic(s), then writes the result **additively**.

Why additive is safe — and required:
  * A genuinely-new topic's ``TOPIC#{topic_id}`` partition has no existing
    rows, so a plain ``batch_write`` cannot orphan or duplicate anything.
  * We therefore never touch ``_materialize_topic_rows`` /
    ``_delete_topic_rows_for_pmid`` — those are scoped to (pmid x author) and
    would wipe a PMID's OTHER 67 topics if handed a single-topic taxonomy.
  * ``FACULTY#`` is the one cross-topic record. We merge the new dense scores
    into the existing ``scoring_results.json`` and reuse
    ``load_dynamodb.build_faculty_records`` verbatim, so every untouched
    topic is preserved exactly; only faculty who co-authored a qualifying
    new-topic publication are rewritten.
  * ``TAXONOMY#{version}`` META is refreshed so the runtime can resolve the
    new topic's label/description.

Fidelity caveat: the screening + dense prompts ask for *absolute* per-topic
relevance, so scoring a topic in isolation closely tracks scoring it among the
full set — but it is not bit-identical to a full re-screen. Use
``--verify-fidelity`` to quantify the delta on a sample before committing.

Corpus: the FULL ``extract_publications()`` set, NOT ``scoring_results.json``.
A pure new-topic paper can score 0 on all existing topics and so be absent from
``scoring_results.json`` entirely — it must still be screened here.

Inputs (the same three files ``load_dynamodb.py`` consumes), plus the taxonomy
that already contains the new topic(s):
    scoring_results.json   -- existing dense scores (for the FACULTY# merge)
    author_mapping.json    -- pmid -> [{cwid, position}]
    faculty_metadata.json  -- cwid -> {name, department, ...}
    taxonomy_v2.json       -- MUST already contain the new topic id(s)

Usage:
    # Dry run (default): score, report what WOULD change, write nothing.
    python3 cli/score_new_topics.py --topics hematology_medical_oncology

    # Validate the single-topic approximation on a 40-pub sample first.
    python3 cli/score_new_topics.py --topics hematology_medical_oncology \\
        --verify-fidelity --sample 40

    # Commit to DynamoDB (additive TOPIC# + merged FACULTY# + TAXONOMY#).
    python3 cli/score_new_topics.py --topics hematology_medical_oncology --execute
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path

from tqdm import tqdm

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import score_publications as sp  # noqa: E402  (extract_*, prompt builders, _dense_score)
from cli.load_dynamodb import build_faculty_records, build_taxonomy_record  # noqa: E402
from utils.bedrock_client import (  # noqa: E402
    BedrockClient,
    HAIKU_MODEL,
    set_cost_accumulator,
)
from utils.dynamodb_helpers import (  # noqa: E402
    TABLE_NAME,
    batch_write,
    get_dynamo_client,
)
from utils.llm_cost import CostAccumulator  # noqa: E402
from utils.topic_records import build_topic_rows_for_pmid  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

FLOOR = sp.SCREENING_THRESHOLD  # config score_floor — same floor TOPIC#/FACULTY# use
DEFAULT_CONCURRENCY = 15
CHECKPOINT_BATCH = 200


# ---------------------------------------------------------------------------
# Input loading + guards
# ---------------------------------------------------------------------------

def _load_json(path: Path):
    assert path.exists(), f"{path.name} not found at {path}"
    return json.load(open(path))


def load_inputs(repo_root: Path) -> tuple[dict, list, dict, dict]:
    """Load taxonomy + the three load_dynamodb input files."""
    taxonomy = _load_json(repo_root / "taxonomy_v2.json")
    scoring_data = _load_json(repo_root / "scoring_results.json")
    scoring_results = scoring_data["scored_publications"]
    author_mapping = _load_json(repo_root / "author_mapping.json")
    faculty_metadata = _load_json(repo_root / "faculty_metadata.json")
    return taxonomy, scoring_results, author_mapping, faculty_metadata


def assert_topics_are_new(target_ids: list[str], taxonomy: dict,
                          scoring_results: list) -> None:
    """Fail loud unless every target id is in the taxonomy and absent from
    every existing dense_scores map (i.e. genuinely new, never scored)."""
    tax_ids = {t["id"] for t in taxonomy["topics"]}
    missing = [tid for tid in target_ids if tid not in tax_ids]
    assert not missing, (
        f"target topics not in taxonomy_v2.json: {missing}. "
        "Add them to the taxonomy before scoring."
    )
    already = set()
    for pub in scoring_results:
        for tid in target_ids:
            if tid in (pub.get("dense_scores") or {}):
                already.add(tid)
    assert not already, (
        f"these topics already have scores in scoring_results.json: {sorted(already)}. "
        "This tool only mints genuinely-new topics; use score_publications.py --force "
        "to re-score an existing topic."
    )


def assert_no_existing_ddb_rows(client, table_name: str,
                                target_ids: list[str]) -> None:
    """Belt-and-suspenders: abort if any TOPIC#{id} rows already exist in DDB,
    which would mean the additive write could collide with live data."""
    for tid in target_ids:
        resp = client.query(
            TableName=table_name,
            KeyConditionExpression="PK = :pk",
            ExpressionAttributeValues={":pk": {"S": f"TOPIC#{tid}"}},
            Limit=1,
        )
        n = len(resp.get("Items", []))
        assert n == 0, (
            f"TOPIC#{tid} already has rows in {table_name} — not a new topic. "
            "Refusing to write additively (would risk stale/duplicate rows)."
        )


def single_topic_view(taxonomy: dict, target_ids: list[str]) -> dict:
    """A taxonomy dict containing ONLY the target topics, preserving version.

    Reused by the prompt builders so screening/dense run against just the new
    topic(s) — the entire cost saving.
    """
    target_set = set(target_ids)
    topics = [t for t in taxonomy["topics"] if t["id"] in target_set]
    return {"taxonomy_version": taxonomy["taxonomy_version"], "topics": topics}


# ---------------------------------------------------------------------------
# Scoring (single-topic, two-pass, reusing the canonical prompt builders)
# ---------------------------------------------------------------------------

def score_one(pub: dict, bedrock: BedrockClient, view: dict,
              int_to_id: dict, id_to_int: dict) -> tuple[dict, dict]:
    """Two-pass score for ONE pub against the target topics only.

    Returns (screening_scores, dense_scores) where dense_scores is
    ``{topic_id: {"score": float, "rationale": str}}`` for topics that cleared
    the screening floor (empty when none passed). Mirrors
    ``score_publications.score_one_publication`` minus all DDB/checkpoint/
    materialization side effects.
    """
    raw_screening = bedrock.call_json(
        model=HAIKU_MODEL,
        messages=[{"role": "user", "content": sp.make_screening_prompt(pub, view)}],
    )
    screening: dict = {}
    for int_id, score in raw_screening.items():
        tid = int_to_id.get(str(int_id))
        if not tid:
            continue
        try:
            screening[tid] = float(score)
        except (TypeError, ValueError):
            continue

    passed = {tid: s for tid, s in screening.items() if s >= FLOOR}
    if not passed:
        return screening, {}

    dense_prompt = sp.make_dense_prompt(pub, passed, view, id_to_int)
    raw_dense, _fallback = sp._dense_score(bedrock, dense_prompt)
    dense: dict = {}
    for int_id, value in raw_dense.items():
        tid = int_to_id.get(str(int_id))
        if not tid:
            continue
        try:
            if isinstance(value, dict):
                dense[tid] = {
                    "score": float(value.get("score", 0.0)),
                    "rationale": str(value.get("rationale", "")),
                }
            else:
                dense[tid] = {"score": float(value), "rationale": ""}
        except (TypeError, ValueError):
            continue
    return screening, dense


async def score_corpus(corpus: list, view: dict, int_to_id: dict,
                       id_to_int: dict, concurrency: int,
                       checkpoint_path: Path) -> dict:
    """Score every corpus pub for the target topics, concurrently, with a
    resumable local checkpoint. Returns ``{pmid: dense_scores}`` (only pmids
    whose dense scoring ran — i.e. at least one topic cleared screening)."""
    checkpoint: dict = {}
    if checkpoint_path.exists():
        checkpoint = json.load(open(checkpoint_path))
        logger.info("Resuming from checkpoint: %d pmids already scored",
                    len(checkpoint))

    todo = [p for p in corpus if str(p["pmid"]) not in checkpoint]
    logger.info("Scoring %d pubs (%d already checkpointed)",
                len(todo), len(corpus) - len(todo))

    bedrock = BedrockClient()
    sem = asyncio.Semaphore(concurrency)
    bar = tqdm(total=len(todo), desc="screen+dense", unit="pub")
    failed: list[str] = []

    async def run_one(pub: dict) -> tuple[str, dict | None]:
        pmid = str(pub["pmid"])
        async with sem:
            try:
                _screening, dense = await asyncio.to_thread(
                    score_one, pub, bedrock, view, int_to_id, id_to_int
                )
            except Exception as e:  # noqa: BLE001 — record + continue; never abort the run
                logger.warning("score failed pmid=%s: %s", pmid, e)
                # None (not {}) so a transient failure is NOT mistaken for a
                # legit no-pass: it stays out of the checkpoint and is retried
                # on the next run instead of being silently dropped forever.
                dense = None
            finally:
                bar.update(1)
        return pmid, dense

    # Batch so the checkpoint is flushed periodically (resume after a crash).
    for i in range(0, len(todo), CHECKPOINT_BATCH):
        chunk = todo[i:i + CHECKPOINT_BATCH]
        for pmid, dense in await asyncio.gather(*(run_one(p) for p in chunk)):
            if dense is None:
                failed.append(pmid)
            else:
                checkpoint[pmid] = dense
        with open(checkpoint_path, "w") as fh:
            json.dump(checkpoint, fh)
    bar.close()
    if failed:
        logger.warning(
            "%d pmids ERRORED and were not scored (not checkpointed — re-run to "
            "retry): %s%s",
            len(failed), ", ".join(failed[:10]),
            " ..." if len(failed) > 10 else "",
        )

    # Keep only pmids with at least one topic at/above the floor.
    scored: dict = {}
    for pmid, dense in checkpoint.items():
        keep = {tid: sd for tid, sd in (dense or {}).items()
                if sd.get("score", 0.0) >= FLOOR}
        if keep:
            scored[pmid] = keep
    return scored


# ---------------------------------------------------------------------------
# Record building (additive)
# ---------------------------------------------------------------------------

def build_additive_topic_rows(scored: dict, corpus_by_pmid: dict,
                              author_mapping: dict, taxonomy_version: str) -> list:
    """TOPIC# rows for the new topic(s) only, via the canonical row builder."""
    rows: list = []
    for pmid, dense in scored.items():
        authors = author_mapping.get(pmid, [])
        if not authors:
            continue
        pub = corpus_by_pmid.get(pmid, {})
        rows.extend(build_topic_rows_for_pmid(
            pmid=pmid,
            dense_scores=dense,
            authors=authors,
            taxonomy_version=taxonomy_version,
            min_score=FLOOR,
            synopsis=str(pub.get("synopsis") or ""),
            title=str(pub.get("title") or ""),
            impact_score=pub.get("impact_score"),
            impact_justification=str(pub.get("impact_justification") or ""),
        ))
    return rows


def build_merged_faculty_rows(scored: dict, scoring_results: list,
                              author_mapping: dict, faculty_metadata: dict,
                              taxonomy_version: str) -> list:
    """Merge new dense scores into scoring_results, rebuild FACULTY# via the
    canonical builder, and return ONLY the records for affected faculty.

    Merging then reusing ``build_faculty_records`` guarantees every existing
    topic survives the recompute — the new topic simply joins the top-10
    contest. Filtering to affected faculty keeps the write surface minimal: a
    faculty with no qualifying new-topic pub produces a byte-identical record,
    so there is nothing to write.
    """
    sr_by_pmid = {str(p["pmid"]): p for p in scoring_results}
    for pmid, dense in scored.items():
        entry = sr_by_pmid.get(pmid)
        if entry is None:
            entry = {"pmid": pmid, "screening_scores": {}, "dense_scores": {}}
            sr_by_pmid[pmid] = entry
            scoring_results.append(entry)
        entry.setdefault("dense_scores", {})
        entry["dense_scores"].update(dense)

    affected_pks = set()
    for pmid in scored:
        for author in author_mapping.get(pmid, []):
            affected_pks.add(f"FACULTY#cwid_{author['cwid']}")

    all_rows = build_faculty_records(
        scoring_results, author_mapping, faculty_metadata, taxonomy_version, FLOOR
    )
    return [r for r in all_rows if r["PK"]["S"] in affected_pks]


# ---------------------------------------------------------------------------
# Fidelity verification
# ---------------------------------------------------------------------------

def verify_fidelity(sample: list, taxonomy: dict, target_ids: list[str],
                    view: dict, int_to_id: dict, id_to_int: dict) -> None:
    """Score a sample both single-topic and full-taxonomy; report the delta on
    the target topic's dense score so the operator can judge the approximation
    before committing a full run."""
    full_int_to_id, full_id_to_int = sp.build_topic_index(taxonomy)
    bedrock = BedrockClient()
    deltas: list = []
    print(f"\n=== Fidelity check: {len(sample)} pubs, single-topic vs full-{len(taxonomy['topics'])}-topic ===")
    for pub in tqdm(sample, desc="fidelity", unit="pub"):
        _s1, d_single = score_one(pub, bedrock, view, int_to_id, id_to_int)
        _s2, d_full = score_one(pub, bedrock, taxonomy, full_int_to_id, full_id_to_int)
        for tid in target_ids:
            s_single = d_single.get(tid, {}).get("score")
            s_full = d_full.get(tid, {}).get("score")
            a = 0.0 if s_single is None else s_single
            b = 0.0 if s_full is None else s_full
            deltas.append(abs(a - b))
            print(f"  {pub['pmid']:>9} {tid}: single={a:.2f} full={b:.2f} Δ={abs(a - b):.2f}")
    if deltas:
        mean = sum(deltas) / len(deltas)
        print(f"\n  mean |Δ| = {mean:.3f}   max |Δ| = {max(deltas):.3f}   n = {len(deltas)}")
        print("  (low Δ ⇒ single-topic scoring closely tracks the full re-screen)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--topics", required=True,
                        help="comma-separated NEW topic id(s) to score, e.g. "
                             "hematology_medical_oncology")
    parser.add_argument("--execute", action="store_true",
                        help="actually write to DynamoDB (default: dry run)")
    parser.add_argument("--table-name", default=TABLE_NAME)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--checkpoint", default="score_new_topics_checkpoint.json",
                        help="local resume checkpoint path")
    parser.add_argument("--corpus-file",
                        help="JSON list of pubs to score instead of "
                             "extract_publications() (testing/repeatability)")
    parser.add_argument("--verify-fidelity", action="store_true",
                        help="score a sample single-topic AND full-taxonomy, "
                             "report the per-topic score delta, then exit")
    parser.add_argument("--sample", type=int, default=40,
                        help="sample size for --verify-fidelity")
    args = parser.parse_args()

    target_ids = [t.strip() for t in args.topics.split(",") if t.strip()]
    assert target_ids, "--topics must name at least one topic id"

    taxonomy, scoring_results, author_mapping, faculty_metadata = load_inputs(REPO_ROOT)
    taxonomy_version = taxonomy["taxonomy_version"]
    assert_topics_are_new(target_ids, taxonomy, scoring_results)

    view = single_topic_view(taxonomy, target_ids)
    assert view["topics"], f"no taxonomy entries matched {target_ids}"
    int_to_id, id_to_int = sp.build_topic_index(view)

    print(f"Target topics ({len(target_ids)}): {', '.join(target_ids)}")
    print(f"Taxonomy version: {taxonomy_version} ({len(taxonomy['topics'])} topics)")

    # Corpus
    if args.corpus_file:
        corpus = _load_json(Path(args.corpus_file))
    else:
        corpus = sp.extract_publications()
    corpus_by_pmid = {str(p["pmid"]): p for p in corpus}
    print(f"Corpus: {len(corpus)} publications")

    # Fidelity-only mode: cheap sample comparison, no writes.
    if args.verify_fidelity:
        sample = corpus[:max(0, args.sample)]
        verify_fidelity(sample, taxonomy, target_ids, view, int_to_id, id_to_int)
        return

    # Cost capture wraps the whole scoring pass (Bedrock calls only).
    acc = CostAccumulator()
    set_cost_accumulator(acc)
    try:
        scored = asyncio.run(score_corpus(
            corpus, view, int_to_id, id_to_int,
            concurrency=args.concurrency,
            checkpoint_path=Path(args.checkpoint),
        ))
    finally:
        set_cost_accumulator(None)

    qualifying_pmids = len(scored)
    topic_rows = build_additive_topic_rows(
        scored, corpus_by_pmid, author_mapping, taxonomy_version
    )
    faculty_rows = build_merged_faculty_rows(
        scored, scoring_results, author_mapping, faculty_metadata, taxonomy_version
    )
    taxonomy_record = build_taxonomy_record(taxonomy)

    # Per-topic qualifying counts for the report.
    per_topic = defaultdict(int)
    for dense in scored.values():
        for tid in dense:
            per_topic[tid] += 1

    if qualifying_pmids == 0:
        print("\n⚠️  ZERO publications qualified for the new topic(s). This is the "
              "pre-flight failure mode — either the topic genuinely matches nothing "
              "in the corpus, or synopses/corpus are empty. Investigate before --execute.")

    print("\n=== Plan ===")
    print(f"  Pubs screened:            {len(corpus):>8,}")
    print(f"  Pubs qualifying (>= {FLOOR}): {qualifying_pmids:>8,}")
    for tid in target_ids:
        print(f"    - {tid}: {per_topic.get(tid, 0):,} pubs")
    print(f"  TOPIC# rows to add:       {len(topic_rows):>8,}")
    print(f"  FACULTY# rows to update:  {len(faculty_rows):>8,}")
    print(f"  TAXONOMY# topic_count:    {taxonomy_record['topic_count']['N']:>8}")
    print(f"  Bedrock cost (observed):  ${acc.total_usd}")
    print(f"  Cost detail: {acc.summary()}")

    if not args.execute:
        print("\nDRY RUN — nothing written. Re-run with --execute to commit.")
        return

    client = get_dynamo_client()
    assert_no_existing_ddb_rows(client, args.table_name, target_ids)

    print(f"\nWriting to {args.table_name} ...")
    batch_write(client, args.table_name, topic_rows)
    print(f"  wrote {len(topic_rows):,} TOPIC# rows")
    batch_write(client, args.table_name, faculty_rows)
    print(f"  wrote {len(faculty_rows):,} FACULTY# rows")
    client.put_item(TableName=args.table_name, Item=taxonomy_record)
    print(f"  wrote TAXONOMY#{taxonomy_version} ({taxonomy_record['topic_count']['N']} topics)")
    print("Done.")


if __name__ == "__main__":
    main()

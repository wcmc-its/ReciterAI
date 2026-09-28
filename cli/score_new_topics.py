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
  * ``FACULTY#`` is the one cross-topic record. For each affected faculty we
    read their EXISTING ``TOPIC#`` scores from the live ``FacultyIndex`` GSI
    (DynamoDB is the durable source of truth) and merge the new topic in,
    recomputing top-10 + scored_pub_count exactly. Every untouched topic is
    preserved; only faculty who co-authored a qualifying new-topic pub are
    rewritten. (An earlier design merged into ``scoring_results.json`` — wrong:
    that file is an ephemeral byproduct of a full run, absent on a standalone
    additive run.)
  * ``TAXONOMY#{version}`` META is refreshed so the runtime can resolve the
    new topic's label/description.

Fidelity caveat: the screening + dense prompts ask for *absolute* per-topic
relevance, so scoring a topic in isolation closely tracks scoring it among the
full set — but it is not bit-identical to a full re-screen. Use
``--verify-fidelity`` to quantify the delta on a sample before committing.

Corpus: the FULL ``extract_publications()`` set, NOT ``scoring_results.json``.
A pure new-topic paper can score 0 on all existing topics and so be absent from
``scoring_results.json`` entirely — it must still be screened here.

Inputs / data sources:
    taxonomy_v2.json   -- file; MUST already contain the new topic id(s)
    author mapping     -- regenerated from ReciterDB (extract_author_mapping)
    faculty metadata   -- regenerated from ReciterDB (extract_faculty_metadata)
    corpus             -- ReciterDB + DynamoDB synopsis join (extract_publications)
    existing FACULTY#  -- read live from the DynamoDB FacultyIndex GSI
No scoring_results.json / *.json pipeline byproducts are required.

Contrastive scoring: scoring a topic in isolation over-includes borderline
papers (a lung-cancer paper looks "cancer-ish → 0.5" alone, but the full run
routes it to ``lung_cancer`` and heme/onc scores 0). Pass the competitor topics
via ``--context-topics`` so screening/dense see them and route correctly; their
scores are used only for routing and never written. Always ``--verify-fidelity``
to confirm the target's scores track the full run before committing.

Usage (heme/onc example — competitors = cancer cluster + hematology):
    CTX=breast_cancer,lung_cancer,prostate_urologic_cancer,gi_cancer,\\
neuro_oncology,gynecologic_oncology,melanoma_skin_cancer,cancer_biology_general,hematology

    # 1. Validate fidelity on a sample (cheap, no writes).
    python3 cli/score_new_topics.py --topics hematology_medical_oncology \\
        --context-topics "$CTX" --verify-fidelity --sample 40

    # 2. Dry run: score full corpus, report what WOULD change, write nothing.
    python3 cli/score_new_topics.py --topics hematology_medical_oncology \\
        --context-topics "$CTX"

    # 3. Commit (additive TOPIC# + merged FACULTY# + TAXONOMY#).
    python3 cli/score_new_topics.py --topics hematology_medical_oncology \\
        --context-topics "$CTX" --execute
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
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
    to_decimal,
)
from utils.llm_cost import CostAccumulator  # noqa: E402
from utils.build_info import minted_by  # noqa: E402
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


def _load_text(path: Path) -> str:
    assert path.exists(), f"rubric file not found at {path}"
    return path.read_text().strip()


def load_taxonomy(repo_root: Path) -> dict:
    """Load the taxonomy file (must already contain the new topic id(s))."""
    return _load_json(repo_root / "taxonomy_v2.json")


def assert_topics_in_taxonomy(target_ids: list[str], taxonomy: dict) -> None:
    """Fail loud unless every target id exists in the taxonomy.

    The 'genuinely new — never scored' half of the check is
    ``assert_no_existing_ddb_rows``, which queries DynamoDB directly rather than
    a possibly-absent ``scoring_results.json``.
    """
    tax_ids = {t["id"] for t in taxonomy["topics"]}
    missing = [tid for tid in target_ids if tid not in tax_ids]
    assert not missing, (
        f"target topics not in taxonomy_v2.json: {missing}. "
        "Add them to the taxonomy before scoring."
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

_SCREENING_JSON_REMINDER = (
    'Return ONLY a JSON object with integer topic number keys and float values, '
    'e.g. {"0": 0.85}.'
)
_DENSE_JSON_REMINDER = (
    'Return ONLY a JSON object with integer topic number keys and '
    '{"score": float, "rationale": str} values.'
)


def _with_rubric(prompt: str, rubric: str, json_reminder: str) -> str:
    """Append a topic-specific rubric to a canonical prompt.

    The generic 0.0–1.0 guidance in the canonical screening/dense prompt is
    topic-agnostic, which makes single-topic scoring over-include borderline
    papers. A per-topic rubric (decision order + anchored bands) appended here
    restores the routing the full 68-topic run gets implicitly. The JSON-format
    line is restated last so the trailing rubric can't bury it.
    """
    if not rubric:
        return prompt
    return (
        f"{prompt}\n\n"
        "=== TOPIC-SPECIFIC RUBRIC (authoritative — apply this decision order and "
        "score bands; it overrides the generic guidance above) ===\n"
        f"{rubric}\n\n{json_reminder}"
    )


def score_one(pub: dict, bedrock: BedrockClient, view: dict,
              int_to_id: dict, id_to_int: dict, rubric: str = "") -> tuple[dict, dict]:
    """Two-pass score for ONE pub against the target topics only.

    Returns (screening_scores, dense_scores) where dense_scores is
    ``{topic_id: {"score": float, "rationale": str}}`` for topics that cleared
    the screening floor (empty when none passed). Mirrors
    ``score_publications.score_one_publication`` minus all DDB/checkpoint/
    materialization side effects. ``rubric`` (when given) is appended to both
    the screening and dense prompts to sharpen single-topic routing.
    """
    raw_screening = bedrock.call_json(
        model=HAIKU_MODEL,
        messages=[{"role": "user", "content": _with_rubric(
            sp.make_screening_prompt(pub, view), rubric, _SCREENING_JSON_REMINDER)}],
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

    dense_prompt = _with_rubric(
        sp.make_dense_prompt(pub, passed, view, id_to_int), rubric, _DENSE_JSON_REMINDER)
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
                       id_to_int: dict, target_ids: list,
                       concurrency: int, checkpoint_path: Path,
                       rubric: str = "") -> dict:
    """Score every corpus pub against the (contrastive) view, concurrently, with
    a resumable local checkpoint. Returns ``{pmid: dense_scores}`` containing
    ONLY the target topics' scores at/above the floor — context (competitor)
    topic scores are used for routing during scoring but dropped from the
    result so they are never written.

    The checkpoint is keyed to the exact topic set it was built for; resuming
    against a different ``--topics``/``--context-topics`` set fails loud rather
    than mixing scores from two different scoring views.
    """
    # Key the checkpoint to the exact topic set AND rubric — a changed rubric
    # invalidates prior scores, so resuming across a rubric edit must fail loud.
    rubric_tag = hashlib.sha1(rubric.encode()).hexdigest()[:8] if rubric else "none"
    view_key = ",".join(sorted(t["id"] for t in view["topics"])) + f"|rubric:{rubric_tag}"
    checkpoint: dict = {}
    if checkpoint_path.exists():
        data = json.load(open(checkpoint_path))
        if data.get("view") and data["view"] != view_key:
            raise SystemExit(
                f"checkpoint {checkpoint_path} was built for a different topic "
                f"set/rubric ([{data['view']}] != [{view_key}]); use a fresh "
                "--checkpoint."
            )
        checkpoint = data.get("scores", {})
        logger.info("Resuming from checkpoint: %d pmids already scored",
                    len(checkpoint))

    def _save() -> None:
        with open(checkpoint_path, "w") as fh:
            json.dump({"view": view_key, "scores": checkpoint}, fh)

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
                    score_one, pub, bedrock, view, int_to_id, id_to_int, rubric
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
        _save()
    bar.close()
    if failed:
        logger.warning(
            "%d pmids ERRORED and were not scored (not checkpointed — re-run to "
            "retry): %s%s",
            len(failed), ", ".join(failed[:10]),
            " ..." if len(failed) > 10 else "",
        )

    # Keep only the TARGET topics' scores at/above the floor — context topics
    # informed routing during scoring but are never written.
    target_set = set(target_ids)
    scored: dict = {}
    for pmid, dense in checkpoint.items():
        keep = {tid: sd for tid, sd in (dense or {}).items()
                if tid in target_set and sd.get("score", 0.0) >= FLOOR}
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
            minted_by=minted_by("score_new_topics"),
            pmid=pmid,
            dense_scores=dense,
            authors=authors,
            taxonomy_version=taxonomy_version,
            min_score=FLOOR,
            synopsis=str(pub.get("synopsis") or ""),
            title=str(pub.get("title") or ""),
            year=pub.get("year"),
            impact_score=pub.get("impact_score"),
            impact_justification=str(pub.get("impact_justification") or ""),
        ))
    return rows


def fetch_faculty_existing(client, table_name: str, cwid: str) -> tuple[dict, set]:
    """Return ``(topic_max, pmids)`` for a faculty's existing ``TOPIC#`` rows,
    read live from the ``FacultyIndex`` GSI (faculty_uid HASH, PK RANGE).

    ``topic_max`` is ``{topic_id: max dense score}`` across the faculty's
    publications; ``pmids`` is the distinct scored-publication set (parsed from
    the score SK, which always encodes the PMID). This is the durable, live
    state the FACULTY# rebuild merges the new topic into — no dependency on the
    ephemeral ``scoring_results.json``. Paginates on LastEvaluatedKey.
    """
    topic_max: dict = {}
    pmids: set = set()
    start_key = None
    while True:
        kwargs = {
            "TableName": table_name,
            "IndexName": "FacultyIndex",
            "KeyConditionExpression": "faculty_uid = :f AND begins_with(PK, :t)",
            "ExpressionAttributeValues": {
                ":f": {"S": f"cwid_{cwid}"},
                ":t": {"S": "TOPIC#"},
            },
            "ProjectionExpression": "PK, SK, score",
        }
        if start_key:
            kwargs["ExclusiveStartKey"] = start_key
        resp = client.query(**kwargs)
        for item in resp.get("Items", []):
            pk = item.get("PK", {}).get("S", "")
            if not pk.startswith("TOPIC#"):
                continue
            topic_id = pk[len("TOPIC#"):]
            try:
                score = float(item.get("score", {}).get("N", "0"))
            except (TypeError, ValueError):
                continue
            if score > topic_max.get(topic_id, 0.0):
                topic_max[topic_id] = score
            # SK = SCORE#NNNN#ACTIVITY#pmid_{pmid}#cwid_{cwid}
            sk = item.get("SK", {}).get("S", "")
            if "ACTIVITY#pmid_" in sk:
                pmids.add(sk.split("ACTIVITY#pmid_", 1)[1].split("#cwid_", 1)[0])
        start_key = resp.get("LastEvaluatedKey")
        if not start_key:
            break
    return topic_max, pmids


def build_merged_faculty_rows(scored: dict, author_mapping: dict,
                              faculty_metadata: dict, taxonomy_version: str,
                              fetch_existing) -> list:
    """Rebuild ``FACULTY#`` for faculty affected by the new topic(s), merging
    the new dense scores into their EXISTING ``TOPIC#`` state.

    ``fetch_existing(cwid) -> (topic_max, pmids)`` supplies that faculty's live
    topic→max-score map and scored-pmid set (the FacultyIndex GSI in prod; an
    injected fake in tests). The merge preserves every existing topic and
    recomputes the top-10 + scored_pub_count exactly — the new topic just joins
    the contest — with no dependency on ``scoring_results.json``. The emitted
    record shape mirrors ``load_dynamodb.build_faculty_records`` field-for-field.
    Faculty absent from ``faculty_metadata`` are skipped (same as the canonical
    builder); their additive ``TOPIC#`` rows are still written.
    """
    # New-topic contributions per faculty, derived from the scored corpus.
    new_topic_max: dict = defaultdict(lambda: defaultdict(float))  # cwid -> {tid: max}
    new_pmids: dict = defaultdict(set)                             # cwid -> {pmid}
    for pmid, dense in scored.items():
        for author in author_mapping.get(pmid, []):
            cwid = author["cwid"]
            qualified = False
            for tid, sd in dense.items():
                score = sd.get("score", 0.0) if isinstance(sd, dict) else sd
                if score >= FLOOR:
                    new_topic_max[cwid][tid] = max(new_topic_max[cwid][tid], score)
                    qualified = True
            if qualified:
                new_pmids[cwid].add(pmid)

    rows: list = []
    for cwid in tqdm(sorted(new_topic_max), desc="faculty merge", unit="cwid"):
        meta = faculty_metadata.get(cwid)
        if not meta:
            continue  # no metadata → no FACULTY# record (matches build_faculty_records)
        existing_max, existing_pmids = fetch_existing(cwid)
        merged: dict = dict(existing_max)
        for tid, score in new_topic_max[cwid].items():
            if score > merged.get(tid, 0.0):
                merged[tid] = score
        pmids = set(existing_pmids) | new_pmids[cwid]

        sorted_topics = sorted(merged.items(), key=lambda x: -x[1])[:10]
        top_topics_list = [
            {"M": {"topic_id": {"S": tid},
                   "max_score": {"N": str(to_decimal(score))}}}
            for tid, score in sorted_topics
        ]
        rows.append({
            "PK": {"S": f"FACULTY#cwid_{cwid}"},
            "SK": {"S": "PROFILE"},
            "faculty_uid": {"S": f"cwid_{cwid}"},
            "name": {"S": meta.get("name", "")},
            "department": {"S": meta.get("department", "")},
            "h_index": {"N": str(meta.get("h_index", 0) or 0)},
            "article_count": {"N": str(meta.get("article_count", 0) or 0)},
            "first_author_count": {"N": str(meta.get("first_author_count", 0) or 0)},
            "last_author_count": {"N": str(meta.get("last_author_count", 0) or 0)},
            "scored_pub_count": {"N": str(len(pmids))},
            "top_topics": {"L": top_topics_list},
            "taxonomy_version": {"S": taxonomy_version},
        })
    return rows


# ---------------------------------------------------------------------------
# Fidelity verification
# ---------------------------------------------------------------------------

def verify_fidelity(sample: list, taxonomy: dict, target_ids: list[str],
                    view: dict, int_to_id: dict, id_to_int: dict,
                    rubric: str = "") -> None:
    """Score a sample two ways and report the per-target score delta.

    Left side = our cheap path: the single-topic view + the topic rubric.
    Right side = the truth we want to match: the full 68-topic taxonomy with NO
    rubric (the canonical pipeline's behavior). Low delta ⇒ the cheap path
    reproduces what the full run would have scored.
    """
    full_int_to_id, full_id_to_int = sp.build_topic_index(taxonomy)
    bedrock = BedrockClient()
    deltas: list = []
    print(f"\n=== Fidelity check: {len(sample)} pubs, view-of-{len(view['topics'])}"
          f"{'+rubric' if rubric else ''} vs full-{len(taxonomy['topics'])}-topic "
          f"(target: {', '.join(target_ids)}) ===")
    for pub in tqdm(sample, desc="fidelity", unit="pub"):
        _s1, d_single = score_one(pub, bedrock, view, int_to_id, id_to_int, rubric)
        _s2, d_full = score_one(pub, bedrock, taxonomy, full_int_to_id, full_id_to_int, "")
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
    parser.add_argument("--context-topics", default="",
                        help="comma-separated EXISTING competitor topic id(s) to "
                             "include in screening/dense so the model routes "
                             "correctly (contrastive scoring). Scored for routing "
                             "context only — never written. E.g. the cancer cluster "
                             "+ hematology for a heme/onc topic.")
    parser.add_argument("--execute", action="store_true",
                        help="actually write to DynamoDB (default: dry run)")
    parser.add_argument("--table-name", default=TABLE_NAME)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--checkpoint", default="score_new_topics_checkpoint.json",
                        help="local resume checkpoint path")
    parser.add_argument("--corpus-file",
                        help="JSON list of pubs to score instead of "
                             "extract_publications() (testing/repeatability)")
    parser.add_argument("--rubric-file", default="",
                        help="path to a topic-specific scoring rubric appended to "
                             "the screening/dense prompts. If omitted, auto-loads "
                             "config/rubrics/<topic>.txt for a single target topic.")
    parser.add_argument("--verify-fidelity", action="store_true",
                        help="score a sample single-topic AND full-taxonomy, "
                             "report the per-topic score delta, then exit")
    parser.add_argument("--sample", type=int, default=40,
                        help="sample size for --verify-fidelity")
    args = parser.parse_args()

    target_ids = [t.strip() for t in args.topics.split(",") if t.strip()]
    assert target_ids, "--topics must name at least one topic id"

    context_ids = [t.strip() for t in args.context_topics.split(",") if t.strip()]
    overlap = set(target_ids) & set(context_ids)
    assert not overlap, f"--context-topics overlaps --topics: {sorted(overlap)}"

    taxonomy = load_taxonomy(REPO_ROOT)
    taxonomy_version = taxonomy["taxonomy_version"]
    assert_topics_in_taxonomy(target_ids, taxonomy)
    assert_topics_in_taxonomy(context_ids, taxonomy)

    # Scoring view = the new topic(s) + competitor context topics. Screening +
    # dense run over all of them so the model routes a borderline paper to its
    # true topic (contrastive); only the target topics' scores are kept/written.
    view = single_topic_view(taxonomy, target_ids + context_ids)
    assert view["topics"], f"no taxonomy entries matched {target_ids}"
    int_to_id, id_to_int = sp.build_topic_index(view)

    # Topic-specific rubric: explicit --rubric-file, else config/rubrics/<topic>.txt
    # for a single target. The rubric is the primary lever against single-topic
    # over-inclusion; contrastive --context-topics is an alternative if no rubric.
    rubric = ""
    rubric_path = None
    if args.rubric_file:
        rubric_path = Path(args.rubric_file)
    elif len(target_ids) == 1:
        cand = REPO_ROOT / "config" / "rubrics" / f"{target_ids[0]}.txt"
        if cand.exists():
            rubric_path = cand
    if rubric_path:
        rubric = _load_text(rubric_path)

    print(f"Target topics ({len(target_ids)}): {', '.join(target_ids)}")
    if context_ids:
        print(f"Context topics ({len(context_ids)}): {', '.join(context_ids)}")
    print(f"Rubric: {rubric_path if rubric else '(none)'}")
    print(f"Taxonomy version: {taxonomy_version} ({len(taxonomy['topics'])} topics)")

    # DDB client + genuinely-new guard UP FRONT — fail before spending any
    # Bedrock if the topic already has rows (DDB is also the FACULTY# source).
    client = get_dynamo_client()
    assert_no_existing_ddb_rows(client, args.table_name, target_ids)

    # Corpus (ReciterDB + DDB synopsis join, or a file for testing).
    if args.corpus_file:
        corpus = _load_json(Path(args.corpus_file))
    else:
        corpus = sp.extract_publications()
    corpus_by_pmid = {str(p["pmid"]): p for p in corpus}
    print(f"Corpus: {len(corpus)} publications")

    # Fidelity-only mode: cheap sample comparison, no writes, no DB regen.
    if args.verify_fidelity:
        sample = corpus[:max(0, args.sample)]
        verify_fidelity(sample, taxonomy, target_ids, view, int_to_id, id_to_int, rubric)
        return

    # Author mapping + faculty metadata fresh from ReciterDB (the canonical
    # source — the *.json files are pipeline-run byproducts, often stale/empty).
    print("Regenerating author_mapping + faculty_metadata from ReciterDB ...")
    author_mapping = sp.extract_author_mapping()
    faculty_metadata = sp.extract_faculty_metadata()

    # Cost capture wraps the whole scoring pass (Bedrock calls only).
    acc = CostAccumulator()
    set_cost_accumulator(acc)
    try:
        scored = asyncio.run(score_corpus(
            corpus, view, int_to_id, id_to_int, target_ids,
            concurrency=args.concurrency,
            checkpoint_path=Path(args.checkpoint),
            rubric=rubric,
        ))
    finally:
        set_cost_accumulator(None)

    qualifying_pmids = len(scored)
    topic_rows = build_additive_topic_rows(
        scored, corpus_by_pmid, author_mapping, taxonomy_version
    )
    faculty_rows = build_merged_faculty_rows(
        scored, author_mapping, faculty_metadata, taxonomy_version,
        fetch_existing=lambda cwid: fetch_faculty_existing(client, args.table_name, cwid),
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

    # Re-check just before the write (cheap belt-and-suspenders vs a concurrent
    # writer landing rows since the up-front guard).
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

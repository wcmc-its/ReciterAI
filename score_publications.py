"""
Publication scoring pipeline for ReCiter AI Chatbot.

Extracts ~10K publications from ReciterDB and scores them against the frozen
taxonomy via two-pass Bedrock calls:
  1. Haiku screening — one call per publication, scores all topics simultaneously
  2. Sonnet dense scoring — only topics that passed 0.3 screening threshold

Processing tracker in DynamoDB enables checkpoint/resume (D-09) so the
pipeline can survive interruptions and restart cleanly.

Usage:
    python3 score_publications.py                     # Full run
    python3 score_publications.py --test 50           # Test batch (D-14)
    python3 score_publications.py --concurrency 20    # Adjust concurrency

Outputs:
    scoring_results.json     -- Scored publication data for load_dynamodb.py
    faculty_metadata.json    -- Faculty profiles for load_dynamodb.py

Security (T-03-01, T-03-02):
- DB and AWS credentials via environment variables only.
- Output files not committed to git (.gitignore).
- Publication data (titles, abstracts, synopses) is publicly available PubMed content.
"""

import hashlib
import json
import re
import sys
import os
import asyncio
import logging
import argparse
import time
from decimal import Decimal
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime, timezone

from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent))
from utils.bedrock_client import (
    BedrockClient, BedrockEmptyContentError,
    HAIKU_MODEL, SONNET_MODEL, MODEL_IDS_BY_STAGE,
)
from utils.openai_client import (
    GPT5_MODEL as OPENAI_FALLBACK_MODEL,
    call_with_retry as openai_call_with_retry,
    get_default_client as get_openai_client,
)
from utils.dynamodb_helpers import (
    get_dynamo_client, get_table, TABLE_NAME, mark_processing,
    mark_processing_failed, get_processing_status, to_decimal, make_score_sk
)
from utils.sql_queries import (
    PUBLICATION_EXTRACTION_SQL, FACULTY_METADATA_SQL,
    AUTHOR_MAPPING_SQL, get_db_connection,
)
from utils.stage_records import (
    build_complete_record,
    build_skipped_record,
    compute_input_hash,
    should_skip,
    write_complete,
    write_failed,
    write_skipped,
)
from utils.event_records import load_thresholds, write_uncovered_pmid
from utils.iso_clock import now_iso

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)

# Taxonomy file (taxonomy_v2 is the reviewed, approved taxonomy)
TAXONOMY_FILE = Path(__file__).parent / "taxonomy_v2.json"

# Screening threshold — topics below this score are excluded from dense
# scoring. Lifted to config/thresholds.json `score_floor` (G-18) so the
# pre-dense filter and the assign_subtopics/backfill_topic filters share
# one knob.
SCREENING_THRESHOLD = load_thresholds()["score_floor"]

# Target failure rate (D-11). Lifted to config/thresholds.json
# `target_failure_rate` (#57 Tier B-1).
TARGET_FAILURE_RATE = float(load_thresholds()["target_failure_rate"])

# Phase 10 STAGE# substrate (D-07). Run-level memoization uses GLOBAL scope;
# per-PMID failure records use scope = "pmid:{pmid}" so a single bad PMID
# does not pollute the run-level skip cache.
STAGE_NAME = "score_publications"
STAGE_SCOPE_GLOBAL = "GLOBAL"

# Stubbed cost — Phase 10 follow-up wires a Bedrock usage counter through
# from BedrockClient. Skip rows continue to use SKIP_COST_OBSERVED_USD.
SCORE_COST_USD = Decimal("0")

STAGE_MODEL_IDS = [
    MODEL_IDS_BY_STAGE["screening"],
    MODEL_IDS_BY_STAGE["scoring"],
]




def _pmid_scope(pmid: str) -> str:
    return f"pmid:{pmid}"


def compute_score_input_hash(
    *,
    taxonomy_version: str,
    pmids: list,
) -> str:
    """Content-addressed input hash for the score_publications run.

    `pmid_set_sha256` collapses the (potentially large) PMID set so two
    runs over identical inputs collide on the substrate skip cache.
    Different delta windows naturally produce different PMID sets, so
    the window timestamp does not need to enter the hash separately.
    """
    pmid_set_hash = hashlib.sha256(
        ",".join(sorted({str(p) for p in pmids})).encode("utf-8")
    ).hexdigest()
    return compute_input_hash(
        STAGE_NAME,
        {
            "taxonomy_version": taxonomy_version,
            "pmid_set_sha256": pmid_set_hash,
            "model_ids": STAGE_MODEL_IDS,
        },
    )


def _per_pmid_input_hash(taxonomy_version: str, pmid: str) -> str:
    return compute_input_hash(
        STAGE_NAME,
        {
            "taxonomy_version": taxonomy_version,
            "pmid": str(pmid),
            "model_ids": STAGE_MODEL_IDS,
        },
    )


def _maybe_write_uncovered_event(
    *,
    stage_table,
    thresholds: dict | None,
    pmid: str,
    taxonomy_version: str,
    screening_scores: dict,
    dense_scores: dict,
) -> None:
    """Write an UNCOVERED_PMID# row if top topic score is below the floor.

    No-op when `stage_table` is None (dry-run / no AWS creds) or when
    `thresholds` is None (legacy callers that opted out). Failures are
    logged and swallowed — feedback-event writes must not break scoring.
    """
    if stage_table is None or thresholds is None:
        return
    floor = thresholds.get("uncovered_score_floor")
    if floor is None:
        return
    top_topic_score, top3 = _evaluate_uncovered(screening_scores, dense_scores)
    if top_topic_score >= float(floor):
        return
    try:
        write_uncovered_pmid(
            stage_table,
            pmid=pmid,
            taxonomy_version=taxonomy_version,
            top_topics=top3,
        )
    except Exception as exc:
        logger.warning(f"Failed to write UNCOVERED_PMID# row for pmid={pmid}: {exc}")


def _evaluate_uncovered(
    screening_scores: dict,
    dense_scores: dict,
) -> tuple[float, list[tuple[str, float]]]:
    """Compute the top topic score and top-3 (topic_id, score) tuples for the
    UNCOVERED_PMID# event check (spec §9).

    Prefers dense (Sonnet-refined) scores when present; falls back to
    screening scores for the early-exit case where no topics cleared the
    0.3 screening threshold. Both branches surface "the best the model
    could fit" so the drift evaluator can decide whether the corpus has
    drifted out from under the taxonomy.
    """
    if dense_scores:
        scored = [
            (tid, float(v.get("score", 0.0))) for tid, v in dense_scores.items()
        ]
    elif screening_scores:
        scored = [(tid, float(s)) for tid, s in screening_scores.items()]
    else:
        return 0.0, []
    scored.sort(key=lambda t: (-t[1], t[0]))
    return scored[0][1], scored[:3]


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class ScoringResult:
    """Holds the scoring outcome for a single publication.

    Publication-level only — no author data. Author mapping is separate
    so we can expand to middle authors without rescoring.
    """

    pmid: str
    synopsis: str
    abstract: str
    screening_scores: dict = field(default_factory=dict)  # {topic_id: score}
    dense_scores: dict = field(default_factory=dict)      # {topic_id: {score, rationale}}
    status: str = "pending"
    error: str = None
    # Set to the fallback model ID when the dense pass fell back from Sonnet
    # (e.g. on BedrockEmptyContentError from content_filtered). None when
    # Sonnet handled the dense scoring directly. Surfaces in the per-PMID
    # serialized output and DDB PROCESSING# tracker so downstream consumers
    # (drift evaluator, ad-hoc query) can identify fallback-scored PMIDs.
    fallback_model: str | None = None
    # True when the dense pass hit Sonnet's content filter, regardless of
    # whether the OpenAI fallback then recovered the PMID. Distinct from
    # `fallback_model`: a content-filter that the fallback ALSO failed leaves
    # fallback_model None but content_filtered True. Aggregated into the
    # run-level `content_filter_count` on the score STAGE# row.
    content_filtered: bool = False


# ---------------------------------------------------------------------------
# Phase 1: Extract publications from ReciterDB
# ---------------------------------------------------------------------------

def extract_publications(delta_since: str | None = None) -> list:
    """
    Extract publications from ReciterDB using PUBLICATION_EXTRACTION_SQL.

    Returns a list of dicts with keys: pmid, synopsis, abstract.
    Minimal — only what's needed for LLM scoring. Article metadata
    is looked up from ReciterDB at query time.

    When `delta_since` is supplied (hot-path mode), restricts the result
    set to publications added to Entrez on or after that ISO-8601
    timestamp. Mirrors the orchestrator's delta query at
    `pipeline_hot/orchestrator.py`. Without this filter, every hot-path
    run extracts the entire scoreable corpus and the Lambda hits its
    15-min wall before reaching the score loop.
    """
    sql = PUBLICATION_EXTRACTION_SQL
    params: dict[str, str] = {}
    if delta_since:
        # Inject the delta filter immediately before ORDER BY. The
        # base SQL is a frozen constant in utils/sql_queries.py; this
        # is a localized override for the hot path. `a1.` alias matches
        # the SQL's table alias for analysis_summary_article.
        sql = sql.replace(
            "ORDER BY a1.pmid DESC",
            "AND a1.datePublicationAddedToEntrez >= :since "
            "ORDER BY a1.pmid DESC",
        )
        params["since"] = delta_since
    conn = get_db_connection()
    try:
        from sqlalchemy import text
        result = conn.execute(text(sql), params)
        rows = result.mappings().all()
        publications = [dict(row) for row in rows]
        if delta_since:
            print(f"Extracted {len(publications)} publications from ReciterDB "
                  f"(delta-since={delta_since})")
        else:
            print(f"Extracted {len(publications)} publications from ReciterDB")
        return publications
    finally:
        conn.close()


def extract_publications_by_pmids(pmids: list[str]) -> list:
    """Extract publications for an explicit PMID list (no date filter).

    Used by the `--rescore-pmids` CLI flag for targeted operator recovery
    of PMIDs that fall outside the weekly hot-path delta window. The
    `articleYear >= 2020` filter from PUBLICATION_EXTRACTION_SQL still
    applies — pre-2020 PMIDs are silently excluded just as they are
    elsewhere in the pipeline.

    Args:
        pmids: List of PMID strings. Empty list returns [].

    Returns:
        List of publication dicts (same shape as extract_publications).
        PMIDs not present in `analysis_summary_article` (or filtered out
        by the year cutoff / synopsis-existence requirement in
        PUBLICATION_EXTRACTION_SQL) are silently omitted; caller should
        compare the returned length against the input length to detect
        missing PMIDs.
    """
    if not pmids:
        return []
    from sqlalchemy import bindparam, text

    sql = PUBLICATION_EXTRACTION_SQL.replace(
        "ORDER BY a1.pmid DESC",
        "AND a1.pmid IN :pmid_list ORDER BY a1.pmid DESC",
    )
    conn = get_db_connection()
    try:
        stmt = text(sql).bindparams(bindparam("pmid_list", expanding=True))
        result = conn.execute(stmt, {"pmid_list": pmids})
        rows = result.mappings().all()
        publications = [dict(row) for row in rows]
        print(
            f"Extracted {len(publications)} publications from ReciterDB "
            f"(rescore-pmids: {len(pmids)} requested, "
            f"{len(pmids) - len(publications)} not found / pre-2020 / no synopsis)"
        )
        return publications
    finally:
        conn.close()


def extract_faculty_metadata() -> dict:
    """
    Extract faculty metadata from ReciterDB using FACULTY_METADATA_SQL.

    Returns a dict keyed by cwid:
        {cwid: {"name": ..., "department": ..., "h_index": ...,
                "article_count": ..., "first_author_count": ..., "last_author_count": ...}}
    """
    conn = get_db_connection()
    try:
        from sqlalchemy import text
        result = conn.execute(text(FACULTY_METADATA_SQL))
        rows = result.mappings().all()
        metadata = {}
        for row in rows:
            r = dict(row)
            cwid = str(r['cwid'])
            metadata[cwid] = {
                'name': r.get('name', ''),
                'department': r.get('department', ''),
                'h_index': int(r.get('h_index') or 0),
                'article_count': int(r.get('article_count') or 0),
                'first_author_count': int(r.get('first_author_count') or 0),
                'last_author_count': int(r.get('last_author_count') or 0),
            }
        print(f"Extracted metadata for {len(metadata)} faculty members from ReciterDB")
        return metadata
    finally:
        conn.close()


def extract_author_mapping() -> dict:
    """
    Extract author-to-publication mapping from ReciterDB using AUTHOR_MAPPING_SQL.

    Returns {pmid_str: [{"cwid": str, "position": str}, ...]}

    Separate from publication extraction so author scope can be expanded
    (e.g., to middle authors) without rescoring publications.
    """
    conn = get_db_connection()
    try:
        from sqlalchemy import text
        result = conn.execute(text(AUTHOR_MAPPING_SQL))
        rows = result.mappings().all()

        mapping = {}
        for row in rows:
            pmid = str(row['pmid'])
            if pmid not in mapping:
                mapping[pmid] = []
            mapping[pmid].append({
                'cwid': str(row['cwid']),
                'position': str(row.get('authorPosition', '')),
            })

        total_authors = sum(len(v) for v in mapping.values())
        print(f"Extracted author mapping: {total_authors} author-publication links "
              f"across {len(mapping)} publications")
        return mapping
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Phase 2: Prompt builders
# ---------------------------------------------------------------------------

def build_topic_index(taxonomy: dict) -> tuple:
    """Build bidirectional int <-> topic_id mappings for compact LLM output.

    Returns (int_to_id, id_to_int) where keys are strings.
    """
    int_to_id = {str(i): t['id'] for i, t in enumerate(taxonomy['topics'])}
    id_to_int = {t['id']: str(i) for i, t in enumerate(taxonomy['topics'])}
    return int_to_id, id_to_int


def make_screening_prompt(pub: dict, taxonomy: dict) -> str:
    """
    Build the Haiku screening prompt for a single publication.

    Uses integer topic IDs for compact output — mapped back to names after parsing.
    """
    topics_list = "\n".join(
        f"- {i}: {t['label']} — {t['description']}"
        for i, t in enumerate(taxonomy['topics'])
    )

    abstract = pub.get('abstract') or ''
    synopsis = pub.get('synopsis') or ''

    return f"""Score this publication's relevance to each research topic. This is a screening pass — err on the side of inclusion. A second pass will refine scores.

Publication:
Synopsis: {synopsis}
Abstract: {abstract}

Topics:
{topics_list}

Scoring guidance:
- 0.0: No connection whatsoever
- 0.1-0.2: Tangential or incidental mention
- 0.3-0.5: Meaningful but secondary relevance (methods used, population studied, comorbidity addressed)
- 0.6-0.8: Substantial relevance — a core theme of this publication
- 0.9-1.0: Primary focus of this publication

When in doubt, score higher. Topics scoring >= 0.3 advance to detailed review.
Return ONLY a JSON object with integer topic number keys and float values.
Example: {{"0": 0.85, "14": 0.20, "32": 0.35}}"""


def make_dense_prompt(pub: dict, passed_topics: dict, taxonomy: dict, id_to_int: dict) -> str:
    """
    Build the Sonnet dense scoring prompt for a single publication.

    Only includes topics that passed the 0.3 screening threshold.
    Uses integer topic IDs for compact output.
    """
    topic_index = {t['id']: t for t in taxonomy['topics']}

    topics_list = "\n".join(
        f"- {id_to_int[topic_id]}: {topic_index[topic_id]['label']} — {topic_index[topic_id]['description']}"
        for topic_id in passed_topics
        if topic_id in topic_index and topic_id in id_to_int
    )

    abstract = pub.get('abstract') or ''
    synopsis = pub.get('synopsis') or ''

    return f"""You are calibrating research topic relevance scores for a publication.

Publication:
Synopsis: {synopsis}
Abstract: {abstract}

The following topics passed initial screening. Provide a calibrated relevance score (0.0-1.0) and a short rationale (max 80 characters) for each.

Topics to score:
{topics_list}

Return ONLY a JSON object with integer topic number keys:
{{"14": {{"score": 0.85, "rationale": "RCT on drug-eluting scaffold for PAD"}}, ...}}

Rules:
- Each rationale must be 80 characters or fewer
- A 0.9 means this publication is a primary contribution to this domain"""


# ---------------------------------------------------------------------------
# Phase 2: Dense scoring with content-filter fallback
# ---------------------------------------------------------------------------

_OPENAI_FALLBACK_SYSTEM_PROMPT = (
    "You are a topic-classification assistant. Score the relevance of the "
    "given publication against each topic on a 0.0-1.0 scale. Return ONLY "
    "a JSON object — no markdown fences, no commentary."
)


def _dense_score(bedrock: BedrockClient, dense_prompt: str) -> tuple[dict, str | None]:
    """Run the dense scoring pass for one publication.

    Tries Sonnet 4.6 first. On BedrockEmptyContentError (Anthropic's safety
    filter returning empty content for biomedical animal-model abstracts,
    etc.), falls back once to OpenAI gpt-5.1 against the same prompt. The
    fallback is the only mitigation that survived empirical testing — see
    the sibling planning doc `sonnet-content-filter-on-dense-scoring.md`
    for the chunking / Opus / system-framing probes that all failed.

    Returns:
        (raw_dense_dict, fallback_model) where:
        - raw_dense_dict: JSON dict from the LLM with integer topic ID keys
          and either a float score or a {"score": float, "rationale": str} value.
        - fallback_model: None when Sonnet handled the call directly,
          OPENAI_FALLBACK_MODEL when the fallback fired.

    Raises:
        Any exception from the OpenAI path (JSONDecodeError, openai.* errors)
        if the fallback also fails. score_one_publication's outer except clause
        catches and marks the PMID failed with the OpenAI error_code.
    """
    try:
        raw_dense = bedrock.call_json(
            model=SONNET_MODEL,
            messages=[{"role": "user", "content": dense_prompt}],
        )
        return raw_dense, None
    except BedrockEmptyContentError as filter_err:
        logger.info(
            "Sonnet content-filtered on dense scoring "
            f"(stopReason={filter_err.stop_reason!r}); "
            f"falling back to {OPENAI_FALLBACK_MODEL}"
        )
        try:
            openai = get_openai_client()
            completion = openai_call_with_retry(
                openai,
                model=OPENAI_FALLBACK_MODEL,
                system_prompt=_OPENAI_FALLBACK_SYSTEM_PROMPT,
                user_prompt=dense_prompt,
                response_format={"type": "json_object"},
                max_completion_tokens=4096,
            )
            content = (completion.choices[0].message.content or "").strip()
            cleaned = re.sub(r'```json\n?|\n?```', '', content).strip()
            raw_dense = json.loads(cleaned)
            return raw_dense, OPENAI_FALLBACK_MODEL
        except Exception as fallback_err:
            # The fallback also failed. Tag the exception so the caller can
            # still count this PMID as a content-filter event — the
            # run-level content_filter_count must see both outcomes, and a
            # filtered-then-failed PMID is exactly the quarantine candidate
            # the hot-path retry sweep cares about.
            fallback_err.reciterai_content_filtered = True
            raise


# ---------------------------------------------------------------------------
# Phase 2: Core scoring function
# ---------------------------------------------------------------------------

def score_one_publication(
    pub: dict,
    bedrock: BedrockClient,
    taxonomy: dict,
    dynamo_client,
    table_name: str,
    int_to_id: dict,
    id_to_int: dict,
    stage_table=None,
    thresholds: dict | None = None,
) -> ScoringResult:
    """
    Score a single publication via two-pass Bedrock calls.

    Pass 1 (Haiku): Screen all topics via integer IDs, returns {int: score}.
    Pass 2 (Sonnet): Dense-score topics >= 0.3 threshold only.

    Integer IDs are mapped back to topic_id strings before storing results.
    """
    pmid = str(pub['pmid'])
    result = ScoringResult(
        pmid=pmid,
        synopsis=str(pub.get('synopsis') or ''),
        abstract=str(pub.get('abstract') or ''),
    )

    t_pmid_start = time.monotonic()
    pmid_started_at = now_iso()

    try:
        taxonomy_version = taxonomy['taxonomy_version']

        # Mark as pending in DynamoDB processing tracker (D-09)
        mark_processing(dynamo_client, table_name, pmid, 'pending', taxonomy_version)

        # --- Screening pass (Haiku) — returns integer topic IDs ---
        screening_prompt = make_screening_prompt(pub, taxonomy)
        raw_screening = bedrock.call_json(
            model=HAIKU_MODEL,
            messages=[{"role": "user", "content": screening_prompt}],
        )

        # Parse screening result: map int IDs back to topic names
        screening_scores = {}
        for int_id, score in raw_screening.items():
            topic_id = int_to_id.get(str(int_id))
            if not topic_id:
                logger.debug(f"Unknown topic int ID {int_id}, skipping")
                continue
            try:
                screening_scores[topic_id] = float(score)
            except (TypeError, ValueError):
                logger.debug(f"Skipping malformed screening score for topic {int_id}: {score}")

        result.screening_scores = screening_scores

        # Filter topics that passed 0.3 threshold
        passed_topics = {
            tid: s for tid, s in screening_scores.items()
            if s >= SCREENING_THRESHOLD
        }

        # Mark as screened with passed topic list
        mark_processing(
            dynamo_client, table_name, pmid, 'screened', taxonomy_version,
            screened_at=datetime.now(timezone.utc).isoformat(),
            screening_passed_topics=list(passed_topics.keys()),
        )

        # If no topics passed, this publication is not relevant — mark complete
        if not passed_topics:
            mark_processing(
                dynamo_client, table_name, pmid, 'complete', taxonomy_version,
                scored_at=datetime.now(timezone.utc).isoformat(),
                screening_passed_topics=[],
            )
            _maybe_write_uncovered_event(
                stage_table=stage_table,
                thresholds=thresholds,
                pmid=pmid,
                taxonomy_version=taxonomy_version,
                screening_scores=screening_scores,
                dense_scores={},
            )
            result.status = 'complete'
            return result

        # --- Dense scoring pass (Sonnet, with OpenAI fallback on content_filter) ---
        dense_prompt = make_dense_prompt(pub, passed_topics, taxonomy, id_to_int)
        raw_dense, fallback_used = _dense_score(bedrock, dense_prompt)
        if fallback_used:
            result.fallback_model = fallback_used
            # Fallback fired ⇒ Sonnet content-filtered (and was recovered).
            result.content_filtered = True

        # Parse dense result: map int IDs back to topic names
        dense_scores = {}
        for int_id, value in raw_dense.items():
            topic_id = int_to_id.get(str(int_id))
            if not topic_id:
                logger.debug(f"Unknown topic int ID {int_id} in dense scores, skipping")
                continue
            try:
                if isinstance(value, dict):
                    dense_scores[topic_id] = {
                        'score': float(value.get('score', 0.0)),
                        'rationale': str(value.get('rationale', '')),
                    }
                else:
                    dense_scores[topic_id] = {
                        'score': float(value),
                        'rationale': '',
                    }
            except (TypeError, ValueError):
                logger.debug(f"Skipping malformed dense score for topic {topic_id}: {value}")

        result.dense_scores = dense_scores

        # Mark as complete; persist fallback_model when the OpenAI path
        # produced these dense scores so the DDB tracker records which
        # PMIDs were scored under the fallback contract.
        complete_kwargs = dict(
            scored_at=datetime.now(timezone.utc).isoformat(),
            screening_passed_topics=list(passed_topics.keys()),
        )
        if result.fallback_model:
            complete_kwargs['fallback_model'] = result.fallback_model
        mark_processing(
            dynamo_client, table_name, pmid, 'complete', taxonomy_version,
            **complete_kwargs,
        )
        _maybe_write_uncovered_event(
            stage_table=stage_table,
            thresholds=thresholds,
            pmid=pmid,
            taxonomy_version=taxonomy_version,
            screening_scores=screening_scores,
            dense_scores=dense_scores,
        )
        result.status = 'complete'

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Failed to score pmid={pmid}: {error_msg}")
        # A content-filter whose OpenAI fallback also failed: _dense_score
        # tags the exception so this filtered-then-failed PMID still counts
        # toward the run-level content_filter_count.
        if getattr(e, "reciterai_content_filtered", False):
            result.content_filtered = True
        try:
            # mark_processing_failed is an UpdateItem with ADD retry_count
            # :one — the counter survives across re-score attempts, which is
            # what lets the hot-path retry sweep quarantine an un-fixable
            # PMID instead of retrying it forever. It also stamps failed_at
            # for the sweep's age filter.
            mark_processing_failed(
                dynamo_client, table_name, pmid,
                error=error_msg,
                taxonomy_version=taxonomy.get('taxonomy_version', 'unknown'),
            )
        except Exception as dynamo_err:
            logger.error(f"Failed to write failed status to DynamoDB for pmid={pmid}: {dynamo_err}")
        if stage_table is not None:
            try:
                tv = taxonomy.get('taxonomy_version', 'unknown')
                write_failed(
                    stage_table,
                    stage=STAGE_NAME,
                    scope=_pmid_scope(pmid),
                    input_hash=_per_pmid_input_hash(tv, pmid),
                    error_code=type(e).__name__,
                    error_message=error_msg[:1000],
                    started_at=pmid_started_at,
                    completed_at=now_iso(),
                    duration_ms=int((time.monotonic() - t_pmid_start) * 1000),
                    cost_observed_usd=SCORE_COST_USD,
                    model_ids_snapshot=STAGE_MODEL_IDS,
                )
            except Exception as stage_err:
                logger.error(f"Failed to write STAGE# failed row for pmid={pmid}: {stage_err}")
        result.status = 'failed'
        result.error = error_msg

    return result


# ---------------------------------------------------------------------------
# Phase 3: Checkpoint/resume + async concurrent scoring
# ---------------------------------------------------------------------------

def get_unscored_publications(publications: list, dynamo_client, table_name: str) -> list:
    """
    Filter publications to those not yet fully scored (checkpoint/resume — D-09).

    Queries DynamoDB processing tracker for all PMIDs and returns only those
    with status != 'complete'.

    Args:
        publications: List of publication dicts from ReciterDB extraction.
        dynamo_client: boto3 DynamoDB client.
        table_name: DynamoDB table name.

    Returns:
        Filtered list of publications that still need scoring.
    """
    pmids = [str(pub['pmid']) for pub in publications]
    status_map = get_processing_status(dynamo_client, table_name, pmids)

    completed_pmids = {pmid for pmid, status in status_map.items() if status == 'complete'}
    unscored = [pub for pub in publications if str(pub['pmid']) not in completed_pmids]

    skipped = len(publications) - len(unscored)
    if skipped > 0:
        print(f"Skipping {skipped} already-scored publications, {len(unscored)} remaining")
    else:
        print(f"No previously scored publications found — scoring all {len(unscored)}")

    return unscored


async def score_batch_async(
    publications: list,
    bedrock: BedrockClient,
    taxonomy: dict,
    dynamo_client,
    table_name: str,
    int_to_id: dict,
    id_to_int: dict,
    concurrency: int = 15,
    stage_table=None,
    thresholds: dict | None = None,
) -> list:
    """
    Score publications concurrently using asyncio with a semaphore.

    Uses asyncio.to_thread() to wrap synchronous boto3 calls.
    """
    semaphore = asyncio.Semaphore(concurrency)
    success_count = 0
    failure_count = 0

    pbar = tqdm(
        total=len(publications),
        desc="Scoring publications",
        unit="pub",
        dynamic_ncols=True,
    )

    async def score_one_async(pub):
        nonlocal success_count, failure_count
        async with semaphore:
            result = await asyncio.to_thread(
                score_one_publication, pub, bedrock, taxonomy,
                dynamo_client, table_name, int_to_id, id_to_int,
                stage_table, thresholds,
            )
            if result.status == 'failed':
                failure_count += 1
            else:
                success_count += 1
            pbar.update(1)
            pbar.set_postfix(ok=success_count, fail=failure_count)
            return result

    tasks = [score_one_async(pub) for pub in publications]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    pbar.close()

    # Filter out unexpected exceptions (should not happen — score_one_publication
    # handles all exceptions internally, but gather() with return_exceptions=True
    # can still surface task-level errors)
    results = []
    for r in raw_results:
        if isinstance(r, Exception):
            logger.error(f"Unexpected task exception: {r}")
            failure_count += 1
        else:
            results.append(r)

    return results


# ---------------------------------------------------------------------------
# Output serialization
# ---------------------------------------------------------------------------

def serialize_results(results: list) -> list:
    """
    Serialize ScoringResult objects to dicts for scoring_results.json.

    Only includes publications with at least one dense score (status == 'complete'
    and has dense_scores). Publications where no topic passed screening have
    status='complete' but empty dense_scores — these are excluded.

    Publication-level only — author mapping is saved separately.
    """
    output = []
    for r in results:
        if r.status == 'complete' and r.dense_scores:
            entry = {
                'pmid': r.pmid,
                'screening_scores': r.screening_scores,
                'dense_scores': r.dense_scores,
            }
            if r.fallback_model:
                entry['fallback_model'] = r.fallback_model
            output.append(entry)
    return output


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    parser = argparse.ArgumentParser(
        description='Score WCM publications against the research taxonomy via Bedrock.'
    )
    parser.add_argument(
        '--test', type=int, metavar='N',
        help='Score only the first N publications (D-14 small batch testing)'
    )
    parser.add_argument(
        '--concurrency', type=int, default=15,
        help='Maximum concurrent Bedrock calls (default: 15, T-03-03)'
    )
    parser.add_argument(
        '--delta-since', metavar='ISO8601', default=None,
        help=(
            'Hot-path delta cutoff. Recorded in the STAGE# input_hash so '
            'distinct delta windows do not share skip cache. PMID-set '
            'resolution is the orchestrator (pipeline_hot) responsibility; '
            'this script trusts the inbound publication set.'
        ),
    )
    parser.add_argument(
        '--emit-envelope', action='store_true',
        help=(
            'Phase 10 D-07 hot-path mode. On exit, emit the STAGE# '
            'complete/skipped record as JSON on stdout instead of writing '
            'to DynamoDB. The Step Functions DynamoDB:PutItem SDK '
            'integration consumes the envelope and persists the row.'
        ),
    )
    parser.add_argument(
        '--rescore-pmids', metavar='PMID1,PMID2,...', default=None,
        help=(
            'Comma-separated list of PMIDs to rescore. Bypasses '
            '--delta-since AND the PROCESSING# checkpoint cache so even '
            'already-complete PMIDs get a fresh scoring pass. Useful for '
            'targeted operator recovery — e.g. flipping content-filter '
            'survivors after a fallback-path change, or nudging PMIDs '
            'that aged out of the 14-day hot-path delta window. Mutually '
            'exclusive with --delta-since.'
        ),
    )
    parser.add_argument(
        '--retry-pmids', metavar='PMID1,PMID2,...', default=None,
        help=(
            'Comma-separated list of PMIDs to union into the score set on '
            'top of the --delta-since date delta. Set by the hot-path '
            'orchestrator retry sweep so failed PMIDs that aged out of the '
            'publication-date delta window still get re-scored. Additive '
            '(compatible with --delta-since); unlike --rescore-pmids it '
            'does NOT bypass the PROCESSING# checkpoint or the STAGE# skip '
            'cache — a failed row is status != complete, so the normal '
            'checkpoint re-scores it once it is in the set. Mutually '
            'exclusive with --rescore-pmids.'
        ),
    )
    args = parser.parse_args()

    if args.rescore_pmids and args.delta_since:
        parser.error(
            "--rescore-pmids and --delta-since are mutually exclusive — "
            "they are competing scoping mechanisms (explicit list vs. date filter)"
        )

    if args.rescore_pmids and args.retry_pmids:
        parser.error(
            "--rescore-pmids and --retry-pmids are mutually exclusive — "
            "--rescore-pmids is operator force-recovery (bypasses caches); "
            "--retry-pmids is the orchestrator's additive sweep"
        )

    # --- STAGE# substrate setup (Phase 10 D-07) ---
    stage_started_at = now_iso()
    t_stage_start = time.monotonic()
    stage_table = get_table()

    # --- Phase 10 thresholds (T6 — UNCOVERED_PMID# event floor) ---
    try:
        thresholds = load_thresholds()
    except FileNotFoundError:
        logger.warning(
            "config/thresholds.json missing; skipping UNCOVERED_PMID# event writes"
        )
        thresholds = None

    # --- Load taxonomy (taxonomy_v2 is the reviewed, approved taxonomy) ---
    assert TAXONOMY_FILE.exists(), (
        f"Taxonomy file not found: {TAXONOMY_FILE}\n"
        "Run generate_taxonomy.py first and review the output."
    )
    with open(TAXONOMY_FILE) as f:
        taxonomy = json.load(f)
    assert 'taxonomy_version' in taxonomy, (
        f"taxonomy_version key missing from {TAXONOMY_FILE}"
    )
    print(f"Loaded taxonomy: {taxonomy['taxonomy_version']} "
          f"({len(taxonomy['topics'])} topics)")

    # --- Build topic int <-> id mappings for compact LLM output ---
    int_to_id, id_to_int = build_topic_index(taxonomy)

    # --- Initialize clients ---
    bedrock = BedrockClient()
    dynamo_client = get_dynamo_client()

    # --- Phase 1: Extract from ReciterDB ---
    print("\n--- Phase 1: Extracting publications from ReciterDB ---")
    if args.rescore_pmids:
        rescore_pmids = [p.strip() for p in args.rescore_pmids.split(',') if p.strip()]
        if not rescore_pmids:
            print("--rescore-pmids supplied an empty list; nothing to do.")
            return
        print(f"[--rescore-pmids mode] {len(rescore_pmids)} PMIDs requested")
        publications = extract_publications_by_pmids(rescore_pmids)
    else:
        publications = extract_publications(delta_since=args.delta_since)
        if args.retry_pmids:
            # Hot-path retry sweep: union the orchestrator's failed-PMID
            # list into the date delta. Deduplicate against the delta so a
            # PMID that is both recent and failed is not extracted twice.
            retry_pmids = [p.strip() for p in args.retry_pmids.split(',') if p.strip()]
            if retry_pmids:
                existing = {str(p['pmid']) for p in publications}
                extra = [
                    pub for pub in extract_publications_by_pmids(retry_pmids)
                    if str(pub['pmid']) not in existing
                ]
                print(
                    f"[--retry-pmids] hot-path retry sweep: {len(retry_pmids)} "
                    f"requested, {len(extra)} unioned into the score set "
                    "(rest already in the date delta, or not found / "
                    "pre-2020 / no synopsis)"
                )
                publications = publications + extra
    author_mapping = extract_author_mapping()
    faculty_metadata = extract_faculty_metadata()

    if args.rescore_pmids:
        # Bypass the checkpoint — operator explicitly wants to rescore
        # even already-complete PMIDs. PROCESSING# rows will be overwritten
        # on success; STAGE# substrate appends new per-PMID rows alongside
        # the prior history (audit trail preserved).
        print("\n--- [--rescore-pmids] Bypassing PROCESSING# checkpoint cache ---")
        unscored = publications
    else:
        # --- Checkpoint/resume: skip already-completed publications (D-09) ---
        print("\n--- Checkpoint/resume: checking DynamoDB processing tracker ---")
        unscored = get_unscored_publications(publications, dynamo_client, TABLE_NAME)

    # --- Apply --test flag (D-14) ---
    if args.test:
        unscored = unscored[:args.test]
        print(f"[--test mode] Limiting run to first {args.test} publications")

    # --- Phase 10 D-07: STAGE# input_hash + should_skip gate ---
    input_hash = compute_score_input_hash(
        taxonomy_version=taxonomy['taxonomy_version'],
        pmids=[str(p['pmid']) for p in unscored],
    )
    if args.rescore_pmids:
        # --rescore-pmids explicitly forces this run. Bypass the run-level
        # skip cache, otherwise a prior `records_written=0` complete row
        # (e.g. a smoke that finished after every PMID failed) collides
        # with the same input_hash and blocks the rescore. Operator
        # otherwise has to delete the offending STAGE# row by hand.
        skip, prior = False, None
        print(
            "[--rescore-pmids] Bypassing STAGE# substrate skip cache "
            f"(input_hash {input_hash[:12]})"
        )
    else:
        skip, prior = should_skip(
            stage_table,
            stage=STAGE_NAME,
            scope=STAGE_SCOPE_GLOBAL,
            input_hash=input_hash,
        )
    if skip:
        completed_at = now_iso()
        duration_ms = int((time.monotonic() - t_stage_start) * 1000)
        skip_reason = (
            f"input_hash unchanged since prior complete run at "
            f"{prior.get('started_at', '?')}"
        )
        skipped_kwargs = dict(
            stage=STAGE_NAME,
            scope=STAGE_SCOPE_GLOBAL,
            input_hash=input_hash,
            skip_reason=skip_reason,
            started_at=stage_started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            model_ids_snapshot=STAGE_MODEL_IDS,
        )
        if args.emit_envelope:
            print(json.dumps(build_skipped_record(**skipped_kwargs), default=str))
        else:
            write_skipped(stage_table, **skipped_kwargs)
        print(
            f"[STAGE# skip] score_publications skipped — prior complete at "
            f"{prior.get('started_at', '?')} (input_hash {input_hash[:12]})"
        )
        return

    scored_records: list = []
    content_filter_count = 0
    scoring_results_path = Path(__file__).parent / 'scoring_results.json'

    if not unscored:
        print("Nothing to score — all publications already complete.")
    else:
        # --- Phase 2: Score publications via Bedrock ---
        print(f"\n--- Phase 2: Scoring {len(unscored)} publications "
              f"(concurrency={args.concurrency}) ---")
        results = await score_batch_async(
            unscored, bedrock, taxonomy, dynamo_client, TABLE_NAME,
            int_to_id, id_to_int, concurrency=args.concurrency,
            stage_table=stage_table,
            thresholds=thresholds,
        )

        # --- Summary ---
        total = len(results)
        succeeded = sum(1 for r in results if r.status == 'complete')
        failed = sum(1 for r in results if r.status == 'failed')
        failure_rate = failed / total if total > 0 else 0.0
        content_filter_count = sum(1 for r in results if r.content_filtered)

        print(f"\n--- Scoring Summary ---")
        print(f"  Total publications scored: {total}")
        print(f"  Successfully scored:       {succeeded}")
        print(f"  Failed:                    {failed}")
        print(f"  Failure rate:              {failure_rate * 100:.2f}%")
        print(f"  Content-filtered (Sonnet): {content_filter_count}")

        if failure_rate > TARGET_FAILURE_RATE:
            print(f"\n  WARNING: Failure rate {failure_rate * 100:.2f}% exceeds "
                  f"{TARGET_FAILURE_RATE * 100:.0f}% target (D-11). "
                  "Check logs for error details.")

        # --- Phase 3: Save outputs ---
        print("\n--- Phase 3: Saving outputs ---")
        scored_records = serialize_results(results)

        # Hot-path envelope mode emits the run record to stdout; the cold
        # path / local CLI still writes JSON artifacts for load_dynamodb.py.
        # Lambda's /var/task is read-only, so we must skip the writes there.
        if not args.emit_envelope:
            output = {
                'taxonomy_version': taxonomy['taxonomy_version'],
                'scored_publications': scored_records,
            }
            with open(scoring_results_path, 'w') as f:
                json.dump(output, f, indent=2, default=str)
            print(f"Saved {len(scored_records)} scored publications -> {scoring_results_path}")

    author_mapping_path = Path(__file__).parent / 'author_mapping.json'
    faculty_metadata_path = Path(__file__).parent / 'faculty_metadata.json'
    if not args.emit_envelope:
        with open(author_mapping_path, 'w') as f:
            json.dump(author_mapping, f, indent=2)
        print(f"Saved author mapping ({len(author_mapping)} publications) -> {author_mapping_path}")

        with open(faculty_metadata_path, 'w') as f:
            json.dump(faculty_metadata, f, indent=2)
        print(f"Saved {len(faculty_metadata)} faculty profiles -> {faculty_metadata_path}")

        print("\nResults saved. Run load_dynamodb.py next.")

    # --- Phase 10 D-07: STAGE# complete row (direct write or envelope emit) ---
    completed_at = now_iso()
    duration_ms = int((time.monotonic() - t_stage_start) * 1000)
    complete_kwargs = dict(
        stage=STAGE_NAME,
        scope=STAGE_SCOPE_GLOBAL,
        input_hash=input_hash,
        started_at=stage_started_at,
        completed_at=completed_at,
        duration_ms=duration_ms,
        cost_observed_usd=SCORE_COST_USD,
        output_pointer=str(scoring_results_path),
        records_written=len(scored_records),
        model_ids_snapshot=STAGE_MODEL_IDS,
        content_filter_count=content_filter_count,
    )
    if args.emit_envelope:
        print(json.dumps(build_complete_record(**complete_kwargs), default=str))
    else:
        write_complete(stage_table, **complete_kwargs)


if __name__ == "__main__":
    asyncio.run(main())

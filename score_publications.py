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

import json
import sys
import os
import asyncio
import logging
import argparse
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime, timezone

from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent))
from utils.bedrock_client import BedrockClient, HAIKU_MODEL, SONNET_MODEL
from utils.dynamodb_helpers import (
    get_dynamo_client, TABLE_NAME, mark_processing,
    get_processing_status, to_decimal, make_score_sk
)
from utils.sql_queries import (
    PUBLICATION_EXTRACTION_SQL, FACULTY_METADATA_SQL,
    AUTHOR_MAPPING_SQL, get_db_connection,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)

# Taxonomy file (taxonomy_v2 is the reviewed, approved taxonomy)
TAXONOMY_FILE = Path(__file__).parent / "taxonomy_v2.json"

# Screening threshold — topics below this score are excluded from dense scoring
SCREENING_THRESHOLD = 0.3

# Target failure rate (D-11)
TARGET_FAILURE_RATE = 0.01  # 1%


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


# ---------------------------------------------------------------------------
# Phase 1: Extract publications from ReciterDB
# ---------------------------------------------------------------------------

def extract_publications() -> list:
    """
    Extract publications from ReciterDB using PUBLICATION_EXTRACTION_SQL.

    Returns a list of dicts with keys: pmid, synopsis, abstract.
    Minimal — only what's needed for LLM scoring. Article metadata
    is looked up from ReciterDB at query time.
    """
    conn = get_db_connection()
    try:
        from sqlalchemy import text
        result = conn.execute(text(PUBLICATION_EXTRACTION_SQL))
        rows = result.mappings().all()
        publications = [dict(row) for row in rows]
        print(f"Extracted {len(publications)} publications from ReciterDB")
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
            result.status = 'complete'
            return result

        # --- Dense scoring pass (Sonnet) — returns integer topic IDs ---
        dense_prompt = make_dense_prompt(pub, passed_topics, taxonomy, id_to_int)
        raw_dense = bedrock.call_json(
            model=SONNET_MODEL,
            messages=[{"role": "user", "content": dense_prompt}],
        )

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

        # Mark as complete
        mark_processing(
            dynamo_client, table_name, pmid, 'complete', taxonomy_version,
            scored_at=datetime.now(timezone.utc).isoformat(),
            screening_passed_topics=list(passed_topics.keys()),
        )
        result.status = 'complete'

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Failed to score pmid={pmid}: {error_msg}")
        try:
            mark_processing(
                dynamo_client, table_name, pmid, 'failed', taxonomy.get('taxonomy_version', 'unknown'),
                error=error_msg[:1000],  # DynamoDB attribute size limit
                retry_count=0,
            )
        except Exception as dynamo_err:
            logger.error(f"Failed to write failed status to DynamoDB for pmid={pmid}: {dynamo_err}")
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
                dynamo_client, table_name, int_to_id, id_to_int
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
            output.append({
                'pmid': r.pmid,
                'screening_scores': r.screening_scores,
                'dense_scores': r.dense_scores,
            })
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
    args = parser.parse_args()

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
    publications = extract_publications()
    author_mapping = extract_author_mapping()
    faculty_metadata = extract_faculty_metadata()

    # --- Checkpoint/resume: skip already-completed publications (D-09) ---
    print("\n--- Checkpoint/resume: checking DynamoDB processing tracker ---")
    unscored = get_unscored_publications(publications, dynamo_client, TABLE_NAME)

    # --- Apply --test flag (D-14) ---
    if args.test:
        unscored = unscored[:args.test]
        print(f"[--test mode] Limiting run to first {args.test} publications")

    if not unscored:
        print("Nothing to score — all publications already complete.")
    else:
        # --- Phase 2: Score publications via Bedrock ---
        print(f"\n--- Phase 2: Scoring {len(unscored)} publications "
              f"(concurrency={args.concurrency}) ---")
        results = await score_batch_async(
            unscored, bedrock, taxonomy, dynamo_client, TABLE_NAME,
            int_to_id, id_to_int, concurrency=args.concurrency,
        )

        # --- Summary ---
        total = len(results)
        succeeded = sum(1 for r in results if r.status == 'complete')
        failed = sum(1 for r in results if r.status == 'failed')
        failure_rate = failed / total if total > 0 else 0.0

        print(f"\n--- Scoring Summary ---")
        print(f"  Total publications scored: {total}")
        print(f"  Successfully scored:       {succeeded}")
        print(f"  Failed:                    {failed}")
        print(f"  Failure rate:              {failure_rate * 100:.2f}%")

        if failure_rate > TARGET_FAILURE_RATE:
            print(f"\n  WARNING: Failure rate {failure_rate * 100:.2f}% exceeds "
                  f"{TARGET_FAILURE_RATE * 100:.0f}% target (D-11). "
                  "Check logs for error details.")

        # --- Phase 3: Save outputs ---
        print("\n--- Phase 3: Saving outputs ---")
        scored_records = serialize_results(results)

        scoring_results_path = Path(__file__).parent / 'scoring_results.json'
        output = {
            'taxonomy_version': taxonomy['taxonomy_version'],
            'scored_publications': scored_records,
        }
        with open(scoring_results_path, 'w') as f:
            json.dump(output, f, indent=2, default=str)
        print(f"Saved {len(scored_records)} scored publications -> {scoring_results_path}")

    # Always save author mapping and faculty metadata (needed by load_dynamodb.py)
    author_mapping_path = Path(__file__).parent / 'author_mapping.json'
    with open(author_mapping_path, 'w') as f:
        json.dump(author_mapping, f, indent=2)
    print(f"Saved author mapping ({len(author_mapping)} publications) -> {author_mapping_path}")

    faculty_metadata_path = Path(__file__).parent / 'faculty_metadata.json'
    with open(faculty_metadata_path, 'w') as f:
        json.dump(faculty_metadata, f, indent=2)
    print(f"Saved {len(faculty_metadata)} faculty profiles -> {faculty_metadata_path}")

    print("\nResults saved. Run load_dynamodb.py next.")


if __name__ == "__main__":
    asyncio.run(main())

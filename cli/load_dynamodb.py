"""
DynamoDB data loader for ReCiter AI Chatbot.

Loads scored publication data into DynamoDB as 6 record types:
  1. TOPIC# — publication scores per topic (from scoring_results.json)
  2. FACULTY# — faculty profiles with top_topics vector
  3. TOOL# — keyword relevance scores (from reciterai_keyword_relevance)
  4. TOOL_INDEX# — canonicalized tool definitions by functional category
  5. DEEPDIVE# — deep dive analysis records
  6. TAXONOMY# — taxonomy definition for runtime lookup

IMPACT# records are no longer built here — the daily-enrichment job
(`pipeline_enrichment/daily_job.py`) owns IMPACT# writes (#37 + #138 lift).
Rebuilding IMPACT# from MariaDB is retired in #143 to avoid clobbering
the live DDB rows with stale data.

Input files (produced by score_publications.py):
  - scoring_results.json   (pmid + dense_scores with rationales)
  - author_mapping.json    (pmid -> [{cwid, position}])
  - faculty_metadata.json  (cwid -> {name, department, h_index, ...})
  - taxonomy_v2.json       (topic definitions)

Usage:
    python3 load_dynamodb.py
    python3 load_dynamodb.py --table-name custom-table
    python3 load_dynamodb.py --min-score 0.4   # override dense score threshold
"""

import json
import sys
import os
import logging
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal
from collections import defaultdict

from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.dynamodb_helpers import (
    get_dynamo_client, TABLE_NAME, batch_write, make_score_sk,
    to_decimal, create_chatbot_table, wait_for_table
)
from utils.sql_queries import TOOL_EXTRACTION_SQL, get_raw_db_connection
from utils.env_check import load_thresholds
from utils.build_info import minted_by
from utils.topic_records import build_topic_rows_for_pmid

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)

# Minimum dense score to load into DynamoDB (filters noise from generous
# screening). Lifted to config/thresholds.json `score_floor` (G-18) so the
# DDB load floor matches the screening floor in score_publications and the
# qualifying-activity floor in assign_subtopics/backfill_topic.
DEFAULT_MIN_SCORE = load_thresholds()["score_floor"]


# ---------------------------------------------------------------------------
# Record Type 1: TOPIC# score records (DB-02)
# ---------------------------------------------------------------------------

def build_topic_records(
    scoring_results: list,
    author_mapping: dict,
    taxonomy_version: str,
    min_score: float = DEFAULT_MIN_SCORE,
) -> list:
    """
    Build TOPIC# DynamoDB items from scoring results + author mapping.

    Each publication-topic pair with dense score >= min_score produces one
    record per faculty author (from author_mapping). This enables the
    FacultyTopicsIndex GSI to find all topics for a given faculty member.
    """
    records = []
    pubs_with_scores = 0
    seen_keys = set()

    for pub in scoring_results:
        pmid = str(pub['pmid'])
        authors = author_mapping.get(pmid, [])
        if not authors:
            continue

        pmid_rows = build_topic_rows_for_pmid(
            minted_by=minted_by("load_dynamodb"),
            pmid=pmid,
            dense_scores=pub.get('dense_scores', {}),
            authors=authors,
            taxonomy_version=taxonomy_version,
            min_score=min_score,
            year=pub.get('year'),
            # #212 Part A — cold-path build-time join. scoring_results.json
            # carries impact_score/impact_justification only when the IMPACT#
            # row was enriched at score time; absent keys leave the row at its
            # historical shape (Part B fills it when enrichment later lands).
            impact_score=pub.get('impact_score'),
            impact_justification=pub.get('impact_justification', ''),
        )
        for row in pmid_rows:
            key = (row['PK']['S'], row['SK']['S'])
            if key in seen_keys:
                continue
            seen_keys.add(key)
            records.append(row)
        if pmid_rows:
            pubs_with_scores += 1

    print(f'Built {len(records)} TOPIC# records from {pubs_with_scores} publications')
    return records


# ---------------------------------------------------------------------------
# Record Type 2: FACULTY# profile records (DB-03)
# ---------------------------------------------------------------------------

def build_faculty_records(
    scoring_results: list,
    author_mapping: dict,
    faculty_metadata: dict,
    taxonomy_version: str,
    min_score: float = DEFAULT_MIN_SCORE,
) -> list:
    """
    Build FACULTY# DynamoDB items with top_topics vector.

    Aggregates scored publications per faculty (via author_mapping),
    computes max dense score per topic, returns top 10.
    """
    # Aggregate: cwid -> {topic_id: max_score}
    faculty_topics = defaultdict(lambda: defaultdict(float))
    faculty_pub_set = defaultdict(set)

    for pub in scoring_results:
        pmid = str(pub['pmid'])
        authors = author_mapping.get(pmid, [])
        for author in authors:
            cwid = author['cwid']
            faculty_pub_set[cwid].add(pmid)
            for topic_id, score_data in pub.get('dense_scores', {}).items():
                score = score_data['score'] if isinstance(score_data, dict) else score_data
                if score >= min_score:
                    faculty_topics[cwid][topic_id] = max(
                        faculty_topics[cwid][topic_id], score
                    )

    records = []
    for cwid, meta in faculty_metadata.items():
        if cwid not in faculty_topics:
            continue

        # Top 10 topics by max score
        sorted_topics = sorted(
            faculty_topics[cwid].items(), key=lambda x: -x[1]
        )[:10]

        top_topics_list = [
            {'M': {
                'topic_id': {'S': tid},
                'max_score': {'N': str(to_decimal(score))},
            }}
            for tid, score in sorted_topics
        ]

        records.append({
            'PK': {'S': f'FACULTY#cwid_{cwid}'},
            'SK': {'S': 'PROFILE'},
            'faculty_uid': {'S': f'cwid_{cwid}'},
            'name': {'S': meta.get('name', '')},
            'department': {'S': meta.get('department', '')},
            'h_index': {'N': str(meta.get('h_index', 0) or 0)},
            'article_count': {'N': str(meta.get('article_count', 0) or 0)},
            'first_author_count': {'N': str(meta.get('first_author_count', 0) or 0)},
            'last_author_count': {'N': str(meta.get('last_author_count', 0) or 0)},
            'scored_pub_count': {'N': str(len(faculty_pub_set[cwid]))},
            'top_topics': {'L': top_topics_list},
            'taxonomy_version': {'S': taxonomy_version},
        })

    print(f'Built {len(records)} FACULTY# records')
    return records


# ---------------------------------------------------------------------------
# Record Type 3: TOOL# records (from reciterai_tools)
# ---------------------------------------------------------------------------

def build_tool_records(author_mapping: dict) -> list:
    """
    Build TOOL# DynamoDB items from reciterai_tools.

    LLM-extracted tools/instruments/methods linked to publications.
    Each tool-publication-author triple becomes one record.
    """
    conn = get_raw_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(TOOL_EXTRACTION_SQL)
        rows = cursor.fetchall()
    finally:
        conn.close()

    records = []
    unique_tools = set()
    seen_keys = set()
    for row in rows:
        pmid = str(row['pmid'])
        if pmid not in author_mapping:
            continue

        tool_name = row['tool_name']
        tool_category = row['tool_category'] or ''
        confidence = int(row['confidence_tool'] or 0)
        context = str(row.get('context') or '')
        # Normalize confidence 0-10 → 0.0-1.0 for score SK
        normalized_score = confidence / 10.0
        # Use tool_name as the tool_id (sanitize for PK)
        tool_id = tool_name.replace('/', '_').replace('#', '_')
        unique_tools.add(tool_id)

        for author in author_mapping[pmid]:
            cwid = author['cwid']
            sk = f'{make_score_sk(normalized_score, pmid)}#cwid_{cwid}'
            key = (f'TOOL#{tool_id}', sk)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            records.append({
                'PK': {'S': f'TOOL#{tool_id}'},
                'SK': {'S': sk},
                'faculty_uid': {'S': f'cwid_{cwid}'},
                'score': {'N': str(to_decimal(normalized_score))},
                'pmid': {'S': pmid},
                'tool_category': {'S': tool_category},
                'context': {'S': context},
            })

    assert len(records) > 0, (
        'TOOL# records: 0 rows from reciterai_tools. '
        'Run utils/env_check.py to diagnose.'
    )

    print(f'Built {len(records)} TOOL# records from {len(unique_tools)} unique tools')
    return records


def build_tool_index_records() -> list:
    """
    Build TOOL_INDEX# records — one per functional category.

    Reads tool_index.json (produced by canonicalization pass) and creates
    DynamoDB records the chat runtime can use to resolve tool queries.
    """
    index_path = Path(__file__).parent.parent / 'tool_index.json'
    assert index_path.exists(), (
        'tool_index.json not found — run the tool canonicalization step first'
    )
    index = json.load(open(index_path))

    # Group by functional category
    from collections import defaultdict
    cats = defaultdict(list)
    for tool in index['tools']:
        cats[tool['functional_category']].append(tool)

    records = []
    for category, tools in cats.items():
        sorted_tools = sorted(tools, key=lambda t: -t['pub_count'])
        tools_list = [
            {'M': {
                'canonical_name': {'S': t['canonical_name']},
                'pub_count': {'N': str(t['pub_count'])},
                'original_names': {'L': [{'S': n} for n in t['original_names']]},
                'original_category': {'S': t.get('original_category', '')},
            }}
            for t in sorted_tools
        ]
        records.append({
            'PK': {'S': f'TOOL_INDEX#{category}'},
            'SK': {'S': 'META'},
            'functional_category': {'S': category},
            'tool_count': {'N': str(len(tools))},
            'tools': {'L': tools_list},
        })

    print(f'Built {len(records)} TOOL_INDEX# records ({sum(len(t) for t in cats.values())} canonical tools)')
    return records


# ---------------------------------------------------------------------------
# Record Type 4: DEEPDIVE# aging record (DB-05)
# ---------------------------------------------------------------------------

def build_aging_deep_dive(taxonomy_version: str) -> dict:
    """Build a single DEEPDIVE record for the aging domain."""
    return {
        'PK': {'S': 'DEEPDIVE#aging_geroscience'},
        'SK': {'S': 'META'},
        'domain': {'S': 'aging_geroscience'},
        'generated_at': {'S': datetime.now(timezone.utc).isoformat()},
        'taxonomy_version': {'S': taxonomy_version},
        'subtopics': {'L': [
            {'S': 'cognitive_decline'},
            {'S': 'neurodegeneration'},
            {'S': 'longevity'},
            {'S': 'geriatric_medicine'},
            {'S': 'age_related_disease'},
        ]},
        'content': {'S': 'Deep dive analysis pending generation.'},
        'reviewed_by': {'S': ''},
        'review_status': {'S': 'pending'},
    }


# ---------------------------------------------------------------------------
# Record Type 5: TAXONOMY# definition record
# ---------------------------------------------------------------------------

def build_taxonomy_record(taxonomy: dict) -> dict:
    """
    Build a TAXONOMY# record storing the full taxonomy for runtime lookup.

    The chat runtime reads this once on startup to map topic IDs to labels
    and descriptions. Versioned so the runtime knows which taxonomy
    produced the current scores.
    """
    version = taxonomy['taxonomy_version']
    topics_list = [
        {'M': {
            'id': {'S': t['id']},
            'label': {'S': t['label']},
            'description': {'S': t['description']},
        }}
        for t in taxonomy['topics']
    ]

    return {
        'PK': {'S': f'TAXONOMY#{version}'},
        'SK': {'S': 'META'},
        'taxonomy_version': {'S': version},
        'topic_count': {'N': str(len(taxonomy['topics']))},
        'created_at': {'S': datetime.now(timezone.utc).isoformat()},
        'topics': {'L': topics_list},
    }


# ---------------------------------------------------------------------------
# Batch loading helpers
# ---------------------------------------------------------------------------

def load_records(dynamo_client, table_name: str, records: list, label: str):
    """Load records with tqdm progress."""
    if not records:
        print(f'No {label} records to load')
        return
    # batch_write handles 25-item chunking
    batch_write(dynamo_client, table_name, records)
    print(f'Loaded {len(records)} {label} records')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description='Load scored publication data into DynamoDB.'
    )
    parser.add_argument(
        '--table-name', default=TABLE_NAME,
        help=f'DynamoDB table name (default: {TABLE_NAME})'
    )
    parser.add_argument(
        '--min-score', type=float, default=DEFAULT_MIN_SCORE,
        help=f'Minimum dense score to load (default: {DEFAULT_MIN_SCORE})'
    )
    args = parser.parse_args()
    table_name = args.table_name
    min_score = args.min_score

    # --- Load input files ---
    scoring_path = Path(__file__).parent.parent / 'scoring_results.json'
    author_path = Path(__file__).parent.parent / 'author_mapping.json'
    faculty_path = Path(__file__).parent.parent / 'faculty_metadata.json'
    taxonomy_path = Path(__file__).parent.parent / 'taxonomy_v2.json'

    assert scoring_path.exists(), f'scoring_results.json not found — run score_publications.py first'
    assert author_path.exists(), f'author_mapping.json not found — run score_publications.py first'
    assert faculty_path.exists(), f'faculty_metadata.json not found — run score_publications.py first'
    assert taxonomy_path.exists(), f'taxonomy_v2.json not found — run generate_taxonomy.py first'

    scoring_data = json.load(open(scoring_path))
    scoring_results = scoring_data['scored_publications']
    taxonomy_version = scoring_data['taxonomy_version']
    author_mapping = json.load(open(author_path))
    faculty_metadata = json.load(open(faculty_path))
    taxonomy = json.load(open(taxonomy_path))

    print(f'Loaded {len(scoring_results)} scored publications (taxonomy: {taxonomy_version})')
    print(f'Loaded {len(author_mapping)} author mappings')
    print(f'Loaded {len(faculty_metadata)} faculty profiles')
    print(f'Loaded {len(taxonomy["topics"])} taxonomy topics')
    print(f'Min dense score threshold: {min_score}')

    # --- Initialize DynamoDB ---
    dynamo_client = get_dynamo_client()

    # Ensure table exists
    try:
        dynamo_client.describe_table(TableName=table_name)
        print(f'\nTable {table_name} exists')
    except dynamo_client.exceptions.ResourceNotFoundException:
        print(f'\nCreating table {table_name}...')
        create_chatbot_table(dynamo_client, table_name)
        wait_for_table(dynamo_client, table_name)

    # --- Build and load each record type ---

    # 1. TOPIC# records
    print('\n--- Building TOPIC# records ---')
    topic_records = build_topic_records(
        scoring_results, author_mapping, taxonomy_version, min_score
    )
    load_records(dynamo_client, table_name, topic_records, 'TOPIC#')

    # 2. FACULTY# records
    print('\n--- Building FACULTY# records ---')
    faculty_records = build_faculty_records(
        scoring_results, author_mapping, faculty_metadata, taxonomy_version, min_score
    )
    load_records(dynamo_client, table_name, faculty_records, 'FACULTY#')

    # 3. TOOL# records
    print('\n--- Building TOOL# records ---')
    tool_records = build_tool_records(author_mapping)
    load_records(dynamo_client, table_name, tool_records, 'TOOL#')

    # 4. DEEPDIVE# record
    print('\n--- Loading DEEPDIVE# record ---')
    deep_dive = build_aging_deep_dive(taxonomy_version)
    dynamo_client.put_item(TableName=table_name, Item=deep_dive)
    print('Loaded DEEPDIVE#aging_geroscience (review_status: pending)')

    # 5. TOOL_INDEX# records (canonicalized tools by functional category)
    print('\n--- Building TOOL_INDEX# records ---')
    tool_index_records = build_tool_index_records()
    for rec in tool_index_records:
        dynamo_client.put_item(TableName=table_name, Item=rec)
    print(f'Loaded {len(tool_index_records)} TOOL_INDEX# records')

    # 6. TAXONOMY# record
    print('\n--- Loading TAXONOMY# record ---')
    taxonomy_record = build_taxonomy_record(taxonomy)
    dynamo_client.put_item(TableName=table_name, Item=taxonomy_record)
    print(f'Loaded TAXONOMY#{taxonomy_version} ({len(taxonomy["topics"])} topics)')

    # --- Summary ---
    topic_count = len(topic_records)
    faculty_count = len(faculty_records)
    tool_count = len(tool_records)
    tool_index_count = len(tool_index_records)

    print(f'\n=== DynamoDB Load Summary ===')
    print(f'  TOPIC# records:      {topic_count:>8,}')
    print(f'  FACULTY# records:    {faculty_count:>8,}')
    print(f'  TOOL# records:       {tool_count:>8,}')
    print(f'  TOOL_INDEX# records: {tool_index_count:>8,}')
    print(f'  DEEPDIVE# records:   {1:>8,}')
    print(f'  TAXONOMY# records:   {1:>8,}')
    print(f'  Table: {table_name}')
    print(f'  Taxonomy: {taxonomy_version}')

    assert topic_count > 0, 'TOPIC# records = 0 — check scoring_results.json'
    assert faculty_count > 0, 'FACULTY# records = 0 — check author_mapping.json'
    assert tool_count > 0, 'TOOL# records = 0 — check reciterai_tools'


if __name__ == '__main__':
    main()

"""Reproduce Bug 2 by running the full score_one_publication path locally.

Pulls one PMID from DB, runs through screening + dense scoring against real
Bedrock + DDB. Verbose traceback on any IndexError.

Run: python3 scripts/repro_bug2_full.py 41198049
"""
import logging
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import json
from sqlalchemy import text

from utils.bedrock_client import BedrockClient
from utils.dynamodb_helpers import get_dynamo_client, TABLE_NAME
from utils.sql_queries import PUBLICATION_EXTRACTION_SQL, get_db_connection
from score_publications import build_topic_index, score_one_publication

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s")

PMIDS = [
    "41198049", "41485218", "41864986", "41883166", "41387968",
    "40910582", "41865371", "40987459", "40053362", "39794471",
]


def fetch_one(pmid: str) -> dict:
    sql = PUBLICATION_EXTRACTION_SQL.replace(
        "ORDER BY a1.pmid DESC",
        "AND a1.pmid = :pmid ORDER BY a1.pmid DESC",
    )
    conn = get_db_connection()
    try:
        row = conn.execute(text(sql), {"pmid": pmid}).mappings().first()
        return dict(row) if row else None
    finally:
        conn.close()


def main():
    pmid = sys.argv[1] if len(sys.argv) > 1 else "41198049"
    pub = fetch_one(pmid)
    if pub is None:
        print(f"PMID {pmid} not in DB.")
        return 1
    print(f"pmid={pmid} abstract_len={len(pub.get('abstract') or '')} synopsis_len={len(pub.get('synopsis') or '')}")

    taxonomy = json.load(open(Path(__file__).resolve().parents[2] / "taxonomy_v2.json"))
    int_to_id, id_to_int = build_topic_index(taxonomy)
    bedrock = BedrockClient()
    dynamo = get_dynamo_client()

    # Wrap to capture deep traceback
    import score_publications as sp
    orig = sp.score_one_publication

    try:
        result = score_one_publication(
            pub=pub,
            bedrock=bedrock,
            taxonomy=taxonomy,
            dynamo_client=dynamo,
            table_name=TABLE_NAME,
            int_to_id=int_to_id,
            id_to_int=id_to_int,
            stage_table=None,  # match Lambda current behavior (stage_table not None in Lambda; try both)
            thresholds=None,
        )
        print(f"\nstatus={result.status} error={result.error}")
        print(f"screening_scores ({len(result.screening_scores)}): {dict(list(result.screening_scores.items())[:5])}")
        print(f"dense_scores ({len(result.dense_scores)}): {dict(list(result.dense_scores.items())[:5])}")
    except Exception as e:
        print(f"\nCaught: {type(e).__name__}: {e}")
        traceback.print_exc()
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Reproduce Bug 2 with full traceback exposure.

Patches score_one_publication's `except Exception as e: logger.error(...)` to
also print the full traceback so we can see WHERE the IndexError fires.
"""
import json
import logging
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from utils.bedrock_client import BedrockClient, SONNET_MODEL, HAIKU_MODEL
from utils.dynamodb_helpers import get_dynamo_client, TABLE_NAME
from utils.sql_queries import PUBLICATION_EXTRACTION_SQL, get_db_connection
from score_publications import build_topic_index, make_screening_prompt, make_dense_prompt, SCREENING_THRESHOLD

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


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
    print(f"pmid={pmid} abstract_len={len(pub.get('abstract') or '')} synopsis_len={len(pub.get('synopsis') or '')}")

    taxonomy = json.load(open(Path(__file__).resolve().parents[1] / "taxonomy_v2.json"))
    int_to_id, id_to_int = build_topic_index(taxonomy)
    bedrock = BedrockClient()

    # Replicate score_one_publication MANUALLY without try/except wrapping so we see traceback
    screening_prompt = make_screening_prompt(pub, taxonomy)
    raw_screening = bedrock.call_json(model=HAIKU_MODEL, messages=[{"role": "user", "content": screening_prompt}])
    print(f"raw_screening type={type(raw_screening).__name__} len={len(raw_screening) if hasattr(raw_screening, '__len__') else 'n/a'}")
    print(f"raw_screening sample: {dict(list(raw_screening.items())[:5]) if isinstance(raw_screening, dict) else str(raw_screening)[:200]}")

    screening_scores = {}
    for int_id, score in raw_screening.items():
        topic_id = int_to_id.get(str(int_id))
        if not topic_id:
            continue
        try:
            screening_scores[topic_id] = float(score)
        except (TypeError, ValueError):
            continue
    passed_topics = {tid: s for tid, s in screening_scores.items() if s >= SCREENING_THRESHOLD}
    print(f"passed_topics ({len(passed_topics)}): {passed_topics}")

    dense_prompt = make_dense_prompt(pub, passed_topics, taxonomy, id_to_int)
    print(f"dense_prompt len: {len(dense_prompt)}")
    print(f"\nCalling Bedrock SONNET for dense scoring (NO try/except — raw traceback)...")

    # NO try/except — let the IndexError bubble up with full traceback
    raw_dense = bedrock.call_json(model=SONNET_MODEL, messages=[{"role": "user", "content": dense_prompt}])
    print(f"raw_dense: {raw_dense}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n!!! Caught {type(e).__name__}: {e}")
        traceback.print_exc()

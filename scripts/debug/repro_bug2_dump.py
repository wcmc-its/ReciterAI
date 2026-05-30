"""Dump the raw Sonnet response that triggers Bug 2."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from utils.bedrock_client import BedrockClient, SONNET_MODEL, HAIKU_MODEL
from utils.sql_queries import PUBLICATION_EXTRACTION_SQL, get_db_connection
from score_publications import build_topic_index, make_screening_prompt, make_dense_prompt, SCREENING_THRESHOLD


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
    taxonomy = json.load(open(Path(__file__).resolve().parents[1] / "taxonomy_v2.json"))
    int_to_id, id_to_int = build_topic_index(taxonomy)
    bedrock = BedrockClient()

    # Get screening passed_topics
    screening_prompt = make_screening_prompt(pub, taxonomy)
    raw_screening = bedrock.call_json(model=HAIKU_MODEL, messages=[{"role": "user", "content": screening_prompt}])
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

    # Build dense prompt
    dense_prompt = make_dense_prompt(pub, passed_topics, taxonomy, id_to_int)
    print(f"dense_prompt len: {len(dense_prompt)}")
    print(f"\n--- DENSE PROMPT ---\n{dense_prompt[:1500]}\n--- end ---\n")

    # Call Bedrock RAW (skip the [0] index)
    client = bedrock._get_client()
    converse_messages, system_list = bedrock._translate_messages(
        [{"role": "user", "content": dense_prompt}], None
    )

    resp = client.converse(
        modelId=SONNET_MODEL,
        messages=converse_messages,
        inferenceConfig={"maxTokens": 4096, "temperature": 0.0},
    )

    print("\n--- RAW SONNET RESPONSE ---")
    print(json.dumps({k: v for k, v in resp.items() if k != 'ResponseMetadata'}, default=str, indent=2))
    print("--- end ---\n")

    print(f"stopReason: {resp.get('stopReason')}")
    print(f"output keys: {list(resp.get('output', {}).keys())}")
    msg = resp.get('output', {}).get('message', {})
    print(f"message keys: {list(msg.keys())}")
    print(f"content len: {len(msg.get('content', []))}")


if __name__ == "__main__":
    main()

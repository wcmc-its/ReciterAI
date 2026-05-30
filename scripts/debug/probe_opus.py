"""Send the failing dense prompt to Opus 4.7 to see whether the content
filter behavior differs from Sonnet 4.6.

Run: python3 scripts/debug/probe_opus.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import text

from utils.bedrock_client import BedrockClient, HAIKU_MODEL, SONNET_MODEL, OPUS_MODEL
from utils.sql_queries import PUBLICATION_EXTRACTION_SQL, get_db_connection
from score_publications import (
    SCREENING_THRESHOLD,
    build_topic_index,
    make_dense_prompt,
    make_screening_prompt,
)


PMID = "41198049"


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


def probe(label: str, model: str, prompt: str, client: BedrockClient, temperature=0.0):
    bedrock = client._get_client()
    msgs, _ = client._translate_messages([{"role": "user", "content": prompt}], None)
    inference_config = {"maxTokens": 4096}
    if temperature is not None:
        inference_config["temperature"] = temperature
    resp = bedrock.converse(
        modelId=model,
        messages=msgs,
        inferenceConfig=inference_config,
    )
    stop = resp.get("stopReason", "?")
    content = resp.get("output", {}).get("message", {}).get("content", []) or []
    n = len(content)
    full = (content[0].get("text", "") if n else "")
    sample = full[:300].replace("\n", " ")
    print(f"  {label:8s} model={model.split('.')[-1]:30s} stopReason={stop:18s} content_blocks={n}")
    print(f"           text[:300]={sample!r}")
    print(f"           usage={resp.get('usage')}")
    return stop, full


def main():
    pub = fetch_one(PMID)
    taxonomy = json.load(open(Path(__file__).resolve().parents[2] / "taxonomy_v2.json"))
    int_to_id, id_to_int = build_topic_index(taxonomy)
    client = BedrockClient()

    # Reproduce screening to get passed_topics
    screening_prompt = make_screening_prompt(pub, taxonomy)
    raw_screening = client.call_json(model=HAIKU_MODEL, messages=[{"role": "user", "content": screening_prompt}])
    screening_scores = {}
    for int_id, score in raw_screening.items():
        topic_id = int_to_id.get(str(int_id))
        if topic_id:
            try:
                screening_scores[topic_id] = float(score)
            except (TypeError, ValueError):
                pass
    passed_topics = {tid: s for tid, s in screening_scores.items() if s >= SCREENING_THRESHOLD}
    dense_prompt = make_dense_prompt(pub, passed_topics, taxonomy, id_to_int)

    print(f"Testing the dense prompt for PMID {PMID} (length: {len(dense_prompt)} chars)\n")

    print("Probe — Sonnet 4.6 (production, failing):")
    probe("sonnet", SONNET_MODEL, dense_prompt, client, temperature=0.0)

    print("\nProbe — Opus 4.7 (temperature=None per Opus deprecation):")
    probe("opus", OPUS_MODEL, dense_prompt, client, temperature=None)

    print("\nProbe — Haiku 4.5 (known-working baseline):")
    probe("haiku", HAIKU_MODEL, dense_prompt, client, temperature=0.0)


if __name__ == "__main__":
    main()

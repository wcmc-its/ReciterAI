"""Dump the dense prompt that Sonnet 4.6 content-filters on PMID 41198049,
and probe which portion triggers the filter.

Run: python3 scripts/debug/which_prompt_fails.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import text

from utils.bedrock_client import BedrockClient, HAIKU_MODEL, SONNET_MODEL
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


def probe(label: str, model: str, prompt: str, client: BedrockClient) -> str:
    bedrock = client._get_client()
    msgs, sys_list = client._translate_messages(
        [{"role": "user", "content": prompt}], None
    )
    resp = bedrock.converse(
        modelId=model,
        messages=msgs,
        inferenceConfig={"maxTokens": 4096, "temperature": 0.0},
    )
    stop = resp.get("stopReason", "?")
    content = resp.get("output", {}).get("message", {}).get("content", []) or []
    n = len(content)
    sample = (content[0].get("text", "") if n else "")[:120].replace("\n", " ")
    print(f"  {label}: model={model.split('.')[-1]:30s} stopReason={stop:18s} content_blocks={n}  text[:120]={sample!r}")
    return stop


def main():
    pub = fetch_one(PMID)
    taxonomy = json.load(open(Path(__file__).resolve().parents[2] / "taxonomy_v2.json"))
    int_to_id, id_to_int = build_topic_index(taxonomy)
    client = BedrockClient()

    # Replicate screening to get passed_topics
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
    print(f"passed_topics ({len(passed_topics)}): {list(passed_topics.keys())}\n")

    dense_prompt = make_dense_prompt(pub, passed_topics, taxonomy, id_to_int)

    print("=" * 80)
    print("FAILING PROMPT — dense (Sonnet 4.6) for PMID 41198049")
    print("=" * 80)
    print(f"length: {len(dense_prompt)} chars\n")
    print(dense_prompt)
    print("=" * 80)
    print()

    # Probe 1: dense prompt on Sonnet (the failure)
    print("Probe 1 — original dense prompt:")
    probe("dense   ", SONNET_MODEL, dense_prompt, client)

    # Probe 2: dense prompt on Haiku — does Haiku filter the same content?
    print("\nProbe 2 — same prompt to Haiku (compare filter behavior):")
    probe("dense->H", HAIKU_MODEL, dense_prompt, client)

    # Probe 3: dense prompt with abstract stripped — isolate where the trigger is
    abstract = pub.get("abstract") or ""
    synopsis = pub.get("synopsis") or ""
    no_abs = dense_prompt.replace(abstract, "[abstract redacted]")
    no_syn = dense_prompt.replace(synopsis, "[synopsis redacted]")
    no_both = no_abs.replace(synopsis, "[synopsis redacted]")

    print("\nProbe 3 — strip abstract:")
    probe("no_abs  ", SONNET_MODEL, no_abs, client)

    print("\nProbe 4 — strip synopsis:")
    probe("no_syn  ", SONNET_MODEL, no_syn, client)

    print("\nProbe 5 — strip both:")
    probe("no_both ", SONNET_MODEL, no_both, client)

    # Probe 6: screening prompt (Haiku) — known good baseline
    print("\nProbe 6 — screening prompt on Sonnet (was sent to Haiku in prod):")
    probe("screen  ", SONNET_MODEL, screening_prompt, client)


if __name__ == "__main__":
    main()

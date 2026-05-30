"""Test whether chunking topics into smaller groups changes the
Sonnet 4.6 content-filter behavior on PMID 41198049.

Varies the number of topics in the dense prompt (1, 2, 4, 7) and also
tries the minimal "score this paper, no topics" form to isolate whether
the trigger is publication content alone or the publication + topic
combination.

Run: python3 scripts/debug/probe_chunking.py
"""
import json
import sys
import time
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


def probe(label: str, model: str, prompt: str, client: BedrockClient):
    bedrock = client._get_client()
    msgs, _ = client._translate_messages([{"role": "user", "content": prompt}], None)
    resp = bedrock.converse(
        modelId=model,
        messages=msgs,
        inferenceConfig={"maxTokens": 4096, "temperature": 0.0},
    )
    stop = resp.get("stopReason", "?")
    content = resp.get("output", {}).get("message", {}).get("content", []) or []
    n = len(content)
    txt = (content[0].get("text", "") if n else "")[:180].replace("\n", " ")
    print(f"  {label:20s} stopReason={stop:18s} blocks={n}  out_tokens={resp.get('usage', {}).get('outputTokens')}")
    print(f"  {' '*20}     text[:180]={txt!r}")
    return stop


def main():
    pub = fetch_one(PMID)
    taxonomy = json.load(open(Path(__file__).resolve().parents[2] / "taxonomy_v2.json"))
    int_to_id, id_to_int = build_topic_index(taxonomy)
    client = BedrockClient()

    # Get the 7 passed topics
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
    passed = {tid: s for tid, s in screening_scores.items() if s >= SCREENING_THRESHOLD}
    topic_ids = list(passed.keys())
    print(f"7 passed topics: {topic_ids}\n")

    # Probe 0: full 7-topic prompt (baseline known-failing case)
    full_prompt = make_dense_prompt(pub, passed, taxonomy, id_to_int)
    print(f"--- Baseline (7 topics, {len(full_prompt)} chars) ---")
    probe("7 topics", SONNET_MODEL, full_prompt, client)

    # Probe 1: 4 topics
    subset4 = {k: passed[k] for k in topic_ids[:4]}
    p4 = make_dense_prompt(pub, subset4, taxonomy, id_to_int)
    print(f"\n--- 4-topic chunk ({len(p4)} chars) ---")
    probe("4 topics", SONNET_MODEL, p4, client)

    # Probe 2: 2 topics
    subset2 = {k: passed[k] for k in topic_ids[:2]}
    p2 = make_dense_prompt(pub, subset2, taxonomy, id_to_int)
    print(f"\n--- 2-topic chunk ({len(p2)} chars) ---")
    probe("2 topics", SONNET_MODEL, p2, client)

    # Probe 3: 1 topic each — 7 separate calls
    print(f"\n--- 1-topic chunks (7 separate Sonnet calls) ---")
    pass_count = 0
    fail_count = 0
    for tid in topic_ids:
        single = {tid: passed[tid]}
        p1 = make_dense_prompt(pub, single, taxonomy, id_to_int)
        stop = probe(f"1 topic [{tid[:18]}]", SONNET_MODEL, p1, client)
        if stop == "content_filtered":
            fail_count += 1
        elif stop == "end_turn":
            pass_count += 1
        time.sleep(0.3)
    print(f"\n  1-topic chunks: pass={pass_count} filtered={fail_count}")

    # Probe 4: just the publication content, no topics — does the filter
    # trigger on pub content alone regardless of surrounding scaffolding?
    minimal = (
        f"Score the relevance of this publication to topic 'biomedical research' (0-1):\n\n"
        f"Synopsis: {pub.get('synopsis')}\n\n"
        f"Abstract: {pub.get('abstract')}\n\n"
        f"Return only a number."
    )
    print(f"\n--- Minimal prompt (pub content + 1 generic ask, {len(minimal)} chars) ---")
    probe("minimal", SONNET_MODEL, minimal, client)

    # Probe 5: synopsis only, no abstract
    synopsis_only = (
        f"Score relevance (0-1) of this paper to topic 'infectious disease':\n\n"
        f"{pub.get('synopsis')}\n\n"
        f"Return only a number."
    )
    print(f"\n--- Synopsis only ({len(synopsis_only)} chars) ---")
    probe("synopsis only", SONNET_MODEL, synopsis_only, client)


if __name__ == "__main__":
    main()

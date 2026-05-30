"""Reproduce Bug 2 — list index out of range — for one PMID.

Hits real Bedrock + real DB to capture the exact response shape that fails.
Run: python scripts/repro_bug2.py 41198049
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from utils.bedrock_client import BedrockClient, HAIKU_MODEL
from utils.sql_queries import PUBLICATION_EXTRACTION_SQL, get_db_connection
from score_publications import make_screening_prompt


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
    print(f"Fetching pmid={pmid}...")
    pub = fetch_one(pmid)
    if pub is None:
        print(f"PMID {pmid} not in DB.")
        return 1

    print(f"abstract len = {len(pub.get('abstract') or '')}")
    print(f"synopsis len = {len(pub.get('synopsis') or '')}")
    print(f"synopsis = {(pub.get('synopsis') or '')[:200]}")

    taxonomy = json.load(open(Path(__file__).resolve().parents[1] / "taxonomy_v2.json"))
    prompt = make_screening_prompt(pub, taxonomy)
    print(f"prompt len = {len(prompt)}")

    client = BedrockClient()
    bedrock = client._get_client()

    converse_messages, system_list = client._translate_messages(
        [{"role": "user", "content": prompt}], None
    )

    print("\nCalling Bedrock Converse (Haiku)...")
    resp = bedrock.converse(
        modelId=HAIKU_MODEL,
        messages=converse_messages,
        inferenceConfig={"maxTokens": 4096, "temperature": 0.0},
    )
    print(f"stopReason: {resp.get('stopReason')}")
    print(f"usage: {resp.get('usage')}")
    print(f"output keys: {list(resp.get('output', {}).keys())}")
    message = resp.get('output', {}).get('message', {})
    print(f"message keys: {list(message.keys())}")
    content = message.get('content', [])
    print(f"content list len: {len(content)}")
    if content:
        for i, block in enumerate(content):
            print(f"  block[{i}] keys: {list(block.keys())}")
            if 'text' in block:
                print(f"  block[{i}].text[:200] = {block['text'][:200]}")
    else:
        print("  content is EMPTY — this would trigger IndexError at bedrock_client.py:154")

    print("\nFull response (trimmed):")
    print(json.dumps({k: v for k, v in resp.items() if k != 'ResponseMetadata'}, default=str, indent=2)[:2000])
    return 0


if __name__ == "__main__":
    sys.exit(main())

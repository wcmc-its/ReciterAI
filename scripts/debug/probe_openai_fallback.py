"""Send the failing dense prompt to OpenAI gpt-5.1 to confirm it is a
viable fallback for the Sonnet 4.6 content-filter blocker.

If gpt-5.1 returns valid JSON without content-policy intervention, the
operator-preferred OpenAI fallback PR proceeds. If it also content-filters,
the fallback falls back to Claude Haiku 4.5.

Run: python3 scripts/debug/probe_openai_fallback.py [PMID]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import text

from utils.bedrock_client import BedrockClient, HAIKU_MODEL
from utils.openai_client import (
    GPT5_MODEL,
    call_with_retry,
    get_default_client,
)
from utils.sql_queries import PUBLICATION_EXTRACTION_SQL, get_db_connection
from score_publications import (
    SCREENING_THRESHOLD,
    build_topic_index,
    make_dense_prompt,
    make_screening_prompt,
)


PMID = sys.argv[1] if len(sys.argv) > 1 else "41198049"


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
    pub = fetch_one(PMID)
    if pub is None:
        print(f"PMID {PMID} not in DB.")
        return 1

    taxonomy = json.load(open(Path(__file__).resolve().parents[2] / "taxonomy_v2.json"))
    int_to_id, id_to_int = build_topic_index(taxonomy)

    # Build the dense prompt as the live pipeline does
    bedrock = BedrockClient()
    screening_prompt = make_screening_prompt(pub, taxonomy)
    raw_screening = bedrock.call_json(model=HAIKU_MODEL, messages=[{"role": "user", "content": screening_prompt}])
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

    print(f"PMID {PMID}: dense prompt length {len(dense_prompt)} chars, {len(passed_topics)} topics to score")
    print(f"Sending to OpenAI {GPT5_MODEL}...\n")

    client = get_default_client()
    response = call_with_retry(
        client,
        model=GPT5_MODEL,
        system_prompt=(
            "You are a topic-classification assistant. Score the relevance of "
            "the given publication against each topic on a 0.0-1.0 scale. "
            "Return ONLY a JSON object — no markdown fences."
        ),
        user_prompt=dense_prompt,
        response_format={"type": "json_object"},
        max_completion_tokens=4096,
    )

    finish_reason = response.choices[0].finish_reason
    content = response.choices[0].message.content
    refusal = getattr(response.choices[0].message, "refusal", None)
    usage = response.usage

    print(f"finish_reason: {finish_reason}")
    print(f"refusal: {refusal!r}")
    print(f"usage: input={usage.prompt_tokens} output={usage.completion_tokens} total={usage.total_tokens}")
    print(f"content_len: {len(content or '')}")

    if not content:
        print("\n!! gpt-5.1 returned no content. Possible policy refusal. Inspect refusal field above.")
        print(f"Full response choice: {response.choices[0]}")
        return 2

    print(f"\ncontent[:500]:\n{content[:500]}")

    # Parse to verify it's valid JSON with the expected shape
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        print(f"\n!! Returned content is not valid JSON: {e}")
        return 3

    print(f"\nparsed JSON keys ({len(parsed)}): {list(parsed.keys())}")
    print(f"sample entries: {dict(list(parsed.items())[:3])}")

    # Compare against the Sonnet-failing baseline
    print("\n--- Verdict ---")
    if finish_reason == "stop" and content and isinstance(parsed, dict) and len(parsed) > 0:
        print(f"✅ PASS — gpt-5.1 returns valid JSON on the content that Sonnet 4.6 content-filters.")
        print(f"   OpenAI fallback is viable. The fallback PR uses {GPT5_MODEL}.")
        return 0
    else:
        print(f"❌ FAIL — gpt-5.1 did not return a valid scoring response.")
        print(f"   finish_reason={finish_reason}, content_present={bool(content)}, parsed_len={len(parsed) if isinstance(parsed, dict) else 'n/a'}")
        print(f"   Fall back to Haiku 4.5 for the fallback PR (per the planning doc).")
        return 4


if __name__ == "__main__":
    sys.exit(main())

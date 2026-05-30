"""Test whether explicit academic/medical research framing in the system
prompt shifts Sonnet 4.6's content-filter behavior on PMID 41198049.

If the classifier is context-aware, framing the call as "scoring
peer-reviewed PubMed publications for a faculty research-profile system"
may unblock the dense prompt without any other changes.

Run: python3 scripts/debug/probe_system_framing.py
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


def probe(label: str, system: str | None, prompt: str, client: BedrockClient):
    bedrock = client._get_client()
    msgs, sys_list = client._translate_messages(
        [{"role": "user", "content": prompt}], system
    )
    kwargs = {
        "modelId": SONNET_MODEL,
        "messages": msgs,
        "inferenceConfig": {"maxTokens": 4096, "temperature": 0.0},
    }
    if sys_list:
        kwargs["system"] = sys_list
    resp = bedrock.converse(**kwargs)
    stop = resp.get("stopReason", "?")
    content = resp.get("output", {}).get("message", {}).get("content", []) or []
    n = len(content)
    sample = (content[0].get("text", "") if n else "")[:200].replace("\n", " ")
    print(f"  [{label}] stopReason={stop:18s} blocks={n} out_tokens={resp.get('usage', {}).get('outputTokens')}")
    if sample:
        print(f"        text[:200]={sample!r}")
    return stop


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

    print(f"Testing system-prompt framing on Sonnet 4.6 for PMID {PMID}\n")

    print("=== Baseline — no system prompt ===")
    probe("baseline   ", None, dense_prompt, client)

    print("\n=== Probe A — minimal academic framing ===")
    sysA = "You are scoring peer-reviewed biomedical publications from PubMed for a faculty research-profile system at an academic medical center."
    probe("academic_min", sysA, dense_prompt, client)

    print("\n=== Probe B — same framing, more explicit on the safe-research context ===")
    sysB = (
        "You are a metadata classifier for an academic medical center's "
        "faculty research-profile system. You are scoring peer-reviewed "
        "biomedical publications retrieved from PubMed against a controlled "
        "research-topic taxonomy. The publications cover the full scope of "
        "biomedical research including animal-model studies, clinical "
        "trials, and basic science. Your role is informational topic "
        "classification, not medical advice or treatment recommendation."
    )
    probe("academic_full", sysB, dense_prompt, client)

    print("\n=== Probe C — Anthropic-style 'helpful assistant + research scope' ===")
    sysC = (
        "You are a helpful research-classification assistant operating on "
        "peer-reviewed PubMed publications. Score the relevance of each "
        "supplied topic to the publication. Do not provide medical advice; "
        "this is a topic-tagging task only."
    )
    probe("research_help", sysC, dense_prompt, client)

    print("\n=== Probe D — explicit acknowledgment that animal-research language is expected ===")
    sysD = (
        "You are scoring peer-reviewed biomedical publications from PubMed "
        "for a faculty research-profile system at an academic medical "
        "center. Publications may describe IACUC-approved animal model "
        "research, including standard veterinary terminology (euthanasia, "
        "humane endpoints, immunocompromised strains). This is routine "
        "biomedical literature; your task is topic classification only."
    )
    probe("iacuc_explicit", sysD, dense_prompt, client)


if __name__ == "__main__":
    main()

"""
Taxonomy generation script for ReCiter AI Chatbot.

Generates a validated ~50-topic research taxonomy from WCM publication synopses
via AWS Bedrock (Sonnet), validates it against sample queries, and writes
taxonomy_v1.json for human review before scoring begins (D-07 gate).

Three phases:
  Phase 1: Extract synopses from ReciterDB, batch-process topic clusters via Sonnet
  Phase 2: Consolidate ~150 raw topics into ~50 final topics via Sonnet
  Phase 3: Automated validation — test 12 sample queries against the taxonomy

Usage:
    python3 generate_taxonomy.py [--batch-size N] [--skip-extraction]

    --batch-size N      Synopses per LLM batch (default 75, per D-05 and RESEARCH.md Pattern 9)
    --skip-extraction   Use synopses cached in synopses_cache.json (skip DB query)

Output:
    taxonomy_v1.json    Frozen taxonomy with ~50 topics and taxonomy_version field

IMPORTANT: Review taxonomy_v1.json before running score_publications.py (D-07).
Bad taxonomy wastes the full ~$35 Bedrock scoring budget.
"""

import json
import sys
import os
import logging
import argparse
from pathlib import Path

# D-01: Add ReciterAI to path for database connection management
sys.path.insert(0, str(Path(__file__).parent))
from utils.bedrock_client import BedrockClient, SONNET_MODEL
from utils.dynamodb_helpers import get_dynamo_client, scan_all_synopses

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Validation queries (D-08): 12 sample research dean queries
# Source: RESEARCH.md Code Examples "Validation Sample Queries"
# ---------------------------------------------------------------------------

VALIDATION_QUERIES = [
    "Who works on aging and cognitive decline?",
    "Find researchers using CRISPR gene editing",
    "Who has expertise in cardiovascular disease prevention?",
    "Find oncology researchers focused on immunotherapy",
    "Who works on health equity and social determinants?",
    "Find researchers in diabetes and metabolic disorders",
    "Who studies neurodegeneration and Alzheimer's disease?",
    "Find clinical trials expertise in cardiology",
    "Who does research on cancer genomics?",
    "Find researchers working on AI and machine learning in medicine",
    "Who works on pediatric health outcomes?",
    "Find experts in infectious disease and epidemiology",
]


# ---------------------------------------------------------------------------
# Phase 1: Extract synopses from ReciterDB
# ---------------------------------------------------------------------------

def extract_synopses() -> list[str]:
    """
    Extract all publication synopses from DynamoDB IMPACT# rows.

    Synopsis source moved from MariaDB `reciterai_synopsis` to DDB in #38
    (after the #138 historical lift). `scan_all_synopses` scans every
    `IMPACT#` row with a non-empty `synopsis` attribute.

    Returns:
        List of non-empty synopsis strings.
    """
    logger.info("Scanning DynamoDB for publication synopses...")
    client = get_dynamo_client()
    synopses_by_pmid = scan_all_synopses(client)
    synopses = [s for s in synopses_by_pmid.values() if s and s.strip()]
    print(f"Extracted {len(synopses)} synopses from DynamoDB")
    return synopses


def make_batch_prompt(synopses: list[str]) -> str:
    """
    Build the batch taxonomy extraction prompt for a chunk of synopses.

    Source: RESEARCH.md Code Examples "Taxonomy Generation Prompt (Batch Pass)"

    Args:
        synopses: List of synopsis strings for this batch.

    Returns:
        Prompt string requesting 8-12 topic clusters in JSON format.
    """
    synopsis_text = "\n\n".join(f"[{i+1}] {s}" for i, s in enumerate(synopses))
    return f"""You are analyzing research publications from a major academic medical center (Weill Cornell Medicine).

Below are {len(synopses)} research synopses from publications.

<synopses>
{synopsis_text}
</synopses>

Derive 8-15 research DOMAIN clusters that cover the dominant themes in these synopses.

IMPORTANT — focus on DOMAINS, not methods:
- Domains are disease areas, organ systems, basic science fields, population contexts, or research infrastructure areas
- Examples of domains: "aging_geroscience", "cardiovascular_disease", "cell_biology", "health_economics", "basic_neuroscience"
- Do NOT include method/technique clusters like "machine learning", "genomics", "CRISPR", "clinical trials methodology" — those are tracked separately as research tools
- Exception: include methods ONLY when they represent a real departmental identity (e.g., "biomedical_engineering", "biomedical_informatics")
- Include foundational/basic science fields (cell biology, biochemistry, biophysics) — not just clinical specialties
- Use stable, mid-level domain names — not too specific ("tau_pathology") or too broad ("medicine")

Respond with JSON only:
{{"topics": [{{"id": "snake_case_id", "label": "Human Readable Label", "description": "2-3 sentence description"}}]}}"""


def extract_topic_clusters(
    client: BedrockClient,
    synopses: list[str],
    batch_size: int = 50,
) -> list[dict]:
    """
    Batch-process all synopses through Bedrock Sonnet to extract raw topic clusters.

    50 synopses per batch keeps responses within 8192 token limit to avoid
    truncated JSON. With ~7K synopses, this produces ~142 batches. With ~10K synopses, this produces
    ~133 batches and ~150 raw topics for the consolidation pass.

    Args:
        client: BedrockClient instance.
        synopses: Full list of synopsis strings.
        batch_size: Number of synopses per LLM call (default 75).

    Returns:
        Flat list of all raw topic dicts (id, label, description) from all batches.
    """
    chunks = [synopses[i:i + batch_size] for i in range(0, len(synopses), batch_size)]
    total = len(chunks)
    all_topics: list[dict] = []

    logger.info(f"Processing {len(synopses)} synopses in {total} batches of {batch_size}")

    for i, chunk in enumerate(chunks):
        batch_num = i + 1
        logger.info(f"Batch {batch_num}/{total}: calling Bedrock Sonnet for {len(chunk)} synopses")
        try:
            response = client.call_json(
                model=SONNET_MODEL,
                messages=[{"role": "user", "content": make_batch_prompt(chunk)}],
                max_tokens=8192,
            )
            topics = response.get("topics", [])
            all_topics.extend(topics)
            print(f"Batch {batch_num}/{total}: extracted {len(topics)} topics (running total: {len(all_topics)})")
        except Exception as e:
            logger.error(f"Batch {batch_num}/{total} failed: {e}")
            # Continue with remaining batches — partial results feed consolidation
            continue

    return all_topics


# ---------------------------------------------------------------------------
# Phase 2: Consolidate raw topics into ~50 final topics
# ---------------------------------------------------------------------------

def make_consolidation_prompt(raw_topics: list[dict], target_count: int = 65) -> str:
    """
    Build the consolidation prompt to merge raw topic clusters into final taxonomy.

    Source: RESEARCH.md Code Examples "Taxonomy Consolidation Prompt (Final Pass)"

    Args:
        raw_topics: List of raw topic dicts from batch extraction pass.
        target_count: Desired number of final topics (default 50).

    Returns:
        Prompt string requesting exactly target_count consolidated topics in JSON format,
        with taxonomy_version field set to taxonomy_v1.
    """
    topics_json = json.dumps(raw_topics, indent=2)
    return f"""You have {len(raw_topics)} raw domain clusters extracted from batches of research synopses at Weill Cornell Medicine.
Consolidate them into exactly {target_count} final DOMAIN topics for a research taxonomy.

This taxonomy is Axis 1 (domains) of a multi-axis system:
- Axis 1 (this taxonomy): Disease areas, organ systems, basic science fields, population contexts, research infrastructure
- Axis 2 (separate, already exists): Research tools and methods (CRISPR, machine learning, flow cytometry, etc.)
- The query system combines both axes, so domains should NOT duplicate methods

Rules:
- Merge overlapping domains (e.g., "aging" + "geroscience" -> "aging_geroscience")
- Focus on DOMAINS — disease areas, organ systems, basic science fields, population/context areas, and research infrastructure
- Do NOT include pure method/technique topics (genomics, AI, imaging methods, clinical trials methodology) — those are covered by Axis 2 (research tools)
- Exception: include methods that represent real departmental identity at an academic medical center (e.g., "biomedical_engineering", "biomedical_informatics")
- Include foundational/basic science domains: cell biology, biochemistry, biophysics, basic neuroscience, developmental biology, systems biology
- Include research infrastructure domains: translational science, implementation science, clinical trials science (the field, not the method), health economics
- Include emerging strategic domains: planetary health / climate & health, single-cell & spatial biology
- Aim for stable, mid-level granularity — not too specific, not too broad
- Each topic should represent a domain a research dean, department chair, or NIH study section would recognize

<raw_clusters>
{topics_json}
</raw_clusters>

Respond with JSON only:
{{"taxonomy_version": "taxonomy_v1", "topics": [{{"id": "snake_case_id", "label": "Human Readable Label", "description": "2-3 sentence description"}}]}}"""


def consolidate_taxonomy(client: BedrockClient, raw_topics: list[dict]) -> dict:
    """
    Consolidate all raw topic clusters into a final ~50-topic taxonomy via Bedrock Sonnet.

    Validates that the response has required keys and a topic count in the 40-60 range.

    Args:
        client: BedrockClient instance.
        raw_topics: All raw topics from the batch extraction pass.

    Returns:
        Full taxonomy dict with taxonomy_version and topics array.

    Raises:
        ValueError: If response is missing required keys.
    """
    logger.info(f"Consolidating {len(raw_topics)} raw topics into ~65 domain topics via Bedrock Sonnet")
    taxonomy = client.call_json(
        model=SONNET_MODEL,
        messages=[{"role": "user", "content": make_consolidation_prompt(raw_topics)}],
        max_tokens=16384,
    )

    # Validate required keys (T-02-04: JSON schema validation on response)
    if "taxonomy_version" not in taxonomy:
        raise ValueError("Bedrock consolidation response missing 'taxonomy_version' field")
    if "topics" not in taxonomy:
        raise ValueError("Bedrock consolidation response missing 'topics' field")

    topic_count = len(taxonomy["topics"])
    if topic_count < 50 or topic_count > 80:
        print(
            f"WARNING: Taxonomy has {topic_count} topics (expected 50-80). "
            "Consider re-running consolidation or editing taxonomy_v1.json manually."
        )
    else:
        print(f"Consolidation complete: {topic_count} final topics (version: {taxonomy['taxonomy_version']})")

    # Validate each topic has required fields (T-02-04)
    for i, topic in enumerate(taxonomy["topics"]):
        for field in ("id", "label", "description"):
            if field not in topic:
                logger.warning(f"Topic {i} missing field '{field}': {topic}")

    return taxonomy


# ---------------------------------------------------------------------------
# Phase 3: Automated validation (D-08)
# ---------------------------------------------------------------------------

def make_validation_prompt(query: str, taxonomy: dict) -> str:
    """
    Build a validation prompt to test a sample query against the full taxonomy.

    Args:
        query: A sample research dean query string.
        taxonomy: The full taxonomy dict (with topics list).

    Returns:
        Prompt string requesting matched_topics in JSON format.
    """
    topics_list = "\n".join(
        f"- {t['id']}: {t['label']} -- {t['description']}"
        for t in taxonomy.get("topics", [])
    )
    return f"""You are evaluating a research taxonomy for a university faculty expertise system.

Query: "{query}"

Taxonomy topics:
{topics_list}

Which topics from this taxonomy would be relevant to answer this query?

Return JSON only:
{{"matched_topics": [{{"id": "topic_id", "relevance": "high/medium/low", "reason": "brief explanation"}}]}}

Only include topics with meaningful relevance. Omit topics that are not relevant."""


def validate_taxonomy(client: BedrockClient, taxonomy: dict) -> dict:
    """
    Validate the taxonomy against VALIDATION_QUERIES (D-08).

    Runs each of the 12 sample queries through Bedrock Sonnet and reports
    how many queries have at least one high-relevance topic match.

    Args:
        client: BedrockClient instance.
        taxonomy: The consolidated taxonomy dict.

    Returns:
        Validation summary dict:
        {
            "total_queries": N,
            "queries_with_match": M,
            "match_rate": M/N,
            "details": [{"query": ..., "matched_topics": [...]}]
        }
    """
    logger.info(f"Validating taxonomy against {len(VALIDATION_QUERIES)} sample queries")
    details = []
    queries_with_match = 0

    for i, query in enumerate(VALIDATION_QUERIES):
        logger.info(f"Validation query {i+1}/{len(VALIDATION_QUERIES)}: {query!r}")
        try:
            response = client.call_json(
                model=SONNET_MODEL,
                messages=[{"role": "user", "content": make_validation_prompt(query, taxonomy)}],
            )
            matched_topics = response.get("matched_topics", [])
            high_relevance = [t for t in matched_topics if t.get("relevance") == "high"]
            has_high_match = len(high_relevance) > 0
            if has_high_match:
                queries_with_match += 1

            # Print query results for human review
            print(f"\nQuery {i+1}: {query}")
            if matched_topics:
                for t in matched_topics:
                    marker = "[HIGH]" if t.get("relevance") == "high" else f"[{t.get('relevance', '?').upper()}]"
                    print(f"  {marker} {t.get('id', '?')}: {t.get('reason', '')}")
            else:
                print("  (no matched topics)")

            details.append({
                "query": query,
                "matched_topics": matched_topics,
                "has_high_match": has_high_match,
            })
        except Exception as e:
            logger.error(f"Validation query {i+1} failed: {e}")
            details.append({
                "query": query,
                "matched_topics": [],
                "has_high_match": False,
                "error": str(e),
            })

    total = len(VALIDATION_QUERIES)
    match_rate = queries_with_match / total if total > 0 else 0.0
    return {
        "total_queries": total,
        "queries_with_match": queries_with_match,
        "match_rate": match_rate,
        "details": details,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    """
    Main entry point: run all three phases and write taxonomy_v1.json.

    Phases:
      1. Extract synopses from ReciterDB (or load from cache with --skip-extraction)
      2. Batch-extract topic clusters via Bedrock Sonnet
      3. Consolidate into ~50 final topics via Bedrock Sonnet
      4. Validate against 12 sample queries
      5. Write taxonomy_v1.json with review gate warning (D-07)
    """
    parser = argparse.ArgumentParser(
        description="Generate a validated research taxonomy from WCM publication synopses"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Number of synopses per LLM batch call (default: 50)",
    )
    parser.add_argument(
        "--skip-extraction",
        action="store_true",
        help="Skip DB extraction and load synopses from synopses_cache.json",
    )
    args = parser.parse_args()

    output_path = Path(__file__).parent / "taxonomy_v1.json"
    cache_path = Path(__file__).parent / "synopses_cache.json"

    client = BedrockClient()

    # -------------------------------------------------------------------------
    # Phase 1: Extract synopses
    # -------------------------------------------------------------------------
    if args.skip_extraction:
        if not cache_path.exists():
            logger.error(
                f"--skip-extraction specified but {cache_path} does not exist. "
                "Run without --skip-extraction first to populate the cache."
            )
            sys.exit(1)
        with open(cache_path) as f:
            synopses = json.load(f)
        print(f"Loaded {len(synopses)} synopses from cache: {cache_path}")
    else:
        synopses = extract_synopses()
        # Cache synopses to allow re-runs without DB query
        with open(cache_path, "w") as f:
            json.dump(synopses, f)
        logger.info(f"Synopses cached to {cache_path}")

    if not synopses:
        logger.error(
            "No synopses extracted. Check DDB IMPACT# rows carry the "
            "`synopsis` attribute (run scripts/backfill_legacy_synopsis_to_ddb.py)."
        )
        sys.exit(1)

    # -------------------------------------------------------------------------
    # Phase 2: Batch topic extraction + consolidation
    # -------------------------------------------------------------------------
    raw_topics = extract_topic_clusters(client, synopses, batch_size=args.batch_size)
    print(f"\nPhase 1 complete: {len(raw_topics)} raw topic clusters extracted from {len(synopses)} synopses")

    if not raw_topics:
        logger.error("No raw topics extracted from batch pass. Check Bedrock connectivity.")
        sys.exit(1)

    taxonomy = consolidate_taxonomy(client, raw_topics)
    topic_count = len(taxonomy.get("topics", []))
    print(f"\nPhase 2 complete: {topic_count} final topics consolidated (version: {taxonomy.get('taxonomy_version')})")

    # -------------------------------------------------------------------------
    # Phase 3: Automated validation (D-08)
    # -------------------------------------------------------------------------
    print("\nPhase 3: Validating taxonomy against sample research dean queries...")
    validation = validate_taxonomy(client, taxonomy)

    print(f"\n{'='*60}")
    print("VALIDATION SUMMARY")
    print(f"{'='*60}")
    print(f"Total queries tested:    {validation['total_queries']}")
    print(f"Queries with high match: {validation['queries_with_match']}")
    print(f"Match rate:              {validation['match_rate'] * 100:.0f}%")

    if validation["match_rate"] < 0.8:
        print(
            f"\nWARNING: Taxonomy may need revision -- only "
            f"{validation['match_rate'] * 100:.0f}% of sample queries had a high-relevance match "
            f"(expected >= 80%)"
        )

    # Attach validation results to the taxonomy for reference
    taxonomy["_validation"] = validation

    # -------------------------------------------------------------------------
    # Write output
    # -------------------------------------------------------------------------
    with open(output_path, "w") as f:
        json.dump(taxonomy, f, indent=2)

    print(f"\nTaxonomy written to {output_path}")
    print(
        "\n*** REVIEW REQUIRED (D-07) ***\n"
        "Review taxonomy_v1.json before running score_publications.py.\n"
        "Bad taxonomy wastes the full ~$35 Bedrock scoring budget.\n"
        "Check that topics cover major WCM research areas and have appropriate granularity."
    )


if __name__ == "__main__":
    main()

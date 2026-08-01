"""
Pass 1 (Discovery): Cluster topic activities into subtopics via Sonnet.

Reads TOPIC# DynamoDB records for a given topic_id, fetches activities
scoring ≥0.3, and calls Sonnet (temperature=0) to cluster them into 8-25
thematic subtopics. If coverage after the first pass is <85%, a second
extension pass is triggered (up to 2× the initial cluster count cap, D-01).

Usage:
    python discover_subtopics.py --topic aging_geroscience
    python discover_subtopics.py --topic aging_geroscience --dry-run
    python discover_subtopics.py --topic aging_geroscience --min-activities 30
    python discover_subtopics.py --topic aging_geroscience --extension-cap 15 --output-dir /tmp

Output:
    {output_dir}/hierarchy_draft_{topic_id}.json   — draft hierarchy for human review
    {output_dir}/hierarchy_draft_{topic_id}.raw.txt — raw Bedrock response (on parse failure)

Exit codes:
    0 — success
    1 — unexpected error
    2 — topic has fewer activities than cold-start floor (D-01)
    3 — Bedrock response JSON parse failure after fence-strip + retry

Security:
    AWS credentials via default credential chain only. Never printed or logged.
    DynamoDB reads only — no writes in Pass 1 (writes are Phase 4 Pass 2/3).
"""

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure repo root is on sys.path for utils imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.bedrock_client import BedrockClient, SONNET_MODEL
from utils.dynamodb_helpers import get_table, TABLE_NAME
from utils.env_check import load_thresholds
from prompts.subtopic_discovery import (
    DISCOVERY_SYSTEM_PROMPT,
    DISCOVERY_EXTENSION_PROMPT,
    BUILD_DISCOVERY_USER_MESSAGE,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# --- Constants ---
TAXONOMY_FILE = Path(__file__).parent.parent / "taxonomy_v2.json"
# Lifted to config/thresholds.json `score_floor` (G-18) — same value the
# rest of the pipeline (assign_subtopics, backfill_topic, backfill_all,
# score_publications, load_dynamodb) consumes.
SCORE_FLOOR = load_thresholds()["score_floor"]
COVERAGE_TARGET = 0.85   # D-01: must assign ≥85% of activities
# Lifted to config/thresholds.json `discover_min_cluster_size` (#57 Tier B-1).
MIN_CLUSTER_SIZE = int(load_thresholds()["discover_min_cluster_size"])



# ---------------------------------------------------------------------------
# Slug / ID generation
# WARNING: IDs are NOT stable across recomputes — D-06
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    """
    Convert a human-readable label into an ID-safe slug.

    WARNING: IDs are NOT stable across recomputes — D-06
    Subtopic IDs may change on each full recompute. Consumers MUST re-read
    hierarchy.json after regeneration and MUST NOT persist subtopic IDs
    in external systems that outlive a recompute cycle.

    Examples:
        "Cellular Senescence & Senolytics" -> "cellular_senescence_senolytics"
        "HIV/AIDS Research"               -> "hiv_aids_research"
    """
    lower = text.lower()
    slug = re.sub(r"[^a-z0-9]+", "_", lower)
    slug = slug.strip("_")
    return slug


def _make_subtopic_id(topic_prefix: str, label: str) -> str:
    """Build a subtopic ID from topic_prefix and slugified label.

    WARNING: IDs are NOT stable across recomputes — D-06
    """
    return f"{topic_prefix}_{_slugify(label)}"


# ---------------------------------------------------------------------------
# Taxonomy helpers
# ---------------------------------------------------------------------------

def _load_taxonomy() -> dict:
    """Load taxonomy_v2.json and return a dict keyed by topic_id."""
    with open(TAXONOMY_FILE) as f:
        data = json.load(f)
    topics = data.get("topics", [])
    return {t["id"]: t for t in topics}


def _get_topic_prefix(topic_id: str) -> str:
    """Extract the first segment of topic_id as the subtopic ID prefix.

    Examples:
        "aging_geroscience"       -> "aging"
        "cardiovascular_disease"  -> "cardiovascular"
        "infectious_disease_immunology" -> "infectious"
    """
    return topic_id.split("_")[0]


# ---------------------------------------------------------------------------
# DynamoDB query
# ---------------------------------------------------------------------------

def _parse_score_from_sk(sk: str) -> float:
    """
    Extract the relevance score from a SCORE# sort key.

    SK format: SCORE#{4-digit-score}#ACTIVITY#pmid_{pmid}
    The 4-digit integer is score * 1000, zero-padded.

    Examples:
        "SCORE#0850#ACTIVITY#pmid_12345" -> 0.85
        "SCORE#0300#ACTIVITY#pmid_67890" -> 0.30
    """
    parts = sk.split("#")
    if len(parts) < 2 or not parts[1].isdigit():
        return 0.0
    return int(parts[1]) / 1000.0


def _query_topic_activities(topic_id: str) -> list:
    """
    Query DynamoDB TOPIC#<topic_id> partition for activities scoring ≥ SCORE_FLOOR.

    Returns a deduplicated list of activity dicts:
        {pmid (str), title (str), synopsis (str), impact_score (float), relevance_score (float)}

    DynamoDB SK prefix "SCORE#" ensures only activity records are fetched.
    Score filtering uses the 4-digit zero-padding so we can filter client-side.
    """
    table = get_table(TABLE_NAME)

    from boto3.dynamodb.conditions import Key

    pk = f"TOPIC#{topic_id}"
    logger.info(f"Querying DynamoDB partition: {pk}")

    items = []
    last_key = None

    while True:
        kwargs = {
            "KeyConditionExpression": (
                Key("PK").eq(pk) & Key("SK").begins_with("SCORE#")
            ),
            "Limit": 1000,
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key

        response = table.query(**kwargs)
        items.extend(response.get("Items", []))

        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break

    logger.info(f"Fetched {len(items)} raw SCORE# items for {topic_id}")

    # Deduplicate by pmid, keep highest-scored record per pmid
    seen: dict[str, dict] = {}
    for item in items:
        sk = item.get("SK", "")
        score = _parse_score_from_sk(sk)
        if score < SCORE_FLOOR:
            continue

        # Extract pmid from SK: "SCORE#NNNN#ACTIVITY#pmid_{pmid}"
        pmid_str = None
        sk_parts = sk.split("#")
        if len(sk_parts) >= 4 and sk_parts[3].startswith("pmid_"):
            pmid_str = sk_parts[3][len("pmid_"):]

        if not pmid_str:
            # Fallback: try item attribute
            pmid_str = str(item.get("pmid", ""))

        if not pmid_str:
            continue

        if pmid_str not in seen or score > seen[pmid_str].get("_score", 0):
            seen[pmid_str] = {
                "pmid": pmid_str,
                "title": str(item.get("title", "") or ""),
                "synopsis": str(item.get("synopsis", "") or ""),
                "impact_score": float(item.get("impact_score", 0) or 0),
                "relevance_score": score,
                "_score": score,  # internal — stripped before passing to LLM
            }

    # Strip internal _score key
    activities = []
    for a in seen.values():
        entry = {k: v for k, v in a.items() if k != "_score"}
        activities.append(entry)

    logger.info(
        f"After dedup + score filter (≥{SCORE_FLOOR}): "
        f"{len(activities)} unique activities"
    )
    return activities


# ---------------------------------------------------------------------------
# JSON fence stripper (mirrors Phase 2 stripJsonFences pattern)
# ---------------------------------------------------------------------------

def _strip_json_fences(text: str) -> str:
    """Remove markdown code fences from a JSON response string."""
    stripped = re.sub(r"```(?:json)?\s*", "", text)
    stripped = stripped.replace("```", "")
    return stripped.strip()


# ---------------------------------------------------------------------------
# Sonnet call helpers
# ---------------------------------------------------------------------------

def _call_discovery_pass(
    client: BedrockClient,
    topic_id: str,
    topic_label: str,
    topic_description: str,
    activities: list,
    topic_prefix: str,
) -> tuple[dict, dict]:
    """
    Call Sonnet for the initial discovery pass.

    Returns (parsed_response_dict, usage_dict).
    Raises json.JSONDecodeError on parse failure (caller handles exit code 3).
    """
    user_msg = BUILD_DISCOVERY_USER_MESSAGE(
        topic_id=topic_id,
        topic_label=topic_label,
        topic_description=topic_description,
        activities=activities,
        topic_prefix=topic_prefix,
    )

    messages = [{"role": "user", "content": user_msg}]

    # D-01: temperature=0 for reproducibility
    raw_response = client._call_with_retry(
        model=SONNET_MODEL,
        messages_converse=client._translate_messages(messages, DISCOVERY_SYSTEM_PROMPT)[0],
        system_list=client._translate_messages(messages, DISCOVERY_SYSTEM_PROMPT)[1],
        max_tokens=16384,  # 8192 truncated mid-JSON on a 1104-activity topic; 16384 matches generate_taxonomy.py's precedent for the same model
        temperature=0.0,  # D-01 reproducibility requirement
    )

    text = raw_response["output"]["message"]["content"][0]["text"]
    usage = raw_response.get("usage", {})

    logger.info(
        f"Sonnet response: inputTokens={usage.get('inputTokens', '?')}, "
        f"outputTokens={usage.get('outputTokens', '?')}"
    )

    cleaned = _strip_json_fences(text)
    parsed, _ = json.JSONDecoder().raw_decode(cleaned)  # tolerate trailing prose after the JSON object
    return parsed, usage


def _call_extension_pass(
    client: BedrockClient,
    existing_subtopics: list,
    uncovered_activities: list,
    extension_cap: int,
    topic_prefix: str,
    current_coverage: float,
) -> tuple[dict, dict]:
    """
    Call Sonnet for the extension pass when coverage < COVERAGE_TARGET.

    Args:
        current_coverage: Coverage fraction from the initial pass (e.g., 0.72).

    Returns (parsed_response_dict, usage_dict).
    """
    current_subtopics_json = json.dumps(
        [{"id": s["id"], "label": s["label"]} for s in existing_subtopics]
    )
    uncovered_activities_json = json.dumps(uncovered_activities)

    prompt_text = DISCOVERY_EXTENSION_PROMPT.format(
        current_coverage_pct=current_coverage,
        uncovered_count=len(uncovered_activities),
        extension_cap=extension_cap,
        current_subtopics_json=current_subtopics_json,
        uncovered_activities_json=uncovered_activities_json,
    )

    messages = [{"role": "user", "content": prompt_text}]
    converse_msgs, system_list = client._translate_messages(messages, DISCOVERY_SYSTEM_PROMPT)

    raw_response = client._call_with_retry(
        model=SONNET_MODEL,
        messages_converse=converse_msgs,
        system_list=system_list,
        max_tokens=16384,  # 4096 truncated mid-JSON on 774 uncovered activities; match the initial-pass cap
        temperature=0.0,  # D-01 reproducibility requirement
    )

    text = raw_response["output"]["message"]["content"][0]["text"]
    usage = raw_response.get("usage", {})

    logger.info(
        f"Extension pass Sonnet: inputTokens={usage.get('inputTokens', '?')}, "
        f"outputTokens={usage.get('outputTokens', '?')}"
    )

    cleaned = _strip_json_fences(text)
    parsed, _ = json.JSONDecoder().raw_decode(cleaned)  # tolerate trailing prose after the JSON object
    return parsed, usage


# ---------------------------------------------------------------------------
# Cluster validation and cleanup
# ---------------------------------------------------------------------------

def _validate_and_clean_clusters(
    subtopics: list,
    total_activity_count: int,
) -> tuple[list, list]:
    """
    Validate clusters: drop any cluster with fewer than MIN_CLUSTER_SIZE seed_pmids.

    Returns (valid_subtopics, dropped_pmids).
    """
    valid = []
    dropped_pmids = []

    for cluster in subtopics:
        seed_pmids = cluster.get("seed_pmids", [])
        if len(seed_pmids) < MIN_CLUSTER_SIZE:
            logger.warning(
                f"Dropping cluster '{cluster.get('id', '?')}' — "
                f"only {len(seed_pmids)} seed_pmids (floor={MIN_CLUSTER_SIZE})"
            )
            dropped_pmids.extend(seed_pmids)
        else:
            valid.append(cluster)

    return valid, dropped_pmids


def _compute_coverage(subtopics: list, total_activity_count: int) -> tuple[set, float]:
    """
    Compute coverage: fraction of total activities assigned to at least one cluster.

    Returns (assigned_pmids_set, coverage_fraction).
    """
    assigned = set()
    for cluster in subtopics:
        for pmid in cluster.get("seed_pmids", []):
            assigned.add(str(pmid))
    coverage = len(assigned) / total_activity_count if total_activity_count > 0 else 0.0
    return assigned, coverage


# ---------------------------------------------------------------------------
# Main discovery logic
# ---------------------------------------------------------------------------

def discover_subtopics(
    topic_id: str,
    min_activities: int = 30,
    extension_cap: int = 15,
    output_dir: Path = Path(".planning/phases/04-subtopic-system"),
    dry_run: bool = False,
) -> dict:
    """
    Run Pass 1 discovery for a single topic.

    Args:
        topic_id: Topic ID from taxonomy_v2.json (e.g., "aging_geroscience").
        min_activities: Cold-start floor — skip topic if fewer activities (D-01).
        extension_cap: Max additional clusters in extension pass (D-01).
        output_dir: Directory to write hierarchy_draft_{topic_id}.json.
        dry_run: If True, skip writing output file; print summary only.

    Returns:
        Output dict (as would be written to JSON).

    Raises:
        SystemExit(2) if topic has fewer than min_activities activities.
        SystemExit(3) if Bedrock response cannot be parsed as JSON.
    """
    # 1. Load taxonomy
    taxonomy = _load_taxonomy()
    if topic_id not in taxonomy:
        logger.error(f"Topic '{topic_id}' not found in taxonomy_v2.json")
        sys.exit(1)

    topic_entry = taxonomy[topic_id]
    topic_label = topic_entry["label"]
    topic_description = topic_entry["description"]
    topic_prefix = _get_topic_prefix(topic_id)

    logger.info(f"Topic: {topic_label} (id: {topic_id}, prefix: {topic_prefix})")

    # 2. Query DynamoDB for activities
    activities = _query_topic_activities(topic_id)

    if len(activities) < min_activities:
        msg = (
            f"Topic {topic_id} has only {len(activities)} activities "
            f"(floor={min_activities}); skipping. "
            f"Add to excluded_topics when assembling hierarchy.json."
        )
        print(msg)
        logger.warning(msg)
        sys.exit(2)

    logger.info(
        f"Proceeding with {len(activities)} activities "
        f"(min_activities floor={min_activities})"
    )

    # All activities are sent to Sonnet in a single pass. At ~47K input tokens
    # for ~571 activities, this is well within Sonnet's 200K context limit.
    # RESEARCH.md note about batching for >300 activities is a precaution that
    # is not needed in practice given the observed token budget.
    all_activity_pmids = {a["pmid"] for a in activities}

    # 3. Build Bedrock client
    client = BedrockClient()

    # 4. Initial discovery pass
    logger.info("Starting initial Sonnet discovery pass...")
    total_input_tokens = 0
    total_output_tokens = 0

    try:
        pass1_result, pass1_usage = _call_discovery_pass(
            client=client,
            topic_id=topic_id,
            topic_label=topic_label,
            topic_description=topic_description,
            activities=activities,
            topic_prefix=topic_prefix,
        )
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse failure on initial pass: {e}")
        if not dry_run:
            raw_path = output_dir / f"hierarchy_draft_{topic_id}.raw.txt"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(f"JSON parse error: {e}\n")
        sys.exit(3)

    total_input_tokens += pass1_usage.get("inputTokens", 0)
    total_output_tokens += pass1_usage.get("outputTokens", 0)

    subtopics = pass1_result.get("subtopics", [])
    passes_executed = 1

    # 5. Validate and clean initial clusters (drop singletons)
    subtopics, extra_uncovered = _validate_and_clean_clusters(subtopics, len(activities))

    # Get uncovered: union of (LLM-reported uncovered) + (singletons dropped above)
    # also add any activities whose pmid never appeared in any seed_pmids list
    llm_uncovered = {str(p) for p in pass1_result.get("uncovered", [])}
    assigned_pmids_set, coverage = _compute_coverage(subtopics, len(activities))
    all_activity_set = {str(a["pmid"]) for a in activities}
    truly_uncovered = all_activity_set - assigned_pmids_set
    uncovered_pmids = list(truly_uncovered | llm_uncovered | {str(p) for p in extra_uncovered})

    # 6. Compute coverage
    assigned_pmids, coverage = _compute_coverage(subtopics, len(activities))
    logger.info(
        f"Pass 1: {len(subtopics)} clusters, coverage={coverage:.1%}, "
        f"uncovered={len(uncovered_pmids)}"
    )

    # 7. Extension pass if needed (D-01)
    # Cap: can add up to min(extension_cap, initial_count) NEW clusters.
    # Spirit of D-01: total clusters must not exceed 2× initial_count.
    initial_cluster_count = len(subtopics)
    actual_extension_cap = min(extension_cap, initial_cluster_count)  # add at most N more

    if coverage < COVERAGE_TARGET and actual_extension_cap > 0:
        logger.info(
            f"Coverage {coverage:.1%} < {COVERAGE_TARGET:.0%}. "
            f"Running extension pass (cap={actual_extension_cap} new clusters)..."
        )

        # Build uncovered activities list for extension prompt
        uncovered_set = set(uncovered_pmids)
        uncovered_activities = [a for a in activities if str(a["pmid"]) in uncovered_set]

        try:
            ext_result, ext_usage = _call_extension_pass(
                client=client,
                existing_subtopics=subtopics,
                uncovered_activities=uncovered_activities,
                extension_cap=actual_extension_cap,
                topic_prefix=topic_prefix,
                current_coverage=coverage,
            )
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse failure on extension pass: {e}")
            logger.warning("Continuing with initial pass results only.")
            ext_result = {"new_subtopics": [], "still_uncovered": uncovered_pmids}
            ext_usage = {}

        total_input_tokens += ext_usage.get("inputTokens", 0)
        total_output_tokens += ext_usage.get("outputTokens", 0)
        passes_executed = 2

        new_subtopics = ext_result.get("new_subtopics", [])
        new_valid, new_extra_uncovered = _validate_and_clean_clusters(
            new_subtopics, len(activities)
        )
        subtopics.extend(new_valid)

        still_uncovered_raw = ext_result.get("still_uncovered", [])
        uncovered_pmids = list(
            set([str(p) for p in still_uncovered_raw] +
                [str(p) for p in new_extra_uncovered])
        )

        # Recompute coverage after extension
        assigned_pmids, coverage = _compute_coverage(subtopics, len(activities))
        logger.info(
            f"Pass 2: {len(subtopics)} clusters total, "
            f"coverage={coverage:.1%}, uncovered={len(uncovered_pmids)}"
        )
    elif coverage < COVERAGE_TARGET:
        logger.warning(
            f"Coverage {coverage:.1%} < {COVERAGE_TARGET:.0%} but "
            f"extension_cap=0 or no room for more clusters. Proceeding."
        )

    # 8. Assemble output
    output = {
        "topic_id": topic_id,
        "topic_label": topic_label,
        "subtopics": subtopics,
        "uncovered_pmids": uncovered_pmids,
        "total_activities": len(activities),
        "coverage_pct": round(coverage, 4),
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "sonnet_model_id": SONNET_MODEL,
        "passes_executed": passes_executed,
    }

    # 9. Print summary
    print(
        f"\n=== Pass 1 Discovery Results ===\n"
        f"  topic_id:         {topic_id}\n"
        f"  topic_label:      {topic_label}\n"
        f"  total_activities: {len(activities)}\n"
        f"  cluster_count:    {len(subtopics)}\n"
        f"  coverage_pct:     {coverage:.1%}\n"
        f"  uncovered:        {len(uncovered_pmids)}\n"
        f"  passes_executed:  {passes_executed}\n"
        f"  input_tokens:     {total_input_tokens}\n"
        f"  output_tokens:    {total_output_tokens}\n"
        f"  dry_run:          {dry_run}\n"
    )

    if dry_run:
        print("[dry-run] Skipping file write.")
        return output

    # 10. Write output
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"hierarchy_draft_{topic_id}.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"[output] Written to: {out_path}")
    logger.info(f"Wrote hierarchy draft to {out_path}")

    return output


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _parse_args():
    parser = argparse.ArgumentParser(
        description="Pass 1 discovery: cluster topic activities into subtopics via Sonnet."
    )
    parser.add_argument(
        "--topic",
        required=True,
        help="Topic ID from taxonomy_v2.json (e.g., aging_geroscience)",
    )
    parser.add_argument(
        "--min-activities",
        type=int,
        default=30,
        metavar="N",
        help="Cold-start floor: skip topic if fewer than N activities (default: 30, D-01)",
    )
    parser.add_argument(
        "--extension-cap",
        type=int,
        default=15,
        metavar="N",
        help=(
            "Max additional clusters allowed in extension pass "
            "when coverage <85%% (default: 15, D-01)"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".planning/phases/04-subtopic-system"),
        metavar="DIR",
        help=(
            "Directory to write hierarchy_draft_{topic_id}.json "
            "(default: .planning/phases/04-subtopic-system)"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip writing output file; print summary only (useful for testing)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    discover_subtopics(
        topic_id=args.topic,
        min_activities=args.min_activities,
        extension_cap=args.extension_cap,
        output_dir=args.output_dir,
        dry_run=args.dry_run,
    )

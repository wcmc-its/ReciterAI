"""
See-also generation: single-pass Sonnet + bidirectionality filter.

Reads a hierarchy JSON (full hierarchy.json or a Pass 1 single-topic draft) and
emits a filtered `see_also` array where each surviving link has its reverse
counterpart also present in the same Sonnet output.

Algorithm (Research §See-Also Generation, CONTEXT D-08/D-09/D-10):
  1. Load input JSON. Count distinct parent topics (parents with >=1 subtopic).
  2. If parent topic count < 2, write empty output and exit 0 (no cross-topic
     links are possible from a single-topic fragment).
  3. Call Sonnet ONCE at temperature=0 (default) with the static
     SEE_ALSO_SYSTEM_PROMPT + a user message flattening all subtopics.
  4. Parse the JSON response {"links": [...]}.
  5. Apply _apply_bidirectionality_filter:
       - drop self-loops
       - deduplicate on (from, to) tuple, keep first occurrence
       - retain link (a,b) only if (b,a) also appeared in the Sonnet output
         (after dedup and self-loop removal)
  6. Write output JSON with generated_at, temperature, pre_filter_count,
     post_filter_count, sonnet_model_id, and the filtered see_also array.

Design decisions:
  D-06  Consumers must re-read after each regen (IDs not stable across recomputes).
  D-08  See-also generation is Sonnet batch after Pass 1; regenerated per recompute.
  D-09  Bidirectionality is the quality gate in lieu of human review.
  D-10  See-also used for Tier 2 router expansion AND topic_decompose navigation.

Principle P-09 (asymmetric-but-valid links risk):
  A genuinely one-way relationship will be dropped by this filter. That is an
  acceptable tradeoff: bidirectionality gives us a cheap, deterministic quality
  gate without human review, at the cost of occasionally losing a true positive.

Usage:
    python generate_see_also.py --input hierarchy.json --output see_also.json
    python generate_see_also.py --input draft.json --output out.json --temperature 0.0

Exit codes:
    0 — success (including empty-output short-circuit)
    1 — unexpected error
    2 — Bedrock response JSON parse failure after fence-strip + retry
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure repo root is on sys.path for utils / prompts imports
sys.path.insert(0, str(Path(__file__).parent))

from utils.bedrock_client import BedrockClient, SONNET_MODEL
from prompts.see_also_generation import (
    SEE_ALSO_SYSTEM_PROMPT,
    BUILD_SEE_ALSO_USER_MESSAGE,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bidirectionality filter
# ---------------------------------------------------------------------------

def _apply_bidirectionality_filter(links: list) -> tuple:
    """
    Filter a list of proposed see-also links down to bidirectional survivors.

    Applied in order:
      1. Drop self-loops (from == to) unconditionally.
      2. Deduplicate on (from, to) — keep the first occurrence.
      3. Retain (a, b) only if (b, a) is also present in the post-dedup set.

    Args:
        links: list of dicts with keys "from", "to", and "reason".

    Returns:
        tuple of (kept_links, stats) where stats is a dict with counts of
        dropped_self_loop, dropped_duplicate, and dropped_non_bidirectional.

    References:
        D-09  bidirectionality is the quality gate
        P-09  acknowledged: genuinely asymmetric but valid links are dropped
    """
    stats = {
        "dropped_self_loop": 0,
        "dropped_duplicate": 0,
        "dropped_non_bidirectional": 0,
    }

    # Step 1 + 2: drop self-loops, then dedupe by (from, to) preserving first.
    seen: set = set()
    deduped: list = []
    for link in links:
        f = link.get("from", "")
        t = link.get("to", "")
        if f == t:
            stats["dropped_self_loop"] += 1
            continue
        key = (f, t)
        if key in seen:
            stats["dropped_duplicate"] += 1
            continue
        seen.add(key)
        deduped.append(link)

    # Step 3: keep only bidirectional pairs.
    adj = {(l["from"], l["to"]) for l in deduped}
    kept: list = []
    for link in deduped:
        f = link["from"]
        t = link["to"]
        if (t, f) in adj:
            kept.append(link)
        else:
            stats["dropped_non_bidirectional"] += 1

    return kept, stats


# ---------------------------------------------------------------------------
# Sonnet call wrapper (patchable for tests)
# ---------------------------------------------------------------------------

def _call_sonnet_for_see_also(user_message: str, temperature: float,
                              max_tokens: int = 32768) -> dict:
    """
    Call Sonnet once with the static see-also prompt and return parsed JSON.

    Separated into its own function so tests can mock this boundary without
    needing to patch BedrockClient internals.

    Args:
        user_message: user message body produced by BUILD_SEE_ALSO_USER_MESSAGE.
        temperature: sampling temperature (0.0 default for reproducibility per D-08).
        max_tokens: token cap for the response (8192 default — see-also batches are modest).

    Returns:
        Parsed dict from Sonnet's JSON response. Expected shape: {"links": [...]}.
    """
    client = BedrockClient()
    return client.call_json(
        model=SONNET_MODEL,
        messages=[{"role": "user", "content": user_message}],
        system=SEE_ALSO_SYSTEM_PROMPT,
        max_tokens=max_tokens,
        temperature=temperature,
    )


# ---------------------------------------------------------------------------
# Parent-topic counting (shape-aware)
# ---------------------------------------------------------------------------

def _count_parent_topics(hierarchy_json: dict) -> int:
    """
    Count distinct parent topics with at least one subtopic.

    Accepts full-hierarchy ({"topics": {...}}) and single-topic-draft
    ({"topic_id": ..., "subtopics": [...]}) shapes.
    """
    if "topics" in hierarchy_json and isinstance(hierarchy_json["topics"], dict):
        count = 0
        for _, entry in hierarchy_json["topics"].items():
            subs = entry.get("subtopics", []) if isinstance(entry, dict) else []
            if subs:
                count += 1
        return count
    if "topic_id" in hierarchy_json and "subtopics" in hierarchy_json:
        return 1 if hierarchy_json.get("subtopics") else 0
    raise ValueError(
        "hierarchy_json must have either a top-level 'topics' dict "
        "or 'topic_id' + 'subtopics' keys"
    )


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------

def generate_see_also(input_path: str, output_path: str,
                      temperature: float = 0.0) -> dict:
    """
    Generate filtered see-also links for a hierarchy JSON.

    Args:
        input_path: path to input hierarchy JSON (full or single-topic draft).
        output_path: path to write the filtered see_also output JSON.
        temperature: Sonnet sampling temperature (default 0.0 for reproducibility).

    Returns:
        The output dict that was written to output_path.
    """
    input_file = Path(input_path)
    output_file = Path(output_path)

    with input_file.open("r", encoding="utf-8") as f:
        hierarchy = json.load(f)

    parent_count = _count_parent_topics(hierarchy)
    logger.info("Parent topics with >=1 subtopic: %d", parent_count)

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    if parent_count < 2:
        logger.info(
            "Short-circuit: only %d parent topic(s) present. "
            "No cross-topic see-also links possible; writing empty output.",
            parent_count,
        )
        output = {
            "see_also": [],
            "generated_at": generated_at,
            "temperature": temperature,
            "pre_filter_count": 0,
            "post_filter_count": 0,
            "sonnet_model_id": SONNET_MODEL,
            "short_circuit_reason": "fewer than 2 parent topics in input",
            "parent_topic_count": parent_count,
        }
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with output_file.open("w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        logger.info("Wrote %s (empty see_also, short-circuited).", output_file)
        return output

    # Full path: call Sonnet once, then filter.
    user_message = BUILD_SEE_ALSO_USER_MESSAGE(hierarchy)
    logger.info("Calling Sonnet (temperature=%s) for see-also proposals...", temperature)
    response = _call_sonnet_for_see_also(user_message, temperature=temperature)

    links = response.get("links", []) if isinstance(response, dict) else []
    pre_count = len(links)
    logger.info("Sonnet proposed %d raw links.", pre_count)

    kept, stats = _apply_bidirectionality_filter(links)
    post_count = len(kept)

    logger.info(
        "Filter stats: proposed=%d, kept=%d, dropped_non_bidirectional=%d, "
        "dropped_self_loop=%d, dropped_duplicate=%d",
        pre_count, post_count,
        stats["dropped_non_bidirectional"],
        stats["dropped_self_loop"],
        stats["dropped_duplicate"],
    )

    output = {
        "see_also": kept,
        "generated_at": generated_at,
        "temperature": temperature,
        "pre_filter_count": pre_count,
        "post_filter_count": post_count,
        "sonnet_model_id": SONNET_MODEL,
        "filter_stats": stats,
        "parent_topic_count": parent_count,
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    logger.info("Wrote %s (%d see_also links).", output_file, post_count)

    return output


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list = None):
    parser = argparse.ArgumentParser(
        description="Generate filtered see-also links for a hierarchy JSON "
                    "(single-pass Sonnet + bidirectionality filter).",
    )
    parser.add_argument(
        "--input", required=True,
        help="Path to input hierarchy JSON (full hierarchy or single-topic draft).",
    )
    parser.add_argument(
        "--output", required=True,
        help="Path to write the filtered see_also output JSON.",
    )
    parser.add_argument(
        "--temperature", type=float, default=0.0,
        help="Sonnet sampling temperature (default 0.0 for reproducibility).",
    )
    return parser.parse_args(argv)


def main(argv: list = None) -> int:
    args = _parse_args(argv)
    try:
        generate_see_also(
            input_path=args.input,
            output_path=args.output,
            temperature=args.temperature,
        )
        return 0
    except json.JSONDecodeError as e:
        logger.error("Bedrock response JSON parse failure: %s", e)
        return 2
    except Exception as e:
        logger.exception("Unexpected error: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())

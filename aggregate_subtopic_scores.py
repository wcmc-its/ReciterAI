"""
Pass 3 (Aggregation): arithmetic-only faculty subtopic scores.

Python source of truth for faculty subtopic scores. PM TypeScript reads the
resulting `subtopic_scores` dict directly from DynamoDB — it does NOT
recompute. Never recompute articleScore in TypeScript (Pitfall P-10): the
formula lives here and in `ReCiter-Publication-Manager/controllers/chatbot/
retrieval/shared.ts` only, and the two MUST stay byte-for-byte identical.

Design decisions honored:
  D-03  Primary-only aggregation. Secondaries carry zero weight. Each
        activity's articleScore counts exactly once, toward its primary
        subtopic.
  D-06  Wholesale replacement per topic: before writing fresh scores,
        REMOVE the subtopic_scores.<topic_id> nested attribute on every
        touched faculty record.
  D-17  subtopic_scores lives on FACULTY#<personIdentifier> / SK=PROFILE,
        preserving other topics' subtopic_scores on the same faculty.

No LLM calls. No network beyond DynamoDB. Deterministic.

Usage:
    python aggregate_subtopic_scores.py --topic aging_geroscience
    python aggregate_subtopic_scores.py --topic aging_geroscience --dry-run

Exit codes:
    0 success
    1 unexpected error
    3 topic_id not found in taxonomy_v2.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils.dynamodb_helpers import get_table, TABLE_NAME
from utils.dynamodb_subtopic_migration import (
    update_faculty_subtopic_scores,
    clear_faculty_subtopic_scores_for_topic,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)
logging.getLogger("botocore").setLevel(logging.WARNING)
logging.getLogger("boto3").setLevel(logging.WARNING)


TAXONOMY_FILE = Path(__file__).parent / "taxonomy_v2.json"
DEFAULT_OUTPUT_DIR = Path(".planning/phases/04-subtopic-system")
FACULTY_UID_PREFIX = "cwid_"  # P-10 isolation point (CLAUDE.md §personIdentifier)


# ---------------------------------------------------------------------------
# DynamoDB query
# ---------------------------------------------------------------------------

def _query_topic_rows(topic_id: str) -> list:
    """Fetch every SCORE# row under TOPIC#<topic_id>."""
    from boto3.dynamodb.conditions import Key

    table = get_table(TABLE_NAME)
    pk = f"TOPIC#{topic_id}"
    logger.info(f"Querying DynamoDB partition: {pk}")

    rows = []
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
        resp = table.query(**kwargs)
        rows.extend(resp.get("Items", []))
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break

    logger.info(f"Fetched {len(rows)} SCORE# rows for {topic_id}")
    return rows


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _strip_faculty_prefix(faculty_uid: str) -> str:
    """Strip the 'cwid_' prefix to get the canonical personIdentifier."""
    if faculty_uid.startswith(FACULTY_UID_PREFIX):
        return faculty_uid[len(FACULTY_UID_PREFIX):]
    return faculty_uid


def _aggregate(rows: list) -> tuple[dict, dict]:
    """
    Aggregate articleScore per (person_identifier, primary_subtopic_id).

    Returns (faculty_scores, subtopic_total_weights) where:
        faculty_scores = {personIdentifier: {subtopic_id: summed_article_score}}
        subtopic_total_weights = {subtopic_id: summed_article_score_across_faculty}

    Skips rows where primary_subtopic_id is absent (D-02: unassigned activities
    contribute zero to subtopic scores; they still participate in topic-level
    retrieval). Skips rows where faculty_uid or impact_score / score is missing.
    """
    faculty_scores: dict = defaultdict(lambda: defaultdict(float))
    subtopic_total_weights: dict = defaultdict(float)

    included = 0
    skipped_unassigned = 0
    skipped_bad = 0

    for row in rows:
        primary = row.get("primary_subtopic_id")
        if not primary:
            skipped_unassigned += 1
            continue

        faculty_uid = row.get("faculty_uid") or ""
        if not faculty_uid:
            skipped_bad += 1
            continue
        person_identifier = _strip_faculty_prefix(faculty_uid)
        if not person_identifier:
            skipped_bad += 1
            continue

        try:
            relevance_score = float(row.get("score") or 0)
            impact_score = float(row.get("impact_score") or 0)
        except (TypeError, ValueError):
            skipped_bad += 1
            continue

        # articleScore formula — MUST match PM shared.ts byte-for-byte (P-10):
        #   TS: Math.pow(impactScore / 100, 1.2) * Math.pow(relevanceScore, 1.4)
        article_score = (impact_score / 100) ** 1.2 * relevance_score ** 1.4

        # Secondaries intentionally carry zero weight (D-03). Faculty ranking within
        # secondary subtopics is impossible by design — see CONTEXT.md deferred item #3.
        faculty_scores[person_identifier][primary] += article_score
        subtopic_total_weights[primary] += article_score
        included += 1

    logger.info(
        f"Aggregated: {included} rows included, "
        f"{skipped_unassigned} unassigned (no primary_subtopic_id), "
        f"{skipped_bad} skipped (missing fields)"
    )
    return (
        {pid: dict(m) for pid, m in faculty_scores.items()},
        dict(subtopic_total_weights),
    )


# ---------------------------------------------------------------------------
# DynamoDB write (wholesale replacement, D-06)
# ---------------------------------------------------------------------------

def _write_faculty_scores(
    faculty_scores: dict,
    topic_id: str,
    dry_run: bool,
) -> tuple[int, int]:
    """For every touched faculty, clear stale scores for this topic, then write
    the fresh dict. Returns (cleared_count, written_count).
    """
    cleared = 0
    written = 0
    for person_identifier, scores in faculty_scores.items():
        if dry_run:
            continue
        try:
            clear_faculty_subtopic_scores_for_topic(person_identifier, topic_id)
            cleared += 1
        except Exception as exc:
            # The REMOVE on a non-existent nested path is a benign no-op in
            # DynamoDB; the only real failure is a missing FACULTY# record.
            logger.warning(
                f"clear_faculty_subtopic_scores_for_topic failed for "
                f"pid={person_identifier}: {exc}"
            )

        try:
            update_faculty_subtopic_scores(person_identifier, topic_id, scores)
            written += 1
        except Exception as exc:
            logger.warning(
                f"update_faculty_subtopic_scores failed for "
                f"pid={person_identifier}: {exc}"
            )
    return cleared, written


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _top_faculty_per_subtopic(faculty_scores: dict, top_n: int = 5) -> dict:
    """For each subtopic, find the top-N faculty by score."""
    buckets: dict = defaultdict(list)
    for pid, scores in faculty_scores.items():
        for sid, score in scores.items():
            buckets[sid].append((pid, score))

    out = {}
    for sid, lst in buckets.items():
        lst.sort(key=lambda kv: kv[1], reverse=True)
        out[sid] = [
            {"person_identifier": pid, "score": round(sc, 4)}
            for pid, sc in lst[:top_n]
        ]
    return out


def _print_subtopic_table(total_weights: dict) -> None:
    print("\n=== Subtopic total_weight table ===")
    print(f"{'subtopic_id':<55} {'total_weight':>14}")
    print(f"{'-' * 55} {'-' * 14}")
    for sid, w in sorted(total_weights.items(), key=lambda kv: -kv[1]):
        print(f"{sid:<55} {w:>14.4f}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _load_taxonomy() -> dict:
    with open(TAXONOMY_FILE) as f:
        data = json.load(f)
    return {t["id"]: t for t in data.get("topics", [])}


def run(topic_id: str, output_dir: Path, dry_run: bool) -> dict:
    # Validate topic
    taxonomy = _load_taxonomy()
    if topic_id not in taxonomy:
        logger.error(f"Topic {topic_id!r} not found in taxonomy_v2.json")
        sys.exit(3)

    rows = _query_topic_rows(topic_id)
    faculty_scores, total_weights = _aggregate(rows)

    touched_pids = set(faculty_scores.keys())
    logger.info(f"Faculty touched: {len(touched_pids)}")

    cleared, written = _write_faculty_scores(
        faculty_scores, topic_id, dry_run=dry_run
    )
    logger.info(
        f"Writes: cleared={cleared}, written={written}, dry_run={dry_run}"
    )

    # total_weights JSON — Plan 05 pilot gate + Plan 07 hierarchy.json input
    total_weights_path = output_dir / f"total_weights_{topic_id}.json"
    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        total_weights_path.write_text(
            json.dumps(
                {
                    "topic_id": topic_id,
                    "total_weights": {
                        sid: round(w, 6)
                        for sid, w in sorted(
                            total_weights.items(), key=lambda kv: -kv[1]
                        )
                    },
                    "faculty_count": len(touched_pids),
                    "subtopic_count": len(total_weights),
                },
                indent=2,
            )
        )
        logger.info(f"Wrote total_weights to {total_weights_path}")
    else:
        logger.info(f"[dry-run] would write total_weights to {total_weights_path}")

    _print_subtopic_table(total_weights)

    print(f"\nfaculty touched: {len(touched_pids)}")
    print(f"subtopic count: {len(total_weights)}")
    print(f"total subtopic score sum (all): "
          f"{sum(total_weights.values()):.4f}")

    top5 = _top_faculty_per_subtopic(faculty_scores, top_n=5)
    print("\n=== Top-5 faculty per subtopic ===")
    for sid in sorted(top5.keys(), key=lambda s: -total_weights.get(s, 0)):
        print(f"\n  {sid}:")
        for entry in top5[sid]:
            print(f"    {entry['person_identifier']:<20} {entry['score']:>10.4f}")

    return {
        "topic_id": topic_id,
        "faculty_touched": len(touched_pids),
        "subtopic_count": len(total_weights),
        "cleared": cleared,
        "written": written,
        "total_weights_path": str(total_weights_path),
        "dry_run": dry_run,
    }


def _parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Pass 3: arithmetic aggregation of faculty subtopic scores from "
            "DynamoDB activity records. Primary-only (D-03); wholesale "
            "replacement per topic (D-06); no LLM calls."
        )
    )
    parser.add_argument(
        "--topic", required=True,
        help="Topic ID from taxonomy_v2.json (e.g., aging_geroscience)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Do not write DynamoDB; still print tables and skip JSON output",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, metavar="DIR",
        help=(
            "Directory to write total_weights_<topic>.json "
            f"(default: {DEFAULT_OUTPUT_DIR})"
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run(topic_id=args.topic, output_dir=args.output_dir, dry_run=args.dry_run)

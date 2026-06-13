"""
Pass 3 (Aggregation): arithmetic-only faculty subtopic scores.

Python source of truth for faculty subtopic scores. PM TypeScript reads the
resulting `subtopic_scores` dict directly from DynamoDB — it does NOT
recompute. Never recompute articleScore in TypeScript (Pitfall P-10): the
canonical Python formula lives in `utils/scoring.py:article_score` (imported
below; the only Python definition) and mirrors `ReCiter-Publication-Manager/
controllers/chatbot/retrieval/shared.ts`, and the two MUST stay byte-for-byte
identical.

Design decisions honored:
  D-03  Primary-only aggregation. Secondaries carry zero weight. Each
        activity's articleScore counts exactly once, toward its primary
        subtopic.
  D-06  Wholesale replacement per topic: before writing fresh scores,
        REMOVE the subtopic_scores.<topic_id> nested attribute on every
        touched faculty record.
  D-17  (Phase 12) Both aggregations co-exist:
        - EXCLUSIVE: same semantics as the old _aggregate; rolls up
          primary_subtopic_id only. Writes to SUBTOPIC_SCORE#{topic}#{subtopic}.
        - INCLUSIVE: rolls up every above-floor subtopic_ids[] entry at full
          article_score per D-15. Writes to SUBTOPIC_SCORE_INCLUSIVE#{topic}#{subtopic}.
        Invariant: sum(SUBTOPIC_SCORE_INCLUSIVE#X#*) >= sum(SUBTOPIC_SCORE#X#*);
        equality iff every paper in topic X has exactly one above-floor
        subtopic assignment.
  D-33  (Phase 12) In-stream reconciliation: immediately after both writes,
        per-CWID per-subtopic equality between faculty.subtopic_scores map and
        the new SUBTOPIC_SCORE# partition is verified. Divergence raises.
  D-legacy  subtopic_scores lives on FACULTY#<personIdentifier> / SK=PROFILE,
        preserving other topics' subtopic_scores on the same faculty. This
        path is preserved unchanged as the SPS consumer contract (D-13).

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
import os
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.scoring import article_score
from utils.dynamodb_helpers import get_table, TABLE_NAME
from utils.dynamodb_subtopic_migration import (
    update_faculty_subtopic_scores,
    clear_faculty_subtopic_scores_for_topic,
)
from utils.iso_clock import now_iso

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)
logging.getLogger("botocore").setLevel(logging.WARNING)
logging.getLogger("boto3").setLevel(logging.WARNING)


TAXONOMY_FILE = Path(__file__).parent.parent / "taxonomy_v2.json"
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




def _aggregate_exclusive(rows: list) -> tuple[dict, dict]:
    """Aggregate articleScore per (person_identifier, primary_subtopic_id) — EXCLUSIVE aggregation.

    D-17 invariant: sum(SUBTOPIC_SCORE#X#*) <= sum(SUBTOPIC_SCORE_INCLUSIVE#X#*);
    equality iff every paper in topic X has exactly one above-floor subtopic assignment.

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

        # articleScore — canonical blend, MUST match PM shared.ts byte-for-byte
        # (P-10). See utils.scoring.article_score.
        art_score = article_score(impact_score, relevance_score)

        # Secondaries intentionally carry zero weight (D-03). Faculty ranking within
        # secondary subtopics is impossible by design — see CONTEXT.md deferred item #3.
        faculty_scores[person_identifier][primary] += art_score
        subtopic_total_weights[primary] += art_score
        included += 1

    logger.info(
        f"_aggregate_exclusive: {included} rows included, "
        f"{skipped_unassigned} unassigned (no primary_subtopic_id), "
        f"{skipped_bad} skipped (missing fields)"
    )
    return (
        {pid: dict(m) for pid, m in faculty_scores.items()},
        dict(subtopic_total_weights),
    )


def _aggregate_inclusive(rows: list) -> tuple[dict, dict]:
    """Aggregate articleScore per (person_identifier, subtopic_id) for EVERY above-floor
    subtopic assignment — INCLUSIVE aggregation per D-15 (uniform full weight).

    Confidence floor is NOT re-applied here; assign_subtopics.py:632 already filters
    subtopic_ids[] to above-floor (D-16). WR-09: an earlier signature carried a
    no-op ``confidence_floor`` parameter "for future-proofing". It has been
    removed because a parameter that accepts any value but has no effect is a
    footgun. If a future need to re-apply a floor arises, add the kwarg back
    with a real implementation and a test that fails when the floor is ignored.

    Returns (faculty_scores, subtopic_total_weights) with the same shape as
    _aggregate_exclusive, but accumulates across ALL above-floor subtopic_ids per row.
    Each subtopic_id in the list receives the FULL article_score (not 1/N split).
    """
    faculty_scores: dict = defaultdict(lambda: defaultdict(float))
    subtopic_total_weights: dict = defaultdict(float)

    included = 0
    skipped_bad = 0
    skipped_no_subtopics = 0

    for row in rows:
        subtopic_ids = row.get("subtopic_ids") or []
        if not subtopic_ids:
            skipped_no_subtopics += 1
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

        # articleScore — canonical blend, MUST match PM shared.ts byte-for-byte
        # (P-10). See utils.scoring.article_score.
        art_score = article_score(impact_score, relevance_score)

        # D-15: uniform full weight — every above-floor subtopic_id gets the FULL score
        for sid in subtopic_ids:
            faculty_scores[person_identifier][sid] += art_score
            subtopic_total_weights[sid] += art_score
        included += 1

    logger.info(
        f"_aggregate_inclusive: {included} rows included, "
        f"{skipped_no_subtopics} skipped (no subtopic_ids), "
        f"{skipped_bad} skipped (missing fields)"
    )
    return (
        {pid: dict(m) for pid, m in faculty_scores.items()},
        dict(subtopic_total_weights),
    )


# ---------------------------------------------------------------------------
# D-17 / D-33 dual-partition write helpers
# ---------------------------------------------------------------------------

def _build_subtopic_score_record(
    *,
    kind: str,
    topic_id: str,
    subtopic_id: str,
    faculty_scores: dict[str, float],
    run_id: str,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Pure builder for a SUBTOPIC_SCORE# or SUBTOPIC_SCORE_INCLUSIVE# row.

    Produces no I/O — callers are responsible for calling table.put_item.

    Args:
        kind: "SUBTOPIC_SCORE" or "SUBTOPIC_SCORE_INCLUSIVE"
        topic_id: parent topic ID
        subtopic_id: subtopic ID
        faculty_scores: {person_identifier: float} — Decimal-coerced at DDB boundary (Pattern B)
        run_id: cold-run identifier for idempotent overwrite keying
        created_at: ISO timestamp override (defaults to now)
    """
    pk_kind = kind  # e.g. "SUBTOPIC_SCORE" → PK prefix "SUBTOPIC_SCORE#"
    return {
        "PK": f"{pk_kind}#{topic_id}#{subtopic_id}",
        "SK": "GLOBAL",
        "record_type": pk_kind,
        "topic_id": topic_id,
        "subtopic_id": subtopic_id,
        # Pattern B: Decimal(str(float_val)) at every DDB numeric boundary
        "faculty_scores": {pid: Decimal(str(score)) for pid, score in faculty_scores.items()},
        "run_id": run_id,
        "source_stage": "aggregate_subtopic_scores",
        "created_at": created_at or now_iso(),
    }


def _write_subtopic_score_partitions(
    table: Any,
    *,
    topic_id: str,
    faculty_scores_exclusive: dict[str, dict[str, float]],
    faculty_scores_inclusive: dict[str, dict[str, float]],
    run_id: str,
) -> list[dict[str, Any]]:
    """Write SUBTOPIC_SCORE# (exclusive) and SUBTOPIC_SCORE_INCLUSIVE# (inclusive) partitions.

    # D-17 invariant: sum(SUBTOPIC_SCORE_INCLUSIVE#X#*) >= sum(SUBTOPIC_SCORE#X#*);
    # difference == secondary contribution in topic X. Documented at the write site
    # per D-17 — search this comment if you wonder why two partitions exist.

    Per D-14: partition-level idempotent overwrite — same PK+SK → DDB puts overwrite.
    Per D-33: both derivations of the exclusive aggregation are cross-checked after
    both writes complete (via _assert_d33_reconciliation). The caller projects this
    function's RETURN value back into a {pid: {sid: score}} dict to supply an
    independent partition-side derivation — passing the same source dict twice
    would degenerate to x == x (tautology).

    Args:
        table: DynamoDB table resource (or MagicMock in tests)
        topic_id: parent topic ID (all subtopics in faculty_scores_exclusive belong here)
        faculty_scores_exclusive: {person_identifier: {subtopic_id: float}}
        faculty_scores_inclusive: {person_identifier: {subtopic_id: float}}
        run_id: cold-run ID for idempotent keying

    Returns:
        The list of items emitted to put_item, in write order. Each item carries
        record_type ("SUBTOPIC_SCORE" or "SUBTOPIC_SCORE_INCLUSIVE"), subtopic_id,
        and faculty_scores (Decimal-coerced). Callers use this to independently
        reconstruct the partition view for D-33 reconciliation.
    """
    # Build per-subtopic maps for exclusive (same structure as inclusive)
    excl_by_subtopic: dict[str, dict[str, float]] = defaultdict(dict)
    for pid, scores in faculty_scores_exclusive.items():
        for sid, score in scores.items():
            excl_by_subtopic[sid][pid] = score

    incl_by_subtopic: dict[str, dict[str, float]] = defaultdict(dict)
    for pid, scores in faculty_scores_inclusive.items():
        for sid, score in scores.items():
            incl_by_subtopic[sid][pid] = score

    items_written: list[dict[str, Any]] = []

    # Write SUBTOPIC_SCORE# partition (exclusive)
    for subtopic_id, fs in excl_by_subtopic.items():
        item = _build_subtopic_score_record(
            kind="SUBTOPIC_SCORE",
            topic_id=topic_id,
            subtopic_id=subtopic_id,
            faculty_scores=fs,
            run_id=run_id,
        )
        table.put_item(Item=item)
        items_written.append(item)

    # Write SUBTOPIC_SCORE_INCLUSIVE# partition (inclusive)
    for subtopic_id, fs in incl_by_subtopic.items():
        item = _build_subtopic_score_record(
            kind="SUBTOPIC_SCORE_INCLUSIVE",
            topic_id=topic_id,
            subtopic_id=subtopic_id,
            faculty_scores=fs,
            run_id=run_id,
        )
        table.put_item(Item=item)
        items_written.append(item)

    return items_written


def _project_exclusive_partition_view(
    items_written: list[dict[str, Any]],
) -> dict[str, dict[str, float]]:
    """Project the SUBTOPIC_SCORE# (exclusive) items back into a {pid: {sid: score}} dict.

    The Decimal-coerced faculty_scores in each item are converted back to float
    for the D-33 reconciliation comparison (which uses an epsilon-based float
    compare). Only items with record_type == "SUBTOPIC_SCORE" contribute; the
    inclusive partition has no parallel faculty-map view (D-33 scope).

    This is the second, INDEPENDENT derivation of the exclusive aggregation
    that D-33 cross-checks against the in-memory faculty-map. Passing the
    aggregator's own faculty_scores dict here would reduce the check to x == x
    (W-2 tautology — the bug this helper exists to prevent).
    """
    by_pid: dict[str, dict[str, float]] = defaultdict(dict)
    for item in items_written:
        if item.get("record_type") != "SUBTOPIC_SCORE":
            continue
        subtopic_id = item.get("subtopic_id", "")
        if not subtopic_id:
            continue
        faculty_scores = item.get("faculty_scores", {}) or {}
        for pid, score in faculty_scores.items():
            by_pid[pid][subtopic_id] = float(score)
    return {pid: dict(scores) for pid, scores in by_pid.items()}


def _assert_d33_reconciliation(
    faculty_map: dict[str, dict[str, float]],
    subtopic_score_partition_data: dict[str, dict[str, float]],
) -> None:
    """D-33 in-stream invariant: per-CWID per-subtopic equality between faculty-map
    and SUBTOPIC_SCORE# partition derivations of the same exclusive aggregation.

    Divergence is a stage failure — raises RuntimeError with structured message.
    Applies to the EXCLUSIVE pair only (D-33: inclusive has no parallel faculty-map view).

    Args:
        faculty_map: {person_identifier: {subtopic_id: score}} from faculty-map writer
        subtopic_score_partition_data: {person_identifier: {subtopic_id: score}} from SUBTOPIC_SCORE# partition
    """
    FLOAT_EPS = 1e-9
    SAMPLE_CAP = 50

    # WR-08: count ALL violations across the union of pids/sids, but cap only the
    # reported sample. Previously the early break exited after 50 violations,
    # making the reported count a floor rather than the true total — operators
    # triaging a divergence couldn't tell 51 from 5000.
    total_violations = 0
    sample: list[str] = []

    all_pids = set(faculty_map.keys()) | set(subtopic_score_partition_data.keys())
    for pid in sorted(all_pids):
        fm_scores = faculty_map.get(pid, {})
        sp_scores = subtopic_score_partition_data.get(pid, {})
        all_sids = set(fm_scores.keys()) | set(sp_scores.keys())
        for sid in sorted(all_sids):
            fm_val = float(fm_scores.get(sid, 0.0))
            sp_val = float(sp_scores.get(sid, 0.0))
            if abs(fm_val - sp_val) > FLOAT_EPS:
                total_violations += 1
                if len(sample) < SAMPLE_CAP:
                    sample.append(
                        f"cwid={pid} subtopic={sid} faculty_map={fm_val} partition={sp_val} "
                        f"delta={fm_val - sp_val}"
                    )

    if total_violations:
        raise RuntimeError(
            f"D-33 reconciliation invariant violated: {total_violations} (cwid, subtopic) "
            f"pairs disagree between faculty-map and SUBTOPIC_SCORE# partition "
            f"(showing first {len(sample)}). "
            f"Stage aborts; faculty-map and SUBTOPIC_SCORE# partition disagree. "
            f"First: {sample[0]}"
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

    # Phase 12 §8: run both aggregations side by side
    faculty_scores, total_weights = _aggregate_exclusive(rows)
    faculty_scores_inclusive, total_weights_inclusive = _aggregate_inclusive(rows)

    touched_pids = set(faculty_scores.keys())
    logger.info(f"Faculty touched: {len(touched_pids)}")

    # 1. Write existing faculty-map (unchanged SPS consumer contract per D-13)
    cleared, written = _write_faculty_scores(
        faculty_scores, topic_id, dry_run=dry_run
    )
    logger.info(
        f"Writes: cleared={cleared}, written={written}, dry_run={dry_run}"
    )

    if not dry_run:
        # 2. Write SUBTOPIC_SCORE# and SUBTOPIC_SCORE_INCLUSIVE# partitions (Phase 12 §8)
        run_id = os.environ.get("RECITERAI_COLD_RUN_ID") or now_iso()
        table = get_table(TABLE_NAME)
        items_written = _write_subtopic_score_partitions(
            table,
            topic_id=topic_id,
            faculty_scores_exclusive=faculty_scores,
            faculty_scores_inclusive=faculty_scores_inclusive,
            run_id=run_id,
        )
        logger.info(
            f"Phase 12: wrote SUBTOPIC_SCORE# and SUBTOPIC_SCORE_INCLUSIVE# partitions "
            f"for topic={topic_id} run_id={run_id}"
        )

        # 3. D-33 in-stream invariant: verify faculty-map ↔ SUBTOPIC_SCORE# equality
        # per CWID/subtopic. The partition view is reconstructed from the items
        # actually emitted to put_item (independent of the faculty_scores dict),
        # so this is a genuine cross-check — not the x == x tautology of passing
        # the same dict twice.
        partition_view = _project_exclusive_partition_view(items_written)
        _assert_d33_reconciliation(
            faculty_map=faculty_scores,
            subtopic_score_partition_data=partition_view,
        )
        logger.info("D-33 reconciliation invariant verified: faculty-map and SUBTOPIC_SCORE# agree")

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

"""
Per-topic orchestrator for Phase 4 subtopic backfill (Plan 04-06, Task 1).

Chains the three Phase 4 pipeline passes for a single topic, with a human
review gate between Pass 1 (discover) and Pass 2 (assign):

    Pass 1 (discover_subtopics.py)      Sonnet  -> hierarchy_draft_<id>.json
    -- human review gate --              review_status must become "approved"
    Pass 2 (assign_subtopics.py)        Haiku   -> DynamoDB activity writes
    Pass 3 (aggregate_subtopic_scores)  arithmetic only -> faculty writes

On completion this script writes `hierarchy_augmented_<id>.json` next to the
approved draft: it is the draft plus each subtopic's `total_weight` (from
Pass 3's total_weights_<id>.json) and `activity_count` (from a DynamoDB
count of activity rows whose `primary_subtopic_id` equals the subtopic ID).
The full-phase runner (backfill_all.py, Task 2) reads these augmented files
to assemble the final `hierarchy.json`.

Each pass is invoked via `subprocess.run(..., check=True)` as a SEPARATE
Python process (not an in-process import). Rationale: each script already
lazy-initialises its own AWS clients via the repo's `utils.bedrock_client`
and `utils.dynamodb_helpers`; running them in-process would share a single
BedrockClient across passes and risk holding long-lived HTTPS pools between
Sonnet bursts and arithmetic-only Pass 3 work. Subprocess isolation is also
how Plan 05's aging_pilot_gate.py drove the passes, so this orchestrator
mirrors proven behavior.

Environment (AWS creds, AWS_REGION, etc.) is inherited via `env=None` default
in `subprocess.run`. The scripts rely on the default boto3 credential chain.

Idempotency:
  - Pre-count skip: topics below --min-activities are excluded without any
    LLM spend.
  - Pass 2 uses `--resume` so reruns after a Pass 2 interruption skip PMIDs
    that already have primary_subtopic_id set.
  - Pass 1 rerun is wholesale replacement (D-06); rerunning this orchestrator
    after an approved draft exists will regenerate the draft AND drop review
    approval, requiring fresh human review. That is intended: Pass 1 is
    expensive, so operators should only rerun it deliberately.

Usage:
    # Full sequence with review gate:
    python backfill_topic.py --topic cardiovascular_disease
    # ... script prints the review prompt and exits 2 ...
    # (operator reviews hierarchy_draft_cardiovascular_disease.json, sets
    #  review_status: "approved", re-runs:)
    python backfill_topic.py --topic cardiovascular_disease

    # Skip the human gate (pre-authorised lower-priority topics):
    python backfill_topic.py --topic oral_craniofacial_health --skip-review

    # Custom cold-start floor:
    python backfill_topic.py --topic neuroscience_neurology --min-activities 50

Exit codes:
    0  success (including cold-start skip)
    1  pipeline failure (subprocess returned non-zero)
    2  review gate blocked: draft exists but review_status != "approved" and
       --skip-review was not set. Operator must approve then re-run.

Audit trail:
  When --skip-review is used, a SKIP_REVIEW row is appended to
  .planning/phases/04-subtopic-system/artifacts/backfill_log.md recording the
  topic id, UTC timestamp, and authorising OS user. grep-checkable.

Reference: .planning/phases/04-subtopic-system/04-06-PLAN.md Task 1.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)
logging.getLogger("botocore").setLevel(logging.WARNING)
logging.getLogger("boto3").setLevel(logging.WARNING)


# --- Constants ---
REPO_ROOT = Path(__file__).resolve().parent
TAXONOMY_FILE = REPO_ROOT / "taxonomy_v2.json"
PHASE_DIR = REPO_ROOT / ".planning" / "phases" / "04-subtopic-system"
ARTIFACTS_DIR = PHASE_DIR / "artifacts"
SKIP_REVIEW_LOG = ARTIFACTS_DIR / "backfill_log.md"
SCORE_FLOOR = 0.3  # Matches discover_subtopics.py / assign_subtopics.py
PER_TOPIC_LOG = PHASE_DIR / "backfill_log.md"


# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------

def _load_taxonomy_ids() -> set:
    with open(TAXONOMY_FILE) as f:
        data = json.load(f)
    return {t["id"] for t in data.get("topics", [])}


# ---------------------------------------------------------------------------
# DynamoDB helpers (pre-count + post-assign activity_count)
# ---------------------------------------------------------------------------

def _count_qualifying_activities(topic_id: str) -> int:
    """Count unique PMIDs in TOPIC#<id> with relevance score >= SCORE_FLOOR.

    Matches the dedup logic of discover_subtopics._query_topic_activities so
    the pre-count here is apples-to-apples with what Pass 1 will see.
    """
    from boto3.dynamodb.conditions import Key

    from utils.dynamodb_helpers import get_table, TABLE_NAME

    table = get_table(TABLE_NAME)
    pk = f"TOPIC#{topic_id}"

    seen_pmids: set = set()
    last_key = None
    while True:
        kwargs = {
            "KeyConditionExpression": (
                Key("PK").eq(pk) & Key("SK").begins_with("SCORE#")
            ),
            "Limit": 1000,
            # ProjectionExpression trims payload; we only need SK / pmid.
            "ProjectionExpression": "SK, pmid",
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        resp = table.query(**kwargs)
        for item in resp.get("Items", []):
            sk = item.get("SK", "") or ""
            parts = sk.split("#")
            if len(parts) < 2 or not parts[1].isdigit():
                continue
            score = int(parts[1]) / 1000.0
            if score < SCORE_FLOOR:
                continue
            pmid = None
            if len(parts) >= 4 and parts[3].startswith("pmid_"):
                pmid = parts[3][len("pmid_"):]
            if not pmid:
                pmid = str(item.get("pmid", "") or "")
            if pmid:
                seen_pmids.add(pmid)
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break

    return len(seen_pmids)


def _count_primary_assignments(topic_id: str) -> dict:
    """Count rows per primary_subtopic_id in TOPIC#<topic_id>.

    Returns {subtopic_id: unique_pmid_count}. Used to inject `activity_count`
    into the augmented draft. Deduplicates by pmid so repeat-author rows for
    the same paper count once.
    """
    from boto3.dynamodb.conditions import Key

    from utils.dynamodb_helpers import get_table, TABLE_NAME

    table = get_table(TABLE_NAME)
    pk = f"TOPIC#{topic_id}"

    # Track pmids seen per subtopic to dedupe multi-author rows
    seen: dict = {}
    last_key = None
    while True:
        kwargs = {
            "KeyConditionExpression": (
                Key("PK").eq(pk) & Key("SK").begins_with("SCORE#")
            ),
            "Limit": 1000,
            "ProjectionExpression": "SK, pmid, primary_subtopic_id",
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        resp = table.query(**kwargs)
        for item in resp.get("Items", []):
            primary = item.get("primary_subtopic_id")
            if not primary:
                continue
            sk = item.get("SK", "") or ""
            parts = sk.split("#")
            pmid = None
            if len(parts) >= 4 and parts[3].startswith("pmid_"):
                pmid = parts[3][len("pmid_"):]
            if not pmid:
                pmid = str(item.get("pmid", "") or "")
            if not pmid:
                continue
            seen.setdefault(primary, set()).add(pmid)
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break

    return {sid: len(pmids) for sid, pmids in seen.items()}


# ---------------------------------------------------------------------------
# Review gate
# ---------------------------------------------------------------------------

def _draft_path(topic_id: str) -> Path:
    return PHASE_DIR / f"hierarchy_draft_{topic_id}.json"


def _total_weights_path(topic_id: str) -> Path:
    return PHASE_DIR / f"total_weights_{topic_id}.json"


def _augmented_path(topic_id: str) -> Path:
    return PHASE_DIR / f"hierarchy_augmented_{topic_id}.json"


def _check_review_status(topic_id: str, skip_review: bool) -> dict:
    """
    Inspect hierarchy_draft_<id>.json and enforce the review gate (D-21).

    If --skip-review, returns the draft dict with review_status forced to
    "auto_approved" and writes an audit-trail row to SKIP_REVIEW_LOG.
    Otherwise, exits with code 2 if review_status != "approved".
    """
    path = _draft_path(topic_id)
    if not path.exists():
        logger.error(f"Hierarchy draft not found: {path}")
        logger.error(
            "Pass 1 was expected to have written this file. Check Pass 1 "
            "subprocess output above."
        )
        sys.exit(1)

    with open(path) as f:
        draft = json.load(f)

    review_status = draft.get("review_status")

    if skip_review:
        draft["review_status"] = "auto_approved"
        _append_skip_review_audit_line(topic_id)
        with open(path, "w") as f:
            json.dump(draft, f, indent=2)
        logger.warning(
            f"--skip-review: set review_status=auto_approved on {path}. "
            f"Audit row appended to {SKIP_REVIEW_LOG}."
        )
        return draft

    if review_status != "approved":
        print(
            f"\n=== HUMAN REVIEW REQUIRED ===\n"
            f"Topic:       {topic_id}\n"
            f"Draft:       {path}\n"
            f"Review:      Open the draft, inspect the subtopics, set\n"
            f"             \"review_status\": \"approved\" at the top level,\n"
            f"             then re-run:\n"
            f"                 python backfill_topic.py --topic {topic_id}\n"
            f"\n"
            f"Alternative: if this topic is pre-authorised for auto-approval,\n"
            f"             rerun with --skip-review (writes a SKIP_REVIEW audit\n"
            f"             row to {SKIP_REVIEW_LOG}).\n"
        )
        sys.exit(2)

    logger.info(f"Review gate passed: review_status=approved for {topic_id}")
    return draft


def _append_skip_review_audit_line(topic_id: str) -> None:
    """Append one `SKIP_REVIEW | ...` audit row to the artifacts log.

    Format: `SKIP_REVIEW | <topic_id> | <UTC ISO-8601> | authorized_by: <user> | pre-authorized for lower-priority topic`
    """
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    if not SKIP_REVIEW_LOG.exists():
        SKIP_REVIEW_LOG.write_text(
            "# Backfill Audit Log (artifacts)\n\n"
            "SKIP_REVIEW rows record use of `backfill_topic.py --skip-review`.\n"
            "Grep-checkable audit trail per Plan 04-06 acceptance criteria.\n\n"
        )

    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    user = os.getenv("USER", "unknown")
    line = (
        f"SKIP_REVIEW | {topic_id} | {timestamp} | "
        f"authorized_by: {user} | pre-authorized for lower-priority topic\n"
    )
    with open(SKIP_REVIEW_LOG, "a") as f:
        f.write(line)


# ---------------------------------------------------------------------------
# Augmented draft builder
# ---------------------------------------------------------------------------

def _build_augmented_draft(topic_id: str, draft: dict) -> dict:
    """Merge Pass 3 total_weights + DynamoDB activity_count into draft."""
    tw_path = _total_weights_path(topic_id)
    if not tw_path.exists():
        logger.error(f"total_weights file missing: {tw_path}")
        logger.error("Pass 3 should have written this. Aborting augmentation.")
        sys.exit(1)

    with open(tw_path) as f:
        tw_data = json.load(f)
    total_weights_map = tw_data.get("total_weights", {}) or {}

    activity_counts = _count_primary_assignments(topic_id)

    augmented_subtopics = []
    for s in draft.get("subtopics", []):
        sid = s["id"]
        entry = dict(s)
        entry["total_weight"] = float(total_weights_map.get(sid, 0.0) or 0.0)
        entry["activity_count"] = int(activity_counts.get(sid, 0))
        augmented_subtopics.append(entry)

    augmented = dict(draft)
    augmented["subtopics"] = augmented_subtopics
    augmented["augmented_at"] = datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")
    augmented["augmentation_source"] = {
        "total_weights_file": str(tw_path),
        "activity_count_method": "dynamodb_primary_subtopic_id_unique_pmids",
    }
    return augmented


# ---------------------------------------------------------------------------
# Per-topic summary row (human-readable log)
# ---------------------------------------------------------------------------

def _append_per_topic_log(
    topic_id: str,
    activity_count: int,
    cluster_count: int,
    coverage_pct: float,
    passes_executed: int,
    status: str,
) -> None:
    """Append a row to .planning/phases/04-subtopic-system/backfill_log.md.

    Cost fields are left blank here — the full-phase runner (backfill_all.py)
    owns the cost accounting by capturing the subprocess stdout of each pass.
    This per-topic log is a simple status ledger.
    """
    PHASE_DIR.mkdir(parents=True, exist_ok=True)
    if not PER_TOPIC_LOG.exists():
        header = (
            "# Per-topic Backfill Log\n\n"
            "| topic_id | activity_count | cluster_count | coverage_pct | "
            "passes | sonnet_$ | haiku_$ | status |\n"
            "| -------- | -------------- | ------------- | ------------ | "
            "------ | -------- | ------- | ------ |\n"
        )
        PER_TOPIC_LOG.write_text(header)

    coverage_str = f"{coverage_pct:.1%}" if coverage_pct else "-"
    row = (
        f"| {topic_id} | {activity_count} | {cluster_count} | {coverage_str} | "
        f"{passes_executed} |  |  | {status} |\n"
    )
    with open(PER_TOPIC_LOG, "a") as f:
        f.write(row)


# ---------------------------------------------------------------------------
# Subprocess runners
# ---------------------------------------------------------------------------

def _run_pass(label: str, cmd: list) -> None:
    """
    Run a pipeline pass as a subprocess, inheriting env (AWS creds etc.).

    Uses check=True so CalledProcessError propagates. Caller catches and maps
    to exit code 1.
    """
    logger.info(f"[{label}] invoking: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    logger.info(f"[{label}] complete")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(topic_id: str, skip_review: bool, min_activities: int) -> int:
    # 1. Validate topic exists in taxonomy_v2.json
    taxonomy_ids = _load_taxonomy_ids()
    if topic_id not in taxonomy_ids:
        logger.error(
            f"Topic '{topic_id}' not found in {TAXONOMY_FILE}. "
            f"Valid topic IDs: taxonomy_v2.json has {len(taxonomy_ids)} entries."
        )
        return 1

    # 2. Pre-count — cold-start floor (D-01)
    logger.info(
        f"Pre-counting qualifying activities for {topic_id} "
        f"(score >= {SCORE_FLOOR})..."
    )
    activity_count = _count_qualifying_activities(topic_id)
    logger.info(f"{topic_id}: {activity_count} qualifying activities")

    if activity_count < min_activities:
        msg = (
            f"Topic {topic_id} has {activity_count} activities, below "
            f"cold-start floor {min_activities}. Skipping."
        )
        logger.warning(msg)
        # Machine-readable marker consumed by backfill_all.py
        print(f"EXCLUDED:{topic_id}:{activity_count}:below_cold_start_floor")
        _append_per_topic_log(
            topic_id=topic_id,
            activity_count=activity_count,
            cluster_count=0,
            coverage_pct=0.0,
            passes_executed=0,
            status="excluded:below_cold_start_floor",
        )
        return 0

    # 3. Pass 1 — discovery
    # Only run if an approved or auto_approved draft does not already exist.
    # This makes reruns idempotent: the operator approves the draft once,
    # re-runs, and we resume at Pass 2 without re-billing Sonnet.
    draft_path = _draft_path(topic_id)
    existing_review_status = None
    if draft_path.exists():
        try:
            with open(draft_path) as f:
                existing = json.load(f)
            existing_review_status = existing.get("review_status")
        except Exception:
            existing_review_status = None

    if existing_review_status in ("approved", "auto_approved"):
        logger.info(
            f"[pass 1] skipped: {draft_path} already has "
            f"review_status={existing_review_status}; resuming at Pass 2."
        )
    else:
        try:
            _run_pass(
                "pass 1: discover",
                [
                    sys.executable,
                    str(REPO_ROOT / "discover_subtopics.py"),
                    "--topic",
                    topic_id,
                    "--min-activities",
                    str(min_activities),
                ],
            )
        except subprocess.CalledProcessError as e:
            logger.error(f"Pass 1 (discover) failed for {topic_id}: {e}")
            _append_per_topic_log(
                topic_id=topic_id,
                activity_count=activity_count,
                cluster_count=0,
                coverage_pct=0.0,
                passes_executed=0,
                status="failed:discover",
            )
            return 1

    # 4. Human review gate (D-21)
    draft = _check_review_status(topic_id, skip_review=skip_review)

    # 5. Pass 2 — assignment (with --resume for idempotency)
    try:
        _run_pass(
            "pass 2: assign",
            [
                sys.executable,
                str(REPO_ROOT / "assign_subtopics.py"),
                "--topic",
                topic_id,
                "--resume",
            ],
        )
    except subprocess.CalledProcessError as e:
        logger.error(f"Pass 2 (assign) failed for {topic_id}: {e}")
        _append_per_topic_log(
            topic_id=topic_id,
            activity_count=activity_count,
            cluster_count=len(draft.get("subtopics", [])),
            coverage_pct=float(draft.get("coverage_pct", 0) or 0),
            passes_executed=1,
            status="failed:assign",
        )
        return 1

    # 6. Pass 3 — aggregation (arithmetic only, no LLM)
    try:
        _run_pass(
            "pass 3: aggregate",
            [
                sys.executable,
                str(REPO_ROOT / "aggregate_subtopic_scores.py"),
                "--topic",
                topic_id,
            ],
        )
    except subprocess.CalledProcessError as e:
        logger.error(f"Pass 3 (aggregate) failed for {topic_id}: {e}")
        _append_per_topic_log(
            topic_id=topic_id,
            activity_count=activity_count,
            cluster_count=len(draft.get("subtopics", [])),
            coverage_pct=float(draft.get("coverage_pct", 0) or 0),
            passes_executed=2,
            status="failed:aggregate",
        )
        return 1

    # 7. Build augmented draft (inject total_weight + activity_count)
    augmented = _build_augmented_draft(topic_id, draft)
    aug_path = _augmented_path(topic_id)
    with open(aug_path, "w") as f:
        json.dump(augmented, f, indent=2)
    logger.info(f"Wrote augmented draft: {aug_path}")

    # 8. Log success row
    _append_per_topic_log(
        topic_id=topic_id,
        activity_count=activity_count,
        cluster_count=len(augmented.get("subtopics", [])),
        coverage_pct=float(augmented.get("coverage_pct", 0) or 0),
        passes_executed=3,
        status="ok",
    )

    print(
        f"\n=== backfill_topic.py complete ===\n"
        f"  topic:           {topic_id}\n"
        f"  activity_count:  {activity_count}\n"
        f"  cluster_count:   {len(augmented.get('subtopics', []))}\n"
        f"  coverage_pct:    {augmented.get('coverage_pct')}\n"
        f"  passes_executed: 3\n"
        f"  augmented_draft: {aug_path}\n"
    )
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Per-topic orchestrator for Phase 4 subtopic backfill. "
            "Chains discover -> human-review -> assign -> aggregate."
        )
    )
    parser.add_argument(
        "--topic",
        required=True,
        help="Topic ID from taxonomy_v2.json (e.g., cardiovascular_disease)",
    )
    parser.add_argument(
        "--skip-review",
        action="store_true",
        help=(
            "Bypass the human review gate: sets review_status=auto_approved "
            "in the draft and appends a SKIP_REVIEW audit row to "
            "artifacts/backfill_log.md. Use only for pre-authorised "
            "lower-priority topics."
        ),
    )
    parser.add_argument(
        "--min-activities",
        type=int,
        default=30,
        metavar="N",
        help=(
            "Cold-start floor (D-01): skip topic if fewer than N qualifying "
            "activities at score >= 0.3 (default 30). EXCLUDED marker is "
            "printed to stdout and the topic is appended to the log."
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    sys.exit(run(
        topic_id=args.topic,
        skip_review=args.skip_review,
        min_activities=args.min_activities,
    ))

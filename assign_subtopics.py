"""
Pass 2 (Assignment): Per-activity Haiku subtopic classifier.

Reads the approved hierarchy draft produced by Pass 1 (Plan 04-02) for a given
topic_id, queries DynamoDB TOPIC#<topic_id> for activities scoring >= 0.3, and
invokes Haiku once per unique PMID to decide which subtopics that activity
belongs to. Results are filtered to a confidence floor (default 0.3) and
written back to every matching activity record via
`utils.dynamodb_subtopic_migration.update_activity_subtopics`.

Key design points:
  D-02  Per-activity Haiku call; primary = argmax on confidence (post-processed,
        not in prompt). Multi-assignment allowed; below-floor assignments are
        dropped and the activity stays unassigned (Tier 3 fallback).
  D-06  Pass 1 hierarchy is replaced wholesale on recompute, so we trust its
        subtopic_ids blindly (this run) — no migration logic.
  D-16  Writes go to activity records: `subtopic_ids[]`, `primary_subtopic_id`,
        `subtopic_confidences{}`.
  Review item #2: primary tiebreaker (higher total_weight -> alphabetical id)
  lives in _resolve_primary_on_tie (Python), not in the Haiku prompt.

Idempotent: rerun with `--resume` skips activities that already have
`primary_subtopic_id` set. Unassigned activities (empty assignments list) are
recorded via a sentinel PROCESSING# marker so reruns do not re-invoke Haiku on
them; without this, every rerun would re-process the topic-level fallback set.

Concurrency: ThreadPoolExecutor with 10-20 workers (Phase 1 precedent, see
score_publications.score_batch_async). The underlying BedrockClient already
does exponential backoff on ThrottlingException / ModelTimeoutException.

Usage:
    python assign_subtopics.py --topic aging_geroscience
    python assign_subtopics.py --topic aging_geroscience --resume
    python assign_subtopics.py --topic aging_geroscience --concurrency 10
    python assign_subtopics.py --topic aging_geroscience --confidence-floor 0.35
    python assign_subtopics.py --topic aging_geroscience --limit 10 --dry-run

Exit codes:
    0  success
    1  unexpected error
    2  hierarchy draft missing or review_status != "approved"
    3  topic_id not found in taxonomy_v2.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from threading import Lock

# Ensure repo root is importable regardless of cwd
sys.path.insert(0, str(Path(__file__).parent))

from utils.bedrock_client import BedrockClient, HAIKU_MODEL, MODEL_IDS_BY_STAGE
from utils.dynamodb_helpers import get_table, TABLE_NAME
from utils.dynamodb_subtopic_migration import update_activity_subtopics
from utils.stage_records import (
    build_complete_record,
    build_skipped_record,
    compute_input_hash,
    should_skip,
    write_complete,
    write_failed,
    write_skipped,
)
from utils.event_records import load_thresholds, write_low_confidence_assignment
from prompts.subtopic_assignment import (
    ASSIGNMENT_SYSTEM_PROMPT,
    BUILD_ASSIGNMENT_USER_MESSAGE,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)
# Silence boto3 INFO chatter during parallel queries
logging.getLogger("botocore").setLevel(logging.WARNING)
logging.getLogger("boto3").setLevel(logging.WARNING)


# --- Constants ---
TAXONOMY_FILE = Path(__file__).parent / "taxonomy_v2.json"
# Phase 12 G-18: tunables lifted to config/thresholds.json. CLI --confidence-floor still overrides per D-23.
# WR-02: lazy-load to avoid an import-time crash if config/thresholds.json is
# absent or malformed. The constants SCORE_FLOOR, DEFAULT_CONFIDENCE_FLOOR, and
# TIE_EPSILON are exposed via module __getattr__ (PEP 562) and resolved on first
# read. This lets unrelated tests import this module without a thresholds.json
# present, and turns any schema regression into a clear runtime error rather
# than an opaque import failure.
from utils.env_check import load_thresholds as _load_thresholds_cfg

_CFG: dict | None = None
_CACHED_THRESHOLDS: dict[str, float] = {}


_LAZY_THRESHOLD_ATTRS = {
    "SCORE_FLOOR": "score_floor",                     # Minimum relevance score to qualify as an "activity"
    "DEFAULT_CONFIDENCE_FLOOR": "confidence_floor",   # Below this, assignments are dropped (D-02)
    "TIE_EPSILON": "tie_epsilon",                     # Confidences within this are considered tied
}


def _get_threshold(key: str) -> float:
    """Lazy-resolve a single threshold value, caching after first read.

    Pure-functional: does NOT mutate module globals() — that broke the
    test_env_check_thresholds.py reload+patch flow, because importlib.reload
    does not clear dynamically-set globals, so once a value was primed
    every subsequent reload kept seeing the stale cached attribute.

    In-module code MUST call `_get_threshold("score_floor")` etc. directly
    rather than reading bare-name constants. The module-level __getattr__
    handles external `assign_subtopics.SCORE_FLOOR` access; bare names
    inside this module bypass __getattr__ entirely (PEP 562).
    """
    if key in _CACHED_THRESHOLDS:
        return _CACHED_THRESHOLDS[key]
    global _CFG
    if _CFG is None:
        _CFG = _load_thresholds_cfg()
    val = float(_CFG[key])
    _CACHED_THRESHOLDS[key] = val
    return val


def __getattr__(name: str):  # PEP 562 — module-level __getattr__
    """Defer threshold lookup until the attribute is actually read (WR-02)."""
    if name in _LAZY_THRESHOLD_ATTRS:
        return _get_threshold(_LAZY_THRESHOLD_ATTRS[name])
    raise AttributeError(f"module 'assign_subtopics' has no attribute {name!r}")


DEFAULT_CONCURRENCY = 15           # Phase 1 precedent (score_publications)
DEFAULT_DRAFT_DIR = Path(".planning/phases/04-subtopic-system")

# Phase 10 STAGE# substrate (D-07). Run-level memoization is keyed on
# topic_id because this script runs per-topic. Per-PMID failure rows use
# scope = "pmid:{pmid}" so failures do not pollute the run-level cache.
STAGE_NAME = "assign_subtopics"
STAGE_MODEL_IDS = [MODEL_IDS_BY_STAGE["subtopic_assignment"]]

# Stubbed cost — Phase 10 follow-up wires a Bedrock usage counter. Skip
# rows continue to use the substrate's SKIP_COST_OBSERVED_USD.
ASSIGN_COST_USD = Decimal("0")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _topic_scope(topic_id: str) -> str:
    return f"topic:{topic_id}"


def _pmid_scope(pmid: str) -> str:
    return f"pmid:{pmid}"


def _hierarchy_canonical_sha256(hierarchy_draft: dict) -> str:
    """Canonical sha256 over the hierarchy draft, excluding non-deterministic fields.

    Substitute for `hierarchy_version` until Phase 11 stamps it first-class on
    every record (spec §3, Decision 2). Excludes any `generated_at` /
    `review_status` keys that re-stamp on rerun without changing content.
    """
    ignore = {"generated_at", "review_status"}
    h_for_hash = {k: v for k, v in hierarchy_draft.items() if k not in ignore}
    canonical = json.dumps(
        h_for_hash, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def compute_assign_input_hash(
    *,
    hierarchy_draft: dict,
    pmids: list,
) -> str:
    """Content-addressed input hash for an assign_subtopics run.

    Phase 11 will replace `hierarchy_draft_sha256` with the stamped
    `hierarchy_version`; the substitution preserves the substrate
    contract because both collapse the hierarchy state to a stable key.
    """
    pmid_set_hash = hashlib.sha256(
        ",".join(sorted({str(p) for p in pmids})).encode("utf-8")
    ).hexdigest()
    return compute_input_hash(
        STAGE_NAME,
        {
            "hierarchy_draft_sha256": _hierarchy_canonical_sha256(hierarchy_draft),
            "pmid_set_sha256": pmid_set_hash,
            "model_ids": STAGE_MODEL_IDS,
        },
    )


def _per_pmid_assign_input_hash(hierarchy_draft: dict, pmid: str) -> str:
    return compute_input_hash(
        STAGE_NAME,
        {
            "hierarchy_draft_sha256": _hierarchy_canonical_sha256(hierarchy_draft),
            "pmid": str(pmid),
            "model_ids": STAGE_MODEL_IDS,
        },
    )


def _maybe_write_low_confidence_event(
    *,
    stage_table,
    thresholds: dict | None,
    pmid: str,
    topic_id: str,
    candidate_confidences: dict[str, float],
) -> None:
    """Write a LOW_CONFIDENCE_ASSIGNMENT# row when all valid candidate
    confidences are below `low_confidence_floor` (spec §9).

    No-op when `stage_table` or `thresholds` is None. Failures are
    logged and swallowed — feedback writes must not break assignment.
    """
    if stage_table is None or thresholds is None:
        return
    floor = thresholds.get("low_confidence_floor")
    if floor is None:
        return
    if not candidate_confidences:
        # No valid candidates at all is a model/prompt issue, not a
        # low-confidence signal; let the existing logging/STAGE# path handle.
        return
    max_conf = max(candidate_confidences.values())
    if max_conf >= float(floor):
        return
    try:
        write_low_confidence_assignment(
            stage_table,
            pmid=pmid,
            topic_id=topic_id,
            candidate_confidences=candidate_confidences,
        )
    except Exception as exc:
        logger.warning(
            f"Failed to write LOW_CONFIDENCE_ASSIGNMENT# row for pmid={pmid}: {exc}"
        )


def _parse_pmid_list_arg(value: str | None) -> list[str] | None:
    """Parse --delta-pmids: 'pmid1,pmid2,...' or '@/path/to/file' (one PMID per line).

    Returns None when no value is provided (the run processes the full topic set).
    """
    if value is None:
        return None
    if value.startswith("@"):
        path = Path(value[1:])
        if not path.exists():
            raise SystemExit(f"--delta-pmids file not found: {path}")
        return [
            line.strip()
            for line in path.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]
    return [s.strip() for s in value.split(",") if s.strip()]


# ---------------------------------------------------------------------------
# Tiebreaker — review item #2 (CONTEXT.md deferred item)
# ---------------------------------------------------------------------------

def _resolve_primary_on_tie(
    confidences: dict,
    subtopic_defs: list,
    hierarchy_draft: dict,
) -> str:
    """
    Resolve primary subtopic when the top-2 confidences are within TIE_EPSILON.

    Review item #2 default (see CONTEXT.md deferred items and RESEARCH.md
    "Tiebreaker default"):
      1. Higher total_weight subtopic wins (signal density beats intuition).
         total_weight is not yet known at Pass 2 time — a preliminary Pass 3
         aggregation has not run — so Pass 1's `seed_pmid_count` is used as a
         weight proxy (more seed PMIDs = denser cluster).
      2. If still tied, alphabetically lower subtopic_id wins.

    Args:
        confidences: {subtopic_id: confidence_float} for this activity.
        subtopic_defs: List of {id, label, description, ...} — unused directly;
                       kept in the signature because the prompt-space design
                       made it available.
        hierarchy_draft: The full approved hierarchy dict loaded from
                         hierarchy_draft_<topic>.json; used to read seed_pmid
                         counts as a total_weight proxy.

    Returns:
        The subtopic_id chosen as primary.
    """
    if not confidences:
        raise ValueError("_resolve_primary_on_tie called with empty confidences")

    # Rank by confidence desc
    sorted_items = sorted(
        confidences.items(), key=lambda kv: (-kv[1], kv[0])
    )

    top_id, top_conf = sorted_items[0]
    if len(sorted_items) == 1:
        return top_id

    second_id, second_conf = sorted_items[1]
    tie_epsilon = _get_threshold("tie_epsilon")
    if abs(top_conf - second_conf) > tie_epsilon:
        # Clear winner
        return top_id

    # Collect all contenders within tie_epsilon of the top
    contenders = [
        sid for sid, conf in sorted_items
        if abs(conf - top_conf) <= tie_epsilon
    ]

    # Build a weight proxy map from Pass 1 seed_pmid counts (D-02 note:
    # total_weight not yet defined at Pass 2 time; use seed density instead)
    seed_counts = {
        s["id"]: len(s.get("seed_pmids", []) or [])
        for s in hierarchy_draft.get("subtopics", [])
    }

    # Pick by highest seed count, then alphabetical ascending
    contenders.sort(key=lambda sid: (-seed_counts.get(sid, 0), sid))
    return contenders[0]


# ---------------------------------------------------------------------------
# Taxonomy + hierarchy loaders
# ---------------------------------------------------------------------------

def _load_taxonomy() -> dict:
    with open(TAXONOMY_FILE) as f:
        data = json.load(f)
    topics = data.get("topics", [])
    return {t["id"]: t for t in topics}


def _load_hierarchy_draft(path: Path) -> dict:
    """Load and validate the approved hierarchy draft.

    Exits with code 2 if the file does not exist or review_status != 'approved'
    (review_status.*approved gate is a plan acceptance criterion).
    """
    if not path.exists():
        logger.error(f"Hierarchy draft not found: {path}")
        sys.exit(2)

    with open(path) as f:
        data = json.load(f)

    review_status = data.get("review_status")
    if review_status not in ("approved", "auto_approved"):
        logger.error(
            f"Hierarchy draft {path} has review_status={review_status!r}; "
            f"expected 'approved' or 'auto_approved'. Pass 2 is gated on review."
        )
        sys.exit(2)
    if review_status == "auto_approved":
        logger.warning(
            f"Hierarchy draft {path} is auto_approved (bypassed human review). "
            f"SKIP_REVIEW audit row should exist in backfill_log.md."
        )

    return data


# ---------------------------------------------------------------------------
# DynamoDB query — all activity rows in TOPIC# partition at score >= floor
# ---------------------------------------------------------------------------

def _parse_score_from_sk(sk: str) -> float:
    parts = sk.split("#")
    if len(parts) < 2 or not parts[1].isdigit():
        return 0.0
    return int(parts[1]) / 1000.0


def _query_topic_activity_rows(topic_id: str) -> list:
    """
    Query every activity row in TOPIC#<topic_id> at score >= SCORE_FLOOR.

    NOTE: multiple rows share the same PMID (one row per author — see
    SK format `SCORE#NNNN#ACTIVITY#pmid_{pmid}#cwid_{cwid}`). All rows
    for the same PMID share the same subtopic assignment (the classifier
    reads only pmid/title/synopsis, which are author-invariant), so Pass 2
    calls Haiku once per unique PMID and writes the result to every row
    that carries that PMID.

    Returns a list of raw row dicts with keys: PK, SK, pmid, title,
    synopsis, impact_score, score, primary_subtopic_id (if already set).
    """
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

    logger.info(f"Fetched {len(rows)} raw SCORE# rows for {topic_id}")

    score_floor = _get_threshold("score_floor")
    qualified = []
    for row in rows:
        sk = row.get("SK", "")
        score = _parse_score_from_sk(sk)
        if score < score_floor:
            continue
        qualified.append(row)

    logger.info(
        f"After score floor ({score_floor}): {len(qualified)} qualified rows"
    )
    return qualified


def _dedupe_by_pmid(rows: list) -> dict:
    """Group qualified rows by PMID.

    Returns {pmid_str: {"activity": {pmid,title,synopsis}, "rows": [row, ...]}}.
    Activity payload is taken from the first row encountered (author-invariant).
    """
    grouped: dict = {}
    for row in rows:
        pmid = str(row.get("pmid", "") or "")
        if not pmid:
            # Fallback: pull from SK
            sk = row.get("SK", "")
            sk_parts = sk.split("#")
            if len(sk_parts) >= 4 and sk_parts[3].startswith("pmid_"):
                pmid = sk_parts[3][len("pmid_"):]
        if not pmid:
            continue

        if pmid not in grouped:
            grouped[pmid] = {
                "activity": {
                    "pmid": int(pmid) if pmid.isdigit() else pmid,
                    "title": str(row.get("title", "") or ""),
                    "synopsis": str(row.get("synopsis", "") or ""),
                },
                "rows": [],
                "has_primary": False,
            }
        grouped[pmid]["rows"].append(row)
        if row.get("primary_subtopic_id"):
            grouped[pmid]["has_primary"] = True
    return grouped


# ---------------------------------------------------------------------------
# Haiku worker
# ---------------------------------------------------------------------------

def _strip_json_fences(text: str) -> str:
    import re
    stripped = re.sub(r"```(?:json)?\s*", "", text)
    stripped = stripped.replace("```", "")
    return stripped.strip()


def _classify_activity(
    client: BedrockClient,
    activity: dict,
    topic_meta: dict,
    subtopic_defs: list,
) -> tuple[list, dict]:
    """
    Call Haiku once for a single activity.

    Returns (raw_assignments_list, usage_dict).
    Raises on Bedrock or JSON parse failure; caller handles.
    """
    user_msg = BUILD_ASSIGNMENT_USER_MESSAGE(
        activity=activity,
        topic_meta=topic_meta,
        subtopic_defs=subtopic_defs,
    )

    converse_msgs, system_list = client._translate_messages(
        [{"role": "user", "content": user_msg}],
        ASSIGNMENT_SYSTEM_PROMPT,
    )
    raw = client._call_with_retry(
        model=HAIKU_MODEL,
        messages_converse=converse_msgs,
        system_list=system_list,
        max_tokens=400,
        temperature=0.0,
    )
    text = raw["output"]["message"]["content"][0]["text"]
    usage = raw.get("usage", {}) or {}

    cleaned = _strip_json_fences(text)
    parsed = json.loads(cleaned)
    return parsed.get("assignments", []) or [], usage


# ---------------------------------------------------------------------------
# Per-PMID orchestration
# ---------------------------------------------------------------------------

def _filter_valid_assignments(
    raw_assignments: list,
    valid_ids: set,
    confidence_floor: float,
) -> dict:
    """Keep only {subtopic_id: confidence} pairs that (a) appear in valid_ids
    and (b) meet the confidence floor. Deduplicates on subtopic_id keeping the
    highest confidence seen.
    """
    out: dict = {}
    for item in raw_assignments:
        if not isinstance(item, dict):
            continue
        sid = item.get("subtopic_id")
        conf = item.get("confidence")
        if sid is None or conf is None:
            continue
        try:
            conf_f = float(conf)
        except (TypeError, ValueError):
            continue
        if sid not in valid_ids:
            continue
        if conf_f < confidence_floor:
            continue
        if sid in out:
            if conf_f > out[sid]:
                out[sid] = conf_f
        else:
            out[sid] = conf_f
    return out


def _process_pmid(
    pmid: str,
    group: dict,
    client: BedrockClient,
    topic_meta: dict,
    subtopic_defs: list,
    valid_subtopic_ids: set,
    hierarchy_draft: dict,
    confidence_floor: float,
    dry_run: bool,
    stage_table=None,
    thresholds: dict | None = None,
    hierarchy_version: str = "",
) -> dict:
    """
    Worker: classify one PMID and (unless dry-run) write to every matching row.

    Returns a stats dict used by the main loop for progress reporting.
    """
    stats = {
        "pmid": pmid,
        "status": "ok",
        "error": None,
        "rows_written": 0,
        "assigned": False,
        "primary": None,
        "filtered_count": 0,
        "input_tokens": 0,
        "output_tokens": 0,
    }

    t_pmid_start = time.monotonic()
    pmid_started_at = _now_iso()
    try:
        raw_assignments, usage = _classify_activity(
            client=client,
            activity=group["activity"],
            topic_meta=topic_meta,
            subtopic_defs=subtopic_defs,
        )
    except Exception as exc:
        logger.warning(f"Haiku call failed for pmid={pmid}: {exc}")
        stats["status"] = "failed"
        stats["error"] = str(exc)
        if stage_table is not None:
            try:
                write_failed(
                    stage_table,
                    stage=STAGE_NAME,
                    scope=_pmid_scope(pmid),
                    input_hash=_per_pmid_assign_input_hash(hierarchy_draft, pmid),
                    error_code=type(exc).__name__,
                    error_message=str(exc)[:1000],
                    started_at=pmid_started_at,
                    completed_at=_now_iso(),
                    duration_ms=int((time.monotonic() - t_pmid_start) * 1000),
                    cost_observed_usd=ASSIGN_COST_USD,
                    model_ids_snapshot=STAGE_MODEL_IDS,
                )
            except Exception as stage_err:
                logger.warning(
                    f"Failed to write STAGE# failed row for pmid={pmid}: {stage_err}"
                )
        return stats

    stats["input_tokens"] = usage.get("inputTokens", 0) or 0
    stats["output_tokens"] = usage.get("outputTokens", 0) or 0

    confidences = _filter_valid_assignments(
        raw_assignments, valid_subtopic_ids, confidence_floor
    )
    stats["filtered_count"] = len(confidences)

    # T6 — LOW_CONFIDENCE_ASSIGNMENT# feedback event. Evaluated against the
    # full candidate set (no confidence_floor) so a subtopic at 0.31 doesn't
    # silently disqualify the PMID from drift detection.
    raw_valid = _filter_valid_assignments(
        raw_assignments, valid_subtopic_ids, 0.0
    )
    _maybe_write_low_confidence_event(
        stage_table=stage_table,
        thresholds=thresholds,
        pmid=pmid,
        topic_id=topic_meta.get("id", ""),
        candidate_confidences=raw_valid,
    )

    if not confidences:
        # Unassigned activity — leave the row alone; Tier 3 fallback applies.
        # We intentionally do NOT write a sentinel; --resume uses presence of
        # `primary_subtopic_id` as the skip signal, so unassigned PMIDs are
        # re-classified on rerun. In practice Haiku is deterministic at
        # temperature 0, so this is fine and avoids an extra schema field.
        return stats

    # Primary selection with tiebreaker
    sorted_pairs = sorted(confidences.items(), key=lambda kv: (-kv[1], kv[0]))
    top_id, top_conf = sorted_pairs[0]
    if (
        len(sorted_pairs) >= 2
        and abs(top_conf - sorted_pairs[1][1]) <= _get_threshold("tie_epsilon")
    ):
        primary = _resolve_primary_on_tie(
            confidences, subtopic_defs, hierarchy_draft
        )
    else:
        primary = top_id

    # Rank full subtopic_ids list by confidence desc, then id asc
    subtopic_ids = [sid for sid, _c in sorted_pairs]

    stats["assigned"] = True
    stats["primary"] = primary

    if dry_run:
        return stats

    # Write to every row sharing this PMID
    for row in group["rows"]:
        try:
            update_activity_subtopics(
                pk=row["PK"],
                sk=row["SK"],
                subtopic_ids=subtopic_ids,
                primary_subtopic_id=primary,
                confidences=confidences,
                hierarchy_version=hierarchy_version,
            )
            stats["rows_written"] += 1
        except Exception as exc:
            logger.warning(
                f"DynamoDB update failed pmid={pmid} sk={row.get('SK')}: {exc}"
            )
            stats["status"] = "partial"
            stats["error"] = str(exc)
    return stats


# ---------------------------------------------------------------------------
# Cost model (based on RESEARCH.md §Pass 2 batching strategy)
# ---------------------------------------------------------------------------

# Bedrock Haiku 4.5 pricing (per RESEARCH.md assumption; verify against
# https://aws.amazon.com/bedrock/pricing/ if reconciling invoices):
#   input:  $1 per million tokens
#   output: $5 per million tokens
_HAIKU_INPUT_PRICE_PER_M = 1.0
_HAIKU_OUTPUT_PRICE_PER_M = 5.0


def _estimate_cost(input_tokens: int, output_tokens: int) -> float:
    return (
        input_tokens / 1_000_000 * _HAIKU_INPUT_PRICE_PER_M
        + output_tokens / 1_000_000 * _HAIKU_OUTPUT_PRICE_PER_M
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(
    topic_id: str,
    draft_path: Path,
    concurrency: int,
    confidence_floor: float,
    limit: int | None,
    resume: bool,
    dry_run: bool,
    delta_pmids: list[str] | None = None,
    emit_envelope: bool = False,
) -> dict:
    # Phase 11 D-01: resolve hierarchy_version from env before any work.
    # pipeline_cold.run.main() sets this env var for all subprocess stages.
    # Raise early with an actionable message if absent (prevents silent
    # writes without version stamps).
    hierarchy_version = os.environ.get("RECITERAI_HIERARCHY_VERSION")
    if not hierarchy_version:
        raise RuntimeError(
            "RECITERAI_HIERARCHY_VERSION is not set. "
            "Cold-path plumbing (pipeline_cold.run.main) sets this env var "
            "automatically. For manual runs, export RECITERAI_HIERARCHY_VERSION "
            "before invoking assign_subtopics."
        )

    t0 = time.time()
    stage_started_at = _now_iso()
    t_stage_start = time.monotonic()
    # --dry-run keeps STAGE# writes off too — symmetric with publish.py.
    stage_table = None if dry_run else get_table(TABLE_NAME)

    # T6 — Phase 10 thresholds (low_confidence_floor for the
    # LOW_CONFIDENCE_ASSIGNMENT# event). Missing file is non-fatal so
    # operators can still run the pipeline without the config.
    try:
        thresholds = load_thresholds()
    except FileNotFoundError:
        logger.warning(
            "config/thresholds.json missing; "
            "skipping LOW_CONFIDENCE_ASSIGNMENT# event writes"
        )
        thresholds = None

    # 1. Load taxonomy + hierarchy draft
    taxonomy = _load_taxonomy()
    if topic_id not in taxonomy:
        logger.error(f"Topic {topic_id!r} not found in taxonomy_v2.json")
        sys.exit(3)

    topic_entry = taxonomy[topic_id]
    topic_meta = {
        "id": topic_id,
        "label": topic_entry.get("label", ""),
        "description": topic_entry.get("description", ""),
    }

    hierarchy = _load_hierarchy_draft(draft_path)
    subtopic_defs = [
        {
            "id": s["id"],
            "label": s.get("label", ""),
            "description": s.get("description", ""),
        }
        for s in hierarchy.get("subtopics", [])
    ]
    valid_subtopic_ids = {s["id"] for s in subtopic_defs}
    if not valid_subtopic_ids:
        logger.error(f"Hierarchy draft {draft_path} has zero subtopics")
        sys.exit(2)

    logger.info(
        f"Loaded hierarchy: {len(valid_subtopic_ids)} subtopics "
        f"(review_status=approved)"
    )

    # 2. Query activity rows from DynamoDB
    qualified_rows = _query_topic_activity_rows(topic_id)
    grouped = _dedupe_by_pmid(qualified_rows)
    logger.info(f"Unique PMIDs: {len(grouped)}")

    # 2b. --delta-pmids filter (Phase 10 hot path)
    if delta_pmids is not None:
        delta_set = {str(p) for p in delta_pmids}
        grouped = {p: g for p, g in grouped.items() if str(p) in delta_set}
        logger.info(
            f"--delta-pmids: restricted to {len(grouped)} PMIDs "
            f"(intersection of topic set and delta list)"
        )

    # 3. Apply --resume filter
    pmids_todo: list = []
    skipped_resume = 0
    for pmid, group in grouped.items():
        if resume and group["has_primary"]:
            skipped_resume += 1
            continue
        pmids_todo.append(pmid)
    if resume:
        logger.info(
            f"--resume: skipping {skipped_resume} PMIDs already assigned; "
            f"{len(pmids_todo)} remaining"
        )

    # 4. --limit
    if limit is not None and limit > 0:
        pmids_todo = pmids_todo[:limit]
        logger.info(f"--limit={limit} applied; {len(pmids_todo)} PMIDs to process")

    # 4b. Phase 10 D-07: STAGE# input_hash + should_skip gate.
    # Hash the actual to-process set so the skip cache is content-addressed
    # against the *intent* of this run.
    input_hash = compute_assign_input_hash(
        hierarchy_draft=hierarchy, pmids=pmids_todo
    )
    if stage_table is not None:
        skip, prior = should_skip(
            stage_table,
            stage=STAGE_NAME,
            scope=_topic_scope(topic_id),
            input_hash=input_hash,
        )
        if skip:
            completed_at = _now_iso()
            duration_ms = int((time.monotonic() - t_stage_start) * 1000)
            skip_reason = (
                f"input_hash unchanged since prior complete run at "
                f"{prior.get('started_at', '?')}"
            )
            skipped_kwargs = dict(
                stage=STAGE_NAME,
                scope=_topic_scope(topic_id),
                input_hash=input_hash,
                skip_reason=skip_reason,
                started_at=stage_started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
                model_ids_snapshot=STAGE_MODEL_IDS,
            )
            if emit_envelope:
                print(json.dumps(build_skipped_record(**skipped_kwargs), default=str))
            else:
                write_skipped(stage_table, **skipped_kwargs)
            logger.info(
                f"[STAGE# skip] assign_subtopics topic={topic_id} skipped "
                f"(input_hash {input_hash[:12]}, prior run {prior.get('started_at', '?')})"
            )
            return {
                "topic_id": topic_id,
                "total_pmids_in_topic": len(grouped),
                "processed": 0,
                "stage_status": "skipped",
                "input_hash": input_hash,
            }

    total = len(pmids_todo)
    if total == 0:
        logger.info("Nothing to process. Exiting.")
        return {
            "topic_id": topic_id,
            "total_pmids": len(grouped),
            "skipped_resume": skipped_resume,
            "assigned": 0,
            "unassigned": 0,
            "failed": 0,
            "rows_written": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost_usd": 0.0,
            "elapsed_s": 0,
        }

    # 5. Parallel classify
    client = BedrockClient()
    stats_list: list = []
    lock = Lock()
    done = 0
    assigned_count = 0
    unassigned_count = 0
    failed_count = 0
    total_in_tokens = 0
    total_out_tokens = 0
    total_rows_written = 0

    logger.info(
        f"Classifying {total} PMIDs with concurrency={concurrency}, "
        f"confidence_floor={confidence_floor}, dry_run={dry_run}"
    )

    def _submit(pmid):
        return _process_pmid(
            pmid=pmid,
            group=grouped[pmid],
            client=client,
            topic_meta=topic_meta,
            subtopic_defs=subtopic_defs,
            valid_subtopic_ids=valid_subtopic_ids,
            hierarchy_draft=hierarchy,
            confidence_floor=confidence_floor,
            dry_run=dry_run,
            stage_table=stage_table,
            thresholds=thresholds,
            hierarchy_version=hierarchy_version,
        )

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {executor.submit(_submit, pmid): pmid for pmid in pmids_todo}
        for fut in as_completed(futures):
            pmid = futures[fut]
            try:
                result = fut.result()
            except Exception as exc:
                logger.error(f"Unexpected worker exception pmid={pmid}: {exc}")
                result = {
                    "pmid": pmid,
                    "status": "failed",
                    "error": str(exc),
                    "assigned": False,
                    "filtered_count": 0,
                    "rows_written": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "primary": None,
                }

            with lock:
                stats_list.append(result)
                done += 1
                if result["status"] == "failed":
                    failed_count += 1
                elif result["assigned"]:
                    assigned_count += 1
                else:
                    unassigned_count += 1
                total_in_tokens += result.get("input_tokens", 0) or 0
                total_out_tokens += result.get("output_tokens", 0) or 0
                total_rows_written += result.get("rows_written", 0) or 0

                if done % 50 == 0 or done == total:
                    elapsed = time.time() - t0
                    logger.info(
                        f"[progress] {done}/{total} processed: "
                        f"assigned={assigned_count}, unassigned={unassigned_count}, "
                        f"failed={failed_count}, elapsed={elapsed:.0f}s"
                    )

    # 6. Summary
    elapsed = time.time() - t0
    cost_usd = _estimate_cost(total_in_tokens, total_out_tokens)

    # Median confidence of chosen primaries
    primary_confidences = []
    for s in stats_list:
        if s.get("assigned") and s.get("primary"):
            # approximate — not tracked separately; compute via re-inspection
            pass
    # Coverage vs total_qualified
    coverage = (
        assigned_count / total if total > 0 else 0.0
    )

    # Failed-twice list (manual review) — worker already retries via BedrockClient.
    # We mark a pmid as "needs manual review" if its final status is 'failed'.
    failed_pmids = [s["pmid"] for s in stats_list if s.get("status") == "failed"]

    summary = {
        "topic_id": topic_id,
        "total_pmids_in_topic": len(grouped),
        "skipped_resume": skipped_resume,
        "processed": total,
        "assigned": assigned_count,
        "unassigned": unassigned_count,
        "failed": failed_count,
        "failed_pmids": failed_pmids,
        "rows_written": total_rows_written,
        "coverage_pct_of_processed": round(coverage, 4),
        "input_tokens": total_in_tokens,
        "output_tokens": total_out_tokens,
        "estimated_cost_usd": round(cost_usd, 4),
        "elapsed_s": round(elapsed, 1),
    }

    print("\n=== Pass 2 Assignment Results ===")
    for k, v in summary.items():
        if isinstance(v, list):
            print(f"  {k}: {len(v)} (first 10: {v[:10]})")
        else:
            print(f"  {k}: {v}")

    # --- Phase 10 D-07: STAGE# complete row (direct write or envelope emit) ---
    if stage_table is not None:
        completed_at = _now_iso()
        duration_ms = int((time.monotonic() - t_stage_start) * 1000)
        complete_kwargs = dict(
            stage=STAGE_NAME,
            scope=_topic_scope(topic_id),
            input_hash=input_hash,
            started_at=stage_started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            cost_observed_usd=ASSIGN_COST_USD,
            records_written=total_rows_written,
            model_ids_snapshot=STAGE_MODEL_IDS,
        )
        if emit_envelope:
            print(json.dumps(build_complete_record(**complete_kwargs), default=str))
        else:
            write_complete(stage_table, **complete_kwargs)

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args():
    default_confidence_floor = _get_threshold("confidence_floor")
    parser = argparse.ArgumentParser(
        description=(
            "Pass 2: per-activity Haiku subtopic assignment. "
            "Reads approved hierarchy draft, writes subtopic_ids / "
            "primary_subtopic_id / subtopic_confidences to activity rows."
        )
    )
    parser.add_argument(
        "--topic", required=True,
        help="Topic ID from taxonomy_v2.json (e.g., aging_geroscience)",
    )
    parser.add_argument(
        "--draft-path", type=Path, default=None, metavar="PATH",
        help=(
            "Path to approved hierarchy_draft_<topic>.json. Default: "
            ".planning/phases/04-subtopic-system/hierarchy_draft_<topic>.json"
        ),
    )
    parser.add_argument(
        "--concurrency", type=int, default=DEFAULT_CONCURRENCY, metavar="N",
        help=f"Max concurrent Haiku calls (default {DEFAULT_CONCURRENCY})",
    )
    parser.add_argument(
        "--confidence-floor", type=float, default=default_confidence_floor,
        metavar="FLOOR",
        help=(
            f"Drop assignments below this confidence "
            f"(default {default_confidence_floor}; D-02)"
        ),
    )
    parser.add_argument(
        "--limit", type=int, default=None, metavar="N",
        help="Process only the first N unassigned PMIDs (smoke-test)",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Skip PMIDs that already have primary_subtopic_id set",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Classify but do NOT write DynamoDB updates",
    )
    parser.add_argument(
        "--delta-pmids", default=None, metavar="LIST",
        help=(
            "Phase 10 hot-path filter. Comma-separated PMIDs or '@/path' to a "
            "file with one PMID per line. Restricts processing to PMIDs in "
            "the intersection of this topic and the delta list."
        ),
    )
    parser.add_argument(
        "--emit-envelope", action="store_true",
        help=(
            "Phase 10 D-07 hot-path mode. Emit the STAGE# complete/skipped "
            "record as JSON on stdout instead of writing to DynamoDB. Step "
            "Functions DynamoDB:PutItem SDK integration persists the row."
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    draft_path = args.draft_path or (
        DEFAULT_DRAFT_DIR / f"hierarchy_draft_{args.topic}.json"
    )
    run(
        topic_id=args.topic,
        draft_path=draft_path,
        concurrency=args.concurrency,
        confidence_floor=args.confidence_floor,
        limit=args.limit,
        resume=args.resume,
        dry_run=args.dry_run,
        delta_pmids=_parse_pmid_list_arg(args.delta_pmids),
        emit_envelope=args.emit_envelope,
    )

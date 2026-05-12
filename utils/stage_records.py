"""
STAGE# record CRUD and content-addressed skip semantics.

Phase 9 substrate per docs/RECITERAI-SPEC.md §5 (Decision 4). Every
pipeline stage that integrates with the substrate:

1. Computes its `input_hash` from a canonical view of its inputs.
2. Calls `should_skip(...)` to check whether an existing `complete`
   row already covers that hash.
3. If skip: writes a `skipped` row carrying duration_ms and the
   pinned skip cost (per spec §5 — skips emit cost rows so dashboards
   can split real-work cost from skip-detection cost).
4. If run: writes a `complete` row on success or a `failed` row with
   structured error context.

Records live in the existing single `reciterai-chatbot` table:

    PK: STAGE#{stage_name}#{scope}    e.g. STAGE#publish_hierarchy#GLOBAL
    SK: RUN#{started_at}              ISO8601, lex order = chronological

The scope segment lets per-topic stages (subtopic discovery,
assignment) distinguish runs without colliding under one PK. Use
"GLOBAL" for whole-pipeline stages.

Query pattern in Phase 9: query by PK, filter on `input_hash` in
Python. Few rows per stage; GSI on `input_hash` is deferred to Phase
10 when more stages integrate. See `find_existing_complete` for the
filter logic.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

# Pinned skip cost (spec §5). One sub-1KB DDB GetItem at on-demand
# pricing (~$0.25 / million reads). Wall-clock skip duration is
# dominated by Python startup, which is not an AWS cost — hence a
# constant rather than a duration-derived estimate.
SKIP_COST_USD = Decimal("0.0000003")

# Status enum. Strings rather than IntEnum because they're written to
# DynamoDB and read by humans scanning the table.
STATUS_COMPLETE = "complete"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def compute_input_hash(stage: str, inputs: dict[str, Any]) -> str:
    """
    Canonical sha256 over `inputs`, namespaced by `stage`.

    `inputs` is serialized with sorted keys and no whitespace so dict
    insertion order is irrelevant. The stage name is prepended to the
    hashed bytes so two stages with structurally-identical input dicts
    do not collide.

    Callers compose `inputs` themselves; the schema is per-stage and
    documented in the plan. Typical composition:

        compute_input_hash("publish_hierarchy", {
            "hierarchy_canonical_sha256": "<bytes-sha-excluding-generated_at>",
            "schema_sha256": "<bytes-sha>",
            "excluded_topics_sha256": "<bytes-sha>",
            "model_ids": [MODEL_IDS_BY_STAGE["screening"], ...],
            "bundler_version": pipeline_hierarchy.__version__,
        })

    Model IDs are intentionally included even for stages that don't
    invoke models, so a model swap invalidates skip cache uniformly
    across stages.
    """
    canonical = json.dumps(inputs, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(f"{stage}\x00{canonical}".encode("utf-8")).hexdigest()


def _pk(stage: str, scope: str) -> str:
    return f"STAGE#{stage}#{scope}"


def _sk(started_at: str) -> str:
    return f"RUN#{started_at}"


def find_existing_complete(
    table: Any,
    *,
    stage: str,
    scope: str,
    input_hash: str,
) -> dict | None:
    """
    Return the most recent `complete` row under (stage, scope) whose
    `input_hash` matches, or None.

    Query is on the partition key alone; the input_hash filter runs in
    Python. With one stage per PK and few rows per stage in Phase 9,
    this is cheap. Phase 10 may add a GSI keyed on input_hash if the
    row count per PK grows.

    `failed` and `skipped` rows are deliberately ignored — only a
    prior real success counts as a cache hit.
    """
    pk = _pk(stage, scope)
    resp = table.query(
        KeyConditionExpression="#pk = :pk",
        ExpressionAttributeNames={"#pk": "PK"},
        ExpressionAttributeValues={":pk": pk},
        ScanIndexForward=False,  # newest first
    )
    for item in resp.get("Items", []):
        if item.get("status") == STATUS_COMPLETE and item.get("input_hash") == input_hash:
            return item
    return None


def should_skip(
    table: Any,
    *,
    stage: str,
    scope: str,
    input_hash: str,
) -> tuple[bool, dict | None]:
    """
    Convenience wrapper over `find_existing_complete`.

    Returns (True, prior_row) when a matching `complete` row exists;
    (False, None) when the stage should run.
    """
    prior = find_existing_complete(
        table, stage=stage, scope=scope, input_hash=input_hash
    )
    return (prior is not None, prior)


def _base_item(
    *,
    stage: str,
    scope: str,
    input_hash: str,
    status: str,
    started_at: str,
    completed_at: str,
    duration_ms: int,
    cost_estimate_usd: Decimal,
) -> dict[str, Any]:
    return {
        "PK": _pk(stage, scope),
        "SK": _sk(started_at),
        "stage": stage,
        "scope": scope,
        "input_hash": input_hash,
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_ms": duration_ms,
        "cost_estimate_usd": cost_estimate_usd,
    }


def write_complete(
    table: Any,
    *,
    stage: str,
    scope: str,
    input_hash: str,
    started_at: str,
    completed_at: str | None = None,
    duration_ms: int,
    cost_estimate_usd: Decimal,
    output_pointer: str | None = None,
    records_written: int | None = None,
    model_ids_snapshot: list[str] | None = None,
    force_reason: str | None = None,
) -> dict[str, Any]:
    """
    Write a STAGE# completion row with status=complete.

    `force_reason` is set only when a `block`-severity gate was
    overridden via `gates/cli.py --force --force-reason "..."`.
    """
    item = _base_item(
        stage=stage,
        scope=scope,
        input_hash=input_hash,
        status=STATUS_COMPLETE,
        started_at=started_at,
        completed_at=completed_at or _now_iso(),
        duration_ms=duration_ms,
        cost_estimate_usd=cost_estimate_usd,
    )
    if output_pointer is not None:
        item["output_pointer"] = output_pointer
    if records_written is not None:
        item["records_written"] = records_written
    if model_ids_snapshot is not None:
        item["model_ids_snapshot"] = list(model_ids_snapshot)
    if force_reason is not None:
        item["force_reason"] = force_reason
    table.put_item(Item=item)
    return item


def write_skipped(
    table: Any,
    *,
    stage: str,
    scope: str,
    input_hash: str,
    skip_reason: str,
    started_at: str,
    completed_at: str | None = None,
    duration_ms: int,
    model_ids_snapshot: list[str] | None = None,
) -> dict[str, Any]:
    """
    Write a STAGE# skip row.

    Skips still emit cost rows (per spec §5) — `cost_estimate_usd` is
    pinned to `SKIP_COST_USD` so dashboards can split real-work cost
    from skip-detection cost without losing skipped runs to invisibility.
    """
    item = _base_item(
        stage=stage,
        scope=scope,
        input_hash=input_hash,
        status=STATUS_SKIPPED,
        started_at=started_at,
        completed_at=completed_at or _now_iso(),
        duration_ms=duration_ms,
        cost_estimate_usd=SKIP_COST_USD,
    )
    item["skip_reason"] = skip_reason
    if model_ids_snapshot is not None:
        item["model_ids_snapshot"] = list(model_ids_snapshot)
    table.put_item(Item=item)
    return item


def write_failed(
    table: Any,
    *,
    stage: str,
    scope: str,
    input_hash: str,
    error_code: str,
    error_message: str,
    started_at: str,
    completed_at: str | None = None,
    duration_ms: int,
    cost_estimate_usd: Decimal,
    failure_details: dict | None = None,
    model_ids_snapshot: list[str] | None = None,
) -> dict[str, Any]:
    """
    Write a STAGE# failure row.

    `failure_details` carries structured per-gate or per-step context
    (e.g., the list of subtopics that violated `parent_prefix`).
    Truncate large payloads at the call site if they risk approaching
    DynamoDB's 400KB item limit.
    """
    item = _base_item(
        stage=stage,
        scope=scope,
        input_hash=input_hash,
        status=STATUS_FAILED,
        started_at=started_at,
        completed_at=completed_at or _now_iso(),
        duration_ms=duration_ms,
        cost_estimate_usd=cost_estimate_usd,
    )
    item["error_code"] = error_code
    item["error_message"] = error_message
    if failure_details is not None:
        item["failure_details"] = failure_details
    if model_ids_snapshot is not None:
        item["model_ids_snapshot"] = list(model_ids_snapshot)
    table.put_item(Item=item)
    return item

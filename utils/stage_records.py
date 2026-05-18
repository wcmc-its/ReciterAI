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

Records live in the existing single `reciterai` table:

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
from decimal import Decimal
from typing import Any
from utils.iso_clock import now_iso

# Skip observed cost (Phase 10 D-09). Skipped stages did no work — the
# observed cost is zero. The DDB GetItem cost (~$0.0000003) is below
# noise floor and orthogonal to "what did the work cost?", so it is
# explicitly not folded in. Treat skips as first-class zeros for
# cost-aggregation queries; absence breaks SUM() over the field.
SKIP_COST_OBSERVED_USD = Decimal("0")

# Status enum. Strings rather than IntEnum because they're written to
# DynamoDB and read by humans scanning the table.
STATUS_COMPLETE = "complete"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"

# #80 Phase 2 — the new-researcher onboarding workflow surfaces a 4-state
# terminal taxonomy (R7): complete | partial | skipped | failed.
# `complete` / `skipped` / `failed` are shared with the per-stage records
# above; `partial` (the workflow ran but some PMIDs did not reach a scored
# state) is unique to the `STAGE#onboarding#cwid` workflow row written by
# `pipeline_onboarding`. Defined here so the substrate has one status
# vocabulary rather than a second enum drifting in the onboarding package.
# (The `deferred` status was retired when onboarding moved synopsis
# generation inline — see pipeline_onboarding/enrich.py, #112.)
STATUS_PARTIAL = "partial"




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
    cost_observed_usd: Decimal,
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
        "cost_observed_usd": cost_observed_usd,
    }


def build_complete_record(
    *,
    stage: str,
    scope: str,
    input_hash: str,
    started_at: str,
    completed_at: str | None = None,
    duration_ms: int,
    cost_observed_usd: Decimal,
    output_pointer: str | None = None,
    records_written: int | None = None,
    model_ids_snapshot: list[str] | None = None,
    force_reason: str | None = None,
    run_id: str | None = None,
    tunable_inputs: dict[str, Any] | None = None,
    content_filter_count: int | None = None,
    input_pmid_set: list[str] | None = None,
    rollup_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    """
    Pure builder for a STAGE# complete row dict. No I/O.

    Phase 10 (D-07): split out of `write_complete` so a Step Functions
    SDK integration can write the row instead of Python. Python
    crashing between "work done" and "row written" is now the state
    machine's problem, not a missed completion signal.

    `force_reason` is set only when a `block`-severity gate was
    overridden via `gates/cli.py --force --force-reason "..."`.

    `run_id` (Phase 11 D-13): optional cold-run identifier used by diff.json
    producers to correlate assign-stage STAGE# rows with the current cold-run.
    Backwards-compatible — omitted when None so existing consumers see no change.

    Phase 12 D-28: `tunable_inputs` is an optional input-audit dict carrying
    tunable values read from `config/thresholds.json` and CLI overrides.
    Convention: keys are tunable names (e.g., 'confidence_floor'), plus a
    parallel `<key>_source` discriminator with one of {'config','cli','default'}.
    Same backwards-compatible additive pattern as `run_id` (Phase 11 D-13) —
    omitted when None.

    `content_filter_count` is an optional run-level count of publications
    whose dense-scoring pass hit the model content filter (Sonnet returned
    empty content), regardless of whether the OpenAI fallback then recovered
    them. Surfaces on the score_publications STAGE# row so a content-filter
    rate spike is visible without a per-PMID scan. Same additive pattern —
    omitted when None.

    `input_pmid_set` (#80 Phase 2 / #90) is the optional list of PMIDs a
    CWID-scoped rollup run consumed — the live `analysis_summary_author`
    snapshot for the CWID at rollup time. The onboarding detector reads it
    off the most recent `STAGE#rollup_by_cwid#cwid:{cwid}` complete row to
    detect ReCiter attribution drift (R9 churn): a current accepted set that
    no longer matches the recorded set means the CWID needs re-onboarding.
    Same additive pattern — omitted when None.

    `rollup_counts` (#80 Phase 2 / #90) is the optional per-CWID rollup
    result map — `n_activities`, `n_distinct_topics`,
    `n_subtopic_activities`, `n_distinct_subtopics`. The CWID-scoped rollup
    path records it on the STAGE# row because, unlike the GLOBAL CSV path,
    it has no shared `cwid_rollup.csv` to write the per-CWID numbers to.
    Same additive pattern — omitted when None.
    """
    item = _base_item(
        stage=stage,
        scope=scope,
        input_hash=input_hash,
        status=STATUS_COMPLETE,
        started_at=started_at,
        completed_at=completed_at or now_iso(),
        duration_ms=duration_ms,
        cost_observed_usd=cost_observed_usd,
    )
    if output_pointer is not None:
        item["output_pointer"] = output_pointer
    if records_written is not None:
        item["records_written"] = records_written
    if model_ids_snapshot is not None:
        item["model_ids_snapshot"] = list(model_ids_snapshot)
    if force_reason is not None:
        item["force_reason"] = force_reason
    if run_id is not None:
        item["run_id"] = run_id
    if tunable_inputs is not None:
        item["tunable_inputs"] = dict(tunable_inputs)
    if content_filter_count is not None:
        item["content_filter_count"] = content_filter_count
    if input_pmid_set is not None:
        item["input_pmid_set"] = list(input_pmid_set)
    if rollup_counts is not None:
        item["rollup_counts"] = dict(rollup_counts)
    return item


def build_skipped_record(
    *,
    stage: str,
    scope: str,
    input_hash: str,
    skip_reason: str,
    started_at: str,
    completed_at: str | None = None,
    duration_ms: int,
    model_ids_snapshot: list[str] | None = None,
    run_id: str | None = None,
    tunable_inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Pure builder for a STAGE# skip row dict. No I/O.

    Skips still emit cost rows so SUM() over `cost_observed_usd` returns
    a meaningful aggregate. Phase 10 D-09 pins skip cost to zero (no
    work performed); the DDB GetItem lookup cost is not modeled per row.

    `run_id` (Phase 11 D-13): optional cold-run identifier. Backwards-compatible.

    Phase 12 D-28: `tunable_inputs` is an optional input-audit dict carrying
    tunable values read from `config/thresholds.json` and CLI overrides.
    Convention: keys are tunable names (e.g., 'confidence_floor'), plus a
    parallel `<key>_source` discriminator with one of {'config','cli','default'}.
    Same backwards-compatible additive pattern as `run_id` (Phase 11 D-13) —
    omitted when None.
    """
    item = _base_item(
        stage=stage,
        scope=scope,
        input_hash=input_hash,
        status=STATUS_SKIPPED,
        started_at=started_at,
        completed_at=completed_at or now_iso(),
        duration_ms=duration_ms,
        cost_observed_usd=SKIP_COST_OBSERVED_USD,
    )
    item["skip_reason"] = skip_reason
    if model_ids_snapshot is not None:
        item["model_ids_snapshot"] = list(model_ids_snapshot)
    if run_id is not None:
        item["run_id"] = run_id
    if tunable_inputs is not None:
        item["tunable_inputs"] = dict(tunable_inputs)
    return item


def build_failed_record(
    *,
    stage: str,
    scope: str,
    input_hash: str,
    error_code: str,
    error_message: str,
    started_at: str,
    completed_at: str | None = None,
    duration_ms: int,
    cost_observed_usd: Decimal,
    failure_details: dict | None = None,
    model_ids_snapshot: list[str] | None = None,
    run_id: str | None = None,
    tunable_inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Pure builder for a STAGE# failure row dict. No I/O.

    `failure_details` carries structured per-gate or per-step context
    (e.g., the list of subtopics that violated `parent_prefix`).
    Truncate large payloads at the call site if they risk approaching
    DynamoDB's 400KB item limit.

    `run_id` (Phase 11 D-13): optional cold-run identifier. Backwards-compatible.

    Phase 12 D-28: `tunable_inputs` is an optional input-audit dict carrying
    tunable values read from `config/thresholds.json` and CLI overrides.
    Convention: keys are tunable names (e.g., 'confidence_floor'), plus a
    parallel `<key>_source` discriminator with one of {'config','cli','default'}.
    Same backwards-compatible additive pattern as `run_id` (Phase 11 D-13) —
    omitted when None.
    """
    item = _base_item(
        stage=stage,
        scope=scope,
        input_hash=input_hash,
        status=STATUS_FAILED,
        started_at=started_at,
        completed_at=completed_at or now_iso(),
        duration_ms=duration_ms,
        cost_observed_usd=cost_observed_usd,
    )
    item["error_code"] = error_code
    item["error_message"] = error_message
    if failure_details is not None:
        item["failure_details"] = failure_details
    if model_ids_snapshot is not None:
        item["model_ids_snapshot"] = list(model_ids_snapshot)
    if run_id is not None:
        item["run_id"] = run_id
    if tunable_inputs is not None:
        item["tunable_inputs"] = dict(tunable_inputs)
    return item


def write_complete(table: Any, **kwargs: Any) -> dict[str, Any]:
    """Build a STAGE# complete row and persist it via `table.put_item`.

    Thin writer: see `build_complete_record` for the full kwargs
    signature. Cold-path Python uses this; hot-path Step Functions
    consumes the builder output via SDK integration instead.
    """
    item = build_complete_record(**kwargs)
    table.put_item(Item=item)
    return item


def write_skipped(table: Any, **kwargs: Any) -> dict[str, Any]:
    """Build a STAGE# skip row and persist it. See `build_skipped_record`."""
    item = build_skipped_record(**kwargs)
    table.put_item(Item=item)
    return item


def write_failed(table: Any, **kwargs: Any) -> dict[str, Any]:
    """Build a STAGE# failed row and persist it. See `build_failed_record`."""
    item = build_failed_record(**kwargs)
    table.put_item(Item=item)
    return item

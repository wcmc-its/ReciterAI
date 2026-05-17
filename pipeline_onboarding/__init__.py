"""New-researcher onboarding orchestrator — #80 Phase 2.

ReciterAI's hot path is delta-based on publication recency, so a researcher
who joins WCM with a substantial prior publication history has most of
those papers fall outside every weekly delta window. This package is the
CWID-scoped backfill workflow that closes that gap: it scores, assigns,
top-topics, and rolls up one researcher's full accepted-publication set
regardless of publication date.

Architecture (PLAN §3) — the `reciterai-onboarding` Step Functions state
machine drives the cascade:

    Orchestrate -> CheckProceed -> Score -> AssignFanOut -> TopTopic
                                -> Rollup -> Finalize

mirroring the hot path's proven shape (`pipeline_hot/`). It reuses the four
hot per-stage Lambdas (`reciterai-hot-score / -assign / -top-topic /
-rollup`; the Assign stage fans `-assign` out per topic, PR 4) and adds
onboarding-specific entry points:

    orchestrator.py   first Task — scope, synopsis precondition, cost guard
    assign_fanout.py  DeriveDirtyTopics Task — per-topic Assign Map fan-out
    finalize.py       last Task — decide complete|partial; the notify Lambda
    state_machine.asl.json   the cascade, with ${...Arn} deploy placeholders

The workflow writes one `STAGE#onboarding#cwid:{cwid}` row per run carrying
the 5-state terminal taxonomy (R7): ``complete | partial | deferred |
skipped | failed``. `build_onboarding_record` below is the single builder
for that row — shared by the orchestrator (deferred / skipped / cost-guard
terminals) and finalize (complete / partial) so the row schema lives in one
place.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from utils.iso_clock import now_iso
from utils.stage_records import (
    STATUS_COMPLETE,
    STATUS_DEFERRED,
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_SKIPPED,
)

# Stage name for the workflow-level STAGE# row. The four per-stage rows the
# cascade writes (score_publications, assign_subtopics, rollup_by_cwid, ...)
# keep their own stage names; `onboarding` is the umbrella workflow row.
ONBOARDING_STAGE = "onboarding"

# The 5-state terminal taxonomy (R7). A workflow run ends in exactly one.
ONBOARDING_STATUSES = frozenset(
    {STATUS_COMPLETE, STATUS_PARTIAL, STATUS_DEFERRED, STATUS_SKIPPED, STATUS_FAILED}
)

# Cost-guard refusal error code (D-COSTSTATUS). A cost-guard trip is modelled
# as status=failed + this error_code rather than a sixth `blocked` status, so
# the spec's "exactly one of five" holds. Operators re-run with
# allow_cost_override=true; the failure_details carry the numbers.
ERROR_CODE_COST_GUARD = "CostGuardExceeded"

# Catch-all error code for a catastrophic Lambda/Task crash routed through
# the state machine's Catch to the inline failed-row writer.
ERROR_CODE_TASK_FAILED = "WorkflowTaskFailed"

_ZERO_USD = Decimal("0")


def onboarding_scope(cwid: str) -> str:
    """Return the STAGE# scope segment for a CWID-scoped onboarding run."""
    return f"cwid:{cwid}"


def onboarding_pk(cwid: str) -> str:
    """Return the `STAGE#onboarding#cwid:{cwid}` partition key for `cwid`."""
    return f"STAGE#{ONBOARDING_STAGE}#{onboarding_scope(cwid)}"


def build_onboarding_record(
    *,
    cwid: str,
    status: str,
    started_at: str,
    run_id: str,
    input_hash: str,
    completed_at: str | None = None,
    duration_ms: int = 0,
    cost_observed_usd: Decimal = _ZERO_USD,
    pmid_count: int | None = None,
    net_work_count: int | None = None,
    projected_cost_usd: str | None = None,
    partial_failure_count: int | None = None,
    failed_pmids: list[str] | None = None,
    deferred_reason: str | None = None,
    deferred_pmids: list[str] | None = None,
    skip_reason: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    failure_details: dict[str, Any] | None = None,
    stage_input_hashes: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Pure builder for one `STAGE#onboarding#cwid:{cwid}` workflow row. No I/O.

    One row per onboarding run (PLAN §3 substrate):

        PK  STAGE#onboarding#cwid:{cwid}
        SK  RUN#{started_at}

    `status` must be one of the 5-state taxonomy (R7). All status-specific
    fields are optional and additive — omitted when None, the same pattern
    `utils.stage_records.build_complete_record` uses — so a `complete` row
    carries no `deferred_reason`, a `deferred` row no `error_code`, etc.

    The orchestrator builds the `deferred` / `skipped` / cost-guard `failed`
    rows from this; finalize builds the `complete` / `partial` row. Both
    convert the result with `pipeline_common.envelope.to_ddb_typed_envelope`
    before handing it to the state machine's `dynamodb:putItem` integration.

    Numeric fields are kept as `int` / `Decimal` (never `float`) so the
    DynamoDB `TypeSerializer` types them as `N`; `projected_cost_usd` is a
    pre-formatted display string (an estimate, not measured spend) and is
    stored as `S`.
    """
    if status not in ONBOARDING_STATUSES:
        raise ValueError(
            f"build_onboarding_record: status {status!r} is not one of the "
            f"onboarding 5-state taxonomy {sorted(ONBOARDING_STATUSES)}"
        )

    item: dict[str, Any] = {
        "PK": onboarding_pk(cwid),
        "SK": f"RUN#{started_at}",
        "stage": ONBOARDING_STAGE,
        "scope": onboarding_scope(cwid),
        "status": status,
        "cwid": cwid,
        "run_id": run_id,
        "input_hash": input_hash,
        "started_at": started_at,
        "completed_at": completed_at or now_iso(),
        "duration_ms": int(duration_ms),
        "cost_observed_usd": cost_observed_usd,
    }
    if pmid_count is not None:
        item["pmid_count"] = int(pmid_count)
    if net_work_count is not None:
        item["net_work_count"] = int(net_work_count)
    if projected_cost_usd is not None:
        item["projected_cost_usd"] = str(projected_cost_usd)
    if partial_failure_count is not None:
        item["partial_failure_count"] = int(partial_failure_count)
    if failed_pmids is not None:
        item["failed_pmids"] = [str(p) for p in failed_pmids]
    if deferred_reason is not None:
        item["deferred_reason"] = deferred_reason
    if deferred_pmids is not None:
        item["deferred_pmids"] = [str(p) for p in deferred_pmids]
    if skip_reason is not None:
        item["skip_reason"] = skip_reason
    if error_code is not None:
        item["error_code"] = error_code
    if error_message is not None:
        item["error_message"] = error_message
    if failure_details is not None:
        item["failure_details"] = failure_details
    if stage_input_hashes is not None:
        item["stage_input_hashes"] = dict(stage_input_hashes)
    return item


__all__ = [
    "ONBOARDING_STAGE",
    "ONBOARDING_STATUSES",
    "ERROR_CODE_COST_GUARD",
    "ERROR_CODE_TASK_FAILED",
    "onboarding_scope",
    "onboarding_pk",
    "build_onboarding_record",
]

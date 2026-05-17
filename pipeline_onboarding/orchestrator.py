"""Onboarding orchestrator: state-machine entry Task (#80 Phase 2, PR 3).

The first Task of the `reciterai-onboarding` state machine. For one CWID it:

1. Scopes the work set — `get_pmids_for_cwid` (R2): the researcher's full
   accepted-publication PMID set, regardless of publication date.
2. Checks the synopsis precondition — `check_synopsis_coverage` (R3 step 1):
   if any PMID lacks a synopsis the run is **deferred** (the operator-laptop
   cron owns synopsis generation; onboarding does not generate them — see
   #92).
3. Culls the PROCESSING# checkpoint: PMIDs already `complete` are dropped to
   compute the **net work** set — what will actually hit Bedrock/OpenAI.
4. Applies the cost guard (R5 / D-COSTSTATUS): net work above
   `onboarding_cost_guard_max_pmids` with no override terminates the run
   `cost_exceeded` → a `failed` row carrying `error_code=CostGuardExceeded`.

It returns a routing status for the `CheckProceed` Choice — `ready` /
`deferred` / `skipped` / `cost_exceeded` — distinct from the 5-state
workflow `status` written to the `STAGE#onboarding#cwid` row (the hot
orchestrator draws the same routing-vs-record distinction).

Unlike the hot orchestrator there is **no concurrency lock**: onboarding is
operator-triggered per CWID, and same-CWID races are absorbed by per-stage
STAGE#/PROCESSING# idempotency — so no onboarding Lambda calls `states:*`.

The Lambda entry point is `handler(event, context)`. `evaluate_onboarding`
holds the pure decision logic and is unit-tested with injected work-set
data; `handler` does the I/O (ReciterDB + DynamoDB reads, the cost-preview
Teams alert) and composes it.
"""

from __future__ import annotations

import hashlib
import logging
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

# Ensure repo root is importable regardless of cwd.
sys.path.insert(0, str(Path(__file__).parent.parent))

# Populate DB_* env vars from Secrets Manager when running in Lambda. Must
# precede `utils.sql_queries` so the SQLAlchemy engine factory finds creds.
import utils.secrets_loader  # noqa: F401

from pipeline_common.envelope import to_ddb_typed_envelope
from pipeline_enrichment import alerting
from pipeline_onboarding import (
    ERROR_CODE_COST_GUARD,
    ONBOARDING_STAGE,
    build_onboarding_record,
)
# `onboarding_cost_guard_tripped` is the canonical cost-guard decision
# delivered in #80 Phase 1; reuse it so the orchestrator (the primary
# decision point) and `score_publications --pmids` (defense-in-depth) cannot
# drift. ONBOARDING_COST_GUARD_MAX_PMIDS resolves config/thresholds.json.
from score_publications import (
    ONBOARDING_COST_GUARD_MAX_PMIDS,
    onboarding_cost_guard_tripped,
)
from utils.bedrock_client import HAIKU_MODEL, SONNET_MODEL
from utils.dynamodb_helpers import (
    TABLE_NAME,
    get_dynamo_client,
    get_processing_status,
)
from utils.iso_clock import now_iso
from utils.sql_queries import check_synopsis_coverage, get_pmids_for_cwid
from utils.stage_records import (
    STATUS_COMPLETE,
    STATUS_DEFERRED,
    STATUS_FAILED,
    STATUS_SKIPPED,
    compute_input_hash,
)

logger = logging.getLogger(__name__)

# Routing statuses returned to the `CheckProceed` Choice. `ready` proceeds
# to Score; the other three route to a known-status terminal writer.
ROUTE_READY = "ready"
ROUTE_DEFERRED = "deferred"
ROUTE_SKIPPED = "skipped"
ROUTE_COST_EXCEEDED = "cost_exceeded"

# Conservative per-call token estimates for the onboarding cost preview
# (R5 / R10 #4). These size the Teams cost note an operator sees before a
# run; they are NOT measured spend — the score STAGE# row's
# `cost_observed_usd` is the instrumented figure (currently a Phase 10
# stub). Screening scores every taxonomy topic in one Haiku call; dense
# scoring re-scores only the passed subset in one Sonnet call. One Sonnet
# call per PMID is assumed even though a PMID where no topic clears
# screening makes none — an intentional overcount, since a cost preview
# should err high.
_EST_SCREENING_INPUT_TOKENS = 5000
_EST_SCREENING_OUTPUT_TOKENS = 1500
_EST_DENSE_INPUT_TOKENS = 2000
_EST_DENSE_OUTPUT_TOKENS = 1000


# ---------------------------------------------------------------------------
# Input hash + cost preview
# ---------------------------------------------------------------------------


def compute_onboarding_input_hash(cwid: str, pmids: list[str]) -> str:
    """Content-addressed input hash for an onboarding run.

    Namespaced by `cwid` and collapsing the PMID set, so a re-run over the
    same researcher's identical publication set is identifiable on the
    `STAGE#onboarding#cwid` row. This hash labels the audit row; it is not a
    skip gate — onboarding idempotency is the PROCESSING# cull plus
    `score_publications`'s own taxonomy-aware skip cache.
    """
    pmid_set_hash = hashlib.sha256(
        ",".join(sorted({str(p) for p in pmids})).encode("utf-8")
    ).hexdigest()
    return compute_input_hash(
        ONBOARDING_STAGE,
        {"cwid": cwid, "pmid_set_sha256": pmid_set_hash},
    )


def estimate_onboarding_cost(net_work_count: int) -> Decimal | None:
    """Best-effort USD estimate for scoring `net_work_count` PMIDs.

    Sums a one-Haiku-screening-call + one-Sonnet-dense-call estimate per
    PMID against `config/llm_prices.yaml`. Returns None (logged) if the
    price table is unavailable — a cost preview is observability, never
    flow control, so it must not break the run.
    """
    if net_work_count <= 0:
        return Decimal("0")
    try:
        from utils.llm_cost import estimate_cost

        screening = estimate_cost(
            HAIKU_MODEL,
            n_calls=net_work_count,
            avg_input_tokens=_EST_SCREENING_INPUT_TOKENS,
            avg_output_tokens=_EST_SCREENING_OUTPUT_TOKENS,
        )
        dense = estimate_cost(
            SONNET_MODEL,
            n_calls=net_work_count,
            avg_input_tokens=_EST_DENSE_INPUT_TOKENS,
            avg_output_tokens=_EST_DENSE_OUTPUT_TOKENS,
        )
        return screening + dense
    except Exception as exc:  # noqa: BLE001 — cost preview is best-effort
        logger.warning("Onboarding cost estimate unavailable: %s", exc)
        return None


def _cost_display(cost: Decimal | None) -> str:
    """Format an estimated cost for the STAGE# row / Teams note."""
    return f"~${cost}" if cost is not None else "unavailable"


# ---------------------------------------------------------------------------
# Decision logic (pure — unit-tested with injected work-set data)
# ---------------------------------------------------------------------------


def evaluate_onboarding(
    *,
    cwid: str,
    run_id: str,
    started_at: str,
    allow_cost_override: bool,
    pmids: list[str],
    synopsis_missing: list[str],
    processing_status: dict[str, str],
    duration_ms: int = 0,
) -> dict[str, Any]:
    """Decide the routing status for one onboarding run. Pure — no I/O.

    `pmids` is the CWID's full accepted set; `synopsis_missing` the subset
    with no synopsis (from `check_synopsis_coverage`); `processing_status`
    maps pmid -> PROCESSING# status for the checkpoint cull.

    Decision order (PLAN §3): no PMIDs -> `skipped`; any synopsis missing ->
    `deferred`; net work (PMIDs not already `complete`) empty -> `skipped`;
    net work over the cost guard with no override -> `cost_exceeded`; else
    `ready`.

    Returns ``{"status", "input", "terminal_envelope"}`` — `input` carries
    the work payload for the ready cascade, `terminal_envelope` the
    DDB-typed `STAGE#onboarding#cwid` row for the three non-ready writers.
    The unused key is an empty dict; the state machine only dereferences the
    one the taken branch needs.
    """
    input_hash = compute_onboarding_input_hash(cwid, pmids)

    def _terminal(status: str, **record_kwargs: Any) -> dict[str, Any]:
        row = build_onboarding_record(
            cwid=cwid,
            status=status,
            started_at=started_at,
            completed_at=now_iso(),
            run_id=run_id,
            input_hash=input_hash,
            duration_ms=duration_ms,
            pmid_count=len(pmids),
            **record_kwargs,
        )
        return {
            "status": _ROUTE_FOR_STATUS[status],
            "input": {},
            "terminal_envelope": to_ddb_typed_envelope(row),
        }

    # 1. No accepted publications — nothing to onboard.
    if not pmids:
        return _terminal(
            STATUS_SKIPPED,
            net_work_count=0,
            skip_reason=(
                "CWID has no accepted publications "
                "(Academic Article, articleYear >= 2020)"
            ),
        )

    # 2. Synopsis precondition (R3 step 1). Any PMID without a synopsis
    #    defers the whole run — onboarding does not generate synopses.
    if synopsis_missing:
        return _terminal(
            STATUS_DEFERRED,
            net_work_count=len(pmids),
            deferred_reason=(
                f"{len(synopsis_missing)} of {len(pmids)} PMID(s) have no "
                "synopsis; run deferred pending synopsis backfill by the "
                "enrichment job (#92)"
            ),
            deferred_pmids=sorted(synopsis_missing),
        )

    # 3. PROCESSING# checkpoint cull — the net Bedrock/OpenAI-bound work set.
    net_work = [p for p in pmids if processing_status.get(p) != STATUS_COMPLETE]
    if not net_work:
        return _terminal(
            STATUS_SKIPPED,
            net_work_count=0,
            skip_reason=(
                f"all {len(pmids)} PMID(s) already scored — "
                "idempotent no-op"
            ),
        )

    # 4. Cost guard (R5 / D-COSTSTATUS). The orchestrator is the single
    #    decision point; score_publications' own guard is defense-in-depth.
    projected_cost = estimate_onboarding_cost(len(net_work))
    if onboarding_cost_guard_tripped(
        len(net_work),
        threshold=ONBOARDING_COST_GUARD_MAX_PMIDS,
        override=allow_cost_override,
    ):
        return _terminal(
            STATUS_FAILED,
            net_work_count=len(net_work),
            projected_cost_usd=_cost_display(projected_cost),
            error_code=ERROR_CODE_COST_GUARD,
            error_message=(
                f"net scoring work is {len(net_work)} PMID(s), above the "
                f"{ONBOARDING_COST_GUARD_MAX_PMIDS}-PMID cost guard. Nothing "
                "is broken — re-run with allow_cost_override=true to proceed."
            ),
            failure_details={
                "net_work_count": len(net_work),
                "projected_cost_usd": _cost_display(projected_cost),
                "threshold": ONBOARDING_COST_GUARD_MAX_PMIDS,
            },
        )

    # 5. Ready — hand the work payload to the Score cascade.
    return {
        "status": ROUTE_READY,
        "input": {
            "cwid": cwid,
            "run_id": run_id,
            "started_at": started_at,
            "pmids": pmids,
            "allow_cost_override": allow_cost_override,
            "net_work_count": len(net_work),
            "projected_cost_usd": _cost_display(projected_cost),
            "input_hash": input_hash,
        },
        "terminal_envelope": {},
    }


# Routing status for each terminal workflow status. `cost_exceeded` is a
# routing label distinct from its `failed` record status (D-COSTSTATUS).
_ROUTE_FOR_STATUS = {
    STATUS_SKIPPED: ROUTE_SKIPPED,
    STATUS_DEFERRED: ROUTE_DEFERRED,
    STATUS_FAILED: ROUTE_COST_EXCEEDED,
}


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------


def handler(event: dict, context: Any = None) -> dict:
    """Onboarding orchestrator Lambda handler.

    Expected event (from the ASL `Orchestrate` Task Parameters):
        {
          "execution_input": {"cwid": "abc1234",
                               "allow_cost_override": false},
          "run_id": "<$$.Execution.Name>"
        }

    Returns ``{"status", "input", "terminal_envelope"}`` — see
    `evaluate_onboarding`. A missing `cwid` is an operator error and raises
    (the state machine's Catch writes the failed row).
    """
    started_at = now_iso()
    t0 = time.monotonic()

    exec_input = event.get("execution_input") or {}
    cwid = (exec_input.get("cwid") or "").strip()
    allow_cost_override = bool(exec_input.get("allow_cost_override", False))
    run_id = event.get("run_id") or started_at

    if not cwid:
        raise ValueError(
            "onboarding orchestrator: execution input has no 'cwid'; start "
            'the state machine with input {"cwid": "<cwid>"}'
        )

    logger.info(
        "Onboarding orchestrator: cwid=%s run_id=%s allow_cost_override=%s",
        cwid, run_id, allow_cost_override,
    )

    # ReciterDB: the CWID's accepted PMID set + synopsis coverage.
    pmids = get_pmids_for_cwid(cwid)
    synopsis_missing = (
        check_synopsis_coverage(pmids)["missing"] if pmids else []
    )
    # DynamoDB: the PROCESSING# checkpoint for the net-work cull.
    processing_status = (
        get_processing_status(get_dynamo_client(), TABLE_NAME, pmids)
        if pmids
        else {}
    )

    result = evaluate_onboarding(
        cwid=cwid,
        run_id=run_id,
        started_at=started_at,
        allow_cost_override=allow_cost_override,
        pmids=pmids,
        synopsis_missing=synopsis_missing,
        processing_status=processing_status,
        duration_ms=int((time.monotonic() - t0) * 1000),
    )

    # Cost visibility (R5 / R10 #4). The ready path emits an informational
    # Teams preview (no @-mention — not actionable); a `cost_exceeded` run
    # is surfaced instead by the state machine's NotifyCostExceeded Task, so
    # net-work-exists always produces exactly one Teams cost mention.
    if result["status"] == ROUTE_READY:
        work = result["input"]
        logger.info(
            "Onboarding ready: cwid=%s net_work=%s projected_cost=%s",
            cwid, work["net_work_count"], work["projected_cost_usd"],
        )
        alerting.alert(
            "WARN",
            f"Onboarding cost preview — CWID {cwid}",
            f"Onboarding run for CWID {cwid} will score "
            f"{work['net_work_count']} publication(s). Estimated cost "
            f"{work['projected_cost_usd']}.",
            {
                "cwid": cwid,
                "run_id": run_id,
                "net_work_count": work["net_work_count"],
                "projected_cost_usd": work["projected_cost_usd"],
            },
            mention=False,
        )

    return result

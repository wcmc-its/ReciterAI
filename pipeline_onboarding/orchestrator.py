"""Onboarding orchestrator: state-machine entry Task (#80 Phase 2, PR 3).

The first Task of the `reciterai-onboarding` state machine. For one CWID it:

1. Scopes the work set — `get_pmids_for_cwid` (R2): the researcher's full
   accepted-publication PMID set, regardless of publication date.
2. Culls the PROCESSING# checkpoint: PMIDs already `complete` are dropped to
   compute the **net work** set — what will actually hit Bedrock/OpenAI.
3. Applies the cost guard (R5 / D-COSTSTATUS): net work above
   `onboarding_cost_guard_max_pmids` with no override terminates the run
   `cost_exceeded` → a `failed` row carrying `error_code=CostGuardExceeded`.

A run whose net-work set is empty normally `skipped`s as an idempotent
no-op — but only when the CWID's rollup is current. If the rollup is
missing or stale (a prior cascade died after Score, or ReCiter
de-attributed a PMID since the last rollup), the orchestrator routes
`ready` for a recovery re-run instead (#115).

It returns a routing status for the `CheckProceed` Choice — `ready` /
`skipped` / `cost_exceeded` — distinct from the 4-state workflow `status`
written to the `STAGE#onboarding#cwid` row (the hot orchestrator draws the
same routing-vs-record distinction). The synopsis precondition that used to
route `deferred` is retired: onboarding now generates synopses inline in the
Enrich stage (#112), which runs after a `ready` route.

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
from utils.bedrock_client import HAIKU_MODEL, SONNET_MODEL
from utils.dynamodb_helpers import (
    TABLE_NAME,
    get_dynamo_client,
    get_processing_status,
    get_table,
)
from utils.iso_clock import now_iso
from utils.sql_queries import get_pmids_for_cwid
from utils.stage_records import (
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_SKIPPED,
    compute_input_hash,
    find_latest_complete,
)

logger = logging.getLogger(__name__)

# Routing statuses returned to the `CheckProceed` Choice. `ready` proceeds
# into the Enrich->Score cascade; the other two route to a known-status
# terminal writer.
ROUTE_READY = "ready"
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
# Rollup-completeness probe (#115 recovery)
# ---------------------------------------------------------------------------

# The per-CWID rollup stage writes STAGE#rollup_by_cwid#cwid:{cwid} rows
# (rollup_by_cwid.py's STAGE_NAME + _cwid_scope). Kept as literals rather than
# imported: a module-scope `import rollup_by_cwid` would break the lean
# onboarding-detector zip, which bundles orchestrator.py for its cost-preview
# chain but not rollup_by_cwid.py (cf. the function-local score_publications
# import in evaluate_onboarding). These two strings are the DynamoDB partition
# key — as stable as a schema constant; scan_rollup_baselines in detector.py
# hardcodes the same prefix for the same reason.
_ROLLUP_STAGE = "rollup_by_cwid"


def _rollup_scope(cwid: str) -> str:
    """STAGE# scope segment for a CWID-scoped rollup row."""
    return f"cwid:{cwid}"


def latest_rollup_pmid_set(table: Any, cwid: str) -> set[str] | None:
    """The accepted PMID set the CWID's most recent rollup consumed.

    Reads `input_pmid_set` off the latest `complete`
    `STAGE#rollup_by_cwid#cwid:{cwid}` row (written by `rollup_by_cwid
    --cwid`, PR 2 / #90). Returns:

    - the PMID set — the CWID has a completed rollup;
    - `None` — the CWID has never had a complete rollup, or its latest
      complete rollup row carries no `input_pmid_set`. Either way the #115
      recovery check treats it as "not rolled up" and routes a recovery
      re-run, the safe direction.

    The recovery check compares this against the CWID's live accepted set:
    a mismatch (or `None`) means a prior cascade scored the CWID but did not
    finish rolling it up, so the run must re-execute the cascade rather than
    skip as an idempotent no-op.
    """
    row = find_latest_complete(
        table, stage=_ROLLUP_STAGE, scope=_rollup_scope(cwid)
    )
    if row is None:
        return None
    raw = row.get("input_pmid_set")
    if raw is None:
        return None
    return {str(p) for p in raw}


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
    processing_status: dict[str, str],
    rollup_input_pmid_set: set[str] | None,
    duration_ms: int = 0,
) -> dict[str, Any]:
    """Decide the routing status for one onboarding run. Pure — no I/O.

    `pmids` is the CWID's full accepted set; `processing_status` maps
    pmid -> PROCESSING# status for the checkpoint cull;
    `rollup_input_pmid_set` is the accepted PMID set the CWID's most recent
    complete rollup consumed (`latest_rollup_pmid_set`), or None when the
    CWID has never had a complete rollup.

    Decision order:

    - no PMIDs -> `skipped`;
    - net work (PMIDs not already `complete`) empty, and the rollup for this
      exact accepted set has landed -> `skipped` (a true idempotent no-op);
    - net work empty but the rollup is missing or stale -> `ready` with the
      `recovery` flag set (#115): a prior cascade scored the CWID but did
      not finish rolling it up — or ReCiter de-attributed a PMID since — so
      the cascade re-runs. Enrich/Score/Assign idempotently skip; only
      Rollup recomputes (D8's no-model-cost rollup recomputation);
    - net work over the cost guard with no override -> `cost_exceeded`;
    - else -> `ready`.

    A `ready` run proceeds into the Enrich->Score cascade; the Enrich stage
    generates any missing synopses (#112), so there is no synopsis
    precondition here.

    Returns ``{"status", "input", "terminal_envelope"}`` — `input` carries
    the work payload for the ready cascade, `terminal_envelope` the
    DDB-typed `STAGE#onboarding#cwid` row for the two non-ready writers.
    The unused key is an empty dict; the state machine only dereferences the
    one the taken branch needs.
    """
    # score_publications is imported here, not at module scope, so a plain
    # `import pipeline_onboarding.orchestrator` stays lightweight — it does
    # not drag in score_publications' scoring dependency tree (tqdm, openai,
    # pymysql/sqlalchemy). That lets the lean onboarding-detector Lambda zip
    # bundle orchestrator.py for its cost-preview chain without the ~30-40 MB
    # of scoring wheels (#80 PR 6 D-DETECTOR-COST option C; #102). Keep this
    # import function-local.
    # onboarding_cost_guard_tripped is the canonical #80-Phase-1 cost-guard
    # decision, reused so the orchestrator (the primary decision point) and
    # `score_publications --pmids` (defense-in-depth) cannot drift;
    # ONBOARDING_COST_GUARD_MAX_PMIDS resolves config/thresholds.json.
    from score_publications import (
        ONBOARDING_COST_GUARD_MAX_PMIDS,
        onboarding_cost_guard_tripped,
    )

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

    def _ready(
        *, net_work_count: int, projected_cost: Decimal, recovery: bool
    ) -> dict[str, Any]:
        """Build the `ready` result — the work payload the Enrich->Score
        cascade consumes.

        `recovery` True marks a #115 re-run: every PMID is already scored
        (net_work_count 0) and the cascade re-executes only to land a rollup
        a prior run left missing or stale. The handler reads the flag to
        word the run's Teams note — a recovery spends no model budget.
        """
        return {
            "status": ROUTE_READY,
            "input": {
                "cwid": cwid,
                "run_id": run_id,
                "started_at": started_at,
                "pmids": pmids,
                "allow_cost_override": allow_cost_override,
                "net_work_count": net_work_count,
                "projected_cost_usd": _cost_display(projected_cost),
                "input_hash": input_hash,
                "recovery": recovery,
            },
            "terminal_envelope": {},
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

    # 2. PROCESSING# checkpoint cull — the net Bedrock/OpenAI-bound work set.
    net_work = [p for p in pmids if processing_status.get(p) != STATUS_COMPLETE]
    if not net_work:
        # Every accepted PMID is scored. Skip only when the CWID's rollup is
        # current; otherwise re-run the cascade (#115). A prior cascade can
        # die after Score and before Rollup — leaving the CWID
        # scored-but-not-rolled-up — and ReCiter can de-attribute a PMID
        # after a rollup; both leave the recorded `input_pmid_set` != the
        # live accepted set. A missing rollup row (None) means a prior run
        # scored these PMIDs but the CWID was never rolled up at all.
        if rollup_input_pmid_set is not None and rollup_input_pmid_set == {
            str(p) for p in pmids
        }:
            return _terminal(
                STATUS_SKIPPED,
                net_work_count=0,
                skip_reason=(
                    f"all {len(pmids)} PMID(s) already scored and rolled "
                    "up — idempotent no-op"
                ),
            )
        # Recovery re-run: net scoring work is 0, so the cost guard is moot
        # (and D8's rollup recomputation bypasses it by design).
        return _ready(
            net_work_count=0, projected_cost=Decimal("0"), recovery=True
        )

    # 3. Cost guard (R5 / D-COSTSTATUS). The orchestrator is the single
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

    # 4. Ready — hand the work payload to the Enrich->Score cascade.
    return _ready(
        net_work_count=len(net_work),
        projected_cost=projected_cost,
        recovery=False,
    )


# Routing status for each terminal workflow status. `cost_exceeded` is a
# routing label distinct from its `failed` record status (D-COSTSTATUS).
_ROUTE_FOR_STATUS = {
    STATUS_SKIPPED: ROUTE_SKIPPED,
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

    # ReciterDB: the CWID's accepted PMID set.
    pmids = get_pmids_for_cwid(cwid)
    # DynamoDB: the PROCESSING# checkpoint for the net-work cull, and the
    # CWID's most recent rollup snapshot for the #115 recovery check.
    processing_status: dict[str, str] = {}
    rollup_pmid_set: set[str] | None = None
    if pmids:
        processing_status = get_processing_status(
            get_dynamo_client(), TABLE_NAME, pmids
        )
        rollup_pmid_set = latest_rollup_pmid_set(get_table(TABLE_NAME), cwid)

    result = evaluate_onboarding(
        cwid=cwid,
        run_id=run_id,
        started_at=started_at,
        allow_cost_override=allow_cost_override,
        pmids=pmids,
        processing_status=processing_status,
        rollup_input_pmid_set=rollup_pmid_set,
        duration_ms=int((time.monotonic() - t0) * 1000),
    )

    # Cost visibility (R5 / R10 #4). A `ready` run emits one informational
    # Teams note (no @-mention — not actionable); a `cost_exceeded` run is
    # surfaced instead by the state machine's NotifyCostExceeded Task, so
    # every ready/cost-exceeded run produces exactly one Teams mention.
    if result["status"] == ROUTE_READY:
        work = result["input"]
        if work.get("recovery"):
            # #115 recovery re-run: every PMID is already scored; the
            # cascade re-runs only to land a rollup a prior run left
            # missing or stale. No model spend — the worded note reflects
            # that so an operator does not read it as a fresh-scoring run.
            logger.info(
                "Onboarding recovery re-run: cwid=%s run_id=%s "
                "(rollup missing or stale)",
                cwid, run_id,
            )
            alerting.alert(
                "WARN",
                f"Onboarding recovery re-run — CWID {cwid}",
                f"A prior onboarding run for CWID {cwid} scored its "
                "publications but did not finish the rollup. Re-running the "
                "cascade to land it — no new scoring, no model cost.",
                {"cwid": cwid, "run_id": run_id, "recovery": True},
                mention=False,
            )
        else:
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

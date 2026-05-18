"""Onboarding Enrich Lambda — the cascade's synopsis + impact stage (#80 / #112).

The first cascade stage after the orchestrator's `ready` route. For the
CWID's accepted PMID set it calls `run_enrichment_backfill` — synopsis +
impact for every PMID still missing either — so the downstream Score stage
finds a synopsis for each.

This replaces the synopsis-precondition check that used to route an
un-synopsized CWID to the (now retired) `deferred` terminal: onboarding
generates the synopses inline instead of waiting for the operator-laptop
daily enrichment job. The spec's R3 always called the precondition a
workaround "until the synopsis pipeline moves to cloud"; this is that.

`run_enrichment_backfill` (#112 / PR #113) is reused unchanged. It is
idempotent — PMIDs that already carry synopsis + impact are culled, so an
onboarding re-run does no duplicate LLM work — and partial-tolerant: a
per-PMID failure does not abort the stage. The cascade proceeds to Score
regardless; a PMID whose enrichment failed simply has no synopsis, Score
skips it, and `finalize` counts it as a gap (the run ends `partial`). Only
a Lambda-level exception routes — via the ASL Catch — to the failed
onboarding terminal.

The Lambda entry point is `handler(event, context)`. It maps the
`EnrichmentBackfillResult` to a `STAGE#enrichment_backfill#cwid:{cwid}`
record and returns it DDB-attribute-typed for the state machine's
`WriteEnrichStageRow` `dynamodb:putItem` to persist.
"""

from __future__ import annotations

import logging
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

# Ensure repo root is importable regardless of cwd.
sys.path.insert(0, str(Path(__file__).parent.parent))

# Populate DB_* + OPENAI_API_KEY from Secrets Manager when running in
# Lambda. Must precede the first touch of utils.sql_queries / utils.db /
# utils.openai_client so the engine + OpenAI client factories find creds.
import utils.secrets_loader  # noqa: F401

from pipeline_common.envelope import to_ddb_typed_envelope
from pipeline_enrichment.daily_job import (
    STATUS_DDB_BATCH_FAILED,
    STATUS_FAILED,
    STATUS_NO_OP,
    run_enrichment_backfill,
)
from utils.db import get_engine
from utils.iso_clock import now_iso
from utils.stage_records import (
    build_complete_record,
    build_failed_record,
    build_skipped_record,
    compute_input_hash,
)

logger = logging.getLogger(__name__)

# STAGE# stage name for the enrich-stage audit row —
# PK = STAGE#enrichment_backfill#cwid:{cwid}.
ENRICH_STAGE = "enrichment_backfill"

_ZERO_USD = Decimal("0")


def _silent_alert(*args: Any, **kwargs: Any) -> bool:
    """No-op alert for run_enrichment_backfill inside the cascade.

    The onboarding workflow's own `finalize` notification (spec R10) is the
    single operator signal for a run; the enrich stage does not fire a
    separate Teams message. The enrich outcome is on the
    `STAGE#enrichment_backfill#cwid` row for anyone investigating.
    """
    return False


def handler(event: dict, context: Any = None) -> dict:
    """Enrich Task — run synopsis + impact for the CWID's PMID set.

    Expected event (from the ASL `Enrich` Task `InputPath: $.orchestrate.input`):
        {"cwid": "abc1234", "pmids": ["...", ...], ...}

    Returns the DDB attribute-typed `STAGE#enrichment_backfill#cwid:{cwid}`
    row; the state machine's `WriteEnrichStageRow` persists it via `Item.$`.
    A missing `cwid` is an operator/wiring error and raises (the state
    machine's Catch writes the failed onboarding row).
    """
    cwid = (event.get("cwid") or "").strip()
    if not cwid:
        raise ValueError(
            "enrich handler: event has no 'cwid'; the onboarding state "
            "machine populates it from $.orchestrate.input.cwid"
        )
    pmids = [str(p) for p in event.get("pmids") or []]

    started_at = now_iso()
    t0 = time.monotonic()
    scope = f"cwid:{cwid}"
    input_hash = compute_input_hash(
        ENRICH_STAGE, {"cwid": cwid, "pmids": sorted(set(pmids))}
    )

    # run_enrichment_backfill is idempotent + partial-tolerant; its alerts
    # are silenced because the onboarding workflow owns operator notification.
    result = run_enrichment_backfill(
        pmids=pmids, engine=get_engine(), alert_fn=_silent_alert
    )
    duration_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        "Onboarding enrich: cwid=%s status=%s requested=%s succeeded=%s "
        "failed=%s impact_rows_written=%s",
        cwid, result.status, result.requested, result.succeeded,
        result.failed, result.impact_rows_written,
    )

    common = dict(
        stage=ENRICH_STAGE,
        scope=scope,
        input_hash=input_hash,
        started_at=started_at,
        completed_at=now_iso(),
        duration_ms=duration_ms,
    )
    if result.status == STATUS_NO_OP:
        row = build_skipped_record(
            **common,
            skip_reason=(
                f"all {result.requested} PMID(s) already carry synopsis "
                "+ impact — no enrichment work"
            ),
        )
    elif result.status in (STATUS_FAILED, STATUS_DDB_BATCH_FAILED):
        # The stage failed, but the cascade still proceeds to Score (D3):
        # any PMID that WAS enriched is scoreable, and finalize surfaces the
        # rest as gaps. This failed STAGE# row is the audit signal.
        row = build_failed_record(
            **common,
            cost_observed_usd=result.cost_observed_usd or _ZERO_USD,
            error_code=(
                "EnrichmentImpactBatchFailed"
                if result.status == STATUS_DDB_BATCH_FAILED
                else "EnrichmentFailed"
            ),
            error_message=result.failure_reason or "enrichment failed",
            failure_details={
                "requested": result.requested,
                "succeeded": result.succeeded,
                "failed": result.failed,
            },
        )
    else:  # complete | partial — the stage ran; per-PMID gaps (partial)
        # are recorded in the fields below and surface in the onboarding
        # run's own complete/partial terminal status.
        row = build_complete_record(
            **common,
            cost_observed_usd=result.cost_observed_usd or _ZERO_USD,
            records_written=result.succeeded,
        )

    # Enrich-specific summary, for an operator scanning the STAGE# row.
    row["enrichment_status"] = result.status
    row["requested"] = result.requested
    row["already_complete"] = result.already_complete
    row["unresolved"] = result.unresolved
    row["attempted"] = result.attempted
    row["succeeded"] = result.succeeded
    row["failed"] = result.failed
    row["impact_rows_written"] = result.impact_rows_written or 0
    return to_ddb_typed_envelope(row)

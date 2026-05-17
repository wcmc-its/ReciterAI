"""Onboarding finalize + notify Lambda (#80 Phase 2, PR 3).

This module hosts the two onboarding entry points the state machine drives
after the per-stage cascade — the "finalize/notify Lambda" PLAN §3 left for
PR 3 to decompose. Keeping them in one module (one deploy zip, two Lambda
handlers) avoids a fourth tiny package file:

- `handler` — the **Finalize** Task. The last step of a `ready` run. It
  decides the workflow's terminal status (`complete` vs `partial`) and
  returns the DDB-typed `STAGE#onboarding#cwid` row for the state machine's
  inline `dynamodb:putItem` to persist (D-07 crash-safety: finalize writes
  nothing itself, so a crash routes cleanly to one `failed` row).

- `notify_handler` — the **Notify** Tasks (`NotifyDeferred`,
  `NotifyCostExceeded`, `NotifyFailed`). Sends the operator a Teams alert
  *after* the terminal row is persisted, so the alert reflects the row.

complete-vs-partial is decided from the **PROCESSING# checkpoint** (D-09):
after the Score stage, every PMID in the work set has a `PROCESSING#pmid_*`
row whose status is the canonical per-PMID outcome (`complete` / `failed` /
`quarantined`). The PLAN's shorthand for this was "per-PMID
`STAGE#score_publications#pmid:` outcomes" — but those STAGE# rows are
written for *failures only* and are not cheaply run-correlated, whereas the
PROCESSING# tracker is the per-PMID state store `get_unscored_publications`
itself reads. Finalize runs seconds after Score in the same execution, so
the checkpoint reflects this run.
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

# Ensure repo root is importable regardless of cwd.
sys.path.insert(0, str(Path(__file__).parent.parent))

import utils.secrets_loader  # noqa: F401  — populate DB_*/AWS creds in Lambda

from pipeline_common.envelope import to_ddb_typed_envelope
from pipeline_enrichment import alerting
from pipeline_onboarding import build_onboarding_record
from utils.dynamodb_helpers import (
    TABLE_NAME,
    get_dynamo_client,
    get_processing_status,
)
from utils.iso_clock import now_iso
from utils.stage_records import STATUS_COMPLETE, STATUS_PARTIAL

logger = logging.getLogger(__name__)

# `kind` values for `notify_handler` — one per non-`ready`/non-`complete`
# terminal the state machine routes through a Notify Task.
NOTIFY_DEFERRED = "deferred"
NOTIFY_COST_EXCEEDED = "cost_exceeded"
NOTIFY_FAILED = "failed"

# The four per-stage envelopes finalize receives, in cascade order. Used to
# sum observed cost and record each stage's input_hash on the workflow row.
_STAGE_ENVELOPE_KEYS = (
    "score_envelope",
    "assign_envelope",
    "top_topic_envelope",
    "rollup_envelope",
)


# ---------------------------------------------------------------------------
# DDB-typed envelope readers
# ---------------------------------------------------------------------------


def _ddb_str(envelope: dict, key: str, default: str = "") -> str:
    """Read a string attribute from a DDB attribute-typed envelope."""
    value = envelope.get(key)
    if isinstance(value, dict) and "S" in value:
        return value["S"]
    return default


def _ddb_decimal(envelope: dict, key: str) -> Decimal:
    """Read a numeric attribute from a DDB-typed envelope; 0 when absent."""
    value = envelope.get(key)
    if isinstance(value, dict) and "N" in value:
        try:
            return Decimal(str(value["N"]))
        except Exception:  # noqa: BLE001 — malformed N degrades to zero
            return Decimal("0")
    return Decimal("0")


def _duration_ms(started_at: str) -> int:
    """Milliseconds between the workflow `started_at` and now."""
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return 0
    delta = datetime.now(timezone.utc) - start
    return max(0, int(delta.total_seconds() * 1000))


# ---------------------------------------------------------------------------
# Finalize Task
# ---------------------------------------------------------------------------


def decide_terminal_status(pmids: list[str], processing_status: dict[str, str]):
    """Return (workflow_status, incomplete_pmids) for the finalize decision.

    A PMID is terminal-good iff its PROCESSING# status is `complete`; any
    other value (`failed`, `quarantined`, or absent) is a gap. All PMIDs
    terminal-good -> `complete`; otherwise -> `partial`. Pure — unit-tested.
    """
    incomplete = sorted(
        p for p in pmids if processing_status.get(p) != STATUS_COMPLETE
    )
    status = STATUS_PARTIAL if incomplete else STATUS_COMPLETE
    return status, incomplete


def handler(event: dict, context: Any = None) -> dict:
    """Finalize Task — decide complete|partial, return the workflow row.

    Expected event (from the ASL `Finalize` Task Parameters):
        {
          "cwid": "abc1234",
          "pmids": ["...", ...],
          "run_id": "...", "started_at": "...", "input_hash": "...",
          "net_work_count": 12, "projected_cost_usd": "~$0.41",
          "score_envelope": {<DDB-typed>}, "assign_envelope": {...},
          "top_topic_envelope": {...}, "rollup_envelope": {...}
        }

    Returns the DDB attribute-typed `STAGE#onboarding#cwid` row; the state
    machine's `WriteOnboardingFinal` persists it via `Item.$`.
    """
    cwid = event["cwid"]
    pmids = [str(p) for p in event.get("pmids") or []]
    run_id = event["run_id"]
    started_at = event["started_at"]

    status_map = get_processing_status(get_dynamo_client(), TABLE_NAME, pmids)
    workflow_status, incomplete = decide_terminal_status(pmids, status_map)

    # Sum observed per-stage cost and record each stage's input_hash.
    cost_observed = Decimal("0")
    stage_input_hashes: dict[str, str] = {}
    for env_key in _STAGE_ENVELOPE_KEYS:
        envelope = event.get(env_key) or {}
        cost_observed += _ddb_decimal(envelope, "cost_observed_usd")
        stage = env_key.removesuffix("_envelope")
        ih = _ddb_str(envelope, "input_hash")
        if ih:
            stage_input_hashes[stage] = ih

    logger.info(
        "Onboarding finalize: cwid=%s status=%s pmids=%s incomplete=%s",
        cwid, workflow_status, len(pmids), len(incomplete),
    )

    record_kwargs: dict[str, Any] = {
        "pmid_count": len(pmids),
        "stage_input_hashes": stage_input_hashes,
    }
    if event.get("net_work_count") is not None:
        record_kwargs["net_work_count"] = int(event["net_work_count"])
    if event.get("projected_cost_usd") is not None:
        record_kwargs["projected_cost_usd"] = str(event["projected_cost_usd"])
    if workflow_status == STATUS_PARTIAL:
        record_kwargs["partial_failure_count"] = len(incomplete)
        record_kwargs["failed_pmids"] = incomplete

    row = build_onboarding_record(
        cwid=cwid,
        status=workflow_status,
        started_at=started_at,
        completed_at=now_iso(),
        run_id=run_id,
        input_hash=event.get("input_hash", ""),
        duration_ms=_duration_ms(started_at),
        cost_observed_usd=cost_observed,
        **record_kwargs,
    )

    # Partial runs alert the operator (R10 #2). One partial run finalizes
    # exactly once, so a single alert per partial run is the dedup rule —
    # no extra de-duplication machinery is needed.
    if workflow_status == STATUS_PARTIAL:
        alerting.alert(
            "WARN",
            f"Onboarding completed with gaps — CWID {cwid}",
            f"Onboarding for CWID {cwid} finished, but {len(incomplete)} of "
            f"{len(pmids)} publication(s) did not reach a scored state. "
            "Re-running onboarding for this CWID retries only the gaps "
            "(scoring is idempotent).",
            {
                "cwid": cwid,
                "run_id": run_id,
                "partial_failure_count": len(incomplete),
                "pmid_count": len(pmids),
            },
        )

    return to_ddb_typed_envelope(row)


# ---------------------------------------------------------------------------
# Notify Tasks
# ---------------------------------------------------------------------------


def _override_hint(cwid: str) -> str:
    """Re-run instruction for a cost-guard-tripped CWID.

    Embeds a copy-pasteable `start-execution` command when the state
    machine ARN is in the environment; otherwise a plain instruction.
    """
    import os

    arn = os.environ.get("RECITERAI_ONBOARDING_STATE_MACHINE_ARN", "").strip()
    payload = f'{{"cwid": "{cwid}", "allow_cost_override": true}}'
    if arn:
        return (
            "Re-run with the cost override:\n"
            f"aws stepfunctions start-execution --state-machine-arn {arn} "
            f"--input '{payload}'"
        )
    return (
        "Re-run the onboarding state machine for this CWID with input "
        f"{payload} to proceed."
    )


def notify_handler(event: dict, context: Any = None) -> dict:
    """Notify Task — send the operator a Teams alert for a terminal run.

    Expected event (from an ASL `Notify*` Task Parameters):
        {"kind": "deferred"|"cost_exceeded"|"failed",
         "cwid": "...", "reason": "...", "run_id": "..."}

    Best-effort: `alerting.alert` never raises. Returns whether the webhook
    accepted the post (the state machine ignores the result).
    """
    kind = event.get("kind", NOTIFY_FAILED)
    cwid = event.get("cwid", "?")
    reason = event.get("reason", "")
    run_id = event.get("run_id", "")
    context_block = {"cwid": cwid, "run_id": run_id}

    if kind == NOTIFY_DEFERRED:
        severity, title = "WARN", f"Onboarding deferred — CWID {cwid}"
        message = (
            f"Onboarding for CWID {cwid} was deferred: {reason} "
            "Re-run onboarding for this CWID once the synopses are backfilled."
        )
    elif kind == NOTIFY_COST_EXCEEDED:
        severity, title = "WARN", f"Onboarding cost guard tripped — CWID {cwid}"
        message = (
            f"Onboarding for CWID {cwid} stopped at the cost guard: {reason} "
            + _override_hint(cwid)
        )
    else:  # NOTIFY_FAILED
        severity, title = "ERROR", f"Onboarding workflow failed — CWID {cwid}"
        message = (
            f"The onboarding state machine failed for CWID {cwid}. "
            f"Cause: {reason}"
        )

    logger.info("Onboarding notify: kind=%s cwid=%s", kind, cwid)
    accepted = alerting.alert(severity, title, message, context_block)
    return {"notified": bool(accepted), "kind": kind, "cwid": cwid}

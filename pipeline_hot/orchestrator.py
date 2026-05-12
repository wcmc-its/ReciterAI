"""Hot-path orchestrator: state-machine entrypoint.

Responsibilities:

1. Resolve `last_successful_hot_run_at` by querying the latest
   `STAGE#hot_run#GLOBAL` `complete` row (D-06).
2. Compute the delta PMID set by asking ReciterDB for publications
   added or modified since that timestamp.
3. Detect a concurrent hot-path execution (per Open Q5). If one is
   running, persist a `STAGE#hot_run#GLOBAL` `skipped` row with
   `skip_reason: "prior_run_in_progress"` and short-circuit.
4. Return the initial state-machine input: the delta PMID set, the
   resolved cutoff, and the run identifiers the downstream handlers
   need to compose their envelopes.

The Lambda entry point is `handler(event, context)` and is registered
in `infra/eventbridge.json` (T12) as the target of the weekly cron
rule. It is invoked by Step Functions as the first Task state of
`state_machine.asl.json`.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure repo root is importable regardless of cwd
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.dynamodb_helpers import get_table, TABLE_NAME
from utils.stage_records import (
    STATUS_COMPLETE,
    build_skipped_record,
    write_skipped,
)

logger = logging.getLogger(__name__)

HOT_RUN_STAGE = "hot_run"
HOT_RUN_SCOPE = "GLOBAL"
SKIP_REASON_LOCKED = "prior_run_in_progress"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# D-06: last successful hot run
# ---------------------------------------------------------------------------


def resolve_last_successful_hot_run(table: Any) -> str | None:
    """Return the `started_at` of the most recent `STAGE#hot_run#GLOBAL`
    `complete` row, or None if no such row exists.

    Implementation per D-06: ScanIndexForward=false, walk newest first,
    return the first complete row encountered. We do not stop at
    skipped/failed rows because the question is "when did we last
    *successfully* process the corpus", not "when did we last run".
    """
    pk = f"STAGE#{HOT_RUN_STAGE}#{HOT_RUN_SCOPE}"
    resp = table.query(
        KeyConditionExpression="#pk = :pk",
        ExpressionAttributeNames={"#pk": "PK"},
        ExpressionAttributeValues={":pk": pk},
        ScanIndexForward=False,
    )
    for item in resp.get("Items", []):
        if item.get("status") == STATUS_COMPLETE:
            return item.get("started_at")
    return None


# ---------------------------------------------------------------------------
# Delta PMID resolution
# ---------------------------------------------------------------------------


# Bootstrap fallback when no prior successful hot run exists. The cold
# path is the right way to populate the corpus from scratch; the hot
# path should never be asked to score "all of history" via cron. We
# refuse to fall back further than this look-back window to keep the
# delta-set query bounded.
_BOOTSTRAP_LOOKBACK_DAYS = 14


def resolve_delta_pmids(
    last_run_at: str | None,
    *,
    query_fn=None,
) -> list[str]:
    """Resolve the delta PMID set: new publications since `last_run_at`.

    `query_fn(since_iso) -> list[str]` is injected for testability — in
    production it queries ReciterDB; in tests it is mocked. The query
    contract: return PMIDs added or modified at or after `since_iso`.

    When `last_run_at` is None (no prior successful hot run), we look
    back `_BOOTSTRAP_LOOKBACK_DAYS` rather than asking ReciterDB for
    the entire corpus — the cold path owns full-corpus runs.
    """
    if query_fn is None:
        raise ValueError(
            "resolve_delta_pmids requires a query_fn; production wires this to "
            "ReciterDB. Tests should inject a stub."
        )

    if last_run_at is None:
        # Bootstrap window. We refuse to fall back further to keep
        # delta-set sizes bounded for state-machine cost.
        lookback = datetime.now(timezone.utc).replace(microsecond=0)
        lookback = lookback.replace(
            day=max(1, lookback.day - _BOOTSTRAP_LOOKBACK_DAYS)
        )
        since_iso = lookback.isoformat(timespec="seconds").replace("+00:00", "Z")
        logger.warning(
            "No prior successful hot run found; bootstrapping with "
            f"{_BOOTSTRAP_LOOKBACK_DAYS}-day lookback (since={since_iso}). "
            "If the corpus is empty, run the cold path first."
        )
    else:
        since_iso = last_run_at

    pmids = list(query_fn(since_iso))
    # Deduplicate + stringify defensively.
    return sorted({str(p) for p in pmids})


# ---------------------------------------------------------------------------
# Open Q5: prior-run-in-progress lock
# ---------------------------------------------------------------------------


def is_state_machine_running(
    sfn_client: Any,
    *,
    state_machine_arn: str,
    self_execution_arn: str | None = None,
) -> bool:
    """Return True iff any execution OTHER THAN this one is currently RUNNING.

    Open Q5 resolution: skip-with-warn. The orchestrator is invoked as
    the first Task of the very state machine it's checking, so it must
    exclude its own execution from the conflict check.
    """
    resp = sfn_client.list_executions(
        stateMachineArn=state_machine_arn,
        statusFilter="RUNNING",
    )
    for execution in resp.get("executions", []):
        if execution.get("executionArn") != self_execution_arn:
            return True
    return False


def write_skipped_hot_run_locked(
    table: Any,
    *,
    started_at: str,
    duration_ms: int,
    input_hash: str = "",
) -> dict:
    """Emit the `STAGE#hot_run#GLOBAL` skipped row for the lock-collision case.

    Carries `skip_reason: "prior_run_in_progress"` so the drift evaluator
    can decide whether collisions are frequent enough to revisit Open Q5.
    """
    return write_skipped(
        table,
        stage=HOT_RUN_STAGE,
        scope=HOT_RUN_SCOPE,
        input_hash=input_hash or "lock-collision",
        skip_reason=SKIP_REASON_LOCKED,
        started_at=started_at,
        completed_at=_now_iso(),
        duration_ms=duration_ms,
    )


# ---------------------------------------------------------------------------
# Initial state-machine input shape
# ---------------------------------------------------------------------------


def build_state_machine_input(
    *,
    pmids: list[str],
    last_run_at: str | None,
    started_at: str,
    run_id: str,
) -> dict:
    """Produce the dict the state machine's first Task receives.

    Downstream Task states read this via JSONPath to compose their
    envelopes. Kept small: the corpus itself stays in DynamoDB / S3;
    we pass identifiers, not bytes.
    """
    return {
        "run_id": run_id,
        "started_at": started_at,
        "last_successful_hot_run_at": last_run_at,
        "delta": {
            "pmids": pmids,
            "size": len(pmids),
        },
        "trace": {
            "orchestrator_version": "0.1.0",
        },
    }


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------


def handler(event: dict, context: Any = None) -> dict:
    """Orchestrator Lambda handler.

    Expected event:
        {
          "state_machine_arn": "arn:aws:states:...:stateMachine:reciterai-hot",
          "execution_arn":     "arn:aws:states:...:execution:reciterai-hot:...",
          "run_id":            "2026-05-13T12:00:00Z-abcdef"
        }

    Returns either:
        {"status": "ready", "input": <state_machine_input>}  — proceed to Score
        {"status": "skipped", "skip_reason": "prior_run_in_progress"} — short-circuit
    """
    started_at = _now_iso()
    state_machine_arn = event.get("state_machine_arn") or os.environ.get(
        "RECITERAI_HOT_STATE_MACHINE_ARN"
    )
    self_execution_arn = event.get("execution_arn")
    run_id = event.get("run_id") or started_at

    table = get_table(TABLE_NAME)

    # 1. Lock check.
    if state_machine_arn:
        import boto3

        sfn = boto3.client("stepfunctions")
        if is_state_machine_running(
            sfn,
            state_machine_arn=state_machine_arn,
            self_execution_arn=self_execution_arn,
        ):
            write_skipped_hot_run_locked(
                table, started_at=started_at, duration_ms=0
            )
            logger.warning(
                "Hot path skipped — prior execution still RUNNING. "
                f"state_machine_arn={state_machine_arn}"
            )
            return {
                "status": "skipped",
                "skip_reason": SKIP_REASON_LOCKED,
                "started_at": started_at,
            }

    # 2. Resolve last successful hot run + delta PMID set.
    last_run_at = resolve_last_successful_hot_run(table)
    from utils.sql_queries import get_db_connection  # local import: tests stub

    def _ddb_to_pmid_query(since_iso: str) -> list[str]:
        # Production query stub. The exact SQL depends on the ReciterDB
        # schema column that flags "added since"; encoded here as a
        # parameterized query the operator will wire to the right column
        # at deploy time. Kept minimal in this phase — T8 / cold path
        # exercises the same query path.
        from sqlalchemy import text

        conn = get_db_connection()
        try:
            sql = text(
                "SELECT DISTINCT pmid FROM analysis_summary_article "
                "WHERE dateLastModified >= :since "
                "ORDER BY pmid DESC"
            )
            return [str(row[0]) for row in conn.execute(sql, {"since": since_iso})]
        finally:
            conn.close()

    pmids = resolve_delta_pmids(last_run_at, query_fn=_ddb_to_pmid_query)

    return {
        "status": "ready",
        "input": build_state_machine_input(
            pmids=pmids,
            last_run_at=last_run_at,
            started_at=started_at,
            run_id=run_id,
        ),
    }

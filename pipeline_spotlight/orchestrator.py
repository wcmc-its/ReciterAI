"""Spotlight orchestrator: the monthly scheduled spotlight refresh.

Runs as an ECS Fargate task (`python -m pipeline_spotlight.orchestrator`,
EventBridge rule `reciterai-spotlight-monthly` — #329 shape a2: the regen
shells out to the full `cli.backfill_spotlight --publish`, which needs the
whole repo + LLM deps and does not fit Lambda's 15-minute ceiling).
`handler(event, context)` is retained as the callable seam the tests and any
future Lambda-shaped invoker use.

Flow:
1. Resolve `last_successful_spotlight_at` from the latest
   `STAGE#spotlight_refresh#GLOBAL` `complete` row.
2. Query STAGE# rows newer than that cutoff for score_publications +
   assign_subtopics complete rows to discover landed-since PMIDs.
3. Resolve each new PMID's primary_subtopic_id from its activity row
   (the production query path; injectable for tests).
4. Load the top-50 from `spotlight.pool_ranker.rank_pool`.
5. Call `dirty_gate.evaluate_gate`.
6. Below threshold → write a `skipped` STAGE# row + return early.
7. At-or-above → shell out to `backfill_spotlight.py --publish` and
   write a `complete` row.

The orchestrator delegates the heavy lifting to small, testable
seams (lambda-friendly):
- `resolve_last_spotlight_complete(table)` — D-06-style query.
- `resolve_assignments_since(...)` — DDB query for STAGE# rows since
  cutoff; in tests this is mocked.
- `evaluate_gate` — pure function in `dirty_gate.py`.
- `invoke_backfill_spotlight(...)` — subprocess wrapper around
  `backfill_spotlight.py --publish`, separated so tests can stub it.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

# Ensure repo root is importable regardless of cwd
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.dynamodb_helpers import get_table, TABLE_NAME
from utils.event_records import load_thresholds
from utils.stage_records import (
    STATUS_COMPLETE,
    compute_input_hash,
    write_complete,
    write_failed,
    write_skipped,
)

from pipeline_spotlight.dirty_gate import GateResult, evaluate_gate
from pipeline_common import alert
from utils.iso_clock import now_iso

logger = logging.getLogger(__name__)

SPOTLIGHT_STAGE = "spotlight_refresh"
SPOTLIGHT_SCOPE = "GLOBAL"
SPOTLIGHT_COST_USD = Decimal("0")  # the actual pipeline cost is recorded on
                                    # the per-stage scripts the orchestrator invokes




# ---------------------------------------------------------------------------
# Substrate query seams
# ---------------------------------------------------------------------------


def resolve_last_spotlight_complete(table: Any) -> str | None:
    """Return the started_at of the most recent
    STAGE#spotlight_refresh#GLOBAL complete row, or None."""
    pk = f"STAGE#{SPOTLIGHT_STAGE}#{SPOTLIGHT_SCOPE}"
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


def resolve_new_pmid_assignments(
    table: Any,
    *,
    since_iso: str | None,
) -> dict[str, list[str]]:
    """Discover (pmid -> [subtopic_id, ...]) for PMIDs whose TOPIC#
    activity rows landed since `since_iso`.

    Uses the same scan pattern as `spotlight.pool_ranker.rank_pool`
    (FilterExpression `begins_with(PK, "TOPIC#")`) but additionally
    filters on `created_at >= since_iso` to scope to landed-since rows.
    `since_iso=None` means "no prior spotlight" — return everything
    (caller will likely regen unconditionally).

    The returned mapping is what `evaluate_gate` consumes.
    """
    pk_prefix = "TOPIC#"
    kwargs: dict[str, Any] = {
        "FilterExpression": "begins_with(#pk, :p)",
        "ExpressionAttributeNames": {"#pk": "PK"},
        "ExpressionAttributeValues": {":p": pk_prefix},
    }
    if since_iso:
        kwargs["FilterExpression"] += " AND created_at >= :since"
        kwargs["ExpressionAttributeValues"][":since"] = since_iso

    assignments: dict[str, list[str]] = {}
    last_key = None
    while True:
        if last_key is not None:
            kwargs["ExclusiveStartKey"] = last_key
        resp = table.scan(**kwargs)
        for item in resp.get("Items", []):
            pmid = item.get("pmid")
            subtopic_id = item.get("primary_subtopic_id")
            if not pmid or not subtopic_id:
                continue
            assignments.setdefault(str(pmid), []).append(str(subtopic_id))
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
    return assignments


def resolve_top_subtopic_ids(
    *,
    pool_size: int = 50,
    rank_pool_fn: Callable | None = None,
) -> list[str]:
    """Return the top-N subtopic_ids from `spotlight.pool_ranker.rank_pool`.

    `rank_pool_fn` is injected for testability and defaults to the
    production ranker at call time.
    """
    if rank_pool_fn is None:
        from spotlight.pool_ranker import rank_pool as rank_pool_fn  # type: ignore
    entries = rank_pool_fn(pool_size=pool_size)
    return [getattr(e, "subtopic_id", None) or e["subtopic_id"] for e in entries]


# ---------------------------------------------------------------------------
# Spotlight regen invocation
# ---------------------------------------------------------------------------


REPO_ROOT = Path(__file__).resolve().parent.parent


def invoke_backfill_spotlight(*, runner: Callable | None = None) -> int:
    """Shell out to `backfill_spotlight.py --publish`.

    `runner` is injected for testability and falls back to
    `subprocess.run` at call time (so monkeypatch on subprocess.run
    intercepts the call from main()).
    """
    invoker = runner if runner is not None else subprocess.run
    proc = invoker(
        [sys.executable, "-m", "cli.backfill_spotlight", "--publish"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"backfill_spotlight exited {proc.returncode}: "
            f"stderr={proc.stderr[-2000:]}"
        )
    return proc.returncode


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


def _compose_input_hash(
    *, last_complete_at: str | None, gate: GateResult
) -> str:
    return compute_input_hash(
        SPOTLIGHT_STAGE,
        {
            "last_complete_at": last_complete_at or "<none>",
            "new_pmid_count": gate.new_pmid_count,
            "dirty_subtopics": gate.dirty_subtopics,
        },
    )


def run_gate(
    *,
    table: Any,
    new_pmid_assignments: dict[str, list[str]],
    top_subtopic_ids: list[str],
    thresholds: dict[str, Any],
    spotlight_runner: Callable | None = None,
) -> dict[str, Any]:
    """Pure-ish orchestration: applies the gate, persists the STAGE# row,
    and (if dirty) invokes spotlight regen.

    Separated from `handler` so tests can call this directly with
    synthetic STAGE# corpora without faking the full Lambda event shape.
    """
    started_at = now_iso()
    t_start = time.monotonic()

    min_dirty = int(thresholds["spotlight_dirty_subtopic_min"])
    min_pubs = int(thresholds["spotlight_dirty_pubs_per_subtopic_min"])

    last_complete_at = resolve_last_spotlight_complete(table)

    gate = evaluate_gate(
        new_pmid_assignments=new_pmid_assignments,
        top_subtopic_ids=top_subtopic_ids,
        min_dirty_subtopics=min_dirty,
        min_pubs_per_subtopic=min_pubs,
    )
    input_hash = _compose_input_hash(last_complete_at=last_complete_at, gate=gate)

    if not gate.should_regen:
        duration_ms = int((time.monotonic() - t_start) * 1000)
        write_skipped(
            table,
            stage=SPOTLIGHT_STAGE,
            scope=SPOTLIGHT_SCOPE,
            input_hash=input_hash,
            skip_reason=gate.reason(min_dirty=min_dirty, min_pubs_per=min_pubs),
            started_at=started_at,
            completed_at=now_iso(),
            duration_ms=duration_ms,
        )
        return {
            "status": "skipped",
            "reason": gate.reason(min_dirty=min_dirty, min_pubs_per=min_pubs),
            "new_pmid_count": gate.new_pmid_count,
            "dirty_subtopics": gate.dirty_subtopics,
        }

    # Dirty enough to regen.
    try:
        invoke_backfill_spotlight(runner=spotlight_runner)
    except Exception as exc:
        duration_ms = int((time.monotonic() - t_start) * 1000)
        write_failed(
            table,
            stage=SPOTLIGHT_STAGE,
            scope=SPOTLIGHT_SCOPE,
            input_hash=input_hash,
            error_code=type(exc).__name__,
            error_message=str(exc)[:1000],
            started_at=started_at,
            completed_at=now_iso(),
            duration_ms=duration_ms,
            cost_observed_usd=SPOTLIGHT_COST_USD,
            failure_details={
                "dirty_subtopics": gate.dirty_subtopics,
                "new_pmid_count": gate.new_pmid_count,
            },
        )
        raise

    duration_ms = int((time.monotonic() - t_start) * 1000)
    write_complete(
        table,
        stage=SPOTLIGHT_STAGE,
        scope=SPOTLIGHT_SCOPE,
        input_hash=input_hash,
        started_at=started_at,
        completed_at=now_iso(),
        duration_ms=duration_ms,
        cost_observed_usd=SPOTLIGHT_COST_USD,
        records_written=len(gate.dirty_subtopics),
    )
    return {
        "status": "complete",
        "regenerated": True,
        "dirty_subtopics": gate.dirty_subtopics,
        "new_pmid_count": gate.new_pmid_count,
    }


def handler(event: dict, context: Any = None) -> dict:
    """Lambda entry point for the monthly spotlight cron.

    Event (all fields optional; cron invocation passes none of them):
        {
          "new_pmid_assignments": {...},   # test injection only
          "top_subtopic_ids":     [...],   # test injection only
          "force":                false    # if true, bypass the dirty gate
        }

    Production path:
      1. Resolve last spotlight complete -> since_iso.
      2. Scan TOPIC# rows since since_iso -> {pmid: [subtopic_id,...]}.
      3. Rank the top-50 via spotlight.pool_ranker.
      4. Run the dirty gate; persist STAGE# row.

    Failures route to alert.dispatch(severity='ERROR', open_issue=True)
    per D-11. Successful gate skips and successful regens are silent;
    operators inspect the STAGE# row via the dashboard.
    """
    table = get_table(TABLE_NAME)
    thresholds = load_thresholds()

    new_pmid_assignments = event.get("new_pmid_assignments")
    top_subtopic_ids = event.get("top_subtopic_ids")

    if new_pmid_assignments is None:
        last_complete_at = resolve_last_spotlight_complete(table)
        new_pmid_assignments = resolve_new_pmid_assignments(
            table, since_iso=last_complete_at
        )
    if top_subtopic_ids is None:
        top_subtopic_ids = resolve_top_subtopic_ids()

    try:
        return run_gate(
            table=table,
            new_pmid_assignments=new_pmid_assignments,
            top_subtopic_ids=top_subtopic_ids,
            thresholds=thresholds,
        )
    except Exception as exc:
        # run_gate already writes the failed STAGE# row before re-raising.
        # Surface the failure to operators per D-11.
        alert.dispatch(
            "ERROR",
            f"Spotlight refresh failed: {type(exc).__name__}",
            {
                "source": "pipeline_spotlight.orchestrator",
                "error_type": type(exc).__name__,
                "error_message": str(exc)[:500],
                "new_pmid_count": len(new_pmid_assignments),
                "top_subtopic_count": len(top_subtopic_ids),
            },
            open_issue=True,
        )
        raise


def main() -> int:
    """Entry point for the scheduled ECS task (and hand runs).

    Both gate outcomes — skipped and complete — are successes (exit 0).
    On failure, handler() has already written the failed STAGE# row and
    dispatched the ERROR Teams alert before the exception reaches here,
    so this only records the traceback and returns non-zero.
    """
    logging.basicConfig(level=logging.INFO)
    try:
        result = handler({})
    except Exception:
        logger.exception("spotlight orchestrator failed")
        return 1
    print(json.dumps(result, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

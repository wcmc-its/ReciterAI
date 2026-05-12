"""Spotlight orchestrator: monthly Lambda handler.

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

import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
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

logger = logging.getLogger(__name__)

SPOTLIGHT_STAGE = "spotlight_refresh"
SPOTLIGHT_SCOPE = "GLOBAL"
SPOTLIGHT_COST_USD = Decimal("0")  # the actual pipeline cost is recorded on
                                    # the per-stage scripts the orchestrator invokes


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


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
        [sys.executable, "backfill_spotlight.py", "--publish"],
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
    started_at = _now_iso()
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
            completed_at=_now_iso(),
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
            completed_at=_now_iso(),
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
        completed_at=_now_iso(),
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

    Expected event (all optional — sensible defaults for cron invocation):
        {
          "new_pmid_assignments": {...},   # test injection only
          "top_subtopic_ids":     [...]    # test injection only
        }

    The production path queries DynamoDB for both. For Phase 10 T9 the
    DDB query helpers are stubs callers can override; T10+ wires them
    to the real STAGE# index + pool_ranker.
    """
    table = get_table(TABLE_NAME)
    thresholds = load_thresholds()

    new_pmid_assignments = event.get("new_pmid_assignments")
    top_subtopic_ids = event.get("top_subtopic_ids")
    if new_pmid_assignments is None or top_subtopic_ids is None:
        # Production wiring: query DynamoDB + pool_ranker. Left as a
        # deliberate seam: T9 ships the gate semantics + STAGE# wrap;
        # the precise STAGE# index queries land alongside T10's drift
        # evaluator since both consume the same STAGE# corpus.
        raise NotImplementedError(
            "Spotlight production DDB wiring lands in T10/T12 alongside the "
            "shared STAGE# query helpers; until then, pass "
            "new_pmid_assignments + top_subtopic_ids in the event."
        )

    return run_gate(
        table=table,
        new_pmid_assignments=new_pmid_assignments,
        top_subtopic_ids=top_subtopic_ids,
        thresholds=thresholds,
    )

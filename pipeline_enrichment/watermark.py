"""Watermark read/write for the daily enrichment job (#37 step 2).

Single DDB item tracking the last successful daily-enrichment run. The job
queries the delta of PMIDs since the last successful watermark; failed runs
leave the successful watermark untouched so the next run retries the same
delta. Writes are idempotent (PutItem overwrite).

DDB key:
    PK = WATERMARK#daily_enrichment
    SK = STATE

Attributes:
    last_successful_run_at      ISO 8601 (Z)         — last cleanly-finished run
    last_successful_max_pmid    int                  — max pmid covered by that run
    last_run_started_at         ISO 8601 (Z)         — most recent attempt start
    last_run_status             "complete"|"failed"|"in_progress"
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from utils.dynamodb_helpers import get_table

PK = "WATERMARK#daily_enrichment"
SK = "STATE"

STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETE = "complete"
STATUS_FAILED = "failed"


@dataclass(frozen=True)
class Watermark:
    last_successful_run_at: Optional[str]
    last_successful_max_pmid: Optional[int]
    last_run_started_at: Optional[str]
    last_run_status: Optional[str]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_watermark(table=None) -> Optional[Watermark]:
    """Return the current watermark, or None if no run has ever completed."""
    t = table or get_table()
    resp = t.get_item(Key={"PK": PK, "SK": SK})
    item = resp.get("Item")
    if not item:
        return None
    max_pmid = item.get("last_successful_max_pmid")
    return Watermark(
        last_successful_run_at=item.get("last_successful_run_at"),
        last_successful_max_pmid=int(max_pmid) if max_pmid is not None else None,
        last_run_started_at=item.get("last_run_started_at"),
        last_run_status=item.get("last_run_status"),
    )


def mark_run_started(table=None) -> str:
    """Stamp the start of a new run. Returns the timestamp written.

    Preserves last_successful_* attributes so a failed-then-retried run still
    knows its delta starting point.
    """
    t = table or get_table()
    started_at = _now_iso()
    t.update_item(
        Key={"PK": PK, "SK": SK},
        UpdateExpression=(
            "SET last_run_started_at = :started, last_run_status = :status"
        ),
        ExpressionAttributeValues={
            ":started": started_at,
            ":status": STATUS_IN_PROGRESS,
        },
    )
    return started_at


def mark_run_complete(max_pmid: int, table=None) -> str:
    """Commit a successful run. Advances last_successful_max_pmid.

    Returns the timestamp written. Caller passes the max pmid covered by the
    run; on the next run, the delta query filters `pmid > max_pmid`.
    """
    t = table or get_table()
    run_at = _now_iso()
    t.update_item(
        Key={"PK": PK, "SK": SK},
        UpdateExpression=(
            "SET last_successful_run_at = :run_at, "
            "last_successful_max_pmid = :max_pmid, "
            "last_run_status = :status"
        ),
        ExpressionAttributeValues={
            ":run_at": run_at,
            ":max_pmid": int(max_pmid),
            ":status": STATUS_COMPLETE,
        },
    )
    return run_at


def mark_run_failed(table=None) -> None:
    """Record failure without advancing last_successful_*.

    Next run will retry the same delta.
    """
    t = table or get_table()
    t.update_item(
        Key={"PK": PK, "SK": SK},
        UpdateExpression="SET last_run_status = :status",
        ExpressionAttributeValues={":status": STATUS_FAILED},
    )

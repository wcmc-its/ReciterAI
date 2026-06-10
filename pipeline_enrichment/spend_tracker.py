"""Cumulative annual spend tracker for the daily enrichment job (#37).

Maintains a per-calendar-year DynamoDB counter and posts a Teams alert each
time cumulative spend crosses another ``$increment`` boundary — incremental
budget visibility against the ~$400/yr ceiling, not a single binary alarm.

Counter item (in the shared ``reciterai`` table)::

    PK: SPEND#daily_enrichment
    SK: YEAR#<yyyy>            calendar-year window; a new year = a fresh SK
                              with a $0 counter and no notified threshold, so
                              the budget self-resets on Jan 1 (no cron/cleanup)
    cumulative_usd            Decimal, ADD-incremented atomically per run
    runs_counted              int, ADD-incremented per run
    last_notified_threshold   int, highest $increment multiple already alerted
    year, updated_at

Spend on FAILED runs is counted too — the LLM calls cost money regardless of
whether the watermark advanced.
"""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from pipeline_enrichment import alerting
from utils.dynamodb_helpers import get_table
from utils.env_check import load_thresholds
from utils.iso_clock import now_iso

logger = logging.getLogger(__name__)

SPEND_PK = "SPEND#daily_enrichment"
DEFAULT_INCREMENT_USD = Decimal("100")
ANNUAL_CEILING_USD = Decimal("400")


def _increment_usd() -> Decimal:
    """The $boundary between alerts. Config knob ``spend_alert_increment_usd``
    (default 100); falls back to the default on any read error."""
    try:
        return Decimal(str(load_thresholds().get("spend_alert_increment_usd", 100)))
    except Exception:
        return DEFAULT_INCREMENT_USD


def record_spend(
    cost_usd: Decimal,
    *,
    started_at: str | None = None,
    run_id: str | None = None,
    table: Any = None,
    alert_fn: Callable = alerting.alert,
) -> None:
    """Add ``cost_usd`` to the current calendar year's cumulative counter and
    fire a WARN alert when cumulative spend crosses a new ``$increment``
    boundary.

    Exactly-once per boundary: the notified high-water mark is advanced with a
    conditional write, so concurrent/retried runs never double-alert. A single
    large run (e.g. an annual ``--full`` rescore) may jump several boundaries at
    once; the alert names the highest one reached.

    Best-effort: never raises. A tracker failure must not fail an
    otherwise-successful enrichment run.
    """
    try:
        cost = Decimal(str(cost_usd))
    except (InvalidOperation, ValueError, TypeError):
        return
    if cost <= 0:
        return

    try:
        year = (started_at or now_iso())[:4]
        sk = f"YEAR#{year}"
        increment = _increment_usd()
        tbl = table if table is not None else get_table()

        # Atomic increment + read-back in one round-trip.
        resp = tbl.update_item(
            Key={"PK": SPEND_PK, "SK": sk},
            UpdateExpression=(
                "ADD cumulative_usd :c, runs_counted :one "
                "SET updated_at = :u, #yr = :year"
            ),
            ExpressionAttributeNames={"#yr": "year"},
            ExpressionAttributeValues={
                ":c": cost,
                ":one": 1,
                ":u": now_iso(),
                ":year": year,
            },
            ReturnValues="ALL_NEW",
        )
        attrs = resp.get("Attributes", {})
        new_total = Decimal(str(attrs.get("cumulative_usd", cost)))
        last_notified = int(attrs.get("last_notified_threshold", 0) or 0)

        new_multiple = int(new_total // increment) * int(increment)
        if new_multiple <= last_notified:
            return

        # Advance the notified high-water mark idempotently. If a concurrent run
        # already moved it past :nm the condition fails and we skip the alert —
        # the boundary was (or will be) announced exactly once.
        try:
            tbl.update_item(
                Key={"PK": SPEND_PK, "SK": sk},
                UpdateExpression="SET last_notified_threshold = :nm",
                ConditionExpression=(
                    "attribute_not_exists(last_notified_threshold) "
                    "OR last_notified_threshold < :nm"
                ),
                ExpressionAttributeValues={":nm": new_multiple},
            )
        except Exception:
            return

        alert_fn(
            "WARN",
            f"ReciterAI enrichment spend crossed ${new_multiple}",
            f"Cumulative {year} enrichment spend is ${new_total:.2f}, past the "
            f"${new_multiple} mark (annual ceiling ~${ANNUAL_CEILING_USD}). A "
            f"single run may cross more than one ${int(increment)} boundary; "
            f"this names the highest reached.",
            context={
                "year": year,
                "cumulative_usd": str(new_total),
                "threshold_crossed": new_multiple,
                "increment_usd": str(increment),
                "run_id": run_id,
            },
        )
    except Exception as e:  # noqa: BLE001 — best-effort, must never fail the run
        logger.warning(
            "spend_tracker: failed to record spend (best-effort): %s", e
        )

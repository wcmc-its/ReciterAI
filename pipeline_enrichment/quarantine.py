"""Per-PMID quarantine for the daily enrichment job (#137).

The daily job advances its watermark on all-or-nothing success. Correct for
transient flakes, pathological for content-specific failures: a PMID that
consistently breaks a Bedrock call stalls the entire delta forever, every
tick burning LLM cost without progress (see PR #136 + the 2026-05-20 11:00
UTC incident on PMID 42119587).

This module tracks per-PMID consecutive failure counts so the daily job can
treat a PMID as a write-off after N failures — the all-or-nothing gate
ignores it, the watermark advances past it, and the operator gets a Teams
ERROR card listing the quarantined PMIDs for triage.

Schema (deliberately distinct from the scoring pipeline's `QUARANTINE#` rows
in `utils/dynamodb_helpers.py` — that one carries `taxonomy_version` + a
companion `PROCESSING#` checkpoint, which enrichment doesn't have):

    PK = ENRICHMENT_QUARANTINE#pmid_{pmid}
    SK = STATUS

    pmid                     str
    consecutive_failures     int            ADD-incremented per failure
    last_failure_at          ISO 8601 (Z)
    last_failure_reason      str (≤ 1000 chars — truncated)
    last_attempted_run_id    str            the most recent daily-job run UUID
    created_at               ISO 8601 (Z)   set on first failure, never updated
"""

from __future__ import annotations

from typing import Optional

from botocore.exceptions import ClientError

from utils.dynamodb_helpers import get_table
from utils.iso_clock import now_iso

PK_PREFIX = "ENRICHMENT_QUARANTINE#pmid_"
SK = "STATUS"

# Last-error string column is capped to match the scoring-quarantine helper
# in utils/dynamodb_helpers.py:526 (`str(last_error)[:1000]`). Same DDB table,
# same operator-review surface, same truncation budget.
_REASON_MAX_LEN = 1000

# Real PMIDs are 7–9 digit positive integers; the daily-enrichment delta
# additionally filters articleYear >= 2020, so live PMIDs are 30M+ in
# practice. Anything below 1M is almost certainly a test fixture that
# escaped from a REPL or ad-hoc script running against prod creds — the
# 2026-05-20 #112 backfill cleanup found 5 such stale rows
# (pmid_20 / pmid_2001 / pmid_3001 / pmid_4001 / pmid_5001, run_id
# "run-xyz", test-string reasons like "boom"). Refuse them at the write
# boundary so the next test escape fails loud rather than silently
# polluting prod DDB.
_MIN_REAL_PMID = 1_000_000


def _pk(pmid: str) -> str:
    return f"{PK_PREFIX}{pmid}"


def _validate_pmid(pmid) -> None:
    """Reject non-real PMIDs at the write boundary (see _MIN_REAL_PMID)."""
    try:
        pmid_int = int(pmid)
    except (TypeError, ValueError):
        raise ValueError(
            f"refusing to record enrichment quarantine for non-numeric "
            f"pmid {pmid!r} — if this is a test, inject a mock table "
            "instead of writing to prod DynamoDB"
        )
    if pmid_int < _MIN_REAL_PMID:
        raise ValueError(
            f"refusing to record enrichment quarantine for pmid {pmid_int} "
            f"(below the {_MIN_REAL_PMID:,} real-PMID floor) — if this is "
            "a test, inject a mock table instead of writing to prod DynamoDB"
        )


def record_failure(
    pmid: str, *, reason: str, run_id: str, table=None
) -> int:
    """Increment consecutive_failures for ``pmid``. Returns the new count.

    Idempotent on ``(pmid, run_id)``: a second call with the same run_id is
    rejected by a ConditionExpression so a retried subprocess can't
    double-count. Returns the existing count in that case.

    First-failure semantics: the UpdateItem `ADD consecutive_failures :one`
    on a non-existent attribute initializes to 1, and the row is created if
    it didn't exist. `created_at` is written via `if_not_exists` so it
    survives subsequent increments.

    Raises ``ValueError`` if ``pmid`` does not look like a real PMID — a
    write-boundary guard against test fixtures leaking into prod DDB.
    """
    _validate_pmid(pmid)
    t = table if table is not None else get_table()
    ts = now_iso()
    try:
        resp = t.update_item(
            Key={"PK": _pk(pmid), "SK": SK},
            UpdateExpression=(
                "ADD consecutive_failures :one "
                "SET pmid = :pmid, "
                "    last_failure_at = :ts, "
                "    last_failure_reason = :reason, "
                "    last_attempted_run_id = :run_id, "
                "    created_at = if_not_exists(created_at, :ts)"
            ),
            ConditionExpression=(
                "attribute_not_exists(last_attempted_run_id) "
                "OR last_attempted_run_id <> :run_id"
            ),
            ExpressionAttributeValues={
                ":one": 1,
                ":pmid": str(pmid),
                ":ts": ts,
                ":reason": str(reason)[:_REASON_MAX_LEN],
                ":run_id": run_id,
            },
            ReturnValues="ALL_NEW",
        )
        return int(resp["Attributes"]["consecutive_failures"])
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code != "ConditionalCheckFailedException":
            raise
        # Same (pmid, run_id) was already recorded — read back the existing
        # count so the caller gets a consistent return value.
        existing = t.get_item(Key={"PK": _pk(pmid), "SK": SK}).get("Item") or {}
        return int(existing.get("consecutive_failures", 0))


def is_quarantined(pmid: str, threshold: int, *, table=None) -> bool:
    """True iff a quarantine row exists with ``consecutive_failures >= threshold``."""
    t = table if table is not None else get_table()
    item = t.get_item(Key={"PK": _pk(pmid), "SK": SK}).get("Item")
    if not item:
        return False
    return int(item.get("consecutive_failures", 0)) >= int(threshold)


def clear(pmid: str, *, table=None) -> None:
    """Idempotent DeleteItem on the quarantine row.

    Called on every successful per-PMID outcome — a transient blip that
    recovers before threshold leaves no trace. DeleteItem on an absent key
    is a DDB no-op.
    """
    t = table if table is not None else get_table()
    t.delete_item(Key={"PK": _pk(pmid), "SK": SK})


def list_quarantined(
    *, threshold: Optional[int] = None, table=None
) -> list[dict]:
    """Return all quarantine rows, sorted by pmid.

    Each row is normalized to a dict::

        {
            "pmid": str,
            "consecutive_failures": int,
            "last_failure_at": str,
            "last_failure_reason": str,
            "last_attempted_run_id": str,
            "created_at": str,
        }

    If ``threshold`` is provided, filters to rows where
    ``consecutive_failures >= threshold`` (the write-offs, vs the
    accumulating in-progress counts below threshold).

    Backed by a paginated ``Table.scan`` with a ``begins_with(PK, ...)``
    FilterExpression — quarantine rows are expected to be rare (a handful
    in a healthy system) so a Scan is acceptable; matches the
    ``scan_all_synopses`` pattern in ``utils/dynamodb_helpers.py``.
    """
    t = table if table is not None else get_table()
    rows: list[dict] = []
    kwargs = {
        "FilterExpression": "begins_with(PK, :p)",
        "ExpressionAttributeValues": {":p": PK_PREFIX},
    }
    while True:
        resp = t.scan(**kwargs)
        for item in resp.get("Items", []):
            count = int(item.get("consecutive_failures", 0))
            if threshold is not None and count < int(threshold):
                continue
            rows.append({
                "pmid": str(item.get("pmid", "")),
                "consecutive_failures": count,
                "last_failure_at": item.get("last_failure_at", ""),
                "last_failure_reason": item.get("last_failure_reason", ""),
                "last_attempted_run_id": item.get("last_attempted_run_id", ""),
                "created_at": item.get("created_at", ""),
            })
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek
    rows.sort(key=lambda r: r["pmid"])
    return rows

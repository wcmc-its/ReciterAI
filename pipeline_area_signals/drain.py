"""The ``PK=AREAINPUT`` queue SPS writes and this package drains.

Item contract (SPS writes; ReciterAI updates only the status fields):

    PK="AREAINPUT", SK="{kind}#{text_key}", kind in {grant, trial}
    kind, text_key, mode ("full"|"title"), title, text, funder, mechanism,
    phase, study_type, conditions (list), interventions (list),
    start_year (number), status ("pending"|"scored"|"failed"),
    taxonomy_version (set here when scored), fail_reason, created_at (ISO)

ReciterAI also keeps ``attempts`` and ``last_error`` on an item that failed
transiently and is still pending (or still scored on an older taxonomy).

An item is due when it is ``pending``, or ``scored`` under a taxonomy version
other than the current one (taxonomy-change re-score). ``failed`` is terminal: a
corrected text gets a new ``text_key`` from SPS and arrives as a new item.

Every status write is conditional on the item still existing, so a row SPS
deleted mid-run is never recreated as a stub.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from pipeline_area_signals.signals import INPUT_PK, SIGNAL_SK, signal_pk
from utils.iso_clock import now_iso

log = logging.getLogger(__name__)

STATUS_PENDING = "pending"
STATUS_SCORED = "scored"
STATUS_FAILED = "failed"

# A transient failure (Bedrock error, empty synopsis, scorer failure) leaves the
# item due and retries it on the next run; the third failed attempt is terminal.
MAX_ATTEMPTS = 3

REASON_PENDING = "pending"
REASON_TAXONOMY = "taxonomy_changed"


def due_reason(item: dict, taxonomy_version: str) -> Optional[str]:
    """Why an item needs scoring now, or None when it does not."""
    status = item.get("status")
    if status == STATUS_PENDING:
        return REASON_PENDING
    if status == STATUS_SCORED and item.get("taxonomy_version") != taxonomy_version:
        return REASON_TAXONOMY
    return None


def list_inputs(table) -> list[dict]:
    """Every AREAINPUT item (paginated Query on the one partition)."""
    items: list[dict] = []
    kwargs: dict[str, Any] = {"KeyConditionExpression": Key("PK").eq(INPUT_PK)}
    while True:
        page = table.query(**kwargs)
        items.extend(page.get("Items", []))
        last = page.get("LastEvaluatedKey")
        if not last:
            return items
        kwargs["ExclusiveStartKey"] = last


def select_due(items: list[dict], taxonomy_version: str) -> list[tuple[dict, str]]:
    """Due items as (item, reason): new inputs first, then taxonomy re-scores,
    each oldest first."""
    due = [(i, r) for i in items if (r := due_reason(i, taxonomy_version))]
    order = {REASON_PENDING: 0, REASON_TAXONOMY: 1}
    return sorted(due, key=lambda p: (order[p[1]], str(p[0].get("created_at") or ""),
                                      str(p[0].get("SK") or "")))


def get_input(table, sk: str) -> Optional[dict]:
    return table.get_item(Key={"PK": INPUT_PK, "SK": sk}).get("Item")


def get_signal(table, kind: str, text_key: str) -> Optional[dict]:
    return table.get_item(Key={"PK": signal_pk(kind, text_key), "SK": SIGNAL_SK}).get("Item")


def put_signal(table, item: dict) -> None:
    table.put_item(Item=item)


def _conditional_update(table, sk: str, expression: str, names: Optional[dict],
                        values: dict) -> bool:
    """UpdateItem that never recreates a deleted row. False when it was gone."""
    kwargs: dict[str, Any] = {
        "Key": {"PK": INPUT_PK, "SK": sk},
        "UpdateExpression": expression,
        "ConditionExpression": "attribute_exists(PK)",
        "ExpressionAttributeValues": values,
    }
    if names:  # DynamoDB rejects unused ExpressionAttributeNames
        kwargs["ExpressionAttributeNames"] = names
    try:
        table.update_item(**kwargs)
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            log.info("AREAINPUT %s is gone; status not written", sk)
            return False
        raise


def mark_scored(table, sk: str, *, taxonomy_version: str) -> bool:
    return _conditional_update(
        table, sk,
        "SET #st = :st, taxonomy_version = :tv, scored_at = :ts, attempts = :zero "
        "REMOVE fail_reason, last_error",
        {"#st": "status"},
        {":st": STATUS_SCORED, ":tv": taxonomy_version, ":ts": now_iso(), ":zero": 0},
    )


def mark_failed(table, sk: str, *, reason: str, attempts: int) -> bool:
    return _conditional_update(
        table, sk,
        "SET #st = :st, fail_reason = :r, failed_at = :ts, attempts = :n",
        {"#st": "status"},
        {":st": STATUS_FAILED, ":r": reason[:500], ":ts": now_iso(), ":n": attempts},
    )


def record_attempt(table, sk: str, *, error: str, attempts: int) -> bool:
    """A transient failure below MAX_ATTEMPTS: status unchanged, still due."""
    return _conditional_update(
        table, sk, "SET attempts = :n, last_error = :e", None,
        {":n": attempts, ":e": error[:500]},
    )

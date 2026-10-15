"""The ``PK=AREAINPUT`` queue SPS writes and this package drains.

Item contract (SPS writes; ReciterAI updates only the status fields):

    PK="AREAINPUT", SK="{kind}#{text_key}", kind in {grant, trial}
    kind, text_key, mode ("full"|"title"), title, text, funder, mechanism,
    phase, study_type, conditions (list), interventions (list),
    start_year (number), status ("pending"|"scored"|"failed"),
    taxonomy_version (set here when scored), fail_reason, created_at (ISO)

ReciterAI also keeps ``attempts`` and ``last_error`` on an item that failed
transiently and is still pending (or still scored on an older taxonomy),
``prompt_version`` on a scored item, and ``lease_until`` / ``lease_run`` while a
run holds the item. Subarea assignment (``subareas``) adds ``subarea_version``
(the run's ``SubareaContext.version`` once the item's AREASIGNAL row carries
current subareas) and, while an assign-only retry is pending,
``subarea_attempts`` / ``subarea_error``.

An item is due when it is ``pending``, or ``scored`` under a taxonomy version
other than the current one (taxonomy-change re-score), or ``scored`` under a
prompt version other than ``prompts.PROMPT_VERSION`` (prompt-change re-score),
or ``scored`` with a ``subarea_version`` other than the run's (a hierarchy
draft or the subarea prompt moved, or the item was scored before subareas
existed). That last one runs the assign-only path: no synopsis, no re-score,
only the stale areas' classifier calls. It is also the one-time backfill.
``failed`` is terminal for the daily run: a corrected text gets a new
``text_key`` from SPS and arrives as a new item. An operator re-queues failed
items (after an outage, say) with ``--requeue-failed``.

Before spending, a run claims the item with a conditional UpdateItem: the
status (and taxonomy/prompt version) it saw, and no live lease from another
run. Two overlapping runs therefore never score the same item. A lease left by a
crashed run expires after ``LEASE_SECONDS``.

Every status write is conditional on the item still existing, so a row SPS
deleted mid-run is never recreated as a stub.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from datetime import datetime, timedelta, timezone

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from pipeline_area_signals import prompts
from pipeline_area_signals.signals import INPUT_PK, SIGNAL_SK, signal_pk
from utils.iso_clock import now_iso

log = logging.getLogger(__name__)

STATUS_PENDING = "pending"
STATUS_SCORED = "scored"
STATUS_FAILED = "failed"

# A transient failure (Bedrock error, empty synopsis, scorer failure) leaves the
# item due and retries it on the next run; the third failed attempt is terminal.
MAX_ATTEMPTS = 3

# A claim outlives any single item's scoring by a wide margin (a few Bedrock
# calls with a 120 s read timeout each), and a crashed run's claims lapse well
# before the next daily run.
LEASE_SECONDS = 1800

REASON_PENDING = "pending"
REASON_TAXONOMY = "taxonomy_changed"
REASON_PROMPT = "prompt_changed"
REASON_REQUEUED = "requeued"
REASON_SUBAREA = "subareas_stale"


def due_reason(item: dict, taxonomy_version: str, *,
               requeue_failed: bool = False,
               subarea_version: Optional[str] = None) -> Optional[str]:
    """Why an item needs scoring now, or None when it does not.

    ``subarea_version`` is the run's ``SubareaContext.version``; None (no
    drafts loaded) never makes an item due for subareas.
    """
    status = item.get("status")
    if status == STATUS_PENDING:
        return REASON_PENDING
    if status == STATUS_SCORED:
        if item.get("taxonomy_version") != taxonomy_version:
            return REASON_TAXONOMY
        if item.get("prompt_version") != prompts.PROMPT_VERSION:
            return REASON_PROMPT
        if subarea_version is not None and item.get("subarea_version") != subarea_version:
            return REASON_SUBAREA
    if status == STATUS_FAILED and requeue_failed:
        return REASON_REQUEUED
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


def select_due(items: list[dict], taxonomy_version: str, *,
               requeue_failed: bool = False,
               subarea_version: Optional[str] = None) -> list[tuple[dict, str]]:
    """Due items as (item, reason): new inputs first, then re-queued failures,
    then taxonomy and prompt re-scores, then subarea refreshes, each oldest
    first."""
    due = [(i, r) for i in items
           if (r := due_reason(i, taxonomy_version, requeue_failed=requeue_failed,
                               subarea_version=subarea_version))]
    order = {REASON_PENDING: 0, REASON_REQUEUED: 1, REASON_TAXONOMY: 2, REASON_PROMPT: 3,
             REASON_SUBAREA: 4}
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
    }
    if values:  # DynamoDB rejects an empty ExpressionAttributeValues
        kwargs["ExpressionAttributeValues"] = values
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


def _iso_after(seconds: int) -> str:
    when = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    return when.isoformat(timespec="seconds").replace("+00:00", "Z")


def claim(table, item: dict, *, run_id: str, lease_seconds: int = LEASE_SECONDS,
          requeue: bool = False) -> bool:
    """Take the item for this run before spending on it. False when it moved on.

    Conditional on the status, taxonomy_version, prompt_version and
    subarea_version this run read (so an item another run just finished is not scored again) and on no
    unexpired lease (so an item another run is scoring right now is left to it).
    ``requeue`` (a ``failed`` item under ``--requeue-failed``) also puts it back
    to ``pending`` with its attempts reset, so the normal retry path applies.
    """
    sk = str(item.get("SK") or "")
    now = now_iso()
    clauses = ["attribute_exists(PK)", "#st = :st",
               "(attribute_not_exists(lease_until) OR lease_until < :now)"]
    values: dict[str, Any] = {":st": item.get("status"), ":now": now,
                              ":lu": _iso_after(lease_seconds), ":lr": run_id}
    for attr, placeholder in (("taxonomy_version", ":otv"), ("prompt_version", ":opv"),
                              ("subarea_version", ":osv")):
        if item.get(attr) is None:
            clauses.append(f"attribute_not_exists({attr})")
        else:
            clauses.append(f"{attr} = {placeholder}")
            values[placeholder] = item[attr]
    update = "SET lease_until = :lu, lease_run = :lr"
    if requeue:
        update += ", #st = :pending, attempts = :zero"
        values.update({":pending": STATUS_PENDING, ":zero": 0})
    try:
        table.update_item(
            Key={"PK": INPUT_PK, "SK": sk},
            UpdateExpression=update,
            ConditionExpression=" AND ".join(clauses),
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues=values,
        )
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            log.info("AREAINPUT %s is claimed by another run or moved on; skipped", sk)
            return False
        raise


def release(table, sk: str) -> bool:
    """Drop this run's lease without counting an attempt (run aborted)."""
    return _conditional_update(table, sk, "REMOVE lease_until, lease_run", None, {})


def mark_scored(table, sk: str, *, taxonomy_version: str,
                subarea_version: Optional[str] = None) -> bool:
    """``subarea_version`` is set only when the stored row's subareas are all
    current; otherwise it is removed, so the item comes back for them."""
    values = {":st": STATUS_SCORED, ":tv": taxonomy_version, ":pv": prompts.PROMPT_VERSION,
              ":ts": now_iso(), ":zero": 0}
    expression = ("SET #st = :st, taxonomy_version = :tv, prompt_version = :pv, "
                  "scored_at = :ts, attempts = :zero")
    remove = "fail_reason, last_error, lease_until, lease_run, subarea_attempts, subarea_error"
    if subarea_version is None:
        remove += ", subarea_version"
    else:
        expression += ", subarea_version = :sv"
        values[":sv"] = subarea_version
    return _conditional_update(table, sk, f"{expression} REMOVE {remove}",
                               {"#st": "status"}, values)


def mark_subareas(table, sk: str, *, subarea_version: str,
                  error: Optional[str] = None) -> bool:
    """Assign-only path done: the item is current for this subarea version.

    ``error`` is set when the run gave up on an area after ``MAX_ATTEMPTS``
    (the item is not retried until the subarea version moves again).
    """
    values: dict[str, Any] = {":sv": subarea_version}
    expression = "SET subarea_version = :sv"
    remove = "subarea_attempts, lease_until, lease_run"
    if error:
        expression += ", subarea_error = :e"
        values[":e"] = error[:500]
    else:
        remove += ", subarea_error"
    return _conditional_update(table, sk, f"{expression} REMOVE {remove}", None, values)


def record_subarea_attempt(table, sk: str, *, error: str, attempts: int) -> bool:
    """An assign-only failure below MAX_ATTEMPTS: still due for subareas."""
    return _conditional_update(
        table, sk,
        "SET subarea_attempts = :n, subarea_error = :e REMOVE lease_until, lease_run", None,
        {":n": attempts, ":e": error[:500]},
    )


def mark_failed(table, sk: str, *, reason: str, attempts: int) -> bool:
    return _conditional_update(
        table, sk,
        "SET #st = :st, fail_reason = :r, failed_at = :ts, attempts = :n "
        "REMOVE lease_until, lease_run",
        {"#st": "status"},
        {":st": STATUS_FAILED, ":r": reason[:500], ":ts": now_iso(), ":n": attempts},
    )


def record_attempt(table, sk: str, *, error: str, attempts: int) -> bool:
    """A transient failure below MAX_ATTEMPTS: status unchanged, still due."""
    return _conditional_update(
        table, sk, "SET attempts = :n, last_error = :e REMOVE lease_until, lease_run", None,
        {":n": attempts, ":e": error[:500]},
    )

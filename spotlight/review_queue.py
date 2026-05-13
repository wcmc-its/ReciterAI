"""Review queue read/write for SPOTLIGHT_REVIEW#{publish_id}#{subtopic_id} records.

Implements the queue mechanism backing the ``--review-queue``, ``--approve``,
and ``--reject`` CLI flags (Plan 06-06). Supports SPOT-08 (sensitive_tag
flagged) and SPOT-07 (critic persistent failure flagged).

Composite SK lets a single ``Query`` list all flagged entries for a publish
run. Status state machine enforces ``pending → approved`` and
``pending → rejected`` only -- enforced at the DynamoDB layer via
``ConditionExpression`` so a malicious caller cannot re-approve a rejected
entry by issuing a raw UpdateItem.

Schema reference: ``docs/spotlight-dynamodb-schema.md``
§"SPOTLIGHT_REVIEW#{publish_id}".

Security invariants (CLAUDE.md hard rule):
  * No string interpolation of user-controlled values into UpdateExpression,
    FilterExpression, or ConditionExpression strings.
  * All user values flow through ExpressionAttributeValues parameter binding.
  * The Key construction (PK / SK strings) does use f-string composition
    against the publish_id and subtopic_id; these are opaque key strings to
    DynamoDB (not expressions) -- the operator-controlled CLI is the trust
    boundary for the key namespace itself.
"""

from __future__ import annotations

import logging
from typing import Any

import boto3
from utils.iso_clock import now_iso

logger = logging.getLogger(__name__)


# Module constants
TABLE_NAME = "reciterai"
REGION = "us-east-1"
VALID_FLAG_REASONS = {"critic", "sensitive_tag", "both"}
VALID_TARGET_STATUSES = {"approved", "rejected"}


# ---------------------------------------------------------------------------
# Lazy boto3 client (mirrors spotlight/pool_ranker.py:_get_default_client)
# ---------------------------------------------------------------------------

_default_client = None


def _get_default_client():
    global _default_client
    if _default_client is None:
        _default_client = boto3.client("dynamodb", region_name=REGION)
    return _default_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------




def _to_av(value: Any) -> dict:
    """Convert a Python value to a boto3 low-level AttributeValue.

    Supports: str → S, bool → BOOL, int / float → N, list → L (recursive),
    dict → M (recursive). None → NULL. Empty containers preserved.
    """
    if value is None:
        return {"NULL": True}
    if isinstance(value, bool):
        # bool must come before int (bool is a subclass of int).
        return {"BOOL": value}
    if isinstance(value, str):
        return {"S": value}
    if isinstance(value, (int, float)):
        return {"N": str(value)}
    if isinstance(value, list):
        return {"L": [_to_av(v) for v in value]}
    if isinstance(value, dict):
        return {"M": {k: _to_av(v) for k, v in value.items()}}
    raise TypeError(f"Unsupported type for AttributeValue conversion: {type(value)}")


def _from_av(av: dict) -> Any:
    """Inverse of ``_to_av`` -- flatten a boto3 AttributeValue to a Python value."""
    if "S" in av:
        return av["S"]
    if "N" in av:
        # Prefer int when the encoded value has no decimal; else float.
        n = av["N"]
        if "." in n or "e" in n or "E" in n:
            return float(n)
        return int(n)
    if "BOOL" in av:
        return av["BOOL"]
    if "NULL" in av:
        return None
    if "L" in av:
        return [_from_av(x) for x in av["L"]]
    if "M" in av:
        return {k: _from_av(v) for k, v in av["M"].items()}
    if "SS" in av:
        return list(av["SS"])
    if "NS" in av:
        return [int(n) if "." not in n else float(n) for n in av["NS"]]
    raise ValueError(f"Unsupported AttributeValue: {av!r}")


def _flatten_item(item: dict) -> dict:
    """Flatten a DynamoDB low-level Item dict into native Python types."""
    return {k: _from_av(v) for k, v in item.items()}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def write_review_entry(client, entry: dict) -> None:
    """PutItem a SPOTLIGHT_REVIEW#{publish_id} / SUBTOPIC#{subtopic_id} record.

    Required keys in ``entry``: publish_id, subtopic_id, parent_topic,
    lede_text, flag_reason, papers_used (list of PMID strings), regen_count.

    Optional keys: critic_verdict (dict), sensitive_tag_matched (str),
    attempts (list of dicts).

    The ``status`` field is FORCED to ``"pending"`` regardless of caller
    input -- only ``set_status`` can transition the row.
    """
    client = client or _get_default_client()

    flag_reason = entry["flag_reason"]
    if flag_reason not in VALID_FLAG_REASONS:
        raise ValueError(
            f"flag_reason must be one of {sorted(VALID_FLAG_REASONS)}; "
            f"got {flag_reason!r}"
        )

    publish_id = entry["publish_id"]
    subtopic_id = entry["subtopic_id"]

    item: dict[str, dict] = {
        "PK": {"S": f"SPOTLIGHT_REVIEW#{publish_id}"},
        "SK": {"S": f"SUBTOPIC#{subtopic_id}"},
        "publish_id": {"S": publish_id},
        "subtopic_id": {"S": subtopic_id},
        "parent_topic": {"S": entry["parent_topic"]},
        "lede_text": {"S": entry["lede_text"]},
        "flag_reason": {"S": flag_reason},
        "papers_used": {
            "L": [{"S": pmid} for pmid in entry["papers_used"]]
        },
        "regen_count": {"N": str(int(entry["regen_count"]))},
        # status is FORCED -- the caller cannot override.
        "status": {"S": "pending"},
        "created_at": {"S": now_iso()},
    }

    # Optional fields
    if "critic_verdict" in entry and entry["critic_verdict"] is not None:
        item["critic_verdict"] = _to_av(entry["critic_verdict"])
    if "sensitive_tag_matched" in entry and entry["sensitive_tag_matched"] is not None:
        item["sensitive_tag_matched"] = {"S": entry["sensitive_tag_matched"]}
    if "attempts" in entry and entry["attempts"] is not None:
        item["attempts"] = _to_av(list(entry["attempts"]))

    client.put_item(TableName=TABLE_NAME, Item=item)
    logger.info(
        "Review entry written: publish_id=%s subtopic_id=%s flag_reason=%s",
        publish_id,
        subtopic_id,
        flag_reason,
    )


def list_pending(client, publish_id: str) -> list[dict]:
    """Query SPOTLIGHT_REVIEW#{publish_id} for entries with status=pending.

    Returns a list of native-type dicts (low-level AttributeValues already
    flattened). Empty list if no pending entries.

    ``status`` is a DynamoDB reserved word, so the FilterExpression uses
    the ``#s`` ExpressionAttributeNames alias.
    """
    client = client or _get_default_client()
    resp = client.query(
        TableName=TABLE_NAME,
        KeyConditionExpression="PK = :pk",
        FilterExpression="#s = :pending",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":pk": {"S": f"SPOTLIGHT_REVIEW#{publish_id}"},
            ":pending": {"S": "pending"},
        },
    )
    items = resp.get("Items", [])
    return [_flatten_item(item) for item in items]


def set_status(
    client,
    publish_id: str,
    subtopic_id: str,
    target_status: str,
    reviewer: str = "cli",
) -> None:
    """UpdateItem SPOTLIGHT_REVIEW row from pending → approved | rejected.

    Enforces the state machine via ``ConditionExpression`` -- the row must
    currently be in ``pending`` state. boto3 raises
    ``ConditionalCheckFailedException`` otherwise; the exception
    propagates to the caller (CLI surface in Plan 06-06).

    ``target_status`` is validated against ``VALID_TARGET_STATUSES`` to
    fail-loud on operator typos. ``reviewer`` defaults to ``"cli"`` when
    invoked from the command-line dispatcher.

    All four user-controlled values (publish_id, subtopic_id,
    target_status, reviewer) flow through ExpressionAttributeValues binding
    or the Key dict only -- never interpolated into expression strings
    (CLAUDE.md hard rule, T-06-04-02).
    """
    if target_status not in VALID_TARGET_STATUSES:
        raise ValueError(
            f"target_status must be one of {sorted(VALID_TARGET_STATUSES)}; "
            f"got {target_status!r}"
        )

    client = client or _get_default_client()
    client.update_item(
        TableName=TABLE_NAME,
        Key={
            "PK": {"S": f"SPOTLIGHT_REVIEW#{publish_id}"},
            "SK": {"S": f"SUBTOPIC#{subtopic_id}"},
        },
        UpdateExpression="SET #s = :s, reviewer = :r, reviewed_at = :t",
        ConditionExpression="#s = :pending",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s": {"S": target_status},
            ":r": {"S": reviewer},
            ":t": {"S": now_iso()},
            ":pending": {"S": "pending"},
        },
    )
    logger.info(
        "Review status: %s/%s -> %s by %s",
        publish_id,
        subtopic_id,
        target_status,
        reviewer,
    )

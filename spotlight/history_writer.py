"""Post-publish writer for SPOTLIGHT_HISTORY#{subtopic_id} state.

Implements the write half of SPOT-04. Called by ``spotlight/publish.py``
after S3 PutObjects succeed (Plan 06-06). Each Selection produces one
DynamoDB UpdateItem call:

    UpdateExpression = "ADD shown_count :one
                        SET last_shown_at = :now,
                            last_shown_publish_id = :pid"

The PK carries no version segment. Subtopic IDs are durable across
hierarchy versions (#191), and rotation history must outlive any single
hierarchy or spotlight publish — a versioned PK partitions the history per
publish, so every read lands on an empty partition and decay never applies.

Idempotent on same-day re-publish in the audit-trail sense: ``ADD`` is
additive, so a same-day re-publish increments ``shown_count`` again.
``last_shown_at`` and ``last_shown_publish_id`` overwrite — the most
recent publish wins, which is the desired state for the rotation
selector's decay calculation.

Security (CLAUDE.md, T-06-03-01): ``publish_id`` flows through
``ExpressionAttributeValues`` only. The UpdateExpression string is a
literal — never f-string'd or .format()'d with user-controlled values.
"""

from __future__ import annotations

import logging

import boto3

from spotlight.rotation_selector import Selection
from utils.iso_clock import now_iso

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

TABLE_NAME = "reciterai"
REGION = "us-east-1"

# Literal UpdateExpression — must remain a constant (T-06-03-01: no
# interpolation of caller-controlled strings).
_UPDATE_EXPRESSION = (
    "ADD shown_count :one "
    "SET last_shown_at = :now, last_shown_publish_id = :pid"
)


# ---------------------------------------------------------------------------
# Lazy boto3 client (mirrors pool_ranker.py / rotation_selector.py)
# ---------------------------------------------------------------------------

_default_client = None


def _get_default_client():
    """Get or create the module-level default DynamoDB client.

    No AWS calls happen at import time. Tests inject their own client and
    never reach this path.
    """
    global _default_client
    if _default_client is None:
        _default_client = boto3.client("dynamodb", region_name=REGION)
    return _default_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def update_history(
    client, selections: list[Selection], publish_id: str
) -> None:
    """SPOT-04 write — UpdateItem on SPOTLIGHT_HISTORY# for each Selection.

    PK shape is ``SPOTLIGHT_HISTORY#{subtopic_id}`` — unversioned, so history
    accumulates across publishes and the rotation selector's decay can see it.

    Increments ``shown_count`` by 1, sets ``last_shown_at`` to the current
    UTC ISO timestamp (Z suffix), and records ``last_shown_publish_id``.
    All values flow through ``ExpressionAttributeValues``; the
    UpdateExpression itself is a module-level constant string.

    Args:
        client: low-level boto3 DynamoDB client (or test stub). If None
            is passed, the lazy default-credential client is used.
        selections: list of ``Selection`` objects from
            ``rotation_selector.select_with_diversity()``.
        publish_id: opaque publish-run identifier (typically
            ``v{ISO-date}``). Operator-controlled; flows through EAV only.
    """
    client = client or _get_default_client()
    now = now_iso()

    for s in selections:
        client.update_item(
            TableName=TABLE_NAME,
            Key={
                "PK": {"S": f"SPOTLIGHT_HISTORY#{s.entry.subtopic_id}"},
                "SK": {"S": "STATE"},
            },
            UpdateExpression=_UPDATE_EXPRESSION,
            ExpressionAttributeValues={
                ":one": {"N": "1"},
                ":now": {"S": now},
                ":pid": {"S": publish_id},
            },
        )

    logger.info(
        "SPOTLIGHT_HISTORY updated for %d subtopics, publish_id=%s",
        len(selections),
        publish_id,
    )

"""
DynamoDB UpdateItem helpers for Phase 4 subtopic schema migration (SUB-06).

All writes use UpdateItem with SET/REMOVE expressions — never
BatchWriteItem/PutItem — so existing activity/faculty record attributes
(synopsis, impact_score, top_topics, etc.) are preserved during backfill.

Decimal coercion is mandatory for every numeric (see Phase 1 Plan 01 Pitfall
1 recorded in STATE.md: DynamoDB rejects Python floats). This module uses
the existing `to_decimal` helper from utils.dynamodb_helpers.

Idempotent: rerunning an UpdateItem on the same row overwrites subtopic
fields with the same values — no double-counting risk.

Portability note (CLAUDE.md §personIdentifier): the literal `cwid_` prefix
used in `FACULTY#cwid_<pid>` is the Python write-side counterpart to PM's
`WCM_FACULTY_UID_PREFIX` constant (exported from
`ReCiter-Publication-Manager/controllers/chatbot/retrieval/shared.ts`). If
porting to another institution's identifier scheme (NetID, Access ID), both
must be updated together along with Phase 1's `load_dynamodb.py`.

Public API (exactly three names):
- update_activity_subtopics(pk, sk, subtopic_ids, primary_subtopic_id, confidences)
- update_faculty_subtopic_scores(person_identifier, topic_id, scores)
- clear_faculty_subtopic_scores_for_topic(person_identifier, topic_id)
"""

from __future__ import annotations

from typing import Mapping

from utils.dynamodb_helpers import to_decimal, get_table

TABLE_NAME = "reciterai-chatbot"


def update_activity_subtopics(
    pk: str,
    sk: str,
    subtopic_ids: list[str],
    primary_subtopic_id: str,
    confidences: Mapping[str, float],
) -> dict:
    """Write Pass 2 output to an activity record (D-16 schema).

    Args:
        pk: DynamoDB partition key, e.g. "TOPIC#aging_geroscience".
        sk: DynamoDB sort key, e.g. "SCORE#0850#ACTIVITY#pmid_12345#cwid_abc".
        subtopic_ids: All subtopics this activity was assigned to (≥ confidence floor).
        primary_subtopic_id: Highest-confidence subtopic; used for Tier 1 ranking.
        confidences: {subtopic_id: confidence_float}. Raw Python floats are
                     accepted; they are internally coerced via to_decimal().

    Returns:
        The boto3 update_item response dict.
    """
    table = get_table(TABLE_NAME)
    return table.update_item(
        Key={"PK": pk, "SK": sk},
        UpdateExpression=(
            "SET subtopic_ids = :sids, "
            "primary_subtopic_id = :pid, "
            "subtopic_confidences = :confs"
        ),
        ExpressionAttributeValues={
            ":sids": list(subtopic_ids),
            ":pid": primary_subtopic_id,
            ":confs": {k: to_decimal(v) for k, v in confidences.items()},
        },
    )


def update_faculty_subtopic_scores(
    person_identifier: str,
    topic_id: str,
    scores: Mapping[str, float],
) -> dict:
    """Write Pass 3 output to a faculty record (D-17 schema).

    Scoped to `subtopic_scores.<topic_id>` via a nested-path SET expression
    so other topics' subtopic_scores on the same faculty profile are
    preserved. Topic id is passed through ExpressionAttributeNames to avoid
    any reserved-word or special-char collisions.

    Args:
        person_identifier: WCM personIdentifier (formerly cwid). The literal
                           `cwid_` prefix is added here to form the PK.
        topic_id: Parent topic id, e.g. "aging_geroscience".
        scores: {subtopic_id: summed_articleScore}. Raw floats are coerced.

    Returns:
        The boto3 update_item response dict.
    """
    table = get_table(TABLE_NAME)
    # Two-step write: DynamoDB's nested-path SET fails with ValidationException
    # when the outer map attribute does not already exist on the item. First
    # ensure subtopic_scores exists as a map (no-op if present), then set the
    # per-topic nested key. Both calls are idempotent.
    table.update_item(
        Key={"PK": f"FACULTY#cwid_{person_identifier}", "SK": "PROFILE"},
        UpdateExpression=(
            "SET subtopic_scores = if_not_exists(subtopic_scores, :empty)"
        ),
        ExpressionAttributeValues={":empty": {}},
    )
    return table.update_item(
        Key={"PK": f"FACULTY#cwid_{person_identifier}", "SK": "PROFILE"},
        UpdateExpression="SET subtopic_scores.#t = :scores",
        ExpressionAttributeNames={"#t": topic_id},
        ExpressionAttributeValues={
            ":scores": {k: to_decimal(v) for k, v in scores.items()},
        },
    )


def clear_faculty_subtopic_scores_for_topic(
    person_identifier: str,
    topic_id: str,
) -> dict:
    """Wholesale-replacement precursor (D-06): remove stale scores for one
    topic before Pass 3 rewrites them. Leaves other topics' subtopic_scores
    intact.

    Args:
        person_identifier: WCM personIdentifier (cwid_ prefix added here).
        topic_id: Parent topic id to clear, e.g. "aging_geroscience".

    Returns:
        The boto3 update_item response dict.
    """
    table = get_table(TABLE_NAME)
    return table.update_item(
        Key={"PK": f"FACULTY#cwid_{person_identifier}", "SK": "PROFILE"},
        UpdateExpression="REMOVE subtopic_scores.#t",
        ExpressionAttributeNames={"#t": topic_id},
    )


__all__ = [
    "update_activity_subtopics",
    "update_faculty_subtopic_scores",
    "clear_faculty_subtopic_scores_for_topic",
]

"""Sensitive-topic gate.

Reads SPOTLIGHT_CONFIG#sensitive_tags from DynamoDB and pattern-matches
each subtopic. Fail-closed: missing config or read failure raises (SPOT-08
hard security invariant). The pipeline aborts rather than publishing
potentially sensitive content under a silent fail-open.

Tag patterns live in DynamoDB ONLY -- they are operationally sensitive
and never appear in source. See ``docs/spotlight-dynamodb-schema.md``
SPOTLIGHT_CONFIG#sensitive_tags for the partition shape and operator
seeding instructions.

Match strategy (v1): case-insensitive substring against the concatenation
``subtopic.label + " " + subtopic.description + " " + parent_topic_label``
(per 06-RESEARCH.md Open Q §4 and CONTEXT D-19). Lede text is NOT a match
target -- the lede generator is constrained by anchor-in-synopses, and
widening the gate to lede output would be redundant.
"""

from __future__ import annotations

import logging
from typing import NamedTuple

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


# Module constants
TABLE_NAME = "reciterai"
REGION = "us-east-1"
CONFIG_PK = "SPOTLIGHT_CONFIG#sensitive_tags"
CONFIG_SK = "CONFIG"


class SubtopicMeta(NamedTuple):
    """Subtopic metadata fed into the sensitive gate.

    Loaded by Plan 06-05's lede generator from the hierarchy artifact
    (pool_ranker only carries subtopic_id + parent_topic; the label and
    description live in the hierarchy JSON).
    """

    subtopic_id: str
    label: str
    description: str
    parent_topic_label: str


# ---------------------------------------------------------------------------
# Lazy boto3 client (mirrors spotlight/pool_ranker.py:_get_default_client)
# ---------------------------------------------------------------------------

_default_client = None


def _get_default_client():
    """Return module-level default DynamoDB client, creating on first call.

    No AWS calls happen at import time. Tests inject their own client.
    """
    global _default_client
    if _default_client is None:
        _default_client = boto3.client("dynamodb", region_name=REGION)
    return _default_client


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_sensitive_tags(client=None) -> list[dict]:
    """Read SPOTLIGHT_CONFIG#sensitive_tags from DynamoDB.

    Returns a list of ``{pattern, match_type, reason}`` dicts.

    RAISES ``RuntimeError`` (fail closed) if:
      - the config record is missing, or
      - boto3 ``GetItem`` raises a ``ClientError``.

    Silent fail-open (returning ``[]``) is FORBIDDEN -- the upstream
    pipeline must abort rather than publish content that bypassed the
    sensitive gate. SPOT-08 hard security invariant.
    """
    client = client or _get_default_client()

    try:
        resp = client.get_item(
            TableName=TABLE_NAME,
            Key={"PK": {"S": CONFIG_PK}, "SK": {"S": CONFIG_SK}},
        )
    except ClientError as e:
        # fail closed on DynamoDB error -- surface to caller, never swallow.
        raise RuntimeError(
            f"fail closed: SPOTLIGHT_CONFIG#sensitive_tags read failed: {e}"
        ) from e

    if "Item" not in resp:
        # fail closed on missing config record -- operator must seed via
        # ``aws dynamodb put-item`` before --publish (see schema doc).
        raise RuntimeError(
            "fail closed: SPOTLIGHT_CONFIG#sensitive_tags config record missing "
            "-- operator must seed via aws dynamodb put-item before --publish"
        )

    tags_attr = resp["Item"].get("tags", {}).get("L", [])
    parsed: list[dict] = []
    for tag_attr in tags_attr:
        tag_map = tag_attr.get("M", {})
        pattern = tag_map.get("pattern", {}).get("S")
        if not pattern:
            # Skip empty/malformed tags; operator-curated list may have
            # blanks but they are not actionable.
            continue
        match_type = tag_map.get("match_type", {}).get("S", "substring")
        reason = tag_map.get("reason", {}).get("S", "")
        parsed.append(
            {"pattern": pattern, "match_type": match_type, "reason": reason}
        )

    # Log only the count -- pattern values are operationally sensitive
    # (operator-curated; T-06-04-04).
    logger.info("Loaded %d sensitive tags from SPOTLIGHT_CONFIG", len(parsed))
    return parsed


def is_sensitive(meta: SubtopicMeta, tags: list[dict]) -> tuple[bool, str | None]:
    """Match strategy: case-insensitive substring against
    ``meta.label + " " + meta.description + " " + meta.parent_topic_label``
    per 06-RESEARCH.md Open Q §4.

    Returns ``(True, matched_pattern)`` on first match (the original-case
    pattern from the tag dict, so callers can log the operator's input
    verbatim), or ``(False, None)`` if no tag matches.

    The ``match_type`` field on each tag is forward-compat -- v1 implements
    substring only. Non-substring entries log a warning and fall through
    to substring matching (per RESEARCH §"Match strategy (v1)" -- the v1
    behavior is to ignore non-substring rows with a warning; we treat
    them as substring to remain fail-safe rather than skip silently).
    """
    haystack = (
        meta.label + " " + meta.description + " " + meta.parent_topic_label
    ).lower()

    for tag in tags:
        original_pattern = tag["pattern"]
        pattern_lower = original_pattern.lower()
        match_type = tag.get("match_type", "substring")
        if match_type != "substring":
            logger.warning(
                "match_type=%s not implemented in v1; treating as substring",
                match_type,
            )
        if pattern_lower in haystack:
            return (True, original_pattern)
    return (False, None)

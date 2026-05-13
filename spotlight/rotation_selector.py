"""Rotation selector — picks 10 subtopics from top-50 pool with exponential
decay against last-shown state and one-per-parent-topic diversity.

Implements SPOT-03 (rotation selector with exponential decay + parent
diversity) and the read half of SPOT-04 (last-shown queryable by
subtopic_id in DynamoDB).

Decay formula (CONTEXT decision Q1.3, RESEARCH §Pattern 2):
    multiplier = 1 - exp(-weeks_since_last_shown / DECAY_TAU_WEEKS)
    selection_score = pool_score * multiplier

Cold-start (last_shown_at is None) → multiplier = 1.0 (full pool_score).

Greedy parent-diversity selection enforces exactly one subtopic per
parent_topic. SELECTION_SIZE is a FLOOR (RESEARCH §Open Q §8): if the
pool yields fewer distinct parent topics than SELECTION_SIZE, the function
raises ValueError with an operator-friendly message.

The pattern follows ``spotlight/pool_ranker.py`` for the lazy boto3 client
and reads ``SPOTLIGHT_HISTORY#{subtopic_id}`` partitions via BatchGetItem
chunked at 25 keys per call (DynamoDB hard limit safe default).
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import boto3

from spotlight.types import PoolEntry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

DECAY_TAU_WEEKS = 12  # CONTEXT decision Q1.3
SELECTION_SIZE = 10  # CONTEXT decision Q1.1 (FLOOR; not a ceiling)
TABLE_NAME = "reciterai"
REGION = "us-east-1"
BATCH_GET_LIMIT = 25  # DynamoDB BatchGetItem safe per-call default

_HISTORY_PK_PREFIX = "SPOTLIGHT_HISTORY#"
_SECONDS_PER_WEEK = 7 * 86400


# ---------------------------------------------------------------------------
# Selection dataclass (module-local; assembler/lede use a richer type later)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Selection:
    """One selected subtopic plus its post-decay score and history snapshot.

    Frozen so downstream stages (history_writer, assembler) cannot mutate
    selection state by accident. ``last_shown_at`` is None on cold-start.
    """

    entry: PoolEntry
    sel_score: float
    last_shown_at: Optional[str]


# ---------------------------------------------------------------------------
# Lazy boto3 client (mirrors pool_ranker.py / utils.bedrock_client._get_client)
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
# Public API
# ---------------------------------------------------------------------------


def selection_score(pool_score: float, last_shown_at: Optional[str]) -> float:
    """SPOT-03 decay formula.

    ``selection_score = pool_score × (1 - exp(-weeks_since / DECAY_TAU_WEEKS))``

    Cold-start (``last_shown_at is None``) returns the full pool_score
    (multiplier 1.0). A malformed ISO timestamp is treated as cold-start
    too — operator-controlled value, but tolerating parse errors keeps
    the rotation pipeline running on a single corrupt history row
    (T-06-03-02 mitigation).

    Args:
        pool_score: numeric score from PoolEntry (sum of impact_score over
            the 24-month window for one subtopic).
        last_shown_at: ISO 8601 UTC timestamp string (Z suffix or +00:00),
            or None for never-shown subtopics.

    Returns:
        Multiplied score (float). Cold-start returns ``pool_score`` exactly.
    """
    if last_shown_at is None:
        return pool_score

    try:
        last = datetime.fromisoformat(last_shown_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        # Malformed history value — fall back to cold-start (T-06-03-02).
        logger.warning(
            "selection_score: malformed last_shown_at=%r; treating as cold-start",
            last_shown_at,
        )
        return pool_score

    delta_seconds = (datetime.now(timezone.utc) - last).total_seconds()
    weeks = delta_seconds / _SECONDS_PER_WEEK
    multiplier = 1.0 - math.exp(-weeks / DECAY_TAU_WEEKS)
    return pool_score * multiplier


def fetch_history(
    client, subtopic_ids: list[str], *, hierarchy_version: str
) -> dict[str, Optional[str]]:
    """Read SPOTLIGHT_HISTORY#{hierarchy_version}#{subtopic_id} via BatchGetItem.

    Phase 11 D-04: PK shape is now
    ``SPOTLIGHT_HISTORY#{hierarchy_version}#{subtopic_id}``.
    ``hierarchy_version`` is a required keyword argument.

    Returns a dict keyed by subtopic_id with ``last_shown_at`` (str) values.
    Subtopic IDs absent from DynamoDB are NOT included in the returned dict
    (caller's ``.get(sid)`` returns None → cold-start).

    Chunks at ``BATCH_GET_LIMIT`` (25) per BatchGetItem call. Implements
    one-shot retry on ``UnprocessedKeys`` (T-06-03-03 mitigation, mirrors
    ``import_enrichment.py:_flush_batch`` pattern). After one retry,
    unprocessed subtopics are silently dropped (caller treats them as
    cold-start, which is the safe default).

    Args:
        client: a low-level boto3 DynamoDB client (or test stub).
        subtopic_ids: list of subtopic_id strings to look up.
        hierarchy_version: The active hierarchy version (e.g. "v2026-06-01").
            Operator/code-controlled; embedded in PK key strings.

    Returns:
        dict mapping subtopic_id → last_shown_at ISO string (or None if
        the row exists but has no last_shown_at attribute).
    """
    client = client or _get_default_client()
    result: dict[str, Optional[str]] = {}

    for start in range(0, len(subtopic_ids), BATCH_GET_LIMIT):
        chunk = subtopic_ids[start : start + BATCH_GET_LIMIT]
        keys = [
            {
                "PK": {"S": f"{_HISTORY_PK_PREFIX}{hierarchy_version}#{sid}"},
                "SK": {"S": "STATE"},
            }
            for sid in chunk
        ]
        request_items = {TABLE_NAME: {"Keys": keys}}

        resp = client.batch_get_item(RequestItems=request_items)
        _ingest_responses(resp, result)

        # One retry on UnprocessedKeys (T-06-03-03)
        unprocessed = resp.get("UnprocessedKeys") or {}
        if unprocessed:
            time.sleep(0.1)
            retry_resp = client.batch_get_item(RequestItems=unprocessed)
            _ingest_responses(retry_resp, result)
            still_unprocessed = retry_resp.get("UnprocessedKeys") or {}
            if still_unprocessed:
                # Surface to logs; caller treats missing IDs as cold-start.
                logger.warning(
                    "fetch_history: UnprocessedKeys remain after retry; "
                    "affected subtopics will be treated as cold-start"
                )

    return result


def _ingest_responses(resp: dict, result: dict[str, Optional[str]]) -> None:
    """Extract subtopic_id → last_shown_at from a batch_get_item response.

    Phase 11 D-04: PK shape is ``SPOTLIGHT_HISTORY#{version}#{subtopic_id}``.
    Uses ``rsplit('#', 1)[-1]`` to extract the subtopic_id as the LAST segment
    so that subtopic_ids containing ``#`` are handled correctly and future
    additional segments (if any) would not break parsing.

    Mutates ``result`` in place. Skips items missing PK or last_shown_at
    cleanly (cold-start fallback applies via ``.get``).
    """
    for item in resp.get("Responses", {}).get(TABLE_NAME, []):
        pk = item.get("PK", {}).get("S", "")
        if not pk.startswith(_HISTORY_PK_PREFIX):
            continue
        # rsplit on '#' with maxsplit=1 takes the LAST segment as subtopic_id.
        # This is correct for the new PK shape SPOTLIGHT_HISTORY#{version}#{sid}
        # and also handles subtopic_ids that themselves contain '#'.
        sid = pk.rsplit("#", 1)[-1]
        last = item.get("last_shown_at", {}).get("S")
        result[sid] = last


def select_with_diversity(
    pool: list[PoolEntry],
    history: dict[str, Optional[str]],
    n: int = SELECTION_SIZE,
) -> list[Selection]:
    """SPOT-03 greedy parent-diversity selection.

    Sorts the pool by ``(-sel_score, subtopic_id)`` for deterministic
    tiebreaker stability, then greedily picks one subtopic per parent_topic
    until ``n`` selections are made.

    Selection size is a FLOOR (RESEARCH §Open Q §8): if the pool yields
    fewer than ``n`` distinct parent topics, raises ValueError with an
    operator-friendly message. T-06-03-05 mitigation.

    Args:
        pool: list of PoolEntry from ``pool_ranker.rank_pool()``. NOT
            mutated by this function.
        history: dict mapping subtopic_id → last_shown_at ISO string (or
            None for cold-start). Use ``fetch_history`` to populate.
        n: floor on number of selections to return. Default SELECTION_SIZE.

    Returns:
        List of ``n`` Selection objects, each with a unique parent_topic.

    Raises:
        ValueError: if fewer than ``n`` distinct parent topics are
            available in the pool ("selection floor failure").
    """
    scored = [
        Selection(
            entry=e,
            sel_score=selection_score(e.pool_score, history.get(e.subtopic_id)),
            last_shown_at=history.get(e.subtopic_id),
        )
        for e in pool
    ]
    # Deterministic tiebreaker: highest sel_score first; then subtopic_id ASC.
    # Use sorted() (not list.sort) to leave caller's reference untouched if
    # they passed a list aliased into ``scored`` — defensive vs aliasing.
    scored = sorted(scored, key=lambda s: (-s.sel_score, s.entry.subtopic_id))

    selected: list[Selection] = []
    parents: set[str] = set()
    for s in scored:
        if s.entry.parent_topic in parents:
            continue
        selected.append(s)
        parents.add(s.entry.parent_topic)
        if len(selected) == n:
            break

    if len(selected) < n:
        raise ValueError(
            f"selection floor failure: pool yields {len(selected)} distinct "
            f"parent topics; need {n}. Distinct parents: {sorted(parents)}"
        )

    logger.info(
        "Rotation selector: %d selections from pool of %d, top sel_score=%.2f",
        len(selected),
        len(pool),
        selected[0].sel_score if selected else 0.0,
    )
    return selected

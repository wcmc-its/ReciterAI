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
SELECTION_SIZE = 10  # CONTEXT decision Q1.1 (function-default FLOOR; not a ceiling)
# #164: build a CANDIDATE POOL of up to this many distinct, non-equivalent
# subtopics (a CEILING). The caller passes n=SELECTION_TARGET as the ceiling and
# n_floor=SELECTION_FLOOR as the minimum. Pass 1 fills the ceiling clone-free;
# Pass 2 force-admits near-clones ONLY to reach the floor, so a thin pool yields
# fewer CLEAN candidates rather than padding with duplicates ("clean over
# count"). This is the candidate pool the lede stage draws from — NOT the
# published count (see PUBLISH_TARGET).
SELECTION_TARGET = 25
# Minimum publishable spotlight size. Below this many clone-free selections the
# pool is degenerate enough that admitting a few near-clones (or, failing that,
# raising) is the lesser evil. Set low so realistic pools never force a clone.
SELECTION_FLOOR = 8
# Ship-all (operator decision 2026-06-10): publish EVERY cleared candidate (up to
# SELECTION_TARGET), not a pre-truncated top-N. Ledes are generated over the whole
# candidate set anyway (#167), so truncating to 9 only discarded ~16 paid-for
# ledes. SPS already random-samples 8 of however many cards it receives (its
# home-page `randomSample`), so the on-page selection moves to SPS. Residual
# cross-card overlap among the ~25 is low (measured 2026-06-10: only 2 of 300
# pairs share >=40% of papers, both containment-nested cancer-genomics cards) —
# SPS owns any further de-dup. Keeping PUBLISH_TARGET as a knob (== SELECTION_TARGET
# here) means lowering it re-enables the #167 truncation + the scholar-coverage
# penalty (now inert) in one place.
PUBLISH_TARGET = SELECTION_TARGET
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
    client, subtopic_ids: list[str]
) -> dict[str, Optional[str]]:
    """Read SPOTLIGHT_HISTORY#{subtopic_id} via BatchGetItem.

    The PK carries no version segment: subtopic IDs are durable (#191) and
    rotation history must outlive any single publish.

    Returns a dict keyed by subtopic_id with ``last_shown_at`` (str) values.
    Subtopic IDs absent from DynamoDB are NOT included in the returned dict
    (caller's ``.get(sid)`` returns None → cold-start).

    Chunks at ``BATCH_GET_LIMIT`` (25) per BatchGetItem call. Implements
    one-shot retry on ``UnprocessedKeys`` (T-06-03-03 mitigation, same
    pattern as ``utils/dynamodb_helpers.batch_write``). After one retry,
    unprocessed subtopics are silently dropped (caller treats them as
    cold-start, which is the safe default).

    Args:
        client: a low-level boto3 DynamoDB client (or test stub).
        subtopic_ids: list of subtopic_id strings to look up.

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
                "PK": {"S": f"{_HISTORY_PK_PREFIX}{sid}"},
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

    PK shape is ``SPOTLIGHT_HISTORY#{subtopic_id}``; the subtopic_id is
    whatever follows the prefix, so IDs containing ``#`` survive round-trip.

    Mutates ``result`` in place. Skips items missing PK or last_shown_at
    cleanly (cold-start fallback applies via ``.get``).
    """
    for item in resp.get("Responses", {}).get(TABLE_NAME, []):
        pk = item.get("PK", {}).get("S", "")
        if not pk.startswith(_HISTORY_PK_PREFIX):
            continue
        sid = pk[len(_HISTORY_PK_PREFIX):]
        last = item.get("last_shown_at", {}).get("S")
        result[sid] = last


def _count_selected_near_clones(
    subtopic_id: str,
    near_clones: dict[str, set[str]] | None,
    selected_sids: set[str],
) -> int:
    """Count how many already-selected subtopics are near-clones of this one.

    Returns 0 when ``near_clones`` is None (the gate is disabled). Drives the
    Pass-2 best-effort fill order in ``select_with_diversity`` (#91): the
    candidate with the lowest count is filled first.
    """
    if near_clones is None:
        return 0
    return len(near_clones.get(subtopic_id, frozenset()) & selected_sids)


def select_with_diversity(
    pool: list[PoolEntry],
    history: dict[str, Optional[str]],
    n: int = SELECTION_SIZE,
    *,
    n_floor: int | None = None,
    near_clones: dict[str, set[str]] | None = None,
) -> list[Selection]:
    """SPOT-03 greedy parent-diversity selection with a #91 near-clone gate.

    Sorts the pool by ``(-sel_score, subtopic_id)`` for deterministic
    tiebreaker stability, then selects ``n`` subtopics in two passes.

    **Pass 1** greedily picks a candidate when BOTH hold: its ``parent_topic``
    is not yet used (the original SPOT-03 gate) AND it is not a near-clone of
    an already-selected subtopic (#91 — active only when ``near_clones`` is
    supplied). A near-clone is two subtopics whose ``short_description``
    embeddings are too similar; ``spotlight.theme_dedup.find_near_clones``
    produces the adjacency. The clone gate stops one publish cycle from
    surfacing several differently-parented paraphrases of a single theme as
    near-duplicate cards.

    **Pass 2** runs only if Pass 1 came up short of ``n`` because the
    near-clone gate skipped otherwise-valid candidates. It keeps the
    parent-topic gate but drops the clone gate, filling the remaining slots
    least-cloned-first: the candidate that is a near-clone of the fewest
    already-selected subtopics wins, ties broken by ``sel_score``. This is
    best-effort — the near-clone gate can never block a monthly publish — and
    each forced near-clone admission is logged at WARNING.

    ``n`` is a CEILING (Pass 1 stops at it) and ``n_floor`` is the FLOOR that
    Pass 2 force-fills to. When ``n_floor < n`` (the #164 "clean over count"
    publish), Pass 1 takes as many clone-free, parent-distinct subtopics as the
    pool offers up to ``n``, and Pass 2 admits near-clones ONLY if Pass 1 fell
    below ``n_floor`` — so a thin pool publishes fewer CLEAN subtopics rather
    than padding the count with duplicates. If even Pass 2 cannot reach
    ``n_floor``, raises ValueError (genuine parent-topic shortage; T-06-03-05).
    When ``n_floor is None`` it defaults to ``n`` — the original behavior where
    ``n`` is a hard floor and Pass 2 pads to it.

    Args:
        pool: list of PoolEntry from ``pool_ranker.rank_pool()``. NOT
            mutated by this function.
        history: dict mapping subtopic_id → last_shown_at ISO string (or
            None for cold-start). Use ``fetch_history`` to populate.
        n: ceiling — Pass 1 selects up to this many clone-free, parent-distinct
            subtopics. Default SELECTION_SIZE.
        n_floor: minimum selections Pass 2 force-fills to (admitting near-clones
            only if needed). Defaults to ``n`` (hard-floor / original behavior).
        near_clones: optional symmetric adjacency mapping each subtopic_id to
            the set of pool subtopic_ids it is a near-clone of
            (``theme_dedup.NearClones.adjacency``). None (the default)
            disables the near-clone gate — Pass 1 is then exactly the
            original parent-only selection, so existing callers are
            unaffected. An empty dict has the same effect and is what
            ``backfill_spotlight`` passes when the embedding step fails
            (graceful degradation, #91 plan §9.6).

    Returns:
        List of between ``n_floor`` and ``n`` Selection objects, each with a
        unique parent_topic.

    Raises:
        ValueError: if fewer than ``n_floor`` distinct parent topics are
            available in the pool ("selection floor failure").
    """
    if n_floor is None:
        n_floor = n
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
    selected_sids: set[str] = set()

    # Pass 1: parent-distinct AND (when near_clones is given) near-clone-free.
    for s in scored:
        sid = s.entry.subtopic_id
        if s.entry.parent_topic in parents:
            continue
        if near_clones is not None and (
            near_clones.get(sid, frozenset()) & selected_sids
        ):
            continue
        selected.append(s)
        parents.add(s.entry.parent_topic)
        selected_sids.add(sid)
        if len(selected) == n:
            break

    # Self-check (regression guard, #91): Pass 1's result must be pairwise
    # near-clone-free. A future refactor that drops the Pass-1 clone gate
    # trips this assertion. Pass 2 is intentionally exempt — it admits
    # near-clones when the pool is too thin to fill n slots without them.
    if near_clones is not None:
        for s in selected:
            overlap = (
                near_clones.get(s.entry.subtopic_id, frozenset())
                & selected_sids
            )
            assert not overlap, (
                f"rotation_selector Pass 1 invariant violated: selected "
                f"{s.entry.subtopic_id} alongside near-clone(s) "
                f"{sorted(overlap)} — the #91 near-clone gate regressed"
            )

    # Pass 2 (best-effort fill, #91): Pass 1 fell short of n because the
    # clone gate skipped valid candidates. Relax the clone gate, keep the
    # parent gate, and fill least-cloned-first so the publish doubles up on
    # the least-repeated theme only as forced — never just by raw sel_score.
    if len(selected) < n_floor:
        remaining = [
            s
            for s in scored
            if s.entry.subtopic_id not in selected_sids
            and s.entry.parent_topic not in parents
        ]
        while len(selected) < n_floor and remaining:
            # ``remaining`` stays in sel_score-DESC order, so min() returns
            # the first candidate achieving the lowest clone-overlap count —
            # i.e. sel_score is the natural tiebreaker.
            best = min(
                remaining,
                key=lambda s: _count_selected_near_clones(
                    s.entry.subtopic_id, near_clones, selected_sids
                ),
            )
            overlap = (
                near_clones.get(best.entry.subtopic_id, frozenset())
                & selected_sids
                if near_clones is not None
                else set()
            )
            if overlap:
                logger.warning(
                    "Rotation selector Pass 2: pool too thin for a fully "
                    "near-clone-free publish; admitting %s, a near-clone of "
                    "%s",
                    best.entry.subtopic_id,
                    sorted(overlap),
                )
            selected.append(best)
            parents.add(best.entry.parent_topic)
            selected_sids.add(best.entry.subtopic_id)
            # Drop ``best`` and any now parent-colliding candidate.
            remaining = [
                s for s in remaining if s.entry.parent_topic not in parents
            ]

    if len(selected) < n_floor:
        raise ValueError(
            f"selection floor failure: pool yields {len(selected)} distinct "
            f"parent topics; need {n_floor}. Distinct parents: {sorted(parents)}"
        )

    logger.info(
        "Rotation selector: %d selections from pool of %d, top sel_score=%.2f",
        len(selected),
        len(pool),
        selected[0].sel_score if selected else 0.0,
    )
    return selected

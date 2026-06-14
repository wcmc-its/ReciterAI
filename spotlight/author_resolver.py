"""Resolve first + last author pairs for PMIDs from analysis_summary_author.

B1 strict-author policy (per operator decision 2026-05-07): a paper enters
the spotlight pool only when BOTH its first author AND its last author are
WCM faculty resolvable in ``analysis_summary_author``. Papers where either
position is non-WCM (or missing from the table) are dropped from the pool.

This is the runtime alternative to backfilling first_author_*/last_author_*
fields onto every TOPIC# row; it costs one MariaDB round-trip per
``rank_pool`` call but keeps the artifact contract honest (schema's
``personIdentifier minLength: 1`` on both authors).

The byline string from ``analysis_summary_author.authors`` (varchar 1000) is
truncated for ~80% of papers, but the truncation pattern preserves the
last author at the end ("Smith J, Jones K, ..., Lyden D"), so first + last
names parse reliably regardless of truncation.
"""

from __future__ import annotations

import logging
import re
from typing import NamedTuple

logger = logging.getLogger(__name__)


class AuthorPair(NamedTuple):
    """Resolved first + last author pair for a single PMID."""

    first_person_identifier: str
    first_display_name: str
    last_person_identifier: str
    last_display_name: str


# ---------------------------------------------------------------------------
# Default SQLAlchemy engine — the shared hardened factory (#224)
# ---------------------------------------------------------------------------


def _get_default_engine():
    # #224: use utils.db.get_engine (pool_pre_ping + pool_recycle + connect/
    # read/write timeouts) instead of a second, un-hardened engine. It reads
    # the same DB_* env vars and is a singleton; the only DSN delta is the
    # correct ?charset=utf8mb4 it appends (a latent fix for byline mojibake).
    from utils.db import get_engine

    return get_engine()


# ---------------------------------------------------------------------------
# Byline parsing
# ---------------------------------------------------------------------------


_PAREN_WCM_MARKER = re.compile(r"\(\(([^)]+)\)\)")


def _strip_wcm_markers(name: str) -> str:
    """Drop the ``((author))`` markers used to highlight WCM-resolved authors."""
    return _PAREN_WCM_MARKER.sub(r"\1", name).strip()


def _parse_first_last_names(byline: str) -> tuple[str, str]:
    """Return ``(first_name, last_name)`` from an analysis_summary_author byline.

    Handles the truncation marker ``...`` by treating it as a sentinel: the
    segment immediately after the last ``...`` is the last author. Falls back
    to the final comma-separated segment when no truncation is present.

    Returns empty strings when the byline can't be split (single author, all
    whitespace, etc.) — caller decides how to handle.
    """
    if not byline:
        return "", ""
    cleaned = _strip_wcm_markers(byline.strip())

    # First author: text before the first ", ".
    first_split = cleaned.split(",", 1)
    first = first_split[0].strip()

    # Last author: prefer text after the LAST "..." sentinel; otherwise the
    # final comma segment.
    if "..." in cleaned:
        tail = cleaned.rsplit("...", 1)[-1]
    else:
        tail = cleaned
    last_split = tail.rsplit(",", 1)
    last = last_split[-1].strip()

    if first == last and "," not in cleaned:
        # Single-author paper.
        return first, ""

    return first, last


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


def resolve_authors(
    pmids: list[str],
    engine=None,
) -> dict[str, AuthorPair]:
    """Return ``{pmid: AuthorPair}`` only for PMIDs with BOTH WCM first AND last.

    Queries ``analysis_summary_author`` for the supplied PMIDs and groups by
    PMID. Returns an entry only when there is at least one row with
    ``authorPosition == "first"`` AND at least one with ``authorPosition ==
    "last"`` — both authors are then guaranteed to be WCM faculty (with
    valid personIdentifier values).

    PMIDs missing from the result map fail the B1 contract and should be
    dropped from the pool by the caller.

    Args:
        pmids: list of PMID strings (or ints) to resolve. Empty list returns {}.
        engine: optional SQLAlchemy engine for tests. None uses the lazy
            DB_USERNAME/DB_PASSWORD/DB_HOST/DB_NAME default.

    Returns:
        Dict keyed by pmid (str) with AuthorPair values; only B1-passing
        PMIDs are included.
    """
    if not pmids:
        return {}

    engine = engine or _get_default_engine()

    # Coerce to a sorted unique int list for IN-clause stability + plan reuse.
    int_pmids = sorted({int(p) for p in pmids})

    from sqlalchemy import text

    query = text(
        "SELECT pmid, personIdentifier, authorPosition, authors "
        "FROM analysis_summary_author "
        "WHERE pmid IN :pmids AND authorPosition IN ('first','last')"
    ).bindparams(pmids=tuple(int_pmids))

    with engine.connect() as conn:
        rows = conn.execute(query).fetchall()

    by_pmid: dict[str, dict] = {}
    for r in rows:
        pmid_str = str(r[0])
        person_identifier = (r[1] or "").strip()
        position = (r[2] or "").strip()
        byline = r[3] or ""
        slot = by_pmid.setdefault(pmid_str, {"byline": byline})
        if position == "first" and "first_pid" not in slot:
            slot["first_pid"] = person_identifier
        elif position == "last" and "last_pid" not in slot:
            slot["last_pid"] = person_identifier

    resolved: dict[str, AuthorPair] = {}
    for pmid_str, slot in by_pmid.items():
        first_pid = slot.get("first_pid")
        last_pid = slot.get("last_pid")
        if not (first_pid and last_pid):
            continue
        first_name, last_name = _parse_first_last_names(slot.get("byline", ""))
        resolved[pmid_str] = AuthorPair(
            first_person_identifier=first_pid,
            first_display_name=first_name,
            last_person_identifier=last_pid,
            last_display_name=last_name,
        )

    logger.info(
        "Author resolver: %d/%d PMIDs have both WCM first + last (B1 strict)",
        len(resolved),
        len(int_pmids),
    )
    # #224 fast WARN: a resolved/requested ratio below the floor is the symptom
    # of a degraded analysis_summary_author read (vs genuinely non-WCM bylines).
    # The hard abort stays downstream at rotation_selector's n_floor.
    if int_pmids:
        ratio = len(resolved) / len(int_pmids)
        try:
            from utils.env_check import load_thresholds

            min_ratio = float(
                load_thresholds().get("spotlight_author_resolve_min_ratio", 0.5)
            )
        except Exception:
            min_ratio = 0.5
        if ratio < min_ratio:
            logger.warning(
                "Author resolver: only %d/%d PMIDs resolved (%.0f%% < %.0f%% floor) "
                "— possible degraded analysis_summary_author read",
                len(resolved), len(int_pmids), ratio * 100, min_ratio * 100,
            )
    return resolved

"""Resolve first + last author pairs for PMIDs from analysis_summary_author.

Two B1 spotlight-pool rules, selected by the ``spotlight_b1_faculty_or_enabled``
threshold flag:

* Default (flag false) — legacy strict-AND: a paper enters the pool only when
  BOTH its first AND its last byline lead have a non-empty personIdentifier (the
  WCM-disambiguation presence proxy). Every emitted AuthorPair carries two
  non-empty pids, so the published artifact satisfies the schema's
  ``personIdentifier minLength: 1``. This is the behavior shipped before #231.

* Flag true — #231 fulltime-faculty OR: a paper qualifies when its first OR its
  last author is ``identity.fullTimeFaculty = 'yes'`` (joined on
  ``cwid = personIdentifier``), unlocking ~1,850 recent faculty papers the
  strict-AND rule suppressed (mostly faculty-last-author papers with an
  external/trainee first author). Papers whose leads are neither fulltime
  faculty are skipped and logged (``no_fulltime_faculty_lead``); their DDB
  substrate rows are untouched. Both byline leads are still emitted ("show
  both"); the displayed pid at each position prefers the fulltime-faculty author
  so the SPS headshot join lands on the faculty member.

The OR rule is OFF by default because its unlocked papers carry an EMPTY co-lead
personIdentifier (the external/trainee side has no WCM UID), which violates the
published schema's ``minLength: 1`` (publish.py hard-validates). Flip the flag only
after that minLength is relaxed (the #233 follow-up). SPS's spotlight render
re-derives authors from its own scholar DB by PMID and does NOT read the artifact
``personIdentifier``, so an empty co-lead pid is render-safe there — no SPS change.

Eligibility keys on ``fullTimeFaculty = 'yes'`` ONLY — it deliberately does NOT
filter on active/end-date status. "Active" is enforced one layer down by the
consumer at display time (SPS requires an active scholar among the PMID's authors),
so a second upstream active filter would be redundant and could drop faculty whose
end-date is a contract-renewal artifact. See docs/spotlight-contract.md
§"B1 Author Eligibility & the Two-Layer Active Check".

This is the runtime alternative to backfilling first_author_*/last_author_*
fields onto every TOPIC# row; it costs one MariaDB round-trip per
``rank_pool`` call.

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


def _faculty_or_enabled() -> bool:
    """Read the #231 B1 OR-rule flag. Default False ⇒ legacy strict-AND.

    Fails closed: any threshold-load error keeps the publish-safe default.
    """
    try:
        from utils.env_check import load_thresholds

        return bool(load_thresholds().get("spotlight_b1_faculty_or_enabled", False))
    except Exception:
        return False


def _warn_if_degraded(n_resolved: int, n_requested: int) -> None:
    """#224 fast WARN when resolved/requested falls below the configured floor.

    A low ratio is the symptom of a degraded analysis_summary_author read (vs
    genuinely non-WCM bylines). The hard abort stays downstream at
    rotation_selector's n_floor.
    """
    if not n_requested:
        return
    ratio = n_resolved / n_requested
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
            n_resolved, n_requested, ratio * 100, min_ratio * 100,
        )


def resolve_authors(
    pmids: list[str],
    engine=None,
) -> dict[str, AuthorPair]:
    """Return ``{pmid: AuthorPair}`` for B1-passing PMIDs (flag-selected rule).

    Dispatches on the ``spotlight_b1_faculty_or_enabled`` threshold flag:
    default False uses the legacy strict-AND rule (both leads present); True uses
    the #231 fulltime-faculty OR rule. See the module docstring for the full
    contract and why the OR rule is gated off by default.

    PMIDs missing from the result map fail the active B1 rule and should be
    dropped from the pool by the caller.

    Args:
        pmids: list of PMID strings (or ints) to resolve. Empty list returns {}.
        engine: optional SQLAlchemy engine for tests. None uses the lazy
            DB_USERNAME/DB_PASSWORD/DB_HOST/DB_NAME default.

    Returns:
        Dict keyed by pmid (str) with AuthorPair values; only B1-passing PMIDs
        are included.
    """
    if not pmids:
        return {}

    engine = engine or _get_default_engine()

    # Coerce to a sorted unique int list for IN-clause stability + plan reuse.
    int_pmids = sorted({int(p) for p in pmids})

    if _faculty_or_enabled():
        return _resolve_faculty_or(engine, int_pmids)
    return _resolve_strict_and(engine, int_pmids)


def _resolve_strict_and(engine, int_pmids: list[int]) -> dict[str, AuthorPair]:
    """Legacy B1 rule (default): keep PMIDs with BOTH first AND last present.

    Faculty membership is the legacy WCM-disambiguation proxy — a non-empty
    personIdentifier at the first AND the last byline position. Every emitted
    AuthorPair carries two non-empty pids, so the published artifact is
    schema-safe. This is the behavior shipped before #231, byte-identical to it.
    """
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
    _warn_if_degraded(len(resolved), len(int_pmids))
    return resolved


def _resolve_faculty_or(engine, int_pmids: list[int]) -> dict[str, AuthorPair]:
    """#231 B1 rule (flag-on): keep PMIDs with a fulltime-faculty first OR last.

    Joins ``analysis_summary_author`` to ``identity`` (LEFT, so the non-faculty
    co-lead's row survives for "show both") and qualifies a PMID when either
    byline lead is ``fullTimeFaculty = 'yes'``. The displayed pid at each
    position prefers the fulltime-faculty author. Emitted AuthorPairs MAY carry
    an empty co-lead pid (external/trainee) — which is why this path is gated off
    by default (schema ``minLength: 1`` + SPS headshot join). Papers with no
    fulltime-faculty lead are skipped and logged (``no_fulltime_faculty_lead``);
    their DDB substrate rows are untouched.
    """
    from sqlalchemy import text

    query = text(
        "SELECT a.pmid, a.personIdentifier, a.authorPosition, a.authors, "
        "id.fullTimeFaculty "
        "FROM analysis_summary_author a "
        "LEFT JOIN identity id ON id.cwid = a.personIdentifier "
        "WHERE a.pmid IN :pmids AND a.authorPosition IN ('first','last') "
        "ORDER BY a.pmid, a.authorPosition, a.personIdentifier"
    ).bindparams(pmids=tuple(int_pmids))

    with engine.connect() as conn:
        rows = conn.execute(query).fetchall()

    by_pmid: dict[str, dict] = {}
    for r in rows:
        position = (r[2] or "").strip()
        if position not in ("first", "last"):
            continue
        pmid_str = str(r[0])
        person_identifier = (r[1] or "").strip()
        byline = r[3] or ""
        # Guard the faculty signal on a non-empty pid: the qualifying join is
        # id.cwid = personIdentifier, so a blank-pid row can't legitimately be
        # faculty — and this stops a degenerate blank-cwid identity row from
        # clobbering a real co-lead pid below.
        is_faculty = bool(person_identifier) and (r[4] or "").strip().lower() == "yes"
        slot = by_pmid.setdefault(pmid_str, {"byline": byline})
        pid_key = f"{position}_pid"
        fac_key = f"{position}_faculty"
        if is_faculty:
            # A fulltime-faculty author at this position wins the displayed pid
            # so the SPS headshot join lands on the faculty member. First such
            # row wins (the SQL ORDER BY makes that deterministic).
            if not slot.get(fac_key):
                slot[pid_key] = person_identifier
                slot[fac_key] = True
        else:
            # "Show both as-is": keep the non-faculty co-lead's pid as a fallback
            # so it is emitted when this position has no faculty author.
            slot.setdefault(pid_key, person_identifier)

    resolved: dict[str, AuthorPair] = {}
    skipped: list[str] = []
    for pmid_str, slot in by_pmid.items():
        # OR rule: qualify when the first OR last author is fulltime faculty.
        if not (slot.get("first_faculty") or slot.get("last_faculty")):
            skipped.append(pmid_str)
            continue
        first_name, last_name = _parse_first_last_names(slot.get("byline", ""))
        resolved[pmid_str] = AuthorPair(
            first_person_identifier=slot.get("first_pid", ""),
            first_display_name=first_name,
            last_person_identifier=slot.get("last_pid", ""),
            last_display_name=last_name,
        )

    logger.info(
        "Author resolver (B1 OR): %d/%d PMIDs have a fulltime-faculty first or "
        "last author; %d skipped (no_fulltime_faculty_lead)",
        len(resolved),
        len(int_pmids),
        len(skipped),
    )
    if skipped:
        # Per-run skip audit (operator decision #231: flag-and-log, no review
        # rows). Skipped PMIDs stay in the DDB substrate and are only excluded
        # from the spotlight pool. INFO carries a sample; DEBUG the full list.
        logger.info(
            "Author resolver skip audit: %d PMIDs no_fulltime_faculty_lead "
            "(sample: %s)",
            len(skipped),
            ", ".join(sorted(skipped)[:20]),
        )
        logger.debug(
            "Author resolver no_fulltime_faculty_lead PMIDs: %s",
            ",".join(sorted(skipped)),
        )
    _warn_if_degraded(len(resolved), len(int_pmids))
    return resolved

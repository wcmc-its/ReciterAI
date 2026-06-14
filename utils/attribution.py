"""Live faculty-author attribution set (#222) — the single source of truth for
"is this (faculty, pmid) TOPIC# attribution still live?".

Used by the read-time live-intersection filters (cli/aggregate_subtopic_scores,
cli/count_by_cwid) so stale rows stop inflating user-facing scores WITHOUT
deleting anything, and — later, if ever built — by the destructive attribution
cull, which MUST share this exact predicate.

SCOPE PARITY (load-bearing — see tests/test_attribution_scope_parity.py):
the live set MUST be derived at the TOPIC# *mint* scope: ALL author positions
(first/last/middle/NULL), full-time faculty only — i.e. AUTHOR_MAPPING_SQL via
``extract_author_mapping``. It must NOT be derived from the first/last-scoped
helpers ``spotlight.author_resolver.resolve_authors`` /
``PMIDS_BY_CWID_SQL`` (``get_pmids_for_cwid``): the mint path
(utils.topic_records.build_topic_rows_for_pmid) writes one row per author
regardless of position, so a first/last-scoped live set would mark every
middle/NULL-author row stale and drop real attributions.

The empty/degraded-source abort guard lives in-body in
``extract_author_mapping`` (#224, ``corpus_read_floor_author_links``); this
module reuses that function rather than re-querying, so every consumer inherits
the floor for free.
"""

from __future__ import annotations

FACULTY_UID_PREFIX = "cwid_"


def strip_faculty_prefix(faculty_uid: str) -> str:
    """``cwid_abc1234`` -> ``abc1234``; pass through anything unprefixed."""
    fu = str(faculty_uid or "")
    if fu.startswith(FACULTY_UID_PREFIX):
        return fu[len(FACULTY_UID_PREFIX):]
    return fu


def pairs_from_author_mapping(author_mapping: dict) -> set[tuple[str, str]]:
    """Flatten ``{pmid: [{cwid, position}, ...]}`` to a ``{(cwid, pmid)}`` set.

    Position is intentionally dropped — the mint scope is all-positions, so the
    live set is keyed only on (cwid, pmid). Pure; no I/O.
    """
    pairs: set[tuple[str, str]] = set()
    for pmid, authors in (author_mapping or {}).items():
        pm = str(pmid)
        if not pm:
            continue
        for author in authors or []:
            cwid = str(author.get("cwid", "")) if isinstance(author, dict) else ""
            if cwid:
                pairs.add((cwid, pm))
    return pairs


def load_live_attribution_pairs(author_mapping: dict | None = None) -> set[tuple[str, str]]:
    """Return the live ``{(cwid, pmid)}`` attribution set at TOPIC# mint scope.

    Reuses ``score_publications.extract_author_mapping`` (which carries the #224
    under-floor abort guard) as the single source. The import is deferred so the
    lean read-time consumers don't pull the scoring/Bedrock stack at import time.

    Pass ``author_mapping`` to inject a precomputed mapping (tests, or a caller
    that already holds one) and skip the DB read.
    """
    if author_mapping is None:
        from score_publications import extract_author_mapping  # lazy: avoids Bedrock import
        author_mapping = extract_author_mapping()
    return pairs_from_author_mapping(author_mapping)


def is_stale(live_pairs: set[tuple[str, str]], faculty_uid: str, pmid: str) -> bool:
    """True iff ``(strip(faculty_uid), pmid)`` is provably NOT in the live set.

    Returns False for any row that does not yield a clean ``(cwid, pmid)`` — an
    un-resolvable row is never asserted stale, so read filters keep it and the
    future cull never deletes it (handoff: "skip + report, never stale"). This
    is the one predicate shared by the read-time filter (drop where stale) and
    the deferred cull (delete where stale).
    """
    cwid = strip_faculty_prefix(faculty_uid)
    pm = str(pmid or "")
    if not cwid or not pm:
        return False
    return (cwid, pm) not in live_pairs

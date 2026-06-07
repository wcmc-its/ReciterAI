"""A2 extraction corpus loader (docs/tool-classifier-spec.md).

Assembles the corpus the extraction harness sweeps: the WCM full-time-faculty
**lead/senior-authored Academic Articles ≥ 2020** — one row per paper, carrying
the faculty authors (CWID + role) who own it, plus the title + abstract.

This is composed from already-verified production queries, not a fresh schema
guess:
  - ``FACULTY_GAP_SCAN_SQL`` (utils.sql_queries) already returns every
    full-time-faculty first/last-author Academic-Article ≥2020 (CWID, PMID)
    pair — the exact ≈1,406-faculty / ≈8,146-paper scope. We add only
    ``au.authorPosition`` to tell lead (first) from senior (last).
  - ``fetch_publications_for_enrichment`` already returns the title +
    ``abstractVarchar`` + bibliometric columns for a PMID set, in the shape the
    extraction prompt reads.

The grouping/merge logic is pure (``group_authorship_rows`` / ``merge_corpus``)
so it unit-tests without a DB; only ``fetch_extraction_corpus`` touches the
(VPN-gated) RDS. Each output row is one PMID with an ``authors`` list — the
harness extracts tools once per paper and carries the author list onto every
mention, so the later per-faculty salience spread (§5) and rollup (§7.1) can
explode per (scholar, tool) without re-querying.
"""

from __future__ import annotations

import logging
from typing import Iterable

logger = logging.getLogger(__name__)

# FACULTY_GAP_SCAN_SQL + authorPosition (the only addition): one row per
# (CWID, PMID, position) over every full-time-faculty first/last-author
# Academic Article ≥2020. Author scope first/last, faculty scope
# fullTimeFaculty='yes', pub scope Academic Article + articleYear>=2020 —
# identical to the queries the onboarding detector / hot orchestrator use.
EXTRACTION_CORPUS_SQL = """
SELECT DISTINCT
    au.personIdentifier AS cwid,
    a.pmid AS pmid,
    au.authorPosition AS author_position
FROM analysis_summary_author au
JOIN identity id ON id.cwid = au.personIdentifier
JOIN analysis_summary_article a ON a.pmid = au.pmid
WHERE id.fullTimeFaculty = 'yes'
    AND au.authorPosition IN ('first', 'last')
    AND a.publicationTypeCanonical = 'Academic Article'
    AND a.articleYear >= 2020
ORDER BY a.pmid DESC, au.personIdentifier
"""

# first author = lead; last author = senior (the handoff's lead/senior framing).
POSITION_TO_ROLE = {"first": "lead", "last": "senior"}


def group_authorship_rows(rows: Iterable[dict]) -> dict[str, list[dict]]:
    """Group (cwid, pmid, author_position) rows into ``{pmid: [{cwid, author_role}]}``.

    Pure — no DB. A row with an unmapped position is skipped (the SQL already
    filters to first/last, so this is belt-and-suspenders). Authors are
    de-duplicated per PMID and kept in stable (role, cwid) order so the corpus
    is reproducible run to run.
    """
    by_pmid: dict[str, dict[tuple[str, str], dict]] = {}
    for r in rows:
        pmid = str(r.get("pmid", "")).strip()
        cwid = str(r.get("cwid", "")).strip()
        role = POSITION_TO_ROLE.get((r.get("author_position") or "").strip().lower())
        if not pmid or not cwid or role is None:
            continue
        by_pmid.setdefault(pmid, {})[(role, cwid)] = {"cwid": cwid, "author_role": role}
    return {
        pmid: [authors[k] for k in sorted(authors)]
        for pmid, authors in by_pmid.items()
    }


def merge_corpus(
    pub_rows: Iterable[dict],
    authorship_by_pmid: dict[str, list[dict]],
) -> list[dict]:
    """Attach the faculty ``authors`` list to each publication row (by PMID).

    Pure — no DB. A pub row whose PMID has no authorship entry (shouldn't happen,
    since the PMID set comes FROM the authorship rows) is carried through with an
    empty ``authors`` list and logged, never dropped.
    """
    out: list[dict] = []
    for row in pub_rows:
        pmid = str(row.get("pmid", "")).strip()
        authors = authorship_by_pmid.get(pmid, [])
        if not authors:
            logger.warning("corpus: pmid=%s has no faculty authorship row; carrying empty authors", pmid)
        out.append({**row, "authors": authors})
    return out


def fetch_extraction_corpus(engine, *, limit: int | None = None) -> list[dict]:
    """Load the A2 extraction corpus (VPN-gated RDS). Returns one row per PMID.

    Steps (each a verified query): run ``EXTRACTION_CORPUS_SQL`` → group by PMID
    → take the newest ``limit`` PMIDs if given (a deterministic top-N probe,
    newest-first, matching the existing PMID ordering) → fetch title/abstract via
    ``fetch_publications_for_enrichment`` → merge the author lists on.

    Args:
        engine: sqlalchemy Engine (``utils.db.get_engine()``).
        limit: cap to the newest N PMIDs for a probe; None = the full corpus.

    Returns:
        List of pub rows ``{pmid, articleTitle, journalTitleVerbose, articleYear,
        abstractVarchar, …, authors: [{cwid, author_role}]}`` — the shape
        ``run_extraction`` / the extraction prompt consume.
    """
    from sqlalchemy import text

    from utils.sql_queries import fetch_publications_for_enrichment

    with engine.connect() as conn:
        rows = conn.execute(text(EXTRACTION_CORPUS_SQL)).mappings().all()
    authorship = group_authorship_rows(dict(r) for r in rows)

    pmids = sorted(authorship, key=lambda p: int(p) if p.isdigit() else 0, reverse=True)
    if limit is not None:
        pmids = pmids[:limit]
        authorship = {p: authorship[p] for p in pmids}
    logger.info("corpus: %d distinct faculty PMID(s)%s", len(pmids),
                f" (limited to newest {limit})" if limit is not None else "")

    pub_rows = fetch_publications_for_enrichment(engine, pmids)
    corpus = merge_corpus(pub_rows, authorship)
    logger.info("corpus: resolved %d/%d PMID(s) to title+abstract rows", len(corpus), len(pmids))
    return corpus

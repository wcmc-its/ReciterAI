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

# Grants carry no first/last author position; the faculty linked to a grant via
# grant_provenance are tagged with this role. Grant-sourced mentions feed
# extraction/salience/family discovery but NOT the publication pub-filter
# (docs/tools-producer-model.md §grants); source_kind="grant" keeps them out.
GRANT_AUTHOR_ROLE = "investigator"

# NIH RePORTER grant corpus (ReciterDB grant_reporter_project + grant_provenance,
# both in the same reporting DB as analysis_summary_*). One row per (appl_id,
# faculty CWID) for WCM full-time faculty whose accepted pubs link to the grant;
# grouped to one extraction per grant project. abstract_text is the RePORTER
# project abstract retrieveReporter.py populates ("for future reciterdb-side
# consumers" — exactly this).
GRANT_CORPUS_SQL = """
SELECT DISTINCT
    grp.appl_id AS appl_id,
    grp.project_title AS project_title,
    grp.abstract_text AS abstract_text,
    gp.personIdentifier AS cwid
FROM grant_reporter_project grp
JOIN grant_provenance gp ON gp.appl_id = grp.appl_id
JOIN identity id ON id.cwid = gp.personIdentifier
WHERE id.fullTimeFaculty = 'yes'
    AND grp.abstract_text IS NOT NULL
    AND grp.abstract_text <> ''
ORDER BY grp.appl_id DESC, gp.personIdentifier
"""


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


# ---------------------------------------------------------------------------
# Grant corpus (NIH RePORTER) — the v1 secondary signal
# ---------------------------------------------------------------------------


def group_grant_rows(rows: Iterable[dict]) -> list[dict]:
    """Group (appl_id, project_title, abstract_text, cwid) rows into grant pub-rows.

    Pure — no DB. One row per grant project (``appl_id``), carrying the distinct
    full-time-faculty CWIDs linked to it as ``authors`` (role ``investigator`` —
    grants have no first/last position). The record id is namespaced
    ``grant:<appl_id>`` so grant ids never collide with publication PMIDs in the
    checkpoint or the downstream registry, and ``source_kind="grant"`` keeps the
    mention out of the publication pub-filter. The title/abstract are emitted in
    the same keys the extraction prompt reads (``articleTitle``/``abstractVarchar``).
    """
    by_appl: dict[str, dict] = {}
    for r in rows:
        appl = str(r.get("appl_id", "")).strip()
        cwid = str(r.get("cwid", "")).strip()
        if not appl:
            continue
        row = by_appl.get(appl)
        if row is None:
            row = {
                "pmid": f"grant:{appl}",
                "appl_id": appl,
                "source_kind": "grant",
                "articleTitle": (r.get("project_title") or "").strip(),
                "abstractVarchar": (r.get("abstract_text") or "").strip(),
                "authors": [],
                "_cwids": set(),
            }
            by_appl[appl] = row
        if cwid and cwid not in row["_cwids"]:
            row["_cwids"].add(cwid)
            row["authors"].append({"cwid": cwid, "author_role": GRANT_AUTHOR_ROLE})
    # Drop the helper set; keep authors in stable CWID order; newest grant first.
    out: list[dict] = []
    for appl in sorted(by_appl, key=lambda a: int(a) if a.isdigit() else 0, reverse=True):
        row = by_appl[appl]
        row.pop("_cwids", None)
        row["authors"].sort(key=lambda a: a["cwid"])
        out.append(row)
    return out


def fetch_grant_corpus(engine, *, limit: int | None = None) -> list[dict]:
    """Load the A2 grant corpus (VPN-gated RDS). One row per RePORTER grant project.

    Runs ``GRANT_CORPUS_SQL`` (full-time-faculty grants with a non-empty RePORTER
    abstract) → groups by ``appl_id`` → takes the newest ``limit`` if given.
    Returns rows in the same shape ``run_extraction`` consumes, tagged
    ``source_kind="grant"``.
    """
    from sqlalchemy import text

    with engine.connect() as conn:
        rows = conn.execute(text(GRANT_CORPUS_SQL)).mappings().all()
    grants = group_grant_rows(dict(r) for r in rows)
    if limit is not None:
        grants = grants[:limit]
    logger.info("grant corpus: %d grant project(s)%s", len(grants),
                f" (limited to newest {limit})" if limit is not None else "")
    return grants

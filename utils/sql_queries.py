"""
SQL query constants and DB connection helpers for ReCiter AI Chatbot pipeline.

All SQL queries extract data from ReciterDB (MariaDB) to feed the offline
scoring pipeline.

Security:
- DB credentials read from env vars only, never logged or hardcoded.
- get_engine() reads DB_HOST/DB_USERNAME/DB_PASSWORD/DB_NAME from os.environ.
- Missing required vars raise ValueError with instructions.
"""

import os

from utils.db import get_engine


# ---------------------------------------------------------------------------
# Publication extraction SQL (primary pipeline input)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Daily-enrichment delta query (#37 step 2)
# ---------------------------------------------------------------------------

NEW_PUBLICATIONS_FOR_ENRICHMENT_SQL = """
SELECT
    a.pmid,
    a.articleTitle,
    a.journalTitleVerbose,
    a.articleYear,
    a.datePublicationAddedToEntrez,
    a.citationCountNIH,
    a.percentileNIH,
    a.relativeCitationRatioNIH,
    r.abstractVarchar
FROM analysis_summary_article a
LEFT JOIN reporting_abstracts r ON r.pmid = a.pmid
WHERE a.publicationTypeCanonical = 'Academic Article'
  AND a.articleYear >= 2020
  AND a.pmid > :last_max_pmid
  AND EXISTS (
      SELECT 1
      FROM analysis_summary_author au
      JOIN identity id ON id.cwid = au.personIdentifier
      WHERE au.pmid = a.pmid
        AND id.fullTimeFaculty = 'yes'
  )
ORDER BY a.pmid ASC
LIMIT :limit_n
"""
# Returns the bibliometric+abstract row shape the impact prompt expects
# (per `pipeline_enrichment/impact.py:9-13`) plus what the synopsis prompt
# needs. The orchestrator uses this to fetch new PMIDs since the watermark;
# the new watermark after a successful run is MAX(pmid) of the returned set.
#
# Why an EXISTS subquery for the faculty filter, not a JOIN: a paper can
# have multiple WCM authors, and we want each PMID once. EXISTS short-
# circuits as soon as one match is found.
#
# The :limit_n parameter is a safety valve, NOT a pagination handle. The
# cost guard refuses anomalously large deltas before they hit the LLM, so
# limit_n should be set well above the cost-guard trip count (~500 papers
# at default rates). The orchestrator owns the value.


PUBLICATION_EXTRACTION_SQL = """
SELECT DISTINCT
    a1.pmid,
    a1.articleTitle AS title,
    r.abstractVarchar AS abstract
FROM analysis_summary_article a1
LEFT JOIN reporting_abstracts r ON r.pmid = a1.pmid
WHERE a1.publicationTypeCanonical = 'Academic Article'
    AND a1.articleYear >= 2020
ORDER BY a1.pmid DESC
"""
# Publication-level only — no author joins. Scoring is per-publication.
# Author mapping is a separate query (AUTHOR_MAPPING_SQL) so we can
# expand to middle authors without rescoring.
#
# Synopsis used to be INNER-joined here from `reciterai_synopsis`; #38
# moved the synopsis read to DynamoDB (`IMPACT#pmid_{pmid}` / SK SCORE,
# `synopsis` attribute). The MariaDB synopsis table is being decommissioned
# in #37 step 6; this query is now corpus + abstract only. The caller
# (`score_publications.extract_publications*`) post-joins synopsis from
# DDB via `utils.dynamodb_helpers.fetch_synopses_for_pmids`, dropping
# PMIDs without a synopsis to preserve the legacy INNER-JOIN semantics.


# ---------------------------------------------------------------------------
# Author mapping SQL (separate from scoring — expandable to middle authors)
# ---------------------------------------------------------------------------

AUTHOR_MAPPING_SQL = """
SELECT
    a.pmid,
    a.personIdentifier AS cwid,
    a.authorPosition
FROM analysis_summary_author a
JOIN identity id ON id.cwid = a.personIdentifier
WHERE id.fullTimeFaculty = 'yes'
ORDER BY a.pmid, a.personIdentifier
"""
# Returns all author positions (first, last, and middle) for WCM full-time
# faculty. The load step can filter by position if needed — currently uses
# first/last for v1, expandable to middle authors without rescoring.


# ---------------------------------------------------------------------------
# Synopsis extraction
# ---------------------------------------------------------------------------
# `SYNOPSIS_EXTRACTION_SQL` was removed in #38: taxonomy generation now
# reads synopses from DynamoDB (IMPACT# rows) via
# `utils.dynamodb_helpers.scan_all_synopses` instead of `reciterai_synopsis`.


# ---------------------------------------------------------------------------
# Faculty metadata SQL (for FACULTY# profile records)
# ---------------------------------------------------------------------------

FACULTY_METADATA_SQL = """
SELECT
    asp.personIdentifier AS cwid,
    CONCAT(asp.nameFirst, ' ', asp.nameLast) AS name,
    asp.department,
    asp.hindexNIH AS h_index,
    asp.countAll AS article_count,
    asp.countFirst AS first_author_count,
    asp.countSenior AS last_author_count
FROM analysis_summary_person asp
JOIN identity id ON id.cwid = asp.personIdentifier
WHERE id.fullTimeFaculty = 'yes'
ORDER BY asp.personIdentifier
"""
# Uses pre-computed counts from analysis_summary_person rather than
# re-aggregating from analysis_summary_author. Simpler and faster.


# ---------------------------------------------------------------------------
# Tool extraction SQL (for TOOL# records — from reciterai_tools)
# ---------------------------------------------------------------------------

TOOL_EXTRACTION_SQL = """
SELECT
    t.external_id AS pmid,
    t.raw_name AS tool_name,
    t.tool_category,
    t.confidence_tool,
    t.context
FROM reciterai_tools t
WHERE t.entity_type = 'publication'
    AND t.confidence_tool >= 7
ORDER BY t.external_id
"""
# Source: reciterai_tools — LLM-extracted tools/instruments/methods per publication.
# confidence_tool >= 7 filters out low-confidence extractions.
# Tool categories: instrument, software, reagent, assay, technique, dataset, etc.


# ---------------------------------------------------------------------------
# DB connection functions
# ---------------------------------------------------------------------------

def fetch_new_publications(engine, *, last_max_pmid: int, limit: int = 1000) -> list[dict]:
    """Fetch PMIDs eligible for daily enrichment, past the watermark (#37 step 2).

    Args:
        engine: sqlalchemy Engine (use get_engine()).
        last_max_pmid: only return papers with pmid > this value. Pass 0
            for first-ever runs.
        limit: safety cap on rows returned. Set well above the cost-guard
            trip count so the cost guard fires first on anomalies.

    Returns:
        List of dicts with the bibliometric + abstract columns required by
        the synopsis and impact prompts. Ordered by pmid ASC; the new
        watermark after a successful run is the last row's pmid.
    """
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        rows = conn.execute(
            _text(NEW_PUBLICATIONS_FOR_ENRICHMENT_SQL),
            {"last_max_pmid": int(last_max_pmid), "limit_n": int(limit)},
        ).mappings().all()
    return [dict(r) for r in rows]


def get_db_connection():
    """
    Get a SQLAlchemy connection to ReciterDB. Caller closes it.

    Credentials are read from environment variables (DB_HOST, DB_USERNAME,
    DB_PASSWORD, DB_NAME). Never logged or printed.
    """
    assert os.environ.get('DB_USERNAME'), (
        "DB_USERNAME environment variable not set -- check ~/.zshrc and run: source ~/.zshrc"
    )
    engine = get_engine()
    return engine.connect()


def get_raw_db_connection():
    """
    Get a raw pymysql connection for cursor-based database access.

    Use this when you need cursor-based access (e.g., fetchall(), fetchone())
    rather than SQLAlchemy's text() execute pattern.

    Returns:
        pymysql connection with DictCursor (rows as dicts).

    Raises:
        AssertionError: If DB_USERNAME environment variable is not set.
        KeyError: If any required env var (DB_HOST, DB_USERNAME, DB_PASSWORD, DB_NAME)
                  is not set.

    Security (T-01-01):
        Never logs or prints credential values.
    """
    import pymysql
    assert os.environ.get('DB_USERNAME'), (
        "DB_USERNAME environment variable not set -- check ~/.zshrc and run: source ~/.zshrc"
    )
    return pymysql.connect(
        host=os.environ['DB_HOST'],
        user=os.environ['DB_USERNAME'],
        password=os.environ['DB_PASSWORD'],
        database=os.environ['DB_NAME'],
        cursorclass=pymysql.cursors.DictCursor,
    )


# ---------------------------------------------------------------------------
# New-researcher onboarding (#80 Phase 1) — CWID scoping + synopsis coverage
# ---------------------------------------------------------------------------

PMIDS_BY_CWID_SQL = """
SELECT DISTINCT a.pmid
FROM analysis_summary_article a
JOIN analysis_summary_author au ON au.pmid = a.pmid
WHERE au.personIdentifier = :cwid
    AND au.authorPosition IN ('first', 'last')
    AND a.publicationTypeCanonical = 'Academic Article'
    AND a.articleYear >= 2020
ORDER BY a.pmid DESC
"""
# Per-CWID accepted-publication set for the onboarding workflow (#80 R2).
#
# Deliberately NOT a variant of PUBLICATION_EXTRACTION_SQL. That query
# INNER-joins reciterai_synopsis on a non-null synopsis — building the
# CWID query on top of it would silently drop exactly the synopsis-less
# PMIDs that the onboarding synopsis-precondition check (R3 step 1, see
# check_synopsis_coverage) exists to surface. This query returns the
# CWID's first/last-author Academic-Article set (articleYear >= 2020 per
# D4); synopsis coverage is a separate, explicit check.
#
# Author scope: first/last position only (`authorPosition IN ('first',
# 'last')`) — the v1 faculty-facing scope. spotlight/author_resolver.py
# applies the same SQL filter; the cold path scopes AUTHOR_MAPPING_SQL's
# output to first/last at load. `analysis_summary_author.authorPosition`
# is {first, last, NULL}; NULL is a middle author, excluded by the filter.
#
# `analysis_summary_author.personIdentifier` is the CWID column — the
# same join AUTHOR_MAPPING_SQL uses. The spec's "reporting_authorships"
# is this table.


def get_pmids_for_cwid(cwid: str) -> list[str]:
    """Return the accepted-publication PMID set for one CWID (#80 R2).

    Academic Articles only, articleYear >= 2020 (the D4 cutoff), first/last
    author position only (the v1 faculty-facing scope). PMIDs are returned
    as strings, newest first. Synopsis and score coverage are NOT
    filtered here — callers run `check_synopsis_coverage` and the
    PROCESSING# checkpoint separately.

    An empty/blank CWID, or a CWID with no accepted publications, returns
    an empty list.
    """
    if not cwid or not cwid.strip():
        return []
    from sqlalchemy import text

    conn = get_db_connection()
    try:
        rows = conn.execute(text(PMIDS_BY_CWID_SQL), {"cwid": cwid.strip()})
        return [str(row[0]) for row in rows]
    finally:
        conn.close()


SYNOPSIS_COVERAGE_SQL = """
SELECT external_id
FROM reciterai_synopsis
WHERE entity_type = 'publication'
    AND synopsis IS NOT NULL
    AND synopsis != ''
    AND external_id IN :pmid_list
"""
# Synopsis-precondition check for the onboarding workflow (#80 R3 step 1).
# `external_id` holds the PMID as varchar (per the A6 correction noted on
# PUBLICATION_EXTRACTION_SQL), so it is matched against stringified PMIDs.


def check_synopsis_coverage(pmids: list[str]) -> dict[str, list[str]]:
    """Partition `pmids` by whether a non-empty reciterai_synopsis row exists.

    Returns ``{"present": [...], "missing": [...]}`` — both lists sorted
    and stringified. The onboarding orchestrator (#80 R3 step 1) uses
    `missing` to decide whether to defer a run pending synopsis backfill.

    Empty input returns empty lists without opening a DB connection.
    """
    wanted = sorted({str(p) for p in pmids if str(p).strip()})
    if not wanted:
        return {"present": [], "missing": []}
    from sqlalchemy import bindparam, text

    stmt = text(SYNOPSIS_COVERAGE_SQL).bindparams(
        bindparam("pmid_list", expanding=True)
    )
    conn = get_db_connection()
    try:
        rows = conn.execute(stmt, {"pmid_list": wanted})
        present = {str(row[0]) for row in rows}
    finally:
        conn.close()
    return {
        "present": sorted(present),
        "missing": sorted(set(wanted) - present),
    }


# ---------------------------------------------------------------------------
# Onboarding detector — global faculty gap scan (#80 Phase 2, PR 5)
# ---------------------------------------------------------------------------

FACULTY_GAP_SCAN_SQL = """
SELECT DISTINCT
    au.personIdentifier AS cwid,
    a.pmid AS pmid,
    CASE WHEN s.external_id IS NOT NULL THEN 1 ELSE 0 END AS has_synopsis
FROM analysis_summary_author au
JOIN identity id ON id.cwid = au.personIdentifier
JOIN analysis_summary_article a ON a.pmid = au.pmid
LEFT JOIN reciterai_synopsis s
    ON s.external_id = CAST(a.pmid AS CHAR) COLLATE utf8mb4_unicode_ci
    AND s.entity_type = 'publication'
    AND s.synopsis IS NOT NULL
    AND s.synopsis != ''
WHERE id.fullTimeFaculty = 'yes'
    AND au.authorPosition IN ('first', 'last')
    AND a.publicationTypeCanonical = 'Academic Article'
    AND a.articleYear >= 2020
ORDER BY au.personIdentifier, a.pmid
"""
# The onboarding detector's R1 "global gap scan" (#80 PR 5). One row per
# (CWID, PMID) across every full-time-faculty accepted publication, each
# tagged with whether a non-empty reciterai_synopsis row exists.
#
# This is the SQL "outer loop" of the cross-store join R1 specifies: SQL is
# authoritative for "what publications should exist" for a CWID; the detector
# then BatchGetItems PROCESSING# rows in DynamoDB for the "what is scored"
# inner check. Score coverage is deliberately NOT joined here — it lives in
# DynamoDB, not MariaDB.
#
# Faculty scope (identity.fullTimeFaculty = 'yes') mirrors AUTHOR_MAPPING_SQL
# and resolves the spec's OQ-2. Author scope is first/last position only
# (`authorPosition IN ('first', 'last')`) — the v1 faculty-facing scope, the
# same filter spotlight/author_resolver.py and PMIDS_BY_CWID_SQL apply.
# Publication scope (Academic Article, articleYear >= 2020) matches
# PMIDS_BY_CWID_SQL / D4; with the matching position filter, a CWID's row
# set here is identical to what get_pmids_for_cwid would return for it.
#
# The synopsis LEFT JOIN repeats PUBLICATION_EXTRACTION_SQL's
# external_id = CAST(pmid AS CHAR) COLLATE utf8mb4_unicode_ci join (the A6
# correction): reciterai_synopsis.external_id is a varchar PMID.


def scan_faculty_publication_gaps() -> list[dict]:
    """Return every full-time-faculty first/last-author accepted publication
    with a synopsis flag.

    Drives the onboarding detector's global gap scan (#80 R1, PR 5). One dict
    per (CWID, PMID): ``{"cwid": str, "pmid": str, "has_synopsis": bool}``.

    `has_synopsis` reflects only the MariaDB synopsis precondition; score
    coverage is a separate DynamoDB PROCESSING# check the detector runs after
    this query. A CWID appears once per accepted PMID; a PMID appears once per
    first/last-author faculty CWID (the cross-institution co-authorship case
    is fine — each CWID is evaluated independently).

    Returns an empty list only when no full-time faculty have post-2020
    first/last-author Academic Articles — never the normal case.
    """
    from sqlalchemy import text

    conn = get_db_connection()
    try:
        rows = conn.execute(text(FACULTY_GAP_SCAN_SQL)).mappings().all()
    finally:
        conn.close()
    return [
        {
            "cwid": str(r["cwid"]),
            "pmid": str(r["pmid"]),
            "has_synopsis": bool(r["has_synopsis"]),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Enrichment backfill — explicit-PMID synopsis + impact (#112 / onboarding)
# ---------------------------------------------------------------------------

PUBLICATIONS_BY_PMIDS_FOR_ENRICHMENT_SQL = """
SELECT
    a.pmid,
    a.articleTitle,
    a.journalTitleVerbose,
    a.articleYear,
    a.datePublicationAddedToEntrez,
    a.citationCountNIH,
    a.percentileNIH,
    a.relativeCitationRatioNIH,
    r.abstractVarchar
FROM analysis_summary_article a
LEFT JOIN reporting_abstracts r ON r.pmid = a.pmid
WHERE a.publicationTypeCanonical = 'Academic Article'
  AND a.articleYear >= 2020
  AND a.pmid IN :pmid_list
ORDER BY a.pmid ASC
"""
# Explicit-PMID variant of NEW_PUBLICATIONS_FOR_ENRICHMENT_SQL for the
# enrichment backfill (#112). The SELECT column list is byte-identical: the
# backfill reuses `pipeline_enrichment.daily_job._process_one_pmid`, so the
# row must carry everything the synopsis AND impact prompts read (the impact
# prompt needs the bibliometric columns — see `pipeline_enrichment/impact.py`).
#
# Two deliberate differences from NEW_PUBLICATIONS_FOR_ENRICHMENT_SQL:
#   1. `pmid IN :pmid_list` replaces the `pmid > :last_max_pmid` watermark
#      window. The backfill targets an explicit historical work set — which
#      is the whole point, since the watermark cannot reach below itself.
#   2. No `fullTimeFaculty` EXISTS filter. The work set is already faculty-
#      and first/last-author-scoped (it comes from scan_faculty_publication_
#      gaps, or an operator's explicit list); the query trusts its input, the
#      same way score_publications' `extract_publications_by_pmids` does.
#
# `Academic Article` + `articleYear >= 2020` stay as the pipeline-wide
# invariant (D4): a pre-2020 or non-existent PMID is silently dropped, and
# the caller detects that by comparing returned vs requested counts.


def fetch_publications_for_enrichment(engine, pmids: list[str]) -> list[dict]:
    """Fetch the synopsis+impact input rows for an explicit PMID list (#112).

    Mirrors `fetch_new_publications`'s row shape, but scoped to `pmids`
    rather than the watermark window — backs `run_enrichment_backfill`.

    Args:
        engine: sqlalchemy Engine (use get_engine()).
        pmids: PMID strings. An empty list returns [] without a DB call.

    Returns:
        List of dicts with the bibliometric + abstract columns the synopsis
        and impact prompts require. PMIDs absent from
        `analysis_summary_article`, or filtered out by the
        `articleYear >= 2020` / Academic-Article cutoff, are silently
        omitted; the caller compares returned vs requested to find them.
    """
    if not pmids:
        return []
    from sqlalchemy import bindparam, text

    stmt = text(PUBLICATIONS_BY_PMIDS_FOR_ENRICHMENT_SQL).bindparams(
        bindparam("pmid_list", expanding=True)
    )
    with engine.connect() as conn:
        rows = conn.execute(
            stmt, {"pmid_list": [str(p) for p in pmids]}
        ).mappings().all()
    return [dict(r) for r in rows]


IMPACT_COVERAGE_SQL = """
SELECT external_id
FROM reciterai_impact
WHERE entity_type = 'publication'
    AND impactScore IS NOT NULL
    AND external_id IN :pmid_list
"""
# Impact-coverage check for the enrichment backfill's idempotency cull
# (#112) — the impact-side mirror of SYNOPSIS_COVERAGE_SQL. `external_id`
# holds the PMID as varchar (per the A6 correction noted on
# PUBLICATION_EXTRACTION_SQL), so it is matched against stringified PMIDs.


def check_enrichment_coverage(pmids: list[str]) -> dict[str, list[str]]:
    """Partition `pmids` by enrichment completeness (#112).

    A PMID is ``complete`` iff it has BOTH a non-empty `reciterai_synopsis`
    row AND a non-null `reciterai_impact` row; ``incomplete`` is missing
    either. `run_enrichment_backfill` uses ``incomplete`` as its idempotency
    cull — only those PMIDs are sent to the LLM (unless --force).

    Returns ``{"complete": [...], "incomplete": [...]}`` — both lists
    sorted, stringified, de-duplicated. Empty input returns empty lists
    without opening a DB connection.
    """
    wanted = sorted({str(p) for p in pmids if str(p).strip()})
    if not wanted:
        return {"complete": [], "incomplete": []}
    from sqlalchemy import bindparam, text

    syn_stmt = text(SYNOPSIS_COVERAGE_SQL).bindparams(
        bindparam("pmid_list", expanding=True)
    )
    imp_stmt = text(IMPACT_COVERAGE_SQL).bindparams(
        bindparam("pmid_list", expanding=True)
    )
    conn = get_db_connection()
    try:
        synopsis_present = {
            str(row[0]) for row in conn.execute(syn_stmt, {"pmid_list": wanted})
        }
        impact_present = {
            str(row[0]) for row in conn.execute(imp_stmt, {"pmid_list": wanted})
        }
    finally:
        conn.close()
    complete = synopsis_present & impact_present
    return {
        "complete": sorted(complete),
        "incomplete": sorted(set(wanted) - complete),
    }


# ---------------------------------------------------------------------------
# Hot path — PMID-set to dirty-CWID resolution (#119)
# ---------------------------------------------------------------------------

CWIDS_BY_PMIDS_SQL = """
SELECT DISTINCT au.personIdentifier AS cwid
FROM analysis_summary_author au
JOIN identity id ON id.cwid = au.personIdentifier
WHERE au.pmid IN :pmid_list
    AND id.fullTimeFaculty = 'yes'
    AND au.authorPosition IN ('first', 'last')
ORDER BY au.personIdentifier
"""
# The inverse of PMIDS_BY_CWID_SQL: given a publication set, return the
# full-time-faculty CWIDs whose first/last-author work intersects it. The
# hot orchestrator uses this to derive the per-run dirty-CWID set for the
# weekly Rollup fan-out (#119).
#
# Author scope is first/last position only — the v1 faculty-facing scope,
# matching PMIDS_BY_CWID_SQL and FACULTY_GAP_SCAN_SQL. fullTimeFaculty is
# filtered via the identity join, the same as AUTHOR_MAPPING_SQL.
#
# No analysis_summary_article join: unlike FACULTY_GAP_SCAN_SQL, the caller
# passes PMIDs already scoped to Academic Articles / articleYear >= 2020 —
# the hot orchestrator's delta query (and the retry sweep that feeds it)
# apply that filter upstream. Here the PMID set is itself the scope.


def get_cwids_for_pmids(pmids: list[str]) -> list[str]:
    """Return the full-time-faculty CWIDs attributed to any of `pmids` (#119).

    The inverse of `get_pmids_for_cwid`: given a publication set, return the
    faculty whose first/last-author work intersects it. The hot path uses
    this to derive the dirty-CWID set the weekly Rollup fan-out iterates.

    `pmids` is trusted to be pre-scoped to Academic Articles / articleYear
    >= 2020 (see `CWIDS_BY_PMIDS_SQL`). CWIDs are returned sorted and
    de-duplicated; an empty or blank-only input returns `[]` without
    opening a DB connection.
    """
    wanted = sorted({str(p) for p in pmids if str(p).strip()})
    if not wanted:
        return []
    from sqlalchemy import bindparam, text

    stmt = text(CWIDS_BY_PMIDS_SQL).bindparams(
        bindparam("pmid_list", expanding=True)
    )
    conn = get_db_connection()
    try:
        rows = conn.execute(stmt, {"pmid_list": wanted})
        return sorted({str(row[0]) for row in rows})
    finally:
        conn.close()

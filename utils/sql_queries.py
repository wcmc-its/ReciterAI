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
    s.synopsis,
    r.abstractVarchar AS abstract
FROM analysis_summary_article a1
JOIN reciterai_synopsis s ON s.external_id = CAST(a1.pmid AS CHAR) COLLATE utf8mb4_unicode_ci
    AND s.entity_type = 'publication'
    AND s.synopsis IS NOT NULL AND s.synopsis != ''
LEFT JOIN reporting_abstracts r ON r.pmid = a1.pmid
WHERE a1.publicationTypeCanonical = 'Academic Article'
    AND a1.articleYear >= 2020
ORDER BY a1.pmid DESC
"""
# Publication-level only — no author joins. Scoring is per-publication.
# Author mapping is a separate query (AUTHOR_MAPPING_SQL) so we can
# expand to middle authors without rescoring.
#
# NOTE: Uses s.external_id (NOT s.entity_id) for the reciterai_synopsis join.
# Rationale:
#   1. RESEARCH.md A6 correction explicitly specifies external_id
#   2. Schema: reciterai_synopsis.external_id is varchar(50) matching PMID format;
#      entity_id is bigint (internal numeric ID, not the PMID)


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
# Synopsis extraction SQL (for taxonomy generation input)
# ---------------------------------------------------------------------------

SYNOPSIS_EXTRACTION_SQL = """
SELECT external_id AS pmid, synopsis
FROM reciterai_synopsis
WHERE entity_type = 'publication'
    AND synopsis IS NOT NULL
    AND synopsis != ''
ORDER BY external_id
"""
# NOTE: Uses external_id AS pmid per A6 correction.
# external_id is the PMID for publication records (varchar(50)).


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
# Impact score extraction SQL (for IMPACT# records)
# ---------------------------------------------------------------------------

IMPACT_EXTRACTION_SQL = """
SELECT
    external_id AS pmid,
    impactScore AS impact_score,
    justification,
    model
FROM reciterai_impact
WHERE entity_type = 'publication'
    AND impactScore IS NOT NULL
ORDER BY external_id
"""
# Schema verified in env_check.py: keyword, relevanceScore, external_id, entity_type.


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
# CWID's full Academic-Article set (articleYear >= 2020 per D4); synopsis
# coverage is a separate, explicit check.
#
# `analysis_summary_author.personIdentifier` is the CWID column — the
# same join AUTHOR_MAPPING_SQL uses. The spec's "reporting_authorships"
# is this table.


def get_pmids_for_cwid(cwid: str) -> list[str]:
    """Return the accepted-publication PMID set for one CWID (#80 R2).

    Academic Articles only, articleYear >= 2020 (the D4 cutoff). PMIDs are
    returned as strings, newest first. Synopsis and score coverage are NOT
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
# and resolves the spec's OQ-2. Publication scope (Academic Article,
# articleYear >= 2020) matches PMIDS_BY_CWID_SQL / D4, so a CWID's row set
# here is identical to what get_pmids_for_cwid would return for it.
#
# The synopsis LEFT JOIN repeats PUBLICATION_EXTRACTION_SQL's
# external_id = CAST(pmid AS CHAR) COLLATE utf8mb4_unicode_ci join (the A6
# correction): reciterai_synopsis.external_id is a varchar PMID.


def scan_faculty_publication_gaps() -> list[dict]:
    """Return every full-time-faculty accepted publication with a synopsis flag.

    Drives the onboarding detector's global gap scan (#80 R1, PR 5). One dict
    per (CWID, PMID): ``{"cwid": str, "pmid": str, "has_synopsis": bool}``.

    `has_synopsis` reflects only the MariaDB synopsis precondition; score
    coverage is a separate DynamoDB PROCESSING# check the detector runs after
    this query. A CWID appears once per accepted PMID; a PMID appears once per
    co-authoring faculty CWID (the cross-institution co-authorship case is
    fine — each CWID is evaluated independently).

    Returns an empty list only when no full-time faculty have post-2020
    Academic Articles — never the normal case.
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

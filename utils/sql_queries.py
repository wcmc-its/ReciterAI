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

---
phase: 01-offline-pipeline
plan: 01
subsystem: infra
tags: [boto3, dynamodb, sqlalchemy, pymysql, bedrock, aws]

# Dependency graph
requires: []
provides:
  - utils/bedrock_client.py — BedrockClient with Converse API, lazy init, retry, JSON validation
  - utils/dynamodb_helpers.py — DynamoDB table creation (both GSIs), batch write, score SK builder, processing tracker
  - utils/sql_queries.py — Corrected extraction SQL (external_id join), DB connection via ReciterAI core/db.py
  - utils/env_check.py — DB schema pre-verification (DESCRIBE queries for 3 tables)
  - requirements.txt — Python dependencies for the integration repo
affects:
  - 01-02: generate_taxonomy.py depends on BedrockClient and sql_queries (SYNOPSIS_EXTRACTION_SQL)
  - 01-03: score_publications.py depends on BedrockClient, dynamodb_helpers, sql_queries
  - 01-04: load_dynamodb.py depends on all utils modules

# Tech tracking
tech-stack:
  added:
    - boto3>=1.42.0 (Bedrock Converse API + DynamoDB)
    - tqdm>=4.67.0 (progress bars for scoring run)
    - pymysql>=1.1.0 (raw DB access)
    - sqlalchemy>=2.0.0 (ReciterAI core/db.py dependency)
  patterns:
    - Lazy boto3 client init (no import-time side effects)
    - Exponential backoff retry: min(2**attempt, 30)s on ThrottlingException etc.
    - DynamoDB zero-padded score SK for lexicographic descending sort (SCORE#NNNN)
    - to_decimal() wrapper to prevent DynamoDB float-type rejection
    - sys.path.insert for ReciterAI core/db.py reuse (D-01)

key-files:
  created:
    - utils/__init__.py
    - utils/env_check.py
    - utils/bedrock_client.py
    - utils/dynamodb_helpers.py
    - utils/sql_queries.py
    - requirements.txt
    - .gitignore
  modified: []

key-decisions:
  - "BedrockClient uses lazy boto3 init — no credentials required at import time"
  - "PUBLICATION_EXTRACTION_SQL uses s.external_id (not s.entity_id) per A6 correction and inclusion_criteria.py pattern"
  - "to_decimal() uses Decimal(str(round(value, 4))) to prevent DynamoDB float type rejection"
  - "DynamoDB GSI FacultyTopicsIndex uses PK from main table as range key for flexible topic queries"
  - "get_raw_db_connection() added alongside get_db_connection() for cursor-based access patterns"

patterns-established:
  - "Pattern: Lazy boto3 client init — client is None in __init__, created on _get_client() first call"
  - "Pattern: SQL external_id join — reciterai_synopsis joins to PMID via external_id (varchar), not entity_id (bigint)"
  - "Pattern: DynamoDB score SK — SCORE#NNNN#ACTIVITY#pmid_{pmid} with min(int(score*1000), 9999).zfill(4)"
  - "Pattern: D-01 reuse — sys.path.insert(0, ReciterAI) then from core.db import get_engine"

requirements-completed: [PIPE-06, DB-01, DB-07, DB-08]

# Metrics
duration: 4min
completed: 2026-04-08
---

# Phase 1 Plan 01: Shared Utilities Summary

**BedrockClient (Converse API + retry), DynamoDB table helpers with 2 GSIs, and corrected ReciterDB SQL using external_id for synopsis join — all four utils modules importable from project root**

## Performance

- **Duration:** 4 min
- **Started:** 2026-04-08T20:20:41Z
- **Completed:** 2026-04-08T20:25:08Z
- **Tasks:** 3 (Task 0, Task 1, Task 2)
- **Files created:** 7

## Accomplishments
- BedrockClient with lazy boto3 init, exponential backoff (min(2**attempt, 30)s), JSON fence stripping, and single-retry on non-JSON responses
- DynamoDB table creation with FacultyTopicsIndex GSI (INCLUDE projection) and ProcessingByVersionIndex GSI (KEYS_ONLY), batch write in 25-item chunks, score SK builder, processing tracker
- Corrected PUBLICATION_EXTRACTION_SQL using `s.external_id` (not `s.entity_id`) per RESEARCH.md A6 correction — confirmed by ReciterAI/pipeline_publications/inclusion_criteria.py line 37
- env_check.py DESCRIBE-based schema verification resolves RESEARCH.md Open Questions 2-4 before any pipeline SQL runs

## Task Commits

Each task was committed atomically:

1. **Task 0: Environment pre-check** - `5b0f84b` (feat)
2. **Task 1: BedrockClient + requirements.txt + package marker** - `a8cdd27` (feat)
3. **Task 2: DynamoDB helpers + SQL queries** - `c8e9084` (feat)
4. **Chore: .gitignore** - `7969a6a` (chore)

**Plan metadata:** (docs commit, pending)

## Files Created/Modified
- `utils/env_check.py` - DESCRIBE-based schema pre-checks for 3 ReciterDB tables, asserts external_id exists on reciterai_synopsis
- `utils/__init__.py` - Package marker
- `utils/bedrock_client.py` - BedrockClient class with call(), call_json(), _call_with_retry(), HAIKU_MODEL, SONNET_MODEL constants
- `utils/dynamodb_helpers.py` - create_chatbot_table(), batch_write(), make_score_sk(), to_decimal(), mark_processing(), get_processing_status()
- `utils/sql_queries.py` - PUBLICATION_EXTRACTION_SQL, SYNOPSIS_EXTRACTION_SQL, FACULTY_METADATA_SQL, TOOL_EXTRACTION_SQL, get_db_connection(), get_raw_db_connection()
- `requirements.txt` - boto3, tqdm, pymysql, sqlalchemy
- `.gitignore` - Python artifacts and pipeline outputs

## Decisions Made
- BedrockClient uses lazy init (no boto3 client in __init__) to avoid import-time credential requirements
- PUBLICATION_EXTRACTION_SQL uses `s.external_id = CAST(a1.pmid AS CHAR)` — confirmed correct by ReciterAI/pipeline_publications/inclusion_criteria.py line 37 which uses `synopsis.external_id = m.external_id`
- Added `get_raw_db_connection()` alongside `get_db_connection()` for pipeline scripts that need cursor-based (fetchall/fetchone) DB access
- make_score_sk() uses `min(int(score * 1000), 9999).zfill(4)` — score 1.0 maps to '1000' (4 digits), never exceeds 4 chars

## Deviations from Plan

None — plan executed exactly as written.

## Issues Encountered
None.

## User Setup Required
None — no external service configuration required for this plan. DB credentials must already be set in ~/.zshrc (verified by env_check.py's DB_USER assertion).

## Next Phase Readiness
- All utils modules are importable and tested
- BedrockClient ready for generate_taxonomy.py (Plan 02) and score_publications.py (Plan 03)
- DynamoDB helpers ready for load_dynamodb.py (Plan 04) and DynamoDB table creation in Plan 02
- SQL queries ready for data extraction in Plans 02-04
- env_check.py should be run first to validate DB schema before any pipeline script executes

---
*Phase: 01-offline-pipeline*
*Completed: 2026-04-08*

## Self-Check: PASSED

Files verified:
- utils/env_check.py: EXISTS
- utils/__init__.py: EXISTS
- utils/bedrock_client.py: EXISTS
- utils/dynamodb_helpers.py: EXISTS
- utils/sql_queries.py: EXISTS
- requirements.txt: EXISTS
- .gitignore: EXISTS

Commits verified:
- 5b0f84b: EXISTS (feat(01-01): add env_check.py)
- a8cdd27: EXISTS (feat(01-01): add BedrockClient)
- c8e9084: EXISTS (feat(01-01): add DynamoDB helpers and SQL queries)
- 7969a6a: EXISTS (chore(01-01): add .gitignore)

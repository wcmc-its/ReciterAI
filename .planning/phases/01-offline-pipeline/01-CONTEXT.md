# Phase 1: Offline Pipeline - Context

**Gathered:** 2026-04-08
**Status:** Ready for planning

<domain>
## Phase Boundary

Generate a 50-topic taxonomy from WCM publication data, score ~10K publications against that taxonomy via AWS Bedrock, and load all scored results into a single DynamoDB table with 6 record types and 2 GSIs. The pipeline runs offline (one-time initial load) and produces the data layer that the chat runtime (Phase 2) queries.

</domain>

<decisions>
## Implementation Decisions

### Code Organization & Reuse
- **D-01:** Import the existing ReciterAI repo as a dependency (`pip install -e` or `sys.path`). Reuse `core/db.py`, `core/data_retrieval.py`, and `core/processing_registry.py` directly. New Bedrock client and scoring logic live in this integration repo.
- **D-02:** Flat script structure — top-level `generate_taxonomy.py`, `score_publications.py`, `load_dynamodb.py` with shared utilities in a `utils/` folder. Matches the 3-phase pipeline in the design spec.
- **D-03:** ReciterDB credentials via standard environment variables (`DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`).
- **D-04:** Bedrock client adapted from CViche's `llm_client.py` — port into a new `utils/bedrock_client.py` with boto3 Converse API, lazy client init, exponential backoff retries, and scoring-specific prompt formatting. This module will also be reusable for Phase 2's runtime LLM calls.

### Taxonomy Curation Workflow
- **D-05:** Generate taxonomy from all ~10K existing synopses in the `reciterai_synopsis` table (not 500 raw abstracts as in the original spec). Synopses are already distilled summaries — concentrated signal across all WCM research output. Batch synopses into chunks (50-100 per LLM call), extract topic clusters per batch via Bedrock Sonnet, then run a consolidation pass to merge into ~50 final topics.
- **D-06:** Existing `reciterai_theme` table contains subtopics within specific themes (e.g., aging), not a broad institutional taxonomy. Start fresh from synopses rather than seeding from existing themes. Theme data may inform the aging domain deep dive record.
- **D-07:** Review gate before scoring — output taxonomy to a JSON file (`taxonomy_v1.json`). User reviews and optionally edits before running the scoring step. Prevents bad taxonomy from burning ~$35 in Bedrock scoring costs.
- **D-08:** Automated validation — lightweight validation prompt gives Sonnet a sample query + the taxonomy and asks which topics match. Script runs 10-15 sample queries and reports match quality. If matches look poor, taxonomy is revised before freezing.

### Pipeline Resilience
- **D-09:** Use DynamoDB PROCESSING# records as checkpoint state. On restart, query processing tracker for `status != 'complete'` and skip already-scored publications. The design spec already defines this record type — this gives it operational value beyond audit state.
- **D-10:** Console progress bar (tqdm or rich) showing publications scored / total, estimated time remaining, and current success/failure counts. Interactive and informative for the 2-4 hour run.
- **D-11:** On permanent failure (3 retries exceeded): mark as `status: failed` in processing tracker with error details, continue scoring the rest. Print failure summary at end of run. Target: <1% failure rate.

### DynamoDB Provisioning & Testing
- **D-12:** `load_dynamodb.py` creates the DynamoDB table + both GSIs if they don't exist (boto3 `create_table`). Self-contained, no CDK dependency. Appropriate for demo timeline.
- **D-13:** On-demand capacity mode — pay-per-request, no capacity planning. Free tier covers demo-level storage and read volumes.
- **D-14:** Test directly against real AWS resources (Bedrock + DynamoDB) in the dev AWS account. Small test batch (e.g., 50 publications) to validate end-to-end before the full run. No mocking infrastructure.

### Claude's Discretion
- Exact batching strategy for taxonomy generation (chunk size, overlap handling)
- tqdm vs. rich for progress bar
- Prompt engineering for screening and dense scoring calls
- batch_write_item batch size for DynamoDB loading
- Exact SQL queries for ReciterDB extraction (guided by existing ReciterAI patterns)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Design Spec
- `docs/superpowers/specs/2026-04-08-reciterai-chatbot-design.md` §3-5 — DynamoDB schema (all 6 record types + 2 GSIs), topic taxonomy generation process, data pipeline phases (extract, score, load)
- `docs/superpowers/specs/2026-04-08-chatbot-design-part2.md` — Additional design details

### Bedrock Reference Implementation
- `~/Dropbox/GitHub/CViche/src/unified_pipeline/llm_client.py` — Working Bedrock integration using boto3 Converse API with lazy client init, exponential backoff retries, and normalized response format. Port and adapt for scoring pipeline.

### Existing ReciterAI Codebase (import dependency)
- `~/Dropbox/GitHub/ReciterAI/core/db.py` — MariaDB connection management
- `~/Dropbox/GitHub/ReciterAI/core/data_retrieval.py` — Publication data extraction from ReciterDB
- `~/Dropbox/GitHub/ReciterAI/core/processing_registry.py` — Processing state tracking
- `~/Dropbox/GitHub/ReciterAI/core/sql.py` — SQL query definitions
- `~/Dropbox/GitHub/ReciterAI/core/error_handling.py` — Retry and error handling patterns

### ReciterDB Tables (data sources)
- `reciterai_synopsis` — Per-publication synopses (~10K records, input to taxonomy generation)
- `reciterai_entities` — Entity records linking publications to faculty
- `reciterai_impact` — Impact scores per publication
- `reciterai_keyword_relevance` — Keyword relevance scores
- `reciterai_theme` / `reciterai_theme_assignments` — Existing theme subtopics (aging domain, narrow scope)
- `analysis_summary_person` — Faculty metadata (h-index, article counts, department)
- `analysis_summary_author` — Author position data (first/last author filter)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `ReciterAI/core/db.py`: Database connection management with environment variable configuration — import directly
- `ReciterAI/core/data_retrieval.py`: Tested SQL extraction patterns for publications from ReciterDB
- `ReciterAI/core/processing_registry.py`: Processing state tracking — pattern to adapt for DynamoDB tracker records
- `ReciterAI/core/error_handling.py`: Retry logic with exponential backoff — pattern already proven
- `CViche/src/unified_pipeline/llm_client.py`: boto3 Converse API integration — port to new bedrock_client.py

### Established Patterns
- ReciterAI uses flat scripts with shared core modules — matches the chosen flat script structure
- ReciterDB access via environment variables and direct MariaDB connections
- Existing pipeline uses batch processing with progress logging

### Integration Points
- ReciterDB (MariaDB) as read-only data source for publication extraction
- AWS Bedrock as LLM provider (Haiku for screening, Sonnet for dense scoring + taxonomy generation)
- DynamoDB as output target for scored results
- Processing tracker records in DynamoDB as operational checkpoint state

</code_context>

<specifics>
## Specific Ideas

- Use all 10K existing synopses from `reciterai_synopsis` for taxonomy generation (user's suggestion) — better signal than sampling raw abstracts since synopses are already distilled by the existing ReciterAI pipeline
- Existing `reciterai_theme` table has aging subtopics that may inform the aging deep dive record but are too narrow for the broad institutional taxonomy
- Bedrock client module should be designed for reuse in Phase 2 (chat runtime LLM calls)

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 01-offline-pipeline*
*Context gathered: 2026-04-08*

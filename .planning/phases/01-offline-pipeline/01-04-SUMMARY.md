---
phase: 01-offline-pipeline
plan: 04
status: complete
started: 2026-04-09
completed: 2026-04-09
---

## What Was Built

`load_dynamodb.py` — loads 8 record types into DynamoDB:

| Type | Count | Source |
|------|-------|--------|
| TOPIC# | 78,103 | LLM-scored domain relevance per pub-topic-author |
| FACULTY# | 1,560 | Faculty profiles with top_topics vector |
| TOOL# | 14,721 | reciterai_tools (instruments, software, reagents, models) |
| TOOL_INDEX# | 15 | Canonicalized tools by functional category (175 tools) |
| IMPACT# | 7,097 | Publication impact scores + justifications |
| DEEPDIVE# | 1 | Aging domain placeholder |
| TAXONOMY# | 1 | 67 domain topics for runtime lookup |
| PROCESSING# | 6,569 | Scoring tracker (written during Plan 03) |

## Key Architecture Decisions

- TOOL# sourced from reciterai_tools (not reciterai_keyword_relevance) — real instruments, reagents, methods
- TOOL_INDEX# provides canonicalized tool catalog by functional category (imaging, sequencing, computational, etc.)
- IMPACT# as independent record type — decoupled from topics, used for weighting/prioritization
- TAXONOMY# stored in DynamoDB for runtime lookup — versioned with scores
- SK includes cwid to avoid duplicate key errors on co-authored publications
- GSIs updated: FacultyIndex (ALL projection), PmidIndex (new), ProcessingByVersionIndex

## Issues Encountered

- Duplicate PK/SK on co-authored papers — fixed by adding cwid to SK
- TOOL_EXTRACTION_SQL collation mismatch — fixed with COLLATE
- reciterai_keyword_relevance was mostly aging keywords — switched to reciterai_tools
- Tool name deduplication — 230 high-frequency tools canonicalized to 175 via Sonnet pass
- GSI projection was stale — recreated table with ALL projection and new PmidIndex

## Self-Check: PASSED

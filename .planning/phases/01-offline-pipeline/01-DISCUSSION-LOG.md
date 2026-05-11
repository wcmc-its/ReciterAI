# Phase 1: Offline Pipeline - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-08
**Phase:** 01-offline-pipeline
**Areas discussed:** Code organization & reuse, Taxonomy curation workflow, Pipeline resilience, DynamoDB provisioning & testing

---

## Code Organization & Reuse

| Option | Description | Selected |
|--------|-------------|----------|
| Import from ReciterAI | Add as dependency, reuse db.py, data_retrieval.py, processing_registry.py directly | ✓ |
| Copy & adapt key modules | Copy modules into this repo, no cross-repo dependency | |
| Fresh implementation | Write everything from scratch, reference CViche and ReciterAI as inspiration only | |

**User's choice:** Import from ReciterAI
**Notes:** Reuse existing DB, data retrieval, and processing registry modules. New Bedrock client and scoring logic in this repo.

| Option | Description | Selected |
|--------|-------------|----------|
| Flat scripts | Top-level generate_taxonomy.py, score_publications.py, load_dynamodb.py with utils/ folder | ✓ |
| Package with CLI | chatbot_pipeline/ package with submodules and CLI entrypoint | |
| Single orchestrator script | One main.py with flags to skip phases | |

**User's choice:** Flat scripts
**Notes:** Matches the 3-phase pipeline in the design spec.

| Option | Description | Selected |
|--------|-------------|----------|
| Environment variables | Standard DB_HOST, DB_USER, DB_PASSWORD, DB_NAME env vars | ✓ |
| Reuse ReciterAI's config.py | Import config.py which reads env vars and provides get_db_connection() | |
| You decide | Claude picks | |

**User's choice:** Environment variables

| Option | Description | Selected |
|--------|-------------|----------|
| Adapt CViche pattern | Port llm_client.py into new bedrock_client.py with scoring-specific prompt formatting | ✓ |
| Minimal inline client | boto3 calls directly in score_publications.py | |
| You decide | Claude picks | |

**User's choice:** Adapt CViche pattern

---

## Taxonomy Curation Workflow

| Option | Description | Selected |
|--------|-------------|----------|
| Generate + review file | Script generates taxonomy to JSON/YAML, user reviews and edits | |
| Interactive CLI refinement | Generate, show in terminal, prompt for merges/adds/removes | |
| Multi-pass LLM refinement | Automated generate -> self-critique -> refine cycle | |

**User's choice:** (Custom) Generate from existing 10K synopses in reciterai_synopsis table
**Notes:** User suggested using existing synopses as basis instead of sampling raw abstracts. Better signal since synopses are already distilled. Batch into chunks, extract topic clusters per batch, consolidation pass to ~50 topics. Output to reviewable JSON file.

| Option | Description | Selected |
|--------|-------------|----------|
| Manual spot-check | Output taxonomy + sample queries, user reviews mapping | |
| Automated scoring | Run sample queries through validation prompt, score topic matches | ✓ |
| You decide | Claude picks | |

**User's choice:** Automated scoring
**Notes:** Lightweight validation prompt — Sonnet gets sample query + taxonomy, returns topic matches.

| Option | Description | Selected |
|--------|-------------|----------|
| ReciterDB tables | Synopses in reciterai_* tables, query directly | ✓ |
| Local batch output files | JSON files from previous pipeline runs | |
| Both — DB is canonical | Both places but DB is truth | |

**User's choice:** ReciterDB tables
**Notes:** All in the reciterai_* namespace. Check docs for specific table names.

| Option | Description | Selected |
|--------|-------------|----------|
| Review gate | Output taxonomy to JSON, review/edit before scoring | ✓ |
| Straight to scoring | Generate and immediately start scoring | |

**User's choice:** Review gate
**Notes:** Prevents bad taxonomy from burning ~$35 in Bedrock scoring costs.

| Option | Description | Selected |
|--------|-------------|----------|
| Start fresh from synopses | Ignore existing themes, generate purely from 10K synopses | ✓ |
| Use themes as seed | Read existing themes as starting point, refine using synopses | |
| You decide | Claude evaluates theme quality | |

**User's choice:** Start fresh from synopses
**Notes:** Existing reciterai_theme table contains subtopics within a single theme (aging), not a broad institutional taxonomy. Too narrow for chatbot needs. May have overlap given another theme.

---

## Pipeline Resilience

| Option | Description | Selected |
|--------|-------------|----------|
| Processing tracker resume | Use PROCESSING# records as checkpoint state, skip already-scored on restart | ✓ |
| Local checkpoint file | JSON checkpoint after each batch, resume from file | |
| No resume — rerun from scratch | Idempotent DynamoDB overwrites, accept re-scoring cost | |

**User's choice:** Processing tracker resume
**Notes:** Design spec already defines PROCESSING# records — gives them operational value.

| Option | Description | Selected |
|--------|-------------|----------|
| Console progress bar | tqdm/rich showing scored/total, ETA, success/failure counts | ✓ |
| Periodic log lines | Status line every N publications | |
| You decide | Claude picks | |

**User's choice:** Console progress bar

| Option | Description | Selected |
|--------|-------------|----------|
| Log and continue | Mark failed in tracker, continue, print summary at end | ✓ |
| Pause and alert | Pause if failure rate exceeds threshold | |
| Both — log + threshold pause | Log individuals, pause at 5% cumulative failure | |

**User's choice:** Log and continue
**Notes:** Target <1% failure rate.

---

## DynamoDB Provisioning & Testing

| Option | Description | Selected |
|--------|-------------|----------|
| Script creates table | load_dynamodb.py creates table + GSIs via boto3 if not exist | ✓ |
| AWS CDK | Define in ReCiter-CDK repo | |
| Manual via AWS Console | Create manually | |

**User's choice:** Script creates table
**Notes:** Self-contained, no CDK dependency. Good for demo timeline.

| Option | Description | Selected |
|--------|-------------|----------|
| On-demand | Pay-per-request, no capacity planning | ✓ |
| Provisioned with auto-scaling | Set baseline with auto-scaling | |
| You decide | Claude picks | |

**User's choice:** On-demand

| Option | Description | Selected |
|--------|-------------|----------|
| Real AWS resources | Test against real Bedrock + DynamoDB, small test batch | ✓ |
| DynamoDB Local + mock Bedrock | Docker DynamoDB Local, canned Bedrock responses | |
| You decide | Claude picks | |

**User's choice:** Real AWS resources
**Notes:** Small test batch (50 publications) to validate end-to-end before full run.

---

## Claude's Discretion

- Exact batching strategy for taxonomy generation (chunk size, overlap handling)
- tqdm vs. rich for progress bar
- Prompt engineering for screening and dense scoring calls
- batch_write_item batch size for DynamoDB loading
- Exact SQL queries for ReciterDB extraction

## Deferred Ideas

None — discussion stayed within phase scope

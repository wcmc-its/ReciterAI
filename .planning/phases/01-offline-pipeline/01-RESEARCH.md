# Phase 1: Offline Pipeline - Research

**Researched:** 2026-04-08
**Domain:** Python batch pipeline — AWS Bedrock scoring, DynamoDB loading, ReciterDB extraction
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Code Organization & Reuse**
- D-01: Import the existing ReciterAI repo as a dependency (`pip install -e` or `sys.path`). Reuse `core/db.py`, `core/data_retrieval.py`, and `core/processing_registry.py` directly. New Bedrock client and scoring logic live in this integration repo.
- D-02: Flat script structure — top-level `generate_taxonomy.py`, `score_publications.py`, `load_dynamodb.py` with shared utilities in a `utils/` folder. Matches the 3-phase pipeline in the design spec.
- D-03: ReciterDB credentials via standard environment variables (`DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`).
- D-04: Bedrock client adapted from CViche's `llm_client.py` — port into a new `utils/bedrock_client.py` with boto3 Converse API, lazy client init, exponential backoff retries, and scoring-specific prompt formatting. This module will also be reusable for Phase 2's runtime LLM calls.

**Taxonomy Curation Workflow**
- D-05: Generate taxonomy from all ~10K existing synopses in the `reciterai_synopsis` table (not 500 raw abstracts). Batch synopses into chunks (50-100 per LLM call), extract topic clusters per batch via Bedrock Sonnet, then run a consolidation pass to merge into ~50 final topics.
- D-06: Start fresh from synopses rather than seeding from existing `reciterai_theme` table. Theme data may inform the aging domain deep dive record.
- D-07: Review gate before scoring — output taxonomy to `taxonomy_v1.json`. User reviews and optionally edits before running the scoring step.
- D-08: Automated validation — lightweight validation prompt gives Sonnet a sample query + the taxonomy and asks which topics match. Script runs 10-15 sample queries and reports match quality.

**Pipeline Resilience**
- D-09: Use DynamoDB PROCESSING# records as checkpoint state. On restart, query processing tracker for `status != 'complete'` and skip already-scored publications.
- D-10: Console progress bar (tqdm or rich) for the 2-4 hour run.
- D-11: On permanent failure (3 retries exceeded): mark as `status: failed` in processing tracker with error details, continue scoring the rest. Print failure summary at end of run. Target: <1% failure rate.

**DynamoDB Provisioning & Testing**
- D-12: `load_dynamodb.py` creates the DynamoDB table + both GSIs if they don't exist (boto3 `create_table`). Self-contained, no CDK dependency.
- D-13: On-demand capacity mode — pay-per-request, no capacity planning.
- D-14: Test directly against real AWS resources (Bedrock + DynamoDB) in the dev AWS account. Small test batch (e.g., 50 publications) to validate end-to-end before the full run. No mocking infrastructure.

### Claude's Discretion
- Exact batching strategy for taxonomy generation (chunk size, overlap handling)
- tqdm vs. rich for progress bar
- Prompt engineering for screening and dense scoring calls
- batch_write_item batch size for DynamoDB loading
- Exact SQL queries for ReciterDB extraction (guided by existing ReciterAI patterns)

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| TAX-01 | Generate ~50-topic taxonomy from WCM publication data via Bedrock Sonnet | Taxonomy generation pattern documented in §Architecture Patterns. Synopses batch strategy in §Standard Stack. |
| TAX-02 | Validate taxonomy against 10-15 sample queries a research dean would ask | Validation prompt pattern documented. Sample query set in §Code Examples. |
| TAX-03 | Freeze taxonomy as `taxonomy_v1` with version field on every score record | JSON freeze pattern documented. Version propagation in DynamoDB schema §. |
| PIPE-01 | Extract ~10K publications from ReciterDB (WCM full-time faculty, first/last author, accepted/asserted) | SQL pattern verified in ReciterAI codebase. fullTimeFaculty filter confirmed as `identity.fullTimeFaculty = 'yes'`. |
| PIPE-02 | Extract faculty metadata from ReciterDB (`analysis_summary_person`) | Table structure verified in ReciterAI SQL patterns. |
| PIPE-03 | Screen all publications against 50 topics via Bedrock Haiku (one call per pub) | One-call-per-pub pattern documented. Haiku model ID confirmed ACTIVE. |
| PIPE-04 | Dense-score publications passing 0.3+ threshold via Bedrock Sonnet | Two-pass pattern documented. Sonnet model ID confirmed ACTIVE. |
| PIPE-05 | Reuse existing ReciterAI pipeline output (synopses, impact scores, tools) | `reciterai_synopsis` table confirmed. Existing data_retrieval.py pattern verified. |
| PIPE-06 | Retry failed Bedrock calls up to 3 times with exponential backoff | CViche `llm_client.py` retry pattern verified and ready to port. |
| DB-01 | Create single DynamoDB table `reciterai-chatbot` with topic-first key design | DynamoDB access verified. Table does not yet exist. `create_table` pattern documented. |
| DB-02 | Load topic score records (PK: `TOPIC#X`, SK: `SCORE#NNNN#ACTIVITY#pmid_Y`, scores >= 0.3) | Zero-padded SK pattern for lexicographic sort documented. |
| DB-03 | Load faculty profile records (PK: `FACULTY#cwid_X`, SK: `PROFILE`) | Faculty metadata query pattern from `analysis_summary_person` documented. |
| DB-04 | Load tool score records (PK: `TOOL#X`, same pattern as topics) | Tool scores available from `reciterai_keyword_relevance` or `reciterai_tools` tables. |
| DB-05 | Load canned deep dive record for aging domain | Deep dive schema documented. `review_status: pending` on creation. |
| DB-06 | Load processing tracker records (PK: `PROCESSING#pmid_X`, SK: `STATUS`) | DynamoDB-based tracker (not ReciterDB) — new implementation. Checkpoint/resume pattern documented. |
| DB-07 | Create GSI 1: Faculty-to-Topics (PK: `faculty_uid`, SK: main table PK) | GSI creation in `create_table` call documented. Projection attributes listed. |
| DB-08 | Create GSI 2: Processing-by-Version (PK: `taxonomy_version`, SK: `status`, KEYS_ONLY) | KEYS_ONLY projection pattern documented. |
</phase_requirements>

---

## Summary

Phase 1 builds the entire data layer for the ReCiter AI Chatbot. It consists of three sequentially-dependent scripts: taxonomy generation (`generate_taxonomy.py`), publication scoring (`score_publications.py`), and DynamoDB loading (`load_dynamodb.py`). The pipeline is one-time for the demo and runs offline over 2-4 hours.

The technical domain is well-understood and grounded in existing, proven code. The Bedrock client pattern is directly portable from CViche's `llm_client.py`. The SQL patterns for ReciterDB extraction are verified directly in ReciterAI's codebase. Both pinned model IDs (`anthropic.claude-haiku-4-5-20251001-v1:0` and `anthropic.claude-sonnet-4-6`) are confirmed ACTIVE in the dev Bedrock account with cross-region quotas of 10,000 RPM — far above the 10-20 concurrent calls planned. The `reciterai-chatbot` DynamoDB table does not yet exist (verified), so table creation is a required Wave 1 task.

The main risks are (1) the taxonomy generation quality — bad taxonomy burns $35 in scoring before the flaw is detected — and (2) DB_USER environment variable is not currently set (DB_HOST, DB_PASSWORD, DB_NAME are set), which needs resolution before extraction can run. The design spec's review gate (D-07) and validation step (D-08) are the right mitigations for risk 1.

**Primary recommendation:** Build and test in strict order: (1) table creation + schema validation, (2) small extraction test (50 pubs), (3) taxonomy generation + review + validation, (4) full scoring run, (5) DynamoDB load. Do not start the $35 scoring run until taxonomy is reviewed.

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| boto3 | 1.42.54 (verified) | Bedrock Converse API + DynamoDB operations | Official AWS SDK, already installed |
| botocore | 1.42.54 (verified) | AWS error types for retry logic | Transitive boto3 dependency |
| pymysql | 1.1.1 (in ReciterAI) | MariaDB connection for ReciterDB | Used by all ReciterAI pipelines |
| sqlalchemy | 2.0.x (in ReciterAI) | ORM / connection management | ReciterAI's db.py depends on it |
| tqdm | 4.67.3 (verified) | Progress bar for 2-4 hour scoring run | Lighter than rich, already installed |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| rich | 14.3.2 (verified) | Richer progress display with tables | Alternative to tqdm if multi-stat display needed |
| asyncio | stdlib | Concurrent Bedrock calls with semaphore | Built-in, no install needed |
| json | stdlib | taxonomy_v1.json serialization | Built-in |
| dataclasses | stdlib | Typed record containers | Built-in, cleaner than dicts |

**Installation (for integration repo only — ReciterAI imported via sys.path or pip install -e):**
```bash
pip install boto3 tqdm
# ReciterAI import: pip install -e /Users/paulalbert/Dropbox/GitHub/ReciterAI
# or: sys.path.insert(0, '/path/to/ReciterAI')
```

**Version verification:** [VERIFIED: aws cli `pip3 show boto3`, `pip3 show tqdm`, `pip3 show rich`]

### Pinned Bedrock Model IDs
| Model | Bedrock Model ID | Status | Use |
|-------|-----------------|--------|-----|
| Claude Haiku 4.5 | `anthropic.claude-haiku-4-5-20251001-v1:0` | ACTIVE | Screening (one call per pub, returns all 50 topic scores) |
| Claude Sonnet 4.6 | `anthropic.claude-sonnet-4-6` | ACTIVE | Dense scoring + taxonomy generation |

[VERIFIED: `aws bedrock list-foundation-models` + `aws bedrock get-foundation-model` — both ACTIVE in us-east-1, 2026-04-08]

**Note on model ID format:** `anthropic.claude-sonnet-4-6` appears without a date suffix in the Bedrock model list (unlike Haiku). Use this exact string as the `modelId` in Converse API calls.

### Bedrock Quotas (Confirmed, No Quota Increase Needed)
| Model | Cross-Region RPM | Cross-Region TPM | Assessment |
|-------|-----------------|------------------|------------|
| Haiku 4.5 | 10,000 RPM | 5,000,000 TPM | Vastly exceeds 10-20 concurrent calls needed |
| Sonnet 4.6 | 10,000 RPM | 6,000,000 TPM | Well within limits for dense scoring |

[VERIFIED: `aws service-quotas list-service-quotas --service-code bedrock`, 2026-04-08]

The design spec notes to "request Bedrock quota increases if needed" — this is NOT required. Cross-region quotas of 10,000 RPM with ~10-20 concurrent calls means no throttling is expected.

---

## Architecture Patterns

### Recommended Project Structure
```
ReciterAI -ReCiter-Integration/
├── generate_taxonomy.py      # Step 1: Generate + validate taxonomy
├── score_publications.py     # Step 2: Extract + score all publications
├── load_dynamodb.py          # Step 3: Load all record types into DynamoDB
├── utils/
│   ├── __init__.py
│   ├── bedrock_client.py     # Ported from CViche llm_client.py, scoring-specific
│   ├── dynamodb_helpers.py   # Table creation, batch_write_item helpers
│   └── sql_queries.py        # SQL for ReciterDB extraction (publication + faculty)
├── taxonomy_v1.json          # Output of step 1 (reviewed before step 2)
└── docs/
    └── superpowers/specs/    # Existing design specs
```

### Pattern 1: Two-Pass Bedrock Scoring
**What:** Each publication goes through two LLM calls: (1) Haiku screens against all 50 topics simultaneously (cheap, fast), (2) Sonnet dense-scores only topics that passed 0.3 threshold (expensive, calibrated).
**When to use:** Mandatory for PIPE-03 + PIPE-04.

```python
# Source: docs/superpowers/specs/2026-04-08-reciterai-chatbot-design.md §5
# Screening: one Haiku call, returns JSON {topic_id: score} for all 50 topics
screening_result = bedrock_client.call(
    model="anthropic.claude-haiku-4-5-20251001-v1:0",
    messages=[{"role": "user", "content": screening_prompt}],
    response_format={"type": "json_object"},
)
passed_topics = {k: v for k, v in screening_result.items() if v >= 0.3}

# Dense scoring: Sonnet call with ONLY passed topics (not all 50)
if passed_topics:
    dense_result = bedrock_client.call(
        model="anthropic.claude-sonnet-4-6",
        messages=[{"role": "user", "content": dense_prompt(passed_topics)}],
        response_format={"type": "json_object"},
    )
```

### Pattern 2: DynamoDB Zero-Padded Sort Key for Score-First Retrieval
**What:** Sort key encodes score as 4-digit zero-padded integer so DynamoDB's lexicographic sort returns highest-scoring records first. Score 0.95 -> `0950`, score 0.40 -> `0400`.
**When to use:** All TOPIC# and TOOL# records (DB-02, DB-04).

```python
# Source: docs/superpowers/specs/2026-04-08-reciterai-chatbot-design.md §3
def make_score_sk(score: float, pmid: str) -> str:
    """SK: SCORE#NNNN#ACTIVITY#pmid_X  (descending by score via lexicographic sort)"""
    padded = str(int(score * 1000)).zfill(4)
    return f"SCORE#{padded}#ACTIVITY#pmid_{pmid}"

# Access pattern: Query TOPIC#aging_geroscience WHERE SK >= 'SCORE#0600'
# returns all publications scoring 0.6+ in descending score order
```

### Pattern 3: DynamoDB Checkpoint/Resume via PROCESSING# Records
**What:** Before scoring each publication, write a `PROCESSING#pmid_X` record with `status: pending`. On completion write `status: complete`. On restart, query for records not yet `complete` to skip already-processed pubs.
**When to use:** D-09 — required for the 2-4 hour scoring run.

```python
# Source: CONTEXT.md D-09 + design spec §3 Record Type 6
def get_unscored_pmids(dynamo_client, table_name: str, all_pmids: list) -> list:
    """Return pmids that don't have status=complete in the tracker."""
    # Batch GetItem for all pmids to check status
    # Filter out those with status == 'complete'
    ...

def mark_processing_complete(dynamo_client, table_name: str, pmid: str,
                              taxonomy_version: str, passed_topics: list):
    dynamo_client.put_item(TableName=table_name, Item={
        "PK": {"S": f"PROCESSING#{pmid}"},
        "SK": {"S": "STATUS"},
        "status": {"S": "complete"},
        "taxonomy_version": {"S": taxonomy_version},
        "scored_at": {"S": datetime.utcnow().isoformat()},
        "screening_passed_topics": {"L": [{"S": t} for t in passed_topics]},
    })
```

### Pattern 4: Bedrock Client Port from CViche
**What:** Adapt CViche's `llm_client.py` to a scoring-focused module: keep boto3 Converse API, lazy client init, exponential backoff, JSON validation retry. Remove OpenAI support and stage-config system — use direct kwargs instead.
**When to use:** D-04 — `utils/bedrock_client.py`.

Key elements to keep from CViche `llm_client.py`:
- `_get_bedrock_client()` — lazy init with `AWS_DEFAULT_REGION` env var [VERIFIED: line 86-99]
- `_call_with_retry()` — exponential backoff `min(2**attempt, 30)` with `BEDROCK_RETRYABLE_CODES` [VERIFIED: lines 102-136]
- `_translate_messages()` — OpenAI-style to Converse API format [VERIFIED: lines 178-212]
- JSON validation retry on non-JSON response [VERIFIED: lines 369-389]
- `BEDROCK_RETRYABLE_CODES = {"ThrottlingException", "ModelTimeoutException", "InternalServerException", "ServiceUnavailableException"}` [VERIFIED: lines 54-58]

### Pattern 5: ReciterDB Publication Extraction SQL
**What:** The verified SQL join pattern for extracting WCM full-time faculty first/last author publications. Based on ReciterAI's established patterns.

```sql
-- Source: ReciterAI/core/sql.py + ReciterAI/archive/legacy_inclusion_criteria/synopsis_inclusion_criteria.py
-- Filter: WCM full-time faculty, first/last author, academic articles
SELECT DISTINCT
    a1.pmid,
    a1.articleTitle          AS title,
    r.abstractVarchar        AS abstract,
    a1.journalTitleVerbose   AS journal,
    a1.articleYear           AS pub_year,
    s.synopsis,              -- from reciterai_synopsis (pre-computed)
    i.impactScore,           -- from reciterai_impact
    a.personIdentifier       AS cwid,
    asp.name,
    asp.primaryDepartment    AS department,
    asp.hIndex               AS h_index
FROM analysis_summary_article  a1
JOIN analysis_summary_author   a   ON a.pmid = a1.pmid
JOIN identity                  id  ON id.cwid = a.personIdentifier
JOIN analysis_summary_person   asp ON asp.personIdentifier = a.personIdentifier
LEFT JOIN reporting_abstracts  r   ON r.pmid = a1.pmid
LEFT JOIN reciterai_synopsis   s   ON s.entity_id = CAST(a1.pmid AS CHAR)
                                   AND s.entity_type = 'publication'
LEFT JOIN reciterai_impact     i   ON i.entity_id = CAST(a1.pmid AS CHAR)
                                   AND i.entity_type = 'publication'
WHERE a1.publicationTypeCanonical = 'Academic Article'
  AND a.authorPosition IN ('first', 'last')
  AND a.userAssertion IN ('ACCEPTED', 'ASSERTED')
  AND id.fullTimeFaculty = 'yes'
ORDER BY a1.pmid
```

[VERIFIED: `identity.fullTimeFaculty = 'yes'` filter in ReciterAI/core/pipeline_config.py lines 450, 484, 521, 674, 734 and ReciterAI/archive/legacy_inclusion_criteria/synopsis_inclusion_criteria.py line 188]

### Pattern 6: DynamoDB Table + GSI Creation
**What:** Create table and GSIs in a single `create_table` call. Use on-demand (PAY_PER_REQUEST) billing.
**When to use:** D-12, D-13 — `load_dynamodb.py` table setup.

```python
# Source: design spec §3 + CONTEXT.md D-12, D-13
def create_chatbot_table(dynamo_client, table_name: str = "reciterai-chatbot"):
    try:
        dynamo_client.create_table(
            TableName=table_name,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
                {"AttributeName": "faculty_uid", "AttributeType": "S"},
                {"AttributeName": "taxonomy_version", "AttributeType": "S"},
                {"AttributeName": "status", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {   # GSI 1: Faculty-to-Topics
                    "IndexName": "FacultyTopicsIndex",
                    "KeySchema": [
                        {"AttributeName": "faculty_uid", "KeyType": "HASH"},
                        {"AttributeName": "PK", "KeyType": "RANGE"},
                    ],
                    "Projection": {
                        "ProjectionType": "INCLUDE",
                        "NonKeyAttributes": ["score", "synopsis", "impact_score",
                                             "year", "department", "title", "top_topics"],
                    },
                },
                {   # GSI 2: Processing-by-Version
                    "IndexName": "ProcessingByVersionIndex",
                    "KeySchema": [
                        {"AttributeName": "taxonomy_version", "KeyType": "HASH"},
                        {"AttributeName": "status", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "KEYS_ONLY"},
                },
            ],
            BillingMode="PAY_PER_REQUEST",
        )
    except dynamo_client.exceptions.ResourceInUseException:
        pass  # Table already exists
```

### Pattern 7: asyncio Semaphore for Concurrent Bedrock Calls
**What:** Use asyncio with a semaphore to run 10-20 concurrent Bedrock calls. The boto3 client is synchronous; wrap in `asyncio.to_thread()` (Python 3.9+) to avoid blocking the event loop.
**When to use:** `score_publications.py` main scoring loop.

```python
# Source: design spec §5 "use asyncio with semaphore (10-20 concurrent calls)"
import asyncio

async def score_batch(publications: list, semaphore: asyncio.Semaphore,
                      bedrock_client, taxonomy: dict) -> list:
    async def score_one(pub):
        async with semaphore:
            return await asyncio.to_thread(score_publication, pub, bedrock_client, taxonomy)

    return await asyncio.gather(*[score_one(p) for p in publications], return_exceptions=True)

# Entry point
async def main():
    semaphore = asyncio.Semaphore(15)  # 15 concurrent calls
    results = await score_batch(publications, semaphore, client, taxonomy)
```

### Pattern 8: DynamoDB batch_write_item
**What:** DynamoDB batch writes max 25 items per call. Batch all record types in chunks of 25.
**When to use:** `load_dynamodb.py` for all record types.

```python
# Source: [ASSUMED] — standard boto3 DynamoDB pattern
def batch_write(dynamo_client, table_name: str, items: list):
    for i in range(0, len(items), 25):
        chunk = items[i:i+25]
        dynamo_client.batch_write_item(RequestItems={
            table_name: [{"PutRequest": {"Item": item}} for item in chunk]
        })
```

### Pattern 9: Taxonomy Generation via Batched Synopses
**What:** Batch all ~10K synopses into chunks of 75 per Sonnet call to extract topic clusters. Then run a consolidation pass to merge ~150 raw topics into ~50 final topics.
**When to use:** `generate_taxonomy.py` — D-05.

Chunk strategy: 75 synopses per batch at ~200 tokens each = ~15,000 tokens input per call. Well within Sonnet 4.6's context window. With 10K synopses, that's ~133 batches for cluster extraction, then one final consolidation call.

### Anti-Patterns to Avoid
- **Scoring all 50 topics per pub with Sonnet directly:** Haiku screen is 6x cheaper and faster; skip it and cost blows up from ~$35 to ~$180.
- **One Bedrock call per topic per publication:** Design spec is explicit: "one API call per publication, not one per topic."
- **Relying on ReciterAI's `processing_registry.py` for checkpoint state:** That module writes to ReciterDB MariaDB tables (`reciterai_entity_processing`). Phase 1 checkpoint state lives in DynamoDB PROCESSING# records (D-09). Do not confuse them.
- **Creating the DynamoDB table without GSIs:** GSIs must be created in the initial `create_table` call. Adding them later requires table recreation.
- **Using `Decimal` type for DynamoDB scores:** DynamoDB's Python SDK requires `Decimal` not `float` for numeric attributes. Use `from decimal import Decimal; Decimal(str(score))`.
- **Starting scoring before taxonomy review:** The review gate (D-07) exists because bad taxonomy wastes the full ~$35 scoring budget.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Bedrock API retry logic | Custom retry loop | Port CViche's `_call_with_retry()` | Already handles all retryable error codes, exponential backoff, and per-error-code routing |
| JSON response validation | `try: json.loads()` inline | CViche's `_validate_json_response()` + stronger hint retry | Handles the "Bedrock wraps JSON in markdown fences" failure mode |
| Converse API message translation | Manual dict construction | CViche's `_translate_messages()` | System message separation, JSON hint injection already solved |
| DynamoDB batch size limits | Chunking loops | Use the `batch_write` pattern (25 items max, enforced by AWS) | AWS rejects batches > 25 items with `ValidationException` |
| Concurrent HTTP with asyncio | `aiohttp` or `httpx` | `asyncio.to_thread()` wrapping boto3 sync calls | boto3 is synchronous; `to_thread` unblocks event loop without replacing the SDK |
| SQL extraction for publications | New query from scratch | Adapt `ReciterAI/core/sql.py` patterns | ReciterAI has tested join chains for WCM full-time faculty + first/last author — reuse |
| Decimal conversion for DynamoDB | Manual float handling | `boto3.dynamodb.types.TypeSerializer` or explicit `Decimal(str(x))` | float is rejected by DynamoDB Python SDK for numeric attributes |

**Key insight:** The most dangerous hand-roll is the Bedrock retry/JSON-validation logic — the CViche implementation already solved the "Bedrock returns non-JSON despite system prompt" failure mode with a stronger hint retry. Skip it and you'll see random JSON parse failures at scale.

---

## Common Pitfalls

### Pitfall 1: DynamoDB Float Type Rejection
**What goes wrong:** `put_item` raises `TypeError: Float types are not supported` when storing score values as Python `float`.
**Why it happens:** DynamoDB's Python SDK serializes numbers via the Decimal type. Floats cause ambiguity.
**How to avoid:** Always wrap scores: `Decimal(str(round(score, 4)))` before storing.
**Warning signs:** TypeError in DynamoDB write, not in Bedrock call.

### Pitfall 2: Bedrock Returns JSON Wrapped in Markdown Fences
**What goes wrong:** Sonnet or Haiku occasionally wraps JSON in ` ```json ... ``` ` even when prompted for plain JSON. `json.loads()` fails.
**Why it happens:** Models sometimes add formatting for readability.
**How to avoid:** Use CViche's `_validate_json_response()` + stronger hint retry pattern. Or strip fences: `content = re.sub(r'```json\n?|\n?```', '', content).strip()` as a fallback.
**Warning signs:** JSON decode error mid-run, not at start. May only affect 1-5% of calls.

### Pitfall 3: Missing `DB_USER` Environment Variable
**What goes wrong:** ReciterDB connection fails with authentication error at start of `score_publications.py`.
**Why it happens:** Current environment has `DB_HOST`, `DB_PASSWORD`, `DB_NAME` set but `DB_USER` is NOT SET.
[VERIFIED: environment check, 2026-04-08]
**How to avoid:** Set `DB_USER` before running any script that imports ReciterAI's `core/db.py`. Check: `python3 -c "import os; print(os.environ.get('DB_USER', 'MISSING'))"`.
**Warning signs:** Script fails immediately at connection, not during scoring.

### Pitfall 4: asyncio + boto3 Blocking Event Loop
**What goes wrong:** Using `await boto3_client.method()` — boto3 is not async-native. This causes the event loop to block on each call, eliminating concurrency benefit.
**Why it happens:** Developers expect AWS SDK to be async like modern HTTP clients.
**How to avoid:** Use `await asyncio.to_thread(sync_bedrock_call, args)` — runs the sync call in a thread pool without blocking the event loop.
**Warning signs:** Scoring runs at serial speed despite asyncio semaphore.

### Pitfall 5: DynamoDB GSI Not Projecting `faculty_uid`
**What goes wrong:** GSI 1 (Faculty-to-Topics) doesn't return expected attributes, causing None results for person-centric queries in Phase 2.
**Why it happens:** The GSI PK is `faculty_uid` — but if `faculty_uid` isn't set on TOPIC# and TOOL# records at write time, the GSI index has no items.
**How to avoid:** Every TOPIC# and TOOL# score record MUST include `faculty_uid` as an attribute. Verify in the load script before writing.
**Warning signs:** GSI query returns 0 items even when table has topic records.

### Pitfall 6: Sort Key Score Encoding Off-by-One
**What goes wrong:** A score of 1.0 becomes `1000` (5 digits), breaking the fixed-width sort key format.
**Why it happens:** `int(1.0 * 1000) = 1000` which is 4 digits but would sort after `0999`. The SK becomes `SCORE#1000#...` which sorts after `SCORE#0999#...` — actually works lexicographically. BUT access pattern `SK >= SCORE#0600` still correctly retrieves 1.0-scored items.
**How to avoid:** Use `str(min(int(score * 1000), 9999)).zfill(4)`. Scores of exactly 1.0 are rare but should be handled.
**Warning signs:** Publications with score=1.0 not returned by threshold queries.

### Pitfall 7: Taxonomy Generation Batch Too Large
**What goes wrong:** 100+ synopses per batch (~20K tokens) approaches token limits or causes response quality degradation.
**Why it happens:** Bedrock has per-request token limits and quality degrades with very large context.
**How to avoid:** Start with 50-75 synopses per batch (research recommendation). Test first batch before processing all 133 batches.
**Warning signs:** Taxonomy batches return fewer or lower quality clusters than expected.

### Pitfall 8: ReciterAI Import Path Conflict
**What goes wrong:** `import reciterai` fails or imports wrong version when both the integration repo and ReciterAI repo are on `sys.path`.
**Why it happens:** Python resolves the first matching package on sys.path.
**How to avoid:** Use `pip install -e /path/to/ReciterAI` (editable install) so the package is properly registered. Or use absolute `sys.path.insert(0, '/path/to/ReciterAI')` with a single entry point.
**Warning signs:** AttributeError on ReciterAI functions that exist in source but not the installed version.

---

## Code Examples

### Taxonomy Generation Prompt (Batch Pass)
```python
# Source: design spec §4 + CONTEXT.md D-05
def make_taxonomy_batch_prompt(synopses: list[str]) -> str:
    synopsis_text = "\n\n".join(f"[{i+1}] {s}" for i, s in enumerate(synopses))
    return f"""You are analyzing research publications from a major academic medical center.

Below are {len(synopses)} research synopses from publications.

<synopses>
{synopsis_text}
</synopses>

Derive 8-12 mid-level research topic clusters that cover the dominant themes in these synopses.
For each topic cluster:
- Use a stable, mid-level domain name (e.g., "aging_geroscience" not "tau pathology")
- Avoid overly specific or overly broad terms
- Include both disease areas and methodological topics

Respond with JSON only:
{{"topics": [{{"id": "snake_case_id", "label": "Human Readable Label", "description": "2-3 sentence description"}}]}}"""
```

### Taxonomy Consolidation Prompt (Final Pass)
```python
# Source: design spec §4 + CONTEXT.md D-05
def make_consolidation_prompt(raw_topics: list[dict], target_count: int = 50) -> str:
    topics_json = json.dumps(raw_topics, indent=2)
    return f"""You have {len(raw_topics)} raw topic clusters extracted from batches of research synopses.
Consolidate them into exactly {target_count} final topics for a research taxonomy.

Rules:
- Merge overlapping topics (e.g., "aging" + "geroscience" -> "aging_geroscience")
- Keep both disease areas and methodological topics
- Aim for stable, mid-level granularity — not too specific, not too broad
- Each topic should represent a meaningful research domain a research dean would ask about

<raw_topics>
{topics_json}
</raw_topics>

Respond with JSON only:
{{"taxonomy_version": "taxonomy_v1", "topics": [{{"id": "snake_case_id", "label": "Human Readable Label", "description": "2-3 sentence description"}}]}}"""
```

### Screening Prompt (Haiku, One Call Per Publication)
```python
# Source: design spec §5 + CONTEXT.md D-05
def make_screening_prompt(pub: dict, taxonomy: dict) -> str:
    topics_list = "\n".join(
        f"- {t['id']}: {t['label']} — {t['description']}" 
        for t in taxonomy['topics']
    )
    return f"""Score this publication against each research topic. Return a JSON object mapping topic_id to relevance score (0.0 to 1.0).

Publication:
Title: {pub['title']}
Abstract: {pub.get('abstract', '')}
Synopsis: {pub.get('synopsis', '')}

Topics:
{topics_list}

Score each topic from 0.0 (not relevant) to 1.0 (directly relevant).
Return ONLY a JSON object with topic_id keys and float values.
Example: {{"aging_geroscience": 0.85, "cardiovascular_disease": 0.20, ...}}"""
```

### Validation Sample Queries
```python
# Source: design spec §4 "Validate against 10-15 sample queries a research dean would actually ask"
VALIDATION_QUERIES = [
    "Who works on aging and cognitive decline?",
    "Find researchers using CRISPR gene editing",
    "Who has expertise in cardiovascular disease prevention?",
    "Find oncology researchers focused on immunotherapy",
    "Who works on health equity and social determinants?",
    "Find researchers in diabetes and metabolic disorders",
    "Who studies neurodegeneration and Alzheimer's disease?",
    "Find clinical trials expertise in cardiology",
    "Who does research on cancer genomics?",
    "Find researchers working on AI and machine learning in medicine",
    "Who works on pediatric health outcomes?",
    "Find experts in infectious disease and epidemiology",
]
```

---

## DynamoDB Schema Reference (All 6 Record Types)

Extracted from design spec §3 for direct use during implementation:

| Record Type | PK | SK | Key Attributes |
|-------------|----|----|----------------|
| Topic Score | `TOPIC#<topic_id>` | `SCORE#<0000-9999>#ACTIVITY#pmid_<pmid>` | `faculty_uid`, `score` (Decimal), `synopsis`, `impact_score`, `year`, `journal`, `title`, `topic_scores_version` |
| Faculty Index | `FACULTY#cwid_<cwid>` | `PROFILE` | `name`, `department`, `title`, `h_index`, `article_count`, `first_author_count`, `last_author_count`, `top_topics` (list) |
| Tool Score | `TOOL#<tool_id>` | `SCORE#<0000-9999>#ACTIVITY#pmid_<pmid>` | Same as Topic Score |
| Deep Dive | `DEEPDIVE#<domain>` | `META` | `domain`, `generated_at`, `taxonomy_version`, `subtopics`, `content`, `reviewed_by`, `review_status` (starts as "pending") |
| Evaluation | `EVAL#<resp_id>` | `META` | `eval_type`, `faculty_uid`, `topic`, `rating`, `narrative`, `tags`, `rated_at`, `taxonomy_version` |
| Processing Tracker | `PROCESSING#pmid_<pmid>` | `STATUS` | `status` (pending/screened/complete/failed), `taxonomy_version`, `screened_at`, `scored_at`, `screening_passed_topics`, `retry_count`, `error` |

**GSI 1 — FacultyTopicsIndex:**
- PK: `faculty_uid`, SK: main table PK
- Projection: INCLUDE — `faculty_uid`, `PK`, `SK`, `score`, `synopsis`, `impact_score`, `year`, `department`, `title`, `top_topics`

**GSI 2 — ProcessingByVersionIndex:**
- PK: `taxonomy_version`, SK: `status`
- Projection: KEYS_ONLY

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3 | All scripts | Yes | 3.14.2 | — |
| boto3 | Bedrock + DynamoDB | Yes | 1.42.54 | — |
| tqdm | Progress bar | Yes | 4.67.3 | Use rich (also installed) |
| rich | Progress bar (alt) | Yes | 14.3.2 | Use tqdm |
| AWS CLI | Quota/model verification | Yes | 2.33.11 | — |
| AWS account access | Bedrock + DynamoDB | Yes | Account 665083158573 | — |
| Bedrock Haiku 4.5 | PIPE-03 screening | Yes (ACTIVE) | anthropic.claude-haiku-4-5-20251001-v1:0 | — |
| Bedrock Sonnet 4.6 | TAX-01, PIPE-04 scoring | Yes (ACTIVE) | anthropic.claude-sonnet-4-6 | — |
| DynamoDB `reciterai-chatbot` table | All DB-XX tasks | No — does not yet exist | — | Create in Wave 0 task |
| ReciterDB (MariaDB) | PIPE-01, PIPE-02 | DB_HOST+DB_NAME+DB_PASSWORD set; DB_USER NOT SET | — | Set DB_USER env var |
| ReciterAI repo | Reusable core modules | Yes (at /Users/paulalbert/Dropbox/GitHub/ReciterAI) | 1.0.0 | — |
| asyncio | Concurrent scoring | Yes | stdlib (Python 3.14) | — |

**Missing dependencies with no fallback:**
- `reciterai-chatbot` DynamoDB table — must be created in Wave 0 before any load tasks.

**Missing dependencies with fallback:**
- `DB_USER` env var — not set in current shell session. Must be confirmed set before ReciterDB extraction. Check `~/.zshrc` (per CLAUDE.md, credentials are stored there).

[VERIFIED: all environment checks run 2026-04-08]

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Bedrock InvokeModel API (model-specific JSON) | Bedrock Converse API (unified format) | Mid-2024 | One SDK call works for all Claude models; CViche already uses this |
| One Bedrock call per topic per publication | One call returning all topic scores as JSON | Design spec § 5 | 50x fewer API calls, same scoring quality |
| DynamoDB provisioned capacity (pre-set RCU/WCU) | On-demand capacity (PAY_PER_REQUEST) | 2018, but now default for new projects | No capacity planning, scales automatically, free tier covers demo volumes |

**Note on Bedrock Batch Inference:** AWS Bedrock supports batch inference for large-scale jobs. At 10K publications, on-demand API with asyncio concurrency is simpler and takes 2-4 hours — not worth the complexity of batch job setup. Batch API is worth considering for v2 (100K+ publications). [ASSUMED — no formal benchmarking done for this specific workload size]

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | 75 synopses per batch is appropriate chunk size for taxonomy generation | Architecture Patterns §9 | If too large, response quality degrades or hits token limits. Mitigation: test first batch before full run. |
| A2 | `analysis_summary_person` has columns `name`, `primaryDepartment`, `hIndex`, `article_count`, `first_author_count`, `last_author_count` | Code Examples §SQL | Faculty profile records would have wrong/missing fields. Mitigation: verify column names with `DESCRIBE analysis_summary_person` before writing load script. |
| A3 | `reciterai_tools` or `reciterai_keyword_relevance` contains tool scores suitable for TOOL# records (DB-04) | Phase Requirements | Tool score records may need manual generation if pre-existing scores aren't in the right format. |
| A4 | `a.userAssertion IN ('ACCEPTED', 'ASSERTED')` is the correct filter for "accepted/asserted" publications | Code Examples §SQL | Extraction could miss publications or include wrong ones. Verify column name and values in `analysis_summary_author`. |
| A5 | Bedrock Batch Inference API would be more complex than on-demand + asyncio for 10K pubs | State of the Art | Negligible — on-demand is the specified approach (design spec §5). |
| A6 | `reciterai_synopsis.entity_id` joins to `CAST(pmid AS CHAR)` | Code Examples §SQL | Join returns no rows; publications have no synopsis for scoring input. Verify join key via ReciterAI's `inclusion_criteria.py` pattern (uses `external_id` column). [VERIFIED: inclusion_criteria.py line 37 uses `synopsis.entity_id = m.external_id`] |

**Correction on A6:** ReciterAI's `inclusion_criteria.py` uses `synopsis.external_id = m.external_id` (not `entity_id`). The correct join for synopsis is `reciterai_synopsis.external_id = a1.pmid` (or cast). Verify `reciterai_synopsis` column names before writing SQL.

---

## Open Questions

1. **`DB_USER` environment variable not set**
   - What we know: DB_HOST, DB_PASSWORD, DB_NAME are set in current shell. DB_USER is missing.
   - What's unclear: Whether it's in ~/.zshrc (likely yes per CLAUDE.md policy) but not exported to this shell session.
   - Recommendation: Developer should confirm `DB_USER` is set before running any extraction. Add a startup check to `score_publications.py`: `assert os.environ.get('DB_USER'), "DB_USER not set"`.

2. **`analysis_summary_person` exact column names for faculty metadata**
   - What we know: The design spec lists `h_index`, `article_count`, `department`. ReciterAI's legacy code references `hIndex`, `primaryDepartment`.
   - What's unclear: Exact column names vs. aliases. The SQL in ReciterAI's `sql.py` uses aliases (e.g., `AS h_index`).
   - Recommendation: Run `DESCRIBE analysis_summary_person` against ReciterDB early in Wave 1 to confirm column names.

3. **`reciterai_synopsis` join column: `entity_id` vs `external_id`**
   - What we know: ReciterAI's modern `inclusion_criteria.py` joins on `synopsis.external_id`. But `ReciterAI/core/sql.py` references `reciterai_pubs_synopsis` (different table name).
   - What's unclear: The Phase 1 integration repo reads from `reciterai_synopsis` — confirm whether this table uses `entity_id` or `external_id` as the join column for PMIDs.
   - Recommendation: `DESCRIBE reciterai_synopsis` before writing extraction SQL.

4. **Tool score source for DB-04 (TOOL# records)**
   - What we know: `reciterai_keyword_relevance` and `reciterai_tools` both exist in ReciterDB. Design spec says to load TOOL# records from "existing ReciterAI tool extraction."
   - What's unclear: Which table contains the per-publication tool scores in the right format, and whether they align with a canonical tool taxonomy.
   - Recommendation: Inspect `reciterai_tools` and `reciterai_keyword_relevance` schema before implementing DB-04. This may be a Wave 2 item if tooling data needs preparation.

---

## Sources

### Primary (HIGH confidence)
- `/Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/docs/superpowers/specs/2026-04-08-reciterai-chatbot-design.md` — DynamoDB schema (§3), taxonomy generation (§4), data pipeline (§5)
- `/Users/paulalbert/Dropbox/GitHub/CViche/src/unified_pipeline/llm_client.py` — Bedrock Converse API implementation, retry logic, JSON validation
- `/Users/paulalbert/Dropbox/GitHub/ReciterAI/core/sql.py` — Publication extraction SQL patterns
- `/Users/paulalbert/Dropbox/GitHub/ReciterAI/core/processing_registry.py` — Processing state tracking pattern
- `/Users/paulalbert/Dropbox/GitHub/ReciterAI/core/error_handling.py` — Retry + circuit breaker patterns
- `/Users/paulalbert/Dropbox/GitHub/ReciterAI/pipeline_publications/inclusion_criteria.py` — Verified join pattern for `reciterai_synopsis`
- `aws bedrock list-foundation-models` + `get-foundation-model` — Model status confirmation [VERIFIED 2026-04-08]
- `aws service-quotas list-service-quotas --service-code bedrock` — Quota confirmation [VERIFIED 2026-04-08]
- `aws sts get-caller-identity` — Account identity confirmation [VERIFIED 2026-04-08]
- `pip3 show boto3 tqdm rich` — Package availability [VERIFIED 2026-04-08]

### Secondary (MEDIUM confidence)
- `.planning/phases/01-offline-pipeline/01-CONTEXT.md` — User decisions (locked)
- `.planning/REQUIREMENTS.md` — Requirement descriptions
- AWS DynamoDB `list-tables` — Confirmed `reciterai-chatbot` does not yet exist [VERIFIED 2026-04-08]

### Tertiary (LOW confidence)
- [ASSUMED] Batch size of 75 synopses per LLM call is optimal for taxonomy generation
- [ASSUMED] `analysis_summary_person` column names match legacy ReciterAI query aliases

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all packages and models verified against live environment
- Architecture: HIGH — patterns ported directly from verified, working code (CViche, ReciterAI)
- Pitfalls: HIGH — most identified from direct code inspection of retry logic, DynamoDB SDK behavior, and verified environment state
- SQL extraction: MEDIUM — join patterns verified in ReciterAI, but exact column names in `reciterai_synopsis` and `analysis_summary_person` need `DESCRIBE` confirmation before writing final queries

**Research date:** 2026-04-08
**Valid until:** 2026-05-08 (stable domain — boto3 and DynamoDB patterns are stable; Bedrock model availability could change sooner)

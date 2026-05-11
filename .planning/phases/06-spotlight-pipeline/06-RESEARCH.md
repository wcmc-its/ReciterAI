# Phase 6: Spotlight Pipeline & Publishing Contract — Research

**Researched:** 2026-05-07
**Domain:** Editorial-content pipeline + voice-constrained LLM generation + S3 versioned-artifact publishing (Phase 5 pattern reuse)
**Confidence:** HIGH for stack and pattern reuse; MEDIUM for hybrid-critic regex set (drafted from prompt v0, needs review); MEDIUM for SPOTLIGHT_HISTORY decay-formula-state interaction (math is right; cold-start edge case verified).

## Summary

Phase 6 is overwhelmingly an **assembly-and-publish phase, not a greenfield architecture phase**. Six of the seven plans hang off existing infrastructure already shipped in this repo:

1. **Reads** from DynamoDB `reciterai-chatbot` `TOPIC#` partitions enriched by `import_enrichment.py` (`synopsis`, `impact_score`, `impact_justification`, `title`, `journal`, `year`, `author_position` are already populated per record).
2. **Reuses** `utils/s3_client.py` (lazy boto3, `put_object`, `key_exists`) verbatim — only the bucket constant changes (or the constructor arg overrides it).
3. **Mirrors** `backfill_all.py:_run_publish()` step-for-step: schema load → fail-fast `Draft202012Validator` → in-memory bytes + sha256 → manifest in locked field order → 6 PutObjects (3 versioned + 3 latest) → idempotency `key_exists` warn-overwrite.
4. **Mirrors** `docs/hierarchy-contract.md` and `docs/sps-integration-handoff.md` structure character-for-character (sections: URL Pattern, Schema, Manifest, Cadence, Breaking-Change Policy, Integration Pattern, Changelog, FAQ).

The genuinely **new** work is concentrated in three places — pool ranker math + SPOTLIGHT_HISTORY DynamoDB partition design + Bedrock Sonnet generate-and-critic loop. Everything else slots into ironclad Phase 5 conventions.

**Primary recommendation:** Plan Phase 6 as **7 plans in 4 waves**, with Wave 1 = bucket migration + DynamoDB partition design, Wave 2 = pool ranker + rotation selector + lede generator + critic, Wave 3 = sensitive-tag gate + assembler + publish, Wave 4 = contract docs + SPS handoff + smoke test. Treat plan 06-01 (bucket migration) as a hard prerequisite that gates publish but does not block code authoring on plans 06-02 through 06-05.

## User Constraints (from CONTEXT.md)

### Locked Decisions

**Pipeline stages (locked):**

| Stage | Output | Logic |
|---|---|---|
| 1. Pool ranker | top-50 subtopics | `score = Σ (impactScore)` over publications **published in the last 24 months** (hard cutoff). No within-window recency weighting. |
| 2. Rotation selector | 10 active spotlights | One per parent topic enforced. `selection_score = pool_score × (1 - exp(-weeks_since_last_shown / 12))`. Cold-start: never-shown subtopics get multiplier = 1 (full pool_score). Last-shown state in DynamoDB. |
| 3. Lede generator | 10 ledes | Bedrock Sonnet + voice-constrained prompt at `prompts/spotlight_synopsis_v0.md`. Grounding = `synopsis` + `impactJustification` from 2-3 selected papers per spotlight. |
| 4. Critic pass | validated ledes or flagged | **Hybrid critic.** Code-checks deterministic constraints. Single LLM call only for tone/voice/marketing-language judgment. Auto-regen up to 3 retries. Persistent failure → `SPOTLIGHT_REVIEW#` queue. |
| 5. Editorial gate | publish-ready set | Sensitive-topic tag match (DynamoDB `SPOTLIGHT_CONFIG#sensitive_tags`) → `SPOTLIGHT_REVIEW#` queue. |
| 6. Publish | spotlight.json + schema + manifest | Reuses Phase 5 publish path. New S3 prefix: `s3://wcmc-reciterai-artifacts/spotlight/v{date}/` and `latest/`. |

**Ranking & rotation:**
- Pool size = 50, selection size = 10
- Recency = hard cutoff at 24 months, no within-window decay
- Rotation decay: `selection_score = pool_score × (1 - exp(-weeks_since_last_shown / 12))`, τ = 12 weeks
- Cold-start: never-shown → multiplier = 1
- Parent diversity: enforced (one subtopic per parent topic)

**Storage & publish path:**
- Rotation state in DynamoDB `SPOTLIGHT_HISTORY#{subtopic_id}` records
- S3: `s3://wcmc-reciterai-artifacts/spotlight/v{date}/` + `s3://wcmc-reciterai-artifacts/spotlight/latest/`
- Bucket migration `wcmc-reciterai-hierarchy → wcmc-reciterai-artifacts` is a Phase 6 prerequisite
- Artifact body: 10 active + 50-subtopic pool snapshot
- Per-spotlight provenance: NOT in artifact for v1 (lives in DynamoDB review/history records)

**Critic & manual review queue:**
- Hybrid critic: code-deterministic + single LLM call for tone/voice
- Retry budget: 3 LLM regen attempts
- Review queue: DynamoDB `SPOTLIGHT_REVIEW#{publish_id}#{subtopic_id}`
- Schema must be **forward-compatible with v2 Publication Manager review surface**
- Required attributes: `publish_id, subtopic_id, lede_text, flag_reason (critic|sensitive_tag|both), critic_verdict (JSON), papers_used (PMIDs), regen_count, status (pending|approved|rejected), reviewer, reviewed_at`
- Review CLI v1: `--review-queue [--publish-id <id>]`, `--approve <subtopic_id>`, `--reject <subtopic_id>`

**Operator workflow:**
- Refresh cadence: weekly, operator-run
- `--dry-run`: pool + selection + draft ledes, NO Bedrock LLM critic, NO publish
- `--dry-run-full`: full pipeline incl. Bedrock generation/critic/sensitive-tag check, but no publish; writes `./out/spotlight-{date}.json`
- `--regen-only <subtopic_id>`: re-roll one lede without re-ranking the pool
- `--approve <subtopic_id>` / `--reject <subtopic_id>`: clear review-queue entries

**Schema & SPS contract:**
- Synopsis grounding = `synopsis` + `impactJustification` from 2-3 selected papers (NOT `display_name`/`short_description` per D-19; NOT `title`/`journal` since enrichment fields carry richer signal)
- Author payload: `{personIdentifier, displayName, position}` for first AND last author of each grounding paper
- `personIdentifier` IS the SPS photo-store join key (confirmed via Phase 2 retrieval work)
- Sensitive-tag list: DynamoDB only, NOT in repo, lives at `SPOTLIGHT_CONFIG#sensitive_tags`
- Artifact shape: separate `spotlight.json` (NOT a hierarchy.json extension)
- Papers per lede: 2-3
- Schema versioning mirrors Phase 5: `spotlight_schema_v1.json` checked in, `schema_version` field in artifact

### Claude's Discretion

- DynamoDB schema shape detail for `SPOTLIGHT_HISTORY#`, `SPOTLIGHT_REVIEW#`, `SPOTLIGHT_CONFIG#` (proposed below)
- Whether bucket migration is Phase 6 Plan 01, a backported Phase 5 follow-on, or its own mini-phase (researcher recommendation: Plan 06-01)
- Banned-word + tic regex list (drafted below from prompt v0)
- Critic prompt model choice — Haiku vs Sonnet (researcher recommendation: Haiku 4-5 for the LLM-judgment slice; rationale below)
- Manual-review-queue file format if any local-disk artifact also gets emitted alongside the DynamoDB writes
- Cold-start treatment in code (formula handles it naturally; researcher confirms below)
- JSON Schema validation library (already pinned: `jsonschema>=4.23.0` in `requirements.txt`)
- Whether `SPOTLIGHT_HISTORY#` should TTL or accumulate (researcher recommendation: accumulate; storage cost trivial; useful for analytics)

### Deferred Ideas (OUT OF SCOPE)

- 335-wide synopsis generation — top-N rotation IS the design
- SPS-side ETL implementation
- Suppress Recent Contributions home-page change in SPS
- Automated weekly cron runner — operator-run for v1
- LLM judge for sensitive-topic detection — tag-list pattern match only
- Formal comms-strategy sign-off workflow
- Embedding-based subtopic re-ranking
- Editorial CMS UI for hand-curated overrides
- Publication Manager review-queue dashboard surface — v2 work; v1 schema is forward-compat
- Per-spotlight provenance in published artifact

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SPOT-01 | Pool ranker top-50 by `Σ (impactScore × recency_weight(year))` from TOPIC# enrichment | Stack §Pool ranker; `import_enrichment.py` confirms TOPIC# fields populated; CONTEXT supersedes the smooth-decay form with hard 24-month cutoff |
| SPOT-02 | Pool ranking deterministic given input | No randomness in design; sort by `(score DESC, subtopic_id ASC)` for tiebreaker stability — see Architecture Patterns §Determinism |
| SPOT-03 | Rotation selector picks 10, one-per-parent, with `pool_score × (1-exp(-weeks/12))` decay | Architecture Patterns §Rotation Selector; cold-start handled (multiplier=1 when `last_shown_at` is null) |
| SPOT-04 | Last-shown state in DynamoDB queryable by subtopic_id | DynamoDB Schema Design §SPOTLIGHT_HISTORY#; PK on subtopic_id directly, no SK needed (one row per subtopic) |
| SPOT-05 | Bedrock Sonnet lede via `prompts/spotlight_synopsis_v0.md`, 2-3 papers grounding | Code Examples §Lede generation; prompt v0 already authored; `synopsis` + `impactJustification` substitution noted in prompt header |
| SPOT-06 | Voice contract enforcement (em-dash, time-bound, marketing words, "WCM scholars are X-ing" tic) | Architecture Patterns §Hybrid critic; Banned-word regex set drafted below |
| SPOT-07 | Critic pass with up to 3 regens, persistent failures route to review queue | Architecture Patterns §Generate-and-critic loop |
| SPOT-08 | Sensitive-tag gate, DynamoDB-stored patterns at `SPOTLIGHT_CONFIG#sensitive_tags` | DynamoDB Schema Design §SPOTLIGHT_CONFIG#; pattern match strategy (substring vs glob) discussed |
| SPOT-09 | Per-paper payload `{pmid, title, journal, year}` + first/last author `{personIdentifier, displayName, position}` | Schema design §spotlight.schema.json; D-19 rule satisfied |
| SPOT-10 | `spotlight.schema.json` (Draft 2020-12) co-published | Mirror of `docs/hierarchy.schema.json` Draft 2020-12 pattern |
| SPOT-11 | `python backfill_spotlight.py --publish` validates + uploads + idempotent on re-run | Architecture Patterns §Publish path; lifts from `_run_publish()` in `backfill_all.py:429-549` |
| SPOT-12 | `manifest.json` carries 6 fields in locked order | Manifest Contract §; mirror Phase 5 — sole new fields are `spotlight_version` (replaces `taxonomy_version` semantic position; both required) |
| SPOT-13 | `docs/spotlight-contract.md` consumer contract | Contract Doc Pattern §; mirror `docs/hierarchy-contract.md` structure |
| SPOT-14 | `docs/sps-spotlight-handoff.md` + `docs/sps-spotlight-etl-reference.ts` | Handoff Doc Pattern §; mirror `docs/sps-integration-handoff.md` + `docs/sps-etl-reference.ts` |

## Project Constraints (from CLAUDE.md)

**Security (HARD):**
- Never hardcode credentials. AWS via default credential chain or env vars from `~/.zshrc`. Never read or display `~/.zshrc`.
- Never log secret values, even partially.
- `client = OpenAI()` pattern: do not pass `api_key=` (legacy mapping cost). Same posture for Bedrock — `bedrock-runtime` boto3 client uses default credential chain.

**Naming (LOCKED):**
- `personIdentifier` is the canonical field. The literal string `"cwid_"` exists ONLY in `WCM_FACULTY_UID_PREFIX` (and Phase 1 `load_dynamodb.py`). Phase 6 Python code reads `personIdentifier` from extracted SK or a dedicated attribute; never hardcodes `cwid_*` outside that one constant.
- Models pinned: `us.anthropic.claude-haiku-4-5-20251001-v1:0` (HAIKU_MODEL) and `us.anthropic.claude-sonnet-4-6` (SONNET_MODEL) — verbatim from `utils/bedrock_client.py:39-40`. Never invent model IDs.

**Module patterns:**
- Lazy singletons / lazy boto3 init — no AWS calls at import time. Mirror `S3HierarchyClient` and `BedrockClient`.
- Parameterized queries only — never interpolate user values into DynamoDB expressions.
- Logging: structured operational metadata only; no credential values.

**Workflow:**
- `commit-to-subrepo` for PM worktree code; plain `commit` for `.planning/` docs and Python pipeline. Phase 6 is **Python pipeline + docs only** — all commits use plain `commit`.
- No AI attribution in commits / PRs / code comments.
- GSD workflow: `/gsd-execute-phase` for planned work.

**Discovery:** No `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, or `.github/skills/` directory present. No SKILL.md to load.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Pool ranker (read TOPIC# + score) | Database (DynamoDB scan/query) + Backend (Python) | — | Aggregates impact across publications per subtopic; pure read + arithmetic |
| Rotation selector (decay against last-shown) | Database (DynamoDB BatchGetItem on SPOTLIGHT_HISTORY#) + Backend (Python) | — | Reads small per-subtopic history rows; selection logic in code |
| Lede generation | Backend (Python) → External (Bedrock Sonnet) | — | LLM call via boto3 `bedrock-runtime` (Converse API), Phase 1 pattern |
| Hybrid critic | Backend (Python regex/length checks) + External (Bedrock Haiku tone judge) | — | Deterministic checks in code; only tone/voice slice goes to LLM |
| Sensitive-tag gate | Database (DynamoDB GetItem on SPOTLIGHT_CONFIG#sensitive_tags) + Backend (pattern match) | — | Single config row read at run start; substring/glob match in code |
| Review-queue mutate (`--approve`/`--reject`) | Database (DynamoDB UpdateItem on SPOTLIGHT_REVIEW#) | Backend (CLI flag dispatch) | Status transitions only |
| Schema validation | Backend (jsonschema Draft202012Validator) | — | Pure Python, no I/O |
| S3 publish | External (S3 PutObject) | Backend (S3HierarchyClient → S3ArtifactsClient) | 6 objects per publish, idempotent overwrite |
| Manifest generation | Backend (Python) | — | sha256 over in-memory bytes; insertion order locked |
| SPS-side ETL (out of scope) | — (consumer, separate repo/phase) | — | Not Phase 6 work |
| Headshot rendering (out of scope) | Frontend (SPS) | — | Not Phase 6 work; consumer responsibility |

**Key tier observation:** This phase has **zero browser-tier work**. The artifact is consumed by a separate repo's ETL Lambda. Plans should never reference React/Bootstrap/CSS.

## Standard Stack

### Core (already in use, reuse verbatim)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `boto3` | `>=1.42.0` | DynamoDB + Bedrock + S3 client | Already pinned in `requirements.txt`; Phase 1 + Phase 5 use it |
| `jsonschema` | `>=4.23.0` | Draft 2020-12 validation | Already pinned; `Draft202012Validator` used by `backfill_all.py:_run_publish()` |
| `sqlalchemy` | `>=2.0.0` | Not needed in Phase 6 — pool ranker reads DynamoDB only | (not used here; listed for context) |
| `tqdm` | `>=4.67.0` | Progress bars on long loops | Already pinned |

### Bedrock model IDs (LOCKED — `utils/bedrock_client.py:39-40`)

```python
HAIKU_MODEL  = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
SONNET_MODEL = "us.anthropic.claude-sonnet-4-6"
```

| Model | Phase 6 Use | Temperature | Rationale |
|-------|-------------|-------------|-----------|
| `SONNET_MODEL` | Lede generation (3 stage) | `0.4-0.7` (suggest 0.5) [ASSUMED — needs review] | Editorial voice needs some variation; deterministic temp=0 produces flat output. **Locked decision needs operator confirmation** before plan 06-03. |
| `HAIKU_MODEL` | Critic LLM-judge slice (4 stage) | `0.0` | Critic is a binary pass/fail with structured rationale. Determinism matters here. |

### BedrockClient call shape (from `utils/bedrock_client.py`)

`BedrockClient.complete(model, messages, system, max_tokens, temperature)` — Converse API; lazy `boto3.client("bedrock-runtime")` init; retries with exponential backoff already implemented (PIPE-06 complete). Lede generator and critic both reuse this.

### New module additions

| New file | Purpose |
|----------|---------|
| `backfill_spotlight.py` | CLI entry point; mirrors `backfill_all.py` argparse + dispatch shape |
| `spotlight/pool_ranker.py` | Reads TOPIC# + filters last-24-months + sums impactScore per subtopic |
| `spotlight/rotation_selector.py` | Reads SPOTLIGHT_HISTORY# + applies decay + parent-diversity selection |
| `spotlight/lede_generator.py` | Bedrock Sonnet call wrapping `prompts/spotlight_synopsis_v0.md` |
| `spotlight/critic.py` | Deterministic regex checks + Bedrock Haiku tone judge + retry loop |
| `spotlight/sensitive_gate.py` | Read SPOTLIGHT_CONFIG# + pattern-match against subtopic |
| `spotlight/review_queue.py` | DynamoDB read/write on SPOTLIGHT_REVIEW#; `--approve`/`--reject`/`--review-queue` |
| `spotlight/assembler.py` | Build `spotlight.json` shape; insert pool snapshot + selected ledes |
| `spotlight/publish.py` | Schema-validate + sha256 + manifest + 6 PutObjects (Phase 5 pattern) |
| `prompts/spotlight_critic_v0.md` | NEW prompt for the LLM-judge slice (tone/voice only) |
| `docs/spotlight.schema.json` | JSON Schema 2020-12 |
| `docs/spotlight-contract.md` | Consumer contract |
| `docs/sps-spotlight-handoff.md` | SPS handoff brief |
| `docs/sps-spotlight-etl-reference.ts` | Working TypeScript reference |

**Installation:** No new dependencies. All required libraries are already pinned in `requirements.txt`. [VERIFIED: read `requirements.txt` 2026-05-07]

**Version verification:**
```bash
python -c "import jsonschema; print(jsonschema.__version__)"  # expect >=4.23.0
python -c "import boto3; print(boto3.__version__)"             # expect >=1.42.0
```
[CITED: requirements.txt]

## Architecture Patterns

### System Architecture Diagram

```
                           backfill_spotlight.py CLI
                                     │
              ┌──────────────────────┼──────────────────────────────────┐
              │                      │                                  │
        --dry-run               --dry-run-full                     --publish
              │                      │                                  │
              ▼                      ▼                                  ▼
   ┌──────────────────────────────────────────────────────────────────────────┐
   │                    1. POOL RANKER (spotlight/pool_ranker.py)             │
   │   DynamoDB scan TOPIC# partitions → sum impactScore over pubs in last    │
   │   24 months, group by subtopic_id → top-50 sorted (score DESC, id ASC)   │
   └──────────────────────────────────┬───────────────────────────────────────┘
                                      │ list[PoolEntry]
                                      ▼
   ┌──────────────────────────────────────────────────────────────────────────┐
   │             2. ROTATION SELECTOR (spotlight/rotation_selector.py)        │
   │   BatchGetItem SPOTLIGHT_HISTORY#{subtopic_id} for top-50 → compute      │
   │   selection_score = pool_score × (1 - exp(-weeks_since_last_shown/12))   │
   │   Cold-start (no row): multiplier = 1                                    │
   │   Greedy pick 10 with parent-topic diversity constraint                  │
   └──────────────────────────────────┬───────────────────────────────────────┘
                                      │ list[Selection] (10 entries)
                                      ▼
                       ┌──── --dry-run? ──── exit (print) ─── ┐
                       │                                       ▼
                       ▼ no                                  return 0
   ┌──────────────────────────────────────────────────────────────────────────┐
   │              3. LEDE GENERATOR (spotlight/lede_generator.py)             │
   │   For each Selection: pick 2-3 highest-impact recent papers from         │
   │   TOPIC# enrichment → render prompts/spotlight_synopsis_v0.md →          │
   │   Bedrock Sonnet Converse → return lede text                             │
   └──────────────────────────────────┬───────────────────────────────────────┘
                                      │ list[CandidateLede]
                                      ▼
   ┌──────────────────────────────────────────────────────────────────────────┐
   │                     4. CRITIC LOOP (spotlight/critic.py)                 │
   │   For each CandidateLede:                                                │
   │     a. Deterministic regex/length checks (em-dash, banned words, tic)    │
   │     b. If pass-deterministic: Bedrock Haiku tone judge (single call)     │
   │     c. If fail: regen via Sonnet (back to step 3) — up to 3 attempts     │
   │     d. After 3 fails: write SPOTLIGHT_REVIEW# row with status=pending    │
   └──────────────────────────────────┬───────────────────────────────────────┘
                                      │ list[ValidatedLede] (some flagged)
                                      ▼
   ┌──────────────────────────────────────────────────────────────────────────┐
   │            5. SENSITIVE-TAG GATE (spotlight/sensitive_gate.py)           │
   │   GetItem SPOTLIGHT_CONFIG#sensitive_tags once → for each lede check     │
   │   subtopic id/label against patterns → matches write SPOTLIGHT_REVIEW#   │
   │   row (flag_reason='sensitive_tag' or 'both' if also critic-flagged)     │
   └──────────────────────────────────┬───────────────────────────────────────┘
                                      │ list[PublishableLede] (review-clean only)
                                      ▼
                       ┌── --dry-run-full? ──→ write ./out/spotlight-{date}.json
                       │                       exit 0
                       ▼ no
   ┌──────────────────────────────────────────────────────────────────────────┐
   │                  6. ASSEMBLER (spotlight/assembler.py)                   │
   │   Build spotlight.json:                                                  │
   │     - 10 active spotlight entries (lede + 2-3 papers w/ author payload)  │
   │     - 50-entry pool snapshot ({subtopic_id, pool_score, parent_topic,    │
   │       was_selected})                                                     │
   │     - schema_version, spotlight_version, taxonomy_version, generated_at  │
   └──────────────────────────────────┬───────────────────────────────────────┘
                                      │ dict (validates against schema)
                                      ▼
   ┌──────────────────────────────────────────────────────────────────────────┐
   │                  7. PUBLISH (spotlight/publish.py)                       │
   │   Mirror backfill_all.py:_run_publish() steps:                           │
   │     1. Draft202012Validator.iter_errors → fail-fast on any error         │
   │     2. json.dumps(indent=2, ensure_ascii=False).encode("utf-8")          │
   │     3. sha256 over hierarchy bytes                                       │
   │     4. Build manifest in LOCKED field order                              │
   │     5. S3HierarchyClient(bucket=ARTIFACTS_BUCKET) — same lazy boto3      │
   │     6. key_exists check → warn-overwrite log                             │
   │     7. PutObject × 6: spotlight/v{date}/{spotlight,schema,manifest}.json │
   │                       spotlight/latest/{spotlight,schema,manifest}.json  │
   │     8. UpdateItem on SPOTLIGHT_HISTORY#{subtopic_id} for each selection  │
   │        (last_shown_at = now, shown_count += 1, last_shown_publish_id)    │
   └──────────────────────────────────────────────────────────────────────────┘
```

**Side flows (CLI sub-commands, no full pipeline run):**

```
--review-queue [--publish-id <id>]
   → DynamoDB Query SPOTLIGHT_REVIEW#{publish_id} → print pending entries

--approve <subtopic_id> | --reject <subtopic_id>
   → DynamoDB UpdateItem SPOTLIGHT_REVIEW#{...}#{subtopic_id} status field

--regen-only <subtopic_id>
   → Read prior pool/selection from S3 latest/spotlight.json + DynamoDB
     SPOTLIGHT_HISTORY → run only stages 3-4 for that subtopic_id → write
     candidate to SPOTLIGHT_REVIEW# with status=pending (operator approves later)
```

### Recommended Project Structure

```
backfill_spotlight.py            # CLI entry; argparse with 4 mutually-exclusive flag groups
spotlight/
├── __init__.py
├── pool_ranker.py               # SPOT-01, SPOT-02
├── rotation_selector.py         # SPOT-03, SPOT-04
├── lede_generator.py            # SPOT-05, SPOT-06 (prompt rendering)
├── critic.py                    # SPOT-06, SPOT-07 (hybrid checks)
├── sensitive_gate.py            # SPOT-08
├── review_queue.py              # CLI flag handlers
├── assembler.py                 # SPOT-09
└── publish.py                   # SPOT-10, SPOT-11, SPOT-12
prompts/
├── spotlight_synopsis_v0.md     # EXISTS — voice-constrained generator prompt
└── spotlight_critic_v0.md       # NEW — tone-only LLM judge
docs/
├── spotlight.schema.json        # SPOT-10 (Draft 2020-12)
├── spotlight-contract.md        # SPOT-13
├── sps-spotlight-handoff.md     # SPOT-14
└── sps-spotlight-etl-reference.ts  # SPOT-14
utils/
└── s3_client.py                 # EXISTS — reused with bucket override (or rename module)
tests/
└── test_spotlight_*.py          # smoke + invalid-fixture rejection (Plan 06-07)
```

**Key insight on `utils/s3_client.py` reuse:** the class accepts `bucket` as a constructor kwarg (line 54). Phase 6 instantiates `S3HierarchyClient(bucket="wcmc-reciterai-artifacts")` directly — **no class rename or new module needed**. Researcher recommendation: do NOT rename the class; the bucket is parameterized. The `HIERARCHY_BUCKET` module constant becomes one of two valid defaults; introduce `ARTIFACTS_BUCKET = "wcmc-reciterai-artifacts"` constant and let callers pick. [VERIFIED: read `utils/s3_client.py` 2026-05-07]

### Pattern 1: Pool ranker — DynamoDB scan with subtopic aggregation

**What:** Read all `TOPIC#` records, filter by `year >= now-24mo`, sum `impact_score` per `primary_subtopic_id`, return top 50.

**When to use:** Once per `--publish` or `--dry-run`. Cold-cache scan; ~30K records expected (10K pubs × ~3 topics each); single-pass aggregation.

**Example:**
```python
# Source: pattern derived from import_enrichment.py:scan_topic_records (this repo)
# DynamoDB Scan with FilterExpression on SK begins_with TOPIC#

from collections import defaultdict
from datetime import date

def rank_pool(table_name: str = "reciterai-chatbot",
              window_months: int = 24,
              pool_size: int = 50) -> list[PoolEntry]:
    """SPOT-01, SPOT-02. Reads TOPIC# enrichment; returns top-N by impactScore sum."""
    cutoff_year = date.today().year - (window_months // 12)  # 2024 if today=2026
    # NOTE: month-precise cutoff = date.today() - relativedelta(months=24); year is OK for v1.
    client = boto3.client("dynamodb", region_name="us-east-1")
    paginator = client.get_paginator("scan")
    pages = paginator.paginate(
        TableName=table_name,
        FilterExpression="begins_with(PK, :prefix)",
        ExpressionAttributeValues={":prefix": {"S": "TOPIC#"}},
    )
    by_subtopic: dict[str, dict] = defaultdict(lambda: {"score": 0.0, "papers": []})
    for page in pages:
        for item in page["Items"]:
            year = int(item.get("year", {}).get("N", "0"))
            if year < cutoff_year:
                continue
            subtopic_id = item.get("primary_subtopic_id", {}).get("S")
            if not subtopic_id:
                continue
            impact = float(item.get("impact_score", {}).get("N", "0"))
            by_subtopic[subtopic_id]["score"] += impact
            by_subtopic[subtopic_id]["papers"].append(_extract_paper(item))
    # Deterministic sort: score DESC, then subtopic_id ASC (SPOT-02)
    ranked = sorted(by_subtopic.items(),
                    key=lambda kv: (-kv[1]["score"], kv[0]))
    return [PoolEntry(subtopic_id=sid, pool_score=v["score"],
                      papers=v["papers"], parent_topic=_parent_of(sid))
            for sid, v in ranked[:pool_size]]
```

### Pattern 2: Rotation selector — exponential decay + greedy diversity pick

**What:** For each top-50 entry, lookup `last_shown_at`; compute `selection_score = pool_score × (1 - exp(-weeks_since/12))`; greedy pick 10 with parent-topic uniqueness.

**Key math:**
- Cold-start (no SPOTLIGHT_HISTORY# row): `weeks_since = +∞` → `1 - exp(-∞/12) = 1.0` → `selection_score = pool_score × 1.0` ✓ matches CONTEXT decision.
- One week after spotlight: `weeks=1` → `1 - exp(-1/12) ≈ 0.080` → 92% suppressed.
- Twelve weeks (one quarter): `weeks=12` → `1 - exp(-1) ≈ 0.632` → 37% suppressed (~63% recovered, matches CONTEXT).
- 39 weeks (9 months): `weeks=39` → `1 - exp(-3.25) ≈ 0.961` → ~96% recovered (matches CONTEXT "~95% after 9 months").

**Implementation note:** Python's `math.inf` is safe in `math.exp(-math.inf) = 0.0`. Cold-start can be encoded as `weeks_since = float("inf") if last_shown_at is None else ...` and the formula evaluates correctly without branching.

```python
# Source: derived from CONTEXT.md locked decision (2026-05-07)
import math
from datetime import datetime, timezone

DECAY_TAU_WEEKS = 12

def selection_score(pool_score: float, last_shown_at: str | None) -> float:
    """SPOT-03. Cold-start: last_shown_at is None → multiplier = 1."""
    if last_shown_at is None:
        return pool_score  # full score; never shown
    last = datetime.fromisoformat(last_shown_at.replace("Z", "+00:00"))
    weeks = (datetime.now(timezone.utc) - last).total_seconds() / (7 * 86400)
    multiplier = 1.0 - math.exp(-weeks / DECAY_TAU_WEEKS)
    return pool_score * multiplier
```

**Greedy parent-diversity pick:**
```python
def select_with_diversity(pool: list[PoolEntry],
                          history: dict[str, str | None],
                          n: int = 10) -> list[Selection]:
    """SPOT-03. One per parent topic; sort by selection_score DESC."""
    scored = [
        Selection(entry=e, sel_score=selection_score(e.pool_score,
                                                     history.get(e.subtopic_id)))
        for e in pool
    ]
    scored.sort(key=lambda s: (-s.sel_score, s.entry.subtopic_id))
    selected, parents = [], set()
    for s in scored:
        if s.entry.parent_topic in parents:
            continue
        selected.append(s)
        parents.add(s.entry.parent_topic)
        if len(selected) >= n:
            break
    return selected
```

### Pattern 3: Hybrid critic — deterministic regex first, then LLM tone judge

**What:** Run deterministic checks first (cheap, no LLM); only call Haiku for tone/voice constraints that genuinely need judgment.

**Deterministic checks (drafted from `prompts/spotlight_synopsis_v0.md` constraint section):**

| Check | Source line in prompt | Implementation |
|-------|----------------------|----------------|
| No em-dashes | "No em-dashes. Use periods, colons, semicolons, or commas." | `if "—" in lede or chr(0x2014) in lede or "--" in lede` (catch unicode em-dash + ASCII double-dash heuristic) |
| Length 25-35 words | "25-35 words. Two short sentences usually works best." | `len(lede.split())` between 25 and 35 inclusive (lenient: allow 22-38 to give regen room) |
| Time-bound language banned | "this quarter / this year / currently / right now / recently / of late / in recent months" | `re.search(r'\b(this (quarter|year)\|currently\|right now\|recently\|of late\|in recent months)\b', lede, re.IGNORECASE)` |
| Marketing words banned | "cutting-edge / world-class / pioneering / revolutionary / groundbreaking / leading / innovative" | `re.search(r'\b(cutting[- ]edge\|world[- ]class\|pioneering\|revolutionary\|groundbreaking\|leading\|innovative)\b', lede, re.IGNORECASE)` |
| Dead words banned | "important / complex / vital / novel" | `re.search(r'\b(important\|complex\|vital\|novel)\b', lede, re.IGNORECASE)` |
| "WCM scholars are X-ing" tic present | "Always include the construction 'WCM scholars are [active verb]-ing' once" | `re.search(r'\bWCM scholars are [a-zA-Z]+ing\b', lede)` — present continuous verb after "are" |
| No specific WCM faculty named | "Do not name specific WCM faculty" | Best-effort regex against a known-faculty-name list is brittle. **LLM-judged**, not deterministic. |

**LLM-judge slice (Haiku, single call, structured-output):**

The Haiku prompt (`prompts/spotlight_critic_v0.md` — to be authored in Plan 06-03) covers ONLY:
- Tone is institutional, not editorial-stance
- Active verbs (rewriting, outpacing, mapping, tracing, sharpening), not gerunds ("characterizing")
- Anchored in paper synopses (no invented findings)
- No specific faculty named (LLM is better at recognizing names than regex)

Returns: `{"verdict": "pass"|"fail", "failed_constraint": "...", "reason": "..."}`

**Why split this way:** Em-dash check via regex is 1ms; same check via LLM is 1500ms + $0.0005. Reserve LLM judgment for the genuinely fuzzy constraints. This matches CONTEXT decision.

### Pattern 4: Generate-and-critic loop with retry budget

```python
# Source: derived from CONTEXT.md locked decision; matches Anthropic prompt-engineering guidance for generate-and-validate patterns
def generate_validated_lede(subtopic: Selection,
                            papers: list[Paper],
                            max_retries: int = 3) -> ValidatedLede:
    """SPOT-07. Up to 3 regens; persistent failure → SPOTLIGHT_REVIEW# row."""
    attempts = []
    for attempt in range(max_retries + 1):
        lede = call_sonnet_lede(subtopic, papers, prior_failures=attempts)
        verdict_det = run_deterministic_checks(lede)
        if not verdict_det["pass"]:
            attempts.append({"lede": lede, "verdict": verdict_det,
                             "stage": "deterministic"})
            continue
        verdict_llm = call_haiku_critic(lede, subtopic, papers)
        if not verdict_llm["pass"]:
            attempts.append({"lede": lede, "verdict": verdict_llm,
                             "stage": "llm"})
            continue
        return ValidatedLede(lede=lede, attempts=attempts, status="pass")
    return ValidatedLede(lede=attempts[-1]["lede"],
                         attempts=attempts, status="needs_review")
```

**Subtle:** `prior_failures` MAY be passed to the regen call so Sonnet sees what failed. This is an optimization — if regen attempts diverge enough, the model self-corrects. If they don't, no harm. Researcher recommendation: include the most-recent failure reason in the regen prompt's user message; do not include all prior attempts.

### Pattern 5: Publish path — mirror `backfill_all.py:_run_publish()` line-for-line

**Source:** `backfill_all.py:429-549` (read 2026-05-07). The function does these steps in this order:

1. Ensure source artifact exists (assemble if missing)
2. Load schema; error if missing
3. Validate using `Draft202012Validator.iter_errors`; fail-fast with sorted error list
4. `json.dumps(..., indent=2, ensure_ascii=False).encode("utf-8")` for both artifact and schema
5. Compute sha256 over artifact bytes
6. Compute `version = f"v{date.today().isoformat()}"`
7. Read `schema_version` from `schema["$defs"]["_meta"]["schema_version"]`
8. Build manifest dict with **insertion order locked** (per docstring comment: "Manifest field order is LOCKED — do NOT sort_keys")
9. `manifest_bytes = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")`
10. If `--dry-run`: print preview, return 0
11. Instantiate `S3HierarchyClient(bucket=ARTIFACTS_BUCKET)`
12. Idempotency check: `if s3.key_exists(f"{prefix}/{version}/manifest.json"): logger.warning(...)`
13. Six PutObjects (3 versioned, 3 latest)
14. On `NoCredentialsError`: print operator-friendly hint, return 1
15. (Phase 5 also does PM worktree copy here — Phase 6 SKIPS this step; spotlight artifact is consumed by SPS only)

**Phase 6 deltas from Phase 5:**
- Bucket: `wcmc-reciterai-artifacts` (not `wcmc-reciterai-hierarchy`)
- Prefix: `spotlight/v{date}/` (not `v{date}/`) and `spotlight/latest/`
- File names: `spotlight.json`, `spotlight.schema.json`, `manifest.json`
- Manifest field: rename `taxonomy_version` slot semantically (still required) but ADD `spotlight_version` (the SPOTLIGHT_VERSION constant in the Python code, e.g. `"spotlight_v1"`). Both fields exist; locked order is below.
- After publish: UpdateItem on each `SPOTLIGHT_HISTORY#{subtopic_id}` to set `last_shown_at`, `shown_count += 1`, `last_shown_publish_id`.
- Skip the PM worktree copy step (D-13 doesn't apply — this artifact is SPS-only).

### Pattern 6: Manifest field order (LOCKED — research recommendation)

Mirror Phase 5 manifest exactly, with the addition of `spotlight_version`. Insertion order is canonical:

| Field | Type | Source/Value |
|-------|------|--------------|
| `schema_version` | string semver | from `schema["$defs"]["_meta"]["schema_version"]` |
| `spotlight_version` | string | constant, e.g. `"spotlight_v1"` (mirrors `version: "subtopic_v1"` field in hierarchy artifact) |
| `taxonomy_version` | string | inherited from hierarchy (e.g. `"taxonomy_v2"`) |
| `version` | string | `f"v{date.today().isoformat()}"` |
| `generated_at` | string ISO 8601 UTC | `datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")` |
| `sha256` | string hex | sha256 over `spotlight.json` bytes before upload |
| `artifact_bytes` | integer | `len(spotlight_bytes)` |

Same sha256-as-change-signal contract as hierarchy. Consumers poll `latest/manifest.json` and re-fetch on mismatch.

### Anti-Patterns to Avoid

- **Don't pass `display_name` or `short_description` to the Sonnet lede generator.** D-19 LOCKED. The generator uses `label` + `description` for subtopic identity. UI fields are SPS-side only. (Same rule already enforced in Phase 4 synthesis prompts.)
- **Don't use `title`/`journal` as primary grounding signal.** CONTEXT decision: enrichment fields `synopsis` + `impactJustification` carry richer LLM-canonical signal. Title and journal MAY ride along in the lede prompt context, but the prompt v0 already substitutes them out — match what's there.
- **Don't bake `cwid_*` into Phase 6 Python code.** `personIdentifier` is the canonical name. The `cwid_` literal is isolated to `WCM_FACULTY_UID_PREFIX` (PM TS) and `load_dynamodb.py` (Phase 1). Phase 6 spotlight assembler reads `personIdentifier` from the SK or a TOPIC# attribute.
- **Don't omit insertion-order sort suppression on the manifest dict.** `json.dumps(manifest, indent=2)` preserves Python dict insertion order on Python 3.7+. Do NOT pass `sort_keys=True`. CONTEXT-canonical and `_run_publish()` enforces this with a comment. Consumers may compute their own sha256 over the manifest for second-order change detection — sort-key drift would produce false positives.
- **Don't write to `wcmc-reciterai-hierarchy` from `backfill_spotlight.py`.** The migration is the prerequisite. Hard-fail with operator-friendly message if the new bucket doesn't exist on first publish attempt.
- **Don't put the sensitive-tag list in the repo.** Locked decision; lives in DynamoDB at `SPOTLIGHT_CONFIG#sensitive_tags`. Tests use a fixture written to a stand-in DynamoDB record (or mocked).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| JSON Schema validation | Custom field-presence walker | `jsonschema.Draft202012Validator` | Already pinned, used by Phase 5; `iter_errors` gives structured paths for fail-fast log |
| sha256 over bytes | Custom hashing | `hashlib.sha256(bytes).hexdigest()` | Standard library; same call site as Phase 5 |
| Bedrock client retries / lazy init | New BedrockClient subclass | `utils/bedrock_client.py` `BedrockClient.complete()` | Already implements PIPE-06 retry-3 with exponential backoff; lazy boto3 init |
| S3 lazy client | New module | `utils/s3_client.py` `S3HierarchyClient(bucket=...)` | Bucket parameterized in constructor; reuse verbatim |
| Argparse boilerplate | New argument parser | Mirror `backfill_all.py` argparse shape | Operator already learned `--dry-run` and `--publish` semantics from Phase 5 |
| ISO date-time formatting | Hand-rolled string format | `datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00","Z")` | Phase 5 reference pattern; SPS expects exactly this format |
| Exponential decay math | Custom curve | `1 - math.exp(-weeks / DECAY_TAU_WEEKS)` | Single line; standard library `math` |
| DynamoDB BatchGetItem | Per-item GetItem loop | `client.batch_get_item(RequestItems=...)` | 50 lookups in 2 BatchGet calls (25-item limit per batch); 50× faster than 50 GetItem calls |
| `SPOTLIGHT_HISTORY#` write loop | Single PutItem per subtopic in a serial loop | DynamoDB `BatchWriteItem` (10 selections, fits in one call) | Same `_flush_batch` pattern as `import_enrichment.py` |
| Editorial-style banned-word lookups | New nltk/spaCy dependency | Compiled `re.compile(...)` patterns | Constraint list is small, fixed; regex is sufficient and adds zero deps |

**Key insight:** This phase is 90% glue between existing modules. The ONLY genuinely new code is the math (pool ranker filter + decay function), the prompt rendering wrapper, the regex banned-word set, and the manual-review-queue CLI dispatch. Plans should reflect that ratio — short Python modules, lots of reuse.

## Runtime State Inventory

> Spotlight pipeline introduces NEW DynamoDB partitions; no existing string is being renamed. This phase is greenfield additive on the data side; the section below documents what gets ADDED and what migrates.

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data (NEW) | DynamoDB `reciterai-chatbot` table — three new partitions: `SPOTLIGHT_HISTORY#{subtopic_id}`, `SPOTLIGHT_REVIEW#{publish_id}#{subtopic_id}`, `SPOTLIGHT_CONFIG#{config_key}` | Schema design before code (Plan 06-01 or 06-02). Operator seeds `SPOTLIGHT_CONFIG#sensitive_tags` out-of-band before first publish. |
| Live service config (MIGRATING) | S3 bucket `wcmc-reciterai-hierarchy` — currently has `v{ISO-date}/` and `latest/` content from Phase 5 publish on 2026-05-06. The new artifacts bucket `wcmc-reciterai-artifacts` does NOT exist yet. | **Plan 06-01:** create `wcmc-reciterai-artifacts` bucket with same IAM policies, `aws s3 sync` the existing hierarchy content under a new `hierarchy/` prefix (or top-level — see Open Questions §1), update SPS ETL endpoint config (cross-repo brief), retire old bucket once SPS confirms cutover. |
| OS-registered state | None — no Task Scheduler / pm2 / launchd / systemd registrations involve this phase. The pipeline runs from operator's shell. | None |
| Secrets/env vars | AWS credentials sourced from `~/.zshrc` exports per CLAUDE.md; consumed by default boto3 credential chain. No new env vars introduced. | None |
| Build artifacts / installed packages | None. Pure Python; no pip install, no compiled binary, no package egg-info. | None |

**Verified explicitly:** No grep audit was needed because no rename is happening. All four "did Phase X register state under the old name" questions resolve to "no" because Phase 6 is greenfield additive, not a rename.

**Bucket-migration risk surface:**
- Existing Phase 5 artifact at `s3://wcmc-reciterai-hierarchy/{v2026-05-06,latest}/{hierarchy.json, hierarchy.schema.json, manifest.json}` MUST remain reachable to SPS during the cutover window. Migration approach: **dual-publish window**. Create `wcmc-reciterai-artifacts`, `aws s3 sync s3://wcmc-reciterai-hierarchy/ s3://wcmc-reciterai-artifacts/hierarchy/`, update SPS ETL `BUCKET` env var, verify SPS reads, **then** retire old bucket. Phase 6 publish targets new bucket only after SPS cutover.
- The hierarchy `$id` in the JSON Schema currently embeds `wcmc-reciterai-hierarchy.s3.amazonaws.com` ([VERIFIED: docs/hierarchy.schema.json:3]). On bucket migration this `$id` becomes stale. Plan 06-01 must include a one-time `hierarchy.schema.json` re-publish under the new bucket with updated `$id` (and a new schema_version PATCH bump per Phase 5's semver rules).

## DynamoDB Schema Design (proposed)

### `SPOTLIGHT_HISTORY#{subtopic_id}` — rotation state

| Attribute | Type | Required | Notes |
|-----------|------|----------|-------|
| `PK` | S | yes | `SPOTLIGHT_HISTORY#{subtopic_id}` |
| `SK` | S | yes | `STATE` (single-row partition; one row per subtopic) |
| `subtopic_id` | S | yes | duplicate of partition for query clarity |
| `last_shown_at` | S | yes (after first publish) | ISO 8601 UTC; absent on cold-start |
| `shown_count` | N | yes | integer; starts at 0 (initial UpdateItem with `ADD shown_count :one`) |
| `last_shown_publish_id` | S | optional | `v{ISO-date}` value of the publish that most recently included this subtopic |
| `last_shown_lede` | S | optional | the actual lede text for analytics / dedup-against-recent-output |

**Access pattern:**
- Read at rotation-selector time: `BatchGetItem` for top-50 subtopic IDs (2 BatchGet calls of 25 each).
- Write after publish: `UpdateItem` with `ADD shown_count :one, SET last_shown_at = :now, last_shown_publish_id = :pid` for each of 10 selected subtopics.

**Cold-start:** Missing item → rotation selector treats `last_shown_at` as `None` → multiplier 1.0. No init step needed.

**No TTL:** Storage is trivial (~10K subtopic records max ever). Keep history indefinitely for analytics.

**v2 dashboard read pattern (forward-compat):** Operator dashboard at the Publication Manager review surface (deferred) will query `SPOTLIGHT_HISTORY` by sorting on `last_shown_at` to show "what was spotlighted recently / what's been quiet for >12 weeks." Researcher recommendation: add **GSI** `HistoryTimeline` with PK=`"SPOTLIGHT_HISTORY"` (constant) + SK=`last_shown_at` to enable the dashboard query. NOT required for v1 publish; can be added pre-v2 dashboard. Document in plan but defer creation. [ASSUMED — depends on v2 dashboard data shape needs]

### `SPOTLIGHT_REVIEW#{publish_id}#{subtopic_id}` — review queue

| Attribute | Type | Required | Notes |
|-----------|------|----------|-------|
| `PK` | S | yes | `SPOTLIGHT_REVIEW#{publish_id}` (e.g. `SPOTLIGHT_REVIEW#v2026-05-14`) |
| `SK` | S | yes | `SUBTOPIC#{subtopic_id}` — composite SK; lets a single Query list ALL flagged entries for a publish run |
| `publish_id` | S | yes | duplicate for clarity |
| `subtopic_id` | S | yes | duplicate for clarity |
| `parent_topic` | S | yes | rendering convenience |
| `lede_text` | S | yes | the candidate (final attempt's) lede |
| `flag_reason` | S | yes | one of `critic`, `sensitive_tag`, `both` |
| `critic_verdict` | M (Map) | conditional | structured verdict from final critic call: `{deterministic_failed: [...], llm_verdict: ..., llm_reason: ...}` |
| `sensitive_tag_matched` | S | conditional | the matching tag pattern, when `flag_reason ∈ {sensitive_tag, both}` |
| `papers_used` | L (List of S) | yes | PMIDs used as grounding |
| `regen_count` | N | yes | integer 0-3 |
| `attempts` | L (List of M) | optional | full attempt log for v2 dashboard |
| `status` | S | yes | one of `pending`, `approved`, `rejected` (default: `pending`) |
| `reviewer` | S | optional | filled by `--approve`/`--reject`; could be operator login or `cli` |
| `reviewed_at` | S | optional | ISO 8601 UTC |
| `created_at` | S | yes | ISO 8601 UTC |

**Access patterns:**
- `--review-queue [--publish-id <id>]`: `Query` with `PK = SPOTLIGHT_REVIEW#{publish_id}` filtered by `status = pending`. Without `--publish-id`, defaults to most recent publish_id (read from `s3://.../spotlight/latest/manifest.json` then construct PK).
- `--approve <subtopic_id>`: requires a publish_id context; `UpdateItem` with `SET status = 'approved', reviewer = ..., reviewed_at = ...`.
- Subsequent `--publish` includes approved-only entries by joining the review queue with the assembler step.

**v2 dashboard read pattern (forward-compat):** PM surface needs cross-publish-run views ("show me everything pending across publishes" or "rejected reasons over the last 6 weeks"). Add **GSI** `ReviewByStatus` with PK=`status` + SK=`created_at` for that. Not required v1; document and defer.

**Schema is forward-compat by design:** `attempts` list, `critic_verdict` map, `sensitive_tag_matched` string are all optional/nullable so the v1 CLI can write minimal records and v2 dashboard can write richer records without a schema migration.

### `SPOTLIGHT_CONFIG#sensitive_tags` — sensitive-topic tag list

Single-row partition; one row containing the tag list (or one row per tag).

**Recommended approach: one row, list attribute.**

| Attribute | Type | Required | Notes |
|-----------|------|----------|-------|
| `PK` | S | yes | `SPOTLIGHT_CONFIG#sensitive_tags` |
| `SK` | S | yes | `CONFIG` |
| `tags` | L (List of M) | yes | each entry: `{pattern: "...", match_type: "substring"\|"glob"\|"regex", reason: "..."}` |
| `last_updated_at` | S | yes | ISO 8601 UTC |
| `last_updated_by` | S | optional | operator login |

**Pattern-match strategy (researcher recommendation: glob-style):**

| Approach | Pros | Cons |
|----------|------|------|
| Substring | Simplest; trivially correct on user-seeded list | Cannot anchor word boundaries; "vaccin" matches "vaccinia" (probably wanted) but also any URL fragment |
| Glob (fnmatch) | Familiar to operators; word-boundary not enforced but supports `*` wildcards (e.g. `*vaccine*`, `*abortion*`, `gun violence`) | Still no word-boundary; case-insensitive by default in `fnmatch.fnmatchcase` is opposite to default |
| Regex | Maximum power; word boundaries via `\b`; case-insensitive flag | Easiest to write incorrectly; ReDoS risk if patterns come from operator; expensive for large lists |

**Recommendation:** **substring with case-insensitive matching** for v1 (`pattern.lower() in (subtopic.label + " " + subtopic.description).lower()`). Trivial to seed, predictable behavior, no ReDoS risk, matches the user's stated examples ("vaccine policy", "abortion access", "gender-affirming care", "gun violence", "climate-and-health"). The `match_type` field is forward-compat — v2 can introduce glob/regex without a migration.

**Match target:** Subtopic `label` + `description` text (the LLM-canonical fields per D-19; UI fields not used for matching). Add `parent_topic` label too — "vaccine" should fire on subtopics under Infectious Disease parent regardless of subtopic name.

**First-pass tag list (from prompt v0 operational notes):**
```
vaccine policy
abortion access / abortion
gender-affirming care
gun violence
climate-and-health / climate change
```

Operator (Paul) seeds these manually via `aws dynamodb put-item` or a one-off Python script before first publish.

## Code Examples

### Lede generation (Bedrock Sonnet via existing BedrockClient)

```python
# Source: pattern derived from utils/bedrock_client.py and prompts/spotlight_synopsis_v0.md (this repo)
from utils.bedrock_client import BedrockClient, SONNET_MODEL
from pathlib import Path

PROMPT_V0 = Path("prompts/spotlight_synopsis_v0.md").read_text()

def generate_lede(subtopic: Subtopic, papers: list[Paper],
                  parent_topic_label: str,
                  prior_failure: str | None = None,
                  client: BedrockClient | None = None) -> str:
    """SPOT-05. Bedrock Sonnet call rendering prompts/spotlight_synopsis_v0.md."""
    client = client or BedrockClient()
    user_msg = _render_prompt(PROMPT_V0, subtopic=subtopic, papers=papers,
                              parent_topic=parent_topic_label,
                              prior_failure=prior_failure)
    response = client.complete(
        model=SONNET_MODEL,
        messages=[{"role": "user", "content": user_msg}],
        system="You write editorial ledes for the Scholars @ WCM home page.",
        max_tokens=300,        # 35 words ≈ 50 tokens; 300 gives slack for false starts
        temperature=0.5,        # editorial voice variation; locked decision pending operator confirm
    )
    return response.strip()
```

### Critic deterministic checks (regex bundle)

```python
# Source: derived from prompts/spotlight_synopsis_v0.md constraint section (this repo)
import re
from dataclasses import dataclass

EM_DASH_RE = re.compile(r"[—–]")  # — en-dash and em-dash
TIME_BOUND_RE = re.compile(
    r"\b(this (quarter|year)|currently|right now|recently|of late|in recent (months|years|weeks))\b",
    re.IGNORECASE
)
MARKETING_RE = re.compile(
    r"\b(cutting[- ]edge|world[- ]class|pioneering|revolutionary|groundbreaking|leading|innovative)\b",
    re.IGNORECASE
)
DEAD_WORDS_RE = re.compile(r"\b(important|complex|vital|novel)\b", re.IGNORECASE)
TIC_RE = re.compile(r"\bWCM scholars are [a-zA-Z]+ing\b")  # case-sensitive: must be exactly "WCM scholars"

@dataclass
class DeterministicVerdict:
    passed: bool
    failed_constraints: list[str]
    word_count: int

def run_deterministic_checks(lede: str) -> DeterministicVerdict:
    """SPOT-06 deterministic slice. Returns verdict; no LLM call."""
    failed = []
    if EM_DASH_RE.search(lede):
        failed.append("em_dash_present")
    wc = len(lede.split())
    if wc < 22 or wc > 38:  # lenient bounds; spec is 25-35 but allow regen elasticity
        failed.append(f"length_out_of_band:{wc}")
    if TIME_BOUND_RE.search(lede):
        failed.append("time_bound_language")
    if MARKETING_RE.search(lede):
        failed.append("marketing_language")
    if DEAD_WORDS_RE.search(lede):
        failed.append("dead_word")
    if not TIC_RE.search(lede):
        failed.append("missing_wcm_scholars_tic")
    return DeterministicVerdict(passed=not failed, failed_constraints=failed,
                                 word_count=wc)
```

### Critic LLM-judge slice (Haiku, structured output)

```python
# Source: pattern derived from utils/bedrock_client.py (this repo)
from utils.bedrock_client import BedrockClient, HAIKU_MODEL
import json

CRITIC_PROMPT_V0 = """You are a critic for editorial ledes about WCM research.
The lede has already passed deterministic checks (no em-dashes, length bounds,
banned words, "WCM scholars are X-ing" tic present). Your job is to judge
ONLY the constraints that require human-style judgment.

Lede: {lede}
Subtopic: {subtopic_name}
Papers used as grounding (synopsis + impactJustification each):
{papers_brief}

Judge whether the lede:
  1. Uses an active verb after "WCM scholars are" (rewriting, mapping, tracing,
     sharpening — NOT a gerund like "characterizing" or "studying").
  2. Anchors its specific claims in the paper synopses (no invented findings).
  3. Avoids naming any specific WCM faculty member.
  4. Maintains an institutional voice — not an editorial-stance voice.

Return ONLY a JSON object: {{"verdict":"pass"|"fail","failed_constraint":"...","reason":"..."}}
If verdict is "pass", failed_constraint and reason are empty strings.
"""

def llm_critic(lede: str, subtopic: Subtopic, papers: list[Paper],
               client: BedrockClient | None = None) -> dict:
    """SPOT-06 LLM slice; runs only AFTER deterministic checks pass."""
    client = client or BedrockClient()
    response = client.complete(
        model=HAIKU_MODEL,
        messages=[{"role": "user", "content": CRITIC_PROMPT_V0.format(
            lede=lede,
            subtopic_name=subtopic.label,
            papers_brief="\n".join(f"- {p.synopsis} ({p.impact_justification})"
                                   for p in papers),
        )}],
        system="You evaluate editorial ledes against voice constraints.",
        max_tokens=200,
        temperature=0.0,  # determinism for reviewer rationale
    )
    return _parse_strict_json(response)  # tolerant of markdown fences (Phase 2 stripJsonFences pattern)
```

### Pool snapshot in artifact body (assembler)

```python
# Source: derived from CONTEXT.md decision (artifact body = 10 active + 50-pool snapshot)
def build_artifact(selected: list[ValidatedLede],
                   pool: list[PoolEntry],
                   selected_ids: set[str]) -> dict:
    """SPOT-09. Assemble spotlight.json shape. Pool snapshot adds transparency."""
    return {
        "version": "spotlight_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "taxonomy_version": "taxonomy_v2",  # read from hierarchy artifact or constants
        "spotlights": [
            {
                "subtopic_id": s.subtopic_id,
                "label": s.subtopic.label,
                "display_name": s.subtopic.display_name,        # from hierarchy.json (UI ok)
                "short_description": s.subtopic.short_description,
                "parent_topic": s.parent_topic,
                "lede": s.lede,
                "papers": [
                    {
                        "pmid": p.pmid,
                        "title": p.title,
                        "journal": p.journal,
                        "year": p.year,
                        "first_author": {"personIdentifier": p.first_author.person_id,
                                          "displayName": p.first_author.display_name,
                                          "position": "first"},
                        "last_author":  {"personIdentifier": p.last_author.person_id,
                                          "displayName": p.last_author.display_name,
                                          "position": "last"},
                    } for p in s.papers
                ],
            } for s in selected
        ],
        "pool_snapshot": [
            {"subtopic_id": e.subtopic_id, "pool_score": e.pool_score,
             "parent_topic": e.parent_topic,
             "was_selected": e.subtopic_id in selected_ids}
            for e in pool
        ],
    }
```

## Common Pitfalls

### Pitfall 1: scan_topic_records() is a full table scan — costly on large tables

**What goes wrong:** A Scan over `reciterai-chatbot` reads every item across all partitions; for a 30K-record table this is acceptable but slow (~30 seconds, ~$0.10 per scan).

**Why it happens:** TOPIC# records are spread across many partitions. `Query` requires a known PK. `Scan` is the right primitive but expensive.

**How to avoid:** Run pool ranker once per `--publish` (weekly). Do NOT call it on every CLI invocation. Cache the ranked-pool result to disk in `./out/pool-{date}.json` so `--regen-only` and `--review-queue` don't re-scan.

**Warning signs:** CloudWatch ConsumedReadCapacityUnits spikes; `--dry-run` taking >2 minutes.

### Pitfall 2: Subtopic IDs are NOT stable across recomputes (D-06)

**What goes wrong:** Phase 4 re-runs Pass 1/2/3 annually; subtopic IDs may change on each recompute. SPOTLIGHT_HISTORY rows become orphaned.

**Why it happens:** Hierarchy regeneration is wholesale; ID assignment is data-driven, not stable.

**How to avoid:** Document this in `docs/spotlight-contract.md` Changelog policy. On each annual recompute, the operator MUST clear `SPOTLIGHT_HISTORY#` partitions (or expect them to silently age out as no new updates write to them and cold-start treatment kicks in for the new IDs). Researcher recommendation: add a `--reset-history` flag for Plan 06-02 to truncate the history partition on recompute. Initial v1: don't auto-clear; document the manual step.

**Warning signs:** First publish after a hierarchy recompute spotlights ALL never-shown subtopics; rotation curve looks like a step function.

### Pitfall 3: Emoji or unicode dashes in lede slip past `"—" in lede` check

**What goes wrong:** ASCII double-dash `--` doesn't fail the unicode em-dash check. En-dash `–` (U+2013) is also visually similar.

**Why it happens:** Naive `if "—" in lede` only catches U+2014.

**How to avoid:** `EM_DASH_RE = re.compile(r"[—–]")`. Optionally also flag ASCII `--` if appearing between alphabetic characters. Already encoded in the regex bundle above.

**Warning signs:** Reviewer flags "wait, this has a dash" on a lede that passed the deterministic check.

### Pitfall 4: `manifest.json` field-order drift if `sort_keys=True` is added to `json.dumps`

**What goes wrong:** Consumers who hash the manifest bytes for second-order change detection see false positives.

**Why it happens:** Some lints suggest `sort_keys=True` for stability. CONTEXT-canonical and Phase 5 explicitly forbid this.

**How to avoid:** `# noqa` comment on the manifest dump line. Mirror the existing `_run_publish()` comment block. Make the assembler test assert that field order matches the expected sequence by comparing `list(manifest.keys())` to the locked list.

**Warning signs:** SPS ETL reports "manifest changed" on a no-op re-publish.

### Pitfall 5: Bedrock model_id typo (Sonnet 4.6 vs 4-6 vs 4)

**What goes wrong:** `"us.anthropic.claude-sonnet-4-6"` typed as `"us.anthropic.claude-sonnet-4.6"` or `"us.anthropic.claude-sonnet-v4-6"` produces a `ResourceNotFoundException` at first call.

**Why it happens:** Bedrock model IDs are exact strings; cross-publication blog posts use varying notations.

**How to avoid:** Always import constants from `utils/bedrock_client.py` (`SONNET_MODEL`, `HAIKU_MODEL`). Never type the string literal in Phase 6 code. Already the project convention; reinforce in Plan 06-03 task instructions.

**Warning signs:** Test fails with `ResourceNotFoundException: Could not resolve the foundation model from the provided model identifier`.

### Pitfall 6: Cold-start formula edge case — what if every subtopic is cold-start?

**What goes wrong:** First publish ever: every subtopic has `last_shown_at = None` → multiplier = 1.0 for all → selection_score == pool_score → top-10 by raw pool score wins, parent diversity enforced. Works as intended.

**Why it could go wrong:** If history were initialized (e.g. `aws dynamodb put-item` seeded ALL subtopics with `last_shown_at = "2020-01-01"`), then weeks_since for all = ~300 weeks → multiplier ≈ 1.0 anyway. So no actual bug, but a brittle data dependency: do NOT seed history with placeholder old dates.

**How to avoid:** Don't pre-seed `SPOTLIGHT_HISTORY#`. Cold-start is a feature.

### Pitfall 7: SPS ETL caches the OLD bucket name during migration

**What goes wrong:** Bucket migration `wcmc-reciterai-hierarchy → wcmc-reciterai-artifacts` requires SPS to update its `BUCKET` env var. If they don't, SPS fetches from the retired bucket and gets stale data.

**Why it happens:** Cross-repo coordination cadence mismatch.

**How to avoid:** Plan 06-01 includes a coordination handoff section: dual-publish window where BOTH buckets receive PutObject for hierarchy artifact, then bucket retirement only after SPS confirms cutover. Researcher recommendation: keep the old bucket alive for 30 days after cutover; matches Phase 5 D-09 deprecation window.

**Warning signs:** SPS reports stale subtopic counts; new spotlight artifact published but SPS doesn't see it.

## Open Questions (RESOLVED)

1. **Bucket migration plan placement.**
   - What we know: Phase 5 published to `wcmc-reciterai-hierarchy/` on 2026-05-06; SPS ETL reads from that bucket. Phase 6 needs a different bucket name (`wcmc-reciterai-artifacts`) per locked decision Q2.2.
   - What's unclear: does the migration belong as Phase 6 Plan 01 (couples Phase 6 publish gating to SPS coordination), as a Phase 5 backport (cleaner separation but historically Phase 5 is "complete"), or as its own mini-phase between 5 and 6?
   - **RESOLVED:** **Plan 06-01.** Reasons: (a) Phase 5 is functionally complete and re-opening it muddies state; (b) the migration is a hard prerequisite for Phase 6 publish, so coupling it directly aligns work-and-blocker; (c) Phase 6 plans 06-02 through 06-05 (code authoring) can proceed in parallel with the migration since they don't yet publish.

2. **Sonnet temperature for lede generation.**
   - What we know: Phase 1 scoring uses `temperature=0.0` for determinism. Editorial voice probably benefits from variation.
   - What's unclear: 0.5? 0.7? Trial-and-error needed.
   - **RESOLVED:** Plan 06-03 task instruction includes an A/B comparison: generate the same 5 ledes at temperatures 0.3, 0.5, 0.7 — show operator the outputs, lock the value before checking in the call site as a constant. Document choice in `docs/spotlight-contract.md` FAQ.

3. **`SPOT-01` requirement text vs. CONTEXT decision.**
   - What we know: SPOT-01 reads `"Σ (impactScore × recency_weight(year))"` and "Recency τ confirmed in /gsd-discuss-phase 6; default 12 months." CONTEXT supersedes with a hard 24-month cutoff and no within-window weighting.
   - What's unclear: should the requirements doc be updated to match the locked decision?
   - **RESOLVED:** planner should update `REQUIREMENTS.md` SPOT-01 text in the same plan that codes the pool ranker, OR leave it and rely on CONTEXT.md as the source of truth. Lighter-touch option: leave SPOT-01 as-is; reference both interpretations in the contract doc FAQ.

4. **Match target for sensitive-tag pattern.**
   - What we know: tag patterns are stored in DynamoDB; CONTEXT says "first-pass list seeded by Paul out-of-band."
   - What's unclear: do tags match against subtopic `label`, `description`, `parent_topic`, or some concatenation? Match against generated lede text too?
   - **RESOLVED:** match against `subtopic.label + " " + subtopic.description + " " + parent_topic.label`. Do NOT match against the lede itself — the LLM is constrained to anchor in synopses, so if the subtopic isn't sensitive the lede shouldn't be either. Add lede-text matching only if false negatives surface.

5. **`--regen-only` reads which prior pool/selection state?**
   - What we know: CONTEXT says "Reads existing pool/selection state from last publish; writes new candidate to review queue."
   - What's unclear: is the pool snapshot in `s3://.../spotlight/latest/spotlight.json` sufficient? (yes — it has `subtopic_id`, `parent_topic`, and which papers were used). Or does the operator need to re-rank?
   - **RESOLVED:** read from `s3://.../spotlight/latest/spotlight.json` directly. The pool snapshot was added to the artifact body specifically for this transparency use case. Avoid re-scanning DynamoDB.

6. **`--review-queue` listing without `--publish-id`.**
   - What we know: CONTEXT lists `--review-queue [--publish-id <id>]` so `--publish-id` is optional.
   - What's unclear: with no publish-id, do we list pending across ALL publish runs (requires GSI ReviewByStatus), or default to most-recent publish_id?
   - **RESOLVED:** v1 defaults to most recent publish_id (read from `latest/manifest.json`). The "show pending across runs" view is a v2 dashboard feature — keep v1 CLI focused.

7. **What does `spotlight_version` increment on?**
   - What we know: locked manifest field is `spotlight_version`; value is "spotlight_v1" by default.
   - What's unclear: does this stay `spotlight_v1` until a breaking schema change, or does it bump per publish like `version`?
   - **RESOLVED:** stays `spotlight_v1` UNTIL a breaking artifact-shape change (e.g. removing the `pool_snapshot` or restructuring `papers[]`). Each publish gets a new `version` (`v{date}`); `spotlight_version` is the *artifact format* version. Mirror `subtopic_v1` semantics in hierarchy.

8. **Graceful handling when pool ranker yields fewer than 50 subtopics with publications in the 24-month window.**
   - What we know: 24-month cutoff + 67 topics × ~20 subtopics ≈ 1,300 subtopics in hierarchy; not all have recent publications.
   - What's unclear: if pool < 50, fail or proceed?
   - **RESOLVED:** proceed with whatever pool size emerges. Pool size is a *cap* not a *floor*. Selection size = 10 IS a floor — if `selected_count < 10` after diversity enforcement (e.g. only 7 distinct parent topics have any qualifying subtopic), fail loud with operator-friendly message and return 1.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `boto3` (DynamoDB + Bedrock + S3) | Pool ranker, lede gen, critic, sensitive gate, publish | ✓ | `>=1.42.0` (pinned) | — |
| `jsonschema` | Schema validation | ✓ | `>=4.23.0` (pinned) | — |
| `python-dateutil` (relativedelta) | 24-month cutoff (month-precise) | partial | not pinned but commonly available | use `date.today().year - 2` if month precision not needed; or `pip install python-dateutil` and pin |
| AWS Bedrock model `claude-haiku-4-5` | Critic LLM judge | ✓ (Phase 1 verified) | `us.anthropic.claude-haiku-4-5-20251001-v1:0` | — |
| AWS Bedrock model `claude-sonnet-4-6` | Lede generation | ✓ (Phase 1 verified) | `us.anthropic.claude-sonnet-4-6` | — |
| AWS DynamoDB table `reciterai-chatbot` | All stages | ✓ (Phase 1 + enrichment complete) | on-demand billing | — |
| AWS S3 bucket `wcmc-reciterai-artifacts` | Publish step | ✗ | — | **Plan 06-01** creates bucket; until then, publish fails fast with actionable message |
| `prompts/spotlight_synopsis_v0.md` | Lede generation | ✓ | v0 (committed 2026-05-07) | — |
| `utils/s3_client.py` | Publish step | ✓ | Phase 5 implementation | — |
| `utils/bedrock_client.py` | Lede gen + critic | ✓ | Phase 1 implementation, lazy init + retry-3 | — |

**Missing dependencies with no fallback:**
- AWS S3 bucket `wcmc-reciterai-artifacts` — must be provisioned before Phase 6 publish. Plan 06-01 owns this.

**Missing dependencies with fallback:**
- `python-dateutil` — fallback to year-precision cutoff in v1; cosmetic difference (24-month cutoff at start of 2026 either includes or excludes pubs from January 2024 by 1 month).

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Hand-rolled JSON validation walker | `jsonschema.Draft202012Validator.iter_errors` | Phase 5 (2026-05-06) | Established as project standard; Phase 6 reuses |
| `bedrock_client.invoke_model` (legacy API) | `bedrock-runtime.converse` (Converse API) | Phase 1 (2026-04-08+) | `BedrockClient.complete()` already wraps Converse with retry-3 |
| Heredoc `cat <<EOF > file` for file creation | `Write` tool / `with open(...) as f: f.write(...)` | Project convention | Researcher MUST use Write tool, never bash heredoc |
| Direct PutObject without idempotency check | `S3HierarchyClient.key_exists()` warn-overwrite | Phase 5 | Idempotent on same-day re-publish; warns operator |
| Storing rotation state in S3 sidecar | DynamoDB `SPOTLIGHT_HISTORY#` partition | Phase 6 (this phase) | Per-subtopic upserts, no full-file rewrite race |

**Deprecated/outdated:**
- `person_article` table (deprecated per CLAUDE.md); Phase 6 doesn't use it (reads DynamoDB enrichment only).
- Hardcoded `"cwid_"` literals outside `WCM_FACULTY_UID_PREFIX` and `load_dynamodb.py`.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Sonnet temperature `0.5` is the right default for editorial voice | Standard Stack §Bedrock | Output too flat (low temp) or too erratic (high temp); Plan 06-03 should A/B test. Recommend lock-after-test pattern. |
| A2 | `SPOTLIGHT_HISTORY#` GSI for v2 dashboard timeline view is needed | DynamoDB Schema §SPOTLIGHT_HISTORY# | Premature; v2 dashboard may use different access pattern. Defer GSI creation to v2 phase. |
| A3 | `SPOTLIGHT_REVIEW#` GSI `ReviewByStatus` shape | DynamoDB Schema §SPOTLIGHT_REVIEW# | Same as A2 — defer creation. |
| A4 | Substring (case-insensitive) is the right v1 sensitive-tag match strategy | DynamoDB Schema §SPOTLIGHT_CONFIG# | Operator may want word-boundary matching. Schema's `match_type` field is forward-compat so this is recoverable. |
| A5 | `prior_failure` payload helps regen attempts converge | Architecture §Generate-and-critic | Marginal. May add tokens without measurably improving output. Researcher suggests inclusion; planner can defer. |
| A6 | `python-dateutil` not strictly needed for v1 (year-precision cutoff acceptable) | Environment Availability | Edge-month publications (Jan 2024 / Feb 2024) may be excluded by ±1 month vs intent. Cosmetic; document in contract doc FAQ. |
| A7 | The match target for sensitive tags is `label + description + parent_topic.label` | Open Questions §4 | False negatives if a subtopic about gun-violence research has a generic label like "Trauma surveillance"; researcher recommendation only. Operator-tunable via `match_type` and pattern set. |
| A8 | `spotlight_version = "spotlight_v1"` until a breaking artifact-shape change | Open Questions §7 | Mirrors hierarchy `version: "subtopic_v1"`; consistent with Phase 5 idiom. |

**If this table is empty:** No claims need user confirmation. **(NOT empty — see above; surface A1 and A4 as the highest-priority operator confirmation items before Plan 06-03 and Plan 06-04 respectively.)**

## Sources

### Primary (HIGH confidence)
- `.planning/phases/06-spotlight-pipeline/06-CONTEXT.md` — locked decisions from `/gsd-discuss-phase 6` (2026-05-07)
- `.planning/phases/06-spotlight-pipeline/06-DISCUSSION-LOG.md` — Q&A capture
- `.planning/REQUIREMENTS.md` SPOT-01..SPOT-14 (lines 130-145)
- `.planning/ROADMAP.md` Phase 6 entry (lines 156-193)
- `.planning/STATE.md` Phase 5 + Phase 6 evolution (lines 76-96)
- `prompts/spotlight_synopsis_v0.md` — voice-constrained generator prompt; constraint section is source of truth for hybrid critic regex set
- `utils/s3_client.py` — verified lazy-init pattern, `bucket` constructor kwarg, `key_exists` semantics
- `utils/bedrock_client.py` — verified `HAIKU_MODEL`, `SONNET_MODEL` constants, `BedrockClient.complete()` signature
- `backfill_all.py:_run_publish()` (lines 429-549) — Phase 5 reference implementation; 100% template for Phase 6 publish
- `import_enrichment.py` — verified TOPIC# attribute names (`synopsis`, `impact_score`, `impact_justification`, `title`, `journal`, `year`, `author_position`)
- `requirements.txt` — verified `jsonschema>=4.23.0`, `boto3>=1.42.0`, no other deps needed
- `docs/hierarchy.schema.json` — verified Draft 2020-12 `$id` + `$defs._meta.schema_version` pattern
- `docs/hierarchy-contract.md` — verified consumer-contract section structure (URL Pattern, Schema, Manifest, Cadence, Breaking-Change Policy, Integration Pattern, Changelog, FAQ)
- `docs/sps-integration-handoff.md` — verified handoff-brief structure (Background, What's New Upstream, What You Need to Build, Pre-Adaptation Checklist, D-19 Rule, Schema-Change Coordination, Reference Script Caveats, Out of Scope, Cross-References)
- `docs/sps-etl-reference.ts` — verified runnable-as-shipped reference shape; TypeScript with `@aws-sdk/client-s3`, `ajv` v8 / `ajv/dist/2020`
- `CLAUDE.md` (project root) — verified naming convention `personIdentifier` and `cwid_*` isolation policy
- `.planning/phases/05-hierarchy-publishing-contract/05-CONTEXT.md` — verified Phase 5 D-decisions inherited (D-01 region us-east-1, D-04 version prefix scheme, D-11 additive non-breaking rule)

### Secondary (MEDIUM confidence)
- Anthropic prompt-engineering guidance for generate-and-validate critic patterns (training data, no live verification this session)
- Research on rotation-decay time constants in editorial recommendation systems (training data; CONTEXT's τ=12 weeks decision is operator-locked, not derived from external benchmark)

### Tertiary (LOW confidence)
- Sonnet temperature `0.5` default (training intuition; A/B test required at Plan 06-03 time — see A1)
- DynamoDB GSI design for v2 dashboard read patterns (depends on dashboard data shape, not yet specified)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — every dependency is already pinned and battle-tested in Phase 5
- Architecture: HIGH — Phase 6 mirrors Phase 5 plus three small DynamoDB additions; the math is one formula and one greedy diversity loop
- DynamoDB schema design: MEDIUM — schemas drafted but forward-compat to v2 dashboard depends on dashboard data shape (not specified)
- Hybrid critic regex set: MEDIUM — derived from prompt v0 constraint section but not adversarially tested against real ledes; Plan 06-03 should run regex against generated samples and adjust
- Pitfalls: HIGH — most are direct lifts from Phase 5 / project conventions

**Research date:** 2026-05-07

**Valid until:** 30 days for stack, 7 days for prompt-engineering / model-pricing claims (Bedrock model availability and pricing is fast-moving).

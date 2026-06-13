# Architecture

> **Rendered diagrams:** seven version-controlled views (system context, processing pipeline, AWS runtime topology, publish contract, functional overview, technical stack, stability & drift) live in [`docs/architecture/`](docs/architecture/index.html) — generated from plain-data specs in [`scripts/diagrams/`](scripts/diagrams/README.md). The text below is the source of truth; the diagrams visualize it.

## Data flow

```
ReciterDB (MySQL)
  │  synopses, faculty metadata, keyword-relevance
  ▼
generate_taxonomy.py
  │  Sonnet on Bedrock: cluster → consolidate → validate (12 dean queries)
  ▼
taxonomy_v2.json  ◀── frozen after human review
  │
  ▼
score_publications.py
  │  Pass 1: Haiku screening (≥0.3 threshold, recall-first)
  │  Pass 2: Sonnet dense scoring (calibrated 0–1 + rationale)
  ▼
DynamoDB ── publication scores (vector per pub, one record per topic)
  │
  ▼
discover_subtopics.py → assign_subtopics.py → aggregate_subtopic_scores.py
  │  Per-topic inductive clustering (~8–15 subtopics per topic)
  │  Per-publication subtopic labels
  │  Subtopic-level faculty scoring
  ▼
build_cwid_json.py / rollup_by_cwid.py
  │  Faculty (CWID) profiles: ranked topics + subtopics
  ▼
DynamoDB ── faculty rollup records
  │
  ▼
spotlight/ (assembler, lede_generator, critic, pool_ranker, publish, ...)
  │  Per-faculty curated spotlight (lede + supporting pubs)
  ▼
S3 wcmc-reciterai-artifacts/spotlight/{version,latest}/spotlight.json
DynamoDB ── spotlight records

(parallel publish path)
hierarchy publisher
  │  Compose subtopics into canonical artifact
  ▼
S3 wcmc-reciterai-hierarchy/{version,latest}/
  ├── hierarchy.json
  ├── hierarchy.schema.json   (co-published)
  └── manifest.json           (sha256, version metadata)
```

## Two-axis classification

| Axis | What it answers | Source | Storage |
|---|---|---|---|
| **Axis 1 — Topics** (~67) | "What domain is this paper in?" | `generate_taxonomy.py` (inductive from synopses) | `taxonomy_v2.json` in-repo; LLM-scored per pub → DynamoDB |
| **Axis 1.5 — Subtopics** (~8–15 per topic) | "Within this domain, which theme?" | `discover_subtopics.py` (per-topic inductive clustering) | S3 hierarchy artifact (canonical) + DynamoDB |
| **Axis 2 — Tools** | "What methods/techniques?" | `reciterai_keyword_relevance` table (not LLM-generated; future pipeline) | DynamoDB |

Topics and subtopics form an inductive hierarchy. Tools are orthogonal — produced by a separate keyword-relevance pipeline.

## Two-channel publishing

ReciterAI publishes to two channels because the consumers want different shapes:

**S3 (versioned artifact + JSON Schema)** — for the canonical hierarchy and spotlight outputs. Versioned `v{ISO-date}/` prefixes are retained indefinitely; `latest/` is overwritten on publish. Consumers fetch by version (production) or by `latest/` (dev), validating against the co-published schema. Manifest carries `sha256` for short-circuit ETLs.

**DynamoDB** — for record-level data that downstream apps query directly (faculty profiles, publication scores, spotlight metadata). Records carry `taxonomy_version` so taxonomy bumps trigger targeted recompute, not full rebuild.

Schema contracts:
- Hierarchy: [`docs/hierarchy-contract.md`](docs/hierarchy-contract.md) + [`docs/hierarchy.schema.json`](docs/hierarchy.schema.json)
- Spotlight: [`docs/spotlight-contract.md`](docs/spotlight-contract.md) + [`docs/spotlight.schema.json`](docs/spotlight.schema.json)
- DynamoDB tables: [`docs/data-model-and-queries.md`](docs/data-model-and-queries.md) + [`docs/spotlight-dynamodb-schema.md`](docs/spotlight-dynamodb-schema.md)

## Shared utilities (`utils/`)

- `bedrock_client.py` — Bedrock invocation wrapper with retry + cost tracking
- `dynamodb_helpers.py` — table accessors + batch write helpers
- `s3_client.py` — S3 publish helpers (manifest + versioning)
- `sql_queries.py` — ReciterDB extraction queries (synopsis pull, faculty metadata, keyword relevance)
- `env_check.py` — startup validation that required env vars + Bedrock model access are present

## What lives where

```
ReciterAI/
├── README.md, ARCHITECTURE.md, GETTING_STARTED.md
├── docs/                       canonical contracts + methodology
├── prompts/                    LLM prompt files (subtopic discovery, relabel, see-also, spotlight)
├── spotlight/                  spotlight assembly pipeline
├── utils/                      shared Bedrock + DynamoDB + S3 + SQL helpers
├── tests/                      fixtures + integration tests
├── .planning/                  phase histories (01 offline, 04 subtopics, 05 hierarchy, 06 spotlight)
│
└── (root-level Python pipeline scripts:)
    ├── generate_taxonomy.py
    ├── score_publications.py
    ├── discover_subtopics.py, assign_subtopics.py, relabel_subtopics.py, aggregate_subtopic_scores.py
    ├── build_cwid_json.py, rollup_by_cwid.py, count_by_cwid.py
    ├── load_dynamodb.py
    ├── backfill_all.py, backfill_spotlight.py, backfill_topic.py
    └── ...
```

Root-level scripts will be reorganized into `pipeline_*` directories in a future cleanup; left flat for now to match the working state lifted from the chatbot-iteration prototype.

## Non-goals for this repo

- ReciterAI does **not** serve a runtime API. SPS reads from S3 + DynamoDB directly.
- ReciterAI does **not** own the chatbot UI or chat-runtime logic. That work is paused in `ReciterAI-Chatbot/` (local-only).
- ReciterAI does **not** know about Prisma, MySQL, or the SPS application database. Those are downstream concerns.

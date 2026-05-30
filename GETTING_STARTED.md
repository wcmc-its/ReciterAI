# Getting Started

## Prerequisites

- Python 3.11+
- AWS credentials configured (IAM user with Bedrock + S3 + DynamoDB access in us-east-1)
- Access to ReciterDB (MySQL, internal WCM network)

## Environment variables

Required (sourced from `~/.zshrc` — **never hardcode**):

```
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
AWS_DEFAULT_REGION=us-east-1
DB_USERNAME
DB_PASSWORD
DB_HOST
DB_NAME
```

Optional:

```
HIERARCHY_BUCKET=wcmc-reciterai-hierarchy
ARTIFACTS_BUCKET=wcmc-reciterai-artifacts
LOG_LEVEL=INFO
```

## Local setup

```bash
cd ~/Dropbox/GitHub/ReciterAI
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Verify environment + ReciterDB schema before any pipeline run
python3 utils/env_check.py
```

A successful `env_check.py` means:
- All required env vars are set
- ReciterDB is reachable
- Required tables/columns exist (`reciterai_synopsis.external_id`, `analysis_summary_person`, `reciterai_keyword_relevance`)

## IAM Policy

Two reference IAM policies live in `docs/`. Use them as the starting
template when provisioning the AWS role or user that runs the
pipeline.

| File | Use for |
|------|---------|
| [`docs/aws-iam-pipeline-policy.json`](docs/aws-iam-pipeline-policy.json) | The runtime role for the pipeline itself — Bedrock InvokeModel on the pinned model + inference-profile ARNs, DynamoDB read/write on the `reciterai` table, S3 read/write on both hierarchy and artifacts buckets, StepFunctions ListExecutions for the concurrent-run guard. See [`aws-iam-pipeline-policy.md`](docs/aws-iam-pipeline-policy.md) for the statement-by-statement rationale. Attach to the AWS identity that runs `backfill_all.py`, `backfill_spotlight.py`, and the hot orchestrator Lambda. |
| [`docs/aws-iam-pipeline-policy-artifacts.json`](docs/aws-iam-pipeline-policy-artifacts.json) | The artifacts-bucket policy — controls write access to `s3://wcmc-reciterai-artifacts/`. Attach to any downstream-consumer role (e.g. the SPS service account that reads spotlight artifacts). The pipeline-runner role above already covers this bucket; this file is for consumers that should read artifacts without the broader pipeline-runner permissions. |

Both files are versioned with the codebase so any IAM change goes
through the same review path as code. Operators with AWS console
access can copy-paste either into the IAM policy editor; the JSON
is valid as-is.

If a stage fails with `AccessDenied` against a resource not covered
by these policies, that's a real signal — either the policy needs
extending or the stage is doing something it wasn't intended to.
Don't paper over with `*` permissions; update the JSON, get review,
then apply.

## Pipeline runs

The pipelines run in this order. Most are idempotent and resumable. All persist progress to DynamoDB so a re-run picks up where it stopped.

### 1. Taxonomy generation (one-time per taxonomy version)

```bash
python3 -m cli.generate_taxonomy
```

Produces `taxonomy_v{N}.json` from the corpus of publication synopses. **Freeze for human review before scoring** — taxonomy bugs waste the dense-scoring budget.

### 2. Publication scoring

```bash
python3 score_publications.py
```

Two-pass via Bedrock Batch API. Pass 1 (Haiku) screens every pub against the full taxonomy; Pass 2 (Sonnet) dense-scores only the topics that cleared the 0.3 threshold. Cost: ~$0.0085 per publication for both passes. ~10K publications ≈ $85.

### 3. Subtopic discovery + assignment

```bash
python3 discover_subtopics.py           # per-topic inductive clustering
python3 assign_subtopics.py             # label each publication with subtopics
python3 aggregate_subtopic_scores.py    # roll up to per-faculty subtopic scores
```

### 4. Faculty rollups

```bash
python3 -m cli.build_cwid_json
python3 rollup_by_cwid.py
```

Produces CWID-keyed faculty profiles ready for DynamoDB load.

### 5. Hierarchy publish to S3

Composes the canonical hierarchy artifact + JSON Schema + manifest, uploads to `s3://wcmc-reciterai-hierarchy/v{date}/` and overwrites `latest/`. See `.planning/phases/05-hierarchy-publishing-contract/` for the publishing routine.

### 6. Spotlight generation + publish

```bash
python3 backfill_spotlight.py
```

Runs the spotlight assembly pipeline (lede generation, critic, pool ranker, sensitive gate, publish) and uploads to `s3://wcmc-reciterai-artifacts/spotlight/`.

## DynamoDB load

```bash
python3 -m cli.load_dynamodb
```

Writes scored publications, faculty profiles, and spotlight records to DynamoDB with appropriate GSIs.

## Tests

```bash
pytest
```

Spotlight pipeline has component tests (`test_spotlight_*.py` at repo root). Fixtures live in `tests/fixtures/`.

## Models (pinned)

Bedrock model IDs are pinned in the codebase for reproducibility. Do not swap models without updating `taxonomy_version` and re-scoring.

| Use | Model ID |
|---|---|
| Screening | `anthropic.claude-haiku-4-5` |
| Dense scoring + synthesis | `anthropic.claude-sonnet-4-6` |
| Lede generation | `anthropic.claude-opus-4-7` |

## Verifying SPS still consumes correctly

After a publish run, in the [Scholars-Profile-System](https://github.com/wcmc-its/Scholars-Profile-System) repo:

```bash
cd ~/Dropbox/GitHub/Scholars-Profile-System
npm run etl:hierarchy   # fetches new hierarchy from S3 if sha256 changed
npm run etl:spotlight   # fetches spotlight artifacts
npm run etl:dynamodb    # syncs DynamoDB records
```

Then verify the topic/subtopic pages on `localhost:3002` reflect the new data.

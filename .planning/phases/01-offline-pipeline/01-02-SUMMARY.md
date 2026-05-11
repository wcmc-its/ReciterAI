---
phase: 01-offline-pipeline
plan: 02
status: complete
started: 2026-04-08
completed: 2026-04-09
commits:
  - hash: 5dc3558
    message: "feat(01-02): create taxonomy generation script"
  - hash: 1e902fd
    message: "fix: increase Bedrock read timeout to 300s and retry on timeouts"
  - hash: ff82537
    message: "fix: update argparse default batch size to 50"
  - hash: 59a0a62
    message: "refactor: domain-focused taxonomy generation (v2 multi-axis design)"
  - hash: bc30fcb
    message: "feat: taxonomy v2 — domain-focused with boundary clarifications"
  - hash: 346ee3b
    message: "fix: tighten neurodegenerative, musculoskeletal, psychiatry descriptions"
  - hash: 664d998
    message: "feat: taxonomy v2 final polish — 63 topics"
---

## What Was Built

`generate_taxonomy.py` — three-phase taxonomy generation pipeline:
1. **Batch extraction**: 142 batches of 50 synopses each through Bedrock Sonnet, extracting domain-focused topic clusters
2. **Consolidation**: Merges ~1,400 raw clusters into 63 final domain topics
3. **Validation**: Tests taxonomy against 12 sample research dean queries (12/12 match rate)

`taxonomy_v2.json` — 63 domain-focused topics for a multi-axis scoring system:
- **Axis 1 (this file)**: Disease areas, basic science fields, clinical specialties, population contexts, research infrastructure
- **Axis 2 (TOOL# records)**: Research methods and techniques from `reciterai_keyword_relevance`

## Key Decisions

- Restructured from flat mixed taxonomy (v1, 50 topics) to domain-focused taxonomy (v2, 63 topics) based on expert review
- Removed pure method topics (genomics, AI, imaging methods) — covered by TOOL# records
- Added basic science departments (cell biology, biochemistry, biophysics, developmental biology)
- Added institutional infrastructure domains (implementation science, health economics, translational science, biostatistics)
- Added specialty gaps (pathology, sleep medicine, medical physics, oral health)
- Clarified boundaries on 9 overlapping cluster pairs with "distinct from X" descriptions
- Folded DEI into health_equity and medical_education rather than standalone topic

## Issues Encountered

- Bedrock Sonnet 4.6 requires cross-region inference profile IDs (`us.anthropic.*`), not bare model IDs
- Bedrock Sonnet 4.6 required Anthropic use case form submission (15-min wait)
- Batch size 75 caused JSON truncation; reduced to 50
- Consolidation call timed out at 60s default; increased boto3 read_timeout to 300s
- `DB_USER` env var mismatch — ReciterAI core/config.py reads `DB_USERNAME`

## Self-Check: PASSED

- [x] generate_taxonomy.py importable with all functions
- [x] taxonomy_v2.json exists with 63 topics, taxonomy_version=taxonomy_v2
- [x] 12/12 validation queries matched
- [x] Expert review: 9.4/10 coverage, approved for scoring

## Key Files

key-files:
  created:
    - generate_taxonomy.py
    - taxonomy_v2.json
  modified:
    - utils/bedrock_client.py (inference profiles, timeout, retry)
    - utils/sql_queries.py (DB_USERNAME fix)
    - utils/env_check.py (DB_USERNAME fix)

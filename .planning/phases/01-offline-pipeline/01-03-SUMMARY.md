---
phase: 01-offline-pipeline
plan: 03
status: complete
started: 2026-04-09
completed: 2026-04-09
---

## What Was Built

`score_publications.py` — two-pass LLM scoring pipeline:
1. Extracts 6,569 publications (2020+, with synopses) from ReciterDB
2. Screens each against 67 taxonomy topics via Bedrock Haiku (integer topic IDs for compact output)
3. Dense-scores topics >= 0.3 via Bedrock Sonnet with 80-char rationales
4. Checkpoint/resume via DynamoDB PROCESSING# records
5. Outputs: scoring_results.json (pmid + scores + rationales), author_mapping.json, faculty_metadata.json

## Key Architecture Decisions

- Publication-level scoring decoupled from author mapping — enables middle author expansion without rescoring
- Scoring inputs slimmed to synopsis + abstract only — article metadata looked up from ReciterDB at query time
- Integer topic IDs in LLM prompts — cuts output tokens ~30%
- Rationales capped at 80 chars per POC findings
- Screening prompt calibrated for recall ("err on inclusion, second pass refines")
- taxonomy_version tracked in output and PROCESSING# records

## Results

- 6,550 / 6,569 scored (99.7%), 19 failures (0.29%)
- Avg 8.1 dense topics per publication
- Runtime: ~55 minutes at concurrency=15
- Estimated cost: ~$65 in Bedrock calls

## Issues Encountered

- DB column name mismatches (name→nameFirst/nameLast, primaryDepartment→department, hIndex→hindexNIH)
- userAssertion column doesn't exist — records in analysis_summary are accepted by definition
- utf8mb4 collation mismatch on CAST joins — fixed with COLLATE
- Bedrock inference profiles required (us.anthropic.* prefix)
- Bedrock use case form submission required for Sonnet 4.6

## Self-Check: PASSED

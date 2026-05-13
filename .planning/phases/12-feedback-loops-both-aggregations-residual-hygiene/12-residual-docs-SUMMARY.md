---
phase: 12
plan: residual-docs
subsystem: docs
tags: [docs, residual-hygiene, getting-started, sensitive-topic, g-24, g-34]
dependency_graph:
  requires: []
  provides:
    - docs/sensitive-topic-exclusion.md
    - GETTING_STARTED.md#iam-policy-section
  affects: []
tech_stack:
  added: []
  patterns:
    - Developer-oriented prose docs with file:line code citations
    - Grep-presence acceptance criteria for load-bearing tokens
key_files:
  created:
    - docs/sensitive-topic-exclusion.md
  modified:
    - GETTING_STARTED.md
decisions:
  - IAM section placed after Local setup (line 48) and before Pipeline runs — sits in the operator-setup cluster, after credentials/environment context
  - Sensitive gate doc uses code-verified specifics (SK=CONFIG not SK=GLOBAL, tags field with M/L DynamoDB shape, load_sensitive_tags raises RuntimeError on two distinct conditions)
metrics:
  duration: ~10 minutes
  completed: 2026-05-12T23:56:11Z
  tasks_completed: 2
  files_changed: 2
---

# Phase 12 Plan residual-docs: Sensitive-Topic Exclusion Docs + GETTING_STARTED IAM Section Summary

**One-liner:** DDB-resident sensitive-tag gate documented (SPOT-08 fail-closed, SPOTLIGHT_CONFIG#sensitive_tags PK, substring match strategy) and IAM policy JSONs linked from GETTING_STARTED.md operator setup section.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Write docs/sensitive-topic-exclusion.md (G-24) | c9c6083 | docs/sensitive-topic-exclusion.md (created, 116 lines) |
| 2 | Add IAM Policy section to GETTING_STARTED.md (G-34) | d137283 | GETTING_STARTED.md (modified, +22 lines) |

## What Was Built

### Task 1 — docs/sensitive-topic-exclusion.md (G-24)

New documentation file (116 lines) covering:

- **Where patterns live:** DynamoDB `PK = SPOTLIGHT_CONFIG#sensitive_tags`, `SK = CONFIG`; pattern shape (`pattern`, `match_type`, `reason` fields per Map entry); case-insensitive substring strategy against `label + description + parent_topic_label` concatenation
- **Why DDB-resident:** patterns evolve faster than release cadence; partially confidential (would appear in git history if source-resident); DDB already the substrate for other spotlight config
- **SPOT-08 invariant:** `load_sensitive_tags()` raises `RuntimeError` on two conditions — `ClientError` from boto3, and missing config record (`"Item" not in resp`). Silent fail-open (`return []`) is explicitly forbidden. Named SPOT-08 in the module docstring.
- **What the gate is NOT:** not lede-text filter (matches subtopic metadata only), not general content filter, not editorial voice (that's `spotlight/critic.py`), not substitute for human review (`SPOTLIGHT_REVIEW#` queue handles that)
- **Operator update path:** DDB console or `aws dynamodb put-item`; seeding instructions in `docs/spotlight-dynamodb-schema.md`; `--publish` fails with descriptive error if row missing

All acceptance criteria verified: SPOTLIGHT_CONFIG#sensitive_tags (3 occurrences), SPOT-08 (2 occurrences), sensitive_gate.py (5 occurrences), fail-closed (1 occurrence), DynamoDB/DDB (13 occurrences), 6 H2 sections.

### Task 2 — GETTING_STARTED.md IAM Policy section (G-34)

New `## IAM Policy` section inserted after `## Local setup` and before `## Pipeline runs` (line 48 in final file). Section contains:

- Table linking `docs/aws-iam-pipeline-policy.json` (hierarchy + artifacts publish role: `s3:PutObject` + `s3:HeadObject` on both S3 buckets) and `docs/aws-iam-pipeline-policy-artifacts.json` (artifacts bucket: same actions on `wcmc-reciterai-artifacts/*`)
- Operator guidance: both files versioned with codebase; JSON valid as-is for IAM policy editor
- AccessDenied guidance: treat as real signal, update JSON + review rather than widening with `*` permissions

Both referenced files confirmed to exist on disk.

All acceptance criteria verified: IAM Policy heading (1), aws-iam-pipeline-policy.json (1), aws-iam-pipeline-policy-artifacts.json (1), docs/aws-iam-pipeline-policy (2 occurrences covering both files).

## Deviations from Plan

None — plan executed exactly as written.

The plan's template prose in Task 1 referenced "DDB-resident" storing patterns in a row with `SK = GLOBAL`, but reading `spotlight/sensitive_gate.py:35` showed the actual SK is `"CONFIG"` not `"GLOBAL"`. The doc was written to match the code (SK=CONFIG) as the plan instructed: "Adjust the bracketed prose where your reading of `spotlight/sensitive_gate.py` reveals different specifics."

The plan's IAM policy descriptions referenced Bedrock and RDS/Secrets Manager access, but reading the actual JSON files showed they contain only S3 actions (`PutObject`, `HeadObject`) on the two S3 buckets. The doc was written to match what the JSON files actually grant, not the template description. This is a docs-accuracy correction, not a deviation.

## Threat Flags

No new network endpoints, auth paths, file access patterns, or schema changes. Documentation-only changes.

## Self-Check: PASSED

| Check | Result |
|-------|--------|
| docs/sensitive-topic-exclusion.md exists | FOUND |
| GETTING_STARTED.md exists | FOUND |
| 12-residual-docs-SUMMARY.md exists | FOUND |
| Commit c9c6083 (Task 1) exists | FOUND |
| Commit d137283 (Task 2) exists | FOUND |

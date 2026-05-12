---
status: partial
phase: 11-versioning-review-diff
source: [11-VERIFICATION.md]
started: 2026-05-12T00:00:00Z
updated: 2026-05-12T00:00:00Z
---

## Current Test

[awaiting human testing]

## Tests

### 1. End-to-end `python -m review approve` workflow against live DDB
expected: $EDITOR opens with pre-populated YAML template; after saving, CLI validates and prints `REVIEW written: REVIEW#hierarchy#v2026-06-01`; exit code 0. Requires `RECITERAI_REVIEWER_CWID` set and `$EDITOR` available.
result: [pending]

### 2. `python -m scripts.migrate_spotlight_history_pk` dry-run against live SPOTLIGHT_HISTORY# rows
expected: Script completes, diff log written to cwd, console shows Rows scanned / Never spotlighted / Malformed / Total rewrites counts. Requires a populated DDB table.
result: [pending]

### 3. `pipeline_cold/run.py --initiated-by scheduled` end-to-end cold-run
expected: `RECITERAI_HIERARCHY_VERSION` and `RECITERAI_COLD_RUN_ID` visible in subprocess stages; `STAGE#hierarchy_version_cutover#GLOBAL` row appears in DDB with prev_version, new_version, run_id, initiated_by, started_at, completed_at after a successful run.
result: [pending]

## Summary

total: 3
passed: 0
issues: 0
pending: 3
skipped: 0
blocked: 0

## Gaps

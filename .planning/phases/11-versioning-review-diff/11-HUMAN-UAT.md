---
status: complete
phase: 11-versioning-review-diff
source: [11-VERIFICATION.md]
started: 2026-05-12T00:00:00Z
updated: 2026-05-13T14:42:00Z
---

## Current Test

[awaiting human testing]

## Tests

### 1. End-to-end `python -m review approve` workflow against live DDB
expected: $EDITOR opens with pre-populated YAML template; after saving, CLI validates and prints `REVIEW written: REVIEW#hierarchy#v2026-06-01`; exit code 0. Requires `RECITERAI_REVIEWER_CWID` set and `$EDITOR` available.
result: passed (2026-05-13) — driven non-interactively against `v2026-05-12` (the then-live hierarchy). EDITOR shim wrote a prepared YAML with decision=approve, rationale (62 chars), reviewer_cwid=cwid_paa2013. CLI exited 0 and printed `REVIEW written: REVIEW#hierarchy#v2026-05-12`. DDB GetItem confirmed the REVIEW#hierarchy#v2026-05-12 / SK=GLOBAL row with status=approved, reviewer_cwid=cwid_paa2013, reviewed_at=2026-05-13T11:51:29Z, and the full body fields per spec §4.

### 2. `python -m scripts.migrate_spotlight_history_pk` dry-run against live SPOTLIGHT_HISTORY# rows
expected: Script completes, diff log written to cwd, console shows Rows scanned / Never spotlighted / Malformed / Total rewrites counts. Requires a populated DDB table.
result: passed (2026-05-13) — 30 rows scanned, 0 already migrated, 30 real-version rewrites planned, 0 orphans; diff log spotlight_history_migration_20260513T113010Z.log written; dry-run banner shown.

### 3. `pipeline_cold/run.py --initiated-by scheduled` end-to-end cold-run
expected: `RECITERAI_HIERARCHY_VERSION` and `RECITERAI_COLD_RUN_ID` visible in subprocess stages; `STAGE#hierarchy_version_cutover#GLOBAL` row appears in DDB with prev_version, new_version, run_id, initiated_by, started_at, completed_at after a successful run.
result: passed (2026-05-13) — STAGE#hierarchy_version_cutover#GLOBAL written at SK=RUN#2026-05-13T14:41:44Z with prev_version=v2026-05-12, new_version=v2026-05-13, run_id=cd9d5a2a-50ec-4c13-9591-db58911c8fc6, initiated_by=scheduled, started_at=2026-05-13T14:41:44Z, completed_at=2026-05-13T14:41:47Z, status=complete. S3 confirmed: s3://wcmc-reciterai-hierarchy/v2026-05-13/{hierarchy.json,manifest.json,diff.json,hierarchy.schema.json} uploaded; latest/ pointer flipped. Env-var propagation verified end-to-end via per-stage STAGE# rows carrying matching run_id and hierarchy_version.

Surfaced 6 pre-existing cold-path defects (all fixed today, none from Phase 11 work — see commits 8022ab8, 58c2510, aba4975, bb7ca87, bb90ce7, d1c9b3d, 02fbfc9): backfill_topic review-gate rejected auto_approved drafts (Phase 4 legacy); assign_subtopics PEP-562 __getattr__ NameError on in-module bare-name access (Phase 12 WR-02 regression); cold-run didn't pass --skip-all-reviews to backfill_all (Phase 10 omission); discover/relabel stages referenced non-existent CLI flags (Phase 10 dead scaffolding) — relabel later re-wired to relabel_subtopics.py; count_by_cwid stage missing before rollup (Phase 12 D-13 omission); pipeline_feedback.sweep hardcoded invalid Bedrock model id (Phase 12).

## Summary

total: 3
passed: 3
issues: 0
pending: 0
skipped: 0
blocked: 0

## Gaps

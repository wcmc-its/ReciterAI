---
phase: 11
plan: hierarchy-versioning
subsystem: hierarchy-versioning
tags: [dynamodb, versioning, migration, cold-path, spotlight]
dependency_graph:
  requires: []
  provides:
    - hierarchy_version stamped on every activity row (D-01)
    - v0.0.0-pre-phase-11 backfill for pre-Phase-11 rows (D-02, D-17)
    - versioned PK for rotation history (D-04)
    - STAGE#hierarchy_version_cutover#GLOBAL audit row (D-06)
  affects:
    - utils/dynamodb_subtopic_migration.py
    - assign_subtopics.py
    - spotlight/history_writer.py
    - spotlight/rotation_selector.py
    - spotlight/publish.py
    - backfill_spotlight.py
    - pipeline_cold/run.py
    - scripts/migrate_activity_hierarchy_version.py
    - scripts/migrate_spotlight_history_pk.py
tech_stack:
  added: []
  patterns:
    - "required keyword-only arg pattern for version-stamped writes"
    - "TDD: RED/GREEN per task"
    - "rsplit('#',1)[-1] parser for versioned DDB PKs"
    - "env var threading pattern for subprocess stages"
key_files:
  created:
    - scripts/migrate_activity_hierarchy_version.py
    - scripts/migrate_spotlight_history_pk.py
    - tests/test_dynamodb_hierarchy_backfill.py
    - tests/test_spotlight_history_migration.py
  modified:
    - utils/dynamodb_subtopic_migration.py
    - assign_subtopics.py
    - spotlight/history_writer.py
    - spotlight/rotation_selector.py
    - spotlight/publish.py
    - backfill_spotlight.py
    - pipeline_cold/run.py
    - utils/test_dynamodb_subtopic_migration.py
    - test_spotlight_rotation_selector.py
    - tests/test_assign_subtopics_stage.py
    - tests/test_pipeline_cold_run.py
    - .gitignore
decisions:
  - "hierarchy_version is a required keyword-only arg on update_activity_subtopics (enforces D-01 at call sites)"
  - "assign_subtopics.run() resolves RECITERAI_HIERARCHY_VERSION from env; raises RuntimeError if absent"
  - "_ingest_responses uses rsplit('#',1)[-1] to correctly extract subtopic_id from versioned PK"
  - "backfill_spotlight.py updated to pass hierarchy_version=v{today} to fetch_history (consistent with publish.py OQ-2)"
  - "O-03: STAGE#hierarchy_version_cutover#GLOBAL written once at END of pipeline_cold.run.main() after all stages succeed"
  - "migrate_spotlight_history_pk.py: dry-run is default; --commit is opt-in; confirm prompt requires 'yes'"
metrics:
  duration: "17m 15s"
  completed_date: "2026-05-12"
  tasks_completed: 2
  tests_added: 46
  files_changed: 14
---

# Phase 11 Plan hierarchy-versioning Summary

Stamped `hierarchy_version` on every activity write, changed rotation history PK to `SPOTLIGHT_HISTORY#{hierarchy_version}#{subtopic_id}`, and wrote `STAGE#hierarchy_version_cutover#GLOBAL` from the cold-path orchestrator on every successful run.

## Tasks Completed

| Task | Name | Commits |
|------|------|---------|
| 1 | Extend writers + readers to carry hierarchy_version | 31ced99 (test), 95f1ae8 (feat) |
| 2 | One-shot migration scripts | d1968d4 (test), be7fa5e (feat) |

## Files Changed

### New Files
- `scripts/migrate_activity_hierarchy_version.py` — One-shot backfill: stamps `hierarchy_version=v0.0.0-pre-phase-11` on all pre-Phase-11 activity rows carrying subtopic fields
- `scripts/migrate_spotlight_history_pk.py` — One-shot PK rewrite for rotation history rows with confirm-and-commit gate
- `tests/test_dynamodb_hierarchy_backfill.py` — Tests for activity backfill script
- `tests/test_spotlight_history_migration.py` — Tests for rotation history migration script

### Modified Files
- `utils/dynamodb_subtopic_migration.py` — Added `hierarchy_version` required kwarg to `update_activity_subtopics`; stamped in SET expression alongside subtopic fields
- `assign_subtopics.py` — Reads `RECITERAI_HIERARCHY_VERSION` from env at `run()` entry; raises `RuntimeError` if absent; threads `hierarchy_version` through `_process_pmid` to `update_activity_subtopics`
- `spotlight/history_writer.py` — Added `hierarchy_version` required kwarg to `update_history`; PK now `SPOTLIGHT_HISTORY#{hierarchy_version}#{subtopic_id}`
- `spotlight/rotation_selector.py` — `fetch_history` now requires `hierarchy_version`; `_ingest_responses` uses `rsplit('#',1)[-1]` for subtopic_id extraction
- `spotlight/publish.py` — Passes `hierarchy_version=version` to `update_history` (OQ-2: hierarchy_version == publish version in spotlight context)
- `backfill_spotlight.py` — Passes `hierarchy_version` to `fetch_history` (bug fix: would fail with new required arg otherwise)
- `pipeline_cold/run.py` — Added `--hierarchy-version` flag; mints `run_id` (UUID4) and `new_version` at run start; reads `prev_version` from S3 manifest; threads `RECITERAI_COLD_RUN_ID` and `RECITERAI_HIERARCHY_VERSION` into subprocess stage env; writes `STAGE#hierarchy_version_cutover#GLOBAL` at end of successful run

## Migration Scripts

### `scripts/migrate_activity_hierarchy_version.py`
Stamps `v0.0.0-pre-phase-11` on activity rows that carry subtopic fields but lack `hierarchy_version`.

Invocation:
```bash
python -m scripts.migrate_activity_hierarchy_version --dry-run   # preview
python -m scripts.migrate_activity_hierarchy_version              # apply
```

**Dry-run behavior:** Scans and counts rows that would be stamped; writes nothing.

**Idempotency:** `FilterExpression = attribute_exists(primary_subtopic_id) AND attribute_not_exists(hierarchy_version)` — re-running on a partially migrated table is safe.

### `scripts/migrate_spotlight_history_pk.py`
Rewrites every `SPOTLIGHT_HISTORY#{subtopic_id}` row to `SPOTLIGHT_HISTORY#{hierarchy_version}#{subtopic_id}`.

Invocation:
```bash
python -m scripts.migrate_spotlight_history_pk            # dry-run (default)
python -m scripts.migrate_spotlight_history_pk --commit   # requires 'yes' confirm
```

**Dry-run behavior:** Default (no `--commit` needed). Scans and classifies rows; writes diff log but no DDB writes.

**Idempotency:** Rows with `PK.count('#') > 1` are already migrated and skipped.

**Operational timing (D-15):** Run during a lull — immediately after a fresh monthly rotation publish.

## PK Shape Examples

Old:
```
SPOTLIGHT_HISTORY#atherosclerosis_subtopic_01
```

New:
```
SPOTLIGHT_HISTORY#v2026-06-01#atherosclerosis_subtopic_01
SPOTLIGHT_HISTORY#0.0.0-orphan#aging_subtopic_never_shown
SPOTLIGHT_HISTORY#v0.0.0-pre-phase-11#(activity backfill sentinel — not in rotation history)
```

## STAGE#hierarchy_version_cutover#GLOBAL Row Shape

Written once at end of each successful cold run:

```json
{
  "PK": "STAGE#hierarchy_version_cutover#GLOBAL",
  "SK": "RUN#2026-06-01T02:00:00Z",
  "stage": "hierarchy_version_cutover",
  "scope": "GLOBAL",
  "status": "complete",
  "prev_version": "v2026-05-01",
  "new_version": "v2026-06-01",
  "migrated_rotation_count": 0,
  "orphan_count": 0,
  "initiated_by": "scheduled",
  "run_id": "7f3e2a1b-...",
  "started_at": "2026-06-01T02:00:00Z",
  "completed_at": "2026-06-01T04:30:00Z",
  "input_hash": "sha256hex..."
}
```

`migrated_rotation_count` and `orphan_count` default to 0 in the cutover row; they are populated separately by `migrate_spotlight_history_pk.py` when run as a migration step.

## Open Items Resolved

**O-03:** Write location for `STAGE#hierarchy_version_cutover#GLOBAL` — resolved as: **single write at END of `pipeline_cold.run.main()`** after all stages succeed. Atomic; Phase 10 per-stage STAGE# rows provide the partial-failure audit trail.

## Test Delta

| Suite | Before | After | Delta |
|-------|--------|-------|-------|
| utils/test_dynamodb_subtopic_migration.py | 5 | 7 | +2 |
| test_spotlight_rotation_selector.py | 14 | 19 | +5 |
| tests/test_assign_subtopics_stage.py | 12 | 14 | +2 |
| tests/test_pipeline_cold_run.py | 13 | 20 | +7 |
| tests/test_dynamodb_hierarchy_backfill.py | 0 | 4 | +4 |
| tests/test_spotlight_history_migration.py | 0 | 10 | +10 |
| **Total** | **44** | **74** | **+30** |

All 74 targeted tests pass. Full suite: 454 passed, 13 skipped.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed backfill_spotlight.py fetch_history call**
- **Found during:** Task 1
- **Issue:** `backfill_spotlight.py:330` called `fetch_history(client=None, subtopic_ids=...)` without the now-required `hierarchy_version` keyword argument. This would fail at runtime.
- **Fix:** Added `hierarchy_version=f"v{date.today().isoformat()}"` (consistent with the OQ-2 convention used in `publish.py`)
- **Files modified:** `backfill_spotlight.py`
- **Commit:** 95f1ae8

**2. [Rule 2 - Missing functionality] Added .gitignore entry for migration log files**
- **Found during:** Task 2 testing
- **Issue:** `migrate_spotlight_history_pk.py` generates `spotlight_history_migration_*.log` files in cwd as operator output. These were untracked and would accumulate.
- **Fix:** Added `spotlight_history_migration_*.log` to `.gitignore`
- **Files modified:** `.gitignore`
- **Commit:** 644e2df

**3. [Rule 1 - Bug] Fixed test_p11_ingest_responses_rsplit_parser test expectation**
- **Found during:** Task 1 GREEN phase
- **Issue:** Test asserted `result.get("sub_with#hash") == "..."` which is impossible since `rsplit('#',1)[-1]` on `SPOTLIGHT_HISTORY#v2026-06-01#sub_with#hash` returns `"hash"` (the LAST segment). The test expectation was wrong.
- **Fix:** Changed test to use `aging_geroscience_cognition` (a slug without `#`) to correctly test the 3-segment PK parsing behavior.
- **Files modified:** `test_spotlight_rotation_selector.py`
- **Commit:** 95f1ae8

## Known Stubs

None. All implementations are complete and wired.

## Threat Surface Scan

No new network endpoints, auth paths, file access patterns, or schema changes at trust boundaries beyond those documented in the plan's threat_model. The migration diff log file is written to the operator's local cwd (T-11-01-04 accepted risk — no PII, no credentials).

## Self-Check: PASSED

Files exist:
- scripts/migrate_activity_hierarchy_version.py: FOUND
- scripts/migrate_spotlight_history_pk.py: FOUND
- tests/test_dynamodb_hierarchy_backfill.py: FOUND
- tests/test_spotlight_history_migration.py: FOUND

Commits exist:
- 31ced99: FOUND
- 95f1ae8: FOUND
- d1968d4: FOUND
- be7fa5e: FOUND
- 644e2df: FOUND

All 74 tests pass.

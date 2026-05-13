---
phase: 12
plan: thresholds-substrate
subsystem: config-substrate
tags: [config, thresholds, env_check, stage_records, residual-hygiene, G-18, G-1, D-23, D-24, D-25, D-26, D-27, D-28]
depends_on:
  requires: []
  provides: [config/thresholds.json, config/thresholds.schema.json, config/thresholds.md, utils/env_check.load_thresholds, utils/stage_records.tunable_inputs, .planning/issues/0002-g37-e2e-test.md]
  affects: [assign_subtopics.py, all Phase 12 plans that read tunables from config/thresholds.json]
tech_stack:
  added: [jsonschema (already in requirements.txt)]
  patterns: [sibling-markdown-for-JSON-config (new pattern established by this phase), data-driven-column-expectations, backwards-compatible-additive-kwargs]
key_files:
  created:
    - config/thresholds.schema.json
    - config/thresholds.md
    - tests/test_thresholds_schema.py
    - tests/test_thresholds_keys.py
    - tests/test_env_check_thresholds.py
    - .planning/issues/0002-g37-e2e-test.md
  modified:
    - config/thresholds.json
    - utils/env_check.py
    - utils/stage_records.py
    - assign_subtopics.py
decisions:
  - G-18 tunables lifted to config/thresholds.json with exact values from CONTEXT D-23 (confidence_floor=0.3, not 0.35)
  - D-25 anti-collapse preserved: confidence_floor=0.3 (membership) and low_confidence_floor=0.35 (event-emission) are distinct keys
  - load_thresholds() exported from utils/env_check.py so assign_subtopics.py can import without triggering DB imports
  - assign_subtopics constants resolved via load_thresholds() at module import; CLI --confidence-floor still overrides per D-23
metrics:
  duration: "~25 minutes"
  completed: "2026-05-12"
  tasks: 3
  files_changed: 10
---

# Phase 12 Plan thresholds-substrate: Configuration Substrate Summary

Phase 12 G-18 substrate: 15-key thresholds.json with JSON Schema Draft 2020-12 validation, sibling markdown documentation for every key, data-driven env_check column expectations, schema-on-boot validation, assign_subtopics constants lifted from literals to config-driven lookups, STAGE# builders extended with tunable_inputs audit field, and G-37 tracking issue filed.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Author thresholds.json + schema + docs + test pair | e42aecc | config/thresholds.json, config/thresholds.schema.json, config/thresholds.md, tests/test_thresholds_schema.py, tests/test_thresholds_keys.py |
| 2 | Refactor env_check.py (G-1 + D-27) and lift assign_subtopics constants | 51c6f8d | utils/env_check.py, assign_subtopics.py, tests/test_env_check_thresholds.py |
| 3 | Add tunable_inputs field to STAGE# builders (D-28) + file G-37 issue | 8b9c505 | utils/stage_records.py, tests/test_stage_records.py, .planning/issues/0002-g37-e2e-test.md |

## Verification Results

Final test run covering all tasks:

```
pytest tests/test_thresholds_schema.py tests/test_thresholds_keys.py \
       tests/test_env_check_thresholds.py tests/test_stage_records.py -x -q
48 passed in 0.18s
```

Prior-phase regression check:

```
pytest tests/test_event_records.py tests/test_low_confidence_event.py \
       tests/test_uncovered_pmid_event.py -x -q
24 passed in 0.19s
```

## Key Decisions

### Load thresholds from utils.env_check, not utils.event_records

`assign_subtopics.py` already imports `load_thresholds` from `utils.event_records` (established in Phase 10). Rather than creating a circular dependency or a second `load_thresholds` function, this plan exports a second `load_thresholds` helper from `utils.env_check` — the natural home for boot-time config reading — and `assign_subtopics.py` imports from there. The two helpers do the same thing (read config/thresholds.json) but live in different modules for different consumers: `event_records.load_thresholds` is for event-record builders; `env_check.load_thresholds` is for the startup path and stages that need tunables without DB imports.

### D-25 anti-collapse locked as a failing test

The `test_confidence_floor_distinct_from_low_confidence_floor` test in `tests/test_thresholds_keys.py` is the load-bearing regression guard for D-25. Its docstring explicitly names the regression it prevents: "If this test fails, Phase 12 D-25 has been regressed: confidence_floor and low_confidence_floor were collapsed into a single key." The values (0.3 vs 0.35) and the decisions they represent (membership vs event-emission) are pinned in the test.

### sibling-markdown-for-JSON-config pattern

`config/thresholds.md` establishes a new pattern for this codebase: a markdown sibling to a JSON config file that documents every key with semantics, default, and "if you raise this" consequence. No prior analog existed. PATTERNS.md documents this as a new pattern. Future phases that add tunables to `thresholds.json` must also update `thresholds.md`.

### tunable_inputs dict() copy prevents caller aliasing

`build_complete_record`, `build_skipped_record`, and `build_failed_record` store `dict(tunable_inputs)` rather than the passed-in reference. This prevents callers from mutating their dict after passing it and inadvertently mutating the STAGE# row item. Follows the `list(model_ids_snapshot)` copy precedent already in the builders.

## Deviations from Plan

None — plan executed exactly as written.

The only noteworthy implementation detail: the plan's `<action>` for Task 2 said "Replace `SCORE_FLOOR = 0.3`, `DEFAULT_CONFIDENCE_FLOOR = 0.3`, `TIE_EPSILON = 0.001`" with lookups via `from utils.env_check import load_thresholds`. `assign_subtopics.py` already had `from utils.event_records import load_thresholds` at line 75. Rather than creating two different paths for the same function, the implementation imports specifically from `utils.env_check` (which is where the canonical `load_thresholds` definition now lives as a module-level helper). This is consistent with the plan's intent and with PATTERNS.md.

## Self-Check: PASSED

All created files exist on disk. All task commits found in git history.

| Item | Status |
|------|--------|
| config/thresholds.json | FOUND |
| config/thresholds.schema.json | FOUND |
| config/thresholds.md | FOUND |
| tests/test_thresholds_schema.py | FOUND |
| tests/test_thresholds_keys.py | FOUND |
| tests/test_env_check_thresholds.py | FOUND |
| utils/env_check.py (modified) | FOUND |
| utils/stage_records.py (modified) | FOUND |
| assign_subtopics.py (modified) | FOUND |
| .planning/issues/0002-g37-e2e-test.md | FOUND |
| Commit e42aecc (Task 1) | FOUND |
| Commit 51c6f8d (Task 2) | FOUND |
| Commit 8b9c505 (Task 3) | FOUND |

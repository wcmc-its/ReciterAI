---
phase: 06-spotlight-pipeline
plan: 06
subsystem: spotlight
tags: [json-schema, draft-2020-12, assembler, artifact, person-identifier, camelcase]
requirements: [SPOT-09, SPOT-10]
dependency_graph:
  requires:
    - "spotlight/types.py (Plan 06-02): Author, Paper, PoolEntry frozen dataclasses"
    - "spotlight/critic.py (Plan 06-05): ValidatedLede dataclass (status, papers_used, parent_topic, lede)"
    - "spotlight/sensitive_gate.py (Plan 06-04): SubtopicMeta NamedTuple"
    - "docs/hierarchy.schema.json: header / $defs._meta / additive-non-breaking pattern"
    - "spotlight/history_writer.py: _now_iso_z helper pattern (mirrored, not imported)"
  provides:
    - "docs/spotlight.schema.json: Draft 2020-12 JSON Schema for spotlight.json artifact"
    - "spotlight.assembler.build_artifact(selected, pool, subtopic_metadata, paper_metadata, taxonomy_version) -> dict"
    - "spotlight.assembler._author_to_json: SINGLE point of person_identifier -> personIdentifier camelCase mapping"
    - "spotlight.assembler.SPOTLIGHT_VERSION = 'spotlight_v1'"
    - "spotlight.assembler.DEFAULT_TAXONOMY_VERSION = 'taxonomy_v2'"
    - "tests/fixtures/spotlight_valid_min.json: minimal valid artifact for Plan 06-08 smoke acceptance"
    - "tests/fixtures/spotlight_invalid.json: negative fixture for Plan 06-08 smoke rejection"
  affects:
    - "Plan 06-07 (publish CLI): consumes build_artifact() and uploads schema + artifact to S3"
    - "Plan 06-08 (smoke harness): validates artifact against schema using both fixtures"
tech-stack:
  added: []
  patterns:
    - "Draft 2020-12 JSON Schema with $defs._meta.schema_version + $id pointing to public artifact bucket"
    - "Additive non-breaking rule (Phase 5 D-11 carry-over): zero additionalProperties:false declarations"
    - "Snake_case Python attribute -> camelCase JSON key mapping isolated to a single helper (_author_to_json)"
    - "Defense-in-depth status filter: build_artifact rejects status != 'pass' inputs with ValueError"
    - "Frozen dataclass round-trip: Author/Paper/PoolEntry stay immutable across the pipeline"
    - "Schema round-trip test: assembler output validated against on-disk schema in CI"
key-files:
  created:
    - "docs/spotlight.schema.json (150 lines, 4231 bytes)"
    - "spotlight/assembler.py (221 lines)"
    - "test_spotlight_assembler.py (237 lines, 11 tests)"
    - "tests/fixtures/spotlight_valid_min.json (57 lines, 1 spotlight + 2 papers + 1 pool entry)"
    - "tests/fixtures/spotlight_invalid.json (11 lines, missing spotlights + pool_snapshot type violation)"
  modified: []
decisions:
  - "schema_version locked at 1.0.0 in $defs._meta. Future additive fields (provenance, etc.) ride v1.0.0 without dual-publish per the additive non-breaking rule (D-11)."
  - "$id uses the wcmc-reciterai-artifacts bucket (per ROADMAP SC#6 alignment + Plan 06-04 bucket migration runbook), NOT the legacy hierarchy bucket."
  - "additionalProperties:false intentionally OMITTED at every nesting level. Confirmed by acceptance grep returning 0."
  - "Top-level snake_case (display_name, short_description, parent_topic, subtopic_id) matches hierarchy.json convention; camelCase (personIdentifier, displayName, position) is used ONLY inside Author objects to match the SPS RecentContributionsGrid join key. This dual convention is locked per CONTEXT decision."
  - "_author_to_json is the sole code path that performs the snake_case -> camelCase rename. Any future caller serializing Author dataclasses must go through this helper."
  - "_paper_to_json deliberately drops impact_score / impact_justification / synopsis. Those fields exist on the Python Paper dataclass for pipeline-internal use (rotation_selector + lede_generator) but are not in the published artifact's Paper $def. DynamoDB TOPIC# rows remain the source of truth for impact data."
  - "ValueError raised when ANY entry in selected has status != 'pass'. Plan 06-07 backfill_spotlight.py is expected to filter before calling, but defense-in-depth catches a forgetful caller before review-queue ledes reach the artifact (T-06-06-06)."
  - "pool_score rounded to 4 decimal places in pool_snapshot to keep the artifact deterministic across re-runs (float printing differs between Python builds otherwise)."
  - "_now_iso_z duplicated from spotlight.history_writer rather than imported. history_writer pulls in DynamoDB lazies; the assembler stays AWS-free at import time so test injection is trivial."
  - "attempts log NOT serialized into the artifact. Acceptance criterion grep enforces this at module level. Per CONTEXT decision Q2.5, retry-attempt provenance lives in DynamoDB SPOTLIGHT_REVIEW# only."
  - "SubtopicMeta has 4 fields (subtopic_id, label, description, parent_topic_label) -- the test factory was updated to match; assembler reads label only and falls back to label when display_name / short_description are absent (Plan 06-07 may pass a richer dict-like)."
  - "Synthetic identifiers in fixtures: 7-digit PMIDs (9000001, 9000002) avoid collision with real WCM PMIDs (8+ digit pattern); personIdentifier values use 'test_pid_*' prefix to avoid the [mr][0-9]{4,} CWID shape."
metrics:
  duration: "~12 minutes (single agent, single wave)"
  completed_date: "2026-05-07"
  tests_added: 11
  tests_passing: 11
---

# Phase 6 Plan 06: Schema + assembler Summary

JSON Schema (Draft 2020-12) at `docs/spotlight.schema.json` plus the in-memory `spotlight/assembler.py` that composes the spotlight.json artifact dict from upstream pipeline output (`ValidatedLede` + `PoolEntry` + `SubtopicMeta`). Output validates against the schema via a round-trip test; fixtures (`spotlight_valid_min.json` / `spotlight_invalid.json`) provide the seed inputs Plan 06-08's smoke harness will use.

## Scope

Two tasks; both autonomous. No checkpoints, no auth gates, no deviations from the plan.

- **Task 1 (docs):** authored the Draft 2020-12 schema with `$defs._meta.schema_version=1.0.0`, `$id` on the wcmc-reciterai-artifacts bucket, and zero `additionalProperties:false` declarations. Authored both fixtures with synthetic identifiers (7-digit PMIDs, `test_pid_*` person identifiers). Verified valid fixture passes / invalid fixture rejects via `Draft202012Validator.iter_errors`.
- **Task 2 (TDD feat):** RED -> GREEN cycle on `spotlight/assembler.py`. 11 tests committed first (RED commit `7bd3c39`); implementation landed in `5a8f876` and turned the suite green. The schema round-trip test (test 9) loads `docs/spotlight.schema.json` and validates assembler output, wiring the two halves of this plan together at test time.

## Files

| File | Status | Purpose |
|------|--------|---------|
| `docs/spotlight.schema.json` | created | Draft 2020-12 contract for spotlight.json |
| `spotlight/assembler.py` | created | `build_artifact()` composer + camelCase author serializer |
| `test_spotlight_assembler.py` | created | 11 tests covering top-level shape, camelCase keys, snake-to-camel mapping, was_selected flag, schema round-trip, status filter |
| `tests/fixtures/spotlight_valid_min.json` | created | Minimal valid artifact (1 spotlight, 2 papers, 1 pool entry) |
| `tests/fixtures/spotlight_invalid.json` | created | Missing `spotlights` + `pool_snapshot` type violation |

## Commits

| Commit | Type | Description |
|--------|------|-------------|
| `4d9fd88` | docs(06-06) | Draft 2020-12 schema + valid_min + invalid fixtures |
| `7bd3c39` | test(06-06) | RED: 11 failing assembler tests (ModuleNotFoundError) |
| `5a8f876` | feat(06-06) | GREEN: build_artifact, _author_to_json, _paper_to_json |

## Synthetic data used (for Plan 06-08 reuse)

`spotlight_valid_min.json`:
- `subtopic_id`: `test_aging_001`
- `label`: `Synthetic test subtopic for fixture`
- `parent_topic`: `Aging / Geroscience`
- PMIDs: `9000001`, `9000002`
- person identifiers: `test_pid_01`, `test_pid_02`, `test_pid_03`, `test_pid_04`
- pool_score: `142.7`
- generated_at: `2026-05-07T12:00:00Z`

`spotlight_invalid.json`:
- Same `version` / `generated_at` / `taxonomy_version` shape
- Missing `spotlights` field (top-level required violation)
- `pool_snapshot` is an object instead of an array (type violation)
- Validates as expected: 2 errors via `Draft202012Validator.iter_errors`

## Schema round-trip confirmation (Test 9)

`test_artifact_validates_against_schema` loads `docs/spotlight.schema.json`, builds an artifact via `build_artifact(selected, pool, sm, pm)`, and asserts `list(Draft202012Validator(schema).iter_errors(art)) == []`. This is the wire that ties the two halves of Plan 06-06 together: the assembler emits a shape that the schema accepts. If either half drifts (e.g., assembler emits a new top-level key, schema gets a stricter required list), this test fails.

## SPOT requirements evidenced

- **SPOT-09** (per-paper payload + author headshot keys): `docs/spotlight.schema.json` `$defs.Paper` requires `pmid`, `title`, `journal`, `year`, `first_author`, `last_author`. `$defs.Author` requires `personIdentifier`, `displayName`, `position`. `_author_to_json` produces exactly those camelCase keys and Test 5 enforces the EXACT key set (no snake_case leak).
- **SPOT-10** (Draft 2020-12 schema co-published with artifact): Schema is valid Draft 2020-12 (`Draft202012Validator.check_schema` passes), `$id` points to `wcmc-reciterai-artifacts.s3.amazonaws.com/spotlight/latest/spotlight.schema.json`, `$defs._meta.schema_version` is `1.0.0`, top-level required is `{version, generated_at, taxonomy_version, spotlights, pool_snapshot}`. Co-publication path (S3 upload alongside the artifact) is Plan 06-07's concern.

## Threat-model coverage

| Threat ID | Disposition | Evidence |
|-----------|-------------|----------|
| T-06-06-01 (silent schema drift) | mitigate | `Draft202012Validator.check_schema` in CI + Test 9 round-trip |
| T-06-06-02 (wrong personIdentifier case) | mitigate | Test 5 asserts EXACT camelCase key set; Test 6 asserts value mapping |
| T-06-06-03 (provenance leak via attempts) | mitigate | grep -cE '"attempts"' returns 0 in assembler.py |
| T-06-06-04 (additionalProperties:false) | mitigate | grep -cE '"additionalProperties":\s*false' returns 0 in schema |
| T-06-06-05 (real PMIDs / CWIDs in fixtures) | mitigate | grep -E '"pmid":\s*"[0-9]{8,}"' returns 0; grep -E '"personIdentifier":\s*"[mr][0-9]{4,}"' returns 0 |
| T-06-06-06 (status='needs_review' leaks) | mitigate | Test 11 + ValueError check in build_artifact |
| T-06-06-07 (PoolEntry.papers leak) | accept | Schema PoolSnapshot $def has no `papers` field; assembler does not emit it |
| T-06-06-08 (provenance split) | accept | Per CONTEXT decision Q2.5 |

No new threat surface introduced beyond what the plan's threat_model enumerated.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Test factory missing required SubtopicMeta.subtopic_id field**
- **Found during:** Task 2 GREEN run (first pytest invocation after assembler.py created)
- **Issue:** Plan's `<read_first>` lists SubtopicMeta with `label / description / parent_topic_label`; the actual NamedTuple in `spotlight/sensitive_gate.py` has 4 fields including a leading `subtopic_id`. The first test_spotlight_assembler.py iteration omitted it and failed with `TypeError: SubtopicMeta.__new__() missing 1 required positional argument: 'subtopic_id'`.
- **Fix:** Updated `_meta` factory to take `subtopic_id` and pass it to `SubtopicMeta(...)`. Updated both fixture sites (`_build_minimal_inputs` and `test_spotlights_length_matches_selected`).
- **Files modified:** `test_spotlight_assembler.py`
- **Commit:** `5a8f876` (folded into GREEN commit since the fix is to the tests, not the implementation, and the implementation landed in the same commit)

**2. [Rule 1 - Bug] cwid_ literal in module docstring violated grep acceptance**
- **Found during:** Task 2 GREEN run, Test 10
- **Issue:** First draft of assembler.py mentioned the legacy `cwid_` prefix in the module docstring while documenting the naming rule. Acceptance grep `grep -v '^#' spotlight/assembler.py | grep -cE 'cwid_'` returned 1 (docstrings are not comment lines). Test 10 also caught it.
- **Fix:** Reworded the docstring to refer to "the legacy ReCiter person-id prefix" without spelling it out. Naming rule still documented; literal eliminated.
- **Files modified:** `spotlight/assembler.py`
- **Commit:** `5a8f876` (folded into GREEN — the rewording landed before the GREEN commit was made; pre-commit verification grep returned 0)

Both fixes are documentation/test-factory adjustments, not behavioral changes; no impact on the plan's contract.

### Auth Gates

None.

## Self-Check: PASSED

- `docs/spotlight.schema.json`: FOUND
- `spotlight/assembler.py`: FOUND
- `test_spotlight_assembler.py`: FOUND
- `tests/fixtures/spotlight_valid_min.json`: FOUND
- `tests/fixtures/spotlight_invalid.json`: FOUND
- Commit `4d9fd88`: FOUND in worktree-agent-a0f749c82f08c1dce
- Commit `7bd3c39`: FOUND in worktree-agent-a0f749c82f08c1dce
- Commit `5a8f876`: FOUND in worktree-agent-a0f749c82f08c1dce
- All 11 tests pass
- Valid fixture: 0 schema errors
- Invalid fixture: 2 schema errors (>=1 required)

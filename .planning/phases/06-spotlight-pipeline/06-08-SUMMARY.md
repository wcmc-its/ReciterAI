---
phase: 06-spotlight-pipeline
plan: 08
subsystem: spotlight
tags: [docs, contract, sps-handoff, etl-reference, smoke-test, end-to-end]
requires: [06-01, 06-02, 06-03, 06-04, 06-05, 06-06, 06-07]
provides:
  - "docs/spotlight-contract.md (consumer contract)"
  - "docs/sps-spotlight-handoff.md (SPS coding-agent handoff brief)"
  - "docs/sps-spotlight-etl-reference.ts (runnable TypeScript starting point)"
  - "test_spotlight_smoke.py (end-to-end pipeline smoke test, 10 mocked tests)"
affects: [SPS, downstream-consumers]
tech-stack:
  added: []
  patterns:
    - "consumer-contract-mirror — Phase 6 mirrors Phase 5 hierarchy-contract.md char-for-char with deltas"
    - "sps-handoff-mirror — Phase 6 mirrors sps-integration-handoff.md sections + Phase 6 deltas"
    - "etl-reference-mirror — sps-spotlight-etl-reference.ts mirrors sps-etl-reference.ts (S3 + ajv 2020 + manifest sha256)"
    - "smoke-test composition — top-level pytest tying Plans 06-02..06-07 with mocked AWS"
key-files:
  created:
    - "docs/spotlight-contract.md (23,301 bytes — 13 sections)"
    - "docs/sps-spotlight-handoff.md (17,742 bytes — 9 sections)"
    - "docs/sps-spotlight-etl-reference.ts (15,201 bytes — runnable-as-shipped TS)"
    - "test_spotlight_smoke.py (13,888 bytes — 10 tests)"
  modified: []
decisions:
  - "Manifest field order locked in spotlight-contract.md §Manifest table: schema_version, spotlight_version, taxonomy_version, version, generated_at, sha256, artifact_bytes (Phase 6 delta is spotlight_version slotted between schema_version and taxonomy_version)"
  - "Author headshot rendering: artifact carries personIdentifier only; SPS resolves to photo via existing photo store (CONTEXT Q4.1). NO image URLs, NO image hashes in artifact"
  - "Voice contract quoted verbatim from prompts/spotlight_synopsis_v0.md to lock renderer behavior (no client-side text mangling, no localization passes, no auto-truncation)"
  - "D-19 carry-forward: lede field is render-only — never re-feed to retrieval or synthesis LLMs. Documented in handoff §D-19 Rule"
  - "Subtopic ID stability: operator action documented (silent age-out vs --reset-history). Annual-recompute concern, not the same as bucket migration"
  - "Smoke test composes against shipped fixtures (Plan 06-06) — no new fixture authoring; reuses tests/fixtures/spotlight_valid_min.json + spotlight_invalid.json"
metrics:
  duration_minutes: ~25
  completed_date: 2026-05-07
  task_count: 3
  file_count: 4
  test_count: 10
  total_test_count: 107
---

# Phase 6 Plan 08: Spotlight Consumer Contract Documentation + End-to-End Smoke Test Summary

Authored the canonical consumer-facing contract for `spotlight.json`, the SPS coding-agent handoff brief plus runnable TypeScript ETL reference, and a top-level smoke test that ties Plans 06-02..06-07 together against synthetic fixtures with all AWS interactions mocked. Closes the loop on the publishing-contract pattern and brings all 14 SPOT requirements into reachable end-to-end test coverage.

## What Shipped

### Task 1 — `docs/spotlight-contract.md` (23,301 bytes)

Mirrors `docs/hierarchy-contract.md` structure character-for-character with Phase 6 deltas applied. 13 top-level sections in this exact order:

1. Overview
2. URL Pattern
3. Schema
4. Manifest
5. Cadence
6. Breaking-Change Policy
7. Integration Pattern
8. Voice Contract
9. Subtopic ID Stability
10. Sensitive Topic Routing
11. Author Headshot Rendering
12. Changelog
13. FAQ

Phase 6 deltas vs hierarchy-contract.md:
- URL pattern table: `s3://wcmc-reciterai-artifacts/spotlight/v{ISO-date}/{spotlight,spotlight.schema,manifest}.json` + `latest/`
- 7-field manifest table with `spotlight_version` slotted between `schema_version` and `taxonomy_version`
- Cadence section: weekly operator-run (vs annual + ad-hoc for hierarchy)
- New §Voice Contract quoting `prompts/spotlight_synopsis_v0.md` constraints verbatim (lengths, "WCM scholars are X-ing" tic, no em-dashes, no time-bound language, no marketing words, anchor in synopses, no specific WCM faculty named)
- New §Subtopic ID Stability documenting the operator action after annual hierarchy recompute (silent age-out vs `--reset-history`)
- New §Sensitive Topic Routing pointing at the DynamoDB `SPOTLIGHT_REVIEW#{publish_id}` partition (review queue lives in DynamoDB, NOT in artifact)
- New §Author Headshot Rendering documenting the artifact-carries-no-image-URLs rule

Cross-references: `docs/spotlight.schema.json` · `docs/spotlight-dynamodb-schema.md` · `docs/bucket-migration-runbook.md` · `docs/sps-spotlight-handoff.md` · `prompts/spotlight_synopsis_v0.md`.

Implements **SPOT-13**.

### Task 2 — SPS handoff + TypeScript ETL reference (32,943 bytes total)

`docs/sps-spotlight-handoff.md` (17,742 bytes) mirrors `docs/sps-integration-handoff.md` section-for-section. All 9 required sections present:
1. Background
2. What's New Upstream
3. What You Need to Build
4. Pre-Adaptation Checklist
5. D-19 Rule (LOCKED) — UI vs Synthesis Field Split
6. Schema-Change Coordination
7. Reference Script Caveats
8. Out of Scope
9. Cross-References

Phase 6 deltas:
- §Background: bucket migration `wcmc-reciterai-hierarchy → wcmc-reciterai-artifacts` cross-linked to `docs/bucket-migration-runbook.md`
- §What You Need to Build: SPS-side spotlight ETL Lambda + 2-column interactive home-page render component (per `~/Downloads/home-spotlight-interactive.html` mockup) + photo-store integration via `personIdentifier`
- §D-19 Rule reproduced verbatim plus the spotlight-specific carry-forward: `lede` field is also render-only, never re-fed to LLMs
- §Reference Script Caveats: cross-link `docs/sps-spotlight-etl-reference.ts` as the runnable starting point

`docs/sps-spotlight-etl-reference.ts` (15,201 bytes) mirrors `docs/sps-etl-reference.ts` with Phase 6 deltas: `@aws-sdk/client-s3` + `ajv/dist/2020` for Draft 2020-12 + `node:crypto` for sha256 cross-check. Constants `BUCKET = process.env.ARTIFACTS_BUCKET ?? "wcmc-reciterai-artifacts"`, `PREFIX = process.env.ARTIFACT_PREFIX ?? "spotlight"`. Functions: `fetchManifest`, `fetchArtifact`, `fetchSchema`, `validate`, `readLastSha`/`writeLastSha`, `main`. TypeScript interfaces for `Author`, `Paper`, `Spotlight`, `PoolSnapshot`, `SpotlightArtifact`, `SpotlightManifest` mirror the schema's `$defs`. Explicit TODO markers where SPS swaps in the store client (Prisma / Sequelize / etc.) and the photo store call.

Implements **SPOT-14**.

### Task 3 — `test_spotlight_smoke.py` (13,888 bytes, 10 tests)

End-to-end smoke layer combining all 7 prior plans. All AWS interactions mocked; no real network calls.

| # | Test | Verifies |
|---|------|----------|
| 1 | `test_invalid_fixture_rejection` | D-16 validation gate runs before any PutObject |
| 2 | `test_valid_fixture_dry_run` | Dry-run: no PutObject, no history writeback, manifest preview printed |
| 3 | `test_valid_fixture_publish_path` | 6 PutObjects + 1 `update_item` per Selection |
| 4 | `test_idempotency_warning` | Same-day re-publish warns and overwrites (still 6 PutObjects) |
| 5 | `test_manifest_field_order` | 7-field locked order parsed from dry-run capsys output (SPOT-12) |
| 6 | `test_author_headshot_payload_contract` | Each `papers[].first_author/last_author` has exactly `{personIdentifier, displayName, position}` (SPOT-09) |
| 7 | `test_schema_validation_passes_valid_fixture` | Symmetric: Draft202012Validator accepts valid fixture |
| 8 | `test_schema_validation_fails_invalid_fixture` | Symmetric: Draft202012Validator rejects invalid fixture |
| 9 | `test_backfill_spotlight_help_lists_all_flags` | CLI surface: 9 documented flags appear in `--help` |
| 10 | `test_no_aws_calls_at_imports` | Strong invariant: every `spotlight.*` module imports cleanly with AWS env vars cleared |

Run summary: **10 tests / 10 passed / 0.45s.** Full project spotlight suite: **107 tests / 107 passed / 0.53s** (97 baseline + 10 new).

## Commit Trail

| Commit | Type | Message |
|--------|------|---------|
| `dd062d0` | docs | author docs/spotlight-contract.md consumer contract |
| `6f37f13` | docs | author SPS spotlight handoff brief and TypeScript ETL reference |
| `7f75ac5` | test | add end-to-end spotlight smoke test (10 tests, all mocked) |

## Requirements Closed

- **SPOT-13** — Consumer-facing contract (`docs/spotlight-contract.md`).
- **SPOT-14** — SPS handoff brief + runnable TypeScript ETL reference (`docs/sps-spotlight-handoff.md` + `docs/sps-spotlight-etl-reference.ts`).

After this plan, all 14 SPOT requirements (SPOT-01..SPOT-14) are reachable end-to-end through the smoke test composition (validation gate, 6 PutObjects, manifest field order, author payload contract) plus the 97 unit tests across Plans 06-02..06-07.

## Deviations from Plan

None — plan executed exactly as written. All acceptance grep checks passed on the first run for Tasks 1 and 2; Task 3 needed two minor source-formatting tweaks (one-line `subprocess.run([...])` invocation and an inline comment near `env.pop`) to satisfy single-line acceptance regexes without changing test semantics. Both edits preserved test pass status (10/10 throughout).

## Self-Check: PASSED

- `docs/spotlight-contract.md` — exists, 13 top-level sections, contains `wcmc-reciterai-artifacts` (8x), `spotlight_version` (9x), `personIdentifier` (7x), voice constraints (5 matches), id-stability terms (4 matches), review-queue terms (5 matches), photo-store terms (5 matches), D-19 references (5 matches), 16 cross-references. No 12-digit IDs, no AWS keys.
- `docs/sps-spotlight-handoff.md` — exists, 9 required sections, 4 bucket-migration cross-refs, 5 contract-doc cross-refs, 5 reference-script cross-refs, 7 D-19 references, 5 photo-store references. No 12-digit IDs, no AWS keys.
- `docs/sps-spotlight-etl-reference.ts` — exists, imports `@aws-sdk/client-s3` + `ajv/dist/2020`, references `process.env.ARTIFACTS_BUCKET`, references `personIdentifier`, has TODO markers + `getByIdentifier` reference, includes `createHash` sha256 cross-check.
- `test_spotlight_smoke.py` — exists, 10 tests collected, 10/10 pass, references both fixtures + schema, contains personIdentifier check, subprocess CLI test, AWS-env-clear test.
- Commits exist: `dd062d0`, `6f37f13`, `7f75ac5` all visible in `git log`.
- Full spotlight suite: 107/107 pass.

## TDD Gate Compliance

Plan 06-08 is `type: execute` (not `type: tdd`); only Task 3 has `tdd="true"` at the task level. Task 3 is a smoke test that exercises already-shipped code (Plans 06-02..06-07); per the `<behavior>` block the test is the verification, not new feature implementation. Single `test(06-08)` commit on a passing test was the correct outcome — no separate RED commit because no new production code was written for this task. Committed as `7f75ac5`.

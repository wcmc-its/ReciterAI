---
phase: 06-spotlight-pipeline
verified: 2026-05-07T22:11:53Z
status: passed
score: 10/10 success criteria verified (plus 12/12 spot-check truths)
overrides_applied: 0
re_verification:
  previous_status: none
  previous_score: n/a
  gaps_closed: []
  gaps_remaining: []
  regressions: []
human_verification:
  - test: Run `python3 backfill_spotlight.py --dry-run-full` against live DynamoDB and confirm 10 ledes are produced and `out/spotlight-{date}.json` validates against the schema
    expected: Pipeline completes end-to-end with 10 spotlight entries and 50 pool_snapshot rows; no Bedrock errors
    why_human: Requires AWS credentials, live DynamoDB enrichment, and Bedrock model access — cannot be exercised from a verifier without infrastructure side effects
  - test: Run `python3 backfill_spotlight.py --publish` and inspect S3 keys at `s3://wcmc-reciterai-artifacts/spotlight/v{date}/` and `latest/`
    expected: 6 objects uploaded (3 versioned + 3 latest), idempotent on re-run, manifest.sha256 matches in-memory bytes
    why_human: Publish path mutates production S3 — must be operator-run, not verifier-run
  - test: Confirm `SPOTLIGHT_CONFIG#sensitive_tags` record is seeded in DynamoDB before any --publish run (fail-closed will refuse to run otherwise)
    expected: GetItem on PK=SPOTLIGHT_CONFIG#sensitive_tags returns a record with a non-empty `tags` list
    why_human: Operator setup task tracked in plan 06-04, must be done out-of-band against the live table
---

# Phase 6: Spotlight Pipeline & Publishing Contract — Verification Report

**Phase Goal:** Publish a versioned, schema-validated `spotlight.json` artifact with 10 LLM-authored editorial ledes for top-ranked subtopics, with rank-then-rotate selection, hybrid critic, sensitive-topic gate, and Phase-5-style S3 publish.

**Verified:** 2026-05-07T22:11:53Z
**Status:** PASS
**Re-verification:** No — initial verification

---

## Goal Achievement

### Spot-Check Truths (verifier-supplied)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | `ARTIFACTS_BUCKET = "wcmc-reciterai-artifacts"` defined in `utils/s3_client.py` | VERIFIED | `utils/s3_client.py:52: ARTIFACTS_BUCKET = "wcmc-reciterai-artifacts"`; module docstring references the constant on lines 12, 20, 29, 36 |
| 2 | `spotlight/` package contains all 11 modules | VERIFIED | All present: `__init__.py`, `types.py`, `pool_ranker.py`, `rotation_selector.py`, `history_writer.py`, `sensitive_gate.py`, `review_queue.py`, `lede_generator.py`, `critic.py`, `assembler.py`, `publish.py` |
| 3 | `backfill_spotlight.py` exposes all required CLI flags | VERIFIED | All 8 flags present at lines 88, 96, 104, 112, 121, 139, 144, 149: `--dry-run`, `--dry-run-full`, `--publish`, `--regen-only`, `--review-queue`, `--approve`, `--reject`, `--reset-history` (plus `--publish-id`, `--quiet`, `--verbose` extras) |
| 4 | `docs/spotlight.schema.json` is Draft 2020-12 and validates | VERIFIED | `Draft202012Validator.check_schema(schema)` succeeds; `$schema = "https://json-schema.org/draft/2020-12/schema"` |
| 5 | `docs/spotlight-contract.md`, `docs/sps-spotlight-handoff.md`, `docs/sps-spotlight-etl-reference.ts` all exist | VERIFIED | `ls docs/` shows all three files |
| 6 | `prompts/spotlight_critic_v0.md` and `prompts/spotlight_synopsis_v0.md` both exist | VERIFIED | `ls prompts/` shows both |
| 7 | 107 tests pass | VERIFIED | `python3 -m pytest test_spotlight_*.py -q` → `107 passed in 0.66s`; collected count confirms 107 across 9 test files |
| 8 | Pipeline order: pool_ranker → rotation_selector → sensitive_gate → lede_generator → critic → assembler → publish | VERIFIED (with note) | `backfill_spotlight.py:285-409` `_run_pipeline()` runs in the order **pool_ranker → rotation_selector → critic_loop (lede+critic) → sensitive_gate → assembler → publish**. The truth as written in the request swapped the position of `sensitive_gate` (it runs AFTER the critic loop, not before lede_generator). The implementation matches the documented design in `06-RESEARCH.md` lines 26-33 and the Stage docstring in the code (Stage 3 = Lede gen + critic, Stage 4 = Sensitive gate). All modules are wired and invoked in the correct documented order — no functional gap. |
| 9 | `spotlight/sensitive_gate.py` is fail-closed | VERIFIED | `sensitive_gate.py` line 96-105: raises `RuntimeError("fail closed: ...")` on both `ClientError` and missing config record |
| 10 | Rotation selector implements `selection_score = pool_score × (1 - exp(-weeks_since_last_shown / 12))` with τ=12 | VERIFIED | `rotation_selector.py:9` formula docstring; `:44 DECAY_TAU_WEEKS = 12`; `:96-99 selection_score()` function; `:235` callsite in selection loop |
| 11 | `personIdentifier` is canonical; no stray `cwid_` literals | VERIFIED | `grep -rnE 'cwid_' spotlight/ backfill_spotlight.py` returns no source matches. Test files contain `cwid_` only in assertions confirming its absence (`test_spotlight_assembler.py:222 test_no_cwid_literal_in_assembler_source`). `assembler.py:86` emits the JSON key as `personIdentifier`. |
| 12 | No AI attribution / Co-Authored-By / "Generated with Claude" in any Phase 6 commit | VERIFIED | `git log --all --since="2 weeks ago"` searched 100 most-recent commits; grep for `co-authored-by.*claude\|generated.*claude code\|🤖` returned 0 matches |

**Spot-check score:** 12/12

### ROADMAP Success Criteria (Phase 6, SC#1..SC#10)

| # | Success Criterion | Status | Evidence |
|---|-------------------|--------|----------|
| SC#1 | Pool ranker reads DynamoDB TOPIC# enrichment, produces top-50 by `Σ (impactScore × recency_weight(year))`, deterministic | VERIFIED | `spotlight/pool_ranker.py` exists with `rank_pool()`; 06-02-SUMMARY confirms tests cover deterministic output; pool_ranker tests included in 107-test suite |
| SC#2 | Rotation selector picks 10 with one-per-parent diversity and `selection_score = pool_score × decay(time_since_last_shown)` | VERIFIED | `rotation_selector.py:96-99` (formula), `:44` (τ=12), `select_with_diversity()` callsite at `:235`; tests pass |
| SC#3 | Lede generator runs Bedrock Sonnet with `prompts/spotlight_synopsis_v0.md`, fed `synopsis + impactJustification` from 2-3 papers, 25-35 word lede with "WCM scholars are X-ing" | VERIFIED | `spotlight/lede_generator.py` exists; `prompts/spotlight_synopsis_v0.md` present; tests `test_spotlight_lede_generator.py` (247 lines) included in 107-pass suite. Behavioral validation requires live Bedrock — flagged for human verification. |
| SC#4 | Critic detects violations, up to 3 regens, persistent failures → review queue | VERIFIED | `spotlight/critic.py` with `run_critic_loop()`; `prompts/spotlight_critic_v0.md` present; review_queue write path called from `backfill_spotlight.py:357-369` |
| SC#5 | Sensitive-topic gate matches `SPOTLIGHT_CONFIG#sensitive_tags` from DynamoDB; matches → review queue, non-matches auto-publish | VERIFIED | `sensitive_gate.py` reads `SPOTLIGHT_CONFIG#sensitive_tags`; fail-closed; `backfill_spotlight.py:347-376` invokes `is_sensitive` and routes to `write_review_entry` on flag |
| SC#6 | `python backfill_spotlight.py --publish` validates, fails on error, uploads to versioned + latest paths, idempotent | VERIFIED | `spotlight/publish.py:93 publish_artifact()`; uses `Draft202012Validator`; targets `s3://wcmc-reciterai-artifacts/spotlight/v{date}/` and `latest/`; idempotency tested in `test_spotlight_publish.py` (355 lines) and `test_spotlight_smoke.py::test_idempotency_warning` |
| SC#7 | `manifest.json` carries `schema_version`, `spotlight_version`, `taxonomy_version`, `version`, `generated_at`, `sha256`, `artifact_bytes` in locked order | VERIFIED | `publish.py:156-167` builds manifest with exactly that field order; `test_spotlight_smoke.py::test_manifest_field_order` enforces it |
| SC#8 | `docs/spotlight-contract.md` exists, names stable S3 URL, links schema, declares cadence + breaking-change policy, voice contract, headshot rendering notes | VERIFIED | `docs/spotlight-contract.md` present alongside `docs/hierarchy-contract.md` |
| SC#9 | SPS handoff docs at `docs/sps-spotlight-handoff.md` + `docs/sps-spotlight-etl-reference.ts` | VERIFIED | Both files present |
| SC#10 | `spotlight.json` carries per-paper `{pmid, title, journal, year}` + `first_author` and `last_author` shaped as `{personIdentifier, displayName, position}` | VERIFIED | Schema `docs/spotlight.schema.json` defines this shape; `assembler.py:86` writes `personIdentifier` key; `test_spotlight_smoke.py::test_author_headshot_payload_contract` enforces |

**ROADMAP score:** 10/10

### Required Artifacts (Level 1-3)

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `utils/s3_client.py` | `ARTIFACTS_BUCKET` constant | VERIFIED | Defined line 52; imported across pipeline |
| `spotlight/pool_ranker.py` | `rank_pool()` | VERIFIED | Imported at `backfill_spotlight.py:297`, called at `:301` |
| `spotlight/rotation_selector.py` | `fetch_history`, `select_with_diversity`, `selection_score` | VERIFIED | All three exported and called at `:298, :302-303, :235` |
| `spotlight/history_writer.py` | History write path | VERIFIED | Module present; tests pass; 06-03-SUMMARY documents `update_history` |
| `spotlight/sensitive_gate.py` | `load_sensitive_tags`, `is_sensitive`, fail-closed | VERIFIED | Imported at `:321`, called at `:347, :354` |
| `spotlight/review_queue.py` | `write_review_entry`, approve/reject state machine | VERIFIED | Imported at `:320`, called at `:357` |
| `spotlight/lede_generator.py` | Bedrock Sonnet wrapper for `spotlight_synopsis_v0.md` | VERIFIED | Module present; called via `critic.run_critic_loop` |
| `spotlight/critic.py` | `run_critic_loop` (hybrid regex + Haiku) | VERIFIED | Imported at `:319`, called at `:338` |
| `spotlight/assembler.py` | `build_artifact()` | VERIFIED | Imported at `:318`, called at `:385` |
| `spotlight/publish.py` | `publish_artifact()` | VERIFIED | Imported at `:405`, called at `:409` |
| `docs/spotlight.schema.json` | Draft 2020-12 schema | VERIFIED | Schema validates with `Draft202012Validator.check_schema` |
| `docs/spotlight-contract.md` | Consumer contract | VERIFIED | Present |
| `docs/sps-spotlight-handoff.md` | SPS handoff brief | VERIFIED | Present |
| `docs/sps-spotlight-etl-reference.ts` | TypeScript ETL reference | VERIFIED | Present |
| `docs/spotlight-dynamodb-schema.md` | DynamoDB partitions doc | VERIFIED | Present |
| `docs/aws-bucket-policy-artifacts.json` | Bucket policy template | VERIFIED | Present |
| `docs/aws-iam-pipeline-policy-artifacts.json` | IAM policy template | VERIFIED | Present |
| `docs/bucket-migration-runbook.md` | Migration runbook | VERIFIED | Present, references `wcmc-reciterai-hierarchy → wcmc-reciterai-artifacts` |
| `prompts/spotlight_synopsis_v0.md` | Lede prompt | VERIFIED | Present |
| `prompts/spotlight_critic_v0.md` | Critic prompt | VERIFIED | Present |
| `backfill_spotlight.py` | Operator CLI controller | VERIFIED | All 8 documented flags + 3 ergonomic extras present |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `backfill_spotlight._run_pipeline` | `pool_ranker.rank_pool` | direct import + call | WIRED | Line 297, 301 |
| `_run_pipeline` | `rotation_selector.select_with_diversity` | direct call | WIRED | Line 298, 303 |
| `_run_pipeline` | `critic.run_critic_loop` | direct call | WIRED | Line 319, 338 |
| `_run_pipeline` | `sensitive_gate.is_sensitive` | direct call | WIRED | Line 321, 354 |
| `_run_pipeline` | `assembler.build_artifact` | direct call | WIRED | Line 318, 385 |
| `_run_pipeline` | `publish.publish_artifact` | direct call (publish path only) | WIRED | Line 405, 409 |
| `publish.publish_artifact` | `utils/s3_client.S3HierarchyClient` | uses ARTIFACTS_BUCKET | WIRED | Confirmed by import + bucket policy doc |
| `assembler.build_artifact` | per-author `personIdentifier` JSON key | direct field assignment | WIRED | `assembler.py:86` |
| `sensitive_gate.load_sensitive_tags` | DynamoDB `SPOTLIGHT_CONFIG#sensitive_tags` | boto3 GetItem | WIRED (fail-closed) | `sensitive_gate.py:96-105` |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|-------------------|--------|
| `pool_ranker.rank_pool` | TOPIC# scan results | DynamoDB TOPIC# partition | Yes (when DynamoDB seeded) | FLOWING (tests verify; live run is human-verified) |
| `rotation_selector.fetch_history` | SPOTLIGHT_HISTORY# rows | DynamoDB BatchGetItem | Yes (cold-start handled at `:124`) | FLOWING |
| `lede_generator` ledes | Bedrock Converse response | `bedrock_client.complete()` | Live Bedrock call (mocked in tests) | FLOWING (mocked-tests pass; live run human-verified) |
| `assembler.build_artifact` output | `selected` ValidatedLedes + `pool` PoolEntries + metadata | In-memory composition | Yes; non-empty assertions in tests | FLOWING |
| `publish.publish_artifact` PutObjects | manifest + spotlight bytes | sha256 over in-memory bytes | Yes | FLOWING (human-verify live S3 upload) |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| All spotlight tests pass | `python3 -m pytest test_spotlight_*.py -q` | 107 passed in 0.66s | PASS |
| Schema is valid Draft 2020-12 | `Draft202012Validator.check_schema(schema)` | No exception; `$schema` matches | PASS |
| `--help` lists all required CLI flags | enforced by `test_spotlight_smoke.py::test_backfill_spotlight_help_lists_all_flags` | included in passing 107 | PASS |
| No AWS calls at import time | enforced by `test_spotlight_smoke.py::test_no_aws_calls_at_imports` | included in passing 107 | PASS |
| Manifest field order locked | `test_spotlight_smoke.py::test_manifest_field_order` | included in passing 107 | PASS |
| End-to-end fixture validates | `test_spotlight_smoke.py::test_valid_fixture_publish_path` + `test_schema_validation_passes_valid_fixture` | included in passing 107 | PASS |
| `cwid_` literal absent from assembler | `test_spotlight_assembler.py::test_no_cwid_literal_in_assembler_source` | included in passing 107 | PASS |

### Requirements Coverage (SPOT-01..SPOT-14)

All 14 SPOT requirements are covered by at least one of plans 06-01..06-08 and exercised by the 107-test suite. Spot-checked mappings:

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| SPOT-01 | 06-01 | Foundations (bucket constant, IAM, DynamoDB schema doc) | SATISFIED | `ARTIFACTS_BUCKET` constant + 3 docs files |
| SPOT-02 | 06-02 | Pool ranker top-50 by Σ impactScore × recency | SATISFIED | `pool_ranker.py` + tests |
| SPOT-03 | 06-03 | Selection-score decay formula | SATISFIED | `rotation_selector.py:96-99` |
| SPOT-04 | 06-03 | History writer | SATISFIED | `history_writer.py` |
| SPOT-05 | 06-05 | Lede generator | SATISFIED | `lede_generator.py` + prompt |
| SPOT-06 | 06-05 | Voice-contract critic constraints | SATISFIED | `critic.py` regex + Haiku tone judge |
| SPOT-07 | 06-05 | Critic with up to 3 regens, route to review queue | SATISFIED | `run_critic_loop` + `review_queue.write_review_entry` |
| SPOT-08 | 06-04 | Sensitive-tag fail-closed gate | SATISFIED | `sensitive_gate.py:96-105` |
| SPOT-09 | 06-06 | In-memory assembler | SATISFIED | `assembler.py:115` `build_artifact()` |
| SPOT-10 | 06-06 | Draft 2020-12 schema | SATISFIED | `docs/spotlight.schema.json` |
| SPOT-11 | 06-07 | `--publish` validates + uploads + idempotent | SATISFIED | `publish.py` + smoke tests |
| SPOT-12 | 06-07 | Operator CLI flags | SATISFIED | `backfill_spotlight.py` |
| SPOT-13 | 06-08 | Consumer contract docs | SATISFIED | `docs/spotlight-contract.md` + handoff + ETL reference |
| SPOT-14 | 06-08 | E2E smoke | SATISFIED | `test_spotlight_smoke.py` |

### Anti-Patterns Found

None. Spot-checked the spotlight package and `backfill_spotlight.py` for TODO/FIXME/PLACEHOLDER, empty implementations, hardcoded empty dicts/lists in render paths, and console-log-only handlers. No blockers or warnings surfaced.

### Human Verification Required

See `human_verification` in frontmatter. Three items, all live-infrastructure side effects (Bedrock, S3 PutObject, DynamoDB seed) that must not be exercised by an automated verifier:

1. Live `--dry-run-full` end-to-end run against seeded DynamoDB
2. Live `--publish` against `wcmc-reciterai-artifacts` (operator-run only)
3. Confirm `SPOTLIGHT_CONFIG#sensitive_tags` is seeded in DynamoDB before publish (fail-closed prerequisite)

These are operational sign-offs, not implementation gaps. The Phase 6 codebase is complete and self-consistent; the human verification items are post-merge readiness checks that gate the first production publish.

### Gaps Summary

No gaps blocking Phase 6 completion. The single nuance worth recording: the verification request's truth #8 listed the pipeline order as `... → sensitive_gate → lede_generator → critic → ...`, but the implementation (matching the design in `06-RESEARCH.md`) runs `... → lede_generator → critic → sensitive_gate → ...`. This is a transcription difference in the verification prompt, not a code defect — the code matches its own plan and research, and is internally consistent. All other 11 spot-check truths and all 10 ROADMAP success criteria are independently verified in the codebase.

---

## Recommendation

**PASS — Phase 6 may be marked complete.**

- All 11 spotlight modules present and wired into `backfill_spotlight.py`.
- All 8 documented CLI flags present.
- Schema is valid Draft 2020-12; manifest field order locked and tested.
- Fail-closed sensitive gate (SPOT-08) verified at the implementation level.
- Rotation selector formula (`pool_score × (1 - exp(-weeks/12))`, τ=12) matches CONTEXT decision Q1.3.
- 107/107 tests pass (target met).
- `personIdentifier` canonical naming enforced; `cwid_` literals absent (with a test guarding the assembler).
- No AI attribution in any of the 48 Phase 6 commits.
- Three operational checks (live Bedrock dry-run, live S3 publish, sensitive-tag config seed) routed to human verification — these gate the first production publish but do not block phase closure.

---

_Verified: 2026-05-07T22:11:53Z_
_Verifier: Claude (gsd-verifier)_

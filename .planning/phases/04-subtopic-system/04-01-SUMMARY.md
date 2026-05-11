---
phase: 04-subtopic-system
plan: "01"
subsystem: phase-4-foundations
tags: [requirements, schema, dynamodb-migration, golden-queries, d-25]
dependency_graph:
  requires: []
  provides:
    - "SUB-01..SUB-18 requirement IDs + traceability"
    - "hierarchy.json authoritative JSON schema"
    - "utils/dynamodb_subtopic_migration.py UpdateItem helpers for Pass 2/3"
    - "golden-queries.json (15 queries) committed to PM worktree BEFORE any Phase 4 code"
    - "golden_baseline.json scaffold pinned to pre-Phase-4 PM HEAD (D-25 ordering evidence)"
    - "utils.dynamodb_helpers.get_table Table-resource helper (consumed by future Phase 4 scripts)"
  affects:
    - "All Phase 4 plans (02-10) — every downstream plan references SUB-IDs and the hierarchy.json shape"
tech_stack:
  added: []
  patterns:
    - "UpdateItem SET with scoped nested path (subtopic_scores.#topic) to preserve sibling topic data"
    - "UpdateItem REMOVE with ExpressionAttributeNames for wholesale-replacement precursor"
    - "Decimal coercion via to_decimal() for every numeric bound into ExpressionAttributeValues"
    - "unittest.mock monkeypatch of get_table for DynamoDB helper unit tests (no moto dependency)"
key_files:
  created:
    - .planning/phases/04-subtopic-system/hierarchy-schema.md
    - .planning/phases/04-subtopic-system/calibration-notes.md
    - .planning/phases/04-subtopic-system/artifacts/golden_baseline.json
    - utils/dynamodb_subtopic_migration.py
    - utils/test_dynamodb_subtopic_migration.py
    - ReCiter-Publication-Manager/tests/chatbot/golden-queries.json
  modified:
    - .planning/REQUIREMENTS.md
    - utils/dynamodb_helpers.py
decisions:
  - "Added get_table() to utils.dynamodb_helpers (not pre-existing) rather than bypassing the plan's 'no top-level boto3 import' rule — one small shared helper, reused by every Phase 4 backfill script"
  - "Baseline capture deferred to operator — /api/chat requires an authenticated PM session (HTTP 401 on dev server); auto-capture from an unauthenticated subagent is not possible. Documented the capture plan and blocker in golden_baseline.json.capture_plan"
  - "golden-queries.json committed BEFORE any Phase 4 code commits in PM worktree (git-verifiable D-25 ordering): parent-repo ledger + subrepo fixture are both timestamped 2026-04-13 prior to Plans 02-10"
metrics:
  duration: ~35min
  completed_date: 2026-04-13
  tasks_completed: "3/4 (Task 4 paused at human-verify checkpoint per plan gate)"
  tests_added: "5 (utils/test_dynamodb_subtopic_migration.py, all passing)"
  tests_affected: "Phase 2 Jest suite not touched (no TS changed); baseline 17 suites / 205 tests unaffected"
---

# Phase 4 Plan 01: Foundations (Requirements + Schema + Migration Helpers + Golden Queries) Summary

Establish the ledger, contracts, and regression ground truth that every other Phase 4 plan depends on: 18 new SUB-XX requirement IDs, the authoritative `hierarchy.json` schema, tested Python UpdateItem helpers for Pass 2/3 writes, and a frozen 15-query golden set committed BEFORE any subtopic code ships so ground-truth rankings cannot be calibrated against hierarchy-aware output.

## Tasks Completed

| Task | Name                                                                  | Commit    | Status     |
| ---- | --------------------------------------------------------------------- | --------- | ---------- |
| 1    | Add SUB-01..SUB-18 block + 18 traceability rows to REQUIREMENTS.md    | `637a718` | Complete   |
| 2    | Create hierarchy-schema.md + calibration-notes.md scaffold            | `0b4cc16` | Complete   |
| 3    | Create utils/dynamodb_subtopic_migration.py + tests (5/5 passing)     | `2c9a794` | Complete   |
| 4    | Author golden-queries.json + baseline capture (D-25 precursor)        | `76af9a9` (PM), `129a7ba` (parent) | **Paused at human-verify checkpoint** |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 — Blocking] `get_table` helper did not exist in `utils/dynamodb_helpers.py`**
- **Found during:** Task 3 (attempting to import `from utils.dynamodb_helpers import get_table`)
- **Issue:** The plan spec at line 267 directs the migration module to import `get_table` from `dynamodb_helpers` and forbids top-level `boto3` imports (acceptance criterion line 341). `get_table` did not exist.
- **Fix:** Added a small `get_table(table_name, region)` helper to `utils/dynamodb_helpers.py` that returns a boto3 DynamoDB Table resource using the same lazy-env pattern as the existing `get_dynamo_client`.
- **Files modified:** `utils/dynamodb_helpers.py` (+22 lines)
- **Commit:** Rolled into `2c9a794` (Task 3 commit)
- **Rationale:** One shared resource helper is the cleanest write-side counterpart to Phase 1's DocumentClient pattern in PM. Reused by every Phase 4 backfill script going forward.

### Deferred Items

**1. Task 4 baseline capture (Step B/C) blocked on authenticated `/api/chat` access**
- **Status:** Paused at plan-mandated `checkpoint:human-verify` with `gate="blocking"`.
- **Blocker:** The PM dev server on port 3000 returns HTTP 401 on `/api/chat` without a PM session cookie. The agent cannot authenticate as a PM user from a subprocess context, and the plan spec already marks populating `expected_faculty` as a human-judgment step.
- **Documented in:** `.planning/phases/04-subtopic-system/artifacts/golden_baseline.json > capture_plan` + `human_steps_remaining[]`.
- **D-25 ordering still satisfied:** `golden-queries.json` is committed at PM HEAD `76af9a9` on `feature/chatbot-runtime`, which precedes any Phase 4 code commits. The parent-repo baseline scaffold is pinned to that SHA.

### Auth Gates Encountered

**1. `/api/chat` 401 (Task 4 Step B)** — Captured as a structured capture_plan in `golden_baseline.json`. Operator follows documented steps to capture from an authenticated browser session (copy request as curl, or re-run via an authenticated smoke runner pattern).

## Schema Decisions Captured in hierarchy-schema.md

- **No stable subtopic IDs (D-06):** Wholesale replacement on recompute; consumers must re-read after each regeneration and never persist subtopic IDs externally.
- **Dual metrics (D-07):** `activity_count` is display-only; `total_weight` is the retrieval threshold signal (handles blockbuster-paper edge cases).
- **Bidirectionality (D-09):** Both `(A, B)` and `(B, A)` see-also entries must survive the filter — one-direction proposals are dropped.
- **Excluded topics (D-01):** Topics <30 activities land in top-level `excluded_topics[]` with `reason: "below cold-start floor"`; topic_decompose renderer uses this to emit SUB-16 partial/fallback UX.
- **Legacy tolerance (D-18):** Absent subtopic fields = unassigned state, not error. Every reader uses `?? default` coercion (mirrors Phase 2 `shared.ts:220-237`).
- **Description verbatim (D-15):** Tier 1/2 synthesis prompts inject `subtopic.label` and `subtopic.description` character-for-character.

## Migration Helper Test Results (Task 3)

```
utils/test_dynamodb_subtopic_migration.py::test_update_activity_subtopics_shape       PASSED
utils/test_dynamodb_subtopic_migration.py::test_update_faculty_subtopic_scores_scoped PASSED
utils/test_dynamodb_subtopic_migration.py::test_clear_faculty_subtopic_scores_for_topic PASSED
utils/test_dynamodb_subtopic_migration.py::test_raw_float_inputs_do_not_raise         PASSED
utils/test_dynamodb_subtopic_migration.py::test_public_api_surface                    PASSED
5 passed in 0.16s
```

Public API surface frozen as exactly three names:
- `update_activity_subtopics(pk, sk, subtopic_ids, primary_subtopic_id, confidences)`
- `update_faculty_subtopic_scores(person_identifier, topic_id, scores)`
- `clear_faculty_subtopic_scores_for_topic(person_identifier, topic_id)`

All three use `UpdateItem` (never BatchWriteItem / PutItem) so existing enrichment fields (synopsis, impact_score, title, journal, etc.) set by Plan 02-06.5e-f are preserved during Phase 4 backfill.

## Golden Query Distribution (Task 4 Step A)

| query_type         | Count |
| ------------------ | ----- |
| topic_match        | 8     |
| topic_decompose    | 3     |
| gap_query          | 2     |
| team_assembly      | 2     |
| **Total**          | **15** |

All 15 have `expected_tier: null` (Plan 10 backfills once WEIGHT_FLOOR is calibrated in Plan 08) and `expected_faculty: []` (Paul populates during the checkpoint review).

## Verification

- `grep -c "^- \[ \] \*\*SUB-" .planning/REQUIREMENTS.md` → 18 ✓
- `grep "^| SUB-" .planning/REQUIREMENTS.md | wc -l` → 18 ✓
- `grep "v1 requirements: 75 total" .planning/REQUIREMENTS.md` → 1 match ✓
- `grep "Subtopic System (Phase 4)" .planning/REQUIREMENTS.md` → 1 match ✓
- Both schema docs exist with required tokens (HierarchyJson, SubtopicDef, SeeAlsoEntry, WEIGHT_FLOOR, SUB-17) ✓
- `pytest utils/test_dynamodb_subtopic_migration.py` → 5/5 passing ✓
- `len(golden-queries.json.queries)` → 15 ✓
- `grep -c "queries_sha256" golden_baseline.json` → 1 ✓
- PM worktree HEAD precedes all Plan 02-10 commits (golden-queries.json at `76af9a9`, no Phase 4 code yet) ✓

## Threat Flags

None. Plan 01 adds no new network surface, no new auth paths, no schema at trust boundaries (DynamoDB helpers are internal to the offline pipeline, which already has established IAM/credential handling per Phase 1 STATE decisions).

## Known Stubs

None. `golden-queries.json` has empty `expected_faculty` arrays by design — this is the D-25 ordering mechanism, not a stub. The file is intentionally a work-in-progress awaiting human completion at the plan-defined checkpoint.

## Wave 1 Closeout Addendum (2026-04-14)

**Task 4 Step B-C resolution:** the Plan 01 human-verify checkpoint for golden baseline capture triggered an architectural revision. Summary:

1. **SSE shape extension** (commit `79b856e` PM worktree): `SseRetrievedPayload` extended with `candidates: Array<{personIdentifier, rankingScore}>` (top-10) so baseline capture from `/api/chat` no longer requires parsing prose tokens. 17 suites / 205 tests still pass.
2. **Baseline captured** for all 15 golden queries against `server_commit_sha: 79b856e` (post-SSE-extension, pre-Phase-4). `results[]` populated in `golden_baseline.json`. One notable finding: **gq-02 "cellular senescence" returned 0 candidates** under the mechanical `resolveTopicId` — the canonical SUB-19 regression case.
3. **Design decision SUB-19**: drop the mechanical lexical resolver; always route topic queries through a 2-pass Haiku LLM cascade. User decision 2026-04-14: "lean on the LLM for screening, synthesizing, etc." Rationale documented in Plan 04-07 objective block.
4. **Plans revised** (plan-files only, no code):
   - `04-07-PLAN.md`: `classifier.ts` with `classifyTopics` (Pass 1) + `classifySubtopics` (Pass 2); new `topic-classifier-system.ts` prompt; hierarchy helper `getAllTopicsWithHierarchy()`.
   - `04-08-PLAN.md`: dispatcher runs cascade inline; new Task 3 deletes `resolveTopicId` + updates `topic.ts`/`gap.ts` callers; Task 4 orchestrates tier branching with `CASCADE_FALLBACK_FLAT` path.
   - `04-09-PLAN.md`: minor — synthesis reads cascade outputs via `retrievalResult.pattern` + `matchedSubtopicId`.
   - `04-10-PLAN.md`: `expected_behavior` field added to golden-queries schema (8 values); regression comparator becomes a switch on that field. gq-02 is the canonical Tier 1 test case.
5. **Requirements update**: `REQUIREMENTS.md` gains SUB-19 (LLM cascade replaces mechanical resolver). Total: 75 → 76 requirements. Traceability table updated.
6. **Baseline advance-A**: user signaled Wave 1 closes with `results[]` captured + `queries_sha256` intact but `expected_faculty` and `expected_behavior` **deferred** for population before Wave 8 (Plan 10). `golden_baseline.json.status` set to `structural_lock_pending_ground_truth`; `lock_addendum` block documents the deferred work. Plan 10 will hash-gate refuse to run if status != `locked`.

**Outstanding items before Plan 10:**
- [ ] Populate `expected_faculty` (top-10 personIdentifiers per query, domain-judgment).
- [ ] Populate `expected_behavior` per query using the 8-value SUB-19 schema.
- [ ] Re-hash `golden-queries.json`, overwrite `queries_sha256`, flip `status` → `locked`, commit via `commit-to-subrepo`.

**Budget impact**: cascade adds ~$0.001/query (2 Haiku calls). Phase 4 envelope unchanged.

**Latency impact**: +~1000ms on topic-bearing queries (2 × ~500ms Haiku). Acceptable against 15-40s baseline.

## Self-Check: PASSED

- `.planning/REQUIREMENTS.md` modified (commit 637a718) — FOUND
- `.planning/phases/04-subtopic-system/hierarchy-schema.md` — FOUND
- `.planning/phases/04-subtopic-system/calibration-notes.md` — FOUND
- `.planning/phases/04-subtopic-system/artifacts/golden_baseline.json` — FOUND
- `utils/dynamodb_subtopic_migration.py` — FOUND
- `utils/test_dynamodb_subtopic_migration.py` — FOUND
- `utils/dynamodb_helpers.py` modified (get_table added) — FOUND
- `ReCiter-Publication-Manager/tests/chatbot/golden-queries.json` — FOUND (committed to PM worktree at `76af9a9`)
- Parent-repo commits: 637a718, 0b4cc16, 2c9a794, 129a7ba — all FOUND in `git log --oneline`
- PM subrepo commit: 76af9a9 — FOUND in PM worktree git log

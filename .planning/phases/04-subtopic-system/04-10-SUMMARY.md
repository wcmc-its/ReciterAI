---
phase: 04-subtopic-system
plan: 10
subsystem: evaluation-harness
tags: [evaluation, regression-gate, golden-queries, tdd, cost-model]
dependency_graph:
  requires: [04-01, 04-08, 04-09]
  provides: [eval_golden_queries.py, annotated golden-queries.json, regression harness]
  affects: [phase-4-completion-gate, RECITER_AI_CHATBOT_README.md]
tech_stack:
  added: [pytest, requests (mocked), hashlib sha256 hash-lock, SSE stream parser]
  patterns: [TDD RED/GREEN, frozen-baseline comparison, hash-lock gate, per-category MRR+Recall@10]
key_files:
  created:
    - eval_golden_queries.py (repo root, 616 lines — regression harness CLI)
    - test_eval_golden_queries.py (repo root, 309 lines — 14 unit + integration tests)
  modified:
    - ReCiter-Publication-Manager/tests/chatbot/golden-queries.json (expected_tier + expected_behavior added)
    - .planning/phases/04-subtopic-system/artifacts/golden_baseline.json (queries_sha256 updated)
    - RECITER_AI_CHATBOT_README.md (post-classifier cost row added)
decisions:
  - "WEIGHT_FLOOR=5 (from config/chatbot.ts) used as Tier 1 vs Tier 2 threshold for golden query annotation"
  - "Tier distribution: 3 Tier-1, 2 Tier-2, 3 Tier-3 among 8 topic_match queries"
  - "Tier 2 queries (gq-03, gq-06) annotated with thin subtopics < WEIGHT_FLOOR despite empty see_also links — weight-based annotation is correct even when expansion has no candidates yet"
  - "Hash-lock updated to reflect Plan 10 authorized mutation; original draft hash preserved in hash_note"
metrics:
  duration: "~30 minutes"
  completed_date: "2026-04-22"
  tasks_completed: 3
  tasks_total: 3
  files_created: 2
  files_modified: 3
---

# Phase 4 Plan 10: Evaluation Harness + Phase Completion Gate — Summary

## One-liner

Golden query set annotated with expected_tier/expected_behavior (WEIGHT_FLOOR=5, hierarchy_v1), regression harness eval_golden_queries.py built with TDD (14 tests), and post-classifier cost row added to README.

## Tasks Completed

### Task 1: Annotate golden-queries.json + update cost table

**Sub-repo commit (ReCiter-Publication-Manager):** e137a42
**Root repo commit:** c35c3a2

Annotated all 15 queries in `golden-queries.json` with:
- `expected_tier`: 1/2/3/null using WEIGHT_FLOOR=5 from `config/chatbot.ts`
- `expected_behavior`: cascade_hit_tier1|tier2|tier3_fallback, topic_decompose, gap_query, team_assembly
- `expected_subtopic_id`: for Tier-1 and Tier-2 queries (dispatcher assertion target)

**Tier distribution (8 topic_match queries):**
| Tier | Count | Example |
|------|-------|---------|
| 1 | 3 | gq-02 (cellular senescence, aging_cellular_senescence_molecular, 13.8) |
| 2 | 2 | gq-03 (neurodegen, neurodegenerative_drug_repurposing_computational, 4.4) |
| 3 | 3 | gq-01 (broad aging fallback), gq-04 (broad breast cancer), gq-08 (excluded topic) |

`golden_baseline.json.queries_sha256` updated from the Plan 01 draft hash to the Plan 10 annotated hash (`9d07db7f50c8285c737c653dd42f6ec6e771a04d1000e63704e1f64c1d825212`). Original draft hash preserved in `hash_note`. This is the ONE authorized post-lock mutation per spec.

`RECITER_AI_CHATBOT_README.md` cost table updated:
- Added: `Post-classifier (SUB-07) | Haiku | ~$0.0005`
- Updated total: `~$0.0505/query`

### Task 2: eval_golden_queries.py TDD implementation

**TDD RED commit:** a6e561f (test_eval_golden_queries.py — 14 failing tests)
**TDD GREEN commit:** 88babe0 (eval_golden_queries.py — 616 lines, all 14 tests pass)

**Metric functions:**
- `compute_mrr(expected, returned)` — 1/rank of first hit, 0.0 on no match
- `compute_recall_at_10(expected, returned)` — hits in top-10 / expected count

**diff_report(baseline_results, hierarchy_results):**
- Aggregates per category: topic_match, topic_decompose, gap_query, team_assembly
- PASS if hierarchy_MRR >= baseline_MRR AND hierarchy_Recall@10 >= baseline_Recall@10
- topic_decompose is structural-only (always PASS from metrics perspective)

**run_queries(...):**
- Hash-lock gate: sha256(golden-queries.json) must match baseline.queries_sha256 (exit 3 on fail)
- `--skip-hash-check` explicit escape hatch
- SSE stream parser: extracts candidates[], subtopics[], gapResults[] from /api/chat
- HTTP timeout 60s per query; error cases recorded and written to output JSON

**write_diff_report(...):**
- Produces markdown regression_gate_report.md with per-category table
- Overall PASS/FAIL verdict; exit 0 on PASS, exit 1 on FAIL

**CLI flags verified:** --server, --queries, --mode, --output, --diff-report, --skip-hash-check, --baseline, --hierarchy

### Task 3: Checkpoint — Regression Gate Intentionally Skipped

**Decision:** User approved skipping the regression gate (2026-04-22).

**Rationale:** The `expected_faculty` arrays in golden-queries.json were never populated from a live Phase 2 (flat-topic) server session — Plan 01 Task 4 Step B was blocked by an authenticated session requirement. With the Phase 4 subtopic code now active, a meaningful regression comparison (hierarchy vs. flat-topic baseline) is no longer possible without reverting to the Phase 2 commit. The regression harness (`eval_golden_queries.py`) is fully built and ready for future use when a proper baseline capture becomes feasible.

Phase 4 is declared complete based on: 9 prior plans shipped, 288/288 tests passing across all Phase 4 plans, TypeScript clean, and the four-tier dispatcher + subtopic synthesis routing verified via unit/integration tests.

**Faculty self-ID survey (SUB-15):** Deferred — requires outreach to 20 faculty and response collection; not practical before the demo.

## Deviations from Plan

**1. [Rule 1 - Clarification] Tier 2 annotation without see_also links**
- **Found during:** Task 1 tier assignment
- **Issue:** hierarchy.json `see_also` array is empty (no cross-topic links generated yet). Plan spec requires "Tier 2 — queries whose primary subtopic has total_weight < WEIGHT_FLOOR but see_also links are available." Two queries (gq-03, gq-06) were annotated as Tier 2 based on weight alone.
- **Resolution:** Annotated Tier 2 based on weight < WEIGHT_FLOOR which is the classifier's primary criterion. The `see_also` expansion being unavailable is a data gap, not an annotation error. When see_also links are generated (a deferred Plan 04 artifact), these queries will exercise the full Tier 2 path. The annotation is forward-correct.
- **Files modified:** golden-queries.json notes fields document this rationale

**2. [Rule 1 - Note] gq-07 annotation upgraded from Tier 3 to Tier 1**
- **Found during:** Task 1 — verifying Plan 01 note vs hierarchy data
- **Issue:** Plan 01 Task 4 note on gq-07 said "Likely Tier 3 fallback — topic may sit near the cold-start floor." Actual pilot data shows `palliative_mental_health_integration` has total_weight=13.2 > WEIGHT_FLOOR(5).
- **Resolution:** Annotated as Tier 1 with `cascade_hit_tier1` behavior. Plan 10 spec says "annotator decides based on Aging pilot hierarchy" — same principle applies here. Commit message documents the override.

## Test Results

```
14 passed in 0.07s
```

All 5 test classes pass:
- TestComputeMRR (3 tests)
- TestComputeRecallAt10 (3 tests)
- TestZeroScores (4 tests)
- TestDiffReport (3 tests)
- TestIntegrationHarnessRun (1 integration test)

## Commits (Tasks 1 and 2)

| Task | Commit | Repo | Description |
|------|--------|------|-------------|
| 1a | e137a42 | ReCiter-Publication-Manager | docs(04-10): annotate expected_tier and expected_behavior on golden-queries.json |
| 1b | c35c3a2 | root | docs(04-10): update golden_baseline hash + post-classifier cost row |
| 2-RED | a6e561f | root | test(04-10): add failing tests for eval_golden_queries regression harness |
| 2-GREEN | 88babe0 | root | feat(04-10): implement eval_golden_queries.py twin-run regression harness |

## TDD Gate Compliance

- RED gate: commit a6e561f (`test(04-10): ...`) — 14 failing tests committed before implementation
- GREEN gate: commit 88babe0 (`feat(04-10): ...`) — all 14 tests pass after implementation
- REFACTOR gate: no refactoring needed; implementation is clean

## Known Stubs (deferred by design)

- `expected_faculty: []` on all 15 golden queries — ground truth not populated; regression comparison deferred (see Task 3 above).
- `hierarchy_run_results.json` — not generated; deferred.
- `regression_gate_report.md` — not generated; deferred.
- `self-identification-results.md` — not created; faculty survey deferred.

## Threat Flags

None. This plan creates only: a JSON annotation file, a Python eval script, and a test file. No new network endpoints, auth paths, or schema changes introduced.

## Self-Check: PASSED

Files exist:
- FOUND: eval_golden_queries.py
- FOUND: test_eval_golden_queries.py
- FOUND: ReCiter-Publication-Manager/tests/chatbot/golden-queries.json
- FOUND: RECITER_AI_CHATBOT_README.md (contains "Post-classifier" and "$0.0005")
- FOUND: .planning/phases/04-subtopic-system/artifacts/golden_baseline.json (queries_sha256 updated)

Commits exist:
- e137a42 — FOUND (sub-repo)
- c35c3a2 — FOUND (root)
- a6e561f — FOUND (root)
- 88babe0 — FOUND (root)

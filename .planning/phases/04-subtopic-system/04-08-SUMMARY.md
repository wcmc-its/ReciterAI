---
phase: 04-subtopic-system
plan: 08
subsystem: retrieval
tags: [dynamodb, bedrock, llm-cascade, subtopic, retrieval, tdd, jest]

# Dependency graph
requires:
  - phase: 04-subtopic-system/04-07
    provides: classifyTopics + classifySubtopics LLM cascade functions
  - phase: 04-subtopic-system/04-05
    provides: calibration-notes.md with Aging pilot knee-point (5.0271)
  - phase: 04-subtopic-system/04-06
    provides: hierarchy.json and hierarchy.ts loader helpers
provides:
  - WEIGHT_FLOOR=5 constant in config/chatbot.ts (SUB-17, calibrated from Aging pilot)
  - Tier 1 retrieval: querySubtopicCandidates (FilterExpression on primary_subtopic_id)
  - Tier 2 retrieval: querySubtopicWithExpansion (N+1 parallel + pmid dedup)
  - Tier 4 renderer: renderTopicDecompose (stateless, zero LLM calls)
  - resolveSubtopicParent helper in hierarchy.ts for cross-topic see_also labels
  - Extended dispatcher in retrieval/index.ts with inline LLM cascade (Tier 1/2/3/4)
  - SUB-19 complete: resolveTopicId deleted from shared.ts; all callers use canonical IDs
  - matchedSubtopicId on Tier 1/2 results (cross-plan data contract for Plan 09 synthesis)
affects:
  - 04-09 (synthesis uses matchedSubtopicId from RetrievalResult)
  - 04-10 (golden set regression gate tests full cascade path)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "LLM cascade inline in dispatcher: Pass1 (classifyTopics) then Pass2 (classifySubtopics) before branching on tier"
    - "Tier branch: 4=topic_decompose(no LLM), 1=subtopic direct, 2=see_also expansion, 3=flat topic"
    - "CASCADE_FALLBACK_FLAT: normalized router phrases + partial:true when Pass1 fails"
    - "CASCADE_PASS2_SKIPPED: gap_query runs Pass1 only (subtopics don't apply)"
    - "DynamoDB FilterExpression on primary_subtopic_id for subtopic-scoped partition scans"
    - "N+1 parallel Promise.all with pmid dedup keeping highest articleScore for Tier 2"
    - "SUB-19 pattern: canonical topicId accepted AS-IS from cascade, no mechanical resolution"

key-files:
  created:
    - ReCiter-Publication-Manager/controllers/chatbot/retrieval/subtopic.ts
    - ReCiter-Publication-Manager/controllers/chatbot/retrieval/topic-decompose.ts
    - ReCiter-Publication-Manager/controllers/chatbot/__tests__/retrieval-subtopic.test.ts
    - ReCiter-Publication-Manager/controllers/chatbot/__tests__/retrieval-topic-decompose.test.ts
    - ReCiter-Publication-Manager/controllers/chatbot/__tests__/retrieval-dispatch.test.ts
  modified:
    - ReCiter-Publication-Manager/config/chatbot.ts
    - ReCiter-Publication-Manager/controllers/chatbot/retrieval/index.ts
    - ReCiter-Publication-Manager/controllers/chatbot/retrieval/shared.ts
    - ReCiter-Publication-Manager/controllers/chatbot/retrieval/topic.ts
    - ReCiter-Publication-Manager/controllers/chatbot/retrieval/gap.ts
    - ReCiter-Publication-Manager/controllers/chatbot/hierarchy.ts

key-decisions:
  - "WEIGHT_FLOOR=5 calibrated from Aging pilot knee-point 5.0271 (largest relative drop in descending weight sequence at aging_geriatric_emergency_trauma)"
  - "Tier 4 (topic_decompose) checked BEFORE Pass2 LLM call — avoids unnecessary Bedrock spend when user just wants hierarchy overview"
  - "gap_query skips Pass2 (CASCADE_PASS2_SKIPPED): subtopic granularity doesn't apply to gap analysis which operates at topic level"
  - "CASCADE_FALLBACK_FLAT uses normalizeTopicId on raw router phrases rather than calling Pass1 again — keeps fallback fast and deterministic"
  - "resolveTopicId deleted (not deprecated) to prevent accidental reuse; only resolveTopicId call site eliminated, normalizeTopicId retained for deep_dive"
  - "Existing retrieval-dispatcher.test.ts mocked with classifyTopics defaulting to fallback:true so prior flat-topic assertions hold without regressions"

patterns-established:
  - "Cascade inline in dispatcher: do NOT call classify functions from retrieval modules — dispatcher owns the cascade"
  - "matchedSubtopicId propagation: all Tier 1/2 items must carry this field for downstream synthesis"
  - "Tier check order: 4 (decompose?) → 1/2 (subtopic known?) → 3 (flat topic) — never reverse"
  - "Test isolation: jest.mock cascade dependencies in existing test files to preserve prior assertions"

requirements-completed: [SUB-08, SUB-10, SUB-16, SUB-17, SUB-19]

# Metrics
duration: ~14min (code commits) + context carry from prior session
completed: 2026-04-21
---

# Phase 4 Plan 08: Subtopic Retrieval Dispatch Summary

**Four-tier retrieval dispatcher with inline LLM cascade (classifyTopics + classifySubtopics), subtopic retrieval modules (FilterExpression Tier 1, N+1 parallel Tier 2), stateless topic-decompose renderer (Tier 4), and full SUB-19 retirement deleting resolveTopicId**

## Performance

- **Duration:** ~14 min (code commits 22:23-22:37 UTC-4)
- **Started:** 2026-04-21T22:23:32-04:00
- **Completed:** 2026-04-21T22:37:24-04:00
- **Tasks:** 4
- **Files modified:** 11 (5 created, 6 modified)

## Accomplishments
- Wired full Tier 1/2/3/4 retrieval dispatch inline in the dispatcher — topic queries now routed by Haiku semantic matching instead of mechanical string resolution
- Implemented subtopic.ts with querySubtopicCandidates (FilterExpression on primary_subtopic_id) and querySubtopicWithExpansion (N+1 parallel with pmid dedup by highest articleScore)
- Implemented topic-decompose.ts renderTopicDecompose — stateless, no LLM, three short-circuits (excluded/no-hierarchy/normal) with cross-topic see_also enrichment
- Deleted resolveTopicId from shared.ts and updated all callers (topic.ts, gap.ts) to accept canonical IDs directly — SUB-19 fully retired
- Added 23 new tests across 3 new test suites; full suite 283/283 passing; tsc --noEmit exits 0

## Task Commits

Each task was committed atomically:

1. **Task 1: Freeze WEIGHT_FLOOR in config** - `94cb4a1` (feat)
2. **Task 2: Tier 1/2 subtopic retrieval + Tier 4 topic-decompose renderer** - `c5d2346` (feat)
3. **Task 3: Delete resolveTopicId, update callers** - `9ac95b4` (refactor)
4. **Task 4: Extend dispatcher with inline LLM cascade** - `9e8a3cc` (feat)

_Note: Tasks 2 and 3 include TDD test suites committed in the same task commit per plan spec._

## Files Created/Modified
- `config/chatbot.ts` - Added WEIGHT_FLOOR=5 constant (SUB-17)
- `retrieval/subtopic.ts` - Tier 1 (querySubtopicCandidates) and Tier 2 (querySubtopicWithExpansion)
- `retrieval/topic-decompose.ts` - Tier 4 stateless renderTopicDecompose renderer
- `retrieval/index.ts` - Extended dispatcher: inline cascade, tier branching, cascade fallback, matchedSubtopicId propagation
- `retrieval/shared.ts` - resolveTopicId and TAXONOMY_V2 import deleted; SUB-19 retirement comment added
- `retrieval/topic.ts` - Removed resolveTopicId import/call; canonical AS-IS
- `retrieval/gap.ts` - Removed resolveTopicId import/call; canonical AS-IS
- `hierarchy.ts` - Added resolveSubtopicParent helper for cross-topic see_also label resolution
- `__tests__/retrieval-subtopic.test.ts` - 6 tests: FilterExpression, threshold defaults, empty result, logStage, N+1 dedup, cross-topic routing
- `__tests__/retrieval-topic-decompose.test.ts` - 6 tests: normal path, excluded topic, unknown topic, see_also enrichment, no-LLM import check, no-resolveTopicId check
- `__tests__/retrieval-dispatch.test.ts` - 11 tests: all cascade branches, CASCADE_FALLBACK_FLAT, CASCADE_PASS2_SKIPPED, Tier 1/2/3/4 routing, matchedSubtopicId propagation

## Decisions Made
- WEIGHT_FLOOR=5 from Aging pilot knee-point 5.0271 — largest relative drop in descending weight sequence
- Tier 4 (topic_decompose) is checked BEFORE Pass2 LLM call to avoid unnecessary Bedrock spend
- gap_query skips Pass2 (CASCADE_PASS2_SKIPPED) because subtopic granularity doesn't apply to gap analysis
- CASCADE_FALLBACK_FLAT uses normalizeTopicId on raw router phrases for deterministic, fast fallback
- resolveTopicId deleted (not deprecated) to prevent accidental reuse

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Test mock setup: getSubtopicsForTopics vs getSubtopicsForTopic mismatch**
- **Found during:** Task 2 (retrieval-topic-decompose.test.ts Test 10)
- **Issue:** topic-decompose.ts called `getSubtopicsForTopics([parentTopicId])` (plural) but test mock only had `getSubtopicsForTopic` (singular). Test 10 failed with "not a function."
- **Fix:** Changed topic-decompose.ts to call `getSubtopicsForTopic(parentTopicId)` (singular, matching the hierarchy.ts export). Updated Test 10 mock to use `mockReturnValueOnce` for both the primary and label-resolution calls.
- **Files modified:** retrieval/topic-decompose.ts, __tests__/retrieval-topic-decompose.test.ts
- **Verification:** Full suite passed after fix
- **Committed in:** c5d2346 (Task 2 commit)

**2. [Rule 1 - Bug] Test regex false positive on comment strings (Tests 11/12)**
- **Found during:** Task 2 (retrieval-topic-decompose.test.ts Tests 11-12)
- **Issue:** Tests used `not.toMatch(/getBedrockClient/)` on full file source but the source file had `getBedrockClient` in a comment ("It must never import getBedrockClient"). Same issue with resolveTopicId appearing in comments.
- **Fix:** Changed regexes to anchor on import patterns: `not.toMatch(/^import.*getBedrockClient/m)` and `not.toMatch(/import[^;]*resolveTopicId/)` / `not.toMatch(/resolveTopicId\s*\()`.
- **Files modified:** __tests__/retrieval-topic-decompose.test.ts
- **Verification:** Tests 11-12 passed with specific anchored patterns
- **Committed in:** c5d2346 (Task 2 commit)

**3. [Rule 1 - Bug] Existing retrieval-dispatcher.test.ts topic_match tests timing out after cascade added**
- **Found during:** Task 4 (after extending index.ts with cascade)
- **Issue:** After dispatcher was rewritten to call classifyTopics inline, existing topic_match tests in retrieval-dispatcher.test.ts timed out (5000ms) because classifyTopics wasn't mocked and attempted real Bedrock calls.
- **Fix:** Added jest.mock blocks for classifier, retrieval/subtopic, retrieval/topic-decompose, and hierarchy at top of retrieval-dispatcher.test.ts. classifyTopics defaults to `{topics:[], fallback:true}` so dispatcher takes CASCADE_FALLBACK_FLAT path and falls through to queryTopicCandidates — exactly what old assertions expected.
- **Files modified:** __tests__/retrieval-dispatcher.test.ts
- **Verification:** All 283 tests pass, no regressions
- **Committed in:** 9e8a3cc (Task 4 commit)

---

**Total deviations:** 3 auto-fixed (3 Rule 1 bugs)
**Impact on plan:** All three were test correctness issues introduced during implementation. No scope changes, no architectural deviations.

## Issues Encountered
- The Aging pilot calibration-notes.md had the knee-point analysis but the "Chosen value" section was blank. Used the documented knee-point value (5.0271, the largest relative drop at aging_geriatric_emergency_trauma) rounded to integer 5 per the plan's intent.

## Known Stubs
None — all retrieval modules wire to actual DynamoDB via getDocClient() and actual Bedrock via classifyTopics/classifySubtopics. No placeholder data flows to UI rendering.

## User Setup Required
None - no external service configuration required beyond what Plans 07 and prior established.

## Next Phase Readiness
- Plan 09 (synthesis) can use matchedSubtopicId from RetrievalResult items to resolve subtopic metadata directly from hierarchy.ts
- Plan 10 (golden set regression) can run the full cascade path end-to-end against real DynamoDB
- All Phase 2 tests still pass (283 total, 0 failures)
- TypeScript compiles clean (tsc --noEmit exits 0)

---
*Phase: 04-subtopic-system*
*Completed: 2026-04-21*

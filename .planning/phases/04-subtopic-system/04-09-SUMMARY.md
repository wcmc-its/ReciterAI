---
phase: 04-subtopic-system
plan: 09
subsystem: synthesis
tags: [synthesis, subtopic, bedrock, tdd, jest, prompts]

# Dependency graph
requires:
  - phase: 04-subtopic-system/04-07
    provides: classifyTopics + classifySubtopics LLM cascade functions
  - phase: 04-subtopic-system/04-08
    provides: matchedSubtopicId on RetrievalResult for Tier 1/2 subtopic_match results
provides:
  - SUBTOPIC_SYNTHESIS_SYSTEM constant in prompts/synthesis-system.ts (SUB-09, D-15)
  - buildSubtopicContextBlock() helper injecting verbatim subtopic label + description
  - synthesis.ts routes pattern === subtopic_match through subtopic-framed system prompt
  - topic_decompose short-circuit: yields JSON-stringified carousel data, no Bedrock call
  - partial fallback disclaimer prepended before Bedrock stream (SUB-16)
  - SYNTHESIS_FALLBACK logged when matchedSubtopicId absent or hierarchy entry missing
affects:
  - 04-10 (golden set should include ≥2 Tier 1 and ≥2 Tier 4 queries to exercise new synthesis paths)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Pattern-aware system prompt selection in streamSynthesis (retrieval.pattern drives prompt choice)"
    - "topic_decompose short-circuit: return before Bedrock call, yield structured JSON for UI carousel"
    - "SYNTHESIS_FALLBACK degradation: log warning + fall back to topic-level when subtopic metadata absent"
    - "Partial disclaimer: yield delta chunk before Bedrock stream when retrieval.partial=true"
    - "D-15 verbatim injection: buildSubtopicContextBlock injects label+description as-is, no paraphrase"

key-files:
  created:
    - ReCiter-Publication-Manager/controllers/chatbot/__tests__/synthesis-subtopic.test.ts
  modified:
    - ReCiter-Publication-Manager/controllers/chatbot/prompts/synthesis-system.ts
    - ReCiter-Publication-Manager/controllers/chatbot/synthesis.ts

key-decisions:
  - "System prompt override happens in streamSynthesis (not buildSynthesisContext) — routing depends on retrieval.pattern + matchedSubtopicId, not query_type alone"
  - "topic_decompose short-circuit fires before buildSynthesisContext to avoid wasted prompt hash computation"
  - "fallback_message field (snake_case) matches existing RetrievalResult interface; plan spec's camelCase was aspirational — kept snake_case for consistency"
  - "SYNTHESIS_FALLBACK distinguishes two degradation reasons: matchedSubtopicId_missing vs subtopic_metadata_missing"

requirements-completed: [SUB-09, SUB-19]

# Metrics
duration: ~4min
completed: 2026-04-22
---

# Phase 4 Plan 09: Subtopic Synthesis Routing Summary

**Subtopic-level system prompt (SUBTOPIC_SYNTHESIS_SYSTEM + buildSubtopicContextBlock) added to synthesis-system.ts; streamSynthesis routes Tier 1/2 subtopic_match through verbatim D-15 context injection, short-circuits topic_decompose to JSON carousel, and prepends partial fallback disclaimer per SUB-16**

## Performance

- **Duration:** ~4 min
- **Started:** 2026-04-22T02:40:53Z
- **Completed:** 2026-04-22T02:45:00Z (approx)
- **Tasks:** 2
- **Files modified:** 3 (1 created, 2 modified)

## Accomplishments

- Added `SUBTOPIC_SYNTHESIS_SYSTEM` constant and `buildSubtopicContextBlock()` to `prompts/synthesis-system.ts` — zero changes to existing topic-level exports
- Updated `streamSynthesis` in `synthesis.ts` with pattern-aware routing:
  - Tier 4 `topic_decompose`: short-circuits before Bedrock, yields `JSON.stringify({type:"topic_decompose", data:...})` for UI carousel rendering
  - Tier 1/2 `subtopic_match`: selects `SUBTOPIC_SYNTHESIS_SYSTEM` + builds context block with verbatim subtopic label and description (D-15)
  - `SYNTHESIS_FALLBACK` logged when `matchedSubtopicId` is missing or hierarchy entry absent; degrades gracefully to topic-level synthesis
  - `partial + fallback_message`: yields italic disclaimer delta before Bedrock stream (SUB-16)
- Created 5-test suite `synthesis-subtopic.test.ts` following TDD RED/GREEN cycle (4 initially failing → all 5 passing after implementation)
- Full Jest suite: 288/288 passing (was 283 before this plan; +5 new tests)
- TypeScript: `tsc --noEmit` exits 0

## Task Commits

Each task was committed atomically:

1. **Task 1: Extend prompts/synthesis-system.ts** - `c38b48e` (feat)
2. **Task 2 RED: Failing test suite** - `b4d5e3d` (test)
3. **Task 2 GREEN: Synthesis routing implementation + test fix** - `6c4ea10` (feat)

## Files Created/Modified

- `prompts/synthesis-system.ts` — Added `SUBTOPIC_SYNTHESIS_SYSTEM` (constant) and `buildSubtopicContextBlock()` (helper); existing exports unchanged
- `synthesis.ts` — Added imports (SUBTOPIC_SYNTHESIS_SYSTEM, buildSubtopicContextBlock, getSubtopicsForTopic, TAXONOMY_V2); rewrote `streamSynthesis` with topic_decompose short-circuit, subtopic_match prompt selection, partial disclaimer yield
- `__tests__/synthesis-subtopic.test.ts` — 5 tests: (1) subtopic prompt with verbatim label+desc, (2) topic_match regression, (3) partial disclaimer, (4) topic_decompose short-circuit, (5) SYNTHESIS_FALLBACK degradation

## Decisions Made

- System prompt selection lives in `streamSynthesis`, not `buildSynthesisContext` — because routing depends on `retrieval.pattern` + `retrieval.matchedSubtopicId`, not `routed.query_type` alone. `buildSynthesisContext` remains a pure context builder.
- `topic_decompose` short-circuit fires before `buildSynthesisContext` to avoid unnecessary prompt hash computation.
- `fallback_message` (snake_case) matches the existing `RetrievalResult` interface field established in Plan 08 — plan spec's camelCase `fallbackMessage` was aspirational.
- `SYNTHESIS_FALLBACK` distinguishes two distinct degradation reasons: `matchedSubtopicId_missing` (field not set by dispatcher) vs `subtopic_metadata_missing` (field set but hierarchy has no matching entry).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Test fixture used camelCase `fallbackMessage` but interface has snake_case `fallback_message`**
- **Found during:** Task 2 RED phase (test fixture compilation)
- **Issue:** Test 3 fixture used `fallbackMessage` in the `makeRetrieval` override. `RetrievalResult` defines `fallback_message` (snake_case), established in Plan 08. TypeScript would accept the unknown property but the runtime check `retrieval.fallback_message` would never see it.
- **Fix:** Changed test fixture to `fallback_message` to match the actual interface. Implementation in `streamSynthesis` reads `retrieval.fallback_message` (snake_case) consistently.
- **Files modified:** `__tests__/synthesis-subtopic.test.ts`
- **Committed in:** `6c4ea10` (Task 2 GREEN commit includes the test fix alongside the implementation)

## Known Stubs

None — `SUBTOPIC_SYNTHESIS_SYSTEM` and `buildSubtopicContextBlock` flow through to real Bedrock calls. The `topic_decompose` short-circuit yields the actual `renderTopicDecompose` payload (hierarchy data). No placeholder data flows to UI rendering.

## Note to Plan 10

The golden query set should include:
- At least 2 Tier 1/2 queries (e.g., "who works on cellular senescence?", "aging senolytics researchers") to exercise the subtopic-framed synthesis path
- At least 2 Tier 4 queries (e.g., "show me the aging subtopics", "break down the neuroscience topic") to exercise the `topic_decompose` short-circuit and verify the JSON carousel payload reaches the UI correctly

## Self-Check: PASSED

- `c38b48e` exists: confirmed
- `b4d5e3d` exists: confirmed
- `6c4ea10` exists: confirmed
- `prompts/synthesis-system.ts` export count: `grep -c "SUBTOPIC_SYNTHESIS_SYSTEM\|buildSubtopicContextBlock"` → 3 matches
- `synthesis.ts` routing: `grep "SUBTOPIC_SYNTHESIS_SYSTEM"` → 2 matches (import + usage)
- `synthesis.ts` topic_decompose: 5 matches
- `synthesis.ts` fallback_message: 2 matches (check + yield)
- Full Jest suite: 288/288
- `tsc --noEmit`: exits 0

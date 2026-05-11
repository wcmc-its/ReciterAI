---
phase: 04-subtopic-system
plan: 07
subsystem: chatbot-runtime
tags: [classifier, hierarchy-loader, llm-cascade, subtopics, types]
dependency_graph:
  requires: ["04-06"]
  provides: ["hierarchy-loader", "topic-classifier", "subtopic-classifier", "cascade-types"]
  affects: ["04-08-dispatcher", "04-09-synthesis"]
tech_stack:
  added: ["ConverseCommand (non-streaming)", "hierarchy.json JSON bundling"]
  patterns: ["two-pass LLM cascade", "hallucination guardrail via Set.has", "short-circuit before LLM call"]
key_files:
  created:
    - ReCiter-Publication-Manager/controllers/chatbot/hierarchy.ts
    - ReCiter-Publication-Manager/controllers/chatbot/classifier.ts
    - ReCiter-Publication-Manager/controllers/chatbot/prompts/topic-classifier-system.ts
    - ReCiter-Publication-Manager/controllers/chatbot/prompts/subtopic-classifier-system.ts
    - ReCiter-Publication-Manager/controllers/chatbot/__tests__/hierarchy.test.ts
    - ReCiter-Publication-Manager/controllers/chatbot/__tests__/classifier.test.ts
  modified:
    - ReCiter-Publication-Manager/controllers/chatbot/types.ts
decisions:
  - "ClassifierTopicMatch named distinctly from retrieval TopicMatch to avoid type collision — both live in types.ts but serve different layers"
  - "DEFAULT_WEIGHT_FLOOR=0 defers calibration to Plan 08 (CHATBOT_CONFIG.WEIGHT_FLOOR pending Aging pilot per SUB-17); all subtopics with total_weight>0 are Tier 1 until then"
  - "tier logic uses > not >= DEFAULT_WEIGHT_FLOOR — total_weight=0 is the absent/unscored state, not a valid below-floor case"
  - "resolveTopicId in shared.ts NOT deleted — Plan 08 migrates dispatcher and removes it (per plan spec)"
metrics:
  duration: "~15 minutes"
  completed: "2026-04-15"
  tasks: 3
  files: 7
requirements_satisfied: [SUB-07, SUB-11, SUB-16, SUB-19]
---

# Phase 4 Plan 07: LLM Cascade Classifier + Hierarchy Loader Summary

**One-liner:** Two-pass Haiku cascade (topic → subtopic) with hallucination guardrails and typed hierarchy.json loader replacing mechanical `resolveTopicId` string matching (SUB-19).

## What Was Built

### Task 1: Types + hierarchy.ts loader (SUB-11)

**types.ts** extended with 8 new Phase 4 types:
- `SubtopicDef`, `HierarchyJson`, `SeeAlsoEntry`, `ExcludedTopic` — schema types matching hierarchy-schema.md
- `Tier` (1|2|3|4) — retrieval tier assignment
- `ClassifierTopicMatch`, `TopicClassifierOutput`, `SubtopicMatch`, `SubtopicClassifierOutput` — cascade classifier I/O contracts

**hierarchy.ts** — 7 exported functions:
- `getSubtopicsForTopic(topicId)` → `SubtopicDef[]` (empty array, never undefined, for unknown/excluded topics)
- `getSubtopicsForTopics(topicIds[])` → `Array<SubtopicDef & { parent_topic_id: string }>` (flat, tagged)
- `getTotalWeight(topicId, subtopicId)` → `number` (0 on miss — safe default per D-18)
- `getSeeAlso(topicId, subtopicId)` → `SeeAlsoEntry[]` filtered by `from === subtopicId`
- `isExcludedTopic(topicId)` → `boolean`
- `getAllTopicsWithHierarchy()` → `string[]` (keys of hierarchy.json `topics` record)
- `getHierarchyVersion()` → `string`

**hierarchy.test.ts** — 9 tests (plan spec said 7; 2 extra for getTotalWeight and getSeeAlso edge cases).

### Task 2: classifyTopics — Pass 1 (SUB-19)

**prompts/topic-classifier-system.ts:**
- `TOPIC_CLASSIFIER_SYSTEM` — static system prompt (prompt-injection trust boundary)
- `buildTopicClassifierUserMessage()` — constructs user-turn with topic list, optional router hints block, optional maxTopics cap

**classifier.ts — `classifyTopics(input: TopicClassifierInput)`:**
- Single Haiku `ConverseCommand` call at `temperature: 0, maxTokens: 200`
- Output space constrained to `getAllTopicsWithHierarchy()` (topics with hierarchy entries only)
- Hallucination guardrail: drops any `topic_id` not in that set
- Sorts by confidence desc, caps at `maxTopics` (default 3)
- On Haiku error or JSON parse failure: returns `{topics: [], fallback: true}` + `logStage(..., "TOPIC_CLASSIFIER_FALLBACK", ...)`

**classifier.test.ts** — 7 classifyTopics tests covering: happy path, novel terms (adjacent topics), hint propagation, guardrail filter, parse fallback, throw fallback, maxTopics cap.

### Task 3: classifySubtopics — Pass 2 (SUB-07, SUB-16)

**prompts/subtopic-classifier-system.ts:**
- `SUBTOPIC_CLASSIFIER_SYSTEM` — static prompt with `topic_decompose` intent detection instruction
- `buildSubtopicClassifierUserMessage()` — formats candidate subtopics as `parent/id: label — description` lines

**classifier.ts — `classifySubtopics(input: SubtopicClassifierInput)`:**

Short-circuit conditions (no Haiku call):
1. `topicIds.length === 0` → `{subtopics: [], tier: 3, topic_decompose: false}`
2. Any `topicId` in `excluded_topics` → `{..., tier: 3, partial: true, fallback_message: "Hierarchy pending for ...; falling back to topic-level results."}` (SUB-16)
3. No subtopics found for any topicId → tier 3 (topic not yet through Pass 1)

Main path:
- Haiku `ConverseCommand` at `temperature: 0, maxTokens: 400`
- Hallucination guardrail: drops `subtopic_id` values not in `"parent_topic_id/subtopic_id"` set; `null` subtopic_id always preserved (topic-level fallback signal)
- `topic_decompose: true` → immediate `{subtopics: [], tier: 4, topic_decompose: true}` return
- Tier from primary match `total_weight`: `> DEFAULT_WEIGHT_FLOOR` → tier 1; else tier 2; no match → tier 3
- On error: `{subtopics: [], tier: 3, topic_decompose: false}` + `logStage(..., "SUBTOPIC_CLASSIFIER_FALLBACK", ...)`

**classifier.test.ts** — 7 classifySubtopics tests (tests 8-14): happy path tier, topic_decompose signal, excluded topic short-circuit, empty topicIds short-circuit, hallucination guardrail, parse fallback, throw fallback.

## Test Results

| Suite | Tests | Status |
|-------|-------|--------|
| hierarchy.test.ts | 9 | PASS |
| classifier.test.ts | 14 | PASS |
| **Full suite** | **260** | **PASS (22 suites)** |

Delta from plan 06 baseline: +23 new tests, +2 new test files, zero regressions.

## Known Items

### WEIGHT_FLOOR pending calibration
`DEFAULT_WEIGHT_FLOOR` is `0` (from `CHATBOT_CONFIG.WEIGHT_FLOOR ?? 0`). This means all subtopics with `total_weight > 0` are Tier 1. The calibrated value will be added to `CHATBOT_CONFIG` in Plan 08 after Aging pilot data is available (per SUB-17). The `?? 0` fallback makes the cascade operational without it.

### resolveTopicId stays in shared.ts
The mechanical resolver is NOT deleted here. Plan 08 (dispatcher) will swap all dispatcher call sites to use `classifyTopics` + `classifySubtopics`, then remove `resolveTopicId`. This plan only adds the replacement — the old path stays callable until migration.

### Cascade latency budget
~500ms per Haiku pass × 2 passes = ~1000ms added to the critical path. Acceptable given the current 15–40s total synthesis latency baseline. Documented in plan spec and CONTEXT.md.

## Deviations from Plan

### Auto-fixed issues

**1. [Rule 1 - Naming collision] ClassifierTopicMatch renamed from TopicMatch**
- **Found during:** Task 1 type design
- **Issue:** `types.ts` already exports `TopicMatch` as the per-publication retrieval result shape (from `retrieval/shared.ts` convention). Adding another `TopicMatch` for the classifier's Pass 1 output would create a name collision and ambiguity in the same file.
- **Fix:** Named the classifier's topic match type `ClassifierTopicMatch` to distinguish it from the retrieval-layer `TopicMatch`. All downstream consumers (classifier.ts, classifier.test.ts) import from the correct namespace.
- **Files modified:** `types.ts`, `classifier.ts`, `classifier.test.ts`
- **Impact:** None — Plan 08 will import `ClassifierTopicMatch` from `types.ts` directly. The plan frontmatter's `TopicMatch` description referred to the classifier shape; the rename is semantically faithful.

**2. [Rule 2 - Missing guard] tier logic uses `>` not `>=` for WEIGHT_FLOOR**
- **Found during:** Task 3 implementation
- **Issue:** `total_weight: 0` on a subtopic record means "not scored yet" (unassigned state per D-18), not "below threshold but valid". Using `>= 0` would incorrectly route unscored subtopics to Tier 1.
- **Fix:** Changed tier check to `weight > DEFAULT_WEIGHT_FLOOR` so that the exact-zero case falls to Tier 2 (thin subtopic), where the see_also expansion gives it the best chance of returning relevant results.
- **Files modified:** `classifier.ts`

## Threat Flags

| Flag | File | Description |
|------|------|-------------|
| threat_flag: user-content-in-llm-prompt | classifier.ts | User message flows to Haiku via user-role content block in both cascade passes. System prompt is static (trust boundary enforced). No user content in system block. Standard pattern — same as router.ts. |

## Self-Check: PASSED

| Item | Status |
|------|--------|
| hierarchy.ts exists | FOUND |
| classifier.ts exists | FOUND |
| topic-classifier-system.ts exists | FOUND |
| subtopic-classifier-system.ts exists | FOUND |
| hierarchy.test.ts exists | FOUND |
| classifier.test.ts exists | FOUND |
| commit 36ebf5d (Task 1) | FOUND |
| commit 8c702c5 (Task 2) | FOUND |
| commit 3a38a4a (Task 3) | FOUND |
| Full Jest suite (260 tests, 22 suites) | PASSED |

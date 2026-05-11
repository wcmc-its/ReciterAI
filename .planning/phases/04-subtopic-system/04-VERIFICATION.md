---
phase: 04-subtopic-system
verified: 2026-04-22T05:30:00Z
status: gaps_found
score: 5/7 roadmap success criteria verified
overrides_applied: 0
gaps:
  - truth: "Aging pilot met all four go/no-go criteria (D-20 coverage ≥85%, D-21 ≤3 corrections/subtopic, D-22 blind spot-check ≥80% agreement, D-23 no >40% pairwise overlap) before full backfill began"
    status: partial
    reason: "D-20 and D-23 automated gates PASS, but D-21 (reviewer corrections count) was never instrumented in the Plan 02 draft and D-22 (blind spot-check second reviewer) was deferred and never completed. REQUIREMENTS.md also notes D-23 measurement is vacuous — it computes Jaccard over primary_subtopic_id sets, which are mechanically disjoint."
    artifacts:
      - path: ".planning/phases/04-subtopic-system/aging_pilot_results.md"
        issue: "D-21 row shows 'not measured' / PENDING. D-22 row shows 'pending second reviewer' / PENDING. D-23 is PASS but measurement gap documented."
    missing:
      - "D-22 blind-check worksheet reviewer fill + agreement rate documented in aging_pilot_results.md"
      - "D-21 reviewer corrections count captured (requires adding reviewer_corrections_count to hierarchy draft schema)"
  - truth: "Golden query set regression gate passes on the new hierarchy (hit rate not below flat-topic baseline)"
    status: failed
    reason: "The regression gate was intentionally skipped. expected_faculty arrays in golden-queries.json are all empty (populated: 0 of 15 queries). hierarchy_run_results.json and regression_gate_report.md do not exist. A live comparison of hierarchy-mode vs flat-topic-baseline has never run."
    artifacts:
      - path: "ReCiter-Publication-Manager/tests/chatbot/golden-queries.json"
        issue: "All 15 queries have expected_faculty: [] — ground truth never populated. expected_tier annotated on 8 of 15, expected_behavior on all 15, but the ranking comparison cannot run without expected_faculty."
      - path: ".planning/phases/04-subtopic-system/artifacts/golden_baseline.json"
        issue: "golden_baseline.json.status is structural_lock_pending_ground_truth — expected_faculty was deferred from Plan 01 Task 4. Flat-topic baseline results were captured (results[] populated) but expected_faculty from domain-expert review was never completed."
    missing:
      - "expected_faculty populated for at least the 8 topic_match queries (requires domain-expert ranking)"
      - "Regression run: python eval_golden_queries.py against hierarchy-active dev server"
      - "regression_gate_report.md produced and showing PASS per category"

deferred:
  - truth: "Faculty self-identification spot check — 20-faculty survey post-backfill, agreement rate ≥70%"
    addressed_in: "Post-Phase-4 (SUB-15 deferred by user on 2026-04-22)"
    evidence: "Plan 10 Task 3 summary explicitly defers faculty self-ID survey: 'requires outreach to 20 faculty and response collection; not practical before the demo.' self-identification-results.md does not exist. User accepted this deferral."
  - truth: "see_also[] in hierarchy.json is non-empty (bidirectional cross-topic links generated)"
    addressed_in: "Future iteration (chunked see_also generation)"
    evidence: "Plan 06 summary documents the failure: 'see_also[] regeneration: currently 0 links. Single-shot Sonnet at 8k and then 32k max_tokens both produced malformed JSON.' Per D-10, see_also is navigation-aid only and does NOT block retrieval. Plans 07-10 are confirmed unblocked. Deferred to a chunked approach (split parents into groups of 15-20 per call)."
---

# Phase 4: Subtopic System Verification Report

**Phase Goal:** Add a second level of granularity below the 67-topic flat taxonomy via data-driven subtopic discovery, per-activity assignment, and subtopic-level faculty scoring. Enables `topic_decompose` query type and tier-based retrieval improvements across all topic-aware queries.
**Verified:** 2026-04-22T05:30:00Z
**Status:** gaps_found
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths (ROADMAP Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| SC1 | `hierarchy.json` contains data-driven subtopic definitions for all 67 topics passing cold-start floor | VERIFIED | hierarchy.json: version=subtopic_v1, 65 topics with subtopics (2 excluded: implementation_science, oral_craniofacial_health), all 65 have ≥12 subtopics, total 1,526 subtopics, all with total_weight > 0 |
| SC2 | Every qualifying activity has subtopic fields from Pass 2 | VERIFIED | Plan 03 summary: 1,424 activity rows written with subtopic_ids/primary_subtopic_id/subtopic_confidences; 97% coverage on aging; full backfill ran 66 topics per Plan 06 |
| SC3 | Every faculty record has subtopic_scores from Pass 3 | VERIFIED | Plan 03 summary: 368 faculty records touched for aging alone; aggregate_subtopic_scores.py confirmed functional; full backfill ran Pass 3 per topic |
| SC4 | Post-classifier resolves subtopics; Tier 1/2/3/4 live; synthesis frames at subtopic level | VERIFIED | classifier.ts exports classifyTopics + classifySubtopics with fallbacks; retrieval/index.ts calls both cascade passes and branches on tier; synthesis.ts routes subtopic_match through SUBTOPIC_SYNTHESIS_SYSTEM + buildSubtopicContextBlock |
| SC5 | topic_decompose returns structured subtopic lists via template renderer, no LLM call | VERIFIED | topic-decompose.ts exports renderTopicDecompose; no getBedrockClient/ConverseCommand imports; partial=true + fallback_message for excluded/pending topics per SUB-16 |
| SC6 | Golden query set regression gate passes on new hierarchy | FAILED | expected_faculty empty on all 15 queries; no live regression run; hierarchy_run_results.json absent; regression_gate_report.md absent. User authorized skip 2026-04-22. |
| SC7 | Aging pilot met all four D-20..D-23 criteria before backfill | PARTIAL | D-20 PASS (96.8%), D-23 PASS (vacuous measurement), D-21 NOT MEASURED, D-22 NOT COMPLETED. GO verdict given with documented caveats. |

**Score:** 5/7 roadmap success criteria verified (SC6 failed, SC7 partial; SC6 was user-authorized skip; SC7 two gates incomplete)

---

### Deferred Items

Items not yet met but intentionally deferred.

| # | Item | Addressed In | Evidence |
|---|------|-------------|----------|
| 1 | Faculty self-identification spot check (SUB-15) | Post-Phase-4 | Plan 10 Task 3: user deferred as "not practical before the demo." self-identification-results.md does not exist. |
| 2 | see_also[] non-empty in hierarchy.json (SUB-05 stored output) | Future iteration | Plan 06 summary: Sonnet JSON truncation at 8k and 32k max_tokens. see_also=[] in hierarchy.json. Per D-10: navigation-aid only, does not block retrieval. Chunked generation approach needed. |

---

## Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `utils/dynamodb_subtopic_migration.py` | DynamoDB UpdateItem helpers | VERIFIED | 3 public functions: update_activity_subtopics, update_faculty_subtopic_scores, clear_faculty_subtopic_scores_for_topic |
| `utils/test_dynamodb_subtopic_migration.py` | 5 unit tests | VERIFIED | 5/5 passing per Plan 01 summary |
| `.planning/phases/04-subtopic-system/hierarchy-schema.md` | Authoritative JSON schema doc | VERIFIED | Contains HierarchyJson, SubtopicDef, SeeAlsoEntry, excluded_topics, total_weight, bidirectionality, D-06/D-07/D-09/D-13/D-18 references |
| `.planning/phases/04-subtopic-system/calibration-notes.md` | WEIGHT_FLOOR calibration log | PARTIAL | Method (knee-point) and Aging weight distribution populated. "Chosen value" section is still a template placeholder. However, config/chatbot.ts has WEIGHT_FLOOR: 5 committed, so the constant is frozen in code. |
| `discover_subtopics.py` | Pass 1 CLI, 120+ lines | VERIFIED | 653 lines; temperature=0; D-06 comment present; --dry-run and 5 CLI flags |
| `prompts/subtopic_discovery.py` | Static Sonnet prompts | VERIFIED | DISCOVERY_SYSTEM_PROMPT, DISCOVERY_EXTENSION_PROMPT, BUILD_DISCOVERY_USER_MESSAGE exported |
| `.planning/phases/04-subtopic-system/hierarchy_draft_aging_geroscience.json` | Approved Aging draft | VERIFIED | 30 subtopics, review_status=approved, 84.1% coverage (authorized deviation) |
| `assign_subtopics.py` | Pass 2 CLI, 150+ lines | VERIFIED | ThreadPoolExecutor, _resolve_primary_on_tie, review_status gate, --resume |
| `prompts/subtopic_assignment.py` | Static Haiku prompt | VERIFIED | ASSIGNMENT_SYSTEM_PROMPT, BUILD_ASSIGNMENT_USER_MESSAGE |
| `aggregate_subtopic_scores.py` | Pass 3 CLI, 80+ lines | VERIFIED | articleScore formula, clear_faculty_subtopic_scores_for_topic, D-03/D-06 comments |
| `.planning/phases/04-subtopic-system/total_weights_aging_geroscience.json` | Per-subtopic weights | VERIFIED | 30 subtopics with total_weight floats |
| `generate_see_also.py` | See-also generator with bidirectionality | VERIFIED | _apply_bidirectionality_filter (def + call site), temperature=0, single-pass |
| `prompts/see_also_generation.py` | Static Sonnet prompt | VERIFIED | SEE_ALSO_SYSTEM_PROMPT, BUILD_SEE_ALSO_USER_MESSAGE |
| `.planning/phases/04-subtopic-system/aging_pilot_results.md` | Pilot go/no-go record | PARTIAL | Verdict: GO recorded. D-20 PASS, D-23 PASS, D-21 PENDING, D-22 PENDING. |
| `aging_pilot_gate.py` | Automated D-20/D-23 + worksheet | VERIFIED | compute_coverage, compute_pairwise_overlap, build_blind_check_worksheet, knee_point all defined |
| `backfill_topic.py` | Per-topic orchestrator, 100+ lines | VERIFIED | 607 lines; subprocess.run ×3 (discover/assign/aggregate); review_status gate; SKIP_REVIEW audit trail |
| `backfill_all.py` | Full-phase runner, 120+ lines | VERIFIED | 634 lines; Verdict: GO gate; generate_see_also.py subprocess; excluded_topics assembly |
| `ReCiter-Publication-Manager/controllers/chatbot/hierarchy.json` | Final canonical hierarchy | VERIFIED | version=subtopic_v1, 65 topics, 1,526 subtopics, 2 excluded, see_also=[] (known gap, deferred) |
| `ReCiter-Publication-Manager/controllers/chatbot/hierarchy.ts` | Typed hierarchy loader | VERIFIED | 8 exports: getSubtopicsForTopic, getSubtopicsForTopics, getTotalWeight, getSeeAlso, isExcludedTopic, getAllTopicsWithHierarchy, getHierarchyVersion, resolveSubtopicParent |
| `ReCiter-Publication-Manager/controllers/chatbot/types.ts` | Phase 4 types | VERIFIED | SubtopicDef, HierarchyJson, ClassifierTopicMatch, SubtopicMatch, Tier, TopicClassifierOutput, SubtopicClassifierOutput exported |
| `ReCiter-Publication-Manager/controllers/chatbot/classifier.ts` | LLM cascade classifier | VERIFIED | classifyTopics + classifySubtopics; TOPIC_CLASSIFIER_FALLBACK + SUBTOPIC_CLASSIFIER_FALLBACK; getAllTopicsWithHierarchy guardrail |
| `ReCiter-Publication-Manager/controllers/chatbot/prompts/topic-classifier-system.ts` | Static Pass 1 prompt | VERIFIED | TOPIC_CLASSIFIER_SYSTEM, buildTopicClassifierUserMessage |
| `ReCiter-Publication-Manager/controllers/chatbot/prompts/subtopic-classifier-system.ts` | Static Pass 2 prompt | VERIFIED | SUBTOPIC_CLASSIFIER_SYSTEM, buildSubtopicClassifierUserMessage |
| `ReCiter-Publication-Manager/config/chatbot.ts` | WEIGHT_FLOOR constant | VERIFIED | WEIGHT_FLOOR: 5 (from Aging pilot knee-point 5.0271) |
| `ReCiter-Publication-Manager/controllers/chatbot/retrieval/subtopic.ts` | Tier 1/2 retrieval | VERIFIED | querySubtopicCandidates + querySubtopicWithExpansion; FilterExpression on primary_subtopic_id |
| `ReCiter-Publication-Manager/controllers/chatbot/retrieval/topic-decompose.ts` | Tier 4 renderer | VERIFIED | renderTopicDecompose; no LLM imports; partial=true + fallback_message for excluded/pending topics |
| `ReCiter-Publication-Manager/controllers/chatbot/retrieval/index.ts` | Extended dispatcher | VERIFIED | classifyTopics + classifySubtopics called inline; CASCADE_FALLBACK_FLAT; matchedSubtopicId propagated; resolveTopicId absent |
| `ReCiter-Publication-Manager/controllers/chatbot/retrieval/shared.ts` | resolveTopicId DELETED | VERIFIED | Only SUB-19 retirement comment remains; function itself removed |
| `ReCiter-Publication-Manager/controllers/chatbot/prompts/synthesis-system.ts` | Subtopic synthesis prompt | VERIFIED | SUBTOPIC_SYNTHESIS_SYSTEM + buildSubtopicContextBlock exported; existing topic-level exports unchanged |
| `ReCiter-Publication-Manager/controllers/chatbot/synthesis.ts` | Pattern-aware synthesis routing | VERIFIED | 9 matches for SUBTOPIC_SYNTHESIS_SYSTEM/buildSubtopicContextBlock/subtopic_match/fallback_message/topic_decompose |
| `ReCiter-Publication-Manager/tests/chatbot/golden-queries.json` | 15-query golden set | PARTIAL | 15 queries, correct type distribution (8 topic_match, 3 topic_decompose, 2 gap_query, 2 team_assembly), expected_behavior on all 15, expected_tier on 8, but expected_faculty=[] on ALL 15 |
| `eval_golden_queries.py` | Regression harness CLI | VERIFIED | compute_mrr, compute_recall_at_10, diff_report, hash-lock gate (queries_sha256 checked ×4), --skip-hash-check flag |
| `RECITER_AI_CHATBOT_README.md` | Post-classifier cost row | VERIFIED | "Post-classifier (SUB-07) | Haiku | ~$0.0005" present |

---

## Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `utils/dynamodb_subtopic_migration.py` | `utils/dynamodb_helpers.py` | `from utils.dynamodb_helpers import to_decimal, get_table` | WIRED | Plan 01 confirmed; get_table added to helpers during Plan 01 |
| `assign_subtopics.py` | `utils.dynamodb_subtopic_migration.update_activity_subtopics` | import + per-activity UpdateItem call | WIRED | Per Plan 03 |
| `aggregate_subtopic_scores.py` | `utils.dynamodb_subtopic_migration.clear_faculty_subtopic_scores_for_topic + update_faculty_subtopic_scores` | clear then write per D-06 | WIRED | D-03/D-06 comments present in code |
| `classifier.ts` | `hierarchy.ts getSubtopicsForTopics` | builds candidate slice before Pass 2 prompt | WIRED | Confirmed via Plan 07 summary |
| `classifier.ts` | `taxonomy.ts TAXONOMY_V2` | classifyTopics enumerates topics | WIRED | Confirmed in Plan 07 |
| `classifier.ts` | `bedrock-client.ts getBedrockClient` | Haiku calls in both cascade passes | WIRED | Plan 07 summary |
| `retrieval/index.ts` | `classifier.ts classifyTopics + classifySubtopics` | inline cascade for topic-bearing queries | WIRED | grep: 9 matches for classifyTopics/classifySubtopics in index.ts |
| `retrieval/index.ts` | `retrieval/subtopic.ts + topic-decompose.ts` | dispatch branches on subtopicResult.tier | WIRED | grep: 11+ matches for querySubtopicCandidates/querySubtopicWithExpansion/renderTopicDecompose |
| `config/chatbot.ts WEIGHT_FLOOR` | `classifier.ts DEFAULT_WEIGHT_FLOOR` | CHATBOT_CONFIG.WEIGHT_FLOOR lookup | WIRED | Plan 08; value is 5 |
| `synthesis.ts` | `prompts/synthesis-system.ts buildSubtopicContextBlock` | when pattern === 'subtopic_match' | WIRED | 2 matches for SUBTOPIC_SYNTHESIS_SYSTEM in synthesis.ts |
| `synthesis.ts` | `retrieval dispatcher matchedSubtopicId` | reads matchedSubtopicId from RetrievalResult | WIRED | Plan 09 summary confirms; wave 7 cross-plan data contract |
| `eval_golden_queries.py` | `artifacts/golden_baseline.json queries_sha256` | hash-lock verification | WIRED | 4 matches for golden_baseline/queries_sha256 in eval script |

---

## Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `hierarchy.ts` | `HIERARCHY` | `hierarchy.json` (bundled at build time via Next.js JSON import) | Yes — 65 topics, 1,526 subtopics with real total_weight values from Pass 3 | FLOWING |
| `retrieval/subtopic.ts querySubtopicCandidates` | activity records filtered by primary_subtopic_id | DynamoDB TOPIC# partition with FilterExpression="primary_subtopic_id = :sid" | Yes — real DynamoDB; data written by Pass 2 (1,424 rows for aging alone) | FLOWING |
| `retrieval/topic-decompose.ts renderTopicDecompose` | subtopics array | hierarchy.ts getSubtopicsForTopic (hierarchy.json data) | Yes — 1,526 real subtopics with labels, descriptions, total_weight | FLOWING |
| `synthesis.ts streamSynthesis` | SUBTOPIC_SYNTHESIS_SYSTEM + context block | hierarchy.ts getSubtopicsForTopic; matchedSubtopicId from dispatcher | Yes — verbatim label+description from hierarchy.json; real candidates from DynamoDB retrieval | FLOWING |
| `eval_golden_queries.py` | expected_faculty | golden-queries.json (user domain-expert ranking) | No — expected_faculty=[] on all 15 queries; regression comparison impossible | DISCONNECTED |

---

## Behavioral Spot-Checks

Step 7b: SKIPPED for Python pipeline scripts and TypeScript modules (no runnable server in current state). The PM dev server requires authentication and is not running. Test suites serve as proxy for behavioral verification.

**Jest suite status (per Plan 09 summary):** 288/288 passing (22+ suites). This covers:
- hierarchy.test.ts: 9 tests
- classifier.test.ts: 14 tests (classifyTopics ×7 + classifySubtopics ×7)
- retrieval-subtopic.test.ts: 6 tests
- retrieval-topic-decompose.test.ts: 6 tests
- retrieval-dispatch.test.ts: 11 tests
- synthesis-subtopic.test.ts: 5 tests
- All prior Phase 2 suites: no regressions

**Python test status (per plan summaries):**
- test_dynamodb_subtopic_migration.py: 5/5 pass
- test_generate_see_also.py: 4/4 pass
- test_aging_pilot_gate.py: 20/20 pass (per Plan 05)
- test_eval_golden_queries.py: 14/14 pass

---

## Requirements Coverage

| Requirement | Source Plan(s) | Description (abbreviated) | Status | Evidence |
|-------------|---------------|---------------------------|--------|----------|
| SUB-01 | 04-02 | Pass 1 Discovery script | SATISFIED | discover_subtopics.py (653 lines); ran live on aging (30 subtopics, 84.1% coverage) |
| SUB-02 | 04-02, 04-06 | Human review gate | SATISFIED | hierarchy_draft_aging_geroscience.json: review_status=approved; full backfill used --skip-review with audit trail |
| SUB-03 | 04-03 | Pass 2 Assignment (Haiku, confidence floor 0.3) | SATISFIED | assign_subtopics.py; 1,424 rows written for aging; tiebreaker implemented; REQUIREMENTS.md: [x] Complete |
| SUB-04 | 04-03 | Pass 3 Aggregation (primary-only, zero-weight secondaries) | SATISFIED | aggregate_subtopic_scores.py; 368 faculty records; D-03 comment in code; REQUIREMENTS.md: [x] Complete |
| SUB-05 | 04-04 | See-also generation (bidirectionality filter, stored in hierarchy.json) | PARTIAL | Script exists and is tested (4/4 pass). hierarchy.json see_also=[] due to Sonnet JSON truncation at full scale. REQUIREMENTS.md marks [x] Complete. The requirement says "stored in hierarchy.json see_also array" — the array is empty, not populated. Deferred (navigation-aid only per D-10). |
| SUB-06 | 04-01, 04-03 | DynamoDB schema migration (subtopic fields on activity/faculty records) | SATISFIED | Migration helpers tested; Pass 2/3 ran live; legacy-tolerance documented; REQUIREMENTS.md: [x] Complete |
| SUB-07 | 04-07 | Post-classifier — Haiku resolves subtopics/tier/topic_decompose | SATISFIED | classifier.ts classifySubtopics; fallback to Tier 3 on error; REQUIREMENTS.md: [x] Complete |
| SUB-08 | 04-08 | Four-tier retrieval dispatch | SATISFIED | retrieval/index.ts dispatches to subtopic.ts (T1/2), topic.ts (T3), topic-decompose.ts (T4); REQUIREMENTS.md traceability shows Pending but code is verified Complete |
| SUB-09 | 04-09 | Subtopic-level synthesis (verbatim label+description, D-15) | SATISFIED | synthesis.ts routes subtopic_match through SUBTOPIC_SYNTHESIS_SYSTEM + buildSubtopicContextBlock; REQUIREMENTS.md traceability shows Pending but code is verified Complete |
| SUB-10 | 04-08 | Topic-decompose renderer (stateless, no LLM, partial flag) | SATISFIED | renderTopicDecompose exported; no LLM imports; partial=true + fallback_message for excluded/pending; REQUIREMENTS.md traceability shows Pending but code verified Complete |
| SUB-11 | 04-07 | hierarchy.ts loader with 6 helper exports | SATISFIED | 8 exports confirmed; bundled via Next.js JSON import; REQUIREMENTS.md: [x] Complete |
| SUB-12 | 04-05 | Aging pilot gate (D-20..D-23) | PARTIAL | D-20 PASS (96.8%), D-23 PASS (measurement gap noted), D-21 NOT MEASURED, D-22 NOT COMPLETED. GO verdict accepted with documented caveats. REQUIREMENTS.md traceability shows Pending. |
| SUB-13 | 04-06 | Full backfill across 66 remaining topics | SATISFIED | hierarchy.json contains 65 topics (aging + 64 backfilled); 2 excluded; REQUIREMENTS.md traceability shows Pending but backfill ran and hierarchy.json is evidence |
| SUB-14 | 04-01, 04-10 | Golden query set (15 queries) + regression harness | PARTIAL | golden-queries.json has 15 queries, expected_behavior on all, expected_tier on 8; eval_golden_queries.py built. But expected_faculty=[] — regression gate cannot run. REQUIREMENTS.md traceability shows Pending. |
| SUB-15 | 04-10 | Faculty self-ID spot check (20 faculty, ≥70% agreement) | NOT SATISFIED | self-identification-results.md does not exist. Deferred by user. REQUIREMENTS.md traceability shows Pending. |
| SUB-16 | 04-07, 04-08 | Rollout UX — partial flag + fallback_message for pending topics | SATISFIED | topic-decompose.ts returns partial=true + "Hierarchy pending for …; falling back to topic-level results."; classifySubtopics returns partial on excluded topics; REQUIREMENTS.md: [x] Complete |
| SUB-17 | 04-05, 04-08 | WEIGHT_FLOOR calibration documented + frozen as constant | SATISFIED | calibration-notes.md has knee-point analysis (5.0271); config/chatbot.ts: WEIGHT_FLOOR: 5. Note: "Chosen value" section in calibration-notes.md still has template placeholder text — the value is frozen in code but the doc is not fully updated. REQUIREMENTS.md traceability shows Pending but code verified Complete. |
| SUB-18 | 04-10 | Cost table updated with post-classifier line | SATISFIED | RECITER_AI_CHATBOT_README.md: "Post-classifier (SUB-07) | Haiku | ~$0.0005". REQUIREMENTS.md traceability shows Pending but artifact verified Complete. |
| SUB-19 | 04-07, 04-08 | LLM cascade replaces mechanical resolveTopicId | SATISFIED | resolveTopicId deleted from shared.ts (only comment remains); classifier.ts exports classifyTopics + classifySubtopics with hallucination guardrails; REQUIREMENTS.md: [x] Complete |

**Traceability table note:** REQUIREMENTS.md traceability table is stale — many items show "Pending" that are clearly complete based on code verification. The checklist in the requirements body is more accurate (uses [x] for completed items). The traceability table was not updated during or after Plans 07-10.

---

## Anti-Patterns Found

| File | Pattern | Severity | Impact |
|------|---------|----------|--------|
| `ReCiter-Publication-Manager/tests/chatbot/golden-queries.json` | `expected_faculty: []` on all 15 queries | BLOCKER | Regression gate cannot run — no ground truth to compare against. The eval_golden_queries.py script is fully implemented but produces no meaningful MRR/Recall@10 without expected_faculty populated. |
| `.planning/phases/04-subtopic-system/calibration-notes.md` | "Chosen value" section contains `<!-- Freeze as WEIGHT_FLOOR ... -->` template placeholder | WARNING | WEIGHT_FLOOR is correctly frozen in config/chatbot.ts (=5); the calibration doc is not synchronized. Minor documentation gap. |
| `ReCiter-Publication-Manager/controllers/chatbot/hierarchy.json` | `"see_also": []` | WARNING | see_also links were intended to populate Tier 2 expansion targets. Currently hierarchy has 0 bidirectional links. Per D-10 (navigation-aid only), this does not block retrieval — Tier 2 expansion would simply return no additional candidates. Documented as a known deferred item. |

---

## Human Verification Required

### 1. Regression Gate Execution

**Test:** Start the PM dev server with hierarchy-active code (feature/chatbot-runtime). Populate expected_faculty in golden-queries.json by: (a) running eval_golden_queries.py against the hierarchy-active server to get returned faculty lists, (b) having a domain expert (Paul Albert) rank the returned faculty for each query as expected_faculty ground truth. Then re-run eval_golden_queries.py with --diff-report to compare against golden_baseline.json flat-topic results.
**Expected:** regression_gate_report.md shows PASS for all 4 query categories (topic_match, topic_decompose, gap_query, team_assembly). gq-02 "cellular senescence" should now return subtopic-scoped Aging faculty (vs 0 candidates under flat-topic baseline) — the canonical SUB-19 regression case.
**Why human:** Requires an authenticated PM dev server session AND domain-expert judgment to rank returned faculty. Cannot be automated without real faculty data and domain expertise.

### 2. D-22 Blind Spot-Check Completion

**Test:** Provide the second reviewer (not Plan 02's reviewer) with aging_blind_check_worksheet.csv (generated by aging_pilot_gate.py). The reviewer fills reviewer_pick_1 and reviewer_pick_2 columns WITHOUT seeing the actual primary_subtopic_id assignments. Compare reviewer picks to DynamoDB TOPIC#aging_geroscience items' primary_subtopic_id for the 10 sampled PMIDs. Compute agreement rate.
**Expected:** Agreement ≥80% (8 of 10 PMIDs: reviewer's pick_1 or pick_2 matches the Haiku-assigned primary_subtopic_id). Result appended to aging_pilot_results.md.
**Why human:** Requires a second domain-expert reviewer who has not seen the Pass 2 output.

---

## Gaps Summary

Two gaps block full goal achievement:

**Gap 1: Regression gate not run (SC6 FAILED).** The eval_golden_queries.py harness is built and tested (14/14 unit tests pass). The flat-topic baseline results are captured in golden_baseline.json. But expected_faculty is empty on all 15 golden queries — the domain-expert ranking step (Plan 01 Task 4 Step B, Plan 10 Task 3) was deferred. Without expected_faculty, the MRR and Recall@10 comparison cannot run. This is the most significant gap: the phase's core quality gate was never executed. The user authorized this skip in Plan 10 ("Phase 4 declared complete based on 288/288 tests passing"), but by the roadmap's success criteria, it is unmet.

**Gap 2: Aging pilot D-21 and D-22 not completed (SC7 PARTIAL).** D-22 (blind spot-check with a second reviewer) was deferred — the worksheet was generated but the reviewer was never sent the form. D-21 (reviewer corrections count) was never instrumented in the Plan 02 hierarchy draft, so it cannot be measured automatically. The GO verdict was accepted with these gates deferred as "non-blocking," but the ROADMAP success criterion SC7 requires all four criteria to be met.

Both gaps require human action to resolve. The automated infrastructure is in place for both. These gaps do not affect the functional correctness of the deployed code — the subtopic system is wired and operational. They affect the quality assurance evidence required by the roadmap.

---

*Verified: 2026-04-22T05:30:00Z*
*Verifier: Claude (gsd-verifier)*

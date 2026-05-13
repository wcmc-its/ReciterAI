---
phase: 12-feedback-loops-both-aggregations-residual-hygiene
plan: feedback-producer
subsystem: database
tags: [spotlight, critic, dynamodb, event-producer, enum, strEnum, feedback-loops]

# Dependency graph
requires:
  - phase: 10-hot-cold-path-split
    provides: utils/event_records.py builder pattern (build_uncovered_pmid_record + write_uncovered_pmid as template)
  - phase: 10-hot-cold-path-split
    provides: pipeline_common.alert.dispatch for vocabulary-drift WARN alerts
provides:
  - "CritReasonCode StrEnum (5 members, exact LLM vocabulary) in spotlight/critic.py"
  - "build_critic_reject_record pure builder in utils/event_records.py (per-publish/per-subtopic/per-pmid_set keyed)"
  - "write_critic_reject thin writer in utils/event_records.py"
  - "_compute_pmid_set_hash helper for stable order-invariant PMID set hashing"
  - "CRITIC_REJECT#{publish_id}#{subtopic_id}#{pmid_set_hash} DDB write at critic exhaustion site"
  - "stage_table seam on run_critic_loop for testable DDB injection"
affects:
  - feedback-consumer (wave-2 SPOTLIGHT_DIAGNOSTIC#{subtopic_id} aggregator reads CRITIC_REJECT# rows)
  - 12-both-aggregations (independent wave-1 plan; no dependency)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "CritReasonCode StrEnum with exact LLM vocab spelling (preserves historical comparability)"
    - "Additive dual-write at exhaustion site: write_review_entry (SPOTLIGHT_REVIEW#) then write_critic_reject (CRITIC_REJECT#)"
    - "stage_table DynamoDB seam on run_critic_loop (matches score_publications.py / assign_subtopics.py pattern)"
    - "last_deterministic_verdict tracked in regen loop for PRE_LLM_GATE path"
    - "Vocabulary drift handled gracefully: dispatch WARN + reason_code='unknown' + raw_failed_constraint"
    - "PII boundary enforced at builder: no lede_text on CRITIC_REJECT# rows (Pattern H)"
    - "author_cwids derived from last_papers first/last Author.person_identifier (sorted+deduped)"

key-files:
  created:
    - "utils/event_records.py (_compute_pmid_set_hash + build_critic_reject_record + write_critic_reject)"
    - "tests/test_critic_reject_event.py (12 builder/writer tests)"
    - "tests/test_critic_reject_producer.py (12 producer integration tests)"
  modified:
    - "utils/event_records.py (appended CRITIC_REJECT# section; hashlib added to imports)"
    - "spotlight/critic.py (CritReasonCode StrEnum added; stage_table param + dual-write at exhaustion)"

key-decisions:
  - "PK is per-(publish_id, subtopic_id, pmid_set_hash) NOT per-cwid (D-08 re-framing): spotlight artifacts span many CWIDs; no single 'spotlight cwid' exists"
  - "author_cwids carried in body (not in key) for future per-faculty drill-down (D-08 + D-32) without forcing per-CWID aggregation now"
  - "stage_table parameter added to run_critic_loop (not cwid) — the only new parameter needed; cwid threading is NOT required"
  - "pipeline_common.alert.dispatch('WARN', ...) used (not a non-existent 'alert()' function) for vocabulary-drift alerting"
  - "last_deterministic_verdict tracked alongside last_llm_verdict to support PRE_LLM_GATE path without API change"
  - "TDD RED/GREEN for both tasks; test lede uses 30-word deterministic-passing sentence to avoid MIN_PAPERS / length floor in generate_lede"

patterns-established:
  - "CRITIC_REJECT# event-record follows utils/event_records.py builder pattern exactly (keyword-only args, _now_iso fallback, Decimal coercion at boundary)"
  - "Additive dual-write: existing write_review_entry call unchanged; new write_critic_reject placed immediately after"
  - "Vocabulary drift via StrEnum try/except: known code passes through verbatim; unknown code → 'unknown' bucket + WARN alert + raw preserved"

requirements-completed:
  - "spec-§9-producer"
  - "D-08"
  - "D-09"
  - "D-10"
  - "D-30"
  - "D-31"

# Metrics
duration: 14min
completed: 2026-05-13
---

# Phase 12 Plan feedback-producer: CRITIC_REJECT# Producer Summary

**CritReasonCode StrEnum (5 LLM vocab values) + CRITIC_REJECT#{publish_id}#{subtopic_id}#{pmid_set_hash} dual-write at critic exhaustion alongside unchanged SPOTLIGHT_REVIEW# write, with author_cwids from selected_papers and vocabulary-drift alerting**

## Performance

- **Duration:** 14 min
- **Started:** 2026-05-12T23:59:21Z
- **Completed:** 2026-05-13T00:13:24Z
- **Tasks:** 2 (each with TDD RED + GREEN commits)
- **Files modified:** 4 (2 created, 2 modified)

## Accomplishments

- `build_critic_reject_record` + `write_critic_reject` + `_compute_pmid_set_hash` added to `utils/event_records.py` following existing idempotent builder pattern; PK is 4-segment `CRITIC_REJECT#{publish_id}#{subtopic_id}#{hash}`; no lede text anywhere in module (PII boundary)
- `CritReasonCode` StrEnum with 5 exact members (matches LLM `failed_constraint` vocabulary verbatim for historical comparability) added to `spotlight/critic.py` after `LLMVerdict`
- Additive dual-write at exhaustion site: `write_review_entry` (SPOTLIGHT_REVIEW#) unchanged; `write_critic_reject` (CRITIC_REJECT#) added after; vocabulary drift handled via `dispatch("WARN", ...)` + `reason_code='unknown'` + `raw_failed_constraint`; pre-LLM gate path uses `PRE_LLM_GATE` + `pre_llm_constraint` from `last_deterministic_verdict`
- 24 new tests (12 builder/writer + 12 producer) + 395 total passing across the test suite

## Task Commits

Each task was committed atomically (TDD — two commits per task):

1. **Task 1 RED: Failing tests for build_critic_reject_record** - `795d195` (test)
2. **Task 1 GREEN: Add build_critic_reject_record + write_critic_reject** - `9459df4` (feat)
3. **Task 2 RED: Failing tests for CritReasonCode + dual-write** - `ee06339` (test)
4. **Task 2 GREEN: CritReasonCode enum + CRITIC_REJECT# dual-write** - `f63c241` (feat)

**Plan metadata:** (pending — final commit after SUMMARY)

## Files Created/Modified

- `utils/event_records.py` — Added `hashlib` import, `_compute_pmid_set_hash`, `build_critic_reject_record`, `write_critic_reject` (88 lines appended; 0 lede references)
- `spotlight/critic.py` — Added `from enum import StrEnum`, `CritReasonCode` StrEnum class, `stage_table` parameter to `run_critic_loop`, `last_deterministic_verdict` tracking, CRITIC_REJECT# dual-write block at exhaustion site
- `tests/test_critic_reject_event.py` — 12 tests: PK shape, hash stability, hash invariance, PK per publish_id/subtopic_id, author_cwids sorted+deduped, pmid_set full list, unknown/pre_llm_gate paths, PII boundary, writer put_item, idempotency
- `tests/test_critic_reject_producer.py` — 12 tests: CritReasonCode enum values, dual-write, review_queue unchanged, PK shape with D-08 values in scope, author_cwids from selected_papers, known/unknown/pre_llm_gate constraints, PII boundary, regression (review entry still carries lede_text), passing critic does not write CRITIC_REJECT#

## Decisions Made

- **stage_table not cwid**: `run_critic_loop` gained `stage_table=None` (DynamoDB resource table seam, matches `score_publications.py` pattern) — NOT a `cwid` parameter. The D-08 re-framing means CWID is not in the PK at all; no threading needed.
- **pipeline_common.alert.dispatch**: The plan referenced `alert(message, *, severity)` but the actual module only exposes `dispatch(severity, message, context)` with `"WARN"` / `"ERROR"` as valid severity values. Used `dispatch("WARN", message, context_dict)` directly.
- **last_deterministic_verdict tracking**: Added `last_deterministic_verdict: DeterministicVerdict | None = None` to the loop locals and set it on each deterministic failure. This minimal change gives the PRE_LLM_GATE path access to `failed_constraints[0]` without changing the public API.
- **TDD lede fixture**: Tests use a 30-word deterministic-passing lede (`_DETERMINISTIC_PASSING_LEDE`) to satisfy `generate_lede`'s `MIN_PAPERS >= 2` and `word_count >= 22` requirements. Tests that need deterministic failure use an em-dash lede of the same length.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] pipeline_common.alert signature mismatch**
- **Found during:** Task 2 (reading alert.py before implementing)
- **Issue:** Plan specified `alert(message, *, severity="warning")` but `pipeline_common/alert.py` only exports `dispatch(severity, message, context)` with `"WARN"` / `"ERROR"` as valid severities. No `alert()` function exists.
- **Fix:** Used `dispatch("WARN", message, context_dict)` directly (positional first arg = severity). The behavior is identical: best-effort Slack notification at WARN level, never crashes.
- **Files modified:** spotlight/critic.py (import + call site)
- **Verification:** Tests patch `pipeline_common.alert.dispatch` and assert it is called once with `"WARN"` as first positional arg
- **Committed in:** f63c241 (Task 2 feat commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 — bug: API mismatch in plan's interface reference)
**Impact on plan:** Fix required for correctness; behavior is identical (WARN-severity alert, never crashes producer). No scope creep.

## Issues Encountered

- `generate_lede` requires `MIN_PAPERS >= 2` valid papers (papers with author identity). Tests initially used 1 paper and hit `ValueError: requires at least 2 papers with valid author payload`. Fixed by using 2 papers in all producer tests.
- Default test lede was 6 words (too short for deterministic `LENGTH_MIN=22`). Fixed with a 30-word lede that passes all deterministic checks. Tests needing a det-fail lede use a 30-word lede containing an em dash.

## User Setup Required

None - no external service configuration required. The `stage_table` parameter defaults to `None` (skip CRITIC_REJECT# write) when no DDB table is injected, matching existing dry-run behavior.

## Next Phase Readiness

- Wave-2 feedback-consumer plans can now read `CRITIC_REJECT#{publish_id}#{subtopic_id}#{pmid_set_hash}` rows to build `SPOTLIGHT_DIAGNOSTIC#{subtopic_id}` aggregations
- `author_cwids` field in CRITIC_REJECT# body is ready for D-32 per-faculty drill-down in the consumer
- The `stage_table` seam allows production callers to pass `get_table()` from `utils.dynamodb_helpers` at the `spotlight/publish.py` call site (this wiring is not in scope for this plan; it is a wave-2 or integration concern)

---

## Known Stubs

None - all fields in the CRITIC_REJECT# record carry real data at the write site. No hardcoded empty values flow to consumers.

## Threat Flags

No new network endpoints or auth paths introduced. The CRITIC_REJECT# write adds a DDB `put_item` call that uses the same resource table seam as other event records. PII boundary (Pattern H) is enforced: `lede_text` is absent from all CRITIC_REJECT# rows, verified by both builder and producer tests.

---

*Phase: 12-feedback-loops-both-aggregations-residual-hygiene*
*Completed: 2026-05-13*

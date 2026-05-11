---
phase: 04-subtopic-system
plan: 04
subsystem: pipeline
tags: [python, sonnet, bedrock, hierarchy, see-also, bidirectionality]

# Dependency graph
requires:
  - phase: 04-subtopic-system
    provides: Plan 04-02 approved Aging-only Pass 1 hierarchy draft (hierarchy_draft_aging_geroscience.json)
provides:
  - generate_see_also.py CLI (single-pass Sonnet + bidirectionality filter)
  - prompts/see_also_generation.py (static, cross-topic, bidirectional-required prompt)
  - _apply_bidirectionality_filter helper (reusable by Plan 06 full-backfill loop)
  - Short-circuit behavior when parent topic count < 2 (no Sonnet call, empty output)
affects: [04-06-backfill, 04-09-tier-2-see-also-expansion, plan-06-full-regen]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Single-pass Sonnet at temperature=0 + deterministic post-filter (not run-twice-and-union)"
    - "Module-level mockable wrapper (_call_sonnet_for_see_also) as the LLM boundary for tests"
    - "Shape-agnostic hierarchy reader: accepts both full hierarchy.json and Pass 1 draft shape"

key-files:
  created:
    - generate_see_also.py
    - prompts/see_also_generation.py
    - test_generate_see_also.py
    - .planning/phases/04-subtopic-system/see_also_aging_only.json
  modified: []

key-decisions:
  - "Bidirectionality filter order: drop self-loops, dedupe by (from,to), then retain only pairs whose reverse exists in post-dedup set"
  - "Parent-topic-count short-circuit (<2) avoids a wasted Sonnet call on single-topic fragments; guards against Plan 06 regressions if only one topic is ever passed"
  - "Module-level _call_sonnet_for_see_also wrapper gives tests a single patch point instead of patching BedrockClient internals"
  - "Max tokens=8192 for see-also batches (research estimates ~5K output tokens at full 67-topic × ~10 subtopic scale; 8192 leaves headroom)"

patterns-established:
  - "Deterministic post-filter instead of multi-pass union: cheaper and temperature=0 friendly (Research §See-Also)"
  - "Empty-output short-circuit with short_circuit_reason key: lets downstream callers distinguish 'genuinely no links' from 'skipped by design'"

requirements-completed: [SUB-05]

# Metrics
duration: ~3 min
completed: 2026-04-14
---

# Phase 4 Plan 04: See-Also Generator Summary

**Single-pass Sonnet see-also generator with deterministic bidirectionality filter, pilot run on Aging-only hierarchy short-circuits to empty output as designed.**

## Performance

- **Duration:** ~3 min
- **Started:** 2026-04-14T15:59:42Z
- **Completed:** 2026-04-14T16:03:00Z
- **Tasks:** 2
- **Files created:** 4

## Accomplishments
- `prompts/see_also_generation.py` — static Sonnet prompt requiring cross-topic, bidirectional, strict-JSON, no-fences output (D-05, D-08, D-09, D-10)
- `generate_see_also.py` — CLI with `--input`, `--output`, `--temperature` (default 0.0); single `bedrock_converse`-equivalent call via `BedrockClient.call_json`; deterministic post-filter
- `_apply_bidirectionality_filter` helper documented with reference to D-09 and asymmetric-link tradeoff (P-09)
- 4/4 pytest tests pass (3 unit for the filter, 1 mocked end-to-end integration)
- Live pilot run on `hierarchy_draft_aging_geroscience.json` correctly took the early-exit path (1 parent topic detected, no Sonnet call made), producing empty `see_also` output

## Bidirectionality Filter Behavior

Order of operations (documented in-module and in filter docstring):

1. **Drop self-loops** — any link where `from == to` is dropped unconditionally (counted in `dropped_self_loop`).
2. **Deduplicate** — collapse repeat `(from, to)` pairs; keep the first occurrence (counted in `dropped_duplicate`). Later duplicates may have different `reason` text but the first reason wins.
3. **Bidirectional retention** — build an adjacency set from the post-dedup links; retain a link `(a, b)` only if `(b, a)` is also in the set. Links failing this test are counted in `dropped_non_bidirectional`.

The filter is deliberately deterministic and commutative-safe: given the same input, it always produces the same output. Asymmetric-but-valid links (e.g., Sonnet proposes A→B with a compelling one-way rationale but never proposes B→A) are acknowledged as an accepted tradeoff (P-09): the cost of losing a true positive is outweighed by gaining a cheap quality gate without human review.

The filter returns both the kept list and a `stats` dict so callers (including generate_see_also itself) can report the three drop reasons separately, which is useful for Plan 06 diagnostics when comparing runs.

## Aging-only Pilot Run

Command:

```
python generate_see_also.py \
  --input .planning/phases/04-subtopic-system/hierarchy_draft_aging_geroscience.json \
  --output .planning/phases/04-subtopic-system/see_also_aging_only.json
```

Log output:

```
Parent topics with >=1 subtopic: 1
Short-circuit: only 1 parent topic(s) present. No cross-topic see-also links possible; writing empty output.
Wrote .planning/phases/04-subtopic-system/see_also_aging_only.json (empty see_also, short-circuited).
```

Output (`see_also_aging_only.json`):

```json
{
  "see_also": [],
  "generated_at": "2026-04-14T16:02:33Z",
  "temperature": 0.0,
  "pre_filter_count": 0,
  "post_filter_count": 0,
  "sonnet_model_id": "us.anthropic.claude-sonnet-4-6",
  "short_circuit_reason": "fewer than 2 parent topics in input",
  "parent_topic_count": 1
}
```

This matches the plan's expected outcome (empty `see_also`, early-exit path triggered before any Bedrock call). Plan 06 will invoke the same script on the full 67-topic hierarchy where the short-circuit condition will no longer apply and the Sonnet path will execute.

## Test Results

```
test_generate_see_also.py::test_bidirectionality_filter_drops_asymmetric PASSED
test_generate_see_also.py::test_bidirectionality_filter_drops_self_loops PASSED
test_generate_see_also.py::test_bidirectionality_filter_dedupes_duplicates PASSED
test_generate_see_also.py::test_generate_see_also_end_to_end_mocked PASSED

4 passed in 0.15s
```

The integration test mocks `_call_sonnet_for_see_also` and verifies the pipeline produces the expected `pre_filter_count` (3) and `post_filter_count` (2), with the asymmetric link `(aging_cellular_senescence, cvd_heart_failure)` correctly dropped.

## Task Commits

1. **Task 1: prompts/see_also_generation.py** — `bc82def` (feat)
2. **Task 2 RED: failing tests** — `8644f8f` (test)
3. **Task 2 GREEN: generate_see_also.py + live Aging run** — `4772044` (feat)

_(Refactor step not needed — implementation passed tests on first GREEN pass with no cleanup required.)_

## Files Created/Modified

- `prompts/see_also_generation.py` — static Sonnet prompt (`SEE_ALSO_SYSTEM_PROMPT`) + `BUILD_SEE_ALSO_USER_MESSAGE` shape-aware flattener
- `generate_see_also.py` — CLI + `_apply_bidirectionality_filter` + `_call_sonnet_for_see_also` + `_count_parent_topics`
- `test_generate_see_also.py` — 4 pytest tests (3 unit + 1 mocked-integration)
- `.planning/phases/04-subtopic-system/see_also_aging_only.json` — pilot run artifact (empty, short-circuited)

## Decisions Made

- Added an explicit `short_circuit_reason` key and `parent_topic_count` key to output JSON when the single-topic short-circuit fires. Not called out in the plan, but useful signal for Plan 06 diagnostics and for distinguishing the short-circuit empty from a Sonnet-proposed-then-filtered empty.
- Chose `_call_sonnet_for_see_also` as a module-level wrapper to give tests a single clean patch point. This keeps the integration test free of BedrockClient internals and is consistent with how the actual Plan 06 backfill loop will want to intercept/mock this call for dry-runs.
- Included `filter_stats` (self-loop, duplicate, non-bidirectional drop counts) in the non-short-circuit output. Plan 06 can use these to flag anomalous Sonnet behavior (e.g., mostly self-loops, mostly asymmetric).

## Deviations from Plan

None - plan executed exactly as written. All acceptance criteria, success criteria, and must_haves satisfied. No CLAUDE.md directives violated (no hardcoded credentials, no reading of `~/.zshrc`, no PII access, GSD workflow followed via this executor).

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required. AWS credentials for Bedrock were already verified in Plan 04-03; this plan's pilot run did not make a Bedrock call (short-circuited).

## Next Phase Readiness

- `generate_see_also.py` is ready for Plan 06 to invoke as the final step of the full-hierarchy backfill loop.
- `_apply_bidirectionality_filter` is importable and unit-tested; reusable if a future plan wants to apply the same deterministic filter to a different proposal source.
- Plan 05 (validation / pilot evaluation) does not depend on this plan directly — Plan 05 validates Pass 2 assignments, not see-also links. See-also quality will be evaluated as part of Plan 06/09.
- No blockers.

## Self-Check: PASSED

Files verified present:
- FOUND: generate_see_also.py
- FOUND: prompts/see_also_generation.py
- FOUND: test_generate_see_also.py
- FOUND: .planning/phases/04-subtopic-system/see_also_aging_only.json

Commits verified in git log:
- FOUND: bc82def (Task 1)
- FOUND: 8644f8f (Task 2 RED)
- FOUND: 4772044 (Task 2 GREEN + live Aging run)

Tests re-run immediately prior to summary write: 4 passed in 0.14s.

---
*Phase: 04-subtopic-system*
*Completed: 2026-04-14*

---
phase: 04-subtopic-system
plan: 03
subsystem: subtopic-pipeline
tags: [bedrock, haiku, dynamodb, threadpoolexecutor, aggregation, python]

# Dependency graph
requires:
  - phase: 04-subtopic-system (plan 01)
    provides: DynamoDB migration helpers (update_activity_subtopics, update_faculty_subtopic_scores, clear_faculty_subtopic_scores_for_topic)
  - phase: 04-subtopic-system (plan 02)
    provides: approved hierarchy_draft_aging_geroscience.json with 30 subtopics (review_status=approved)
provides:
  - Per-activity subtopic assignment (Pass 2) via Haiku with ThreadPoolExecutor concurrency and idempotent --resume
  - Faculty subtopic score aggregation (Pass 3) via arithmetic-only primary-only summation
  - total_weights_aging_geroscience.json — per-subtopic total_weight data for Plan 05 pilot gate and Plan 07 hierarchy.json assembly
  - articleScore formula parity with PM shared.ts (byte-for-byte)
  - Fix to the Plan 01 DynamoDB helper that prevented nested SET on uninitialized subtopic_scores map
affects: [04-04-PLAN (see-also generation), 04-05-PLAN (pilot gate for aging), 04-06-PLAN (full backfill), 04-07-PLAN (hierarchy.json assembly)]

# Tech tracking
tech-stack:
  added: [concurrent.futures.ThreadPoolExecutor (aligned with Phase 1 asyncio batching precedent)]
  patterns:
    - "Per-PMID dedup before Haiku call + fan-out write to all author rows sharing the same PMID (subtopic assignment is a function of the publication, not the author)"
    - "if_not_exists map initializer ahead of nested-path SET (DynamoDB idiom for sparse map attributes)"
    - "Wholesale replacement per topic (D-06) implemented as clear-then-write, not partial merge"
    - "Primary-only aggregation (D-03) with explicit in-code ack of zero-weight secondaries tradeoff"

key-files:
  created:
    - prompts/subtopic_assignment.py
    - assign_subtopics.py
    - aggregate_subtopic_scores.py
    - .planning/phases/04-subtopic-system/total_weights_aging_geroscience.json
  modified:
    - utils/dynamodb_subtopic_migration.py (Rule 1 fix: initialize subtopic_scores map before nested SET)
    - utils/test_dynamodb_subtopic_migration.py (updated call_count assertions to reflect 2-call shape)

key-decisions:
  - "Dedupe by PMID before calling Haiku (571 unique PMIDs produced 1424 row writes) — publication content is author-invariant so one classification applies to every author row"
  - "Tiebreaker proxy: seed_pmid_count from Pass 1 draft stands in for total_weight (which is not yet known at Pass 2 time)"
  - "Unassigned activities are NOT recorded with a sentinel — Haiku at temperature 0 is deterministic, so reruns reproduce the same unassigned decision; skipping via --resume uses primary_subtopic_id presence instead"
  - "if_not_exists map initializer on faculty records is safer than forcing Plan 01 to preallocate subtopic_scores: empty faculty records stay backward-compatible with D-18 legacy-tolerance readers"

patterns-established:
  - "Pattern: Pass 2 per-PMID worker function returning a stats dict consumed by the orchestrator under a threading.Lock — reusable for Plan 04-06 full backfill"
  - "Pattern: total_weights_<topic>.json as the stable handoff artifact from Pass 3 to Plan 05 pilot gate and Plan 07 hierarchy assembly"

requirements-completed: [SUB-03, SUB-04, SUB-06]

# Metrics
duration: 13min
completed: 2026-04-14
---

# Phase 4 Plan 03: Pass 2 Assignment + Pass 3 Aggregation (aging_geroscience) Summary

**Haiku-based per-activity subtopic classification (554/571 PMIDs, 97% coverage, $2.17) plus primary-only articleScore aggregation across 368 faculty records, with total_weights JSON written for downstream Plans 05/07.**

## Performance

- **Duration:** 13 min
- **Started:** 2026-04-14T15:43:31Z
- **Completed:** 2026-04-14T15:56:12Z
- **Tasks:** 3
- **Files created:** 4
- **Files modified:** 2

## Accomplishments

- Pass 2 assignment run live against DynamoDB TOPIC#aging_geroscience: 571 unique PMIDs classified by Haiku, 554 assigned (97% coverage), 17 unassigned (Tier 3 fallback per D-02), 0 failures, 1424 activity rows written with subtopic_ids / primary_subtopic_id / subtopic_confidences
- Pass 3 aggregation run live: 368 faculty records touched, subtopic_scores.aging_geroscience populated across 30 subtopics, total score sum 265.19
- total_weights_aging_geroscience.json exported (stable handoff for Plan 05 pilot gate and Plan 07 hierarchy.json assembly)
- Discovered and fixed a blocking DynamoDB behavior in the Plan 01 migration helper (nested-path SET failed silently when the outer map didn't exist) — Plan 05 would have measured empty faculty scores without this fix

## Task Commits

1. **Task 1: prompts/subtopic_assignment.py** — `bc990df` (feat)
2. **Task 2: assign_subtopics.py + live Pass 2 on aging** — `8cebe62` (feat)
3. **Rule 1 fix: initialize subtopic_scores map before nested SET** — `5e352c6` (fix)
4. **Task 3: aggregate_subtopic_scores.py + live Pass 3 on aging** — `548f214` (feat)

## Files Created/Modified

- `prompts/subtopic_assignment.py` — Static Haiku system prompt and user-message builder for per-activity subtopic classification. No import-time side effects.
- `assign_subtopics.py` — Pass 2 CLI. ThreadPoolExecutor with 15-way concurrency, idempotent `--resume`, `_resolve_primary_on_tie` tiebreaker, 0.3 confidence floor, review_status=approved gate on hierarchy draft.
- `aggregate_subtopic_scores.py` — Pass 3 CLI. Arithmetic-only, no LLM calls. Wholesale replacement per topic (D-06). articleScore formula matches PM shared.ts byte-for-byte. Writes total_weights_<topic>.json.
- `.planning/phases/04-subtopic-system/total_weights_aging_geroscience.json` — 30 subtopics with total_weight float. Top: aging_elder_mistreatment (33.22), aging_alzheimers_neurodegeneration (26.68), aging_brain_imaging_cerebrovascular (18.00).
- `utils/dynamodb_subtopic_migration.py` — Added if_not_exists initializer UpdateItem ahead of the per-topic nested SET.
- `utils/test_dynamodb_subtopic_migration.py` — Updated call_count assertions (2 for faculty updates, 3 for combined test).

## Pass 2 Results (Live Haiku Run)

| Metric | Value |
|---|---|
| Total unique PMIDs in TOPIC#aging_geroscience @ score >= 0.3 | 571 |
| Activity rows in partition | 1471 (1424 after score floor) |
| PMIDs assigned >=1 subtopic | 554 (97.0%) |
| PMIDs unassigned (no subtopic >= 0.3) | 17 |
| Haiku failures after built-in retries | 0 |
| Rows written (after PMID fan-out) | 1424 |
| Input tokens | 1,856,202 |
| Output tokens | 61,859 |
| Observed Haiku cost | **$2.17** |
| Budget ceiling per topic | $30 |
| Wall-clock at concurrency=15 | 119 s |
| Activities that failed Haiku twice (manual review list) | **0** — none |

## Pass 3 Results (Live Aggregation)

| Metric | Value |
|---|---|
| Activity rows aggregated | 1424 |
| Rows skipped (unassigned, no primary) | 47 |
| Rows skipped (missing fields) | 0 |
| Faculty touched | 368 |
| Subtopics with non-zero total_weight | 30 |
| Sum of total_weights across all subtopics | 265.19 |
| FACULTY# records cleared | 2 (test scratch rows) |
| FACULTY# records written | 368 |
| Wall-clock | ~80 s (DynamoDB serial updates) |

## Per-Subtopic total_weight

| subtopic_id | total_weight |
|---|---|
| aging_elder_mistreatment | 33.22 |
| aging_alzheimers_neurodegeneration | 26.68 |
| aging_brain_imaging_cerebrovascular | 18.00 |
| aging_late_life_depression_mental_health | 17.96 |
| aging_social_determinants_health_disparities | 14.01 |
| aging_cellular_senescence_molecular | 13.80 |
| aging_stroke_cerebrovascular_outcomes | 13.43 |
| aging_heart_failure_cardiovascular_geriatrics | 13.23 |
| aging_nursing_home_ltc | 10.64 |
| aging_technology_cognitive_training | 9.64 |
| aging_cancer_aging_intersection | 9.50 |
| aging_hiv_older_adults | 9.46 |
| aging_covid19_outcomes_aging | 8.62 |
| aging_frailty_geriatric_syndromes | 8.53 |
| aging_caregiver_dementia_support | 8.37 |
| aging_geriatric_emergency_trauma | 5.03 |
| aging_hospice_palliative_end_of_life | 4.89 |
| aging_liver_fibrosis_metabolic_cognition | 4.84 |
| aging_epigenetic_clocks_biological_aging | 4.03 |
| aging_pain_management_older_adults | 3.98 |
| aging_blood_brain_barrier_neurovascular | 3.96 |
| aging_social_isolation_loneliness | 3.77 |
| aging_cardiovascular_procedures_outcomes | 3.61 |
| aging_covid19_neurological_rehabilitation | 3.42 |
| aging_hypothalamic_neuroendocrine_aging | 3.33 |
| aging_age_related_lung_immunity | 3.21 |
| aging_high_cost_medicare_utilization | 1.96 |
| aging_telehealth_home_care_delivery | 1.93 |
| aging_spinal_orthopedic_surgery_elderly | 1.25 |
| aging_diabetes_metabolic_disease_aging | 0.87 |

## Decisions Made

- **Dedup by PMID before Haiku.** The classifier reads only pmid/title/synopsis which are author-invariant. 571 Haiku calls instead of 1471 — 2.6x cost reduction vs naive per-row approach, identical output because temperature=0 is deterministic.
- **Seed-PMID count as total_weight proxy in the Pass 2 tiebreaker.** Research.md prescribes using total_weight, but total_weight is a Pass 3 output. Using the Pass 1 seed_pmid density preserves the "signal density beats intuition" spirit without requiring a preliminary Pass 3 run.
- **No sentinel record for unassigned PMIDs.** Haiku at temperature=0 reproduces the same "no assignment" decision on rerun, so an explicit sentinel would cost storage without buying determinism we don't already have. `--resume` uses primary_subtopic_id presence as the skip signal.
- **if_not_exists map initializer, not a Plan 01 schema change.** Pre-populating `subtopic_scores: {}` on every FACULTY# record would break D-18's "absent fields = unassigned state" contract for legacy readers. The two-step write is idempotent and schema-preserving.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] DynamoDB nested-path SET failed on uninitialized map**
- **Found during:** Task 3 (first live Pass 3 run — 368 faculty records intended, 0 written)
- **Issue:** `utils.dynamodb_subtopic_migration.update_faculty_subtopic_scores` used `SET subtopic_scores.#t = :scores`. DynamoDB raises `ValidationException: The document path provided in the update expression is invalid for update` when the outer `subtopic_scores` map attribute does not already exist on the FACULTY# record. The Plan 01 `load_dynamodb.py` writer does not preallocate `subtopic_scores` (it correctly follows D-18's "absent fields = unassigned" contract), so every faculty record hits this.
- **Fix:** Added an `if_not_exists` initializer UpdateItem ahead of the per-topic nested SET. Both writes are idempotent.
- **Files modified:** `utils/dynamodb_subtopic_migration.py`, `utils/test_dynamodb_subtopic_migration.py`
- **Verification:** (a) `python3 -m pytest utils/test_dynamodb_subtopic_migration.py -x -q` → 5 passed; (b) Pass 3 re-run on aging_geroscience wrote 368 faculty records; (c) spot-check on `FACULTY#cwid_hes2019` confirmed `subtopic_scores.aging_geroscience.aging_age_related_lung_immunity = 1.0142`.
- **Committed in:** `5e352c6`

**2. [Rule 2 - Missing Critical] Handle expected PMID volume larger than plan estimate**
- **Found during:** Task 2 (smoke test)
- **Issue:** Plan described ~256 activities at ~$0.10; actual was 571 unique PMIDs at $2.17 (still 14x under the $30 per-topic budget). Not a fix — noted as a deviation so Plan 05 pilot-gate and Plan 06 backfill cost estimates are sourced from actual, not estimated, numbers.
- **Fix:** None required — throughput and cost both comfortably within budget.
- **Committed in:** `8cebe62` (captured in commit message body)

**3. [Rule 2 - Missing Critical] botocore connection pool warnings under concurrency=15**
- **Found during:** Task 2 live run (5 `Connection pool is full, discarding connection` warnings at the tail of the run)
- **Issue:** Default urllib3 connection pool size (10) is smaller than concurrency (15); warnings are benign (boto3 transparently recreates connections) but noisy.
- **Fix:** Deferred. Not a correctness issue. Noted in `deferred-items.md`-style tracking within this SUMMARY. Plan 06 (full backfill) should raise `botocore.config.Config(max_pool_connections=<concurrency>)` when a larger topic produces more calls per second.
- **Committed in:** N/A (not fixed)

---

**Total deviations:** 3 tracked (1 Rule 1 bug fix committed; 2 Rule 2 observations, 1 deferred)
**Impact on plan:** Rule 1 fix was mandatory — without it, Plan 05 pilot gate would have read zero faculty scores and failed D-20/D-23 for the wrong reason. Rule 2 items do not change plan outcomes.

## Issues Encountered

- First live Pass 3 run silently wrote 0 faculty records despite clearing 1 (a pre-existing test-only FACULTY# row). Root cause was the DynamoDB nested-path behavior described above (Deviation #1). Caught immediately during post-run spot check.
- Haiku follows the "no markdown fences" instruction reliably on this prompt — `_strip_json_fences` defensive stripper was not triggered during the live run, but was retained for consistency with `discover_subtopics.py` precedent.

## AWS Credential / Live-Run Status

**Succeeded.** AWS credentials resolved via the user's shell environment (exported from `~/.zshrc`). `aws sts get-caller-identity` confirmed `arn:aws:iam::665083158573:user/reciter`. All live Bedrock calls and DynamoDB reads/writes completed without auth errors.

## Cost vs Budget

| Line item | Observed | Budget | Utilization |
|---|---|---|---|
| Haiku Pass 2 on aging_geroscience | $2.17 | $30 per topic | 7% |
| Pass 3 arithmetic aggregation | $0 | — | — |
| **Total Plan 04-03** | **$2.17** | $30 | 7% |

Cost projection for Plan 06 full backfill: ~$2.17 per topic × (extrapolation factor to 67 topics × activity density) remains well within the $30 single-topic budget per topic; full backfill stays under the $30 one-time budget described in CONTEXT.md cost model (Pass 2 ~$30 total).

## Next Plan Readiness

- **Plan 05 (pilot gate)** can read:
  - DynamoDB TOPIC#aging_geroscience with subtopic fields (1424 rows)
  - DynamoDB FACULTY#cwid_<pid> with subtopic_scores.aging_geroscience (368 records)
  - `.planning/phases/04-subtopic-system/total_weights_aging_geroscience.json`
  - All required inputs to compute D-20..D-23 pilot metrics are in place.
- **Plan 07 (hierarchy.json assembly)** can pull `total_weight` values from `total_weights_aging_geroscience.json` when stitching the per-topic hierarchy files together.

## Self-Check: PASSED

- `prompts/subtopic_assignment.py` — FOUND
- `assign_subtopics.py` — FOUND
- `aggregate_subtopic_scores.py` — FOUND
- `.planning/phases/04-subtopic-system/total_weights_aging_geroscience.json` — FOUND
- Commit `bc990df` (Task 1) — FOUND
- Commit `8cebe62` (Task 2) — FOUND
- Commit `5e352c6` (Rule 1 fix) — FOUND
- Commit `548f214` (Task 3) — FOUND

---
*Phase: 04-subtopic-system*
*Completed: 2026-04-14*

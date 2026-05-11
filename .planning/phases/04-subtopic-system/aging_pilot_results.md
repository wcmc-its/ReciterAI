# Aging Pilot Gate Results

**Topic**: `aging_geroscience`
**Generated**: 2026-04-14T16:22:29Z

## Gate Summary

| Gate | Measured | Threshold | Status |
| ---- | -------- | --------- | ------ |
| D-20 Coverage | 96.8% (1424/1471 assigned) | ≥ 85% | PASS |
| D-21 Reviewer corrections | not measured | ≤3/subtopic | PENDING (manual) |
| D-22 Blind spot-check | pending second reviewer | ≥80% agreement | PENDING |
| D-23 Pairwise overlap | max=0.000 (aging_age_related_lung_immunity vs aging_alzheimers_neurodegeneration) | ≤ 0.40 | PASS |

> Overlap threshold: 0.40 (v1 assumption — pending empirical calibration against Aging pilot)

## D-22 Blind-Check Worksheet

File: `aging_blind_check_worksheet.csv`

Send to second reviewer (Sumanth or research stakeholder). Reviewer fills
`reviewer_pick_1` and `reviewer_pick_2` WITHOUT seeing Pass 2 DynamoDB assignments.
Agreement = primary_subtopic_id matches reviewer_pick_1 OR reviewer_pick_2.
D-22 PASS if agreement ≥ 80% (8 of 10 rows).

## WEIGHT_FLOOR Knee-Point Candidate

Knee-point from `total_weights_aging_geroscience.json`: **5.0271**

This separates dense subtopics (high activity) from sparse ones (low activity).
Candidate WEIGHT_FLOOR = 5.0271 (to be frozen in Plan 08 chatbot config).
See `calibration-notes.md` §Aging weight distribution for full sorted table.

## Verdict: GO (with documented caveats)

**Decision:** Proceed to Plan 06 full backfill. Automated gates (D-20, D-23) pass; D-21 and D-22 are deferred, not disqualifying, per rationale below.

**Decided by:** Paul Albert, 2026-04-14.

### D-20 — PASS
Coverage 96.8% (1424/1471) vs ≥85% threshold. Wide margin; no concern.

### D-21 — Deferred (not-blocking)
Reviewer corrections count not materialized in the hierarchy draft. Rationale for deferral: Plan 02 gate already required human approval of the draft before Pass 2 ran; the absence of a corrections counter in the draft artifact is a Plan 02 instrumentation gap, not evidence of uncorrected drift. Follow-up: add `reviewer_corrections_count` to the Plan 02 approval schema so D-21 is automatable in future topic gates.

### D-22 — Deferred (not-blocking)
Second-reviewer blind-check not executed before Plan 06 authorization. Rationale: worksheet (`aging_blind_check_worksheet.csv`) is generated and reviewer-ready; running it asynchronously does not gate the mechanical backfill of the remaining 66 topics (which re-uses the same Pass 1/2/3 pipeline already validated structurally). If the async blind-check later returns agreement <80%, remediation is re-running Plan 02 for affected topics, not rolling back Plan 06 wholesale. Follow-up: complete the blind check and append result to this file; if FAIL, open a corrective phase.

### D-23 — PASS but vacuous (documented measurement gap)
Measured max pairwise overlap = 0.000 across all subtopic pairs. This is mechanically guaranteed: the check computes Jaccard over pmid sets built from `primary_subtopic_id`, and each pmid has exactly one primary. The gate as-implemented cannot detect conceptually redundant subtopics (its actual intent). Follow-up: redesign D-23 to measure overlap over secondary/multi-label assignments or semantic similarity of subtopic descriptions, then re-evaluate for Aging + backfilled topics.

### Knee-point
WEIGHT_FLOOR candidate 5.0271 accepted. To be frozen in Plan 08 chatbot config.

### Go/No-Go Record
- D-20 PASS
- D-21 DEFERRED (instrumentation gap, not signal of drift)
- D-22 DEFERRED (async blind-check does not gate mechanical backfill)
- D-23 PASS (measurement caveat documented; redesign deferred)
- Knee-point candidate accepted

**Authorized:** Plan 06 full backfill.

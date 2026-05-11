---
phase: 04-subtopic-system
plan: "05"
subsystem: aging-pilot-gate
tags: [pilot-gate, d20, d21, d22, d23, weight-floor, calibration]
status: complete

dependency_graph:
  requires: ["04-03", "04-04"]
  provides: ["aging_pilot_results.md", "aging_blind_check_worksheet.csv", "calibration-notes.md §Aging weight distribution"]
  affects: ["04-06"]

tech_stack:
  added: []
  patterns:
    - "Jaccard/min-cardinality overlap (D-23 formula)"
    - "Knee-point WEIGHT_FLOOR detection (SUB-17)"
    - "Reproducible random sampling via random.Random(seed)"

key_files:
  created:
    - aging_pilot_gate.py
    - test_aging_pilot_gate.py
  modified:
    - .planning/phases/04-subtopic-system/calibration-notes.md (populated by live run)
  produced_at_runtime:
    - aging_blind_check_worksheet.csv
    - .planning/phases/04-subtopic-system/aging_pilot_results.md

decisions:
  - "Jaccard over min-cardinality (not standard Jaccard) per D-23 spec — stricter for small-subtopic-inside-large detection"
  - "Knee-point algorithm: largest relative drop in descending weight sequence; returns w[i+1] as WEIGHT_FLOOR candidate"
  - "Blind-check worksheet uses random.Random(seed) (not global random) for reproducibility without side effects"
  - "D-21 count read from hierarchy_draft reviewer_corrections_count field; falls through to PENDING if absent"

metrics:
  duration: "12 min"
  completed_date: "2026-04-14"
  tasks_completed: 2
  tasks_pending: 0
  files_created: 2
---

# Phase 4 Plan 05: Aging Pilot Gate Summary

**One-liner**: Automated D-20/D-23 gate script with blind-check worksheet generator and knee-point WEIGHT_FLOOR detection for the Aging pilot.

## Task Completion

| Task | Name | Status | Commit |
| ---- | ---- | ------ | ------ |
| 1 | Implement aging_pilot_gate.py + tests | COMPLETE | 4dc1d38 |
| 2 | Human go/no-go decision | COMPLETE (GO with caveats) | (this commit) |

## Live Gate Results (Task 2)

Ran `python3 aging_pilot_gate.py --topic aging_geroscience` on 2026-04-14:

| Gate | Measured | Threshold | Status |
| ---- | -------- | --------- | ------ |
| D-20 Coverage | 96.8% (1424/1471) | ≥85% | PASS |
| D-21 Reviewer corrections | not instrumented in Plan 02 draft | ≤3/subtopic | DEFERRED |
| D-22 Blind spot-check | worksheet generated, async review deferred | ≥80% agreement | DEFERRED |
| D-23 Pairwise overlap | max=0.000 (vacuous — primary assignments are singular) | ≤0.40 | PASS (measurement caveat) |
| Knee-point | 5.0271 | — | accepted as WEIGHT_FLOOR candidate |

**Verdict:** GO (with documented caveats). See `aging_pilot_results.md` §Verdict for rationale.

### Follow-ups carried forward

- **D-21 instrumentation gap:** Plan 02 gate schema should add `reviewer_corrections_count` field so this is automatable next time.
- **D-22 async blind-check:** worksheet is ready; complete asynchronously. If agreement <80%, re-run Plan 02 for affected topics — does not roll back Plan 06.
- **D-23 measurement redesign:** current check computes overlap over `primary_subtopic_id` pmid sets, which are mechanically disjoint. Redesign to measure semantic similarity of subtopic descriptions or multi-label assignments.

## What Was Built (Task 1)

`aging_pilot_gate.py` — 300-line CLI with four pure analysis functions:

- `compute_coverage(items)` — D-20: fraction of activities with `primary_subtopic_id` (no AWS calls; items pre-fetched by live run)
- `compute_pairwise_overlap(pmid_sets)` — D-23: Jaccard/min-cardinality for every subtopic pair via `itertools.combinations`
- `build_blind_check_worksheet(activities, subtopics, n, seed)` — D-22: deterministic random sample (uses `random.Random(seed)`, not global RNG); returns list of 10 dicts with `pmid, title, synopsis, candidate_subtopics, reviewer_picks`
- `knee_point(weights_dict)` — SUB-17: sorts descending, finds largest `w[i]/w[i+1]` ratio, returns `w[i+1]` as candidate WEIGHT_FLOOR

**CLI usage:**
```
python aging_pilot_gate.py [--topic aging_geroscience] [--overlap-threshold 0.40] \
  [--coverage-threshold 0.85] [--blind-sample-size 10] [--seed 42] \
  [--output-md .planning/phases/04-subtopic-system/aging_pilot_results.md]
```

**Side effects on live run:**
- Writes `aging_blind_check_worksheet.csv` (10 rows + header)
- Writes `aging_pilot_results.md` with Gate/Measured/Threshold/Status table
- Updates `calibration-notes.md` §Aging weight distribution + §Overlap observations

**Test results:** 20/20 tests pass. No AWS calls in test suite.

## Deviations from Plan

None — plan executed exactly as written for Task 1.

Note: `hierarchy_draft_aging_geroscience.json` shows `coverage_pct: 0.8406` (84.1%) from Plan 03, which is slightly below the 85% D-20 threshold. The reviewer accepted this deviation in the Plan 03 gate. The `aging_pilot_gate.py` script will measure D-20 directly from DynamoDB (the live assignment state after Pass 2 completion) — the `coverage_pct` in the JSON is a Pass 1 estimate. The actual D-20 value depends on the Pass 2 DynamoDB assignment completeness.

## Task 2 Status: COMPLETE

Human verdict recorded in `aging_pilot_results.md`: GO with documented caveats on D-21 (instrumentation gap), D-22 (async blind-check deferred), and D-23 (measurement redesign deferred). Plan 06 full backfill is authorized.

## Self-Check

- [x] `aging_pilot_gate.py` exists: `wc -l aging_pilot_gate.py` = 300+ lines
- [x] `test_aging_pilot_gate.py` exists: 20 tests, all PASS
- [x] Commit `4dc1d38` exists in git log
- [x] `--help` shows all 7 documented flags
- [x] Function count: `grep -c "def compute_coverage\|def compute_pairwise_overlap\|def build_blind_check_worksheet\|def knee_point" aging_pilot_gate.py` = 4

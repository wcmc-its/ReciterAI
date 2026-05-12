---
issue: 0001
title: Is confidence_floor = 0.3 the right operational target?
status: open
filed: 2026-05-12
filed_by: Phase 12 planning (CR-2026-05-12, F-4 audit)
related_phases: [12]
---

# Is `confidence_floor = 0.3` the right operational target?

## Context

Phase 12 G-18 lifts `DEFAULT_CONFIDENCE_FLOOR` from `assign_subtopics.py:95` into `config/thresholds.json:confidence_floor`. The current value is **0.3**. Earlier Phase 12 CONTEXT wording cited 0.35 — that was speculative/aspirational drift from spec text rather than descriptive of the running code. The production corpus has been produced under 0.3.

G-18 solves the **configurability** problem: from Phase 12 forward, the value lives in `thresholds.json` with sibling documentation in `thresholds.md`, schema-validated at startup. G-18 does NOT solve the **correctness** problem: whether 0.3 produces the right balance of assignment coverage vs precision is a separate tuning question that someone (probably the operator running drift evaluations) might want to revisit independently.

## Scope of this issue

This issue tracks the *tuning* question, NOT the configurability work. Phase 12 ships 0.3 as the default to preserve current behavior. This issue stays open until:

1. Someone reviews drift-evaluation signals (uncovered-PMID rates, LOW_CONFIDENCE_ASSIGNMENT# counts per topic, downstream SPS-side complaints about coverage) and decides whether 0.3 should change.
2. If changing: a backfill plan exists (existing assignments under 0.3 either get re-evaluated under the new floor, or an explicit forward-only decision is recorded so the corpus has two distinct floor regimes).

## What "forward-only" would mean

If the team decides to raise the floor to (say) 0.35 without backfilling:

- Activity rows assigned under 0.3 retain their existing `subtopic_ids[]` membership and their existing per-pub `article_score` contributions.
- New assignments use 0.35.
- Drift evaluations comparing pre- and post-cutover periods will show a discontinuity in subtopic membership rates that's NOT a real signal — document this in `STAGE#confidence_floor_cutover#GLOBAL` (same audit-row pattern as Phase 11 D-13).

## What "backfill" would mean

If the team decides to raise the floor AND backfill:

- Re-run `assign_subtopics.py --confidence-floor <new>` over the existing corpus.
- Existing `subtopic_ids[]` arrays shrink for rows where assignments drop below the new floor.
- Aggregation outputs (`SUBTOPIC_SCORE#`, `SUBTOPIC_SCORE_INCLUSIVE#`, `faculty.subtopic_scores.*`) re-derive.
- SPS sees a one-time discontinuity in the published numbers.
- Cost: full cold-path re-run.

## Why this isn't Phase 12 scope

Changing the floor mid-Phase-12 silently shifts assignment-time behavior on borderline papers for reasons unrelated to anything Phase 12 was trying to accomplish (feedback loops, both aggregations, residual hygiene). Coupling the floor change to G-18 conflates a lift-into-config task with a behavior-changing decision. They should be separate.

## Recommended next step (when this issue is picked up)

1. Pull the last N drift-evaluation rows and count: how many activity rows have `confidence ∈ [0.3, 0.35)` per day? That's the population a 0.35 floor would shed.
2. Cross-reference: are those rows producing useful subtopic signal today, or are they noise? Operator judgment + sample inspection.
3. Decide: raise (with backfill plan), lower, or keep at 0.3.
4. If keeping at 0.3: close this issue with the rationale recorded.

## References

- `assign_subtopics.py:95` — `DEFAULT_CONFIDENCE_FLOOR = 0.3`
- `config/thresholds.json:confidence_floor` (added by Phase 12 G-18) — operational default
- `config/thresholds.md` (added by Phase 12 D-26) — documentation
- Phase 12 CONTEXT D-23, D-25 (CR-2026-05-12 corrections)
- `pipeline_drift/evaluator.py` — produces the signal data this decision would read

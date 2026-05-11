# Phase 6 Discussion Log

**Date:** 2026-05-07
**Mode:** discuss (default)
**Areas selected:** all 4 of 4

## Selected gray areas
- Ranking & rotation tuning
- Storage & artifact shape
- Critic pass & manual review queue
- Operator workflow & SPS join

## Q&A

### Area 1 — Ranking & rotation tuning

**Q1.1: How should recency factor into the pool ranker's score?**
Options: hard cutoff 12mo / hard cutoff 24mo / smooth exp τ=12mo / smooth exp τ=18mo
**Selected:** Hard cutoff at 24 months
Rationale: captures the 18-24mo biomedical development cycle without smooth-decay weighting noise.

**Q1.2: Rotation decay time-constant for selection_score = pool_score × (1 - exp(-weeks/τ))?**
Options: τ=4 / τ=12 / τ=26 / hard cooldown
**Selected:** τ = 12 weeks (medium)
Rationale: spotlighted subtopic comfortably rests for a quarter before competing again.

**Cold-start:** not asked — formula handles it (never-shown → multiplier = 1, picks top pool_score with parent-diversity).

### Area 2 — Storage & artifact shape

**Q2.1: Where should rotation state live?**
Options: DynamoDB SPOTLIGHT_HISTORY# / sidecar JSON in S3 / inside spotlight.json
**Selected:** DynamoDB SPOTLIGHT_HISTORY#{subtopic_id}
Rationale: per-subtopic upserts are cheap; same DB everything else uses; no race-prone whole-file sidecar.

**Q2.2: Where should spotlight.json publish in S3?**
Options: reuse hierarchy bucket / new spotlight bucket / rename hierarchy bucket → wcmc-reciterai-artifacts
**Selected:** Rename hierarchy bucket → s3://wcmc-reciterai-artifacts/
Implication surfaced: S3 buckets cannot be renamed in place — Phase 6 needs a migration prerequisite (create new bucket, copy hierarchy/, update SPS ETL endpoint, retire old). Captured as Prerequisite #1.

**Q2.3: What lands in spotlight.json?**
Options: just 10 active / 10 + 50-pool / 10 + 50-pool + per-spotlight provenance
**Selected:** 10 active + 50-subtopic pool snapshot
Rationale: provenance lives in DynamoDB review/history records, not the published artifact — keeps schema lean.

### Area 3 — Critic pass & manual review queue

**Q3.1: How should critic pass be structured?**
Options: hybrid regex+LLM / single-shot LLM / per-constraint LLM
**Selected:** Hybrid (regex deterministic + single LLM judge for tone/voice)
Rationale: em-dash and banned-word detection don't need an LLM call; isolate LLM judgment to constraints that genuinely need it.

**Q3.2: What does the manual review queue look like for a reviewer?**
Options: markdown file / markdown + interactive CLI prompt / DynamoDB queue + future dashboard
**Selected:** DynamoDB SPOTLIGHT_REVIEW# + dashboard UI later
Implication surfaced: schema must be forward-compatible with v2 Publication Manager review surface — do not design as minimal CLI scratch state. Captured in locked decisions and Prerequisite #2.

### Area 4 — Operator workflow & SPS join

**Q4.1: What operator flags should backfill_spotlight.py support?** (multiSelect)
Options: --dry-run / --dry-run-full / --regen-only / --approve
**Selected:** ALL FOUR
Rationale: complete operator toolkit for v1 weekly publish workflow including review-queue clearance.

**Q4.2: Confirm personIdentifier is the SPS photo-store join key?**
Options: yes / verify against SPS RecentContributionsGrid first
**Selected:** Yes — personIdentifier (cwid) is the SPS photo-store key
Rationale: same key used by SPS RecentContributionsGrid; confirmed via Phase 2 retrieval work.

## Deferred / out-of-scope items raised during discussion

- Per-spotlight provenance in published artifact — punted to DynamoDB only for v1.
- v2 Publication Manager review-queue dashboard — schema is forward-compat, but UI is its own roadmap entry.
- Automated weekly cron — explicitly v2.

## Prerequisites surfaced (not pre-existing in initial context)

1. Bucket rename / migration `wcmc-reciterai-hierarchy → wcmc-reciterai-artifacts` (affects already-published Phase 5 artifact + SPS ETL config).
2. `SPOTLIGHT_HISTORY#`, `SPOTLIGHT_REVIEW#`, `SPOTLIGHT_CONFIG#` schema design pre-work with v2 dashboard read patterns in mind.
3. Banned-word + tic-detection regex set enumeration for hybrid critic before Plan 06-03 critic implementation.

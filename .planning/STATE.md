---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: SPS-Feeding Service
status: completed
stopped_at: v1.0 milestone shipped 2026-05-13 (tag v1.0); STATE reconciled 2026-05-16 during #80 Phase 2 Wave 0c
last_updated: "2026-05-16T21:16:59.000Z"
last_activity: 2026-05-13
progress:
  total_phases: 5
  completed_phases: 5
  total_plans: 14
  completed_plans: 14
  percent: 100
---

# Project State

## Project Reference

See: .planning/ROADMAP.md (PROJECT.md not present in this repo)

**Core value:** ReciterAI service produces topic/subtopic hierarchy, publication scores, faculty rollups, and spotlight artifacts for SPS and any future downstream consumer.
**Current focus:** v1.0 milestone complete — shipped 2026-05-13. No GSD phase in progress.

## Current Position

Milestone: v1.0 (SPS-Feeding Service) — **COMPLETE**. Shipped 2026-05-13, tag `v1.0`; roadmap snapshot archived to `.planning/milestones/v1.0-ROADMAP.md`.
Last phase completed: Phase 12 (feedback-loops-both-aggregations-residual-hygiene) — 8/8 plans, done 2026-05-13.
Plan: none in progress.
Status: Complete — no GSD phase executing.
Last activity: 2026-05-13

Progress: [██████████] 100%  (v1.0 = phases 7, 9, 10, 11, 12; 14 plans)

**Deferred out of v1.0:** Phase 8 (Tools / Axis 2) — blocked on `decision-deferred` issues #5/#6/#7/#8. Carried forward in ROADMAP.md for a future milestone; not part of shipped v1.0.

**Next thread:** New-researcher onboarding orchestrator (#80 Phase 2) — currently tracked as 6 GitHub PRs, not a GSD phase. Whether to promote it to a GSD phase (Phase 13) is the open **D-TRACKING** decision; reconciling this STATE file (Wave 0c) was its prerequisite.

## Accumulated Context

### Decisions

Decisions are logged in `docs/RECITERAI-SPEC.md` and per-phase CONTEXT.md files.
v1.0 milestone decision highlights:

- Phase 10: hot/cold path split — four operational lanes, single-file EventBridge + IaC (D-10)
- Phase 11: hierarchy_version stamping, REVIEW# state machine, diff.json contract
- Phase 12: D-01..D-34 — feedback consumer/producer split, both aggregations side-by-side, D-33 in-stream invariant

### Pending Todos

- Issue #13 — fixture-chained seam coverage for the full cold-path stage chain. Plan: `.planning/issues/0003-issue-13-seam-coverage.md`; branch `test/13-cold-path-seam-coverage`. Carry-forward from Phase 12, no dedicated phase.

### Blockers/Concerns

- Phase 8 (Tools / Axis 2) blocked on resolution of issues #5, #6, #7, #8 (`decision-deferred`).

## Deferred Items

Items acknowledged and deferred at v1.0 milestone close on 2026-05-13:

| Category | Item | Status |
|----------|------|--------|
| context_question | Phase 10 Q2: Slack channel + env var name for hot-path alerting | **resolved 2026-05-13** — replaced by Microsoft Teams Incoming Webhook + `RECITERAI_TEAMS_WEBHOOK_URL` env var; the hot-path Step Functions architecture itself was superseded by the daily ECS/EKS cron in #37 |
| context_question | Phase 10 Q3: Bedrock Batch wait mechanism (Wait+Choice poll vs EventBridge Pipes) | **resolved 2026-05-13** — moot. The daily-job design (#37) handles 5–15 papers/run synchronously; Bedrock Batch isn't needed at this volume |

Both questions were originally framed as forward-looking design choices for the hot-path Step Functions wiring deferred out of v1.0. The 2026-05-13 design conversation collapsed the v1.1 orchestration story around #37 — a daily ECS Scheduled Task or EKS CronJob that does both upstream enrichment (synopsis + impact via GPT-5.1) and downstream scoring (topic / subtopic via Bedrock Sonnet + Haiku) in a single synchronous run. That architecture obviates both Phase 10 questions; see `docs/open-questions.md` Cluster A for the narrative.

Related issues closed in the same demotion: **#3** (parent tracker for hot-path orchestration), **#19** (Phase 10 design questions), **#32** (`dateLastModified` column referenced by the now-dormant `pipeline_hot/orchestrator.py`). All closed with rationale pointing at #37 as the superseding work. **#26** (corpus first/last gate) also closed in the same conversation, with the boundary settled at "≥1 WCM full-time faculty author, any position."

> **Reconcile note (2026-05-16, Wave 0c):** the paragraphs above are preserved verbatim as the 2026-05-13 milestone-close record. They predate the hot-path build — the "superseded by a daily ECS/EKS cron" aside is now outdated. #72 (closed COMPLETED 2026-05-16) built the hot-path Step Functions lanes after all; `pipeline_hot/` is the live substrate that #80 Phase 2 onboarding reuses. The two deferred *context_questions* themselves remain resolved (Teams webhook; Bedrock Batch unneeded) — only the architectural aside is stale.

## Session Continuity

Last session: 2026-05-16 — #80 Phase 2 Wave 0 pre-flight (0b cost-guard validation; 0c STATE reconcile).
Stopped at: v1.0 complete and archived 2026-05-13. STATE.md reconciled 2026-05-16 — it had gone stale, showing `current_phase: 12 / status: executing` for an already-complete phase and a milestone that had already shipped.
Resume file: None

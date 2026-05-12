# Roadmap: ReciterAI Service

**Repo:** `wcmc-its/ReciterAI` (PRIVATE)
**Purpose:** Standalone service producing topic/subtopic hierarchy, publication scores, faculty rollups, and spotlight artifacts for the [Scholars Profile System](https://github.com/wcmc-its/Scholars-Profile-System) (and any future downstream consumer).

---

## History — completed phases (lifted from prior project)

These phases were executed before the 2026-05-11 restructure, when ReciterAI work happened inside the now-paused chatbot project. Their full artifacts (CONTEXT, RESEARCH, PLANs, SUMMARYs, VERIFICATION) live in `.planning/phases/` for design rationale.

- [x] **Phase 1: Offline Pipeline** — Taxonomy generation (`generate_taxonomy.py`), two-pass scoring (`score_publications.py` via Bedrock Haiku + Sonnet), DynamoDB load. 4 plans.
- [x] **Phase 4: Subtopic System** — Per-topic inductive clustering, per-publication subtopic assignment, subtopic-level faculty scoring. Adds Axis 1.5 (~8–15 subtopics per topic). Verified.
- [x] **Phase 5: Hierarchy Publishing Contract** — Canonical `hierarchy.json` + JSON Schema + manifest, published to `s3://wcmc-reciterai-hierarchy/{version,latest}/`. Contract locked; see `docs/hierarchy-contract.md`. Verified.
- [x] **Phase 6: Spotlight Pipeline** — Per-faculty curated spotlight (lede generator on Opus 4.7, critic gates, pool ranker, sensitive gate, rotation selector, publish to `s3://wcmc-reciterai-artifacts/spotlight/`). Verified.

**Note:** Numbering reflects the original chatbot project's phase IDs (1, 4, 5, 6 — phases 2/3 were the now-paused chat runtime and chat UI, which do not belong to this service). Future phases in this repo restart at Phase 7.

---

## Milestone M2 — SPS-feeding service (current)

**Goal:** ReciterAI fully owns the production of all data SPS consumes — no logic that produces ReciterAI artifacts should live in SPS.

### Phases

- [x] **Phase 7: Hierarchy Publisher** — Done 2026-05-11. `pipeline_hierarchy/` package owns artifact production + S3 publish. SPS stopgap deleted; SPS #180 closed. See `phases/07-hierarchy-publisher/07-SUMMARY.md`.

- [ ] **Phase 8: Tools / Axis 2 Pipeline** — Productionize the tool/method extraction pipeline (currently a placeholder).
  - **Blocked on resolution of #5, #6, #7, #8.** Do not start producer implementation until all four `decision-deferred` issues close. See [docs/RECITERAI-SPEC.md §10](../docs/RECITERAI-SPEC.md#10-decision-axis-2-tools--commit-to-the-producer-model-not-a-date).
  - Source: `reciterai_keyword_relevance` table (not LLM-generated)
  - Output: TOOL# records in DynamoDB
  - The Phase 5 hierarchy contract already accommodates tool integration on the consumer side.

### Out of scope for M2

- Chatbot UI / chat runtime work (paused; lives in `~/Dropbox/GitHub/ReciterAI-Chatbot`, local-only)
- SPS-side rendering or DAL changes
- Re-running the taxonomy or recomputing scores (operational; not a phase)

---

## Conventions

- Phase artifacts: `.planning/phases/{padded_phase}-{slug}/{padded_phase}-{TYPE}.md` where TYPE ∈ {CONTEXT, RESEARCH, PLAN, SUMMARY, VERIFICATION}
- Plan files: `{padded_phase}-PLAN.md` (single) or `{padded_phase}-PLAN-{slug}.md` (multi-plan wave)
- One commit per plan; commit messages reference the plan ID
- Verify against goal-backward criteria before marking a phase complete

## Related repos

- [`wcmc-its/Scholars-Profile-System`](https://github.com/wcmc-its/Scholars-Profile-System) — downstream consumer
- [`wcmc-its/ReCiterAI-POC`](https://github.com/wcmc-its/ReCiterAI-POC) — superseded original POC (reference only)
- `~/Dropbox/GitHub/ReciterAI-Chatbot` — paused chatbot iteration (local only, no remote)
- [`wcmc-its/ReCiter`](https://github.com/wcmc-its/ReCiter) — author disambiguation engine (upstream data via ReciterDB)

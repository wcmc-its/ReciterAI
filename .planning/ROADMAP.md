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

- [x] **Phase 9: Substrate — Stages & Gates** — Done 2026-05-12. Content-addressed `STAGE#` records (`utils/stage_records.py`) + registered quality gates (`gates/registry.py` with `parent_prefix`, `pii_scan`, `schema_validation`, `schema_roundtrip`) + `python -m gates` CLI. `pipeline_hierarchy.publish` wired to both substrates. Spec [§5 Decision 4](../docs/RECITERAI-SPEC.md#5-decision-4--content-addressed-stage-completion) + [§7 Decision 6](../docs/RECITERAI-SPEC.md#7-decision-6--quality-gates-as-a-registered-framework). 115 tests passing. See `phases/09-substrate-stages-and-gates/09-SUMMARY.md`.

- [x] **Phase 10: Hot/Cold Path Split** — Done 2026-05-12. Four operational lanes (hot weekly Step Functions, cold operator CLI, monthly spotlight with dirty-gate, daily drift evaluator) wrapped in single-file EventBridge + IaC (D-10) with documented CDK migration trigger. Four new DynamoDB record types (`STAGE#hot_run#GLOBAL`, `UNCOVERED_PMID#`, `LOW_CONFIDENCE_ASSIGNMENT#`, `DRIFT#evaluation`); severity-tagged alerting via `pipeline_common.alert` (Slack + gh issue dedup); incremental rollup with byte-identical parity gate (D-08). Verifier 6/6 PASS; security 5/5 closed. 283 tests passing. See `phases/10-hot-cold-path-split/10-SUMMARY.md`.

- [x] **Phase 11: Versioning, Review State, Diff Signaling** — Implements spec [§3 Decision 2](../docs/RECITERAI-SPEC.md#3-decision-2--hierarchy_version-is-first-class-on-every-read-and-write), [§4 Decision 3](../docs/RECITERAI-SPEC.md#4-decision-3--review-state-is-machine-readable-pipeline-state), and [§5/§6 Decision 5](../docs/RECITERAI-SPEC.md#6-decision-5--structured-change-signaling). (completed 2026-05-12)
  - `hierarchy_version` stamped on every activity record; rotation state keyed by `(cwid, hierarchy_version)`
  - `REVIEW#` records as machine-readable cold-path gate; `python -m review approve …` CLI with pre-write validator
  - `diff.json` per publish + S3 write-order contract + read-tolerance rules + `Cache-Control` on `latest/*`
  - Move `generated_at` out of `hierarchy.json` (G-29 fix)
  - Estimate: 8–12 days

- [ ] **Phase 12: Feedback Loops, Both Aggregations, Residual Hygiene** — Implements spec §8 (both aggregations), §9 (feedback-event *consumption* — critic-reject events, uncovered-PMID Sonnet sweeps), and §11 residual maintenance items (G-1, G-18, G-24, G-34, G-36, G-37).
  - Estimate: 4–6 days
  - Plans: 8 (waves: 6 parallel in wave 1, 2 in wave 2, 1 in wave 3 — see Phase details)

- [ ] **Phase 8: Tools / Axis 2 Pipeline** — Productionize the tool/method extraction pipeline (currently a placeholder).
  - **Blocked on resolution of #5, #6, #7, #8.** Do not start producer implementation until all four `decision-deferred` issues close. See [docs/RECITERAI-SPEC.md §10](../docs/RECITERAI-SPEC.md#10-decision-axis-2-tools--commit-to-the-producer-model-not-a-date).
  - Source: `reciterai_keyword_relevance` table (not LLM-generated)
  - Output: TOOL# records in DynamoDB
  - The Phase 5 hierarchy contract already accommodates tool integration on the consumer side.

---

## Phase details

### Phase 11: Versioning, Review State, Diff Signaling

Implements spec [§3 Decision 2](../docs/RECITERAI-SPEC.md#3-decision-2--hierarchy_version-is-first-class-on-every-read-and-write), [§4 Decision 3](../docs/RECITERAI-SPEC.md#4-decision-3--review-state-is-machine-readable-pipeline-state), and [§5/§6 Decision 5](../docs/RECITERAI-SPEC.md#6-decision-5--structured-change-signaling).

**Deliverables:**

- `hierarchy_version` stamped on every activity record (TOPIC#, IMPACT#, TOOL#, etc.); rotation state keyed by `(cwid, hierarchy_version)` rather than by `cwid` alone. Lets dashboards pin to a version and unblocks A/B testing of hierarchies.
- `REVIEW#` records as machine-readable cold-path gate. `python -m review approve …` CLI with a pre-write validator that refuses to flag a cold-path mint as approved if any quality gate failed.
- `diff.json` per publish + S3 write-order contract + read-tolerance rules + `Cache-Control` on `latest/*`.
- Move `generated_at` out of `hierarchy.json` (G-29 fix) so the file is bit-stable across reruns with identical inputs.

**Canonical refs:** `docs/RECITERAI-SPEC.md` §3, §4, §5, §6, §11 (G-29).

**Estimate:** 8–12 days.

**Plans:** 0/3 plans complete
- [ ] `11-PLAN-hierarchy-versioning.md` — D-01..D-06, D-17: hierarchy_version stamping, rotation history PK rewrite, cold-path cutover audit row
- [ ] `11-PLAN-review-state.md` — D-07, D-08: REVIEW# DDB row + `python -m review approve|validate` CLI + pre-write validator
- [ ] `11-PLAN-change-signaling.md` — D-09..D-16, D-18: diff.json + 5-step S3 write-order + Cache-Control + G-29 fix + STAGE# run_id substrate

### Phase 12: Feedback Loops, Both Aggregations, Residual Hygiene

Implements spec §8 (both aggregations) and §9 (feedback-event *consumption* — critic-reject events, uncovered-PMID Sonnet sweeps), plus §11 residual maintenance (G-1, G-18, G-24, G-34, G-36, G-37).

**Deliverables:**

- Consume the `UNCOVERED_PMID#` + `LOW_CONFIDENCE_ASSIGNMENT#` event records that Phase 10 produces — trigger Sonnet sweeps for taxonomy expansion, surface subtopic split/merge candidates.
- Both aggregations live side by side: the exclusive primary-subtopic rollup (current) plus the inclusive multi-assignment rollup.
- Critic-reject event records and the §11 residual maintenance items.

**Canonical refs:** `docs/RECITERAI-SPEC.md` §8, §9, §11.

**Estimate:** 4–6 days.

**Plans:** 7/8 plans executed

Wave 1 (parallel):
- [x] `12-thresholds-substrate-PLAN.md` — G-1, G-18, D-23..D-28: lift constants to `config/thresholds.json` + schema + sibling docs + STAGE# tunable_inputs audit field + file G-37 tracking issue (D-22)
- [x] `12-feedback-producer-PLAN.md` — D-08, D-30, D-31: `CritReasonCode` StrEnum + `CRITIC_REJECT#{cwid}#{pmid_set_hash}` additive write at `spotlight/critic.py:571` alongside existing SPOTLIGHT_REVIEW#
- [x] `12-drift-extension-PLAN.md` — D-34: additive sparse `per_topic_low_confidence` on `DriftEvaluation.to_dynamodb_item()`; behavior contract preserved
- [x] `12-aggregations-PLAN.md` — D-12..D-18, D-33: `_aggregate_exclusive` + `_aggregate_inclusive` + parallel DDB partitions + CSV rename with dual-write deprecation + reconciliation gate + D-33 in-stream invariant
- [x] `12-residual-docs-PLAN.md` — G-24, G-34: `docs/sensitive-topic-exclusion.md` + IAM Policy section in `GETTING_STARTED.md`

Wave 2 (parallel, depend on wave 1):
- [x] `12-feedback-consumer-PLAN.md` — D-01..D-07, D-09, D-11, D-32: `pipeline_feedback/` package — sweep + three finding records + Sonnet uncovered-PMID pass + recluster trigger + SPOTLIGHT_DIAGNOSTIC# aggregation + deterministic markdown render + `python -m pipeline_feedback` CLI + cold-stage registration
- [x] `12-g36-reproducibility-PLAN.md` — G-36: extend `tests/test_hierarchy_reproducibility.py` to cover `publish()` end-to-end

Wave 3 (depends on every other wave-1 + wave-2 plan, gated per D-20):
- [ ] `12-g37-e2e-PLAN.md` — G-37: one bounded E2E test through cold path + fixture corpus; precondition is pytest 0 on main for every other Phase 12 plan's tests (D-20 SHA-able gate)

### Phase 8: Tools / Axis 2 Pipeline

Productionize the tool/method extraction pipeline (currently a placeholder). **Blocked on resolution of #5, #6, #7, #8.** Do not start producer implementation until all four `decision-deferred` issues close. See [docs/RECITERAI-SPEC.md §10](../docs/RECITERAI-SPEC.md#10-decision-axis-2-tools--commit-to-the-producer-model-not-a-date).

**Source:** `reciterai_keyword_relevance` table (not LLM-generated).
**Output:** TOOL# records in DynamoDB.
**Note:** The Phase 5 hierarchy contract already accommodates tool integration on the consumer side.

---

### Out of scope for M2

- Chatbot UI / chat runtime work (paused; lives in `~/Dropbox/GitHub/ReciterAI-Chatbot`, local-only)
- SPS-side rendering or DAL changes
- Re-running the taxonomy or recomputing scores (operational; not a phase)

---

## Conventions

- Phase artifacts: `.planning/phases/{padded_phase}-{slug}/{padded_phase}-{TYPE}.md` where TYPE ∈ {CONTEXT, RESEARCH, PLAN, SUMMARY, VERIFICATION}
- Plan files: `{padded_phase}-PLAN.md` (single) or `{padded_phase}-{slug}-PLAN.md` (multi-plan wave). The slug goes BEFORE `-PLAN.md` — `gsd-sdk` discovers plans via `.endsWith('-PLAN.md')`, so files named `{padded_phase}-PLAN-{slug}.md` will not be picked up by `phase-plan-index` or `/gsd-execute-phase`.
- One commit per plan; commit messages reference the plan ID
- Verify against goal-backward criteria before marking a phase complete

## Related repos

- [`wcmc-its/Scholars-Profile-System`](https://github.com/wcmc-its/Scholars-Profile-System) — downstream consumer
- [`wcmc-its/ReCiterAI-POC`](https://github.com/wcmc-its/ReCiterAI-POC) — superseded original POC (reference only)
- `~/Dropbox/GitHub/ReciterAI-Chatbot` — paused chatbot iteration (local only, no remote)
- [`wcmc-its/ReCiter`](https://github.com/wcmc-its/ReCiter) — author disambiguation engine (upstream data via ReciterDB)

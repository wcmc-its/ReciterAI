# Roadmap: ReCiter AI Chatbot

**Milestone:** M1 -- v1 Launch
**Granularity:** Coarse
**Coverage:** 57/57 v1 requirements mapped
**Repos:** ReciterAI (Python, offline pipeline) + ReCiter-Publication-Manager (Next.js 14, chat runtime + UI)

---

## Phases

- [ ] **Phase 1: Offline Pipeline** - Taxonomy generation, scoring (~10K publications via Bedrock), and DynamoDB load (ReciterAI repo)
- [ ] **Phase 2: Chat Runtime** - API routes for query routing, retrieval, synthesis, and guardrails (Publication Manager repo)
- [ ] **Phase 3: Chat UI + Evaluation** - `/chat` page, guided entry, streaming display, filter badge, and inline feedback (Publication Manager repo)

---

## Phase Details

### Phase 1: Offline Pipeline
**Goal**: Scored publication data is loaded into DynamoDB and ready for the chat runtime to query
**Depends on**: Nothing (first phase -- runs before any chatbot code is built)
**Repos**: ReciterAI (pipeline scripts), AWS DynamoDB (data store)
**Requirements**: TAX-01, TAX-02, TAX-03, PIPE-01, PIPE-02, PIPE-03, PIPE-04, PIPE-05, PIPE-06, DB-01, DB-02, DB-03, DB-04, DB-05, DB-06, DB-07, DB-08
**Success Criteria** (what must be TRUE):
  1. A validated 50-topic taxonomy (`taxonomy_v1`) exists and correctly categorizes at least 10 sample queries a research dean would ask
  2. Running the scoring script against ReciterDB produces ~10K scored publications, with all publications scoring 0.3+ on any topic stored in DynamoDB
  3. DynamoDB contains faculty profile records, tool score records, the aging deep dive record, and processing tracker records -- a spot check of known faculty returns their profile and top topics
  4. Both GSIs (Faculty-to-Topics, Processing-by-Version) are created and return expected results for test queries
  5. The pipeline handles Bedrock failures gracefully: retries 3 times with exponential backoff, logs failures to the processing tracker, and completes with <1% failure rate on the full dataset
**Plans:** 3/4 plans executed

Plans:
- [x] 01-01-PLAN.md -- Shared utilities (Bedrock client, DynamoDB helpers, SQL queries)
- [x] 01-02-PLAN.md -- Taxonomy generation, validation, and human review
- [x] 01-03-PLAN.md -- Publication scoring pipeline (extract, screen, dense-score)
- [x] 01-04-PLAN.md -- DynamoDB data load and verification

**UI hint**: no

### Phase 2: Chat Runtime
**Goal**: The `/api/chat` route correctly routes queries, retrieves relevant candidates from DynamoDB and ReciterDB, synthesizes streamed responses, and enforces guardrails
**Depends on**: Phase 1 (DynamoDB must be populated before retrieval can be tested end-to-end; can begin with mock data)
**Repos**: ReCiter-Publication-Manager (Next.js API routes, Bedrock + DynamoDB clients)
**Requirements**: ROUTE-01, ROUTE-02, ROUTE-03, ROUTE-04, ROUTE-05, ROUTE-06, ROUTE-07, RET-01, RET-02, RET-03, RET-04, RET-05, RET-06, RET-07, RET-08, RET-09, SYNTH-01, SYNTH-02, SYNTH-03, SYNTH-04, SYNTH-05, SYNTH-06, SYNTH-07, GUARD-01, GUARD-02, GUARD-03, GUARD-04, GUARD-05, GUARD-06
**Success Criteria** (what must be TRUE):
  1. Posting "Who works on aging?" to `/api/chat` returns a streaming response with ranked faculty candidates, each with rationale drawn from their scored publications
  2. Posting "Build a P01 team for cardiovascular disease" returns a composed team with role assignments, cross-departmental spread, and co-authorship signal
  3. A follow-up message "narrow that to Department of Medicine" filters the previous candidate set without re-querying DynamoDB (server-side cache is used)
  4. An HR-adjacent query ("who hasn't published in 2 years?") is refused with an appropriate message; an overly broad query ("who's the best researcher?") is reframed with a clarifying question
  5. The synthesis response includes the active filter set in its output and surfaces a faculty engagement caveat when a candidate has a thin profile
**Plans:** 8 plans

Plans:
- [x] 02-01-PLAN.md — Wave 0: verify DynamoDB TTL, install AWS SDK v3 deps, create config/chatbot.ts, author smoke queries
- [x] 02-02-PLAN.md — Wave 1: Bedrock/DynamoDB clients (CViche port), trace/logging helpers, canonical types
- [x] 02-03-PLAN.md — Wave 1: CONV# cache with ownership binding + TTL, smart-defaults filter merge
- [x] 02-04-PLAN.md — Wave 2: Bedrock Haiku query router + guardrail refuse/reframe handler
- [ ] 02-05-PLAN.md — Wave 2: topic, person, gap, deep-dive retrieval patterns
- [ ] 02-06-PLAN.md — Wave 3: publication-report, team-assembly, bibliometric enrich, co-authorship, runRetrieval dispatcher
- [ ] 02-07-PLAN.md — Wave 4: Bedrock Sonnet streaming synthesis + mode-specific system prompts
- [ ] 02-08-PLAN.md — Wave 5: /api/chat handler, k8s secrets, smoke runner, human verification checkpoint
**UI hint**: no

### Phase 3: Chat UI + Evaluation
**Goal**: The `/chat` page in Publication Manager is polished, demo-ready, and captures inline evaluation feedback
**Depends on**: Phase 2 (streaming API) AND Phase 4 (subtopic system — real subtopic data is required for demo per user decision Option B; Phase 3 UI is blocked on Phase 4 completion)
**Repos**: ReCiter-Publication-Manager (Next.js pages, Bootstrap 5 + CSS modules)
**Requirements**: UI-01, UI-02, UI-03, UI-04, UI-05, UI-06, UI-07, UI-08, EVAL-01, EVAL-02, EVAL-03
**Success Criteria** (what must be TRUE):
  1. Navigating to `/chat` in Publication Manager shows the carousel guided entry (auto-rotating question templates with topic slot + rotating real-faculty names), matching PM's design system with WCM-red accent palette
  2. Clicking the active carousel card (or typing a query and submitting) displays the streamed response word-by-word via SSE, rendered as chat bubbles
  3. The filter badge (Academic articles | WCM full-time | First/last author) is toggleable and applies to the NEXT query; faculty names in responses link to their PM profile pages
  4. After a response, the inline evaluation widget (progressive-disclosure thumbs/stars/tags/text) is visible below the bot bubble; submitting feedback writes an EVAL# record and transactionally updates FACULTY#ACCURACY aggregates in DynamoDB
  5. The "Start over" button resets the session to guided entry (client new conversationId + server-side CONV# record deletion); after 6 turns the UI surfaces an inline-banner suggestion to start a new query
  6. Guided entry carousel shows promoted query types including `topic_decompose` ("What are subtopics of X?") and `topic_recent` ("What's new in X?"); `team_assembly` is soft-demoted (not in carousel, still accepts typed queries)
**Plans**: 8 plans

Plans:
- [ ] 03-01-PLAN.md — Wave 1: chat.tsx page shell + AppLayout auth gate + SideNavbar "AI Chat" link
- [ ] 03-02-PLAN.md — Wave 2A: CarouselGuidedEntry (6-card react-slick, topic/faculty slots) + CarouselTicker + faculty-sample API
- [ ] 03-03-PLAN.md — Wave 2B: ChatBubble + StreamingMarkdown (react-markdown + faculty-link renderer) + LoadingSkeleton + SubtopicPill + install react-markdown
- [ ] 03-04-PLAN.md — Wave 2C: FilterBadge (3 toggle pills) + StickyInputBar ("Ask" CTA) + TurnBanner
- [ ] 03-05-PLAN.md — Wave 3: chat.tsx state machine wiring (SSE client hook + all Wave 2 components composed)
- [ ] 03-06-PLAN.md — Wave 4A: EvalWidget (progressive disclosure) + eval.ts DynamoDB transact write
- [x] 03-07-PLAN.md — Wave 4B: reset.ts endpoint + ShortCircuitCard (4 variants) + DisambiguationButtons
- [ ] 03-08-PLAN.md — Wave 5: wire EvalWidget + ShortCircuitCard into page + synthesis prompt faculty links + human verification

**UI hint**: yes
**Design notes**: See `.planning/phases/03-chat-ui-evaluation/03-CONTEXT.md` for full decisions (carousel + bubble layout, WCM-red palette, progressive-disclosure eval, real-faculty rotation via server-fetched random sample)

---

### Phase 4: Subtopic System
**Goal**: Add a second level of granularity below the 67-topic flat taxonomy via data-driven subtopic discovery, per-activity assignment, and subtopic-level faculty scoring. Enables `topic_decompose` query type and tier-based retrieval improvements across all topic-aware queries.
**Depends on**: Phase 1 (scoring pipeline, `reciterai_synopsis` and `reciterai_keyword_relevance` tables must be populated), Phase 2 (router and retrieval modules to extend)
**Execution order**: Runs BEFORE Phase 3 (reverse of numerical order) because Phase 3 demo requires real subtopic data across all 67 topics per user decision Option B
**Repos**: ReciterAI -ReCiter-Integration (Python pipeline scripts), ReCiter-Publication-Manager (router post-classifier, new retrieval modules, hierarchy.json)
**Requirements**: SUB-01, SUB-02, SUB-03, SUB-04, SUB-05, SUB-06, SUB-07, SUB-08, SUB-09, SUB-10, SUB-11, SUB-12, SUB-13, SUB-14, SUB-15, SUB-16, SUB-17, SUB-18
**Success Criteria** (what must be TRUE):
  1. `hierarchy.json` contains data-driven subtopic definitions for all 67 topics that pass the cold-start floor (≥30 activities), with ≥85% activity coverage per topic and human review approval
  2. Every qualifying activity in DynamoDB has `subtopic_ids[]`, `primary_subtopic_id`, and `subtopic_confidences{}` fields populated by Pass 2
  3. Every faculty record has `subtopic_scores{}` populated by Pass 3 using primary-subtopic-only aggregation
  4. Router post-classifier resolves `subtopics[]` for topic-bearing queries; Tier 1/2/3/4 retrieval logic is live and synthesis prompts frame responses at subtopic level for Tiers 1/2
  5. `topic_decompose` query type returns structured subtopic lists via template renderer (no synthesis LLM call)
  6. Golden query set regression gate passes on the new hierarchy (hit rate not below flat-topic baseline)
  7. Aging pilot met all four go/no-go criteria (coverage ≥85%, ≤3 corrections/subtopic, blind spot-check ≥80% agreement, no >40% pairwise overlap) before full backfill began
**Plans**: 10 plans

Plans:
- [x] 04-01-PLAN.md — Requirements + hierarchy.json schema + DynamoDB migration helpers
- [x] 04-02-PLAN.md — Pass 1 Discovery (Sonnet clustering) + Aging pilot draft + human-review gate
- [x] 04-03-PLAN.md — Pass 2 Assignment (Haiku per-activity) + Pass 3 Aggregation for Aging
- [x] 04-04-PLAN.md — See-also generation script with bidirectionality filter
- [x] 04-05-PLAN.md — Aging pilot gate (D-20..D-23) + go/no-go checkpoint
- [x] 04-06-PLAN.md — Full backfill (66 remaining topics) + hierarchy.json to PM
- [x] 04-07-PLAN.md — Post-classifier + hierarchy.ts loader + types (PM repo)
- [x] 04-08-PLAN.md — WEIGHT_FLOOR + Tier 1/2/4 retrieval modules + dispatcher
- [x] 04-09-PLAN.md — Subtopic-level synthesis prompts (Tier 1/2 framing)
- [x] 04-10-PLAN.md — Golden query set + regression gate + self-ID survey
**UI hint**: no (backend-only; Phase 3 consumes the outputs)
**Design notes**: See `.planning/phases/04-subtopic-system/04-CONTEXT.md` for full spec. Second-order review items (10 issues) flagged for resolution during planning.

---

### Phase 5: Hierarchy Publishing Contract
**Goal**: Establish `hierarchy.json` as a versioned, schema-validated, contract-documented artifact that any current or future downstream consumer (SPS, PM, future analytics) integrates against — replacing the current ad-hoc cross-repo file-copy pattern that caused the SPS data-path mismatch (SPS expected hierarchy structure in DynamoDB; pipeline only ever wrote it as a co-located JSON file in the PM repo). Deliverables: `hierarchy.schema.json` (JSON Schema generated from `hierarchy-schema.md`), `docs/hierarchy-contract.md` (consumer-facing contract — stable URL pattern, schema location, publish cadence, integration pattern, breaking-change policy), `backfill_all.py --publish` flag (schema-validate hierarchy + S3 PutObject to `s3://<bucket>/v{date}/` and `latest/` + write `manifest.json` with version/sha256/taxonomy_version + fail-on-validation-error), STATE.md + `hierarchy-schema.md` D-19 cross-references to the new contract doc.
**Depends on**: Phase 4 (hierarchy.json shape and the `display_name`/`short_description` D-19 fields are stable; relabel-pass + `--assemble-only` are landed)
**Repos**: ReciterAI -ReCiter-Integration (Python pipeline + S3 publish + schema + contract doc)
**Requirements**: HPC-01, HPC-02, HPC-03, HPC-04, HPC-05, HPC-06, HPC-07, HPC-08
**Success Criteria** (what must be TRUE):
  1. `hierarchy.schema.json` validates `hierarchy_full.json` and lives at a stable repo path that's co-published with every artifact upload.
  2. `docs/hierarchy-contract.md` exists, names the stable S3 URL pattern, links to the schema, declares publish cadence + breaking-change policy, and is the single reference any consumer reads to integrate.
  3. `python backfill_all.py --publish` validates `hierarchy_full.json` against schema (fails build on error), uploads `hierarchy.json` + schema + manifest to `s3://<bucket>/v{date}/` AND `s3://<bucket>/latest/`, and is idempotent on re-runs of the same recompute.
  4. `manifest.json` carries the artifact's version, taxonomy_version, generated_at, sha256(hierarchy.json), and schema_version.
  5. STATE.md and `hierarchy-schema.md` D-19 link to `docs/hierarchy-contract.md` as the authoritative consumer-facing reference.
  6. SPS coordination handoff exists: a brief in this repo describing the SPS-side ETL change (out-of-scope for this phase but prepped so SPS can pick it up immediately).
**Out of scope** (separate downstream work, NOT in this phase):
  - SPS-side ETL implementation (separate session in SPS repo).
  - PM migration off the worktree-file pattern (deferred; current copy-via-`backfill_all.py` keeps working).
  - DynamoDB load step for hierarchy data (rejected — fights the 400KB item limit and adds infrastructure for a problem the artifact pattern solves more cleanly).
**Plans:** 6 plans

Plans:
- [ ] 05-01-PLAN.md — Wave 1: hierarchy.schema.json (Draft 2020-12) + IAM JSON snippets + jsonschema dependency pin
- [ ] 05-02-PLAN.md — Wave 1: utils/s3_client.py (lazy boto3) + backfill_all.py --publish (validate + manifest + 6 PutObjects)
- [ ] 05-03-PLAN.md — Wave 2: docs/hierarchy-contract.md (consumer-facing contract — URL pattern, schema, cadence, CHANGELOG, FAQ)
- [ ] 05-04-PLAN.md — Wave 2: docs/sps-integration-handoff.md + docs/sps-etl-reference.ts (working TypeScript reference)
- [ ] 05-05-PLAN.md — Wave 3: STATE.md + hierarchy-schema.md cross-references to docs/hierarchy-contract.md
- [ ] 05-06-PLAN.md — Wave 3: smoke test — invalid-fixture rejection + --publish --dry-run positive path
**UI hint**: no (backend pipeline + docs only)
**Design notes**: Pre-planning context lives in this conversation thread (2026-05-06); first task during `/gsd-discuss-phase 5` is to capture the S3 bucket name + IAM provisioning answer from the user before planning begins.

---

### Phase 6: Spotlight Pipeline & Publishing Contract
**Goal**: Publish a versioned, schema-validated `spotlight.json` artifact with 10 LLM-authored editorial ledes for top-ranked subtopics. Selection is rank-then-rotate: pool of top-50 subtopics by `Σ (impactScore × recency_weight(year))` from DynamoDB enrichment, then 10 picked enforcing one-per-parent-topic diversity with exponential decay against last-shown timestamps so stale-but-strong subjects rotate in. Lede generation uses Bedrock Sonnet with the voice-constrained prompt at `prompts/spotlight_synopsis_v0.md` grounded in per-paper `synopsis` + `impactJustification` (richer signal than titles + journals). A critic pass auto-flags constraint violations (em-dash, time-bound phrases, marketing words, missing "WCM scholars are X-ing" tic) with up to 3 regens before routing to a manual review queue. Sensitive-topic tag matches (DynamoDB-stored, NOT in repo) also route to manual review. Each spotlight carries 2-3 representative papers with `{personIdentifier, displayName, position}` for first/last authors so SPS can render headshots via its existing faculty photo store. Mirrors Phase 5's S3 + manifest + IAM + sha256 publishing pattern; reuses `utils/s3_client.py`.
**Depends on**: Phase 4 (subtopic system + DynamoDB enrichment of `synopsis` + `impactJustification` per TOPIC# record), Phase 5 (publishing pattern reuse — schema, manifest, S3 client, IAM, sha256 polling)
**Repos**: ReciterAI -ReCiter-Integration (Python pipeline + S3 publish + schema + contract doc + sensitive-tag review queue)
**Requirements**: SPOT-01, SPOT-02, SPOT-03, SPOT-04, SPOT-05, SPOT-06, SPOT-07, SPOT-08, SPOT-09, SPOT-10, SPOT-11, SPOT-12, SPOT-13, SPOT-14
**Success Criteria** (what must be TRUE):
  1. Pool ranker reads DynamoDB TOPIC# enrichment and produces a top-50 list of subtopics scored by `Σ (impactScore × recency_weight(year))`. Recency τ confirmed in discuss-phase; default 12 months. Output is deterministic given input data.
  2. Rotation selector picks exactly 10 subtopics from the pool, one per parent topic, with `selection_score = pool_score × exponential_decay(time_since_last_shown)` against last-shown state in DynamoDB.
  3. Lede generator runs Bedrock Sonnet with `prompts/spotlight_synopsis_v0.md`, fed `synopsis` + `impactJustification` from 2-3 papers per spotlight, producing a 25-35 word lede that includes the "WCM scholars are X-ing" construction.
  4. Critic pass detects constraint violations and triggers up to 3 regens; persistent failures land in a manual review queue file alongside the publish run.
  5. Sensitive-topic gate matches subtopic against `SPOTLIGHT_CONFIG#sensitive_tags` patterns from DynamoDB; matches route to the manual review queue, non-matches auto-publish.
  6. `python backfill_spotlight.py --publish` validates the artifact against `spotlight.schema.json`, fails build on error, uploads to `s3://wcmc-reciterai-artifacts/spotlight/v{date}/` AND `s3://wcmc-reciterai-artifacts/spotlight/latest/`, and is idempotent on re-runs. (CONTEXT decision Q2.1 supersedes earlier bucket name `wcmc-reciterai-hierarchy`.)
  7. `manifest.json` carries `schema_version`, `spotlight_version`, `taxonomy_version`, `version`, `generated_at`, `sha256`, `artifact_bytes`.
  8. `docs/spotlight-contract.md` exists alongside `docs/hierarchy-contract.md` — names the stable S3 URL pattern, links to the schema, declares cadence + breaking-change policy, includes the voice contract and author-headshot rendering notes.
  9. SPS coordination handoff exists at `docs/sps-spotlight-handoff.md` mirroring the hierarchy handoff structure, plus `docs/sps-spotlight-etl-reference.ts` as a working starting point for the SPS-side ETL.
  10. spotlight.json carries per-paper `{pmid, title, journal, year}` plus `first_author` and `last_author` shaped as `{personIdentifier, displayName, position}` so SPS can render headshots via its existing faculty photo store.
**Out of scope** (deferred):
  - 335-wide synopsis generation — top-N rotation is the design; non-spotlighted subtopics get no lede in v1.
  - SPS-side ETL implementation (separate follow-up session in SPS repo after the contract publishes).
  - "Suppress Recent Contributions" home-page change in SPS (SPS-side UI change, separate follow-up).
  - Automated weekly cron runner (operator-run for v1).
  - LLM judge for sensitive-topic detection (tag-list pattern match only for v1).
  - Formal comms-strategy sign-off workflow (manual review queue is the v1 mechanism).
  - Embedding-based subtopic re-ranking (signal-based ranking is sufficient).
  - Editorial CMS UI for hand-curated overrides (operator-curated via CLI flags only for v1).
**Plans:** 8 plans

Plans:
**Wave 1**
- [ ] 06-01-PLAN.md — Foundations: ARTIFACTS_BUCKET constant + IAM/bucket policy templates + DynamoDB schema doc (SPOTLIGHT_HISTORY/REVIEW/CONFIG) + bucket-migration runbook

**Wave 2** *(blocked on Wave 1 completion)*
- [ ] 06-02-PLAN.md — Pool ranker (top-50 by impactScore over 24mo) + spotlight/types.py dataclasses + tests
- [ ] 06-04-PLAN.md — Sensitive-topic gate (fail-closed) + review queue (write/list/set_status) + parameterized DynamoDB expressions

**Wave 3** *(blocked on Wave 2 completion)*
- [ ] 06-03-PLAN.md — Rotation selector with exponential decay τ=12wk + parent-diversity + SPOTLIGHT_HISTORY# read+write

**Wave 4** *(blocked on Wave 3 completion)*
- [ ] 06-05-PLAN.md — Lede generator (Bedrock Sonnet) + hybrid critic (regex bundle + Haiku LLM judge) + retry-3 + critic prompt v0

**Wave 5** *(blocked on Wave 4 completion)*
- [ ] 06-06-PLAN.md — spotlight.schema.json (Draft 2020-12) + assembler + valid/invalid fixtures

**Wave 6** *(blocked on Wave 5 completion)*
- [ ] 06-07-PLAN.md — spotlight/publish.py (Phase 5 mirror) + backfill_spotlight.py CLI orchestrator (9 flags)

**Wave 7** *(blocked on Wave 6 completion)*
- [ ] 06-08-PLAN.md — docs/spotlight-contract.md + sps-spotlight-handoff.md + sps-spotlight-etl-reference.ts + end-to-end smoke test

**UI hint**: no (backend pipeline + docs only; UI consumer lives in SPS as separate follow-up)
**Design notes**: Pre-planning context lives in `.planning/phases/06-spotlight-pipeline/06-CONTEXT.md` — locks pool=50, selection=10, parent diversity, exponential decay, critic retries=3, weekly cadence, papers-per-lede=2-3, headshot author payload. Open questions for /gsd-discuss-phase 6: recency τ, decay time-constant, rotation-state storage layout, cold-start behavior, manual-review-queue mechanics, publish-prefix vs new bucket, what lands in artifact (just spotlights vs pool snapshot too), `--dry-run` + `--regen-only` operator workflow. Sensitive-topic tag list seeded directly to DynamoDB by Paul out-of-band — never committed to the repo.

---

## Progress Table

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Offline Pipeline | 3/4 | In Progress|  |
| 2. Chat Runtime | 8/8 | Complete | 2026-04-12 |
| 3. Chat UI + Evaluation | 7/8 | In Progress | - |
| 4. Subtopic System | 10/10 | Complete | 2026-04-22 |
| 5. Hierarchy Publishing Contract | 0/6 | Planned | - |
| 6. Spotlight Pipeline & Publishing Contract | 0/7 | Planned | - |

---

## Coverage Notes

**Phase 1** covers the longest critical path item (scoring pipeline runs 2-4 hours and must complete before end-to-end testing). It can run in the background while Phase 2 and Phase 3 work proceeds against mock data.

**Demo-ready subset** (Section 14 of design spec): all demo capabilities are covered by Phase 1 + Phase 2 + a minimal Phase 3 UI. The interactive filter badge toggles (UI-05 interactive) are full v1 but not demo-critical -- a static badge displaying the active defaults satisfies the demo.

---

---

## Backlog

### Phase 999.1: Follow-up — Phase 4 verification gaps (BACKLOG)

**Goal:** Resolve Phase 4 verification items that could not be completed before advancing to Phase 3
**Source phase:** 04 (Subtopic System)
**Deferred at:** 2026-04-22 during /gsd-next advancement to Phase 3
**Items:**
- [ ] Golden query regression gate: populate `expected_faculty` for 8 topic_match queries (requires domain-expert ranking), run `eval_golden_queries.py` against live dev server, produce `regression_gate_report.md`
- [ ] Aging pilot D-21: add `reviewer_corrections_count` to hierarchy draft schema and capture corrections from reviewer
- [ ] Aging pilot D-22: complete blind spot-check with second reviewer and document agreement rate in `aging_pilot_results.md`

*Roadmap created: 2026-04-08*
*Last updated: 2026-05-07 — Phase 6 (Spotlight Pipeline & Publishing Contract) added. Pre-planning context at `.planning/phases/06-spotlight-pipeline/06-CONTEXT.md`. Lede prompt at `prompts/spotlight_synopsis_v0.md`. Triggered by SPS home-page redesign requirement to suppress Recent Contributions and replace Selected Research with an interactive spotlight (mockup: `~/Downloads/home-spotlight-interactive.html`).*

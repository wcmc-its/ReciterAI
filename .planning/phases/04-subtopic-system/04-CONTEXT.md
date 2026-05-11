# Phase 4: Subtopic System - Context

**Gathered:** 2026-04-13
**Status:** Ready for planning (after second-order review items resolved)
**Execution order:** Runs BEFORE Phase 3 (reverse of numerical order). Phase 3 depends on Aging subtopics as a minimum to demo with real data; full 67-topic coverage required before Phase 3 ships per user decision (Option B).

<domain>
## Phase Boundary

Add a second level of granularity below the existing 67-topic flat taxonomy. Subtopics are induced from real WCM activity data per topic, pre-computed as faculty scores, and surfaced via a new `topic_decompose` query type plus router expansion logic.

This is a full milestone of work: new DynamoDB fields, three new pipeline passes (Discovery, Assignment, Aggregation), new router logic, a backfill job, and an evaluation harness. It is not a Phase 3 add-on.

In scope:
- Data-driven subtopic discovery per topic via Sonnet clustering over activity synopses (Pass 1)
- Per-activity subtopic assignment with primary + secondary designations via Haiku (Pass 2)
- Faculty score aggregation at subtopic level using existing articleScore formula (Pass 3)
- `hierarchy.json` as the authoritative subtopic definition store
- See-also cross-topic relationships with bidirectionality validation
- Post-classifier router pass for subtopic intent resolution
- Four-tier retrieval (direct subtopic / thin subtopic + see_also / topic-level fallback / `topic_decompose` renderer)
- `topic_decompose` query type with template-only renderer (no synthesis LLM call)
- DynamoDB migration: new `subtopic_ids[]`, `primary_subtopic_id`, `subtopic_confidences{}` on activity records; `subtopic_scores{}` on faculty records
- Backfill job (Aging first, then descending by activity count)
- Pilot validation on Aging with four go/no-go criteria
- Evaluation harness (golden query set, faculty self-identification, regression gate)

Out of scope (explicit):
- Editorial/curated hierarchy approach (rejected — data-driven wins; see deferred)
- DAG data model (rejected — tree + see_also escape hatch instead)
- Fractional-weight secondary subtopic scoring (rejected — zero weight for secondaries)
- Broadening Tier 2 expansion beyond thin subtopics (deferred to future iteration)
- Subtopic surfacing in Publication Manager faculty profile UI (separate decision; remains internal to chatbot for v1)
- Non-publication activity types (schema forward-compatible, but v1 = publications only)

</domain>

<decisions>
## Implementation Decisions

### Pipeline architecture (three passes with distinct cadences)

- **D-01:** Pass 1 (Discovery) — Sonnet clusters topic activities into 8-15 thematic subtopics. Targets topics with ≥30 qualifying activities (cold-start floor). Includes a second extension pass up to 2× initial N with stopping criteria: ≥85% activity coverage, no singleton clusters. Human reviewer approves before production. Recompute is wholesale replacement, not incremental. Trigger is deliberate (~50 new activities since last run or significant programmatic event), not calendar-based.
- **D-02:** Pass 2 (Assignment) — Per-activity Haiku call classifies which subtopic(s) the activity belongs to. Model outputs confidence scores. Primary subtopic = highest confidence assignment. Multi-assignment allowed; secondaries carry zero weight in faculty aggregation. Activities that don't fit at acceptable confidence remain unassigned (topic-level fallback). Runs on indexing cadence (weekly or on-demand).
- **D-03:** Pass 3 (Aggregation) — Arithmetic-only, no LLM inference. Activity's full `articleScore` ((impactScore/100)^1.2 × relevanceScore^1.4) counts only toward its primary subtopic. Secondary assignments carry zero weight. Rationale: subtopic scores must be comparable across subtopics within a topic; double-counting inflates and breaks ranking.
- **D-04:** Coverage rule — no "Other" bucket. Extend subtopics up to 2× limit. Remaining unassigned activities demoted to topic-level (participate in topic-level retrieval, zero contribution to subtopic scores). State is internal; no UX exposure.

### Hierarchy data model

- **D-05:** Subtopics are scoped to their parent topic — no shared nodes across topics. Cross-topic relationships via `see_also` only.
- **D-06:** No stable ID scheme. On recompute, hierarchy is replaced wholesale. No migration logic for subtopic IDs.
- **D-07:** `hierarchy.json` stores both `activity_count` and `total_weight` per subtopic. `total_weight` is the retrieval threshold signal (handles blockbuster-paper edge case); `activity_count` is display-only.

### See-also links

- **D-08:** See-also generation is a one-time Sonnet batch after Pass 1 completes. Regenerated with every discovery recompute.
- **D-09:** Validation rule: links must be bidirectional to be retained. If Sonnet proposes A→B but not B→A, the link is dropped. Bidirectionality is the quality gate in lieu of human review.
- **D-10:** See-also is used for Tier 2 router expansion AND surfaced in `topic_decompose` responses as navigation. Not used in retrieval scoring.

### Router integration

- **D-11:** Main router (Haiku) is unchanged. A lightweight post-classifier pass resolves `subtopics[]` by injecting only the subtopic definitions for matched topics (~8-12 subtopics, not full taxonomy). Keeps main router lean.
- **D-12:** Four-tier retrieval:
  - Tier 1: subtopic match with total_weight above floor → subtopic-level retrieval + synthesis
  - Tier 2: subtopic match with total_weight at/below floor → subtopic + see_also expansion → subtopic-level synthesis
  - Tier 3: topic-level match only → topic-level retrieval + topic-level synthesis (current Phase 2 behavior)
  - Tier 4: `topic_decompose` → template renderer, no synthesis LLM call
- **D-13:** Volume threshold uses `total_weight`, not `activity_count`. Specific threshold value is a v1 assumption pending calibration against Aging pilot data.
- **D-14:** Tier 4 renderer is a stateless formatting function — takes `hierarchy.json` subtopic array and returns structured response (label, description, activity_count, total_weight, see_also). No LLM call.
- **D-15:** Synthesis prompts for Tier 1/2 must frame responses at subtopic level, not topic level. Prompt must inject the full subtopic label and description verbatim.

### DynamoDB schema migration

- **D-16:** New activity record fields: `subtopic_ids[]`, `primary_subtopic_id`, `subtopic_confidences{}`.
- **D-17:** New faculty record fields: `subtopic_scores{}`.
- **D-18:** Compatibility rule: absent subtopic fields = unassigned state, not an error. All reads must tolerate legacy records. Topics not yet through Pass 1 permanently lack fields until their discovery pass runs.
- **D-19:** Backfill order: Aging first (pilot validation), then remaining topics in descending activity count order.

### Pilot criteria (Aging, gates Step 1)

- **D-20:** Coverage ≥85% of Aging activities assigned to a named subtopic
- **D-21:** Reviewer corrections ≤3 substantive per subtopic during human review
- **D-22:** Blind spot-check: second reviewer matches 10 randomly sampled activities to subtopics without seeing Pass 2 assignments. Agreement rate ≥80%
- **D-23:** Subtopic distinctiveness: no two subtopics share >40% of their assigned activities
- **D-24:** Failure on any criterion → revise discovery prompt and re-run before proceeding to full backfill.

### Evaluation (runs on every hierarchy recompute)

- **D-25:** Golden query set: 10-20 queries with domain-expert ranked faculty answers. Established before subtopic code ships. Used as regression gate: new hierarchy cannot drop hit rate below flat-topic baseline.
- **D-26:** Faculty self-identification spot check after initial backfill. Sample size TBD during planning.
- **D-27:** Automated regression gate on every recompute, must pass before production deployment.

### Claude's Discretion
- Exact Sonnet / Haiku prompt wording for discovery and assignment
- Tier 4 renderer format choice (template string vs markdown formatter)
- See-also generation prompt structure and temperature setting
- Post-classifier model (default Haiku but open to Sonnet if quality demands)
- Backfill batch size and concurrency tuning

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### This spec
- `.planning/phases/04-subtopic-system/04-CONTEXT.md` — this file (design decisions)
- `.planning/design/topic-decompose-hierarchy.md` — predecessor design-questions doc (superseded by this spec; kept for history)

### Existing taxonomy and retrieval
- `ReCiter-Publication-Manager/controllers/chatbot/taxonomy.ts` — 67 flat topic IDs and labels
- `ReCiter-Publication-Manager/controllers/chatbot/shared.ts` §`resolveTopicId` — current taxonomy resolver (brittle on synonyms; will need aliases layer alongside hierarchy)
- `ReCiter-Publication-Manager/controllers/chatbot/prompts/router-system.ts` — main router prompt (unchanged by this spec; post-classifier is a separate pass)
- `ReCiter-Publication-Manager/controllers/chatbot/retrieval/topic.ts` — RET-01 topic-level retrieval pattern (baseline for subtopic-level retrieval)

### Scoring and data
- `CLAUDE.md` §Conventions — articleScore formula, scoring scopes, reciterai_impact and reciterai_synopsis sources
- `.planning/phases/01-offline-pipeline/01-CONTEXT.md` — DynamoDB table design, TOPIC# partition schema, TTL policy
- `.planning/phases/02-chat-runtime/02-08-SUMMARY.md` — Phase 2 retrieval module inventory

### Project-level
- `.planning/PROJECT.md` — cost target (~$0.05/query), data-scope constraints, taxonomy design principle
- `.planning/REQUIREMENTS.md` — current requirement coverage; new reqs needed for subtopic work

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- Existing `TOPIC#<id>` DynamoDB partitions — subtopic retrieval reuses this partition key, filtered by new `subtopic_ids` attribute
- Phase 2 synthesis prompts — Tier 1/2 prompts inherit structure, only framing changes (topic-level → subtopic-level)
- `reciterai_keyword_relevance` (TOOL# records, Axis 2) — used as numeric signal in Pass 1 discovery prompts; NOT conflated with subtopics (different decomposition axis)
- `reciterai_synopsis` — source of the synopses fed to Sonnet in Pass 1 (not raw abstracts)
- `getBedrockClient()`, `logStage(traceId, stage, fields)` — reuse for new pipeline code

### Established Patterns
- Pre-computed scores landing in DynamoDB — Pass 3 follows this pattern
- Haiku for classification, Sonnet for generation/synthesis — Pass 1 uses Sonnet, Pass 2 uses Haiku, post-classifier uses Haiku
- Wholesale replacement over incremental migration — precedent from Phase 1 taxonomy rebuilds

### Integration Points
- `controllers/chatbot/hierarchy.json` — new authoritative file
- `controllers/chatbot/taxonomy_aliases.json` — new aliases layer (deferred from the design-questions doc; may or may not be part of this phase)
- `controllers/chatbot/retrieval/subtopic.ts` — new retrieval module for Tier 1/2
- `controllers/chatbot/retrieval/topic-decompose.ts` — new retrieval/renderer for Tier 4
- `controllers/chatbot/post-classifier.ts` — new post-router pass
- Pass 1/2/3 pipelines — new scripts in the Phase 1 Python codebase (`ReciterAI -ReCiter-Integration/` root), or TypeScript in PM (decide during planning)

### Cost model (approved baseline)
- Pass 1 discovery: ~$0.20-0.30/topic Sonnet × 67 topics = ~$15-20 one-time; same per recompute
- Pass 2 backfill: ~$0.001/activity Haiku × ~30K activities = ~$30 one-time; ~$1/week ongoing
- See-also batch: ~$1-3/full regen Sonnet
- Pass 3: arithmetic only, ~$0
- Post-classifier per query: ~$0.0005 Haiku (not in spec's cost table; add during planning)
- Full initial build total: ~$50. Ongoing weekly: ~$1. Periodic recompute: ~$20.

</code_context>

<specifics>
## Specific Ideas

- Pilot topic: Aging. Existing deep-dive work (Phase 1 canned deep dive per PROJECT.md) reduces risk; aging is also data-rich enough to stress-test the discovery clustering.
- When demoing "subtopics of Cancer" — expect ~7 cancer-specific leaves (breast, lung, prostate, GI, neuro-oncology, gynecologic, melanoma) in the flat 67 to become grouped under "Cancer" subtopics within a single parent via the discovery pass. This is the natural validation that the clustering mirrors intuition.
- Aging subtopic example record (from spec) shows `total_weight` ~11.5K, `activity_count` 256 for a single subtopic — reasonable demo scale.
- Per-activity synopsis length is already constrained by Phase 1 synthesis (`reciterai_synopsis` table), so Pass 1 token budget is predictable.

</specifics>

<deferred>
## Deferred Ideas

### Alternatives rejected during design
- **Editorial curation of hierarchy** — Rejected in favor of data-driven discovery. Rationale: taxonomy reflects what WCM actually publishes, not theoretical opinion. See spec §"Why Not Editorial Curation?".
- **LLM-drafted hierarchy with human review** (approach from earlier design-questions doc) — Rejected for same reason; data-driven clusters activities, not topic names.
- **DAG multi-parent data model** — Rejected in favor of tree + see_also. Rationale: tree is simpler to reason about; see_also captures cross-topic value without multi-membership complexity.
- **Fractional-weight secondary subtopic scoring** (e.g., 0.3× articleScore toward secondaries) — Rejected in favor of primary-only (zero-weight secondaries). Tradeoff acknowledged: breadth representation lost in intra-subtopic ranking. If this turns out to matter empirically, revisit.
- **Broader Tier 2 expansion (any query could see see_also)** — Deferred. Current design expands only on thin subtopics. Broadening requires a query-breadth classification signal.

### Second-order review items (to resolve during planning)
These surfaced in the second-pass critical review of spec v2; not blockers but should be nailed during Phase 4 planning:

1. **Singleton cluster threshold** — minimum subtopic size undefined. Pick a number (likely 2-3 activities).
2. **Primary-confidence tiebreaker** — when two subtopics tie on confidence, which wins? Default: higher total_weight subtopic; if still tied, alphabetical ID.
3. **Zero-weight secondaries tradeoff** — document explicitly that faculty ranking within secondary subtopics is impossible by design. One-line acknowledgment in code comments and user-facing docs.
4. **Post-classifier model + latency** — specify Haiku, document ~500ms additional latency budget, add to cost table.
5. **Rollout UX during partial backfill** — user-facing message when requesting subtopics for an unprocessed topic. Candidate: "Hierarchy pending for \[topic\]; falling back to topic-level results."
6. **Volume threshold calibration method** — pick one: knee-point of weight distribution OR empirical synthesis-quality comparison OR domain-expert judgment. Document the calibration step as part of Pass 1 completion.
7. **Overlap threshold justification** — 40% distinctiveness rule currently arbitrary. Either empirical defense or "v1 assumption" label.
8. **Synthesis prompt example** — add at least one concrete subtopic-level prompt template to prevent implementation drift.
9. **See-also non-determinism risk** — temperature=0 for generation, OR run twice and take union of bidirectional candidates. Document whichever.
10. **Golden query set as Step 0** — add to implementation sequence. Who authors, how many queries, domain-expert ranking process.

### Future / v2+
- **`taxonomy_aliases.json`** (colloquial labels → canonical IDs) — surfaced in design-questions doc. May land in Phase 4 or as a separate slice; decide during planning.
- **Subtopic display in Publication Manager faculty profiles** — remains internal to chatbot for v1; profile UI integration is separate future work.
- **Non-publication activity types (grants, trials)** — schema is forward-compatible, but v1 = publications only.
- **Incremental hierarchy updates** — wholesale replacement chosen for v1. Incremental updates with stable IDs can be added later if recompute cost becomes a constraint.

</deferred>

---

*Phase: 04-subtopic-system*
*Context gathered: 2026-04-13*
*Source spec: reciterai-subtopic-spec2.md (user-authored, reviewed and refined across two rounds)*

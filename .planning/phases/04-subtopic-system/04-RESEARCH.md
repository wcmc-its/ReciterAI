# Phase 4: Subtopic System - Research

**Researched:** 2026-04-13
**Domain:** Data-driven subtopic induction, LLM clustering/assignment, DynamoDB schema migration, post-classifier routing
**Confidence:** HIGH on architecture + reuse patterns (grounded in Phase 1/2 code); MEDIUM on prompt specifics + calibration numbers (needs Aging pilot data to lock); LOW on golden-query authorship process (organizational decision, not technical)

---

## Research Objective

Answer for the planner: "What do I need to know to plan Phase 4 well?" — specifically, how Passes 1/2/3 compose, where code lives, how the DynamoDB migration is staged, how the post-classifier integrates with the already-shipped router, what the `topic_decompose` renderer looks like, and how the 10 second-order review items in `04-CONTEXT.md` get resolved in concrete task form.

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions (D-01 through D-27)

**Pipeline (three passes, distinct cadences):**
- **D-01** Pass 1 Discovery: Sonnet clusters activities into 8-15 subtopics per topic. Cold-start floor ≥30 activities. Extension pass up to 2× initial N with stopping: ≥85% coverage, no singleton clusters. Human-reviewer approval gate. Wholesale replacement on recompute. Trigger is deliberate (~50 new activities or programmatic event).
- **D-02** Pass 2 Assignment: Per-activity Haiku call emits confidence scores. Primary = highest confidence. Multi-assignment allowed; secondaries zero-weight. Unassigned activities fall back to topic-level. Weekly/on-demand cadence.
- **D-03** Pass 3 Aggregation: Arithmetic-only. Activity's full `articleScore = (impactScore/100)^1.2 × relevanceScore^1.4` counts only toward primary subtopic. Secondaries zero weight (comparability rule).
- **D-04** Coverage rule: No "Other" bucket. Extend to 2× cap, then demote unassigned to topic-level. Internal state, no UX exposure.

**Hierarchy data model:**
- **D-05** Subtopics scoped to parent topic (no shared nodes); cross-topic relations via `see_also`.
- **D-06** No stable IDs — wholesale replacement on recompute.
- **D-07** `hierarchy.json` stores both `activity_count` (display) and `total_weight` (retrieval threshold).

**See-also:**
- **D-08** One-time Sonnet batch after Pass 1; regenerated per recompute.
- **D-09** Bidirectionality validation — A→B retained only if B→A also proposed.
- **D-10** Used for Tier 2 expansion and `topic_decompose` navigation; NOT used in retrieval scoring.

**Router integration:**
- **D-11** Main router unchanged. Post-classifier pass resolves `subtopics[]` using only matched topics' subtopic defs (~8-12 entries).
- **D-12** Four-tier retrieval (subtopic-direct / subtopic+see_also / topic fallback / topic_decompose template).
- **D-13** Volume threshold uses `total_weight`, not `activity_count`. Specific value = v1 assumption pending Aging calibration.
- **D-14** Tier 4 renderer is stateless formatting function, no LLM call.
- **D-15** Synthesis prompts for Tier 1/2 frame at subtopic level; inject label + description verbatim.

**DynamoDB migration:**
- **D-16** New activity fields: `subtopic_ids[]`, `primary_subtopic_id`, `subtopic_confidences{}`.
- **D-17** New faculty field: `subtopic_scores{}`.
- **D-18** Absent fields = unassigned state, NOT an error. All reads tolerate legacy records.
- **D-19** Backfill order: Aging first (pilot), then descending activity count.

**Pilot criteria (Aging):**
- **D-20** Coverage ≥85% • **D-21** ≤3 reviewer corrections/subtopic • **D-22** Blind spot-check ≥80% agreement on 10 samples • **D-23** No pairwise overlap >40%. **D-24** Any failure → revise prompt and re-run.

**Evaluation:**
- **D-25** Golden query set 10-20 queries with domain-expert ranked faculty answers. Established BEFORE code ships. Regression gate: new hierarchy cannot drop hit rate below flat-topic baseline.
- **D-26** Faculty self-ID spot check post-backfill (sample size TBD).
- **D-27** Automated regression gate on every recompute before production deploy.

### Claude's Discretion
- Exact Sonnet/Haiku prompt wording for discovery + assignment
- Tier 4 renderer output format (template string vs markdown)
- See-also generation prompt structure and temperature
- Post-classifier model (default Haiku; Sonnet open if quality demands)
- Backfill batch size and concurrency tuning

### Deferred Ideas (OUT OF SCOPE)
- Editorial/curated hierarchy (rejected — data-driven wins)
- LLM-drafted hierarchy with human review (same rationale)
- DAG multi-parent data model (rejected — tree + see_also)
- Fractional-weight secondary subtopic scoring (rejected — zero weight)
- Broader Tier 2 expansion beyond thin subtopics (deferred)
- `taxonomy_aliases.json` — adjacent, decide in planning whether in scope
- Subtopic display in PM faculty profile UI (future work)
- Non-publication activity types (schema forward-compatible, v1 = publications)
- Incremental hierarchy updates (wholesale replacement for v1)
</user_constraints>

## Project Constraints (from CLAUDE.md)

- **Naming:** `personIdentifier` canonical (NOT `cwid`). The literal `"cwid_"` exists ONLY in `WCM_FACULTY_UID_PREFIX` (`retrieval/shared.ts`). Phase 4 must not re-introduce `cwid` anywhere.
- **articleScore formula:** `(impactScore/100)^1.2 × relevanceScore^1.4` — Pass 3 aggregation must use this exact formula (already implemented in `itemToTopicMatch`).
- **LLM scope:** `analysis_summary_person` (2,474 FT faculty). Subtopic discovery reads synopses only for activities of faculty in this set.
- **Data source of truth:** `reciterai_synopsis` for synopses; `reciterai_impact` for impact scores. Do NOT use raw abstracts for Pass 1 — use synopses.
- **Deprecated:** `person_article` table. Do not reintroduce.
- **Tables for faculty-scoped queries:** `analysis_summary_author + _article + _person`.
- **Models pinned:** `anthropic.claude-haiku-4-5` (screening/routing/assignment) and `anthropic.claude-sonnet-4-6` (clustering/synthesis). Pass 1 = Sonnet, Pass 2 = Haiku, Post-classifier = Haiku default, See-also generation = Sonnet.
- **Reuse singletons:** `getBedrockClient()`, `getDocClient()`, `logStage(traceId, stage, fields)`. No new AWS client singletons.
- **Parameterized queries only:** never interpolate user values into SQL or DynamoDB expressions.
- **Two-repo discipline:** Python pipeline at repo root; PM code in `ReCiter-Publication-Manager/` worktree on `feature/chatbot-runtime`. Commit PM code via `commit-to-subrepo`; plain `commit` for `.planning/` docs.
- **Taxonomy design principle:** Topics reflect what's actually in the data, not theoretical opinion. Subtopics inherit this — empty subtopics add noise without signal.

## Phase Requirements

See "Proposed REQ-IDs" section below — Phase 4 introduces a new requirement block (`SUBTOPIC-*`) that the planner should add to `.planning/REQUIREMENTS.md` before plan-authoring.

---

## Pipeline Architecture

**Recommendation: Passes 1/2/3 live in the Python pipeline at repo root (`ReciterAI -ReCiter-Integration/`). `hierarchy.json` emission is the handoff artifact to the PM repo. Post-classifier, subtopic retrieval, and topic-decompose renderer live in PM TypeScript.**

### Rationale

| Axis | Python (root repo) | TypeScript (PM) |
|------|--------------------|------------------|
| Precedent | Phase 1 established boto3 Bedrock client + `score_publications.py` + `load_dynamodb.py` — this is the same shape of work [VERIFIED: STATE.md, generate_taxonomy.py, score_publications.py exist at repo root] | None — PM has no batch pipeline precedent |
| Bedrock SDK maturity | boto3 Converse API is well-supported; Phase 1 uses it [VERIFIED: .planning/phases/01-offline-pipeline/01-CONTEXT.md D-04] | `@aws-sdk/client-bedrock-runtime` ConverseCommand exists and is used by `bedrock-client.ts` — works, but no batch/orchestration infra |
| Reuse of Phase 1 code | Direct — same `utils/bedrock_client.py`, same ReciterDB access via `core/db.py`, same DynamoDB helpers | Would require reimplementing batching, retry, checkpointing |
| Runtime characteristic | Long-running (~hours for 30K activities), offline, parallelizable via threadpool — Python's operational model | Next.js is request/response, not a batch runner |
| Cost of splitting | One JSON handoff (`hierarchy.json` committed to PM repo) + DynamoDB writes that PM reads. Low coupling. | Sharing taxonomy logic across languages is already handled — `taxonomy.ts` is generated from `taxonomy_v2.json`. Same pattern for hierarchy.json. |

**Implication for planning:** Pass 1/2/3 are new Python scripts (`discover_subtopics.py`, `assign_subtopics.py`, `aggregate_subtopic_scores.py`, `generate_see_also.py`), modeled on Phase 1's flat-script structure (D-02 from 01-CONTEXT). PM gets only the `hierarchy.json` import + new TS retrieval modules.

### Script Layout (Python side)

```
ReciterAI -ReCiter-Integration/
├── discover_subtopics.py          # Pass 1 (Sonnet)
├── assign_subtopics.py            # Pass 2 (Haiku, parallel)
├── aggregate_subtopic_scores.py   # Pass 3 (arithmetic, DynamoDB writes)
├── generate_see_also.py           # One-time Sonnet batch after Pass 1
├── backfill_topic.py              # Orchestrator: runs 1→see_also→2→3 for one topic (Aging pilot)
├── eval_golden_queries.py         # Regression gate (D-25/27)
└── utils/
    └── bedrock_client.py          # Existing — already reused for all LLM calls
```

### Script Layout (PM side — `ReCiter-Publication-Manager/controllers/chatbot/`)

```
controllers/chatbot/
├── hierarchy.json                  # Generated artifact; committed to PM repo
├── hierarchy.ts                    # Typed loader + helpers (getSubtopicsForTopic, getTotalWeight, etc.)
├── post-classifier.ts              # NEW — second Haiku pass for subtopic resolution
├── prompts/
│   └── post-classifier-system.ts   # NEW — static system prompt for subtopic disambiguation
└── retrieval/
    ├── subtopic.ts                 # NEW — Tier 1/2 subtopic-partition range query
    └── topic-decompose.ts          # NEW — Tier 4 stateless renderer
```

---

## Pass 1: Discovery (Sonnet clustering)

### Input envelope (per topic)

- Source: `reciterai_synopsis` joined with the TOPIC# DynamoDB partition, filtered to activities scoring ≥0.3 on the topic (cold-start floor: count must be ≥30 to proceed).
- Per-activity payload: `{ pmid, title, synopsis, impact_score, relevance_score }`. Synopses are already distilled (~150-400 tokens each per Phase 1 convention, see `reciterai_synopsis`).
- **Token budget math:** 30 activities × ~400 tokens/synopsis = ~12K input tokens; 256 activities (Aging example from CONTEXT) × ~400 = ~100K — still within Sonnet's 200K context. For topics >300 activities, batch across multiple Sonnet calls and use a consolidation pass (precedent: Phase 1 D-05 batched taxonomy generation).
- **Full taxonomy context:** Send topic label + description only for the parent topic. Do NOT send the other 66 topics — subtopics are scoped (D-05).

### Output JSON shape

```json
{
  "topic_id": "aging_geroscience",
  "subtopics": [
    {
      "id": "aging_cellular_senescence",
      "label": "Cellular Senescence & Senolytics",
      "description": "Mechanisms of cellular senescence and therapeutic targeting of senescent cells.",
      "seed_pmids": [12345, 67890, 11122],
      "coverage_estimate": 0.18
    }
  ],
  "singleton_discarded": [],
  "uncovered": [],
  "total_activities": 256,
  "coverage_pct": 0.89
}
```

### Prompt pattern recommendations

- **Temperature: 0** (review item #9 calls for it on see-also; same rationale applies to discovery — reproducibility).
- **Format:** Ask for cluster count in target range (8-15). Explicit instruction: each cluster ≥N activities (N=3 recommended to resolve review item #1 singleton threshold; 2 is too permissive for ~200-activity topics, 3 gives robust clusters).
- **Coverage mechanic:** First pass returns clusters. Script computes coverage; if <85%, re-invoke with instruction to split heterogeneous clusters OR add new clusters covering the uncovered seed set. Cap at 2× initial N (D-01).
- **Stopping criteria in-prompt:** "Do not create a cluster smaller than 3 activities; leave those activities unassigned. We will handle them at the topic level."
- **Rationale output:** Each cluster must include a 1-2 sentence description — this becomes the synthesis-prompt injection (D-15) and the `topic_decompose` navigation text.
- **Cluster ID naming:** Python generates deterministic IDs from Sonnet-supplied labels: `{topic_prefix}_{slug(label)}`, e.g. `aging_cellular_senescence`. IDs are NOT stable across recomputes (D-06) — recompute replaces wholesale. Document this in code comments.

### Human review gate

- Output Pass 1 result to `hierarchy_draft_<topic>.json` file.
- Reviewer opens JSON, adjusts labels/descriptions, can merge or split clusters, then saves to `hierarchy.json`.
- Approval commit message convention: `docs(subtopics-<topic>): approve v1 hierarchy`.
- Reviewer is the primary implementer (Sumanth per STATE.md) or user (Paul Albert).

### Cost sanity

- Aging (~256 activities): ~1 Sonnet call × ~100K input + ~5K output = ~$0.45 per run (Bedrock Sonnet 4.6 pricing: input $3/M, output $15/M) [ASSUMED: pricing — verify against current Bedrock pricing page before planning]. CONTEXT's $0.20-0.30/topic figure assumes shorter synopses or smaller topics; Aging is upper end.
- All 67 topics one-time: ~$15-30 total. Recompute at same cost.

---

## Pass 2: Assignment (Haiku per-activity)

### Input envelope (per activity)

Per-activity prompt includes:
- Activity: `{ pmid, title, synopsis }`
- Parent topic label + description
- The 8-15 subtopic definitions (id + label + description) for that topic — NOT the full taxonomy

### Output JSON shape

```json
{
  "pmid": 12345,
  "topic_id": "aging_geroscience",
  "assignments": [
    {"subtopic_id": "aging_cellular_senescence", "confidence": 0.87},
    {"subtopic_id": "aging_mitochondria", "confidence": 0.42}
  ]
}
```

- Confidence floor: document a threshold (recommend 0.3) below which the assignment is dropped.
- `primary_subtopic_id` = argmax on confidence (set in post-processing, not by Haiku — cheaper + more reliable).
- `subtopic_ids[]` = all assignments with confidence ≥ floor.
- `subtopic_confidences{}` = dict keyed by subtopic_id.

### Tiebreaker default (resolves review item #2)

**When two subtopics tie on confidence:**
1. Higher `total_weight` subtopic wins (signal density beats intuition).
2. If still tied, alphabetical ID.

Implement as Python function at aggregation time (after Pass 1 produces `total_weight`, which happens in a preliminary Pass 3 run) — not in the prompt. Document as `assign_subtopics.py::_resolve_primary_on_tie`.

### Batching strategy

- Haiku per-activity is ~500 input + 50 output tokens ≈ $0.0004 per call [ASSUMED: verify against Bedrock Haiku 4.5 pricing]. 30K activities → ~$12-15. CONTEXT's $30 figure is a safe upper bound.
- Parallelize: 10-20 concurrent Haiku calls via `concurrent.futures.ThreadPoolExecutor` (Phase 1 precedent — STATE §Todos "Request Bedrock quota increases before starting scoring pipeline (10-20 concurrent calls)").
- Checkpointing: write to DynamoDB on success per-activity (`UpdateItem` adding subtopic fields to existing SK=SCORE#... item). Idempotent — rerun resumes from unassigned activities.
- Cost sanity: at $0.0004 × 30K = $12, well within $30 budget. Blowup risk = missing batching or retrying retryable errors too aggressively. Use exponential backoff already in `utils/bedrock_client.py` (D-04 Phase 1).

---

## Pass 3: Aggregation (arithmetic)

### Algorithm pseudocode

```python
# Input: DynamoDB TOPIC# partition items with subtopic_ids + primary_subtopic_id + articleScore
# Output: FACULTY# record with subtopic_scores{} dict

for each topic_id:
    # Clear stale subtopic_scores for this topic on all faculty
    # (recompute is wholesale — D-06)
    for each faculty_record in topic:
        clear_subtopic_scores_for_topic(faculty_record, topic_id)

    # Query all activity records in this topic partition
    for each item in TOPIC#<topic_id>:
        if not item.primary_subtopic_id:
            continue   # Unassigned activities don't contribute (D-02/D-04)
        faculty = item.faculty_uid
        score = item.article_score   # Already computed in itemToTopicMatch convention
        # Primary-only aggregation (D-03)
        subtopic_scores[faculty][item.primary_subtopic_id] += score

    # Write back to FACULTY# records
    batch_write_updates(faculty_records, subtopic_scores)
```

### Edge cases

| Case | Behavior |
|------|----------|
| Activity with no assignment (Pass 2 confidence below floor) | Contributes zero to subtopic scores; still counts for topic-level queries via existing partition. |
| Activity with primary but zero secondaries | Primary gets full articleScore. |
| Activity with multi-assignment | Secondaries are stored on the activity record (for display/see-also queries) but contribute zero to faculty aggregation (D-03). This is the tradeoff acknowledged in review item #3. |
| Faculty with scores in multiple subtopics of same topic | Each faculty's `subtopic_scores{}` is a dict `{subtopic_id: score_sum}`. Intra-topic ranking uses this dict. |
| Faculty whose top activities are all secondaries | Faculty's subtopic_scores for those subtopics will be zero. Faculty still appears via topic-level retrieval. Document this limitation (review item #3). |

### Faculty score write path

- `subtopic_scores{}` lives on the existing `FACULTY#<personIdentifier>` partition, on SK `PROFILE` (alongside `top_topics`). Use `UpdateItem` with SET expression to add/replace the attribute. D-17 treats this as a new top-level attribute on the existing item — no new record type.
- **Concurrency note:** If Pass 3 runs while live queries read FACULTY# records, they must tolerate absent `subtopic_scores` (D-18). Current retrieval code does not read this field — no coordination needed for v1.

---

## See-Also Generation

### Bidirectionality approach (resolves review item #9)

**Recommendation: one-pass Sonnet generation at temperature=0, followed by script-side bidirectionality filter. Do NOT run twice and union — temperature=0 makes that degenerate.**

Pseudocode:
```python
# After all 67 topics have Pass 1 hierarchies
proposed_links = sonnet_generate_see_also(all_subtopics)   # Returns [(from_id, to_id, reason), ...]

# Build adjacency set
adj = {(f, t) for f, t, _ in proposed_links}

# Keep only bidirectional links (D-09)
bidirectional = [(f, t, r) for f, t, r in proposed_links if (t, f) in adj]

hierarchy_json["see_also"] = bidirectional
```

### Prompt structure

Input: flat list of all subtopics (id + label + description) across all topics. ~67 topics × ~10 subtopics = ~670 subtopics. At ~30 tokens/subtopic → ~20K tokens input.

Ask Sonnet to propose cross-topic links (from `aging_cardiovascular_disease` to `cardiovascular_disease_*`, etc.). Output JSON:

```json
{
  "links": [
    {"from": "aging_cardiovascular_disease", "to": "cardiovascular_disease_heart_failure", "reason": "overlap in late-life cardiac decline"}
  ]
}
```

### Regeneration cadence

- Regenerated whenever ANY topic's Pass 1 is re-run (D-08). Cost is ~$1-3/batch, cheap enough to run unconditionally.
- Store in `hierarchy.json` under a top-level `"see_also"` key, NOT inline on each subtopic. Makes bidirectionality filter simpler.

### Cost sanity

~20K input + ~5K output Sonnet call = ~$0.15. CONTEXT's $1-3 figure probably assumes multiple iterations; one-pass at temp=0 is cheaper.

---

## Router Post-Classifier

### Composition with existing router

The main router (Plan 02-04 — `router.ts` + `prompts/router-system.ts`) stays **unchanged** (D-11). The post-classifier runs AFTER the router, BEFORE the dispatcher. Flow:

```
message → router (Haiku, main) → { query_type, topics[], ... }
                                       ↓
                                 [IF topics.length > 0]
                                       ↓
                              post-classifier (Haiku, lightweight)
                                       ↓
                              Adds: { subtopics: [{topic_id, subtopic_id, confidence}], tier: 1|2|3|4 }
                                       ↓
                              runRetrieval dispatcher
```

### Input contract

```ts
interface PostClassifierInput {
  message: string;                 // original user message
  topics: TopicMatch[];            // router-resolved canonical topic IDs
  hierarchy: HierarchySlice;       // ONLY the subtopic defs for matched topics (~8-12 entries)
}
```

The `HierarchySlice` is produced by `getSubtopicsForTopics(topicIds[])` — a PM-side helper that reads `hierarchy.json` and returns only subtopic definitions for the matched topics. Keeps the prompt lean (D-11).

### Output contract

```ts
interface PostClassifierOutput {
  subtopics: Array<{
    topic_id: string;
    subtopic_id: string | null;  // null when no subtopic matches — Tier 3 fallback
    confidence: number;
  }>;
  tier: 1 | 2 | 3 | 4;
  topic_decompose: boolean;  // true if user asked "what are the subtopics of X" — Tier 4
}
```

### Latency budget (resolves review item #4)

- **Target: ~500ms** additional per-query latency. Haiku 4.5 typical latency is ~300-800ms for small prompts [ASSUMED: based on Phase 2 smoke-test observations of ~1.5-15s total latency].
- **Cost:** ~$0.0005/query (~500 input tokens, ~100 output). Add to cost table; total per-query cost goes from ~$0.05 to ~$0.0505. Negligible.
- **Fallback on failure:** If post-classifier fails/times out, default to Tier 3 (topic-level retrieval). No user-visible error. Log as `POST_CLASSIFIER_FALLBACK` via `logStage`.

### Tier 4 detection

The post-classifier also detects `topic_decompose` intent ("What are subtopics of aging?", "Break down cancer research"). Router already has 9 query types and is unchanged; `topic_decompose` is detected by post-classifier via the prompt asking explicitly. Alternative is to add `topic_decompose` as a 10th `query_type` in the router — but CONTEXT D-11 says main router is unchanged, so this pattern keeps it here.

**Planner decision gate:** Is `topic_decompose` a new `query_type` in the router, OR a post-classifier flag on existing `topic_match`? CONTEXT implies the latter. Lock during planning.

---

## Four-Tier Retrieval Dispatch

### Where tier selection lives

**Recommendation: tier selection is an OUTPUT of post-classifier, consumed by `runRetrieval`. `retrieval/index.ts` dispatcher adds a tier-aware branch at the top of its existing switch on `query_type`.**

```ts
// retrieval/index.ts (pseudo)
export async function runRetrieval(routed, postClassified, ...) {
  if (postClassified.topic_decompose) {
    return renderTopicDecompose(postClassified.subtopics, hierarchy);  // Tier 4
  }

  if (routed.query_type === 'topic_match' && postClassified.subtopics.length > 0) {
    const subtopic = postClassified.subtopics[0];  // primary matched subtopic
    const weight = getTotalWeight(subtopic.topic_id, subtopic.subtopic_id);
    if (weight >= WEIGHT_FLOOR) {
      return querySubtopicCandidates(subtopic);  // Tier 1
    } else {
      const seeAlsoTargets = getSeeAlso(subtopic);
      return querySubtopicWithExpansion(subtopic, seeAlsoTargets);  // Tier 2
    }
  }

  // Existing Phase 2 topic-level behavior (Tier 3)
  return queryTopicCandidates(...);
}
```

### Total weight floor calibration (resolves review item #6)

**Recommendation: empirical calibration during Aging pilot, then expert-judgment freeze.**

Method (document in `.planning/phases/04-subtopic-system/calibration-notes.md`):
1. After Aging Pass 1+2+3, sort Aging subtopics by `total_weight` descending.
2. Plot weight distribution. Look for knee-point (sharp drop in weight).
3. Run 10 sample subtopic queries at the knee-point threshold. Compare synthesis quality at weight-above-knee (Tier 1) vs weight-below-knee (Tier 2 expansion).
4. If quality difference is clear → freeze threshold at knee-point. If not → use domain-expert judgment (Paul Albert eyeballs 5 above, 5 below, picks cutoff).
5. Document the chosen value as `WEIGHT_FLOOR` constant in `config/chatbot.ts`. Mark as "v1 assumption, calibrated against Aging pilot" per D-13.

### Routing decision tree

| Condition | Tier | Retrieval module | Synthesis |
|-----------|------|------------------|-----------|
| `topic_decompose == true` | 4 | `retrieval/topic-decompose.ts` renderer | No LLM call |
| subtopic matched AND weight ≥ floor | 1 | `retrieval/subtopic.ts` | Subtopic-level prompt |
| subtopic matched AND weight < floor | 2 | `retrieval/subtopic.ts` + see_also expansion | Subtopic-level prompt |
| No subtopic match (null) | 3 | `retrieval/topic.ts` (existing) | Topic-level prompt (existing) |

### Subtopic retrieval query (Tier 1/2)

Reuses `TOPIC#<topic_id>` partition but adds a filter on `primary_subtopic_id`. Two options:

**Option A — FilterExpression on Query:**
```ts
QueryCommand({
  KeyConditionExpression: "PK = :pk AND SK BETWEEN :lo AND :hi",
  FilterExpression: "primary_subtopic_id = :sid",
  ExpressionAttributeValues: { ":pk": `TOPIC#${topicId}`, ":lo": lo, ":hi": hi, ":sid": subtopicId }
})
```
Filter runs after Key evaluation — consumes RCUs on filtered items too. For topics of 256 activities, this is fine. For topics of 2000+ activities, RCU cost is notable.

**Option B — New GSI: `SubtopicActivitiesIndex` (PK=subtopic_id, SK=SCORE#NNNN#ACTIVITY#pmid_X):**
More efficient but doubles storage. Skip for v1; revisit if Tier 1 queries show latency problems.

**Recommendation: Option A for v1.** Phase 1's partition scheme already sized for topic-level queries; subtopic filter is a light addition.

---

## Topic Decompose Renderer (Tier 4)

### Stateless function signature

```ts
// retrieval/topic-decompose.ts
export interface TopicDecomposeInput {
  topicId: string;
  hierarchy: HierarchyJson;  // full hierarchy.json
}

export interface TopicDecomposeOutput {
  topic: { id: string; label: string; description: string };
  subtopics: Array<{
    id: string;
    label: string;
    description: string;
    activity_count: number;
    total_weight: number;
    see_also: Array<{ topic_id: string; subtopic_id: string; label: string; reason: string }>;
  }>;
  partial: boolean;   // true if topic has no hierarchy yet (D-18/rollout UX)
  fallback_message?: string;  // "Hierarchy pending for aging; falling back to topic-level results." per review item #5
}

export function renderTopicDecompose(input: TopicDecomposeInput): TopicDecomposeOutput { ... }
```

### Output format (D-14 Claude's Discretion)

**Recommendation: structured JSON (not markdown).** Phase 3 UI needs structured data for carousel integration ("What are subtopics of X?" card). Markdown would force UI to parse. Synthesis layer in Phase 2 emits markdown for streamed narrative; topic_decompose is NOT a narrative — it's a navigation UI element.

The Phase 3 UI renders the JSON into subtopic cards with activity_count chips and see_also links. This is a contract, not a rendering decision — lock it in planning.

### UI contract (consumed by Phase 3)

Phase 3 `/chat` carousel already anticipates this (`topic_decompose` in ROADMAP Phase 3 success criterion 6). The UI renders each subtopic as a clickable card that fires a new query `"Who works on <subtopic label>?"`. The see_also entries appear as chip navigation.

---

## DynamoDB Schema Migration

### New fields (D-16, D-17)

**On activity records (PK=`TOPIC#<topic_id>`, SK=`SCORE#NNNN#ACTIVITY#pmid_X#cwid_<pid>`):**

| Field | Type | Semantics |
|-------|------|-----------|
| `subtopic_ids` | List<String> | All subtopics this activity was assigned to (confidence ≥ floor). |
| `primary_subtopic_id` | String | Highest-confidence subtopic. Used for ranking in Tier 1. |
| `subtopic_confidences` | Map<String, Number> | `{subtopic_id: confidence}` — full detail for audit/regen. |

**On faculty records (PK=`FACULTY#<personIdentifier>`, SK=`PROFILE`):**

| Field | Type | Semantics |
|-------|------|-----------|
| `subtopic_scores` | Map<String, Number> | `{subtopic_id: sum_of_article_scores}` — primary-only aggregation. |

### Legacy tolerance (D-18)

- Every Phase 2 read path tolerates absent subtopic fields. Current code already coerces with `?? ""` / `?? 0` in `itemToTopicMatch` [VERIFIED: shared.ts:220-237]. Pattern is already there — extend with similar coerce for new fields.
- Topics below cold-start floor (≥30 activities) never get Pass 1/2/3. Their records permanently lack subtopic fields → default to Tier 3 topic-level retrieval.

### Backfill strategy

**Use `UpdateItem` with SET expression, NOT full rewrite.** Three reasons:
1. Existing records already have `synopsis`, `impact_score`, `title`, `journal`, etc. populated by the Plan 06.5e-f enrichment pass (STATE §last session). Full rewrite would risk losing any concurrently-written fields.
2. `UpdateItem` is idempotent — rerun resumes.
3. `BatchWriteItem` requires full PutItem on each row (no partial updates) — less safe.

Pattern:
```python
client.update_item(
    TableName="reciterai-chatbot",
    Key={"PK": f"TOPIC#{topic_id}", "SK": sk},
    UpdateExpression="SET subtopic_ids = :sids, primary_subtopic_id = :pid, subtopic_confidences = :confs",
    ExpressionAttributeValues={
        ":sids": subtopic_ids,
        ":pid": primary_subtopic_id,
        ":confs": {k: Decimal(str(round(v, 4))) for k, v in confidences.items()},
    }
)
```

**Decimal coercion is mandatory** — STATE §"Plan 01 to_decimal() pattern" — DynamoDB rejects Python float types. Reuse the existing `to_decimal()` helper.

### Backfill order (D-19)

1. Aging (pilot, gates D-20..D-23)
2. Remaining 66 topics sorted by activity count descending
3. Topics below cold-start floor (<30 activities) — skip entirely (document list of skipped topics in `hierarchy.json` under `"excluded_topics": [...]`)

---

## Hierarchy.json

### Location

`ReCiter-Publication-Manager/controllers/chatbot/hierarchy.json`

Sits alongside `taxonomy.ts` and `taxonomy_v2.json`. Committed to the PM repo on `feature/chatbot-runtime`. Regenerated by Python pipeline and copied into PM via manual commit (same pattern as `taxonomy_v2.json` → `taxonomy.ts` generation per Plan 02-08).

### JSON schema

```json
{
  "version": "subtopic_v1",
  "generated_at": "2026-04-15T12:00:00Z",
  "taxonomy_version": "taxonomy_v2",
  "excluded_topics": [
    {"id": "oral_craniofacial_health", "reason": "below cold-start floor", "activity_count": 18}
  ],
  "topics": {
    "aging_geroscience": {
      "subtopics": [
        {
          "id": "aging_cellular_senescence",
          "label": "Cellular Senescence & Senolytics",
          "description": "Mechanisms of cellular senescence and therapeutic targeting...",
          "activity_count": 47,
          "total_weight": 11253.8
        }
      ]
    }
  },
  "see_also": [
    {"from": "aging_cardiovascular_disease", "to": "cardiovascular_disease_heart_failure", "reason": "..."}
  ]
}
```

### TypeScript loader (`hierarchy.ts`)

Pattern mirrors `taxonomy.ts`:

```ts
import hierarchyData from "./hierarchy.json";
import type { HierarchyJson, SubtopicDef } from "./types";
const HIERARCHY = hierarchyData as HierarchyJson;
export function getSubtopicsForTopic(topicId: string): SubtopicDef[] {
  return HIERARCHY.topics[topicId]?.subtopics ?? [];
}
export function getTotalWeight(topicId: string, subtopicId: string): number {
  const st = HIERARCHY.topics[topicId]?.subtopics.find(s => s.id === subtopicId);
  return st?.total_weight ?? 0;
}
export function getSeeAlso(topicId: string, subtopicId: string): SeeAlsoEntry[] {
  return HIERARCHY.see_also.filter(l => l.from === `${topicId}_${subtopicId}` || l.from === subtopicId);
}
```

Bundled at build time — Next.js imports JSON natively. No DB round-trip needed. Refresh requires redeploy (same as `taxonomy.ts` today).

---

## Evaluation Harness

### Golden query set authoring (resolves review item #10)

**Process recommendation:**
1. **Author:** Paul Albert (domain knowledge of WCM research + priority queries) OR a named research-dean stakeholder. Not the primary implementer.
2. **Count:** 15 queries (mid-range of D-25's 10-20). Breakdown:
   - 5 `topic_match` on topics with subtopics (Tier 1/2 test)
   - 3 `topic_match` on topics below cold-start floor (Tier 3 fallback test)
   - 3 `topic_decompose` queries (Tier 4 renderer test)
   - 2 `gap_query` across subtopics
   - 2 `team_assembly` across subtopic-aware roles
3. **Format:** Stored as `tests/chatbot/golden-queries.json` following existing `smoke-queries.json` pattern [VERIFIED: `ReCiter-Publication-Manager/tests/chatbot/smoke-queries.json` exists].
4. **Expected answers:** Each query gets a ranked list of 5-10 expected faculty personIdentifiers. Derived by: dean-stakeholder reviews flat-topic-baseline output, edits ranking to "correct" order.
5. **Regression gate (D-27):** Script `eval_golden_queries.py` runs each query through the full pipeline, compares top-10 returned faculty to expected list. Metric: Mean Reciprocal Rank (MRR) or Recall@10. Gate: new hierarchy must not reduce either below flat-topic baseline.
6. **Invocation:** Manual pre-deploy check, NOT CI hook. Rationale: CI would need AWS creds and costs money per run. Gate runs when Pass 1 is re-run.

### Faculty self-identification spot check (D-26)

**Design:**
- Sample size: 20 faculty (statistically modest but operationally feasible).
- Selection: random sample from each quartile of `activity_count` — ensures coverage of prolific and thin profiles.
- Artifact: email/form sent to each faculty with their top 5 subtopic assignments per topic. Ask: "Do these describe your work?"
- Scoring: binary agree/disagree per faculty. Target ≥70% agreement (pilot-gate level, not production gate).
- Timing: Post-initial backfill, before Phase 3 demo. Document results in `.planning/phases/04-subtopic-system/self-identification-results.md`.

### Synthesis prompt template (resolves review item #8)

Example Tier 1 synthesis prompt fragment (to be included in `prompts/synthesis-system.ts` on top of existing Phase 2 base):

```
[INJECT: subtopic_context]
The user is asking about the subtopic "{subtopic.label}" within "{topic.label}".
Subtopic description: {subtopic.description}

Frame your response around this subtopic specifically — not the broader topic.
When citing faculty, mention which articles fall under this subtopic vs others.
```

Prompt injects `subtopic.label` and `subtopic.description` verbatim (D-15) — no further LLM processing of the subtopic def.

---

## Pilot Operationalization (Aging)

### Who runs what

| Check | Operator | Artifact |
|-------|----------|----------|
| D-20 Coverage ≥85% | Python script (`discover_subtopics.py` logs `coverage_pct` after Pass 1) | Stdout + `hierarchy_draft_aging.json` |
| D-21 ≤3 substantive corrections/subtopic | Human reviewer (Paul) during gate | Manual annotation in `hierarchy_aging_review.md` |
| D-22 Blind spot-check ≥80% agreement | Second reviewer (Sumanth or stakeholder); 10 random pmids, match to subtopics without seeing Pass 2 output | Spreadsheet, pasted into `hierarchy_aging_review.md` |
| D-23 No >40% pairwise overlap | Python script | Script output |

### SQL/Python sketches

**Coverage (D-20):**
```python
# After Pass 1 + Pass 2 on Aging
total = count(activities scoring >= 0.3 on 'aging_geroscience')
assigned = count(activities with primary_subtopic_id != null in aging partition)
coverage = assigned / total
assert coverage >= 0.85, f"FAIL D-20: {coverage:.2%}"
```

**Pairwise overlap (D-23):**
```python
# For every pair of Aging subtopics, compute Jaccard of their pmid sets
from itertools import combinations
subs = hierarchy["topics"]["aging_geroscience"]["subtopics"]
pmid_sets = {s["id"]: set(pmids_assigned_to(s["id"])) for s in subs}
for a, b in combinations(pmid_sets, 2):
    overlap = len(pmid_sets[a] & pmid_sets[b]) / min(len(pmid_sets[a]), len(pmid_sets[b]))
    assert overlap <= 0.40, f"FAIL D-23: {a} vs {b} = {overlap:.2%}"
```

Note: D-23's 40% threshold is currently arbitrary (review item #7). Pilot run will produce empirical numbers; if 40% is wildly off, revise and document.

**Blind check (D-22):**
- Second reviewer gets Google Sheet with 10 random pmids + their titles + synopses + the list of Aging subtopics (id + label + description).
- Reviewer picks 1-2 subtopics per pmid.
- Script compares reviewer picks to Pass 2 `primary_subtopic_id`.
- Agreement rate = (exact primary match) OR (primary in reviewer's top-2).

### Failure handling (D-24)

If any of D-20..D-23 fails:
1. Inspect which subtopics cluster poorly or overlap.
2. Revise discovery prompt — add more steering ("be more granular", "avoid combining distinct clinical vs basic-science threads", etc.).
3. Re-run Pass 1 for Aging only (~$0.45).
4. Re-run gates. If persistent failure, escalate to Paul for prompt rewrite.

Do NOT proceed to full 67-topic backfill until all four pilot gates pass.

---

## Proposed REQ-IDs

Planner should add these to `.planning/REQUIREMENTS.md` under a new `### Subtopic System` block before plan-authoring.

| ID | Scope |
|----|-------|
| **SUB-01** | Pass 1 Discovery — Sonnet clusters topic activities into 8-15 subtopics; supports extension to 2× with stopping criteria; outputs `hierarchy_draft_<topic>.json` with per-subtopic label, description, seed_pmids, coverage. |
| **SUB-02** | Human review gate — reviewer approves/edits `hierarchy_draft_<topic>.json` → commits as `hierarchy.json`. |
| **SUB-03** | Pass 2 Assignment — per-activity Haiku emits primary_subtopic_id + confidences; tiebreaker defaults applied in post-processing; confidence floor (default 0.3). |
| **SUB-04** | Pass 3 Aggregation — arithmetic sum of primary-only articleScores into faculty `subtopic_scores{}`; writes FACULTY# records. |
| **SUB-05** | See-also generation — one-shot Sonnet batch, bidirectionality filter, stored in `hierarchy.json` top-level. |
| **SUB-06** | DynamoDB schema migration — `subtopic_ids[]`, `primary_subtopic_id`, `subtopic_confidences{}` on activity records; `subtopic_scores{}` on faculty records; legacy tolerance. |
| **SUB-07** | Post-classifier — Haiku pass after main router that resolves `subtopics[]` + `tier` + `topic_decompose` flag; latency budget ≤500ms; falls back to Tier 3 on failure. |
| **SUB-08** | Four-tier retrieval dispatch — `retrieval/index.ts` routes to subtopic.ts (Tier 1/2), topic.ts (Tier 3), or topic-decompose.ts (Tier 4) based on post-classifier output. |
| **SUB-09** | Subtopic-level synthesis — Tier 1/2 prompts inject subtopic label + description verbatim; frame response at subtopic level. |
| **SUB-10** | Topic-decompose renderer — stateless function, no LLM call, emits structured JSON consumed by Phase 3 UI. |
| **SUB-11** | Hierarchy.json loader — `hierarchy.ts` exports `getSubtopicsForTopic`, `getTotalWeight`, `getSeeAlso`; bundled at build time. |
| **SUB-12** | Aging pilot gate — script checks D-20..D-23 before full backfill. |
| **SUB-13** | Full backfill — runs Pass 1→see-also→2→3 across all 67 qualifying topics in activity-count-descending order. |
| **SUB-14** | Golden query set + regression harness — `tests/chatbot/golden-queries.json` + `eval_golden_queries.py` enforces MRR/Recall@10 non-regression. |
| **SUB-15** | Faculty self-identification spot check — 20-faculty survey post-backfill; agreement rate documented. |
| **SUB-16** | Rollout UX during partial backfill — post-classifier returns `partial: true` for topics without hierarchy; message "Hierarchy pending for <topic>; falling back to topic-level results." |
| **SUB-17** | `total_weight` floor calibration — documented in `calibration-notes.md`; frozen value in `config/chatbot.ts` as `WEIGHT_FLOOR`. |
| **SUB-18** | Cost table update — post-classifier per-query ($0.0005) added to runtime cost model. |

Total: 18 new requirements. Map to phase 4 in REQUIREMENTS.md traceability table.

---

## Pitfalls

### P-01: Zero-weight secondaries break intra-subtopic ranking for secondary assignments
**What goes wrong:** An activity whose primary is subtopic A but with a strong secondary in subtopic B contributes zero to B's faculty scores. A faculty member whose work is genuinely distributed across two subtopics will appear in their primary subtopic but not their secondary — breaking breadth representation.
**Why it happens:** D-03 design tradeoff for comparability across subtopics within a topic.
**How to avoid:** Document explicitly in code comments on `aggregate_subtopic_scores.py` AND in user-facing `/chat` disclaimer when surfacing subtopic rankings. Review item #3.
**Warning signs:** Faculty complaints during self-ID check ("I work on X, why am I not listed under X?"). If >20% of self-ID disagreements trace to this, revisit fractional-weight decision for v2.

### P-02: Schema migration + live production reads
**What goes wrong:** Backfill writes partial subtopic fields to activity records while live queries read them. Retrieval code that hard-references new fields without absent-tolerance will crash.
**Why it happens:** DynamoDB attributes are schemaless — adding fields is free, but reads must defend against absence.
**How to avoid:** D-18 compatibility rule. ALL new read paths use `?? default` coercion pattern already established in `itemToTopicMatch` (shared.ts:220-237). Write tests that mock legacy-absent records.
**Warning signs:** `undefined` leaking into synthesis prompts. Pitfall 4-style silent mismatches.

### P-03: Non-determinism in Sonnet clustering across reruns
**What goes wrong:** Cluster IDs change across recomputes (D-06). Any cached Tier 1 retrieval or UI bookmark pointing at a specific subtopic_id breaks.
**Why it happens:** No stable ID scheme by design.
**How to avoid:** temperature=0 for discovery; document in PR message that recompute invalidates external references. Phase 3 UI must not persist subtopic_ids in user bookmarks. Cache TTL on CONV# records (existing D-21 from Phase 2) short enough to not reference stale IDs.
**Warning signs:** Tier 1 retrieval returning zero results for a subtopic_id that existed in a previous run.

### P-04: Cost blowup on Pass 2 without batching
**What goes wrong:** Naive serial Haiku calls on 30K activities would take ~8-10 hours and risk rate limiting.
**Why it happens:** No orchestration.
**How to avoid:** ThreadPoolExecutor with 10-20 concurrent calls (Phase 1 precedent). Exponential backoff in `utils/bedrock_client.py` handles 429s. Checkpoint via `UpdateItem` per-activity so crashes resume cleanly.
**Warning signs:** Bedrock quota errors. Cost >$30 (should be $12-15).

### P-05: Aliases layer (`taxonomy_aliases.json`) scope ambiguity
**What goes wrong:** CONTEXT lists `taxonomy_aliases.json` as a "new aliases layer (deferred from design-questions doc; may or may not be part of this phase)". If planner assumes it's in scope without clarifying, work expands unexpectedly. If planner assumes it's out of scope without clarifying, `resolveTopicId` mechanical matching continues to be brittle when subtopics multiply the namespace.
**Why it happens:** Scope ambiguity in CONTEXT.
**How to avoid:** **Planner MUST explicitly confirm in/out during plan-discuss.** Recommendation: defer to a separate phase. Subtopics already multiply the resolution surface; coupling alias resolution adds risk.

### P-06: DynamoDB Decimal coercion on new fields
**What goes wrong:** Pass 2 emits float confidences (0.87). Writing them to DynamoDB without Decimal coercion fails with `TypeError: Float types are not supported`.
**Why it happens:** Phase 1 Pitfall 1 already documented this for score fields.
**How to avoid:** Reuse existing `to_decimal()` helper. Apply to every numeric field in subtopic_confidences{} and subtopic_scores{}.
**Warning signs:** `TypeError` from boto3 client. Test writes with round-trip fetch.

### P-07: Cold-start floor topics silently skipped
**What goes wrong:** Topics below 30-activity floor never get subtopic fields. Post-classifier sees no subtopic defs, returns Tier 3, user wonders why `topic_decompose` says "no subtopics".
**Why it happens:** D-01 explicit design — but not surfaced in UI.
**How to avoid:** `hierarchy.json` includes `excluded_topics: [...]` with reason. Topic-decompose renderer emits explicit "This topic has too few indexed activities to decompose" message when queried. Review item #5.
**Warning signs:** User confusion in Phase 3 demo. Pre-empt with UX copy.

### P-08: Post-classifier failure cascades to wrong tier
**What goes wrong:** Post-classifier Haiku call fails (rate limit, parse error) and pipeline defaults wrong. Defaulting to Tier 1 with a null subtopic_id breaks retrieval; defaulting to Tier 3 loses subtopic precision silently.
**Why it happens:** Haiku occasional JSON fence issue already seen in Phase 2 (Plan 02-08 Deviation 2).
**How to avoid:** Reuse `stripJsonFences` helper from `router.ts`. Defensive coercion with explicit fallback to Tier 3 (safer degradation). Log every fallback via `logStage`.
**Warning signs:** Silent precision loss. Monitor `POST_CLASSIFIER_FALLBACK` log count.

### P-09: Bidirectionality filter drops asymmetric-but-valid links
**What goes wrong:** Aging → cardiovascular disease is intuitive; cardiovascular disease → aging may not always be Sonnet-proposed even when true. Valid links dropped.
**Why it happens:** Natural asymmetry in scientific domain relationships.
**How to avoid:** Acknowledge in code comments on `generate_see_also.py`. If persistent loss of intuitive links, add a second-pass prompt asking Sonnet to check: "Given the link A→B, does B→A also make sense?" and populate missing direction.
**Warning signs:** Review of first see-also output shows obvious missing links.

### P-10: `articleScore` recomputation drift between Python and TypeScript
**What goes wrong:** Pass 3 computes articleScore in Python; `itemToTopicMatch` recomputes in TypeScript. If formulas drift, faculty scores computed by Pass 3 disagree with live-query ranking.
**Why it happens:** Dual implementation.
**How to avoid:** Treat Python as source of truth for Pass 3 FACULTY# writes. TypeScript `itemToTopicMatch` remains for Tier 3 topic-level ranking from raw item attributes; Tier 1/2 reads `subtopic_scores` dict directly (already aggregated). Never recompute in TS. Test: property-based test that Python and TS implementations agree on sample inputs (documented once, not run in CI).
**Warning signs:** Faculty ranking differs between subtopic query and topic query for the same domain.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Bedrock Sonnet 4.6 pricing ~$3/M input, $15/M output | Pass 1 cost sanity | Cost estimate off by 2-5×; still within budget envelope. Verify before budget sign-off. |
| A2 | Bedrock Haiku 4.5 pricing makes per-activity call ~$0.0004 | Pass 2 batching | If higher, $30 ceiling breached. Verify via short 100-activity test before full backfill. |
| A3 | Haiku latency ~300-800ms for small prompts | Post-classifier latency budget | If higher, 500ms target missed. Measure during Wave 0. |
| A4 | Synopsis length ~150-400 tokens | Pass 1 token budget math | If longer, topic-level Sonnet call exceeds 200K context for large topics (>400 activities). Mitigate with batching. |
| A5 | Post-classifier as flag-on-topic_match, not 10th query_type | Router post-classifier | Under CONTEXT D-11 ("main router unchanged") this is the right read, but planner should lock during plan-discuss. |
| A6 | Option A (FilterExpression) for subtopic retrieval is performant enough for v1 | Four-tier retrieval dispatch | If Tier 1 latency >1s, revisit with GSI (Option B). Measure during Aging pilot. |
| A7 | 40% pairwise overlap threshold (D-23) is directionally right | Pilot operationalization | If Aging shows all subtopics overlap >40%, threshold may be too tight. Revise empirically. |
| A8 | Golden query set authored by Paul Albert or research-dean stakeholder | Evaluation harness | Organizational question; if no stakeholder named, falls to implementer — weaker ground truth. |
| A9 | 20-faculty self-ID sample size is sufficient | Faculty self-identification | If response rate low (<50%), sample effectively ~10, statistical power weak. Plan larger invite. |
| A10 | Cluster size floor = 3 activities (review item #1) | Pass 1 prompt | If Aging has meaningful 2-activity themes, floor drops them. Revise after pilot if reviewer flags missed themes. |

---

## Open Questions (RESOLVED)

1. **Is `topic_decompose` a new router query_type or a post-classifier flag?**
   - **RESOLVED:** post-classifier detection (preserves D-11 literally); confirm in plan-discuss.
   - What we know: CONTEXT D-11 says main router is unchanged.
   - What's unclear: Whether the planner should still add a 10th query_type for cleaner dispatch, OR keep post-classifier detection.
   - Recommendation: post-classifier detection (preserves D-11 literally); confirm in plan-discuss.

2. **Is `taxonomy_aliases.json` in scope for Phase 4?**
   - **RESOLVED:** Defer. Subtopic namespace already expands resolution surface; aliases layer is a distinct problem (colloquial-label mapping vs subtopic hierarchy). See pitfall P-05.
   - What we know: CONTEXT calls it "adjacent but not automatically in scope".
   - What's unclear: Whether to couple or defer.
   - Recommendation: Defer. Subtopic namespace already expands resolution surface; aliases layer is a distinct problem (colloquial-label mapping vs subtopic hierarchy). See pitfall P-05.

3. **Who authors the golden query set?**
   - **RESOLVED:** Paul Albert + one research-dean stakeholder review. Lock during planning.
   - What we know: D-25 says "established before subtopic code ships" but no author named.
   - What's unclear: Paul Albert, Sumanth, named research dean, or default to implementer?
   - Recommendation: Paul Albert + one research-dean stakeholder review. Lock during planning.

4. **Does the regression gate run in CI or only pre-deploy?**
   - **RESOLVED:** Manual pre-deploy. Script `eval_golden_queries.py` is first-class deliverable; CI hook is future work.
   - What we know: D-27 says "on every recompute, must pass before production deployment".
   - What's unclear: Automated CI trigger requires AWS creds + incurs cost.
   - Recommendation: Manual pre-deploy. Script `eval_golden_queries.py` is first-class deliverable; CI hook is future work.

5. **What is the post-classifier's authentication context?**
   - **RESOLVED:** No new auth surface — post-classifier runs server-side in the same request as main router; NextAuth JWT already established at handler entry (Plan 02-08). Planner to verify during Plan 07.
   - What we know: Post-classifier runs server-side in the same request as main router; auth already established by NextAuth JWT at handler entry (Plan 02-08).
   - What's unclear: Nothing — but planner should verify no new auth surface is opened.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.14 + boto3 | Pass 1/2/3 scripts | yes (Phase 1 precedent) | Phase 1 stack | — |
| AWS Bedrock (Sonnet 4.6 + Haiku 4.5) | Pass 1/2, post-classifier | yes (Phase 2 smoke validated) | pinned model IDs | — |
| DynamoDB `reciterai-chatbot` table | All passes read/write | yes (deployed) | on-demand capacity | — |
| ReciterDB MariaDB read-only | Pass 1 reads synopses | yes (Phase 1 precedent) | existing creds | — |
| `@aws-sdk/lib-dynamodb` TS SDK | PM subtopic retrieval | yes (Phase 2 installed) | existing version | — |
| Next.js 14 PM worktree on `feature/chatbot-runtime` | PM-side code | yes (STATE confirms active branch) | — | — |

**Missing dependencies with no fallback:** None.
**Missing dependencies with fallback:** None.

---

## Sources

### Primary (HIGH confidence)
- `.planning/phases/04-subtopic-system/04-CONTEXT.md` — 28 user-locked decisions, 10 review items, canonical refs
- `.planning/phases/01-offline-pipeline/01-CONTEXT.md` — DynamoDB schema + TTL + script structure precedent
- `.planning/phases/02-chat-runtime/02-08-SUMMARY.md` — Phase 2 complete inventory, smoke test outcomes, taxonomy resolver behavior
- `.planning/STATE.md` — Current state, Decimal coercion pattern, concurrency expectations, `personIdentifier` rename, enrichment import
- `CLAUDE.md` (repo-level) — Conventions, scoring scopes, table rules, model pinning
- `ReCiter-Publication-Manager/controllers/chatbot/taxonomy.ts` — 67 topic canonical list
- `ReCiter-Publication-Manager/controllers/chatbot/retrieval/shared.ts` — `WCM_FACULTY_UID_PREFIX`, `itemToTopicMatch`, `TopicMatch` interface, articleScore computation
- `ReCiter-Publication-Manager/controllers/chatbot/prompts/router-system.ts` — router prompt boundary, 9 query types
- `ReCiter-Publication-Manager/controllers/chatbot/retrieval/topic.ts` — RET-01 baseline pattern for subtopic retrieval

### Secondary (MEDIUM confidence)
- Phase 1 D-05 taxonomy batching (justifies Pass 1 batching approach for large topics)
- Phase 2 Plan 02-08 Deviation 2 (Haiku JSON fence issue — justifies `stripJsonFences` reuse in post-classifier)

### Tertiary (LOW confidence — flagged in Assumptions Log)
- Bedrock per-token pricing (A1, A2) — not verified against live pricing page in this session
- Haiku latency estimate (A3) — inferred from Phase 2 smoke aggregate latency, not direct measurement

---

## Metadata

**Confidence breakdown:**
- Pipeline architecture: HIGH — grounded in Phase 1/2 code + CONTEXT decisions
- Pass 1/2/3 algorithms: HIGH — arithmetic pass is deterministic; Pass 1/2 follow established Phase 1 batching precedent
- Prompt wording: MEDIUM — final wording is Claude's discretion per CONTEXT; recommendations provided
- Cost sanity: MEDIUM — pricing assumed from training data (A1, A2)
- Calibration numbers (WEIGHT_FLOOR, overlap threshold): LOW — empirical calibration from Aging pilot required
- Golden query authorship process: LOW — organizational decision

**Research date:** 2026-04-13
**Valid until:** 2026-05-13 (30 days — Phase 2 code is stable; only Bedrock pricing + Aging pilot data change underlying assumptions)

---

## RESEARCH COMPLETE

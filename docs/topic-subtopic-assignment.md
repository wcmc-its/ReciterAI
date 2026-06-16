# Topic & Subtopic Assignment Pipeline

This document describes how publications get assigned to topics and subtopics: where the taxonomy comes from, how the per-paper labels are produced, how the layers are stored, and what they actually mean (mutually exclusive? multi-label? primary?).

It complements `taxonomy-methodology.md`, which covers the design *principles* behind the taxonomy. This doc covers the *mechanics* of generation and assignment.

## Plain-language summary — "How are topics assigned?" (Scholars About page)

> Copy approved for public, faculty-facing use (e.g., the SPS Scholars About page). Keep it jargon-free; the technical detail lives in the sections below.

Topics aren't chosen from a fixed list — they're derived from the research itself. Rather than borrowing a standard subject classification, the system reads across plain-language summaries of every Weill Cornell publication and lets the major research domains emerge from what's actually there — areas like Cardiovascular Disease, Immunology, and Cancer Biology. The AI consolidates overlapping areas and validates the result against a set of representative queries, so the domains hold together without being hand-built. As an independent check, that map was benchmarked against authoritative institutional reference points — Weill Cornell's divisions and departments, its strategic research roadmap, and NIH research designations — and aligned cleanly with all three, confirming that what the AI surfaced from the literature mirrors how the institution and the wider field already organize science. Within each domain, the same approach surfaces more specific subtopics, and every publication is scored for how strongly it relates to each topic. Because real research often spans several areas, a single paper can be associated with more than one. The topics shown on a scholar's profile reflect the balance of their published work across these areas, and they update automatically as new publications are added.

## 1. Two-Axis Architecture

The classification system is two orthogonal axes, not a single hierarchy:

| Axis | What it answers | Source |
|---|---|---|
| **Axis 1 — Topics** (~67) | "What domain is this paper in?" | `taxonomy_v2.json`, generated inductively from synopses |
| **Axis 1.5 — Subtopics** (~8–15 per topic) | "Within this domain, which theme?" | Per-topic inductive clustering from scored activities |
| **Axis 2 — Tools** | "What methods/techniques?" | LLM-extracted from faculty publication/grant abstracts, then deduped → classified → grouped into method families (A2 pipeline) |

Topics and subtopics together form an inductive hierarchy: subtopics are scoped to their parent topic (D-05). Tools are produced by a completely separate pipeline (LLM extraction from abstracts → canonical-tool dedup → classification → ~878 method families; see [`tools-a2-architecture.md`](./tools-a2-architecture.md)) and are not covered here.

## 2. Where Topics Come From

Generator: `generate_taxonomy.py`. The output is `taxonomy_v1.json` (initial) → `taxonomy_v2.json` (current, 67 topics, `taxonomy_version: "taxonomy_v2"`).

The taxonomy is **derived from the corpus**, not authored from a list of biomedical disciplines:

### Phase 1 — Extract & cluster

- Pull all publication synopses from ReciterDB via `SYNOPSIS_EXTRACTION_SQL`
- Batch synopses (default 75 per batch) through Claude Sonnet on AWS Bedrock
- Each batch produces raw topic candidates that Sonnet observes in the synopses
- Result: ~150 raw topics across all batches

### Phase 2 — Consolidate

- Second Sonnet pass collapses the ~150 raw topics into ~50–70 final topics
- Merges near-synonyms, broadens hyper-specific topics (avoiding the "tau-pathology" brittleness problem — see CLAUDE.md taxonomy principle)
- Each topic gets `id`, `label`, and a 2–4 sentence `description` that will later be injected verbatim into screening and scoring prompts

### Phase 3 — Validate

- 12 hardcoded research-dean queries (`generate_taxonomy.py:51`) are run against the candidate taxonomy
- Examples: "Who works on aging and cognitive decline?", "Find researchers using CRISPR gene editing", "Who studies neurodegeneration and Alzheimer's disease?"
- If any query has no plausible topic match, the taxonomy fails validation and must be regenerated
- **Convergent-validity sanity check:** beyond the automated query pass, the emergent domain set was benchmarked against external institutional reference points — WCM divisions/departments, the institutional research roadmap, and NIH research designations — and found to align with all three. These sources were a corroboration cross-check, **not** an input: the domains were derived inductively from the corpus, then confirmed to mirror how the institution and NIH already organize research.

### Human review gate (D-07)

- `taxonomy_v1.json` is **frozen for human review** before any scoring runs
- Bad taxonomy wastes the ~$170 Bedrock dense-scoring budget at full scale
- After review, the taxonomy is locked with a `taxonomy_version` field

### Topic ID stability

Topic IDs **are stable** across the lifecycle of a `taxonomy_version`. Every score record in DynamoDB carries the `taxonomy_version` it was scored against, so taxonomy bumps trigger *targeted* recomputation rather than a full rebuild.

### Adding a single topic later (additive path, #225)

A genuinely new domain can be minted into the existing taxonomy without re-running the full generate-and-score pipeline. Hematology & Medical Oncology (taxonomy `67 → 68`) was added this way.

Generator: `cli/score_new_topics.py`.

- Hand-add the new topic to `taxonomy_v2.json`, then score **only that topic** against a small set of "context" competitor topics for contrastive routing (so a heme/onc paper isn't mis-routed to a neighboring cancer topic).
- Same two-pass shape as the full scorer (Haiku screen ≥ 0.3 → Sonnet dense), reusing `score_publications.py`'s prompts, plus a **per-topic rubric** in `config/rubrics/<topic_id>.txt` (decision order + score bands) that restores routing fidelity lost when a topic is scored in isolation. `--verify-fidelity` quantifies the delta on a sample before any writes.
- Writes are **additive**: new `TOPIC#` rows (never a delete-and-rewrite), affected `FACULTY#` rows merged from the live `FacultyIndex` GSI (the ephemeral scoring-results file is not the source of truth), and a refreshed `TAXONOMY#{version}` record.
- Cost is roughly an order of magnitude below a full run, but note it is **not** trivial: per-call cost is content-dominated, so scoring one topic across the corpus is still on the order of a full pass's per-paper spend.

## 3. How Papers Get Assigned to Topics

Two-pass, offline, batch-processed. Lives in `score_publications.py`.

### Pass 1 — Haiku screening (recall-first)

- **Input**: one publication's synopsis + the full taxonomy (all ~67 topics in a single prompt)
- **Model**: Claude Haiku via Bedrock Batch API
- **Output**: JSON map `{topic_id: score}` for every topic the model finds plausible
- **Threshold**: `SCREENING_THRESHOLD = 0.3` (`score_publications.py:61`)
- **Rubric (from the prompt)**:
  - 0.9–1.0: primary focus of the publication
  - 0.3–0.5: meaningful but secondary (methods used, population studied, comorbidity)
  - <0.3: dropped, not stored, not advanced
- **Principle**: "err on the side of inclusion. A second pass will refine scores." Recall over precision.
- **Cost**: ~$0.0005/activity

### Pass 2 — Sonnet dense scoring (precision)

- **Input**: only the topics that cleared 0.3 in Pass 1 (typically 30–40% of activities; per-activity, only the topics that passed)
- **Model**: Claude Sonnet via Batch API
- **Output**: calibrated 0.0–1.0 score per topic + ≤80-char rationale
- **Cost**: ~$0.008/activity

### Result

Each publication carries a **vector of scores** across topics. There is **no single "primary topic"**. A cardiology paper that touches genomics and aging gets three records (one per topic), each above 0.3 — all three are persisted.

### Floor vs display — two different decisions (#69)

The 0.3 cutoff above is the *qualification* floor (`score_floor`): it decides whether a paper's score for a topic is high enough for the paper to be persisted as a topic-activity row at all. It is recall-oriented, screening-level.

A separate *display* threshold (`display_threshold`, per-topic, default 0.5) governs what renders by default on the SPS Scholars Topic page (`/topics/<topic_id>`). Papers with `score[topic] >= display_threshold[topic]` appear in the strongly-relevant default view; papers with `score_floor <= score[topic] < display_threshold[topic]` appear only behind the "View additional articles that are relevant" affordance.

- `score_floor` lives in `config/thresholds.json`.
- `display_threshold` is carried per-topic on `taxonomy_v2.json` and republished in `hierarchy.json` for SPS to consume; the global fallback for untuned topics is `display_threshold_default` in `config/thresholds.json` (currently 0.5).
- `display_threshold` is consumed at SPS read time only. It does **not** change qualification, does not change `subtopic_ids[]`, does not change §8 rollup arithmetic, and does not change which records are persisted. Per-topic tuning is a separate, paced workstream (see `docs/topic-page-inclusion-threshold.md`).

## 4. How Topic Scores Are Stored

DynamoDB, topic-first key layout for the dominant query pattern ("give me all activities above threshold for topic X"):

```
PK: "TOPIC#aging_geroscience"
SK: "SCORE#0.95#ACTIVITY#pmid_38765432"
{
  faculty_uid: "cwid_jsmith",
  score: 0.95,
  activity_type: "publication",
  synopsis: "...",
  impact_score: 0.87,
  year: 2023,
  taxonomy_version: "taxonomy_v2"
}
```

Key properties:
- Score is zero-padded in the SK so DynamoDB range queries (`score >= threshold`) work naturally
- Only records with `score >= 0.3` are persisted — keeps the table lean
- `taxonomy_version` on every record enables version-scoped invalidation when the taxonomy changes
- Same key structure used for tools (`PK: "TOOL#whole_genome_sequencing"`)

Multi-topic intersection (e.g., "aging AND genomics") is handled by querying each topic separately and intersecting `faculty_uid`s in application code. Fine at WCM's scale.

## 5. Where Subtopics Come From

Generator: `discover_subtopics.py` (Pass 1 of the subtopic pipeline).

Subtopics are **derived per-topic from the activities scored against that topic**. There is no global subtopic list; each parent topic gets its own 8–15 subtopics, scoped to that topic only (D-05).

### Inductive discovery

For each topic:

1. Query DynamoDB for all `TOPIC#<topic_id>` records with `score >= 0.3`
2. Send the activities (title + synopsis + impact + relevance) to Sonnet with `DISCOVERY_SYSTEM_PROMPT` (in `prompts/subtopic_discovery.py`), **temperature=0** for reproducibility (D-01)
3. Sonnet returns 8–15 clusters: `{id, label, description, seed_pmids, coverage_estimate}` + an `uncovered` list of PMIDs that didn't fit cleanly

### Hard constraints in the prompt

- Minimum **3 seed PMIDs** per cluster — anything thinner stays uncovered
- Activities that don't fit a coherent cluster go to `uncovered`, **not** forced into a weak bucket
- "Do NOT invent subtopics not supported by the activities provided" — the CLAUDE.md taxonomy principle repeated verbatim in the discovery prompt
- Subtopic IDs follow `{topic_prefix}_{slug}` (e.g., `aging_cellular_senescence`)

### Coverage gate (D-01)

- Target: ≥85% of input activities assigned to some cluster
- If first pass falls short, run an **extension pass** with `DISCOVERY_EXTENSION_PROMPT` (up to 2× the initial cluster count cap)
- If still short, the gap is accepted — better to leave activities unassigned than fabricate subtopics

### Human review gate

- Output: `.planning/phases/04-subtopic-system/hierarchy_draft_<topic_id>.json`
- Reviewed by a human (typically the research dean or domain expert) before Pass 2 assignment runs
- On Bedrock JSON-parse failure, the raw response is saved as `hierarchy_draft_<topic_id>.raw.txt` for debugging

### Subtopic ID stability — important caveat

**Subtopic IDs are NOT stable across recomputes** (D-06, called out at `discover_subtopics.py:_slugify`).

> Subtopic IDs may change on each full recompute. Consumers MUST re-read `hierarchy.json` after regeneration and MUST NOT persist subtopic IDs in external systems that outlive a recompute cycle.

This is the opposite of topic IDs (which are stable within a `taxonomy_version`). The reasoning: subtopics are wholesale-regenerated when their parent topic's activity set shifts meaningfully, and trying to preserve IDs across runs would lock in stale clusters.

#### Durable opaque IDs — the stability layer (#191)

The unstable slugs above are the *clustering-time* identity. A separate **durable-ID layer** (issue #191, "Option C") sits on top to give consumers a stable handle across re-clustering: each subtopic gets a mint-once opaque id (`st_` + 26 random chars) that survives label/slug churn. After each hierarchy publish, a post-upload **match-or-mint reconcile** maps the new run's slugs back to existing durable ids via a deterministic-first cascade — Stage 1 seed-PMID membership overlap → Stage 2 label-embedding centroid (Titan v2) → Stage 3 LLM arbiter (Sonnet, OpenAI fallback; verdicts cached by *stable content hash*, never by id or mint order) — and records split/merge lineage edges. The bricks (A mint-store, B reconcile, C lineage, D consumer alias map, E relabel/lede skip-logic, F lifecycle) are merged **flag-off**; gate-on is pending a prod cold-run (see #204). Full contract: `docs/subtopic-durable-id-store.md` and `docs/subtopic-lifecycle-and-evolution.md`.

## 6. How Papers Get Assigned to Subtopics

Assigner: `assign_subtopics.py` (Pass 2). Different shape from topic scoring — this is a **classifier**, not a relevance scorer.

### Per-activity Haiku classification

- One Haiku call per unique PMID
- Input: the activity (title + synopsis) + the reviewed subtopic hierarchy for its parent topic
- Output: confidence map `{subtopic_id: confidence}` plus the model's chosen primary

### What gets written to the activity record

Three fields (`assign_subtopics.py:17`):

| Field | Shape | Meaning |
|---|---|---|
| `subtopic_ids[]` | list of strings | All subtopics that cleared the confidence floor (default 0.35) |
| `primary_subtopic_id` | string | The single best subtopic for navigation/rollup views |
| `subtopic_confidences{}` | map | Full confidence distribution from the classifier |

### Both aggregations (Phase 12 §8)

Exclusive aggregation rolls up each activity row's `primary_subtopic_id` only; inclusive aggregation rolls up every entry in `subtopic_ids[]` at full `article_score` per entry. Invariant (D-17): `sum(SUBTOPIC_SCORE_INCLUSIVE#X#*) ≥ sum(SUBTOPIC_SCORE#X#*)`; equality iff every paper in topic X has exactly one above-floor subtopic assignment. The difference equals the total `article_score` contributed by secondary assignments in topic X. The reconciliation gate (`gates/reconciliation.py`) verifies this invariant before publish; a separate aggregator-internal invariant (D-33) verifies per-CWID per-subtopic equality between the legacy `faculty.subtopic_scores` map and the new `SUBTOPIC_SCORE#` partition.

### Primary selection logic

- Default: `primary = argmax(subtopic_confidences)`
- **Tiebreaker** (`_resolve_primary_on_tie`, `assign_subtopics.py:92`): when the top-2 confidences are within `TIE_EPSILON`:
  1. Higher **total_weight** subtopic wins (signal density — favors the cluster with more aggregate evidence)
  2. If still tied, alphabetically lower `subtopic_id` wins
- Tiebreaker runs in Python, not in the Haiku prompt — keeps the model's job simple and the tiebreak deterministic

### Unassigned is a valid outcome

If no subtopic clears the confidence floor, the activity gets an empty `subtopic_ids[]` and no `primary_subtopic_id`. These are **not** errors — they're activities the LLM correctly refused to force-fit. Resume runs skip activities that already have a `primary_subtopic_id` set (`--resume` flag).

### Per-paper `top_topic_id` — observational, not a designation (#68)

Each activity record carries a derived `top_topic_id` field equal to the topic with the highest score in the paper's topic-score vector among topics that cleared `score_floor`. This is a **read-time convenience** for the SPS Scholars Topic page (`/topics/<topic_id>`), which is inclusive-multi-label by design (every above-floor topic shows the paper); `top_topic_id` lets the UI render an honest one-bit "Top topic: X" affordance inline.

`top_topic_id` is **not** a designation, **not** a rollup input, and **not** an authoritative claim that the paper is "about" that topic. The system remains multi-label and continues to assert there is no single "primary topic" (§3). The §8 exclusive rollup is unaffected — it operates on `primary_subtopic_id`, not on `top_topic_id`.

Tiebreak (mirrors the per-subtopic pattern above, at the topic level): when the top-2 topic scores fall within `tie_epsilon`, higher `sum(subtopic_confidences[topic])` wins (topic-level signal density); if still tied, alphabetically lowest `topic_id` wins. Deterministic, runs in Python, reproducible across runs given identical scores.

Papers with no above-floor topic carry no `top_topic_id`. Producer: `compute_top_topic.py` (cold path: stage after `relabel`; hot path: state between `Assign` and `Rollup`).

#### Three-fields disambiguation

The taxonomy now has two notions of "primary" (both unchanged) plus one new observational field. They are categorically different concepts:

| Field | Scope | Kind | Source |
|---|---|---|---|
| `primary_subtopic_id` | per paper, per topic | **Designation** — assignment-time, with deterministic tiebreak | `assign_subtopics.py:_resolve_primary_on_tie` |
| §8 exclusive rollup aggregation | per CWID, per (sub)topic | **Aggregation rule** — operates on `primary_subtopic_id` | `aggregate_subtopic_scores.py` |
| `top_topic_id` | per paper | **Observational** — argmax of topic-score vector, with deterministic tiebreak | `compute_top_topic.py` (#68) |

## 7. Mutual Exclusivity — The Honest Answer

Neither layer is strictly mutually exclusive, despite the design doc (`RECITER_AI_CHATBOT_README.md:225`) claiming subtopics are. The implementation evolved past that. Here's the actual shape:

| Layer | Mutually exclusive? | What you actually get |
|---|---|---|
| Topics | **No** — multi-label by design | Vector of scores; all topics ≥0.3 are persisted |
| Subtopics | **No** — multi-label, but with a designated primary | `subtopic_ids[]` (list) + `primary_subtopic_id` (single) |

### Practical implication for rollups

For "papers in subtopic X" counts, you have a deliberate choice to make:

- **Use `primary_subtopic_id == X`** → mutually exclusive; each paper appears in exactly one subtopic bucket; rollup totals sum cleanly to the topic total
- **Use `X in subtopic_ids`** → inclusive; papers can appear in multiple subtopic buckets; better for "find anything related to X" use cases

The CSVs in the repo root (`cwid_subtopic_counts.csv`, `cwid_topic_counts.csv`) made one of these choices. Verify which before using those numbers anywhere load-bearing.

### Why both axes are multi-label

- **Topics**: biomedical research is intrinsically multi-domain. A paper on "social determinants of cardiovascular outcomes in aging populations" legitimately scores high on cardiovascular_disease, health_equity, and aging_geroscience. Forcing single-label loses information that the synthesis LLM needs at query time.
- **Subtopics**: same logic at finer granularity, plus the system needs a single canonical bucket for navigation and per-subtopic summaries. Hence the `primary_subtopic_id` compromise.

## 8. Versioning & Recomputation

| Layer | Version field | Stability | Recompute trigger |
|---|---|---|---|
| Topic taxonomy | `taxonomy_version` (e.g., `taxonomy_v2`) | Stable within a version | New topics added, descriptions materially changed |
| Topic scores | `taxonomy_version` on each record | Stable | Activity synopsis changes, taxonomy bump |
| Subtopic hierarchy | Wholesale replacement | **IDs not stable across recomputes** | Topic's activity set shifts ~20%+ |
| Subtopic assignments | Tied to current hierarchy | Re-run after hierarchy refresh | New hierarchy published |

Cost of a full taxonomy-version bump (per `RECITER_AI_CHATBOT_README.md`): ~$170 at 50k activities. Incremental for new activities: ~$0.01 each.

## 9. Cost Summary

| Stage | Model | Per-activity cost |
|---|---|---|
| Topic screening | Haiku (Batch) | ~$0.0005 |
| Topic dense scoring | Sonnet (Batch) | ~$0.008 (only on ~35% that pass screening) |
| Subtopic discovery | Sonnet | ~$0.20–$1.00 per topic (whole-corpus, not per-activity) |
| Subtopic assignment | Haiku | ~$0.0005 per activity |

Full initial build at WCM scale: ~$170. Annual maintenance: ~$50–80.

## 10. Key Files

| File | Role |
|---|---|
| `generate_taxonomy.py` | Inductive taxonomy generation (3 phases + validation) |
| `cli/score_new_topics.py` | Additive single-topic minting (no full re-run); `config/rubrics/<topic>.txt` per-topic rubrics |
| `taxonomy_v1.json` / `taxonomy_v2.json` | Locked taxonomy artifacts (D-07 review gate output) |
| `score_publications.py` | Two-pass topic scoring (Haiku screening → Sonnet dense) |
| `utils/bedrock_client.py` | Current model pins (`MODEL_IDS_BY_STAGE`): screening/assignment → Haiku 4.5; scoring/discovery/relabel/reconcile → Sonnet 4.6; spotlight lede → Opus 4.7 |
| `pipeline_hierarchy/` + `docs/subtopic-durable-id-store.md` | Hierarchy assembly/publish + durable opaque subtopic IDs & match-or-mint reconcile (#191) |
| `discover_subtopics.py` | Per-topic inductive subtopic clustering (Pass 1) |
| `prompts/subtopic_discovery.py` | Discovery + extension prompts; encodes the "no speculative subtopics" rule |
| `assign_subtopics.py` | Per-activity Haiku subtopic classifier (Pass 2), primary selection + tiebreaker |
| `utils/dynamodb_subtopic_migration.py` | Writes `subtopic_ids[] / primary_subtopic_id / subtopic_confidences{}` to activity records |
| `.planning/phases/04-subtopic-system/` | Phase 4 plans, summaries, and hierarchy drafts |
| `docs/hierarchy-contract.md`, `docs/hierarchy.schema.json` | Hierarchy data contract for downstream consumers |
| `docs/taxonomy-methodology.md` | Companion doc — design *principles* (this doc covers the *pipeline*) |

## 11. Design Decisions Referenced

The codebase uses `D-NN` markers to reference Phase 4 design decisions. The ones cited here:

- **D-01** — Temperature=0 for subtopic discovery; ≥85% coverage target; extension pass cap at 2×
- **D-05** — Subtopics scoped to parent topic only; no cross-topic clustering
- **D-06** — Subtopic IDs not stable across recomputes; wholesale replacement on regenerate
- **D-07** — Human review gate on `taxonomy_v1.json` before scoring spend
- **D-15** — Subtopic descriptions injected verbatim into synthesis prompts
- **D-16** — Pass 2 writes `subtopic_ids[]`, `primary_subtopic_id`, `subtopic_confidences{}` to activity records

Full text of these decisions lives in `.planning/phases/04-subtopic-system/` plans and summaries.

# Topic & Subtopic Assignment Pipeline

This document describes how publications get assigned to topics and subtopics: where the taxonomy comes from, how the per-paper labels are produced, how the layers are stored, and what they actually mean (mutually exclusive? multi-label? primary?).

It complements `taxonomy-methodology.md`, which covers the design *principles* behind the taxonomy. This doc covers the *mechanics* of generation and assignment.

## 1. Two-Axis Architecture

The classification system is two orthogonal axes, not a single hierarchy:

| Axis | What it answers | Source |
|---|---|---|
| **Axis 1 — Topics** (~67) | "What domain is this paper in?" | `taxonomy_v2.json`, generated inductively from synopses |
| **Axis 1.5 — Subtopics** (~8–15 per topic) | "Within this domain, which theme?" | Per-topic inductive clustering from scored activities |
| **Axis 2 — Tools** | "What methods/techniques?" | `reciterai_keyword_relevance` (not LLM-generated) |

Topics and subtopics together form an inductive hierarchy: subtopics are scoped to their parent topic (D-05). Tools are produced by a completely separate pipeline (ReCiter AI keyword extraction) and are not covered here.

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

### Human review gate (D-07)

- `taxonomy_v1.json` is **frozen for human review** before any scoring runs
- Bad taxonomy wastes the ~$170 Bedrock dense-scoring budget at full scale
- After review, the taxonomy is locked with a `taxonomy_version` field

### Topic ID stability

Topic IDs **are stable** across the lifecycle of a `taxonomy_version`. Every score record in DynamoDB carries the `taxonomy_version` it was scored against, so taxonomy bumps trigger *targeted* recomputation rather than a full rebuild.

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
| `taxonomy_v1.json` / `taxonomy_v2.json` | Locked taxonomy artifacts (D-07 review gate output) |
| `score_publications.py` | Two-pass topic scoring (Haiku screening → Sonnet dense) |
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

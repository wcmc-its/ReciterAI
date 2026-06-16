# Research-Area & Subarea Assignment Pipeline

This document describes how publications get assigned to research areas and subareas: where the research-area taxonomy comes from, how the per-paper labels are produced, how the layers are stored, and what they actually mean (mutually exclusive? multi-label? primary?).

It complements `taxonomy-methodology.md`, which covers the design *principles* behind the research-area taxonomy. This doc covers the *mechanics* of generation and assignment.

> **Terminology.** In the Scholars UI, these AI-derived layers are displayed as **research areas** (top level) and **subareas** (within each). The code — and the file/identifier names in this doc — also use the older words **topic**/**subtopic** (`TOPIC#`, `SUBTOPIC#`, `taxonomy_v2.json`, `subtopic_id`, `score_publications.py`, …); those are kept verbatim wherever they name real code, even though prose now says "research area"/"subarea." Do **not** confuse any of this with the SPS profile **"Topics"** section, which is a *separate* signal — MeSH keywords from ReCiterDB (see the second summary below) — not the AI-derived research areas described here.

## Plain-language summary — "How are research areas assigned?" (Scholars About page)

> Copy approved for public, faculty-facing use (e.g., the SPS Scholars About page). Keep it jargon-free; the technical detail lives in the sections below.

Research areas aren't chosen from a fixed list — they're derived from the research itself. Rather than borrowing a standard subject classification, the system reads across plain-language summaries of every Weill Cornell publication and lets the major research domains emerge from what's actually there — areas like Cardiovascular Disease, Immunology, and Cancer Biology. The AI consolidates overlapping areas and validates the result against a set of representative queries, so the areas hold together without being hand-built. As an independent check, that map was benchmarked against authoritative institutional reference points — Weill Cornell's divisions and departments, its strategic research roadmap, and NIH research designations — and aligned cleanly with all three, confirming that what the AI surfaced from the literature mirrors how the institution and the wider field already organize science. Within each area, the same approach surfaces more specific subareas, and every publication is scored for how strongly it relates to each one. Because real research often spans several areas, a single paper can be associated with more than one. The research areas shown on a scholar's profile reflect the balance of their published work, and they update automatically as new publications are added.

## Plain-language summary — "How are topics assigned?" (MeSH; Scholars About page)

> Note: a scholar's profile **"Topics"** are a *separate* signal from the AI-derived research areas above — they are **not** produced by this pipeline. Included here only so the two aren't confused.

Topics are standard subject keywords from the National Library of Medicine's Medical Subject Headings (MeSH) — the established vocabulary used to index articles in PubMed. A scholar's topics are simply the MeSH terms carried on their own indexed publications, surfaced from the publication record (ReCiterDB). Because they come straight from the established PubMed index rather than being generated here, they offer a familiar, standardized complement to the research areas — which are derived from the full body of Weill Cornell research rather than borrowed from a fixed vocabulary.

## 1. Two-Axis Architecture

The classification system is two orthogonal axes, not a single hierarchy:

| Axis | What it answers | Source |
|---|---|---|
| **Axis 1 — Research areas** (~67) | "What domain is this paper in?" | `taxonomy_v2.json`, generated inductively from synopses |
| **Axis 1.5 — Subareas** (~8–15 per area) | "Within this domain, which theme?" | Per-research-area inductive clustering from scored activities |
| **Axis 2 — Methods & tools** | "What methods/techniques?" | LLM-extracted from faculty publication/grant abstracts, then deduped → classified → grouped into method families (A2 pipeline) |

Research areas and subareas together form an inductive hierarchy: subareas are scoped to their parent research area (D-05). Methods & tools are produced by a completely separate pipeline (LLM extraction from abstracts → canonical-tool dedup → classification → ~878 method families; see [`tools-a2-architecture.md`](./tools-a2-architecture.md)) and are not covered here. (The SPS profile **"Topics"** section is a further, separate signal — MeSH keywords from ReCiterDB — not part of this AI-derived hierarchy.)

## 2. Where Research Areas Come From

Generator: `generate_taxonomy.py`. The output is `taxonomy_v1.json` (initial) → `taxonomy_v2.json` (current, 67 research areas, `taxonomy_version: "taxonomy_v2"`).

The research-area taxonomy is **derived from the corpus**, not authored from a list of biomedical disciplines:

### Phase 1 — Extract & cluster

- Pull all publication synopses from ReciterDB via `SYNOPSIS_EXTRACTION_SQL`
- Batch synopses (default 75 per batch) through Claude Sonnet on AWS Bedrock
- Each batch produces raw research-area candidates that Sonnet observes in the synopses
- Result: ~150 raw research areas across all batches

### Phase 2 — Consolidate

- Second Sonnet pass collapses the ~150 raw research areas into ~50–70 final research areas
- Merges near-synonyms, broadens hyper-specific areas (avoiding the "tau-pathology" brittleness problem — see CLAUDE.md taxonomy principle)
- Each research area gets `id`, `label`, and a 2–4 sentence `description` that will later be injected verbatim into screening and scoring prompts

### Phase 3 — Validate

- 12 hardcoded research-dean queries (`generate_taxonomy.py:51`) are run against the candidate taxonomy
- Examples: "Who works on aging and cognitive decline?", "Find researchers using CRISPR gene editing", "Who studies neurodegeneration and Alzheimer's disease?"
- If any query has no plausible research-area match, the taxonomy fails validation and must be regenerated
- **Convergent-validity sanity check:** beyond the automated query pass, the emergent domain set was benchmarked against external institutional reference points — WCM divisions/departments, the institutional research roadmap, and NIH research designations — and found to align with all three. These sources were a corroboration cross-check, **not** an input: the research areas were derived inductively from the corpus, then confirmed to mirror how the institution and NIH already organize research.

### Human review gate (D-07)

- `taxonomy_v1.json` is **frozen for human review** before any scoring runs
- Bad taxonomy wastes the ~$170 Bedrock dense-scoring budget at full scale
- After review, the taxonomy is locked with a `taxonomy_version` field

### Research-area ID stability

Research-area IDs (`topic_id`) **are stable** across the lifecycle of a `taxonomy_version`. Every score record in DynamoDB carries the `taxonomy_version` it was scored against, so taxonomy bumps trigger *targeted* recomputation rather than a full rebuild.

### Adding a single research area later (additive path, #225)

A genuinely new domain can be minted into the existing taxonomy without re-running the full generate-and-score pipeline. Hematology & Medical Oncology (taxonomy `67 → 68`) was added this way.

Generator: `cli/score_new_topics.py`.

- Hand-add the new research area to `taxonomy_v2.json`, then score **only that area** against a small set of "context" competitor areas for contrastive routing (so a heme/onc paper isn't mis-routed to a neighboring cancer area).
- Same two-pass shape as the full scorer (Haiku screen ≥ 0.3 → Sonnet dense), reusing `score_publications.py`'s prompts, plus a **per-area rubric** in `config/rubrics/<topic_id>.txt` (decision order + score bands) that restores routing fidelity lost when an area is scored in isolation. `--verify-fidelity` quantifies the delta on a sample before any writes.
- Writes are **additive**: new `TOPIC#` rows (never a delete-and-rewrite), affected `FACULTY#` rows merged from the live `FacultyIndex` GSI (the ephemeral scoring-results file is not the source of truth), and a refreshed `TAXONOMY#{version}` record.
- Cost is roughly an order of magnitude below a full run, but note it is **not** trivial: per-call cost is content-dominated, so scoring one area across the corpus is still on the order of a full pass's per-paper spend.

## 3. How Papers Get Assigned to Research Areas

Two-pass, offline, batch-processed. Lives in `score_publications.py`.

### Pass 1 — Haiku screening (recall-first)

- **Input**: one publication's synopsis **and abstract** + the full taxonomy (all ~67 research areas in a single prompt). Only those two text fields are scored — the title, MeSH descriptors, and NIH RePORTER terms are **not** inputs to research-area scoring (the title is stored on the `TOPIC#` row for display; MeSH feeds the separate "Topics" lens; NIH RePORTER feeds the methods/tools pipeline). See `make_screening_prompt` (`score_publications.py:510`).
- **Model**: Claude Haiku via Bedrock Batch API
- **Output**: JSON map `{topic_id: score}` for every research area the model finds plausible
- **Threshold**: `SCREENING_THRESHOLD = 0.3` (`score_publications.py:61`)
- **Rubric (from the prompt)**:
  - 0.9–1.0: primary focus of the publication
  - 0.3–0.5: meaningful but secondary (methods used, population studied, comorbidity)
  - <0.3: dropped, not stored, not advanced
- **Principle**: "err on the side of inclusion. A second pass will refine scores." Recall over precision.
- **Cost**: ~$0.0005/activity

### Pass 2 — Sonnet dense scoring (precision)

- **Input**: the same synopsis + abstract, re-scored against only the research areas that cleared 0.3 in Pass 1 (typically 30–40% of activities; per-activity, only the areas that passed)
- **Model**: Claude Sonnet via Batch API
- **Output**: calibrated 0.0–1.0 score per research area + ≤80-char rationale
- **Cost**: ~$0.008/activity

### Result

Each publication carries a **vector of scores** across research areas. There is **no single "primary research area"**. A cardiology paper that touches genomics and aging gets three records (one per area), each above 0.3 — all three are persisted.

### Floor vs display — two different decisions (#69)

The 0.3 cutoff above is the *qualification* floor (`score_floor`): it decides whether a paper's score for a research area is high enough for the paper to be persisted as a research-area-activity row at all. It is recall-oriented, screening-level.

A separate *display* threshold (`display_threshold`, per-area, default 0.5) governs what renders by default on the SPS Scholars research-area page (`/topics/<topic_id>`). Papers with `score[area] >= display_threshold[area]` appear in the strongly-relevant default view; papers with `score_floor <= score[area] < display_threshold[area]` appear only behind the "View additional articles that are relevant" affordance.

- `score_floor` lives in `config/thresholds.json`.
- `display_threshold` is carried per-area on `taxonomy_v2.json` and republished in `hierarchy.json` for SPS to consume; the global fallback for untuned areas is `display_threshold_default` in `config/thresholds.json` (currently 0.5).
- `display_threshold` is consumed at SPS read time only. It does **not** change qualification, does not change `subtopic_ids[]`, does not change §8 rollup arithmetic, and does not change which records are persisted. Per-area tuning is a separate, paced workstream (see `docs/topic-page-inclusion-threshold.md`).

## 4. How Research-Area Scores Are Stored

DynamoDB, research-area-first key layout (the `TOPIC#` partition) for the dominant query pattern ("give me all activities above threshold for research area X"):

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

Multi-research-area intersection (e.g., "aging AND genomics") is handled by querying each area separately and intersecting `faculty_uid`s in application code. Fine at WCM's scale.

## 5. Where Subareas Come From

Generator: `discover_subtopics.py` (Pass 1 of the subarea pipeline).

Subareas are **derived per-research-area from the activities scored against that area**. There is no global subarea list; each parent research area gets its own 8–15 subareas, scoped to that area only (D-05).

### Inductive discovery

For each research area:

1. Query DynamoDB for all `TOPIC#<topic_id>` records with `score >= 0.3`
2. Send the activities (title + synopsis + impact + relevance) to Sonnet with `DISCOVERY_SYSTEM_PROMPT` (in `prompts/subtopic_discovery.py`), **temperature=0** for reproducibility (D-01)
3. Sonnet returns 8–15 clusters: `{id, label, description, seed_pmids, coverage_estimate}` + an `uncovered` list of PMIDs that didn't fit cleanly

### Hard constraints in the prompt

- Minimum **3 seed PMIDs** per cluster — anything thinner stays uncovered
- Activities that don't fit a coherent cluster go to `uncovered`, **not** forced into a weak bucket
- "Do NOT invent subareas not supported by the activities provided" — the CLAUDE.md taxonomy principle repeated verbatim in the discovery prompt
- Subarea IDs follow `{topic_prefix}_{slug}` (e.g., `aging_cellular_senescence`)

### Coverage gate (D-01)

- Target: ≥85% of input activities assigned to some cluster
- If first pass falls short, run an **extension pass** with `DISCOVERY_EXTENSION_PROMPT` (up to 2× the initial cluster count cap)
- If still short, the gap is accepted — better to leave activities unassigned than fabricate subareas

### Human review gate

- Output: `.planning/phases/04-subtopic-system/hierarchy_draft_<topic_id>.json`
- Reviewed by a human (typically the research dean or domain expert) before Pass 2 assignment runs
- On Bedrock JSON-parse failure, the raw response is saved as `hierarchy_draft_<topic_id>.raw.txt` for debugging

### Subarea ID stability — important caveat

**Subarea IDs (`subtopic_id`) are NOT stable across recomputes** (D-06, called out at `discover_subtopics.py:_slugify`).

> Subarea IDs may change on each full recompute. Consumers MUST re-read `hierarchy.json` after regeneration and MUST NOT persist subarea IDs in external systems that outlive a recompute cycle.

This is the opposite of research-area IDs (which are stable within a `taxonomy_version`). The reasoning: subareas are wholesale-regenerated when their parent area's activity set shifts meaningfully, and trying to preserve IDs across runs would lock in stale clusters.

#### Durable opaque IDs — the stability layer (#191)

The unstable slugs above are the *clustering-time* identity. A separate **durable-ID layer** (issue #191, "Option C") sits on top to give consumers a stable handle across re-clustering: each subarea gets a mint-once opaque id (`st_` + 26 random chars) that survives label/slug churn. After each hierarchy publish, a post-upload **match-or-mint reconcile** maps the new run's slugs back to existing durable ids via a deterministic-first cascade — Stage 1 seed-PMID membership overlap → Stage 2 label-embedding centroid (Titan v2) → Stage 3 LLM arbiter (Sonnet, OpenAI fallback; verdicts cached by *stable content hash*, never by id or mint order) — and records split/merge lineage edges. The bricks (A mint-store, B reconcile, C lineage, D consumer alias map, E relabel/lede skip-logic, F lifecycle) are merged **flag-off**; gate-on is pending a prod cold-run (see #204). Full contract: `docs/subtopic-durable-id-store.md` and `docs/subtopic-lifecycle-and-evolution.md`.

## 6. How Papers Get Assigned to Subareas

Assigner: `assign_subtopics.py` (Pass 2). Different shape from research-area scoring — this is a **classifier**, not a relevance scorer.

### Per-activity Haiku classification

- One Haiku call per unique PMID
- Input: the activity (title + synopsis) + the reviewed subarea hierarchy for its parent research area
- Output: confidence map `{subtopic_id: confidence}` plus the model's chosen primary

### What gets written to the activity record

Three fields (`assign_subtopics.py:17`):

| Field | Shape | Meaning |
|---|---|---|
| `subtopic_ids[]` | list of strings | All subareas that cleared the confidence floor (default 0.35) |
| `primary_subtopic_id` | string | The single best subarea for navigation/rollup views |
| `subtopic_confidences{}` | map | Full confidence distribution from the classifier |

### Both aggregations (Phase 12 §8)

Exclusive aggregation rolls up each activity row's `primary_subtopic_id` only; inclusive aggregation rolls up every entry in `subtopic_ids[]` at full `article_score` per entry. Invariant (D-17): `sum(SUBTOPIC_SCORE_INCLUSIVE#X#*) ≥ sum(SUBTOPIC_SCORE#X#*)`; equality iff every paper in research area X has exactly one above-floor subarea assignment. The difference equals the total `article_score` contributed by secondary assignments in research area X. The reconciliation gate (`gates/reconciliation.py`) verifies this invariant before publish; a separate aggregator-internal invariant (D-33) verifies per-CWID per-subarea equality between the legacy `faculty.subtopic_scores` map and the new `SUBTOPIC_SCORE#` partition.

### Primary selection logic

- Default: `primary = argmax(subtopic_confidences)`
- **Tiebreaker** (`_resolve_primary_on_tie`, `assign_subtopics.py:92`): when the top-2 confidences are within `TIE_EPSILON`:
  1. Higher **total_weight** subarea wins (signal density — favors the cluster with more aggregate evidence)
  2. If still tied, alphabetically lower `subtopic_id` wins
- Tiebreaker runs in Python, not in the Haiku prompt — keeps the model's job simple and the tiebreak deterministic

### Unassigned is a valid outcome

If no subarea clears the confidence floor, the activity gets an empty `subtopic_ids[]` and no `primary_subtopic_id`. These are **not** errors — they're activities the LLM correctly refused to force-fit. Resume runs skip activities that already have a `primary_subtopic_id` set (`--resume` flag).

### Per-paper `top_topic_id` — observational, not a designation (#68)

Each activity record carries a derived `top_topic_id` field equal to the research area with the highest score in the paper's research-area-score vector among areas that cleared `score_floor`. This is a **read-time convenience** for the SPS Scholars research-area page (`/topics/<topic_id>`), which is inclusive-multi-label by design (every above-floor area shows the paper); `top_topic_id` lets the UI render an honest one-bit "top research area" affordance inline.

`top_topic_id` is **not** a designation, **not** a rollup input, and **not** an authoritative claim that the paper is "about" that area. The system remains multi-label and continues to assert there is no single "primary research area" (§3). The §8 exclusive rollup is unaffected — it operates on `primary_subtopic_id`, not on `top_topic_id`.

Tiebreak (mirrors the per-subarea pattern above, at the research-area level): when the top-2 research-area scores fall within `tie_epsilon`, higher `sum(subtopic_confidences[area])` wins (research-area-level signal density); if still tied, alphabetically lowest `topic_id` wins. Deterministic, runs in Python, reproducible across runs given identical scores.

Papers with no above-floor research area carry no `top_topic_id`. Producer: `compute_top_topic.py` (cold path: stage after `relabel`; hot path: state between `Assign` and `Rollup`).

#### Three-fields disambiguation

The system now has two notions of "primary" (both unchanged) plus one new observational field. They are categorically different concepts:

| Field | Scope | Kind | Source |
|---|---|---|---|
| `primary_subtopic_id` | per paper, per research area | **Designation** — assignment-time, with deterministic tiebreak | `assign_subtopics.py:_resolve_primary_on_tie` |
| §8 exclusive rollup aggregation | per CWID, per (research area / subarea) | **Aggregation rule** — operates on `primary_subtopic_id` | `aggregate_subtopic_scores.py` |
| `top_topic_id` | per paper | **Observational** — argmax of research-area-score vector, with deterministic tiebreak | `compute_top_topic.py` (#68) |

## 7. Mutual Exclusivity — The Honest Answer

Neither layer is strictly mutually exclusive, despite the design doc (`RECITER_AI_CHATBOT_README.md:225`) claiming subareas are. The implementation evolved past that. Here's the actual shape:

| Layer | Mutually exclusive? | What you actually get |
|---|---|---|
| Research areas | **No** — multi-label by design | Vector of scores; all areas ≥0.3 are persisted |
| Subareas | **No** — multi-label, but with a designated primary | `subtopic_ids[]` (list) + `primary_subtopic_id` (single) |

### Practical implication for rollups

For "papers in subarea X" counts, you have a deliberate choice to make:

- **Use `primary_subtopic_id == X`** → mutually exclusive; each paper appears in exactly one subarea bucket; rollup totals sum cleanly to the research-area total
- **Use `X in subtopic_ids`** → inclusive; papers can appear in multiple subarea buckets; better for "find anything related to X" use cases

The CSVs in the repo root (`cwid_subtopic_counts.csv`, `cwid_topic_counts.csv`) made one of these choices. Verify which before using those numbers anywhere load-bearing.

### Why both axes are multi-label

- **Research areas**: biomedical research is intrinsically multi-domain. A paper on "social determinants of cardiovascular outcomes in aging populations" legitimately scores high on cardiovascular_disease, health_equity, and aging_geroscience. Forcing single-label loses information that the synthesis LLM needs at query time.
- **Subareas**: same logic at finer granularity, plus the system needs a single canonical bucket for navigation and per-subarea summaries. Hence the `primary_subtopic_id` compromise.

## 8. Versioning & Recomputation

| Layer | Version field | Stability | Recompute trigger |
|---|---|---|---|
| Research-area taxonomy | `taxonomy_version` (e.g., `taxonomy_v2`) | Stable within a version | New research areas added, descriptions materially changed |
| Research-area scores | `taxonomy_version` on each record | Stable | Activity synopsis changes, taxonomy bump |
| Subarea hierarchy | Wholesale replacement | **IDs not stable across recomputes** | Research area's activity set shifts ~20%+ |
| Subarea assignments | Tied to current hierarchy | Re-run after hierarchy refresh | New hierarchy published |

Cost of a full taxonomy-version bump (per `RECITER_AI_CHATBOT_README.md`): ~$170 at 50k activities. Incremental for new activities: ~$0.01 each.

## 9. Cost Summary

| Stage | Model | Per-activity cost |
|---|---|---|
| Research-area screening | Haiku (Batch) | ~$0.0005 |
| Research-area dense scoring | Sonnet (Batch) | ~$0.008 (only on ~35% that pass screening) |
| Subarea discovery | Sonnet | ~$0.20–$1.00 per research area (whole-corpus, not per-activity) |
| Subarea assignment | Haiku | ~$0.0005 per activity |

Full initial build at WCM scale: ~$170. Annual maintenance: ~$50–80.

## 10. Key Files

| File | Role |
|---|---|
| `generate_taxonomy.py` | Inductive research-area taxonomy generation (3 phases + validation) |
| `cli/score_new_topics.py` | Additive single-research-area minting (no full re-run); `config/rubrics/<topic>.txt` per-area rubrics |
| `taxonomy_v1.json` / `taxonomy_v2.json` | Locked taxonomy artifacts (D-07 review gate output) |
| `score_publications.py` | Two-pass research-area scoring (Haiku screening → Sonnet dense) |
| `utils/bedrock_client.py` | Current model pins (`MODEL_IDS_BY_STAGE`): screening/assignment → Haiku 4.5; scoring/discovery/relabel/reconcile → Sonnet 4.6; spotlight lede → Opus 4.7 |
| `pipeline_hierarchy/` + `docs/subtopic-durable-id-store.md` | Hierarchy assembly/publish + durable opaque subarea IDs & match-or-mint reconcile (#191) |
| `discover_subtopics.py` | Per-research-area inductive subarea clustering (Pass 1) |
| `prompts/subtopic_discovery.py` | Discovery + extension prompts; encodes the "no speculative subareas" rule |
| `assign_subtopics.py` | Per-activity Haiku subarea classifier (Pass 2), primary selection + tiebreaker |
| `utils/dynamodb_subtopic_migration.py` | Writes `subtopic_ids[] / primary_subtopic_id / subtopic_confidences{}` to activity records |
| `.planning/phases/04-subtopic-system/` | Phase 4 plans, summaries, and hierarchy drafts |
| `docs/hierarchy-contract.md`, `docs/hierarchy.schema.json` | Hierarchy data contract for downstream consumers |
| `docs/taxonomy-methodology.md` | Companion doc — design *principles* (this doc covers the *pipeline*) |

## 11. Design Decisions Referenced

The codebase uses `D-NN` markers to reference Phase 4 design decisions. The ones cited here:

- **D-01** — Temperature=0 for subarea discovery; ≥85% coverage target; extension pass cap at 2×
- **D-05** — Subareas scoped to parent research area only; no cross-area clustering
- **D-06** — Subarea IDs not stable across recomputes; wholesale replacement on regenerate
- **D-07** — Human review gate on `taxonomy_v1.json` before scoring spend
- **D-15** — Subarea descriptions injected verbatim into synthesis prompts
- **D-16** — Pass 2 writes `subtopic_ids[]`, `primary_subtopic_id`, `subtopic_confidences{}` to activity records

Full text of these decisions lives in `.planning/phases/04-subtopic-system/` plans and summaries.

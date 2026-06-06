# Tools / Axis 2 — Producer Model

**Status:** APPROVED 2026-06-06 — gate decisions locked; issues #5/#6/#7/#8 closed. Resolves the four `decision-deferred` issues that block Phase 8.
**Evidence:** `.planning/tools-probe-report.md` (Phase 0 probe, 2026-06-05).
**Companion plan:** `~/Dropbox/Projects/ReciterAI - Planning/tools-taxonomy-PLAN.md`.

This is the 1–2 page producer-model spec SPEC §10 requires before Phase 8 implementation. It answers the four blocking questions. Approval here is the trigger to close #5–#8.

### v1 scope locked (2026-06-06)
- **Subjects facet = MeSH major headings, filter-only, no pages.** No separate curated-topic facet in v1 — a curated topic layer (and its pages) is a v2 concern. Only the **Methods** lens is new ReciterAI work; Subjects ships on the existing SPS MeSH data.
- **Per-person capability narrative (Product B) is OUT of scope for this product.** It is delivered by the separate **feature/summary generator**, which consumes this taxonomy (`canonical_tool_id`, per-pub `tool_ids`) as its controlled vocabulary + grounding. v1 here = **Product A** (canonical taxonomy) + the structured two-lens SPS surface.
- **Signal = publications + grants** (trials/patents excluded for v1). Publications are the primary salience denominator and the **only** click-to-filter surface. **Grant abstracts where available (esp. NIH RePORTER) are a forward-looking secondary signal** folded into extraction, salience, and family discovery — they do **not** get their own pub-filter chip (filters operate on publications). Accept some mismatch between the historical `reciterai_tools` grant seed and current extraction runs.
- **Deferred-with-trigger (not gate-blocking):** (a) borderline family-label policy → resolved as a batch during the global relabel pass against the name-the-task rule; (b) salience calibration (LLM-tier vs cross-corpus-frequency blend + thresholds in `config/thresholds.json` `tool_salience_*`) → tuned after the full-corpus extraction produces the real frequency histogram.

---

## Scope & purpose
A canonical taxonomy of the **distinctive methods and tools** WCM researchers use, published for the Scholars Profile System to render a "Methods & Tools" profile section + a `/tools` browse-by-capability directory. **Distinctive** = a specialized capability someone would search for and that meaningfully narrows the field (patch-clamp, cryo-EM, scRNA-seq, CRISPR screen, a named cohort). Commodity techniques (PCR, Western blot, generic statistics, PubMed) are suppressed, not tracked. Target ≈ a few hundred canonical entries. **Methods-first** — for this audience the experimental/clinical method outranks the software brand.

**Corpus scope (verified 2026-06-05):** assignment and salience run over the **WCM full-time-faculty, first/last-authored, Academic-Article, ≥2020 universe — 8,146 papers (7,151 with abstracts), 1,406 faculty** — NOT the full 74,157 academic-article corpus. That subset renders on profiles and is the correct salience denominator (a method in 50 of ~8k faculty papers is distinctive; 50 of 74k is not). **Grants (where abstracts exist, esp. NIH RePORTER) are a forward-looking secondary signal** folded into extraction/salience/family discovery; publications remain the primary salience denominator and the only click-to-filter surface. **Trials/patents are out of scope for v1.**

## Two products — and the v1 split
The POC's faculty-validated artifact (Tony Rosen, `aer2006`, Emergency Medicine — "a pretty good summary of his tools and methods") is a **per-person `models_and_methods` array of ~5 cited capability statements** — *capabilities*, not reagents — the methods layer the flat `tool_index.json` (reagents/instruments/datasets) entirely lacks. So there are **two products**:
1. **Canonical tool/method taxonomy** (structured; this doc) — powers the two-lens SPS surface, the `/tools` browser, facets, controlled vocabulary, and "who uses X." **This is the v1 deliverable.**
2. **Per-person capability summary** (generative, grounded + cited) — the validated `models_and_methods` narrative. **Out of scope for v1 of this product** (decision 2026-06-06): it is delivered by the separate **feature/summary generator**, which consumes this taxonomy (`canonical_tool_id`, per-pub `tool_ids`) as its controlled vocabulary + grounding.

The taxonomy is the substrate; the narrative is a downstream consumer. This doc specifies the taxonomy (#1) and the vocabulary the generator (#2) will reference.

## SPS surface — two-lens, compact, filter-first
The profile renders two complementary lenses as a **compact filter cluster directly above the Publications list** — they double as publication filters (click → filter pubs), so both lenses (plus the existing MeSH/Topics facet) must fit **one screen** with filters and results visible together. Compactness is a hard constraint; this supersedes any "separate section lower in the stack" placement.

- **Subjects** — *what the work is about*. Source: **MeSH major headings** (already in SPS — the current staging "Topics" facet), **filter-only, no pages**, and no separate curated-topic facet in v1. Compact wrapped chips + counts.
- **Methods & tools** — *how the work is done*. Source: ReciterAI inferred **method families** (the three-tier taxonomy).
  - Render: ranked families (label + ~3 monospace exemplars + count). **Adaptive basis line** computed from the researcher's dominant tool kinds — "models, datasets, software" (computational) vs "animal models, reagents, cell models" (wet-lab); never hardcode "datasets & models." Validated across computational (Peng) and wet-lab (Li Gan, Crystal) — both rich, only the basis phrase differs.
  - **Compact by default**: top families collapsed (chips or top-N rows + "+ N more"), expand on demand — the full exemplar list is too tall to sit above pubs as a filter.
  - **Empty/sparse state required** (few lead papers / methods that never surface as named entities) — collapse or hide, don't show a near-empty lens.
- **Scope = lead/senior (first/last) author articles for BOTH lenses** — no All/Lead toggle (simpler + compact; supersedes the earlier opposite-defaults asymmetry). Light theme; Tabler icons `ti-tag` (Subjects) / `ti-tools` (Methods).
- **Grants feed the Methods signal, not the filter UX**: grant-derived methods (where abstracts exist, esp. NIH) contribute to family detection and salience, but clicking a family filters the scholar's **publications** only — grants get no filter chip.
- **Product B narrative is out of scope here** (delivered by the separate feature/summary generator); the structured, filterable lenses carry the v1 load.

### Family labels — the name-the-task rule (controlled, per-family)
**Name the defining task where it doesn't collide with a MeSH heading; fall back to modality or data-type only when the family is multi-task or the task-name would collide.** Verbs/tasks ("evidence summarization," "report generation," "risk prediction") differentiate from the subject taxonomy better than tool-nouns and outlive any specific model. The label is a property of the **canonical family** (registry `display_label`), set ONCE at **global discovery via a relabel pass** (mirrors `relabel_subtopics`) — never improvised per-profile (else the same family is named differently across scholars). Worked set (Yifan Peng's 6): *Chest radiograph AI · LLM clinical evaluation · Clinical text classification & PICO extraction · Clinical evidence summarization · Retinal & ophthalmic imaging · EHR & registry data.*

### Interaction & pages
- **Both lenses filter the scholar's pubs on click** (compose with AND); active/clearable filter state.
- **Methods family row** = filter (primary) **+ `→` family page** (secondary; "who else at WCM does this," count may split "5 · 38 across WCM →").
- **Subjects (MeSH) = filter-only, no page** (long tail, wrong altitude).
- **Pages only for bounded/curated facets:** **method family is the page level** (~hundreds, parallels subtopic pages); marquee tools selectively (Tier-S / RRID / high-use); supercategory = browse facet, not a page; a curated topic layer is **out of scope for v1** (would carry pages if ever added).

## #5 — Vocabulary source: **Hybrid, RRID-anchored where resolvable**
- **Resources** (software, reagents, antibodies, organisms, cell lines, datasets) → anchor to **RRID / SciCrunch** canonical IDs where they resolve. Stable external IDs, existing metadata, interoperability; RRID-ability is itself a distinctiveness signal for reagents.
- **Methods / techniques / clinical tools** (registries don't cover these — and the probe shows they are 38%+17% of recovered value) → **WCM-curated** controlled vocabulary, `canonical_tool_id = wcm_tool_<slug>`.
- **Provenance recorded per entry:** `source ∈ {rrid, bioregistry, wcm_curated}`, `source_uri`, `source_confidence`.
- *Probe basis:* RRID resolver works (HTTP 200); bulk name→RRID is a bounded API-integration task; methods are not RRID-covered → hybrid is required, not optional.

## #6 — Discovery: **Hand-curated seed (Claude, one-time) → Haiku per-PMID extraction (Bedrock, ongoing)**
- **Seed (one-time, Claude Code, human-gated):** canonicalize POC `tool_index.json` (175 resources) + `tools_to_canonicalize.json` (230) **plus a corpus-wide fresh extraction pass** (the seed's methods axis is ~empty; methods must be extracted) → reviewed `tool_taxonomy.json`. Mirrors `cli/generate_taxonomy.py` + its D-07 review gate.
- **Maintain (ongoing, Bedrock):** per-PMID Haiku extraction from abstracts, folded into the existing `pipeline_enrichment` delta worker (reuses watermark / PROCESSING# / cost-guard / OpenAI-fallback). Matches to the canonical taxonomy; novel candidates queue for a **periodic Sonnet canonicalization + dedup pass** (human-gated promotion of new canonical entries).
- **Grants (v1 secondary signal):** grant abstracts (NIH RePORTER, where available) run through the same Haiku extraction path as a forward-looking signal. Their tool hits feed the taxonomy, salience, and family discovery, but **not** the publication-filter UX (filters operate on pubs). Accept some mismatch between the historical `reciterai_tools` grant seed and current runs. Trials/patents are out of scope for v1.
- *Probe basis:* abstracts yield 3.27 distinctive items/paper (96% coverage), 88% novel vs seed → fresh LLM extraction is essential and sufficient from abstracts alone (no full-text dependency for v1).

## #7 — Parent–canonical cardinality: **1:N, parent optional**
- Canonical entity sits at the **capability** level. `parent_tool_ids[]` is a list — empty/single for most v1 entries, permitting N (e.g. "Cox proportional-hazards regression" parented under SAS *and* R *and* lifelines; a Python lib that also ships with Bioconductor).
- `functional_category` is the **browse facet** (≈10 buckets), distinct from the parent edge. Full method↔implementation hierarchy is a v2 concern. *(Update 2026-06-06: `functional_category` is **retired** by `docs/tool-classifier-spec.md` — its content is carried by `kind` + method family. The earlier "schema never has to migrate" assumption proved false; the record shape was refined pre-implementation — see §#8.)*

## #8 — `TOOL#` record schema
> **CANONICAL SHAPE LIVES IN `docs/tool-classifier-spec.md`.** The record shape was refined pre-implementation (2026-06-06) **without reopening the gate** — #5/#6/#7 stand as decided; only #8's fields are sharpened. The spec adds a `disposition` entry-gate (`method_tool`/`infrastructure`/`excluded`), an **8-value `kind`** enum (`model` split from `method`; adds `organism_or_cells`, `assay`; drops `model_system`/`clinical_tool`), **13 curated supercategories** (the browse facet), an `attributes` block (`delivery`/`provenance`/`license`/`consumable`/`rrid_candidate`), persistent **match-or-mint registries** for both canonical tools and method families (durable IDs, D-06), and **retires `functional_category`**. Defer to the spec wherever it differs from the sketch below.

**Canonical catalog entry** — *original gate-time sketch, kept for provenance; superseded in detail by the spec above* (`tool_taxonomy.json` + `TOOL_INDEX#<functional_category>` META rows):
```
canonical_tool_id   RRID:SCR_* | wcm_tool_<slug>     (STABLE — never recomputed)
display_name        render-only (D-19 LOCKED)
kind                method|instrument|software|reagent|dataset|model_system|clinical_tool
functional_category browse facet (~10)
salience_tier       S|A|B|C
salience_score      float 0–1
aliases[]           original_names + extracted variants
parent_tool_ids[]   optional, 1:N (per #7)
source              rrid|bioregistry|wcm_curated      provenance (#5)
source_uri          RRID URL | null
source_confidence   0–1
pub_count           corpus frequency (drives salience)
description         short, render-only (D-19 LOCKED — never fed to LLM/embedding/retrieval)
```
**Per-publication assignment** (activity rows, UpdateItem clone of `dynamodb_subtopic_migration`): `tool_ids[]`, `primary_tool_id`, `tool_confidences{}`, `extraction_version`.
**Faculty rollup** (`FACULTY#cwid_<cwid>`): `tool_scores.<canonical_tool_id> = summed weight` + `tools[]` top-N convenience vector.
**GSIs:** reuse existing partitions; a `ToolIndex` GSI on `canonical_tool_id` if the browser needs reverse "who uses X" lookups beyond the published artifact. Stable canonical IDs (unlike subtopics' D-06 wholesale replacement) — tools want identity continuity.

## Taxonomy shape — three tiers (validated 2026-06-05; mirrors topic→subtopic)
`kind` (instrument/reagent/method…) is too coarse for dense profiles — a methods-inventor's tools are nearly all one kind. The validated structure is three tiers:
1. **Supercategory** — a small, *curated, stable* institution-wide set (~10–12: Imaging & image analysis · Genomics & sequencing · Computational & statistical methods · Mass spec & proteomics · Animal & cell models · Clinical instruments & assays · Datasets & cohorts …) — the browser's top facets. (≙ the curated ~67 topics.)
2. **Method family** — *LLM-discovered, domain-specific*, emergent from clustering the canonical tools via the **same discovery+dedup engine as subtopics** — the collapsible groups rendered on a profile. (≙ discovered subtopics.)
3. **Canonical tool** — the leaf. (≙ scored activities.)

This makes #7's `parent_tool_ids[]` concrete: a tool's parent is its method family; families roll up to a supercategory. Validated on real data — Yi Wang's 50 canonical tools → supercategory "Quantitative MRI for brain physiology" → 8 coherent families (Susceptibility/iron QSM · Brain oxygenation OEF/qBOLD · Perfusion · PET/tracers · MR fingerprinting · Myelin/white-matter · DL reconstruction · Multimodal); the same engine produced sensible families for an NLP researcher and a med-chem researcher. Families are **cross-kind** (the QSM family spans the QSM algorithm, the mGRE sequence, and the Perls' iron stain — grouped by *capability*, not kind). Profiles render the family tier, not `kind`.

## Salience model (the load-bearing decision)
Salience tier per canonical tool, **objective signal first**:
1. **Cross-paper corpus frequency (primary)** — computed from the full-corpus extraction, NOT the POC `pub_count` (probe showed those are too small). Singletons → low tier by construction; recurring methods accumulate.
2. **LLM rubric (secondary)** — distinctive-vs-commodity, few-shot with the suppress-list as negatives. *Not sufficient alone* — the probe showed the model over-keeps paper-specific one-offs.
3. **Registry presence + specificity (bonus).**
- **Suppress-list (Tier C, never shown):** bibliographic databases (PubMed/Embase/ClinicalTrials.gov), generic statistics/regression/t-test, commodity bench techniques (PCR/Western/ELISA/generic culture/IHC), generic "microscopy"/"imaging". Lives in `config/`, editable.
- Tiers: **S** signature · **A** notable · **B** context (hidden behind "show all") · **C** suppressed. SPS renders S/A, collapses B, drops C. Thresholds in `config/thresholds.json`.

## Blast-radius & gate
- Corpus-wide extraction scales spend with publication count → reuse `pipeline_enrichment/cost_guard.py` cap before any LLM fan-out (Policy 5).
- **CI gate (per SPEC §10):** the workflow on PRs touching `pipeline_tools/` or `TOOL#` producer code requires #5/#6/#7/#8 referenced and `state: closed`. This doc's approval is the trigger to write those resolutions into each issue and close them.

---
*Approval checklist:* confirm #5–#8 answers above → write each resolution into its issue body → close issues → begin the Claude seed run (corpus extraction + canonicalization, human-reviewed) → build `pipeline_tools/`.

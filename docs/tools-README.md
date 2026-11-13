# Methods & Tools: how tools are identified

Everything about how ReciterAI finds the research tools and methods WCM faculty use, how it decides what counts, how it keeps identities stable, how the result reaches Scholars (SPS), and what is still undecided.

As of 2026-11-13 (written against `main` at `0791541`). Paths are relative to this repo's root unless prefixed SPS, which means the Scholars Profile System repo (`wcmc-its/Scholars-Profile-System`, checked at `3ac1ad6b`). The design docs listed under "Where things live" hold the original decisions; where they and this page disagree, this page reflects the code.

## In one paragraph

Every week, ReciterAI reads new publications (full-time faculty, first or last author, academic articles from 2020 on) and NIH grant abstracts. A small model (Haiku) pulls out every distinctive tool or method each one used, with the sentence that names it. A larger model (Sonnet) decides whether each new name is a real research tool and what kind it is. Each tool gets a permanent ID (`tool_NNNNNN`). Names already known attach to their existing tool. Genuinely new ones are minted. A new research tool must join an existing **method family** (`fam_NNNN`, e.g. "Single-cell RNA sequencing") to be published; one that fits no family is **held for human review**. (New `infrastructure` records, such as a core facility, publish without a family.) The result is published as seven JSON files to `s3://wcmc-reciterai-artifacts/tools/latest/`, which SPS imports every night into the Methods & Tools section of profiles, the `/methods` pages and people search.

## Goals

1. **Show what faculty actually do at the bench and keyboard.** A profile should say "uses single-cell RNA-seq, CRISPR screens and mixed-effects models", grounded in that person's own papers, not in self-reported keywords.
2. **Be honest and traceable.** Every tool on a profile links back to specific PMIDs, and every quoted sentence is verbatim from the abstract. Nothing is paraphrased or invented.
3. **Methods over commodities.** Emphasize distinctive methods, not pipettes, t-tests or PCR.
4. **Stable identities.** A tool or family ID never changes meaning once published. Downstream data (faculty rollups, SPS overlays, cores evidence) is keyed on these IDs.
5. **Curation is never silently undone.** Hand fixes made to the live artifact must survive every automated run (the lesson of ReciterAI#308).
6. **Stay current cheaply.** New papers appear on profiles within about a week, at a few dollars a run.

Original target in the producer-model doc: "≈ a few hundred canonical entries". Reality: about 18,500 tools in 816 families. Tools are deliberately under-merged; families are the unit people browse.

## Policy

### Scope: what gets read

| Source | Rule | Where |
|---|---|---|
| Publications | Full-time faculty (`identity.fullTimeFaculty = 'yes'`), first or last author, `publicationTypeCanonical = 'Academic Article'`, `articleYear >= 2020` | `pipeline_tools/corpus.py:37-50` |
| Grants | NIH RePORTER projects linked to full-time faculty, abstract present. No year filter. | `corpus.py:67-80` |
| Out of scope | Trials and patents (stated); middle-author papers, non-faculty and pre-2020 papers (by the SQL above) | `docs/tools-producer-model.md:20`, `corpus.py` |

- The query is live, so new faculty and new papers are picked up automatically. The 2020 floor is fixed, not rolling.
- First author maps to role `lead`, last author to `senior`. Grant investigators get role `investigator`.
- **Grants can create a tool and help it match a family, but they never count toward its tier or a scholar's publication list.** Grant occurrences have no PMIDs and don't count toward spread, so a grant-only tool lands in tier B unless it is RRID-style or force-C. Grants feed a separate `grant_signal`.
- Current corpus: 8,664 faculty publications plus 1,861 grants (from the 11-11 preflight).

### What counts as a tool

From the extraction prompt (`prompts/tool_extract.py:48-100`).

**Counts:**
- wet-lab methods and techniques (scRNA-seq, patch-clamp, CUT&RUN, two-photon imaging)
- computational and statistical methods and study designs (mixed-effects regression, Mendelian randomization, a named CNN)
- assays that produce a readout
- named instruments and platforms
- reagents and probes used as tools (antibodies, tracers, CRISPR/Cas9, vectors)
- drugs or agents, only when they are the studied intervention
- organisms, cell lines, transgenic and knockout models, primary cultures, strains
- datasets, cohorts, registries, software and models

**Does not count:**
- commodity bench gear mentioned in passing (pipettes, generic centrifuge, vortex, freezer, water bath)
- bare elementary statistics (t-test, ANOVA, chi-square, p-value, mean ± SD)
- generic office software (Excel, Word)
- the disease, organ or phenomenon being studied ("anti-PD-1 antibody" counts; "melanoma" does not)

The prompt expects 1–5 mentions per abstract, says "do not pad", and prefers the specific named form ("10x Chromium" over "a microfluidics platform"). When only a generic name is given ("flow cytometry"), it extracts that.

### Classification: the disposition gate

Every new name is classified by Sonnet (`prompts/tool_classify.py`, `pipeline_tools/vocab.py`).

**Disposition** (3 values):
- `method_tool`: a real research tool. Gets a kind, supercategory, family and tier.
- `infrastructure`: real but organizational (a core facility, a service). Gets an ID but no kind, supercategory, family or tier.
- `excluded`: not a tool. Never minted; the name is added to the **denylist** so it is never classified again.

**Kind** (8): `instrument`, `reagent`, `organism_or_cells`, `assay`, `dataset`, `software`, `method`, `model`.

**Supercategory** (14 in code):

| # | id | Label |
|---|---|---|
| 1 | `imaging_image_analysis` | Imaging & image analysis |
| 2 | `microscopy_histology` | Microscopy & histology |
| 3 | `genomics_sequencing` | Genomics & sequencing |
| 4 | `mass_spec_proteomics` | Mass spec & proteomics |
| 5 | `computational_statistical` | Research & analysis methods |
| 6 | `clinical_instruments_assays` | Clinical instruments & assays |
| 7 | `animal_cell_models` | Animal & cell models |
| 8 | `molecular_biochem_reagents` | Molecular & biochemical reagents |
| 9 | `therapeutics_interventions` | Therapeutics & interventions |
| 10 | `datasets_cohorts` | Datasets & cohorts |
| 11 | `software_informatics` | Software & informatics platforms |
| 12 | `structural_biophysical` | Structural & biophysical methods |
| 13 | `other` | Other / uncategorized |
| 14 | `functional_metabolic_cellular_assays` | Functional & metabolic cellular assays |

**Attributes:** `delivery` (in_house / shared_core / vendor), `provenance` (public / proprietary), `license` (open_source / commercial), `consumable`, `rrid_candidate`.

**Routing rules worth knowing** (from the classify prompt):
- Decide from the name and its sentence; the extractor's category hint is only a weak prior.
- Reagents route by use: administered → #9, probe → #8.
- #6 vs #8: the sample decides. Patient specimen → #6; cells or proteins → #8.
- An index you look up is a dataset; an index you compute is a method.
- A bare pathogen name is a reagent (#8); as the studied system it is an organism (#7).
- `other` only when nothing else fits and it is not commodity equipment.

**Failure handling:** the classifier never drops a mention.
- An invalid value is defaulted (to `method_tool`, `other` or null) and flagged.
- An LLM error leaves the name unclassified. Its paper is retried next week, and errors are never cached (the #308 item 3 fix).

### Identity: one tool, one permanent ID

Rule D-06 (`pipeline_tools/ids.py`, `registry.py`): `canonical_tool_id` and `family_id` are **durable, opaque, and never recomputed from a name or membership**. Re-minting an ID orphans everything keyed on the old one.

**How a name resolves**, in order (`registry.py:447-485`):
1. **Denylisted?** It is dropped.
2. **Same surface form as a known tool?** It attaches. Surface forms are:
   - the normalized name (lowercased, punctuation collapsed);
   - any parenthetical acronym ("(MRI)" → `mri`);
   - the name without parentheses;
   - the name with trailing generic words stripped ("scanner", "system", "platform", "database" and similar).

   Instrument nouns like cytometer and sequencer are deliberately not stripped.
3. **Close in meaning to a known tool?** It attaches. This uses Titan v2 embeddings with cosine ≥ 0.86 against every tool's name and aliases.
4. **Otherwise** a new `tool_NNNNNN` is minted.

**Design bias:** the code's own rationale is "a missed match (over-mint) is recoverable … a false match (wrong attach) silently corrupts pub_counts and is not." So the tool layer is intentionally under-merged.

**Curated alias map** (`config/method_alias_map.json`):
- 4 forced merges (HMC-1, SH-SY5Y, MCF-7, MDA-MB-231 cell-line variants).
- 4 never-merge pairs, e.g. HEK293 vs HEK293T. The run log of 11-11 shows this guard blocking "HEK293T cells" from merging into another HEK record.

**What accumulates on a tool:** aliases and PMIDs are unioned, `pub_count` is the number of distinct PMIDs, and one context sentence is kept per PMID (the longer one wins).

### Families

A family is the middle tier: supercategory → family → tool. It groups tools by capability across kinds. For example, a family can hold both an instrument and the assay run on it.

**Label rule ("name the task"):**
- methods, models and software: name the task;
- reagents and organisms: name the material class;
- therapeutics: name the drug class or modality, never the disease.

**How families were built (June 2026, the cold build):** an LLM first broadened each tool to a capability class, then reconciled classes within each supercategory. Groups smaller than 3 tools got no family.

**Hand curation on top, in order:**
1. exact-label dedup;
2. 21 reroutes, 5 relabels, 22 merges (`family_overrides.py`);
3. the 872 → 820 consolidation, which also set display tiers (feature 605 / standard 211 / suppressed 4);
4. an ad hoc batch (2 reroutes, 5 relabels, 4 merges), giving 816 live families.

**Invariants:**
- Active family labels are unique; a same-supercategory collision fails the build (#215).
- Every family gets a generated definition of at most 45 words; it is for display only (D-19).
- A tool may only join a family in its own supercategory, at cosine ≥ 0.80. A cross-supercategory match at ≥ 0.88 is flagged for review, never merged silently.

**Policy for the weekly run:** it never creates or reshapes families. The only code that forms families (`form_families`) re-mints every family ID (#317, open). Running it against live would remap IDs that SPS overlays, cores and faculty rollups depend on. So a new tool either joins an existing family or is held.

### Salience tiers (S / A / B / C)

Institution-level importance, used to keep noise off pages (`pipeline_tools/salience.py`). It does not order an individual profile; that uses the scholar's own paper count per tool.

| Tier | Rule (grounded mode) |
|---|---|
| C | Name matches the force-C suppress list (whole word). Checked first. |
| S | Used by at least `s_spread_min` (currently 5) distinct faculty, as first or last author. The cutoff is the 88th percentile among tools with spread ≥ 2, so far fewer than 12% of all tools are S. |
| A | RRID-style resource, OR 3+ papers, OR 2+ distinct faculty |
| B | Everything else |

- **Force-C list** (`config/tool_force_c_list.json`, 33 terms): placebo, centrifuge, pipette, spectrophotometer, gel electrophoresis, PCR/RT-PCR/qPCR, Western blot, IHC, cell culture, DNA/RNA extraction, t-test, chi-square, ANOVA, linear and logistic regression, Kaplan-Meier, descriptive statistics and a few more. C tools stay in the data but never count toward a family on a profile.
- **Published thresholds:** `s_spread_min 5, a_pub_floor 3, a_spread_floor 2, percentile 0.88`.
- **Frozen tiers:** the weekly run tiers only new tools and never recalibrates existing ones, because recalibration would move the S cutoff and re-tier everything.

**Separate thing: the generics flag.** `config/method_generics_blocklist.json` (146 terms, e.g. "mouse model", "xenograft", "electronic health records") sets `is_generic` on entities. SPS shows those rows but makes them non-clickable. It is skipped for names with a digit, so "3T3-L1" and "PHQ-9" are protected.

### Context sentences

Decided 2026-06-18 (`docs/tool-context-style-decision.md`): the context for each (tool, paper) is **one complete sentence, verbatim from the abstract, never clamped or paraphrased**.

**Why:** SPS's bio generator grounds on these sentences, and SPS rule D-19 / #1119 only lets extracted text into grounding. A paraphrase probe produced identity contamination.

**The verbatim guard** (`context_quality.py`) requires all of:
- 12–600 characters;
- an exact substring of the abstract, ignoring whitespace and case;
- shares a meaningful word or acronym with the tool name;
- a single sentence.

A failure means the sentence is dropped, never trimmed. The June fix took SPS's mid-clause fragment rate from 40.8% to 5.5% (#238/#239).

**Informativeness** (`context_quality.py`, #253): each sentence gets a heuristic score. It is labeled `usage` when ≥ 0.5, otherwise `mention`.

### Human gate

Producer-model decision #6: new canonical entries are human-gated. In practice:
- **New tools that join an existing family:** published automatically. New `infrastructure` records also publish automatically, with no family.
- **New tools that fit no family:** held in `tools/_review/<run>.json`, with a Teams card. They are not published.
- **Cold rebuilds:** dry-run by default; `--publish` is an explicit operator action (D-07).

## How a weekly run works

`python -m pipeline_tools.maintain --publish`, on Fargate (`reciterai-tools` task), triggered by EventBridge rule `reciterai-tools-weekly` every Tuesday at 03:30 UTC. That is after Monday's hot run and before SPS's 07:00 UTC nightly import.

1. **Lock.** On a `--publish` run, take a DynamoDB lock (`STAGE#tools_maintain#GLOBAL` / `LOCK`, 24-hour lease) so two runs can't overlap. Dry runs take no lock.
2. **Load and check.**
   - Load the durable state (`tools/_state/latest.json`) and the live artifact (`tools/latest/`).
   - Verify every live object's sha256 against the state's baseline. If anything else changed live, **stop** (fail closed).
3. **Extract.**
   - Find corpus rows not yet in the extraction checkpoint (`tools/_checkpoint/a2_extraction_checkpoint.jsonl`) and extract them with Haiku.
   - Spend is capped by `--hard-cap-usd`: default $25, with 90% for extraction.
   - A run that hits the cap publishes what it has; the rest waits for next week.
4. **No new papers?** Status is `skipped` and nothing republishes.
5. **Resolve names.**
   - Known names attach with no LLM call.
   - The rest are classified (Sonnet), then embedding-matched or minted.
   - `excluded` names join the denylist.
   - A paper with any name left unclassified is retried next week.
6. **Families.** Each new `method_tool` is tiered with the published thresholds, then must match an existing family (see above). If none matches, it is held. New `infrastructure` records skip tiering and families and are published.
7. **Add evidence for new papers only.**
   - New PMIDs and verbatim-checked context sentences.
   - New entity rows.
   - Faculty rollup rows appended.

   Existing records are copied untouched.
8. **Guards, all fail closed.** Nothing publishes unless all of these pass:
   - no object shrinks;
   - every live tool is present, with only `pub_count` and aliases allowed to grow;
   - the family ID set is identical;
   - existing context sentences are identical;
   - fragment metrics do not regress;
   - faculty rows are append-only;
   - entities are kept;
   - grant signal is append-only;
   - labels stay unique.
9. **Publish, in a crash-safe order:**
   1. Save a new immutable state version (pointer not moved yet).
   2. Re-check that live is unchanged.
   3. Upload `tools/versions/<run>/`, then `tools/latest/`, manifest last.
   4. Move the state pointer.
   5. Write the review file, then the ledger row `STAGE#tools_maintain#GLOBAL` / `RUN#<start>`.
   6. Send a Teams card if any tools were held.

**Alerting:**
- A Teams ERROR card fires for failures the code catches. A WARN card fires if half the papers fail extraction or the cost cap halts the run.
- The CloudWatch alarm `reciterai-tools-maintain-errors` fires on any ERROR or Traceback log line.
- An out-of-memory kill or an ECS stop writes nothing. It shows only as the ledger row going stale.

**First run (2026-11-11, catch-up, run `2026-11-11T220611Z`):**
- 78 minutes; about $3.60 (extraction $0.50 measured, classification about $3.09 estimated).
- 1,045 papers and grants folded in (1 failed).
- 92 new tools published and 1,278 held.
- 662 existing tools gained 1,114 paper links.
- 96 faculty gained their first tools.

Live is now 18,497 tools, 816 families, 1,412 faculty.

## Where things live

| What | Where |
|---|---|
| Live artifact (what SPS reads) | `s3://wcmc-reciterai-artifacts/tools/latest/` (7 objects + `manifest.json`); flat copies at `tools/<name>` |
| Per-run snapshots | `tools/versions/<run_id>/` |
| Durable state (registries, denylist, bundle, incorporated PMIDs) | `tools/_state/<version>/`, pointer `tools/_state/latest.json` |
| Extraction checkpoint | `tools/_checkpoint/a2_extraction_checkpoint.jsonl` |
| Held-for-review lists | `tools/_review/<run_id>.json` |
| Pre-automation backup (made by hand 2026-11-11) | `tools/_backup/2026-11-11/latest/` and `tools/_state/2026-11-11-local-snapshot/` |
| Run ledger and lock | DynamoDB `reciterai`, PK `STAGE#tools_maintain#GLOBAL` |
| Logs | CloudWatch `/ecs/reciterai-tools` |

**The seven objects:**
- `tools.json`: the bundle (tools, families, faculty, grant signal, thresholds, hierarchy).
- `families.json`
- `faculty.json`: per scholar, their tools and families with PMIDs.
- `tool_context.json`: tool → PMID → sentence.
- `tool_context_meta.json`: informativeness and usage/mention class.
- `entities.json` and `entity_context.json`: the `/methods` page layer. An entity is a tool seen from its family page; cell lines nest under a parent such as "3T3-L1".

**Key code (ReciterAI):**

| Area | Files |
|---|---|
| Extraction | `pipeline_tools/extract.py`, `prompts/tool_extract.py`, `checkpoint.py`, `cost_guard.py` |
| Classification | `pipeline_tools/classify.py`, `prompts/tool_classify.py`, `vocab.py`, `sanity.py` |
| Identity | `pipeline_tools/registry.py`, `ids.py`, `embeddings.py` |
| Families | `family_rebuild.py`, `family_overrides.py`, `family_consolidation.py`, `family_adhoc_dedup.py`, `family_label_uniqueness.py`, `define_families.py`, `relabel.py` |
| Tiers | `salience.py` |
| Context | `context_quality.py` |
| Rollup | `rollup.py` |
| Entities | `entities.py` |
| Weekly run | `maintain.py`, `state.py`, `publish.py` |
| Cold build | `cli/build_tool_taxonomy_corpus.py`, `pipeline_tools/corpus_run.py` |
| Infra | `infra/tools_task_definition.json`, `tools_task_iam_policy.json`, `eventbridge.json`, and `infra/README.md` "Tools maintain launch path" |

**Design docs:** [`tools-producer-model.md`](./tools-producer-model.md) (decisions #5–#8), [`tool-classifier-spec.md`](./tool-classifier-spec.md), [`tools-a2-architecture.md`](./tools-a2-architecture.md), [`tool-context-sidecar.md`](./tool-context-sidecar.md), [`tool-context-style-decision.md`](./tool-context-style-decision.md).

## How SPS uses it

File paths in this section are in the SPS repo.

**Import** (`etl/tools/index.ts`, nightly step `Tools`, continue-tier):
- Reads the manifest and sha256-verifies the four objects it uses: `tools.json`, `tool_context.json`, `entities.json` and `entity_context.json`. It doesn't read `families.json`, `faculty.json` or `tool_context_meta.json`.
- Skips the work when nothing changed, unless publication suppressions changed since the last run (#1502).
- Fully replaces four tables: `scholar_tool`, `scholar_family`, `family_entity` and `family_entity_usage`.
- Has a 50% shrink guard.
- Includes only active scholars and excludes snippets from taken-down PMIDs.

**Mapping:**
- Each scholar keeps their top 30 tools.
- Salience tier becomes a tie-break number only (S 0.9, A 0.7, B 0.5, C 0.3). It is never displayed.
- Family paper counts already exclude C-tier tools upstream.
- SPS keys its overlays on (supercategory, family label), not `family_id`, because it assumes IDs re-mint on a rebuild.

**Where it shows:**
- the profile Methods section;
- `/methods/<supercategory>/<family>` pages;
- center and org-unit method facets;
- people search (`methodFamily` and `methodContext` fields).

The profile and page surfaces are behind `METHODS_LENS_*` flags; search uses `SEARCH_PEOPLE_METHOD_FAMILY` and `SEARCH_PEOPLE_METHOD_CONTEXT`. All are on in prod, the lens since 2026-07-05.

**SPS's own overlays:**
- Suppressed families: 8 seed rows. 2 are generic statistics (conventional statistical testing, regression modeling), 2 are model-validation steps, and 4 are study designs (observational, case-control, longitudinal, quasi-experimental).
- Sensitive families: 33 seed rows, all live-animal model families (the External Affairs-approved subset). They are hidden from the public profile and revealed to internal viewers: any signed-in session, or an on-network visitor when that flag is on.
- Stewards can change both at `/edit`.
- ReciterAI's own family `display` tier (feature/standard/suppressed) is published but SPS does not read it.

**Freshness:**
- `/edit/etl-status` grades Tools on the artifact's `generated_at`.
- An acknowledgement (until 2026-12-31) currently hides staleness.
- SPS does not yet mirror the `tools_maintain` ledger row (Phase 6, below).

## Safe and unsafe operations now that the schedule is on

**Rule:** anything that writes `tools/latest/` outside the weekly run makes the next weekly run refuse to publish (sha mismatch), and recovery is manual.

**Safe:**
- `maintain --preflight-only`;
- `maintain` dry run with `--no-extract`;
- any CLI without `--publish`;
- `cli/tools_state_reconcile.py` (local only).

**Unsafe while the rule is enabled:**
- `build_tool_taxonomy_corpus --publish`: re-mints family IDs and reverts curation;
- `rebuild_tool_context --republish --publish`;
- `rebuild_entity_context --publish`;
- `publish_entity_sidecar --publish`;
- `collapse_alias_forks --publish`.

To change the curated artifact by hand today:
1. Disable the rule.
2. Publish.
3. Rebuild the state by hand. Held tools and `incorporated_pmids` need manual care.

**To pause the schedule:** set `state` to DISABLED for `reciterai-tools-weekly` in `infra/eventbridge.json`, then run `scripts/deploy_cron.sh --rule reciterai-tools-weekly`. Don't use `aws events disable-rule`; the next full deploy re-applies the file.

**Image pinning:** the task runs whatever image tag `reciterai-tools:<latest revision>` names (now `a9a8a7c`). Merging code changes nothing until you build, push and register a new revision. The alias map, generics list and force-C list are baked into the image and **fail open** if missing.

## History

| When | What |
|---|---|
| 2026-06-06 | Producer-model decisions #5–#8 approved |
| 2026-06 (early) | Classifier (#171), corpus extraction (#172), `tools/latest/` + manifest (#173), per-scholar PMIDs (#176), 820-family consolidation (#177), context sidecar (#194), family definitions (#203), label uniqueness (#216) |
| 2026-06-13 | Full corpus build published: 10,005 papers and grants, $29.50 extraction, 32,171 mentions → 23,397 forms → about 18,400 tools |
| 2026-06-18/19 | Verbatim sentence context (#238/#239) |
| 2026-06-20 to 22 | Entity layer for `/methods` pages (#246, #256, #267, #271/#272) |
| 2026-06-23 | Alias-fork collapse (18,410 → 18,405 tools). Last hand publish. |
| 2026-07-10 | #308 closed: three of four ways a rebuild destroyed curation were fixed. Family ID re-minting split to #317. |
| 2026-07-17 | Objects rewritten byte-identical (cause unknown) |
| 2026-11-11 | Weekly incremental run built (#502), catch-up published, schedule enabled (#503) |
| 2026-11-17 | First scheduled run (Tuesday 03:30 UTC) |

## Known gaps

- **No promotion path for held tools.** 1,278 are waiting, and none has a decision yet. Nothing re-tries held tools when families change.
- **Families cannot grow automatically.** Because of #317 (family IDs re-mint on rebuild), no automated path creates a family. Any new research area stays held until a person acts.
- **No tool-level merge in the registry.** Duplicate tools are collapsed after the fact with `collapse_alias_forks`, which cannot run while the schedule is on.
- **Title-only papers.** About 1,000 papers have no abstract. They are still extracted, and their context sentences can't be verbatim-checked.
- **Classification cache keyed by name only.** A prompt or model change silently reuses old classifications.
- **Cost accounting:**
  - Classification spend is estimated, not measured.
  - Fallback, retry, embedding and `--relabel` spend are not counted against the cap.
  - The $0.006-per-paper constant assumes Haiku 4.5 prices, about 10× today's.
  - Haiku 5.5 and Sonnet 5.5 Bedrock prices in `config/llm_prices.yaml` are marked unverified.
- **Unalerted failures.** An out-of-memory kill or ECS stop writes no log; there is no Fargate timeout; task memory use is unmeasured.
- **No sanity checks on newly minted tools.** The weekly run doesn't call the `sanity.py` routing checks.
- **C-tier tools may still be family members and exemplars,** contrary to the spec's "never a family, never an exemplar". The rollup excludes them from counts.
- **Cores depends on family labels.** The cores daily run fails if any of its 7 curated family labels disappears, so `maintain --relabel` can break cores (#397). This is not documented in the maintain docs.
- **Legacy DynamoDB `TOOL#` and `TOOL_INDEX#` items** (about 15k) are still present. SPS stopped reading them on 2026-07-06. Retirement is untracked.
- **Never-merge rows not enforced.** The 8 family `keep_separate` rows in `config/family_adhoc_dedup.json` are documentation only; no code reads them.

**Doc drift:** the ReciterAI-side drift (13 vs 14 supercategories, the `wcm_tool_<slug>` / RRID id scheme, thresholds said to live in `config/thresholds.json`, the producer doc's tier-C list, the rule said to be DISABLED, and the 14/4/16 override counts) was corrected alongside this page with dated notes in the original docs. Still open on the SPS side:
- SPS `docs/scholar-tools-taxonomy.md` still says the lens is dark and reads `ddb`.
- SPS `freshness-policy.ts` says the producer has no schedule.

## Open questions

These are decisions, not bugs. Each one changes what gets built next.

1. **Who reviews held tools, and where?** Options: a reviewer's spreadsheet per run, a GitHub issue per run, the Teams card only, or an SPS `/edit` queue. What turnaround is acceptable?
2. **What does "promote" mean for a tool that fits no family?**
   - Assign it to an existing family by hand.
   - Create a new family (needs #317 fixed, or a minting path that only appends).
   - Publish it with no family. SPS shows families, not bare tools, so it would be nearly invisible.
3. **Should most single-paper tools be published at all?** 1,075 of 1,278 held tools have one paper. One option is to auto-reject single-paper, tier B, no-family tools after N weeks unless they recur.
4. **Should grants keep creating new tools?** 178 held tools come only from grant abstracts and would have no papers to show on a profile.
5. **Fix #317 or live without new families?** Fixing it means match-or-mint for families, durable override tables, and a full validation run. Without it, the family set is frozen at 816 forever.
6. **Is the 2020 floor right?** It is fixed, so the window grows every year. A rolling window (e.g. last 7 years) would change which tools a profile shows.
7. **Tier policy across systems.**
   - The producer doc says "render S/A, collapse B, drop C".
   - SPS uses the tier only as a tie-break and suppresses through its own CSVs.
   - ReciterAI publishes a family display tier that nobody reads.

   Which one is the policy?
8. **Recalibrating tiers.** Thresholds are still marked provisional, and the weekly run freezes existing tiers. When should a deliberate recalibration happen, and who signs off on tools moving tiers?
9. **Alias map ownership.** The alias map is curated by hand and tiny (4 merges, 4 never-merge pairs). Who maintains it, and should it learn from the review decisions?
10. **The `use_context` attribute** (diagnostic / research / therapeutic). It was proposed in June to dissolve cross-supercategory forks and was never adopted.
11. **Daily instead of weekly?** Extraction is incremental, so daily is cheap, but it would multiply review cards.
12. **Cost cap.** Is $25 per run right for steady state? A normal week should cost well under $1.
13. **Retiring the legacy `TOOL#` DynamoDB items.** This is a destructive operation, and no issue tracks it.
14. **Multi-sentence entity context** (PR #274, open since 2026-06-23). Merge, revise or close?
15. **Cores evidence weights** (#394). Method-family evidence is fed to cores, but its weights are pinned at 0 until a labeled panel exists.

## Next steps already planned

- Check the first scheduled run on Tuesday 2026-11-17: the ledger row, cost, number held and the alarm.
- SPS Phase 6:
  - add `tools_maintain: "ReciterAI-tools"` to `PRODUCER_STAGES` and `TRACKED`;
  - set Tools freshness to weekly plus mirror lag;
  - remove the acknowledgement and fix the stale comment.
- Build the promotion step from a reviewer's per-run decisions: Promote, Merge into existing, Reject (Reject adds the name to the denylist).

## Glossary

| Term | Meaning |
|---|---|
| A2 | The current (second) tools pipeline, as opposed to the A1 seed proof (#169) |
| Canonical tool | One `tool_NNNNNN` record; all its names are aliases |
| Denylist | Names classified `excluded`. Haiku may still extract them, but they are dropped at name resolution and never classified or minted again. |
| Disposition | Whether a name is a real tool (`method_tool`), organizational (`infrastructure`) or not a tool (`excluded`) |
| Entity | A tool as shown on a `/methods` family page, with usage sentences |
| Family | A capability group of tools within one supercategory |
| Force-C | The commodity suppress list that pins a tool to tier C |
| Held | A new tool that matched no family; kept in state, not published |
| Maintain run | The weekly incremental refresh |
| Mint | Create a new permanent ID |
| Spread | Number of distinct faculty (first or last author) whose papers use a tool |
| State | The durable registries and bookkeeping in `tools/_state/` that the weekly run starts from |
| Supercategory | One of 14 top-level areas |

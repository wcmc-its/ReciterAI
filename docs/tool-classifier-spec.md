# Tool & Method-Family Classifier — per-batch operating spec (v3)

**Status.** Canonical classifier spec; commit as `docs/tool-classifier-spec.md`. Supersedes the
A1 draft and the loose parts of `docs/tools-producer-model.md`. **Refines the record shape inside
the already-closed gate decision #8 — does not reopen it.** Gate decisions #5 (hybrid vocabulary),
#6 (discovery), #7 (1:N cardinality) stand as decided; only #8's fields are sharpened,
pre-implementation, at zero migration cost. Amend `tools-producer-model.md` §#8 to point here
(now carrying the disposition gate, the attribute set, and the 8-kind / 13-supercategory
vocabularies). Replace that doc's "schema exists so it never has to migrate" line — it is now
false and superseded by this refinement.

**v3 changes vs v2:**
- §8 now defines a **persistent canonical-tool registry** (match-or-mint, durable `canonical_tool_id`) symmetric to the §7 family registry — the structural gap in v2.
- Global salience (§5) split from **per-profile exemplar ranking** (§7.1): a scholar's own usage orders their profile, not institutional spread.
- **S-tier tightened** (§5): RRID is a page-*eligibility* signal, not an S trigger; cross-faculty spread is the S discriminator; numeric cutoffs are an explicit post-A2 calibration step.
- **Cross-supercategory match guard** (§7): a strong out-of-bucket match flags a possible supercategory error to the queue instead of silently forking; plus a periodic family-dedup sweep.
- **Supercategory #13 Structural & biophysical methods added** (12 → 13) — fixes the crystallography mis-route; see the principle note in §1.
- Pinned: C-tier count reconciliation (§8), infrastructure dedup (§8/§9), the core-vs-tool naming rule (§6.2).

The four axes, each doing exactly one job: **`disposition`** — is it ours? · **`kind`** — what is
it? · **`supercategory`** — what's it for? · **`salience`** — how distinctive? They are orthogonal;
never collapse one into another.

The vocabularies in §0.5, §1, §2 are closed: classify *into* them, never invent values. The family
tier (§7) and the tool registry (§8) are the only things that grow, and both grow by
**match-or-mint against a persistent registry**, never from scratch per batch.

**Cardinal rule** (source of every past failure): **read `raw_name` + `context` and decide
independently. Do not route off the incoming `tool_category` tag** — it is an inconsistent
grab-bag. Use it only as a weak prior via §4.

---

## 0. The three tiers

| Tier | Cardinality | Source | Role |
|---|---|---|---|
| **Supercategory** | 13, closed | curated (frozen here) | browse facet; never on the profile |
| **Method family** | hundreds, growing | discovered, accreted (§7 registry) | the workhorse — profile rows, family pages, pub filter |
| **Canonical tool** | the leaves, growing | deduped, accreted (§8 registry) | evidence; exemplars; "who uses X" |

---

## 0.5 Disposition — the entry gate (run FIRST)

Assign one `disposition` before any capability work. Replaces the v1 `is_tool` boolean.

- `method_tool` — a genuine methodological capability (instrument, reagent, organism, assay, dataset, software, method, model). **Only these get a supercategory + family.** Default.
- `infrastructure` — real and publication-attested but *organizational, not methodological*: pure coordinating centers, data-management cores, trial networks (Stroke Trials Network RCC, Colorado DCC). **Canonicalized and queryable (§8) for institutional questions, but never given a supercategory, never minted into a family, never on the profile.**
- `excluded` — not a research tool at all (Amazon Fresh delivery, Xiaomi Mi Band, SEO tools, generic office software). Dropped from the taxonomy; alias kept on a denylist (§8) so it isn't re-triaged each batch.

`disposition` is orthogonal to `salience` (§5): a real-but-undistinctive tool (a gel rig) is
`method_tool` + salience `C`, **not** `excluded`. Excluded = "not one of our objects"; C = "ours,
demoted."

---

## 1. Supercategories — CLOSED SET OF 13

Assign exactly one, to `method_tool` records only. Route by **capability** from name + context —
not by whether it's a device, service, or kit (orthogonal; §2–§3).

1. **Imaging & image analysis** — *clinical/macro-scale image* as primary output + imaging-specific analysis. MRI/CT/PET/echo/X-ray, OCT/cSLO, QSM & dipole-inversion reconstruction, PET dosimetry, eye-tracking imaging. *Out:* therapy beams (→#9), structural/biophysical instruments (→#12), general ML that merely landed on images (→#5).
2. **Microscopy & histology** — *cellular/tissue micro-scale* imaging and staining: two-photon/confocal, intravital multiphoton, MIBI, Opal multiplex IF, FISH-as-imaging, conventional cellular EM. *cryo-EM used for near-atomic structure determination routes to #12, not here — the seam is purpose (cellular imaging vs structure solving), see §6.5.*
3. **Genomics & sequencing** — sequencing platforms + the services delivering them: 10x Chromium, Nanopore, scRNA-seq, ATAC-seq, Ribo-seq, GoT, sgRNA/CRISPR screening libraries.
4. **Mass spec & proteomics** — LC–MS/MS, ICP-MS, SILAC, proteomic workflows.
5. **Computational & statistical methods** — *general* algorithms, models, statistics, study/analysis designs: CNN/SVM/gradient boosting, MD simulation, mixed-effects & quantile regression, causal-inference frameworks, named ML models (Bio_ClinicalBERT, CSPDarkNet, Llama). *Modality-specific computation routes to its modality (QSM → #1).*
6. **Clinical instruments & assays** — bedside/clinical measurement and lab diagnostics: ECG/EEG/EMG, echocardiography, endoscopes, ELISA & PSA/HPV/HCV assays, clinical rating scales (UPDRS-III, Modified Rankin, Animal Fluency), consumable clinical hardware (stents, sheaths, catheters).
7. **Animal & cell models** — `animal_model` + `invitro_biological_model`: transgenic/knockout mice, zebrafish, NHP models, iPSC-derived lines, patient-derived cultures, cell lines (SH-SY5Y, SKBR3), commensal strains.
8. **Molecular & biochemical reagents** — agents used as *research probes/substrates*: antibodies & stains, cytokines, tracers/isotopes, recombinant proteins, CRISPR/Cas9 systems, gene-delivery vectors (AAV) used as tools.
9. **Therapeutics & interventions** — anything whose role is *to treat or intervene*, any kind: small-molecule drugs (pembrolizumab, carboplatin, apixaban, venetoclax, tirzepatide), biologics-as-therapy, cell therapies (CAR-T: ide-cel, cilta-cel, AIC100), oncolytic viruses (VCN-01), **and therapy devices/procedures** (linear accelerator → `kind=instrument`, therapeutic ultrasound, ablation). A linac is an `instrument` in a *therapeutics* supercategory — kind/capability orthogonality, not a misfiling. *Placebo/comparators land here, forced to salience C.*
10. **Datasets & cohorts** — `dataset_*`: claims/EHR (MarketScan, INSIGHT-CRN, Premier, Epic Cosmos), public cohorts & registries (TCGA, SRTR, GWAS sumstats, WISQARS), bibliographic DBs (PubMed, EMBASE).
11. **Software & informatics platforms** — software whose function is *general* (not imaging/genomics analysis): REDCap, R, dashboards, planning/analytics tools without a modality home.
12. **Structural & biophysical methods** *(new in v3)* — macromolecular structure determination and molecular-interaction biophysics: X-ray crystallography, cryo-EM (structure-determination), NMR (structural), SPR, ITC, pulsed-dipolar/EPR spectroscopy. *Coherent capability axis ("I solve structures / measure binding") that had no home — crystallography was wrongly landing in #7.*
13. **Other / uncategorized** — **gated remainder, not a catch-all.** A `method_tool` lands here only if it fits none of #1–#12. The §0.5 gate has already removed errors, non-tool stopwords, and infrastructure, so #13 holds only honest in-domain unknowns.

> **The spine rule — why #12 is added the same round #10 was removed.** A supercategory is one
> facet per real **capability axis**. Coordination (removed in v2) failed that test — it's
> organizational, not a capability. Structural biology passes all of it — a distinct methodological
> capability, a real WCM community, and no existing home. Same test, opposite outcome. The spine
> isn't minimized by count; it's *one facet per capability*. Anything that is real but not a
> capability axis (coordination) is a `disposition` or a **view**, never a facet.

---

## 2. `kind` — CLOSED ENUM OF 8

Orthogonal to supercategory; assigned to `method_tool` records. Normalize *to* this enum.

- `instrument` — physical measuring/operating device (incl. therapy devices; their *capability* routes to #9).
- `reagent` — any administered/applied agent: chemicals, drugs, antibodies, cytokines, tracers, vectors, cell products. *(Therapeutic vs probe is a supercategory call, not a kind.)*
- `organism_or_cells` — animals, cell lines, primary cultures, strains.
- `assay` — a measurement protocol/kit producing a readout.
- `dataset` — a body of data.
- `software` — runnable code/platforms that are *not* a packaged model artifact.
- `method` — procedures, algorithms-in-the-abstract, study/analysis designs.
- `model` — a *named, loadable/runnable* ML artifact (GPT-4, BERT, Llama 3.1-8B, CSPDarkNet).

**`model` vs `method`:** named loadable artifact → `model`; technique in the abstract → `method`.
Bare "CNN" → `method`; "Llama 3.1-8B" → `model`. *(Drives the AI basis line "models, datasets &
software" instead of collapsing to "methods.")*

> `service` is **not** a kind — strip the wrapper (§6.2); the kind is whatever's underneath, and
> `delivery` records the wrapper as an attribute.

---

## 3. Attributes — fields, not buckets

- `delivery`: `in_house` | `shared_core` | `vendor` — replaces service/facility buckets.
- `provenance`: `public` | `proprietary` — replaces `dataset_public/proprietary`.
- `license`: `open_source` | `commercial` | null — replaces `software_*`.
- `consumable`: bool — replaces `instrument_consumable`.
- `rrid_candidate`: bool — durable external-identity signal; gates page-*eligibility* (§5), not S-tier.

> **Retire `functional_category`** — content carried by `kind` + family; keep only as a transient
> clustering feature, never persisted.

---

## 4. Projection from the legacy tags → `kind` (WEAK PRIOR ONLY)

| legacy `tool_category` | `kind` prior | notes / renormalize |
|---|---|---|
| `instrument` | instrument | by modality; therapy beams → #9; crystallography/SPR/ITC/EPR → #12 |
| `instrument_consumable` | instrument | `consumable=true` |
| `reagent` | reagent | supercategory **by use** (§6.1) |
| `cell_product` | reagent | by use: CAR-T/oncolytic → #9; AAV/vector-as-tool → #8 |
| `assay_kit` | assay | by what it measures |
| `animal_model` / `invitro_biological_model` | organism_or_cells | → #7 |
| `dataset_public` / `dataset_proprietary` | dataset | set `provenance` → #10 |
| `software_commercial` | software | `license=commercial`; by function |
| `software_open_source` | software **or model** | `license=open_source`; ML weights → `model` |
| `computational_method` | method **or model** | **de-mix** (§6.3) |
| `service/facility` | *(strip wrapper)* | kind = thing underneath; set `delivery` (§6.2) |
| `core_facility` | *(strip wrapper)* | named as a core → `disposition=infrastructure`; named as its tool → route by capability + `delivery` |

---

## 5. Salience — GROUND IT, don't guess it; and keep it INSTITUTIONAL

`salience_tier ∈ {S, A, B, C}` — an **institution-level** measure (it gates page-worthiness and
noise, *not* the ordering of any one scholar's profile; that's §7.1). `salience_tier_basis =
grounded` when computed from the signals below; else `llm_provisional`, flagged for regrounding.

Signals: `pub_count` (deduped, tool-level, from §8 registry), `rrid_candidate`, and **cross-faculty
spread** = distinct faculty whose lead/senior pubs touch the tool.

> **Seed-time fallback.** Cross-faculty spread and the built-vs-passed-through read need the
> faculty `tool_scores` rollup, which does **not** exist at seed time (needs A2 + the per-faculty
> join). On a seed batch, score from `pub_count` + `rrid_candidate` only, set basis
> `llm_provisional`, queue every record for **A2 regrounding**, and do not treat seed tiers as
> final ranking input.

Tier rule — **spread is the discriminator, RRID only gates eligibility:**
- **S — marquee (page-worthy):** top by cross-faculty spread. *RRID does not by itself make S.*
- **A — distinctive:** `rrid_candidate` (durable identity ⇒ a page is meaningful) with solid `pub_count`, or strong single-lab depth without broad spread.
- **B — routine:** present but undistinctive.
- **C — demote:** never a family, never an exemplar, **excluded from the counts that drive ranking** (§8).

> **Thresholds are a calibration step, not a guess.** RRID is common (~36% of A1), so an
> RRID-triggers-S rule floods the marquee tier — that is why RRID is demoted to A/eligibility above.
> The numeric cutoffs (what spread percentile is S, what `pub_count` floor is A) must be set from
> the **real post-A2 distribution**, not a 300-record sample. Provisional placeholder until then:
> S ≈ top 10–15% by spread; A requires `pub_count ≥ 3` or `rrid_candidate`. Mark as
> `THRESHOLDS_PROVISIONAL` and recalibrate.

Force to **C:** universal-infrastructure stopwords (gel rig, LN₂ freezer, generic centrifuge,
surgical headlight — these are `method_tool`, just demoted); comparators (placebo); **incidental**
standard instruments (a commercial scanner used as-is — "imaging device used to detect X").
*Contrast:* a scanner that is the methodological subject — a fabricated 17/34 MHz transducer, a
novel pulse sequence — stays S/A. Test: **built/modified/central vs passed-through.**

---

## 6. Routing rules

**6.1 — `reagent`/`cell_product` by USE-CONTEXT:** intervention administered/studied → #9; tool to
study something else → #8. Pembrolizumab dosed in a trial → #9; anti-PD-1 antibody probing a
pathway → #8. Same `kind` either way.

**6.2 — Services: strip the wrapper, then classify the NAMED ENTITY.** Is there a method/instrument
underneath? Yes → route to that capability, set `delivery` (scRNA-seq service → #3; clamp service →
#6; tetraploid complementation → #7). No (coordination is the whole thing) → `disposition=infrastructure`.
**Classify what the extraction names:** "University of Colorado DCC" named as a core → infrastructure
even if it runs tools; "REDCap" named as a platform → `method_tool` software even if a core runs it.
*Known watch-item:* a single record that conflates a core with its platform — route to the
exceptions queue.

**6.3 — De-mix `computational_method`:** named ML artifacts (Bio_ClinicalBERT, CSPDarkNet) → `kind=model`, #5; modality-specific reconstruction (QSM) → `kind=method`, route to the **modality** (#1); clinical scales/criteria (UPDRS-III, Modified Rankin, WHO) → `kind=method`, **#6**; study/consensus designs (Delphi, Simon two-stage, GRADE, RCT design) → #5 or demote to C if generic with no scholar-specific signal.

**6.4 — Disposition before #13.** §0.5 runs first (`excluded`, `infrastructure`). #13 Other receives
only `method_tool` records fitting no other supercategory.

**6.5 — Tightened modality seams.** Imaging admits only a *clinical/macro image as primary output*.
Linear accelerator & therapeutic ultrasound → #9. ICP-MS → #4. **Structure determination → #12:**
X-ray crystallography, SPR, ITC, EPR, and **cryo-EM whose purpose is near-atomic structure**;
conventional cellular/tissue EM stays #2. *(The one judgment call in adding #12: cryo-EM is split by
purpose — structure-solving → #12, cell imaging → #2. If you'd rather keep all cryo-EM in #2, it's a
one-line change here.)*

---

## 7. Method-family tier — MATCH-OR-MINT against the registry

Global structure, local batches. **Never re-cluster from scratch** or you fork near-duplicates.

**Inputs each batch:** the `method_tool` records, the **canonical-tool registry (§8)**, and the
**family registry**. *(Infrastructure/excluded records do not enter this tier.)*

**For each `method_tool` (already resolved to a canonical tool via §8):**
1. **Match** to an existing family — embedding NN on the tool + members, gated to the **same supercategory** (precision).
2. **Cross-supercategory guard (v3):** also run NN *across* supercategories. If the nearest family is in a *different* supercategory with high similarity, do **not** silently mint — flag "possible supercategory error" to the exceptions queue (a confident-but-wrong supercategory would otherwise fork a duplicate into the wrong bucket).
3. Else **mint** a provisional family: `family_id`, label (§7.2), supercategory, `status=provisional`.

**Family registry record:**

    family_id        durable, opaque, stable across re-clusters (NEVER derived from label or membership)
    label            controlled display name (§7.2)
    supercategory    one of the 13
    dominant_kind    drives the profile basis line
    member_tool_ids  []  -> canonical_tool_id values from §8 (may shift between runs WITHOUT changing family_id)
    exemplar_tool_ids[]  institutional default; per-profile rows re-rank locally (§7.1)
    status           active | provisional

**Periodic family-dedup sweep (v3):** between batches, run a global cross-supercategory family
similarity pass; merge forked duplicates onto the older `family_id` (never mint on merge — the
D-06 orphan rule applies to families as it does to tools).

> **Durable-id rule (D-06):** a re-cluster that moves members maps onto the *existing* `family_id`,
> never a fresh one, or it orphans every faculty `tool_score`.

**7.1 — Exemplars are PER-PROFILE, ranked by the scholar's own usage (v3).** Institutional salience
(§5) decides page-worthiness and the global C-floor; it must **not** order a scholar's profile. For
scholar X's row, pick the ~3 exemplar tools by **X's own `pub_count` for each tool**, excluding
global C-tier as a floor. *(Otherwise a scholar's signature tool with low institutional spread gets
buried on their own profile.)* The `exemplar_tool_ids` on the registry is only the institutional
default for surfaces that aren't a specific profile.

**7.2 — Label rule: KIND-AWARE, stay in how-space** (avoids colliding with the MeSH-major Topics lens):
- `method`/`model`/`software` → **name the TASK**: "evidence summarization", "risk prediction", "report generation", "LLM clinical evaluation".
- `reagent`/`organism_or_cells` → **name the AGENT/MATERIAL CLASS**, never the biology studied: sevoflurane → "inhalational anesthetics"; SH-SY5Y → "neuroblastoma cell models"; TDF → "antiretrovirals"; PGE2 → "lipid-mediator probes".
- Therapeutics & interventions → **drug class OR therapy modality**: "antiretrovirals", "anti-PD-1 immunotherapy", "external-beam radiotherapy", "catheter ablation" — never the disease.
- Structural & biophysical → **the technique**: "macromolecular crystallography", "binding thermodynamics (ITC/SPR)".
- Fallback to modality/dataset only where a task-name would rhyme with a MeSH heading ("clinical text models", not "biomedical NLP").
- One canonical label per family, stored on the registry; never re-improvised per batch.

---

## 8. Canonical-tool REGISTRY — match-or-mint (NEW in v3, symmetric to §7)

Dedup is not a per-batch operation — it requires a **persistent canonical-tool registry**, the
tool-level mirror of §7. Without it, `canonical_tool_id` cannot be stable across batches ("MRI
scanner" in batch 1 and "magnetic resonance imaging" in batch 50 must resolve to one id) and §7's
`member_tool_ids` are dangling references.

**Inputs each batch:** raw extracted mentions + the current tool registry.

**For each extracted mention:**
1. **Match** to an existing canonical tool by alias/name + embedding NN. Confident → attach: add the raw form to `aliases`, add the publication to the tool's pub-set, append the study `context`.
2. Else **mint** a new `canonical_tool_id` (durable, opaque, never recomputed from name).

**Canonical-tool registry record:**

    canonical_tool_id   durable, opaque, stable across batches (NEVER recomputed from name)
    display_name        canonical surface form
    aliases             []  accretes every observed raw_name/variant
    disposition         method_tool | infrastructure
    kind / supercategory / attributes / salience_tier      (method_tool only)
    member_of_family    family_id (method_tool only)
    pub_ids             set; pub_count = |pub_ids|  (dedup by (tool, pub) -> counts are correct across batches)
    context_evidence    []  retained study contexts

- **Infrastructure records are canonicalized here too** — same match-or-mint and dedup (the Stroke Trials Network RCC repeats → one record); they simply carry no supercategory/kind/family.
- **Excluded** mentions aren't minted, but their aliases are appended to a persistent **denylist** so the same extraction error isn't re-triaged every batch.

**Count semantics (pinned):**
- Tool-level `pub_count` = `|pub_ids|` on the registry record.
- The **profile family-row count** is a *per-faculty* rollup: that scholar's lead/senior pubs touching any **non-C-tier** member of the family. *(C-reconciliation, v3: a pub whose only family hit is a C-tier member does NOT count — consistent with §5 "C excluded from counts that drive ranking." So a family surfaces on a profile only when the scholar touched a B-or-better member.)*
- Pin this once; render-time aggregation must not reinvent it per surface.

---

## 9. Per-batch outputs (emit all three)

**(a) Enriched canonical-tool records** (registry deltas):

    {
      "canonical_tool_id": "...",
      "display_name": "...",
      "disposition": "method_tool",
      "kind": "reagent",
      "supercategory": "therapeutics_interventions",
      "method_family_id": "fam_0142",
      "method_family_label": "antiretrovirals",
      "salience_tier": "B",
      "salience_tier_basis": "grounded",
      "attributes": { "delivery": null, "provenance": null, "license": null,
                      "consumable": false, "rrid_candidate": true },
      "aliases": ["Tenofovir disoproxil fumarate (TDF)", "TDF"],
      "pub_count": 3,
      "context_evidence": ["..."]
    }

For `infrastructure`, null `supercategory`/`kind`/`method_family_*`/`salience_tier`; the record
still carries `canonical_tool_id`, `aliases`, `pub_count`, `context_evidence` (it is canonicalized,
§8). *(Dropped vs the old shape: `functional_category` (retired); `service` kind and `is_tool`
(subsumed by `disposition`).)*

**(b) Registry deltas** — family registry (`attached`/`minted`/`relabeled`/`merged`) and tool
registry (`attached`/`minted`/`alias-added`).

**(c) Two review artifacts:**
- **Compact 3-level hierarchy** (supercategory → family → exemplars + count) — the scannable review format.
- **Exceptions queue** — only: newly *minted* families, low-confidence supercategory assignments, **cross-supercategory match flags (§7 step 2)**, conflated core/platform records (§6.2), `llm_provisional` records awaiting A2, and `infrastructure` calls for spot-audit. Settled families are never re-surfaced; the queue stays bounded.

---

## 10. What is frozen vs what moves

- **Frozen:** 13 supercategories (§1), 8-value kind enum (§2), 3-value disposition gate (§0.5), attribute set (§3), routing rules (§6).
- **Accretes (match-or-mint, durable ids):** the family registry (§7) and the canonical-tool registry (§8).
- **Recomputed as data arrives:** salience (§5; `llm_provisional` → `grounded` after A2; thresholds recalibrated then), per-faculty family counts (§8).
- **Periodic maintenance:** the §7 cross-supercategory family-dedup sweep.
- **Human-in-the-loop, bounded:** the §9(c) exceptions queue only.

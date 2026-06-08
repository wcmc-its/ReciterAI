# Tools/Methods Taxonomy — A2 Architecture & Learnings

How the corpus-wide tool/method taxonomy (Phase 8, "Axis 2") is built, the
decisions behind it, and the lessons that reshaped the approach mid-build. The
frozen classification contract lives in [`tool-classifier-spec.md`](./tool-classifier-spec.md);
this doc is the *implementation* and *rationale*.

---

## 1. What A2 produces

A browsable **Methods lens** for the Scholars Profile System (SPS), shown beside
the MeSH-based "Subjects" lens. From the WCM full-time-faculty **lead/senior-authored
Academic Articles ≥ 2020** (≈8,166 papers) plus **NIH RePORTER grant abstracts**
(≈1,842 projects), it derives:

- **Canonical tools** — deduped instruments/methods/reagents/datasets/etc., each
  with disposition, kind, supercategory, attributes, salience tier, and the
  publications that used it.
- **Method families** — broad capability classes that group the tools (the lens'
  primary unit).
- **Per-faculty rollups** — which tools/families each scholar's lead/senior work
  used, with grounded counts.

Published as `tools.json` (+ split `families.json` / `faculty.json`) to
`s3://wcmc-reciterai-artifacts/tools/`.

---

## 2. The pipeline

```
                                              ┌─────────────── resumable ───────────────┐
 abstracts/grants ──► EXTRACT (Haiku) ──► CLASSIFY (Sonnet→gpt-5.x) ──► TOOL REGISTRY (§8)
   per-paper           raw mentions          disposition/kind/             match-or-mint,
   cost-guarded        (checkpointed)         supercategory/attrs           real pub_ids
                                                                                   │
   FAMILIES (§7) ◄── SALIENCE (§5) ◄──────────────────────────────────────────────┘
   broaden→class        grounded: spread-driven
   →reconcile→group          │
        │                    ▼
        └──► FACULTY ROLLUP (§7.1/§8) ──► §9 outputs + tools.json ──► (dry-run) S3 publish
```

Two CLIs:
- `cli/extract_tool_mentions.py` — the extraction fan-out (Bedrock Haiku per paper).
- `cli/build_tool_taxonomy_corpus.py` — everything downstream (classify → publish).

### Stage detail

| Stage | Module | Model | Notes |
|---|---|---|---|
| Extract | `extract.py`, `corpus.py`, `prompts/tool_extract.py` | Haiku → gpt-5.x | one call/paper; cost-guarded (`cost_guard.py`), checkpointed (`checkpoint.py`) |
| Classify | `classify.py`, `prompts/tool_classify.py` | Sonnet → gpt-5.x | disposition gate + §1–§6 routing; **parallel** (8 workers), **resumable** checkpoint |
| Tool registry | `registry.py` (`ToolRegistry`) | Titan (NN) | surface-key + conservative 0.86 embedding match-or-mint; real `pub_ids` |
| Salience | `salience.py` | — | grounded: cross-faculty **spread** drives S; RRID gates A; force-C floor |
| Families | `family_rebuild.py`, `prompts/tool_family_broaden.py`, `prompts/tool_family_reconcile.py` | Sonnet | **broaden → reconcile → group** (see §3.1/§3.1b); broaden cached |
| Rollup | `rollup.py` | — | per-(scholar,tool) pub_count + §8 C-reconciled per-(scholar,family) count |
| Publish | `publish.py` | — | builds `tools.json`; S3 upload **dry-run by default** (D-07 review gate) |

Orchestrated by `corpus_run.py` (`run_corpus`). The seed/A1 path (`seed.py`,
`cli/build_tool_taxonomy.py`) remains for the 230-name bootstrap.

---

## 3. Key learnings (these changed the build)

### 3.1 Text/embedding matching is unsustainable for *semantic grouping*

The first family pass used the spec's greedy embedding match-or-mint on tool
**names** + a per-family relabel. At A2 scale it produced **89% singleton
families** — the methods lens basically didn't form.

**Why:** "which capability does this tool represent?" is *knowledge*, not
*spelling*. Same-class tools have arbitrarily different names — *nivolumab* vs
*pembrolizumab* are both anti-PD-1, gene symbols and brand names don't embed
alike. Lowering the match cosine to 0.60 still left therapeutics 84% singletons.
It is **not a tuning problem**; name embeddings don't encode therapeutic/method
class.

**Fix — families by capability CLASS (the LLM supplies what embeddings can't):**
1. **Broaden** each tool's name to its §7.2 capability class via the LLM
   (batched per supercategory, concurrent). *anti-IL-6/13/23 → "anti-interleukin
   biologics"; nivolumab/pembrolizumab → "anti-PD-1 immunotherapy".*
2. **Reconcile** the class strings within a supercategory via the LLM — see §3.1b.
3. **Group** tools by (supercategory, canonical class) → one family each.

Capability-class broadening fixed the names→89% problem and dropped singletons to
**50%** (6,460 families).

### 3.1b The embedding wall recurs one level up — reconcile by LLM, not cosine

The broaden pass runs in ~190 independent parallel batches with no shared
vocabulary, so it mints near-unique labels for the *same* capability across
batches: *"transgenic mouse models"* / *"genetically engineered mouse models"*,
*"anti-PD-1 immunotherapy"* / *"anti-PD-1 checkpoint immunotherapy"*. The original
step 2 tried to close this with embedding NN on the class *descriptions* — and hit
**the exact same wall as §3.1, one level up**: the wording variants that should
merge and the sibling classes that must *not* (*"anti-PD-1"* vs *"anti-PD-L1"*,
*"anti-CD20"* vs *"anti-CD38"*) differ by **the same one-token distance**, so no
cosine separates them. Proven: 0.92 left 50% singletons; 0.84 reached only 5,633
families *and* began over-merging siblings. Not a tuning problem.

**Fix — `reconcile_classes` (LLM as arbiter):** per supercategory, walk the
distinct labels most-frequent-first and **match-or-mint** each against an accreted
canonical vocabulary — the model reuses an existing canonical for a wording/synonym
variant or mints a new one for a genuinely distinct capability. Because every batch
sees the vocabulary accreted so far, cross-batch variants reconcile against a stable
set (the root cause). The prompt is biased to *keep-separate when unsure* (a
redundant family is cheap; a false merge corrupts the lens), and crucially strips
*domain/disease* qualifiers — disease specificity belongs on the MeSH Subjects lens,
so *"rodent cardiovascular disease models"* and *"rodent kidney disease models"*
both fold into *"rodent disease models"* (the method), while *behavioral* /
*surgical* / *infection* models (distinct methods) stay separate.

Result on the full corpus: **6,460 → 1,937 families, singletons 50% → 29%**, and
the over-merge guard holds (anti-PD-1/PD-L1/CD20/CD38/CTLA-4 each stay a distinct
family). Altitude is asymmetric *by design*: therapeutics stays granular (target
precision, 45% singletons), computational/clinical consolidate hard (method-class,
not domain-variant). Broaden output is cached to `_checkpoint`, so tuning reconcile
re-runs for $0 — only the cheap LLM pass repeats.

### 3.2 The durable architecture: conservative substrate + semantic grouping

The lesson generalizes to a division of labor by what each method is *good at*:

- **Text/embedding** does only the jobs it's safe at: deterministic surface-key
  matching and conservative tool dedup (never a *false* merge → the "truly
  different, not synonyms" guarantee). It is *not* trusted with class grouping at
  any level — §3.1b showed the wall recurs on class descriptions too.
- **Semantic knowledge (the LLM)** does every part where the judgment is "same
  capability or not": assigning the capability class *and* reconciling the class
  vocabulary (match-or-mint with the LLM as arbiter).

So the **tool layer stays granular-but-safe** (18k tools, conservative 0.86 NN,
under-merged on purpose) and the **semantic family layer reconciles it**.
Granularity at the tool level is acceptable *because* families group it. If
tool-level duplication ever proves to hurt, the lever is semantic (LLM)
canonicalization of tools — more expensive, deferred until shown necessary.

### 3.3 Grants feed signal but stay out of the pub-filter

Grant-sourced mentions (`source_kind="grant"`, id `grant:<appl_id>`) establish
tool *identity* and contribute a **separate grant signal** (distinct appl_ids +
investigator CWIDs), but never enter `pub_ids`/`pub_count` or the faculty rollup —
the publication pub-filter stays publication-only. One-line revert if grants
should count toward `pub_count`.

### 3.4 Salience is grounded by spread, not guessed

At A2 the per-faculty join exists, so §5 regrounds: **cross-faculty spread** (how
many lead/senior faculty's pubs touch a tool) drives the marquee S tier; RRID
only gates A; the S cutoff is the **calibrated** spread percentile of the real
distribution (`basis=grounded`). Tools with no A2 signal honestly keep
`llm_provisional` rather than being silently demoted.

### 3.5 Performance: the bottleneck was never the LLM

The corpus orchestrator was projected at 3–8h; profiling found the cost was
**serialized I/O + pure-Python similarity math**, not model latency:

- **numpy-vectorized `nearest_match`** — one matmul vs a per-candidate Python
  cosine loop (455 ms → 0.2 ms/query); vectors cached as ndarrays so the matrix
  build is a C memcpy (108 ms → ~10 ms).
- **Threaded Titan embeds** with per-call transient-retry + a sized connection
  pool, and a one-shot **pre-warm** so the match loop is pure matrix math.
- **Parallel classify** (8 workers) — the batches are independent; content-filter
  → gpt-5.x fallbacks overlap instead of serializing (~8h → ~40 min). Content
  filtering is *not* the cost — only ~1.7% of batches hit it.

Result: full run ~1–1.5h, every expensive stage either checkpointed or cheaply
recomputable.

### 3.6 Crash-safety + provenance

- **Resumable classify checkpoint** (`classify_cache.jsonl`, keyed by normalized
  name) — a crash re-classifies only missing forms, never re-spends the ~$21
  Sonnet pass. (Paid off repeatedly during the build.)
- **Per-inference model provenance** — extraction records the model per PMID; the
  classify/relabel seam now stamps which model answered (Sonnet vs gpt-5.x) onto
  each record (`classified_by`) so the ~1–2% gpt-5.x classifications are
  auditable. Cannot be backfilled from logs (the fallback line has no form id and
  parallel workers interleave it).

### 3.7 The `context` field is load-bearing and under-surfaced

Every mention carries a ≤200-char grounding snippet. It drives use-context
routing (§6.1 reagent-as-therapeutic vs probe) and is retained as
`context_evidence` on each canonical tool in `tools.json`. **It is captured but
not yet surfaced in the SPS UI** — an opportunity ("how WCM scholars used this"
on tool/family pages), since the data is already there.

### 3.8 Review-hardening + the cache-keying bug

Family review of the 1,937-family set drove four upstream fixes (never hand-edits
of the output): a **≥3-member floor** (`form_families`; a 1–2 member "family" is a
labelled tool, not a community → 1,937→1,080 post-floor), a **cross-supercategory
fork guard** (`relabel.cross_supercategory_label_forks`: identical label in two
frozen buckets ⇒ a mis-route → flag to the queue, never a silent cross-bucket
merge), **supercategory #14** (`functional_metabolic_cellular_assays`, the
spine-rule Seahorse-XF/metabolic community pulled out of `other`), and resolving a
tool's supercategory by **deterministic pub-weighted majority vote** across its
member forms rather than whichever form minted it.

The expensive lesson was a **cache-coherence bug**: the broaden/reconcile caches
were keyed by `canonical_tool_id`, a mint-order artifact. Re-running after a
reclassify (which flips some dispositions) renumbers match-or-mint, so cached
labels silently attached to the wrong tools at scale — a fungal strain inheriting
"regression modeling" — which spread one label across 13 supercategories, doubled
the reconcile input (6,460→12,814 distinct (supercat,label) pairs), and fragmented
families (1,080→1,697) with 291 spurious forks. It masqueraded as a clustering
regression; three reasoned diagnoses were wrong before a 30-second fresh-broaden
probe pinned it. Fix: **key caches by the stable content the value depends on** —
broaden by `norm(display_name)`, reconcile by `(supercategory, broad_label)` — so
they survive id drift (tested). Lesson: probe a cache/determinism hypothesis on real
data; don't reason about it.

Two further boundary fixes followed: **#6 sharpened to clinical/diagnostic-only**
(bench assays→#8, surgical→#9, echo→#1, cytogenetics→#3) and **#14 tightened to
metabolic/bioenergetic-only** with **#8 as the home for bench mechanistic assays**.
These cascade — sharpening #6 pushed functional assays into #14 (bloating it 17→47)
until #14 was tightened in turn. **Frozen at v6: 894 families, computational 152,
#14=8 (tight metabolic), 17 cross-supercategory forks routed to the review queue.**
The forks are ~irreducible below ~15 because adjacent buckets genuinely blur (a
biopsy is both diagnostic and procedural; an AAV vector is both reagent and therapy)
— the queue is the designed handling, not a defect.

---

## 4. Cost & scale (measured)

| | |
|---|---|
| Extraction | 10,005 papers+grants, **$29.50** Haiku (0 OpenAI fallbacks), ~6h |
| Unique surface forms | 23,397 (from 32,171 raw mentions; ~21% attach as dupes/synonyms) |
| Classify | ~468 batches Sonnet, ~$15–20, ~40 min parallel |
| Canonical tools | ~18,386 (conservative dedup) |
| Families (greedy name-match, **rejected**) | 15,752, 89% singletons |
| Families (capability-class broaden + embedding consolidate, **superseded**) | 6,460, 50% singletons |
| Families (broaden + **LLM reconcile**) | 1,937 pre-floor, 29% singletons |
| Families (+ **≥3 floor, #14, crisp #6/#8/#14, review fixes**) | **894 post-floor (FROZEN v6)** (computational 152, #14=8, 17 forks→queue); see §3.8 |

---

## 5. Module map (`pipeline_tools/`)

| Module | Role |
|---|---|
| `vocab.py` | frozen closed sets (13 supercats, 8 kinds, 3 dispositions, attrs) |
| `ids.py` | durable opaque ids (`tool_000001` / `fam_0001`, D-06) |
| `embeddings.py` | Titan v2 NN — numpy-vectorized, threaded+retried, cache as ndarrays |
| `registry.py` | `ToolRegistry`/`FamilyRegistry` match-or-mint, accretion, denylist |
| `cost_guard.py` / `checkpoint.py` | extraction preflight + runtime ceiling; resumable extraction log |
| `extract.py` / `corpus.py` | per-paper extraction; pub + grant corpus loaders |
| `classify.py` | disposition gate + §1–§6 routing; `classify_batch` is the parallel unit |
| `salience.py` | seed + **grounded** §5 tiering |
| `family_rebuild.py` | **capability-class family formation** (broaden→reconcile→group); `reconcile_classes` = LLM match-or-mint over class vocab; `consolidate_classes` = embedding fallback; broaden cached |
| `relabel.py` | §7.2 relabel + exact-label dedup (legacy family path; superseded by family_rebuild) |
| `rollup.py` | per-(scholar,tool) + C-reconciled per-(scholar,family) counts |
| `publish.py` | `tools.json` assembly + S3 publisher (dry-run default) |
| `seed.py` / `corpus_run.py` | A1 seed orchestrator / A2 corpus orchestrator |

---

## 6. Open items

- **Family altitude — verified.** Broaden + LLM reconcile lands 1,937 broad-but-distinct
  families (29% singletons); same-class/different-name tools group and sibling
  targets stay separate (over-merge guard holds). Optional altitude tune: the
  computational/clinical collapse is aggressive (method-class, not domain-variant) —
  a one-prompt change + $0-broaden re-run if a finer altitude is wanted there.
- **`classified_by` provenance backfill** — this run is aggregate-only (~98% Sonnet);
  stamp `classified_by = us.anthropic.claude-sonnet-4-6` (assumed) on the published
  tools. Per-form provenance is captured for real on the next run.
- **Surface `context_evidence`** in SPS (§3.7).
- **Publish** — `--publish` is gated behind human review of the artifacts (D-07).
- **Legacy DynamoDB supersede** — retire the stale `TOOL#`/`TOOL_INDEX#` items
  (paused-chatbot extraction); intentionally a separate reviewed step (destructive,
  unpinned schema), not in `publish.py`.
- **Optional, deferred:** semantic tool-level canonicalization if tool granularity
  hurts (18k tools, conservative dedup — under-merged on purpose).

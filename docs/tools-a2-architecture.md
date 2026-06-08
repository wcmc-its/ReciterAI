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
   →consolidate→group        │
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
| Families | `family_rebuild.py`, `prompts/tool_family_broaden.py` | Sonnet + Titan | **broaden → consolidate → group** (see §3) |
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
2. **Consolidate** the class strings within a supercategory by embedding NN —
   class *descriptions* embed well (they're descriptive phrases), so cross-batch
   wording variants ("kinase inhibitors" ≈ "kinase inhibitor therapeutics") merge.
3. **Group** tools by (supercategory, canonical class) → one family each.

Validation: 120 granular therapeutics labels → **57 broad classes** at the right
altitude.

### 3.2 The durable architecture: conservative substrate + semantic grouping

The lesson generalizes to a division of labor by what each method is *good at*:

- **Text/embedding** does only the jobs it's safe at: deterministic surface-key
  matching, conservative tool dedup (never a *false* merge → the "truly different,
  not synonyms" guarantee), and merging near-identical class *descriptions*.
- **Semantic knowledge (the LLM)** does the part only it can: assigning the
  capability class.

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

---

## 4. Cost & scale (measured)

| | |
|---|---|
| Extraction | 10,005 papers+grants, **$29.50** Haiku (0 OpenAI fallbacks), ~6h |
| Unique surface forms | 23,397 (from 32,171 raw mentions; ~21% attach as dupes/synonyms) |
| Classify | ~468 batches Sonnet, ~$15–20, ~40 min parallel |
| Canonical tools | ~18,386 (conservative dedup) |
| Families (greedy, **rejected**) | 15,752, 89% singletons |
| Families (capability class) | *pending the rebuild — target a few thousand real families* |

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
| `family_rebuild.py` | **capability-class family formation** (broaden→consolidate→group) |
| `relabel.py` | §7.2 relabel + exact-label dedup (legacy family path; superseded by family_rebuild) |
| `rollup.py` | per-(scholar,tool) + C-reconciled per-(scholar,family) counts |
| `publish.py` | `tools.json` assembly + S3 publisher (dry-run default) |
| `seed.py` / `corpus_run.py` | A1 seed orchestrator / A2 corpus orchestrator |

---

## 6. Open items

- **Family altitude verification** — confirm the rebuilt families land at a usable
  altitude (broad-but-distinct) and same-class/different-name tools group.
- **Surface `context_evidence`** in SPS (§3.7).
- **Legacy DynamoDB supersede** — retire the stale `TOOL#`/`TOOL_INDEX#` items
  (paused-chatbot extraction); intentionally a separate reviewed step (destructive,
  unpinned schema), not in `publish.py`.
- **Publish** — `--publish` is gated behind human review of the artifacts (D-07).
- **Optional, deferred:** cut Titan from the family stage entirely (LLM/controlled
  vocab consolidation); semantic tool-level canonicalization if granularity hurts.

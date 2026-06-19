# Tool-usage `context` — content-style decision (quote vs. gloss)

**Status:** **DECIDED (2026-06-18)** — `context` stays **extracted verbatim**: one COMPLETE sentence that names the tool, copied in full, **never clamped**. The gloss direction was explored and **rejected** on the SPS consumer's own contract. Quality is enforced by shared guards, not a character budget.
**Refs:** #238, PR #239, #193, SPS #1119 / #1122 / #879 D-19, `pipeline_tools/context_quality.py`, `cli/rebuild_tool_context.py`, `docs/tool-context-sidecar.md`, `docs/tools-a2-architecture.md §3.7`

---

## 1. What this was about

Every extracted tool/method mention carries a per-`(tool, pmid)` `context` field with **two consumers**:

1. **The classifier** (`prompts/tool_classify.py`) reads it for *routing* (reagent-as-therapeutic vs. probe).
2. **SPS** surfaces it as the **"How *X* is used"** snippet (via the `tool_context.json` sidecar).

SPS's calibration (PR #1122) found the live `context` frequently reads as a **broken mid-clause fragment** (~16% judged broken; 43% start mid-clause). That spawned #238 → PR #239, which makes the extractor emit the **complete enclosing sentence** instead of a fragment.

A tempting detour followed: the legacy POC produced short paraphrased *glosses* ("High-field imaging device") that read cleaner than any quote, and "verbatim isn't useful downstream" suggested switching `context` to a gloss. **This document explored that, then resolved it against the SPS consumer's actual behaviour: the gloss is wrong for this field. Keep the verbatim.**

---

## 2. The two artifacts (data)

| | **Legacy** `reciterai_tools.context` | **Live A2** `context` (current Haiku) |
|---|---|---|
| Origin | POC `d` field — *"short use context/definition (≤58 chars)"*, gpt-5/o3 | Haiku, *"verbatim-ish ≤200 char"* |
| Content type | Paraphrased **descriptor** (not a quote) | **Verbatim excerpt** |
| Rows / pmids | 6,728 / **2,796** (frozen POC scope) | 32,171 / **8,818** (full corpus) |
| Start lowercase (mid-clause) | 1% | **43%** |
| Reads standalone? | Yes | Often no (the #238 problem) |
| Median length | 48 char | 128 char |

The legacy *values* can't be reused (stale, ~⅓ coverage, a different model's ungrounded paraphrase — the sidecar doc marks the table *"not a source"*). The only live question was whether to reproduce the legacy *style* fresh.

---

## 3. Probe — gloss is *achievable*, but that was never the question (2026-06-18)

For 768 `(tool, pmid)` pairs in both tables, we generated a fresh gloss with current Haiku (grounded in the abstract) and compared it to the legacy `d` and the verbatim quote. Findings: current Haiku **can** reproduce the gloss style at ~$0; but it runs long (median 75 vs. legacy 48) and, critically, exhibits a **systematic identity-contamination failure** — e.g. "Premier Healthcare Database" (a general discharge DB) glossed as *"national cardiac surgery database…"* because this paper's cohort was cardiac. **A gloss is a lossy, sometimes-wrong compression.** That property is what §4 makes decisive.

(Full probe table retained in the PR thread / commit history.)

---

## 4. What the SPS consumer actually does (re-grounded on `origin/master`, 2026-06-18)

The decision hinges on how SPS consumes `context`. Verified against `origin/master` (an initial probe read a 177-commit-stale branch and got Q2 wrong):

**(a) The bio generator grounds on `context` with NO abstract fallback.** `lib/edit/overview-facts.ts:48` — *"deliberately NO `abstractExcerpt` (v3.1 decision 4 — distilled signals replace the raw abstract)"*; the tool snippet is grounding-eligible text the model reads directly. **So `context` fidelity is load-bearing** — a wrong gloss launders straight into the published bio with no source text to catch it (the Premier failure, realised).

**(b) `context` is surfaced to users verbatim.** Two live surfaces:
- Search result card — `How {tool} is used: {context}` (`components/search/people-result-card.tsx:326`)
- Profile methods hover — `How {tool} was used` + `{context}` (`components/profile/methods-section.tsx:99`)

So display readability is a **live** requirement (this is #238's surface), and provenance is half-wired: `etl/tools/tool-context.ts:24` — *"Keep the source pmid for provenance."*

**(c) SPS has a LOCKED contract that `context` must be extracted text, not a gloss.** `lib/edit/overview-facts.ts:362-368`:
> `#879 D-19 LOCKED` — the *generated* family `definition` is RENDER-ONLY and **must never enter the bio generator's grounding**. … `#1119` — `exemplarContexts` … is **EXTRACTED real publication text … grounding-eligible like `synopsis`, not a generated gloss.**

SPS deliberately bars generated glosses from LLM grounding and admits only extracted text. A gloss `context` would either be barred from grounding (defeating its purpose) or violate D-19.

**(d) Division of labour is already clean.** ReciterAI supplies *extracted evidence* (`context`); SPS generates its own *render-only labels* (family `definition`). A gloss from ReciterAI would be redundant with `definition` **and** contract-violating.

---

## 5. Decision

**`context` stays an extracted, sentence-aligned verbatim span (PR #239). The gloss is rejected for this field.** It loses on every axis the real consumer cares about:

| Axis | Verbatim (PR #239) | Gloss |
|---|---|---|
| **Bio grounding** (no abstract fallback) | Max fidelity; safe | Lossy; launders errors into bios (Premier) |
| **User display** ("How X is used") | Complete sentence reads standalone — #239 fixes the fragment | Reads clean, but replaces the authors' framing with a paraphrase |
| **SPS contract** (D-19 / #1119) | Required (extracted, grounding-eligible) | **Barred** from grounding (generated) |
| **Classifier routing** | Lossless | Flattens fine distinctions (therapy vs. stain) |
| **Provenance / receipt** | Traceable to the paper (pmid kept) | A paraphrase is not a receipt |

The earlier premise — "verbatim isn't useful downstream" — is simply false for this consumer: SPS displays it verbatim, grounds an LLM on it with no abstract, and **explicitly chose extracted-not-gloss**. #239 is not superseded; it is the answer. **#238 recharacterizes to: ship #239 (sentence-align) + the §6 quality contract, and backfill the live artifact (`cli.rebuild_tool_context`). The gloss path is closed (this doc is the record of why).**

### Axis "definitional vs. usage" — moot
There is no gloss to author, so the voice question dissolves. (Had a gloss been needed, the right spec was *identity-anchored usage* — tool identity as subject, usage as modifier clause — not a definitional/usage XOR. Recorded for the record only.)

---

## 6. The quality contract: complete sentence, no clamp, guarded

The snippet is **one complete sentence, copied in full, never truncated.** An early version pinned the producer budget to SPS's display clamp (`MAX_SNIPPET_LEN = 240`, `etl/tools/tool-context.ts`) so "stored == displayed" — but that re-introduced the #238 fragment at the *tail*: a 241+-char sentence got cut mid-clause. Clamping anywhere just relocates the fragment. So there is **no character budget**; SPS applies its own 240 display clamp (with an ellipsis) on its side, which is its concern, while the artifact and the bio-grounding carry the complete, faithful sentence.

Quality is enforced by **shared guards** (`pipeline_tools/context_quality.py`), used identically by the live backfill and the extraction path so "now" and "going forward" never diverge. A snippet is accepted only if it is:

1. **verbatim** — a contiguous span of the abstract (extracted, not generated);
2. **tool-naming** — references the specific tool (shares a salient name token or its acronym), so it is about *X* and not a neighbouring sentence;
3. **one sentence** — not two merged into a run-on (whether the merge has a stray internal period or a missing one).

A snippet that fails is **dropped, never clamped**: the backfill keeps the prior snippet; extraction emits no snippet for that mention (SPS selects another). A run-on ceiling (`MAX_SENTENCE_CHARS = 600`) bounds pathological whole-abstract returns. Live calibration of these guards: ~1.7% relevance rejects, ~0.1% run-on rejects, all spot-checked as justified.

**Now:** `cli/rebuild_tool_context.py` regenerated the live sidecar (6,981 papers, context-only, tool set frozen): mid-clause starts 36% → ~5%, end-clean ~79% → 100%. **Going forward:** `prompts/tool_extract.py` + `pipeline_tools/extract.py` apply the same prompt + guards at extraction, so the next cold run produces conforming snippets at source.

---

## 7. The POC recipe (reference)

Legacy `reciterai_tools.context` = the POC `d` field (`ReciterAI-POC/core/tools_extraction_v2_production.py`): *"d: short use context/definition (≤58 chars)"*, gpt-5/o3, per-publication — a generated gloss. Exactly the class of artifact SPS's D-19 rule keeps out of grounding.

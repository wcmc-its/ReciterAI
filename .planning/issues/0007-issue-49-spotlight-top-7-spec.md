---
issue: 0007
github_issue: 49
title: Issue #49 SPEC — spotlight artifact ships top-7 publications per subtopic with deterministic tiebreak
status: drafted 2026-05-15; awaiting review before implementation
filed: 2026-05-15
filed_by: SPEC pass per triage table item 5 (different lane from #37 thread)
related_issues: [49]
related_external_issues:
  - wcmc-its/Scholars-Profile-System#286  (consumer-side sampling)
related_artifacts:
  - docs/spotlight-contract.md
  - docs/spotlight.schema.json
  - spotlight/pool_ranker.py
  - spotlight/assembler.py
  - spotlight/lede_generator.py
  - spotlight/critic.py
  - spotlight/types.py
  - config/thresholds.json
---

# #49 — Ship top-7 publications per subtopic in the spotlight artifact

## What the issue says vs what the code says

Issue #49 frames this as a "trivial two-line change" — bump the publication count per spotlight from 2–3 to 7 and add a deterministic tiebreak. SPS companion (`wcmc-its/Scholars-Profile-System#286`) frames it the same way.

Reading the code first uncovered an important contract shift the framing didn't surface:

| Layer | Current behavior | What "ship 7" actually requires |
|---|---|---|
| `config/thresholds.json` `pool_top_papers_per_subtopic` | **6** (not 2-3 as the issue text suggests) | Bump to **7**, used for both pool-score computation and the artifact's published count |
| `spotlight/pool_ranker.py:289` sort key | `-impact_score` only (no deterministic tiebreak) | Add `(-impact_score, pmid_asc, -year_desc)` tuple sort; ties on impact_score currently resolve by DDB scan order |
| `spotlight/lede_generator.py` `MIN_PAPERS=2`, `MAX_PAPERS=3` | The lede LLM grounds on top 2-3 papers by impact | **Unchanged.** Editorial grounding stays at 2-3 per issue's "no editorial logic changes." |
| `spotlight/assembler.py:155` artifact `papers` field | Built from `vlede.papers_used` = the 2-3 papers the lede grounded against | **Decoupled.** Built from the **pool's top-7** per subtopic, NOT from `papers_used`. This is the contract shift. |
| `docs/spotlight.schema.json` `Spotlight.papers` | `minItems: 2, maxItems: 3` | `minItems: 2, maxItems: 7` (floor stays so under-supplied subtopics don't drop) |

**The contract shift in one sentence:** `Spotlight.papers` changes from *"the papers the lede grounded against"* (a 2-3 editorial slice) to *"the top-7 pool the consumer samples from"* (a 7-paper consumer artifact). Lede grounding remains a separate internal concept and stays at 2-3.

This is not a "two-line change." It's a contract shift in `papers` semantics with downstream consumer (SPS) coordination.

## Concrete contract change

### Schema

`docs/spotlight.schema.json` line 85-92:

```json
"papers": {
  "type": "array",
  "minItems": 2,
  "maxItems": 7,                    // ← was 3
  "items": { "$ref": "#/$defs/Paper" }
}
```

Floor stays at 2 — the current behavior dropping subtopics with <2 valid papers is preserved. Subtopics with 2-6 valid papers ship what they have; subtopics with ≥7 ship exactly 7 (sorted by impact_score DESC, with deterministic tiebreak).

`Paper` shape (lines 28-54) is unchanged. The `first_author` + `last_author` requirement (with `person_identifier` + `display_name` + `position` fields per `$defs/Author`) already satisfies SPS's "verify before merge" item — the schema confirms the fields are present.

### Deterministic tiebreak

`spotlight/pool_ranker.py:289` changes:

```python
# Before:
top = sorted(papers, key=lambda p: -p.impact_score)[:top_papers_per_subtopic]

# After:
top = sorted(
    papers,
    key=lambda p: (-p.impact_score, p.pmid, -p.year),
)[:top_papers_per_subtopic]
```

PMID is a numeric string in the schema (`pattern: "^[0-9]+$"`); sort comparison on PMID strings of equal length is numerically correct. If PMID lengths vary (they will across the corpus), string comparison sorts shorter-PMID papers first ("1234" < "12345"), which is **incorrect** for "PMID ascending." Two options:

- **Option 1 (preferred):** cast to int for the sort key: `key=lambda p: (-p.impact_score, int(p.pmid), -p.year)`. Matches the natural reading of "PMID ascending."
- **Option 2:** keep string comparison and document that "PMID ascending" means lexicographic-on-the-stored-string. Reproducible but counter-intuitive.

Pick Option 1.

### Threshold bump

`config/thresholds.json`:

```json
"pool_top_papers_per_subtopic": 7   // was 6
```

This value is loaded by `spotlight/pool_ranker.py:37` and threaded as `top_papers_per_subtopic` into `rank_pool`. The pool_score computation (line 290) sums the impact_score of the top-N papers; changing 6 → 7 changes pool_score for every subtopic that has 7+ qualifying papers. Pool ordering shifts slightly; rotation history (`history_writer.py`) and the rotation selector pick may shift downstream — note in PR description, no test breakage expected because tests don't pin pool_score values to constants.

### Assembler decouple

`spotlight/assembler.py:155` changes from:

```python
papers = [paper_metadata[pmid] for pmid in vlede.papers_used]
```

To something like:

```python
# vlede.papers_used is the 2-3 papers the lede grounded against (editorial subset).
# The artifact emits the full subtopic pool (up to top_papers_per_subtopic) so
# SPS can sample from a larger surface. The 2-3 grounded papers are guaranteed
# to be a subset of this pool by construction (lede_generator grounds against
# the same impact-score-sorted slice).
subtopic_pool = subtopic_pools[vlede.subtopic_id]
papers = [paper_metadata[pmid] for pmid in subtopic_pool]
```

Wiring requirement: `build_artifact` needs a new input parameter `subtopic_pools: dict[str, tuple[str, ...]]` carrying the PMID-tuple per subtopic. The source is `PoolEntry.papers` (already on each pool entry, see `pool_ranker.py:301`), so the caller (publisher) extracts and passes it. ~5-line change in `assembler.py`'s signature and ~3-line change in the caller.

**Audit-trail decision:** `vlede.papers_used` is still emitted somewhere for editorial provenance, OR dropped from the artifact entirely. Today it's only used to build `papers`, not emitted as a separate field. The artifact loses no information IF we don't carry it forward — but losing the "which 2-3 grounded the lede" link makes editorial debugging harder. Recommend adding a `lede_grounded_pmids` field to `Spotlight` as a separate string array of 2-3 PMIDs, so post-hoc auditing of "why did the lede say X?" still works against published artifacts.

### Lede generator — unchanged

`spotlight/lede_generator.py` `MIN_PAPERS=2`, `MAX_PAPERS=3` remain. Per the issue's "no editorial logic changes." The LLM still gets 2-3 grounding papers; only the artifact's published `papers` array grows.

### Critic — verify

`spotlight/critic.py` uses `papers_used` for in-prompt validation rendering (lines 381, 392, 511, 543, 579, 676). None of these touch the artifact's `papers` field — they operate on the lede-grounding subset, which is unchanged. **Expected no critic changes.** Verify during implementation.

## What this SPEC does NOT do

- **No diversity-aware re-rank.** Per issue: "All editorial logic stays downstream." SPS handles author concentration via soft re-roll in #286.
- **No impact-signal replacement.** Impact score remains the sole upstream ranking signal.
- **No spotlight-level rotation changes.** Which subtopics get into the artifact (top-N by pool_score) is unchanged.
- **No `lede` text changes.** The lede LLM grounds on 2-3 papers, same prompt, same temperature, same output expectations.

## Test coverage to add

1. **Pool ranker tiebreak determinism.** Two-paper scenario with identical impact_score, different PMIDs and years. Run `rank_pool` twice with the same input; assert identical PMID order in `PoolEntry.papers`. Then permute the input list and re-run; assert identical output (tests the sort is total, not stable-only).
2. **Threshold bump regression.** Pool with ≥7 qualifying papers in some subtopic; assert `PoolEntry.papers` length is exactly 7, sorted by `(-impact_score, int(pmid), -year)`.
3. **Schema roundtrip with 7 papers.** Build an artifact with a subtopic that has 7 papers; assert it validates against `docs/spotlight.schema.json` (catches missed `maxItems` update).
4. **Schema roundtrip with under-supplied subtopic.** Build an artifact with a subtopic that has only 3 valid papers; assert it validates (catches accidental `minItems: 7` change).
5. **Assembler decouple sanity.** Mock a `ValidatedLede` with `papers_used = (pmid_A, pmid_B)` and a `subtopic_pools` mapping with 7 PMIDs for the same subtopic. Assert artifact's `papers` array has 7 entries, `papers_used` is a subset of them, and (if the audit field is added) `lede_grounded_pmids` equals `papers_used`.
6. **Caller wiring.** End-to-end test from `pipeline_spotlight.orchestrator` through to artifact JSON; assert artifact pages with 7-paper subtopics survive the full pipeline.

## Implementation plan (estimate)

| Step | Files | Lines | Effort |
|---|---|---|---|
| 1. Schema bump (`maxItems: 7`) | `docs/spotlight.schema.json` | ±1 | 5 min |
| 2. Threshold bump | `config/thresholds.json` | ±1 | 2 min |
| 3. Tiebreak sort in pool_ranker | `spotlight/pool_ranker.py` | ±5 | 15 min |
| 4. Assembler decouple + `subtopic_pools` parameter | `spotlight/assembler.py`, callers in `pipeline_spotlight/orchestrator.py` | ±25 | 1 hour |
| 5. (Optional) `lede_grounded_pmids` audit field | `spotlight/assembler.py`, `docs/spotlight.schema.json`, `spotlight/types.py` | ±15 | 30 min |
| 6. Tests (6 above) | `tests/test_pool_ranker.py`, `tests/test_assembler.py`, `tests/test_spotlight_schema.py` | ±100 | 2-3 hours |
| 7. Sample artifact + dry-run validation | manual, against fixtures | — | 30 min |
| 8. Coordinate with SPS-side #286 cutover | comment + cross-link | — | 15 min |

**Total estimate: ~5 hours.** Bigger than the "two-line" framing but bounded.

## Risks

- **Pool_score shifts.** Threshold bump changes pool_score for any subtopic with ≥7 qualifying papers. Rotation history could mark different subtopics as "winners" in the next cycle. Mitigation: implementation PR notes this in the description; no code change needed beyond confirming history_writer doesn't crash on the new pool_score values.
- **SPS-side cutover window.** Between artifact ships-7 and SPS consumes-7, SPS could fail validation on the larger `papers` array. Mitigation: SPS issue #286 needs to land its schema-permissive code before this artifact change goes prod. Sequence: SPS opens its PR with the relaxed schema first; ReciterAI ships #49 second.
- **`Paper.year` integer typing.** Schema declares `year` as integer minimum 1900. The pool_ranker reads `year` from DDB as `int(item.get("year", {}).get("N", "0"))` — already integer. Tiebreak with `-p.year` is fine.
- **PMID-as-int cast.** Some PMIDs in fixtures may be non-numeric strings ("PMC1234567" style). Schema pattern is `^[0-9]+$` so only digit strings should reach here; if a malformed PMID slipped through earlier validation, `int(pmid)` would raise. Add a unit test for "non-digit PMID raises informatively at pool_ranker" if not already covered.

## Sequencing

1. **SPS #286 lands its schema-permissive code first** (relax existing `papers` consumer to accept arrays of length 2–7, not just 2–3).
2. **This SPEC's implementation lands second** in ReciterAI.
3. **Cross-cycle: monitor** the first 1–2 spotlight publish cycles after deploy. Confirm `papers` arrays in the published JSON match expectations and SPS rendering is clean.
4. **No PR B / follow-up required** unless the SPS escalation threshold trips (per #286, the threshold for revisiting upstream diversity-aware re-rank).

## Open questions

1. **`lede_grounded_pmids` audit field — include or skip?** Including it adds editorial debuggability at the cost of a schema field. Skipping it keeps the artifact minimal. I lean **include** — the cost is one schema field; the benefit is preserving the editorial-vs-consumer-pool distinction for post-hoc analysis. Operator call.
2. **Tiebreak Option 1 (int cast) vs Option 2 (lexicographic) confirmed?** I lean **Option 1** (int cast) — matches the natural reading of "PMID ascending." But if any test fixture or production PMID is non-digit (shouldn't happen per schema), Option 1 would surface that as a runtime error which is the right failure mode.
3. **Pool-score change acceptable as a side-effect?** Bumping `pool_top_papers_per_subtopic` from 6 → 7 changes pool_score values for subtopics with ≥7 qualifying papers. This shifts rotation history but doesn't break it. Confirm operator is OK with the recomputation.

# Issue #150 — Scoring catchup + the "complete-but-no-TOPIC#" zombie root cause

**Status:** Part 2 (recovery) DONE 2026-05-25 · Part 1 (prevention) + Part 3 (1a/1b) IMPLEMENTED 2026-05-25 (PR pending review + deploy)
**Tracks:** wcmc-its/ReciterAI#150
**Date:** 2026-05-25
**Branch:** `fix/150-scoring-zombie-prevention` (this doc); drift work is separate (PR #151)

## TL;DR

While speccing #150's 1a+1b, a coverage check found only **33 / 2,316** WCM-faculty
2026 pubs have `TOPIC#` rows (vs 46% with impact). Investigation showed the bulk
of the gap is **not** the #150 date-delta gap. Of the 1,027 faculty-2026
"impact-but-no-`TOPIC#`" PMIDs:

- **987** have `PROCESSING# status=complete` (taxonomy_v2, `scored_at` 2026-05-12 /
  05-16), an `IMPACT#` row, **no `TOPIC#`, no `UNCOVERED#`** — verified per-PMID via
  `PmidIndex`.
- **40** have no `PROCESSING#` at all — the only ones matching #150's original 1b
  signal ("IMPACT# with synopsis AND no scored PROCESSING# row").
- **1** is genuinely `UNCOVERED`.

The 987 are **cache-poisoned zombies**, not never-scored. 1a+1b as specced would
not recover them (`--additive` respects the `complete` checkpoint; 1b's predicate
excludes a `complete` PROCESSING# row).

## Root cause (proven in code + live)

`score_publications.py` gates `TOPIC#` persistence on `persist_topic_rows =
args.emit_envelope` (`score_publications.py:1401`), but marks
`PROCESSING#='complete'` **unconditionally** during scoring
(`score_publications.py:769-782`, `:836-848`).

- `--emit-envelope` (hot-path Lambda): writes `TOPIC#` **and** marks `complete`
  together — atomic, correct.
- plain CLI `--pmids` (operator / mid-May backfills): marks `complete` during
  scoring, defers `TOPIC#` to a **separate `load_dynamodb.py` run**
  (`score_publications.py:1450` prints "Run load_dynamodb.py next").

If `load_dynamodb.py` is skipped or fails after a CLI scoring batch → `complete`
checkpoint with no `TOPIC#`. The checkpoint then makes the PMID invisible to every
cache-respecting recovery (`--additive`, the date-delta, #150-1b).

**Live demonstration (2026-05-25):** force-rescored 8 sample zombies via CLI
(`--pmids … --force`). All 8 scored OK (dense scores produced) and were re-marked
`complete` at `scored_at=2026-05-25T13:37` — but **TOPIC# rows in DDB stayed 0**,
because CLI mode does not persist them. This is the bug, end to end.

The 05-20 manual catchup (#150) hit the same trap from the other side: it
`succeeded delta.size=0` and the operator then ran `score_publications --pmids`
(CLI) — which marked `complete` without persisting `TOPIC#`.

## Re-scoped fix — three parts

### Part 1 — Prevention (root cause; highest leverage)

**Invariant: `PROCESSING#=complete` ⟺ `TOPIC#` rows are persisted in DDB.**

- Make targeted `--pmids` runs persist `TOPIC#` inline (treat `--pmids` like
  `--emit-envelope` for `persist_topic_rows`), so any targeted scoring is atomic.
- For the full cold-load JSON path (no `--pmids`, no `--emit-envelope` → score
  whole delta to `scoring_results.json` → `load_dynamodb.py`), move the `complete`
  marking **into `load_dynamodb.py`**, after `TOPIC#` is written. A CLI scoring run
  that hasn't been loaded leaves PMIDs in a non-terminal status (re-scoreable), not
  `complete`. **DEFERRED → tracked as #156** (status-vocabulary ripple risk; the
  `--pmids` footgun above was the actual cause of every observed zombie, so this
  cold-path half is belt-and-suspenders). The `--pmids` inline-persist half shipped
  in PR #152 (item 4).

Result: no future run can mark `complete` without `TOPIC#`. This also restores the
trustworthiness of #150's original 1b predicate ("no scored PROCESSING#") — see
Part 3.

### Part 2 — Recovery of the existing zombies (one-time) — ✅ DONE 2026-05-25

**Executed:** the full zombie set was 2,606 (not just 987): 2026: 986, 2025: 916,
2024: 107, 2023: 124, 2022: 153, 2021: 155, 2020: 165. Recovered all of them via
`score_publications --pmids <2606> --force --allow-cost-override` (2,600 extracted,
6 dropped; 2,600/2,600 scored, 0 failures, 25 content-filters recovered via
gpt-5.1) → `scripts/debug/load_topic_only.py` (24,288 `TOPIC#` rows from 2,221 pubs;
385 legitimately topic-less) → `run_cwid_rollup` for 580 affected CWIDs (0 errors).
**2026-faculty `TOPIC#` coverage: 33 → 1,015.** Total scored PMIDs 7,228 → 9,449.

**Open tail (decision needed):** `FACULTY#.top_topics` for the 580 CWIDs is still
stale — it has no safe per-CWID writer (only `load_dynamodb.py`'s cold loader, which
corrupts on partial input). `STAGE#rollup_by_cwid#` is fresh. Resolve based on what
SPS actually reads.

### Part 2 (original plan) — Recovery of the 987 existing zombies (one-time)

They scored fine; they only need `TOPIC#` materialized, bypassing the poisoned
`complete` cache:

```
score_publications --pmids <987> --force --emit-envelope --allow-cost-override
```

`--force` bypasses the `complete` checkpoint; `--emit-envelope` persists `TOPIC#`
directly (no `FACULTY#`/taxonomy rebuild, unlike a full `load_dynamodb.py`).
Est. ~$3-4, ~10-30 min. Then re-run rollups for the affected CWIDs so faculty
rollups reflect the new `TOPIC#` rows (the #150 "rollup stays stale" caveat).
Verify 2026-faculty `TOPIC#` coverage jumps from 33 → ~1,020 afterward.

### Part 3 — 1a + 1b (the original ask), simplified by Part 1

- **1a — operator override.** Honor an explicit `pmids` list from the SFn execution
  input when `initiated_by ∈ {manual_catchup, operator_rerun}`. Requires:
  (i) ASL: pass the execution input through to the orchestrator (`Orchestrate` Task
  currently passes only context fields — `state_machine.asl.json`); (ii) orchestrator
  override branch that bypasses the date-delta/sweeps; (iii) score handler `--force`
  support so a catchup persists `TOPIC#`.
- **1b — eligibility delta.** With Part 1 in place, `complete` again means
  "`TOPIC#` present", so the original signal is correct: augment the date-delta with
  `IMPACT#`-with-synopsis PMIDs that have **no scored (`complete`) PROCESSING# row**,
  excluding `failed`/`quarantined`. Capped via a new `eligibility_sweep_max_pmids`
  threshold (cost guard), routed through the existing additive path
  (`build_state_machine_input` → `delta.all_pmids` → DeriveDirtyTopics/TopTopic/
  Rollup). Mirrors `resolve_retry_sweep`.

## Sequencing

Part 1 (prevention) + Part 2 (recovery) directly fix the observed 2026 gap and stop
new zombies; do them first. Part 3 (1a+1b) is the durable in-band automation and
builds cleanly on Part 1.

## Notes / state touched during investigation

- 8 sample zombie PMIDs (40920586, 40929019, 40960246, 40960311, 40971874,
  40974008, 41002165, 41014284) were force-rescored via CLI on 2026-05-25; they
  remain zombies (re-marked `complete`, no `TOPIC#`) and will be fixed by Part 2.
- `scoring_results.json` was overwritten by the probe (1,060-pub snapshot → 8 pubs);
  it is a regenerable local artifact.

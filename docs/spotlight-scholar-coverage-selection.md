# Spotlight scholar-coverage selection

**Status:** Implemented · merged (PR #185, `5768c02`, 2026-06-10) · **not yet exercised on a live publish** — takes effect on the next operator `python -m cli.backfill_spotlight --publish`.
**Owner decision:** soft people-coverage downweight at the publish-9 step — `m=3`, `λ=0.08`.

A selection-policy refinement for the weekly spotlight: when choosing which
subtopics reach the home page, **prefer cards that surface different
individuals**. A single prolific lab should not front four of nine cards. This
is a downweight, never a hard rule — a genuine star can still earn a second
card; they just can't monopolize the page.

This document records the problem, the analysis that sized it, the chosen
policy and why, what it deliberately does *not* fix, and the implementation +
verification plan. It complements — does not change — the consumer-facing
`docs/spotlight-contract.md`.

---

## 1. Problem

The spotlight today selects with two signals: parent-topic diversity (one card
per parent) and a near-clone gate (#91 description cosine + #164 author-resolved
article-overlap + the editorial `spotlight_theme_cap_patterns`). It then
publishes the best `PUBLISH_TARGET` (9) of the cleared ≤25 candidates by raw
`sel_score` (`_top_publishable`, #167).

None of those signals look at **who** is featured. Two failure modes follow:

1. **One lab monopolizes the page.** On the live pool, the Lyden lab fronted
   **four of nine** cards (Lyden D ×4, Bojmar L ×4) and Mason CE ×3 — because
   their high-impact papers anchor several distinct, legitimately-different
   subtopics (cancer genomics, single-cell multi-omics, spaceflight omics,
   imaging biomarkers). Parent-diversity and the clone gate all pass: the cards
   share *no papers* pairwise, only *people*. So nothing suppresses the
   repetition.

2. **Method look-alikes** ("single-cell multi-omics" leading three different
   cards). Established earlier (see §6) to be a *separate* problem with a
   *different* fix — they share neither papers nor scholars, so they are not
   redundant and are correctly kept by a people-coverage rule.

This policy targets failure mode (1).

## 2. What the data showed

Read-only prototypes ran the real Stage 1+2 (`pool_ranker.rank_pool` +
`rotation_selector.select_with_diversity` with the full #91/#164 adjacency) on
the live pool, then re-picked the published 9 with a people-coverage penalty.
Scholar identity is the per-card set of **first/last `person_identifier`s of the
top-`m` impact-ranked papers** — the people a card actually fronts. This data is
already computed in `pool_ranker` (the B1 strict author resolver) and rides on
`PoolEntry.papers[*].first_author/last_author`; it is simply not consulted at
selection today (only at lede eligibility).

**Scholar overlap ≈ publication overlap.** Where two subtopics are true clones
they share both papers and people; where they are method look-alikes they share
neither (0.00 / 0.00). So "scholars" is not a new *dedup* signal — but it *is* a
new **page-composition** signal: it catches one person spread across
non-overlapping cards, which no content-overlap signal can.

**Soft penalty sweep** (lead-author depth `m=3`; penalty marginal + escalating,
scaled to the pool's median `sel_score` so it self-normalizes as scores drift):

| λ | distinct faces | worst single person | people on ≥2 cards | page mean sel | cards changed vs prod |
|---|---|---|---|---|---|
| 0.00 (= production) | 38 | **×4** | 6 | 428 | 0 |
| 0.02 | 40 | ×3 | 3 | 427 | 2 |
| **0.05** | 43 | **×2** | 2 | 421 | 3 |
| **0.10** | 43 | ×2 | 2 | 421 | 3 |
| 0.20 | 44 | ×1 | 0 | 411 | 4 |
| ≥0.35 | 44 | ×1 | 0 | 411 | 4 |

There is a clean knee. The ×4 monopoly breaks to **×2 at λ≈0.05–0.10** for a
~2% page-quality cost (mean sel 428→421), keeping the lab's *two strongest*
cards (Cancer Genomics 468 + Single-Cell 449) and dropping its 3rd/4th. Pushing
to λ≥0.20 is the hard zero-repeat end (~4% cost). The curve is monotone — the
policy is purely "where on the curve to sit."

**The bigger lever is `m`, not `λ`.** At `m=1` (penalize only the headline
author the lede names), the live page already maxes at ×2 and a token λ=0.02
clears it at *zero* quality cost. `m=3` is the honest "who's credited on the
card" and is where failure mode (1) actually lives.

## 3. Decision

| Knob | Value | Meaning |
|---|---|---|
| Author depth `m` | **3** | "featured individual" = first/last author of the top-3 impact-ranked papers of a subtopic. |
| Penalty `λ` | **0.08** | Dock a candidate by `λ · median_sel · Σ board_count[a]` over its featured authors, where `board_count[a]` = times `a` already appears on the page. Marginal + escalating: a person's 2nd card is cheap, their 4th expensive. **Soft — never forbidden.** |
| Stage | **publish-9** (`_top_publishable`) | Greedy over the cleared ≤25 candidates; replaces the raw-`sel_score` truncation. |

`λ=0.08` sits on the ×2 plateau (between the validated 0.05 and 0.10 points):
the monopoly is broken, a deserving star keeps a deliberate second card, ~2%
quality cost, ~3 of 9 cards swap.

**Recommended-page snapshot** (`m=3, λ≈0.10`; what `λ=0.08` approximates):
distinct faces 38→43, worst person ×4→×2, 6→2 people doubled, mean sel
428→421. Lyden D ×2 and Bojmar L ×2 are *kept by design* (their two best
areas); the excess cards drop. Promoted in: Causal Inference & Comparative
Effectiveness, Telehealth & Digital Care, Alzheimer's & Neurodegeneration —
all well-populated subtopics, not obscure two-author outliers, so the
"diversity surfaces junk" guardrail did not bite.

## 4. Why these parameters

- **Soft, marginal, escalating** — per the owner: zero-repeat is a *preference*,
  not a constraint. The penalty downweights repeats so they are progressively
  unlikely, but a genuinely top-tier second card for a star can still out-score
  the penalty. The earlier *flat, all-14-authors* penalty saturated (any λ≥1
  snapped to zero repeats); keying on lead authors (`m`) and making the penalty
  multiplicity-weighted is what produces the soft knee.
- **`λ` scaled to the pool's median `sel_score`** — keeps the knob meaningful as
  impact scores drift run-to-run (median moved 390→328 between two same-day
  runs; a fixed-point-value λ would not have transferred, a median-scaled one
  does).
- **At the publish-9 step, not the 25-wide selection** — the page *is* the
  published 9. If the penalty only shaped the 25-wide candidate pool, #167's
  `_top_publishable` would re-truncate to top-9 by raw `sel_score` and discard
  the coverage. The publish-9 step is the single decision that reaches the page.

## 5. What this does NOT solve (out of scope)

- **Method-vocabulary look-alikes** (three cards reading "single-cell
  multi-omics," 0.00 paper/scholar overlap pairwise). These are *correctly kept*
  by a people-coverage rule — three different groups doing single-cell work is
  good coverage, not redundancy. If the repeated *phrase* still grates, the fix
  is editorial: de-emphasize the shared method in the lede framing (a
  `spotlight_synopsis` prompt tweak), not selection.
- **Hierarchy over-fragmentation / gradual lifecycle.** The spaceflight cluster
  (same ~9 papers fragmented into 5 subtopics across 5 parents; scholar overlap
  1.0) and the lack of incremental subtopic mint/retire between annual
  recomputes are a *separate, upstream* problem. The durable-opaque-subtopic-ID
  change (port the tools/families D-06 match-or-mint pattern to the hierarchy)
  is the root fix for both the rotation-history resets and the fragmentation,
  and is tracked separately. Selection-time coverage only hides fragmentation;
  it does not collapse it.

## 6. Provenance of the analysis

The "scholars ≈ publications, both bimodal" finding and the method-look-alike vs
true-clone distinction came from a member-overlap + scholar-overlap pass over
the redundancy actually reaching the page (true clones: spaceflight facet
cluster, member overlap 1.00; method look-alikes: the #2/#4/#8 single-cell
triplet, 0.00). That pass is what established scholars cannot be a *dedup*
signal but can be a *page-composition* signal — the premise of this policy.

## 7. Implementation plan

**Shipped in PR #185 (`5768c02`).** Contained: one function plus two config keys. No new author plumbing — the
data is already on `PoolEntry.papers`.

1. **`config/thresholds.json`** — add:
   - `spotlight_scholar_penalty_lambda` = `0.08`
   - `spotlight_scholar_lead_depth` = `3`
   Mirror in `config/thresholds.schema.json` (bounds: λ ∈ [0, 2], depth ∈
   [1, 7]) and document in `config/thresholds.md`.

2. **`cli/backfill_spotlight.py::_top_publishable`** — replace the
   "sort cleared candidates by `sel_score`, take top `target`" body with a
   greedy people-coverage pick. The cleared ≤25 candidates are already
   parent-distinct and clone-free (gates ran in `select_with_diversity`), so the
   publish step inherits both — no gate re-application needed. Reference
   selector (validated form):

   ```python
   def _top_publishable(publishable, target, *, lam, lead_depth):
       # publishable: list[(Selection, ValidatedLede)] already gated + critic-cleared
       median_sel = statistics.median([s.sel_score for s, _ in publishable]) or 1.0
       def lead_authors(sel):
           out = set()
           for p in sel.entry.papers[:lead_depth]:
               for a in (p.first_author, p.last_author):
                   pid = (a.person_identifier or "").strip()
                   if pid:
                       out.add(pid)
           return out
       feat = {id(pair): lead_authors(pair[0]) for pair in publishable}
       chosen, board = [], {}
       remaining = sorted(publishable, key=lambda pr: (-pr[0].sel_score,
                                                       pr[0].entry.subtopic_id))
       while len(chosen) < target and remaining:
           def eff(pr):
               load = sum(board.get(a, 0) for a in feat[id(pr)])
               return (pr[0].sel_score - lam * median_sel * load, pr[0].sel_score)
           best = max(remaining, key=eff)
           chosen.append(best)
           for a in feat[id(best)]:
               board[a] = board.get(a, 0) + 1
           remaining = [pr for pr in remaining if pr is not best]
       return chosen
   ```

   Caller (line ~728) passes the two new thresholds. λ=0 reproduces today's
   exact behavior (pure `sel_score` order), so the change is safe-by-default and
   the gate is a no-op when disabled.

   **Calibration note:** the sweep scaled λ by the median of the full 150-pool;
   `_top_publishable` sees only the cleared ≤25. Re-confirm `λ=0.08` against the
   ≤25 median on a `--dry-run-full` before first publish (the knee may shift
   slightly; the *shape* holds).

3. **Cost-win (deferred, optional).** Applying the same bias inside
   `select_with_diversity` would stop monopoly cards from ever getting a lede
   generated (Bedrock spend), not just from publishing. Deferred — the publish-9
   change is sufficient for the page; the selection-stage change is a spend
   optimization to scope separately.

## 8. Verification plan

Per repo policy, "designed/validated" ≠ "verified." Before claiming done:

1. `python backfill_spotlight.py --dry-run-full` — compare the published set and
   its **distinct-individual count** and **worst single-person card-count**
   against a λ=0 baseline run on the same pool. Expect worst-person ×4→×2,
   distinct +≈5, ~3 cards changed, mean sel within ~2%.
2. Unit test: λ=0 ⇒ output identical to the current `sel_score`-sorted
   truncation (regression guard); a constructed monopoly pool ⇒ the ×N card is
   demoted past `target`.
3. Confirm the published artifact still validates against
   `docs/spotlight.schema.json` (selection changes which 9 publish, not the
   shape).

## 9. References

- `docs/spotlight-contract.md` — consumer contract (artifact shape, author
  payload, voice).
- `spotlight/rotation_selector.py` — `select_with_diversity`, `selection_score`,
  `SELECTION_TARGET/FLOOR`, `PUBLISH_TARGET`.
- `spotlight/pool_ranker.py` — `rank_pool`, B1 strict author resolution
  (`PoolEntry.papers[*].first_author/last_author`).
- `cli/backfill_spotlight.py` — `_run_pipeline`, `_top_publishable` (integration
  point, line ~728).
- `config/thresholds.json` / `.schema.json` / `.md` — tunable home.
- Related: #91 (cosine near-clone gate), #164 (article-overlap gate +
  `spotlight_theme_cap_patterns`), #167 (publish-best-9). This policy is the
  next layer: page composition by *people*, complementary to those *dedup*
  layers.

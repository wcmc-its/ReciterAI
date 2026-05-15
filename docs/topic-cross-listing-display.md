# Cross-listing legibility on topic pages

*Status: Adopted 2026-05-15. Tracking issue: [#68](https://github.com/wcmc-its/ReciterAI/issues/68). History: captured and revised same day; scope redesign replaced "primary topic" with "top topic" and added a citation-modal pattern. Drafted in `Projects/ReciterAI - Planning/`; moved here on adoption.*

## Premise

ReciterAI assigns topics to publications multi-label by design (`docs/topic-subtopic-assignment.md` §3): every topic above the 0.3 threshold is persisted. The SPS Scholars Topic page (`/topics/<topic_id>`) is an **inclusive** surface listing every paper above threshold for that topic, regardless of strongest fit.

Today this is invisible at the citation level. A paper appearing on a topic page looks identical whether that topic was its #1 fit or its #5.

**This spec proposes a two-tier disclosure**:

- **Inline (citation level)**: show the paper's top-scoring topic.
- **Modal (one click away)**: show full title, abstract, the complete list of above-threshold topic assignments with scores, and any other detail SPS already surfaces.

## What this proposes

### Inline citation

On each paper entry on `/topics/<topic_id>`, render a small affordance with the top-scoring topic when it differs from the current page. Suggested copy:

> *Top topic: Mental Health & Psychiatry*

If the current page *is* the top topic, render no affordance (or render "Top topic in this paper" — minor copy choice).

### Modal

Click on the citation (or a dedicated icon) opens a modal showing:

- Full title.
- Full abstract.
- **All above-threshold topic assignments** with scores, sorted descending. The current topic page's topic is marked.
- Any other per-paper detail SPS already surfaces (authors, journal, year, links).

The modal is where the full multi-label spread becomes legible. The inline affordance is the one-bit signal; the modal is the explanation.

## Why "top topic," not "primary topic"

`docs/topic-subtopic-assignment.md:80` is explicit: *"There is no single 'primary topic'."* That statement is correct and this spec does not retract it. Instead it introduces a **derived observational field**:

`top_topic_id` for a paper := the topic with the highest score in the paper's topic-score vector among topics that cleared the 0.3 screening floor.

This is **not** a designation, **not** a rollup-input, and **not** an authoritative claim about what the paper is "about." It is reportage of an argmax. The existing pipeline already uses the same language: `score_publications.py:_evaluate_uncovered` computes `top_topics` (top 3 with scores) for the `UNCOVERED_PMID#` event. The naming is consistent with that.

This naming choice has three consequences:

1. **No doc retraction needed.** The `docs/topic-subtopic-assignment.md` update is additive: "Each paper carries a derived `top_topic_id` field equal to argmax of its topic-score vector. This is a read-time convenience, not a designation; the system remains multi-label and does not designate a primary topic." Adopt as a small edit, not a reversal.
2. **No §8 rollup question.** §8's exclusive aggregation operates on `primary_subtopic_id`. `top_topic_id` is not a primary in any rollup sense, so §8 is unaffected by this spec.
3. **No three-notions-of-primary confusion.** The taxonomy has two notions of primary (`primary_subtopic_id` for assignment; §8 exclusive aggregation for rollups), both unchanged, and one new observational field (`top_topic_id`) for read-time display. They are categorically different concepts.

### Disambiguation table

| Field | Scope | Kind | Defined today? |
|---|---|---|---|
| `primary_subtopic_id` | per paper, per topic | Designation (assignment-time, with deterministic tiebreak) | Yes (`assign_subtopics.py:280`) |
| §8 exclusive rollup aggregation | per CWID, per (sub)topic | Aggregation rule (operates on `primary_subtopic_id`) | Yes |
| `top_topic_id` (new) | per paper | Observational (argmax of score vector, with deterministic tiebreak) | No — this spec introduces it |

## Tiebreak — resolved

When the top-2 topic scores tie within `tie_epsilon` (from `config/thresholds.json`), mirror the pattern at `assign_subtopics.py:280` (`_resolve_primary_on_tie`), with the topic-level analog of "signal density":

1. Within `tie_epsilon` on the top-2 topic scores → tied.
2. Higher `sum(subtopic_confidences[topic])` wins. (The subtopic-level tiebreak uses `seed_pmid_count` because `total_weight` isn't yet computed at Pass 2 time; by the time `top_topic_id` would be computed, subtopic assignment has run and `subtopic_confidences` is populated per (paper, topic) pair, so the cleanest topic-level "signal density" is the sum of within-topic subtopic confidences.)
3. Alphabetically lowest `topic_id` wins.

Runs in Python, not in any LLM prompt. Deterministic. Reproducible across runs given identical scores.

Alternative considered: *count* of above-floor subtopic matches in the topic, instead of *sum* of confidences. Sum has finer resolution; count is more robust to confidence-floor changes. Lean: sum. Pick at file time.

## Where to compute

### Option A — read-time at SPS

SPS reads the topic-score vector per paper from DynamoDB and computes `top_topic_id` at render time. No producer change in ReciterAI; SPS owns the tiebreak.

**Pro**: smaller contract surface; ReciterAI gains no new published field.
**Con**: every consumer reimplements the tiebreak; risk of silent drift if a second consumer arrives and chooses differently; tiebreak logic in SPS code that an SPS-side change can break invisibly.

### Option B — materialize `top_topic_id` in ReciterAI

Add the field to each per-paper activity record. ReciterAI owns the tiebreak. Consumers read a field.

**Pro**: single source of truth; tiebreak rule lives once; no drift risk.
**Con**: contract surface grows by one field; backfill required over existing corpus.

### Decision (provisional)

**Lean Option B** if more than one SPS consumer is in v1 scope. Foreseeable consumers:

1. SPS Scholars Topic page citation list (this spec, v1).
2. Same citation list in the modal (this spec, v1 — same field, same logic, but rendered differently).
3. SPS faculty-profile topic sections (same affordance scoped to one faculty, plausible v1 or v1.1).

Even within v1 of this spec, the inline-vs-modal rendering hits the field twice. Option B avoids reimplementing the tiebreak in two render paths.

If v1 ships only the inline-on-topic-page surface and explicitly defers faculty-profile sections, Option A becomes defensible (one render path, one consumer). Decide at file time.

## UX risks (testable, partially mitigated by the modal)

**The empirical premise — "researchers benefit from inline top-topic disclosure" — is unverified.** The concern: a reader treats `/topics/pediatrics_neonatology` as "WCM work in Peds," reads *"Top topic: Mental Health & Psychiatry"* on a Peds-page entry, and parses it as *"why is this on this page if MH is the top topic?"* — i.e. the affordance creates the confusion it's meant to fix.

The modal pattern mutes this risk significantly. A confused reader clicks through, sees abstract + the full list of above-threshold topics (MH 0.95, Neuro 0.90, Radiology 0.55, Peds 0.41), and self-corrects: "ah, this is a depression / fMRI paper that also touches pediatrics, makes sense it's here." The inline signal is now a teaser; the modal is the explanation.

Residual risks worth cheaply de-risking before commit:

1. SPS analytics on modal-open rate from inline-top-topic clicks (if instrumentable). High click-through suggests readers treat the affordance as useful; near-zero suggests noise.
2. 15-minute walkthrough with one or two researchers: show mock of inline + modal, ask whether the pattern clarifies or confuses. (Faster than a full user check; the modal absorbs most of the design surface.)

Copy choice still matters more than the engineering. *"Top topic: X"* is neutral. *"Primarily about X"* would be a designation we don't want to make. *"Strongest fit: X"* is an acceptable alternative if "top topic" tests poorly.

## Cost (loose; firm at file time)

- **ReciterAI side, Option B**: ~half-day to add `top_topic_id` to per-paper activity records, recompute over existing corpus, update the published shape. Plus ~quarter-day for the additive `docs/topic-subtopic-assignment.md` edit. Round to **less than a day** of ReciterAI work — the additive nature of the doc edit (vs the previous retract-and-restate burden) makes this cheaper than the prior draft suggested.
- **SPS side**: more than the prior draft. Inline citation affordance + modal component. Confirm at file time:
  - Does the Scholars Topic page already use a modal pattern (e.g. for paper details on click)? If yes, this is adding fields to an existing modal — small. If no, building a new modal is the bulk of the work.
  - Which repo owns the topic-page component (`ReCiter-Publication-Manager`? a Scholars-Profile-System frontend?).
- **Caveat on the ReciterAI estimate**: the topic-score vector needs to be cheaply queryable per paper to compute `top_topic_id`. The current topic-first DDB key layout (`PK: TOPIC#<topic_id>`, `SK: SCORE#<score>#ACTIVITY#<pmid>`) is optimized for "all activities above threshold for topic X" queries, not "all topics for activity Y." If reverse assembly is expensive, materialization adds a backfill cost (still probably <1 day with existing scan tooling — verify before committing).

## Not in scope

- Hiding multi-label assignments. The point is to make them legible at two levels (inline teaser + modal detail).
- Per-paper topic override (live score mutation). Different commitment entirely.
- Surfacing `top_topic_id` beyond SPS read-time use (e.g. CSVs, DDB partitions consumed by other systems). Add later if needed.
- Changes to the §8 exclusive rollup. Unaffected by this spec because `top_topic_id` is not a primary in any rollup sense.
- Anything subtopic-level (a paper that appears under the wrong subtopic within a correct topic). Lower-stakes; same design shape if/when adopted; defer.
- Retracting `docs/topic-subtopic-assignment.md:80`'s "no single primary topic" assertion. That statement remains accurate; this spec adds an observational field alongside it.

## Trigger conditions for picking this back up

Adopt when any of:

1. A user (researcher or SPS operator) reports confusion about why a paper appears on a topic page that isn't its strongest fit. (Direct UX motivation.)
2. SPS analytics or a quick walkthrough de-risks the UX-premise concern.
3. A one-off ad-hoc query against `top_topic_id` (computed at query time against existing DDB data — does not require this spec to adopt) shows that some inclusive surface has a high rate of *"appears here but never top"* papers and the operator wants the read surface to reflect that.
4. The Scholars Topic page UX is being touched for another reason and this affordance can ride along cheaply.

## Open questions

- Tiebreak signal: *sum* of `subtopic_confidences` is the lean. Alternative: *count* of above-floor subtopic matches. Pick at file time.
- Modal trigger: clicking anywhere on the citation, or a dedicated icon? Probably whichever matches SPS's existing patterns; confirm.
- Faculty-profile topic sections: v1 or v1.1? Affects Option A vs B decision.
- Copy: *"Top topic: X"* vs *"Strongest fit: X"* vs other framings. Lean "Top topic"; verify with the de-risking walkthrough.
- Modal contents beyond the three named items (title, abstract, all topics): inherit from whatever SPS already shows for paper detail, or scope explicitly? Probably inherit; confirm.

## Cross-references

- `docs/topic-page-inclusion-threshold.md` (sister spec, adopted 2026-05-15; complementary — that one controls which papers appear by default on a topic page, this one labels them; ship order doesn't matter)
- `docs/sps-topic-page-disclosure-handoff.md` (consumer-side handoff for SPS)
- `Projects/ReciterAI - Planning/topic-precision-audit-DRAFT.md` (sister spec, parked 2026-05-15 after spot check; this spec is independent of it)
- `docs/topic-subtopic-assignment.md` §3, §80 (multi-label is intentional; "no single primary topic" — this spec preserves both)
- `assign_subtopics.py:280` (`_resolve_primary_on_tie` — pattern mirrored for the topic-level tiebreak)
- `score_publications.py:_evaluate_uncovered` (existing `top_topics` computation for `UNCOVERED_PMID#` events — same naming, same idea, different consumer)
- `docs/RECITERAI-SPEC.md` §8 (rollup spec; unaffected by this spec)

# Two-tier topic-page inclusion threshold

*Status: Adopted 2026-05-15. Tracking issue: [#69](https://github.com/wcmc-its/ReciterAI/issues/69). History: drafted in `Projects/ReciterAI - Planning/` and moved here on adoption. Sister to `docs/topic-cross-listing-display.md`; complementary, ships independently. Supersedes the operator-audit motivation of `Projects/ReciterAI - Planning/topic-precision-audit-DRAFT.md` (parked) by giving the user control of the precision-recall tradeoff at read time.*

## Premise

`config/thresholds.json` today has one `score_floor` (0.3) doing two jobs:

1. **Qualification**: "this paper scored high enough on this topic to be persisted at all." Screening-level recall.
2. **Display**: "this paper shows on `/topics/<topic_id>`'s Research Articles list." Topic-page precision.

These are different decisions. The original screening→dense architecture (Haiku broad screen → Sonnet refined scores) suggests the floor was always meant to be the *screening* threshold, with a separate, higher *display* threshold tuned to topic-page precision. The implementation flattened them. The spot-check result in `topic-precision-audit-DRAFT.md` (1 clear FP / 10 in Peds long tail) is a direct symptom: papers that barely cleared the floor end up on the topic page indistinguishable from papers that scored 0.95.

## What this proposes

Introduce a `display_threshold` (per-topic or global; see below) above the `score_floor`. SPS read logic:

- **Default view** on `/topics/<topic_id>`: show only papers with `score[topic] ≥ display_threshold[topic]`.
- **Wider-net affordance**: "View additional articles that are relevant" button or section. Expands the list to include papers with `score_floor ≤ score[topic] < display_threshold[topic]`. Visually grouped or labeled so the relevance gap is honest.

The floor stays 0.3 (no change to qualification semantics; the same papers are persisted; the same `subtopic_ids[]` and rollups). Only the topic-page display changes.

## Why this is the right shape

1. **It uses data we already have.** No new producer, no new scoring pass. Each paper's topic-score vector is already published per-topic-per-paper.
2. **It lets the user own the precision-recall tradeoff.** Default = precision (strict view). Click = recall (wider net). No operator triage queue needed — the user self-selects.
3. **It directly addresses the spot-check finding.** PMID 35353857 (the lone clear FP from the Peds spot check — adult-focused COVID epi tagged Peds because it mentions children as transmission vector) would disappear from the default Peds view at any reasonable display threshold and reappear only behind the wider-net click. Same shape works for the noise-floor FPs across all long-tail topics.
4. **It composes cleanly with the cross-listing display spec.** That spec governs how papers shown are *labeled*; this one governs which papers are *shown by default*. Both modify the same topic-page surface; they don't conflict.

## Per-topic vs global threshold

### Option G — single global `display_threshold`

One value in `config/thresholds.json`, e.g. `display_threshold: 0.5`. Applied uniformly.

**Pro**: trivial to ship; one number to tune.
**Con**: ignores that topics differ in calibration. The head-of-distribution topics (biostatistics_quantitative_sciences, translational_clinical_science, epidemiology_population_health — see `topic-precision-audit-DRAFT.md`'s long-tail-vs-head analysis) clearly need stricter thresholds than long-tail topics, because their definitions bleed more.

### Option T — per-topic `display_threshold` published with the topic taxonomy

Each topic carries its own `display_threshold` in `taxonomy_v2.json` (or a separate per-topic threshold file under `config/`).

**Pro**: calibrates to actual precision-recall behavior per topic. The head-of-distribution topics can carry e.g. 0.6+ while well-bounded topics carry 0.5 or lower.
**Con**: more state to maintain; threshold drift over taxonomy versions; tuning workstream up front.

### Decision (provisional)

**Lean Option T**, with a v1 simplification: ship Option G's single global default (probably 0.5) for every topic that hasn't been individually tuned, plus Option T's per-topic override mechanism in place. Tune topics individually as a separate, paced workstream — head of distribution first.

This lets the spec ship without waiting on the full tuning workstream, while making the per-topic refinement a clean later commit.

## Tuning the threshold(s) — LLM-mediated per-topic review

The actual work is picking the threshold values honestly. Cheapest tractable shape:

For each topic to be tuned:

1. Sample N papers (start with 50) above `score_floor` for that topic. Bucket by score: `[0.30, 0.35)`, `[0.35, 0.40)`, `[0.40, 0.45)`, `[0.45, 0.50)`, `[0.50, 0.55)`, `[0.55, 0.60)`, `[0.60+]`.
2. For each bucket, sample ~7–10 papers; LLM (Claude) reads each abstract and classifies: clear TP, defensible marginal, clear FP. Same rubric as the spot check in `topic-precision-audit-DRAFT.md`.
3. Pick `display_threshold[topic]` as the lowest bucket boundary where FP rate is ≤ some operator-chosen target (e.g. ≤ 10%) AND defensible-marginal rate is reasonable.
4. Record rationale per topic alongside the chosen value (which score buckets produced what classification distribution). This becomes the audit trail for "why is the Peds threshold 0.45 and the Biostats threshold 0.65."

**Cost estimate**: ~50 abstracts/topic × 67 topics = 3,350 abstracts to read. At maybe 20–30 seconds per abstract via the LLM, with batching, this is a several-hour-to-one-day workstream when concentrated. Head-of-distribution topics (top ~6 by uncovered count) are the highest-leverage first cohort: tune those, ship the spec, tune the rest incrementally.

**Determinism note**: the LLM rater needs a stable rubric to avoid drift between runs. Pin the model version used for rating and document the rubric alongside the per-topic results. Re-tuning on a taxonomy bump uses the same rubric.

## Wider-net affordance — UX shape

Visual options for the wider-net view:

- **Option A — flat append**: clicking "View additional articles that are relevant" appends the lower-confidence papers below the default list, optionally with a separator label ("Also relevant — lower-confidence matches").
- **Option B — grouped sections**: always render two sections — "Strongly relevant (default)" and "Also relevant (click to expand)" — with the latter collapsed by default. More honest about the tiering; more UI weight.
- **Option C — toggle that mutates the list**: a single "Include lower-confidence matches" toggle. Cleanest but loses the visual distinction between tiers.

**Lean Option B**: it's the most legible (the reader knows the system has two tiers) and the most truthful. Confirm at file time based on SPS's existing patterns.

**Copy**: *"View additional articles that are relevant"* is the user's phrasing; tested-against alternatives: *"Show more"* (too vague), *"View weaker matches"* (negative framing), *"Show borderline papers"* (admits the limitation). Lean the user's phrasing; verify in the same de-risking walkthrough proposed in the cross-listing spec.

## Relation to other drafts

### Cross-listing display (`topic-cross-listing-display-DRAFT.md`)

**Complementary.** That spec labels papers in the topic-page list with `top_topic_id`; this spec controls which papers are in that list by default. Both modify the topic-page surface. Shipping order doesn't matter — neither blocks the other.

A reader on `/topics/pediatrics_neonatology` with both shipped:

- Sees the strongly-relevant section (Peds score ≥ display_threshold). Each entry is labeled with its top topic in parens if not Peds. Click any entry → modal with full topic spread + abstract.
- Optionally expands the "Also relevant" section to see lower-confidence papers, similarly labeled.

The two specs together make the topic page honest at three levels: which papers belong, how strongly each belongs, and which topic each paper is most centrally about.

### Precision audit (`topic-precision-audit-DRAFT.md`, parked)

**Supersedes the audit's motivation.** That spec proposed an operator-facing audit queue for "papers above threshold but not the top topic." The wider-net affordance makes that operator triage largely unnecessary — the user owns the inclusion choice at read time, and bad-fit papers don't need to be hunted down because they're not in the default view.

The audit *would* still be useful for one purpose: feeding back into the threshold-tuning workstream above. Per-topic FP-rate measurement is the audit's actual value. But that's an internal tuning aid, not an operator-facing surface.

## Cost

- **ReciterAI side**: add `display_threshold` key (global v1, per-topic override in v1.1) to `config/thresholds.json` or `taxonomy_v2.json`. Publish in the hierarchy schema if it's a per-topic value (one new field per topic in the published `hierarchy_full.json`). ~half-day for the producer + schema update.
- **SPS side**: topic-page query reads the threshold(s) and filters; wider-net affordance UI (Option B preferred — two sections, one collapsed). Confirm at file time which repo owns the topic-page component.
- **Tuning workstream**: ~half-day to a day for the head-of-distribution cohort (6 topics). Incremental for the rest. Can be paced.

## UX risks

**Strict view hides what it doesn't show.** This is qualitatively different from cross-listing display, which only changes labeling. A reader on `/topics/pediatrics_neonatology` with strict view enabled may not know that anything was filtered out unless the wider-net affordance is visible enough. Mitigation:

- The "View additional articles that are relevant" button must be obvious, not buried. Option B's grouped-section pattern (always visible, even if collapsed) is more honest than Option A's append-on-click.
- A small text marker on the topic page header could state the policy: *"Showing strongly relevant articles. Click below to view all relevant matches."* Optional but worth considering.

**Bad threshold values cause asymmetric harm.** Too-low strict threshold = FPs in default view (no improvement over today). Too-high strict threshold = legitimate papers disappear into the wider-net section, where users may not look. The tuning workstream is the only mitigation; ship Option G's conservative global default (~0.5) only after a small validation pass.

**Wider-net section reintroduces the same noise the strict view filtered.** Reader who clicks "Also relevant" sees PMID 35353857 again. The cross-listing display spec's `top_topic_id` label is the partial mitigation here — the FP is visibly tagged with its real top topic, so the reader has context to dismiss it.

## Not in scope

- Changes to `score_floor` itself. The floor stays 0.3; this spec only adds a *display* threshold above it.
- Per-PMID topic override. Different commitment; not motivated by spot-check data.
- Changes to rollup arithmetic (§8). The floor is unchanged; aggregations operate on the same data.
- Removing papers from DDB. Filtering is purely at SPS read time.
- A "wider net" affordance on faculty profile pages or other surfaces. v1 is topic page only; expand later if useful.

## Trigger conditions for picking this up

Adopt when any of:

1. The cross-listing display spec is adopted and the topic page is being touched — this work is cheap to ride along with that work.
2. An SPS user complains about a paper appearing under the wrong topic page (specifically about the kind of low-score FP that strict view would hide).
3. A focused tuning experiment on one or two head-of-distribution topics (biostats, translational, epi) shows that a strict threshold meaningfully reduces FP rate without losing too many TPs — evidence the workstream is worth the labor.
4. The Phase 11 taxonomy review work picks up and wants a per-topic precision lever beyond prompt-tuning.

## Open questions

- Default global `display_threshold` value for v1. Lean 0.5 but verify with a small pre-tuning sample across 3–5 topics of varying type. Don't ship without that check.
- Per-topic threshold storage: `taxonomy_v2.json` (lives with the topic definitions, versioned together) vs `config/thresholds.json` (lives with other tunables, easier to edit). Lean taxonomy file — thresholds are semantically part of the topic definition.
- Whether the wider-net section should also apply `top_topic_id` labeling (the cross-listing spec's affordance). Almost certainly yes; flag at file time so both specs ship compatible UI.
- Whether to expose the actual score in the UI (e.g. tooltip on each entry). Could help readers calibrate but adds noise. Probably no in v1.
- LLM tuning rubric — should "marginal" be split into "leans TP" and "leans FP"? Three-way classification was sufficient in the Peds spot check; revisit if the rater finds it ambiguous.
- Re-tuning cadence. Tune once on adoption; re-tune on taxonomy version bumps (cold runs); re-tune ad-hoc on operator request. No fixed cadence beyond that.

## Cross-references

- `docs/topic-cross-listing-display.md` (sister spec, adopted 2026-05-15; complementary; both modify topic-page UX)
- `docs/sps-topic-page-disclosure-handoff.md` (consumer-side handoff for SPS)
- `Projects/ReciterAI - Planning/topic-precision-audit-DRAFT.md` (parked; this spec supersedes its motivation, repurposes its sampling rubric for threshold tuning)
- `config/thresholds.json` (where the floor lives today; where the display threshold(s) would also live or be referenced)
- `config/thresholds.md` (current threshold documentation; needs to add `display_threshold` and explain the floor-vs-display distinction)
- `docs/topic-subtopic-assignment.md` §3 (multi-label is intentional; this spec keeps that but tiers the display)
- `score_publications.py` (where Sonnet dense scores are produced; no change needed — only read-time filtering shifts)

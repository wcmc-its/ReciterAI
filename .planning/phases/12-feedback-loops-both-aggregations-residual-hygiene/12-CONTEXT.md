---
phase: 12
phase_name: Feedback Loops, Both Aggregations, Residual Hygiene
created: 2026-05-12
milestone: M2 — SPS-feeding service
---

# Phase 12: Feedback Loops, Both Aggregations, Residual Hygiene — Context

**Gathered:** 2026-05-12
**Status:** Ready for planning

<domain>
## Phase Boundary

Three architecturally independent pillars closing spec §8, §9, and §11:

1. **Both aggregations side-by-side (§8).** Publish `faculty_subtopic_counts_exclusive` (current primary-subtopic rollup) **and** `faculty_subtopic_counts_inclusive` (multi-label rollup) in DynamoDB and CSV. Consumers pick a column. Reconciliation gate registered to enforce arithmetic invariants between exclusive sum and topic totals.
2. **Feedback-event consumption (§9).** Phase 10 already *emits* `UNCOVERED_PMID#` / `LOW_CONFIDENCE_ASSIGNMENT#` and the daily drift evaluator already raises severity-tagged alerts on threshold breach. Phase 12 adds: (a) the `CRITIC_REJECT#` producer in `spotlight/critic.py`; (b) a cold-path/CLI-hybrid **feedback sweep consumer** that produces three typed finding records — `CANDIDATE_TOPIC#`, `RECLUSTER_RECOMMENDATION#`, `SPOTLIGHT_DIAGNOSTIC#` — turning each spec §9 quality signal into a queryable backlog row rather than an operator-vigilance task.
3. **§11 residual maintenance.** G-1, G-18, G-24, G-34, G-36 ship inside the main phase work; G-37 (one bounded E2E integration test) ships as a separately-gated final task within the phase.

**Not in scope:** taxonomy regeneration on the basis of candidate-topic findings (those rows feed the *next* cold run's input pile, not Phase 12's runtime); any auto-action that mutates the live hierarchy without operator approval; per-topic confidence-floor override capability (doesn't exist today and isn't planned here); a GitHub-issue dispatcher that opens issues from finding records (separate follow-up phase that reuses the Phase 10 alert-dispatcher pattern).

</domain>

<decisions>
## Implementation Decisions

### Feedback consumer — trigger and output shape

- **D-01: Hybrid trigger.** Two entry points, one code path: operator CLI `python -m feedback sweep --since DAYS` and a non-gating stage inside `pipeline_cold.run.main()`. Both write findings with the same shape; the row carries `triggered_by ∈ {operator, cold_run}` and a `source_sweep_run_id` (UUID generated at sweep start). The two trigger conditions are genuinely different events — operator-CLI is *diagnosis* (drift alert → operator investigates before deciding whether a cold run is warranted); cold-path-stage is *documentation* (a cold run is happening, fold fresh findings into the same `run_id` context as the regeneration). Neither blocks the other.
- **D-02: Cold-path invocation is non-gating.** A sweep finding "12 candidate new topics" during a cold run does **not** block that cold run from completing its already-approved taxonomy regeneration. The findings flow into the *next* cold run's taxonomy regen input pile. Otherwise we create a chicken-and-egg where the cold run can't complete because the sweep it ran wants to change the taxonomy it's running against.
- **D-03: DDB-primary output, deterministic markdown render.** Source of truth lives in typed DDB records (see D-04). A `python -m feedback render <run_id>` subcommand emits a deterministic markdown view from the rows — same row in, same bytes out, no `generated_at` inside the markdown body. Timestamp lives in the filename, not the content. (G-29 lesson from Phase 11.)
- **D-04: Three typed finding records, no unified discriminator.** Phase 12 emits three record types, one per spec §9 row:
  - `CANDIDATE_TOPIC#{slug}` — from the UNCOVERED_PMID# pool; carries proposed_label, source_pmids[], Sonnet rationale, `source_sweep_run_id`.
  - `RECLUSTER_RECOMMENDATION#{topic_id}` — from persistent LOW_CONFIDENCE_ASSIGNMENT# counts under a single topic; carries topic_id, evaluation_history (which drift days tripped), `source_sweep_run_id`.
  - `SPOTLIGHT_DIAGNOSTIC#{cwid}` — from CRITIC_REJECT# counts per CWID over a window; carries cwid, reason_code distribution, `source_sweep_run_id`.

  A unified `FEEDBACK_ACTION#` with an `action_type` discriminator was rejected because downstream renderers branch on `action_type` anyway, and the three action types have genuinely different evidence shapes (PMID set + Sonnet rationale vs topic ID + evaluation history vs CWID + reason-code distribution). Typed records are honest about that difference.
- **D-05: Idempotent overwrite per `source_sweep_run_id`, no cross-sweep merging.** A sweep retry with the same `run_id` overwrites; a sweep with a new `run_id` produces a separate record. Longitudinal candidate-topic tracking (merging findings across run_ids) is a different feature and should be built explicitly if needed — not as an accidental side effect of overwrite semantics.

### Feedback consumer — Sonnet uncovered-PMID sweep

- **D-06: Since-last-sweep input with cap.** Sweep reads every `UNCOVERED_PMID#` row written since the prior sweep's `started_at`, capped at `feedback_sweep_max_pmids` (lives in `config/thresholds.json` per G-18). When the cap is hit, the sweep's output record carries `truncated: true` + `total_unprocessed_remaining: N` so "cap too low or sweep cadence wrong" surfaces as a tunable, not a hidden failure. Worst-fitting PMIDs go first (sort by `top_topic_score` ascending) so the cap doesn't preferentially drop the most-uncovered cases.

### Feedback consumer — recluster recommendation trigger

- **D-07: Recluster trigger is duration, not evaluation count.** `RECLUSTER_RECOMMENDATION#{topic_id}` is written when `LOW_CONFIDENCE_ASSIGNMENT#` counts under a single topic exceed `drift_low_confidence_topic_max` *for `recluster_persistence_days` consecutive days* (new key in `config/thresholds.json`). Expressing persistence as "N consecutive drift evaluations" was rejected: it conflates frequency with persistence and silently changes meaning if the evaluator cadence ever moves (daily → twice-weekly). Day-window phrasing decouples the threshold from the evaluator's cadence — the consumer queries "how many evaluation rows fall in that window" at read time.

### CRITIC_REJECT producer + consumer

- **D-08: Producer is additive to existing `SPOTLIGHT_REVIEW#` write.** `spotlight/critic.py` already writes `SPOTLIGHT_REVIEW#` entries via `review_queue.write_review_entry` on persistent rejection (after `MAX_RETRIES + 1` attempts, line 571). Phase 12 adds a `CRITIC_REJECT#{cwid}#{pmid_set_hash}` write **alongside** — not replacing — at the same code site. `SPOTLIGHT_REVIEW#` remains the human-reviewer-queue artifact; `CRITIC_REJECT#` is the typed event for consumer aggregation. Two writes, two purposes, both idempotent.
- **D-09: `pmid_set_hash` keying gives natural producer-side dedup.** The same critic can reject the same `(cwid, pmid_set)` combination across regen attempts within a single spotlight run; hash-keying overwrites those retries in place rather than counting each as a separate rejection event. This means `SPOTLIGHT_DIAGNOSTIC#` count semantics are **"distinct rejected pmid_sets over the window,"** not "total rejection events." That distinction must be documented at the producer site and in the `SPOTLIGHT_DIAGNOSTIC#` record's docstring — operators reading the diagnostic and trying to reason about whether the same pubs got rejected repeatedly (stuck ranking) vs different pub sets over time (deeper scoring issue) need to know what the count means.
- **D-10: `reason_code` is a controlled vocabulary, not free text.** Define enum in `spotlight/critic.py`: `LEDE_INCOHERENT`, `WEAK_EVIDENCE`, `OFF_TOPIC_PUBS`, `INSUFFICIENT_NARRATIVE`. (Planner may refine the exact list once they read the current critic verdict shape; the *contract* is that it's a closed enum.) The `SPOTLIGHT_DIAGNOSTIC#{cwid}` record carries reason-code distribution (`{LEDE_INCOHERENT: 4, WEAK_EVIDENCE: 1}`) rather than a count. Reason-code distribution is the diagnostic value: "5 rejections, 4 of which were lede incoherence" suggests a lede generator issue; "5 rejections, 4 of which were off-topic pubs" suggests a scoring/ranking issue for that CWID.
- **D-11: Two tunables for the diagnostic finding.** `critic_reject_persistence_days` (window in days) + `critic_reject_cwid_max` (count threshold within that window), both in `config/thresholds.json`. Same persistence-as-duration pattern as D-07. Diagnostic written when a CWID's distinct-rejected-pmid-set count exceeds the max within the window.

### Both aggregations — record shape, file layout, weighting

- **D-12: Parallel DDB partitions, parallel CSV files.** New partition `SUBTOPIC_SCORE_INCLUSIVE#{topic}#{subtopic}` alongside existing `SUBTOPIC_SCORE#{topic}#{subtopic}`. New CSV `faculty_subtopic_counts_inclusive.csv` alongside renamed `cwid_subtopic_counts.csv` → `faculty_subtopic_counts_exclusive.csv`. Extended fields on existing record was rejected because a field named `score` that means two different things depending on which column you read is exactly the failure mode G-19 was about. Different aggregations of different populations should live in different partitions so a query like "give me all scores for subtopic X" doesn't carry an implicit choice about semantics.
- **D-13: CSV rename is a contract change requiring SPS-consumer audit.** Plan must check whether SPS (or any ad-hoc script/dashboard) reads `cwid_subtopic_counts.csv` today. If yes: emit both filenames for one cold-run cycle, log warnings on old-name reads, drop the old name in a subsequent phase. If no (operator/research artifact only): direct rename is cheap. Do not commit the rename without that audit.
- **D-14: Two-partition writes are idempotent at partition level, not transactional.** The aggregator stage writes to `SUBTOPIC_SCORE#` then `SUBTOPIC_SCORE_INCLUSIVE#` per topic. On crash between writes, a re-run against the same `run_id` cleanly overwrites whichever partition was incomplete. TransactWriteItems was rejected (size limits exceeded by some topics' subtopic counts; complexity vs benefit poor). Existing substrate already supports run-id-keyed idempotent rewrites; reuse that.
- **D-15: Inclusive weighting is uniform full article_score per above-floor assignment.** Every subtopic_id present in the row's `subtopic_ids[]` (primary and all secondaries that cleared the confidence floor) receives the full `article_score`. Confidence-weighted secondaries was rejected: it mixes the "anything related to X" query the inclusive column exists for with a relevance-ranking concern that belongs in a separate aggregation if ever needed. Per-pub 1/N normalization was rejected: it conserves total mass per PMID, which makes it "exclusive aggregation with different weighting basis," not an inclusive aggregation at all.
- **D-16: The confidence floor still gates `subtopic_ids[]` membership.** D-15's "uniform full weight" applies to every *above-floor* assignment, not every conceivable assignment. Papers whose confidence didn't clear the floor for subtopic Y don't appear in the row's `subtopic_ids[]`, so they don't contribute to inclusive aggregation under Y. The inclusive aggregation is "uniform full weight for every above-floor assignment," not "uniform full weight for every conceivable assignment."
- **D-17: Arithmetic invariant is documented at the writes, not only in tests.** The aggregator code carries an inline comment stating:
  - `sum(SUBTOPIC_SCORE_INCLUSIVE#X#*) ≥ sum(SUBTOPIC_SCORE#X#*)` — with equality iff every paper in topic X has exactly one above-floor subtopic assignment in X.
  - `sum(SUBTOPIC_SCORE_INCLUSIVE#X#*) − sum(SUBTOPIC_SCORE#X#*)` = total article_score contributed by secondary assignments in topic X.

  Also document in `docs/topic-subtopic-assignment.md` as a one-liner — future-you reading the code in eighteen months wants to find the invariant where the writes happen, not infer it from gate logic.
- **D-18: Reconciliation gate fails the publish stage, not aggregation.** Register a new gate in `gates/registry.py` (per Phase 9 framework). On reconciliation failure: emit a typed warning + block the publish stage; **do not** block aggregation. Aggregation completing is useful state — operators want to read the partitions to diagnose what went wrong. Blocking publish until human review means SPS never sees inconsistent data; blocking aggregation deprives the operator of the data they need to triage.

### §11 residual hygiene scope

- **D-19: Five items ship inside the main phase work; G-37 ships as a separately-gated final task within the phase.** Items inside main phase: G-1 (ReciterDB column refs into `utils/env_check.py`), G-18 (magic numbers → `config/thresholds.json`), G-24 (document sensitive-topic exclusion list), G-34 (reference IAM policy from `GETTING_STARTED.md`), G-36 (reproducibility test for `pipeline_hierarchy/`). G-37 (E2E integration test) is the only item with a *design problem* (what does a representative fixture corpus look like?) before it's a coding problem; bundling it with the others risks a single slip blocking the entire residual cleanup.
- **D-20: G-37 gating is SHA-able, not verbal.** G-37 work starts only after `pytest` passes on every other Phase 12 deliverable in CI on `main`. "Gated on the feedback work being merged" is too vague — it can mean "the PR is open and looks fine," which is exactly how integration testing gets started against half-stable substrate and produces tests that drift the moment something lands.
- **D-21: G-37 deliverable is one bounded E2E test.** "Run a stripped-down fixture corpus through the cold path from `generate_taxonomy.py` to `pipeline_hierarchy/publish.py`, assert the published manifest validates against the published schema, assert the same fixture corpus produces a byte-identical `hierarchy.json` on a second run (Phase 11 G-29 removed the timestamp fields that previously broke this invariant)." Not "an integration test against fixtures" — one specific test that buys the most architectural confidence per unit of work. Additional integration tests are follow-ups.
- **D-22: G-37 carry-over case is filed as a GitHub issue on day one.** The phase's first PR description includes a tracking-issue URL for G-37 from the start. If G-37 ships inside Phase 12, the issue closes when the phase merges; if G-37 slips, the issue is already filed with the correct context. Same "make carry-forward state queryable from day one" pattern as Phase 11's cold-run audit rows.
- **D-23: G-18 lifts every operationally-tunable number into `config/thresholds.json`.** Single file for: `tie_epsilon` (from `assign_subtopics.py:98`, value 0.001), `confidence_floor` (from `assign_subtopics.py:95` `DEFAULT_CONFIDENCE_FLOOR`, **current operational value 0.3** — preserved as default; whether 0.3 is the right target is a separate tuning question tracked in `.planning/issues/0001-confidence-floor-target.md`, NOT in Phase 12 scope), `score_floor` (from `assign_subtopics.py:94` `SCORE_FLOOR`, value 0.3 — the activity-row qualification threshold; lift alongside the others), and every new Phase 12 tunable (`feedback_sweep_max_pmids`, `recluster_persistence_days`, `critic_reject_persistence_days`, `critic_reject_cwid_max`). Splitting into `thresholds.json` vs `algorithm.json` was rejected — "ops can tune" vs "engineering can tune" is a permissions distinction, not a structural one, and every value has a plausible argument for either bucket. CLI flags (`--confidence-floor`) still override per-invocation. **Correction note (CR-2026-05-12):** earlier D-23 wording cited 0.35 and line 1022; both were drift from spec text rather than code inspection. Actual code: 0.3 at line 95; line 1022 is the CLI-flag default reader, not the constant.
- **D-24: `confidence_floor` key has no `_default` suffix.** Code check confirmed `assign_subtopics.py` exposes only a single global `--confidence-floor` flag — no per-topic override exists today. A `_default` suffix would falsely promise an override mechanism that doesn't exist. If per-topic overrides are ever added, the migration is `confidence_floor` → `confidence_floor_default` + `confidence_floor_overrides: { topic_id: float }` map; not a Phase 12 concern.
- **D-25: `confidence_floor` (this phase, G-18) ≠ `low_confidence_floor` (existing in `thresholds.json`).** Different keys, different decisions, **different current values**: `confidence_floor` = 0.3 (assignment-time decision — whether a subtopic_id appears in the row's `subtopic_ids[]` at all); `low_confidence_floor` = 0.35 (event-emission decision — whether to emit a `LOW_CONFIDENCE_ASSIGNMENT#` event for downstream consumption). Planner must preserve both keys with distinct names and distinct values — collapsing them is a regression. **Correction note (CR-2026-05-12):** earlier D-25 wording claimed "both are 0.35 today"; that was wrong. The two floors have always been operationally distinct in code.
- **D-26: Every key in `thresholds.json` is documented in sibling `config/thresholds.md`.** Markdown lists every key with semantics, default, and a one-sentence "if you raise this, here's what happens." JSON doesn't carry comments; centralizing values without centralizing meaning reproduces half of G-18's gap. A future operator reading `feedback_sweep_max_pmids: 500` needs to know whether 500 is "default we never tuned" vs "default we tuned carefully" vs "default that's known to be too low."
- **D-27: `thresholds.json` is schema-validated at startup via `env_check.py`.** Add `config/thresholds.schema.json`; `utils/env_check.py` validates on load. A typo (forgetting a key, misspelling one, putting a string where a float belongs) currently fails late at whichever stage first reads the missing key. Same pattern as the hierarchy schema-validation gate from Phase 9.
- **D-28: CLI tunable overrides are logged at stage startup and written into STAGE# records.** Every stage that reads a tunable logs at startup which values came from `thresholds.json` vs CLI override, and records them in the STAGE# row. The audit trail for "why did this run produce these numbers" includes inputs, not just outputs. Cheap discipline now, expensive to retrofit.
- **D-29: Plan must reflect parallel-fan dependency, not linear sequence.** The five Phase 12 §11 items have very uneven shapes: G-1 mechanical, G-18 mechanical, G-24 writing task, G-34 writing task, G-36 the only one with real engineering surface. The four mechanical/writing items can ship in parallel; G-36 and G-37 are sequenced long-poles. The plan dependency graph should reflect a fan with two long-pole items, not "six items in a line."

### Added 2026-05-12 after RESEARCH.md verification surfaced F-1/F-2/F-3/F-4

- **D-30: `reason_code` uses `LLMVerdict.failed_constraint` verbatim; deterministic gate failures get `PRE_LLM_GATE` with the specific pre-LLM constraint preserved.** The starting set named in earlier D-10 wording (`LEDE_INCOHERENT`, `WEAK_EVIDENCE`, `OFF_TOPIC_PUBS`, `INSUFFICIENT_NARRATIVE`) was speculative — the actual critic exposes the four LLM codes (`active_verb`, `anchored_in_synopses`, `no_faculty_named`, `institutional_voice`) on `LLMVerdict.failed_constraint` plus a separate set on `DeterministicVerdict.failed_constraints`. Use the LLM codes verbatim as `reason_code` so `SPOTLIGHT_DIAGNOSTIC#` rows speak the same vocabulary operators already see on `SPOTLIGHT_REVIEW#`. **Deterministic gate failures** (lede never reached the LLM) emit a `CRITIC_REJECT#` row with `reason_code: PRE_LLM_GATE` and a separate `pre_llm_constraint` field carrying the specific deterministic code (`active_verb`, etc.). `SPOTLIGHT_DIAGNOSTIC#` distribution counts pre-LLM rejections as a single `PRE_LLM_GATE` bucket while preserving underlying detail for drill-down. Rationale: pre-LLM and post-LLM rejections are different failure layers (lede-quality signal vs substantive-misalignment signal); mixing them in one distribution makes the diagnostic harder to act on. A producer-side translation layer mapping critic codes to a coarser CONTEXT-named enum was rejected — two parallel vocabularies for the same underlying signal compound as the critic vocabulary evolves.

- **D-31: The four LLM `reason_code` values are pinned as a Python enum in `spotlight/critic.py` with producer-side validation.** Define `class CritReasonCode(StrEnum): ACTIVE_VERB = "active_verb"; ANCHORED_IN_SYNOPSES = "anchored_in_synopses"; NO_FACULTY_NAMED = "no_faculty_named"; INSTITUTIONAL_VOICE = "institutional_voice"; PRE_LLM_GATE = "pre_llm_gate"` (exact spelling matches the LLM's output to preserve historical comparability). Producer validates `LLMVerdict.failed_constraint` against the enum before writing `CRITIC_REJECT#`. If the LLM ever emits a code outside the enum: emit a typed warning via `pipeline_common.alert` (severity `warning`, not failure — the rejection still happened and should still be recorded) and write the row with `reason_code: "unknown"` + the raw string preserved in a `raw_failed_constraint` field. The diagnostic distribution then surfaces an `unknown` bucket whose growth is itself a signal that the critic vocabulary drifted. Rationale: without the enum, vocabulary is whatever the LLM happened to say last; historical SPOTLIGHT_DIAGNOSTIC# rows become incomparable across vocabulary changes.

- **D-32: `SPOTLIGHT_DIAGNOSTIC#{cwid}` rows link back to their underlying `CRITIC_REJECT#` provenance for drill-down.** Add field `underlying_rejects: list[str]` carrying the PK suffixes (`{cwid}#{pmid_set_hash}`) of every `CRITIC_REJECT#` row aggregated into this diagnostic. Operator triaging "spotlight is structurally failing for CWID X" navigates from the aggregated counter to the specific ledes that were rejected. Cap the list at `feedback_diagnostic_max_underlying` (new tunable in `thresholds.json`, default 20) to bound row size; on overflow, set `underlying_rejects_truncated: true` + `total_underlying: N`. Rationale: without provenance, the diagnostic is a counter with no path to evidence; operators see "12 rejections, distribution X" and can't reach the actual content. Same "make state queryable" principle as D-22.

- **D-33: Aggregation stage enforces faculty-map ↔ `SUBTOPIC_SCORE#` reconciliation invariant.** Per F-1: `SUBTOPIC_SCORE#{topic}#{subtopic}` and `SUBTOPIC_SCORE_INCLUSIVE#{topic}#{subtopic}` are both NEW partitions; the existing `faculty.subtopic_scores.<topic_id>` map persists as the SPS consumer contract (D-13 forbids SPS-side changes in Phase 12). After writing both the faculty-map and `SUBTOPIC_SCORE#`, the aggregator verifies per-CWID per-subtopic equality between the two derived views of the same exclusive aggregation. **Divergence is a stage failure, not a warning.** Cheap to check (summing the same numbers two ways); prevents the substrate from developing a split-brain that surfaces months later as "dashboard X says 4.2 but CSV says 4.3." Inclusive aggregation has no parallel faculty-map view today, so this invariant applies only to the exclusive pair. Rationale: two derivations of the same aggregation that disagree is the G-19 failure mode repeating one level down.

- **D-34: `per_topic_low_confidence` on `DRIFT#evaluation` rows uses sparse representation.** F-2 extends `DriftEvaluation.to_dynamodb_item()` with `per_topic_low_confidence: dict[topic_id, count]` so D-07's `RECLUSTER_RECOMMENDATION#` consumer reads materialized per-topic counts rather than re-scanning raw `LOW_CONFIDENCE_ASSIGNMENT#` rows (and risking divergence from what drift alerts report). **Only topics with non-zero counts above the existing `low_confidence_floor` are stored**; zero-count entries carry no signal worth persisting and would grow the row to O(taxonomy_size) per day. Today's ~67-topic taxonomy is well under DDB's 400KB item limit either way, but sparse-by-default is the correct contract — future taxonomy growth (500+ topics) doesn't bump up against item-size limits, and the absence of a key cleanly means "zero this day." Document the sparse semantics at the write site and in the consumer (`feedback.sweep`'s recluster-trigger reader) so "key absent" is never confused with "row from before the field existed" (handle the latter via a presence check + skip).

### Claude's Discretion

- ~~Exact list of `reason_code` enum values in `spotlight/critic.py` (D-10). The *contract* is that it's a closed enum; the planner reads the current critic's verdict shape and picks the right discriminators. Starting set proposed: `LEDE_INCOHERENT`, `WEAK_EVIDENCE`, `OFF_TOPIC_PUBS`, `INSUFFICIENT_NARRATIVE`. Refine as needed.~~ **Resolved by D-30/D-31 (2026-05-12).**
- Module layout for the feedback sweep (`feedback/`, `pipeline_feedback/`, or folded into `pipeline_cold/`). Planner picks based on Phase 10/11 module-naming conventions.
- Whether the schema-validation in `env_check.py` (D-27) uses an existing schema library (jsonschema is already in the dep tree if so) or hand-rolled type checks. Planner picks based on existing patterns.
- Initial defaults for the new Phase 12 tunables (`feedback_sweep_max_pmids`, `recluster_persistence_days`, `critic_reject_persistence_days`, `critic_reject_cwid_max`). Planner picks sensible defaults; document the reasoning in `thresholds.md` per D-26.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Spec sections (load-bearing for this phase)
- `docs/RECITERAI-SPEC.md` §8 — Both aggregations: rationale for parallel exclusive/inclusive partitions; consumer-side column-pick contract; G-19/G-20 collapsing rules.
- `docs/RECITERAI-SPEC.md` §9 — Feedback loops: three signals (critic-reject, uncovered-PMID, low-confidence-assignment) and how each becomes a typed event consumed by the cold path.
- `docs/RECITERAI-SPEC.md` §11 — Residual items: G-1, G-18, G-24, G-34, G-36, G-37 definitions and rationale. Note: G-29 already shipped Phase 11 (G-36 is now meaningful); G-32 / G-35 already resolved per spec.

### Prior-phase context (substrate Phase 12 builds on)
- `.planning/phases/09-substrate-stages-and-gates/09-SUMMARY.md` — `gates/registry.py` framework. Phase 12's reconciliation gate (D-18) registers here.
- `.planning/phases/10-hot-cold-path-split/10-SUMMARY.md` — Producers of `UNCOVERED_PMID#` and `LOW_CONFIDENCE_ASSIGNMENT#`; drift evaluator behavior; `pipeline_common.alert` channel. Phase 12 consumes these, does not extend the drift evaluator.
- `.planning/phases/10-hot-cold-path-split/10-CONTEXT.md` — Severity-tagged alert pattern; cold-path orchestrator entry point (`pipeline_cold.run.main()`) where Phase 12's non-gating sweep stage registers.
- `.planning/phases/11-versioning-review-diff/11-CONTEXT.md` — STAGE# `run_id` field (D-13 of Phase 11) — Phase 12's `source_sweep_run_id` follows the same pattern; CLI tool pattern from `python -m review` is the model for `python -m feedback`.
- `.planning/phases/11-versioning-review-diff/11-SUMMARY-change-signaling.md` — Confirms G-29 (`generated_at` removed from `hierarchy.json`) actually shipped. G-36's reproducibility test is only meaningful because of this.

### Producer-side artifacts (Phase 12 modifies)
- `docs/hierarchy-contract.md` — May need a one-paragraph note if the reconciliation gate's failure surfaces in published manifest metadata. Planner decides.
- `docs/topic-subtopic-assignment.md` — Per D-17, gains the one-line invariant about exclusive/inclusive arithmetic.
- `docs/aws-iam-pipeline-policy.json` + `docs/aws-iam-pipeline-policy-artifacts.json` — G-34's target. Reference from `GETTING_STARTED.md`.
- `GETTING_STARTED.md` — G-34 adds an "IAM policy" section.

### Code refs the planner should read
- `utils/event_records.py` — Existing producers (`UNCOVERED_PMID#`, `LOW_CONFIDENCE_ASSIGNMENT#`). Phase 12 adds a third builder + writer for `CRITIC_REJECT#`. Follow the same docstring + Decimal-coercion + idempotent-PK pattern.
- `spotlight/critic.py:571` — Critic rejection write site (`write_review_entry` call after `MAX_RETRIES + 1` attempts; verified via grep). D-08 adds the `CRITIC_REJECT#` write **alongside** the existing `SPOTLIGHT_REVIEW#` write; does not replace. Per D-30/D-31, the `CritReasonCode` enum is defined in this file; validation happens at the producer site here.
- `spotlight/review_queue.py:134` — `write_review_entry` is the existing `SPOTLIGHT_REVIEW#` writer; remains untouched.
- `aggregate_subtopic_scores.py:108-167` — `_aggregate` produces exclusive output today. Phase 12 adds the inclusive aggregator alongside; both must respect the confidence-floor membership rule (D-16).
- `rollup_by_cwid.py` — Reads `cwid_subtopic_counts.csv`. D-13's rename requires updating the reader and verifying no other readers exist.
- `assign_subtopics.py:94` — `SCORE_FLOOR = 0.3` (activity-row qualification threshold). G-18 moves to config alongside the other tunables.
- `assign_subtopics.py:95` — `DEFAULT_CONFIDENCE_FLOOR = 0.3` (assignment-time floor). G-18 moves to config; `config/thresholds.json:confidence_floor` becomes authoritative. Line 1022 is the CLI flag's `default=DEFAULT_CONFIDENCE_FLOOR` read, not the constant definition.
- `assign_subtopics.py:98` — `TIE_EPSILON = 0.001`. G-18 moves to config.
- `assign_subtopics.py:1022` — `--confidence-floor` flag default. G-18 moves the default to config; flag still overrides.
- `assign_subtopics.py:642-648` — Where `subtopic_ids[]` is written; Phase 12 doesn't modify but the confidence-floor decision lives here.
- `pipeline_drift/evaluator.py` — Already counts UNCOVERED and LOW_CONFIDENCE in a rolling window. **Phase 12 may extend `DriftEvaluation.to_dynamodb_item()` additively for downstream consumer needs** (specifically: F-2 surfaced the need for a per-topic `LOW_CONFIDENCE_ASSIGNMENT#` count dict so D-07's `RECLUSTER_RECOMMENDATION#` consumer can read materialized counts rather than re-scanning raw event rows; the additive field follows Phase 11's `run_id` precedent on STAGE# rows). **Phase 12 must NOT change the alerting thresholds, severity logic, or `cold_run_recommended` semantics of the drift evaluator** — preserve the evaluator's *behavior* contract, not its *schema* contract. The original D-04 rationale (critic-reject is a per-CWID signal, not a corpus-scoped one — folding it into the drift evaluator either dilutes the signal or creates a misshapen severity input) still applies to CRITIC_REJECT# specifically; that signal does NOT enter the drift evaluator. Sparse representation for the per-topic dict per D-34.
- `pipeline_cold/run.py` — Cold-path orchestrator entry point; Phase 12 registers the non-gating feedback-sweep stage here, threads the `source_sweep_run_id` UUID.
- `utils/stage_records.py` — STAGE# substrate. D-28's tunable-audit fields land here; backwards-compatible additive change like Phase 11 D-13.
- `gates/registry.py` — Phase 9's gate-registration framework; Phase 12's reconciliation gate (D-18) registers here.
- `utils/env_check.py` — G-1 target (ReciterDB column references) AND D-27 target (thresholds-schema validation).
- `config/thresholds.json` — Existing file (`uncovered_score_floor`, `low_confidence_floor`, `drift_*`, `spotlight_dirty_*`). Phase 12 extends with: `tie_epsilon`, `confidence_floor`, `feedback_sweep_max_pmids`, `recluster_persistence_days`, `critic_reject_persistence_days`, `critic_reject_cwid_max`.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets

- **`utils/event_records.py` builder pattern.** `build_uncovered_pmid_record` / `build_low_confidence_assignment_record` already establish: pure builder + thin `write_*` wrapper; Decimal coercion for floats; idempotent PK (per-PMID), constant SK; `created_at` ISO timestamp; `source_stage` field. The `CRITIC_REJECT#` producer follows the same shape — pure builder + thin writer, PK `CRITIC_REJECT#{cwid}#{pmid_set_hash}`, SK `GLOBAL`, `source_stage="spotlight.critic"`.
- **`pipeline_drift/evaluator.py` already does the LOW_CONFIDENCE-per-topic counting.** The drift evaluator's per-topic count (`low_confidence_max_topic` + `low_confidence_max_count`) is the input signal D-07's recluster trigger reads. The feedback sweep doesn't re-implement the count — it reads the prior N days of `DRIFT#evaluation` rows and applies the persistence threshold.
- **`pipeline_common.alert`** — Existing severity-tagged Slack + GitHub-issue dispatcher from Phase 10. Phase 12's feedback sweep does **not** use this (the locked dispatcher decision defers GitHub-issue integration to a follow-up phase). Listed here so the planner knows it's available if the sweep needs to surface critical failures (sweep itself errors out, not findings) — that's a different concern.
- **`pipeline_cold.run.main()` argparse plumbing** — Already supports stage registration with `--initiated-by` and (per Phase 11 D-13) `run_id` threading. The feedback-sweep stage registers like any other cold-path stage; the `source_sweep_run_id` is generated inside the stage (not the cold-run-level `run_id`) so operator-CLI invocations get their own.
- **Phase 9 `gates/registry.py` framework** — Reconciliation gate (D-18) registers as another entry alongside `parent_prefix`, `pii_scan`, `schema_validation`, `schema_roundtrip`. Same gate-result protocol; same registration pattern.
- **`review/validator.py` / `review/cli.py`** (Phase 11) — Model for the `python -m feedback` CLI. Same subcommand shape, same config-resolution pattern (`~/.reciterai/config.yaml` if any operator-identity field is needed — though Phase 12's feedback sweep is unlikely to need reviewer attribution).

### Established Patterns

- **Idempotent overwrite via stable PK + constant SK** — Used for `UNCOVERED_PMID#`, `LOW_CONFIDENCE_ASSIGNMENT#`, `DRIFT#evaluation` (DAY#-keyed SK). Phase 12 finding records follow the same: stable PK, `SK = "GLOBAL"`, re-runs overwrite. The `source_sweep_run_id` lives in the body, not the key, so a new sweep produces a new record only if its PK differs (per D-05's rule).
- **Run-id threading from `pipeline_cold.run.main()`** — Established by Phase 11 D-13. Phase 12's feedback-sweep stage receives the cold-run-level `run_id` for context, but generates its own `source_sweep_run_id` (UUID) for its findings — the two are different concepts (the cold run is a multi-stage orchestration; the sweep is one stage within it whose findings may outlive the cold run).
- **Single config file with sibling markdown documentation** — Phase 12 sets the precedent here (D-23, D-26). Future phases that add tunables extend `thresholds.json` and update `thresholds.md`.
- **Audit row at the substrate boundary** — Phase 11 D-06/D-16 wrote `STAGE#hierarchy_version_cutover#GLOBAL` / `STAGE#g29_cutover#GLOBAL` rows. Phase 12's tunable-override logging (D-28) extends STAGE# records with the input field set, same pattern.
- **CLI subcommand with explicit `--since` window** — Established by `python -m review` (Phase 11). Phase 12's `python -m feedback sweep` follows; `--since DAYS` overrides the watermark behavior for ad-hoc operator runs.

### Integration Points

- **`aggregate_subtopic_scores.py`** is the load-bearing site for Phase 12 §8 work. Today its `_aggregate` returns `(faculty_scores, subtopic_total_weights)` keyed by `(person_identifier, primary_subtopic_id)`. Phase 12 adds an inclusive aggregation function that enumerates `subtopic_ids[]`, returns a parallel `(faculty_scores_inclusive, subtopic_total_weights_inclusive)`, and the caller writes both to two DDB partitions and two CSVs.
- **`rollup_by_cwid.py`** must learn about the new file. The current code reads `cwid_subtopic_counts.csv` directly; D-13's rename pivots this read and (depending on SPS-consumer audit) may require dual-name emission for a deprecation window.
- **`spotlight/critic.py` rejection-write site (line ~571)** is where `CRITIC_REJECT#` joins `SPOTLIGHT_REVIEW#`. Two writes, idempotent, independent purposes — `SPOTLIGHT_REVIEW#` queues the lede for human review; `CRITIC_REJECT#` feeds aggregation across CWIDs.
- **`pipeline_cold/run.py`** stage chain — Phase 12 adds a feedback-sweep stage. Per D-02, it's non-gating: it writes findings, never raises a stage failure based on what it found.
- **`gates/registry.py`** entry for the reconciliation gate, hooked into the publish stage (not the aggregator). Per D-18: aggregation completes, reconciliation runs, on fail the publish stage refuses to push to S3.
- **`config/thresholds.json` + `config/thresholds.md` + `config/thresholds.schema.json`** is a three-file substrate (data + doc + schema). G-18 establishes the trio. Future phases adding tunables update all three.

</code_context>

<specifics>
## Specific Ideas

- **"Aggregations of different populations that happen to share keys."** The user's framing for why exclusive and inclusive must be parallel partitions (not extended fields on one record). Planner should preserve this framing in the aggregator docstring — the populations are different, the keys are the same, the partitions are honest about both.
- **"Diagnosis vs documentation"** as the two events the hybrid feedback trigger serves. Operator-CLI = diagnosis (drift alert → investigate). Cold-path stage = documentation (cold run is happening; record findings against this run_id). Planner should keep this framing in the CLI's `--help` text and in the stage's docstring.
- **"Make carry-forward state queryable from day one, not reconstructed later."** D-22's principle (G-37 tracking issue filed upfront) — same principle Phase 11 used for cutover audit rows. Worth applying everywhere Phase 12 has a "if it slips, file an issue" moment.
- **Distinct rejected pmid_sets, not total rejection events.** The semantic distinction in D-09 must surface in the `SPOTLIGHT_DIAGNOSTIC#` docstring and in any operator-facing markdown. Counting wrong here is the difference between "spotlight ranking is broken for CWID X" and "spotlight regeneration is being retried a lot."
- **One bounded test, not "an integration test."** D-21's framing — the deliverable for G-37 is a *specific* test, not a test suite. The planner should write the test scope into the plan before writing the test.

</specifics>

<deferred>
## Deferred Ideas

- **GitHub-issue dispatcher reading from `CANDIDATE_TOPIC#` / `RECLUSTER_RECOMMENDATION#` / `SPOTLIGHT_DIAGNOSTIC#` rows.** Separate follow-up phase that reuses the Phase 10 alert-dispatcher pattern. Reads finding records on some cadence, dedupes against existing labels, files/updates issues. Not Phase 12 scope.
- **Longitudinal candidate-topic tracking across sweeps.** Merging findings across `source_sweep_run_id` values to track "this candidate topic has been proposed across the last 3 sweeps." A real feature if/when needed; D-05 explicitly forbids it as an accidental side effect of overwrite semantics.
- **Confidence-weighted secondary aggregation (`SUBTOPIC_SCORE_WEIGHTED#`).** If consumers ever need relevance-ranked subtopic scores, that's a third aggregation, not a knob on the inclusive one. Phase 12 ships exclusive + inclusive; weighted is its own future phase if a consumer asks for it.
- **Per-topic `confidence_floor` override.** `confidence_floor` key has no `_default` suffix (D-24) because no override mechanism exists today. If per-topic overrides are added later, migration is `confidence_floor` → `confidence_floor_default` + `confidence_floor_overrides: { topic_id: float }` map.
- **Drift evaluator extension for CRITIC_REJECT counts.** Rejected (D-04 rationale): critic-reject is a per-CWID signal; corpus-scoped folding either dilutes the signal or creates a misshapen severity input. The right shape is `SPOTLIGHT_DIAGNOSTIC#{cwid}`, not a drift-evaluator input.
- **Per-topic `REVIEW#` granularity.** Already deferred from Phase 11. Phase 12 doesn't revisit.
- **A separate `algorithm.json` config file.** Rejected (D-23 rationale): "ops can tune" vs "engineering can tune" is a permissions distinction, not a structural one. Single file with sibling markdown.
- **Auto-promotion of `CANDIDATE_TOPIC#` rows to taxonomy.** Findings feed the next cold run's taxonomy regen as input — they do not auto-mutate the taxonomy. Operator decides whether to promote (mechanism for that promotion is its own follow-up; current expectation is a `python -m taxonomy promote <slug>` CLI similar to `python -m review approve`, but Phase 12 does not need to ship that — the findings are queryable without it).
- **A test that asserts `thresholds.md` documents every key in `thresholds.schema.json`.** Nice-to-have, not lock-required (D-26 closing note). If the planner has time and it falls naturally out of the schema-validation work, ship it; otherwise file as a follow-up.

</deferred>

---

*Phase: 12-feedback-loops-both-aggregations-residual-hygiene*
*Context gathered: 2026-05-12*

# Phase 12: Feedback Loops, Both Aggregations, Residual Hygiene — Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in `12-CONTEXT.md` — this log preserves the alternatives considered.

**Date:** 2026-05-12
**Phase:** 12-feedback-loops-both-aggregations-residual-hygiene
**Areas discussed:** Feedback consumer surface, CRITIC_REJECT scope, Both-aggregation shape, §11 residual scope

---

## Area selection

| Option | Description | Selected |
|---|---|---|
| Feedback consumer surface | How UNCOVERED/LOW_CONFIDENCE/CRITIC_REJECT signals turn into action — cold-path auto-stage vs operator CLI vs drift-coupled vs hybrid; output target (DDB vs markdown vs GitHub) | ✓ |
| CRITIC_REJECT scope | Producer + consumer + alerting vs producer + report only | ✓ |
| Both-aggregation shape | DDB record shape, CSV layout, secondary weighting, reconciliation gate scope | ✓ |
| §11 residual scope | Which of G-1, G-18, G-24, G-34, G-36, G-37 to include, G-37 fixture scope, G-18 magic-numbers home | ✓ |

---

## Area 1 — Feedback consumer surface

### Q1: How should the feedback consumer be triggered?

| Option | Description | Selected |
|---|---|---|
| Operator CLI only | `python -m feedback sweep --since DAYS`; drift alert is the trigger signal, operator is the trigger mechanism | |
| Cold-path auto-stage | Register as a stage inside `pipeline_cold.run.main()`; every cold run executes the consumer | |
| Drift-evaluator-coupled | Daily drift evaluator enqueues `FEEDBACK_SWEEP#{date}` row on severity=ERROR; separate scheduled handler picks it up | |
| CLI + cold-path hybrid | Two entry points, same code path; operator CLI for ad-hoc, cold-path also invokes as non-gating stage | ✓ |

**User's choice:** CLI + cold-path hybrid (option 4).
**Notes:** The "authoritative output" concern is a non-issue when findings are keyed by `source_sweep_run_id` — every run produces its own findings record; last-writer-wins isn't a concept. The two trigger conditions are genuinely different events: drift alert → operator runs CLI to investigate (diagnosis); cold run executing → sweep runs as non-gating stage to fold fresh findings into the same `run_id` context (documentation). Cold-path invocation must be explicitly **non-gating**: a finding "12 candidate new topics" doesn't block a cold run that was approved to re-cluster against the *current* taxonomy. Findings flow into the next taxonomy regen's input pile, not this one's — otherwise a chicken-and-egg.

### Q2: Where does the Sonnet uncovered-PMID sweep output land?

| Option | Description | Selected |
|---|---|---|
| DDB candidate-topic records | Typed `CANDIDATE_TOPIC#{slug}` rows; operator promotes via separate CLI | |
| Markdown backlog | `reports/feedback-sweep-{run_id}.md` per run | |
| Both — DDB primary, markdown rendered from it | DDB rows are source of truth; `python -m feedback render` emits markdown view from rows | ✓ |
| Auto-create GitHub issues | Sweep opens GitHub issues via `gh` (one per finding) | |

**User's choice:** DDB primary, markdown rendered from it (option 3).
**Notes:** Three things to lock alongside: (a) `source_sweep_run_id` keying makes idempotent overwrite the default — same run_id overwrites (operator retry of transient failure), new run_id produces separate record, no merging across sweeps (that's longitudinal tracking, a separate feature). (b) Markdown render is deterministic from the DDB row — no `generated_at` in body; timestamp in filename only (G-29 lesson). (c) GitHub-issue dispatcher is a separate follow-up that reuses the Phase 10 alert-dispatcher pattern — don't fold it into Phase 12.

### Q3: What's the input slice the Sonnet uncovered-PMID sweep operates over?

| Option | Description | Selected |
|---|---|---|
| All UNCOVERED# since last sweep_run_id | Self-watermarking; risk is unbounded growth | |
| Rolling 14-day window | Mirrors drift evaluator window | |
| Operator-bounded `--since DAYS` | Sweep takes a flag, default 14 days from cold-path | |
| Since-last-sweep, capped at N | Watermarked since prior sweep, capped at N by `top_topic_score` ascending | ✓ |

**User's choice:** Since-last-sweep, capped at N (option 4).
**Notes:** Cap lives in `config/thresholds.json` as `feedback_sweep_max_pmids` per G-18. The sweep's output record carries `truncated: true` + `total_unprocessed_remaining: N` when the cap is hit — surfaces "cap too low or sweep cadence wrong" as a tunable, not a hidden failure.

### Q4: Should the Phase 12 consumer cover candidate-topic discovery AND subtopic re-cluster recommendations, or just candidate topics?

| Option | Description | Selected |
|---|---|---|
| Both — separate finding types | `CANDIDATE_TOPIC#{slug}` from UNCOVERED pool + `RECLUSTER_RECOMMENDATION#{topic_id}` from persistent LOW_CONFIDENCE counts | ✓ |
| Candidate topics only | Defer LOW_CONFIDENCE consumer to follow-up | |
| Both — unified `FEEDBACK_ACTION#` discriminator | Single record type with `action_type` discriminator | |

**User's choice:** Both, separate finding types (option 1).
**Notes:** Marginal cost is low — candidate-topic needs Sonnet pass, recluster is SQL-shaped count aggregation with no model calls. Operator workflow needs both signals (add topics? re-cluster topics?) — shipping only the first relocates manual work, doesn't eliminate it. Unified discriminator was rejected because downstream renderers branch on `action_type` anyway, and the two evidence shapes are genuinely different (PMID set + Sonnet rationale vs topic ID + evaluation history). Recluster trigger phrased as **duration** (`recluster_persistence_days` in thresholds.json), not evaluation count — decouples threshold from drift-evaluator cadence so a future cadence change (daily → twice-weekly) doesn't silently change meaning.

---

## Area 2 — CRITIC_REJECT scope

### Q1: What's the CRITIC_REJECT consumer surface in this phase?

| Option | Description | Selected |
|---|---|---|
| Producer + SPOTLIGHT_DIAGNOSTIC# consumer | Critic emits `CRITIC_REJECT#`; feedback sweep adds third finding type | ✓ |
| Producer + drift-evaluator extension | Critic emits `CRITIC_REJECT#`; drift evaluator counts them as a fourth severity input | |
| Producer only this phase | Critic emits `CRITIC_REJECT#`; defer consumer rule to follow-up | |

**User's choice:** Producer + `SPOTLIGHT_DIAGNOSTIC#{cwid}` consumer (option 1).
**Notes:** "Producer only" reproduces G-23 in fresh costume — capturing rows no consumer reads is operator-vigilance with extra steps. Drift-evaluator extension was the hardest rejection: critic rejection is a *per-faculty* signal; squashing it into a corpus-scoped drift signal either dilutes the signal or creates a misshapen severity input. Consumer-symmetry argument: the feedback sweep already produces two typed findings (`CANDIDATE_TOPIC#`, `RECLUSTER_RECOMMENDATION#`); adding `SPOTLIGHT_DIAGNOSTIC#{cwid}` as the third follows the same pattern — one sweep, three typed outputs, one render path. Three follow-ups locked: (a) Persistence as duration (`critic_reject_persistence_days`), same pattern as `recluster_persistence_days`. (b) `reason_code` is a controlled vocabulary (enum: `LEDE_INCOHERENT`, `WEAK_EVIDENCE`, `OFF_TOPIC_PUBS`, `INSUFFICIENT_NARRATIVE`); diagnostic record carries the distribution, not just a count, because "5 rejections, 4 of which were lede incoherence" is qualitatively different actionable info than "5 rejections, 4 of which were off-topic pubs". (c) `pmid_set_hash` keying gives natural producer-side dedup — same `(cwid, pmid_set)` rejected across regen retries within one spotlight run overwrites in place; count semantics are "distinct rejected pmid_sets over the window," not "total rejection events," and that distinction must be documented.

---

## Area 3 — Both-aggregation shape

### Q1: How should `faculty_subtopic_counts_inclusive` co-exist with the existing exclusive aggregation?

| Option | Description | Selected |
|---|---|---|
| Parallel partition + parallel CSV | New `SUBTOPIC_SCORE_INCLUSIVE#` partition + `faculty_subtopic_counts_inclusive.csv` + rename of existing CSV | ✓ |
| Extended fields on existing record | Keep `SUBTOPIC_SCORE#` partition; add `score_exclusive` / `score_inclusive` fields | |
| Parallel partition, single CSV with both columns | Hybrid — typed DDB substrate, single CSV with both columns | |

**User's choice:** Parallel partition + parallel CSV (option 1).
**Notes:** Extended fields reproduces G-19 — a field named `score` that means different things by which column you read. Hybrid was the trap: DDB partitions are honest, CSV pretends two things are dimensions of one — generates real diagnostic cost when the inevitable bug appears ("the CSV column doesn't match what I get from the inclusive DDB partition"). Three locks alongside: (a) CSV rename is a contract change — must audit whether SPS reads `cwid_subtopic_counts.csv` before committing; if yes, dual-name emission for one cycle with deprecation warnings. (b) Two-partition writes are **idempotent at partition level**, not transactional — TransactWriteItems has size limits some topics exceed; reuse the existing run-id idempotent-rewrite substrate. (c) Reconciliation gate **fails the publish stage**, not aggregation — aggregation completing is useful state for operator triage; blocking publish until human review keeps SPS from seeing inconsistent data.

### Q2: How should `score_inclusive` weight secondary subtopic assignments?

| Option | Description | Selected |
|---|---|---|
| Uniform full weight per assignment | Every subtopic_id in `subtopic_ids[]` gets full article_score | ✓ |
| Confidence-weighted secondaries | Primary gets full; each secondary gets `article_score × (conf_sec / conf_pri)` | |
| Per-pub normalized (1/N split) | Each subtopic_id gets `article_score / |subtopic_ids|` | |

**User's choice:** Uniform full weight per assignment (option 1).
**Notes:** Confidence-weighting mixes the "anything related to X" query with a relevance-ranking concern that belongs in a separate aggregation (a hypothetical `SUBTOPIC_SCORE_WEIGHTED#` for if a consumer ever asks). Produces values that *look* like numbers but mean something operationally opaque, and reconciliation against topic totals stops being a clean invariant. 1/N normalization is wrong twice — defeats the purpose ("anything related to X" should count, not normalize away) and is indistinguishable from exclusive aggregation up to normalization (conserves total mass per PMID). Two locks alongside: (a) Document the invariant `sum(SUBTOPIC_SCORE_INCLUSIVE#X#*) ≥ sum(SUBTOPIC_SCORE#X#*)` (with equality iff every paper in X has exactly one above-floor assignment in X) in the aggregator code comment AND in `docs/topic-subtopic-assignment.md`. Future-you reads the code, not the gate logic. (b) Confidence floor (G-18's 0.35) still gates membership in `subtopic_ids[]` — uniform weight applies to every *above-floor* assignment, not every conceivable assignment.

---

## Area 4 — §11 residual scope

### Q1: Which §11 residual items belong in Phase 12 vs deferred?

| Option | Description | Selected |
|---|---|---|
| All six in this phase | G-1, G-18, G-24, G-34, G-36, G-37 all ship | |
| Five + G-37 as separately-gated final task | Ship G-1, G-18, G-24, G-34, G-36 in main phase work; G-37 gated within the phase | ✓ |
| Pure-maintenance only | G-1, G-18, G-24, G-34 only; defer G-36 and G-37 | |
| Architectural-context only | G-36 and G-37 only; skip pure maintenance | |

**User's choice:** Five + G-37 as separately-gated final task (option 2).
**Notes:** G-37 is heavier than the other five combined and has a *design problem* (what does a representative fixture corpus look like?) before it's a coding problem; bundling it monolithically risks a single slip blocking everything else. Pure-maintenance-only is wrong: G-36 is only conditionally meaningful (depends on Phase 11 G-29 having shipped, which it did per Phase 11 summaries), so the dependency check resolves "in favor" not "deferred." Four locks alongside: (a) G-37 gating is SHA-able — work starts only after `pytest` passes on every other Phase 12 deliverable in CI on `main`, not "looks fine in PR." (b) G-37 deliverable is bounded: one E2E test running stripped-down fixture corpus through cold path `generate_taxonomy` → `publish.py`, asserts manifest validates against schema, asserts byte-identical hierarchy.json on second run (modulo Phase 11's timestamp removal). Not "integration tests" — one specific test. (c) Tracking issue filed upfront as a phase artifact (PR description carries URL on day one), same "queryable from day one" pattern as Phase 11's cutover audit rows. (d) Plan reflects parallel-fan, not linear sequence: G-1, G-18, G-24, G-34 are mechanical/writing items that can ship in parallel; G-36 (real engineering surface) and G-37 (if it lands) are sequenced long-poles.

### Q2: Which numbers does G-18 lift into config, and into which file?

| Option | Description | Selected |
|---|---|---|
| Extend `config/thresholds.json` | All tunables in one file: TIE_EPSILON, confidence_floor, all new Phase 12 tunables | ✓ |
| Split: `thresholds.json` vs `algorithm.json` | Semantic split between "ops-tunable" and "engineering-tunable" | |
| Only the §11-cited numbers | Strict reading; new Phase 12 tunables live in their own homes | |

**User's choice:** Extend `config/thresholds.json` with all of them (option 1).
**Notes:** Split is a permissions distinction, not a structural one — every value has a plausible argument for either bucket (e.g., TIE_EPSILON's tuning is operationally observable; confidence_floor is the exact knob an operator turns on too-many-unassigned-PMIDs). Strict reading reproduces G-18's failure mode in a fresh location — six new tunables fragmented across new homes is the pre-G-18 state. Four locks alongside: (a) Sibling `config/thresholds.md` documents every key with semantics, default, and "if you raise this, here's what happens" — centralizing values without centralizing meaning reproduces half the gap. (b) CLI flag overrides are logged at stage startup AND written into STAGE# records — audit trail for "why did this run produce these numbers." (c) Schema-validate at startup via `config/thresholds.schema.json` + `utils/env_check.py` — typos currently fail late at first read. (d) `confidence_floor` key has no `_default` suffix (code check confirmed: only single global `--confidence-floor` flag exists, no per-topic override). Suffix would falsely promise an override mechanism that doesn't exist.

---

## Claude's Discretion

- Exact list of `reason_code` enum values in `spotlight/critic.py` — starting set proposed (`LEDE_INCOHERENT`, `WEAK_EVIDENCE`, `OFF_TOPIC_PUBS`, `INSUFFICIENT_NARRATIVE`); planner reads current critic verdict shape and refines.
- Module layout for the feedback sweep (`feedback/`, `pipeline_feedback/`, or folded into `pipeline_cold/`) — pick based on Phase 10/11 module-naming conventions.
- Whether `env_check.py` schema validation uses `jsonschema` library vs hand-rolled type checks — pick based on existing patterns.
- Initial default values for the new Phase 12 tunables (`feedback_sweep_max_pmids`, `recluster_persistence_days`, `critic_reject_persistence_days`, `critic_reject_cwid_max`) — document reasoning in `thresholds.md`.

---

## Deferred Ideas

- GitHub-issue dispatcher reading from finding records — follow-up phase that reuses Phase 10 alert pattern.
- Longitudinal candidate-topic tracking across sweeps — explicit feature, not an accidental side effect of overwrite semantics.
- Confidence-weighted secondary aggregation (`SUBTOPIC_SCORE_WEIGHTED#`) — if/when a consumer asks for it.
- Per-topic `confidence_floor` override — no override mechanism exists today; migration path documented if ever needed.
- Drift evaluator extension for CRITIC_REJECT — rejected; per-CWID signal needs CWID-scoped finding, not corpus-scoped severity input.
- Per-topic `REVIEW#` granularity — already deferred from Phase 11.
- Separate `algorithm.json` config file — rejected; single-file approach with sibling markdown.
- Auto-promotion of `CANDIDATE_TOPIC#` rows to taxonomy — operator-gated promotion is its own follow-up CLI, not Phase 12 deliverable.
- Test asserting `thresholds.md` documents every key in `thresholds.schema.json` — nice-to-have follow-up.

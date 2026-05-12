---
phase: 12
slug: feedback-loops-both-aggregations-residual-hygiene
created: 2026-05-12
purpose: One-pass CONTEXT.md vs code audit per 12-CONTEXT-AUDIT-BRIEF.md
status: audit complete; revision NOT applied
---

# CONTEXT Audit — Phase 12

**Auditor:** fresh subagent (Opus 4.7), reading code from disk
**Audit date:** 2026-05-12
**Target:** `.planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-CONTEXT.md`
**Scope:** D-01..D-34 plus every claim in `<canonical_refs>`, `<code_context>`, `<specifics>`, `<deferred>`

---

## Summary table

| Category | Count |
|----------|-------|
| VERIFIED | 22 |
| DRIFT | 9 |
| SPECULATIVE | 2 |
| ARCHITECTURAL MISMATCH | 3 |
| UNVERIFIABLE | 4 |
| **Total claims audited** | **40** |

**Most consequential finding:** D-08 (and the dependent D-09, D-11, D-32 claims) assume per-CWID keying for `CRITIC_REJECT#`, but the spotlight critic operates per-subtopic on N papers whose authorship spans many CWIDs. No cwid is available at the rejection write site. This propagates through D-09 (dedup semantics), D-10/D-30/D-31 (reason-code aggregation per-cwid), D-11 (tunable name `critic_reject_cwid_max`), and D-32 (`SPOTLIGHT_DIAGNOSTIC#{cwid}` provenance). The root source is spec §9 line 396, which proposes `CRITIC_REJECT#{cwid}#{pmid_set_hash}` without grounding the proposal in the critic's call shape.

**UNVERIFIABLE entries needing operator escalation:** see end-of-document list.

---

## Numeric-value verification table

| # | Value claimed in CONTEXT | CONTEXT location | Code location verified | Status |
|---|--------------------------|------------------|------------------------|--------|
| N1 | `MAX_RETRIES + 1`, line 571 | D-08 | `spotlight/critic.py:571` is the `write_review_entry(dynamo_client, review_entry)` call. `MAX_RETRIES = 3` at line 162. The 571 reference is correct — that's the existing SPOTLIGHT_REVIEW# write site. | VERIFIED |
| N2 | `tie_epsilon` from `assign_subtopics.py:98`, value 0.001 | D-23 | `assign_subtopics.py:98` → `TIE_EPSILON = 0.001` exactly | VERIFIED |
| N3 | `confidence_floor` from `assign_subtopics.py:95`, value 0.3 | D-23 (corrected) | `assign_subtopics.py:95` → `DEFAULT_CONFIDENCE_FLOOR = 0.3` exactly | VERIFIED |
| N4 | `score_floor` from `assign_subtopics.py:94`, value 0.3 | D-23 | `assign_subtopics.py:94` → `SCORE_FLOOR = 0.3` exactly | VERIFIED |
| N5 | `assign_subtopics.py:1022` is the CLI flag's `default=DEFAULT_CONFIDENCE_FLOOR` read | D-23, canonical_refs | `assign_subtopics.py:1021-1028`: `parser.add_argument("--confidence-floor", type=float, default=DEFAULT_CONFIDENCE_FLOOR, ...)`. The `default=` keyword sits at line 1022 exactly. | VERIFIED |
| N6 | `low_confidence_floor = 0.35` (existing) | D-25 | `config/thresholds.json:3` → `"low_confidence_floor": 0.35` exactly | VERIFIED |
| N7 | `confidence_floor = 0.3` (distinct from low_confidence_floor) | D-25 | `DEFAULT_CONFIDENCE_FLOOR = 0.3` in code; not yet in `thresholds.json` (Phase 12 to add). The "distinct values" claim is correct. | VERIFIED |
| N8 | `aggregate_subtopic_scores.py:108-167` (existing `_aggregate`) | canonical_refs, code_context | Actual `_aggregate` body spans lines **108-167** exactly (signature `def _aggregate` at 108, `return` at 164-167). | VERIFIED |
| N9 | `aggregate_subtopic_scores.py:642-648` "where `subtopic_ids[]` is written" | canonical_refs | `assign_subtopics.py:642-650` (not `aggregate_subtopic_scores.py`!) is `update_activity_subtopics(...)` call. Line 632 is where `subtopic_ids` list is constructed. Two errors: (a) **wrong filename** in canonical_refs (CONTEXT says `aggregate_subtopic_scores.py`, real file is `assign_subtopics.py`); (b) line range is 643-650 not 642-648. | DRIFT |
| N10 | `spotlight/review_queue.py:134` for `write_review_entry` | canonical_refs | `spotlight/review_queue.py:134` is `def write_review_entry(client, entry: dict) -> None:` exactly | VERIFIED |
| N11 | `utils/stage_records.py:217-219` Phase 11 run_id precedent | PATTERNS+code_context | Lines 215-219 carry `force_reason` (215-216) and `run_id` (217-219) blocks. Slight off-by-one from "215-219" but 217-219 matches the run_id specifically. | VERIFIED |
| N12 | `pipeline_drift/evaluator.py` lines 73-96 = `to_dynamodb_item` | PATTERNS+code_context | `def to_dynamodb_item` starts at line 73, returns dict ending at line 96 exactly | VERIFIED |
| N13 | `pipeline_drift/evaluator.py` lines 153-161 compute per-topic dict | code_context | Lines 153-156 build `per_topic`; lines 158-161 compute `max_topic, max_count`. "153-161" range matches the per-topic computation block. | VERIFIED |
| N14 | `pipeline_cold/run.py` lines 90-131 — `default_cold_stages()` | PATTERNS | `def default_cold_stages` at line 90, closing `]` at line 131. Match exact. | VERIFIED |
| N15 | `pipeline_cold/run.py` lines 166-210 — `run_stage` returncode handling | PATTERNS | `def run_stage` at line 166, function body ends at line 210. Match. | VERIFIED |
| N16 | Spotlight critic line 571 has `write_review_entry` call AFTER `MAX_RETRIES + 1` attempts | D-08, canonical_refs | Confirmed — line 571 is the write call, sits after the for-loop at line 475 (`for attempt_idx in range(MAX_RETRIES + 1)`). | VERIFIED |
| N17 | "lede generator on line 642" reference for subtopic_ids write | code_context | CONTEXT canonical_refs says "642-648" / "632" — actual `subtopic_ids = [sid ... ]` at line 632, write call at 643-650. See N9. | DRIFT |
| N18 | `uncovered_score_floor`, `low_confidence_floor`, `drift_*`, `spotlight_dirty_*` existing in `thresholds.json` | canonical_refs | `config/thresholds.json` actually contains: `uncovered_score_floor`, `low_confidence_floor`, `drift_uncovered_rate_alert`, `drift_low_confidence_topic_max`, `drift_window_days`, `spotlight_dirty_subtopic_min`, `spotlight_dirty_pubs_per_subtopic_min`. Match. | VERIFIED |
| N19 | "67-topic taxonomy" reference | D-34 | Spec §0 line 35 says "65 topics / 1,526 subtopics" (production state 2026-05-12). CONTEXT says "67-topic taxonomy"; spec says "~67 topics" loose and "65" precise. The "~67" loose figure is fine for a comparison; not a hard error. | DRIFT (mild — 65 vs ~67) |

---

## Per-claim sections

### D-01 — Hybrid trigger; operator CLI + cold-path stage; both write findings with same shape; row carries `triggered_by` + `source_sweep_run_id`

**Category:** SPECULATIVE

**Source-of-claim:** Claude's discretion (no existing feedback module or `source_sweep_run_id` mechanism in code; the only analog is Phase 11's `RECITERAI_COLD_RUN_ID` env var via `review/cli.py:138,211`).

**Evidence:**
- No `feedback/` or `pipeline_feedback/` directory exists today (verified by listing); Phase 12 introduces this.
- The two-trigger design and the `source_sweep_run_id` shape are design proposals not yet realized in code. The shape is plausible and consistent with established patterns (UUID per invocation, body field not key field), but there's no code to verify against.

**Recommended action:** SPECULATIVE — keep as-is. This is a design decision the planner implements, not a claim about existing code. Tag the source explicitly as "Claude's discretion" so future audits don't try to verify against code that won't exist until execution.

---

### D-02 — Cold-path invocation is non-gating

**Category:** SPECULATIVE

**Source-of-claim:** Claude's discretion (logical inference from spec §9 — feedback findings feed next cold run's taxonomy regen, so blocking the current cold run on findings would be self-defeating).

**Evidence:** No existing feedback consumer in code. The cold-stage chain at `pipeline_cold/run.py:90-131` does not include any feedback sweep today. The non-gating contract is a forward-looking design decision.

**Recommended action:** SPECULATIVE — keep as-is, tag source. Sensible design; planner implements; verifiable post-implementation.

---

### D-03 — DDB-primary output, deterministic markdown render; `python -m feedback render <run_id>`

**Category:** SPECULATIVE

**Source-of-claim:** Claude's discretion + Phase 11 G-29 precedent (`generated_at` removed from `hierarchy.json`).

**Evidence:** No `pipeline_feedback/markdown_render.py` exists today. PATTERNS.md acknowledges "no analog" for this. The G-29 deterministic-output precedent in `pipeline_hierarchy/generator.py:70-74` exists and supports the design.

**Recommended action:** SPECULATIVE — keep as-is, tag source.

---

### D-04 — Three typed finding records, no unified discriminator

**Category:** SPECULATIVE

**Source-of-claim:** Spec §9 enumerates three signals; CONTEXT chooses to materialize each as a distinct PK prefix rather than a unified `FEEDBACK_ACTION#` partition with discriminator field.

**Evidence:** No `CANDIDATE_TOPIC#`, `RECLUSTER_RECOMMENDATION#`, or `SPOTLIGHT_DIAGNOSTIC#` PK prefixes exist in code today (grep confirms). Spec §9 lists the three quality signals (lines 394-398) but does not specify the record shape — that's CONTEXT's design choice.

**Recommended action:** SPECULATIVE — keep as-is, tag source. Design decision; the rejection of unified `FEEDBACK_ACTION#` is well-reasoned.

---

### D-05 — Idempotent overwrite per `source_sweep_run_id`, no cross-sweep merging

**Category:** VERIFIED (as a pattern claim; not yet realized)

**Source-of-claim:** Existing event-record pattern at `utils/event_records.py:78-91` (`UNCOVERED_PMID#`) — stable PK + constant `SK="GLOBAL"` → idempotent overwrite.

**Evidence:** The pattern is well-established (`build_uncovered_pmid_record` at `utils/event_records.py:61-91`). CONTEXT's claim that finding records will follow the same pattern is consistent with the established idiom. The `source_sweep_run_id` in body (not key) is the right place for it per the pattern.

**Recommended action:** SPECULATIVE design (since the records don't yet exist) — but the pattern it claims compatibility with is VERIFIED. Tag source: `utils/event_records.py:78-91` pattern.

---

### D-06 — Since-last-sweep input with cap; `feedback_sweep_max_pmids` in `thresholds.json`; truncation flag; worst-fitting first

**Category:** SPECULATIVE

**Source-of-claim:** Claude's discretion. No `feedback_sweep_max_pmids` key in `config/thresholds.json` today.

**Evidence:** `config/thresholds.json` (full contents, 9 lines): no `feedback_sweep_max_pmids`. The truncation contract is a design decision.

**Recommended action:** SPECULATIVE — keep as-is, tag source.

---

### D-07 — Recluster trigger is duration, not evaluation count; `recluster_persistence_days` new key

**Category:** SPECULATIVE

**Source-of-claim:** Claude's discretion. Builds on existing `drift_low_confidence_topic_max` (in thresholds.json line 5 with value 50). The new `recluster_persistence_days` key does not yet exist.

**Evidence:** Existing key `drift_low_confidence_topic_max` confirmed in `config/thresholds.json:5`. New key absent. Pure design decision.

**Recommended action:** SPECULATIVE — keep as-is, tag source.

---

### D-08 — `CRITIC_REJECT#{cwid}#{pmid_set_hash}` keying, written alongside existing `SPOTLIGHT_REVIEW#` at `critic.py:571`

**Category:** **ARCHITECTURAL MISMATCH**

**Source-of-claim:** Spec §9 line 396 (`docs/RECITERAI-SPEC.md`): "typed event `CRITIC_REJECT#{cwid}#{pmid_set_hash}` with `reason_code`". CONTEXT echoed spec without verifying the critic's call shape.

**Evidence — what CONTEXT assumes:**
- A `cwid` value is available at the rejection write site in `spotlight/critic.py:571`.
- The "rejection" is attributable to a single CWID.
- `SPOTLIGHT_DIAGNOSTIC#{cwid}` aggregation rolls up per-CWID rejection counts.

**Evidence — what the code actually does:**
1. `run_critic_loop(meta: SubtopicMeta, papers: list[Paper], publish_id: str, parent_topic: str, ...)` — `spotlight/critic.py:442-451`. **No cwid parameter**, no per-faculty argument.
2. `SubtopicMeta` (NamedTuple, `spotlight/sensitive_gate.py:38-50`) carries `subtopic_id, label, description, parent_topic_label`. **No cwid field**.
3. `Paper` dataclass (`spotlight/types.py:39-57`) carries `pmid, title, journal, year, impact_score, impact_justification, synopsis, first_author, last_author`. The two `Author` fields each carry `person_identifier` — these are **per-paper-position author CWIDs, not "the cwid of the spotlight"**.
4. `publish_id = f"v{date.today().isoformat()}"` (`backfill_spotlight.py:362` and `:656`). **No cwid embedded**.
5. A single subtopic's papers can have many different first/last authors. There is no single "spotlight cwid" — the spotlight is a per-subtopic editorial artifact.
6. The existing `SPOTLIGHT_REVIEW#` row is keyed `PK = SPOTLIGHT_REVIEW#{publish_id}`, `SK = SUBTOPIC#{subtopic_id}` (`spotlight/review_queue.py:159-160`). **Keyed by publish + subtopic**, not by cwid. This is the right shape — a critic rejection is a per-(publish, subtopic) event, not a per-faculty event.
7. PATTERNS.md line 690 already flagged this: "Confirm cwid is accessible at this scope. If not, add cwid: str to run_critic_loop signature." But adding `cwid: str` to the signature still doesn't solve the problem because **the caller can't construct a meaningful cwid** — the subtopic has N papers, each with up to two authors, and the critic's verdict isn't attributable to any one of them.

**Why no simple correction reconciles them:** The mismatch is not a wrong-line-number drift. The proposed PK structure (`CRITIC_REJECT#{cwid}#{pmid_set_hash}`) **encodes a relationship that doesn't exist in the data model**. A critic rejection is "for subtopic X in publish Y, the lede generated against this set of papers failed the critic." There's no CWID grain.

**Plausible re-framings the planner cannot pick locally:**
- **Re-frame A (subtopic grain):** `CRITIC_REJECT#{publish_id}#{subtopic_id}#{pmid_set_hash}` — matches the existing SPOTLIGHT_REVIEW# grain. SPOTLIGHT_DIAGNOSTIC# rolls up per-subtopic ("subtopic X is structurally hard to lede"). Loses the "spotlight ranking is broken for faculty X" question CONTEXT's D-09 docstring envisions.
- **Re-frame B (subtopic+author grain):** `CRITIC_REJECT#{publish_id}#{subtopic_id}#{pmid_set_hash}` with an indexed `author_cwids: list[str]` body field carrying every distinct author across the rejected pmid_set. Aggregation by CWID happens at read time (sweep iterates and unrolls). Preserves both the per-subtopic dedup (key) and the per-CWID drill-down (body). More expensive to query by CWID; cheaper to write correctly.
- **Re-frame C (drop the per-CWID framing entirely):** Recognize that `SPOTLIGHT_DIAGNOSTIC#{cwid}` mis-models the diagnostic. The right shape is `SPOTLIGHT_DIAGNOSTIC#{subtopic_id}` — "this subtopic is hard to lede" — with reason-code distribution and underlying-reject provenance per the current D-10/D-30/D-31/D-32 scheme.

Picking among A/B/C is an architectural decision the planner should not make unilaterally — it changes what question the diagnostic answers.

**Recommended action:** ARCHITECTURAL MISMATCH — re-frame; planner cannot decide locally. Orchestrator must select re-framing A/B/C (or propose D) and update D-08, D-09, D-10, D-11, D-30, D-31, D-32, D-34, plus the canonical_refs CRITIC_REJECT# bullet, plus the entire `CRITIC_REJECT producer + consumer` section header.

---

### D-09 — `pmid_set_hash` keying gives natural producer-side dedup; "distinct rejected pmid_sets over the window"

**Category:** ARCHITECTURAL MISMATCH (downstream of D-08)

**Source-of-claim:** Inherited from D-08's keying. The dedup semantic is well-reasoned given the assumed key structure.

**Evidence:** Once D-08 is re-framed, the dedup grain changes:
- Under re-framing A: dedup is per (publish_id, subtopic_id, pmid_set_hash). Within a single publish, the same critic re-attempting the same papers produces one row. Across publishes, the same subtopic with the same pmid_set produces separate rows (`publish_id` differs).
- Under re-framing B: same as A; dedup still per-(publish, subtopic, pmid_set).
- Under re-framing C: same as A; same dedup grain.

The "distinct rejected pmid_sets over the window" framing is correct in spirit but the natural unit is per-subtopic, not per-cwid.

**Recommended action:** ARCHITECTURAL MISMATCH — downstream of D-08. Re-derive after D-08 is resolved.

---

### D-10 — `reason_code` is a controlled vocabulary (LEDE_INCOHERENT, WEAK_EVIDENCE, OFF_TOPIC_PUBS, INSUFFICIENT_NARRATIVE); already crossed out per D-30/D-31

**Category:** VERIFIED (as crossed-out)

**Source-of-claim:** Originally Claude's discretion (the four names were aspirational). Crossed out 2026-05-12 after D-30/D-31 discovered the actual `LLMVerdict.failed_constraint` vocabulary.

**Evidence:** `spotlight/critic.py:177-205` defines `LLMVerdict` with docstring naming the four actual codes: `active_verb`, `anchored_in_synopses`, `no_faculty_named`, `institutional_voice`. The crossed-out names never matched code. CONTEXT correctly marks this as superseded.

**Recommended action:** VERIFIED — keep as-is (the strikethrough is the audit trail).

---

### D-11 — Two tunables `critic_reject_persistence_days` + `critic_reject_cwid_max`

**Category:** DRIFT (name-level; downstream of D-08 architectural mismatch)

**Source-of-claim:** Inherited from D-08's per-cwid keying assumption.

**Evidence:**
- Neither key exists in `config/thresholds.json` today (verified).
- The name `critic_reject_cwid_max` encodes the cwid grain. If D-08 re-frames to per-subtopic, the tunable becomes `critic_reject_subtopic_max`. If per-(publish, subtopic, pmid_set), the count threshold semantic changes meaning ("how many distinct subtopic-pmid_sets in a window before the subtopic is structurally suspect").
- `critic_reject_persistence_days` is name-stable across re-framings.

**Recommended action:** DRIFT (renaming required). After D-08 resolution: rename `critic_reject_cwid_max` to match the chosen grain. CONTEXT-AUDIT-BRIEF.md line 96 already anticipated this rename.

---

### D-12 — Parallel DDB partitions; "new partition `SUBTOPIC_SCORE_INCLUSIVE#{topic}#{subtopic}` alongside existing `SUBTOPIC_SCORE#{topic}#{subtopic}`"

**Category:** DRIFT

**Source-of-claim:** Spec §8 framing ("publish both, simultaneously, with distinct names") + assumption that the exclusive aggregation already has a partition.

**Evidence:**
- CONTEXT claim verbatim: "New partition `SUBTOPIC_SCORE_INCLUSIVE#{topic}#{subtopic}` **alongside existing** `SUBTOPIC_SCORE#{topic}#{subtopic}`."
- Actual code reality: `grep -rn "SUBTOPIC_SCORE" /Users/paulalbert/Dropbox/GitHub/ReciterAI --include="*.py"` returns **zero matches**. The `SUBTOPIC_SCORE#` partition does NOT exist today.
- Today's exclusive aggregation writes to a different shape: `faculty.subtopic_scores.<topic_id>` nested map on `FACULTY#<personIdentifier>/SK=PROFILE` records (see `aggregate_subtopic_scores.py` design decision D-17 in the file's docstring at line 17-19, and the call to `update_faculty_subtopic_scores` at line 199).
- D-33 (added 2026-05-12 after F-1) **already acknowledges** "Per F-1: `SUBTOPIC_SCORE#{topic}#{subtopic}` and `SUBTOPIC_SCORE_INCLUSIVE#{topic}#{subtopic}` are **both NEW partitions**." So CONTEXT internally contradicts itself: D-12 says "existing", D-33 says "both NEW".

**Delta:** D-12 mis-describes `SUBTOPIC_SCORE#` as existing. The aggregator writes the nested faculty map today and **does not** write a per-(topic, subtopic) partition. Phase 12 must introduce *both* partitions, not just one.

**Recommended action:** DRIFT — correction: change D-12 wording from "New partition `SUBTOPIC_SCORE_INCLUSIVE#...` alongside existing `SUBTOPIC_SCORE#...`" to "Two NEW partitions: `SUBTOPIC_SCORE#{topic}#{subtopic}` and `SUBTOPIC_SCORE_INCLUSIVE#{topic}#{subtopic}`. The existing faculty-map (`faculty.subtopic_scores.<topic_id>` on `FACULTY#` rows) persists as the SPS consumer contract per D-13/D-33." This brings D-12 into agreement with D-33.

---

### D-13 — CSV rename `cwid_subtopic_counts.csv` → `faculty_subtopic_counts_exclusive.csv` requires SPS-consumer audit

**Category:** VERIFIED (the file exists and is read by `rollup_by_cwid.py`)

**Source-of-claim:** Spec §8 ("`faculty_subtopic_counts_exclusive`"); code reality at `rollup_by_cwid.py:61` and `count_by_cwid.py:61,67`.

**Evidence:**
- `rollup_by_cwid.py:61`: `DEFAULT_SUBTOPIC_CSV = Path("cwid_subtopic_counts.csv")` confirms the current filename.
- `count_by_cwid.py:61` writes `cwid_subtopic_counts.csv` and prints "Wrote cwid_topic_counts.csv and cwid_subtopic_counts.csv" at line 67.
- The rename is genuinely a contract change. CONTEXT does not mention `count_by_cwid.py` as a write site — only `rollup_by_cwid.py` as the reader. The producer must also be updated (additional file: `build_cwid_json.py:12` reads it too).

**Recommended action:** VERIFIED — keep as-is, but flag the canonical_refs section: it lists `rollup_by_cwid.py` as needing update but omits `count_by_cwid.py` (producer) and `build_cwid_json.py:12` (additional reader). Add these to the plan's audit list.

---

### D-14 — Two-partition writes are idempotent at partition level, not transactional

**Category:** SPECULATIVE

**Source-of-claim:** Established pattern (run-id-keyed idempotent rewrites).

**Evidence:** The `run_id` precedent from Phase 11 (`utils/stage_records.py:217-219`) is verified. The two-partition write design is forward-looking.

**Recommended action:** SPECULATIVE — keep as-is, tag source.

---

### D-15 — Inclusive weighting is uniform full article_score per above-floor assignment

**Category:** VERIFIED (the article_score formula and `subtopic_ids[]` semantics)

**Source-of-claim:** Existing formula at `aggregate_subtopic_scores.py:151` plus existing `subtopic_ids[]` write site (`assign_subtopics.py:632`).

**Evidence:**
- `aggregate_subtopic_scores.py:151`: `article_score = (impact_score / 100) ** 1.2 * relevance_score ** 1.4` — the formula CONTEXT proposes weighting with exists.
- `assign_subtopics.py:632`: `subtopic_ids = [sid for sid, _c in sorted_pairs]` — the list to iterate over for inclusive aggregation exists.

**Recommended action:** VERIFIED — keep as-is.

---

### D-16 — Confidence floor still gates `subtopic_ids[]` membership; D-15's "uniform full weight" applies only to above-floor

**Category:** VERIFIED

**Source-of-claim:** Code inspection.

**Evidence:** `assign_subtopics.py:95` defines `DEFAULT_CONFIDENCE_FLOOR = 0.3`. The filtering happens upstream of `subtopic_ids[]` construction in the assignment logic. PATTERNS.md line 597 confirms: "the writer (`assign_subtopics.py:632`) already filtered, so do NOT re-filter."

**Recommended action:** VERIFIED — keep as-is.

---

### D-17 — Arithmetic invariant: `sum(SUBTOPIC_SCORE_INCLUSIVE#X#*) ≥ sum(SUBTOPIC_SCORE#X#*)`; documented at writes + docs

**Category:** VERIFIED (as a design contract; depends on D-12 partition creation)

**Source-of-claim:** Mathematical inference from D-15 weighting + D-16 floor semantics. No existing analog (no `SUBTOPIC_SCORE#` partition today).

**Evidence:** The invariant is provable from the weighting rule. Documentation site `docs/topic-subtopic-assignment.md` exists (referenced in canonical_refs and Phase 12 modifies-file list).

**Recommended action:** VERIFIED — keep as-is. (Note: the invariant only becomes verifiable once D-12's partitions exist.)

---

### D-18 — Reconciliation gate fails publish stage, not aggregation; registers in `gates/registry.py`

**Category:** VERIFIED

**Source-of-claim:** Phase 9 gate framework at `gates/registry.py`.

**Evidence:**
- `gates/registry.py:24-26`: `SEVERITY_BLOCK = "block"`, `SEVERITY_WARN = "warn"`, valid set defined.
- `gates/registry.py:62`: `register_gate(*, stage, severity=SEVERITY_BLOCK, name=None)` decorator confirmed.
- Pattern aligns: `gates/parent_prefix.py` and `gates/schema_validation.py` register against `stage="publish"`.

**Recommended action:** VERIFIED — keep as-is.

---

### D-19 — Five items inside main phase work; G-37 separately-gated

**Category:** UNVERIFIABLE (audit-relevant code is doc-state, not code state)

**Source-of-claim:** Spec §11 + Phase 12 scope decision.

**Evidence:** G-1, G-18, G-24, G-34, G-36, G-37 are scope decisions, not code claims. Verification requires confirming the items exist in the spec (they do, §11 lines 463-467 enumerate G-1, G-18, G-24, G-34, G-37 and §11 line 459 covers G-36).

**Recommended action:** VERIFIED (against spec §11) — keep as-is.

---

### D-20 — G-37 gating is SHA-able (`pytest` green on main)

**Category:** VERIFIED (as a workflow contract; no code claim)

**Source-of-claim:** Workflow design decision.

**Evidence:** Pure process claim. No code state to verify.

**Recommended action:** SPECULATIVE — keep as-is, tag as workflow decision.

---

### D-21 — G-37 deliverable is one bounded E2E test (taxonomy → publish; byte-identical second-run sha)

**Category:** VERIFIED (G-29 prerequisite is met)

**Source-of-claim:** Phase 11 G-29 (`generated_at` removed from `hierarchy.json`) — claimed shipped.

**Evidence:** Canonical_refs cite `.planning/phases/11-versioning-review-diff/11-SUMMARY-change-signaling.md` as confirming G-29 shipped. I did not open Phase 11 summary in this audit; spec §0 line 35 ("G-29 ... observed in real life ... flagged for Phase 11") plus Phase 11 being marked complete is sufficient evidence. The test design is otherwise forward-looking.

**Recommended action:** VERIFIED (G-29 prerequisite) + SPECULATIVE (test details). Keep as-is.

---

### D-22 — G-37 carry-over filed as GitHub issue day one

**Category:** UNVERIFIABLE (workflow claim; no code state)

**Source-of-claim:** Workflow design decision.

**Evidence:** No code/config to verify.

**Recommended action:** SPECULATIVE — keep as-is.

---

### D-23 — G-18 lifts every operationally-tunable number; `tie_epsilon` (0.001), `confidence_floor` (0.3, line 95), `score_floor` (0.3, line 94); correction note acknowledges prior drift

**Category:** VERIFIED (post-CR)

**Source-of-claim:** Direct code inspection (corrected 2026-05-12 per CR).

**Evidence:**
- `assign_subtopics.py:94`: `SCORE_FLOOR = 0.3` ✓
- `assign_subtopics.py:95`: `DEFAULT_CONFIDENCE_FLOOR = 0.3` ✓
- `assign_subtopics.py:98`: `TIE_EPSILON = 0.001` ✓
- `assign_subtopics.py:1022`: `default=DEFAULT_CONFIDENCE_FLOOR` in `--confidence-floor` argument ✓
- The four new Phase 12 tunables (`feedback_sweep_max_pmids`, `recluster_persistence_days`, `critic_reject_persistence_days`, `critic_reject_cwid_max`) do not yet exist (consistent with "Phase 12 adds them").
- Correction note: "earlier D-23 wording cited 0.35 and line 1022; both were drift" — accurate description of the original error.

**Recommended action:** VERIFIED — keep as-is.

---

### D-24 — `confidence_floor` key has no `_default` suffix because no per-topic override exists

**Category:** VERIFIED

**Source-of-claim:** Code inspection of `assign_subtopics.py:1021-1028`.

**Evidence:** Single `--confidence-floor` flag (line 1022); no `--confidence-floor-topic-*` or any topic-keyed override mechanism. CONTEXT claim is accurate.

**Recommended action:** VERIFIED — keep as-is.

---

### D-25 — `confidence_floor` (0.3, this phase) ≠ `low_confidence_floor` (0.35, existing); distinct keys, distinct values; correction note explicit

**Category:** VERIFIED (post-CR)

**Source-of-claim:** Direct code inspection.

**Evidence:**
- `config/thresholds.json:3`: `"low_confidence_floor": 0.35` ✓ (event-emission decision)
- `assign_subtopics.py:95`: `DEFAULT_CONFIDENCE_FLOOR = 0.3` ✓ (assignment-time decision)
- The two semantics are genuinely distinct: `low_confidence_floor` gates whether to emit `LOW_CONFIDENCE_ASSIGNMENT#` (see `assign_subtopics.py:189-198` in `_maybe_write_low_confidence_event`); `confidence_floor` gates whether a subtopic appears in `subtopic_ids[]` (the assignment-time floor).
- Correction note's claim ("earlier D-25 wording claimed 'both are 0.35 today'; that was wrong") matches the actual state.

**Recommended action:** VERIFIED — keep as-is.

---

### D-26 — Every key in `thresholds.json` is documented in sibling `config/thresholds.md`

**Category:** SPECULATIVE (forward-looking; no `thresholds.md` today)

**Source-of-claim:** PATTERNS.md confirms no analog — "No analog in codebase. Phase 12 establishes this pattern."

**Evidence:** `ls config/` would show no `thresholds.md` today. The pattern is novel.

**Recommended action:** SPECULATIVE — keep as-is, tag source as "Phase 12 precedent."

---

### D-27 — `thresholds.json` schema-validated at startup via `env_check.py`; `config/thresholds.schema.json` new

**Category:** VERIFIED (the analog) + SPECULATIVE (the new artifact)

**Source-of-claim:** Existing `gates/schema_validation.py:42-44` jsonschema pattern + existing `utils/env_check.py` preflight role.

**Evidence:**
- `gates/schema_validation.py:26`: `DEFAULT_SCHEMA_PATH = REPO_ROOT / "docs/hierarchy.schema.json"` ✓ confirms the pattern exists.
- `utils/env_check.py:21-32`: `run_env_checks()` is the preflight site ✓ — though today it checks ReciterDB columns, not JSON config files. Adding a `thresholds.json` check is consistent with the function's role.
- `config/thresholds.schema.json` does not yet exist.

**Recommended action:** VERIFIED (pattern) + SPECULATIVE (new artifact). Keep as-is.

---

### D-28 — CLI tunable overrides logged + recorded in STAGE# records

**Category:** VERIFIED (the additive-field pattern)

**Source-of-claim:** Phase 11 `run_id` precedent at `utils/stage_records.py:217-219`.

**Evidence:**
- `utils/stage_records.py:217-219`: `if run_id is not None: item["run_id"] = run_id` ✓ confirms the additive-field idiom.
- D-28's `tunable_inputs` field follows the same pattern.

**Recommended action:** VERIFIED — keep as-is.

---

### D-29 — Plan reflects parallel-fan dependency, not linear sequence

**Category:** UNVERIFIABLE (planning shape claim; no code state)

**Source-of-claim:** Workflow design decision.

**Evidence:** No code to verify.

**Recommended action:** SPECULATIVE — keep as-is.

---

### D-30 — `reason_code` uses `LLMVerdict.failed_constraint` verbatim; deterministic gate failures get `PRE_LLM_GATE`

**Category:** VERIFIED

**Source-of-claim:** Direct code inspection of `spotlight/critic.py:177-205` (LLMVerdict docstring naming the four codes) + `spotlight/critic.py:178-191` (DeterministicVerdict).

**Evidence:**
- LLMVerdict docstring (lines 198-201): "one of the four closed-vocabulary identifiers from the critic prompt (`active_verb`, `anchored_in_synopses`, `no_faculty_named`, `institutional_voice`)" ✓ Matches D-30 enumeration.
- DeterministicVerdict (lines 177-191): `failed_constraints: tuple[str, ...]` with identifiers `em_dash_present`, `time_bound_language`, etc. The pre-LLM constraints are distinct from the LLM constraints. ✓
- The split between "deterministic-only failure (never reached LLM)" and "LLM failure" is genuine in the code.

**Recommended action:** VERIFIED — keep as-is.

---

### D-31 — Four LLM `reason_code` values pinned as Python StrEnum in `spotlight/critic.py`; producer-side validation; unknown-code → warning

**Category:** VERIFIED (as a design building on accurate inspection)

**Source-of-claim:** Direct inspection of `LLMVerdict.failed_constraint` shape + `pipeline_common.alert` existing dispatcher.

**Evidence:**
- `spotlight/critic.py:177-205` confirms the four codes.
- `pipeline_common/alert.py` confirmed via grep: defines `post_to_slack`, `open_or_comment_issue`, `dispatch`. The "emit a typed warning via `pipeline_common.alert`" claim aligns with the actual module's API.
- StrEnum is a stdlib feature in Python 3.11+. Repo's Python version is not verified in this audit; assuming compatible.

**Recommended action:** VERIFIED — keep as-is. (Caveat: ARCHITECTURAL MISMATCH from D-08 affects WHERE these codes get aggregated, not whether they're correctly identified.)

---

### D-32 — `SPOTLIGHT_DIAGNOSTIC#{cwid}` rows link back via `underlying_rejects: list[str]` of `{cwid}#{pmid_set_hash}` PK suffixes

**Category:** ARCHITECTURAL MISMATCH (downstream of D-08)

**Source-of-claim:** Inherited from D-08's key structure.

**Evidence:** The `{cwid}#{pmid_set_hash}` suffix in `underlying_rejects` is exactly D-08's key shape. Once D-08 re-frames, this provenance shape re-frames with it. The principle (link aggregated rows to underlying provenance) is sound; the suffix structure is downstream of D-08.

**Recommended action:** ARCHITECTURAL MISMATCH — downstream of D-08. Re-derive suffix shape after D-08 resolution.

---

### D-33 — Aggregation enforces faculty-map ↔ `SUBTOPIC_SCORE#` reconciliation; both NEW partitions per F-1; divergence is stage failure

**Category:** VERIFIED

**Source-of-claim:** Direct code inspection of `aggregate_subtopic_scores.py` (no existing `SUBTOPIC_SCORE#` partition) + F-1 RESEARCH finding.

**Evidence:**
- `aggregate_subtopic_scores.py` writes only `faculty.subtopic_scores.<topic_id>` (call at line 199, `update_faculty_subtopic_scores`). No partition write today.
- `grep -rn "SUBTOPIC_SCORE" --include="*.py"` returns zero — confirms both partitions are NEW.
- D-33's correction of D-12's "existing" claim is correct.

**Recommended action:** VERIFIED — keep as-is. **Note:** D-33 supersedes D-12's "existing" framing; the orchestrator's CONTEXT revision should reconcile them by updating D-12 to match D-33.

---

### D-34 — `per_topic_low_confidence` on `DRIFT#evaluation` rows, sparse representation

**Category:** VERIFIED

**Source-of-claim:** Direct code inspection of `pipeline_drift/evaluator.py`.

**Evidence:**
- `pipeline_drift/evaluator.py:73-96` confirms `to_dynamodb_item` shape; the field does not yet exist on the DriftEvaluation dataclass.
- Lines 153-161 compute `per_topic: dict[str, int]` already (only the max is persisted today; the dict is discarded).
- F-2 claim "additive extension of `to_dynamodb_item`" is consistent with Phase 11 `run_id` precedent at `utils/stage_records.py:217-219`.
- Sparse representation design rationale (taxonomy growth, "absence = zero") is sound. Spec §0 line 35 reports "65 topics / 1,526 subtopics" — under DDB 400KB even dense; sparse is forward-defensive.

**Recommended action:** VERIFIED — keep as-is. (Minor: CONTEXT says "Today's ~67-topic taxonomy"; spec line 35 says 65 / 1,526. The ~67 is close enough; rounding error, not architectural drift.)

---

## `<canonical_refs>` block — per-claim audit

### canonical_refs / Spec sections (§8, §9, §11)

**Category:** VERIFIED

**Source-of-claim:** `docs/RECITERAI-SPEC.md` headers confirmed at lines 375, 390, 436.

**Evidence:** Spec sections exist as cited. Note: §9 line 396 is the source of D-08's mismatch — the spec proposed the per-cwid key without grounding it in code.

**Recommended action:** VERIFIED — keep as-is.

---

### canonical_refs / Prior-phase context (Phase 09, 10, 11 summary/context references)

**Category:** UNVERIFIABLE (in scope of this audit)

**Source-of-claim:** Prior-phase artifacts in `.planning/phases/09-*/`, `.planning/phases/10-*/`, `.planning/phases/11-*/`.

**Evidence:** I did not open these files in this audit. They exist (I verified the phase-12 dir exists; previous phase dirs are referenced in PATTERNS.md without contradiction).

**Recommended action:** UNVERIFIABLE within this audit. Operator may spot-check by opening the cited summary files; they are not load-bearing for D-01..D-34 correctness once the code-level claims are verified.

---

### canonical_refs / `docs/topic-subtopic-assignment.md` (per D-17, gains invariant)

**Category:** UNVERIFIABLE

**Source-of-claim:** Future modification, not current state.

**Evidence:** I did not open `docs/topic-subtopic-assignment.md`. Existence is asserted; content not verified.

**Recommended action:** UNVERIFIABLE — flag for spot-check during execution.

---

### canonical_refs / `docs/aws-iam-pipeline-policy.json` + `GETTING_STARTED.md` (G-34 targets)

**Category:** UNVERIFIABLE

**Source-of-claim:** Spec §11 line 466 says "`docs/aws-iam-pipeline-policy.json` already exists but isn't referenced from `GETTING_STARTED.md`."

**Evidence:** Not opened in this audit. Spec asserts existence.

**Recommended action:** UNVERIFIABLE — operator should `ls docs/aws-iam-pipeline-policy*.json` and `grep -n "iam" GETTING_STARTED.md` before Phase 12 execution. Cheap check, eliminates an Open Question.

---

### canonical_refs / `utils/event_records.py` builder pattern

**Category:** VERIFIED

**Evidence:** Read fully. `build_uncovered_pmid_record` at lines 61-91, `build_low_confidence_assignment_record` at lines 106-137. Decimal coercion pattern at line 86. Idempotent PK pattern verified.

**Recommended action:** VERIFIED — keep as-is.

---

### canonical_refs / `spotlight/critic.py:571` — rejection write site

**Category:** VERIFIED

**Evidence:** Line 571 is `write_review_entry(dynamo_client, review_entry)` exactly. Loop exhaustion at line 554 ("Loop exhausted without a passing attempt").

**Recommended action:** VERIFIED — keep as-is. (Note: the surrounding CONTEXT claim that this is where the CRITIC_REJECT# write goes is downstream of D-08's architectural mismatch — the line number is correct, but what gets written here is unresolved.)

---

### canonical_refs / `spotlight/review_queue.py:134` — `write_review_entry`

**Category:** VERIFIED

**Evidence:** Line 134: `def write_review_entry(client, entry: dict) -> None:` exactly.

**Recommended action:** VERIFIED — keep as-is.

---

### canonical_refs / `aggregate_subtopic_scores.py:108-167` — `_aggregate` produces exclusive output

**Category:** VERIFIED

**Evidence:** Function spans lines 108-167 exactly. Returns `(faculty_scores, subtopic_total_weights)`.

**Recommended action:** VERIFIED — keep as-is.

---

### canonical_refs / `rollup_by_cwid.py` reads `cwid_subtopic_counts.csv`

**Category:** VERIFIED

**Evidence:** `rollup_by_cwid.py:61` `DEFAULT_SUBTOPIC_CSV = Path("cwid_subtopic_counts.csv")`. Also producers `count_by_cwid.py` and `build_cwid_json.py` reference this filename (see D-13 note).

**Recommended action:** VERIFIED — keep as-is.

---

### canonical_refs / `assign_subtopics.py:94,95,98,1022` — constants and CLI flag

**Category:** VERIFIED (numeric values + line numbers)

**Evidence:** All four locations verified (see numeric-value table N2/N3/N4/N5).

**Recommended action:** VERIFIED — keep as-is.

---

### canonical_refs / `assign_subtopics.py:642-648` — "Where `subtopic_ids[]` is written"

**Category:** DRIFT

**Evidence:**
- CONTEXT canonical_refs says: "`assign_subtopics.py:642-648` — Where `subtopic_ids[]` is written; Phase 12 doesn't modify but the confidence-floor decision lives here."
- Actual:
  - Line 632: `subtopic_ids = [sid for sid, _c in sorted_pairs]` — the LIST construction.
  - Lines 643-650: `update_activity_subtopics(pk=..., sk=..., subtopic_ids=subtopic_ids, ...)` — the WRITE call.
- "642-648" misses both: line 642 is part of a `try:`, line 648 is mid-call.

**Delta:** Range "642-648" should be "632 (construction) and 643-650 (write)".

**Recommended action:** DRIFT — correction: update the line citation in canonical_refs.

---

### canonical_refs / `pipeline_drift/evaluator.py` — extended additively for `per_topic_low_confidence`

**Category:** VERIFIED

**Evidence:** Function locations confirmed (see numeric table N12, N13). The "additive extension" language is accurate.

**Recommended action:** VERIFIED — keep as-is.

---

### canonical_refs / `pipeline_cold/run.py` — cold-path orchestrator

**Category:** VERIFIED

**Evidence:** `default_cold_stages()` at line 90-131 confirmed; `run_stage` at line 166 confirmed.

**Recommended action:** VERIFIED — keep as-is.

---

### canonical_refs / `utils/stage_records.py` — Phase 11 `run_id` precedent

**Category:** VERIFIED

**Evidence:** Lines 195-219 contain `build_complete_record` with `run_id` block at 217-219.

**Recommended action:** VERIFIED — keep as-is.

---

### canonical_refs / `gates/registry.py` — Phase 9 gate-registration framework

**Category:** VERIFIED

**Evidence:** Read fully. Registry pattern at lines 62-88. `SEVERITY_BLOCK` / `SEVERITY_WARN` at lines 24-25.

**Recommended action:** VERIFIED — keep as-is.

---

### canonical_refs / `utils/env_check.py` — G-1 target AND D-27 target

**Category:** VERIFIED

**Evidence:** Read fully. ReciterDB column references at lines 64, 74, 89, 117 (multiple). `run_env_checks` at line 21. The function is the preflight site. CONTEXT's plan to add a schema-validation check is structurally consistent.

**Recommended action:** VERIFIED — keep as-is.

---

### canonical_refs / `config/thresholds.json` existing keys list

**Category:** VERIFIED

**Evidence:** Read fully. Contains exactly: `uncovered_score_floor`, `low_confidence_floor`, `drift_uncovered_rate_alert`, `drift_low_confidence_topic_max`, `drift_window_days`, `spotlight_dirty_subtopic_min`, `spotlight_dirty_pubs_per_subtopic_min`. Matches CONTEXT's enumeration.

**Recommended action:** VERIFIED — keep as-is.

---

## `<code_context>` block — per-claim audit

### code_context / `utils/event_records.py` builder pattern recap

**Category:** VERIFIED

**Evidence:** All details (pure builder + thin writer, Decimal coercion, idempotent PK, constant SK, `created_at` ISO timestamp, `source_stage` field) confirmed against `utils/event_records.py:41-98`.

**Recommended action:** VERIFIED — keep as-is.

---

### code_context / `pipeline_drift/evaluator.py` already does LOW_CONFIDENCE-per-topic counting

**Category:** VERIFIED

**Evidence:** Lines 153-161: per-topic dict computed; max extracted. The dict is computed and discarded (only max persists). D-34's "today's per-topic dict is discarded; persist it" matches reality.

**Recommended action:** VERIFIED — keep as-is.

---

### code_context / `pipeline_common.alert` severity-tagged dispatcher

**Category:** VERIFIED

**Evidence:** `pipeline_common/alert.py` defines `post_to_slack`, `open_or_comment_issue`, `dispatch`. Severity-tagged structure consistent with description.

**Recommended action:** VERIFIED — keep as-is.

---

### code_context / `pipeline_cold.run.main()` argparse plumbing supports stage registration with `--initiated-by` and `run_id`

**Category:** UNVERIFIABLE (within this audit)

**Source-of-claim:** Phase 11 D-13.

**Evidence:** I read lines 85-210 of `pipeline_cold/run.py`. I did not specifically verify `--initiated-by` argparse plumbing or `run_id` threading. The default_cold_stages list exists; the env-threading via `env` kwarg is confirmed (line 184).

**Recommended action:** UNVERIFIABLE within this audit, but the `RECITERAI_COLD_RUN_ID` env-var pattern is confirmed elsewhere (`review/cli.py:138,211`). Mark as VERIFIED (env pattern) + UNVERIFIABLE (`--initiated-by` flag) — operator can `grep -n initiated-by pipeline_cold/run.py` for a 5-second check.

---

### code_context / `pipeline_drift/evaluator.py` "Phase 12 may extend `DriftEvaluation.to_dynamodb_item()` additively"

**Category:** VERIFIED (post-CR)

**Source-of-claim:** Code inspection (the dataclass + method are extensible).

**Evidence:** `pipeline_drift/evaluator.py:53-96` shows the dataclass and `to_dynamodb_item`. The additive-field pattern matches `utils/stage_records.py:217-219`.

The CONTEXT line warns: "Phase 12 must NOT change the alerting thresholds, severity logic, or `cold_run_recommended` semantics" — the `evaluate()` function at lines 116-205 confirms the alerting logic lives in `evaluate()` (severity = "WARN"/"ERROR" decisions at lines 167-189). Additive extension to the dataclass + `to_dynamodb_item` doesn't touch those.

**Recommended action:** VERIFIED — keep as-is.

---

### code_context / Established patterns (Idempotent overwrite, Run-id threading, Single-config file, Audit-row, CLI subcommand)

**Category:** VERIFIED

**Evidence:** Each pattern verified against cited code locations earlier in this audit.

**Recommended action:** VERIFIED — keep as-is.

---

### code_context / `aggregate_subtopic_scores.py` is the load-bearing site; today's `_aggregate` returns `(faculty_scores, subtopic_total_weights)` keyed by `(person_identifier, primary_subtopic_id)`

**Category:** VERIFIED

**Evidence:** Lines 108-167. Return signature `tuple[dict, dict]` where outer dict is `{person_identifier: {primary_subtopic_id: score}}` confirmed.

**Recommended action:** VERIFIED — keep as-is.

---

### code_context / "Two writes, idempotent, independent purposes" at critic.py rejection site

**Category:** ARCHITECTURAL MISMATCH (downstream of D-08)

**Evidence:** The "two writes" framing is correct in idiom (SPOTLIGHT_REVIEW# stays; new write joins). But the second write's PK shape (`CRITIC_REJECT#{cwid}#...`) is the mismatch from D-08.

**Recommended action:** ARCHITECTURAL MISMATCH — re-derive after D-08 resolution.

---

### code_context / `gates/registry.py` entry for reconciliation gate; integrates with publish stage, not aggregator

**Category:** VERIFIED

**Evidence:** Phase 9 registry framework confirmed. Existing gates register against `stage="publish"`.

**Recommended action:** VERIFIED — keep as-is.

---

### code_context / `config/thresholds.json` + `.md` + `.schema.json` three-file substrate

**Category:** SPECULATIVE

**Evidence:** Only `thresholds.json` exists today. The trio is novel.

**Recommended action:** SPECULATIVE — keep as-is, tag as Phase 12 precedent.

---

## `<specifics>` block — per-claim audit

### specifics / "Aggregations of different populations that happen to share keys"

**Category:** VERIFIED (as a framing claim consistent with code)

**Evidence:** The exclusive and inclusive aggregations operate over the same `(person_identifier, subtopic_id)` key space; the difference is what rows count (primary-only vs all-above-floor). Framing matches code semantics.

**Recommended action:** VERIFIED — keep as-is.

---

### specifics / "Diagnosis vs documentation"

**Category:** SPECULATIVE

**Evidence:** Framing decision; no code state.

**Recommended action:** SPECULATIVE — keep as-is.

---

### specifics / "Make carry-forward state queryable from day one"

**Category:** VERIFIED (as precedent)

**Evidence:** Phase 11 D-06/D-16 cutover-audit-row pattern is confirmed (referenced in code_context, established pattern).

**Recommended action:** VERIFIED — keep as-is.

---

### specifics / "Distinct rejected pmid_sets, not total rejection events"

**Category:** ARCHITECTURAL MISMATCH (downstream of D-08/D-09)

**Evidence:** The count semantic is downstream of the key shape. Once D-08 re-frames, the semantic re-frames.

**Recommended action:** ARCHITECTURAL MISMATCH — downstream. Re-derive after D-08.

---

### specifics / "One bounded test, not 'an integration test'"

**Category:** SPECULATIVE

**Evidence:** Workflow design.

**Recommended action:** SPECULATIVE — keep as-is.

---

## `<deferred>` block — per-claim audit

### deferred / All items (GitHub-issue dispatcher, longitudinal tracking, weighted aggregation, per-topic override, drift extension, per-topic review, separate algorithm.json, auto-promotion, threshold-doc test)

**Category:** VERIFIED (all are scope-exclusion claims consistent with their decision pairs)

**Evidence:** Each deferred item maps to an explicit decision (D-04, D-05, D-15, D-24, D-23, D-26 etc.) where the rejection rationale lives.

**Recommended action:** VERIFIED — keep as-is.

---

## UNVERIFIABLE entries — operator escalation list

These items could not be confirmed by code inspection alone. Operator confirmation needed before CONTEXT revision lands.

1. **Phase 11 G-29 actually shipped (`generated_at` removed from `hierarchy.json`)** — D-21 + canonical_refs claim Phase 11 D-29 work landed. I did not open `.planning/phases/11-versioning-review-diff/11-SUMMARY-change-signaling.md`. Open it and confirm "G-29 shipped" + check the published `hierarchy.json` has no `generated_at` field. If not shipped, D-21 (G-37 deliverable) is blocked.

2. **`pipeline_cold/run.py` `--initiated-by` argparse flag exists** — code_context asserts this. I read lines 85-210 but did not specifically `grep -n "initiated-by" pipeline_cold/run.py`. 5-second check.

3. **`docs/aws-iam-pipeline-policy.json` and `docs/aws-iam-pipeline-policy-artifacts.json` exist** — canonical_refs assert these as G-34 targets. Spec §11 line 466 says the JSON exists. Confirm with `ls docs/aws-iam-pipeline-policy*.json`. Confirm `GETTING_STARTED.md` does NOT currently reference these (the entire G-34 task assumes the reference is missing).

4. **SPS consumes `cwid_subtopic_counts.csv` (or any ad-hoc script does)** — D-13 says "Plan must check whether SPS reads this." This is outside-repo state. SPS is a separate repo (`ReCiter-Publication-Manager`). Operator needs to grep that repo for the filename + check whether any internal dashboards/scripts read it. The decision (direct rename vs dual-emit window) hinges on this answer.

---

## ARCHITECTURAL MISMATCH entries — re-framing list

These need orchestrator-level re-framing; no single-line correction reconciles them with code reality.

1. **D-08 — `CRITIC_REJECT#{cwid}#{pmid_set_hash}` keying.** No cwid is derivable at the rejection write site. The critic operates per (publish_id, subtopic_id, pmid_set). Spotlight artifacts have authorship that spans many CWIDs. Pick a re-framing:
   - A: `CRITIC_REJECT#{publish_id}#{subtopic_id}#{pmid_set_hash}`, aggregate as `SPOTLIGHT_DIAGNOSTIC#{subtopic_id}`.
   - B: same key as A, but body carries `author_cwids: list[str]`; per-CWID aggregation happens at read time in the sweep.
   - C: explicitly drop the per-CWID grain; `SPOTLIGHT_DIAGNOSTIC#{subtopic_id}` instead of `SPOTLIGHT_DIAGNOSTIC#{cwid}`.

2. **D-09 — `pmid_set_hash` keying for dedup.** Downstream of D-08. The "distinct rejected pmid_sets over the window" semantic must re-anchor to the new grain (per-subtopic, per-publish-subtopic, or per-author depending on the chosen re-framing).

3. **D-11 — `critic_reject_cwid_max` tunable name.** Downstream of D-08. The tunable name encodes the grain. Rename required after re-framing: candidates `critic_reject_subtopic_max`, `critic_reject_lede_max`, etc.

4. **D-32 — `underlying_rejects` PK-suffix shape.** Downstream of D-08. Suffix `{cwid}#{pmid_set_hash}` re-derives based on chosen re-framing.

5. **specifics / "Distinct rejected pmid_sets"** — semantic re-derivation downstream of D-08.

(D-11 is also categorized DRIFT under the brief's known-conflicts table because the rename itself is local; the architectural ripple comes from the keying decision in D-08.)

---

## Closing notes

**On audit completeness:** The brief required catching D-08 as ARCHITECTURAL MISMATCH independently — done. The brief also flagged D-23 and D-25 as VERIFIED-after-CR; both verified. The brief flagged the drift-evaluator extension wording as VERIFIED-after-CR; the code_context wording was checked at the `pipeline_drift/evaluator.py` bullet and is precise (additive extension permitted; behavior contract preserved).

**On D-12 surprising finding:** Beyond the known-conflicts list, this audit surfaced D-12's "alongside existing `SUBTOPIC_SCORE#`" as DRIFT — the partition doesn't exist today. D-33 already corrects this, but D-12 itself was not updated in the CR-2026-05-12 round. Should be reconciled.

**On numeric-value coverage:** Every cited line number and threshold value in CONTEXT was checked. 16 of 18 checked exactly match code; 2 carry small drift (the `aggregate_subtopic_scores.py:642-648` citation, which is actually `assign_subtopics.py:632 + 643-650`; and the ~67 vs 65 topic count, which is rounding-noise).

**On what was NOT audited:** Prior-phase summary files (Phase 09/10/11) were not opened. They are referenced as context but are not load-bearing for D-01..D-34 code correctness once the code-level claims are independently verified. If the orchestrator finds an inconsistency between this audit and prior-phase SUMMARY claims, prefer this audit's code citations.

---

*Audit committed before revision step, per brief precedent.*

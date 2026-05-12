---
phase: 12
slug: feedback-loops-both-aggregations-residual-hygiene
status: ready
nyquist_compliant: true
wave_0_complete: false
created: 2026-05-12
---

# Phase 12 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

Sourced from RESEARCH.md §Validation Architecture; cross-references CONTEXT.md D-01..D-34.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (verified via existing `tests/test_*.py`; no `pyproject.toml`/`pytest.ini` — pytest uses defaults) |
| **Config file** | None at repo root |
| **Quick run command** | `pytest tests/test_event_records.py tests/test_gates_registry.py tests/test_feedback_sweep.py -x` |
| **Full suite command** | `pytest tests/ -x` |
| **Estimated runtime** | Quick ~5–10s; full <60s today (Wave 0 additions may push to ~90s) |

---

## Sampling Rate

- **After every task commit:** Run quick command above.
- **After every plan wave:** Run full suite command.
- **Before `/gsd-verify-work`:** Full suite must be green.
- **Max feedback latency:** <90s end-to-end.

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 12-thresholds-substrate-T1 | thresholds-substrate | 1 | G-18 / D-23 / D-24 / D-25 / D-26 | — | confidence_floor=0.3, low_confidence_floor=0.35 (distinct keys, distinct values) | unit | `pytest tests/test_thresholds_keys.py -x` | ❌ W0 | ⬜ pending |
| 12-thresholds-substrate-T2 | thresholds-substrate | 1 | G-1 / D-27 | — | env_check.py rejects malformed thresholds.json at startup | unit | `pytest tests/test_thresholds_schema.py -x` | ❌ W0 | ⬜ pending |
| 12-thresholds-substrate-T3 | thresholds-substrate | 1 | D-28 / D-22 | — | STAGE# row carries tunable_inputs when overrides given; G-37 tracking issue exists | unit + filecheck | `pytest tests/test_stage_records.py::test_tunable_inputs_in_row && test -f .planning/issues/0002-g37-e2e-test.md` | ❌ W0 (test_stage_records.py exists; extend it) | ⬜ pending |
| 12-feedback-producer-T1 | feedback-producer | 1 | §9 / D-08 / D-09 / D-31 | — | CritReasonCode StrEnum pinned with 5 values; build_critic_reject_record idempotent on (cwid_or_subtopic, pmid_set_hash) | unit | `pytest tests/test_critic_reject_record.py -x` | ❌ W0 | ⬜ pending |
| 12-feedback-producer-T2 | feedback-producer | 1 | §9 / D-08 / D-30 | — | Existing SPOTLIGHT_REVIEW# write unchanged; CRITIC_REJECT# added; deterministic-gate failures emit PRE_LLM_GATE reason_code with raw constraint preserved | unit | `pytest tests/test_critic_reject_producer.py -x` (includes `test_review_queue_write_unchanged` and `test_passing_critic_does_not_write_critic_reject`) | ❌ W0 | ⬜ pending |
| 12-drift-extension-T1 | drift-extension | 1 | F-2 / D-34 | — | DriftEvaluation.to_dynamodb_item gains sparse per_topic_low_confidence dict; existing alerting semantics unchanged | unit | `pytest tests/test_drift_evaluator.py::test_per_topic_low_confidence_sparse` | ❌ W0 (extend existing test file) | ⬜ pending |
| 12-aggregations-T1 | aggregations | 1 | §8 / D-12 / D-14 / D-15 / D-16 / D-17 / D-33 | — | _aggregate_exclusive + _aggregate_inclusive both write to new partitions; D-17 invariant holds; D-33 reconciliation passes; divergence injected at put_item boundary fails the stage | unit | `pytest tests/test_aggregate_inclusive.py tests/test_aggregate_idempotent.py -x` | ❌ W0 | ⬜ pending |
| 12-aggregations-T2 | aggregations | 1 | §8 / D-18 | — | Reconciliation gate registered on publish stage; fails publish (not aggregation) on invariant violation | unit | `pytest tests/test_reconciliation_gate.py -x` | ❌ W0 | ⬜ pending |
| 12-aggregations-T3 | aggregations | 1 | §8 / D-13 | — | Dual-name CSV emission (cwid_subtopic_counts.csv + faculty_subtopic_counts_exclusive.csv) for deprecation cycle; rollup_by_cwid.py reads new name | unit | `pytest tests/test_csv_dual_name.py -x` | ❌ W0 | ⬜ pending |
| 12-residual-docs-T1 | residual-docs | 1 | G-24 | — | Sensitive-topic doc documents DDB-resident pattern + SPOT-08 fail-closed invariant; does NOT leak patterns | filecheck | `grep -E 'SPOTLIGHT_CONFIG#sensitive_tags\|fail-closed\|SPOT-08' docs/sensitive-topic-exclusion.md \| wc -l` (must be ≥3) | ❌ W0 (new doc) | ⬜ pending |
| 12-residual-docs-T2 | residual-docs | 1 | G-34 | — | GETTING_STARTED.md gains IAM section referencing both policy JSONs | filecheck | `grep -c -E '## IAM\|aws-iam-pipeline-policy' GETTING_STARTED.md` (must be ≥3) | ❌ W0 | ⬜ pending |
| 12-feedback-consumer-T1 | feedback-consumer | 2 | §9 / D-04 / D-05 / D-32 | — | Three finding-record builders (CANDIDATE_TOPIC#, RECLUSTER_RECOMMENDATION#, SPOTLIGHT_DIAGNOSTIC#); SPOTLIGHT_DIAGNOSTIC# carries underlying_rejects list bounded by feedback_diagnostic_max_underlying | unit | `pytest tests/test_feedback_finding_records.py -x` | ❌ W0 | ⬜ pending |
| 12-feedback-consumer-T2 | feedback-consumer | 2 | §9 / D-06 / D-07 / D-09 | — | Sweep cap → truncated=true + total_unprocessed_remaining; worst-fitting-first ordering; D-07 persistence-window recluster trigger reads sparse per_topic_low_confidence dict; D-09 distinct-pmid_set counting | unit | `pytest tests/test_feedback_sweep.py -x` | ❌ W0 | ⬜ pending |
| 12-feedback-consumer-T3 | feedback-consumer | 2 | §9 / D-01 / D-02 / D-03 | — | python -m pipeline_feedback sweep + render subcommands; cold-stage registers between rollup and backfill_spotlight (non-gating); deterministic markdown render (byte-identical same row in / same bytes out) | unit + integration | `pytest tests/test_feedback_render.py tests/test_feedback_cli.py -x && python -c "from pipeline_cold.run import default_cold_stages; names=[s.name for s in default_cold_stages()]; assert 'feedback_sweep' in names and names.index('rollup') < names.index('feedback_sweep') < names.index('backfill_spotlight')"` | ❌ W0 | ⬜ pending |
| 12-g36-reproducibility-T1 | g36-reproducibility | 2 | G-36 | — | Existing `tests/test_hierarchy_reproducibility.py` hardened; coverage extended through `publish()` end-to-end; byte-identical on second run | unit | `pytest tests/test_hierarchy_reproducibility.py -x` | ✅ exists; extend | ⬜ pending |
| 12-g37-e2e-T1 | g37-e2e | 3 | G-37 / D-20 | — | SHA-able gating: pytest exits 0 for every other Phase 12 test on git rev-parse main before this task starts | gate | `git rev-parse main && pytest tests/test_thresholds_schema.py tests/test_critic_reject_record.py tests/test_critic_reject_producer.py tests/test_drift_evaluator.py tests/test_aggregate_inclusive.py tests/test_reconciliation_gate.py tests/test_csv_dual_name.py tests/test_feedback_finding_records.py tests/test_feedback_sweep.py tests/test_feedback_render.py tests/test_feedback_cli.py tests/test_hierarchy_reproducibility.py -x` | gate | ⬜ pending |
| 12-g37-e2e-T2 | g37-e2e | 3 | G-37 / D-21 | — | Stripped-down fixture corpus created under tests/fixtures/cold_path_e2e/; documented in fixture README | filecheck | `test -d tests/fixtures/cold_path_e2e && test -f tests/fixtures/cold_path_e2e/README.md` | ❌ W0 | ⬜ pending |
| 12-g37-e2e-T3 | g37-e2e | 3 | G-37 / D-21 | — | One bounded E2E test: corpus through cold path generate_taxonomy.py → pipeline_hierarchy/publish.py; published manifest validates against schema; second run produces byte-identical hierarchy.json | integration | `pytest tests/test_cold_path_e2e.py -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

New test files (must be created before or alongside the implementing task):

- [ ] `tests/test_thresholds_keys.py` — keys-present + distinct-values + key-coverage of thresholds.md
- [ ] `tests/test_thresholds_schema.py` — env_check.py rejects malformed thresholds.json
- [ ] `tests/test_critic_reject_record.py` — builder + idempotency tests for CRITIC_REJECT#
- [ ] `tests/test_critic_reject_producer.py` — call-site dual-write + PRE_LLM_GATE handling
- [ ] `tests/test_aggregate_inclusive.py` — D-15 uniform-weight + D-16 floor-respected + D-17 invariant
- [ ] `tests/test_aggregate_idempotent.py` — re-run with same run_id produces byte-identical writes
- [ ] `tests/test_reconciliation_gate.py` — gate registered, fails publish on D-17 invariant violation
- [ ] `tests/test_csv_dual_name.py` — both CSV names emitted for deprecation cycle
- [ ] `tests/test_feedback_finding_records.py` — builders for the three finding-record types
- [ ] `tests/test_feedback_sweep.py` — cap, worst-fitting-first, recluster trigger, distinct-pmid_set counting
- [ ] `tests/test_feedback_render.py` — byte-identical markdown rendering (no generated_at in body)
- [ ] `tests/test_feedback_cli.py` — sweep + render subcommands; non-gating cold-stage behavior
- [ ] `tests/test_cold_path_e2e.py` — G-37 bounded E2E
- [ ] `tests/fixtures/cold_path_e2e/` (directory + README + corpus files)

Test file extensions to existing files:

- [ ] `tests/test_drift_evaluator.py` — add `test_per_topic_low_confidence_sparse` (file already exists)
- [ ] `tests/test_stage_records.py` — add `test_tunable_inputs_in_row` (file already exists)
- [ ] `tests/test_hierarchy_reproducibility.py` — harden + extend through publish() (file already exists)

Wave 0 framework install: **none required** (pytest verified present).

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Documentation prose quality (G-24, G-34) | G-24 / G-34 | No automated way to verify a doc clearly communicates context; grep-presence is necessary but not sufficient | Reviewer reads `docs/sensitive-topic-exclusion.md` and the new IAM section in `GETTING_STARTED.md` end-to-end; verifies SPOT-08 invariant explanation is operator-actionable; verifies IAM section names both policy JSONs and links them. |
| D-13 SPS-consumer audit | §8 / D-13 | Cannot grep external SPS repo from this workspace | Operator pings SPS team to confirm whether `cwid_subtopic_counts.csv` has any downstream readers. Result recorded in Phase 12 SUMMARY. (Plan-aggregations Task 3 ships dual-name emission as the safe-path default while audit is pending.) |
| G-37 fixture corpus size and shape | G-37 / D-21 | Subjective — "stripped-down" is a judgment about coverage-vs-runtime tradeoff | Reviewer of g37-e2e Task 2 PR confirms fixture corpus is small enough to run end-to-end in <60s but large enough to exercise every cold-path stage. README documents the choice. |
| Operator-facing markdown render readability (D-03) | §9 / D-03 | Byte-identity is testable; whether the output is *useful* to an operator is not | Operator runs `python -m pipeline_feedback render <sweep_run_id>` against a real sweep's findings and confirms the markdown is actionable. |

---

## Nyquist Compliance Checks

- **Check 8a (every task has automated verify):** ✅ all 18 production tasks above carry a command. Two docs-only tasks use grep-presence checks; rationale recorded in residual-docs plan.
- **Check 8c (≥2 of 3 consecutive impl tasks have automated tests):** ✅ wave 1 has 11 tasks all with `pytest` verification except residual-docs-T1/T2 (grep). No two consecutive impl tasks lack tests.
- **Check 8e (Wave 0 test files identified before implementation):** ✅ list above is the authoritative Wave 0 set.
- **Sampling continuity:** every task commit triggers quick command; every wave merge triggers full suite. Latency target <90s satisfied.

---

## Cross-Reference

- RESEARCH.md §Validation Architecture lines 779–818
- CONTEXT.md decisions D-01..D-34 (final post-CR-2026-05-12)
- PATTERNS.md — analog file mappings for the 14 new test files
- `.planning/issues/0001-confidence-floor-target.md` — out-of-scope tracking
- `.planning/issues/0002-g37-e2e-test.md` — to be created by thresholds-substrate Task 3 (D-22)

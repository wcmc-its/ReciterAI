---
phase: "12"
plan: "g37-e2e"
subsystem: "testing"
tags: [e2e, integration, residual-hygiene, fixture-corpus, g37, d20, d21, d22]
dependency_graph:
  requires:
    - 12-thresholds-substrate
    - 12-feedback-producer
    - 12-drift-extension
    - 12-aggregations
    - 12-residual-docs
    - 12-feedback-consumer
    - 12-g36-reproducibility
  provides:
    - "G-37: one bounded E2E test for the cold-path artifact production chain"
    - "D-20 SHA-able gate audit row persisted in test module docstring"
    - "Issue 0002-g37-e2e-test.md closed-by-phase-12"
  affects:
    - "tests/test_cold_path_e2e.py"
    - "tests/fixtures/cold_path_corpus/"
tech_stack:
  added:
    - "tests/fixtures/cold_path_corpus/ — 2-topic × 3-subtopic × 5-pub fixture corpus"
  patterns:
    - "MagicMock DDB table (Option A isolation — cheapest, no moto/DDB-Local required)"
    - "_S3PutCapture stub (reused from test_hierarchy_reproducibility.py)"
    - "Synthetic SCORE# rows bypass score_publications + assign_subtopics Bedrock/RDS deps"
    - "Pinned datetime via _FixedDatetime subclass for deterministic publish timestamps"
key_files:
  created:
    - "tests/test_cold_path_e2e.py"
    - "tests/fixtures/cold_path_corpus/README.md"
    - "tests/fixtures/cold_path_corpus/taxonomy.json"
    - "tests/fixtures/cold_path_corpus/pubs.json"
  modified:
    - ".planning/issues/0002-g37-e2e-test.md (status: open → closed-by-phase-12)"
decisions:
  - "Option A isolation: MagicMock DDB tables (not moto/DDB-Local); matches existing test patterns"
  - "Synthetic SCORE# rows bypass score_publications (Bedrock/RDS) + assign_subtopics (Bedrock/DDB); the aggregate → publish chain is the load-bearing surface to cover"
  - "display_name prefixed with 'Research:' to neutralize parent_prefix gate on synthetic topic IDs"
  - "D-20 gate audit row embedded in test module docstring for persistent SHA-able record"
metrics:
  duration: "~47 minutes"
  completed: "2026-05-13T02:13:00Z"
  tasks_completed: 3
  tasks_total: 3
  files_created: 5
  files_modified: 1
---

# Phase 12 Plan g37-e2e: G-37 Bounded Cold-Path E2E Test Summary

**One-liner:** One bounded E2E test running the cold-path aggregate→publish chain with a 2-topic×3-subtopic×5-pub MagicMock fixture corpus, asserting schema validity + byte-identical reruns + dual-partition writes + D-33 reconciliation.

## D-20 Gate Audit Row (SHA-able gate precondition)

```
D-20 gate audit row: sha=6509595daab2b09e5a7e012d69e8c077a902a658 exit=0
```

186 D-20 gating tests passed on main at the above SHA before G-37 work began. The audit row is also persisted verbatim in the test module docstring per D-20's SHA-able gate requirement.

## What Was Built

### Task 2: Fixture Corpus (commit 1e3ee42)

Three files under `tests/fixtures/cold_path_corpus/`:

- `README.md` — documents corpus shape, why generate_taxonomy.py + assign_subtopics are bypassed, how to update the corpus, synthetic PMID convention
- `taxonomy.json` — 2 topics (`test_topic_alpha`, `test_topic_beta`) × 3 subtopics each, schema-compatible with `taxonomy_v2.json` (id, label, description, subtopics with display_name + short_description)
- `pubs.json` — 30 publications (5 per subtopic), each with synthetic PMID (`TEST_PMID_001`..`TEST_PMID_030`), title, abstract, mesh terms, and `intended_subtopic` field for test-builder reference

### Task 3: One Bounded E2E Test (commit ba51486)

`tests/test_cold_path_e2e.py` — 4 test functions:

1. **`test_cold_path_corpus_through_publish`** — full chain: build SCORE# rows → `_aggregate_exclusive` + `_aggregate_inclusive` → `_write_subtopic_score_partitions` (MagicMock table) → `_assert_d33_reconciliation` → `publish.main()` (mocked S3 + DDB). Asserts `hierarchy.json` validates against `docs/hierarchy.schema.json`; `manifest.json` has `sha256` + `generated_at`; `diff.json` has `diff_schema_version`.

2. **`test_cold_path_byte_identical_second_run`** — runs `publish.main()` twice with different pinned timestamps; asserts `hierarchy.json` bytes are identical (G-36/G-29/D-14 chain extended end-to-end through aggregate → publish).

3. **`test_cold_path_emits_both_subtopic_partitions`** — asserts that `_write_subtopic_score_partitions` emits `SUBTOPIC_SCORE#` AND `SUBTOPIC_SCORE_INCLUSIVE#` PKs for all 6 fixture subtopics across 2 topics (Phase 12 §8 D-17 both-aggregations integration).

4. **`test_cold_path_d33_reconciliation_passes`** — happy path: `_assert_d33_reconciliation` does not raise on aligned data; D-17 invariant (inclusive ≥ exclusive per subtopic) holds for all fixture subtopics.

## Chain Exercised

```
cold_path_corpus/pubs.json
    ↓  (synthetic SCORE# rows — bypasses score_publications + assign_subtopics)
aggregate_subtopic_scores._aggregate_exclusive
aggregate_subtopic_scores._aggregate_inclusive
    ↓
aggregate_subtopic_scores._write_subtopic_score_partitions (MagicMock DDB)
    → SUBTOPIC_SCORE#{topic}#{subtopic}
    → SUBTOPIC_SCORE_INCLUSIVE#{topic}#{subtopic}
    ↓
aggregate_subtopic_scores._assert_d33_reconciliation
    ↓
pipeline_hierarchy.publish.main() (mocked bundle + S3 + DDB)
    → hierarchy.json  ─→ validates against docs/hierarchy.schema.json
    → manifest.json   ─→ has sha256 + generated_at
    → diff.json       ─→ has diff_schema_version
```

Stages bypassed (Bedrock/RDS/DDB dependencies not available in default test run):
- `generate_taxonomy.py` — non-deterministic Bedrock LLM call; fixture taxonomy is INPUT
- `score_publications.py` — requires RDS + Bedrock; SCORE# rows built synthetically
- `assign_subtopics.py` — requires DDB + Bedrock; primary/subtopic assignment encoded in synthetic rows

## Test Results

```
PYTHONPATH=. pytest tests/test_cold_path_e2e.py -x -v
4 passed in 0.25s
```

Full Phase 12 test suite (all 17 D-20 gating files + new E2E file):
```
190 passed in 0.61s
```

Total test count: 508 (pre-wave-3) + 4 (this plan) = **512 passing**

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] parent_prefix gate blocked publish in test**
- **Found during:** Task 3, first test run
- **Issue:** Fixture subtopic `display_name` values like "Alpha Cell Biology" start with "Alpha", which is in the parent token set for `test_topic_alpha`. The gate read the live `taxonomy_v2.json` for known topics; synthetic topic IDs fall back to the ID string ("test_topic_alpha"), producing parent tokens {"test", "topic", "alpha"}.
- **Fix:** Changed `_minimal_sub()` helper to prefix display_name with "Research: " (e.g., "Research: Subtopic One") so the first word is always neutral and never matches parent tokens.
- **Files modified:** `tests/test_cold_path_e2e.py` (only — no production code changed)
- **Commit:** ba51486

## Tracking Issue Closure

`.planning/issues/0002-g37-e2e-test.md` updated from `status: open` to `status: closed-by-phase-12` per plan acceptance criteria and D-22 carry-forward contract.

## Self-Check: PASSED

| Item | Status |
|------|--------|
| `tests/test_cold_path_e2e.py` | FOUND |
| `tests/fixtures/cold_path_corpus/README.md` | FOUND |
| `tests/fixtures/cold_path_corpus/taxonomy.json` | FOUND |
| `tests/fixtures/cold_path_corpus/pubs.json` | FOUND |
| Commit 1e3ee42 (fixture corpus) | FOUND |
| Commit ba51486 (E2E test + issue closure) | FOUND |
| No accidental deletions | CONFIRMED |
| 4 new tests pass | CONFIRMED (`pytest tests/test_cold_path_e2e.py` → 4 passed) |
| 190 D-20 gating tests still pass | CONFIRMED |

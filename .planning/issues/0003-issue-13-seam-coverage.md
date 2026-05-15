---
issue: 0003
github_issue: 13
title: Issue #13 — E2E seam coverage (fixture-chained, not Bedrock-mocked)
status: planning
filed: 2026-05-15
filed_by: planning for GitHub issue #13 follow-on to G-37
related_phases: [12, 13]
branch: test/13-cold-path-seam-coverage
framing: carry-forward from Phase 12 (not a new phase)
---

# Issue #13 — E2E Seam Coverage

## Framing

Issue #13 asks for an end-to-end integration test across the canonical cold-path stages. The literal reading is "import each stage, mock Bedrock and AWS, run them in sequence." That defends against prompt-output schema drift but is large (~1500–2500 LOC of harness) and re-mocks paths the stage-level tests already cover.

The actual integration debt that hit production — Phase 11 UAT-3, six pre-existing defects all fixed on 2026-05-13 — was orchestration-glue, not prompt drift:

1. `assign` stage missing `--skip-all-reviews` flag (Phase 10 omission)
2. `discover` / `relabel` stages referenced non-existent CLI flags (Phase 10 dead scaffolding)
3. `count_by_cwid` stage missing between `assign` and `rollup` (Phase 12 D-13 omission)
4. `pipeline_feedback.sweep` hardcoded invalid Bedrock model id
5. `backfill_topic` review-gate rejected auto_approved drafts (Phase 4 legacy)
6. `assign_subtopics` PEP-562 `__getattr__` NameError on bare-name access

Every one of these would have been caught by a fixture-chained seam test that (a) validates each stage's command line against its script's argparser and (b) feeds each stage the prior stage's output shape and asserts the next stage doesn't choke.

This plan defends that failure class. It does NOT defend prompt-output schema drift inside individual stages (that's stage-test territory — `test_score_publications_stage.py`, `test_assign_subtopics_stage.py` already cover it).

## Honest limit

If a Bedrock prompt is edited and the LLM now emits a slightly different JSON shape, the per-stage tests catch that but this E2E suite would not — because the E2E suite uses the captured fixture as input, not a live Bedrock call. That's the accepted tradeoff. Filed forward as a known gap, not a future feature.

## Scope: two new tests

### Test A — `tests/test_cold_run_command_lines_integration.py`

For each `ColdStage` in `default_cold_stages()`:

1. Resolve the target script/module the command points to.
2. Import its `argparse.ArgumentParser` (either via a `_build_parser()` helper or by intercepting `parser.parse_args` to capture the parser).
3. Run the parser over the stage's `command[2:]` (everything past `python -m foo` or `python foo.py`).
4. Assert it parses cleanly with no `SystemExit`.

Catches Phase 11 UAT-3 bugs #1, #2 (missing flag, non-existent flag).

If a script doesn't expose its parser cleanly, surface as a TODO in the test docstring and skip with reason — don't paper over it.

### Test B — `tests/test_cold_path_seams_integration.py`

In-process chain. Each stage runs as a function call (not subprocess), reads either:
- a fixture file (when stage's true input is upstream-only)
- the prior stage's real in-memory output (when the seam is what we're testing)

Stage list (matches `default_cold_stages()`):

| Stage | Input source | What we assert at the seam |
|---|---|---|
| score | `cold_path_corpus/pubs.json` + mocked Bedrock returning a canned score response | SCORE# rows produced have the fields `assign` reads |
| assign | SCORE# rows from `score` + fixture taxonomy + mocked Bedrock | per-PMID subtopic_ids match taxonomy.subtopic.id values; hierarchy_draft_*.json files have the schema `relabel` reads |
| discover | (no-op in current cold-run; assert it returns 0 and produces nothing `count` depends on) | — |
| relabel | hierarchy_draft from `assign` + mocked Bedrock returning canned display_name/short_description | hierarchy_augmented_*.json has the fields `publish_hierarchy.bundler` requires |
| count | SCORE# rows from `assign` (TOPIC# activity rows) | CSVs have header + per-(cwid, subtopic) rows in the shape `rollup` reads |
| rollup | CSVs from `count` | TOPIC# rollup rows have the shape `backfill_spotlight` ranker reads |
| feedback_sweep | UNCOVERED/LOW_CONFIDENCE/CRITIC_REJECT inputs (fixture) + mocked Bedrock | non-gating (returns 0 even with findings); diagnostic rows have expected PK shape |
| backfill_spotlight | one faculty from `rollup` + mocked Bedrock returning canned lede | SPOTLIGHT# row has the fields `publish_hierarchy` doesn't need but downstream SPS does (validated against `tests/fixtures/spotlight_valid_min.json` schema) |
| publish_hierarchy | hierarchy_augmented_* from `relabel` | hierarchy.json validates against `docs/hierarchy.schema.json` (already covered by `test_cold_path_e2e.py` — assert via composition, don't re-implement) |

Bedrock invocations: replaced with a `MockBedrockClient` that returns canned shapes per stage. S3 and DynamoDB: `MagicMock` capturing writes, asserted on at each seam.

The assertions at each seam are SHAPE assertions on the seam contract — not behavior assertions on stage internals. Stage-internal tests cover internals.

### Test C — DROPPED

Confirmed redundant with `tests/test_pipeline_cold_run.py:123–369`:
- `test_main_writes_complete_row_with_initiated_by` covers rc=0 + the 9-name canonical `stage_names` on the STAGE#cold_run row.
- `test_p11_cutover_row_written_after_cold_run_row` covers cutover-after-cold_run ordering (O-03).
- `test_main_short_circuits_on_first_failure` covers failed-stage propagation.

No gap. Test C dropped.

## What we ARE re-using

- `tests/fixtures/cold_path_corpus/{taxonomy,pubs}.json` — input fixture
- `tests/test_cold_path_e2e.py` helpers — `_make_score_row`, `_minimal_sub`, `_S3PutCapture`, `_run_publish`. Lift into `tests/integration/_helpers.py` if Tests B reuses ≥3 of them; otherwise import directly.
- `tests/fixtures/spotlight_valid_min.json` — schema for SPOTLIGHT# shape check

## What we ARE NOT doing

- Real Bedrock calls
- Real AWS (no moto either — `MagicMock` is enough for shape assertions; moto adds maintenance for no marginal value over MagicMock when we're not testing AWS behavior)
- Performance / runtime budgets (#13 mentions <60s on CI but doesn't gate)
- Stage-internal prompt validation (already covered)
- Test C if it duplicates `test_pipeline_cold_run.py` coverage

## File layout

```
tests/
  test_cold_run_command_lines_integration.py   # Test A
  test_cold_path_seams_integration.py          # Test B
```

Flat naming matches existing `tests/test_*.py` convention. `_integration` suffix signals intent without forcing a directory pattern that doesn't exist in the repo today.

Shared helpers from `test_cold_path_e2e.py` (`_S3PutCapture`, `_run_publish`, `_minimal_sub`) get reused by import if Test B needs ≥3 of them; otherwise leave them where they are.

## Time budget

1–2 day cycle. Survey-and-plan: done (~45 min). Test A: 2–3 hr. Test B: 5–7 hr (most of the time is per-stage Bedrock-response canning and shape assertion authoring).

## Verification

- `pytest tests/test_cold_run_command_lines_integration.py tests/test_cold_path_seams_integration.py` exits 0
- `pytest tests/` exits 0 (no regressions in existing 50+ test files)
- Manual: temporarily break `default_cold_stages()` (e.g. remove `--skip-all-reviews`) and confirm Test A fails with a clear message; restore.
- Manual: temporarily break a stage seam (e.g. rename a field `rollup` writes) and confirm Test B fails at the right stage with a clear message; restore.

## Out of scope (filed forward, not blocking)

- Live Bedrock E2E (would catch prompt drift; high maintenance cost, low historical bug rate)
- Test coverage of `pipeline_feedback.sweep` Bedrock-model-id validation at parse time — better fixed by config-schema validation than by an integration test

## Decisions log

- **Q1** Flat `tests/test_*_integration.py` naming (no new subdir). `tests/integration/` doesn't exist; two-test scope doesn't justify creating it.
- **Q2** Test C dropped — `tests/test_pipeline_cold_run.py` already covers main()+subprocess-mock shape.
- **Q3** Carry-forward from Phase 12 (no new phase ceremony). One-line breadcrumb under Phase 12 in `ROADMAP.md`.
- **Branch** renamed `test/13-e2e-seam-coverage` → `test/13-cold-path-seam-coverage` to drop the phase-13 ambiguity while keeping issue-13 traceability.

## Order of execution

1. Test A first (parser introspection — small, validates the approach).
2. If parser introspection turns out to be impractical for any stage (parser instances not exposed, parse calls `sys.exit` on missing-arg even with `args=[]`, etc.), surface fast and rescope Test A before starting B.
3. Test B after A is green.
4. Final `pytest tests/` regression check. Commit per test. PR for review only — no auto-merge.

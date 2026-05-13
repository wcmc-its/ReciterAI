---
phase: 12-feedback-loops-both-aggregations-residual-hygiene
plan: feedback-consumer
status: complete
subsystem: pipeline_feedback
tags: [feedback, sweep, finding-records, cli, markdown-render, cold-stage, tdd]
dependency_graph:
  requires:
    - feedback-producer  # CRITIC_REJECT# rows produced by this plan
    - drift-extension    # DRIFT#evaluation per_topic_low_confidence field (D-34)
    - thresholds-substrate  # config/thresholds.json tunables
  provides:
    - pipeline_feedback package with sweep + CLI + render
    - feedback_sweep ColdStage registered in pipeline_cold/run.py
    - CANDIDATE_TOPIC#, RECLUSTER_RECOMMENDATION#, SPOTLIGHT_DIAGNOSTIC#{subtopic_id} finding rows
  affects:
    - pipeline_cold/run.py  # stage list extended with feedback_sweep between rollup and backfill_spotlight
tech_stack:
  added:
    - pipeline_feedback/cli.py (argparse, boto3.dynamodb.conditions.Attr)
    - pipeline_feedback/markdown_render.py (io.StringIO, deterministic UTF-8 bytes)
    - pipeline_feedback/__main__.py (python -m pipeline_feedback entry point)
  patterns:
    - Pattern A (pure builder + thin writer)
    - Pattern B (paginated scan, analog review/validator.py:_query_pending_rows)
    - Pattern G (module docstring documents exit codes)
    - TDD RED/GREEN/REFACTOR (3-task plan, tasks 1+2 committed pre-checkpoint, task 3 here)
key_files:
  created:
    - pipeline_feedback/__init__.py
    - pipeline_feedback/finding_records.py
    - pipeline_feedback/sweep.py
    - pipeline_feedback/prompts/uncovered_pmid_sonnet_v0.md
    - pipeline_feedback/cli.py
    - pipeline_feedback/__main__.py
    - pipeline_feedback/markdown_render.py
    - tests/test_feedback_finding_records.py
    - tests/test_feedback_sweep.py
    - tests/test_feedback_cli.py
    - tests/test_feedback_render.py
  modified:
    - pipeline_cold/run.py  # feedback_sweep ColdStage inserted
    - tests/test_pipeline_cold_run.py  # canonical stage list updated (Rule 1 auto-fix)
decisions:
  - "D-08: SPOTLIGHT_DIAGNOSTIC keyed by subtopic_id (not cwid); per-faculty drill-down via author_cwids on underlying CRITIC_REJECT# rows"
  - "D-09: distinct_pmid_set_count = distinct (publish_id, pmid_set_hash) pairs per subtopic — not total rejection events"
  - "D-03: render_sweep_markdown returns bytes; no generated_at in body; timestamp belongs in caller filename"
  - "W-5 closed: _fetch_rows_by_run_id implemented with boto3 pagination (LastEvaluatedKey/ExclusiveStartKey loop); no ellipsis stub"
  - "W-7 closed: pre-edit assertion verified rollup + backfill_spotlight anchor stages present before pipeline_cold/run.py edit"
  - "D-02: feedback_sweep ColdStage returns 0 even with findings — non-gating"
metrics:
  duration_estimate: "~2 hours (continuation agent; Tasks 1+2 pre-committed)"
  completed_date: "2026-05-13"
  tasks_completed: 3
  tests_added: 44  # 13 finding_records + 15 sweep + 10 cli + 6 render = 44
  files_created: 11
  files_modified: 2
---

# Phase 12 Plan feedback-consumer: SUMMARY

**One-liner:** Full §9 feedback consumer — three typed finding-record builders (SPOTLIGHT_DIAGNOSTIC keyed by subtopic_id per D-08), sweep engine with Sonnet uncovered-PMID + recluster trigger + per-subtopic diagnostic aggregation, operator CLI with paginated `_fetch_rows_by_run_id` (W-5 closed), deterministic markdown renderer (D-03), and non-gating cold-stage registration between rollup and backfill_spotlight (W-7 pre-assertion verified).

## Completed Tasks

| Task | Name | Commit | Files Created/Modified |
|------|------|--------|------------------------|
| 1 | finding_records.py + tests | 239d8e8 | pipeline_feedback/__init__.py, pipeline_feedback/finding_records.py, tests/test_feedback_finding_records.py |
| 2 | sweep.py + Sonnet prompt + tests | bd9fa27 | pipeline_feedback/sweep.py, pipeline_feedback/prompts/uncovered_pmid_sonnet_v0.md, tests/test_feedback_sweep.py |
| 2.5 (W-6) | Inter-task checkpoint | 7bf5890 | .planning/…/12-feedback-consumer-SUMMARY.md (interim) |
| 3 RED | Failing tests for CLI + render + cold-stage | 4147522 | tests/test_feedback_cli.py, tests/test_feedback_render.py |
| 3 GREEN | CLI + renderer + cold-stage + paginated fetch | 6591b5b | pipeline_feedback/cli.py, pipeline_feedback/__main__.py, pipeline_feedback/markdown_render.py, pipeline_cold/run.py, tests/test_pipeline_cold_run.py |

## W-6 Inter-Task Checkpoint

W-6 inter-task checkpoint auto-approved by orchestrator after executor self-verification: 28 tests green, pipeline_feedback.sweep importable, D-08 per-subtopic grain confirmed (no `cwid`/`author_cwids` on SPOTLIGHT_DIAGNOSTIC#).

## Verification Results

All acceptance criteria met:

- `pytest tests/test_feedback_finding_records.py tests/test_feedback_sweep.py tests/test_feedback_cli.py tests/test_feedback_render.py` → **44 passed**
- `pytest tests/test_pipeline_cold_run.py` → **20 passed** (no regression)
- Cold-stage placement: rollup(4) < feedback_sweep(5) < backfill_spotlight(6) ✓
- `python -m pipeline_feedback --help` → exit 0; outputs "sweep" and "render" ✓
- `_fetch_rows_by_run_id`: fully implemented with `while True` pagination loop; no ellipsis stub ✓
- `generated_at` not in rendered bytes (D-03) ✓
- `subtopic_id` rendered in diagnostic block (D-08) ✓

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated test_pipeline_cold_run.py canonical stage list**
- **Found during:** Task 3 GREEN — cold-path regression
- **Issue:** `test_default_cold_stages_in_canonical_order`, `test_select_stages_resumes_from_named_stage`, stage_names assertion in `test_main_writes_complete_row_with_initiated_by`, and invocation count in `test_main_from_stage_skips_earlier_stages` all assumed 7 stages without `feedback_sweep`
- **Fix:** Updated all four tests to include `feedback_sweep` at index 5 and adjusted counts (rollup→from-stage now yields 4 stages, not 3)
- **Files modified:** tests/test_pipeline_cold_run.py
- **Commit:** 6591b5b

**2. [Rule 1 - Bug] Fixed test_render_subcommand_reads_run_id_rows mock injection**
- **Found during:** Task 3 GREEN — first test run
- **Issue:** Patching `_default_get_table` via `patch()` doesn't intercept the default-argument reference captured at function definition time in `_run_render(args, *, get_table=_default_get_table)`
- **Fix:** Used `patch.object(cli_mod, "_run_render", side_effect=...)` to inject the mock table directly as a keyword argument to the real `_run_render`
- **Files modified:** tests/test_feedback_cli.py
- **Commit:** 6591b5b (updated test committed alongside implementation)

## Known Stubs

None — `_fetch_rows_by_run_id` is fully implemented with pagination (W-5 closed). No ellipsis stubs anywhere in the package.

## Threat Flags

None — no new network endpoints or auth paths introduced. `pipeline_feedback/cli.py` reads from DynamoDB via the existing `get_table()` helper (same trust boundary as review/cli.py). The cold-stage subprocess calls `python -m pipeline_feedback sweep` which uses the same env-var-based DynamoDB config as all other cold stages.

## Self-Check: PASSED

- pipeline_feedback/cli.py: present ✓
- pipeline_feedback/__main__.py: present ✓
- pipeline_feedback/markdown_render.py: present ✓
- pipeline_cold/run.py modified with feedback_sweep stage ✓
- tests/test_feedback_cli.py: 10 tests ✓
- tests/test_feedback_render.py: 6 tests ✓
- Commits 4147522 (RED) and 6591b5b (GREEN) present in git log ✓
- 64/64 tests in full verification suite ✓

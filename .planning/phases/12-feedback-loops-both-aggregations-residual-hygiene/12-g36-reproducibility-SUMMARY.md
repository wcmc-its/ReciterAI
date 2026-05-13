---
phase: 12-feedback-loops-both-aggregations-residual-hygiene
plan: g36-reproducibility
subsystem: pipeline_hierarchy
tags: [reproducibility, hierarchy, residual-hygiene, tdd, testing, g36, d14, g29]
requirements_satisfied: [G-36, D-19]

dependency_graph:
  requires:
    - 12-aggregations (wave 1 — no direct code dep but wave ordering)
    - pipeline_hierarchy/publish.py (Phase 11 D-11 5-step S3 write order)
    - pipeline_hierarchy/generator.py (Phase 11 D-14 generated_at removal)
  provides:
    - publish()-layer reproducibility lock (tests/test_hierarchy_reproducibility.py)
    - G-36 invariant verified end-to-end: bundle → build → generate → publish
  affects:
    - Any future change re-introducing generated_at into hierarchy body will fail test suite

tech_stack:
  added: []
  patterns:
    - _S3PutCapture stub for capturing put_object call bodies without real S3
    - patch(pipeline_hierarchy.generator.datetime) with fixed subclass to pin generated_at inside generate()
    - _recursive_has_key() for deep-structure absence assertion
    - Two-run identical-input byte-equality pattern (extend from bundle/build layer to publish layer)

key_files:
  created: []
  modified:
    - tests/test_hierarchy_reproducibility.py

decisions:
  - "Patch pipeline_hierarchy.generator.datetime via a _FixedDatetime subclass rather than patching _now_iso; generator.py uses datetime.now() inline with no helper function, so the class-level patch is the correct surface."
  - "Use real generate() (not mocked) in all three publish tests to get actual artifact bytes rather than synthetic mock bytes; this is the distinguishing value of reproducibility tests over integration tests."
  - "Use different pinned timestamps for run 1 and run 2 in byte-identical test to prove hierarchy stability is independent of manifest timestamp — makes the invariant explicit."
  - "_extract_put_body uses key_suffix match (endswith) to tolerate version-prefix variation in S3 keys (e.g., vtest-2026-06-01/hierarchy.json)."

metrics:
  duration: ~5 minutes
  completed: "2026-05-12"
  tasks_completed: 1
  tasks_total: 1
  files_created: 1
  files_modified: 1
---

# Phase 12 Plan g36-reproducibility: Hierarchy Publish Reproducibility Summary

**One-liner:** Extended `test_hierarchy_reproducibility.py` with three publish()-layer tests that byte-assert hierarchy.json stability end-to-end via real `generate()` + `_S3PutCapture` stub.

## What Was Built

Three new test functions appended to `tests/test_hierarchy_reproducibility.py` (previously covering only bundle/build/generate layers):

| Test | Invariant | Method |
|------|-----------|--------|
| `test_publish_byte_identical_across_reruns` | G-36 / D-14: hierarchy.json bytes are bit-identical across two runs with different timestamps | Run `publish.main()` twice with different pinned `generated_at`; compare bytes from `put_object` calls |
| `test_publish_hierarchy_has_no_generated_at` | G-36 / G-29: no `generated_at` key at any nesting depth in hierarchy.json | Recursive key walk after parsing hierarchy bytes |
| `test_publish_manifest_has_generated_at` | D-14 boundary: manifest.json carries `generated_at` (both sides of the D-14 split are asserted) | Parse manifest bytes, assert key present |

Five supporting private helpers added to the file:

- `_S3PutCapture` — minimal stub capturing `put_object` bodies without boto3
- `_make_publish_fixture(tmp_path)` — builds a fixture bundled dict via real `bundle()` using existing `_write_augmented`/`_minimal_sub` helpers
- `_run_publish_with_pinned_time(bundled_dict, generated_at_iso)` — runs `publish.main()` with real `generate()`, DynamoDB mocked, datetime pinned
- `_extract_put_body(s3_stub, key_suffix)` — locates a call by key suffix
- `_recursive_has_key(obj, key)` — deep dict/list walk for absence assertion

## Sanity-Check Failure Injection (verified, not committed)

Temporarily injected `hierarchy["generated_at"] = "x"` into `pipeline_hierarchy/generator.py:build_hierarchy()`. Result: `test_publish_hierarchy_has_no_generated_at` failed with a clear assertion message listing the top-level keys. Injection reverted before commit; generator.py is unchanged.

## Deviations from Plan

None — plan executed exactly as written. The only implementation decision (how to pin `generated_at` inside `generate()`) was resolved by patching `pipeline_hierarchy.generator.datetime` as a subclass, which is consistent with Python best practices for pinning datetime in tests where no helper function exists.

## Commits

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Extend reproducibility tests to publish() end-to-end | 0f96fb8 | tests/test_hierarchy_reproducibility.py |

## Known Stubs

None — all three tests exercise the real `generate()` path and assert on live artifact bytes.

## Threat Flags

None — this plan adds tests only; no new network endpoints, auth paths, or file access patterns introduced.

## Self-Check: PASSED

- `tests/test_hierarchy_reproducibility.py` exists and contains 7 tests (4 pre-existing + 3 new)
- Commit `0f96fb8` verified in git history
- `grep -c "def test_publish_byte_identical_across_reruns\|def test_publish_hierarchy_has_no_generated_at\|def test_publish_manifest_has_generated_at"` returns 3
- `pytest tests/test_hierarchy_reproducibility.py -x` exits 0 (7 passed)

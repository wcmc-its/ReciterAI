---
phase: 12-feedback-loops-both-aggregations-residual-hygiene
plan: g36-reproducibility
type: execute
wave: 2
depends_on:
  - aggregations
files_modified:
  - tests/test_hierarchy_reproducibility.py
autonomous: true
requirements:
  - G-36
  - D-19
tags: [reproducibility, hierarchy, residual-hygiene, tdd]
must_haves:
  truths:
    - "tests/test_hierarchy_reproducibility.py covers bundle, build_hierarchy, generate, AND publish() end-to-end paths for byte-stable output"
    - "G-29 invariant (no generated_at in hierarchy.json body) is asserted at every level"
    - "Tests pass on main and detect regressions if anyone re-introduces a timestamp into the body"
  artifacts:
    - path: "tests/test_hierarchy_reproducibility.py"
      provides: "Extended reproducibility coverage from bundle through publish()"
      contains: "test_publish_byte_identical_across_reruns"
  key_links:
    - from: "tests/test_hierarchy_reproducibility.py extended publish test"
      to: "pipeline_hierarchy/publish.py main path"
      via: "two-run identical-input assertion on the hierarchy.json artifact bytes"
      pattern: "publish"
---

<objective>
Harden the existing reproducibility test suite to cover the full publish() path, not just bundle + build + generate. RESEARCH F-5 confirmed `tests/test_hierarchy_reproducibility.py` already exists from Phase 11 with four tests covering bundle/build/generate byte-stability. G-36 in Phase 12 §11 means extending this to publish() end-to-end — so the test catches regressions where a future change re-introduces a timestamp into hierarchy.json via the publish path even though generate keeps it out.

Why this matters: Phase 11 G-29 removed `generated_at` from `hierarchy.json` so it could be bit-stable across content-identical reruns. That's only meaningful if a test verifies the invariant continues to hold. The existing tests verify generate's output. G-36 extends to verify publish's output — closes a hole where someone could regress the invariant by adding back a timestamp at a later layer.

Output: extended tests/test_hierarchy_reproducibility.py with two new test functions covering publish() end-to-end.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@.planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-CONTEXT.md
@.planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-RESEARCH.md
@.planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-PATTERNS.md
@.planning/phases/11-versioning-review-diff/11-SUMMARY-change-signaling.md

<interfaces>
<!-- The existing test file (RESEARCH F-5 confirmed) -->

tests/test_hierarchy_reproducibility.py exists with these existing tests (verified pre-Phase-12):
- test_bundle_byte_identical
- test_build_hierarchy_byte_identical
- test_generate_byte_identical
- (one more — verify by reading the file in read_first)

Each builds a fixture corpus in tmp_path, runs the operation twice, asserts byte-equality of the JSON output.

<!-- Existing publish() path (Phase 11 D-11 5-step S3 write order) -->

pipeline_hierarchy/publish.py main() — orchestrates: build → bundle → generate → S3 upload. The hierarchy.json bytes are the artifact at step 1 of the 5-step write order. We want to verify these bytes are content-stable across two identical-input invocations.

<!-- Why publish() needs its own test beyond generate() -->

publish() may eventually add metadata to the hierarchy.json via wrapping or post-processing. The test at the publish layer catches that class of regression. Today publish() doesn't modify hierarchy.json after generate, but a test that depends on "I checked once and it didn't" is a test that breaks the moment someone adds a wrapper.

<!-- Fixture approach -->

Reuse the existing fixture helpers in tests/test_hierarchy_reproducibility.py (`_write_augmented`, `_minimal_sub`). For publish, mock the S3 client (capture put_object calls; verify the hierarchy.json bytes argument from the call captured in run 1 equals the bytes from run 2).
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Extend reproducibility tests to cover publish() end-to-end</name>
  <files>tests/test_hierarchy_reproducibility.py</files>
  <read_first>
    - tests/test_hierarchy_reproducibility.py FULL file (need: existing test names, fixture helpers `_write_augmented` + `_minimal_sub`, the SOURCE_HIERARCHY / DEFAULT_AUGMENTED_DIR conventions)
    - pipeline_hierarchy/publish.py FULL file (need: main() signature, upload_to_s3 helper, the exact place where hierarchy.json bytes are written — Phase 11 D-11 5-step order; the bytes argument to S3HierarchyClient.put_object)
    - utils/s3_client.py (S3HierarchyClient + put_object signature — need to mock the right callable surface)
    - .planning/phases/11-versioning-review-diff/11-SUMMARY-change-signaling.md (D-14 G-29 fix narrative — context for why this test matters)
    - .planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-PATTERNS.md "Tests" section
  </read_first>
  <behavior>
    - tests/test_hierarchy_reproducibility.py::test_publish_byte_identical_across_reruns — Build a fixture corpus in tmp_path; invoke pipeline_hierarchy.publish.main (or the lower-level orchestration callable; whichever the existing tests use) twice with identical inputs; capture the hierarchy.json bytes passed to S3HierarchyClient.put_object on each run; assert run-1 bytes == run-2 bytes
    - tests/test_hierarchy_reproducibility.py::test_publish_hierarchy_has_no_generated_at — capture the hierarchy.json bytes from publish; parse as JSON; assert "generated_at" NOT in the top-level dict and not in any nested dict (recursive check — defense against someone embedding a timestamp inside a topic or subtopic)
    - tests/test_hierarchy_reproducibility.py::test_publish_manifest_has_generated_at — companion negative test: manifest.json IS allowed to (and should) carry generated_at; assert it's present in the manifest bytes captured from the publish run. Documents the boundary: manifest may carry timestamps, hierarchy may not.
  </behavior>
  <action>
    Read `tests/test_hierarchy_reproducibility.py` to discover the existing fixture-builder pattern. Reuse it (do NOT rewrite — append new tests at the bottom of the file).

    Add the three new test functions. Each follows the existing pattern:

    ```python
    def test_publish_byte_identical_across_reruns(tmp_path, monkeypatch):
        """G-36 (Phase 12): publish() emits a hierarchy.json that is bit-stable
        across content-identical reruns.

        Phase 11 D-14/G-29 removed generated_at from hierarchy.json so this
        invariant could hold. This test catches regressions where a future
        change re-introduces a timestamp via the publish path even though
        generate already keeps it out.
        """
        # build identical fixture corpus in tmp_path (reuse _write_augmented / _minimal_sub)
        # mock S3HierarchyClient.put_object — capture the bytes arg for hierarchy.json
        # invoke publish twice
        # extract the hierarchy.json bytes from run 1 and run 2
        # assert run1_bytes == run2_bytes
        ...

    def test_publish_hierarchy_has_no_generated_at(tmp_path, monkeypatch):
        """G-36 (Phase 12) + G-29 (Phase 11): the hierarchy.json bytes
        emitted by publish() carry no generated_at field anywhere in the
        tree, including nested topic/subtopic dicts.
        """
        # mock S3, invoke publish, capture hierarchy.json bytes
        # parse JSON
        # recursive walk asserting "generated_at" not in any dict key
        ...

    def test_publish_manifest_has_generated_at(tmp_path, monkeypatch):
        """Boundary test: manifest.json IS allowed to carry generated_at
        even though hierarchy.json must not. Documents the line between
        the two artifacts (Phase 11 D-14: generated_at moves OUT of
        hierarchy and INTO manifest — both directions matter).
        """
        # mock S3, invoke publish, capture manifest.json bytes (different S3 key from hierarchy)
        # parse JSON
        # assert "generated_at" in parsed
        ...
    ```

    Mock approach: patch `utils.s3_client.S3HierarchyClient` (or whatever the publish path uses) so its put_object becomes a MagicMock; in each test, after publish completes, iterate `put_object.call_args_list` and find the call whose `Key` (or `key` kwarg) ends in `hierarchy.json` — that call's `Body` (or `body` / `data` kwarg, whichever the signature uses) is the bytes under test. Same approach for manifest.json with the appropriate S3 key match.

    To stabilize the timestamp inputs that DO legitimately flow into manifest (and that publish DOES embed there): patch `generator._now_iso` or `publish._now_iso` to return a constant ISO string for run 1 and a DIFFERENT constant string for run 2. The hierarchy bytes should still be identical between the runs (proving G-29 robust); the manifest bytes will differ (proving the manifest is the legitimate home for the timestamp).

    Commit message: `test(12-residual): extend hierarchy reproducibility to publish() end-to-end (G-36)`.
  </action>
  <verify>
    <automated>cd /Users/paulalbert/Dropbox/GitHub/ReciterAI && pytest tests/test_hierarchy_reproducibility.py -x</automated>
  </verify>
  <acceptance_criteria>
    - `grep -c "def test_publish_byte_identical_across_reruns\|def test_publish_hierarchy_has_no_generated_at\|def test_publish_manifest_has_generated_at" tests/test_hierarchy_reproducibility.py` returns 3
    - `grep -c "G-36" tests/test_hierarchy_reproducibility.py` returns count >= 2 (referenced in docstrings)
    - `grep -c "generated_at" tests/test_hierarchy_reproducibility.py` returns count >= 4 (used as assertion target across multiple tests)
    - The original (pre-existing) tests still pass: `pytest tests/test_hierarchy_reproducibility.py -k "not publish" -x` exits 0
    - The full suite passes: `pytest tests/test_hierarchy_reproducibility.py -x` exits 0
    - Run a sanity-check failure injection (manual verification, do NOT commit): temporarily add `hierarchy["generated_at"] = "x"` somewhere in pipeline_hierarchy/generator.py near the return; assert `pytest tests/test_hierarchy_reproducibility.py::test_publish_hierarchy_has_no_generated_at -x` exits NON-ZERO (test catches the regression); then revert the injection
  </acceptance_criteria>
  <done>publish()-layer reproducibility is locked in by tests; the G-29 invariant extends to the publish layer, not just generate.</done>
</task>

</tasks>

<verification>
- Task 1 acceptance criteria green
- `pytest tests/test_hierarchy_reproducibility.py -x` exits 0
- Existing reproducibility tests untouched (only additions; verify with `git diff tests/test_hierarchy_reproducibility.py` — only new test functions appear in the diff)
</verification>

<success_criteria>
G-36 ships: the reproducibility test suite covers the full path from bundle through publish, not just the intermediate stages. Any future regression that re-introduces a timestamp into hierarchy.json bytes via any layer will fail this suite.
</success_criteria>

<output>
After completion, create `.planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-g36-reproducibility-SUMMARY.md`.
</output>

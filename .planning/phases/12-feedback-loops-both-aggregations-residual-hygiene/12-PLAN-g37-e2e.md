---
phase: 12-feedback-loops-both-aggregations-residual-hygiene
plan: g37-e2e
type: execute
wave: 3
depends_on:
  - thresholds-substrate
  - feedback-producer
  - drift-extension
  - aggregations
  - residual-docs
  - feedback-consumer
  - g36-reproducibility
files_modified:
  - tests/test_cold_path_e2e.py
  - tests/fixtures/cold_path_corpus/README.md
  - tests/fixtures/cold_path_corpus/taxonomy.json
  - tests/fixtures/cold_path_corpus/pubs.json
autonomous: false
requirements:
  - G-37
  - D-19
  - D-20
  - D-21
  - D-22
tags: [e2e, integration, residual-hygiene, fixture-corpus]
must_haves:
  truths:
    - "One bounded E2E test exists running a stripped-down fixture corpus from generate_taxonomy.py through pipeline_hierarchy/publish.py"
    - "Test asserts the published manifest validates against the published schema"
    - "Test asserts a second run with the same fixture produces a byte-identical hierarchy.json (leveraging Phase 11 G-29 + Plan g36-reproducibility coverage)"
    - "Fixture corpus is documented (2 topics × 3 subtopics × 5 pubs per Pitfall 4 in RESEARCH) BEFORE the test code lands"
    - "G-37 only starts after every other Phase 12 plan's pytest passes on main (D-20 SHA-able gate)"
  artifacts:
    - path: "tests/test_cold_path_e2e.py"
      provides: "One bounded E2E integration test for the cold-path artifact production chain"
      contains: "test_cold_path_corpus_through_publish"
    - path: "tests/fixtures/cold_path_corpus/README.md"
      provides: "Documents the fixture corpus shape, intent, and update procedure"
      contains: "corpus"
    - path: "tests/fixtures/cold_path_corpus/taxonomy.json"
      provides: "Minimal 2-topic taxonomy fixture"
      contains: "topics"
    - path: "tests/fixtures/cold_path_corpus/pubs.json"
      provides: "30-publication corpus fixture (5 pubs × 3 subtopics × 2 topics)"
      contains: "pmid"
  key_links:
    - from: "tests/test_cold_path_e2e.py"
      to: "generate_taxonomy.py → score_publications → assign_subtopics → aggregate_subtopic_scores → pipeline_hierarchy.publish"
      via: "drive each stage in-process (or via subprocess if a stage is unavoidably CLI-only); chain inputs/outputs"
      pattern: "cold_path_corpus"
---

<objective>
Ship one bounded end-to-end test as the final wave-3 item of Phase 12. Per D-21: "Run a stripped-down fixture corpus through the cold path from `generate_taxonomy.py` to `pipeline_hierarchy/publish.py`, assert the published manifest validates against the published schema, assert the same fixture corpus produces a byte-identical `hierarchy.json` on a second run."

D-20 gating: this plan STARTS only after `pytest` exits 0 on main for every other Phase 12 plan's deliverables. The action below encodes that precondition.

Why bounded: per CONTEXT specifics ("one bounded test, not 'an integration test'"), the deliverable is a specific test that buys the most architectural confidence per unit of work. Phase 12 is not the place for an integration test suite — that's a follow-up if one is ever needed.

Output: one new test file + a fixture corpus directory with three files.
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
@.planning/issues/0002-g37-e2e-test.md

<interfaces>
<!-- D-20 SHA-able gate precondition (per CONTEXT) -->

G-37 work starts only AFTER pytest exits 0 on the following test files on main:
- tests/test_thresholds_schema.py (Plan thresholds-substrate)
- tests/test_thresholds_keys.py
- tests/test_env_check_thresholds.py
- tests/test_stage_records.py
- tests/test_critic_reject_event.py (Plan feedback-producer)
- tests/test_critic_reject_producer.py
- tests/test_pipeline_drift_evaluator.py (Plan drift-extension)
- tests/test_aggregate_inclusive.py (Plan aggregations)
- tests/test_aggregate_idempotent.py
- tests/test_reconciliation_gate.py
- tests/test_rollup_incremental_parity.py
- tests/test_feedback_finding_records.py (Plan feedback-consumer)
- tests/test_feedback_sweep.py
- tests/test_feedback_cli.py
- tests/test_feedback_render.py
- tests/test_hierarchy_reproducibility.py (Plan g36-reproducibility)

The first task of this plan asserts those pass; if any fails, abort and surface the failure.

<!-- D-21 deliverable shape -->

One test. Runs the chain:
1. generate_taxonomy.py — consume tests/fixtures/cold_path_corpus/pubs.json (operator-defined fixture), produce a taxonomy.json (compare against tests/fixtures/cold_path_corpus/taxonomy.json or treat that file as the input rather than the output, depending on whether generate_taxonomy.py is in-scope; planner decides based on what the function actually produces — read its main entry point)
2. score_publications — score each fixture pub against the taxonomy
3. assign_subtopics — assign primary + above-floor subtopic_ids
4. aggregate_subtopic_scores — emit both EXCLUSIVE and INCLUSIVE partitions (Plan aggregations)
5. pipeline_hierarchy.publish — produce hierarchy.json + manifest.json + diff.json

Assertions:
- manifest validates against docs/hierarchy.schema.json
- second run (same inputs) produces byte-identical hierarchy.json (G-36/G-29 chain)
- diff.json shape is valid

<!-- D-22 tracking issue (from Plan thresholds-substrate Task 3) -->

.planning/issues/0002-g37-e2e-test.md already exists. This plan closes the loop: when this plan ships, the issue can be marked status: closed (if the PR description handles that) or remain open if G-37 slips out of the phase.

<!-- Fixture corpus shape (RESEARCH Pitfall 4) -->

2 topics × 3 subtopics each × 5 publications per subtopic = 30 publications. Each pub has: pmid (synthetic — "TEST_PMID_001" etc.), title, abstract, mesh terms. The taxonomy fixture defines 2 topic dicts each with 3 subtopic dicts (matching the existing taxonomy schema in source).

<!-- Test isolation -->

The cold-path stages today read/write DDB. For the E2E test, either:
- Option A: drive each stage as a Python function, injecting MagicMock DDB tables (capture put_item calls; assert on the captured rows)
- Option B: use DDB Local or moto and run the stages against an in-memory DDB
- Option C: drive the stages via subprocess against a real DDB but only in a CI-specific environment (avoid in default test run)

PLANNER NOTE: Option A is cheapest, matches the existing test patterns in tests/test_pipeline_drift_evaluator.py and tests/test_event_records.py, and aligns with "one bounded test." Pick Option A unless mid-task discovery says otherwise.
</interfaces>
</context>

<tasks>

<task type="checkpoint:human-verify" gate="blocking">
  <name>Task 1: Verify D-20 SHA-able gating preconditions (capture main SHA + pytest result)</name>
  <what-built>
    Plans thresholds-substrate, feedback-producer, drift-extension, aggregations, residual-docs, feedback-consumer, and g36-reproducibility have all merged to main and their tests pass on main. Per CONTEXT D-20: G-37 work starts only after this gate is satisfied. **W-1 hardening: D-20 demands a SHA-able gate, not a procedural "type gating satisfied" affirmation. This task captures both the SHA of main at gate-evaluation time AND the pytest result against that SHA. The captured SHA + pytest exit-code line is the audit-trail artifact recorded in the Phase 12 SUMMARY.**
  </what-built>
  <how-to-verify>
    1. Confirm current branch is at a commit that includes all wave-1 and wave-2 plans' tests.
    2. Capture the main SHA at gate-evaluation time AND the gating pytest result in one step:
       ```bash
       cd /Users/paulalbert/Dropbox/GitHub/ReciterAI
       GATE_SHA=$(git rev-parse main)
       echo "D-20 gate SHA: ${GATE_SHA}"
       pytest tests/test_thresholds_schema.py tests/test_thresholds_keys.py tests/test_env_check_thresholds.py tests/test_stage_records.py tests/test_critic_reject_event.py tests/test_critic_reject_producer.py tests/test_pipeline_drift_evaluator.py tests/test_aggregate_inclusive.py tests/test_aggregate_idempotent.py tests/test_reconciliation_gate.py tests/test_rollup_incremental_parity.py tests/test_feedback_finding_records.py tests/test_feedback_sweep.py tests/test_feedback_cli.py tests/test_feedback_render.py tests/test_hierarchy_reproducibility.py -x
       GATE_EXIT=$?
       echo "D-20 gate exit: ${GATE_EXIT} (must be 0)"
       echo "D-20 gate audit row: sha=${GATE_SHA} exit=${GATE_EXIT}"
       ```
    3. Confirm `GATE_EXIT` is 0.
    4. Copy the audit row (`sha=... exit=0`) verbatim into the resume signal so the Phase 12 SUMMARY can persist it. This is the SHA-able artifact D-20 demands.
    5. If ANY test in this set is missing/skipped/failing (GATE_EXIT != 0), do NOT proceed with G-37 — report which plan is incomplete and stop.
  </how-to-verify>
  <resume-signal>Paste the line `D-20 gate audit row: sha=<40-hex-chars> exit=0`. The executor MUST verify the SHA matches `git rev-parse main` at the time of receiving the signal (defense against stale paste) AND MUST persist the row verbatim into the Phase 12 SUMMARY before proceeding to Task 2. If exit != 0, describe which plans are still incomplete.</resume-signal>
</task>

<task type="auto">
  <name>Task 2: Author the fixture corpus + README documenting its shape</name>
  <files>tests/fixtures/cold_path_corpus/README.md, tests/fixtures/cold_path_corpus/taxonomy.json, tests/fixtures/cold_path_corpus/pubs.json</files>
  <read_first>
    - tests/fixtures/ (directory listing — see what JSON-schema-validation fixtures already exist for tone/structure)
    - generate_taxonomy.py main() (need: what input shape does this stage consume? What's the publication record shape? What fields does the taxonomy generation require?)
    - score_publications/*.py (need: the publication-record schema — pmid, title, abstract, mesh terms, etc.)
    - The existing live taxonomy.json under source/ (look for `taxonomy_*.json` or whatever the canonical name is — find its schema)
    - .planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-RESEARCH.md (corpus fixture sizing rationale)
  </read_first>
  <action>
    Create directory `tests/fixtures/cold_path_corpus/`.

    Write `tests/fixtures/cold_path_corpus/README.md` documenting the corpus shape:

    ```markdown
    # cold_path_corpus — Phase 12 G-37 E2E fixture

    Minimal stripped-down corpus for the one bounded end-to-end test in
    `tests/test_cold_path_e2e.py`. Not representative of production
    scale; representative of the production *shape* through every stage.

    ## Shape

    | Element | Count | Why this count |
    |---------|-------|----------------|
    | Topics | 2 | One is enough to exercise the chain; two surface any "this only worked because there was a single topic" assumption |
    | Subtopics per topic | 3 | Smallest count that exercises the tie-resolution logic in `assign_subtopics.py:241-283` (need ≥2 to have a tie, +1 to verify the resolver picks correctly) |
    | Pubs per subtopic | 5 | Smallest count above the `spotlight_dirty_pubs_per_subtopic_min` threshold (5 in current config — see `config/thresholds.json`) |
    | Total pubs | 30 | 2 × 3 × 5 |

    ## Files

    - `pubs.json` — the publication corpus; one record per PMID with title, abstract, mesh terms
    - `taxonomy.json` — the taxonomy fixture (consumed by score/assign stages; in this minimal test, it's an INPUT, not the output of `generate_taxonomy.py` — the E2E test starts by reading this fixture, not by regenerating taxonomy from scratch)

    ## Why fixture taxonomy is INPUT not OUTPUT

    `generate_taxonomy.py` is non-deterministic (Bedrock LLM call). E2E
    testing it would require either a recorded-LLM-response replay or
    extensive mocking. Out of scope for one bounded test. Instead, the
    fixture treats taxonomy as INPUT and exercises everything DOWNSTREAM
    of taxonomy generation: scoring, assignment, aggregation, publish.

    If a future plan wants to also exercise `generate_taxonomy.py`, that's
    a separate test (and probably a separate fixture corpus or recorded-
    response cassette).

    ## Updating the corpus

    Update both `pubs.json` and `taxonomy.json` together — they must
    stay schema-consistent. After any update, re-run
    `tests/test_cold_path_e2e.py` to confirm the chain still flows
    cleanly.

    ## Synthetic PMIDs

    PMIDs are synthetic strings like `TEST_PMID_001` — explicitly
    non-numeric so they cannot collide with real PMIDs in any live
    dataset.
    ```

    Write `tests/fixtures/cold_path_corpus/taxonomy.json` with two topics, each having three subtopics. Use the existing live taxonomy.json's schema (read it in `read_first` to confirm field names). Pick topic+subtopic names that are obviously test fixtures — e.g. `test_topic_alpha` / `test_topic_beta`. Subtopic IDs follow the same convention.

    Write `tests/fixtures/cold_path_corpus/pubs.json` with 30 publications. Each pub has a synthetic PMID, a title (something distinguishable), an abstract (short — ~50 words; relevance to the synthetic topic should be obvious so scoring will work without surprises), and mesh terms. Spread the pubs so each subtopic gets exactly 5 strongly-relevant pubs.

    Commit message: `test(12-residual): add cold_path_corpus fixture for G-37 E2E test`.
  </action>
  <verify>
    <automated>cd /Users/paulalbert/Dropbox/GitHub/ReciterAI && python -c "import json; t=json.load(open('tests/fixtures/cold_path_corpus/taxonomy.json')); p=json.load(open('tests/fixtures/cold_path_corpus/pubs.json')); assert len(t.get('topics', t)) == 2 or len(t) == 2, 'expected 2 topics'; assert len(p) == 30, f'expected 30 pubs, got {len(p)}'"</automated>
  </verify>
  <acceptance_criteria>
    - Directory `tests/fixtures/cold_path_corpus/` exists with three files: README.md, taxonomy.json, pubs.json
    - `python -c "import json; print(len(json.load(open('tests/fixtures/cold_path_corpus/pubs.json'))))"` outputs `30`
    - `python -c "import json; t=json.load(open('tests/fixtures/cold_path_corpus/taxonomy.json')); topics=t.get('topics', t); print(len(topics))"` outputs `2`
    - `grep -c "TEST_PMID" tests/fixtures/cold_path_corpus/pubs.json` returns count >= 30 (every pub has a synthetic PMID)
    - `grep -c "^## " tests/fixtures/cold_path_corpus/README.md` returns count >= 3 (Shape, Files, Updating sections at minimum)
    - JSON files are valid: `python -c "import json; json.load(open('tests/fixtures/cold_path_corpus/taxonomy.json')); json.load(open('tests/fixtures/cold_path_corpus/pubs.json'))"` exits 0
  </acceptance_criteria>
  <done>Fixture corpus exists with the documented shape; README pins the design rationale; both JSONs validate.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 3: Write the one bounded E2E test</name>
  <files>tests/test_cold_path_e2e.py</files>
  <read_first>
    - tests/fixtures/cold_path_corpus/ (just created in Task 2)
    - score_publications/ main entry (need: how to call as a function with a corpus + taxonomy input; what it returns)
    - assign_subtopics.py main entry (function-callable surface; what dict/list it returns or persists)
    - aggregate_subtopic_scores.py main entry (function-callable; check Plan aggregations Task 1 result — it now writes to two partitions + faculty-map)
    - pipeline_hierarchy/publish.py main entry (the function that produces hierarchy.json + manifest.json + diff.json bytes)
    - utils/s3_client.py S3HierarchyClient (need to mock put_object to capture bytes)
    - tests/test_hierarchy_reproducibility.py (analog: how it mocks S3 + asserts byte-identical bytes — extend that pattern across more stages)
    - docs/hierarchy.schema.json (need it loaded for the schema-validation assertion)
  </read_first>
  <behavior>
    - tests/test_cold_path_e2e.py::test_cold_path_corpus_through_publish — load fixture corpus; run score → assign → aggregate → publish chain with MagicMock DDB tables and mocked S3; capture published hierarchy.json bytes + manifest.json bytes + diff.json bytes; assert hierarchy.json validates against docs/hierarchy.schema.json; assert manifest.json is well-formed JSON
    - tests/test_cold_path_e2e.py::test_cold_path_byte_identical_second_run — run the entire chain twice with identical fixture inputs; assert hierarchy.json bytes from run 1 == hierarchy.json bytes from run 2 (Phase 11 G-29 + Plan g36-reproducibility's invariant extended end-to-end)
    - tests/test_cold_path_e2e.py::test_cold_path_emits_both_subtopic_partitions — assert during the chain that put_item calls include PKs starting with "SUBTOPIC_SCORE#" AND PKs starting with "SUBTOPIC_SCORE_INCLUSIVE#" (Plan aggregations integration)
    - tests/test_cold_path_e2e.py::test_cold_path_d33_reconciliation_passes — happy path: assert the aggregator's D-33 in-stream invariant does NOT raise (faculty-map and SUBTOPIC_SCORE# agree)
  </behavior>
  <action>
    Write `tests/test_cold_path_e2e.py` as a self-contained file. Module docstring:

    ```python
    """G-37 (Phase 12): one bounded end-to-end test from score_publications
    through pipeline_hierarchy.publish.

    Per D-21: this is ONE specific test, not an integration test suite. It
    buys architectural confidence per unit of work — anything else lives
    in a follow-up phase.

    Per CONTEXT line 'Why fixture taxonomy is INPUT not OUTPUT': taxonomy
    generation (the Bedrock-driven generate_taxonomy.py) is out of scope
    here — the fixture treats taxonomy as input. The chain exercised is
    score → assign → aggregate → publish.

    Mocks: DDB tables (MagicMock — capture put_item calls); S3 client
    (MagicMock — capture put_object calls and their byte payloads); Bedrock
    (replaced with canned responses where any stage invokes the LLM).
    """
    ```

    Build the test using the existing fixture-loading idioms from `tests/test_hierarchy_reproducibility.py`. Use `tmp_path` for any intermediate file output the stages might write. Use `monkeypatch` to patch:
    - `utils.s3_client.S3HierarchyClient` → MagicMock capturing put_object bytes
    - `utils.dynamodb_helpers.get_table` → MagicMock table capturing put_item calls
    - Any Bedrock client invocation (score_publications, spotlight critic if it triggers) → canned response

    The chain (each stage may be a function call or may require driving via its module entry — read first to confirm):

    ```python
    # Load fixture
    corpus = json.loads((FIXTURE_DIR / "pubs.json").read_text())
    taxonomy = json.loads((FIXTURE_DIR / "taxonomy.json").read_text())

    # 1. Score (mock Bedrock returns scores based on simple keyword matching of fixture content)
    scored = score_publications_main(corpus, taxonomy, bedrock_client=mock_bedrock)

    # 2. Assign (uses confidence_floor from config/thresholds.json — Plan thresholds-substrate)
    assigned = assign_subtopics_main(scored, taxonomy)

    # 3. Aggregate (Plan aggregations: writes faculty-map + SUBTOPIC_SCORE# + SUBTOPIC_SCORE_INCLUSIVE#)
    aggregate_subtopic_scores_main(assigned, table=mock_table)

    # 4. Publish (Phase 11 + this phase's reconciliation gate runs)
    publish_main(table=mock_table, s3_client=mock_s3, version="test-v1")

    # Extract written bytes from mock_s3 put_object call_args_list
    hierarchy_bytes = _find_bytes_for_key(mock_s3, "hierarchy.json")
    manifest_bytes = _find_bytes_for_key(mock_s3, "manifest.json")

    # Assertions: schema validation, byte-stability, both-partition writes
    jsonschema.validate(json.loads(hierarchy_bytes), schema=hierarchy_schema)
    ...
    ```

    If a stage's function-callable surface is too narrow for direct invocation (e.g. it requires reading from RDS or a real DDB), use subprocess to invoke `python -m <stage> --fixture-mode` if the stage supports it; if not, the planner should DEFER that piece with a clear comment in the test (e.g. "score_publications does not have a fixture-mode entry; mock its output directly with `scored = _build_synthetic_scored_output(corpus)`"). Pragmatism wins over completeness — D-21 says "one bounded test," not "comprehensive integration."

    Commit message: `test(12-residual): one bounded cold-path E2E test (G-37)`.
  </action>
  <verify>
    <automated>cd /Users/paulalbert/Dropbox/GitHub/ReciterAI && pytest tests/test_cold_path_e2e.py -x</automated>
  </verify>
  <acceptance_criteria>
    - File `tests/test_cold_path_e2e.py` exists
    - `grep -c "test_cold_path_corpus_through_publish\|test_cold_path_byte_identical_second_run\|test_cold_path_emits_both_subtopic_partitions\|test_cold_path_d33_reconciliation_passes" tests/test_cold_path_e2e.py` returns count >= 4
    - `grep -c "G-37" tests/test_cold_path_e2e.py` returns count >= 1 (referenced in module docstring)
    - `grep -c "cold_path_corpus" tests/test_cold_path_e2e.py` returns count >= 1 (fixture loaded)
    - `grep -c "SUBTOPIC_SCORE_INCLUSIVE\|SUBTOPIC_SCORE#" tests/test_cold_path_e2e.py` returns count >= 2 (both partition writes asserted)
    - `pytest tests/test_cold_path_e2e.py -x` exits 0
    - Full Phase 12 test suite still passes: `pytest tests/test_thresholds_schema.py tests/test_thresholds_keys.py tests/test_env_check_thresholds.py tests/test_stage_records.py tests/test_critic_reject_event.py tests/test_critic_reject_producer.py tests/test_pipeline_drift_evaluator.py tests/test_aggregate_inclusive.py tests/test_aggregate_idempotent.py tests/test_reconciliation_gate.py tests/test_rollup_incremental_parity.py tests/test_feedback_finding_records.py tests/test_feedback_sweep.py tests/test_feedback_cli.py tests/test_feedback_render.py tests/test_hierarchy_reproducibility.py tests/test_cold_path_e2e.py -x` exits 0
    - `.planning/issues/0002-g37-e2e-test.md` updated: change `status: open` to `status: closed-by-phase-12` (or leave open if G-37 is decided to remain a follow-up after this test ships — but ideally close)
  </acceptance_criteria>
  <done>One bounded E2E test exists, asserts schema validity + byte-identical second run + dual-partition writes + D-33 invariant; G-37 carry-forward issue is closed by Phase 12 shipping the test.</done>
</task>

</tasks>

<verification>
- All three tasks' acceptance criteria green
- `pytest tests/test_cold_path_e2e.py -x` exits 0
- Full Phase 12 test set still green
- `.planning/issues/0002-g37-e2e-test.md` reflects the resolution status
- **W-1 SHA-able gate audit row**: Phase 12 SUMMARY contains the line `D-20 gate audit row: sha=<40-hex-chars> exit=0` verbatim from Task 1's resume signal. The SHA matches `git rev-parse main` at gate-evaluation time. Without this row, the D-20 gate was not honored.
</verification>

<success_criteria>
G-37 ships: one bounded E2E test running fixture corpus through the cold path, asserting schema validity AND byte-identical reruns AND both-partition writes AND D-33 invariant. Architectural confidence per unit of work is high. Phase 12 §11 residual hygiene is complete.
</success_criteria>

<output>
After completion, create `.planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-g37-e2e-SUMMARY.md`.
</output>

---
phase: 12-feedback-loops-both-aggregations-residual-hygiene
plan: thresholds-substrate
type: execute
wave: 1
depends_on: []
files_modified:
  - config/thresholds.json
  - config/thresholds.schema.json
  - config/thresholds.md
  - utils/env_check.py
  - utils/stage_records.py
  - assign_subtopics.py
  - tests/test_thresholds_schema.py
  - tests/test_thresholds_keys.py
  - tests/test_stage_records.py
  - .planning/issues/0002-g37-e2e-test.md
autonomous: true
requirements:
  - G-18
  - G-1
  - D-23
  - D-24
  - D-25
  - D-26
  - D-27
  - D-28
tags: [config, thresholds, env_check, stage_records, residual-hygiene]
must_haves:
  truths:
    - "Every operationally-tunable number lives in config/thresholds.json"
    - "config/thresholds.json is schema-validated at process bootstrap; typos fail fast"
    - "Every key in config/thresholds.json has a documented entry in config/thresholds.md"
    - "STAGE# records carry the tunable_inputs read at stage startup (config vs cli vs default source)"
    - "ReciterDB column expectations are data-driven, not scattered string literals in env_check.py"
    - "G-37 follow-up tracking issue exists in .planning/issues/ from the first commit of Phase 12"
  artifacts:
    - path: "config/thresholds.json"
      provides: "All tunables — existing + Phase 12 additions"
      contains: "confidence_floor"
    - path: "config/thresholds.schema.json"
      provides: "JSON Schema Draft 2020-12 validation for thresholds.json"
      contains: "additionalProperties"
    - path: "config/thresholds.md"
      provides: "Per-key documentation: semantics, default, raise-side-effect"
      contains: "confidence_floor"
    - path: "utils/env_check.py"
      provides: "EXPECTED_COLUMNS data structure + thresholds-schema validation block"
      contains: "EXPECTED_COLUMNS"
    - path: "utils/stage_records.py"
      provides: "tunable_inputs optional field on build_complete_record / build_skipped_record / build_failed_record"
      contains: "tunable_inputs"
    - path: ".planning/issues/0002-g37-e2e-test.md"
      provides: "Phase-12 G-37 carry-forward tracking issue (per D-22)"
      contains: "G-37"
  key_links:
    - from: "utils/env_check.py"
      to: "config/thresholds.schema.json"
      via: "jsonschema.validate at startup"
      pattern: "jsonschema\\.validate"
    - from: "assign_subtopics.py"
      to: "config/thresholds.json"
      via: "argparse default reads via config loader (CLI override still wins)"
      pattern: "confidence_floor"
    - from: "utils/stage_records.py"
      to: "DDB STAGE# rows"
      via: "tunable_inputs field present-when-set"
      pattern: "tunable_inputs"
---

<objective>
Establish the Phase 12 configuration substrate: lift every operationally-tunable number into config/thresholds.json (G-18), schema-validate it on boot (D-27), document every key in a sibling markdown (D-26), and extend STAGE# records to audit which tunables a stage actually used (D-28). Also: refactor utils/env_check.py to data-driven column expectations (G-1), and file the G-37 carry-forward tracking issue on day one (D-22).

Purpose: every other Phase 12 plan reads from config/thresholds.json. This plan owns the file, the schema, the docs, the validator, and the audit-row substrate that downstream stages need to attribute their inputs.

Output: thresholds.json + thresholds.schema.json + thresholds.md + env_check.py refactor + stage_records.py additive field + G-37 tracking issue.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@.planning/ROADMAP.md
@.planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-CONTEXT.md
@.planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-RESEARCH.md
@.planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-PATTERNS.md
@.planning/issues/0001-confidence-floor-target.md

<interfaces>
<!-- Concrete values pinned by CONTEXT D-23 + RESEARCH; executor must use these verbatim. -->

config/thresholds.json EXISTING keys (preserve unchanged):
  - uncovered_score_floor: 0.4 (float)
  - low_confidence_floor: 0.35 (float) — D-25: NOT the same as confidence_floor
  - drift_uncovered_rate_alert: 0.05 (float)
  - drift_low_confidence_topic_max: 50 (int)
  - drift_window_days: 14 (int)
  - spotlight_dirty_subtopic_min: 3 (int)
  - spotlight_dirty_pubs_per_subtopic_min: 5 (int)

config/thresholds.json NEW keys (Phase 12 G-18 + D-23 + Phase 12 tunables):
  - tie_epsilon: 0.001 (float)   — lifted from assign_subtopics.py:98 TIE_EPSILON
  - confidence_floor: 0.3 (float) — lifted from assign_subtopics.py:95 DEFAULT_CONFIDENCE_FLOOR. CORRECTED VALUE per D-23 CR-2026-05-12 (not 0.35)
  - score_floor: 0.3 (float)      — lifted from assign_subtopics.py:94 SCORE_FLOOR
  - feedback_sweep_max_pmids: 200 (int)
  - recluster_persistence_days: 7 (int)
  - critic_reject_persistence_days: 90 (int) — CORRECTED default per CONTEXT CR-2026-05-12: 90-day window covers ~3 monthly spotlight cycles (Phase 10 D-10 spotlight-monthly cron). 14-day window pre-revision made per-subtopic counts meaningless (at most 1 spotlight attempt per subtopic in 14 days).
  - critic_reject_subtopic_max: 2 (int) — RENAMED from critic_reject_cwid_max per CONTEXT D-08 re-keying (per-subtopic grain, not per-cwid). Default 2 = '2 of last 3 monthly spotlights for this subtopic had a critic-failed pmid_set' — lower over-fires on intermittent failures; higher misses subtopics that fail every other cycle.
  - feedback_diagnostic_max_underlying: 20 (int) — D-32 row-size cap

Existing column-check pattern (utils/env_check.py lines 36-72):
  - results = {}, errors = [], hard sys.exit(1) on critical failure
  - inline string literals: 'external_id', 'synopsis', 'nameFirst' — to be lifted

Existing stage_records.py additive pattern (lines 215-219, Phase 11 D-13 run_id precedent):
  - kwarg defaults None
  - if X is not None: item["X"] = X
  - applies to build_complete_record / build_skipped_record / build_failed_record

D-22 G-37 tracking issue path: .planning/issues/0002-g37-e2e-test.md (next sequential after 0001-confidence-floor-target.md)
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Author thresholds.json + thresholds.schema.json + thresholds.md and the test pair</name>
  <files>config/thresholds.json, config/thresholds.schema.json, config/thresholds.md, tests/test_thresholds_schema.py, tests/test_thresholds_keys.py</files>
  <read_first>
    - config/thresholds.json (existing — must preserve all seven existing keys with current values)
    - docs/hierarchy.schema.json (analog pattern for JSON Schema 2020-12 shape; see PATTERNS.md "config/thresholds.schema.json (JSON schema, config)" section)
    - gates/schema_validation.py lines 29-63 (jsonschema.validate kwarg-style usage)
    - tests/test_event_records.py lines 1-80 (analog for thresholds-keys test docstring + import style)
    - .planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-PATTERNS.md (sections: "config/thresholds.schema.json", "config/thresholds.md", "Tests")
  </read_first>
  <behavior>
    - tests/test_thresholds_schema.py::test_well_formed_thresholds_passes_validation — load actual config/thresholds.json + schema, jsonschema.validate succeeds
    - tests/test_thresholds_schema.py::test_unknown_key_raises_validation_error — inject {"unknown_key": 1} into a copy, expect jsonschema.ValidationError because additionalProperties=false
    - tests/test_thresholds_schema.py::test_wrong_type_raises_validation_error — set confidence_floor to "0.3" (str), expect jsonschema.ValidationError
    - tests/test_thresholds_schema.py::test_negative_value_outside_minimum_raises — set feedback_sweep_max_pmids to -1, expect jsonschema.ValidationError (schema declares minimum: 1)
    - tests/test_thresholds_keys.py::test_confidence_floor_distinct_from_low_confidence_floor — D-25 anti-collapse: load thresholds.json, assert both keys present, assert cfg["confidence_floor"] == 0.3, cfg["low_confidence_floor"] == 0.35, assert cfg["confidence_floor"] != cfg["low_confidence_floor"] (different decision)
    - tests/test_thresholds_keys.py::test_phase_12_keys_present — load thresholds.json, assert every Phase 12 key from the interfaces block is present with the documented default
    - tests/test_thresholds_keys.py::test_no_default_suffix_on_confidence_floor — D-24: assert "confidence_floor_default" NOT in cfg (no override mechanism exists)
  </behavior>
  <action>
    Write `config/thresholds.json` with ALL fifteen keys: the seven existing keys (preserve current values exactly: uncovered_score_floor=0.4, low_confidence_floor=0.35, drift_uncovered_rate_alert=0.05, drift_low_confidence_topic_max=50, drift_window_days=14, spotlight_dirty_subtopic_min=3, spotlight_dirty_pubs_per_subtopic_min=5) PLUS the eight Phase 12 keys verbatim from the `<interfaces>` block above (tie_epsilon=0.001, confidence_floor=0.3 NOT 0.35, score_floor=0.3, feedback_sweep_max_pmids=200, recluster_persistence_days=7, critic_reject_persistence_days=90, critic_reject_subtopic_max=2, feedback_diagnostic_max_underlying=20). JSON only — no comments (JSON spec).

    Write `config/thresholds.schema.json` as JSON Schema Draft 2020-12 with: `"$schema": "https://json-schema.org/draft/2020-12/schema"`, `"type": "object"`, `"additionalProperties": false`, `"required": [<all fifteen keys>]`, and per-property entries with type, minimum, maximum, description. Use minimum:0 for all floats, minimum:1 for all int counts/days, maximum:1 for all float-fractions (uncovered_score_floor, low_confidence_floor, confidence_floor, score_floor, tie_epsilon, drift_uncovered_rate_alert). Every property declares a "description" string matching the one-sentence semantics from thresholds.md.

    Write `config/thresholds.md` with the structure shown in PATTERNS.md "config/thresholds.md (docs) — NO ANALOG" section: H1 title, intro paragraph explaining JSON-has-no-comments rationale, then `## Existing keys (pre-Phase 12)` with one H3 subsection per existing key, then `## Phase 12 additions` with one H3 subsection per new key. Each H3 follows the shape: `### \`key_name\` (type, default N)` + one-paragraph semantics + one-sentence "If you raise this: <consequence>". For confidence_floor, explicitly state the D-25 distinction from low_confidence_floor (assignment-time membership vs event-emission). Reference `.planning/issues/0001-confidence-floor-target.md` from the confidence_floor section as the open tuning question.

    Write `tests/test_thresholds_schema.py` with imports `import json`, `import jsonschema`, `from pathlib import Path`, `import pytest`. Module docstring lists every test case bulleted. Each test loads config/thresholds.json + config/thresholds.schema.json from REPO_ROOT = Path(__file__).resolve().parents[1], calls jsonschema.validate(instance=cfg, schema=schema), and asserts either success or `pytest.raises(jsonschema.ValidationError)` with the expected violation.

    Write `tests/test_thresholds_keys.py` with module docstring listing the four behaviors. Load thresholds.json via json.load. The D-25 anti-collapse test is the load-bearing one — its docstring must say "If this test fails, Phase 12 D-25 has been regressed: confidence_floor and low_confidence_floor were collapsed into a single key. They represent different decisions (assignment-time membership vs event-emission threshold). Restore both keys with distinct values."

    Commit message: `feat(12-thresholds): add config/thresholds.json schema + sibling docs + audit tests` (no Co-Authored-By per project rules).
  </action>
  <verify>
    <automated>cd /Users/paulalbert/Dropbox/GitHub/ReciterAI && pytest tests/test_thresholds_schema.py tests/test_thresholds_keys.py -x</automated>
  </verify>
  <acceptance_criteria>
    - `python -c "import json; cfg=json.load(open('config/thresholds.json')); assert cfg['confidence_floor']==0.3 and cfg['low_confidence_floor']==0.35 and cfg['confidence_floor']!=cfg['low_confidence_floor']"` exits 0
    - `python -c "import json; cfg=json.load(open('config/thresholds.json')); req=['tie_epsilon','confidence_floor','score_floor','feedback_sweep_max_pmids','recluster_persistence_days','critic_reject_persistence_days','critic_reject_subtopic_max','feedback_diagnostic_max_underlying']; [cfg[k] for k in req]"` exits 0
    - `python -c "import json,jsonschema; jsonschema.validate(instance=json.load(open('config/thresholds.json')), schema=json.load(open('config/thresholds.schema.json')))"` exits 0
    - `grep -v '^#' config/thresholds.md | grep -c '^### ' ` returns a count >= 15 (one H3 per key)
    - `grep -c 'confidence_floor' config/thresholds.md` returns count >= 2 (key entry + D-25 distinction reference)
    - `grep -c 'low_confidence_floor' config/thresholds.md` returns count >= 2 (own entry + reference in confidence_floor section)
    - `grep -c '0001-confidence-floor-target' config/thresholds.md` returns count >= 1 (link to open issue)
    - `pytest tests/test_thresholds_schema.py tests/test_thresholds_keys.py -x` exits 0
    - File `config/thresholds.json` does NOT contain the key `confidence_floor_default` (D-24: `grep -c "confidence_floor_default" config/thresholds.json` returns 0)
    - File `config/thresholds.schema.json` contains `"additionalProperties": false` (grep returns count >= 1)
  </acceptance_criteria>
  <done>Schema-validated thresholds.json with all fifteen keys; sibling thresholds.md documents every key; test pair passes; D-25 anti-collapse assertion locked.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Refactor utils/env_check.py (G-1 + D-27) and lift assign_subtopics.py constants</name>
  <files>utils/env_check.py, assign_subtopics.py, tests/test_env_check_thresholds.py</files>
  <read_first>
    - utils/env_check.py (full file — see existing `# --- Check N ---` block structure, results/errors twin lists, sys.exit(1) hard-fail pattern)
    - assign_subtopics.py lines 94-98 (the three constants: SCORE_FLOOR, DEFAULT_CONFIDENCE_FLOOR, TIE_EPSILON) and line 1022 (the argparse `--confidence-floor` default reader — flag override still wins)
    - gates/schema_validation.py (jsonschema usage idiom: kwargs, catch jsonschema.ValidationError)
    - config/thresholds.json + config/thresholds.schema.json (from Task 1)
    - .planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-PATTERNS.md "utils/env_check.py" section
  </read_first>
  <behavior>
    - tests/test_env_check_thresholds.py::test_load_thresholds_returns_dict — call a new exported helper `load_thresholds()` or read directly; assert cfg["confidence_floor"] == 0.3, cfg["score_floor"] == 0.3, cfg["tie_epsilon"] == 0.001
    - tests/test_env_check_thresholds.py::test_assign_subtopics_constants_read_from_config — import assign_subtopics; assert assign_subtopics.SCORE_FLOOR == 0.3, assign_subtopics.DEFAULT_CONFIDENCE_FLOOR == 0.3, assign_subtopics.TIE_EPSILON == 0.001; assert these are read from config/thresholds.json at module load (mock the file with different values, reload, assert constants pick up the new values)
    - tests/test_env_check_thresholds.py::test_cli_flag_still_overrides — argparse `--confidence-floor 0.45` flag still overrides the config-derived default (read the argparse `default=` callable / module-level value at parse time)
    - tests/test_env_check_thresholds.py::test_expected_columns_is_data_driven — assert `utils.env_check.EXPECTED_COLUMNS` is a dict mapping table_name -> list[str]; assert len(EXPECTED_COLUMNS) >= 3 (reciterai_synopsis, analysis_summary_person, reciterai_keyword_relevance); assert 'external_id' in EXPECTED_COLUMNS['reciterai_synopsis']
  </behavior>
  <action>
    In `utils/env_check.py`:

    1. Add a module-level `EXPECTED_COLUMNS: dict[str, list[str]]` constant near the top after imports. Populate from the existing hardcoded string-literal checks: `{"reciterai_synopsis": ["external_id"], "analysis_summary_person": [<the columns currently checked inline>], "reciterai_keyword_relevance": [<the columns currently checked inline>]}`. Read the existing file to extract the exact column names — DO NOT invent. Refactor each `# --- Check N: <table> columns ---` block to loop over `EXPECTED_COLUMNS[table_name]` rather than reference inline literals.

    2. Add a new check section `# --- Check 5: thresholds.json schema validation ---` AFTER the existing column checks. Imports at top: `import json`, `import jsonschema`. Constants: `THRESHOLDS_FILE = REPO_ROOT / "config/thresholds.json"` and `THRESHOLDS_SCHEMA = REPO_ROOT / "config/thresholds.schema.json"` (introduce `REPO_ROOT = Path(__file__).resolve().parents[1]` if not already present). Load both, call `jsonschema.validate(instance=cfg, schema=schema)`, on success append `results['thresholds.json'] = 'schema-valid'`, on `jsonschema.ValidationError as err` append `errors.append(f"CRITICAL: thresholds.json invalid at {list(err.absolute_path)}: {err.message}")`.

    3. Export `load_thresholds() -> dict[str, Any]` as a module-level helper that returns `json.loads((REPO_ROOT / "config/thresholds.json").read_text(encoding="utf-8"))`. If the file is missing, raise `FileNotFoundError` with a clear "Run from repo root or check config/thresholds.json exists" message — do NOT silently return defaults (the env check is the place to fail fast).

    In `assign_subtopics.py`:

    1. Replace `SCORE_FLOOR = 0.3`, `DEFAULT_CONFIDENCE_FLOOR = 0.3`, `TIE_EPSILON = 0.001` with module-level lookups: `from utils.env_check import load_thresholds; _CFG = load_thresholds(); SCORE_FLOOR = float(_CFG["score_floor"]); DEFAULT_CONFIDENCE_FLOOR = float(_CFG["confidence_floor"]); TIE_EPSILON = float(_CFG["tie_epsilon"])`.

    2. Preserve the argparse `default=DEFAULT_CONFIDENCE_FLOOR` at line 1022 — the CLI flag still overrides per D-23 final sentence. No other code changes; the existing references downstream use the constants by name.

    3. Add a comment above the lookup block: `# Phase 12 G-18: tunables lifted to config/thresholds.json. CLI --confidence-floor still overrides per D-23.`

    Write `tests/test_env_check_thresholds.py` with the four behaviors above. Use `unittest.mock.patch` to swap in a temp thresholds.json for the reload test; use `importlib.reload(assign_subtopics)` to validate the module re-reads.

    Commit message: `refactor(12-thresholds): lift assign_subtopics constants to config + env_check schema validation + G-1 data-driven columns`.
  </action>
  <verify>
    <automated>cd /Users/paulalbert/Dropbox/GitHub/ReciterAI && pytest tests/test_env_check_thresholds.py -x</automated>
  </verify>
  <acceptance_criteria>
    - `grep -n "EXPECTED_COLUMNS" utils/env_check.py` returns count >= 2 (definition + at least one use site)
    - `grep -vE '^[[:space:]]*#' utils/env_check.py | grep -E "'external_id'|\"external_id\"" | grep -v EXPECTED_COLUMNS` returns 0 matches (G-1 lifted the literal; remaining references are inside the EXPECTED_COLUMNS structure)
    - `grep -n "jsonschema.validate" utils/env_check.py` returns count >= 1
    - `grep -n "thresholds.schema.json\|THRESHOLDS_SCHEMA" utils/env_check.py` returns count >= 1
    - `grep -n "load_thresholds" utils/env_check.py` returns count >= 1 (definition)
    - `grep -n "load_thresholds" assign_subtopics.py` returns count >= 1 (import + call)
    - `grep -E "^SCORE_FLOOR = 0\\.3$|^DEFAULT_CONFIDENCE_FLOOR = 0\\.3$|^TIE_EPSILON = 0\\.001$" assign_subtopics.py` returns 0 matches (constants now resolved via load_thresholds, not literal)
    - `python -c "import assign_subtopics; assert assign_subtopics.SCORE_FLOOR == 0.3 and assign_subtopics.DEFAULT_CONFIDENCE_FLOOR == 0.3 and assign_subtopics.TIE_EPSILON == 0.001"` exits 0
    - `pytest tests/test_env_check_thresholds.py -x` exits 0
    - `pytest tests/test_env_check_thresholds.py tests/test_thresholds_schema.py tests/test_thresholds_keys.py -x` exits 0 (no regression on Task 1 tests)
  </acceptance_criteria>
  <done>env_check.py is data-driven for columns AND validates thresholds.json on boot; assign_subtopics.py constants resolve from config; CLI flag override still works; test suite green.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 3: Add tunable_inputs field to STAGE# builders (D-28) and file G-37 tracking issue (D-22)</name>
  <files>utils/stage_records.py, tests/test_stage_records.py, .planning/issues/0002-g37-e2e-test.md</files>
  <read_first>
    - utils/stage_records.py lines 195-219 (existing optional-kwarg additive pattern from Phase 11 D-13 run_id)
    - tests/test_stage_records.py (existing test file — extend; do NOT rewrite)
    - .planning/issues/0001-confidence-floor-target.md (template/frontmatter shape for issue file)
    - .planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-PATTERNS.md "utils/stage_records.py tunable_inputs extension" section
  </read_first>
  <behavior>
    - tests/test_stage_records.py::test_build_complete_record_omits_tunable_inputs_when_none — call build_complete_record without tunable_inputs kwarg; assert "tunable_inputs" NOT in result (backwards-compat: absent when None)
    - tests/test_stage_records.py::test_build_complete_record_includes_tunable_inputs_when_set — call with tunable_inputs={"confidence_floor": 0.3, "confidence_floor_source": "config"}; assert result["tunable_inputs"] == that dict
    - tests/test_stage_records.py::test_build_skipped_record_supports_tunable_inputs — same behavior for build_skipped_record
    - tests/test_stage_records.py::test_build_failed_record_supports_tunable_inputs — same behavior for build_failed_record
    - tests/test_stage_records.py::test_tunable_inputs_source_discriminator_values — values for `<key>_source` are one of {"config","cli","default"} (free-form string is allowed but D-28 documents the three values; assert all three are accepted)
  </behavior>
  <action>
    In `utils/stage_records.py`:

    1. Add `tunable_inputs: dict[str, Any] | None = None` as a new keyword-only parameter to all three builders (`build_complete_record`, `build_skipped_record`, `build_failed_record`). Position it after `run_id` to follow the existing additive ordering convention.

    2. In each builder body, after the existing `if run_id is not None: item["run_id"] = run_id` line, add `if tunable_inputs is not None: item["tunable_inputs"] = dict(tunable_inputs)` (dict() copy so callers can mutate after passing without aliasing).

    3. Update each builder's docstring with a new paragraph: "Phase 12 D-28: `tunable_inputs` is an optional input-audit dict carrying tunable values read from `config/thresholds.json` and CLI overrides. Convention: keys are tunable names (e.g., 'confidence_floor'), plus a parallel `<key>_source` discriminator with one of {'config','cli','default'}. Same backwards-compatible additive pattern as `run_id` (Phase 11 D-13) — omitted when None."

    Extend `tests/test_stage_records.py` (do not rewrite the file — append new tests at the bottom). Mirror the existing test style: fully literal expected dicts, MagicMock not used for builders since they're pure functions.

    Write `.planning/issues/0002-g37-e2e-test.md` with frontmatter mirroring 0001 (issue, title, status: open, filed: 2026-05-12, filed_by: Phase 12 planning, related_phases: [12, 13]). Body sections: ## Context (G-37 is one bounded E2E test per D-21; D-19 carves it out as a separately-gated final task per D-20), ## Gating contract (per D-20: starts only after pytest exits 0 on every other Phase 12 deliverable on main), ## Deliverable (per D-21: stripped-down fixture corpus through cold path from generate_taxonomy.py to pipeline_hierarchy/publish.py; assert manifest validates against schema; assert byte-identical hierarchy.json on second run leveraging Phase 11 G-29 fix), ## Carry-forward (if G-37 ships in Phase 12, this issue closes with the phase; if it slips, the issue carries forward with full context), ## Fixture corpus shape (2 topics × 3 subtopics × 5 pubs = 30 pubs, planned in Plan g37-e2e before test code lands per RESEARCH Pitfall 4), ## References (link to phases/12-feedback-loops-both-aggregations-residual-hygiene/12-PLAN-g37-e2e.md, CONTEXT D-19/D-20/D-21/D-22, RESEARCH F-7).

    Commit message: `feat(12-thresholds): add tunable_inputs audit field to STAGE# builders + file G-37 tracking issue`.
  </action>
  <verify>
    <automated>cd /Users/paulalbert/Dropbox/GitHub/ReciterAI && pytest tests/test_stage_records.py -x</automated>
  </verify>
  <acceptance_criteria>
    - `grep -c "tunable_inputs" utils/stage_records.py` returns count >= 6 (three signature params + three conditional-attach lines, minimum)
    - `grep -c "tunable_inputs" tests/test_stage_records.py` returns count >= 4 (one per added test scenario)
    - `pytest tests/test_stage_records.py -x` exits 0
    - File `.planning/issues/0002-g37-e2e-test.md` exists and starts with `---\nissue: 0002`
    - `grep -c "G-37" .planning/issues/0002-g37-e2e-test.md` returns count >= 3 (referenced multiple times throughout)
    - `grep -c "byte-identical" .planning/issues/0002-g37-e2e-test.md` returns count >= 1 (deliverable invariant captured)
    - `pytest tests/test_thresholds_schema.py tests/test_thresholds_keys.py tests/test_env_check_thresholds.py tests/test_stage_records.py -x` exits 0
  </acceptance_criteria>
  <done>STAGE# builders carry the tunable_inputs audit field (absent when None, present when set); G-37 tracking issue exists in .planning/issues/ from day one with carry-forward context; backwards-compatible with all existing call sites.</done>
</task>

</tasks>

<verification>
- All Task 1 + Task 2 + Task 3 acceptance criteria green
- `pytest tests/test_thresholds_schema.py tests/test_thresholds_keys.py tests/test_env_check_thresholds.py tests/test_stage_records.py -x` exits 0
- `grep -c "tunable_inputs" utils/stage_records.py` >= 6
- `grep -c "EXPECTED_COLUMNS" utils/env_check.py` >= 2
- No regressions on prior phase tests: `pytest tests/test_event_records.py tests/test_low_confidence_event.py tests/test_uncovered_pmid_event.py -x` exits 0
</verification>

<success_criteria>
Every Phase 12 module that needs a tunable reads it from `config/thresholds.json`; schema validates on boot; documentation exists for every key; STAGE# records can attribute their inputs; G-37 carry-forward state is queryable from day one of Phase 12.
</success_criteria>

<output>
After completion, create `.planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-thresholds-substrate-SUMMARY.md`.
</output>

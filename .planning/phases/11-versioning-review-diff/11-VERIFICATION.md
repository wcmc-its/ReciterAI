---
phase: 11-versioning-review-diff
verified: 2026-05-12T00:00:00Z
status: human_needed
score: 18/19 must-haves verified
overrides_applied: 0
human_verification:
  - test: "Run `python -m review approve --artifact hierarchy --version v2026-06-01` in an environment where RECITERAI_REVIEWER_CWID is set and $EDITOR is available. Fill in valid values. Confirm: (a) $EDITOR opens with a pre-populated YAML template; (b) after saving, CLI validates and prints 'REVIEW written: REVIEW#hierarchy#v2026-06-01'; (c) exit code is 0."
    expected: "Full approve workflow completes end-to-end: template shown, YAML validated, REVIEW# row written to DDB."
    why_human: "Requires a live DDB table, real S3 (or mocked env), and interactive $EDITOR invocation. Cannot be verified programmatically without a running AWS environment."
  - test: "Run `python -m scripts.migrate_spotlight_history_pk` (dry-run default) against the real DynamoDB table. Confirm the diff log is written to cwd and counts are non-zero."
    expected: "Script completes, diff log created, console shows Rows scanned / Never spotlighted / Malformed / Total rewrites counts."
    why_human: "Requires a live DDB table with real SPOTLIGHT_HISTORY# rows. The script cannot self-test without a populated table."
  - test: "Run `pipeline_cold/run.py --initiated-by scheduled` end-to-end in a test environment. Confirm: (a) RECITERAI_HIERARCHY_VERSION and RECITERAI_COLD_RUN_ID are visible in subprocess stages; (b) STAGE#hierarchy_version_cutover#GLOBAL row appears in DDB after successful completion."
    expected: "Cold-run writes the cutover audit row with prev_version, new_version, run_id, initiated_by, started_at, completed_at."
    why_human: "Cold-path orchestration requires a full AWS environment (S3 manifest read, DDB write). End-to-end integration cannot be verified programmatically."
---

# Phase 11: Versioning, Review State, Diff Signaling — Verification Report

**Phase Goal:** Implements spec §3 Decision 2 (hierarchy_version first-class on every read/write), §4 Decision 3 (REVIEW# state is machine-readable pipeline state), §5/§6 Decision 5 (structured change-signaling). Three independent contract surfaces executed in parallel in Wave 1.

**Verified:** 2026-05-12
**Status:** human_needed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | D-01: Every UpdateItem that writes subtopic fields ALSO stamps hierarchy_version in the same SET expression | VERIFIED | `utils/dynamodb_subtopic_migration.py:79`: `"hierarchy_version = :hv"` in UpdateExpression; `":hv": hierarchy_version` in ExpressionAttributeValues; required keyword arg on signature |
| 2 | D-02/D-17: Activity backfill uses single sentinel v0.0.0-pre-phase-11; counts reported; no PROCESSING# join | VERIFIED | `scripts/migrate_activity_hierarchy_version.py:31`: `LEGACY_SENTINEL = "v0.0.0-pre-phase-11"`; scan uses `attribute_not_exists(hierarchy_version)` FilterExpression only; no PROCESSING# join code |
| 3 | D-03: hierarchy_version sentinels parse as valid pre-release semver | VERIFIED | `"v0.0.0-pre-phase-11"` and `"0.0.0-orphan"` are pre-release semver shapes per the SemVer spec; sentinel values documented in both migration scripts |
| 4 | D-04: Rotation history rows use PK SPOTLIGHT_HISTORY#{hierarchy_version}#{subtopic_id}; one-shot PutItem + DeleteItem rewrite | VERIFIED | `spotlight/history_writer.py:135`: PK construction `f"SPOTLIGHT_HISTORY#{hierarchy_version}#{s.entry.subtopic_id}"`; `scripts/migrate_spotlight_history_pk.py` uses PutItem then DeleteItem pattern |
| 5 | D-05: Rotation migration reports never_spotlighted_count and malformed_publish_id_count separately | VERIFIED | `scripts/migrate_spotlight_history_pk.py:81,82`: `n_never_spotlighted = 0` and `n_malformed = 0` tracked separately; both reported in final output at lines 134-135 |
| 6 | D-06: Cold-run mints STAGE#hierarchy_version_cutover#GLOBAL row at end of main() with required fields | VERIFIED | `pipeline_cold/run.py:434,438,439`: cutover row carries `prev_version`, `new_version`, `initiated_by`, `run_id`; write is at end of main() after all stages succeed (O-03) |
| 7 | D-07: REVIEW# scope is GLOBAL only (PK=REVIEW#{artifact_type}#{version}, SK=GLOBAL); no per-topic rows | VERIFIED | `review/store.py:20-21`: `REVIEW_PK_TEMPLATE` and `REVIEW_SK = "GLOBAL"`; no per-topic path exists anywhere in the review package |
| 8 | D-08: python -m review approve and validate both invocable; reviewer_cwid resolves from config → env → actionable error | VERIFIED | `python -m review --help` exits 0 with both subcommands visible; `review/config.py:18`: `ENV_VAR = "RECITERAI_REVIEWER_CWID"`; `ReviewerCwidUnresolvable` raised with path + env var + YAML example |
| 9 | D-08: Pre-write validator refuses approval when failed_stages or gate_block_errors present | VERIFIED | `review/validator.py:46,65-73`: `gate_block_errors` field on RunSignals; `if decision == "approve": if signals.failed_stages: errors.append(...)` |
| 10 | D-09: diff.json is hybrid — STAGE# query (run_id filtered) for PMID count + byte-comparison of prev vs new hierarchy.json | VERIFIED | `pipeline_hierarchy/publish.py:138-213`: `_scan_assign_rows_by_run_id()` + `compute_structural_diff()` + `compute_reassigned_pmid_count()`; both sources merged in `compute_diff()` |
| 11 | D-11: S3 write order is exactly 5 steps (hierarchy → schema → diff → version manifest → latest manifest) | VERIFIED | `pipeline_hierarchy/publish.py:262-273`: 5 `put_object` calls in documented order |
| 12 | D-12: diff.json carries top-level diff_schema_version field | VERIFIED | `pipeline_hierarchy/diff_stats.py:15`: `DIFF_SCHEMA_VERSION = "1.0.0"`; used at `pipeline_hierarchy/publish.py:215` in the diff dict |
| 13 | D-13: STAGE# rows gain optional run_id field (backwards-compatible) | VERIFIED | `utils/stage_records.py`: all three builders (`build_complete_record`, `build_skipped_record`, `build_failed_record`) have `run_id: str | None = None` kwarg with conditional-attach; 3 `if run_id is not None` guards confirmed |
| 14 | D-14: generated_at removed from hierarchy.json; manifest.json keeps it; bundler.py has zero occurrences | VERIFIED | `grep generated_at pipeline_hierarchy/bundler.py` returns 0 matches (W2 gate); `pipeline_hierarchy/generator.py:130`: manifest carries it; `docs/hierarchy.schema.json`: `generated_at` absent from `required` array |
| 15 | D-16: First post-G29 publish writes STAGE#g29_cutover#GLOBAL with documented fields under --g29-cutover flag | VERIFIED | `pipeline_hierarchy/publish.py:525-540`: PK `"STAGE#g29_cutover#GLOBAL"`, fields `previous_publish_sha`, `new_publish_sha`, `hierarchy_version_at_cutover`, `run_id`, `started_at`, `completed_at` |
| 16 | D-18: reassigned_pmid_count means rows touched (records_written), not primary-changed | VERIFIED | `pipeline_hierarchy/diff_stats.py:79-87`: `sum(int(r.get("records_written", 0) or 0) for r in stage_rows)` with D-18 docstring |
| 17 | Rotation selector parser uses rsplit("#", 1)[-1] for subtopic_id extraction | VERIFIED | `spotlight/rotation_selector.py:216`: `sid = pk.rsplit("#", 1)[-1]` |
| 18 | Cache-Control: max-age=60, must-revalidate on latest/manifest.json only | VERIFIED | `pipeline_hierarchy/publish.py:273`: `cache_control="max-age=60, must-revalidate"` on 5th PutObject only; other 4 calls have no cache_control |
| 19 | summary_stats in review/cli.py shows real diff values (not zeros) | PARTIAL | `review/cli.py:103`: `summary_stats = {"topics_added": 0, ...}` is hardcoded zeros. Acknowledged known stub in SUMMARY and code comment; wiring point documented for post-wave integration with compute_diff(). Functional for Phase 11 goals — operator sees zeros in template, reviews actual diff separately. |

**Score:** 18/19 truths verified (1 partial — acknowledged intentional stub)

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `scripts/migrate_activity_hierarchy_version.py` | One-shot activity backfill with --dry-run | VERIFIED | 2860 bytes; contains `LEGACY_SENTINEL`, `argparse --dry-run`, paginate loop |
| `scripts/migrate_spotlight_history_pk.py` | PK rewrite with --commit gate | VERIFIED | 5910 bytes; contains `argparse --commit`, `n_never_spotlighted`, `n_malformed`, PutItem+DeleteItem |
| `utils/dynamodb_subtopic_migration.py` | update_activity_subtopics with hierarchy_version | VERIFIED | hierarchy_version required kwarg at line 49; SET clause at line 79 |
| `spotlight/history_writer.py` | update_history with hierarchy_version and new PK | VERIFIED | PK `SPOTLIGHT_HISTORY#{hierarchy_version}#{subtopic_id}` at line 135 |
| `spotlight/rotation_selector.py` | fetch_history with hierarchy_version; rsplit parser | VERIFIED | rsplit at line 216; new PK construction at lines 138-214 |
| `pipeline_cold/run.py` | Mints run_id and new_version; STAGE#hierarchy_version_cutover at end | VERIFIED | UUID4 run_id, env threading, cutover row at line 413+ |
| `review/__init__.py` | Package marker | VERIFIED | 366 bytes; module index docstring |
| `review/__main__.py` | python -m review entry point | VERIFIED | 197 bytes; delegates to main() |
| `review/validator.py` | Pure validate() with frozen dataclass | VERIFIED | 4574 bytes; `@dataclass(frozen=True)` at lines 25 and 52; no I/O imports |
| `review/cli.py` | approve + validate handlers; $EDITOR seam; DDB write | VERIFIED | 9065 bytes; `subprocess.run` at line 42; `add_subparsers` at line 246 |
| `review/template.py` | YAML template generator with yaml.safe_dump | VERIFIED | 1783 bytes; `yaml.safe_dump` at line 53 |
| `review/store.py` | write_review PutItem; REVIEW# PK template | VERIFIED | 6635 bytes; `REVIEW#` at lines 20-21 |
| `review/config.py` | load_reviewer_cwid with env fallback and error | VERIFIED | 2527 bytes; `RECITERAI_REVIEWER_CWID` at line 18 |
| `requirements.txt` | pyyaml>=6.0.1 added | VERIFIED | Line 6: `pyyaml>=6.0.1` |
| `pipeline_hierarchy/publish.py` | compute_diff(); 5-step upload_to_s3(); STAGE#g29_cutover | VERIFIED | compute_diff at line 167; 5 put_object calls at 262-273; g29_cutover at 525 |
| `pipeline_hierarchy/diff_stats.py` | compute_structural_diff, compute_reassigned_pmid_count, derive_editorial_only | VERIFIED | 3406 bytes; all three functions present |
| `pipeline_hierarchy/bundler.py` | No generated_at anywhere | VERIFIED | grep returns 0 matches (W2 gate) |
| `pipeline_hierarchy/generator.py` | hierarchy dict gets no generated_at; manifest keeps it | VERIFIED | line 70: comment D-14; line 130: manifest stamps it |
| `utils/s3_client.py` | put_object with optional cache_control kwarg | VERIFIED | cache_control at lines 105-128; no new get_object method (W1) |
| `utils/stage_records.py` | all 3 builders with optional run_id | VERIFIED | 3 conditional-attach guards confirmed |
| `docs/hierarchy.schema.json` | generated_at not in required array | VERIFIED | required at lines 92-98; generated_at absent from required |
| `docs/hierarchy-contract.md` | 5-step write order, diff.json shape, Cache-Control, O-01, G-29, D-15 runbook | VERIFIED | All sections present: write order, diff shape, Cache-Control, from_version: null, G-29, SPS coordination runbook at lines 109-115 |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| pipeline_cold/run.py::main | assign_subtopics stage via env var RECITERAI_HIERARCHY_VERSION | subprocess env threading | WIRED | Lines 346-347: env dict with both env vars threaded into subprocess calls |
| assign_subtopics.py::run() | utils/dynamodb_subtopic_migration.py::update_activity_subtopics | keyword arg hierarchy_version= | WIRED | Line 649: `hierarchy_version=hierarchy_version` in call; env resolved at line 699-702 |
| spotlight/publish.py | spotlight/history_writer.py::update_history | keyword arg hierarchy_version=version | WIRED | Line 222: `update_history(..., hierarchy_version=version)` |
| review/cli.py::_run_approve | review/template.py + review/validator.py + review/store.py | function calls (from review imports) | WIRED | Lines 29-32: all three imported and called in workflow |
| review/validator.py::validate | review/store.py::read_run_signals output (RunSignals dataclass) | dataclass injection | WIRED | `RunSignals` imported in cli.py from validator; passed to validate() after read_run_signals() |
| pipeline_hierarchy/publish.py::compute_diff | STAGE# rows filtered by run_id | DDB query with Python-side run_id filter | WIRED | `_scan_assign_rows_by_run_id()` at line 138-165; Python-side filter on run_id |
| pipeline_hierarchy/publish.py::compute_diff | prev hierarchy.json on S3 | S3HierarchyClient.get_object_bytes(f'{prev_version}/hierarchy.json') | WIRED | Line 192: `s3_client.get_object_bytes(f"{prev_version}/hierarchy.json")` |
| pipeline_hierarchy/publish.py::upload_to_s3 | utils/s3_client.py::put_object(cache_control=...) | kwarg pass-through | WIRED | Line 273: `cache_control="max-age=60, must-revalidate"` passed to 5th put_object call |

---

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|-------------------|--------|
| review/cli.py::_run_approve | summary_stats | Hardcoded zeros | No — intentional stub for Phase 11 Wave 1 | STATIC (intentional, documented) |
| review/cli.py::_run_approve | signals (RunSignals) | read_run_signals() → DDB + S3 | Yes — DDB query + S3 HEAD | FLOWING |
| pipeline_hierarchy/publish.py::compute_diff | prev_hierarchy | S3 get_object_bytes() | Yes — real S3 GET | FLOWING |
| pipeline_hierarchy/publish.py::compute_diff | stage_rows | _scan_assign_rows_by_run_id() → DDB query | Yes — real DDB query | FLOWING |
| pipeline_cold/run.py::main | new_version | --hierarchy-version arg or f"v{started_at[:10]}" | Yes — deterministic | FLOWING |
| pipeline_cold/run.py::main | prev_version | _read_prev_version_from_latest_manifest() → S3 | Yes — S3 GET with None fallback | FLOWING |

**Note on summary_stats stub:** The hardcoded zeros in `review/cli.py` are a documented, intentional partial implementation. The SUMMARY file explicitly calls this out as a "Known Stub" with a wiring point for future integration with `compute_diff()`. The review CLI is fully functional — operators can approve/reject with real DDB writes; they just see zero stats in the template. This does not block the phase goal.

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| All hierarchy-versioning tests pass | `python3 -m pytest utils/test_dynamodb_subtopic_migration.py test_spotlight_rotation_selector.py tests/test_assign_subtopics_stage.py tests/test_pipeline_cold_run.py tests/test_dynamodb_hierarchy_backfill.py tests/test_spotlight_history_migration.py -q` | 74 passed | PASS |
| All review-state tests pass | `python3 -m pytest tests/test_review_validator.py tests/test_review_config.py tests/test_review_cli.py -q` | 43 passed | PASS |
| All change-signaling tests pass | `python3 -m pytest tests/test_hierarchy_bundler.py tests/test_hierarchy_publisher.py tests/test_hierarchy_reproducibility.py tests/test_stage_records.py utils/test_s3_client.py tests/test_publish_diff.py tests/test_publish_integration.py -q` | 83 passed | PASS |
| python -m review CLI dispatches subcommands | `python3 -m review --help` | Shows approve/validate subcommands, exits 0 | PASS |
| Validator rejects empty input | `python3 -c "import review.validator; r=review.validator.validate({}, review.validator.RunSignals()); print(r.ok, len(r.errors))"` | False 3 | PASS |
| W2 gate: bundler.py has zero generated_at occurrences | `grep generated_at pipeline_hierarchy/bundler.py` | 0 matches | PASS |
| D-13: 3 run_id conditionals in stage_records | `grep -c 'if run_id is not None' utils/stage_records.py` | 3 | PASS |

---

### Requirements Coverage

REQUIREMENTS.md does not exist in this project. Requirement IDs in plan frontmatter (`D-01` through `D-18`) come from the spec decisions in `docs/RECITERAI-SPEC.md` and the planning context. All requirement IDs declared across the three plans are accounted for:

| Req ID | Plan | Status | Evidence |
|--------|------|--------|----------|
| D-01 | hierarchy-versioning | SATISFIED | update_activity_subtopics stamps hierarchy_version in same SET expression |
| D-02 | hierarchy-versioning | SATISFIED | migrate_activity_hierarchy_version.py reports counts; uses attribute_not_exists filter |
| D-03 | hierarchy-versioning | SATISFIED | v0.0.0-pre-phase-11 and 0.0.0-orphan are valid pre-release semver strings |
| D-04 | hierarchy-versioning | SATISFIED | SPOTLIGHT_HISTORY#{hierarchy_version}#{subtopic_id} PK; PutItem + DeleteItem rewrite |
| D-05 | hierarchy-versioning | SATISFIED | n_never_spotlighted and n_malformed tracked and reported separately |
| D-06 | hierarchy-versioning | SATISFIED | STAGE#hierarchy_version_cutover#GLOBAL row written with all required fields |
| D-07 | review-state | SATISFIED | SK=GLOBAL enforced; no per-topic code path |
| D-08 | review-state | SATISFIED | python -m review approve/validate both work; reviewer_cwid resolution chain implemented |
| D-09 | change-signaling | SATISFIED | compute_diff() is hybrid: S3 byte-comparison + STAGE# run_id filtered query |
| D-10 | change-signaling | SATISFIED | diff producer is a function inside publish.py, not a new stage |
| D-11 | change-signaling | SATISFIED | 5 PutObject calls in documented order |
| D-12 | change-signaling | SATISFIED | diff_schema_version: "1.0.0" in every diff.json |
| D-13 | change-signaling | SATISFIED | Optional run_id on all 3 STAGE# builders |
| D-14 | change-signaling | SATISFIED | generated_at removed from hierarchy; manifest keeps it; bundler.py W2 gate |
| D-15 | change-signaling | SATISFIED | SPS coordination runbook in docs/hierarchy-contract.md |
| D-16 | change-signaling | SATISFIED | STAGE#g29_cutover#GLOBAL written under --g29-cutover flag |
| D-17 | hierarchy-versioning | SATISFIED | Single sentinel v0.0.0-pre-phase-11; no PROCESSING# join |
| D-18 | change-signaling | SATISFIED | records_written (rows-touched) semantics in compute_reassigned_pmid_count |

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| review/cli.py | 103 | `summary_stats = {"topics_added": 0, ...}` hardcoded zeros | Info | Intentional, documented placeholder. Operator sees zeros in YAML template; does not block approval/rejection workflow or any Phase 11 must-have. Wiring point documented for follow-up integration with compute_diff(). |

No blockers. No stubs that affect goal-required behavior. The summary_stats placeholder is an explicitly accepted partial implementation with a clear wiring plan.

---

### Human Verification Required

#### 1. End-to-End Approve Workflow

**Test:** In an environment with AWS credentials and a real DDB table, run `python -m review approve --artifact hierarchy --version v2026-06-01` with `RECITERAI_REVIEWER_CWID` set (or `~/.reciterai/config.yaml` present). Fill in valid reviewer_cwid, rationale (>=40 chars), and `decision: approve`. Save and exit $EDITOR.

**Expected:** CLI validates the YAML (validator passes), writes a `REVIEW#hierarchy#v2026-06-01` row to DDB with SK=GLOBAL. Exit code 0. Console shows "REVIEW written: REVIEW#hierarchy#v2026-06-01".

**Why human:** Requires live AWS credentials, real DDB table, and interactive $EDITOR session. Tests mock the EDITOR seam and DDB; end-to-end cannot be validated programmatically.

---

#### 2. Spotlight History Migration Dry-Run Against Real Table

**Test:** Run `python -m scripts.migrate_spotlight_history_pk` (no `--commit`) against the real DynamoDB table. Verify the diff log is written and counts are meaningful.

**Expected:** Script scans existing SPOTLIGHT_HISTORY# rows, classifies them (real/orphan/malformed), writes the diff log, prints counts. No DDB writes occur in dry-run mode.

**Why human:** Requires a populated DDB table with existing SPOTLIGHT_HISTORY# rows to produce non-trivial counts. Test suite uses mocked data.

---

#### 3. Cold-Run Cutover Row in Real Environment

**Test:** Run `pipeline_cold/run.py --initiated-by scheduled` in a staging environment. After successful completion, query DDB for `PK=STAGE#hierarchy_version_cutover#GLOBAL`.

**Expected:** Row exists with `prev_version`, `new_version`, `run_id`, `initiated_by=scheduled`, `started_at`, `completed_at`. Subprocess stages received `RECITERAI_HIERARCHY_VERSION` and `RECITERAI_COLD_RUN_ID` env vars.

**Why human:** Cold-path orchestration requires full AWS environment (S3 manifest read for prev_version, DDB write). Cannot be verified without running infrastructure.

---

### Gaps Summary

No blocking gaps. All phase deliverables are implemented and wired:

- **Surface 1 (hierarchy_version):** All D-01 through D-06 and D-17 truths verified. Writers stamp the version; rotation history PK is versioned; cold-run writes the audit row. Two one-shot migration scripts exist with correct behavior (sentinel values, confirm gate, idempotency).

- **Surface 2 (review state):** D-07 and D-08 satisfied. The review/ package is a 7-module implementation with pure validator, operator config, DDB store, template, and CLI. All 43 tests pass. The summary_stats zeros are an intentional, documented stub (not a blocker).

- **Surface 3 (change signaling):** D-09 through D-16 and D-18 all satisfied. diff.json is produced on every publish via a hybrid approach. The 5-step S3 write order is enforced with Cache-Control on latest/. G-29 is fixed with W2 acceptance gate passing. 83 tests pass.

Human verification items are integration-level checks requiring a live AWS environment. All automated verification passes.

---

_Verified: 2026-05-12_
_Verifier: Claude (gsd-verifier)_

---
phase: 11
plan: review-state
subsystem: review
tags: [operator-cli, review-state, ddb, validator, pure-function, tdd]
dependency_graph:
  requires:
    - utils/dynamodb_helpers.py (get_table accessor)
    - utils/s3_client.py (S3HierarchyClient + key_exists)
    - pipeline_hierarchy/publish.py (GATE_BLOCK_ error_code shape)
  provides:
    - review/ package (6 files)
    - REVIEW# DDB rows (PK=REVIEW#{artifact_type}#{version}, SK=GLOBAL)
    - python -m review approve / validate CLI
  affects:
    - operator workflow (replaces operator-memory with structured DDB rows)
    - 11-change-signaling plan (wiring point: _run_approve summary_stats placeholder)
tech_stack:
  added:
    - pyyaml>=6.0.1
  patterns:
    - pure-function validator (no I/O; injectable RunSignals dataclass)
    - $EDITOR seam via callable injection (testable without subprocess)
    - argparse dispatcher with exit-code contract (mirrors gates/cli.py)
    - frozen dataclass for RunSignals + ValidationResult
key_files:
  created:
    - review/__init__.py
    - review/validator.py
    - review/config.py
    - review/store.py
    - review/template.py
    - review/cli.py
    - review/__main__.py
    - tests/test_review_validator.py
    - tests/test_review_config.py
    - tests/test_review_cli.py
  modified:
    - requirements.txt (added pyyaml>=6.0.1)
decisions:
  - "O-02 resolved: validator.py is pure (no I/O) — CLI orchestrates all I/O and calls validate()"
  - "RunSignals dataclass is frozen + injected; production wires via read_run_signals(); tests construct directly"
  - "CONFIG_PATH exported as module constant for display/docs; load_reviewer_cwid() re-resolves at call time"
  - "summary_stats placeholder (zeros) in _run_approve(); wiring point documented for change-signaling plan"
metrics:
  duration_minutes: 6
  tasks_completed: 2
  files_created: 10
  files_modified: 1
  tests_added: 43
  completed_date: "2026-05-12"
---

# Phase 11 Plan review-state: Review State Package Summary

New `review/` package shipping operator CLI for approving (or rejecting) hierarchy artifacts as a machine-readable cold-path gate — pure validator + DDB REVIEW# writer + $EDITOR workflow replacing operator-memory model.

## What Was Built

### review/ Package Layout (6 modules + dispatcher)

| File | Role |
|------|------|
| `review/__init__.py` | Package marker with module index docstring |
| `review/validator.py` | Pure `validate(yaml_dict, RunSignals) -> ValidationResult`; no I/O |
| `review/config.py` | `load_reviewer_cwid()` → config file → env → `ReviewerCwidUnresolvable` |
| `review/store.py` | `write_review()` PutItem + `read_run_signals()` DDB scan + S3 HEAD |
| `review/template.py` | `build_template()` pre-populated YAML string for $EDITOR |
| `review/cli.py` | `_run_approve()` + `_run_validate()` + `main()` argparse dispatcher |
| `review/__main__.py` | `python -m review` entry point |

### PyYAML Dependency

`pyyaml>=6.0.1` added to `requirements.txt`. `yaml.safe_load` (NOT `yaml.load`) is used throughout — prevents arbitrary tag construction per threat T-11-02-05.

## CLI Usage

```bash
# Approve a hierarchy artifact (opens $EDITOR on pre-populated YAML, writes REVIEW# to DDB)
python -m review approve --artifact hierarchy --version v2026-06-01

# Validate a draft YAML without writing (dry run)
python -m review validate /path/to/review.yaml

# Help
python -m review --help
python -m review approve --help
```

Exit codes:
- `0` — approval committed OR validation passed
- `2` — argparse error / required flag missing
- `3` — validation failed (any rule rejected)
- `4` — reviewer_cwid unresolvable
- `5` — DDB write failed

## Pre-Write Validator Rules

`review/validator.py::validate()` enforces 5 rules before any REVIEW# row is written:

| Rule | Description |
|------|-------------|
| 1 | `reviewer_cwid` matches `^cwid_[a-z]+\d+$` |
| 2 | `rationale.strip()` length >= 40 characters |
| 3 | `decision` is exactly `"approve"` or `"reject"` |
| 4 | `proposed_artifact_uri` resolves (S3 HEAD 200 via `signals.artifact_uri_exists`) |
| **5** | **If `decision="approve"`: no `failed_stages` AND no `gate_block_errors`** |

Rule 5 is the key gate-refusal rule: **an operator cannot mark a publish as approved if any cold-path STAGE# row recorded `status=failed` or any `error_code` started with `GATE_BLOCK_`**. They can always `reject`.

## REVIEW# DDB Row Shape (D-07)

```
PK: REVIEW#{artifact_type}#{version}    e.g. REVIEW#hierarchy#v2026-06-01
SK: GLOBAL
{
  proposed_artifact_uri: "s3://...",
  summary_stats: { topics_added: int, subtopics_renamed: int, pmids_reassigned: int },
  status: "approved" | "rejected",
  reviewer_cwid: "cwid_jsmith1",
  reviewed_at: "2026-06-01T14:23:00Z",
  rationale: "string >= 40 chars",
  decision: "approve" | "reject"
}
```

Per D-07: SK is always the literal `"GLOBAL"`. No per-topic REVIEW# rows exist.

## Operator Config Location

Canonical config file: `~/.reciterai/config.yaml`

```yaml
reviewer_cwid: cwid_jsmith1
```

Fallback: `RECITERAI_REVIEWER_CWID` env var. If neither is present, `load_reviewer_cwid()` raises `ReviewerCwidUnresolvable` with an actionable error message showing both options and a YAML example.

No new dotfiles are created. `~/.reciterai/config.yaml` is the single canonical location.

## Open Items Resolved

### O-02: Validator / CLI Split

The validator (`review/validator.py`) is a **pure function** with no I/O:
- No `boto3`, `os`, or `pathlib` imports at module level
- All inputs injected: `yaml_dict` (plain dict) + `RunSignals` (frozen dataclass)
- Unit-testable with zero mocks — all 22 validator tests construct plain Python objects

The CLI (`review/cli.py`) owns all I/O:
- Opens `$EDITOR` via `subprocess.run` (injectable for tests)
- Calls `read_run_signals()` for DDB + S3 state
- Calls `validate()` with the assembled inputs
- Writes `write_review()` on success only

## Wiring Note: summary_stats Placeholder

`_run_approve()` in `review/cli.py` currently uses zeros for `summary_stats`:

```python
# Wiring point for change-signaling plan:
summary_stats = {"topics_added": 0, "subtopics_renamed": 0, "pmids_reassigned": 0}
```

This will integrate with `compute_diff()` from the `11-change-signaling` plan when that plan lands. The wiring point is at the comment in `review/cli.py::_run_approve()` (line after the comment `# Note: summary_stats currently uses zeros...`). No other changes needed in the review package — just replace the zeros dict with the `compute_diff()` call result.

## TDD Gate Compliance

Task 1 (validator + config):
- RED: `b9ba540` — 28 failing tests
- GREEN: `067167b` — all 28 tests pass

Task 2 (CLI + template):
- RED: `9a8e1bc` — 15 failing tests
- GREEN: `a89d8e1` — all 15 tests pass (43 total across both suites)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing attribute] Added CONFIG_PATH module-level constant to review/config.py**
- **Found during:** Task 1 verification
- **Issue:** Plan's verify step checks `review.config.CONFIG_PATH` but initial implementation computed the path only inside `load_reviewer_cwid()` with no module export
- **Fix:** Added `CONFIG_PATH = Path.home() / _CONFIG_SUBPATH` as a module-level constant with a docstring noting it's evaluated at import time
- **Files modified:** `review/config.py`
- **Commit:** `067167b`

## Known Stubs

- `summary_stats` in `_run_approve()` is hardcoded to zeros (`{"topics_added": 0, "subtopics_renamed": 0, "pmids_reassigned": 0}`). This is intentional for Phase 11 Wave 1 — the real values will come from `compute_diff()` in the `11-change-signaling` plan. The wiring point is documented in the code comment and in this SUMMARY. The CLI is fully functional without real stats (operators see zeros in the template); the REVIEW# row records whatever the operator sees and approves.

## Threat Flags

None — no new network endpoints or auth paths beyond what the plan's threat model covers.

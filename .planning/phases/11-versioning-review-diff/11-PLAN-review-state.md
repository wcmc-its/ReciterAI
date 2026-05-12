---
phase: 11
phase_name: Versioning, Review State, Diff Signaling
plan: review-state
type: execute
wave: 1
depends_on: []
files_modified:
  - review/__init__.py
  - review/__main__.py
  - review/cli.py
  - review/validator.py
  - review/template.py
  - review/store.py
  - review/config.py
  - requirements.txt
  - tests/test_review_validator.py
  - tests/test_review_cli.py
  - tests/test_review_config.py
autonomous: true
requirements: []
requirements_addressed:
  - D-07
  - D-08
must_haves:
  truths:
    - "D-07: REVIEW# scope this phase is GLOBAL only (PK shape `REVIEW#{artifact_type}#{version}`, SK `GLOBAL`); per-topic granularity is NOT implemented"
    - "D-08: `python -m review approve --artifact hierarchy --version v2026-06-01` opens $EDITOR on a pre-populated YAML, validates on save, writes REVIEW# to DDB"
    - "D-08: `python -m review validate <path>` validates a draft YAML without writing"
    - "D-08: reviewer_cwid resolves from ~/.reciterai/config.yaml OR RECITERAI_REVIEWER_CWID env var; actionable error if neither"
    - "D-08: pre-write validator refuses approval when any quality gate failed (STAGE# status=failed OR error_code starting with GATE_BLOCK_)"
    - "O-02 resolved: validator lives in review/validator.py as a pure function over (yaml_dict, RunSignals); review/cli.py orchestrates I/O and calls validator"
    - "Operator config canonical location is ~/.reciterai/config.yaml; no new dotfile proliferation"
    - "PyYAML is added to requirements.txt as a new dependency"
  artifacts:
    - path: "review/__init__.py"
      provides: "Package marker"
    - path: "review/__main__.py"
      provides: "argparse dispatcher; supports `python -m review approve` and `python -m review validate`"
      contains: "add_subparsers"
    - path: "review/validator.py"
      provides: "Pure validate(yaml_dict, signals) → ValidationResult function; RunSignals dataclass; no I/O"
      contains: "@dataclass(frozen=True)"
    - path: "review/cli.py"
      provides: "approve + validate command handlers; $EDITOR seam; DDB write via review.store"
      contains: "subprocess.run"
    - path: "review/template.py"
      provides: "Pre-populated YAML template generator (summary_stats fetch + format)"
      contains: "yaml.safe_dump"
    - path: "review/store.py"
      provides: "write_review (PutItem REVIEW#); read_run_signals (DDB queries + S3 HEAD for RunSignals)"
      contains: "REVIEW#"
    - path: "review/config.py"
      provides: "load_reviewer_cwid() — reads ~/.reciterai/config.yaml, falls back to env, actionable error"
      contains: "RECITERAI_REVIEWER_CWID"
    - path: "requirements.txt"
      provides: "PyYAML dependency added"
      contains: "pyyaml"
  key_links:
    - from: "review/cli.py::_run_approve"
      to: "review/template.py + review/validator.py + review/store.py"
      via: "function calls"
      pattern: "from review"
    - from: "review/validator.py::validate"
      to: "review/store.py::read_run_signals output (RunSignals dataclass)"
      via: "dataclass injection"
      pattern: "RunSignals"
    - from: "review/cli.py"
      to: "$EDITOR"
      via: "subprocess.run"
      pattern: "subprocess.run.*EDITOR"
---

<objective>
Phase 11 Surface 2: ship the `review/` package — a new operator CLI for approving (or
rejecting) hierarchy artifacts as a machine-readable cold-path gate. Replaces
operator-memory model with structured DynamoDB rows + a pre-write validator that
refuses to approve when a quality gate failed.

Purpose: spec §4 Decision 3 — REVIEW# state is machine-readable pipeline state. A
human approver cannot accidentally mark a publish as approved if any cold-path stage
ended in `status=failed` or any gate `block` fired.

Output: new `review/` Python package with `__main__.py` dispatch + `approve` and
`validate` subcommands; new `~/.reciterai/config.yaml` operator config loader; PyYAML
dependency add; comprehensive validator + CLI tests.

**Open items resolved in this plan:**
- O-02 → split validator into pure `review/validator.py` (no I/O) and
  `review/cli.py` (subprocess + YAML I/O + DDB write). Pure-function validator
  is unit-testable without mocks; CLI is integration-tested with stubs.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/phases/11-versioning-review-diff/11-CONTEXT.md
@.planning/phases/11-versioning-review-diff/11-RESEARCH.md
@.planning/phases/11-versioning-review-diff/11-PATTERNS.md
@docs/RECITERAI-SPEC.md

<interfaces>
<!-- Key existing signatures the executor needs. Extracted from codebase. -->

From gates/cli.py (closest CLI analog; argparse + exit codes + JSON stdout):
- Module docstring shape with "Exit codes:" section at lines 1-37
- Argparse main with `--stage`, exit-code constants, JSON output via `print(json.dumps(...))`

From utils/stage_records.py:184-212 (pure-builder pattern; pattern for validator dataclass):
```python
# Pure function returns a dict. No I/O. Tests inject all dependencies.
@dataclass(frozen=True)
class StageRecord:
    ...
```

From utils/stage_records.py:290-299 (PutItem writer pattern):
```python
def write_complete(table: Any, **kwargs: Any) -> dict[str, Any]:
    item = build_complete_record(**kwargs)
    table.put_item(Item=item)
    return item
```

From utils/dynamodb_helpers.py:1-100 (lazy table accessor):
```python
def get_table(table_name: str = TABLE_NAME) -> Any:
    # Returns a boto3 Table resource; never instantiated at import time.
```

From utils/s3_client.py (S3 HEAD pattern — used by validator for proposed_artifact_uri check):
```python
# S3HierarchyClient with lazy boto3 client. For HEAD: use head_object via _get_client().
```

From assign_subtopics.py:181-208 (where LOW_CONFIDENCE_ASSIGNMENT# rows are written;
the validator queries these rows as failure signals — read for shape).

From pipeline_hierarchy/publish.py:259 (where STAGE# publish rows are written with
`status=failed` and `error_code=GATE_BLOCK_*`; validator queries these).

Phase 11 REVIEW# row shape (D-07 + spec §4 narrowing):
```
PK: REVIEW#{artifact_type}#{version}    e.g. REVIEW#hierarchy#v2026-06-01
SK: GLOBAL
{
  proposed_artifact_uri: "s3://.../v2026-06-01/hierarchy.json",
  summary_stats: { topics_added: int, subtopics_renamed: int, pmids_reassigned: int },
  status: "approved" | "rejected",
  reviewer_cwid: "cwid_jsmith",
  reviewed_at: "2026-06-01T...Z",
  rationale: "string ≥ 40 chars",
  decision: "approve" | "reject"
}
```

Pre-populated YAML template shape (what the operator sees in $EDITOR):
```yaml
# REVIEW for hierarchy v2026-06-01
# Fill in `reviewer_cwid`, `rationale`, and `decision`.
# decision must be exactly: approve | reject
artifact_type: hierarchy
version: v2026-06-01
proposed_artifact_uri: s3://wcmc-reciterai-hierarchy/v2026-06-01/hierarchy.json
summary_stats:
  topics_added: 2
  subtopics_renamed: 14
  pmids_reassigned: 312
reviewer_cwid:  # required — falls back to ~/.reciterai/config.yaml on save
rationale:      # required — must be ≥ 40 characters
decision:       # required — "approve" or "reject"
```

Validator rules (spec §4 + D-08):
1. `reviewer_cwid` matches `^cwid_[a-z]+\d+$`
2. `rationale.strip()` length ≥ 40
3. `decision` in {"approve", "reject"}
4. `proposed_artifact_uri` resolves (caller pre-resolves via S3 HEAD into `signals.artifact_uri_exists`)
5. If `decision == "approve"`: no STAGE# rows in current cold-run have `status=failed`
   AND no `error_code` starting with `GATE_BLOCK_` on any STAGE# publish row.

Exit code convention (mirrors gates/cli.py shape):
- 0 — approval committed (approve) OR validation passed (validate)
- 2 — argparse error / required flag missing
- 3 — validation failed (any rule rejected)
- 4 — reviewer_cwid unresolvable (no config, no env)
- 5 — DDB write failed
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Pure validator + RunSignals dataclass + operator config loader</name>
  <files>
    review/__init__.py
    review/validator.py
    review/config.py
    review/store.py
    requirements.txt
    tests/test_review_validator.py
    tests/test_review_config.py
  </files>
  <read_first>
    utils/stage_records.py (lines 1-300; pure-builder pattern + write_complete writer)
    utils/dynamodb_helpers.py (lines 1-100; get_table accessor)
    utils/s3_client.py (full file; head_object pattern for proposed_artifact_uri check)
    spotlight/rotation_selector.py (lines 50-200; lazy client pattern + dataclass frozen pattern at lines 59-69)
    pipeline_hierarchy/publish.py:259 (STAGE# failure write site — shape the validator reads)
    assign_subtopics.py:181-208 (LOW_CONFIDENCE_ASSIGNMENT# write site — shape the validator reads)
    requirements.txt (to confirm PyYAML is NOT already present before adding)
  </read_first>
  <behavior>
    - Test 1 (tests/test_review_validator.py): valid YAML dict with all fields + RunSignals(failed_stages=[], artifact_uri_exists=True, low_confidence_count=0, uncovered_pmid_count=0) → `ValidationResult(ok=True, errors=[])`.
    - Test 2: `reviewer_cwid` not matching regex (`"jsmith"`, `"cwid_ABC123"`, empty) → ok=False with regex error.
    - Test 3: `rationale = "too short"` (< 40 chars after strip) → ok=False; `rationale = " " * 50` (all whitespace, strips to empty) → ok=False.
    - Test 4: `decision = "yes"` (not approve/reject) → ok=False.
    - Test 5: `signals.artifact_uri_exists=False` → ok=False with HEAD-404 error.
    - Test 6 (gate-failure refusal): `decision="approve"` + `signals.failed_stages=["publish_hierarchy"]` → ok=False with "cannot approve" error AND mentions the failed stage. Same YAML with `decision="reject"` → ok=True (you can always reject).
    - Test 7 (tests/test_review_config.py): `~/.reciterai/config.yaml` exists with `reviewer_cwid: cwid_jsmith1` → `load_reviewer_cwid()` returns `"cwid_jsmith1"`. Use `monkeypatch.setattr("pathlib.Path.home", ...)`.
    - Test 8 (tests/test_review_config.py): no config file but `RECITERAI_REVIEWER_CWID=cwid_jsmith1` env set → returns `"cwid_jsmith1"`.
    - Test 9 (tests/test_review_config.py): neither config nor env → raises `ReviewerCwidUnresolvable` with an actionable message (must include the literal path `~/.reciterai/config.yaml` AND the literal env var name `RECITERAI_REVIEWER_CWID` AND a literal YAML example).
  </behavior>
  <action>
    A. Add `pyyaml>=6.0.1` to `requirements.txt` (Edit the file; append the line; preserve existing ordering and comments).

    B. `review/__init__.py`:
    ```python
    """ReciterAI review-state package (Phase 11).

    Public modules:
      - review.validator — pure validation logic
      - review.config — operator config loader (~/.reciterai/config.yaml)
      - review.store — DDB read/write for REVIEW# rows + RunSignals fetching
      - review.template — YAML template generator
      - review.cli — argparse handlers (approve, validate)
    """
    ```

    C. `review/validator.py` — pure functions, no I/O:
    ```python
    from __future__ import annotations
    import re
    from dataclasses import dataclass, field
    from typing import Sequence

    _CWID_PATTERN = re.compile(r"^cwid_[a-z]+\d+$")
    _MIN_RATIONALE_LEN = 40

    @dataclass(frozen=True)
    class RunSignals:
        """Signals about the current cold-run quality gates. Injected at
        construction time; production wires this via review.store.read_run_signals.
        """
        failed_stages: tuple[str, ...] = ()
        gate_block_errors: tuple[str, ...] = ()  # error_codes starting with GATE_BLOCK_
        artifact_uri_exists: bool = True
        low_confidence_count: int = 0
        uncovered_pmid_count: int = 0

    @dataclass(frozen=True)
    class ValidationResult:
        ok: bool
        errors: tuple[str, ...] = ()

    def validate(yaml_dict: dict, signals: RunSignals) -> ValidationResult:
        errors: list[str] = []

        cwid = (yaml_dict.get("reviewer_cwid") or "")
        if not isinstance(cwid, str) or not _CWID_PATTERN.match(cwid):
            errors.append("reviewer_cwid must match ^cwid_[a-z]+\\d+$")

        rationale = yaml_dict.get("rationale") or ""
        if not isinstance(rationale, str) or len(rationale.strip()) < _MIN_RATIONALE_LEN:
            errors.append(f"rationale must be ≥ {_MIN_RATIONALE_LEN} chars after whitespace strip")

        decision = yaml_dict.get("decision")
        if decision not in ("approve", "reject"):
            errors.append("decision must be 'approve' or 'reject'")

        if not signals.artifact_uri_exists:
            uri = yaml_dict.get("proposed_artifact_uri", "<unset>")
            errors.append(f"proposed_artifact_uri does not resolve (S3 HEAD 404): {uri}")

        if decision == "approve":
            if signals.failed_stages:
                errors.append(
                    f"cannot approve — failed stages: {list(signals.failed_stages)}"
                )
            if signals.gate_block_errors:
                errors.append(
                    f"cannot approve — gate blocks: {list(signals.gate_block_errors)}"
                )

        return ValidationResult(ok=not errors, errors=tuple(errors))
    ```

    D. `review/config.py`:
    ```python
    from __future__ import annotations
    import os
    from pathlib import Path
    import yaml

    CONFIG_PATH = Path.home() / ".reciterai" / "config.yaml"
    ENV_VAR = "RECITERAI_REVIEWER_CWID"

    class ReviewerCwidUnresolvable(Exception):
        pass

    def load_reviewer_cwid() -> str:
        """Resolve reviewer_cwid from ~/.reciterai/config.yaml, then env, then error."""
        # 1. Config file
        path = Path.home() / ".reciterai" / "config.yaml"
        if path.exists():
            try:
                data = yaml.safe_load(path.read_text()) or {}
            except yaml.YAMLError as exc:
                raise ReviewerCwidUnresolvable(
                    f"{path}: parse error: {exc}"
                ) from exc
            cwid = data.get("reviewer_cwid")
            if isinstance(cwid, str) and cwid.strip():
                return cwid.strip()

        # 2. Env override
        env_value = os.environ.get(ENV_VAR, "").strip()
        if env_value:
            return env_value

        # 3. Actionable error
        raise ReviewerCwidUnresolvable(
            "Could not resolve reviewer_cwid.\n\n"
            f"Option 1 — create {path}:\n"
            "    reviewer_cwid: cwid_jsmith1\n\n"
            f"Option 2 — export {ENV_VAR}:\n"
            f"    export {ENV_VAR}=cwid_jsmith1\n"
        )
    ```

    E. `review/store.py` — DDB read/write wrappers:
    ```python
    from __future__ import annotations
    from typing import Any
    from review.validator import RunSignals

    REVIEW_PK_TEMPLATE = "REVIEW#{artifact_type}#{version}"
    REVIEW_SK = "GLOBAL"

    def write_review(table, *, artifact_type: str, version: str,
                     review_dict: dict) -> dict:
        item = {
            "PK": REVIEW_PK_TEMPLATE.format(artifact_type=artifact_type, version=version),
            "SK": REVIEW_SK,
            **review_dict,
        }
        table.put_item(Item=item)
        return item

    def read_run_signals(table, s3_client, *, run_id: str,
                         proposed_artifact_uri: str) -> RunSignals:
        """Query STAGE# rows + S3 HEAD to assemble RunSignals for the validator.

        Queries:
          - STAGE# rows filtered by run_id where status == "failed" → failed_stages
          - STAGE# publish rows where error_code starts with "GATE_BLOCK_" → gate_block_errors
          - LOW_CONFIDENCE_ASSIGNMENT# rows by run_id → low_confidence_count
          - UNCOVERED_PMID# rows by run_id → uncovered_pmid_count
          - S3 head_object(proposed_artifact_uri) → artifact_uri_exists
        """
        # Implementation: use table.scan or table.query with FilterExpression on
        # run_id (Phase 9 convention: filter in Python until GSI added). Parse
        # bucket and key from proposed_artifact_uri (`s3://bucket/key`), call
        # s3_client.head_object(Bucket=..., Key=...); catch ClientError on 404.
        ...
    ```
    (Full implementation expected; the above is the surface — the executor writes
    the queries. Failure-mode test for read_run_signals lives in the CLI test file
    Task 2.)

    F. Tests:
    - `tests/test_review_validator.py`: cover Tests 1-6. All tests are pure
      Python — construct a YAML dict, construct a RunSignals, call validate(),
      assert on ValidationResult. No mocks needed.
    - `tests/test_review_config.py`: cover Tests 7-9. Use
      `monkeypatch.setattr(Path, "home", lambda: tmp_path)` and
      `monkeypatch.setenv` to control inputs.
  </action>
  <verify>
    <automated>cd /Users/paulalbert/Dropbox/GitHub/ReciterAI &amp;&amp; pytest tests/test_review_validator.py tests/test_review_config.py -x</automated>
    Manual checks:
    - `grep -c "^pyyaml" requirements.txt` &gt;= 1.
    - `python -c "import review.validator; r = review.validator.validate({}, review.validator.RunSignals()); print(r.ok, len(r.errors))"` prints `False <num &gt; 0>`.
    - `python -c "import review.config; print(review.config.CONFIG_PATH)"` prints the expanded `~/.reciterai/config.yaml` path.
    - `grep -n 'cwid_\[a-z\]+\\d+' review/validator.py` returns the regex literal.
    - `grep -n 'RECITERAI_REVIEWER_CWID' review/config.py` returns the env var name.
    - `grep -n 'GATE_BLOCK_' review/validator.py` returns the gate-failure check.
  </verify>
  <done>
    All 9 tests pass; validator is pure (no imports of boto3, os.environ, or pathlib outside of config.py); requirements.txt carries the new pyyaml dep.
  </done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: CLI dispatcher + template generator + $EDITOR seam + integration tests</name>
  <files>
    review/__main__.py
    review/cli.py
    review/template.py
    tests/test_review_cli.py
  </files>
  <read_first>
    gates/cli.py (the closest argparse-with-exit-codes analog; full file)
    pipeline_cold/run.py (lines 229-262; argparse main shape with multi-flag combos)
    review/validator.py (Task 1 product — read for RunSignals + ValidationResult shape)
    review/config.py (Task 1 product — read for load_reviewer_cwid signature)
    review/store.py (Task 1 product — read for write_review and read_run_signals signatures)
  </read_first>
  <behavior>
    - Test 1 (tests/test_review_cli.py — `validate` subcommand happy path): `python -m review validate <tmp.yaml>` on a valid file → exit 0, "validation passed" on stdout. No DDB write occurs (assert by stubbed table never called).
    - Test 2 (`validate` failure): YAML with `decision: yes` → exit 3, error mentions "decision must be 'approve' or 'reject'".
    - Test 3 (`approve` happy path): mocked $EDITOR writes a valid YAML → exit 0, exactly one PutItem to REVIEW#hierarchy#v2026-06-01 with SK=GLOBAL. Asserted via MagicMock.
    - Test 4 (`approve` gate failure): mocked RunSignals returns `failed_stages=("publish_hierarchy",)` → CLI prints rejection error, exits 3, NO PutItem fires. The YAML file is NOT deleted from disk (operator can re-edit).
    - Test 5 (`approve` no reviewer_cwid resolvable): `load_reviewer_cwid()` raises `ReviewerCwidUnresolvable` → CLI exits 4 with the actionable error message.
    - Test 6 (template prepopulation): given a tuple of summary stats (topics_added=2, subtopics_renamed=14, pmids_reassigned=312), `review.template.build_template(...)` produces a YAML string parseable by `yaml.safe_load` AND containing `reviewer_cwid:` (empty for operator to fill if no resolvable cwid; pre-filled otherwise).
    - Test 7 (`$EDITOR` seam): the editor subprocess command is overridable via dependency injection (test passes a fake editor that writes a YAML and returns 0; CLI proceeds to validation).
    - Test 8 (`reviewed_at` stamping): on successful approve, the written REVIEW# row carries `reviewed_at` matching `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$`.
  </behavior>
  <action>
    A. `review/template.py`:
    ```python
    from __future__ import annotations
    import yaml
    from typing import Optional

    _HEADER = """\
    # REVIEW for {artifact_type} {version}
    # Fill in `reviewer_cwid` (if not already set), `rationale`, and `decision`.
    # decision must be exactly: approve | reject
    # rationale must be ≥ 40 characters.
    """

    def build_template(*, artifact_type: str, version: str,
                       proposed_artifact_uri: str,
                       summary_stats: dict,
                       reviewer_cwid: Optional[str] = None) -> str:
        body = {
            "artifact_type": artifact_type,
            "version": version,
            "proposed_artifact_uri": proposed_artifact_uri,
            "summary_stats": dict(summary_stats),
            "reviewer_cwid": reviewer_cwid or "",
            "rationale": "",
            "decision": "",
        }
        return _HEADER.format(artifact_type=artifact_type, version=version) \
               + yaml.safe_dump(body, sort_keys=False, default_flow_style=False)
    ```

    B. `review/__main__.py`:
    ```python
    """`python -m review` entry point."""
    import sys
    from review.cli import main

    if __name__ == "__main__":
        sys.exit(main())
    ```

    C. `review/cli.py`:
    ```python
    """Ad-hoc review CLI — `python -m review approve|validate`.

    Use cases:
      - python -m review approve --artifact hierarchy --version v2026-06-01
        Opens $EDITOR on a pre-populated YAML, validates on save, writes REVIEW#.
      - python -m review validate <path-to-yaml>
        Validates a draft YAML without writing.

    Exit codes:
      0 — approval committed (approve) OR validation passed (validate)
      2 — argparse error / required flag missing
      3 — validation failed (any rule rejected the YAML)
      4 — reviewer_cwid unresolvable (no config, no env)
      5 — DDB write failed
    """
    from __future__ import annotations

    import argparse
    import os
    import subprocess
    import sys
    import tempfile
    from datetime import datetime, timezone
    from pathlib import Path
    from typing import Callable, Optional

    import yaml

    from review.config import load_reviewer_cwid, ReviewerCwidUnresolvable
    from review.template import build_template
    from review.validator import validate, RunSignals
    from review.store import write_review, read_run_signals

    # Test seam: invoke_editor takes (path) -> int return code.
    def _default_invoke_editor(path: Path) -> int:
        editor = os.environ.get("EDITOR", "vim")
        return subprocess.run([editor, str(path)]).returncode

    # Test seams: get_table / get_s3_client factories (production: lazy boto3;
    # tests: inject MagicMock).
    def _default_get_table():
        from utils.dynamodb_helpers import get_table
        return get_table()

    def _default_get_s3_client():
        from utils.s3_client import S3HierarchyClient
        return S3HierarchyClient()

    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    def _run_approve(args, *,
                     invoke_editor: Callable[[Path], int] = _default_invoke_editor,
                     get_table=_default_get_table,
                     get_s3_client=_default_get_s3_client) -> int:
        # 1. Resolve reviewer_cwid
        try:
            cwid = load_reviewer_cwid()
        except ReviewerCwidUnresolvable as exc:
            print(str(exc), file=sys.stderr)
            return 4

        # 2. Compute pre-populated template
        # (For Phase 11: summary_stats is fetched via the same shared compute
        # used by diff.json producer in Surface 3. For this plan, we accept
        # zeros as fallback; the integration with diff_stats lands in the
        # change-signaling plan and the wiring point is documented in SUMMARY.)
        summary_stats = {"topics_added": 0, "subtopics_renamed": 0, "pmids_reassigned": 0}
        proposed_artifact_uri = (
            f"s3://wcmc-reciterai-hierarchy/{args.version}/hierarchy.json"
        )
        template_str = build_template(
            artifact_type=args.artifact, version=args.version,
            proposed_artifact_uri=proposed_artifact_uri,
            summary_stats=summary_stats, reviewer_cwid=cwid,
        )

        # 3. Write template to tempfile, open in $EDITOR
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as fp:
            fp.write(template_str)
            tmppath = Path(fp.name)
        rc = invoke_editor(tmppath)
        if rc != 0:
            print(f"$EDITOR exited with code {rc}; leaving draft at {tmppath}",
                  file=sys.stderr)
            return 3

        # 4. Re-load YAML
        try:
            yaml_dict = yaml.safe_load(tmppath.read_text()) or {}
        except yaml.YAMLError as exc:
            print(f"YAML parse error: {exc}; draft at {tmppath}", file=sys.stderr)
            return 3

        # 5. Fetch RunSignals (table + s3 head)
        table = get_table()
        s3 = get_s3_client()
        run_id = os.environ.get("RECITERAI_COLD_RUN_ID", "")
        signals = read_run_signals(
            table, s3, run_id=run_id,
            proposed_artifact_uri=yaml_dict.get("proposed_artifact_uri", ""),
        )

        # 6. Validate
        result = validate(yaml_dict, signals)
        if not result.ok:
            print("validation FAILED:", file=sys.stderr)
            for e in result.errors:
                print(f"  - {e}", file=sys.stderr)
            print(f"draft preserved at {tmppath}", file=sys.stderr)
            return 3

        # 7. PutItem
        review_dict = {
            "proposed_artifact_uri": yaml_dict["proposed_artifact_uri"],
            "summary_stats": yaml_dict.get("summary_stats", {}),
            "status": "approved" if yaml_dict["decision"] == "approve" else "rejected",
            "reviewer_cwid": yaml_dict["reviewer_cwid"],
            "reviewed_at": _now_iso(),
            "rationale": yaml_dict["rationale"],
            "decision": yaml_dict["decision"],
        }
        try:
            written = write_review(
                table, artifact_type=args.artifact, version=args.version,
                review_dict=review_dict,
            )
        except Exception as exc:
            print(f"DDB write failed: {exc}", file=sys.stderr)
            return 5

        print(f"REVIEW written: {written['PK']}")
        # Cleanup temp file on success
        tmppath.unlink(missing_ok=True)
        return 0

    def _run_validate(args, *, get_table=_default_get_table,
                      get_s3_client=_default_get_s3_client) -> int:
        path = Path(args.path)
        if not path.exists():
            print(f"file not found: {path}", file=sys.stderr)
            return 3
        try:
            yaml_dict = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError as exc:
            print(f"YAML parse error: {exc}", file=sys.stderr)
            return 3

        table = get_table()
        s3 = get_s3_client()
        run_id = os.environ.get("RECITERAI_COLD_RUN_ID", "")
        signals = read_run_signals(
            table, s3, run_id=run_id,
            proposed_artifact_uri=yaml_dict.get("proposed_artifact_uri", ""),
        )
        result = validate(yaml_dict, signals)
        if not result.ok:
            print("validation FAILED:", file=sys.stderr)
            for e in result.errors:
                print(f"  - {e}", file=sys.stderr)
            return 3
        print("validation passed")
        return 0

    def main(argv: Optional[list[str]] = None) -> int:
        parser = argparse.ArgumentParser(prog="review")
        sub = parser.add_subparsers(dest="command", required=True)
        p_approve = sub.add_parser("approve")
        p_approve.add_argument("--artifact", required=True, choices=["hierarchy"])
        p_approve.add_argument("--version", required=True)
        p_validate = sub.add_parser("validate")
        p_validate.add_argument("path")
        args = parser.parse_args(argv)
        if args.command == "approve":
            return _run_approve(args)
        return _run_validate(args)
    ```

    D. `tests/test_review_cli.py`:
    - Use `_run_approve` and `_run_validate` directly (bypass `main`'s argparse
      where convenient; tests for `main` argparse routing can be a single
      smoke test).
    - For `approve` tests: build a stub editor function that writes a known
      YAML and returns 0:
      ```python
      def fake_editor(yaml_content):
          def _invoke(path):
              path.write_text(yaml_content)
              return 0
          return _invoke
      ```
    - Stub `get_table` to return a `MagicMock()`; stub `get_s3_client`
      similarly. Inject via the keyword args on `_run_approve`.
    - For RunSignals injection: monkeypatch
      `review.cli.read_run_signals` to return a fixed RunSignals matching the
      test scenario (preferred over also stubbing the underlying DDB/S3
      calls for these CLI tests; the `read_run_signals` function itself is
      tested separately if you add a `tests/test_review_store.py` — but
      that's optional; the validator already tests the consumption side).
    - Assert exit codes per the behavior contract.
    - Use `argparse.Namespace` constructors to fake args without going
      through `main`.
  </action>
  <verify>
    <automated>cd /Users/paulalbert/Dropbox/GitHub/ReciterAI &amp;&amp; pytest tests/test_review_cli.py -x</automated>
    Smoke check:
    - `python -m review --help` exits 0 and prints subcommand help.
    - `python -m review approve --help` shows `--artifact` and `--version` required flags.
    - `python -m review validate --help` shows `path` positional.
    Greps:
    - `grep -n "subprocess.run" review/cli.py` returns the editor invocation site.
    - `grep -n "REVIEW#" review/store.py` returns the PK template.
    - `grep -c 'yaml.safe_load\|yaml.safe_dump' review/cli.py review/template.py review/config.py | grep -v '^#'` &gt;= 3 (one per file that touches YAML).
  </verify>
  <done>
    All 8 tests pass; `python -m review --help` works; the validator+CLI separation is preserved (cli.py imports validator.validate; validator.py does not import boto3 or os.environ outside of its own typing surface).
  </done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| operator → $EDITOR → YAML on disk | Operator types into a text editor; YAML content is parsed and acted on |
| YAML on disk → DDB PutItem | Validated YAML becomes a REVIEW# row |
| operator config file → CLI | `~/.reciterai/config.yaml` controls reviewer_cwid identity |
| RunSignals (DDB/S3 reads) → validator | Validator refuses approval based on read-only signals |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-11-02-01 | Spoofing | reviewer_cwid identity | mitigate | Operator config is local-disk + env; no auth claim. cwid regex (`^cwid_[a-z]+\d+$`) prevents trivially-bad strings. Pre-write validator enforces the regex. Project-level threat model assumes operator host is trusted (CLI is operator-only). |
| T-11-02-02 | Tampering | YAML file on disk between $EDITOR save and DDB write | accept | Window is sub-second; operator-controlled tmpfile (`tempfile.NamedTemporaryFile`); validator re-reads and re-checks before write. No external write surface. |
| T-11-02-03 | Tampering | review/validator.py rule bypass | mitigate | Validator is a pure function called from both `approve` (pre-write) and `validate` (dry-check). No code path constructs a REVIEW# row without going through `validate()`. Unit tests cover bypass attempts (each rule has a negative test). |
| T-11-02-04 | Repudiation | who approved what when | mitigate | REVIEW# row carries `reviewer_cwid`, `reviewed_at`, `rationale`, `decision`. DDB writes are durable; no delete path in this plan. |
| T-11-02-05 | Information Disclosure | yaml.safe_load on operator-edited file | mitigate | `yaml.safe_load` (NOT `yaml.load`) refuses arbitrary tag construction. Per-CLAUDE.md no credentials in code; PyYAML safe parser blocks tag-based code execution. |
| T-11-02-06 | DoS | n/a — operator CLI, no scaled surface | accept | Single-user invocation; no rate-limit needed. |
| T-11-02-07 | Elevation of Privilege | CLI gates approval on gate state | mitigate | Validator refuses `decision="approve"` when any STAGE# row is failed or any GATE_BLOCK_* error code is present. Cannot be bypassed without DDB write to forge a STAGE# success — out of scope of this CLI. |
</threat_model>

<verification>
- Validator is pure (no I/O); CLI orchestrates I/O and calls validator.
- All gate-failure paths refuse approval (Test 6 above).
- `~/.reciterai/config.yaml` is the canonical operator config; no new dotfile.
- PyYAML dep is added; no other YAML library is introduced.
- Per D-07: PK shape is `REVIEW#{artifact_type}#{version}` and SK is the literal `"GLOBAL"`; no per-topic shape exists.
- $EDITOR seam is dependency-injectable so tests use a fake editor.
</verification>

<success_criteria>
- D-07: REVIEW# rows have SK=GLOBAL only; no per-topic code path.
- D-08: `python -m review approve` and `python -m review validate` are both invocable; reviewer_cwid resolves from config → env → actionable error.
- O-02: validator.py is pure (unit-testable with no I/O); cli.py owns I/O.
- Validator refuses approval when failed_stages or gate_block_errors are present.
- PyYAML is in requirements.txt; `yaml.safe_load` (not `yaml.load`) is used everywhere.
- All tests pass: `pytest tests/test_review_validator.py tests/test_review_config.py tests/test_review_cli.py -x`
</success_criteria>

<output>
Create `.planning/phases/11-versioning-review-diff/11-SUMMARY-review-state.md` documenting:
- New `review/` package layout (6 files)
- PyYAML dependency add
- CLI usage examples (approve, validate, help)
- Pre-write validator rules (with the gate-refusal rule highlighted)
- Operator config file location + env fallback
- Open items resolved: O-02 (validator/CLI split)
- Wiring note: the `summary_stats` placeholder in `_run_approve` (currently zeros) will integrate with `compute_diff()` from the change-signaling plan in a follow-up commit if both plans land in the same wave; document the wiring point.
</output>

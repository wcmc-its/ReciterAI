# Phase 12: Feedback Loops, Both Aggregations, Residual Hygiene — Pattern Map

**Mapped:** 2026-05-12
**Files analyzed:** 24 (13 NEW + 11 MODIFIED)
**Analogs found:** 23 / 24 (one new file — `config/thresholds.md` — has no analog; pattern established by this phase)

## File Classification

### NEW files

| New File | Role | Data Flow | Closest Analog | Match Quality |
|----------|------|-----------|----------------|---------------|
| `pipeline_feedback/__init__.py` | module init | n/a | `pipeline_drift/__init__.py` | exact |
| `pipeline_feedback/__main__.py` | CLI entry | request-response | `review/__main__.py` | exact |
| `pipeline_feedback/cli.py` | operator CLI | request-response | `review/cli.py` | exact (role + flow) |
| `pipeline_feedback/sweep.py` | service / orchestrator | batch | `pipeline_drift/evaluator.py` | role-match |
| `pipeline_feedback/finding_records.py` | event producer | event-driven | `utils/event_records.py` | exact |
| `pipeline_feedback/markdown_render.py` | renderer | transform | `pipeline_hierarchy/generator.py` (deterministic output, no `generated_at` in body) | partial (no direct analog) |
| `pipeline_feedback/prompts/uncovered_pmid_sonnet_v0.md` | LLM prompt | n/a | existing prompt files under `score_publications`/`spotlight` | role-match |
| `gates/reconciliation.py` | gate | request-response | `gates/parent_prefix.py` / `gates/schema_validation.py` | exact |
| `config/thresholds.schema.json` | JSON schema | config | `docs/hierarchy.schema.json` | exact |
| `config/thresholds.md` | docs | doc | **NO ANALOG** — new pattern Phase 12 establishes | none |
| `docs/sensitive-topic-exclusion.md` | docs (G-24) | doc | `docs/topic-subtopic-assignment.md` | role-match |
| `tests/test_feedback_sweep.py` | unit test | test | `tests/test_pipeline_drift_evaluator.py` | exact |
| `tests/test_reconciliation_gate.py` | unit test | test | `tests/test_gates_parent_prefix.py` | exact |
| `tests/test_aggregate_inclusive.py` | unit test | test | (none direct — `aggregate_subtopic_scores.py` has no current test) → use `tests/test_event_records.py` shape | partial |
| `tests/test_critic_reject_event.py` | unit test | test | `tests/test_uncovered_pmid_event.py` | exact |
| `tests/test_thresholds_schema.py` | unit test | test | `tests/test_gates_schema_validation.py` | role-match |
| `tests/test_thresholds_keys.py` | unit test | test | `tests/test_event_records.py::test_load_thresholds_default_path` | exact |
| `tests/test_feedback_render.py` | unit test | test | `tests/test_hierarchy_reproducibility.py` (byte-identical assertion) | role-match |
| `tests/test_aggregate_idempotent.py` | unit test | test | `tests/test_event_records.py` (idempotent overwrite) | partial |
| `tests/test_cold_path_e2e.py` (G-37) | integration test | test | `tests/test_hierarchy_reproducibility.py` (extend pattern) | role-match |
| `tests/fixtures/cold_path_corpus/` | test fixture | test | `tests/fixtures/` (existing JSON-schema fixtures) | role-match |

### MODIFIED files

| Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---------------|------|-----------|----------------|---------------|
| `spotlight/critic.py` (line ~571) | producer | event-driven | `assign_subtopics.py` write-site for LOW_CONFIDENCE_ASSIGNMENT# | role-match |
| `aggregate_subtopic_scores.py:108-167` | service | CRUD | self (existing `_aggregate` becomes `_aggregate_exclusive`; add `_aggregate_inclusive`) | exact |
| `rollup_by_cwid.py` | service | file-I/O | self (read-site for renamed CSV) | exact |
| `assign_subtopics.py:94-98,1022` | config refactor | config | (no direct — lift constants into `config/thresholds.json`) | n/a |
| `utils/event_records.py` | event producer | event-driven | self (mirror `build_uncovered_pmid_record` for CRITIC_REJECT#) | exact |
| `utils/stage_records.py` | substrate builder | event-driven | self (Phase 11 `run_id` precedent at lines 217-219) | exact |
| `utils/env_check.py` | preflight | config | self (existing column-check pattern) + `gates/schema_validation.py` (jsonschema usage) | role-match |
| `pipeline_cold/run.py` | orchestrator | event-driven | self (existing `default_cold_stages()` at lines 90-131) | exact |
| `pipeline_drift/evaluator.py` | additive schema extension | event-driven | self (`DriftEvaluation.to_dynamodb_item()` at lines 73-96) | exact |
| `gates/registry.py` (registration only) | gate | request-response | self (existing registry; reconciliation gate auto-registers via decorator import) | exact |
| `config/thresholds.json` | config | config | self (extend existing JSON) | exact |
| `docs/topic-subtopic-assignment.md` | docs | doc | self (add one-line D-17 invariant) | n/a |
| `GETTING_STARTED.md` | docs (G-34) | doc | self (add IAM Policy section) | n/a |
| `docs/hierarchy-contract.md` | docs (optional) | doc | self | n/a |
| `docs/RECITERAI-SPEC.md` | docs (optional cross-link) | doc | self | n/a |

## Pattern Assignments

### `pipeline_feedback/finding_records.py` + `utils/event_records.py` extension (event producer, event-driven)

**Analog:** `utils/event_records.py` lines 61-98 (`build_uncovered_pmid_record` + `write_uncovered_pmid`)

**Imports pattern** (lines 26-32):
```python
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
```

**Core builder + thin writer pattern** (lines 61-98):
```python
def build_uncovered_pmid_record(
    *,
    pmid: str,
    taxonomy_version: str,
    top_topics: list[tuple[str, float]],
    created_at: str | None = None,
) -> dict[str, Any]:
    """Pure builder for an UNCOVERED_PMID# row.

    `top_topics` is the top-3 (topic_id, score) tuples ordered from
    highest to lowest score. Float scores are converted to Decimal to
    satisfy DynamoDB's numeric type rule.
    """
    top_sorted = sorted(top_topics, key=lambda t: (-float(t[1]), t[0]))[:3]
    top_topic_score = (
        Decimal(str(top_sorted[0][1])) if top_sorted else Decimal("0")
    )
    return {
        "PK": f"UNCOVERED_PMID#{pmid}",
        "SK": "GLOBAL",
        "record_type": "UNCOVERED_PMID",
        "pmid": str(pmid),
        "taxonomy_version": taxonomy_version,
        "top_topic_score": top_topic_score,
        "top_topics": [
            {"topic_id": tid, "score": Decimal(str(score))}
            for tid, score in top_sorted
        ],
        "created_at": created_at or _now_iso(),
        "source_stage": "score_publications",
    }


def write_uncovered_pmid(table: Any, **kwargs: Any) -> dict[str, Any]:
    """Build an UNCOVERED_PMID# row and persist via table.put_item."""
    item = build_uncovered_pmid_record(**kwargs)
    table.put_item(Item=item)
    return item
```

**What's idiomatic:**
- Pure builder returns dict; thin writer composes builder + `table.put_item`
- All keyword-only args (`*,`)
- `created_at: str | None = None` with `_now_iso()` fallback for test determinism
- Stable PK + constant `SK="GLOBAL"` → idempotent overwrite on rerun
- Float→Decimal coercion at the boundary via `Decimal(str(float_val))`
- `source_stage` field naming the producer module/script
- `record_type` field as the human-readable label (string form of the PK prefix)

**Phase 12 applications:**
- `build_critic_reject_record` (in `utils/event_records.py`) → PK `CRITIC_REJECT#{cwid}#{pmid_set_hash}`, SK `GLOBAL`, `source_stage="spotlight.critic"`. Plus `pmid_set_hash` computation via `hashlib.sha256(",".join(sorted(pmids)).encode()).hexdigest()[:16]`.
- `build_candidate_topic_record` (in `pipeline_feedback/finding_records.py`) → PK `CANDIDATE_TOPIC#{slug}`, SK `GLOBAL`, `source_stage="feedback.sweep"`.
- `build_recluster_recommendation_record` → PK `RECLUSTER_RECOMMENDATION#{topic_id}`.
- `build_spotlight_diagnostic_record` → PK `SPOTLIGHT_DIAGNOSTIC#{cwid}`; include `underlying_rejects: list[str]` capped at `feedback_diagnostic_max_underlying` per D-32.

---

### `pipeline_feedback/cli.py` + `pipeline_feedback/__main__.py` (operator CLI, request-response)

**Analog:** `review/cli.py` lines 233-279, `review/__main__.py`

**Module docstring + exit-code pattern** (review/cli.py lines 1-15):
```python
"""Ad-hoc review CLI — `python -m review approve|validate`.

Use cases:
  - python -m review approve --artifact hierarchy --version v2026-06-01
  - python -m review validate <path-to-yaml>

Exit codes:
  0 — approval committed (approve) OR validation passed (validate)
  2 — argparse error / required flag missing
  3 — validation failed
  4 — reviewer_cwid unresolvable
  5 — DDB write failed
"""
```

**Argparse dispatch** (lines 233-278):
```python
def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="review",
        description="ReciterAI artifact review CLI — approve or validate hierarchy artifacts.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_approve = sub.add_parser(
        "approve",
        help="Open $EDITOR on a pre-populated YAML, validate, and write REVIEW# to DDB.",
    )
    p_approve.add_argument("--artifact", required=True, choices=["hierarchy"])
    p_approve.add_argument("--version", required=True)

    p_validate = sub.add_parser(
        "validate",
        help="Validate a draft review YAML without writing to DDB.",
    )
    p_validate.add_argument("path")

    args = parser.parse_args(argv)
    if args.command == "approve":
        return _run_approve(args)
    return _run_validate(args)
```

**Injected seams pattern** (lines 39-53):
```python
def _default_invoke_editor(path: Path) -> int:
    editor = os.environ.get("EDITOR", "vim")
    return subprocess.run([editor, str(path)]).returncode


def _default_get_table():
    from utils.dynamodb_helpers import get_table
    return get_table()
```

**`__main__.py` entry** (review/__main__.py full file):
```python
"""`python -m review` entry point.

Dispatches to review.cli.main which handles all subcommand routing.
"""
import sys

from review.cli import main

if __name__ == "__main__":
    sys.exit(main())
```

**What's idiomatic:**
- Module docstring lists every subcommand example AND every exit code
- `add_subparsers(dest="command", required=True)` — never an implicit default
- All handlers take injected seams (`get_table`, `invoke_editor`) with `_default_*` factories — keeps the CLI testable without monkeypatching
- `main(argv: Optional[list[str]] = None)` signature so tests can pass `argv` directly
- `__main__.py` is three lines; all logic lives in `cli.py`
- `RECITERAI_COLD_RUN_ID` env var read at the boundary (line 138)

**Phase 12 applications:**
- `pipeline_feedback/cli.py` with `sweep` and `render` subcommands
- `sweep --since DAYS [--run-id UUID] [--max-pmids N]`
- `render <run_id> [--output PATH]`
- Mint `source_sweep_run_id` at `_run_sweep` start (one UUID per invocation per Pitfall 2)
- Exit codes: 0 sweep wrote rows; 2 argparse; 3 invalid args; 4 missing DDB env; 5 DDB error; 6 Bedrock unavailable

---

### `gates/reconciliation.py` (gate, request-response)

**Analog:** `gates/parent_prefix.py` (lines 38-94) and `gates/schema_validation.py` (lines 29-63)

**Imports + module docstring pattern** (`gates/schema_validation.py` lines 1-23):
```python
"""schema_validation gate — wraps the existing jsonschema check.

`pipeline_hierarchy.generator.validate()` already runs jsonschema
validation against `docs/hierarchy.schema.json` and raises on failure.
This gate wraps it as a registered check so the publish stage's gate
runner gets a uniform interface across all gates.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

from gates.registry import GateResult, SEVERITY_BLOCK, register_gate
```

**Gate body pattern** (`gates/parent_prefix.py` lines 38-94):
```python
@register_gate(stage="publish", severity=SEVERITY_BLOCK, name="parent_prefix")
def parent_prefix_gate(
    *,
    hierarchy: dict[str, Any],
    topic_labels: dict[str, str] | None = None,
    taxonomy_path: Path = DEFAULT_TAXONOMY_PATH,
    **_: object,
) -> GateResult:
    """Reject hierarchy bundles where any subtopic `display_name` starts
    with a parent-topic word.
    """
    if topic_labels is None:
        topic_labels = _load_topic_labels(taxonomy_path)

    violations: list[dict[str, str]] = []
    for tid, topic in hierarchy.get("topics", {}).items():
        # ... per-item checks ...
        if reason:
            violations.append({"topic_id": tid, "subtopic_id": ..., "reason": reason})

    if violations:
        return GateResult(
            name="parent_prefix",
            passed=False,
            severity=SEVERITY_BLOCK,
            summary=f"{len(violations)} subtopic display_name(s) start with a parent-topic word",
            details={
                "violation_count": len(violations),
                "violations": violations[:50],
                "truncated": len(violations) > 50,
            },
        )

    return GateResult(
        name="parent_prefix",
        passed=True,
        severity=SEVERITY_BLOCK,
        summary="no parent-prefix violations across the bundle",
    )
```

**What's idiomatic:**
- Decorator `@register_gate(stage="publish", severity=SEVERITY_BLOCK, name="reconciliation")` — gate self-registers on import
- All kwargs keyword-only; final `**_: object` to swallow unrelated runner kwargs
- Returns `GateResult(passed=..., severity=..., summary=..., details=...)` — never raises on validation outcome
- Details capped at 50 items with `truncated` flag; respects DDB 400KB row limit
- Summary is one-sentence human-readable; details is structured payload
- Pure function (no DDB writes, no S3 calls) — integrating stage owns I/O

**Phase 12 application** (`gates/reconciliation.py`):
- Register against `stage="publish"`, severity `SEVERITY_BLOCK`, name `"reconciliation"`
- Inputs: `exclusive_totals: dict[str, dict[str, float]]`, `inclusive_totals: dict[str, dict[str, float]]`
- Check D-17 invariant: `incl_sum >= excl_sum - 1e-9` (float tolerance)
- Plus D-33: per-CWID per-subtopic equality between `faculty.subtopic_scores` map and `SUBTOPIC_SCORE#` partition (this is two separate checks but the gate consolidates)
- On failure: `details={"violations": violations[:50], "violation_count": N}`
- Must be imported somewhere reachable at publish-time so the decorator runs — add `from gates import reconciliation` in `gates/__init__.py` or in `pipeline_hierarchy/publish.py` at module import

---

### `pipeline_feedback/sweep.py` (service / orchestrator, batch)

**Analog:** `pipeline_drift/evaluator.py` lines 1-96 (similar shape — reads N-day window of event rows, computes aggregations, writes one summary record)

**Imports + dataclass result pattern** (pipeline_drift/evaluator.py lines 25-96):
```python
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable

logger = logging.getLogger(__name__)

DRIFT_PK = "DRIFT#evaluation"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass
class DriftEvaluation:
    window_start: str
    window_end: str
    drift_window_days: int

    uncovered_count: int
    low_confidence_count: int
    # ...

    def to_dynamodb_item(self) -> dict[str, Any]:
        return {
            "PK": DRIFT_PK,
            "SK": f"DAY#{self.window_end[:10]}",
            "record_type": "DRIFT_EVALUATION",
            # ...
        }
```

**Window-filter helper** (lines 99-113):
```python
def _filter_in_window(
    items: Iterable[dict], *, since: datetime, until: datetime, ts_field: str
) -> list[dict]:
    out = []
    for item in items:
        raw = item.get(ts_field)
        if not raw:
            continue
        try:
            ts = _parse_iso(str(raw))
        except ValueError:
            continue
        if since <= ts <= until:
            out.append(item)
    return out
```

**What's idiomatic:**
- Module-level `_now_iso()` and `_parse_iso()` helpers — never reinvent the ISO 8601 boundary
- Dataclass for the structured result; `.to_dynamodb_item()` on the dataclass keeps build separate from write
- `Iterable[dict]` for inputs so tests can pass lists, prod can pass a paginator
- `since <= ts <= until` window inclusive on both ends — match this exactly
- Module-level constant for the PK prefix (`DRIFT_PK`)
- One `logger = logging.getLogger(__name__)` per module

**Phase 12 application** (`pipeline_feedback/sweep.py`):
- `@dataclass class FeedbackSweepRun` with `source_sweep_run_id`, `triggered_by`, `started_at`, `since`, three lists of finding records
- `def run_sweep(*, table, table_paginator, since: datetime, max_pmids: int, run_id: str, triggered_by: str) -> FeedbackSweepRun`
- Sort uncovered PMIDs by `top_topic_score` ascending (worst-fitting first per D-06); truncate at `max_pmids`; set `truncated: true` + `total_unprocessed_remaining: N`
- For recluster: read N days of `DRIFT#evaluation` rows; use the new `per_topic_low_confidence` field per D-34 (F-2); apply `recluster_persistence_days` threshold
- For diagnostic: read N days of `CRITIC_REJECT#` rows grouped by cwid; count distinct `pmid_set_hash` per cwid per D-09; aggregate `reason_code` distribution per D-30
- The cold-stage subprocess `pipeline_feedback/__main__.py sweep` must return 0 even on findings; only DDB/Bedrock failures return non-zero (D-02)

---

### `pipeline_cold/run.py` stage registration (orchestrator, event-driven)

**Analog:** `pipeline_cold/run.py` lines 90-131 (existing `default_cold_stages()`)

**Existing stage list** (lines 90-131):
```python
def default_cold_stages() -> list[ColdStage]:
    """Canonical cold-stage list per CONTEXT stage-assignment table."""
    return [
        ColdStage(name="score", command=[sys.executable, "-m", "score_publications"],
                  description="Full re-score of all in-corpus publications"),
        ColdStage(name="assign", command=[sys.executable, "backfill_all.py", "--skip-pm-copy"], ...),
        # ...
        ColdStage(name="rollup", command=[sys.executable, "-m", "rollup_by_cwid"], ...),
        ColdStage(name="backfill_spotlight", command=[sys.executable, "backfill_spotlight.py", "--publish"], ...),
        ColdStage(name="publish_hierarchy", command=[sys.executable, "-m", "pipeline_hierarchy.publish"], ...),
    ]
```

**`run_stage` returncode handling** (lines 166-210):
```python
def run_stage(stage: ColdStage, *, repo_root: Path, runner=None, env: dict | None = None) -> StageOutcome:
    t0 = time.monotonic()
    invoker = runner if runner is not None else subprocess.run
    proc = invoker(stage.command, cwd=str(repo_root), capture_output=True, text=True, check=False, env=env)
    duration_ms = int((time.monotonic() - t0) * 1000)
    if proc.returncode == 0:
        return StageOutcome(name=stage.name, status="complete", duration_ms=duration_ms, returncode=0)
    return StageOutcome(name=stage.name, status="failed", ..., stderr_tail=(proc.stderr or "")[-2000:])
```

**What's idiomatic:**
- Each stage is a `ColdStage(name, command, description)` tuple
- Commands invoke `[sys.executable, "-m", "module_name"]` (preferred) or `[sys.executable, "script.py", "--flag"]`
- Stages run sequentially; non-zero subprocess returncode → `outcome.status = "failed"` → orchestrator aborts
- Env vars `RECITERAI_COLD_RUN_ID` + `RECITERAI_HIERARCHY_VERSION` threaded via the `env` kwarg

**Phase 12 application:**
- Insert `ColdStage(name="feedback_sweep", command=[sys.executable, "-m", "pipeline_feedback", "sweep"], description="Non-gating Sonnet sweep over UNCOVERED_PMID#/LOW_CONFIDENCE_ASSIGNMENT#/CRITIC_REJECT# events → three typed finding records")` between `rollup` and `backfill_spotlight` (per RESEARCH F-7 placement rationale)
- The subprocess MUST return 0 even when findings are emitted (D-02); only DDB/Bedrock operational errors return non-zero
- `source_sweep_run_id` is internal to the sweep — do NOT expose as env var (per Open Question 3 in RESEARCH)

---

### `utils/stage_records.py` tunable_inputs extension (substrate builder, event-driven)

**Analog:** `utils/stage_records.py` lines 215-219 (Phase 11 `run_id` precedent)

**Existing additive-field pattern** (lines 195-219):
```python
def build_complete_record(
    *,
    stage: str,
    scope: str,
    # ...
    force_reason: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """
    Pure builder for a STAGE# complete row dict. No I/O.

    `run_id` (Phase 11 D-13): optional cold-run identifier used by diff.json
    producers to correlate assign-stage STAGE# rows with the current cold-run.
    Backwards-compatible — omitted when None so existing consumers see no change.
    """
    item = _base_item(stage=stage, scope=scope, ...)
    if output_pointer is not None:
        item["output_pointer"] = output_pointer
    if records_written is not None:
        item["records_written"] = records_written
    # ...
    if force_reason is not None:
        item["force_reason"] = force_reason
    if run_id is not None:
        item["run_id"] = run_id
    return item
```

**What's idiomatic:**
- New optional kwargs default to `None` — never `{}` or `[]` (avoids mutable-default trap and the "is this empty or absent" question)
- Field added with `if X is not None: item["X"] = X` — present-when-set, absent-when-not
- Docstring notes the Phase that added it and the `Backwards-compatible — omitted when None` invariant
- Same pattern repeated in `build_skipped_record` and `build_failed_record`

**Phase 12 application** (D-28):
- Add `tunable_inputs: dict[str, Any] | None = None` kwarg to all three builders (`build_complete_record`, `build_skipped_record`, `build_failed_record`)
- Set with `{"confidence_floor": 0.3, "confidence_floor_source": "config|cli|default", "tie_epsilon": 0.001, ...}` at stage startup
- `confidence_floor_source` discriminates `"config"` (from thresholds.json), `"cli"` (--confidence-floor flag), `"default"` (hardcoded fallback if config file missing)
- Document in builder docstring: "Phase 12 D-28: optional input audit for the stage. Carries tunable values read from `config/thresholds.json` or CLI overrides; absent when the stage takes no tunables. Same backwards-compatible pattern as `run_id`."

---

### `pipeline_drift/evaluator.py` per_topic_low_confidence extension (additive schema, event-driven)

**Analog:** `pipeline_drift/evaluator.py` lines 73-96 (the `to_dynamodb_item` method itself)

**Existing `to_dynamodb_item`** (lines 73-96):
```python
def to_dynamodb_item(self) -> dict[str, Any]:
    """Render as the DynamoDB item the evaluator persists.

    SK is the window end date so a daily cron produces one row per
    evaluation day. Floats are coerced to Decimal per the DDB rule.
    """
    return {
        "PK": DRIFT_PK,
        "SK": f"DAY#{self.window_end[:10]}",
        "record_type": "DRIFT_EVALUATION",
        "window_start": self.window_start,
        "window_end": self.window_end,
        "drift_window_days": self.drift_window_days,
        "uncovered_count": self.uncovered_count,
        "low_confidence_count": self.low_confidence_count,
        "stage_failed_count": self.stage_failed_count,
        "new_pmid_count": self.new_pmid_count,
        "uncovered_rate": Decimal(str(round(self.uncovered_rate, 6))),
        "low_confidence_max_topic": self.low_confidence_max_topic,
        "low_confidence_max_count": self.low_confidence_max_count,
        "triggered_thresholds": list(self.triggered_thresholds),
        "cold_run_recommended": self.cold_run_recommended,
        "severity": self.severity,
    }
```

**Phase 12 application** (D-34 + F-2):
- Add `per_topic_low_confidence: dict[str, int] = field(default_factory=dict)` to the `DriftEvaluation` dataclass
- In `evaluate()`, populate from existing per-topic dict at lines 153-161 (which today is computed and discarded)
- Sparse: only entries with `count > low_confidence_floor` count threshold; absence means zero
- In `to_dynamodb_item`, add: `if self.per_topic_low_confidence: item["per_topic_low_confidence"] = {k: int(v) for k, v in self.per_topic_low_confidence.items()}` — sparse-by-default
- Consumer (`pipeline_feedback/sweep.py` recluster trigger) reads this dict from `DRIFT#evaluation` rows; presence check distinguishes "field added later" rows from "zero this day" rows
- DO NOT change `low_confidence_max_topic` or `low_confidence_max_count` semantics; DO NOT change alert ladder; DO NOT change `cold_run_recommended` logic — preserve evaluator behavior contract (per CONTEXT line 140)

---

### `aggregate_subtopic_scores.py` inclusive aggregator (service, CRUD)

**Analog:** `aggregate_subtopic_scores.py:108-167` (the existing `_aggregate` function — becomes `_aggregate_exclusive`; new `_aggregate_inclusive` mirrors structure)

**Existing exclusive aggregator** (lines 108-167):
```python
def _aggregate(rows: list) -> tuple[dict, dict]:
    """
    Aggregate articleScore per (person_identifier, primary_subtopic_id).

    Returns (faculty_scores, subtopic_total_weights) where:
        faculty_scores = {personIdentifier: {subtopic_id: summed_article_score}}
        subtopic_total_weights = {subtopic_id: summed_article_score_across_faculty}
    """
    faculty_scores: dict = defaultdict(lambda: defaultdict(float))
    subtopic_total_weights: dict = defaultdict(float)

    included = 0
    skipped_unassigned = 0
    skipped_bad = 0

    for row in rows:
        primary = row.get("primary_subtopic_id")
        if not primary:
            skipped_unassigned += 1
            continue

        faculty_uid = row.get("faculty_uid") or ""
        if not faculty_uid:
            skipped_bad += 1
            continue
        person_identifier = _strip_faculty_prefix(faculty_uid)
        if not person_identifier:
            skipped_bad += 1
            continue

        try:
            relevance_score = float(row.get("score") or 0)
            impact_score = float(row.get("impact_score") or 0)
        except (TypeError, ValueError):
            skipped_bad += 1
            continue

        # articleScore formula — MUST match PM shared.ts byte-for-byte (P-10):
        #   TS: Math.pow(impactScore / 100, 1.2) * Math.pow(relevanceScore, 1.4)
        article_score = (impact_score / 100) ** 1.2 * relevance_score ** 1.4

        faculty_scores[person_identifier][primary] += article_score
        subtopic_total_weights[primary] += article_score
        included += 1

    logger.info(
        f"Aggregated: {included} rows included, "
        f"{skipped_unassigned} unassigned (no primary_subtopic_id), "
        f"{skipped_bad} skipped (missing fields)"
    )
    return (
        {pid: dict(m) for pid, m in faculty_scores.items()},
        dict(subtopic_total_weights),
    )
```

**What's idiomatic:**
- Float arithmetic in pure Python (no Decimal here — Decimal coercion happens at the DDB-write boundary, not in the aggregator)
- `defaultdict(lambda: defaultdict(float))` for the nested map
- Per-row try/except on the float coercion — bad data is skipped, not raised
- `included` / `skipped_*` counters + final logger.info — every aggregator pass tells you what it dropped
- Returns plain `dict` (un-defaultdict'd) so downstream `.get()` returns `None` for unknown keys
- The article_score formula comment is load-bearing — keep the P-10 warning visible

**Phase 12 application** (refactor + new function):
1. Rename existing `_aggregate` → `_aggregate_exclusive`. Add D-17 invariant comment at the function head.
2. Add `_aggregate_inclusive(rows: list, confidence_floor: float) -> tuple[dict, dict]` with:
   - Same row-validity checks as `_aggregate_exclusive` (faculty_uid stripping, float coercion)
   - Iterate over `subtopic_ids` (not `primary_subtopic_id`) — full article_score per above-floor entry per D-15
   - The `confidence_floor` parameter is kept on the signature so a future divergence shows up at the type level, not silently — but per D-16 the writer (`assign_subtopics.py:632`) already filtered, so do NOT re-filter
3. Caller writes BOTH the existing faculty-map AND the new `SUBTOPIC_SCORE#{topic}#{subtopic}` partition (F-1: this partition doesn't exist yet) AND the new `SUBTOPIC_SCORE_INCLUSIVE#{topic}#{subtopic}` partition
4. Inline D-17 invariant comment at the write site (not just in docs)

---

### `spotlight/critic.py` CRITIC_REJECT# additive write (producer, event-driven)

**Analog:** `spotlight/critic.py:565-571` (existing SPOTLIGHT_REVIEW# write — additive write goes adjacent)

**Existing write site** (lines 554-577):
```python
# Loop exhausted without a passing attempt — route to the review queue.
review_entry: dict = {
    "publish_id": publish_id,
    "subtopic_id": meta.subtopic_id,
    "parent_topic": parent_topic,
    "lede_text": last_lede,
    "flag_reason": "critic",
    "papers_used": [p.pmid for p in last_papers],
    "regen_count": MAX_RETRIES,
    "attempts": list(attempts),
}
if last_llm_verdict is not None:
    review_entry["critic_verdict"] = {
        "failed_constraint": last_llm_verdict.failed_constraint,
        "reason": last_llm_verdict.reason,
    }

write_review_entry(dynamo_client, review_entry)

logger.info(
    "Critic exhausted: subtopic_id=%s regen_count=%d -> review queue",
    meta.subtopic_id, MAX_RETRIES,
)
```

**Existing closed-enum source** (lines 177-205):
```python
@dataclass(frozen=True)
class LLMVerdict:
    """Outcome of the Haiku LLM-judge slice.

    ``failed_constraint`` and ``reason`` are empty strings on a passing
    verdict. On failure, ``failed_constraint`` is one of the four
    closed-vocabulary identifiers from the critic prompt (``active_verb``,
    ``anchored_in_synopses``, ``no_faculty_named``, ``institutional_voice``).
    """

    passed: bool
    failed_constraint: str
    reason: str
```

**What's idiomatic:**
- Frozen dataclasses for verdict shapes — the LLM enum is documented in the docstring
- Logger emits structured key=value pairs for grep
- The existing review_queue write happens AFTER `MAX_RETRIES + 1` attempts exhaust — same trigger condition for the new CRITIC_REJECT# write
- `last_llm_verdict is not None` gate — pre-LLM (deterministic-only) failures have no LLM verdict; these get `PRE_LLM_GATE` per D-30

**Phase 12 application** (D-08/D-30/D-31):
1. Define `CritReasonCode` StrEnum in `spotlight/critic.py` immediately after the LLMVerdict dataclass (line ~206):
   ```python
   from enum import StrEnum
   class CritReasonCode(StrEnum):
       ACTIVE_VERB = "active_verb"
       ANCHORED_IN_SYNOPSES = "anchored_in_synopses"
       NO_FACULTY_NAMED = "no_faculty_named"
       INSTITUTIONAL_VOICE = "institutional_voice"
       PRE_LLM_GATE = "pre_llm_gate"
   ```
2. Add CRITIC_REJECT# write AFTER the existing `write_review_entry` call at line 571 (alongside, not replacing):
   ```python
   write_review_entry(dynamo_client, review_entry)  # EXISTING — UNCHANGED
   
   # NEW: additive CRITIC_REJECT# write per D-08
   from utils.event_records import write_critic_reject
   if last_llm_verdict is not None:
       raw = last_llm_verdict.failed_constraint
       try:
           reason_code = CritReasonCode(raw).value
           extra = {}
       except ValueError:
           # Vocabulary drift — emit warning via pipeline_common.alert per D-31
           reason_code = "unknown"
           extra = {"raw_failed_constraint": raw}
   else:
       # Deterministic-only failure — never reached the LLM
       reason_code = CritReasonCode.PRE_LLM_GATE.value
       extra = {"pre_llm_constraint": last_deterministic_verdict.failed_constraints[0] if last_deterministic_verdict.failed_constraints else "unknown"}
   write_critic_reject(table, cwid=cwid, pmid_set=[p.pmid for p in last_papers],
                       reason_code=reason_code, ..., **extra)
   ```
3. Confirm `cwid` is accessible at this scope (per RESEARCH Open Question 2). If not, add `cwid: str` to `run_critic_loop` signature.
4. CRITIC_REJECT# row MUST NOT carry `lede_text` (per RESEARCH Security threat row — that's a SPOTLIGHT_REVIEW# concern only).

---

### `config/thresholds.schema.json` (JSON schema, config)

**Analog:** `docs/hierarchy.schema.json` (referenced by `gates/schema_validation.py:26` — `DEFAULT_SCHEMA_PATH = REPO_ROOT / "docs/hierarchy.schema.json"`)

**Validation usage pattern** (`gates/schema_validation.py:42-44`):
```python
schema = json.loads(schema_path.read_text(encoding="utf-8"))
try:
    jsonschema.validate(instance=hierarchy, schema=schema)
except jsonschema.ValidationError as err:
    return GateResult(passed=False, ...,
        details={
            "json_pointer": list(err.absolute_path),
            "validator": err.validator,
            "validator_value": err.validator_value,
            "message": err.message,
        })
```

**What's idiomatic:**
- Schema lives under `docs/` or `config/` as a sibling to the artifact it validates
- `json.loads(schema_path.read_text(encoding="utf-8"))` — always specify encoding
- `jsonschema.validate(instance=..., schema=...)` — kwargs, never positional
- Catch `jsonschema.ValidationError` specifically; reuse its `.absolute_path`, `.validator`, `.validator_value`, `.message` attributes for structured error output

**Phase 12 application** (D-27):
- `config/thresholds.schema.json` is a JSON Schema Draft 2020-12 document with:
  - `"$schema": "https://json-schema.org/draft/2020-12/schema"`
  - `"type": "object"`, `"additionalProperties": false`
  - Every key in `config/thresholds.json` declared with `type`, `minimum`, `maximum`, `description`
  - All existing keys (`uncovered_score_floor`, `low_confidence_floor`, `drift_*`, `spotlight_dirty_*`) PLUS new Phase 12 keys (`tie_epsilon`, `confidence_floor`, `feedback_sweep_max_pmids`, `recluster_persistence_days`, `critic_reject_persistence_days`, `critic_reject_cwid_max`, `feedback_diagnostic_max_underlying`, `score_floor`)
- `utils/env_check.py` loads the schema at startup and calls `jsonschema.validate(instance=cfg, schema=schema)` — fail fast at boot
- `score_floor` (from `assign_subtopics.py:94`) per D-23 lifted alongside the others

---

### `utils/env_check.py` — schema validation + G-1 (preflight, config)

**Analog:** `utils/env_check.py` lines 1-80 (existing column-check pattern) + `gates/schema_validation.py` (for jsonschema usage)

**Existing column-check pattern** (`utils/env_check.py` lines 36-72):
```python
def run_env_checks():
    """
    Run all environment pre-checks for the ReCiter AI pipeline.

    Checks:
    1. DB_USERNAME environment variable is set
    2. reciterai_synopsis.external_id column exists
    3. analysis_summary_person column names
    4. reciterai_keyword_relevance schema and data presence
    """
    results = {}
    errors = []

    # --- Check 1: DB_USERNAME env var ---
    db_user = os.environ.get('DB_USERNAME')
    assert db_user, "DB_USERNAME not set -- set it in ~/.zshrc and source it"
    results['DB_USERNAME'] = 'OK'

    # ...

    # --- Check 2: reciterai_synopsis columns ---
    synopsis_cols = _describe_table(conn, 'reciterai_synopsis')
    synopsis_col_names = [c['Field'] for c in synopsis_cols]
    if 'external_id' not in synopsis_col_names:
        errors.append("CRITICAL: reciterai_synopsis.external_id NOT FOUND. ...")
```

**What's idiomatic:**
- `results` (success ledger) + `errors` (failure ledger) twin lists; `_print_results(results, errors)` at the end
- Each check is a section marked with `# --- Check N: <name> ---` and `print(f"[CHECK] ...")` / `print(f"[OK] ...")` / `errors.append(f"CRITICAL: ...")`
- Hard exits via `sys.exit(1)` on any critical failure
- DB columns are referenced as string literals — G-1 lifts these into a central data structure

**Phase 12 application:**
- **G-1:** Replace inline string literals like `'external_id'`, `'synopsis'`, `'nameFirst'` with a module-level constant `EXPECTED_COLUMNS: dict[str, list[str]]` mapping table name to required column list. Refactor each `# --- Check N ---` block to loop over the structure.
- **D-27:** Add a new `# --- Check 5: thresholds.json schema validation ---` block:
  ```python
  import jsonschema
  THRESHOLDS_SCHEMA = REPO_ROOT / "config/thresholds.schema.json"
  THRESHOLDS_FILE = REPO_ROOT / "config/thresholds.json"
  cfg = json.loads(THRESHOLDS_FILE.read_text())
  schema = json.loads(THRESHOLDS_SCHEMA.read_text())
  try:
      jsonschema.validate(instance=cfg, schema=schema)
      results['thresholds.json'] = 'schema-valid'
  except jsonschema.ValidationError as err:
      errors.append(f"CRITICAL: thresholds.json invalid at {list(err.absolute_path)}: {err.message}")
  ```

---

### `config/thresholds.md` (docs) — NO ANALOG

**No analog in codebase.** Phase 12 establishes this pattern (sibling markdown documentation for a config JSON file).

**Required structure** (per D-26):
```markdown
# config/thresholds.json — Tunable Reference

Every key in `thresholds.json` documented here. JSON doesn't carry comments;
this sibling file centralizes meaning alongside values.

## Existing keys (pre-Phase 12)

### `uncovered_score_floor` (float, default 0.4)
A publication's top topic score below this floor produces an UNCOVERED_PMID# event.
If you raise this: more PMIDs flagged as uncovered; sweep workload grows.

### `low_confidence_floor` (float, default 0.35)
Subtopic-assignment confidence below this floor produces a LOW_CONFIDENCE_ASSIGNMENT# event.
If you raise this: more topics may trigger RECLUSTER_RECOMMENDATION# under D-07.

(... one section per key ...)

## Phase 12 additions

### `confidence_floor` (float, default 0.3)
Assignment-time floor for subtopic_ids[] membership. Distinct from low_confidence_floor.
Different decision (per D-25): membership vs event-emission.
If you raise this: papers below the threshold drop from subtopic_ids[] entirely.

(... one section per new key ...)
```

**Phase 12 sets the precedent.** Future phases that add tunables update both `thresholds.json` AND `thresholds.md`.

---

### Tests — `tests/test_feedback_sweep.py` and siblings (unit test, test)

**Analogs:**
- `tests/test_event_records.py` (lines 1-80) — for builder shape tests
- `tests/test_gates_parent_prefix.py` (lines 1-60) — for gate tests
- `tests/test_pipeline_drift_evaluator.py` — for evaluator-style tests
- `tests/test_uncovered_pmid_event.py` — for producer-at-write-site tests

**Test file docstring + imports** (`tests/test_event_records.py:1-19`):
```python
"""Tests for utils.event_records — Phase 10 T6 substrate.

Covers:
- UNCOVERED_PMID# record shape, top-3 cap, sort order, Decimal coercion
- LOW_CONFIDENCE_ASSIGNMENT# record shape, max-confidence derivation
- load_thresholds happy path and missing-file behavior
- idempotency: PK constant per PMID + SK constant means rerun overwrites
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from utils import event_records as er
```

**Builder-shape test** (lines 48-62):
```python
def test_uncovered_pmid_record_shape():
    record = er.build_uncovered_pmid_record(
        pmid="12345",
        taxonomy_version="taxonomy_v2",
        top_topics=[("cardio", 0.32), ("neuro", 0.28), ("onco", 0.15)],
        created_at="2026-05-12T12:00:00Z",
    )
    assert record["PK"] == "UNCOVERED_PMID#12345"
    assert record["SK"] == "GLOBAL"
    assert record["record_type"] == "UNCOVERED_PMID"
    assert record["pmid"] == "12345"
    assert record["taxonomy_version"] == "taxonomy_v2"
    assert record["top_topic_score"] == Decimal("0.32")
    assert record["source_stage"] == "score_publications"
    assert record["created_at"] == "2026-05-12T12:00:00Z"
```

**Gate test with injected violation** (`tests/test_gates_parent_prefix.py:28-47`):
```python
def test_parent_prefix_fails_on_injected_violation():
    """Hand-inject a violation and confirm the gate blocks with structured details."""
    if not LIVE_HIERARCHY.exists():
        pytest.skip("v2026-05-12 dry-run artifact not on disk")
    hierarchy = json.loads(LIVE_HIERARCHY.read_text())

    target_topic = "microbiome_research"
    hierarchy["topics"][target_topic]["subtopics"][0]["display_name"] = (
        "Microbiome & Cancer Immunotherapy Response"
    )

    result = parent_prefix_gate(hierarchy=hierarchy)
    assert result.passed is False
    assert result.blocked is True
    assert result.details["violation_count"] == 1
```

**What's idiomatic:**
- Test file docstring lists every behavior covered, separated by `-`
- `from __future__ import annotations` at the top (consistent with prod modules)
- Always pin `created_at` in builder tests to a literal ISO string for determinism
- `MagicMock` for the DDB table; assert `table.put_item.call_args` for the written item
- Build a known-good fixture, then inject a single violation — the difference makes the assertion concrete
- `pytest.skip(...)` when a fixture file is missing on disk, never silently pass

**Phase 12 applications:**
- `tests/test_critic_reject_event.py` — mirror `test_uncovered_pmid_event.py` (builder shape, pmid_set_hash dedup, additive-not-replacing-SPOTLIGHT_REVIEW#)
- `tests/test_reconciliation_gate.py` — mirror `test_gates_parent_prefix.py` (passes on aligned totals; fails with structured `violations` on injected mismatch)
- `tests/test_aggregate_inclusive.py` — covers D-15 (uniform full weight), D-16 (writer pre-filters), D-17 invariant
- `tests/test_aggregate_idempotent.py` — re-run aggregator with same input; assert byte-identical DDB write args via `MagicMock`
- `tests/test_thresholds_schema.py` — malformed `thresholds.json` raises `jsonschema.ValidationError`; well-formed passes
- `tests/test_thresholds_keys.py` — `confidence_floor` AND `low_confidence_floor` both present and distinct (D-25 anti-collapse test)
- `tests/test_feedback_sweep.py` — `test_idempotent_per_run_id`, `test_cap_emits_truncated`, `test_worst_fitting_first`, `test_recluster_trigger`, `test_diagnostic_distinct_pmid_sets`
- `tests/test_feedback_render.py` — byte-identical render assertion (mirror `test_hierarchy_reproducibility.py:119` style)
- `tests/test_cold_path_e2e.py` (G-37) — extend `tests/test_hierarchy_reproducibility.py` pattern to publish-end-to-end. Pitfall 4: plan fixture shape (2 topics × 3 subtopics × 5 pubs = 30 pubs) BEFORE writing the test, document in docstring.

---

## Shared Patterns

### Pattern A: DDB event-record idempotency

**Source:** `utils/event_records.py` lines 78-91
**Apply to:** All four NEW finding records + CRITIC_REJECT#

```python
return {
    "PK": f"<RECORD_TYPE>#{stable_identifier}",
    "SK": "GLOBAL",
    "record_type": "<RECORD_TYPE>",
    # ... fields ...
    "created_at": created_at or _now_iso(),
    "source_stage": "<producing_module>",
}
```

Stable PK + `SK="GLOBAL"` → reruns overwrite. The `source_sweep_run_id` lives in the body, never in the key (D-05).

### Pattern B: Decimal coercion at the builder boundary

**Source:** `utils/event_records.py` line 86, `pipeline_drift/evaluator.py` line 90
**Apply to:** Every float that crosses into a DDB row

```python
Decimal(str(float_val))                       # for top-level numbers
Decimal(str(round(self.float_field, 6)))      # when bounded precision matters
{sid: Decimal(str(c)) for sid, c in d.items()}  # for nested float dicts
```

Never `Decimal(float_val)` directly — that preserves IEEE 754 noise.

### Pattern C: Gate registration via decorator import-side-effect

**Source:** `gates/parent_prefix.py:38`, `gates/schema_validation.py:29`
**Apply to:** `gates/reconciliation.py`

```python
@register_gate(stage="publish", severity=SEVERITY_BLOCK, name="reconciliation")
def reconciliation_gate(*, exclusive_totals, inclusive_totals, **_: object) -> GateResult:
    ...
```

Gate must be IMPORTED somewhere reachable at publish-time so the decorator runs. Add `from gates import reconciliation` to either `gates/__init__.py` or `pipeline_hierarchy/publish.py`.

### Pattern D: Backwards-compatible additive field on existing dataclass/dict builders

**Source:** `utils/stage_records.py:217-219` (Phase 11 `run_id`)
**Apply to:** STAGE# `tunable_inputs` (D-28), DRIFT# `per_topic_low_confidence` (D-34)

```python
def build_X(*, existing_param, new_param: T | None = None) -> dict:
    item = {...}
    if new_param is not None:
        item["new_param"] = new_param
    return item
```

New field defaults to `None`, included only when set, documented in docstring with the Phase it was added.

### Pattern E: ISO 8601 timestamp helpers — never reinvent

**Source:** `utils/event_records.py:41-42`, `pipeline_drift/evaluator.py:38-39`

```python
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

def _parse_iso(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)
```

Every new module that touches timestamps copies these two helpers. UTC always, `Z` suffix always, seconds precision unless a specific reason demands more.

### Pattern F: CLI subcommand + injected seam pattern for testability

**Source:** `review/cli.py:39-53` (`_default_get_table`, `_default_invoke_editor`)
**Apply to:** `pipeline_feedback/cli.py`

Subcommand handlers take callable parameters with `_default_*` factories. Tests pass `MagicMock`s; prod uses defaults.

### Pattern G: Module docstring lists every subcommand + every exit code

**Source:** `review/cli.py:1-15`
**Apply to:** `pipeline_feedback/cli.py` docstring

Exit codes are documented at the top of the file, not in the README. Operators reading `python -m pipeline_feedback --help` follow up with `head -20 pipeline_feedback/cli.py`.

### Pattern H: PII / Information Disclosure boundary

**Source:** `spotlight/sensitive_gate.py:8-11`, RESEARCH security threat table
**Apply to:** CRITIC_REJECT# write (no `lede_text`), SPOTLIGHT_DIAGNOSTIC# row, markdown render (operator-facing only)

CRITIC_REJECT# carries `reason_code`, `pmid_set`, `regen_count` ONLY — never lede text. The existing SPOTLIGHT_REVIEW# does carry `lede_text`; do not duplicate it onto CRITIC_REJECT#.

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `config/thresholds.md` | docs | doc | No existing sibling-markdown-for-config-json pattern in the repo. Phase 12 establishes it (D-26). Closest reference shape: any doc under `docs/` that lists keys + meanings, e.g. `docs/topic-subtopic-assignment.md`. |
| `pipeline_feedback/markdown_render.py` | renderer | transform | No existing deterministic markdown renderer in the codebase. Closest principle: `pipeline_hierarchy/generator.py:70-74` (no `generated_at` in body — timestamp in filename only). Apply that principle: function takes a `run_id` + row payload, returns deterministic markdown bytes; caller writes to `feedback_sweep_{run_id}_{date}.md`. |
| `pipeline_feedback/prompts/uncovered_pmid_sonnet_v0.md` | prompt | n/a | Prompts exist scattered across `score_publications/`, `spotlight/`, but no central convention for prompt-version-file naming. Recommendation: `_v0.md` suffix versioning, single prompt per file. |

## Metadata

**Analog search scope:**
- `/Users/paulalbert/Dropbox/GitHub/ReciterAI/utils/` (event_records, stage_records, env_check)
- `/Users/paulalbert/Dropbox/GitHub/ReciterAI/gates/` (registry, parent_prefix, schema_validation, pii, schema_roundtrip)
- `/Users/paulalbert/Dropbox/GitHub/ReciterAI/review/` (cli, __main__, store, validator)
- `/Users/paulalbert/Dropbox/GitHub/ReciterAI/pipeline_drift/` (evaluator, severity)
- `/Users/paulalbert/Dropbox/GitHub/ReciterAI/pipeline_cold/` (run)
- `/Users/paulalbert/Dropbox/GitHub/ReciterAI/spotlight/` (critic, review_queue, sensitive_gate, types)
- `/Users/paulalbert/Dropbox/GitHub/ReciterAI/pipeline_hierarchy/` (generator)
- `/Users/paulalbert/Dropbox/GitHub/ReciterAI/aggregate_subtopic_scores.py`
- `/Users/paulalbert/Dropbox/GitHub/ReciterAI/assign_subtopics.py`
- `/Users/paulalbert/Dropbox/GitHub/ReciterAI/tests/` (test_event_records, test_gates_parent_prefix, test_hierarchy_reproducibility, test_pipeline_drift_evaluator, test_uncovered_pmid_event)
- `/Users/paulalbert/Dropbox/GitHub/ReciterAI/config/` (thresholds.json, excluded_topics.json)
- `/Users/paulalbert/Dropbox/GitHub/ReciterAI/docs/` (hierarchy.schema.json)

**Files scanned for analogs:** 18 source files + 6 test files = 24 files read.

**Pattern extraction date:** 2026-05-12

---

*Phase: 12-feedback-loops-both-aggregations-residual-hygiene*
*Patterns mapped: 2026-05-12*

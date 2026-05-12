---
phase: 11
phase_name: Versioning, Review State, Diff Signaling
mapped: 2026-05-12
files_analyzed: 17
analogs_found: 16
---

# Phase 11 Pattern Map

## File Classification

### Surface 1 — `hierarchy_version` stamping + rotation rewrite

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `utils/dynamodb_subtopic_migration.py` (modify) | writer | request-response (UpdateItem) | self (`update_activity_subtopics`) | self — extend signature |
| `assign_subtopics.py:642–648` (modify) | caller of writer | request-response | self | self — pass 6th arg |
| `spotlight/history_writer.py:114–127` (modify) | writer | request-response (UpdateItem) | self (`update_history`) | self — change PK shape |
| `spotlight/rotation_selector.py:135–202` (modify) | reader + parser | batch / request-response | self (`fetch_history`, `_ingest_responses`) | self — change key shape + parser split |
| `spotlight/publish.py:218` (modify) | caller plumbing | request-response | self | self — pass `hierarchy_version` alongside `publish_id` |
| `scripts/migrate_activity_hierarchy_version.py` (NEW) | migration script | batch CRUD (scan + UpdateItem) | `scripts/migrate_cost_field.py` | exact |
| `scripts/migrate_spotlight_history_pk.py` (NEW) | migration script | batch CRUD (scan + PutItem+DeleteItem) | `scripts/migrate_cost_field.py` + `backfill_spotlight.py::_run_reset_history` | hybrid (two analogs) |
| `pipeline_cold/run.py` (modify) | CLI orchestrator | request-response | self | self — add `run_id` UUID, thread via env/argv, add `STAGE#hierarchy_version_cutover` write |
| `tests/test_dynamodb_hierarchy_backfill.py` (NEW) | test | unit | `tests/test_stage_records.py` | role-match |
| `tests/test_spotlight_history_migration.py` (NEW) | test | unit | `test_spotlight_rotation_selector.py` + `tests/test_stage_records.py` | hybrid |

### Surface 2 — REVIEW# + CLI

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `review/__main__.py` (NEW) | CLI dispatcher | request-response | `gates/cli.py` (argparse w/ subcommand-style flags) + `pipeline_cold/run.py` (argparse main) | role-match |
| `review/cli.py` (NEW) | CLI handler | request-response | `gates/cli.py` | role-match |
| `review/validator.py` (NEW) | validator (pure) | transform | gate functions in `gates/parent_prefix.py` (per registry pattern) — no direct read here, but the pure-function shape | partial |
| `review/template.py` (NEW) | YAML generator | request-response | (greenfield — flag for planner) | none |
| `review/store.py` (NEW) | DynamoDB writer | request-response (PutItem) | `utils/dynamodb_subtopic_migration.py` (UpdateItem helpers) + `utils/stage_records.py::write_complete` (PutItem) | role-match |
| `tests/test_review_validator.py` (NEW) | test | unit | `tests/test_stage_records.py` (pure-builder tests with MagicMock) | role-match |
| `tests/test_review_cli.py` (NEW) | test | integration | `tests/test_gates_cli.py` (exists; planner should read) | role-match |

### Surface 3 — Structured change signaling (diff.json + G-29 + write order)

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `pipeline_hierarchy/publish.py` (modify) | publisher + audit-row writer | request-response + file-I/O | self | self — add `compute_diff()`, reorder `upload_to_s3()`, write `STAGE#g29_cutover` |
| `pipeline_hierarchy/bundler.py:183–186` (modify) | builder | transform | self | self — drop `generated_at` stamp |
| `pipeline_hierarchy/generator.py:60–67` (modify) | builder | transform | self | self — drop `generated_at` write into hierarchy |
| `utils/s3_client.py:100–117` (modify) | S3 client | request-response | self | self — add `cache_control` kwarg |
| `utils/stage_records.py` (modify) | builder | transform | self | self — add optional `run_id` kwarg to all three builders |
| `docs/hierarchy.schema.json` (modify if required) | schema | config | self | self — mark `generated_at` optional |
| `tests/test_hierarchy_reproducibility.py` (NEW) | test | unit | `tests/test_hierarchy_publisher.py` | role-match |
| `tests/test_publish_diff.py` (NEW) | test | unit | `tests/test_publish_integration.py` (exists; planner should extend it OR add new) | role-match |
| `utils/test_s3_client.py` (NEW) | test | unit | `tests/test_stage_records.py` (MagicMock pattern) | role-match |

---

## Pattern Assignments

### `utils/dynamodb_subtopic_migration.py` — extend `update_activity_subtopics`

**Analog:** self, lines 37–70.

**Current signature + write expression** (lines 37–70):
```python
def update_activity_subtopics(
    pk: str,
    sk: str,
    subtopic_ids: list[str],
    primary_subtopic_id: str,
    confidences: Mapping[str, float],
) -> dict:
    table = get_table(TABLE_NAME)
    return table.update_item(
        Key={"PK": pk, "SK": sk},
        UpdateExpression=(
            "SET subtopic_ids = :sids, "
            "primary_subtopic_id = :pid, "
            "subtopic_confidences = :confs"
        ),
        ExpressionAttributeValues={
            ":sids": list(subtopic_ids),
            ":pid": primary_subtopic_id,
            ":confs": {k: to_decimal(v) for k, v in confidences.items()},
        },
    )
```

**Phase 11 delta:** add 6th positional/keyword arg `hierarchy_version: str` and one extra SET clause. Pattern to copy:
- Decimal coercion convention (line 68) — strings don't need it, but reaffirm: `hierarchy_version` is `S`-typed so no `to_decimal()` needed.
- Idempotency claim in docstring (lines 12–13) — call this out for the new field too.
- Three-name `__all__` policy (lines 139–143) — signature change does NOT add a new name; preserve `__all__`.

---

### `assign_subtopics.py:642–648` — caller threading

**Analog:** self.

**Current call site** (lines 640–656):
```python
for row in group["rows"]:
    try:
        update_activity_subtopics(
            pk=row["PK"],
            sk=row["SK"],
            subtopic_ids=subtopic_ids,
            primary_subtopic_id=primary,
            confidences=confidences,
        )
        stats["rows_written"] += 1
    except Exception as exc:
        logger.warning(
            f"DynamoDB update failed pmid={pmid} sk={row.get('SK')}: {exc}"
        )
        stats["status"] = "partial"
        stats["error"] = str(exc)
```

**Phase 11 delta:** pass `hierarchy_version=<resolved>` keyword. Source of `hierarchy_version` per RESEARCH §"Source of hierarchy_version at write time": thread from cold-path orchestrator (`pipeline_cold.run.main()`) via argv `--hierarchy-version` (same shape precedent as `--initiated-by` in `pipeline_cold/run.py:248`) into `assign_subtopics.run()` signature into this call site. The keyword-arg style above is the existing convention — preserve it.

---

### `spotlight/history_writer.py:114–127` — rotation history writer

**Analog:** self.

**Current writer body** (lines 114–127):
```python
for s in selections:
    client.update_item(
        TableName=TABLE_NAME,
        Key={
            "PK": {"S": f"SPOTLIGHT_HISTORY#{s.entry.subtopic_id}"},
            "SK": {"S": "STATE"},
        },
        UpdateExpression=_UPDATE_EXPRESSION,
        ExpressionAttributeValues={
            ":one": {"N": "1"},
            ":now": {"S": now_iso},
            ":pid": {"S": publish_id},
        },
    )
```

**Phase 11 delta — PK shape** (D-04):
- Old: `f"SPOTLIGHT_HISTORY#{s.entry.subtopic_id}"`
- New: `f"SPOTLIGHT_HISTORY#{hierarchy_version}#{s.entry.subtopic_id}"`

**Signature change:** add `hierarchy_version: str` parameter to `update_history(client, selections, publish_id, hierarchy_version)`. Preserve the existing T-06-03-01 security pattern (lines 18–20, 42–46): `_UPDATE_EXPRESSION` stays a literal constant; `hierarchy_version` flows through the **PK string** (not `ExpressionAttributeValues`), which is acceptable because it's operator/code-controlled, not user-input. Document this in the docstring.

**Caller plumbing in `spotlight/publish.py:218`:**
```python
# Current:
update_history(dynamo_client, selections, publish_id=version)
# Phase 11 — flag OQ-2: planner picks how hierarchy_version reaches this call site.
# If publish_id == hierarchy_version is the adopted convention (recommended per
# RESEARCH OQ-2 option a/b), pass both:
update_history(dynamo_client, selections, publish_id=version, hierarchy_version=version)
```

---

### `spotlight/rotation_selector.py` — reader + parser

**Analog:** self.

**Read site** (lines 158–172):
```python
client = client or _get_default_client()
result: dict[str, Optional[str]] = {}

for start in range(0, len(subtopic_ids), BATCH_GET_LIMIT):
    chunk = subtopic_ids[start : start + BATCH_GET_LIMIT]
    keys = [
        {"PK": {"S": f"{_HISTORY_PK_PREFIX}{sid}"}, "SK": {"S": "STATE"}}
        for sid in chunk
    ]
    request_items = {TABLE_NAME: {"Keys": keys}}

    resp = client.batch_get_item(RequestItems=request_items)
    _ingest_responses(resp, result)
```

**Parser site (THE FRAGILE PART)** (lines 189–201):
```python
def _ingest_responses(resp: dict, result: dict[str, Optional[str]]) -> None:
    for item in resp.get("Responses", {}).get(TABLE_NAME, []):
        pk = item.get("PK", {}).get("S", "")
        if not pk.startswith(_HISTORY_PK_PREFIX):
            continue
        sid = pk[len(_HISTORY_PK_PREFIX) :]   # ← Phase 11 break: this strips
                                              #   only the prefix; under D-04
                                              #   the result is
                                              #   "{hierarchy_version}#{sid}",
                                              #   not "{sid}".
        last = item.get("last_shown_at", {}).get("S")
        result[sid] = last
```

**Phase 11 delta:**

1. **`fetch_history` signature**: add `hierarchy_version: str` (no default — force callers to specify). Keys list comprehension changes to:
   ```python
   keys = [
       {"PK": {"S": f"{_HISTORY_PK_PREFIX}{hierarchy_version}#{sid}"}, "SK": {"S": "STATE"}}
       for sid in chunk
   ]
   ```

2. **`_ingest_responses` split logic**:
   ```python
   sid = pk.rsplit("#", 1)[-1]   # take LAST segment, not everything after prefix
   ```
   Preserve the `startswith` guard (line 197) as-is.

3. **`select_with_diversity` plumbing**: this function is called from `spotlight/publish.py` which already owns `version`. Pass it through `fetch_history(client, sids, hierarchy_version=version)`.

---

### `scripts/migrate_activity_hierarchy_version.py` (NEW) — backfill stamping

**Analog:** `scripts/migrate_cost_field.py` (exact match for shape and concerns).

**Imports + docstring pattern** (analog lines 1–32):
```python
"""
One-off migration for Phase 11 D-02 / D-17: stamp `hierarchy_version`
on every activity record that already carries subtopic fields.

Behavior:
    - Scans rows where attribute_exists(primary_subtopic_id).
    - For each row, stamps hierarchy_version = "v0.0.0-pre-phase-11"
      (per D-17 — PROCESSING# join would yield 100% orphans because
      pre-Phase-11 PROCESSING# rows don't carry hierarchy_version).
    - Idempotent: rows that already carry hierarchy_version are skipped
      (counted under `n_already_stamped`).
    - Reports orphan counts in two buckets per D-02 + D-05 (see surface 1).

Run order:
    python -m scripts.migrate_activity_hierarchy_version --dry-run   # preview
    python -m scripts.migrate_activity_hierarchy_version             # apply

This is a one-shot script. Delete after Phase 11 lands.
"""
from __future__ import annotations

import argparse
import sys
from decimal import Decimal  # only if writing numerics; not needed for string version

from utils.dynamodb_helpers import get_table
```

**Scan + paginate loop pattern** (analog lines 35–93):
```python
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print counts; do not write.")
    args = parser.parse_args()

    table = get_table()
    scan_kwargs = {
        "FilterExpression": "attribute_exists(primary_subtopic_id) AND attribute_not_exists(hierarchy_version)",
    }

    n_total = 0
    n_already_stamped = 0
    n_stamped = 0

    while True:
        resp = table.scan(**scan_kwargs)
        for item in resp.get("Items", []):
            n_total += 1
            # ... per-row logic (UpdateItem with SET hierarchy_version) ...
            if not args.dry_run:
                table.update_item(
                    Key={"PK": item["PK"], "SK": item["SK"]},
                    UpdateExpression="SET hierarchy_version = :v",
                    ExpressionAttributeValues={":v": "v0.0.0-pre-phase-11"},
                )
                n_stamped += 1

        if "LastEvaluatedKey" not in resp:
            break
        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    # Print counts (analog lines 94–100)
    print(f"Rows scanned:        {n_total}")
    print(f"Already stamped:     {n_already_stamped}")
    print(f"Newly stamped:       {n_stamped}")
    if args.dry_run:
        print("\n(dry-run — no writes performed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

**Concrete reuse:**
- Filter convention: `FilterExpression="begins_with(PK, :prefix)"` is in the analog for STAGE# scan; Phase 11 needs `attribute_exists(primary_subtopic_id)` instead — same scan/paginate idiom, different filter.
- Pagination loop with `LastEvaluatedKey` (analog lines 90–92) is verbatim reusable.
- `--dry-run` flag + count-only output (analog lines 99–100) is verbatim reusable.
- One-shot script comment + delete-after-phase-lands note (analog lines 19–20) is verbatim reusable.

---

### `scripts/migrate_spotlight_history_pk.py` (NEW) — rotation history rewrite

**Analogs (hybrid):**
- `scripts/migrate_cost_field.py` for argparse + scan/paginate shape.
- `backfill_spotlight.py:678–733` (`_run_reset_history` + `_flush_delete_batch`) for the scan + batch-delete pattern.

**Scan + decide bucket** (per RESEARCH §"Migration mechanics"):
```python
# Phase 11 D-04 + D-05 — three buckets:
#   real: last_shown_publish_id matches ^v\d{4}-\d{2}-\d{2}$
#   never_spotlighted: last_shown_publish_id missing
#   malformed_publish_id: present but unparseable
import re
_PUBLISH_ID_PATTERN = re.compile(r"^v\d{4}-\d{2}-\d{2}$")

scan_kwargs = {
    "FilterExpression": "begins_with(PK, :prefix)",
    "ExpressionAttributeValues": {":prefix": "SPOTLIGHT_HISTORY#"},
}
```

**Per-row PutItem + DeleteItem** (because PK changes; UpdateItem cannot move PKs). Risk mitigation per CONTEXT R1: dry-run default, `--commit` required, per-row diff log:
```python
# Decision rules (per RESEARCH "rotation history rewrite mechanics"):
last_shown = item.get("last_shown_publish_id")
if last_shown is None:
    new_version = "0.0.0-orphan"
    n_never_spotlighted += 1
elif _PUBLISH_ID_PATTERN.match(last_shown):
    new_version = last_shown   # already v{ISO-date}
    n_real += 1
else:
    new_version = "0.0.0-orphan"
    n_malformed_publish_id += 1
    # FAIL LOUD per CONTEXT R1 — operator confirms via review log
    diff_log.write(f"MALFORMED row PK={item['PK']} last_shown={last_shown!r}\n")

old_sid = item["PK"].split("#", 1)[1]   # strip "SPOTLIGHT_HISTORY#"
new_pk = f"SPOTLIGHT_HISTORY#{new_version}#{old_sid}"

if args.commit:
    new_item = {**item, "PK": new_pk}
    table.put_item(Item=new_item)
    table.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})
```

**Confirm-and-commit gate** (analog: `backfill_spotlight.py:687–693`):
```python
confirm = input(
    "WARNING: this will REWRITE every SPOTLIGHT_HISTORY# row's PK. "
    "Review the diff log at <path> first. Type 'yes' to confirm: "
)
if confirm.strip().lower() != "yes":
    print("Aborted.")
    return 1
```

---

### `pipeline_cold/run.py` — `run_id` threading + `STAGE#hierarchy_version_cutover` write

**Analog:** self.

**Current argparse main + `STAGE#cold_run` write** (lines 229–342). Reuse:
- argparse pattern (lines 229–262) — add new flags below.
- `started_at = _now_iso()` + monotonic clock pattern (lines 278–279) — verbatim.
- `STAGE#cold_run` PutItem block (lines 326–340) — analogous shape for `STAGE#hierarchy_version_cutover`.

**Phase 11 deltas (D-06, D-13):**

1. **Generate `run_id` at top of main** (after argparse, before stage loop):
   ```python
   import uuid
   run_id = str(uuid.uuid4())
   ```

2. **Thread `run_id` via env var into subprocess stages** (cleanest — does not require touching every stage's argv):
   ```python
   import os
   env = {**os.environ, "RECITERAI_COLD_RUN_ID": run_id}
   # Pass env= into subprocess.run call in run_stage()
   ```
   Note: `run_stage` (line 163) currently uses `subprocess.run` directly; minimal change: thread `env=env` from `main()` through.

3. **Mint `hierarchy_version` upfront** (D-06 + Surface 1 OQ resolution recommendation):
   ```python
   new_version = f"v{started_at[:10]}"   # mirrors generator.py:112 convention
   ```
   Thread into `publish_hierarchy` and `assign` stages via env or argv.

4. **Write `STAGE#hierarchy_version_cutover#GLOBAL` row** per D-06. Mirror the existing `STAGE#cold_run` write (lines 326–340):
   ```python
   if table is not None:
       cutover_row = build_complete_record(
           stage="hierarchy_version_cutover",
           scope="GLOBAL",
           input_hash=compute_input_hash("hierarchy_version_cutover",
                                         {"new_version": new_version}),
           started_at=started_at,
           completed_at=_now_iso(),
           duration_ms=duration_ms,
           cost_observed_usd=COLD_RUN_COST_USD,
       )
       cutover_row["prev_version"] = prev_version
       cutover_row["new_version"] = new_version
       cutover_row["migrated_rotation_count"] = migrated_rotation_count
       cutover_row["orphan_count"] = orphan_count
       cutover_row["initiated_by"] = args.initiated_by
       table.put_item(Item=cutover_row)
   ```
   The "extra-attribute-after-builder" pattern (lines 336–339) is the established convention for stage-specific metadata.

**O-03 implementation hook:** the "top-vs-end" decision is whether to call `table.put_item(Item=cutover_row)` once at end (cleaner) vs. twice (early stub + final update). Existing `STAGE#cold_run` chose end-only; recommend mirror.

---

### `review/__main__.py` + `review/cli.py` (NEW) — argparse CLI

**Analog:** `gates/cli.py` (most-similar — argparse + subcommand-style with `--stage`, exit codes, JSON stdout). Secondary: `pipeline_cold/run.py:229–262` (argparse main shape with multi-flag combos).

**Module docstring + imports pattern** (analog `gates/cli.py:1–37`):
```python
"""Ad-hoc review CLI — `python -m review approve|validate`.

Use cases:
  - python -m review approve --artifact hierarchy --version v2026-06-01
    Opens $EDITOR on a pre-populated YAML, validates on save, writes REVIEW#.
  - python -m review validate <path-to-yaml>
    Validates a draft YAML without writing.

Exit codes:
  0 approval committed (approve) OR validation passed (validate)
  2 argparse error / required flag missing
  3 validation failed (any rule rejected the YAML)
  4 reviewer_cwid unresolvable (no config, no env)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from review.validator import validate as run_validator
# ... template, store imports ...
```

**argparse subcommand dispatch** (the closest existing pattern is `gates/cli.py:50–96` which uses flags rather than `add_subparsers`; for Phase 11 use `add_subparsers` since the two commands diverge more):
```python
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="review")
    sub = parser.add_subparsers(dest="command", required=True)

    p_approve = sub.add_parser("approve")
    p_approve.add_argument("--artifact", required=True, choices=["hierarchy"])
    p_approve.add_argument("--version", required=True)

    p_validate = sub.add_parser("validate")
    p_validate.add_argument("path", help="Path to YAML file to validate.")

    args = parser.parse_args(argv)
    if args.command == "approve":
        return _run_approve(args)
    return _run_validate(args)


if __name__ == "__main__":
    sys.exit(main())
```

**`$EDITOR` invocation pattern** (greenfield — no analog in repo; flag for planner). Recommended seam (per RESEARCH §"`$EDITOR` invocation"):
```python
import os, subprocess
editor = os.environ.get("EDITOR", "vim")
subprocess.run([editor, str(tmpfile_path)], check=True)
```

---

### `review/validator.py` (NEW) — pure validation

**Analog:** `utils/stage_records.py::build_complete_record` (lines 169–212) for the pure-builder pattern; tests target the pure function with stubs.

**Pure-function shape to copy** (analog lines 169–212):
```python
# Pattern: pure function returns a dict (or dataclass); no I/O.
# Test seam: caller injects all dependencies.

from dataclasses import dataclass

@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    errors: list[str]

@dataclass(frozen=True)
class RunSignals:
    """Injected at construction time; tests pass synthetic dicts.
    Production wires this from `review/store.py` DDB queries + S3 HEAD.
    """
    failed_stages: list[str]
    low_confidence_count: int
    uncovered_pmid_count: int
    artifact_uri_exists: bool

def validate(yaml_dict: dict, signals: RunSignals) -> ValidationResult:
    errors: list[str] = []

    # Rule 1: reviewer_cwid regex (spec §4)
    import re
    cwid = yaml_dict.get("reviewer_cwid", "")
    if not re.match(r"^cwid_[a-z]+\d+$", cwid):
        errors.append("reviewer_cwid must match ^cwid_[a-z]+\\d+$")

    # Rule 2: rationale length
    if len((yaml_dict.get("rationale") or "").strip()) < 40:
        errors.append("rationale must be ≥ 40 chars after whitespace strip")

    # Rule 3: decision enum
    if yaml_dict.get("decision") not in ("approve", "reject"):
        errors.append("decision must be 'approve' or 'reject'")

    # Rule 4: S3 HEAD (caller pre-resolves into signals.artifact_uri_exists)
    if not signals.artifact_uri_exists:
        errors.append(f"proposed_artifact_uri does not resolve (S3 HEAD 404)")

    # Rule 5 (D-08 + spec §4): refuse approval when gates failed
    if yaml_dict.get("decision") == "approve" and signals.failed_stages:
        errors.append(
            f"cannot approve — failed stages: {signals.failed_stages}"
        )

    return ValidationResult(ok=not errors, errors=errors)
```

**Concrete reuse from analog:**
- `@dataclass(frozen=True)` (analog `spotlight/rotation_selector.py:59–69` for `Selection` — exact same idiom; reuse the freezing rationale: "downstream stages cannot mutate selection state by accident").
- Pure-builder pattern (analog `utils/stage_records.py` line 184 comment: "Pure builder for a STAGE# complete row dict. No I/O.") — the design rationale is identical.
- Test stubs use `MagicMock` for non-pure boundaries (analog `tests/test_stage_records.py:57–61`).

---

### `review/store.py` (NEW) — DDB read/write wrapper

**Analog:** `utils/stage_records.py::write_complete` (lines 290–299) for PutItem; `utils/dynamodb_subtopic_migration.py` (entire file) for the `get_table(TABLE_NAME)` + UpdateExpression idiom; `spotlight/rotation_selector.py:135–186` for read-via-injectable-client.

**Writer pattern** (analog `utils/stage_records.py:290–299`):
```python
def write_complete(table: Any, **kwargs: Any) -> dict[str, Any]:
    item = build_complete_record(**kwargs)
    table.put_item(Item=item)
    return item
```

**Phase 11 writer:**
```python
TABLE_NAME = "reciterai-chatbot"

def write_review(table, *, artifact_type: str, version: str,
                 review_dict: dict) -> dict:
    item = {
        "PK": f"REVIEW#{artifact_type}#{version}",
        "SK": "GLOBAL",
        **review_dict,
    }
    table.put_item(Item=item)
    return item
```

**Reader for `summary_stats` / `failed_stages` signals** (analog `utils/stage_records.py::find_existing_complete` lines 93–122):
```python
# Query by PK; filter in Python (Phase 9 convention until GSI added).
resp = table.query(
    KeyConditionExpression="#pk = :pk",
    ExpressionAttributeNames={"#pk": "PK"},
    ExpressionAttributeValues={":pk": f"STAGE#assign_subtopics#topic:{topic_id}"},
    ScanIndexForward=False,
)
# Then filter `Items` in Python on run_id, status.
```

---

### `pipeline_hierarchy/publish.py` — diff producer + write-order + `STAGE#g29_cutover`

**Analog:** self.

**Current `upload_to_s3`** (lines 136–143):
```python
def upload_to_s3(version: str, hierarchy: bytes, schema: bytes, manifest: dict) -> None:
    s3 = S3HierarchyClient()
    # Version-pinned objects FIRST (D-02).
    s3.put_object(f"{version}/hierarchy.json", hierarchy)
    s3.put_object(f"{version}/hierarchy.schema.json", schema)
    # latest/manifest.json LAST.
    manifest_bytes = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
    s3.put_object("latest/manifest.json", manifest_bytes)
```

**Phase 11 delta — D-11 5-step order + diff.json + Cache-Control**:
```python
def upload_to_s3(
    version: str,
    hierarchy: bytes,
    schema: bytes,
    manifest: dict,
    diff_bytes: bytes,           # NEW (Phase 11)
) -> None:
    s3 = S3HierarchyClient()
    manifest_bytes = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")

    # 1. {version}/hierarchy.json
    s3.put_object(f"{version}/hierarchy.json", hierarchy)
    # 2. {version}/hierarchy.schema.json
    s3.put_object(f"{version}/hierarchy.schema.json", schema)
    # 3. {version}/diff.json — Phase 11; BEFORE manifest so consumer
    #    polling manifest.sha256 can immediately GET diff.json.
    s3.put_object(f"{version}/diff.json", diff_bytes)
    # 4. {version}/manifest.json — Phase 11 adds this version-pinned copy
    #    (was previously missing).
    s3.put_object(f"{version}/manifest.json", manifest_bytes)
    # 5. latest/manifest.json LAST, with Cache-Control on the latest/* key.
    s3.put_object(
        "latest/manifest.json", manifest_bytes,
        cache_control="max-age=60, must-revalidate",
    )
```

**New `compute_diff()` function** — call site is `main()` between gate evaluation (line 245) and `upload_to_s3` (line 314). Pattern to copy from `compute_publish_input_hash` (lines 86–121):
- Docstring with locked-decision references (D-09, D-12, D-13).
- Pure function returning `dict`; caller serializes to bytes.
- Hybrid data sources noted explicitly in docstring (RESEARCH §"Hybrid data sources" table).

**`STAGE#g29_cutover#GLOBAL` write** (D-16) — mirror the `write_complete` call at lines 351–364:
```python
# Detect cutover via argv flag (per RESEARCH OQ recommendation option a):
parser.add_argument("--g29-cutover", action="store_true",
                    help="One-time: write STAGE#g29_cutover#GLOBAL audit row.")
# ...
if args.g29_cutover and not args.dry_run:
    cutover_item = build_complete_record(
        stage="g29_cutover",
        scope="GLOBAL",
        input_hash=compute_input_hash("g29_cutover",
                                       {"new_publish_sha": manifest["sha256"]}),
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=duration_ms,
        cost_observed_usd=PUBLISH_COST_USD,
    )
    cutover_item["previous_publish_sha"] = <fetched from latest/manifest.json>
    cutover_item["new_publish_sha"] = manifest["sha256"]
    cutover_item["hierarchy_version_at_cutover"] = version
    cutover_item["run_id"] = os.environ.get("RECITERAI_COLD_RUN_ID")
    table.put_item(Item=cutover_item)
```

The "extra-attributes-after-builder" pattern is already established in `pipeline_cold/run.py:336–339`.

---

### `pipeline_hierarchy/bundler.py:183–186` + `pipeline_hierarchy/generator.py:60–67` — G-29

**Analog:** self.

**Current bundler stamping site** (lines 183–195):
```python
if generated_at is None:
    generated_at = (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )

return {
    "version": "subtopic_v1",
    "generated_at": generated_at,
    "taxonomy_version": taxonomy_version,
    "excluded_topics": excluded,
    "topics": topics,
    "see_also": [],
}
```

**Phase 11 delta (D-14):** drop `"generated_at": generated_at,` from the returned dict. RESEARCH recommends **also dropping the `generated_at` parameter** since the only in-repo caller is `publish.py` and it doesn't pass it; clean break is in-scope for Phase 11.

**Current generator stamping site** (lines 64–67):
```python
if generated_at is None:
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
hierarchy["generated_at"] = generated_at
hierarchy.setdefault("see_also", [])
```

**Phase 11 delta (D-14):** drop the `hierarchy["generated_at"] = generated_at` line. Keep the `generated_at` local variable (still used at line 112 to derive `version` label) and keep `manifest["generated_at"] = resolved_generated_at` (line 118) per D-14 (manifest still carries it).

---

### `utils/s3_client.py:100–117` — `cache_control` kwarg

**Analog:** self.

**Current signature** (lines 100–118):
```python
def put_object(self, key: str, body: bytes, content_type: str = "application/json") -> None:
    self._get_client().put_object(
        Bucket=self.bucket,
        Key=key,
        Body=body,
        ContentType=content_type,
    )
    logger.info(f"Uploaded s3://{self.bucket}/{key} ({len(body):,} bytes)")
```

**Phase 11 delta:**
```python
def put_object(
    self,
    key: str,
    body: bytes,
    content_type: str = "application/json",
    cache_control: str | None = None,        # NEW
) -> None:
    kwargs = {
        "Bucket": self.bucket,
        "Key": key,
        "Body": body,
        "ContentType": content_type,
    }
    if cache_control is not None:
        kwargs["CacheControl"] = cache_control
    self._get_client().put_object(**kwargs)
    logger.info(f"Uploaded s3://{self.bucket}/{key} ({len(body):,} bytes)")
```

Backwards-compatible (optional kwarg, default `None`). Preserves the operational-metadata-only logging convention (T-05-02-01, lines 70–73).

---

### `utils/stage_records.py` — optional `run_id` kwarg on three builders

**Analog:** self.

**Existing optional-kwarg pattern** (lines 200–212):
```python
item = _base_item(...)
if output_pointer is not None:
    item["output_pointer"] = output_pointer
if records_written is not None:
    item["records_written"] = records_written
if model_ids_snapshot is not None:
    item["model_ids_snapshot"] = list(model_ids_snapshot)
if force_reason is not None:
    item["force_reason"] = force_reason
return item
```

**Phase 11 delta (D-13):** add a sixth optional kwarg `run_id: str | None = None`. Copy the exact pattern:
```python
if run_id is not None:
    item["run_id"] = run_id
```

Apply to all three builders (`build_complete_record`, `build_skipped_record`, `build_failed_record`). Backwards-compatible: hot-path callers (SFN integration) and existing cold-path callers that don't thread `run_id` see no behavior change.

---

## Shared Patterns

### Lazy boto3 client (cross-cutting for all new DDB/S3 callers)

**Source:** `spotlight/history_writer.py:53–65` (DynamoDB) + `utils/s3_client.py:90–98` (S3).

**Apply to:** any new module that talks to AWS (`review/store.py`, the two new migration scripts). NEVER instantiate boto3 clients at module import.

```python
_default_client = None

def _get_default_client():
    """Get or create the module-level default DynamoDB client.
    No AWS calls happen at import time. Tests inject their own client
    and never reach this path.
    """
    global _default_client
    if _default_client is None:
        _default_client = boto3.client("dynamodb", region_name=REGION)
    return _default_client
```

---

### ISO 8601 UTC with Z suffix (cross-cutting timestamp format)

**Source:** `utils/stage_records.py:53–54`, `spotlight/history_writer.py:73–86`, `pipeline_cold/run.py:63–64`, `pipeline_hierarchy/publish.py:73–75`. All four use the identical idiom.

**Apply to:** any new timestamp generation in Phase 11 (REVIEW# `reviewed_at`, `STAGE#hierarchy_version_cutover#GLOBAL` timestamps, `STAGE#g29_cutover` timestamps, diff.json metadata if any).

```python
from datetime import datetime, timezone

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
```

Second-precision, no microseconds. Tests assert via regex `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$` (per `history_writer.py:79`).

---

### Decimal coercion for DDB numerics

**Source:** `utils/dynamodb_subtopic_migration.py:68` + `utils/dynamodb_helpers.py:259–264` (`to_decimal`).

**Apply to:** any new DDB write in Phase 11 carrying numeric (float) values. Phase 11 fields are mostly strings (`hierarchy_version`, `reviewer_cwid`, status enums) — Decimal is NOT needed for those — but `summary_stats` counts in REVIEW# rows and `duration_ms` / cost fields in audit rows are integers (write as `N` natively; integers don't trip Pitfall 1).

**Rule of thumb:** any float — `to_decimal(value)`. Any int — pass as-is. Any string — pass as-is.

---

### One-shot migration script shape

**Source:** `scripts/migrate_cost_field.py` (the exact analog for both Phase 11 migrations).

**Apply to:** `scripts/migrate_activity_hierarchy_version.py` and `scripts/migrate_spotlight_history_pk.py`.

Contract pieces to reuse verbatim:
- `argparse` with `--dry-run` flag (lines 36–42).
- Module docstring with run-order + delete-after-phase-lands note (lines 1–25).
- `from utils.dynamodb_helpers import get_table` (line 32) + `table = get_table()` (line 44) idiom.
- `scan_kwargs` dict + `while True: ... LastEvaluatedKey` pagination (lines 45–92).
- `print(f"... {n_total}")` count summary at end (lines 94–100).
- `return sys.exit(main())` entry pattern (lines 104–105).

For `migrate_spotlight_history_pk.py` ALSO copy from `backfill_spotlight.py:687–693` (confirm-and-commit gate) and `687–693` (batch-delete `_flush_delete_batch`).

---

### Test patterns: MagicMock + Stubber

**Source:**
- `tests/test_stage_records.py:57–61` — `MagicMock` for DDB Table query.
- `tests/test_pipeline_hot_orchestrator.py` (per RESEARCH) — `botocore.stub.Stubber` for boto3 clients.
- `test_spotlight_rotation_selector.py:240–290` — custom stub class (`StubBatchGetClient`, `StubUpdateClient`) for richer per-call assertions.

**Apply to:** all new Phase 11 tests. Three picks by complexity:

| Need | Pick |
|------|------|
| Single query, asserting return value | `MagicMock` with `.query.return_value = {"Items": [...]}` |
| Boto3 client with specific API shape | `botocore.stub.Stubber` |
| Need to assert call ordering across multiple PutObject/UpdateItem calls (e.g., D-11 S3 write order) | Custom stub class capturing `client.calls` list (analog `test_spotlight_rotation_selector.py:309–319`) |

---

## No Analog Found

| File | Role | Reason |
|------|------|--------|
| `review/template.py` | YAML template generator | Codebase has no YAML producers today (PyYAML is a new dep per D-08). Planner: model on `python -c "import yaml; yaml.safe_dump(...)"` with the `summary_stats` dict pre-populated from `compute_diff()` output. No in-repo pattern to copy. |
| `~/.reciterai/config.yaml` loader | operator config reader | Greenfield per D-08 + RESEARCH A9 — no current code reads this path. Planner picks the file location, the YAML schema, and the fallback chain (file → env `RECITERAI_REVIEWER_CWID` → actionable error per RESEARCH R5). |
| `$EDITOR` invocation in `review/cli.py` | subprocess interaction | No in-repo precedent (no other CLI shells out to an editor). Pattern is standard (`subprocess.run([os.environ.get("EDITOR", "vim"), tmpfile_path])`). Test seam: inject the editor command so tests can use `true` or a fixture-writer. |

---

## Metadata

**Analog search scope:**
- `utils/` (stage_records, dynamodb_helpers, dynamodb_subtopic_migration, s3_client)
- `spotlight/` (history_writer, rotation_selector, publish)
- `pipeline_hierarchy/` (publish, bundler, generator)
- `pipeline_cold/` (run)
- `scripts/` (migrate_cost_field)
- `gates/` (cli)
- `tests/` and root `test_*.py` for test convention

**Files scanned (read):** 11 source files + 2 test files.

**Pattern extraction date:** 2026-05-12.

## PATTERN MAPPING COMPLETE

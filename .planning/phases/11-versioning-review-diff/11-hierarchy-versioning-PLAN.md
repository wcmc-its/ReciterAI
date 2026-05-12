---
phase: 11
phase_name: Versioning, Review State, Diff Signaling
plan: hierarchy-versioning
type: execute
wave: 1
depends_on: []
files_modified:
  - utils/dynamodb_subtopic_migration.py
  - assign_subtopics.py
  - spotlight/history_writer.py
  - spotlight/rotation_selector.py
  - spotlight/publish.py
  - pipeline_cold/run.py
  - scripts/migrate_activity_hierarchy_version.py
  - scripts/migrate_spotlight_history_pk.py
  - utils/test_dynamodb_subtopic_migration.py
  - test_spotlight_rotation_selector.py
  - tests/test_assign_subtopics_stage.py
  - tests/test_pipeline_cold_run.py
  - tests/test_dynamodb_hierarchy_backfill.py
  - tests/test_spotlight_history_migration.py
autonomous: true
requirements: []
requirements_addressed:
  - D-01
  - D-02
  - D-03
  - D-04
  - D-05
  - D-06
  - D-17
must_haves:
  truths:
    - "D-01: Every UpdateItem that writes subtopic fields ALSO stamps hierarchy_version in the same SET expression"
    - "D-02: Migration script reports orphan count (rows without PROCESSING# join) as a number, no pre-committed threshold"
    - "D-03: hierarchy_version is a semver-shaped string; sentinels (v0.0.0-pre-phase-11, 0.0.0-orphan) parse as valid pre-release semver"
    - "D-04: Rotation history rows use PK SPOTLIGHT_HISTORY#{hierarchy_version}#{subtopic_id}; one-shot in-place rewrite (PutItem + DeleteItem)"
    - "D-05: Rotation migration reports never_spotlighted_count and malformed_publish_id_count separately; both get 0.0.0-orphan stamp"
    - "D-06: Cold-run mints write STAGE#hierarchy_version_cutover#GLOBAL with prev_version, new_version, migrated_rotation_count, orphan_count, started_at, completed_at, initiated_by"
    - "D-17: Pre-Phase-11 activity-row backfill uses single sentinel v0.0.0-pre-phase-11 (PROCESSING# join skipped; D-02 mechanism would yield 100% orphans)"
    - "Rotation selector parser splits on # and takes LAST segment as subtopic_id (not strip-prefix)"
    - "STAGE#hierarchy_version_cutover write location resolved (O-03): single write at END of pipeline_cold.run.main() after all stages succeed"
  artifacts:
    - path: "scripts/migrate_activity_hierarchy_version.py"
      provides: "One-shot backfill: stamps hierarchy_version=v0.0.0-pre-phase-11 on activity rows carrying subtopic fields"
      contains: "argparse --dry-run"
    - path: "scripts/migrate_spotlight_history_pk.py"
      provides: "One-shot rotation history PK rewrite (PutItem + DeleteItem); confirm-and-commit gate"
      contains: "argparse --commit"
    - path: "utils/dynamodb_subtopic_migration.py"
      provides: "update_activity_subtopics signature includes hierarchy_version positional arg"
      contains: "hierarchy_version"
    - path: "spotlight/history_writer.py"
      provides: "update_history takes hierarchy_version; writes new PK shape"
      contains: 'f"SPOTLIGHT_HISTORY#{hierarchy_version}#'
    - path: "spotlight/rotation_selector.py"
      provides: "fetch_history builds new PK keys; _ingest_responses splits on # for subtopic_id"
      contains: 'rsplit("#", 1)'
    - path: "pipeline_cold/run.py"
      provides: "Mints hierarchy_version and run_id at top of main(); writes STAGE#hierarchy_version_cutover#GLOBAL at end"
      contains: "STAGE#hierarchy_version_cutover"
  key_links:
    - from: "pipeline_cold/run.py::main"
      to: "assign_subtopics stage via env var RECITERAI_HIERARCHY_VERSION"
      via: "subprocess env threading"
      pattern: "RECITERAI_HIERARCHY_VERSION"
    - from: "assign_subtopics.py:642-648"
      to: "utils/dynamodb_subtopic_migration.py::update_activity_subtopics"
      via: "keyword arg hierarchy_version"
      pattern: "hierarchy_version="
    - from: "spotlight/publish.py:218"
      to: "spotlight/history_writer.py::update_history"
      via: "keyword arg hierarchy_version=version"
      pattern: "hierarchy_version=version"
---

## Scope note

Scope note: 14 files modified across 2 tasks. Above the 10-file soft threshold but tasks decompose cleanly; reviewer should expect a heavier single-context execution per task.

<objective>
Phase 11 Surface 1: stamp `hierarchy_version` on every activity record that carries
subtopic fields, change rotation history PK shape to be hierarchy-version-keyed, and
write a `STAGE#hierarchy_version_cutover#GLOBAL` audit row from the cold-path
orchestrator on every cutover run.

Purpose: prevent silent dangling references between activity records and recomputed
hierarchies (D-01..D-03), reset rotation history per-version because subtopic IDs are
not stable across recomputes (D-04, D-05), and close the loop on "what triggered this
cutover" via the Phase 10 `initiated_by` flag (D-06).

Output: extended writer/reader signatures, two one-shot migration scripts, a new
`STAGE#hierarchy_version_cutover#GLOBAL` row shape, and the cold-path plumbing to mint
both `run_id` and `hierarchy_version` at run start.

**Open items resolved in this plan:**
- O-03 → write `STAGE#hierarchy_version_cutover#GLOBAL` once at END of
  `pipeline_cold.run.main()` after all stages succeed (atomic; loses partial-failure
  audit, but Phase 10 per-stage STAGE# rows already provide that trail).
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/phases/11-versioning-review-diff/11-CONTEXT.md
@.planning/phases/11-versioning-review-diff/11-RESEARCH.md
@.planning/phases/11-versioning-review-diff/11-PATTERNS.md
@.planning/phases/10-hot-cold-path-split/10-SUMMARY.md
@docs/RECITERAI-SPEC.md

<interfaces>
<!-- Key existing signatures and patterns the executor needs. Extracted from codebase. -->

From utils/dynamodb_subtopic_migration.py:37-70 (CURRENT — Phase 11 extends):
```python
def update_activity_subtopics(
    pk: str,
    sk: str,
    subtopic_ids: list[str],
    primary_subtopic_id: str,
    confidences: Mapping[str, float],
) -> dict
```
UpdateExpression: "SET subtopic_ids = :sids, primary_subtopic_id = :pid, subtopic_confidences = :confs"
Idempotency: documented in module docstring lines 12-13.
Decimal coercion: `to_decimal(v)` for confidence floats. `hierarchy_version` is a string — no coercion needed.

From spotlight/history_writer.py:114-127 (CURRENT — Phase 11 extends):
```python
# Current PK shape:
Key={"PK": {"S": f"SPOTLIGHT_HISTORY#{s.entry.subtopic_id}"}, "SK": {"S": "STATE"}}
# UpdateExpression is a literal constant `_UPDATE_EXPRESSION` (security pattern T-06-03-01).
```

From spotlight/rotation_selector.py (CURRENT — Phase 11 extends):
- Line 50: `_HISTORY_PK_PREFIX = "SPOTLIGHT_HISTORY#"`
- Lines 158-172: BatchGetItem keys built as `{"PK": {"S": f"{_HISTORY_PK_PREFIX}{sid}"}, "SK": {"S": "STATE"}}`
- Lines 189-201: `_ingest_responses` parser uses `sid = pk[len(_HISTORY_PK_PREFIX):]` — THIS PARSER WILL BREAK under D-04; must change to `pk.rsplit("#", 1)[-1]`.

From pipeline_cold/run.py:229-342 (CURRENT — Phase 11 extends):
- argparse main with `--initiated-by` flag at ~line 248
- `started_at = _now_iso()` pattern at line 278
- `STAGE#cold_run` PutItem block at lines 326-340 — pattern for the new audit row
- "extra-attribute-after-builder" convention at lines 336-339 for stage-specific metadata

From scripts/migrate_cost_field.py (analog for new migration scripts):
- argparse + `--dry-run` flag (lines 36-42)
- `from utils.dynamodb_helpers import get_table` + `table = get_table()` (line 32, 44)
- `while True: ... LastEvaluatedKey` paginate idiom (lines 45-92)
- `print(f"... {n_total}")` count summary at end (lines 94-100)

From backfill_spotlight.py:687-693 (analog for confirm-and-commit gate):
```python
confirm = input("WARNING: ... Type 'yes' to confirm: ")
if confirm.strip().lower() != "yes":
    return 1
```

From utils/stage_records.py:200-212 (pattern for optional-kwarg-then-conditional-attach
in builder; used by the cutover row PutItem in pipeline_cold/run.py):
```python
if output_pointer is not None:
    item["output_pointer"] = output_pointer
# ... extends with new attrs after build_complete_record()
```

From utils/dynamodb_helpers.py:267-320:
- `mark_processing` writes PROCESSING# rows
- `get_processing_status` reads `PK=PROCESSING#pmid_{pmid}, SK=STATUS`
- (Per D-17: NOT USED in this plan — pre-Phase-11 PROCESSING# rows don't carry
  hierarchy_version; the join would yield 100% orphans; single sentinel used instead.)
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Extend writers + readers to carry hierarchy_version (write-site enforcement)</name>
  <files>
    utils/dynamodb_subtopic_migration.py
    assign_subtopics.py
    spotlight/history_writer.py
    spotlight/rotation_selector.py
    spotlight/publish.py
    pipeline_cold/run.py
    utils/test_dynamodb_subtopic_migration.py
    test_spotlight_rotation_selector.py
    tests/test_assign_subtopics_stage.py
    tests/test_pipeline_cold_run.py
  </files>
  <read_first>
    utils/dynamodb_subtopic_migration.py (the file being modified; lines 1-160)
    assign_subtopics.py (lines 600-680 — caller site + run() signature)
    spotlight/history_writer.py (the file being modified; lines 1-160)
    spotlight/rotation_selector.py (lines 1-220 — both fetch_history and _ingest_responses parser)
    spotlight/publish.py (lines 140-230 — update_history caller site)
    pipeline_cold/run.py (lines 229-347 — argparse main + STAGE# write conventions)
    utils/test_dynamodb_subtopic_migration.py (full file — test patterns to extend)
    test_spotlight_rotation_selector.py (lines 240-330 — stub client pattern)
    utils/stage_records.py:184-212 (optional-kwarg conditional-attach pattern)
  </read_first>
  <behavior>
    - Test 1 (utils/test_dynamodb_subtopic_migration.py): `update_activity_subtopics(pk, sk, subtopic_ids, primary_subtopic_id, confidences, hierarchy_version="v2026-06-01")` issues an UpdateItem whose UpdateExpression contains `hierarchy_version = :hv` AND ExpressionAttributeValues contains `":hv": "v2026-06-01"`. Existing tests must keep passing (signature is positional-then-required; add hierarchy_version as required kwarg).
    - Test 2 (test_spotlight_rotation_selector.py): `fetch_history(client, ["sub_a", "sub_b"], hierarchy_version="v2026-06-01")` builds BatchGetItem keys with PK `"SPOTLIGHT_HISTORY#v2026-06-01#sub_a"` and `"SPOTLIGHT_HISTORY#v2026-06-01#sub_b"`.
    - Test 3 (test_spotlight_rotation_selector.py): `_ingest_responses` correctly extracts `sub_a` as the subtopic_id from `PK = "SPOTLIGHT_HISTORY#v2026-06-01#sub_a"` (uses LAST segment after split on `#`).
    - Test 4 (test_spotlight_rotation_selector.py): `update_history(client, selections, publish_id="v2026-06-01", hierarchy_version="v2026-06-01")` issues an UpdateItem with `Key.PK = "SPOTLIGHT_HISTORY#v2026-06-01#{sid}"`.
    - Test 5 (tests/test_assign_subtopics_stage.py): `assign_subtopics.run(..., hierarchy_version="v2026-06-01")` propagates hierarchy_version through to `update_activity_subtopics` (asserted via mock).
    - Test 6 (tests/test_pipeline_cold_run.py): `pipeline_cold.run.main()` reads `--hierarchy-version` argv OR mints `f"v{started_at[:10]}"` if absent; sets env `RECITERAI_HIERARCHY_VERSION` AND `RECITERAI_COLD_RUN_ID` for subprocess stages.
    - Test 7 (tests/test_pipeline_cold_run.py): on successful run, exactly one `STAGE#hierarchy_version_cutover#GLOBAL` row is PutItem'd at end of main with required fields {prev_version, new_version, migrated_rotation_count, orphan_count, started_at, completed_at, initiated_by}. (migrated_rotation_count/orphan_count default to 0 when the migration script hasn't been run as part of this cold run — those fields exist for the cutover-run case; this is documented in the docstring.)
  </behavior>
  <action>
    1. `utils/dynamodb_subtopic_migration.py::update_activity_subtopics`:
       - Add `hierarchy_version: str` as a required keyword arg (positional-after-confidences).
       - Update UpdateExpression to: `"SET subtopic_ids = :sids, primary_subtopic_id = :pid, subtopic_confidences = :confs, hierarchy_version = :hv"`
       - Add `":hv": hierarchy_version` to ExpressionAttributeValues.
       - Preserve `__all__` (no new public name) and idempotency note in docstring.

    2. `assign_subtopics.py:642-648`:
       - Add `hierarchy_version=hierarchy_version` keyword to the `update_activity_subtopics(...)` call.
       - Add `hierarchy_version: str` to `assign_subtopics.run()` signature (or to the per-topic worker call chain — whatever the existing entry point is).
       - Resolve `hierarchy_version` at run() entry from `os.environ.get("RECITERAI_HIERARCHY_VERSION")`; raise `RuntimeError` with actionable message if absent (cold-run plumbing per Task 1.6 sets it).

    3. `spotlight/history_writer.py::update_history`:
       - Add `hierarchy_version: str` required keyword arg.
       - Change PK construction to `f"SPOTLIGHT_HISTORY#{hierarchy_version}#{s.entry.subtopic_id}"`.
       - Preserve `_UPDATE_EXPRESSION` as a literal constant (T-06-03-01 security pattern); `hierarchy_version` flows via the PK string (operator/code-controlled, not user input). Document this in the docstring.

    4. `spotlight/rotation_selector.py`:
       - `fetch_history(client, subtopic_ids, *, hierarchy_version: str)` — required kwarg. Update BatchGetItem keys to: `{"PK": {"S": f"{_HISTORY_PK_PREFIX}{hierarchy_version}#{sid}"}, "SK": {"S": "STATE"}}`.
       - `_ingest_responses`: change `sid = pk[len(_HISTORY_PK_PREFIX):]` to `sid = pk.rsplit("#", 1)[-1]`. Preserve the `startswith` guard.
       - `select_with_diversity` (or whatever calls `fetch_history`): plumb `hierarchy_version` from caller.

    5. `spotlight/publish.py:218`:
       - Caller of `update_history` and `select_with_diversity`: pass `hierarchy_version=version` (per OQ-2 resolution: in spotlight publish, `version == hierarchy_version` because spotlight reads `latest/manifest.json` to find the active hierarchy at publish time; this plan adopts that convention by reading the active `version` from the same source the publisher already uses).

    6. `pipeline_cold/run.py::main()`:
       - Import `uuid` and `os`.
       - Add argparse flag: `--hierarchy-version`, default `None`.
       - After argparse and `started_at = _now_iso()`:
           ```python
           run_id = str(uuid.uuid4())
           new_version = args.hierarchy_version or f"v{started_at[:10]}"
           prev_version = _read_prev_version_from_latest_manifest()  # see below
           env_for_stages = {
               **os.environ,
               "RECITERAI_COLD_RUN_ID": run_id,
               "RECITERAI_HIERARCHY_VERSION": new_version,
           }
           ```
       - Thread `env_for_stages` into `run_stage()` (subprocess.run env= kwarg). Inspect the existing `run_stage` and add env passthrough.
       - `_read_prev_version_from_latest_manifest()`: GET `s3://wcmc-reciterai-hierarchy/latest/manifest.json`, parse `version`. On 404 (first-ever cold run), return `None`. Reuse `utils/s3_client.S3HierarchyClient` for the GET.
       - At end of main(), AFTER all stages succeed (O-03 resolution: single write at end):
           ```python
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
           cutover_row["migrated_rotation_count"] = 0  # populated by migration script if invoked
           cutover_row["orphan_count"] = 0
           cutover_row["initiated_by"] = args.initiated_by
           cutover_row["run_id"] = run_id
           table.put_item(Item=cutover_row)
           ```
         (Note: migrated_rotation_count/orphan_count default to 0; these fields are populated by the migration scripts in Task 2 when run as a separate operator step. The cutover row is the audit-of-cutover, not the audit-of-migration. Documented in the docstring.)

    7. Update tests as listed in `<behavior>`. Test 7 uses MagicMock for the DDB table (mirrors `tests/test_stage_records.py` pattern). Test 3 must explicitly assert the parser works on the new PK shape AND continues to work if any future PK has additional `#`-segments (rsplit-on-1 is the spec).

    Note: Per D-01, `update_faculty_subtopic_scores()` and `clear_faculty_subtopic_scores_for_topic()` are OUT OF SCOPE — do not touch them. Confirm by grep-not-touched.
  </action>
  <verify>
    <automated>cd /Users/paulalbert/Dropbox/GitHub/ReciterAI &amp;&amp; pytest utils/test_dynamodb_subtopic_migration.py test_spotlight_rotation_selector.py tests/test_assign_subtopics_stage.py tests/test_pipeline_cold_run.py -x</automated>
    Manual greps to confirm:
    - `grep -n "hierarchy_version" utils/dynamodb_subtopic_migration.py` returns the new SET clause + param.
    - `grep -n 'SPOTLIGHT_HISTORY#{hierarchy_version}' spotlight/history_writer.py spotlight/rotation_selector.py` returns matches in both files.
    - `grep -n 'rsplit("#", 1)' spotlight/rotation_selector.py` returns the parser change.
    - `grep -c 'hierarchy_version' utils/dynamodb_subtopic_migration.py | grep -v '^#'` &gt;= 3 (signature + SET clause + AttrValue).
    - `grep -n 'update_faculty_subtopic_scores\|clear_faculty_subtopic_scores' utils/dynamodb_subtopic_migration.py | grep hierarchy_version` returns empty (out-of-scope per D-01).
    - `grep -n 'STAGE#hierarchy_version_cutover' pipeline_cold/run.py` returns the write site.
  </verify>
  <done>
    All seven tests pass; greps confirm the contract changes are in place; faculty-row writers are untouched.
  </done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: One-shot migration scripts (activity-row backfill + rotation history PK rewrite)</name>
  <files>
    scripts/migrate_activity_hierarchy_version.py
    scripts/migrate_spotlight_history_pk.py
    tests/test_dynamodb_hierarchy_backfill.py
    tests/test_spotlight_history_migration.py
  </files>
  <read_first>
    scripts/migrate_cost_field.py (the exact analog; full file)
    backfill_spotlight.py:678-733 (`_run_reset_history` + `_flush_delete_batch` for the scan + batch-delete pattern)
    utils/dynamodb_helpers.py:1-100 (get_table helper)
    spotlight/history_writer.py (PK shape this script rewrites to)
    tests/test_stage_records.py (MagicMock-based test pattern)
  </read_first>
  <behavior>
    - Test 1 (tests/test_dynamodb_hierarchy_backfill.py): scanning a table with 3 rows (2 with subtopic_ids set, 1 without) calls `update_item` with `hierarchy_version = v0.0.0-pre-phase-11` on exactly the 2 rows, and the report prints `Rows scanned: 2`, `Newly stamped: 2`, `Already stamped: 0`. `--dry-run` mode writes nothing.
    - Test 2 (tests/test_dynamodb_hierarchy_backfill.py): row already carrying `hierarchy_version` is filtered out by the scan FilterExpression (`attribute_not_exists(hierarchy_version)`) — confirmed via the FilterExpression assertion on the mock scan call.
    - Test 3 (tests/test_spotlight_history_migration.py): row with `last_shown_publish_id = "v2026-05-12"` → new PK = `SPOTLIGHT_HISTORY#v2026-05-12#{sid}`; PutItem then DeleteItem called in that order.
    - Test 4 (tests/test_spotlight_history_migration.py): row with no `last_shown_publish_id` → counted in `never_spotlighted_count`, new PK uses `0.0.0-orphan` stamp.
    - Test 5 (tests/test_spotlight_history_migration.py): row with `last_shown_publish_id = "garbage"` → counted in `malformed_publish_id_count`, new PK uses `0.0.0-orphan` stamp, AND a line is written to the diff log file with the row PK and the malformed value.
    - Test 6 (tests/test_spotlight_history_migration.py): default mode is dry-run; `--commit` flag is required to write; confirm-and-commit prompt requires "yes" exactly.
  </behavior>
  <action>
    A. `scripts/migrate_activity_hierarchy_version.py`:

    Header docstring (verbatim, per the PATTERNS.md analog):
    ```python
    """
    One-off migration for Phase 11 D-02 / D-17: stamp `hierarchy_version`
    on every activity record that already carries subtopic fields.

    Per D-17 (research follow-up to D-02): pre-Phase-11 PROCESSING# rows
    do NOT carry hierarchy_version, so a PROCESSING# join would yield
    100% orphans. This script uses a single sentinel value:
    `v0.0.0-pre-phase-11` — parses as valid pre-release semver per D-03,
    sorts before every real v{ISO-date} version.

    Idempotent: rows already carrying hierarchy_version are filtered out
    by the scan's FilterExpression. Reports counts at end.

    Run order:
        python -m scripts.migrate_activity_hierarchy_version --dry-run
        python -m scripts.migrate_activity_hierarchy_version

    This is a one-shot script. Delete after Phase 11 ships.
    """
    ```

    Implementation (full file):
    - argparse with `--dry-run` flag.
    - `from utils.dynamodb_helpers import get_table; table = get_table()`.
    - Scan with `FilterExpression="attribute_exists(primary_subtopic_id) AND attribute_not_exists(hierarchy_version)"`.
    - `while True: scan → for item: UpdateItem (SET hierarchy_version = :v) → break on missing LastEvaluatedKey`.
    - Counts: `n_total`, `n_stamped`, `n_already_stamped` (always 0 since scan filters them out — kept for symmetry with dry-run reporting).
    - Print summary; exit 0 on success.
    - Sentinel value: `"v0.0.0-pre-phase-11"` (string constant `LEGACY_SENTINEL`).

    B. `scripts/migrate_spotlight_history_pk.py`:

    Header docstring:
    ```python
    """
    One-off migration for Phase 11 D-04 / D-05: rewrite every
    SPOTLIGHT_HISTORY# row's PK from
        SPOTLIGHT_HISTORY#{subtopic_id}
    to
        SPOTLIGHT_HISTORY#{hierarchy_version}#{subtopic_id}

    Inference rule:
      - last_shown_publish_id matches ^v\d{4}-\d{2}-\d{2}$ → use that
      - last_shown_publish_id missing → "0.0.0-orphan" (never_spotlighted_count)
      - last_shown_publish_id present but unparseable → "0.0.0-orphan"
        (malformed_publish_id_count) AND log to diff file

    PK changes mean PutItem + DeleteItem (NOT UpdateItem).

    Default mode is dry-run. `--commit` required to write. Confirm-and-commit
    prompt at start of commit phase. Per-row before/after diff written to
    spotlight_history_migration_<timestamp>.log for operator review.

    This is a one-shot script. Delete after Phase 11 ships.
    """
    ```

    Implementation:
    - argparse with `--commit` flag (NO --dry-run; dry-run is default — `--commit` is the opt-in).
    - `_PUBLISH_ID_PATTERN = re.compile(r"^v\d{4}-\d{2}-\d{2}$")`.
    - Scan with `FilterExpression="begins_with(PK, :prefix)" AND ExpressionAttributeValues={":prefix": "SPOTLIGHT_HISTORY#"}`. Also filter out rows that already have a `#` after the prefix (already migrated): add Python-side check `if item["PK"].count("#") > 1: skip` — idempotent re-run safety.
    - For each row:
        ```python
        last_shown = item.get("last_shown_publish_id")
        if last_shown is None:
            new_version = "0.0.0-orphan"
            n_never_spotlighted += 1
        elif _PUBLISH_ID_PATTERN.match(last_shown):
            new_version = last_shown
            n_real += 1
        else:
            new_version = "0.0.0-orphan"
            n_malformed += 1
            diff_log.write(f"MALFORMED PK={item['PK']} last_shown={last_shown!r}\n")
        old_sid = item["PK"].split("#", 1)[1]
        new_pk = f"SPOTLIGHT_HISTORY#{new_version}#{old_sid}"
        diff_log.write(f"REWRITE {item['PK']} -> {new_pk}\n")
        if args.commit:
            new_item = {**item, "PK": new_pk}
            table.put_item(Item=new_item)
            table.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})
        ```
    - Confirm-and-commit gate (only if args.commit) — copy verbatim shape from
      `backfill_spotlight.py:687-693`.
    - Print final counts: `Rows scanned`, `Real (real hierarchy_version inferred)`,
      `Never spotlighted (orphan)`, `Malformed publish_id (orphan)`, `Total rewrites`.
    - Diff log path: `spotlight_history_migration_{timestamp}.log` in cwd.
    - Exit codes: 0 success, 1 if user declined confirm.

    C. Tests (`tests/test_dynamodb_hierarchy_backfill.py` and
       `tests/test_spotlight_history_migration.py`): use MagicMock for `get_table()`
       return. Mock `.scan` to return canned items + LastEvaluatedKey-then-None
       sequence. Assert `.update_item` / `.put_item` / `.delete_item` call args
       (specifically Key and ExpressionAttributeValues). For confirm-prompt test,
       monkeypatch `builtins.input`.

    D. Manual coordination note (per D-15): add a top-of-file comment in
       `scripts/migrate_spotlight_history_pk.py` documenting that the rotation
       migration should run during an operational lull (Phase 10 spotlight cadence is
       monthly — schedule for immediately after a fresh rotation publish).
  </action>
  <verify>
    <automated>cd /Users/paulalbert/Dropbox/GitHub/ReciterAI &amp;&amp; pytest tests/test_dynamodb_hierarchy_backfill.py tests/test_spotlight_history_migration.py -x</automated>
    Manual greps:
    - `grep -c 'v0.0.0-pre-phase-11' scripts/migrate_activity_hierarchy_version.py | grep -v '^#'` &gt;= 1.
    - `grep -c '0.0.0-orphan' scripts/migrate_spotlight_history_pk.py | grep -v '^#'` &gt;= 1.
    - `grep -n 'never_spotlighted\|malformed_publish_id' scripts/migrate_spotlight_history_pk.py` returns both bucket names.
    - `grep -n 'put_item.*delete_item\|put_item' scripts/migrate_spotlight_history_pk.py` returns the rewrite pair.
    - Dry-run-default check: `python -m scripts.migrate_spotlight_history_pk --help` shows `--commit` flag with default-off semantics (no `--dry-run` flag because dry-run is the default).
  </verify>
  <done>
    All six tests pass; both scripts run cleanly with `--dry-run` / no `--commit` against a stubbed table and report meaningful counts; greps confirm sentinel values and bucket names.
  </done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| operator CLI → DynamoDB | Migration scripts run by operator; bad operator input could corrupt rotation history |
| code-controlled strings → DDB PK | `hierarchy_version` strings flow into PK construction (no user input) |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-11-01-01 | Tampering | spotlight/history_writer.py PK string | mitigate | `_UPDATE_EXPRESSION` stays a literal constant; `hierarchy_version` flows via PK string only (operator/code-controlled). Document in docstring. Preserves T-06-03-01 expression-literal pattern. |
| T-11-01-02 | Tampering | scripts/migrate_spotlight_history_pk.py | mitigate | Dry-run is default; `--commit` opt-in; confirm-and-commit prompt requires "yes"; per-row diff log to disk before any write; idempotent re-run guard (skip rows already migrated via `PK.count("#") > 1` check). |
| T-11-01-03 | Repudiation | STAGE#hierarchy_version_cutover#GLOBAL | mitigate | Every cold-run mint writes a single audit row at end-of-main; row carries `initiated_by` from Phase 10 plumbing + `run_id` UUID; row is content-addressed via `input_hash`. |
| T-11-01-04 | Information Disclosure | migration diff log file | accept | Log file contains row PKs and publish_id strings — no PII, no credentials. Operator-readable on local disk. Cleanup is operator responsibility. |
| T-11-01-05 | DoS | scripts/migrate_activity_hierarchy_version.py | accept | Scan-and-update over the activity table; cost is one-time-bounded; runs during operational lull per D-15. UpdateItem is per-row, not batched (idempotent if interrupted; FilterExpression skips already-stamped rows on re-run). |
| T-11-01-06 | Elevation of Privilege | n/a — no new auth surfaces | accept | All callers use default credential chain; no new IAM grants. |
</threat_model>

<verification>
- All migrations are idempotent (re-running on a partially-migrated table is safe).
- Subtopic-field writes always carry `hierarchy_version` in the same UpdateItem.
- Rotation history reads/writes use the new PK shape; the parser correctly
  extracts subtopic_id via rsplit-on-1.
- Cold-run produces exactly one `STAGE#hierarchy_version_cutover#GLOBAL` row per run.
- Per D-01: faculty-row writers (`update_faculty_subtopic_scores`, etc.) are NOT touched.
</verification>

<success_criteria>
- D-01: every `update_activity_subtopics` call stamps `hierarchy_version`.
- D-02 + D-17: activity backfill uses single sentinel `v0.0.0-pre-phase-11`; counts reported; no PROCESSING# join.
- D-03: sentinel values are semver-shaped (`v0.0.0-pre-phase-11`, `0.0.0-orphan`).
- D-04: rotation history PK shape is `SPOTLIGHT_HISTORY#{hierarchy_version}#{subtopic_id}`; one-shot rewrite via PutItem+DeleteItem.
- D-05: rotation migration reports `never_spotlighted_count` and `malformed_publish_id_count` separately.
- D-06: cutover row PutItem writes the documented fields including `initiated_by` and `run_id`.
- O-03: cutover row written once, at END of main, after all stages succeed.
- All tests pass: `pytest utils/test_dynamodb_subtopic_migration.py test_spotlight_rotation_selector.py tests/test_assign_subtopics_stage.py tests/test_pipeline_cold_run.py tests/test_dynamodb_hierarchy_backfill.py tests/test_spotlight_history_migration.py -x`
</success_criteria>

<output>
Create `.planning/phases/11-versioning-review-diff/11-SUMMARY-hierarchy-versioning.md` documenting:
- Files changed + new files created
- Test count delta
- The two migration scripts (path, invocation, dry-run behavior)
- The new PK shape examples
- The new STAGE#hierarchy_version_cutover#GLOBAL row shape (example)
- Open items resolved in this plan: O-03 (end-of-main write location)
- Any deltas from the plan
</output>

---
phase: 11
phase_name: Versioning, Review State, Diff Signaling
plan: change-signaling
type: execute
wave: 1
depends_on: []
files_modified:
  - pipeline_hierarchy/bundler.py
  - pipeline_hierarchy/generator.py
  - pipeline_hierarchy/publish.py
  - pipeline_hierarchy/diff_stats.py
  - utils/s3_client.py
  - utils/stage_records.py
  - docs/hierarchy.schema.json
  - docs/hierarchy-contract.md
  - tests/test_hierarchy_bundler.py
  - tests/test_hierarchy_publisher.py
  - tests/test_publish_integration.py
  - tests/test_stage_records.py
  - tests/test_hierarchy_reproducibility.py
  - tests/test_publish_diff.py
  - utils/test_s3_client.py
autonomous: true
requirements: []
requirements_addressed:
  - D-09
  - D-10
  - D-11
  - D-12
  - D-13
  - D-14
  - D-15
  - D-16
  - D-18
must_haves:
  truths:
    - "D-09: diff.json is computed as a hybrid — STAGE# row (filtered by run_id) for reassigned PMID count + byte-comparison of prev vs new hierarchy.json for taxonomy/added/removed/renamed subtopics"
    - "D-10: diff.json is written from pipeline_hierarchy/publish.py (one new function, not a new stage)"
    - "D-11: S3 write order is exactly: (1) {version}/hierarchy.json, (2) {version}/hierarchy.schema.json, (3) {version}/diff.json, (4) {version}/manifest.json, (5) latest/manifest.json. Phase 11 ADDS objects 3 and 4 (currently only 3 objects upload; {version}/manifest.json was previously missing)."
    - "D-12: diff.json carries top-level diff_schema_version field"
    - "D-13: STAGE# rows gain optional run_id field (backwards-compatible); diff.json queries are filtered by current cold-run's run_id"
    - "D-14: G-29 — generated_at is removed from hierarchy.json (bundler + generator both stop stamping); manifest.json continues to carry generated_at; old version directories not rewritten"
    - "D-15: G-29 cutover paired with SPS operator coordination — runbook entry"
    - "D-16: First post-G29 publish writes STAGE#g29_cutover#GLOBAL with previous_publish_sha, new_publish_sha, hierarchy_version_at_cutover, run_id, started_at, completed_at"
    - "D-18: reassigned_pmid_count in diff.json means rows touched this run (= records_written), NOT primary_changed semantics"
    - "Cache-Control: max-age=60, must-revalidate is set on latest/manifest.json (hierarchy scope only; spotlight latest/* deferred per OQ-4)"
    - "O-01 resolved: first-ever-publish edge case emits diff.json with from_version: null (explicit signal, does not depend on consumer 404 handling)"
  artifacts:
    - path: "pipeline_hierarchy/publish.py"
      provides: "compute_diff() function; updated upload_to_s3() 5-step order; STAGE#g29_cutover write on --g29-cutover flag"
      contains: "compute_diff"
    - path: "pipeline_hierarchy/diff_stats.py"
      provides: "Shared diff-stats compute (used by diff.json producer; future REVIEW# template wiring)"
      contains: "compute_diff_stats"
    - path: "pipeline_hierarchy/bundler.py"
      provides: "No longer stamps generated_at into hierarchy dict"
      contains: "# generated_at removed per D-14"
    - path: "pipeline_hierarchy/generator.py"
      provides: "Does not write generated_at into hierarchy; still writes generated_at into manifest"
      contains: "manifest"
    - path: "utils/s3_client.py"
      provides: "put_object accepts optional cache_control kwarg"
      contains: "cache_control"
    - path: "utils/stage_records.py"
      provides: "build_complete_record / build_skipped_record / build_failed_record accept optional run_id kwarg"
      contains: "run_id"
    - path: "docs/hierarchy.schema.json"
      provides: "generated_at no longer required on hierarchy.json"
    - path: "docs/hierarchy-contract.md"
      provides: "Documents new 5-step S3 write order, diff.json shape, Cache-Control, first-ever-publish from_version: null behavior, G-29 cutover note"
  key_links:
    - from: "pipeline_hierarchy/publish.py::compute_diff"
      to: "STAGE# rows filtered by run_id"
      via: "DDB query with FilterExpression on run_id"
      pattern: "run_id"
    - from: "pipeline_hierarchy/publish.py::compute_diff"
      to: "prev hierarchy.json on S3"
      via: "S3 GET on s3://wcmc-reciterai-hierarchy/{prev_version}/hierarchy.json"
      pattern: "S3HierarchyClient"
    - from: "pipeline_hierarchy/publish.py::upload_to_s3"
      to: "utils/s3_client.py::put_object(cache_control=...)"
      via: "kwarg pass-through"
      pattern: "cache_control"
---

<objective>
Phase 11 Surface 3: ship the structured change-signaling contract. Three integrated
deltas to `pipeline_hierarchy`:

1. **diff.json producer** in `publish.py` (D-09, D-10, D-12, D-13, D-18). Hybrid:
   STAGE# query (filtered by current cold-run's `run_id`) for PMID-reassignment
   count + byte-comparison of prev vs new hierarchy.json for taxonomy/structural
   changes. Top-level `diff_schema_version` for forward-compat.

2. **S3 write-order contract + Cache-Control** (D-11). Five PutObjects in order;
   `latest/manifest.json` carries `Cache-Control: max-age=60, must-revalidate`.
   PATTERNS.md surfaced that current `upload_to_s3` writes only 3 objects — Phase 11
   ADDS `{version}/diff.json` AND `{version}/manifest.json` (the latter was never
   written before). This is "add 2 + reorder", not "reorder existing 5".

3. **G-29 fix** (D-14, D-15, D-16). Stop writing `generated_at` into the hierarchy
   dict from both `bundler.py:184-186` and `generator.py:60-67`. Manifest keeps its
   own `generated_at`. One-time `STAGE#g29_cutover#GLOBAL` audit row on the first
   post-G29 publish, gated by `--g29-cutover` argv flag.

Plus the substrate addition (D-13): optional `run_id` field on all three STAGE#
builders in `utils/stage_records.py`, backwards-compatible.

Purpose: make `hierarchy.json` bit-stable across content-identical reruns (G-29),
give SPS a structured signal of what changed (diff.json) so it can do incremental
ETL instead of wholesale, and lock the write order so consumers polling
`manifest.sha256` never race a missing diff.

Output: extended publish.py, deterministic hierarchy.json, new diff.json contract,
backwards-compatible STAGE# substrate, contract doc updates.

**Open items resolved in this plan:**
- O-01 → emit diff.json with `from_version: null` on first-ever-publish
  (explicit signal; doesn't depend on consumer 404 handling). Tested
  explicitly in `tests/test_publish_diff.py`.
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
@docs/hierarchy-contract.md

<interfaces>
<!-- Key existing signatures and patterns. Extracted from codebase. -->

From pipeline_hierarchy/publish.py:136-143 (CURRENT — Phase 11 extends):
```python
def upload_to_s3(version: str, hierarchy: bytes, schema: bytes, manifest: dict) -> None:
    s3 = S3HierarchyClient()
    # Version-pinned objects FIRST (D-02 of Phase 5).
    s3.put_object(f"{version}/hierarchy.json", hierarchy)
    s3.put_object(f"{version}/hierarchy.schema.json", schema)
    # latest/manifest.json LAST.
    manifest_bytes = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
    s3.put_object("latest/manifest.json", manifest_bytes)
```
NOTE: only 3 PutObjects today. NO `{version}/manifest.json`. NO `{version}/diff.json`.

From pipeline_hierarchy/publish.py:86-121 (compute_publish_input_hash; pattern for compute_diff):
- Pure function returning a dict
- Defensive strip of `generated_at` at line 108 ("G-29 local mitigation") — after D-14
  the field doesn't exist in the bundle so the strip becomes a no-op; remove for honesty.

From pipeline_hierarchy/publish.py:245-330 (publish_hierarchy main):
- run_gates(stage="publish", ...) at line 245
- generate(...) at line 284
- write_local(...) at line 127
- upload_to_s3(...) at line 314
- run_gates(stage="publish_post", ...) at line 330
- write_complete(...) at line 351 (STAGE# substrate write)

From pipeline_hierarchy/bundler.py:180-195 (CURRENT — Phase 11 modifies):
```python
if generated_at is None:
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

return {
    "version": "subtopic_v1",
    "generated_at": generated_at,   # ← DROP THIS LINE (D-14)
    "taxonomy_version": taxonomy_version,
    "excluded_topics": excluded,
    "topics": topics,
    "see_also": [],
}
```

From pipeline_hierarchy/generator.py:60-67 (CURRENT — Phase 11 modifies):
```python
if generated_at is None:
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
hierarchy["generated_at"] = generated_at   # ← DROP THIS LINE (D-14)
hierarchy.setdefault("see_also", [])
```

From pipeline_hierarchy/generator.py:111-121 (CURRENT — KEEP):
- Line 111 derives `version = f"v{generated_at[:10]}"` — keep `generated_at` local variable for this.
- Line 118 stamps `manifest["generated_at"] = resolved_generated_at` — KEEP per D-14.

From utils/s3_client.py:100-118 (CURRENT — Phase 11 adds cache_control kwarg):
```python
def put_object(self, key: str, body: bytes, content_type: str = "application/json") -> None:
    self._get_client().put_object(
        Bucket=self.bucket, Key=key, Body=body, ContentType=content_type,
    )
    logger.info(f"Uploaded s3://{self.bucket}/{key} ({len(body):,} bytes)")
```

From utils/stage_records.py:200-212 (existing optional-kwarg-then-conditional-attach pattern):
```python
if output_pointer is not None:
    item["output_pointer"] = output_pointer
if records_written is not None:
    item["records_written"] = records_written
# ... Phase 11 adds: if run_id is not None: item["run_id"] = run_id
```

The diff.json shape (D-09, D-12, D-18):
```json
{
  "diff_schema_version": "1.0.0",
  "from_version": "v2026-05-06",   // OR null for first-ever-publish (O-01)
  "to_version": "v2026-06-01",
  "taxonomy_version_changed": false,
  "added_subtopics": [],
  "removed_subtopics": [],
  "renamed_subtopics": [
    {"id": "...", "old_display_name": "...", "new_display_name": "..."}
  ],
  "reassigned_pmid_count": 312,     // = records_written from assign STAGE# rows (D-18)
  "editorial_only": false           // derived: true iff only renames + no adds/removes + no taxonomy change + reassigned == 0
}
```

The STAGE#g29_cutover#GLOBAL row shape (D-16):
```
PK: STAGE#g29_cutover#GLOBAL
SK: RUN#{started_at}
{
  previous_publish_sha, new_publish_sha,
  hierarchy_version_at_cutover, run_id,
  started_at, completed_at
}
```
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: G-29 fix + STAGE# run_id substrate + S3 client cache_control kwarg + reproducibility test</name>
  <files>
    pipeline_hierarchy/bundler.py
    pipeline_hierarchy/generator.py
    pipeline_hierarchy/publish.py
    utils/s3_client.py
    utils/stage_records.py
    docs/hierarchy.schema.json
    tests/test_hierarchy_bundler.py
    tests/test_hierarchy_publisher.py
    tests/test_stage_records.py
    tests/test_hierarchy_reproducibility.py
    utils/test_s3_client.py
  </files>
  <read_first>
    pipeline_hierarchy/bundler.py (full file; the stamp at line 183-195)
    pipeline_hierarchy/generator.py (full file; the stamp at line 60-67 + manifest construction at 114-121)
    pipeline_hierarchy/publish.py (full file; compute_publish_input_hash at line 86-121 — the defensive `generated_at` strip is a no-op after D-14)
    utils/s3_client.py (full file)
    utils/stage_records.py (full file; the three builders + their optional-kwarg pattern)
    docs/hierarchy.schema.json (to determine if `generated_at` is `required` and must be moved to optional)
    tests/test_hierarchy_bundler.py (extend)
    tests/test_hierarchy_publisher.py (extend)
    tests/test_stage_records.py (extend with run_id-passthrough tests)
  </read_first>
  <behavior>
    - Test 1 (tests/test_hierarchy_bundler.py): `bundler.bundle(...)` returns a dict that does NOT contain key `generated_at`.
    - Test 2 (tests/test_hierarchy_publisher.py): `generator.build_hierarchy(...)` produces a hierarchy dict without `generated_at`; the produced manifest DOES contain `generated_at`.
    - Test 3 (tests/test_hierarchy_reproducibility.py): calling `bundler.bundle(...)` twice with the same inputs produces byte-identical `json.dumps(..., sort_keys=True)` output. Calling `generator.build_hierarchy(...)` twice ditto. (This is the G-36 prerequisite.)
    - Test 4 (tests/test_hierarchy_publisher.py): the manifest `generated_at` value passes the standard ISO regex `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$`.
    - Test 5 (utils/test_s3_client.py): `S3HierarchyClient.put_object(key, body, cache_control="max-age=60, must-revalidate")` calls underlying boto3 client with `CacheControl="max-age=60, must-revalidate"`. `put_object(key, body)` (default kwargs) does NOT include `CacheControl` in the boto3 call kwargs. Use `botocore.stub.Stubber` OR MagicMock for `_get_client()`.
    - Test 6 (tests/test_stage_records.py): all three builders accept `run_id="abc-123"` and produce a dict containing `"run_id": "abc-123"`. Called without `run_id`, the produced dict does NOT contain that key (backwards-compat).
    - Test 7 (docs/hierarchy.schema.json): if `generated_at` was in the `required` array on hierarchy.json's schema, it must now be removed; the schema must still validate a hierarchy dict WITHOUT `generated_at` AND validate manifest.json (separate file or pointer) WITH `generated_at`. (Phase 11 only changes the hierarchy schema, not manifest schema.)
  </behavior>
  <action>
    A. `pipeline_hierarchy/bundler.py` (D-14):
       - At lines 183-195: drop the line `"generated_at": generated_at,` from the returned dict.
       - Per RESEARCH recommendation: drop the `generated_at` parameter from the `bundle()` signature entirely (no in-repo callers pass it other than `publish.py`, which is updated in Task 2). Add a docstring note: `# generated_at removed per D-14 — manifest.json carries the timestamp; hierarchy.json is bit-stable across reruns.`
       - If `datetime` import becomes unused, remove it.

    B. `pipeline_hierarchy/generator.py` (D-14):
       - At line 66: drop the line `hierarchy["generated_at"] = generated_at`.
       - KEEP `generated_at` local variable (still used at line 111 to derive `version` label).
       - KEEP `manifest["generated_at"] = resolved_generated_at` at line 118 (D-14: manifest.json continues to carry it).
       - Add a comment at the dropped-line site: `# generated_at NOT written into hierarchy per D-14 (G-29 fix); manifest still carries it.`

    C. `pipeline_hierarchy/publish.py::compute_publish_input_hash` (lines 86-121):
       - The defensive strip of `generated_at` (line 108) is now a no-op (field never exists in the bundle). Remove the strip and update the comment to: `# G-29 fixed upstream per D-14; no embedded timestamp remains in the hierarchy dict.` Be careful not to remove other defensive logic — read the function in full first.

    D. `docs/hierarchy.schema.json`:
       - If `generated_at` is in the `required` array for the hierarchy JSON shape, remove it from `required` (do NOT remove the property definition — leave the optional property in place to not break any consumer that still reads it from a historical version directory).
       - If `generated_at` is NOT in `required`, no change needed; flag this in the summary.
       - Manifest schema (if present in the same file) keeps `generated_at` as required.

    E. `utils/s3_client.py:100-118` — `cache_control` optional kwarg:
       ```python
       def put_object(
           self,
           key: str,
           body: bytes,
           content_type: str = "application/json",
           cache_control: str | None = None,
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

    F. `utils/stage_records.py` (D-13):
       - In `build_complete_record`, `build_skipped_record`, `build_failed_record`: add `run_id: Optional[str] = None` kwarg. Append the conditional-attach line near the existing optional-kwarg block (around lines 200-212):
         ```python
         if run_id is not None:
             item["run_id"] = run_id
         ```
       - Update docstrings to mention `run_id` as an optional Phase 11 substrate addition (D-13) used by cold-path callers to correlate diff.json compute with assign-stage STAGE# rows.

    G. Tests:
       - `tests/test_hierarchy_bundler.py`: add a test that asserts `"generated_at" not in bundle(...)`.
       - `tests/test_hierarchy_publisher.py`: assert `"generated_at" not in hierarchy_dict`; assert `manifest["generated_at"]` matches the ISO regex.
       - `tests/test_hierarchy_reproducibility.py` (NEW): two-pass bundle + build, assert `json.dumps(..., sort_keys=True)` byte-equal across passes. Use fixed `generated_at` parameter (or fixed `datetime.utcnow` via monkeypatch) for the manifest variant of the test to also assert deterministic manifest (the manifest will differ on `generated_at` if generation time differs — that's expected per D-14 — so the manifest reproducibility test is "given fixed generated_at, manifest is byte-equal"; the hierarchy reproducibility test does NOT pin `generated_at` because hierarchy doesn't have it anymore).
       - `tests/test_stage_records.py`: extend with 6 new assertions — two per builder (one with run_id passed, one without).
       - `utils/test_s3_client.py` (NEW): MagicMock-based test for `_get_client()` return; assert `CacheControl` kwarg is conditionally included.

    H. Update the `pipeline_hierarchy.publish` callsite of `upload_to_s3` to NOT yet wire diff.json or cache_control (Task 2 does that). For this task: only the `cache_control` kwarg is exposed; no caller passes it yet. This keeps Task 1 self-contained.
  </action>
  <verify>
    <automated>cd /Users/paulalbert/Dropbox/GitHub/ReciterAI &amp;&amp; pytest tests/test_hierarchy_bundler.py tests/test_hierarchy_publisher.py tests/test_hierarchy_reproducibility.py tests/test_stage_records.py utils/test_s3_client.py -x</automated>
    Manual greps:
    - `grep -n '"generated_at"' pipeline_hierarchy/bundler.py` returns NO match in the returned dict (the only matches should be in comments).
    - `grep -n 'hierarchy\["generated_at"\]' pipeline_hierarchy/generator.py` returns NO match.
    - `grep -n 'manifest\["generated_at"\]\|manifest\.update.*generated_at\|"generated_at": resolved_generated_at' pipeline_hierarchy/generator.py` returns the preserved manifest stamp.
    - `grep -n 'cache_control' utils/s3_client.py` returns the new kwarg.
    - `grep -n 'if run_id is not None' utils/stage_records.py` returns 3 matches (one per builder).
    - Schema check: `python -c "import json; s = json.load(open('docs/hierarchy.schema.json')); print('generated_at' in s.get('required', []))"` prints `False` (or the schema is structured such that the hierarchy-shape doesn't list it as required).
  </verify>
  <done>
    All 7 tests pass; greps confirm G-29 is removed from hierarchy.json side; manifest still carries `generated_at`; STAGE# builders accept the optional `run_id` kwarg with no regression to existing callers; S3 client's `put_object` accepts `cache_control` with default-None backwards-compat.
  </done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: diff.json producer + 5-step S3 write order + STAGE#g29_cutover + contract doc update</name>
  <files>
    pipeline_hierarchy/publish.py
    pipeline_hierarchy/diff_stats.py
    docs/hierarchy-contract.md
    tests/test_publish_integration.py
    tests/test_publish_diff.py
  </files>
  <read_first>
    pipeline_hierarchy/publish.py (full file — every relevant site: compute_publish_input_hash, publish_hierarchy main, upload_to_s3, write_complete)
    pipeline_hierarchy/generator.py (manifest construction at line 114-121; need to know manifest fields available for diff producer)
    utils/s3_client.py (post-Task 1 — confirm cache_control kwarg signature)
    utils/stage_records.py (post-Task 1 — confirm run_id kwarg accepted by build_complete_record)
    tests/test_publish_integration.py (existing test patterns for publish flow)
    docs/hierarchy-contract.md (current contract doc; Phase 11 documents the new write order and diff.json)
  </read_first>
  <behavior>
    - Test 1 (tests/test_publish_diff.py — happy path with prev version): `compute_diff(prev_version="v2026-05-06", new_hierarchy={...}, run_id="abc", table=mock, s3_client=mock)` where mock S3 returns a prev hierarchy with `taxonomy_version="taxonomy_v2"`, 5 subtopics; new hierarchy has `taxonomy_version="taxonomy_v3"`, 6 subtopics (1 added). Mock DDB returns one STAGE# assign row with `records_written=42` for run_id="abc". → diff dict has `diff_schema_version="1.0.0"`, `from_version="v2026-05-06"`, `to_version="<new version>"`, `taxonomy_version_changed=True`, `added_subtopics` length 1, `reassigned_pmid_count=42`, `editorial_only=False`.
    - Test 2 (first-ever-publish per O-01): `compute_diff(prev_version=None, new_hierarchy={...}, run_id="abc", table=mock, s3_client=mock)` → diff dict has `from_version: null` (Python `None`), all `added_subtopics`/`removed_subtopics`/`renamed_subtopics` empty (no comparison), `taxonomy_version_changed=False`, `reassigned_pmid_count` from STAGE# rows, `editorial_only=False`.
    - Test 3 (editorial_only derivation): only renamed_subtopics non-empty, no taxonomy change, no add/remove, reassigned == 0 → `editorial_only=True`. Same scenario with reassigned > 0 → `editorial_only=False`.
    - Test 4 (run_id filter): two STAGE# assign rows in DDB — one with run_id="abc" (records_written=42), one with run_id="xyz" (records_written=999). `compute_diff(..., run_id="abc", ...)` → `reassigned_pmid_count == 42` ONLY (xyz row is filtered out).
    - Test 5 (tests/test_publish_integration.py — write order): asserting the EXACT order of `put_object` calls on a stubbed `S3HierarchyClient`. Use a custom stub class (analog from test_spotlight_rotation_selector.py:309-319) that captures calls into a list. Order must be: (1) `{version}/hierarchy.json`, (2) `{version}/hierarchy.schema.json`, (3) `{version}/diff.json`, (4) `{version}/manifest.json`, (5) `latest/manifest.json`.
    - Test 6 (Cache-Control): the `latest/manifest.json` put_object call (the 5th call) is invoked with `cache_control="max-age=60, must-revalidate"`. The other 4 calls are NOT invoked with cache_control.
    - Test 7 (STAGE#g29_cutover): when `publish_hierarchy(..., g29_cutover=True)` is called, exactly one PutItem fires with PK="STAGE#g29_cutover#GLOBAL", SK starting with "RUN#", and fields {previous_publish_sha, new_publish_sha, hierarchy_version_at_cutover, run_id, started_at, completed_at}. With `g29_cutover=False` (default), NO such PutItem fires.
    - Test 8 (run_id threading into STAGE# write): `publish_hierarchy(..., run_id="abc")` writes its own `STAGE#publish_hierarchy#GLOBAL` row with `run_id="abc"` attached. With no run_id passed, the row has no `run_id` field (backwards-compat).
  </behavior>
  <action>
    A. New module `pipeline_hierarchy/diff_stats.py` — shared diff-stats compute:
    ```python
    """Phase 11 D-09 — hybrid diff-stats compute.

    Two authorities:
      - STAGE# rows (filtered by run_id): "How many PMIDs got reassigned?"
      - Byte-comparison of prev vs new hierarchy.json: "What subtopics changed?"

    Pure functions over inputs; caller fetches both halves and merges.
    """
    from __future__ import annotations
    from typing import Any, Optional, Iterable

    DIFF_SCHEMA_VERSION = "1.0.0"

    def compute_structural_diff(prev_hierarchy: Optional[dict],
                                new_hierarchy: dict) -> dict:
        """Byte-comparison of prev vs new hierarchy structure.

        If prev_hierarchy is None (first-ever-publish per O-01), returns empty
        diffs and from_version remains None at the caller's discretion.
        """
        if prev_hierarchy is None:
            return {
                "taxonomy_version_changed": False,
                "added_subtopics": [],
                "removed_subtopics": [],
                "renamed_subtopics": [],
            }

        prev_tv = prev_hierarchy.get("taxonomy_version")
        new_tv = new_hierarchy.get("taxonomy_version")
        taxonomy_changed = prev_tv != new_tv

        prev_subs = _index_subtopics(prev_hierarchy)
        new_subs = _index_subtopics(new_hierarchy)

        added = sorted(set(new_subs) - set(prev_subs))
        removed = sorted(set(prev_subs) - set(new_subs))
        renamed: list[dict] = []
        for sid in sorted(set(prev_subs) & set(new_subs)):
            prev_label = prev_subs[sid].get("display_name")
            new_label = new_subs[sid].get("display_name")
            if prev_label != new_label:
                renamed.append({
                    "id": sid,
                    "old_display_name": prev_label,
                    "new_display_name": new_label,
                })

        return {
            "taxonomy_version_changed": taxonomy_changed,
            "added_subtopics": added,
            "removed_subtopics": removed,
            "renamed_subtopics": renamed,
        }

    def _index_subtopics(hierarchy: dict) -> dict[str, dict]:
        """Build {subtopic_id: subtopic_dict} index from a hierarchy dict.

        Walks `topics[].subtopics[]`. Subtopic identifier key matches whatever
        the existing schema uses — read the schema before implementing.
        """
        out: dict[str, dict] = {}
        for topic in hierarchy.get("topics", []):
            for sub in topic.get("subtopics", []):
                sid = sub.get("id") or sub.get("subtopic_id")
                if sid:
                    out[sid] = sub
        return out

    def compute_reassigned_pmid_count(stage_rows: Iterable[dict]) -> int:
        """Sum `records_written` across the provided assign-stage STAGE# rows.

        Per D-18: rows-touched semantics, NOT primary-changed semantics. The
        field name `reassigned_pmid_count` is a verbatim representation of
        records_written from assign-stage rows filtered by run_id at the
        caller.
        """
        return sum(int(r.get("records_written", 0) or 0) for r in stage_rows)

    def derive_editorial_only(structural: dict, reassigned_pmid_count: int) -> bool:
        """True iff only display-name renames occurred AND no PMID reassignment.

        Renames-only + no adds/removes + no taxonomy change + reassigned == 0.
        """
        return (
            not structural["taxonomy_version_changed"]
            and not structural["added_subtopics"]
            and not structural["removed_subtopics"]
            and bool(structural["renamed_subtopics"])
            and reassigned_pmid_count == 0
        )
    ```

    B. `pipeline_hierarchy/publish.py::compute_diff` (NEW function):
    ```python
    def compute_diff(*, prev_version: Optional[str], new_hierarchy: dict,
                     to_version: str, run_id: Optional[str],
                     table, s3_client) -> dict:
        """Phase 11 D-09, D-10, D-12, D-13, D-18.

        Produces the diff.json dict. Hybrid:
          - Structural: byte-compare prev vs new hierarchy.json (S3 GET).
          - PMID-count: query STAGE# assign rows filtered by run_id.

        First-ever-publish (O-01): prev_version is None → from_version: null
        in output; structural diffs are empty (no prev to compare).
        """
        # 1. Fetch prev hierarchy
        prev_hierarchy: Optional[dict] = None
        if prev_version is not None:
            try:
                body = s3_client.get_object(f"{prev_version}/hierarchy.json")
                prev_hierarchy = json.loads(body)
            except Exception as exc:
                # Treat as first-ever-publish per O-01 (explicit null)
                logger.warning(f"prev hierarchy GET failed: {exc}; treating as first-publish")
                prev_hierarchy = None

        # 2. Structural diff
        structural = compute_structural_diff(prev_hierarchy, new_hierarchy)

        # 3. STAGE# query for reassignment count
        stage_rows = _query_assign_stage_rows_by_run_id(table, run_id)
        reassigned = compute_reassigned_pmid_count(stage_rows)

        # 4. Assemble
        return {
            "diff_schema_version": DIFF_SCHEMA_VERSION,
            "from_version": prev_version,   # None → JSON null per O-01
            "to_version": to_version,
            "taxonomy_version_changed": structural["taxonomy_version_changed"],
            "added_subtopics": structural["added_subtopics"],
            "removed_subtopics": structural["removed_subtopics"],
            "renamed_subtopics": structural["renamed_subtopics"],
            "reassigned_pmid_count": reassigned,
            "editorial_only": derive_editorial_only(structural, reassigned),
        }
    ```
    Also add `_query_assign_stage_rows_by_run_id(table, run_id)` helper —
    Query by `begins_with(PK, "STAGE#assign_subtopics#")`, scan filter on
    `run_id == :rid` (Phase 9 convention: filter in Python until GSI added).
    Returns the matching items list. If `run_id is None`, returns `[]`
    (no rows attribute to "this run").

    Also need helper `s3_client.get_object(key)`: if the existing
    `S3HierarchyClient` does not have a `get_object` method, add one
    following the same lazy-client + logging convention as `put_object`:
    ```python
    def get_object(self, key: str) -> bytes:
        resp = self._get_client().get_object(Bucket=self.bucket, Key=key)
        body = resp["Body"].read()
        logger.info(f"Downloaded s3://{self.bucket}/{key} ({len(body):,} bytes)")
        return body
    ```
    (This is a small surface addition to `utils/s3_client.py` made in this
    plan because the diff producer is the first in-repo S3 GET caller — flag
    in the SUMMARY.)

    C. `pipeline_hierarchy/publish.py::upload_to_s3` (D-11 new 5-step order):
    ```python
    def upload_to_s3(
        version: str,
        hierarchy: bytes,
        schema: bytes,
        manifest: dict,
        diff_bytes: bytes,           # NEW Phase 11 (D-11 step 3)
    ) -> None:
        s3 = S3HierarchyClient()
        manifest_bytes = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")

        # 1. {version}/hierarchy.json
        s3.put_object(f"{version}/hierarchy.json", hierarchy)
        # 2. {version}/hierarchy.schema.json
        s3.put_object(f"{version}/hierarchy.schema.json", schema)
        # 3. {version}/diff.json — Phase 11 D-11: BEFORE manifest so consumer
        #    polling manifest.sha256 can immediately GET diff.json without
        #    racing eventually-consistent reads.
        s3.put_object(f"{version}/diff.json", diff_bytes)
        # 4. {version}/manifest.json — Phase 11 ADDS this version-pinned copy
        #    (previously missing per PATTERNS.md write-site inventory).
        s3.put_object(f"{version}/manifest.json", manifest_bytes)
        # 5. latest/manifest.json LAST, with Cache-Control on latest/* key.
        s3.put_object(
            "latest/manifest.json", manifest_bytes,
            cache_control="max-age=60, must-revalidate",
        )
    ```

    D. `pipeline_hierarchy/publish.py::publish_hierarchy` (main):
       - Add `--g29-cutover` flag to argparse (action="store_true", help="One-time: write STAGE#g29_cutover#GLOBAL audit row").
       - Add `--run-id` flag (default: `os.environ.get("RECITERAI_COLD_RUN_ID")`).
       - After `generate(...)` and before `upload_to_s3(...)`:
         - Resolve `prev_version` from `latest/manifest.json` via S3 GET; if 404 (first-ever-publish), set to None.
         - Call `diff = compute_diff(prev_version=prev_version, new_hierarchy=hierarchy_dict, to_version=version, run_id=run_id, table=table, s3_client=s3)`.
         - Encode: `diff_bytes = (json.dumps(diff, indent=2) + "\n").encode("utf-8")`.
       - Call `upload_to_s3(version, hierarchy, schema, manifest, diff_bytes)`.
       - In `write_complete(...)` call at line 351: thread `run_id=run_id` kwarg through to `build_complete_record` (the Task-1 substrate addition).
       - If `args.g29_cutover`:
         - Fetch previous publish sha: `prev_manifest = json.loads(s3.get_object("latest/manifest.json"))` (or None on 404); `previous_publish_sha = prev_manifest.get("sha256") if prev_manifest else None`.
         - Write cutover row:
           ```python
           cutover_item = build_complete_record(
               stage="g29_cutover", scope="GLOBAL",
               input_hash=compute_input_hash("g29_cutover",
                                             {"new_publish_sha": manifest["sha256"]}),
               started_at=started_at, completed_at=_now_iso(),
               duration_ms=duration_ms,
               cost_observed_usd=PUBLISH_COST_USD,
               run_id=run_id,
           )
           cutover_item["previous_publish_sha"] = previous_publish_sha
           cutover_item["new_publish_sha"] = manifest["sha256"]
           cutover_item["hierarchy_version_at_cutover"] = version
           table.put_item(Item=cutover_item)
           ```

    E. `docs/hierarchy-contract.md` updates:
       - Document new 5-step S3 write order (with the "diff before manifest" rationale).
       - Document the `Cache-Control: max-age=60, must-revalidate` on `latest/manifest.json`.
       - Document the diff.json shape (full JSON example).
       - Document O-01 resolution: first-ever-publish emits `diff.json` with `from_version: null`. Note that consumers MUST handle this null explicitly (do not fall through to "treat absence as wholesale" — absence vs null are different signals per spec §6).
       - Document G-29: `hierarchy.json` no longer carries `generated_at`; `manifest.json` continues to. Old version directories are not rewritten — historical `manifest.sha256` ↔ `hierarchy.json` pairs remain matched as written.
       - Add a §"Operator coordination (D-15)" section noting the SPS downstream-effects conversation expected at G-29 cutover time. Capture the coordination as a runbook entry: pre-cutover ping, expected reindex window, rollback plan.

    F. Tests:
       - `tests/test_publish_diff.py` (NEW): cover Tests 1-4 above. Pure-function-style — pass dicts in, assert dicts out. Mock S3HierarchyClient with MagicMock; mock DDB table similarly.
       - `tests/test_publish_integration.py` (extend): cover Tests 5, 6, 7, 8. For Test 5 (write order), use a stub `S3HierarchyClient` subclass that captures calls into a list (analog: `test_spotlight_rotation_selector.py:309-319` custom stub pattern).

    G. Per D-15 — operator coordination is captured as a documentation deliverable in `docs/hierarchy-contract.md` and the SUMMARY notes it. No engineering work beyond documentation. The 30-min SPS conversation is an external coordination task; the SUMMARY flags it as a follow-up for the operator at cutover time.
  </action>
  <verify>
    <automated>cd /Users/paulalbert/Dropbox/GitHub/ReciterAI &amp;&amp; pytest tests/test_publish_diff.py tests/test_publish_integration.py -x</automated>
    Manual greps:
    - `grep -n "diff_schema_version" pipeline_hierarchy/diff_stats.py pipeline_hierarchy/publish.py` returns at least 2 matches.
    - `grep -n 'from_version' pipeline_hierarchy/publish.py pipeline_hierarchy/diff_stats.py` returns matches showing `prev_version` flows into `from_version` (which becomes JSON `null` on None).
    - `grep -n 'editorial_only' pipeline_hierarchy/diff_stats.py` returns the derivation function.
    - `grep -n 'cache_control=.max-age=60' pipeline_hierarchy/publish.py` returns exactly 1 match (the latest/manifest.json line).
    - `grep -c 's3.put_object\|s3_client.put_object' pipeline_hierarchy/publish.py | grep -v '^#'` &gt;= 5 (the 5 PutObjects of the new write order — verify count is 5, not 3).
    - `grep -n 'STAGE#g29_cutover' pipeline_hierarchy/publish.py` returns the cutover write site.
    - `grep -n 'g29-cutover' pipeline_hierarchy/publish.py` returns the argparse flag.
    - Doc check: `grep -n 'from_version.*null\|first-ever-publish' docs/hierarchy-contract.md` returns the O-01 documentation.
    - Doc check: `grep -n 'Cache-Control\|max-age=60' docs/hierarchy-contract.md` returns the cache-control documentation.
  </verify>
  <done>
    All 8 tests pass; greps confirm the 5-step write order is in place; first-ever-publish path (prev_version=None) produces `from_version: null`; STAGE#g29_cutover writes only under `--g29-cutover`; docs/hierarchy-contract.md documents all Phase 11 contract additions.
  </done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| S3 GET prev hierarchy → diff producer | Trusted infrastructure; bucket access controlled by IAM |
| STAGE# rows → diff producer | Read-only; rows are written by trusted cold-path stages |
| Operator `--g29-cutover` flag | One-time operator-known action; mistake = an extra audit row, not data corruption |
| SPS (consumer) parses diff.json | Consumer must handle `from_version: null` per O-01 |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-11-03-01 | Tampering | diff.json content | accept | Diff is consumer-advisory; the manifest.sha256 remains the authoritative integrity check on hierarchy.json. Even a corrupted diff cannot cause SPS to ingest bad hierarchy bytes — schema_validation gate at publish time enforces hierarchy integrity. |
| T-11-03-02 | Tampering | S3 write order race | mitigate | D-11 explicit ordering: diff.json before manifest, manifest version-pinned before latest. Consumer polling manifest.sha256 sees the new sha only after diff is durable. Test 5 enforces order via stubbed call-capture. |
| T-11-03-03 | Information Disclosure | reassigned_pmid_count leaks dataset size | accept | The count is a single integer; not PII. Hierarchy publishes are operator-driven; the bucket is access-controlled. |
| T-11-03-04 | DoS | S3 GET on prev hierarchy could fail | mitigate | `compute_diff` catches exceptions on the prev GET and falls back to first-ever-publish semantics (from_version: null). Diff is best-effort; absence of prev does not block publish. |
| T-11-03-05 | Repudiation | who triggered the G-29 cutover | mitigate | `--g29-cutover` flag writes a single audit row carrying `run_id`, `previous_publish_sha`, `new_publish_sha`, `hierarchy_version_at_cutover`, and timestamps. Six-month archaeology is one DDB get. |
| T-11-03-06 | Elevation of Privilege | Cache-Control header on latest/* | accept | Cache-Control is an HTTP cacheability hint; misuse → stale read at SPS, not auth escalation. `max-age=60` is conservative (forces revalidation every minute); `must-revalidate` prevents serving stale content past freshness. |
| T-11-03-07 | Spoofing | Forged STAGE# row inflates reassigned count | mitigate | STAGE# rows are written by the cold-path orchestrator only. `run_id` filtering scopes the diff query to the current cold-run, so historical rows from prior runs cannot pollute the count (D-13). |
</threat_model>

<verification>
- `hierarchy.json` is bit-stable across content-identical reruns (Task 1 reproducibility test).
- `diff.json` carries `diff_schema_version` and is emitted on every publish (including first-ever, with `from_version: null` per O-01).
- S3 write order is exactly D-11; `latest/manifest.json` carries Cache-Control.
- STAGE# substrate accepts optional `run_id` kwarg; existing callers unaffected.
- STAGE#g29_cutover#GLOBAL writes only under `--g29-cutover` flag.
- `docs/hierarchy-contract.md` documents the new contract surfaces (write order, diff.json, Cache-Control, O-01, G-29 cutover).
- Per D-15: SPS operator coordination is captured as a runbook section in
  `docs/hierarchy-contract.md`; the actual conversation is an external task flagged in
  the SUMMARY for the operator to schedule at cutover time.
</verification>

<success_criteria>
- D-09 + D-13: compute_diff filters STAGE# queries by run_id; structural diff is byte-comparison.
- D-10: diff producer lives in `pipeline_hierarchy/publish.py` (and `diff_stats.py` for pure pieces); no new stage.
- D-11: upload_to_s3 issues exactly 5 PutObjects in the documented order.
- D-12: diff.json contains `diff_schema_version: "1.0.0"`.
- D-14: hierarchy.json has no `generated_at`; manifest.json still does.
- D-16: STAGE#g29_cutover#GLOBAL row written under `--g29-cutover` flag with the documented fields.
- D-18: `reassigned_pmid_count` == sum of records_written across the filtered assign STAGE# rows (rows-touched semantics).
- O-01: first-ever-publish emits diff.json with `from_version: null`.
- Cache-Control: `max-age=60, must-revalidate` on `latest/manifest.json`.
- All tests pass: `pytest tests/test_hierarchy_bundler.py tests/test_hierarchy_publisher.py tests/test_hierarchy_reproducibility.py tests/test_stage_records.py utils/test_s3_client.py tests/test_publish_diff.py tests/test_publish_integration.py -x`
</success_criteria>

<output>
Create `.planning/phases/11-versioning-review-diff/11-SUMMARY-change-signaling.md` documenting:
- Files changed + new files
- The 5-step S3 write order (with the "diff before manifest" rationale)
- diff.json shape (full JSON example)
- Cache-Control on latest/manifest.json
- G-29 fix scope (bundler + generator + manifest preservation)
- STAGE# run_id substrate addition (backwards-compatible)
- STAGE#g29_cutover gate (--g29-cutover flag)
- Open items resolved: O-01 (from_version: null on first publish)
- D-15 follow-up: SPS operator-coordination conversation scheduled for cutover day (not engineering deliverable)
- Note on OQ-3 / OQ-4: D-18 resolution adopts rows-touched semantics (records_written); spotlight latest/* Cache-Control is deferred (out of scope this plan)
- Note: added `S3HierarchyClient.get_object()` because the diff producer is the first S3 GET caller in the repo
</output>

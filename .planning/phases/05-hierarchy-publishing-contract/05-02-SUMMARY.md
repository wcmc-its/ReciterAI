---
phase: 05-hierarchy-publishing-contract
plan: "02"
subsystem: pipeline
tags: [s3, publish, schema-validation, manifest, boto3, hierarchy]
dependency_graph:
  requires: [05-01]
  provides: [s3-publish-pipeline, lazy-s3-client, manifest-generation]
  affects: [backfill_all.py, utils/s3_client.py]
tech_stack:
  added: [boto3 S3 client, jsonschema.Draft202012Validator, hashlib]
  patterns: [lazy-init AWS client, fail-fast validation gate, idempotent S3 overwrite]
key_files:
  created:
    - utils/s3_client.py
  modified:
    - backfill_all.py
decisions:
  - "D-13 enforced: PM worktree copy is unconditional in --publish mode (no skip_pm_copy param)"
  - "D-16 enforced: schema validation gates all S3 PutObject calls (fail-or-pass, no warn-and-publish)"
  - "T-05-02-02 enforced: sha256 covers in-memory json.dumps bytes, not on-disk file"
  - "T-05-02-03 enforced: manifest field insertion order is canonical (no sort_keys)"
  - "Rule 2 deviation: added _run_assemble_only() helper (referenced in plan interface but absent from code)"
metrics:
  duration: "8 minutes"
  completed: "2026-05-06T22:08:40Z"
  tasks_completed: 2
  files_modified: 2
---

# Phase 05 Plan 02: S3 Client + --publish Flag Summary

Lazy-init S3HierarchyClient and schema-validated publish pipeline via `backfill_all.py --publish`.

## What Was Built

**utils/s3_client.py** (new): Lazy-init `S3HierarchyClient` mirroring `BedrockClient` from `utils/bedrock_client.py`. No boto3 calls at import time. `put_object()` uploads bytes with Content-Type header. `key_exists()` uses HeadObject and re-raises non-404 `ClientError` so auth failures surface immediately. Module-level constants `HIERARCHY_BUCKET = "wcmc-reciterai-hierarchy"` and `HIERARCHY_REGION = "us-east-1"` per D-01.

**backfill_all.py** (modified): Added 231 lines implementing:

1. **Constants** (`HIERARCHY_SCHEMA_PATH`, `HIERARCHY_BUCKET`) after existing constants block (line 116-117).

2. **`_run_assemble_only(skip_pm_copy: bool) -> int`** (lines ~424-472): New helper that re-assembles `hierarchy_full.json` from per-topic augmented drafts and optionally copies to PM worktree. Added as Rule 2 deviation — plan interface declared it as "existing" but it was absent from the code.

3. **`_run_publish(dry_run: bool) -> int`** (lines ~475-611): Full publish pipeline:
   - Step 1: Auto-assembles hierarchy if missing
   - Step 2: Loads schema from `docs/hierarchy.schema.json`
   - Step 3: `Draft202012Validator.iter_errors()` validation gate (D-16)
   - Step 4: Computes `hierarchy_bytes = json.dumps(hierarchy, indent=2, ensure_ascii=False).encode("utf-8")`, then `sha256 = hashlib.sha256(hierarchy_bytes).hexdigest()`
   - Step 5: Dry-run preview prints manifest and returns 0 (no PutObject)
   - Step 6: 6 PutObjects — `v{ISO-date}/{hierarchy.json,hierarchy.schema.json,manifest.json}` then `latest/{...}`
   - Step 7: PM worktree copy (unconditional per D-13)
   - Step 8: Summary print block

4. **`run()` signature**: Added `assemble_only: bool = False` and `publish: bool = False`; short-circuits precede verdict gate.

5. **Argparse**: `--assemble-only` and `--publish` flags added before `return parser.parse_args()`.

6. **`__main__`**: Passes `assemble_only=args.assemble_only` and `publish=args.publish`.

### Dry-Run Preview Sample Output

```
=== DRY RUN — --publish preview ===
  version:          v2026-05-06
  schema_version:   1.0.0
  taxonomy_version: 2.0
  sha256:           <64-char hex>
  artifact_bytes:   <N>
  Would upload to:  s3://wcmc-reciterai-hierarchy/v2026-05-06/  (3 objects)
                    s3://wcmc-reciterai-hierarchy/latest/     (3 objects, overwrite)

Manifest preview:
{
  "schema_version": "1.0.0",
  "taxonomy_version": "2.0",
  "version": "v2026-05-06",
  "generated_at": "2026-05-06T22:08:40Z",
  "sha256": "...",
  "artifact_bytes": ...
}
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical Functionality] Added `_run_assemble_only()` helper**
- **Found during:** Task 2
- **Issue:** The plan interface declaration listed `_run_assemble_only(skip_pm_copy: bool) -> int` as an "existing helper reused by --publish" and the PATTERNS.md described it at lines 426-497 of backfill_all.py. However, the function did not exist in the actual file — those lines contained the `run()` function body. `_run_publish()` calls `_run_assemble_only(skip_pm_copy=True)` to auto-assemble if `hierarchy_full.json` is missing, so this was a blocking missing dependency.
- **Fix:** Added `_run_assemble_only(skip_pm_copy: bool) -> int` immediately before `_run_publish()`. Extracts assembly logic from `run()` into a callable helper. Also added `--assemble-only` argparse flag and `assemble_only=False` parameter to `run()` to make it accessible from CLI (consistent with PATTERNS.md description).
- **Files modified:** `backfill_all.py`
- **Commits:** 3f5456d

**2. [Plan Clarification] `_run_publish` signature: no `skip_pm_copy` param**
- The RESEARCH.md reference implementation (lines 661-777) included `skip_pm_copy: bool` as a parameter. The plan spec explicitly overrides this with `_run_publish(dry_run: bool) -> int` (no `skip_pm_copy`) citing D-13. The plan spec takes precedence — `_run_publish` has no `skip_pm_copy` parameter and the PM copy is always unconditional.

## Threat Surface Scan

No new network endpoints, auth paths, or trust boundaries beyond what the plan's `<threat_model>` already covers (T-05-02-01 through T-05-02-08). All mitigations implemented:
- T-05-02-01: No credential values logged or printed
- T-05-02-02: sha256 over in-memory bytes (not file)
- T-05-02-03: Manifest dict literal uses explicit insertion order; no sort_keys
- T-05-02-04: AST-verified: `iter_errors` position precedes `put_object` position

## Self-Check: PASSED

- utils/s3_client.py: FOUND
- backfill_all.py: FOUND
- 05-02-SUMMARY.md: FOUND
- Commit ce24798 (Task 1): FOUND
- Commit 3f5456d (Task 2): FOUND

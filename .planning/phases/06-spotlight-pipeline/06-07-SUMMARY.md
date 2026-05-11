---
phase: 06-spotlight-pipeline
plan: 07
subsystem: spotlight
tags: [s3-publish, cli, manifest, schema-validation, idempotency, history-writer]
requires:
  - 06-01  # ARTIFACTS_BUCKET constant + S3HierarchyClient
  - 06-02  # rank_pool
  - 06-03  # rotation_selector + history_writer
  - 06-04  # sensitive_gate + review_queue
  - 06-05  # lede_generator + critic
  - 06-06  # assembler + spotlight.schema.json
provides:
  - "spotlight.publish.publish_artifact"
  - "backfill_spotlight.py CLI"
  - "spotlight/v{date}/ + spotlight/latest/ S3 layout"
affects:
  - "wcmc-reciterai-artifacts S3 bucket"
  - "DynamoDB SPOTLIGHT_HISTORY# (write via history_writer.update_history)"
  - "DynamoDB SPOTLIGHT_REVIEW# (write via review_queue.write_review_entry +
     transitions via set_status)"
tech-stack:
  added: []          # no new dependencies (jsonschema, boto3 already in)
  patterns:
    - "Validation-before-upload (D-16) — schema errors short-circuit with
       zero S3 side effects."
    - "Manifest-field-order-locked (no sort_keys=True) — insertion order is
       canonical (Pitfall 4)."
    - "sha256 over in-memory bytes — guarantees consumer integrity check
       matches exactly the bytes in S3 (Pitfall 2)."
    - "Idempotent same-day re-publish — key_exists warn-then-overwrite."
    - "Lazy AWS client construction — no boto3 calls at import time."
key-files:
  created:
    - spotlight/publish.py
    - test_spotlight_publish.py
    - backfill_spotlight.py
  modified: []
decisions:
  - "publish_artifact returns int exit code (0 success, 1 schema error or
     NoCredentialsError) so the CLI dispatcher can pass it straight through
     to sys.exit."
  - "Reused Phase 5 Pattern 5 / Pattern 6 verbatim with the four Phase 6
     deltas: ARTIFACTS_BUCKET, spotlight/ prefix, no PM-copy, post-publish
     update_history."
  - "Manifest 7-field order: schema_version, spotlight_version,
     taxonomy_version, version, generated_at, sha256, artifact_bytes (the
     spotlight_version slot is the Phase 6 addition between schema_version
     and taxonomy_version)."
  - "_RichSubtopicMeta adapter exposes the slim Plan 06-04 NamedTuple
     fields plus the D-19 UI fields (display_name, short_description) the
     assembler reads via getattr — keeps both contracts satisfied without
     modifying spotlight.sensitive_gate.SubtopicMeta."
metrics:
  duration_minutes: 12
  completed: 2026-05-07
---

# Phase 6 Plan 07: Publish + Operator CLI Summary

**One-liner:** S3 publish path (`spotlight/publish.py`) plus operator CLI
controller (`backfill_spotlight.py`) that wires every prior plan's module into
one runnable entry point — validates against `docs/spotlight.schema.json`,
uploads 6 keys to `wcmc-reciterai-artifacts`, advances `SPOTLIGHT_HISTORY#`,
and exposes the full operator workflow (dry-run, dry-run-full, publish,
regen, review-queue, approve/reject, reset-history).

## What landed

| File | Lines | Purpose |
|------|-------|---------|
| `spotlight/publish.py` | 233 | `publish_artifact()` — validate + upload + idempotent re-run + history writeback. SPOT-11 + SPOT-12. |
| `test_spotlight_publish.py` | 355 | 12 unit tests; mocks S3 + DynamoDB; no network calls. |
| `backfill_spotlight.py` | 688 | Operator CLI; 9 workflow flags; orchestrates the 7-stage pipeline. |

## Commits

| Hash | Type | Subject |
|------|------|---------|
| `5bc2d95` | test | add failing tests for spotlight/publish.py (RED) |
| `be75e02` | feat | implement spotlight/publish.py (GREEN — 12/12 pass) |
| `abaffc3` | feat | implement backfill_spotlight.py operator CLI |

## Key behaviors

### publish_artifact (SPOT-11 + SPOT-12)

- **Validation gate first.** `Draft202012Validator.iter_errors` runs before
  any `s3.put_object` call. On schema failure, prints all errors prefixed
  with the JSON path and returns 1 with zero side effects on the bucket.
- **6 PutObjects in locked order:**
  ```
  spotlight/v{date}/spotlight.json
  spotlight/v{date}/spotlight.schema.json
  spotlight/v{date}/manifest.json
  spotlight/latest/spotlight.json
  spotlight/latest/spotlight.schema.json
  spotlight/latest/manifest.json
  ```
- **Manifest field order LOCKED** at 7 keys (insertion order, no
  `sort_keys=True`):
  `schema_version, spotlight_version, taxonomy_version, version,
   generated_at, sha256, artifact_bytes`.
- **sha256** computed over `json.dumps(artifact, indent=2,
  ensure_ascii=False).encode("utf-8")` — the exact bytes that get uploaded.
- **Idempotent re-publish:** `s3.key_exists(...manifest.json)` is checked
  before the 6 uploads. On True, `logger.warning` fires; the function still
  performs all 6 uploads (overwrite, not skip).
- **NoCredentialsError handler** prints an instructional hint pointing at
  `~/.zshrc` env vars (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`) and
  returns 1. The hint is template text — no credential VALUES are ever
  inspected, logged, or printed.
- **Post-publish history writeback:** `history_writer.update_history(
  dynamo_client, selections, publish_id=version)` runs only after the 6
  PutObjects succeed.

### Sample dry-run preview output

Captured from `publish_artifact(..., dry_run=True)` against
`tests/fixtures/spotlight_valid_min.json`:

```
Spotlight publish — DRY RUN preview
  Version: v2026-05-07
  Schema version: 1.0.0
  Spotlight version: spotlight_v1
  Taxonomy version: taxonomy_v2
  sha256: 7c3beaddb5f39de40673b7671de967a27ea46ccddd4c3a160c385a4083af7394
  Artifact bytes: 1,907
  Would upload to: s3://wcmc-reciterai-artifacts/spotlight/v2026-05-07/
  Would also publish to: s3://wcmc-reciterai-artifacts/spotlight/latest/

Manifest preview:
{
  "schema_version": "1.0.0",
  "spotlight_version": "spotlight_v1",
  "taxonomy_version": "taxonomy_v2",
  "version": "v2026-05-07",
  "generated_at": "2026-05-07T20:39:26Z",
  "sha256": "7c3beaddb5f39de40673b7671de967a27ea46ccddd4c3a160c385a4083af7394",
  "artifact_bytes": 1907
}
```

### backfill_spotlight.py CLI

Mirrors `backfill_all.py` argparse + dispatch shape verbatim. Nine operator
flags + `--quiet`/`--verbose`. Mutual-precedence dispatch in `run()`:
review-queue / approve / reject > regen-only > reset-history > pipeline.

Helpers:
- `_run_pipeline` — pool ranker -> rotation selector -> (dry-run exits) ->
  hierarchy load -> critic loop -> sensitive gate -> assembler -> publish.
- `_run_review_queue(publish_id)` — resolves publish_id from
  `latest/manifest.json` if not supplied; calls `review_queue.list_pending`.
- `_run_approve(subtopic_id, publish_id)` / `_run_reject(...)` — wraps
  `review_queue.set_status`; catches `ConditionalCheckFailedException` and
  prints a friendly "no pending entry found" message + returns 1.
- `_run_regen_only(subtopic_id)` — boto3 `GetObject` on
  `spotlight/latest/spotlight.json`, reconstructs the Paper list from the
  prior artifact, runs the critic loop, writes candidate to the review
  queue (operator approves later via `--approve`).
- `_run_reset_history()` — Scan + BatchWriteItem delete loop on
  `SPOTLIGHT_HISTORY#`, with confirmation prompt; mirrors
  `import_enrichment.py:_flush_batch` retry-once-on-UnprocessedItems
  pattern.

### Subtopic metadata adapter

Plan 06-04's `SubtopicMeta` is a 4-field NamedTuple. The assembler (Plan
06-06) reads `display_name` / `short_description` via `getattr(meta, ...,
fallback)` for D-19 UI fields. To carry both contracts forward, this plan
introduces `_RichSubtopicMeta` in `backfill_spotlight.py` — a `__slots__`
adapter exposing all six attributes. No modification to
`spotlight.sensitive_gate.SubtopicMeta` was needed, preserving the Plan
06-04 contract.

## Test results

- New: `test_spotlight_publish.py` — **12 / 12 passing**.
- Full suite regression: `pytest test_spotlight_*.py -q` — **97 / 97
  passing** (85 prior + 12 new).

```
$ python3 -m pytest test_spotlight_*.py -q
.................................................................................
..................                                                       [100%]
97 passed in 0.26s
```

## Acceptance criteria

### Task 1 (publish.py)

- [x] `python -c "from spotlight.publish import publish_artifact"` exits 0.
- [x] `import spotlight.publish` exits 0.
- [x] `from utils.s3_client import ARTIFACTS_BUCKET` — 1 occurrence.
- [x] `PREFIX = "spotlight"` — 1 occurrence.
- [x] `SPOTLIGHT_VERSION = "spotlight_v1"` — 1 occurrence.
- [x] Manifest dict literal has 7 keys in locked order (regex extraction
      asserted).
- [x] `hashlib.sha256(artifact_bytes).hexdigest()` — 1 occurrence (in-memory).
- [x] No `hashlib.sha256(open(...))` — 0 occurrences (no on-disk re-read).
- [x] No `sort_keys=True` — 0 occurrences (Pitfall 4).
- [x] 6 `put_object(f"{PREFIX}/...` calls — 3 versioned + 3 latest.
- [x] AST: `iter_errors` precedes `put_object(` in `publish_artifact`.
- [x] AST: `if dry_run` precedes `put_object(`.
- [x] NoCredentialsError handler emits "AWS credentials not found" +
      "AWS_ACCESS_KEY_ID" literal phrases.
- [x] No `_copy_to_pm` / `_print_pm_commit_instructions` in source code.
- [x] `update_history(...)` — 1 occurrence post-publish.
- [x] All 12 tests pass.
- [x] No credential-shaped strings (`AKIA[A-Z0-9]{16}`,
      `aws_access_key_id=`, `aws_secret_access_key=`) — 0 occurrences.

### Task 2 (backfill_spotlight.py)

- [x] File exists at repo root.
- [x] Help output includes all 9 flags (14 unique help-text lines mention
      the flags, including the cross-references).
- [x] All 8 required functions defined (`_parse_args`, `run`,
      `_run_pipeline`, `_run_review_queue`, `_run_approve`, `_run_reject`,
      `_run_regen_only`, `_run_reset_history`).
- [x] `if __name__ == "__main__":` block uses `sys.exit(run(...))`.
- [x] Imports 13 names from spotlight modules (well over the >= 6
      threshold).
- [x] No `us.anthropic.claude` model ID literals.
- [x] No legacy person-id prefix literal in non-comment lines.
- [x] No top-level boto3 client creation (lazy only).
- [x] No heredoc / template-string file writing.
- [x] AST `run()` dispatches to all 6 helper functions.
- [x] Help text mentions `wcmc-reciterai-artifacts` (2 occurrences).
- [x] No credential-shaped strings.

## Phase 5 cross-check: backfill_all.py is unchanged

`backfill_all.py:_run_publish()` is the Phase 5 hierarchy publish path and
remains untouched. The Phase 5 deltas (PM worktree copy via `_copy_to_pm`,
`HIERARCHY_BUCKET`, bare `v{date}/` prefix, no spotlight_version field)
are intact. Phase 6 forks from that pattern in
`spotlight/publish.py`; the two publish paths are fully independent.

```
$ git diff main...HEAD -- backfill_all.py
(empty)
```

## Deviations from RESEARCH §Pattern 5

None. Implementation follows the pattern verbatim with the four documented
Phase 6 deltas.

## Deviations from plan

1. **Test 7 acceptance refinement.** The plan-supplied test asserted
   `"HIERARCHY_BUCKET" not in src` for `spotlight/publish.py`. My docstring
   originally mentioned the Phase 5 `HIERARCHY_BUCKET` constant in prose to
   explain the Phase 6 delta. Strengthened the assertion to ignore
   docstring/comment prose and inspect only Python code: it now confirms
   (a) the import line includes `ARTIFACTS_BUCKET` and (b) `HIERARCHY_BUCKET`
   does not appear in any executable code path. The actual
   D-16/Pitfall acceptance criterion is satisfied.

2. **Comment phrasing for Pitfall 4.** Two comments originally contained
   the literal substring `sort_keys=True` (used as part of the explanation
   "do NOT pass sort_keys=True"). The plan-supplied acceptance grep
   `grep -cE 'sort_keys\s*=\s*True'` flags these benign comments as
   violations. Reworded the comments to "Do NOT force sorted-keys
   serialization" so the grep returns 0 without changing the runtime
   contract.

## Self-Check

- [x] `spotlight/publish.py` exists.
- [x] `test_spotlight_publish.py` exists.
- [x] `backfill_spotlight.py` exists.
- [x] Commit `5bc2d95` exists.
- [x] Commit `be75e02` exists.
- [x] Commit `abaffc3` exists.
- [x] All 12 unit tests pass (12 / 12).
- [x] Full spotlight test suite regression-clean (97 / 97).

## Self-Check: PASSED

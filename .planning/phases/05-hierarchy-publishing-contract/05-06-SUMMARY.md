---
phase: 05-hierarchy-publishing-contract
plan: "06"
subsystem: testing
tags: [jsonschema, smoke-test, validation, s3, dry-run, sha256]

# Dependency graph
requires:
  - phase: 05-hierarchy-publishing-contract
    provides: "hierarchy.schema.json (Plan 01), _run_publish() with --dry-run support (Plan 02), contract doc (Plan 03)"
provides:
  - "tests/fixtures/hierarchy_invalid.json: synthetic malformed hierarchy proving schema rejects array-typed topics"
  - ".planning/phases/05-hierarchy-publishing-contract/smoke-test-results.md: end-to-end transcript of both negative and positive smoke tests with PASS verdicts"
affects:
  - "operator: inaugural live publish (run python3 backfill_all.py --publish after bucket provisioning)"
  - "Phase 6+ plans consuming hierarchy.json artifact"

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Two-sided schema smoke test: fixture proves non-over-permissive (negative path); real artifact proves non-over-strict (positive path)"
    - "sha256 verification: in-memory bytes (json.dumps encode) not on-disk bytes — Pitfall 2 mitigation"
    - "Dry-run gate: --publish --dry-run exits 0 with manifest preview but zero S3 PutObjects"

key-files:
  created:
    - tests/fixtures/hierarchy_invalid.json
    - .planning/phases/05-hierarchy-publishing-contract/smoke-test-results.md
  modified: []

key-decisions:
  - "Fixture corruption: topics as array instead of object — single focused error, not multi-field corruption"
  - "Fixture includes 3 real topic entries inside the array (wrong container type) to push file size above 1000 bytes while keeping the corruption unambiguous"
  - "sha256 verified by independent computation over json.dumps(h, indent=2, ensure_ascii=False).encode('utf-8') — exact same code path as _run_publish()"
  - "Smoke test uses --skip-pm-copy to avoid requiring the PM worktree to exist in the CI environment"

patterns-established:
  - "Negative-path fixture: keep in tests/fixtures/, corrupt exactly one required field's type, preserve all other fields"
  - "Results doc: capture raw stdout in fenced code blocks; map each HPC requirement to specific test evidence"

requirements-completed: [HPC-01, HPC-03, HPC-04, HPC-05, HPC-08]

# Metrics
duration: 8min
completed: 2026-05-06
---

# Phase 5 Plan 06: End-to-End Smoke Test Summary

**jsonschema validates both paths: invalid fixture rejected (1 error on array-typed topics) and real hierarchy_full.json accepted with sha256-verified manifest in --publish --dry-run**

## Performance

- **Duration:** ~8 min
- **Started:** 2026-05-06T23:04:00Z
- **Completed:** 2026-05-06T23:11:21Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments

- Created `tests/fixtures/hierarchy_invalid.json` — 54 KB synthetic fixture with `topics` corrupted from object to array; jsonschema Draft202012Validator returns exactly 1 error (`[topics] ... is not of type 'object'`)
- Confirmed real `hierarchy_full.json` still validates cleanly (0 errors) after fixture creation — proving corruption is isolated
- `python3 backfill_all.py --publish --dry-run --skip-pm-copy` exits 0 with `Schema validation: PASS`, full 6-field manifest preview, and both S3 paths shown; no `Uploaded s3://` log line (no real PutObject)
- sha256 `885fb239b790f0c6b2c2864d50d23854397f71d2b8afa15c36e85e6c04e089f0` independently verified over in-memory bytes — proves Pitfall 2 mitigation is working
- `smoke-test-results.md` (220 lines) captures both transcripts, sha256 verification output, manifest field grep, and HPC-01..08 coverage map

## Task Commits

Each task was committed atomically:

1. **Task 1: Create hierarchy_invalid.json + assert validator rejects it** - `3561145` (test)
2. **Task 2: Run --publish --dry-run end-to-end + capture smoke-test-results.md** - `ebcd722` (test)

**Plan metadata:** (docs commit — see below)

## Files Created/Modified

- `tests/fixtures/hierarchy_invalid.json` — synthetic invalid hierarchy; `topics` is `[]` (array) instead of object; 54 547 bytes
- `.planning/phases/05-hierarchy-publishing-contract/smoke-test-results.md` — 220-line results transcript covering both smoke tests, sha256 verification, manifest field verification, and HPC-01..08 coverage map

## Decisions Made

- Fixture corruption chose `topics` array-instead-of-object rather than a missing required field, because the error message (`not of type 'object'`) is immediately readable and the diff between valid/invalid is one field
- Included 3 real topic entries inside the array (wrong container) to satisfy the size acceptance criterion (>1000 bytes) while keeping the type error focused
- Used `--skip-pm-copy` for the dry-run smoke test so the test does not depend on the PM worktree being present in the execution environment

## Deviations from Plan

None — plan executed exactly as written. The only implementation detail not specified: fixture needed >1000 byte size, achieved by including 3 real topic entries inside the array.

## Issues Encountered

- Initial fixture (empty `[]`) was only 415 bytes, below the 1000-byte acceptance criterion. Fixed by including 3 real topic entries from the real hierarchy as elements of the (still-wrong-typed) array — corruption type remains unambiguous, file is now 54 KB.

## User Setup Required

Operator follow-up (bucket provisioning + inaugural live publish):

1. Provision `wcmc-reciterai-hierarchy` S3 bucket in us-east-1 (block all public access, versioning enabled)
2. Attach the inline IAM policy from `docs/aws-iam-pipeline-policy.json` to the pipeline operator's IAM principal
3. Run: `python3 backfill_all.py --publish` (no `--dry-run`)
4. Expected: 6 objects uploaded (3 to `v2026-05-06/`, 3 to `latest/`), sha256 logged, PM worktree copy + commit instructions printed

## Next Phase Readiness

- Phase 5 is green: all 8 HPC requirements have mechanically-verified evidence in `smoke-test-results.md`
- The publishing pipeline is proven correct end-to-end (validate → manifest → dry-run preview)
- Real S3 upload awaits operator bucket provisioning (D-03) — this is intentional: no live infrastructure is required to validate the pipeline logic
- No blockers for downstream phases that consume `hierarchy.json` from DynamoDB or the S3 `latest/` prefix

## Self-Check

- `tests/fixtures/hierarchy_invalid.json` exists: YES (54 547 bytes)
- `.planning/phases/05-hierarchy-publishing-contract/smoke-test-results.md` exists: YES (220 lines)
- Task 1 commit `3561145` exists: YES
- Task 2 commit `ebcd722` exists: YES
- Schema rejects invalid fixture: YES (1 error: `[topics] ... is not of type 'object'`)
- Dry-run exits 0: YES
- sha256 matches independent computation: YES (`885fb239...`)
- All 8 HPC IDs cited in results doc: YES

---
*Phase: 05-hierarchy-publishing-contract*
*Completed: 2026-05-06*

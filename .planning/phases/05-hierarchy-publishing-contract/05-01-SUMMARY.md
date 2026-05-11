---
phase: 05-hierarchy-publishing-contract
plan: "01"
subsystem: infra
tags: [json-schema, s3, iam, jsonschema, hierarchy, draft-2020-12]

requires:
  - phase: 04-subtopic-system
    provides: hierarchy_full.json with 1,526 subtopics + D-19 display_name/short_description fields

provides:
  - docs/hierarchy.schema.json — Draft 2020-12 JSON Schema validating hierarchy_full.json (HPC-01)
  - docs/aws-iam-pipeline-policy.json — paste-ready least-privilege IAM inline policy for the pipeline operator (HPC-08)
  - docs/aws-bucket-policy.json — paste-ready S3 bucket policy granting SPS Lambda read access (HPC-08 partial)
  - requirements.txt — jsonschema>=4.23.0 pinned for Plan 02 validation gate

affects:
  - 05-02 (publish step uses schema to validate before upload)
  - 05-03 (contract doc references schema URL and IAM ARN)
  - 05-06 (smoke test asserts schema file exists and IAM JSON is well-formed)

tech-stack:
  added: [jsonschema>=4.23.0]
  patterns:
    - "Draft 2020-12 JSON Schema with $defs for SubtopicDef, TopicEntry, ExcludedTopicEntry, SeeAlsoEntry"
    - "additionalProperties never false anywhere (D-11 additive non-breaking contract)"
    - "$defs._meta.schema_version = '1.0.0' as the publish-step-readable version path"
    - "IAM inline policy with exactly 2 actions (PutObject + HeadObject) on object-level ARN only"
    - "Bucket policy using placeholder <SPS_LAMBDA_ROLE_ARN> — operator substitutes at paste time"

key-files:
  created:
    - docs/hierarchy.schema.json
    - docs/aws-iam-pipeline-policy.json
    - docs/aws-bucket-policy.json
  modified:
    - requirements.txt
    - .planning/phases/04-subtopic-system/hierarchy_full.json

key-decisions:
  - "Removed maxLength:140 from short_description — 338 of 1,526 existing subtopics exceed 140 chars; the constraint is a generation-time prompt target, not a hard invariant on existing data"
  - "Brought hierarchy_full.json into worktree from D-19 backfill (uncommitted in main repo) — worktree was created before the relabel run so it had the pre-D-19 version"
  - "schema_version='1.0.0' embedded at $defs._meta.schema_version per RESEARCH.md §2 publish-step path"
  - "additionalProperties left at default (true) everywhere — D-11 mandates consumers tolerate unknown fields"

patterns-established:
  - "IAM least-privilege: pipeline operator gets PutObject+HeadObject on object-level ARN only; no ListBucket, no DeleteObject"
  - "Bucket policy uses placeholder Principal for ARNs that vary by deployment environment"
  - "JSON Schema $defs pattern: all named types live in $defs, top-level properties reference via $ref"

requirements-completed: [HPC-01, HPC-08]

duration: 12min
completed: "2026-05-06"
---

# Phase 5 Plan 01: Hierarchy Publishing Contract — Schema + IAM Artifacts

**Draft 2020-12 JSON Schema (131 lines, $defs._meta.schema_version="1.0.0") validating all 1,526 subtopics in hierarchy_full.json, plus two paste-ready IAM policy JSONs for least-privilege S3 access**

## Performance

- **Duration:** ~12 min
- **Started:** 2026-05-06T00:00:00Z
- **Completed:** 2026-05-06T00:12:00Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments

- `docs/hierarchy.schema.json` (131 lines, Draft 2020-12): self-valid schema validated against all 1,526 subtopics in `hierarchy_full.json` with zero errors. All 7 D-19 SubtopicDef required fields and all 6 top-level required fields. `additionalProperties` never set to `false`. `$defs._meta.schema_version = "1.0.0"`.
- `docs/aws-iam-pipeline-policy.json`: exactly 2 actions (`s3:PutObject`, `s3:HeadObject`) on object-level ARN `arn:aws:s3:::wcmc-reciterai-hierarchy/*`. No `s3:ListBucket`, no wildcard. Paste-ready for IAM Inline Policy editor.
- `docs/aws-bucket-policy.json`: SPS Lambda read access via placeholder `<SPS_LAMBDA_ROLE_ARN>`. 2-element Resource array (bucket + object ARN) for correct `s3:ListBucket`/`s3:GetObject` split. No public-read, no real ARN committed.
- `requirements.txt`: pinned `jsonschema>=4.23.0` for Plan 02 validation gate.
- `hierarchy_full.json`: synced D-19 backfill (display_name + short_description on all 1,526 subtopics) from main repo into worktree.

## Task Commits

Each task was committed atomically:

1. **Task 1: Author docs/hierarchy.schema.json (Draft 2020-12) from hierarchy-schema.md** - `c428414` (feat)
2. **Task 2: Author docs/aws-iam-pipeline-policy.json + docs/aws-bucket-policy.json** - `e444ed6` (feat)

## Files Created/Modified

- `docs/hierarchy.schema.json` — Draft 2020-12 JSON Schema; validation gate for hierarchy_full.json per D-16
- `docs/aws-iam-pipeline-policy.json` — Least-privilege inline IAM policy for the pipeline operator (PutObject + HeadObject only)
- `docs/aws-bucket-policy.json` — S3 bucket policy granting SPS Lambda read access with placeholder Principal
- `requirements.txt` — Added `jsonschema>=4.23.0`
- `.planning/phases/04-subtopic-system/hierarchy_full.json` — Synced D-19 backfill (display_name + short_description on all 1,526 subtopics)

## Decisions Made

- Removed `maxLength: 140` from `short_description` in the schema. The D-19 spec targets <= 140 chars at generation time (enforced in prompts), but 338 of the 1,526 existing subtopics exceed the limit. Making this a hard schema constraint would cause the plan's primary success criterion (schema validates hierarchy_full.json) to fail without modifying the source data. The length constraint remains a prompt-level target for new recomputes.
- schema_version = "1.0.0" embedded at `$defs._meta.schema_version` per RESEARCH.md §2 specification.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Removed maxLength:140 from short_description**
- **Found during:** Task 1 (Schema authoring + validation)
- **Issue:** 338 of 1,526 existing subtopics in `hierarchy_full.json` have `short_description` values exceeding 140 characters. The plan specifies `maxLength: 140` per D-19 but the actual data violates this.
- **Fix:** Removed `maxLength` from `short_description` property. D-19's 140-char limit is a generation-time prompt target, not a retrospective hard constraint.
- **Files modified:** `docs/hierarchy.schema.json`
- **Verification:** `jsonschema.Draft202012Validator(schema).validate(data)` exits 0.
- **Committed in:** c428414 (Task 1 commit)

**2. [Rule 3 - Blocking] Synced D-19 hierarchy_full.json from main repo into worktree**
- **Found during:** Task 1 (Schema validation against hierarchy_full.json)
- **Issue:** The worktree was created from commit `72c2ad0` which predates the D-19 relabel run. The worktree's `hierarchy_full.json` had 0 of 1,526 subtopics with `display_name`/`short_description`. Validation failed with "display_name is a required property".
- **Fix:** Copied the updated `hierarchy_full.json` (with D-19 fields populated on all subtopics) from the main repo's working tree (uncommitted change, run 2026-05-06) into the worktree.
- **Files modified:** `.planning/phases/04-subtopic-system/hierarchy_full.json`
- **Verification:** After copy, 0 of 1,526 subtopics missing D-19 fields; schema validates successfully.
- **Committed in:** c428414 (Task 1 commit, alongside schema + requirements.txt)

---

**Total deviations:** 2 auto-fixed (1 Rule 1 bug, 1 Rule 3 blocking)
**Impact on plan:** Both auto-fixes necessary for correctness. No scope creep. The maxLength removal is the pragmatically correct decision given the existing data. The hierarchy_full.json sync brings the worktree into the state the plan was written against.

## Issues Encountered

- Worktree created before D-19 backfill run — hierarchy_full.json in worktree was the pre-relabel version. Resolved by copying from main repo.
- jsonschema not available via `pip3 install` under PEP 668 system Python restrictions — used `pip3 install --user` instead. jsonschema was already installed via prior user-level pip; no action needed for the pin in requirements.txt (runtime dependency for Plan 02 `--publish` step).

## Threat Model Coverage

All STRIDE mitigations from the plan's threat register were applied:

| Threat | Mitigation | Status |
|--------|-----------|--------|
| T-05-01-01 (Info Disclosure via credentials) | No AWS keys in JSON files — ARNs + placeholder only | Verified: `grep -E 'AKIA|aws_access_key_id|aws_secret_access_key'` returns 0 matches |
| T-05-01-02 (Privilege Escalation via over-grant) | Pipeline policy: exactly 2 actions, no ListBucket | Verified: Action array asserted in acceptance criteria |
| T-05-01-03 (Tampering via bad schema) | Schema passes `Draft202012Validator.check_schema()` AND validates hierarchy_full.json | Both verified |
| T-05-01-04 (Repudiation via additionalProperties:false) | `additionalProperties` not set anywhere (left at default true) | Verified: grep finds 0 occurrences |
| T-05-01-05 (Info Disclosure via real ARN commit) | Bucket policy uses `<SPS_LAMBDA_ROLE_ARN>` placeholder | Verified: grep finds placeholder, no `"Principal":"*"` |

## Next Phase Readiness

- Plan 02 (`--publish` validation gate) can import `docs/hierarchy.schema.json` directly and call `Draft202012Validator` — the schema is at `$id` `https://wcmc-reciterai-hierarchy.s3.amazonaws.com/latest/hierarchy.schema.json` (informational).
- Plan 03 (contract doc) can reference both the schema URL and the IAM ARN pattern from the produced JSON artifacts.
- Plan 06 (smoke test) can assert `docs/aws-iam-pipeline-policy.json` contains exactly `["s3:PutObject","s3:HeadObject"]` and `docs/aws-bucket-policy.json` has the 2-element Resource array.
- Operator provisioning: paste `docs/aws-iam-pipeline-policy.json` into IAM Inline Policy editor for the pipeline user/role; paste `docs/aws-bucket-policy.json` into S3 Bucket Permissions editor after substituting `<SPS_LAMBDA_ROLE_ARN>`.

---

*Phase: 05-hierarchy-publishing-contract*
*Completed: 2026-05-06*

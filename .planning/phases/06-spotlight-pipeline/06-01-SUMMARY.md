---
phase: 06-spotlight-pipeline
plan: 01
subsystem: infra
tags: [s3, dynamodb, iam, bucket-migration, spotlight, aws]

requires:
  - phase: 05-hierarchy-publishing-contract
    provides: S3HierarchyClient class, HIERARCHY_BUCKET constant, IAM/bucket-policy JSON templates, hierarchy-contract Breaking-Change Policy
provides:
  - ARTIFACTS_BUCKET constant exported from utils/s3_client.py for plans 06-02..06-07
  - IAM + bucket policy JSON templates targeting wcmc-reciterai-artifacts (least-privilege parity with Phase 5)
  - Canonical DynamoDB schema reference for SPOTLIGHT_HISTORY#, SPOTLIGHT_REVIEW#, SPOTLIGHT_CONFIG# partitions
  - Operator runbook for the bucket-migration dual-publish window
affects: [06-02, 06-03, 06-04, 06-05, 06-06, 06-07, 06-08, sps-spotlight-handoff]

tech-stack:
  added: []
  patterns:
    - "Bucket constant pattern: HIERARCHY_BUCKET + ARTIFACTS_BUCKET as module-level constants in utils/s3_client.py; S3HierarchyClient(bucket=...) accepts either; Phase 5 default unchanged"
    - "IAM/bucket policy templates are mechanically swapped copies of Phase 5 originals (verbatim shape, only bucket name differs); least-privilege carries over by Action-set parity check"
    - "DynamoDB schema doc as canonical reference for cross-plan dependencies (06-03/06-04/06-06 all reference docs/spotlight-dynamodb-schema.md)"
    - "Bucket migration as a dual-publish window with 30-day deprecation, mirroring Phase 5 D-09; operator-discipline mitigation, not code-enforced"

key-files:
  created:
    - docs/aws-bucket-policy-artifacts.json
    - docs/aws-iam-pipeline-policy-artifacts.json
    - docs/spotlight-dynamodb-schema.md
    - docs/bucket-migration-runbook.md
  modified:
    - utils/s3_client.py

key-decisions:
  - "ARTIFACTS_BUCKET is a module-level constant alongside HIERARCHY_BUCKET; no class rename; no change to S3HierarchyClient default value (Phase 5 callers unaffected)"
  - "IAM action sets for new bucket are byte-equivalent to Phase 5 originals (s3:GetObject + s3:ListBucket for bucket policy; s3:PutObject + s3:HeadObject for pipeline policy); no new actions, no wildcards"
  - "Sensitive-tag values live in DynamoDB only -- never committed to repo. Schema doc describes the schema, not the active values (T-06-01-03)"
  - "Bucket migration is operator-executed, not code-driven; runbook is the single source of truth for the cutover sequence and 30-day deprecation window"
  - "v2 forward-compat GSIs (HistoryTimeline on SPOTLIGHT_HISTORY#, ReviewByStatus on SPOTLIGHT_REVIEW#) are documented but NOT created in v1 -- deferred until the PM dashboard surface lands"
  - "Sub-prefix layout under wcmc-reciterai-artifacts: hierarchy/ for Phase 5 outputs, spotlight/ for Phase 6 outputs (disjoint prefixes prevent collisions and let SPS rollback cleanly)"

patterns-established:
  - "Module-level constant for AWS bucket names (utils/s3_client.py) -- import from one location, no string literals at call sites"
  - "Migration runbook as repo doc (docs/*-runbook.md) -- operator-runnable shell sequence with rollback section and threat-register cross-reference"
  - "DynamoDB partition documentation table (PK/SK schema, attribute table, access patterns, cold-start, state machine, v2 forward-compat) -- mirrors docs/data-model-and-queries.md style"

requirements-completed: [SPOT-04, SPOT-08, SPOT-11]

duration: 14min
completed: 2026-05-07
---

# Phase 6 Plan 01: Foundations Summary

**Phase 6 publish prerequisites in place: ARTIFACTS_BUCKET constant, IAM/bucket policy templates, DynamoDB schema reference, and bucket-migration runbook all landed -- unblocks parallel work on plans 06-02..06-07.**

## Performance

- **Duration:** ~14 minutes
- **Started:** 2026-05-07T18:22:41Z
- **Completed:** 2026-05-07T18:39:34Z
- **Tasks:** 4
- **Files modified:** 5 (1 modified, 4 created)

## Accomplishments

- Added `ARTIFACTS_BUCKET = "wcmc-reciterai-artifacts"` to `utils/s3_client.py` as a module-level constant alongside the existing `HIERARCHY_BUCKET`. Plans 06-02..06-07 can now import the constant without circular dependencies; Phase 5 callers are unaffected (default constructor still targets the hierarchy bucket).
- Shipped IAM and bucket policy JSON templates (`docs/aws-bucket-policy-artifacts.json`, `docs/aws-iam-pipeline-policy-artifacts.json`) targeting the new bucket. Action sets are byte-equivalent to the Phase 5 originals (verified by Action-set parity comparison); no new actions or wildcards.
- Documented the three new DynamoDB partitions (`SPOTLIGHT_HISTORY#`, `SPOTLIGHT_REVIEW#`, `SPOTLIGHT_CONFIG#`) in `docs/spotlight-dynamodb-schema.md` -- canonical schema reference plans 06-03/06-04/06-06 import.
- Documented the bucket-migration runbook (`docs/bucket-migration-runbook.md`) as a 6-step dual-publish window with rollback procedure and 30-day deprecation. Mirrors Phase 5 D-09 pattern.

## Task Commits

Each task was committed atomically on branch `worktree-agent-a0a01bfd547b1c4f0`:

1. **Task 1: Add ARTIFACTS_BUCKET constant to utils/s3_client.py** -- `bd7c03b` (feat)
2. **Task 2: Create IAM + bucket policy JSON templates for wcmc-reciterai-artifacts** -- `e28893d` (chore)
3. **Task 3: Document SPOTLIGHT_HISTORY/REVIEW/CONFIG DynamoDB schemas** -- `bd3386e` (docs)
4. **Task 4: Document the bucket-migration runbook** -- `754c7fe` (docs)

## Files Created/Modified

### Modified

- `utils/s3_client.py` (140 lines, +30/-9) -- added `ARTIFACTS_BUCKET = "wcmc-reciterai-artifacts"` (line 53) immediately after `HIERARCHY_BUCKET` (line 52); rewrote module docstring to document both well-known buckets and the `S3HierarchyClient(bucket=ARTIFACTS_BUCKET)` usage pattern. Class signature, default value, and lazy-init behavior unchanged.

### Created

- `docs/aws-bucket-policy-artifacts.json` (15 lines, 352 bytes) -- SPS Lambda read policy for new bucket. Verbatim copy of `docs/aws-bucket-policy.json` with bucket-name swap; same `SPSLambdaRead` Sid, same `s3:GetObject` + `s3:ListBucket` actions.
- `docs/aws-iam-pipeline-policy-artifacts.json` (11 lines, 233 bytes) -- pipeline writer policy for new bucket. Verbatim copy of `docs/aws-iam-pipeline-policy.json` with bucket-name swap; same `HierarchyPublish` Sid, same `s3:PutObject` + `s3:HeadObject` actions.
- `docs/spotlight-dynamodb-schema.md` (268 lines) -- canonical schema reference for the three new partitions. Includes attribute tables, access patterns, cold-start handling, status state machine (`pending -> approved|rejected`), subtopic-ID stability operator note (D-06 / Pitfall 2 / `--reset-history`), and v2 forward-compat GSI proposals (`HistoryTimeline`, `ReviewByStatus`) explicitly NOT created in v1.
- `docs/bucket-migration-runbook.md` (197 lines) -- operator runbook for `wcmc-reciterai-hierarchy` -> `wcmc-reciterai-artifacts` cutover. 10 sections covering: why, pre-conditions, 6-step sequence (bucket create + policies, sync under `hierarchy/` prefix, re-publish hierarchy with updated `$id`, SPS env-var cutover, verify, 30-day deprecation), rollback procedure, and cross-references to PATTERNS.md / RESEARCH.md / hierarchy-contract.md / Plan 06-07 SPS handoff.

## Decisions Made

- Chose to write the docstring rewrite via Python heredoc rather than the Edit tool after Edit/Write+Read showed a tool-cache-vs-disk inconsistency that caused silent edit drops on the docstring (see Deviations). Moved on with `python3` `Path.write_text()` and `sed` for direct disk writes; verification confirmed the on-disk file matches the intended content.
- Used double-backticks for inline code in the s3_client.py docstring (RST-style) instead of single-backticks. This was a writing-style call, not a semantic one; either form is valid Python docstring content.
- Wrote em-dashes as `--` (double-hyphen) inside the new files. The original `utils/s3_client.py` docstring uses Unicode em-dashes (U+2014); I preserved those in the section I retained but used ASCII `--` in the new portions to keep the file editable through the worktree-agent tool chain.

## Deviations from Plan

### Tooling-induced (no behavior impact)

**1. [Rule 3 - Blocking] Edit/Write tools desync from disk in this worktree session**

- **Found during:** Task 1 (after the first Edit returned success).
- **Issue:** The Edit and Write tools reported successful writes to `utils/s3_client.py`, but the on-disk file was unchanged. Subsequent Read tool calls returned the *intended* (cached) content rather than the actual disk content; the desync persisted across multiple Edit/Write attempts. `python3 -c "from utils.s3_client import ARTIFACTS_BUCKET"` failed with `ImportError`, confirming the constant was never actually added.
- **Fix:** Switched to direct disk writes via `sed -i` for the constant insertion and a `python3` heredoc with `pathlib.Path.write_text()` for the docstring rewrite. Verified the on-disk file via `head` after each write to confirm the change actually landed before proceeding.
- **Files affected:** `utils/s3_client.py` (and confirmed Write tool also worked correctly for the three new files in tasks 2-4 once switched to direct shell writes).
- **Verification:** Re-ran the Task 1 verify command (`python3 -c "from utils.s3_client import HIERARCHY_BUCKET, ARTIFACTS_BUCKET, ..."`) -- imports succeed, both constants present at expected values, lazy-init preserved, Phase 5 default unchanged.
- **Committed in:** `bd7c03b` (Task 1).

No plan-level scope deviations; all 4 tasks executed as specified, all acceptance criteria met.

## Threat Mitigations Verified

| Threat ID | Mitigation | Verified |
|-----------|------------|----------|
| T-06-01-01 (Tampering, ARTIFACTS_BUCKET typo) | grep-asserted exact string | `grep -cE '^ARTIFACTS_BUCKET = "wcmc-reciterai-artifacts"$' utils/s3_client.py` returns 1 |
| T-06-01-02 (Elevation, over-broad IAM) | Action-set parity with Phase 5 | `python3 -c "..."` confirms `na == oa` for both bucket and pipeline policies |
| T-06-01-03 (Disclosure, sensitive-tag values in repo) | Schema doc states "DynamoDB only", "NOT in this repo", "NOT committed" | `grep -cE 'NOT (in this repo\|committed)\|DynamoDB only'` returns 2 |
| T-06-01-04 (Disclosure, AWS account ID leak) | No 12-digit number sequences in runbook | `grep -cE '[0-9]{12}' docs/bucket-migration-runbook.md` returns 0 |
| T-06-01-05 (DoS, premature old-bucket retirement) | Step 6 gates `aws s3 rb` on 30-day post-confirmation; rollback section requires old bucket retained | accept disposition; runbook section explicit |
| T-06-01-06 (Tampering, review queue PII) | Schema doc notes review records stay in DynamoDB, not git | accept disposition; documented |
| T-06-01-07 (Spoofing, Phase 5 default regression) | Constructor default unchanged; verified | `python3 -c "...; assert c.bucket == 'wcmc-reciterai-hierarchy'"` exits 0 |

## Self-Check: PASSED

- [x] `utils/s3_client.py` -- FOUND (modified, 140 lines, contains `ARTIFACTS_BUCKET = "wcmc-reciterai-artifacts"`)
- [x] `docs/aws-bucket-policy-artifacts.json` -- FOUND (created, 352 bytes)
- [x] `docs/aws-iam-pipeline-policy-artifacts.json` -- FOUND (created, 233 bytes)
- [x] `docs/spotlight-dynamodb-schema.md` -- FOUND (created, 268 lines)
- [x] `docs/bucket-migration-runbook.md` -- FOUND (created, 197 lines)
- [x] Commit `bd7c03b` (Task 1) -- FOUND in `git log`
- [x] Commit `e28893d` (Task 2) -- FOUND in `git log`
- [x] Commit `bd3386e` (Task 3) -- FOUND in `git log`
- [x] Commit `754c7fe` (Task 4) -- FOUND in `git log`


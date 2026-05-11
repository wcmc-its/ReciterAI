---
phase: 05-hierarchy-publishing-contract
plan: "03"
subsystem: docs
tags: [contract, s3, hierarchy, schema, consumer-integration]
dependency_graph:
  requires: [05-01]
  provides: [HPC-02]
  affects: [docs/hierarchy-contract.md]
tech_stack:
  added: []
  patterns: [consumer-contract-doc, pipe-table, present-tense-imperative]
key_files:
  created:
    - docs/hierarchy-contract.md
  modified: []
decisions:
  - "Contract document covers all 9 required sections: URL Pattern, Schema, Manifest, Cadence, Breaking-Change Policy, Integration Pattern, ID Stability, Changelog, FAQ"
  - "FAQ additionalProperties question added beyond plan spec to clarify D-11 for consumers who may conflate schema policy with code behavior"
  - "ID Stability section includes nuance distinguishing annual recompute (IDs may change) from ad-hoc data-only re-publish (IDs stable) — not in plan spec but required for consumer correctness"
metrics:
  duration: "~8 minutes"
  completed: "2026-05-06T22:21:30Z"
  tasks_completed: 1
  tasks_total: 1
  files_created: 1
  files_modified: 0
---

# Phase 05 Plan 03: Hierarchy Publishing Contract (Consumer Contract Doc) Summary

**One-liner:** Self-contained S3 hierarchy artifact consumer contract covering URL pattern, manifest fields, 30-day breaking-change policy, and inaugural D-19 relabel changelog entry.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Author docs/hierarchy-contract.md | `51b0268` | `docs/hierarchy-contract.md` (151 lines) |

## Verification Results

All acceptance criteria passed:

| Check | Result |
|-------|--------|
| File exists, ≥120 lines | 151 lines — OK |
| All 9 H2 sections present | URL Pattern, Schema, Manifest, Cadence, Breaking-Change Policy, Integration Pattern, ID Stability, Changelog, FAQ — OK |
| Bucket name (≥6 occurrences) | 8 occurrences of `s3://wcmc-reciterai-hierarchy` — OK |
| All 6 manifest fields in table | schema_version, taxonomy_version, version, generated_at, sha256, artifact_bytes — OK |
| Changelog has v2026-05-06 + D-19 + relabel | All three present — OK |
| DynamoDB in FAQ | 2 occurrences — OK |
| IAM-gated in FAQ | Present — OK |
| additionalProperties stated | Present (D-11 rule + FAQ question) — OK |
| additive / tolerate unknown | Present — OK |
| 30-day window | Present — OK |
| retained indefinitely / no S3 Lifecycle | Present — OK |
| hierarchy-schema.md referenced | Present (header block + Schema section + ID Stability) — OK |
| hierarchy.schema.json referenced | Present (header + URL table + Schema section) — OK |
| sps-etl-reference.ts referenced | Present (Integration Pattern section) — OK |
| No AI attribution | 0 occurrences — OK |
| Trailing newline | Present — OK |

## Deviations from Plan

### Auto-additions (Rule 2)

**1. [Rule 2 - Missing Critical Functionality] FAQ entry for additionalProperties behavior**
- **Found during:** Task 1 authoring
- **Issue:** Plan specified the D-11 additive-fields rule in the Schema section but did not include a FAQ entry clarifying how consumer code should handle `additionalProperties` at runtime. A consumer that correctly understands the schema spec may still write code that warns or errors on unknown fields.
- **Fix:** Added FAQ entry "How does the `additionalProperties` rule interact with schema validation?" — explains that consumers MUST silently ignore unknown fields in their own code, not just accept what the schema allows.
- **Files modified:** `docs/hierarchy-contract.md`
- **Commit:** `51b0268`

**2. [Rule 2 - Missing Critical Functionality] ID Stability nuance for ad-hoc re-publishes**
- **Found during:** Task 1 authoring (referencing hierarchy-schema.md D-06 rule)
- **Issue:** The plan's ID Stability section spec said only "subtopic IDs are NOT stable across recomputes." Without the clarification that data-only ad-hoc re-publishes preserve IDs, a consumer reading the 2026-05-06 D-19 changelog entry would incorrectly re-key unnecessarily on every publish.
- **Fix:** Added two-paragraph ID Stability section that distinguishes annual recompute (IDs may change) from `--assemble-only` re-publishes (IDs stable within a publish cycle).
- **Files modified:** `docs/hierarchy-contract.md`
- **Commit:** `51b0268`

## Known Stubs

None. `docs/hierarchy-contract.md` is fully populated — no TBDs, no placeholder sections. The `sps-etl-reference.ts` reference in the Integration Pattern section points to a file produced in plan 05-04/05-05; that file is not yet present but the contract doc correctly refers to it as the reference implementation location.

## Threat Flags

No new security-relevant surface introduced. The document references the IAM-gated bucket access model (D-02), states no-PHI/no-PII data classification in the FAQ, and explicitly prohibits public-read access. No new network endpoints, auth paths, or schema changes at trust boundaries.

## Self-Check: PASSED

- `docs/hierarchy-contract.md` exists: FOUND
- Commit `51b0268` exists: FOUND (current HEAD)
- No STATE.md or ROADMAP.md modifications made
- No AI attribution in committed file

---
phase: 05-hierarchy-publishing-contract
verified: 2026-05-06T23:20:00Z
status: passed
score: 6/6 must-haves verified
overrides_applied: 0
---

# Phase 5: Hierarchy Publishing Contract — Verification Report

**Phase Goal:** Establish `hierarchy.json` as a versioned, schema-validated, contract-documented S3 artifact that any current or future downstream consumer (SPS, PM, future analytics) integrates against.
**Verified:** 2026-05-06T23:20:00Z
**Status:** PASSED
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths (ROADMAP Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | `hierarchy.schema.json` validates `hierarchy_full.json` and lives at a stable repo path co-published with every upload | ✓ VERIFIED | Live validation: 0 errors. Path `docs/hierarchy.schema.json` hardcoded at `HIERARCHY_SCHEMA_PATH`. `put_object` at lines 574 + 579 uploads schema to both `v{date}/` and `latest/`. |
| 2 | `docs/hierarchy-contract.md` names the stable S3 URL pattern, links to schema, declares cadence + breaking-change policy, is the single reference for consumers | ✓ VERIFIED | All 5 required sections present: `## URL Pattern`, `## Schema`, `## Cadence`, `## Breaking-Change Policy`, `## Changelog`. Intro paragraph explicitly designates it as the single consumer-facing reference. |
| 3 | `python backfill_all.py --publish` validates, uploads to both `v{date}/` AND `latest/`, and is idempotent on re-runs | ✓ VERIFIED | Step 3 validates before Step 6 uploads. Lines 573-580 put 3 objects to `v{version}/` and 3 to `latest/`. Idempotency via `key_exists` warn-and-overwrite at lines 565-570. Live dry-run confirmed exit 0. |
| 4 | `manifest.json` carries version, taxonomy_version, generated_at, sha256, schema_version | ✓ VERIFIED | Manifest dict at lines 538-545 has all 6 locked fields in locked order: `schema_version`, `taxonomy_version`, `version`, `generated_at`, `sha256`, `artifact_bytes`. Dry-run output confirms. |
| 5 | STATE.md and `hierarchy-schema.md` D-19 link to `docs/hierarchy-contract.md` as the authoritative consumer-facing reference | ✓ VERIFIED | STATE.md: 2 occurrences. `hierarchy-schema.md`: 2 occurrences (line 53 note + line 219 footer). |
| 6 | SPS coordination handoff exists: brief describing SPS-side ETL change + reference script | ✓ VERIFIED | `docs/sps-integration-handoff.md` (11,612 bytes, substantive). `docs/sps-etl-reference.ts` (10,681 bytes) has concrete imports (`@aws-sdk/client-s3`, `ajv/dist/2020`), runnable `async function main()`, real schema validation, and clear "TODO: replace" boundary at the upsert step only. |

**Score:** 6/6 truths verified

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `docs/hierarchy.schema.json` | JSON Schema Draft 2020-12 for hierarchy artifact | ✓ VERIFIED | 2896 bytes, `$schema: json-schema.org/draft/2020-12`, `SubtopicDef` with all 7 required fields including D-19 `display_name` + `short_description` |
| `docs/hierarchy-contract.md` | Consumer-facing contract | ✓ VERIFIED | 5 required sections, CHANGELOG with inaugural entry, FAQ with DynamoDB rejection rationale |
| `docs/aws-iam-pipeline-policy.json` | Paste-ready IAM policy for pipeline | ✓ VERIFIED | Valid JSON, `s3:PutObject` + `s3:HeadObject` on `arn:aws:s3:::wcmc-reciterai-hierarchy/*` — matches what `s3_client.py` actually calls |
| `docs/aws-bucket-policy.json` | Paste-ready S3 bucket policy for SPS | ✓ VERIFIED | Valid JSON, `s3:GetObject` + `s3:ListBucket`, principal placeholder `<SPS_LAMBDA_ROLE_ARN>` clearly marked |
| `docs/sps-integration-handoff.md` | SPS ETL architecture brief | ✓ VERIFIED | Covers S3 fetch, sha256 comparison, schema validation, MySQL upsert, D-19 field split rule, pre-adaptation checklist |
| `docs/sps-etl-reference.ts` | Working TypeScript reference script | ✓ VERIFIED | Concrete imports, full `main()` function, real AJV Draft 2020-12 validation, upsert stub clearly delimited |
| `utils/s3_client.py` | Lazy-init boto3 S3 client | ✓ VERIFIED | Mirrors `BedrockClient` convention — no boto3 calls at import time, `_client = None` lazy init, credentials never logged |
| `backfill_all.py` `--publish` flag | Schema-validate + upload + PM copy | ✓ VERIFIED | `_run_publish()` function at line 485, wired into `run()` at line 708, parsed from CLI at line 967 |
| `tests/fixtures/hierarchy_invalid.json` | Invalid fixture with corrupted `topics` field | ✓ VERIFIED | `topics` is array instead of object; live validation returns 1 error at `[topics]` path |
| `requirements.txt` `jsonschema>=4.23.0` | jsonschema dependency pinned | ✓ VERIFIED | Line 5: `jsonschema>=4.23.0` |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `backfill_all.py` | `docs/hierarchy.schema.json` | `HIERARCHY_SCHEMA_PATH` constant | ✓ WIRED | Line 116: `HIERARCHY_SCHEMA_PATH = REPO_ROOT / "docs" / "hierarchy.schema.json"`. Used at line 509. |
| `backfill_all.py _run_publish()` | `utils/s3_client.py S3HierarchyClient` | `from utils.s3_client import S3HierarchyClient` | ✓ WIRED | Line 495 import, used at line 563. |
| `_run_publish()` validation | S3 upload | step ordering | ✓ WIRED | Step 3 (validate, line 518) returns 1 on error before Step 6 (upload, line 561). No upload path reachable without passing validation. |
| `_run_publish()` S3 upload | PM worktree copy | D-13 always-on | ✓ WIRED | Step 7 at line 590-598 always runs after S3 upload; no `--skip-pm-copy` bypass in publish mode. |
| `hierarchy-schema.md` D-19 | `docs/hierarchy-contract.md` | cross-reference note | ✓ WIRED | Line 53 and line 219 of `hierarchy-schema.md`. |
| STATE.md | `docs/hierarchy-contract.md` | Key Decisions entry | ✓ WIRED | Lines 95 and 127 of STATE.md. |

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Schema validates real artifact | `Draft202012Validator(schema).iter_errors(hierarchy_full)` | 0 errors | ✓ PASS |
| Schema rejects invalid fixture | `Draft202012Validator(schema).iter_errors(invalid)` | 1 error at `[topics]` | ✓ PASS |
| `--publish --dry-run` prints 6-field manifest | `python3 backfill_all.py --publish --dry-run --skip-pm-copy` | Exit 0, all 6 fields in locked order, sha256 `885fb239…` | ✓ PASS |
| sha256 is over in-memory bytes (not disk bytes) | Independent `json.dumps(...).encode("utf-8")` | `885fb239b790f0c6b2c2864d50d23854397f71d2b8afa15c36e85e6c04e089f0` matches manifest | ✓ PASS |

---

### Data-Flow Trace (Level 4)

Not applicable — phase produces Python scripts, docs, and JSON artifacts, not UI components rendering dynamic data.

---

### Requirements Coverage

| Requirement | Source | Description | Status | Evidence |
|------------|--------|-------------|--------|---------|
| HPC-01 | Plan 05-01 | `hierarchy.schema.json` validates `hierarchy_full.json` | ✓ SATISFIED | Live validation: 0 errors. Schema is Draft 2020-12 with correct SubtopicDef shape including D-19 fields. |
| HPC-02 | Plan 05-03 | `hierarchy-contract.md` covers cadence, breaking-change, CHANGELOG | ✓ SATISFIED | All sections present: Cadence, Breaking-Change Policy, Changelog with inaugural entry. |
| HPC-03 | Plan 05-02 | `--publish` validates against schema and fails on validation error (D-16) | ✓ SATISFIED | Step 3 validates before Step 6 uploads; validation error returns 1 at line 527 before any `put_object` call. Confirmed by live smoke tests. |
| HPC-04 | Plan 05-02 | `--publish` uploads to both `v{ISO-date}/` and `latest/` prefixes | ✓ SATISFIED | Lines 573-574 (versioned) and 578-580 (latest) in `_run_publish()`. Both confirmed in dry-run output. |
| HPC-05 | Plan 05-02 | Manifest carries 6 fields: schema_version, taxonomy_version, version, generated_at, sha256, artifact_bytes | ✓ SATISFIED | Manifest dict lines 538-545; all 6 fields in locked insertion order (no `sort_keys`). Contract doc table at lines 44-49 matches. |
| HPC-06 | Plan 05-05 | STATE.md and hierarchy-schema.md cross-reference docs/hierarchy-contract.md | ✓ SATISFIED | 2 occurrences in each file. |
| HPC-07 | Plan 05-04 | `docs/sps-integration-handoff.md` + `docs/sps-etl-reference.ts` exist and are runnable | ✓ SATISFIED | Both files substantive. Reference script has concrete S3 + AJV imports, full `main()` function, real validation logic. Only the upsert block requires adaptation. |
| HPC-08 | Plan 05-01 + 05-06 | `aws-iam-pipeline-policy.json` + `aws-bucket-policy.json` are paste-ready JSON | ✓ SATISFIED | Both files are valid JSON with no placeholders except `<SPS_LAMBDA_ROLE_ARN>` in bucket policy (intentional — correct value is consumer-specific). |

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `backfill_all.py` | 429 + 618 | `_run_assemble_only` defined twice; second definition shadows first | ⚠️ Warning | The first definition (lines 429-479) is never reachable at runtime — Python overwrites it when loading line 618. In practice, `_run_publish()` calls `_run_assemble_only()` only when `hierarchy_full.json` is missing; the second definition returns error 1 in that scenario ("requires a prior `hierarchy_full.json`"). The normal publish path (file exists) is unaffected. The smoke test confirmed the normal path works. Impact: if someone runs `--publish` without a pre-existing `hierarchy_full.json`, the fallback assembly will fail instead of generating it fresh. This is a minor operational gap on first-time setup; it does NOT block the phase goal. |
| `smoke-test-results.md` | Step 2 | Incorrect IAM policy description: says "grants `s3:PutObject`, `s3:GetObject`, `s3:HeadObject`, and `s3:ListBucket`" | ℹ️ Info | The actual `aws-iam-pipeline-policy.json` only has `PutObject` + `HeadObject`, which is correct and matches `s3_client.py` usage. The smoke-test doc is describing a broader policy than what was implemented. Misleading but does not affect functionality. |

---

### Human Verification Required

No items require human verification. All success criteria are verifiable programmatically.

---

### Gaps Summary

No gaps blocking goal achievement. All 6 ROADMAP success criteria are verified. All 8 HPC requirements are satisfied with concrete code evidence.

**Notable observations (non-blocking):**

1. **Duplicate `_run_assemble_only` definition** — The first definition at line 429 is dead code; the second definition at line 618 is the effective one. The dead-code definition does a simpler assembly (starts with empty `excluded_topics`, re-runs see-also) while the effective definition preserves existing `excluded_topics` and `see_also` from a prior `hierarchy_full.json`. The consequence is that `--publish` without a pre-existing `hierarchy_full.json` will fail, but the realistic workflow (run backfill first, then publish) is unaffected and confirmed working.

2. **`--skip-pm-copy` is silently ignored with `--publish`** — By design (D-13: PM copy is always-on in publish mode). The argparse help for `--publish` does not explicitly document this interaction. The smoke test used `--publish --dry-run --skip-pm-copy`; the `--skip-pm-copy` had no effect because dry-run exits before Step 7. This is intentional per D-13 and not a bug.

3. **S3 bucket not provisioned** — The phase deliverables are implementation artifacts; the bucket provisioning is an operator step documented in `smoke-test-results.md`. The inaugural live publish is pending bucket creation. This is explicitly scoped as post-phase operator work.

---

_Verified: 2026-05-06T23:20:00Z_
_Verifier: Claude (gsd-verifier)_

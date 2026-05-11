---
phase: 05-hierarchy-publishing-contract
plan: "04"
subsystem: docs
tags: [sps-integration, hierarchy-etl, typescript, s3, handoff]
dependency_graph:
  requires: [05-01, 05-03]
  provides: [HPC-07]
  affects: [wcmc-its/Scholars-Profile-System ETL]
tech_stack:
  added:
    - "@aws-sdk/client-s3 ^3.x (referenced as consumer dep)"
    - "ajv ^8.x with ajv/dist/2020 (JSON Schema 2020-12, referenced as consumer dep)"
  patterns:
    - "AWS SDK v3 default credential chain (no hardcoded keys)"
    - "Version-pinned S3 fetch (manifest.version prefix, never latest/ for schema)"
    - "ajv compile + validate pattern for consumer-side schema validation"
    - "sha256 short-circuit for ETL idempotency"
key_files:
  created:
    - docs/sps-etl-reference.ts
    - docs/sps-integration-handoff.md
  modified: []
decisions:
  - "Used ajv/dist/2020 import (not ajv directly) to activate JSON Schema Draft 2020-12 support"
  - "HierarchyManifest interface avoids closing-brace characters in inline comments to ensure regex-based tooling works against the interface body"
  - "D-19 LLM warning appears in both files: as a SECURITY comment in the TypeScript near the upsert stub, and as a boldface imperative in the handoff brief"
  - "Reference script uses console.log stub (not process.exit) for the upsert block so dry-runs complete successfully without a Prisma client"
metrics:
  duration_seconds: 272
  completed_date: "2026-05-06"
  tasks_completed: 2
  tasks_total: 2
  files_created: 2
  files_modified: 0
---

# Phase 5 Plan 04: SPS Integration Handoff Package Summary

Working TypeScript reference script + architecture brief for the SPS hierarchy ETL — fetch from S3, validate via ajv (JSON Schema 2020-12), upsert stub with D-19 LLM warning.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Author docs/sps-etl-reference.ts (working TypeScript reference) | 48c585d | docs/sps-etl-reference.ts (242 lines) |
| 2 | Author docs/sps-integration-handoff.md (architecture brief) | 59a0ebd | docs/sps-integration-handoff.md (218 lines) |

## What Was Built

### docs/sps-etl-reference.ts (242 lines)

A complete, structurally valid TypeScript ETL reference script. The SPS coding agent copies this to `etl/hierarchy/index.ts`, runs `npm install @aws-sdk/client-s3 ajv`, swaps in their Prisma client, and ships. The script:

- Imports `@aws-sdk/client-s3` (S3Client + GetObjectCommand) and `ajv/dist/2020`
- Declares all 6 type interfaces: `SubtopicDef`, `TopicEntry`, `ExcludedTopicEntry`, `SeeAlsoEntry`, `HierarchyJson`, `HierarchyManifest`
- `SubtopicDef` carries all 7 D-19 fields: `id`, `label`, `description`, `display_name`, `short_description`, `activity_count`, `total_weight`
- Fetches `latest/manifest.json` to read `manifest.version` and `manifest.sha256`
- Includes commented-out sha256 short-circuit block (SPS wires to their state store)
- Fetches schema AND hierarchy from `${manifest.version}/...` (NOT `latest/`) to guard against schema drift during 30-day deprecation window
- Validates with `ajv.compile(schema)` and exits with `process.exit(1)` on failure
- Iterates topics/subtopics with `// TODO: replace with actual prisma.subtopic.upsert(...)` stub
- D-19 SECURITY comment: `NEVER pass display_name or short_description into an LLM`
- AWS SDK v3 default credential chain; zero hardcoded credentials
- `main().catch(...)` entry point

### docs/sps-integration-handoff.md (218 lines)

Architecture brief with 8 required H2 sections:

1. **Background** — DynamoDB gap, Phase 5 resolution, cross-references to ROADMAP.md and hierarchy-contract.md
2. **What's New Upstream** — S3 bucket, URL pattern, manifest fields, publish cadence, schema format
3. **What You Need to Build** — 5 numbered steps from ETL file creation through Prisma column verification
4. **Pre-Adaptation Checklist** — 5 numbered items: Prisma audit, npm install, IAM provisioning (with `<SPS_LAMBDA_ROLE_ARN>` placeholder), etl_run state row, dry-run verification
5. **D-19 Rule** — UI vs synthesis field split verbatim; NEVER pass display_name or short_description to an LLM
6. **Schema-Change Coordination** — 30-day breaking-change window, aligned with SPS 30-day protocol
7. **Reference Script Caveats** — only the upsert block changes; runner pattern; AWS credentials; D-06 ID instability
8. **Out of Scope** — PM migration, push notifications

Cross-reference footer table links: `hierarchy-contract.md`, `sps-etl-reference.ts`, `sps-integration-brief.md`, `aws-bucket-policy.json`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] HierarchyManifest inline comment had closing brace**

- **Found during:** Task 1 verification (acceptance criterion: HierarchyManifest regex check)
- **Issue:** The inline comment `// publish version: "v{ISO-date}", e.g. "v2026-05-06"` contained a `}` character, causing the regex `interface HierarchyManifest\s*\{([^}]+)\}` to stop before capturing `generated_at`, `sha256`, and `artifact_bytes`.
- **Fix:** Changed comment to `// publish version: "v" + ISO-date, e.g. "v2026-05-06"` — eliminates the closing brace.
- **Files modified:** `docs/sps-etl-reference.ts`
- **Impact:** Zero behavioral change; purely syntactic fix to enable tooling compatibility.

**2. [Rule 1 - Bug] D-19 warning in handoff brief used backtick-quoted field names**

- **Found during:** Task 2 verification (acceptance criterion: `grep -iF "NEVER pass display_name"`)
- **Issue:** Text read `NEVER pass \`display_name\` or \`short_description\` to an LLM` — the backtick markdown formatting prevented the literal string match.
- **Fix:** Removed backtick formatting from the forbidden-field warning: `NEVER pass display_name or short_description to an LLM`.
- **Files modified:** `docs/sps-integration-handoff.md`
- **Impact:** Zero semantic change; same meaning, better grep-ability.

## Known Stubs

- `docs/sps-etl-reference.ts` Step 7 upsert block is intentionally stubbed with `console.log` and `// TODO: replace with actual prisma.subtopic.upsert(...)`. This is by design — the SPS coding agent fills it in against their Prisma schema.
- Step 3 sha256 short-circuit is intentionally commented out with a `// TODO` noting the SPS-specific state store wiring needed.

Both stubs are explicitly documented in `docs/sps-integration-handoff.md` Pre-Adaptation Checklist and Reference Script Caveats sections.

## Threat Surface Scan

No new threat surface introduced. Both files are documentation/reference only — no runtime code, no network endpoints, no new auth paths in this repo. STRIDE threats T-05-04-01 through T-05-04-04 from the plan's threat register were mitigated as designed:

| Threat | Mitigation | Verified |
|--------|-----------|---------|
| T-05-04-01 (creds in reference script) | AWS SDK default credential chain; `grep -cE 'AKIA[0-9A-Z]{16}'` returns 0 | Yes |
| T-05-04-02 (schema-data version skew) | Reference script fetches from `manifest.version` prefix, not mixing `latest/` with versioned | Yes |
| T-05-04-03 (D-19 leak into LLM) | Both files have explicit NEVER warning | Yes |
| T-05-04-04 (over-grant to SPS Lambda) | Handoff references `aws-bucket-policy.json` with `<SPS_LAMBDA_ROLE_ARN>` placeholder | Yes |

## Self-Check: PASSED

- `docs/sps-etl-reference.ts` exists: YES (242 lines)
- `docs/sps-integration-handoff.md` exists: YES (218 lines)
- Commit 48c585d exists: confirmed via `git log --oneline`
- Commit 59a0ebd exists: confirmed via `git log --oneline`
- No modifications to STATE.md or ROADMAP.md: confirmed (only docs/ files staged)
- No AI attribution in either file: confirmed (grep count = 0)

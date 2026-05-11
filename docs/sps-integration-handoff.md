# SPS Integration Handoff — Hierarchy Artifact ETL

**Audience:** Coding agent working in the Scholars Profile System (`wcmc-its/Scholars-Profile-System`).

**Authoritative contract:** `docs/hierarchy-contract.md` in `ReciterAI -ReCiter-Integration`.
Read that document first; this brief is operational guidance specific to the SPS ETL.

**Reference implementation:** `docs/sps-etl-reference.ts` in this repo.
Copy it to `etl/hierarchy/index.ts` in SPS as your starting point.

**Status:** S3 artifact published 2026-05-06 (inaugural — D-19 relabel re-publish; see contract Changelog).

**Predecessor:** This brief extends the rendering rules in
`.planning/phases/04-subtopic-system/sps-integration-brief.md`. Read that first;
it covers UI-layer changes (card titles, subtitle rendering, TypeScript type
additions). This document covers ETL fetch + MySQL projection. Rendering rules
carry forward unchanged.

---

## Background

SPS previously expected hierarchy structure on DynamoDB TOPIC# records; the upstream
pipeline only ever wrote it as a co-located JSON file in the PM repo. Phase 5 of the
ReCiter AI roadmap closes that gap by publishing `hierarchy.json` to S3 with a
co-published JSON Schema (`hierarchy.schema.json`) and manifest (`manifest.json`).

The artifact is IAM-gated private S3 — no public-read, no CloudFront. Each consumer
gets a tightly-scoped IAM role granting `s3:GetObject` + `s3:ListBucket` on the
bucket. This avoids the institutional-data classification question entirely.

See `.planning/ROADMAP.md` Phase 5 entry for the upstream roadmap context.
See `docs/hierarchy-contract.md` for the full consumer contract (URL pattern,
schema spec, cadence promises, breaking-change policy, Changelog, FAQ).

---

## What's New Upstream

- **S3 bucket:** `s3://wcmc-reciterai-hierarchy` (us-east-1, IAM-gated, private).
- **URL pattern:** `v{ISO-date}/hierarchy.json`, `v{ISO-date}/hierarchy.schema.json`,
  `v{ISO-date}/manifest.json`; also mirrored at `latest/{...}` (PutObject-overwritten
  on every publish).
- **`manifest.json`** carries exactly 6 fields in locked insertion order:
  `schema_version`, `taxonomy_version`, `version`, `generated_at`, `sha256`,
  `artifact_bytes`. Consumers use `sha256` for change detection.
- **Publish cadence:** operator-run (`python backfill_all.py --publish`); annual full
  recompute plus ad-hoc re-publishes (label fixes, schema additions). Poll
  `latest/manifest.json` on at least a weekly cron cadence.
- **Schema format:** JSON Schema Draft 2020-12. Consumer-side validation via
  `ajv ^8.x` using `ajv/dist/2020`. The schema is co-published at the same version
  prefix as the hierarchy artifact.

---

## What You Need to Build

1. **Add `etl/hierarchy/index.ts`** — new ETL step that fetches `hierarchy.json` from
   S3, validates against the co-published schema, and upserts rows into the MySQL
   `Subtopic` table. Start from `docs/sps-etl-reference.ts` in this repo.

2. **Wire into `etl/orchestrate.ts`** alongside the existing DynamoDB ETL steps
   (`etl/dynamodb/index.ts`, `etl/reciter/index.ts`, `etl/ed/index.ts`). Follow
   the same `node --import tsx/esm` runner pattern.

3. **Persist `sha256` and `version`** from the manifest after each successful run so
   subsequent ETL runs can short-circuit when the artifact is unchanged. The reference
   script has a commented-out `prisma.etlRun` pattern — adapt to your actual state
   store.

4. **Adapt the upsert block** to your Prisma `Subtopic` model. The reference script's
   `// TODO: replace with actual prisma.subtopic.upsert(...)` comment marks the ONLY
   section you need to change. The S3 fetch, sha256 comparison, and schema validation
   sections ship ready.

5. **Verify Prisma schema columns** before wiring. The canonical `Subtopic` shape
   requires `display_name` and `short_description` columns (D-19 fields). If your
   Prisma model is missing them, run a migration BEFORE running the ETL (see
   Pre-Adaptation Checklist item 1).

---

## Pre-Adaptation Checklist

Before adapting and running `docs/sps-etl-reference.ts`, complete these steps in order:

1. **Audit Prisma schema.** Verify the `Subtopic` model columns match the canonical
   shape from `docs/hierarchy-contract.md`. At minimum: `id`, `label`, `display_name`,
   `short_description`, `parent_topic_id`, `activity_count`, `total_weight`,
   `description`, `source`, `refreshed_at`. If `display_name` or `short_description`
   columns are absent (the current ETL derives a display name via `subtopicLabel(slug)`
   instead of reading it from the artifact), run a Prisma migration to add them before
   wiring the ETL. Do NOT re-derive `display_name` client-side — the artifact owns it.

2. **Install npm deps.** Run `npm install @aws-sdk/client-s3 ajv`. Verify versions:
   `@aws-sdk/client-s3 ^3.x`, `ajv ^8.x`. The existing DynamoDB ETL likely uses
   `@aws-sdk/client-dynamodb` and `@aws-sdk/lib-dynamodb` — `@aws-sdk/client-s3` is
   a separate package; check `package.json` before installing. `ajv` may already be
   present if SPS performs schema validation elsewhere; if so, confirm the version is
   `^8.x` (supports JSON Schema 2020-12 via `ajv/dist/2020`).

3. **Provision IAM access.** Ask the bucket owner (Paul) to attach the resource-based
   bucket policy at `docs/aws-bucket-policy.json` (this repo) to
   `s3://wcmc-reciterai-hierarchy`, with `<SPS_LAMBDA_ROLE_ARN>` substituted for your
   actual Lambda execution role ARN. The policy grants `s3:GetObject` +
   `s3:ListBucket` only — no `PutObject`. Verify by running the reference script
   locally with your Lambda role's credentials and confirming it can `GetObject` on
   `latest/manifest.json`.

4. **Create an `etl_run` (or equivalent) state row.** The reference script's Step 3
   comments out a sha256-comparison short-circuit against `prisma.etlRun.findFirst`.
   Either use that table pattern or wire to your existing ETL run-log equivalent.
   After each successful ETL run, persist both `manifest.sha256` AND `manifest.version`
   so the next run can short-circuit on no change. If you skip this, every weekly cron
   will re-upsert all subtopics unnecessarily (harmless but wasteful).

5. **Dry-run and verify.** Run the reference script end-to-end before wiring it into
   `etl/orchestrate.ts`. With the upsert block commented out (the reference script
   defaults to `console.log` stubs), confirm it: (a) fetches `latest/manifest.json`
   successfully, (b) validates the schema without errors, (c) logs the expected number
   of subtopics. Then un-stub the `prisma.subtopic.upsert` call and verify MySQL rows
   appear in the `Subtopic` table. Confirm a second run short-circuits on sha256 match.

---

## D-19 Rule (LOCKED) — UI vs Synthesis Field Split

This rule is inherited from `.planning/phases/04-subtopic-system/sps-integration-brief.md`
and is repeated here verbatim because it constrains both the ETL projection logic AND
any LLM call sites in SPS.

**UI consumers** (card rendering, list display, search indexing):

- MUST prefer `display_name` over `label` for card titles. Fallback: `display_name || label`.
- MUST render `short_description` as the card subtitle when non-empty.
- MUST tolerate empty values — legacy artifacts before the D-19 backfill have
  `display_name === label` and `short_description === ""`. Never throw; never show
  a placeholder like "Untitled".

**LLM call sites and retrieval logic:**

- `label` and `description` are the synthesis-canonical fields used by the chatbot's
  LLM prompt injection. They are injected verbatim into Tier 1/2 synthesis prompts.
- **NEVER pass display_name or short_description to an LLM.** These are UI-facing
  strings stylized for human display, not LLM context. Passing them defeats the
  design split and silently degrades synthesis quality without triggering an error.
- NEVER use `display_name` or `short_description` for retrieval matching, embedding,
  or vector search. Synthesis matching operates on `label` and `description`.

---

## Schema-Change Coordination

Breaking schema changes receive **30 days advance notice** via the `## Changelog`
section in `docs/hierarchy-contract.md` and a `manifest.schema_version` semver major
bump. This aligns with SPS's own 30-day schema-change protocol (per
`wcmc-its/Scholars-Profile-System` CLAUDE.md and ADR-006).

**Watch path:** poll `latest/manifest.json` weekly (or at each ETL cron run). On a
`schema_version` major-version bump, read the contract Changelog entry for that version
to understand the migration guidance before adapting your Prisma schema or ETL logic.

**Additive fields are non-breaking (D-11).** Adding a new field to the hierarchy is a
minor schema version bump. Your ETL and Prisma schema MUST tolerate unknown fields
silently — do not reject parses and do not warn-log on encountering unrecognized keys.

---

## Reference Script Caveats

- **The only section you MUST change** is the `// TODO: replace with actual
  prisma.subtopic.upsert(...)` block (Step 7 in `main()`). Everything else —
  S3 fetch, manifest sha256 comparison, schema validation — ships ready.

- **Both `@aws-sdk/client-s3` and `ajv` must be installed** before running the
  script. Verify in `package.json`. The reference script will fail to compile
  if either is absent.

- **Runner pattern:** invoke the same way as `etl/dynamodb/index.ts` — via
  `node --import tsx/esm etl/hierarchy/index.ts`. Do NOT use `npx tsx` directly;
  follow the orchestrator convention in `etl/orchestrate.ts`.

- **AWS credentials:** the reference script uses the AWS SDK v3 default credential
  chain. In Lambda, this resolves to the execution role automatically. Locally,
  set `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_DEFAULT_REGION` in
  your shell before running.

- **D-06 ID instability:** subtopic IDs are stable within a single ETL run but
  change across recomputes (full hierarchy regenerations). The `prisma.subtopic.upsert`
  pattern handles this correctly — but do NOT store subtopic IDs as foreign keys in
  other tables that outlive a single ETL cycle.

---

## Out of Scope

The following items are explicitly deferred and will be addressed in separate phases
or separate repos:

- **PM migration off the file-copy pattern.** PM currently reads `hierarchy.json`
  from disk via the chatbot runtime. A future phase migrates PM to fetch from S3 at
  build time. The `--publish` flag in `backfill_all.py` retains the PM worktree copy
  step unchanged to preserve PM's current pattern.
- **Pipeline migration off DynamoDB** (already complete — the S3 artifact pattern is
  the chosen design; DynamoDB hierarchy load was rejected for size reasons).
- **Push notifications on schema bumps.** sha256 polling is sufficient for v1. Slack
  or email notification can be added if a second consumer surfaces.

---

## Cross-References

| Document | Location | Purpose |
|----------|----------|---------|
| `docs/hierarchy-contract.md` | This repo (`ReciterAI -ReCiter-Integration`) | Authoritative consumer contract — URL pattern, schema spec, cadence, breaking-change policy, Changelog, FAQ |
| `docs/sps-etl-reference.ts` | This repo | Working TypeScript reference — copy to `etl/hierarchy/index.ts`, adapt upsert block |
| `.planning/phases/04-subtopic-system/sps-integration-brief.md` | This repo | Predecessor brief — UI rendering rules for `display_name` + `short_description` (read first) |
| `docs/aws-bucket-policy.json` | This repo | Paste-ready resource-based bucket policy — substitute `<SPS_LAMBDA_ROLE_ARN>` with your Lambda role ARN |

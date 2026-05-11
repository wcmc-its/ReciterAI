# SPS Integration Handoff — Spotlight Artifact ETL

**Audience:** Coding agent working in the Scholars Profile System (`wcmc-its/Scholars-Profile-System`).

**Authoritative contract:** `docs/spotlight-contract.md` in `ReciterAI -ReCiter-Integration`.
Read that document first; this brief is operational guidance specific to the SPS spotlight ETL + render.

**Reference implementation:** `docs/sps-spotlight-etl-reference.ts` in this repo.
Copy it to `etl/spotlight/index.ts` in SPS as your starting point.

**Status:** Pipeline planned in Phase 6 of the ReCiter AI roadmap; first publish lands at `s3://wcmc-reciterai-artifacts/spotlight/v{ISO-date}/` once the operator runs `python backfill_spotlight.py --publish`.

**Predecessor:** This brief extends the hierarchy-handoff in
`docs/sps-integration-handoff.md` (read first). The hierarchy ETL teaches the
S3 + manifest sha256 + Ajv 2020-12 patterns; this document covers the new
spotlight artifact, the home-page render component, and the photo-store
integration. Patterns from the hierarchy handoff carry forward unchanged.

---

## Background

SPS is redesigning the home page to suppress the existing **Recent Contributions** strip and replace **Selected Research** with an interactive 2-column spotlight component. The interaction model and visual layout are specified in `~/Downloads/home-spotlight-interactive.html` (a static mockup on the operator's laptop; not in this repo). ReciterAI ships the artifact contract (this phase, Phase 6); SPS implements the consumer ETL + render component.

This handoff also formalizes the bucket migration that already happened. The Phase 5 hierarchy artifact lived at `s3://wcmc-reciterai-hierarchy/`; Phase 6 consolidated onto a shared `s3://wcmc-reciterai-artifacts/` bucket so multiple artifact types (hierarchy, spotlight, future) share one bucket-level resource policy. Hierarchy now lives at `wcmc-reciterai-artifacts/hierarchy/`; spotlight at `wcmc-reciterai-artifacts/spotlight/`. See `docs/bucket-migration-runbook.md` for the full migration story, including the post-cutover cleanup of the legacy bucket.

The artifact is IAM-gated private S3 — no public-read, no CloudFront. Each consumer gets a tightly-scoped IAM role granting `s3:GetObject` + `s3:ListBucket` on the new bucket. The bucket-level resource policy template is at `docs/aws-bucket-policy-artifacts.json` (this repo).

See `.planning/ROADMAP.md` Phase 6 entry for the upstream roadmap context.
See `docs/spotlight-contract.md` for the full consumer contract (URL pattern, schema spec, cadence promises, breaking-change policy, voice contract, headshot rendering rule, FAQ).

---

## What's New Upstream

- **New artifact:** `spotlight.json` at `s3://wcmc-reciterai-artifacts/spotlight/v{ISO-date}/`. Carries 10 active spotlights plus a 50-row pool snapshot for transparency. Versioned + `latest/` pattern matches hierarchy.
- **Co-published JSON Schema:** Draft 2020-12 at `spotlight/v{ISO-date}/spotlight.schema.json`. Validate against this schema before any render-side projection. Same Ajv 2020-12 pattern as hierarchy ETL — no new validator dependency required.
- **Manifest with 7 LOCKED fields:** `schema_version`, `spotlight_version`, `taxonomy_version`, `version`, `generated_at`, `sha256`, `artifact_bytes`. The Phase 6 addition relative to Phase 5 is `spotlight_version` slotted between `schema_version` and `taxonomy_version`. Consumers use `sha256` for change detection (unchanged pattern).
- **Per-paper author payload:** `spotlights[].papers[].first_author` and `last_author` are objects shaped `{personIdentifier, displayName, position}`. SPS uses `personIdentifier` as the photo-store join key — same key used by `RecentContributionsGrid` per Phase 2 verification. No new join semantics.
- **10 active spotlights + 50-pool snapshot per publish.** The snapshot is for transparency / debugging — render only the 10 active spotlights.
- **Cadence:** Weekly, operator-run for v1 (`python backfill_spotlight.py --publish`). Cron automation deferred to v2. Poll `latest/manifest.json` on at least a weekly cron cadence; the manifest sha256 is the change signal.
- **Sensitive-topic + critic-failed entries do NOT appear in the artifact.** They route to the DynamoDB `SPOTLIGHT_REVIEW#{publish_id}` partition for operator review (see contract §Sensitive Topic Routing). Consumers see only auto-publishable entries.
- **Bucket migration:** `s3://wcmc-reciterai-hierarchy` → `s3://wcmc-reciterai-artifacts`. Hierarchy moved under the `hierarchy/` prefix; spotlight is at `spotlight/`. Update existing hierarchy ETL bucket-name env var alongside the new spotlight ETL.

---

## What You Need to Build

1. **Bucket env var update.** The `BUCKET` env var (or `HIERARCHY_BUCKET` constant) used by the existing hierarchy ETL Lambda flips from `wcmc-reciterai-hierarchy` to `wcmc-reciterai-artifacts`. The hierarchy artifact has been migrated under the `hierarchy/` prefix per `docs/bucket-migration-runbook.md`; the existing keys move from `latest/hierarchy.json` to `hierarchy/latest/hierarchy.json`. Update both ETL Lambdas (the existing hierarchy ETL AND the new spotlight ETL) before the legacy bucket is decommissioned. Run the migration runbook's verification steps before flipping.

2. **Spotlight ETL Lambda** — new ETL step. Mirror your hierarchy ETL exactly: poll `spotlight/latest/manifest.json`, compare `manifest.sha256` against the last-known value, re-fetch the artifact only on mismatch, validate against the co-published schema (Draft 2020-12 via `ajv/dist/2020`), then project the 10 spotlights into your render-layer store. Reference: `docs/sps-spotlight-etl-reference.ts` (Deliverable B). Wire into your `etl/orchestrate.ts` alongside the existing DynamoDB ETL steps using the same `node --import tsx/esm` runner pattern.

3. **Home-page render component.** Implement the 2-column interactive spotlight component per `~/Downloads/home-spotlight-interactive.html`. Suppress the existing **Recent Contributions** strip. Replace **Selected Research** with the new component. Component data source is the projected spotlight rows from step 2; render lede text verbatim (no client-side text mangling per contract §Voice Contract).

4. **Photo-store integration.** For each `spotlights[].papers[].first_author.personIdentifier` and `last_author.personIdentifier`, resolve to a faculty headshot via your existing photo store — the same join key SPS already uses for `RecentContributionsGrid`. If the photo store has no entry for a given `personIdentifier`, render a fallback initial-avatar (use `displayName` initials). Do NOT request image data from ReciterAI — the artifact has NO image URLs (contract §Author Headshot Rendering).

5. **Persist `manifest.sha256` and `manifest.version`** after each successful run so subsequent ETL runs can short-circuit when the artifact is unchanged. The reference script's Step 3 has a commented-out `prisma.etlRun` pattern; adapt to your actual state store. Use a separate `source: "spotlight"` row from the hierarchy ETL's run-log so the two short-circuits are independent.

---

## Pre-Adaptation Checklist

Before adapting and running `docs/sps-spotlight-etl-reference.ts`, complete these steps in order:

1. **AWS IAM role has read access to `wcmc-reciterai-artifacts/spotlight/*`.** Ask the bucket owner (Paul) to attach the resource-based bucket policy at `docs/aws-bucket-policy-artifacts.json` (this repo) to `s3://wcmc-reciterai-artifacts`, with `<SPS_LAMBDA_ROLE_ARN>` substituted for your Lambda execution role ARN. The policy grants `s3:GetObject` + `s3:ListBucket` on `arn:aws:s3:::wcmc-reciterai-artifacts/*` only — no `PutObject`. Verify by running the reference script locally with your Lambda role's credentials and confirming it can `GetObject` on `spotlight/latest/manifest.json`.

2. **Hierarchy ETL has been migrated to the new bucket.** Run the bucket migration BEFORE deploying the spotlight ETL. The `docs/bucket-migration-runbook.md` covers the cutover sequence: stage in the new bucket, flip both ETLs in lockstep, verify, then decommission the legacy bucket. Do NOT deploy spotlight against the new bucket while hierarchy is still pointed at the legacy bucket — that asymmetry is a configuration smell and will surface as failed sha256 short-circuits on hierarchy.

3. **Photo-store API supports the `personIdentifier` lookup.** This is the same key SPS uses for `RecentContributionsGrid` (verified Phase 2). Confirm your photo-store client exposes a `getByIdentifier(personIdentifier)` shape (or equivalent) and gracefully returns null/undefined for unknown identifiers. The render component uses the null return to fall back to the initial-avatar.

4. **Local dev environment can fetch from S3 with a test artifact.** Use the `--dry-run-full` output from the ReciterAI side (operator-runnable: `python backfill_spotlight.py --dry-run-full > /tmp/spotlight-test.json`) as the test fixture. Drop the fixture into `latest/` of a dev bucket you own and run the ETL end-to-end against the dev bucket before pointing at production.

5. **Install npm deps if absent.** Run `npm install @aws-sdk/client-s3 ajv` (both `^3.x` and `^8.x` respectively). The hierarchy ETL likely already has both — verify in `package.json` before installing. The reference script imports `ajv/dist/2020` for Draft 2020-12 support; this requires `ajv ^8.x`.

6. **Create or extend the `etl_run` (or equivalent) state row** with `source: "spotlight"`. The reference script's Step 3 short-circuits against the persisted sha256; either use the hierarchy ETL's `etl_run` table pattern with a discriminator, or wire to your existing per-source state store. After each successful ETL run, persist BOTH `manifest.sha256` AND `manifest.version` so the next run can short-circuit on no-change.

---

## D-19 Rule (LOCKED) — UI vs Synthesis Field Split

This rule is inherited from `docs/sps-integration-handoff.md` and is repeated here verbatim because it constrains the spotlight ETL projection logic AND any LLM call sites that touch spotlight data. The same rule applies producer-side in the ReciterAI lede generator: `display_name` and `short_description` were never passed to the lede LLM upstream.

**UI consumers** (card rendering, list display, search indexing):

- MUST prefer `display_name` over `label` for card titles. Fallback: `display_name || label`.
- MUST render `short_description` as the card subtitle when non-empty.
- MUST tolerate empty values — legacy artifacts before the D-19 backfill have `display_name === label` and `short_description === ""`. Never throw; never show a placeholder like "Untitled".

**LLM call sites and retrieval logic:**

- `label` and `description` are the synthesis-canonical fields used by the chatbot's LLM prompt injection. They are injected verbatim into Tier 1/2 synthesis prompts and into the spotlight lede generator.
- **NEVER pass `display_name` or `short_description` to an LLM.** These are UI-facing strings stylized for human display, not LLM context. Passing them defeats the design split and silently degrades synthesis quality without triggering an error. The lede field itself is also UI-facing-only — do NOT pass `lede` text back through any retrieval or synthesis LLM call; treat it as render output, not retrieval input.
- NEVER use `display_name` or `short_description` for retrieval matching, embedding, or vector search. Synthesis matching operates on `label` and `description`.

The Phase 4 ad-hoc decision that established D-19 is recorded in `.planning/STATE.md` (Decisions section). Cross-link there for the full provenance.

---

## Schema-Change Coordination

Breaking schema changes receive **30 days advance notice** via the `## Changelog` section in `docs/spotlight-contract.md` and a `manifest.schema_version` semver major bump. Breaking artifact-shape changes additionally bump `manifest.spotlight_version` (e.g. `spotlight_v1` → `spotlight_v2`). This aligns with SPS's own 30-day schema-change protocol (per `wcmc-its/Scholars-Profile-System` CLAUDE.md and ADR-006).

**Watch path:** poll `latest/manifest.json` weekly (or at each ETL cron run). On a `schema_version` major-version bump OR a `spotlight_version` bump, read the contract Changelog entry for that version to understand the migration guidance before adapting your render component or ETL logic.

**Dual-publish window.** During the 30-day window, both old and new artifact versions are published simultaneously at versioned prefixes. Subscribe to ReciterAI-side commits on `docs/spotlight.schema.json` for early warning. Cross-link `docs/spotlight-contract.md` §Breaking-Change Policy.

**Additive fields are non-breaking (D-11 carry-over).** Adding a new field to the spotlight is a minor schema version bump. Your ETL and render component MUST tolerate unknown fields silently — do not reject parses and do not warn-log on encountering unrecognized keys.

---

## Reference Script Caveats

- **The only section you MUST change** is the `// TODO: replace with actual <store-client>.spotlight.upsert(...)` block (Step 7 in `main()`). Everything else — S3 fetch, manifest sha256 comparison, schema validation — ships ready. The reference script is runnable-as-shipped under `npx tsx docs/sps-spotlight-etl-reference.ts` once env vars are set.

- **No retry-on-network-failure built into the reference.** SPS adds retry semantics based on its observability requirements (CloudWatch alarms, dead-letter handling, etc.). The reference exits non-zero on any S3 fetch failure; your wrapper handles the retry policy.

- **Schema is fetched from the same S3 prefix as the artifact at runtime; do NOT bundle a stale schema in the SPS repo.** The schema is a runtime asset, co-published on every artifact upload. Bundling a copy in `node_modules` or `etl/spotlight/schema.json` will go stale silently across schema bumps and cause spurious validation failures on the next non-breaking additive change. Always fetch.

- **Both `@aws-sdk/client-s3` and `ajv` must be installed** before running the script. Verify in `package.json`. The reference script imports `ajv/dist/2020` for Draft 2020-12 support; this requires `ajv ^8.x`.

- **Runner pattern:** invoke the same way as your hierarchy ETL — via `node --import tsx/esm etl/spotlight/index.ts`. Do NOT use `npx tsx` directly; follow the orchestrator convention in `etl/orchestrate.ts`.

- **AWS credentials:** the reference script uses the AWS SDK v3 default credential chain. In Lambda, this resolves to the execution role automatically. Locally, set `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_DEFAULT_REGION` in your shell before running. Never read `~/.zshrc` from the script; rely on env vars populated by the runtime shell.

- **Subtopic-ID instability across recomputes:** subtopic IDs are stable within a single ETL run but change across hierarchy recomputes (full hierarchy regenerations). The upsert pattern handles this correctly — but do NOT store `subtopic_id` as a foreign key in other tables that outlive a single ETL cycle. Treat each publish as a full replacement.

---

## Out of Scope

The following items are explicitly deferred and will be addressed in separate phases or separate repos:

- **SPS-side review-queue dashboard surface.** v1 reviews happen via the ReciterAI CLI only (`python backfill_spotlight.py --review-queue [--publish-id <id>]`). PM editorial dashboard is v2 work; the DynamoDB schema is forward-compat (`SPOTLIGHT_REVIEW#` partition documented in `docs/spotlight-dynamodb-schema.md`).
- **Editorial CMS UI for hand-curated spotlight overrides.** v1 has no override surface; the rotation selector is the source of truth for which 10 subtopics are spotlighted on a given publish. v2 may introduce override semantics.
- **Real-time push notification on new publish.** sha256 polling at weekly cron cadence is sufficient for v1. Slack or email notification can be added if a second consumer surfaces.
- **Cron-triggered automation of the ReciterAI-side `--publish`.** Operator-run for v1; cron is v2.
- **PM migration off the file-copy hierarchy pattern.** The Phase 5 hierarchy artifact left a PM worktree copy step in place to preserve PM's current pattern. Spotlight does NOT have a PM worktree copy step — it is SPS-only. PM migration off the file-copy pattern is a separate future phase, unrelated to this handoff.

---

## Cross-References

| Document | Location | Purpose |
|----------|----------|---------|
| `docs/spotlight-contract.md` | This repo (`ReciterAI -ReCiter-Integration`) | Authoritative consumer contract — URL pattern, schema spec, cadence, breaking-change policy, voice contract, headshot rendering rule, Changelog, FAQ |
| `docs/spotlight.schema.json` | This repo | JSON Schema Draft 2020-12 (machine-readable contract) |
| `docs/spotlight-dynamodb-schema.md` | This repo | Review queue + history schema (for v2 dashboard work) |
| `docs/bucket-migration-runbook.md` | This repo | `wcmc-reciterai-hierarchy` → `wcmc-reciterai-artifacts` migration (run BEFORE spotlight ETL deployment) |
| `docs/sps-spotlight-etl-reference.ts` | This repo | Working TypeScript reference — copy to `etl/spotlight/index.ts`, adapt upsert block + photo-store call |
| `docs/sps-integration-handoff.md` | This repo | Predecessor brief — hierarchy ETL handoff, D-19 rule (read first) |
| `docs/aws-bucket-policy-artifacts.json` | This repo | Paste-ready resource-based bucket policy for `wcmc-reciterai-artifacts` — substitute `<SPS_LAMBDA_ROLE_ARN>` with your Lambda role ARN |
| `~/Downloads/home-spotlight-interactive.html` | Operator's laptop (NOT in this repo) | UI mockup for the 2-column interactive spotlight component |

# Spotlight Artifact — Consumer Contract

`spotlight.json` is a versioned, schema-validated S3 artifact carrying 10 LLM-authored editorial ledes for top-ranked WCM research subtopics, refreshed weekly via operator-run publish. It mirrors `hierarchy.json`'s publishing pattern (see `docs/hierarchy-contract.md`) — a versioned + `latest/` URL pattern, a co-published JSON Schema (`spotlight.schema.json`), and a sha256-bearing `manifest.json`. Any current or future downstream consumer (current: SPS home-page render; deferred: PM editorial dashboard) integrates against the artifact using only this document and `docs/spotlight.schema.json`.

**Authoritative schema source:** `docs/spotlight.schema.json` is the machine-readable contract; it is regenerated from the Phase 6 plan-set (06-CONTEXT.md, 06-RESEARCH.md, 06-06-PLAN.md). The schema's `$defs._meta.schema_version` is the source of truth for `manifest.schema_version`.

**Audience:** Any agent or engineer integrating against `spotlight.json`. Current consumer: SPS home-page interactive 2-column spotlight component (see `docs/sps-spotlight-handoff.md`). Deferred consumer: PM editorial dashboard (v2).

---

## Overview

`spotlight.json` carries the 10 active spotlights selected by the Phase 6 rotation pipeline plus a 50-row pool snapshot for transparency. Each spotlight pairs a 25-35 word lede (LLM-authored, deterministic + critic-passed, sensitive-tag-clean) with 2-3 representative WCM publications. Per-paper author payloads name the first and last author by `personIdentifier` for SPS-side photo-store resolution; the artifact carries no image URLs of its own. Publishing is operator-run weekly (`python backfill_spotlight.py --publish`); automated cron is deferred to v2.

---

## URL Pattern

| Path | Purpose |
|------|---------|
| `s3://wcmc-reciterai-artifacts/spotlight/v{ISO-date}/spotlight.json` | Versioned spotlight artifact (e.g. `spotlight/v2026-05-14/spotlight.json`) |
| `s3://wcmc-reciterai-artifacts/spotlight/v{ISO-date}/spotlight.schema.json` | Co-published JSON Schema for the same version |
| `s3://wcmc-reciterai-artifacts/spotlight/v{ISO-date}/manifest.json` | Manifest carrying version metadata and sha256 |
| `s3://wcmc-reciterai-artifacts/spotlight/latest/spotlight.json` | Most recent publish (PutObject-overwritten on every publish) |
| `s3://wcmc-reciterai-artifacts/spotlight/latest/spotlight.schema.json` | Schema for the latest publish |
| `s3://wcmc-reciterai-artifacts/spotlight/latest/manifest.json` | Manifest for the latest publish |

**Bucket:** `wcmc-reciterai-artifacts` (us-east-1, IAM-gated, private). Phase 6 migrated off the Phase 5 single-purpose `wcmc-reciterai-hierarchy` bucket onto a shared artifacts bucket; see `docs/bucket-migration-runbook.md` for the migration story. Hierarchy now lives at `hierarchy/`, spotlight at `spotlight/`. Both share the bucket-level resource policy at `docs/aws-bucket-policy-artifacts.json`.

**Retention:** All `spotlight/v{ISO-date}/` prefixes are retained indefinitely (no S3 Lifecycle rules). Full historical rollback is available.

**Version consistency rule:** Consumers MUST fetch the schema AND the artifact from the SAME prefix. Do NOT mix `latest/spotlight.json` with a cached schema from a prior fetch. Do NOT mix `spotlight/v2026-05-14/spotlight.json` with `spotlight/latest/spotlight.schema.json`. Fetching from a consistent prefix guards against schema drift during a 30-day breaking-change deprecation window (see Breaking-Change Policy).

---

## Schema

The schema is JSON Schema Draft 2020-12. The machine-readable form is `docs/spotlight.schema.json` in this repo, co-published at `s3://wcmc-reciterai-artifacts/spotlight/{v{date},latest}/spotlight.schema.json` on every publish run. The schema's `$id` reflects the `latest/` URL.

Schema version is tracked independently of `spotlight_version`, `taxonomy_version`, and the publish date. Consumers read `manifest.schema_version` (e.g. `"1.0.0"`, sourced from `$defs._meta.schema_version`) to detect schema changes separately from data-only updates.

**Additive-fields rule (Phase 5 D-11 carry-over):** The schema deliberately does not set `additionalProperties: false` at any level. Additive fields are non-breaking by policy. Consumers MUST silently tolerate unknown fields — do not reject parses and do not warn-log on encountering unrecognized keys. Adding a field is a MINOR `schema_version` bump.

---

## Manifest

`manifest.json` is co-published at both `spotlight/v{ISO-date}/manifest.json` and `spotlight/latest/manifest.json` on every publish. The seven fields below are LOCKED; insertion order is canonical (consumers who compute their own sha256 over `manifest.json` bytes for second-order change detection rely on stable ordering).

| Field | Type | Description |
|-------|------|-------------|
| `schema_version` | string (semver) | Reads from `$defs._meta.schema_version` in `spotlight.schema.json` (e.g. `"1.0.0"`). Independent of publish date. |
| `spotlight_version` | string | Artifact format version (e.g. `"spotlight_v1"`). Bumps on a breaking shape change (e.g. removed/renamed required field). Independent of `schema_version`. |
| `taxonomy_version` | string | Inherited from the upstream hierarchy artifact (e.g. `"taxonomy_v2"`). |
| `version` | string | Publish version: `v{ISO-date}` (e.g. `"v2026-05-14"`). Daily granularity. |
| `generated_at` | string (ISO 8601 UTC) | Publish moment, second precision, `Z` suffix (e.g. `"2026-05-14T13:42:00Z"`). No microseconds. |
| `sha256` | string (hex) | sha256 of the in-memory `spotlight.json` bytes (`json.dumps(indent=2, ensure_ascii=False)`) before S3 upload. |
| `artifact_bytes` | integer | Byte length of `spotlight.json` (convenience field for consumer pre-allocation). |

Field insertion order is LOCKED. Consumers MAY compute their own sha256 over `manifest.json` bytes for second-order change detection (e.g. to detect manifest tampering or truncation) — `sort_keys=True` is forbidden producer-side to prevent false positives in that secondary check.

The Phase 6 addition relative to Phase 5 is `spotlight_version` between `schema_version` and `taxonomy_version`.

---

## Cadence

- **Weekly publish (operator-run, v1):** `python backfill_spotlight.py --publish` produces a new `spotlight/v{ISO-date}/` prefix and overwrites `spotlight/latest/`. The recommended operator workflow is `--dry-run` (validation + manifest preview) then `--dry-run-full` (full pipeline preview) then `--publish` once review is satisfied.
- **Ad-hoc re-publishes:** Same-day re-publishes are explicitly first-class; same-prefix overwrite emits a `WARN: spotlight/v{date}/ already exists — overwriting` log line and proceeds. The `manifest.sha256` changes if and only if the bytes changed, so consumers detect real changes and skip no-op re-publishes automatically.
- **Cron automation:** Deferred to v2. v1 is operator-run.
- **Recommended consumer polling:** Poll `latest/manifest.json` weekly (HEAD or GET) and compare `manifest.sha256` against the last-known value. If `sha256` changed, re-run the full ETL fetch-and-load flow. No push notifications exist — the manifest sha256 IS the change signal.

---

## Breaking-Change Policy

**Definition:** A breaking schema change is one that would cause the validator to reject a previously-valid `spotlight.json` when the consumer upgrades to the new schema, or one that changes the meaning of an existing field. Adding a field, adding an optional metadata file, or relaxing a constraint is non-breaking.

**30-day advance notice:** Breaking schema changes require 30 days advance notice before activation. This window aligns with SPS's existing 30-day schema-change protocol. During the 30-day window, the new schema is published alongside the current one so consumers can prepare their validators and update their ETL logic before the breaking version becomes the sole `latest/` schema.

**Notice mechanism:** A CHANGELOG entry (below) is added with the target activation date. `manifest.schema_version` bumps when the new schema goes live; `manifest.spotlight_version` bumps on a breaking artifact-shape change. Consumers watching either field detect the bump and consult the CHANGELOG for migration guidance.

**Semver bump rules:**

| Change type | Version bump | Examples |
|-------------|-------------|---------|
| Additive (new field, relaxed constraint) | `schema_version` MINOR | Adding a new optional `pool_snapshot[].rationale` field |
| Breaking artifact shape (removed field, renamed required field) | `spotlight_version` MAJOR (e.g. `spotlight_v1` → `spotlight_v2`) | Renaming `papers[].first_author` to `papers[].lead` |
| Breaking schema constraint (new required field, type change) | `schema_version` MAJOR | Promoting `display_name` from optional to required |
| Textual (typo in `description` annotation, comment fix) | `schema_version` PATCH | Fixing a typo in a schema annotation |

**Communication channel:** `manifest.schema_version` and `manifest.spotlight_version` bumps plus CHANGELOG entries are the canonical records. No email, no Slack. The manifest sha256 is the signal; the CHANGELOG is the explanation.

---

## Integration Pattern

The recommended consumer flow is a six-step poll-and-load cycle. The reference implementation is `docs/sps-spotlight-etl-reference.ts` (TypeScript, `@aws-sdk/client-s3` + `ajv/dist/2020`). Consumers copy and adapt that script rather than implementing from scratch.

1. **Fetch `latest/manifest.json`** via `s3:GetObject`. Extract `sha256`, `version`, `schema_version`, `spotlight_version`.
2. **Compare `manifest.sha256` against the consumer's last-known value** (persisted in the consumer's own store). If unchanged, exit early — no work needed.
3. **Fetch `{manifest.version}/spotlight.schema.json` AND `{manifest.version}/spotlight.json`** using the versioned prefix from `manifest.version`, not `latest/`. This guarantees schema–data consistency.
4. **Validate `spotlight.json` against the schema (Draft 2020-12).** If validation fails, FAIL the ETL run immediately. Do NOT partially upsert — a partial load produces a corrupted consumer state harder to recover from than a clean failure (D-16 principle, consumer-side mirror).
5. **Project the 10 spotlights** into the consumer's render layer. Treat each publish as a full replacement: re-key on `subtopic_id` and do not carry forward state from a prior version (see Subtopic ID Stability).
6. **Resolve author headshots** for each `spotlights[].papers[].first_author.personIdentifier` and `last_author.personIdentifier` via the consumer's existing photo store. The artifact carries NO image URLs (see Author Headshot Rendering).
7. **Persist `manifest.sha256` and `manifest.version`** so step 2 can short-circuit on the next weekly poll.

Reference implementation: `docs/sps-spotlight-etl-reference.ts` (TypeScript; runnable as-shipped, not pseudocode).

---

## Voice Contract

Spotlight ledes are authored by `prompts/spotlight_synopsis_v0.md` (Bedrock Sonnet) and validated by a deterministic critic regex bundle plus an LLM-as-judge critic pass before publish. The voice constraints are part of the consumer contract because downstream renderers must not modify ledes (e.g. by pluralizing "scholar", swapping em-dashes back in, or appending time-bound copy). What ships is what was approved.

Constraints quoted from `prompts/spotlight_synopsis_v0.md`:

- **Length: 25-35 words.** Lenient bounds 22-38 enforced upstream. Two short sentences usually works best. One sentence works if it earns it. Three sentences is too long.
- **Institutional voice tic.** "Always include the construction `WCM scholars are [active verb]-ing` once. Present continuous tense. Never past or future. This is the institutional voice tic."
- **No em-dashes.** "Use periods, colons, semicolons, or commas."
- **No time-bound language.** "Forbidden: `this quarter`, `this year`, `currently`, `right now`, `recently`, `of late`, `in recent months`. Present continuous (`are mapping`) is permitted because it describes ongoing work without naming a window."
- **No marketing language.** "`cutting-edge`, `world-class`, `pioneering`, `revolutionary`, `groundbreaking`, `leading`, `innovative`."
- **No dead words.** "`important`, `complex`, `vital`, `novel` (as a vague modifier). Replace with concrete claims."
- **No specific WCM faculty named in the lede body.** "The voice is institutional (`WCM scholars`), not individual."
- **Active verbs preferred over gerunds.** "rewriting, outpacing, tracing, sharpening, reading, testing, mapping, working on. Avoid gerunds and passive constructions (`characterizing X`, `studying X`, `research is conducted on Y`)."
- **Anchored in paper synopses.** "Use the paper synopses and impactJustifications as the source of truth for what the work actually does. Don't invent specific findings or methods that aren't reflected in the synopses."

Renderers MUST display lede text verbatim. No client-side text mangling, no localization passes, no auto-truncation. If a renderer needs a shorter form, request a separate field upstream (additive non-breaking change).

---

## Subtopic ID Stability

Subtopic IDs (e.g. `aging_cellular_senescence`) are NOT stable across full hierarchy recomputes. Phase 4's hierarchy regeneration is wholesale and ID assignment is data-driven (D-06 from `hierarchy-schema.md`); annual recompute may rename, split, merge, or retire subtopic IDs without notice.

**Consumer rule:** treat each `spotlight.json` publish as a full replacement. Re-key on `spotlights[].subtopic_id` and do not persist subtopic IDs across recompute boundaries in tables that outlive a single ETL cycle.

**Producer-side operator action.** When a hierarchy recompute lands new subtopic IDs, the existing `SPOTLIGHT_HISTORY#{old_id}` partitions in DynamoDB (used by the rotation selector to apply recency-decay multipliers) become orphaned. The operator has two options on each annual recompute:

1. **Silent age-out (default):** do nothing. Orphaned rows stay in DynamoDB indefinitely (storage is negligible) and the new IDs cold-start naturally with multiplier `1.0`. The first publish after a recompute may spotlight a higher-than-usual share of never-shown subtopics; this is expected.
2. **Wholesale reset:** run `python backfill_spotlight.py --reset-history` to truncate the entire `SPOTLIGHT_HISTORY#` partition. Use this only on a wholesale ID rotation (annual recompute), not on incremental edits.

This decision is annual and operator-driven. The cross-bucket migration in `docs/bucket-migration-runbook.md` does NOT cover it (separate concern; that runbook is one-time per bucket migration, this is recurring per recompute).

---

## Sensitive Topic Routing

The published artifact contains ONLY auto-publishable spotlights — entries that passed BOTH the deterministic + LLM critic gate AND the sensitive-tag gate. Spotlights flagged on either gate are routed to the DynamoDB review queue and do NOT appear in `spotlight.json`. Consumers see nothing about them.

**Review queue partition:** `SPOTLIGHT_REVIEW#{publish_id}` + `SK=SUBTOPIC#{subtopic_id}` on the `reciterai` table. Schema is documented in `docs/spotlight-dynamodb-schema.md` (read that for the full attribute list, status state machine, and v2 forward-compat GSI proposal).

**Review surface (v1):** CLI only. `python backfill_spotlight.py --review-queue [--publish-id <id>]` lists pending entries; `--approve <subtopic_id>` and `--reject <subtopic_id>` resolve them. A subsequent `--publish` reads only `status = approved` rows when joining the review queue with the assembler step.

**Review surface (v2, deferred):** PM editorial dashboard. Schema is forward-compat — the v2 dashboard reads the same partition, no migration needed.

**Sensitive tag list location:** `SPOTLIGHT_CONFIG#sensitive_tags` partition in DynamoDB. The active patterns are operator-curated and live in DynamoDB only — they are NOT in the repo and NOT committed to git (security: avoiding checked-in sensitive-topic patterns is a deliberate disclosure-prevention measure). The schema doc names a v1 first-pass list (vaccine policy, abortion access, gender-affirming care, gun violence, climate-and-health) as operator notes only; the live list may be edited at any time without a code change.

---

## Author Headshot Rendering

Each `spotlights[].papers[]` entry includes `first_author` and `last_author` shaped as `{personIdentifier, displayName, position}`. Per-paper authorship is canonical (D-19 / SPOT-09 design): the artifact names exactly two authors per paper, the WCM full-time faculty in first or last position. Middle authors and other position roles are out of scope for v1.

**Consumer rule:** SPS resolves `personIdentifier` to a faculty headshot via its existing photo store. This is the same join key SPS uses for `RecentContributionsGrid` per Phase 2 verification — no new join semantics. The ReciterAI artifact carries NO image URLs, NO image hashes, and NO image references. If SPS lacks a photo for a given `personIdentifier`, render a fallback initial-avatar (`displayName` initials); do NOT call back to ReciterAI for image data.

**Why no image URLs in the artifact:** the artifact is an editorial+structural payload, not a media manifest. Photo-store URL semantics differ across consumers (SPS has its own URL conventions, future consumers may differ); coupling the artifact to one consumer's URL shape would force schema churn on consumer-side photo-store changes. CONTEXT decision Q4.1 settled this: artifact carries `personIdentifier` only.

**Failure mode:** a `personIdentifier` not resolvable in the consumer's photo store renders the initial-avatar fallback. This is graceful degradation, not an error. The artifact remains valid; the consumer's render layer absorbs the photo-resolution miss locally.

---

## Changelog

Entries are in reverse-chronological order (newest first). Each entry documents the schema_version delta, the change type (additive vs breaking vs data-only), the trigger, and migration notes for consumers.

---

*(Future schema_version and spotlight_version bumps will be entered here above this line.)*

---

## FAQ

**Q: Does the artifact include all 1,300+ subtopics?**
No. Only the 10 active spotlights selected by the rotation pipeline appear in `spotlights[]`. The 50-row `pool_snapshot[]` carries the top-50 candidates with their pool scores for transparency, but only the 10 selected subtopics receive a lede + paper payload. The remaining ~1,290 subtopics get no lede in v1; they continue to live in `hierarchy.json` and are eligible for selection on future publishes via the rotation selector.

**Q: How do I detect changes?**
Poll `latest/manifest.json` weekly and compare `manifest.sha256` against your last-known value. Both routine weekly publishes and ad-hoc same-day re-publishes update the sha256 if the bytes changed. No Slack webhook or email notification is sent — the manifest sha256 IS the signal.

**Q: What if a spotlight is sensitive?**
It does not appear in the published artifact. Sensitive-tag matches and critic-failed entries route to the DynamoDB `SPOTLIGHT_REVIEW#{publish_id}` partition for operator review. See the §Sensitive Topic Routing section above. Approved entries are picked up by the next `--publish` run; rejected entries are excluded indefinitely.

**Q: Why are `display_name` and `short_description` in the artifact when D-19 forbids passing them to LLMs?**
D-19 is a producer-side rule: the `label` and `description` fields are LLM-canonical (passed verbatim to the lede generator and any downstream synthesis prompt); `display_name` and `short_description` are UI-facing strings stylized for human display and were NEVER passed to the lede LLM upstream. Both fields are allowed in the published artifact for SPS-side rendering. The boundary is producer-side, not artifact-side. Do NOT re-derive `display_name` or `short_description` consumer-side or pass them through any LLM call. See `docs/sps-spotlight-handoff.md` §D-19 Rule for the full rule statement.

**Q: What if `manifest.spotlight_version` bumps to `spotlight_v2`?**
That is a breaking artifact-shape change. Expect a 30-day dual-publish window per §Breaking-Change Policy: both `spotlight_v1` and `spotlight_v2` are published simultaneously at versioned prefixes, with the CHANGELOG entry documenting the migration. Consumers update their parser, validate against the new schema, and switch their poll target after their migration is verified. The `latest/` alias flips to `spotlight_v2` after the 30-day window closes.

**Q: What if I need to publish twice on the same day?**
Both `spotlight/v{ISO-date}/` and `spotlight/latest/` are overwritten by PutObject. The pipeline emits a `WARN: spotlight/v{date}/ already exists — overwriting (same-day re-publish)` log line. This is the supported pattern. The `sha256` in `manifest.json` changes if and only if the bytes changed, so consumers detect real changes and skip no-op re-publishes automatically.

**Q: Can I read this artifact from a browser or public URL?**
No. The bucket is IAM-gated private. Each consumer (SPS Lambda, future apps) gets a tightly-scoped IAM role with `s3:GetObject` on this bucket prefix. No public-read ACL, no CloudFront distribution, no S3 static website hosting. This avoids the institutional-data classification question for synopsis-derived publication text.

**Q: How does the `additionalProperties` rule interact with schema validation?**
The schema sets `additionalProperties: true` (or omits the restriction) at every level. The validator accepts documents with unknown fields and does not reject them. Consumers MUST follow the same convention in their own code: unknown fields in `spotlight.json` are silently ignored, not logged as warnings and not treated as errors. This is the Phase 5 D-11 additive-fields rule, carried over to spotlight verbatim. A consumer that rejects unknown fields will break on the next additive schema bump without any breaking-change notice period.

**Q: Does the artifact contain PHI or PII?**
The artifact contains synopsis-derived `lede` text, `display_name` / `short_description` per spotlight, and per-paper title + journal + year + author payload. Author payload includes `personIdentifier` (WCM faculty UID, used as the SPS photo-store join key) and `displayName` (faculty display name for the paper byline). It does NOT contain patient identifiers, contact information, or any PHI. The `personIdentifier` and `displayName` are roughly equivalent to what SPS already exposes via its public faculty profile pages.

---

*Cross-references: [`docs/spotlight.schema.json`](spotlight.schema.json) (machine-readable schema) · [`docs/spotlight-dynamodb-schema.md`](spotlight-dynamodb-schema.md) (review queue + history schema) · [`docs/bucket-migration-runbook.md`](bucket-migration-runbook.md) (Phase 5→6 bucket migration) · [`docs/sps-spotlight-handoff.md`](sps-spotlight-handoff.md) (SPS coding-agent handoff brief) · [`docs/sps-spotlight-etl-reference.ts`](sps-spotlight-etl-reference.ts) (runnable TypeScript reference) · [`prompts/spotlight_synopsis_v0.md`](../prompts/spotlight_synopsis_v0.md) (lede authoring prompt + voice contract source).*

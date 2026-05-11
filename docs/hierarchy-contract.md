# Hierarchy Artifact — Consumer Contract

`hierarchy.json` is the canonical research-domain hierarchy artifact for WCM faculty expertise data. It is published to `s3://wcmc-reciterai-hierarchy` (us-east-1, IAM-gated, private) on a versioned + `latest/` pattern alongside a co-published JSON Schema (`hierarchy.schema.json`) and manifest (`manifest.json`). Any current or future downstream consumer (current: SPS ETL; deferred: PM ETL migration) integrates against the artifact using only this document and `docs/hierarchy.schema.json`.

**Authoritative schema source:** `.planning/phases/04-subtopic-system/hierarchy-schema.md` (narrative source of truth, TypeScript interfaces, decision rules). `docs/hierarchy.schema.json` is the machine-readable projection of that document. In case of divergence, `hierarchy-schema.md` wins.

**Audience:** Any agent or engineer integrating against `hierarchy.json`. Current consumer: SPS ETL Lambda (TypeScript). Deferred consumer: PM ETL migration (future phase).

---

## URL Pattern

| Path | Purpose |
|------|---------|
| `s3://wcmc-reciterai-hierarchy/v{ISO-date}/hierarchy.json` | Versioned hierarchy artifact (e.g. `v2026-05-06/hierarchy.json`) |
| `s3://wcmc-reciterai-hierarchy/v{ISO-date}/hierarchy.schema.json` | Co-published JSON Schema for the same version |
| `s3://wcmc-reciterai-hierarchy/v{ISO-date}/manifest.json` | Manifest carrying version metadata and sha256 |
| `s3://wcmc-reciterai-hierarchy/latest/hierarchy.json` | Most recent publish (PutObject-overwritten on every publish) |
| `s3://wcmc-reciterai-hierarchy/latest/hierarchy.schema.json` | Schema for the latest publish |
| `s3://wcmc-reciterai-hierarchy/latest/manifest.json` | Manifest for the latest publish |

**Retention (D-06):** All `v{ISO-date}/` prefixes are retained indefinitely (no S3 Lifecycle rules in this phase). Full historical rollback is available.

**Version consistency rule:** Consumers MUST fetch the schema AND the hierarchy from the SAME version prefix. Do NOT mix `latest/hierarchy.json` with a cached schema from a prior fetch. Do NOT mix `v2026-05-06/hierarchy.json` with `latest/hierarchy.schema.json`. Fetching from a consistent prefix guards against schema drift during a 30-day breaking-change deprecation window (see Breaking-Change Policy).

---

## Schema

The schema is JSON Schema Draft 2020-12. The authoring source is `.planning/phases/04-subtopic-system/hierarchy-schema.md` in this repo — that document contains the canonical TypeScript interfaces, all D-rule annotations, and concrete examples. The machine-readable form is `docs/hierarchy.schema.json` in this repo, co-published at `s3://wcmc-reciterai-hierarchy/{v{date},latest}/hierarchy.schema.json` on every publish run.

Schema version is tracked independently of `taxonomy_version` and the publish date. Consumers read `manifest.schema_version` (e.g. `"1.0.0"`) to detect schema changes separately from data-only updates.

**D-11 additive-fields rule:** The schema deliberately does not set `additionalProperties: false` at any level. Additive fields are non-breaking by policy. Consumers MUST silently tolerate unknown fields — do not reject parses and do not warn-log on encountering unrecognized keys. Adding a field is a MINOR schema version bump. This rule codifies the precedent established by D-19 (`display_name` and `short_description` were added as non-breaking additive fields).

---

## Manifest

`manifest.json` is co-published at both `v{ISO-date}/manifest.json` and `latest/manifest.json` on every publish. The six fields below are locked; insertion order is canonical (consumers who compute their own sha256 over `manifest.json` bytes for second-order change detection rely on stable ordering).

| Field | Type | Description |
|-------|------|-------------|
| `schema_version` | string (semver) | Version of `hierarchy.schema.json` (e.g. `"1.0.0"`). Independent of publish date. |
| `taxonomy_version` | string | Parent taxonomy version (e.g. `"taxonomy_v2"`). |
| `version` | string | Publish version: `v{ISO-date}` (e.g. `"v2026-05-06"`). |
| `generated_at` | string (ISO 8601 UTC) | Publish moment, second precision (e.g. `"2026-05-06T14:32:01Z"`). |
| `sha256` | string (hex) | sha256 of the in-memory `hierarchy.json` bytes before S3 upload. |
| `artifact_bytes` | integer | Byte length of `hierarchy.json` (convenience field for consumer pre-allocation). |

Consumers detect data changes by comparing `manifest.sha256` against their last-known value. Field insertion order is canonical so consumers MAY compute their own sha256 over `manifest.json` bytes for second-order change detection (e.g. to detect manifest tampering or truncation).

---

## Cadence

- **Annual full recompute (guaranteed, D-08):** The pipeline runs Pass 1/2/3 subtopic discovery followed by `--publish` once per year, producing a new `v{ISO-date}/` prefix and overwriting `latest/`.
- **Ad-hoc re-publishes (explicitly first-class):** Label fixes, contract revisions, schema additions, and any other data-only or schema-additive changes are permitted without frequency constraints. Today's 2026-05-06 D-19 relabel pass is the canonical reference example of an ad-hoc re-publish (see Changelog).
- **Recommended consumer polling:** Poll `latest/manifest.json` weekly (HEAD or GET) and compare `manifest.sha256` against the last-known value. If sha256 changed, re-run the full ETL fetch-and-load flow. No push notifications exist — the manifest sha256 IS the change signal (D-12).

---

## Breaking-Change Policy

**Definition:** A breaking schema change is one that would cause the validator to reject a previously-valid `hierarchy.json` when the consumer upgrades to the new schema, or one that changes the meaning of an existing field. Adding a field, adding an optional metadata file, or relaxing a constraint is non-breaking.

**30-day advance notice (D-09):** Breaking schema changes require 30 days advance notice before activation. This window aligns with SPS's existing 30-day schema-change protocol. During the 30-day window, the new schema is published alongside the current one so consumers can prepare their validators and update their ETL logic before the breaking version becomes the sole `latest/` schema.

**Notice mechanism:** A CHANGELOG entry (below) is added with the target activation date. `manifest.schema_version` bumps when the new schema goes live. Consumers watching `schema_version` in `manifest.json` detect the bump and consult the CHANGELOG for migration guidance.

**Semver bump rules:**

| Change type | Version bump | Examples |
|-------------|-------------|---------|
| Additive (new field, relaxed constraint) | MINOR | Adding `display_name`, `short_description` |
| Breaking (removed field, changed type, new required field) | MAJOR | Renaming `label` to `name` |
| Textual (typo in `description` field, comment fix) | PATCH | Fixing a typo in a schema annotation |

**Communication channel:** `manifest.schema_version` bumps and CHANGELOG entries are the canonical records (D-10). No email, no Slack. The manifest sha256 is the signal; the CHANGELOG is the explanation.

---

## Integration Pattern

The recommended consumer flow is a six-step poll-and-load cycle. The reference implementation is `docs/sps-etl-reference.ts` (TypeScript, SPS-flavored upsert stub). Consumers copy and adapt that script rather than implementing from scratch.

1. **Fetch `latest/manifest.json`** via `s3:GetObject`. Extract `sha256`, `version`, and `schema_version`.
2. **Compare `manifest.sha256` against the consumer's last-known value** (persisted in the consumer's own store). If unchanged, exit early — no ETL work needed.
3. **Fetch `{manifest.version}/hierarchy.schema.json` AND `{manifest.version}/hierarchy.json`** using the versioned prefix from `manifest.version`, not `latest/`. This guarantees schema–data consistency.
4. **Validate `hierarchy.json` against the schema.** If validation fails, FAIL the ETL run immediately. Do NOT partially upsert — a partial load produces a corrupted consumer state that is harder to recover from than a clean failure (D-16 principle applied consumer-side).
5. **Project the validated structure** into the consumer's storage layer (e.g. MySQL Subtopic table for SPS). Consumers MUST treat each publish as a full replacement: re-key on subtopic IDs and do not carry forward state from a prior version (see ID Stability).
6. **Persist `manifest.sha256` and `manifest.version`** so step 2 can short-circuit on the next weekly poll.

Reference implementation: `docs/sps-etl-reference.ts` (TypeScript; SPS-flavored upsert stub — runnable as-shipped, not pseudocode).

---

## ID Stability

Subtopic IDs (e.g. `aging_cellular_senescence`) are NOT stable across full recomputes. On each annual recompute, the hierarchy is replaced wholesale and subtopic IDs may change (D-06 from `hierarchy-schema.md`). Consumers MUST NOT persist subtopic IDs in external systems that outlive a recompute cycle. Re-key every consumed publish by fetching the full `hierarchy.json` and rebuilding the consumer's subtopic mapping from scratch.

This rule does NOT apply within an ad-hoc re-publish that is data-only (no Pass 1/2/3 re-run): the 2026-05-06 D-19 relabel was an `--assemble-only` re-publish that preserved existing IDs per design. The distinction is: if the publish was produced by Pass 1/2/3 (annual recompute), assume IDs changed. If the CHANGELOG entry says "data-only / no schema change", IDs are stable within that publish cycle.

---

## Changelog

Entries are in reverse-chronological order (newest first). Each entry documents the schema_version delta, the change type (additive vs breaking vs data-only), the trigger, and migration notes for consumers.

---

### v2026-05-06 — Inaugural publish (data-only ad-hoc re-publish, D-19 relabel)

- **schema_version:** `1.0.0` (initial)
- **taxonomy_version:** `taxonomy_v2` (unchanged from Phase 4 backfill)
- **Type:** Ad-hoc re-publish — data only; no schema change
- **Trigger:** D-19 added `display_name` and `short_description` to all subtopics via `relabel_subtopics.py` followed by `backfill_all.py --assemble-only`. These UI-facing fields were requested by the SPS coding agent for card title rendering. The relabel pass ran without re-clustering (Pass 1/2/3 skipped), so subtopic IDs, `label`, `description`, `seed_pmids`, `total_weight`, and `activity_count` are unchanged.
- **Migration notes:** None. `display_name` and `short_description` are additive fields per D-11. Consumers that existed before D-19 silently ignore them (unknown fields are tolerated). UI consumers SHOULD prefer `display_name` over `label` for card titles and render `short_description` as the card subtitle.

---

*(Future breaking-change entries go here, above the inaugural entry.)*

---

## FAQ

**Q: Why is this in S3 instead of DynamoDB?**
`hierarchy_full.json` is approximately 1.1 MB and exceeds DynamoDB's 400 KB single-item limit. Partitioning the hierarchy across multiple DynamoDB items would require a partition redesign and consumer-side reassembly logic with no meaningful benefit over a simple S3 GetObject. The S3 artifact pattern solves the consumer integration problem more cleanly: one URL, one fetch, one validation step, one upsert. The Phase 5 design discussion explicitly evaluated and rejected DynamoDB as Option A. (Reference: `.planning/phases/05-hierarchy-publishing-contract/05-CONTEXT.md` `<deferred>` section.)

**Q: Can I read this artifact from a browser or public URL?**
No. The bucket is IAM-gated private. Each consumer (SPS Lambda, future apps) gets a tightly-scoped IAM role with `s3:GetObject` on this bucket prefix. No public-read ACL, no CloudFront distribution, no S3 static website hosting. This avoids the institutional-data classification question for synopsis-derived publication text. (D-02.)

**Q: Will subtopic IDs stay stable across recomputes?**
No. See the ID Stability section above. Consumers MUST re-key on every publish rather than persisting subtopic IDs across recompute boundaries. IDs are stable within an ad-hoc data-only re-publish (e.g. the D-19 relabel), but not across annual recomputes.

**Q: How do I know when there's a new publish?**
Poll `latest/manifest.json` weekly and compare `manifest.sha256` against your last-known value. Both annual recomputes and ad-hoc re-publishes update the sha256. No Slack webhook or email notification is sent — the manifest sha256 IS the signal (D-12).

**Q: What data is in `hierarchy.json`? Does it contain PHI or PII?**
No. The artifact contains synopsis-derived `label`, `description`, `display_name`, and `short_description` text per topic and subtopic, all derived from public publication abstracts. It does not contain per-author identifiers — no `personIdentifier`, no `cwid_*` prefixes, no faculty names, no institutional affiliations. The artifact is research-domain structural data only.

**Q: What if I need to publish twice on the same day?**
Both `v{ISO-date}/` and `latest/` are overwritten by PutObject. The pipeline emits a `WARN: v{date}/ already exists — overwriting` log line. This is the supported pattern for same-day ad-hoc re-publishes (the 2026-05-06 D-19 relabel is the reference). The sha256 in `manifest.json` changes if and only if the `hierarchy.json` bytes changed, so consumers detect real changes and skip no-op re-publishes automatically.

**Q: How does the `additionalProperties` rule interact with schema validation?**
The schema sets `additionalProperties: true` (or omits the restriction) at every level. This means the validator accepts documents with unknown fields and does not reject them. Consumers MUST follow the same convention in their own code: unknown fields in `hierarchy.json` are silently ignored, not logged as warnings and not treated as errors. This is the D-11 additive-fields rule. A consumer that rejects unknown fields will break on the next additive schema bump without any breaking-change notice period.

---

*Authoritative schema source: `.planning/phases/04-subtopic-system/hierarchy-schema.md`. SPS handoff brief: `docs/sps-integration-handoff.md`.*

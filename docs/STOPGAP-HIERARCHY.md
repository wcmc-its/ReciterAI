# Stopgap Hierarchy Artifact (SPS-Side)

**Status:** Active stopgap. Replace with canonical upstream artifact when ready.

**Date opened:** 2026-05-07
**Owner of stopgap:** SPS (`Scholars-Profile-System` repo)
**Owner of canonical replacement:** ReciterAI integration team

## What this is

The Scholars Profile System has shipped Phase 8 (D-19 subtopic display fields), which expects to consume a consolidated `hierarchy.json` artifact from `s3://wcmc-reciterai-hierarchy/`. The upstream pipeline that publishes this artifact has not yet provisioned the bucket or pushed the canonical bundle. To unblock SPS users, the SPS team built a stopgap producer that bundles the per-topic augmented JSON files in this repo (`.planning/phases/04-subtopic-system/hierarchy_augmented_*.json`) into the consolidated artifact format, validates it against `docs/hierarchy.schema.json`, and uploads it to the expected S3 layout.

## Provenance

- **Source data:** `.planning/phases/04-subtopic-system/hierarchy_augmented_*.json` (65 topic files, 1526 subtopics, 100% of SPS DB coverage as of 2026-05-07).
- **Schema source:** `docs/hierarchy.schema.json` (used verbatim).
- **Producer scripts:** in the SPS repo at `scripts/generate-hierarchy-artifact.ts` and `scripts/upload-hierarchy-to-s3.ts`.
- **Currently published artifact:**
  - `s3://wcmc-reciterai-hierarchy/v2026-05-07-sps-stopgap/hierarchy.json`
  - `s3://wcmc-reciterai-hierarchy/v2026-05-07-sps-stopgap/hierarchy.schema.json`
  - `s3://wcmc-reciterai-hierarchy/latest/manifest.json`
- **Manifest `taxonomy_version`:** `1.0.0-sps-stopgap-2026-05-07`

## How to replace with the canonical artifact

When the upstream pipeline is ready:

1. Publish the canonical artifact to the same bucket and key paths. SPS does not care which principal owns the bucket as long as the SPS Lambda role / local IAM principal has `s3:GetObject` + `s3:ListBucket`.
2. Use a `taxonomy_version` distinct from `1.0.0-sps-stopgap-2026-05-07`. The next SPS Hierarchy ETL run will emit the documented HIERARCHY-04 / D-02 WARN line on `taxonomy_version` change — that is the expected, audit-friendly handoff signal.
3. The SPS ETL will short-circuit on `manifest.sha256` match. To force a re-upsert, change either the artifact bytes or the `version` prefix.
4. Once verified, the SPS team can delete the `v2026-05-07-sps-stopgap/` prefix (or you can; it is owner-cleanable).

## How to update the stopgap (if needed before canonical ships)

If the upstream augmented files in this repo change (new subtopics, edited descriptions) before the canonical pipeline is ready, regenerate from the SPS side:

```sh
cd ~/Dropbox/GitHub/Scholars-Profile-System
npx tsx scripts/generate-hierarchy-artifact.ts
npx tsx scripts/upload-hierarchy-to-s3.ts out/hierarchy/v<new-date>-sps-stopgap
npm run etl:hierarchy
```

Bump the date suffix in `VERSION` (and ideally the `TAXONOMY_VERSION` so the WARN line fires cleanly).

## Notes

- The stopgap intentionally uses the augmented files only — not the draft files. Drafts lack `display_name` and `short_description` and would fail schema validation.
- `excluded_topics` and `see_also` are emitted as empty arrays. SPS does not currently consume either, but the schema requires the keys.
- Two topics in the SPS taxonomy (`implementation_science`, `oral_craniofacial_health`) have no subtopics in the SPS database, so they are absent from the artifact entirely. If the canonical pipeline emits subtopics for them, those will arrive on the next replacement.
- The SPS ETL is fail-safe: an artifact validation failure or S3 read error produces a non-zero exit and a recorded EtlRun row with status `failed`. Subtopic display strings fall back to the slug-derived `label` via the `display_name ?? label` chain in `lib/api/{home,topics,search}.ts`.

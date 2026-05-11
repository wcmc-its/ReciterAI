# Phase 7 — Hierarchy Publisher

**Milestone:** M2 — SPS-feeding service
**Tracks:** [SPS #180](https://github.com/wcmc-its/Scholars-Profile-System/issues/180)
**Estimated effort:** 2–3 hours
**Status:** Plan — awaiting approval

## Goal

ReciterAI fully owns hierarchy artifact production and publishing. A single command in this repo produces `s3://wcmc-reciterai-hierarchy/v{date}/{hierarchy.json, hierarchy.schema.json, manifest.json}` and overwrites `latest/manifest.json`. The two SPS-side stopgap scripts (`scripts/generate-hierarchy-artifact.ts`, `scripts/upload-hierarchy-to-s3.ts`) are deleted; SPS's existing `etl:hierarchy` continues to consume from S3 unchanged.

## Current state (what we're replacing)

The SPS-side stopgap is two TypeScript files totaling ~330 lines:

- `generate-hierarchy-artifact.ts` — reads 65 `hierarchy_augmented_*.json` files from this repo's `.planning/phases/04-subtopic-system/`, bundles into `hierarchy.json` per `docs/hierarchy.schema.json`, validates with AJV, canonicalizes (sorted topic keys), computes sha256, writes `hierarchy.json` + `hierarchy.schema.json` + `manifest.json` to `out/hierarchy/<version>/`
- `upload-hierarchy-to-s3.ts` — ensures bucket exists, uploads version-pinned objects FIRST then `latest/manifest.json` (ordering matters for ETL safety — manifest must always point at existing version-pinned objects)

Inputs already live in this repo:
- `.planning/phases/04-subtopic-system/hierarchy_augmented_*.json` (65 files, ~1.1 MB)
- `docs/hierarchy.schema.json`

## Approach

Port to Python under a new top-level `pipeline_hierarchy/` package. Use the existing `utils/s3_client.py` for S3 work.

### Files

```
pipeline_hierarchy/
├── __init__.py
├── generator.py        # bundle augmented → validate → canonicalize → sha256
├── publish.py          # generator + S3 upload (single entry point)
└── README.md           # short usage + design notes
tests/
├── fixtures/
│   └── hierarchy_golden_sha256.txt   # sha256 produced by the SPS TS stopgap
└── test_hierarchy_publisher.py       # golden test: same inputs → same sha256
```

Single CLI entry: `python -m pipeline_hierarchy.publish [--dry-run] [--version-suffix LABEL]`. Default behavior: generate locally to `out/hierarchy/v{ISO-date}/`, validate, then upload to S3 (version prefix + overwrite `latest/manifest.json`).

### Implementation notes

- **JSON Schema validation** — use the `jsonschema` package (already in `requirements.txt` per the Chatbot pipeline) with Draft 2020-12 support.
- **Canonical serialization** — `json.dumps(obj, indent=2, sort_keys=True)` plus pre-sort topic keys in the hierarchy dict before serializing (same logic as the TS script's `Object.keys(...).sort()`).
- **sha256** — `hashlib.sha256(canonical_text.encode('utf-8')).hexdigest()`. Must match the TS output exactly for golden test to pass.
- **Upload ordering** — match the TS script: PUT version-pinned `hierarchy.json` + `hierarchy.schema.json` first, THEN `latest/manifest.json` last.
- **Idempotency** — re-running with the same date produces the same output bytes; sha256 unchanged; safe to no-op or overwrite.

### Golden test

Before deleting the SPS stopgap, run both:

1. `python -m pipeline_hierarchy.publish --dry-run` → produces local hierarchy.json + sha256
2. `cd ~/Dropbox/GitHub/Scholars-Profile-System && npx tsx scripts/generate-hierarchy-artifact.ts` → same

Compare the two sha256 values. If equal, the ports are byte-identical and SPS's ETL will see no change. Save the value in `tests/fixtures/hierarchy_golden_sha256.txt` for regression.

## Open design decisions

These need answers before implementation starts:

1. **`taxonomy_version` derivation.** TS stopgap hardcodes `"1.0.0-sps-stopgap-2026-05-07"`. Options:
   - (a) Hash of `taxonomy_v2.json` content (deterministic; changes whenever taxonomy changes)
   - (b) Read a `taxonomy_version` field from `taxonomy_v2.json` itself
   - (c) ISO date + git short SHA: `taxonomy_v2-2026-05-11-a1b2c3`
   - **Default if unspecified:** (b), falling back to (c) if no field present.
2. **Hierarchy artifact `version` prefix.** Currently `v{ISO-date}-sps-stopgap`. Drop the `-sps-stopgap` suffix once this repo owns publishing — propose `v{ISO-date}`.
3. **Augmented files location.** They currently sit under `.planning/phases/04-subtopic-system/` (treated as planning artifacts). Should they move to a more discoverable runtime location like `out/subtopics/<version>/` or stay where they are?
   - **Default:** leave them where they are; document the input path in `pipeline_hierarchy/README.md`. Moving them risks breaking other consumers.

## Verification (goal-backward)

The phase is complete when ALL of the following are true:

1. `python -m pipeline_hierarchy.publish` runs end-to-end without error
2. The local `out/hierarchy/v{ISO-date}/` directory contains valid `hierarchy.json`, `hierarchy.schema.json`, `manifest.json`
3. The golden test passes: sha256 matches the SPS-stopgap output for the same inputs
4. `s3://wcmc-reciterai-hierarchy/v{ISO-date}/` and `latest/manifest.json` are updated on AWS
5. SPS `npm run etl:hierarchy` succeeds against the new manifest (short-circuits on unchanged sha256 OR upserts ~2,010 subtopics on changed)
6. SPS stopgap scripts (`generate-hierarchy-artifact.ts`, `upload-hierarchy-to-s3.ts`) are deleted in SPS, plus references removed from `docs/spotlight-runbook.md` and `docs/spotlight-integration-plan.md`
7. SPS issue #180 closes

## Plan tasks

- [ ] 07-01: Scaffold `pipeline_hierarchy/` package (`__init__.py`, `README.md`)
- [ ] 07-02: Implement `generator.py` (load augmented → bundle → validate → canonicalize → sha256 → write local)
- [ ] 07-03: Implement `publish.py` (generator + S3 upload via `utils/s3_client.py`, ordered)
- [ ] 07-04: Golden test against SPS-stopgap sha256 (fixture + pytest)
- [ ] 07-05: First real publish run to S3 with `v{ISO-date}` (no -sps-stopgap suffix)
- [ ] 07-06: SPS-side verification — run `npm run etl:hierarchy`, confirm green
- [ ] 07-07: Delete SPS stopgap scripts + update SPS docs + commit on SPS feature branch + PR + merge
- [ ] 07-08: Close issue #180

## Out of scope for this phase

- Recomputing taxonomy or subtopics (those run independently; this phase just bundles + publishes the existing artifacts)
- Spotlight artifact publishing (Phase 6 already owns that)
- DynamoDB writes (separate publish channel; not affected)
- Cron/scheduling (manual invocation for now; scheduler is a future ops concern)

## Risks

- **sha256 drift.** If Python's `json.dumps` output differs from JS's `JSON.stringify` for the same logical content (whitespace, key escaping, number formatting), the golden test fails. Mitigation: explicit canonicalization with `separators=(',', ': ')`, `ensure_ascii=False`, `sort_keys=True`. If still divergent, investigate with diff before tweaking — usually a single floating-point or character-encoding mismatch.
- **Bucket-create permissions.** TS upload script creates bucket if missing. Confirm IAM role has `s3:CreateBucket` if we want to keep that behavior, or require the bucket to exist (it does) and just `PutObject`.
- **Concurrent ETL during upload.** Already handled by upload ordering: version-pinned objects first, manifest last. Keep this invariant in the Python port.

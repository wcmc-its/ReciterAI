# pipeline_hierarchy

Bundles + publishes the canonical research-domain hierarchy artifact consumed by the Scholars Profile System.

## What it does

Reads the pre-bundled hierarchy from `.planning/phases/04-subtopic-system/hierarchy_full.json`, re-stamps `generated_at`, validates against `docs/hierarchy.schema.json`, computes sha256 with stable topic-key ordering, and:

- writes `out/hierarchy/v{ISO-date}/{hierarchy,hierarchy.schema,manifest}.json` locally
- uploads version-pinned objects then `latest/manifest.json` to `s3://wcmc-reciterai-hierarchy/`

## Run

```bash
# Local-only generation (no S3 upload)
python -m pipeline_hierarchy.publish --dry-run

# Full publish to S3
python -m pipeline_hierarchy.publish

# Custom version label
python -m pipeline_hierarchy.publish --version v2026-05-11-test
```

Requires `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION` (or a configured IAM role) for the S3 publish step. Dry-run needs no AWS access.

## Upload ordering invariant

Version-pinned `hierarchy.json` + `hierarchy.schema.json` MUST be uploaded before `latest/manifest.json`. The SPS ETL fetches `latest/manifest.json` first then follows the version pointer; if manifest landed first and pointed at a not-yet-uploaded object, a concurrent ETL run would 404. See `docs/hierarchy-contract.md` (D-02).

## Phase 9 substrate integration

`publish` integrates with `utils.stage_records` (content-addressed `STAGE#` rows) and `gates/` (registered quality gates). On every run:

- A content-addressed `input_hash` is computed from a `generated_at`-free representation of the bundled hierarchy, plus the schema sha, the excluded_topics sha, the pinned model IDs, and the bundler `__version__`. A prior `complete` row with the same hash short-circuits with a `skipped` row (carrying `SKIP_COST_OBSERVED_USD = 0` per Phase 10 D-09).
- Block-severity gates (`schema_validation`, `parent_prefix`, `pii_scan`) run pre-upload. A failure writes a `failed` row and exits non-zero. Override path: `--force --force-reason "..."` proceeds and writes `force_reason` into the complete row's audit field.
- Warn-severity gates (`schema_roundtrip`) run post-upload. Failures surface in stderr but do NOT halt — bytes are already live.
- A complete row is written at the end with `cost_observed_usd`, `output_pointer`, `records_written`, and `model_ids_snapshot`.

Substrate documentation for new stage authors: [`docs/stage-records-and-gates.md`](../docs/stage-records-and-gates.md). Ad-hoc gate runner: `python -m gates --stage publish --hierarchy v2026-05-12`.

## Replaces the SPS-side stopgap

Previously, `Scholars-Profile-System/scripts/generate-hierarchy-artifact.ts` + `upload-hierarchy-to-s3.ts` did this work from SPS. Those scripts are removed in tandem with this phase shipping (SPS issue #180).

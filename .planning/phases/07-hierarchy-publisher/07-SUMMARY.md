# Phase 7 — Hierarchy Publisher — SUMMARY

**Status:** Complete (2026-05-11)
**Tracked:** [SPS #180](https://github.com/wcmc-its/Scholars-Profile-System/issues/180) — closed

## What shipped

- `pipeline_hierarchy/` package (3 modules + README):
  - `generator.py` — load `.planning/phases/04-subtopic-system/hierarchy_full.json`, re-stamp `generated_at`, ensure `see_also: []`, sort topic keys, validate against `docs/hierarchy.schema.json`, compute sha256
  - `publish.py` — orchestrator: generate → write local `out/hierarchy/v{ISO-date}/` → upload to S3 in correct order (version-pinned objects first, `latest/manifest.json` last)
  - `__init__.py`, `README.md`
- `tests/test_hierarchy_publisher.py` — 7 tests covering schema validation, reproducibility, topic sort, default version derivation, required-field coverage. All pass.

## Implementation notes (vs original plan)

The plan assumed re-bundling from per-topic `hierarchy_augmented_*.json` files (mirroring the SPS-stopgap approach). Discovered during implementation that `.planning/phases/04-subtopic-system/hierarchy_full.json` is already a fully-bundled artifact with `taxonomy_version: "taxonomy_v2"` and all schema-required fields populated. The publisher reads this directly, which simplified the port and dropped the need for per-file bundling logic.

Consequence: the "golden test against SPS-stopgap sha256" idea from the plan was replaced with a reproducibility test (same `generated_at` → same sha256). The SPS-stopgap and the new artifact have **different sha256 by design** — taxonomy_version metadata changed (from `"1.0.0-sps-stopgap-2026-05-07"` to the real `"taxonomy_v2"`), and `see_also: []` is now explicit. Topic IDs + subtopic IDs are structurally identical, so SPS sees a content refresh rather than a schema change.

## Verification

- **7/7 pytest tests pass**
- **Dry-run produces valid 65-topic / 1526-subtopic artifact**
- **Real S3 publish:** `v2026-05-12/` written to `s3://wcmc-reciterai-hierarchy/`; `latest/manifest.json` overwritten
- **SPS consumes cleanly:** `npm run etl:hierarchy` → `upsert_complete: 1526 rows, taxonomy_version=taxonomy_v2, sha256=4930d57bcac5…`
- **Rollback safety preserved:** previous `v2026-05-07-sps-stopgap/` retained per D-06 retention

## Follow-up

The ETL emitted 87 `editorial_warning_parent_prefix` warnings — display_names that start with a parent-topic word (e.g. `aging_*` subtopic named "Aging-Related X"). Pre-existing content quality concerns at the ReciterAI source, not caused by this phase. Worth a separate ticket to clean up at the subtopic-relabel pipeline.

## Commits

- ReciterAI: `2523bbf` (plan), `050848d` (implementation), `0531dd2` (this summary + ROADMAP mark)
- SPS: `d818a88` on `fix/176-ai-transparency` (stopgap deletion)

# Spotlight Pipeline Launch Handoff

Written 2026-05-07, after the first live publish of the Phase 6 spotlight artifact. Captures the operational state of the system, the bugs found and fixed during the live smoke, and the remaining work to surface spotlights in the Scholars Profile System.

This is the "what just happened, what's live, what's next" doc. For the consumer-side artifact contract, see `docs/spotlight-contract.md`. For the SPS coding-agent brief, see `docs/sps-spotlight-handoff.md`.

---

## What's live

### Artifact

Published to `s3://wcmc-reciterai-artifacts/spotlight/`:

| Key | Bytes | Notes |
|---|---|---|
| `v2026-05-07/spotlight.json` | 33,667 | Versioned snapshot of this publish |
| `v2026-05-07/spotlight.schema.json` | 4,466 | Co-published schema (Draft 2020-12) |
| `v2026-05-07/manifest.json` | 284 | Version + sha256 + sizes |
| `latest/spotlight.json` | 33,667 | Identical content; SPS reads here |
| `latest/spotlight.schema.json` | 4,466 | |
| `latest/manifest.json` | 284 | |

- **sha256:** `2a84f0264abf659534ff1a11d9f3034c702a5f0dbc60d4a04aa7980a0408d2fa`
- **schema_version:** 1.0.0
- **spotlight_version:** spotlight_v1
- **taxonomy_version:** taxonomy_v2
- **generated_at:** 2026-05-08T01:20:56Z
- **Schema validation:** PASS (0 errors)

### Spotlights produced (10/10)

```
cell_cancer_genomics_molecular_oncology       (parent: cell_molecular_biology)
translational_precision_oncology_genomics     (parent: translational_clinical_science)
epidemiology_racial_ethnic_health_disparities (parent: epidemiology_population_health)
biostatistics_health_disparities              (parent: biostatistics_quantitative_sciences)
health_racial_ethnic_disparities_access       (parent: health_services_policy)
drug_cancer_targeted_therapy_resistance       (parent: drug_discovery_pharmacology)
genetics_precision_oncology_biomarkers        (parent: genetics_genomics_precision_medicine)
surgery_urologic_prostate                     (parent: surgery_perioperative_medicine)
immunology_neuroinflammation_neuroimmune      (parent: immunology_inflammation)
pathology_molecular_genomic_cancer            (parent: pathology_laboratory_medicine)
```

Author payload: 30/30 papers populated with both `first_author` and `last_author` (each carrying `personIdentifier`, `displayName`, `position`).

### Infrastructure

| Resource | State |
|---|---|
| `wcmc-reciterai-artifacts` (S3, us-east-1) | Created. AES-256 encryption, public-access-block matching `wcmc-reciterai-hierarchy` |
| Bucket policy | NOT attached. Deferred until the SPS Lambda role ARN is known (or the SPS team confirms same-account read via identity-side IAM). |
| `SPOTLIGHT_CONFIG#sensitive_tags` (DynamoDB partition) | Seeded with 5 v1 patterns: `vaccine`, `abortion`, `gender-affirming`, `gun violence`, `climate change`. All `match_type=substring`. None of the 10 published spotlights matched a pattern. |
| `SPOTLIGHT_HISTORY#` (DynamoDB partition) | 10 rows written, `last_shown_at = 2026-05-08T01:20Z`. Next publish run will downweight these via `selection_score = pool_score × (1 - exp(-weeks/12))` (τ = 12 weeks). |
| `SPOTLIGHT_REVIEW#v2026-05-07` | 1 stale entry from an earlier dry-run that hit the critic-preamble bug; superseded by the publish run, no operator action needed. |
| Publisher IAM | `user/reciter` has `AdministratorAccess`. No additional policy attachment needed. |

---

## What changed in code this session

49 commits on `main` between `bd7c03b` and `f1e234c`. The phase-6 plan implementation accounts for ~8,500 of the ~10,200 added lines; the rest is recovery from the live smoke.

### Phase 6 plan execution (8 plans across 7 waves)

All plan summaries are in `.planning/phases/06-spotlight-pipeline/06-XX-SUMMARY.md`. Verification report at `.planning/phases/06-spotlight-pipeline/06-VERIFICATION.md` (verdict PASS, 14/14 SPOT-XX requirements satisfied).

### Live-smoke recovery commits

Surfaced by running `python3 backfill_spotlight.py --dry-run` and `--dry-run-full` against production DynamoDB and live Bedrock. The mocked test suite did not catch any of these because it never exercised the lazy-init paths or the real TOPIC# row shape.

| Commit | What it fixed |
|---|---|
| `7618db1` | Lazy-init DynamoDB client in `fetch_history` (rotation_selector). Mocks always passed an explicit stub client; CLI calls with `client=None` were crashing on `.batch_get_item`. |
| `3f4b856` | `pool_score` used to be the sum of every paper's `impact_score` per subtopic — volume dominated quality. Switched to sum of top-6 papers per subtopic. Top score dropped from 3846 to 442; top-10 went from 4 biostatistics + 3 translational to 10 distinct parent topics. |
| `3f4b856` | `_parent_of(subtopic_id)` used a `r'_\d{3}$'` regex assuming `<parent>_NNN` IDs, but real IDs are descriptive slugs (`cardiovascular_coronary_revascularization`). The regex silently returned the input unchanged → diversity gate became a no-op. Now reads `parent_topic` from a hierarchy.json-derived lookup; regex is a fallback. |
| `50f50d2` | `_extract_paper` falls back to `faculty_uid` + `author_position` when the planned `first_author_*`/`last_author_*` fan-out fields are missing from TOPIC# rows. Per-PMID dedup now keeps the row with the strongest author payload (both > one > none) instead of the arbitrary first-encountered row. CLI orchestrator gracefully skips a subtopic if its lede can't be generated, instead of aborting the whole run. |
| `539821c` | Built `spotlight/author_resolver.py` — queries `analysis_summary_author` for the pool's PMIDs, returns `{pmid: AuthorPair}` only for PMIDs with both first AND last positions filled by WCM faculty. Wired into `rank_pool` via the new `author_resolver=` parameter. Operator decision (B1 strict): a paper enters the pool ONLY when both authors are WCM faculty. Live result: 179/1671 PMIDs (10.7%) pass B1 strict; pool still fills 10 spotlights across 10 distinct parents. |
| `539821c` | Two more lazy-init bugs in `spotlight/review_queue.py` (`write_review_entry`, `list_pending`, `set_status`). Same defect class as `7618db1`. |

### New module

`spotlight/author_resolver.py` — MariaDB join for first/last author resolution. Lazy SQLAlchemy engine mirrors `import_enrichment.get_db_engine`. Byline parser handles the `((author))` WCM marker and the `...` truncation sentinel.

### Test coverage

- 109/109 spotlight tests passing (107 baseline + 2 regression tests for top-N cap and parent_lookup)
- Live `--dry-run` smoke: passes
- Live `--dry-run-full` smoke: passes (10/10 spotlights, schema-valid, ~$0.30 Bedrock spend per run, ~64s wall)

---

## Known issues and follow-ups

### Open issues

1. **[wcmc-its/ReciterAI#1](https://github.com/wcmc-its/ReciterAI/issues/1)** — Lede generator emits chain-of-thought preamble when paper-subtopic mismatch is detected, exhausting the critic budget. Severity low (review queue catches it; 9-10/10 spotlights still produced). Three fix candidates documented in the issue. Defer until next time the spotlight pipeline is touched.

### Operational deferrals

- **Bucket policy.** `docs/aws-bucket-policy-artifacts.json` template assumed a cross-account SPS Lambda role. Apply it when the SPS team confirms whether they're cross-account (need their role ARN) or same-account (don't need a bucket policy at all — grant read via SPS Lambda's identity-side IAM).
- **`displayName` quality.** Authors currently render as PubMed byline format (`Lyden D`, `Varmus H`). SPS may want richer display names (`Daniel Lyden, MD, PhD`). Two paths: (a) join WCM directory at SPS-side ETL, (b) extend `analysis_summary_author` ingest to populate full displayName. Punted from B1 since it's a UI concern, not a data-correctness one.
- **`SPOTLIGHT_CONFIG#sensitive_tags` curation.** Seeded with the 5 patterns from the discuss-phase. Operator (Paul) can add/remove patterns at any time via `aws dynamodb put-item` or `update-item`. List is intentionally not in git (CLAUDE.md security rule).

### Audit-flagged longer-term work

- **Author fanout in `import_enrichment.py`.** TOPIC# rows lack the `first_author_personIdentifier` / `first_author_displayName` / `last_author_personIdentifier` / `last_author_displayName` fields the original Phase 6 plan assumed. Today, `pool_ranker._extract_paper` falls back to `faculty_uid` + `author_position` (per-row, single faculty) and `author_resolver` does a MariaDB join for the full pair. Working but hot path. Ideal long-term: extend `import_enrichment.py` to compute and write all four fields at enrichment time, then drop the runtime MariaDB dependency.
- **Subtopic-paper categorization.** Issue #1 surfaced a case where papers about sex/gender disparities ended up under `health_racial_ethnic_disparities_access`. That's a Phase 4 LLM categorization concern, not Phase 6.

---

## How to consume the artifact (SPS team)

The canonical handoff brief is in `docs/sps-spotlight-handoff.md` and the runnable TypeScript ETL reference is in `docs/sps-spotlight-etl-reference.ts`. Short version:

1. Fetch `s3://wcmc-reciterai-artifacts/spotlight/latest/spotlight.json` (and `manifest.json` for sha256/version sanity).
2. Validate against `s3://wcmc-reciterai-artifacts/spotlight/latest/spotlight.schema.json` (Draft 2020-12).
3. The artifact has `spotlights[1..10]`. Each spotlight carries:
   - `subtopic_id`, `label`, `display_name`, `short_description`, `parent_topic`
   - `lede` (25-35 word institutional-voice paragraph, no individual faculty named)
   - `papers[2..3]`: each with `pmid`, `title`, `journal`, `year`, `first_author`, `last_author`
   - Each `Author` carries `personIdentifier`, `displayName`, `position`
4. `pool_snapshot[≤50]` — the top-50 subtopics with their pool_score. Useful for "see more" / dashboard / audit views.
5. Cache. The artifact updates via `--publish` runs (manual cadence, no fixed schedule yet).

If the schema or contract needs to change, update `docs/spotlight.schema.json` + `docs/spotlight-contract.md` and bump `schema_version`. We have not had to do this yet.

---

## How to re-publish

Same as the first publish — no special setup once the bucket exists:

```bash
# Smoke first (~$0 + DynamoDB read only):
python3 backfill_spotlight.py --dry-run

# Full smoke (~$0.30 Bedrock, no S3 write):
python3 backfill_spotlight.py --dry-run-full

# Real publish:
python3 backfill_spotlight.py --publish
```

Each `--publish` writes:
- New version key `s3://wcmc-reciterai-artifacts/spotlight/v{date}/`
- Overwrites `s3://wcmc-reciterai-artifacts/spotlight/latest/`
- 10 SPOTLIGHT_HISTORY# rows (rotation decay)

Operator workflow flags:
- `--review-queue` — list pending review entries
- `--approve <subtopic_id>` / `--reject <subtopic_id>` — transition state machine
- `--regen-only <subtopic_id>` — re-roll one spotlight without touching the others
- `--reset-history` — truncate SPOTLIGHT_HISTORY# (after annual hierarchy recompute when subtopic IDs rotate)

---

## What's next strategically

| | What | Effort |
|---|---|---|
| **Phase 3 — Chat UI + Evaluation** | The only remaining unplanned phase in v1. Chat runtime (Phase 2) is fully wired up server-side; needs UI in Publication Manager + golden-set eval harness. | Several plans, multiple waves. Start with `/gsd-ui-phase 3`. |
| SPS-side ETL | Implement `docs/sps-spotlight-etl-reference.ts` (or its native-stack equivalent) in the Scholars Profile System repo. Render under "Selected Research". | SPS team's call. Our side is done. |
| Bucket policy attachment | When SPS confirms account topology. | 5 minutes once we have the ARN. |
| Issue #1 fix | Lede preamble suppression. | 30 min — small prompt change + targeted test. |

The strategic next move is Phase 3, unless the SPS-side rendering work is the higher priority for shipping v1 to users.

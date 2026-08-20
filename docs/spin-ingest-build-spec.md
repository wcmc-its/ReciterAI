# SPIN Ingest — Full Build Spec

**Status:** proposed, awaiting approval. **Date:** 2026-06-26. Follows
`docs/spin-ingest-spike-findings.md` (conditional GO). Mirrors the existing
**curated** source path (`pipeline_grants/ingest_curated.py` + `wcm_curated.py`).

## Goal
Add SPIN (InfoEd) as a 3rd `pipeline_grants` source: pull biomed-relevant
foundation opportunities missing from grants.gov, filter to material / US-eligible
/ topical, and flow them through the **existing** normalize→gate→score→prestige→
persist pipeline into `GRANT#` items — indistinguishable downstream (SPS indexes
them like any other opp).

## Architecture — 2 new modules + 1 config, everything else reused
The curated path already proved the "second source" shape. SPIN = a third, same shape.

### `pipeline_grants/spin.py` (source module)
- `SpinClient` — the spike client, promoted (~40 lines). Auth from `SPIN_PUBLIC_KEY`
  /`SPIN_SIGNATURE`/`SPIN_INSTITUTION_CODE` (env, never logged). `search()`,
  `fetch_by_ids()`. Endpoint `https://spin.infoedglobal.com/Service/ProgramSearch`.
- `normalize_spin(row, *, ingested_at) -> Opportunity` — field map below.
- `keep_opportunity(row) -> (bool, reason)` — the noise gates (below). Pure, unit-testable.

### `pipeline_grants/ingest_spin.py` (orchestrator — mirrors `ingest_curated.py`)
`build_items(targets, *, bedrock, ingested_at)`:
1. For each target funder: `client.search(['[SOLR]spon_name:"<canonical>"'])` (+ synonyms).
2. `normalize_spin(row)` → `Opportunity` (`source="spin"`).
3. Dedup by `opportunity_id` (same as curated).
4. **Exclusion gate** — `load_excluded_ids()` (source-agnostic, reused as-is).
5. **Noise gates** — `keep_opportunity(row)`; drop + count by reason. **Before scoring**
   so we never pay Bedrock for junk.
6. `scoring.score_grant_text(...)` — production topic scorer (shared taxonomy).
7. **Verdict** — run the grants_gov LLM judge (`judge_opportunity`) for `is_research` +
   `appeal_by_stage` (SPIN is *not* human-vetted, unlike curated → don't synthesize). [decision #2]
8. `build_grant_item(opp, dense, taxonomy_version, judge)` — prestige (incl. `sponsor_tier`)
   computed inside, unchanged.
`run(dry_run)` → `put_grants` + `publish_opportunities_artifact` (reused). `main()` CLI
with `--dry-run`, `--funder`, `--limit`.

### `config/spin_target_funders.json` (BUILT — first pass done)
**113 funders** resolved to canonical SPIN sponsors via the authoritative directory (below),
each `{funder, spin_name, spon_code, relevance, type}`. Seeded from the AI xlsx (non-federal,
non-NIH-IC, relevance≥8). Residual 27 classified (19 present-but-unmatched with directory
candidates listed, 8 likely true gaps e.g. Hartwell) for a ~15-min human pass.

**Sponsor directory (discovered):** `GET …/Service/SponsorList` → all **16,131** sponsors with
`spon_code` (id), `spon_name` (canonical), `spon_abbr`, `ror_id`. Resolve funder names →
canonical sponsor names against this.

**Pull method (verified):** `[SOLR]spon_name:"<exact canonical spin_name>"` returns ONLY that
sponsor's programs (Fox → 4, all Fox). `spon_code` is NOT a queryable program field — pull by
the exact quoted canonical name. A directory sponsor may have 0 current programs (e.g.
Lustgarten) — fine.

### Reused unchanged
`scoring` (`score_grant_text`, `load_taxonomy`, `build_index`), `persist`
(`build_grant_item`, `put_grants`, `publish_opportunities_artifact`), `prestige`
(`sponsor_tier` already shipped), `exclusions`, `models.Opportunity`, the judge in `ingest.py`.

## Field mapping (`normalize_spin`)
| `Opportunity` | SPIN field | note |
|---|---|---|
| `title` | `prog_title` | |
| `sponsor` | `spon_name` | |
| `synopsis` | `synopsis` or `objective` | |
| `program_type` | `project_type` (list→primary) | also feeds the type gate |
| `mechanism` | `""` (try `_activity_code` on title) | foundations rarely have one |
| `award_ceiling`/`estimated_funding` | **None** | SPIN exposes no amount → prestige `size_bucket` abstains |
| `due_date` | `deadline_date` | |
| `status` | derived (open if deadline future) | |
| `eligibility_raw` | `applicant_type` + `geographic` | |
| `cfda_list` | `cfda` | |
| `source`/`source_id`/`opportunity_id` | `"spin"` / `id` / `spin:<id>` | |
| `source_url` | `programurl` or `sponwebsite` | |

## Noise gates (`keep_opportunity`) — the only net-new filtering
Applied before scoring; each drop logged by reason.
1. **`project_type` allow-list** (the clean structured lever from the spike). Keep
   {Research Grant, RFA (NIH), Fellowship, Collaborative Project, Federal Contract Opp};
   drop {Prize or Award, Conference Attendance, Student Scholarship, Artistic/Cultural,
   Exhibits/Collections, Equipment}. [decision #1 — exact allow-list]
2. **`geographic` US-eligibility.** Keep if includes `United States`/`No Restrictions`/`Any`
   or empty; drop if exclusively non-US. Cross-check `project_location` (the spike saw an
   Australian award slip through on `geographic="No Restrictions"`). [decision #6]
3. **status.** Drop suspended ("(Temporarily Suspended)" in title) / past-deadline.
4. **Reused downstream:** `is_honorific` (already on the item), per-opp **topic-match**
   (the real biomed-relevance filter), `exclusions`.

## Testing
- Unit: `normalize_spin` mapping; `keep_opportunity` per gate (project_type / geo / status)
  with fixtures; `opportunity_id` format. No network.
- **Fixture:** one captured SPIN response (sanitized — no creds, no PII) for offline tests.
- Staging dry-run: `ingest_spin --dry-run`, eyeball built items + per-reason drop counts.

## Rollout (operator, staged)
1. Dry-run a few funders → review survivors.
2. Ingest to **STAGING** DDB → SPS `etl:dynamodb` → reindex → eyeball in SPS.
3. Scheduled pull (Fargate) — separate, only after validation. **Confirm SPIN ToS on
   scheduled/bulk API pulls first.** [decision #5]

## Out of scope (v1)
- Synopsis-text amount parsing / size floor — defer (project_type + geo catch most $-noise).
- Funder relevance as a runtime ranking signal (pre-filter only).
- Automated scheduler (manual/validated first).
- The legacy scrape+extract prototype (superseded by the API).

## Open decisions (owner)
1. `project_type` allow-list — are fellowships/travel/seed "real opportunities" here?
2. Run the LLM judge vs synthesize `is_research=True`.
3. Size floor: skip vs synopsis-parse.
4. Funder list: human-review pass + relevance bar to seed from the AI xlsx.
5. SPIN ToS on scheduled bulk pulls.
6. `geographic` "No Restrictions" → US-eligible? (needs `project_location` cross-check).

## Effort
~1.5–2 dev-days: `spin.py` (~0.5d, client done) + `ingest_spin.py` (~0.5d, mirrors curated)
+ gates/config/tests (~0.5d) + staging dry-run validation (~0.5d). Rollout = operator.
Isolated, additive — no change to grants_gov/curated paths or existing items.

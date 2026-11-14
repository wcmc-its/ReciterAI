# Grant Recommendation — Approach and Architecture

Engineering reference for the grant-recommendation system: how funding opportunities are
ingested, scored, and gated in ReciterAI, how they reach the Scholars Profile System (SPS),
and how SPS matches them to (and from) researchers.

Grounded on `ReciterAI@origin/main` and `Scholars-Profile-System@origin/master` as of
2026-07-11. Code references are `file:line` on those branches.

## 1. What the system does

Grant recommendation connects **funding opportunities** to **WCM researchers** in two directions:

- **Forward — "Grants for me"** (scholar-facing): given a scholar, rank the open opportunities
  that best fit their research. Surfaced on the `/edit` profile as a card.
- **Reverse — "Find researchers"** (admin-facing): given an opportunity, rank the WCM
  researchers who best fit it. Superuser/developer only.

A "grant" here is an **opportunity** (a call for applications), not an awarded grant. Awarded
grants are a separate corpus (the InfoEd-sourced `funding` index) and are out of scope except
where they share the sponsor-canonicalization concern (§7).

The producer (ReciterAI) is the source of record for the opportunity corpus. SPS is a pure
consumer: it never writes opportunities, only projects, indexes, matches, and renders them.

## 2. Architecture at a glance

```
  SOURCES                 PRODUCER (ReciterAI)                 BRIDGE                 CONSUMER (SPS)
  ───────                 ────────────────────                 ──────                 ──────────────
  Grants.gov API  ┐
  WCM curated CSV ┤─▶ ingest → normalize → dedupe →     DynamoDB          nightly ETL          MySQL `opportunity`
  staff URL subs  ┤     regex gate → LLM judge →     ▶  `GRANT#{id}`  ─▶  (1) project      ─▶   (Prisma/Aurora)
  (spin: WIP)     ┘     dense score → prestige →          /META            (2) reindex           │
                        [match-compile] → persist                          + alias swap          ├─▶ OpenSearch
                                    │                                                             │   `scholars-opportunities`
                                    └─▶ S3 opportunities.json artifact                            │
                                                                                                  ▼
                                                              forward matcher (OpenSearch candidates → app re-rank)
                                                              reverse matcher (Prisma topic-vector)
                                                              → "Grants for me" card / "Find researchers"
```

Key boundary facts:

- One `GRANT#{opportunity_id}/META` DynamoDB item = one opportunity. `opportunity_id` is
  `"{source}:{source_id}"`.
- A new/changed item is **invisible to SPS until the next projection AND reindex** run — the
  recurring "reaches SPS on next projection + reindex" caveat.
- The honorific flag is `is_honorific` (snake_case) in DynamoDB and is projected to
  `isHonorific` (camelCase) in MySQL/OpenSearch.

## 3. Sources

Four source strings; **three ingest entrypoints on `origin/main`**.

| Source | Entrypoint | Fetch | Notes |
|---|---|---|---|
| `grants_gov` | `pipeline_grants/ingest.py::run` | Public Grants.gov REST API (no key), `grants_gov.py` | Paginated; posted + forecasted |
| `wcm_curated` | `ingest_curated.py::run` | Curated enriched CSV (`data/wcm_curated_opportunities_enriched.csv`) | Curation is the vetting; synthesizes `is_research=True`, skips the regex type-gate |
| `manual_url` | `ingest_submissions.py::run` | Drains `PK="SUBMISSION"` DynamoDB items (staff-pasted URLs from SPS `/edit`) → guarded fetch → LLM page→programs extraction | Runs the LLM judge but no regex gate (staff legitimately submit prize pages) |
| `spin` | none on `origin/main` | — | Priority slot in `dedupe.SOURCE_PRIORITY` only. Live-corpus `spin` rows came from an **unmerged WIP ingester** (see `config/spin_target_funders.json`, `docs/spin-ingest-*`); not yet on master. |

All three live entrypoints converge on the same `persist.build_grant_item → put_grants → S3`
path, so rows are indistinguishable downstream apart from the `source` field.

## 4. Ingest pipeline (`ingest.py::run`, grants_gov path)

Setup: load taxonomy + topic index; `BedrockClient(read_timeout=90)` (a short read timeout so a
hung Bedrock socket can't stall the serial loop); build the cross-source dedupe index; load the
match-compile vocab only under `--compile-match`.

Fetch: `hits = islice(grants_gov.search_all_opportunities(keyword, rows), limit)`
(`ingest.py:51`). `search_all_opportunities` paginates `startRecordNum` to `data.hitCount` and
includes forecasted NOFOs by default (`statuses="posted|forecasted"`). `--rows` is the **page
size**; `--limit` caps total opportunities fetched (None = all; a full sweep is ~1,850 today).
This is #291 — before it, a single page of `statuses="posted"` saw ~200 of ~1,850 (~11%) and
never ingested forecasted opportunities.

Per opportunity, each stage is wrapped in skip-and-continue so one transient failure drops that
item, not the batch:

1. **normalize** — `normalize_grantsgov(fetch_opportunity(id))` → `Opportunity`.
2. **exclusion** — `load_excluded_ids` membership (held out of matching).
3. **cross-source dedupe** — `corpus_index.blocking_id(...)` skips an opportunity whose
   normalized `(title, sponsor)` key is already held by an equal-or-higher-priority source,
   **before any Bedrock spend**.
4. **regex gate** — `denoise.regex_gate` (cheap deterministic drops).
5. **LLM judge** — `denoise.judge_opportunity` drops non-research and off-domain items.
6. **dense score** — `scoring.score_grant_text` → per-topic `{score, rationale}`.
7. **primary-topic floor** — drop if the top topic score < 0.3 (`thresholds.json`); an
   off-domain force-fit guard (#293).
8. **match-compile (optional)** — `_compile_match` only under `--compile-match`, else
   `(None, None)`.
9. **build item** — `build_grant_item(...)`.
10. **persist** — batch into `pending`, flush via `put_grants` every 25.

Finalize: final `put_grants` + `publish_opportunities_artifact`. Returns
`{fetched, kept, failed, persisted, artifact_version}`.

## 5. Normalization, dedupe, and the accuracy gates

The corpus is noisy (Grants.gov lists everything; curated/submissions are human-entered), so
several independent gates run before an item is eligible to be recommended.

### 5.1 Normalize (`normalize.py`)

- **`Opportunity`** fields (`models.py:10`): id, source, source_url, sponsor, title, synopsis,
  program_type, mechanism, award_ceiling/floor, estimated_funding, number_of_awards, open_date,
  due_date, status, eligibility_raw, cfda_list, ingested_at.
- **`_select_sponsor`** — first clean agency across `synopsis.agencyName` → `agencyDetails` →
  `topAgencyDetails`, rejecting **contact blobs** (a newline, or a grants-role title like "Grants
  Management Specialist") — #294 item 1.
- **`canonical_sponsor`** — collapses known near-variant sponsor labels (ACS `(ACS)`/`, Inc.` →
  American Cancer Society; `&`→`and`; two typos) via a short explicit map. Applied at **persist
  time** (`persist.py:54`), not in normalize — #294 item 3.
- **`status`** = `"forecasted" if data.forecast else "posted"`; **`due_date`** parsed from
  `responseDate` (empty → `""`, no crash — matters for forecasted opportunities with no date).

### 5.2 Dedupe (`dedupe.py`)

- **`norm_tokens`** — lowercase, split, minus stopwords. Stopwords strip articles/prepositions
  **plus funding-generic terms** (`research award grant program fellowship foundation fund …`),
  so it is a **dedup key, not a display key** (never reuse it to build a facet label).
- **`SOURCE_PRIORITY`** = `grants_gov 3 > spin 2 > wcm_curated 1 > manual_url 0`.
- **`CorpusKeyIndex.blocking_id`** — ingest-time guard; blank sponsor on either side matches any.
- **`find_duplicates` / `run`** — batch sweep, dry-run by default, `--apply` deletes losers
  (drop-one, no merge). Note: deleting a `GRANT#` row does **not** remove the already-projected
  SPS/OpenSearch row (upsert-only both sides) — an operator must sweep the SPS side too.

### 5.3 Research / off-domain gate (`denoise.py`)

- **`regex_gate`** — drops on title type (travel/conference/prize/medal/lectureship/equipment/…)
  or an expired deadline (past `due_date` and not `continuous`).
- **`judge_opportunity`** — one **Haiku** call returning `is_research`, `is_biomedical_relevant`
  (fail-open — only an explicit `False` drops), `reason`, `appeal_by_stage` (5 career buckets),
  and a structured `eligibility` block (#290, fail-open → None).

### 5.4 Honorific gate (`prestige.py::is_honorific`) — accuracy-critical

This is the **sole** honorific gate for both consumers (SPS removed its redundant title regex in
#1628 — it had zero true positives and dropped 18 applyable items). An honor (prize/medal/named
award) must never be recommended as an applyable grant.

- **Tier 1** — `_HONORIFIC_RE` (prize/prix/medal/lectureship/laureate) OR `_RECOGNITION_RE`
  (designation / named-endowed chair-professorship / election to / hall of fame) → always
  honorific. `_RECOGNITION_RE` is issue #289 (PR #324), added for the "MACC designation" leak.
- **Tier 2** — the bare-"Award" heuristic: `_AWARD_RE` matches AND the item has no `mechanism`
  AND doesn't match `_APPLYABLE_RE` (fellowship/scholarship/pilot/career-dev/…) AND its sponsor
  is **not** on the applyable-sponsor allowlist.
- **Sponsor-allowlist veto** — `_APPLYABLE_SPONSORS` (DoD, Hartwell, Damon Runyon, Burroughs
  Wellcome, ACS, AHA, Simons, disease foundations, …), issue #314 (PR #315). Vetoes ~41
  award-tier items, zero true honors. Extended in #289 (PR #332) to add the federal biomedical
  agencies **NIH and HRSA** — their "…Award" listings (Institutional Network, MERIT, MIRA,
  Kirschstein NRSA) are open competitions, not honors. NSF is deliberately excluded: its
  mis-flagged "…Award" items are off-domain (engineering/archaeology), which #293's biomedical
  gate suppresses, not this one.

Known ceilings: the gate **cannot** help blank-sponsor rows (~9 real grants stay mis-flagged);
title text alone can't separate honor from applyable grant ("Troland Research Award" vs "Hirschl
Research Award" are identical). The durable fix is a typed `opportunity_type` at ingest (#290).

## 6. Scoring, prestige, and match-compile

### 6.1 Dense topic scoring (`scoring.py`)

`score_grant_text` **reuses the production publication scorer**
(`score_publications.score_one_publication`) with a no-op Dynamo client so its side-effect writes
are discarded, feeding the opportunity as a pseudo-publication `{title, synopsis}`. Output is a
dense `{topic_id: {score, rationale}}` vector against `taxonomy_v2`. The argmax topic becomes
`primary_topic_id`; the full vector is stored for cosine matching downstream.

### 6.2 Prestige (`prestige.py::compute_prestige`)

Blends three tiers, **renormalized over present signals only** (a missing input abstains, never
injects a 0): `mechanism_tier` (0.4), `size_bucket` (0.2), `sponsor_tier` (0.4). Floor 0.3 when
nothing is known. Label: ≥0.8 Flagship, ≥0.55 Major, else Standard. `size_bucket` uses a fixed
$10k–$10M log anchor (not corpus min-max) for cross-ingest stability. NIH is deliberately absent
from the sponsor-tier map (it would double-count mechanism). `selectivity` is deferred (null).

### 6.3 Match-compile (`match_compile.py`) — gated off

`compile_match` produces, per opportunity, a `match_dsl` (require/penalize substring patterns over
the subtopic vocabulary) and a `match_query` (weighted BM25 terms) via two **Sonnet** calls. It is
**off by default** (`--compile-match`) — two extra Sonnet calls per grant for data nothing reads
until the SPS reverse subtopic-grain path ships (§7.4). `match_rel` (dense subtopic→pmid cosine)
is **backfill-only** (`backfill_rel.py`), never compiled at ingest.

## 7. Persistence and the bridge to SPS

### 7.1 DynamoDB item (`persist.build_grant_item`)

PK `GRANT#{opportunity_id}`, SK `META`. Always writes: source, source_url, sponsor (through
`canonical_sponsor`), title, synopsis, status, due_date, open_date, eligibility_raw, cfda_list,
taxonomy_version, ingested_at, `topic_vector` (list, score-desc), `primary_topic_id`, `is_research`,
`is_biomedical_relevant`, `appeal_by_stage`, `prestige` (map), **`is_honorific`** (bool).
`award_ceiling/floor`, `estimated_funding`, `number_of_awards`, `mechanism` are **omitted when
null**. `match_dsl`/`match_query` and the `eligibility` map are written only when present.

### 7.2 Preserve-on-put (`put_grants`, #292)

Writes are **upsert-only** with a preserve step: before writing, `fetch_preserved_attrs`
(BatchGetItem) merges any existing `PRESERVED_ATTRS = (match_dsl, match_query, match_rel)` into an
outgoing item that lacks them. These three are the expensive Bedrock products the normal ingest
does not recompute; an item carrying its own value (compile-match on) wins. Everything else
(including prestige/is_honorific) is recomputed each ingest, so new-wins is correct there. This is
why a scheduled re-ingest must not run before preserve-on-put is in place.

### 7.3 S3 artifact + the projection/reindex bridge

`publish_opportunities_artifact` writes `grants/{version}/opportunities.json` + manifests (lean
rows: id, title, sponsor, due_date, primary_topic_id). SPS ingests the DynamoDB corpus, not the
artifact, in **two distinct nightly ETL steps**:

1. **Project** (DynamoDB `GRANT#` → MySQL `opportunity`): `etl/dynamodb/index.ts` "Block 7" scans
   `begins_with(PK,"GRANT#")`, maps via `grant-opportunity-mapper.ts`, idempotent upsert on
   `opportunityId`, drops non-research / missing-field rows.
2. **Reindex** (MySQL → OpenSearch): `etl/search-index/index.ts::indexOpportunities` reads
   `opportunity where isResearch:true`, shapes each via `buildOpportunityDoc` (`lib/search.ts`),
   bulk-indexes a fresh concrete index, then **alias-swaps** `scholars-opportunities` behind a
   health gate (`assertOpportunitiesIndexHealth`). SPS also flat-fills MeSH from title+synopsis
   here, since ReciterAI emits none.

An integrity guard fails the ETL if `scholars-opportunities` is empty while `opportunity` has rows.

### 7.4 Consumer matchers (SPS)

**Forward** (`lib/api/match-opportunities.ts`) is two-stage: Stage 1 retrieves ≤200 candidates
from OpenSearch under the hard gates plus a coarse topic `should` boost; Stage 2 re-ranks in the
app over distinct axes so query-time sort/weight changes don't require re-matching.

- Hard candidate gates (`candidateQueryFilters`): `status ∈ {open, forecasted, continuous}`;
  a dueDate gate (`dueDate ≥ now` OR `status=continuous` OR no dueDate); eligibility
  (`us_eligible` + stage-derived faculty/postdoc); and the honorific exclusion
  `must_not term isHonorific:true`.
- Axes and weights (`DEFAULT_WEIGHTS`): **topicAffinity 1.0** (cosine of the scholar's
  recency/author-position-weighted topic vector against the opportunity vector), stageAppeal 0.5,
  meshOverlap 0.25, deadlineProximity 0.1, **prestige 0**. Stage and prestige *multiply*
  topicAffinity. The dominant signal is dense topic cosine — not `relevanceScore`/`match_dsl`.

**Reverse** (`lib/api/match-researchers.ts`, admin) defaults to a Prisma topic-vector aggregation
across the opportunity's top topics. A **flag-gated subtopic-grain path** uses the per-opportunity
`match_dsl` — but with prod `match_dsl` un-backfilled (0), the flag no-ops to the byte-identical
topic-vector path (this is why subtopic evidence / topic chips are "blocked when match_dsl=0").

### 7.5 Scholar-facing card (`components/edit/grant-recs-card.tsx`)

Renders the forward-matcher results. Per row: title + prestige badge, `sponsor · mechanism ·
deadline · up to $X`, explanation chips ("Matches your work on ⟨label⟩ (N pubs)"), and a
**relative** fit tier (`fitTier`, computed against the strongest match) — a raw score never
renders (house rule). Status labels: `continuous` → "Rolling · continuous", forecasted with no
date → "Forecasted · date TBD", else "Due {date}". Details disclosure lazy-loads the four per-axis
meters.

### 7.6 Feature flags and current state (prod)

| Flag | State | Effect |
|---|---|---|
| `SELF_EDIT_GRANT_RECS` | staging **on** / prod **dark** (`cdk app-stack.ts`) | Gates the whole `/edit` "Grants for me" rail. Prod dark until the prod GRANT# mirror lands (#269) + go-live sign-off. |
| `PRESTIGE_AXIS_WEIGHT` | **0** both envs | Prestige badge + sort render, but prestige has no effect on match order. |
| `GRANT_MATCHER_SUBTOPIC_GRAIN` | staging on / prod off | No-ops without per-opportunity `match_dsl`. |
| `GRANT_MATCHER_ABSTAIN_FLOOR` | 0 (off) | Abstain flag only. |

API routes: `GET /api/scholars/[cwid]/opportunities` (forward, **public per-cwid**, cacheable);
`GET /api/opportunities/[id]` (detail, public); `GET /api/opportunities` (browse) and
`/api/opportunities/[id]/researchers` (reverse) are **admin-only**. The main `/api/search` does
**not** surface opportunities — only awarded-grant signals.

### 7.7 Sponsor facet gap

The live sponsor **facet** is on the awarded-grants `funding` index, built from
**canonicalized** short names (`lib/sponsor-canonicalize.ts`). Opportunities carry a **raw**
sponsor and currently have no sponsor facet. #294's ingest-time `canonical_sponsor` reduces raw
fragmentation on the producer side; if a sponsor facet is ever built over the opportunity index,
that canonicalization is what keeps it from fragmenting.

## 8. Accuracy program, known gaps, and roadmap

The recurring theme is that **title text and dollar amounts do not cleanly separate an honor from
an applyable grant**; sponsor and (eventually) a typed field do.

- **#291 — done.** Pagination + forecasted (§4). ~11% → full corpus coverage.
- **#294 — done.** Contact-blob sponsor (item 1), canonicalization (item 3), + a one-time backfill
  of 12 live rows. Item 2 is a curation data gap, not an ingest bug.
- **#289 side-1 — done (PR #332), reframed from "money-aware gate."** A live-corpus audit of the
  227 honorific-gated rows showed the high-*dollar* set is dominated by genuine prizes (Shaw,
  Kavli, Gruber, VinFuture, …), so a "un-suppress ≥ $500k" gate would wrongly surface ~15 real
  honors — money does not discriminate. The clean recoverable set was the `grants_gov` rows, all
  real NIH/NSF/HRSA opportunities mis-flagged by "Award" in the title and money-independent
  (some at $0). The fix keys on **applyability, not dollars**: NIH and HRSA were added to the
  `_APPLYABLE_SPONSORS` allowlist (§5.4). NSF was deliberately left out — its mis-flags are
  off-domain, so #293's biomedical gate owns them. Live rows correct on the next
  `backfill_prestige.py` / re-ingest.
- **#290 — structured eligibility + typed opportunity type.** The judge already extracts an
  eligibility block; the durable honorific fix is a typed field. Pre-ship gate: a Haiku-vs-Sonnet
  parity check on the eligibility judge.
- **#293 — off-domain gating.** The 0.3 primary-topic floor is the current guard.
- **Stale live `is_honorific`.** The producer only stamps newly-ingested items, so live rows can
  predate the #314 veto / #289 recognition regex. `backfill_prestige.py` (dry-run default,
  `--apply`) recomputes prestige + is_honorific + recovers mechanism from fields already on the
  item (no re-score) — this is the migration that corrects stale flags. Any accuracy measurement of
  the live corpus must account for this.

## 9. Operational quick reference

- **Full ingest:** `python -m pipeline_grants.ingest --rows 200` (paginates all posted+forecasted;
  add `--limit N` to bound a run's Bedrock cost).
- **Backfills** (all dry-run by default, `--apply` to write): `backfill_prestige.py` (stale
  prestige/is_honorific), `backfill_rel.py` (dense `match_rel`), match-compile via
  `--compile-match` on ingest or the dedicated backfill.
- **Dedupe sweep:** `pipeline_grants.dedupe` `run(apply=False)`; remember to sweep the SPS side too.
- **Golden rule:** a producer change reaches SPS only after the next **projection + reindex**;
  never assume a fresh `GRANT#` write is live in the matcher.

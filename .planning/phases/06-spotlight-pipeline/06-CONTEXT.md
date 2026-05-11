# Phase 6 — Spotlight Pipeline & Publishing Contract

**Captured:** 2026-05-07 (initial pre-planning context)
**Updated:** 2026-05-07 (`/gsd-discuss-phase 6` — 10 open questions resolved, 4 new locked decisions surfaced from second-order implications)

## Domain

A versioned, schema-validated S3 artifact (`spotlight.json`) carrying 10 LLM-authored
editorial ledes for top-ranked WCM research subtopics, refreshed weekly via operator-run
publish. SPS consumes the artifact via ETL to render the home-page interactive spotlight
component (component swap is SPS-side follow-up work, out of scope for this phase).

Mirrors Phase 5's S3 + manifest + IAM + sha256 publishing pattern. Reuses
`utils/s3_client.py`, the schema authoring approach, the contract-doc structure, and
the SPS handoff brief structure established in Phase 5.

## Pipeline stages

| Stage | Output | Logic |
|---|---|---|
| 1. Pool ranker | top-50 subtopics | `score = Σ (impactScore)` over publications **published in the last 24 months** (hard cutoff). No within-window recency weighting. |
| 2. Rotation selector | 10 active spotlights | One per parent topic enforced. `selection_score = pool_score × (1 - exp(-weeks_since_last_shown / 12))`. Cold-start: never-shown subtopics get multiplier = 1 (full pool_score). Last-shown state in DynamoDB. |
| 3. Lede generator | 10 ledes | Bedrock Sonnet + voice-constrained prompt at `prompts/spotlight_synopsis_v0.md`. Grounding = `synopsis` + `impactJustification` from 2-3 selected papers per spotlight (NOT titles/journals — richer signal already in DynamoDB enrichment). |
| 4. Critic pass | validated ledes or flagged | **Hybrid critic.** Code-checks deterministic constraints (em-dash, banned-word list, length bounds, missing "WCM scholars are X-ing" tic via regex). Single LLM call only for tone/voice/marketing-language judgment. Auto-regen up to 3 retries. Persistent failure → `SPOTLIGHT_REVIEW#` queue. |
| 5. Editorial gate | publish-ready set | Sensitive-topic tag match (DynamoDB `SPOTLIGHT_CONFIG#sensitive_tags`) → `SPOTLIGHT_REVIEW#` queue. Non-sensitive AND critic-clean → auto-publish. |
| 6. Publish | spotlight.json + schema + manifest | Reuses Phase 5 publish path. New S3 prefix: `s3://wcmc-reciterai-artifacts/spotlight/v{date}/` and `latest/`. **Prerequisite:** bucket migration from `wcmc-reciterai-hierarchy/` (see Prerequisites below). |

## Locked decisions

### Ranking & rotation

| Decision | Value |
|---|---|
| Pool size | 50 (configurable, revisable post-launch) |
| Selection size | 10 (matches mockup `home-spotlight-interactive.html` card grid) |
| Recency model | **Hard cutoff at 24 months.** Pubs older than 24 months contribute zero; pubs within the window contribute `impactScore` directly (no within-window decay). Captures the 18-24 mo biomedical development cycle without ranking noise from unpublished-decay weighting. |
| Rotation decay | `selection_score = pool_score × (1 - exp(-weeks_since_last_shown / 12))`. **τ = 12 weeks.** A spotlighted subtopic is ~63% recovered after a quarter, ~95% after 9 months. Comfortable rest-then-compete cadence. |
| Cold-start | Subtopics never shown → multiplier = 1 (formula handles this naturally; first publish picks top pool_score with parent-diversity). |
| Parent diversity | Enforced. One subtopic per parent topic at selection (matches mockup). |

### Storage & publish path

| Decision | Value |
|---|---|
| Rotation state location | **DynamoDB `SPOTLIGHT_HISTORY#{subtopic_id}`** records: `{subtopic_id, last_shown_at, shown_count, last_shown_publish_id}`. Per-subtopic upserts on publish. Same DB everything else uses. |
| S3 publish prefix | **`s3://wcmc-reciterai-artifacts/spotlight/v{date}/` and `s3://wcmc-reciterai-artifacts/spotlight/latest/`.** Bucket name reads correctly for both Phase 5 hierarchy and Phase 6 spotlight. |
| Bucket migration prerequisite | **NOT optional for Phase 6 publish.** Create `wcmc-reciterai-artifacts`, copy current `wcmc-reciterai-hierarchy/hierarchy/` contents (incl. `latest/manifest.json`), update SPS ETL endpoint config, retire old bucket. **Open question for planner:** does this migration belong as the first plan in Phase 6, or as a Phase 5 follow-on / a separate Phase 5b? Researcher should compare blast radius. |
| Artifact body | **10 active spotlights + 50-subtopic pool snapshot.** Pool snapshot is `{subtopic_id, pool_score, parent_topic, was_selected: bool}` per top-50 entry. Adds transparency / audit / future "how this works" surface. SPS ignores pool field for v1 home-page render but it's available for downstream tooling. |
| Per-spotlight provenance | NOT in artifact for v1. Provenance (PMIDs used, retry count, critic verdicts) lives in DynamoDB review/history records, not in the published artifact. Keeps artifact schema lean; adds back later if editorial trust requires it. |

### Critic & manual review queue

| Decision | Value |
|---|---|
| Critic structure | **Hybrid.** Deterministic constraints (em-dash, banned-word regex, length bounds, missing "WCM scholars are X-ing" tic) handled by code. Single LLM call only for tone/voice/marketing-language judgment. Returns structured verdict `{constraint_id: pass|fail, reason}`. |
| Retry budget | 3 LLM regen attempts on critic failure before routing to review queue. |
| Review queue location | **DynamoDB `SPOTLIGHT_REVIEW#{publish_id}#{subtopic_id}`.** Composite SK so a single query lists all flagged entries for a publish run. Schema must be **forward-compatible with a v2 Publication Manager review surface** — do not design as minimal CLI scratch state. Required attributes: `publish_id, subtopic_id, lede_text, flag_reason (critic|sensitive_tag|both), critic_verdict (JSON), papers_used (PMIDs), regen_count, status (pending|approved|rejected), reviewer, reviewed_at`. |
| Review CLI (v1) | `backfill_spotlight.py --review-queue [--publish-id <id>]` lists pending entries; `--approve <subtopic_id>` and `--reject <subtopic_id>` mutate status. Subsequent `--publish` includes approved-only. |
| Review UI (v2) | NOT in Phase 6 scope, but schema designed to support it. Defer to its own roadmap entry. |

### Operator workflow

| Decision | Value |
|---|---|
| Refresh cadence | Weekly. Operator-run for v1 (`backfill_spotlight.py --publish`). Automated cron is v2. |
| `--dry-run` flag | Print pool ranking + selected 10 + draft ledes. NO Bedrock LLM critic, NO publish. Cheapest preview. Always safe. |
| `--dry-run-full` flag | Full pipeline (Bedrock generation, critic, sensitive-tag check) but no publish. Final candidate `spotlight.json` written locally to `./out/spotlight-{date}.json` for review. Spends LLM budget. |
| `--regen-only <subtopic_id>` flag | Re-roll lede for one specific subtopic without re-ranking the pool. Reads existing pool/selection state from last publish; writes new candidate to review queue. |
| `--approve <subtopic_id>` flag | Mark flagged entry (critic or sensitive-tag) as cleared by reviewer. Subsequent `--publish` includes it. Pairs with `--reject`. |

### Schema & SPS contract

| Decision | Value |
|---|---|
| Synopsis grounding | `synopsis` + `impactJustification` from 2-3 selected papers per spotlight (LLM-canonical fields from DynamoDB enrichment). NOT `display_name` / `short_description` (D-19 forbids passing UI fields to LLMs). NOT `title` / `journal` (richer signal already in DynamoDB enrichment fields). |
| Author payload | `{personIdentifier, displayName, position}` for first AND last author of each grounding paper. SPS resolves `personIdentifier` to faculty headshot via its existing photo store. **Confirmed: `personIdentifier` IS the SPS photo-store join key** (same key used by SPS `RecentContributionsGrid`). ReciterAI artifact carries NO image URLs. |
| Sensitive-tag list | DynamoDB only. NOT in repo. Lives at `SPOTLIGHT_CONFIG#sensitive_tags`. First-pass list seeded by Paul out-of-band before first publish. |
| Artifact shape | Separate `spotlight.json` (NOT a hierarchy.json extension). hierarchy.json unchanged. Independent schema, independent manifest, independent versioning. |
| Papers per lede | 2-3. |
| Schema versioning | Mirrors Phase 5: `spotlight_schema_v1.json` checked into repo, `schema_version` field in artifact. Breaking changes bump version + dual-publish window. |

## Prerequisites surfaced from this discussion

1. **Bucket migration from `wcmc-reciterai-hierarchy` → `wcmc-reciterai-artifacts`.** Affects Phase 5's already-published artifact and SPS ETL config. Researcher should evaluate whether this lives as Phase 6 Plan 01, a backported Phase 5 follow-on, or its own mini-phase. Cannot publish Phase 6 spotlight to the new path until migration is complete.
2. **DynamoDB schema design pre-work.** Three new partitions to design before code: `SPOTLIGHT_HISTORY#`, `SPOTLIGHT_REVIEW#`, `SPOTLIGHT_CONFIG#`. Researcher should propose schemas with v2 dashboard read patterns in mind.
3. **Banned-word + tic-detection regex set for hybrid critic.** Captured during ledes-prompt authoring; needs an enumeration before Plan 06-03 critic implementation. Source for v0 list: existing `prompts/spotlight_synopsis_v0.md` constraint section.

## Out of scope (deferred)

- 335-wide synopsis generation — top-N rotation IS the design; non-spotlighted subtopics get no lede in v1.
- SPS-side ETL implementation — separate follow-up SPS work after artifact contract publishes; mirrors hierarchy handoff.
- Suppress Recent Contributions home-page change in SPS — separate SPS-side change, not part of Phase 6.
- Automated weekly cron runner — operator-run for v1.
- LLM judge for sensitive-topic detection — tag-list pattern match only for v1.
- Formal comms-strategy sign-off workflow — manual editorial review queue is the v1 mechanism.
- Embedding-based subtopic re-ranking — signal-based ranking is sufficient.
- Editorial CMS UI for hand-curated overrides — operator-curated via CLI flags only for v1.
- Publication Manager review-queue dashboard surface — v2 work; v1 schema is forward-compat.
- Per-spotlight provenance in published artifact — lives in DynamoDB only for v1.

## Canonical refs

Downstream agents (researcher, planner) MUST read these:

- **Mockup:** `/Users/paulalbert/Downloads/home-spotlight-interactive.html` — interactive 2-column spotlight + 8 cards; Phase 6 ships 10 instead of 8.
- **Lede prompt v0:** `prompts/spotlight_synopsis_v0.md` — voice-constrained Bedrock Sonnet prompt; constraint list is the source of truth for hybrid critic regex set.
- **Phase 5 publishing pattern (REUSE):**
  - `docs/hierarchy-contract.md` — schema authoring + manifest contract pattern
  - `docs/sps-integration-handoff.md` — SPS handoff brief structure to mirror for spotlight
  - `docs/sps-etl-reference.ts` — SPS ETL reference; spotlight ETL will mirror this
  - `utils/s3_client.py` — S3 publish helper, reuse as-is
  - `backfill_all.py --publish` — Phase 5 publish entrypoint pattern; `backfill_spotlight.py` mirrors structure
- **Phase 5 CONTEXT.md:** `.planning/phases/05-hierarchy-publishing-contract/05-CONTEXT.md` — bucket/IAM/KMS provisioning decisions inherited.
- **DynamoDB enrichment fields (Phase 4 + ad-hoc 2026-05-06):** TOPIC# records carry `synopsis`, `impactScore`, `impactJustification`, `title`, `journal`, `year`, `author_position` per publication. Pool ranker reads from this directly without a fresh ReciterDB join.
- **D-19 rule (LOCKED, inherited from Phase 4):** `display_name` and `short_description` are UI-only fields — never pass them to an LLM. Lede generator uses `label` + `description` for subtopic identity and `synopsis` + `impactJustification` for paper grounding.
- **SPS RecentContributionsGrid (cross-repo verification):** confirms `personIdentifier` is the photo-store join key reused by spotlight artifact. (Confirmed via Phase 2 retrieval work; planner re-verifies if SPS schema changes.)

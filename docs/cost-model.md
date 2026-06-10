# Cost model — what each pipeline job costs

> **Confidence level.** Numbers here are *reasoned estimates from prompt shape + published model pricing*, not measured spend. In-repo Bedrock numbers are within roughly ±2× of reality. The daily-enrichment synopsis + impact numbers were revised on 2026-05-13 (input shape) and again on 2026-05-19 (model rewire to Bedrock Sonnet 4.6); the 2026-05-19 row is grounded against a measured 15-paper spike. To tighten any cell further, see [Grounding the numbers](#grounding-the-numbers) at the end.
>
> **Revision history.**
> - 2026-05-13: corrected daily-enrichment synopsis + impact figures (was Sonnet-on-full-text assumption; actually GPT-5.1 on title+abstract via OpenAI Batch).
> - 2026-05-19: daily-enrichment synopsis + impact rewired to Bedrock Claude Sonnet 4.6 via #37 PRs 1–2; OpenAI gpt-5.1 retained as the content-filter fallback (#37 D3). Per-PMID figures now grounded against the Bedrock spike (`scripts/debug/bedrock_synopsis_impact_spike.py`, n=15) at $0.0141/PMID combined.
> - 2026-06-10: spotlight publish cost is now **measured**, not estimated — `backfill_spotlight` meters every run via the `STAGE#spotlight_publish#GLOBAL` cost ledger (PR #187). First metered publish was **$3.04** (66 Bedrock calls; Opus $3.02 + Haiku $0.02), ~6× the prior `~$0.50` estimate. Root cause of the miss: the publish over-selects `SELECTION_TARGET=25` candidates and generates a lede for each (plus critic retries), not ~10 (#166/#167). Spotlight rows below revised accordingly.
>
> All $ figures assume the current corpus: **~6,200 PMIDs**, **66 topics**, **1,541 subtopics** across **~600 WCM full-time faculty**. PMIDs are filtered to `publicationTypeCanonical = 'Academic Article'`, `articleYear >= 2020`. Author scope today is *all* positions (first / middle / last) of WCM full-time faculty.

## Cost taxonomy

There are three pools that get conflated when people ask "what does it cost to run ReciterAI?":

1. **Daily-enrichment pipeline** (`pipeline_enrichment`) — generates `reciterai_synopsis` + `reciterai_impact` rows in MariaDB and `IMPACT#` rows in DynamoDB for new WCM-faculty publications. Bedrock Claude Sonnet 4.6 on the happy path; OpenAI gpt-5.1 on the content-filter fallback (#37 D3). Used to live outside this repo as a POC laptop script; #37 ported it in-repo (PRs land 2026-05-16…) and moves it to scheduled ECS Fargate (PR 4). **Historically the expensive half; now comparable to in-repo Bedrock at current corpus size.**
2. **In-repo Bedrock work** — `score_publications`, `assign_subtopics`, `discover_subtopics`, `relabel_subtopics`, `generate_taxonomy`, `generate_see_also`, spotlight lede + critic. Sonnet 4.6 / Haiku 4.5 / Opus 4.7 via Bedrock.
3. **In-repo infra** — DynamoDB (PAY_PER_REQUEST), S3 (artifact bucket + hierarchy bucket), Step Functions, Lambda, Fargate. Effectively rounding error at current scale.

The big cost driver in (1) and (2) is **PMID count**. Author-position scope (first/last vs. all) changes which faculty *links* exist; it does NOT change how many PMIDs get scored. Time scope (2020+ vs. earlier years) and pub-type scope are the real cost levers.

## Per-stage cost — in-repo Bedrock

| Stage | Script | Model | Tokens (est.) in / out | Cost per invocation | Unit | Notes |
|---|---|---|---|---|---|---|
| Screening (relevance Pass 1) | `score_publications.py` | Haiku 4.5 | ~3.2K / ~50 | **~$0.0035** | per PMID | Coarse filter against topic list. Token shape depends on batching of topics per call. |
| Scoring (relevance Pass 2) | `score_publications.py` | Sonnet 4.6 | ~2K / ~200 | **~$0.009** | per PMID | Precise per-topic relevance only on papers that survived screening. |
| Subtopic assignment | `assign_subtopics.py` | Haiku 4.5 | ~1.5K / ~400 | **~$0.0035** | per (PMID, topic) | `max_tokens=400`. Each PMID typically lands in ~2 topics → ~2 calls per PMID. |
| Subtopic discovery | `discover_subtopics.py` | Sonnet 4.6 | ~5K / ~8K | **~$0.13** | per topic | `max_tokens=8192`. Cold-start only (per-topic; idempotent on rerun if input_hash unchanged). |
| Subtopic relabel | `relabel_subtopics.py` | Sonnet 4.6 | ~3K / ~4K | **~$0.07** | per subtopic | `max_tokens=4096`. Cold-start only. Generates `display_name` + `short_description`. Skips already-populated subtopics. |
| Taxonomy generation | `generate_taxonomy.py` | Sonnet 4.6 | varies | **~$5–$15** | per full regen | One-shot inductive clustering of synopses. Rare event; only when taxonomy version is bumped. |
| See-also generation | `generate_see_also.py` | Sonnet 4.6 | ~10K / ~30K | **~$0.50** | per cold run | `max_tokens=32768`. One call per cold run. |
| Spotlight lede | `spotlight/lede_generator.py` | **Opus 4.7** | ~2K / ~300 | **~$0.05** | per candidate lede | `max_tokens=300`. The publish over-selects `SELECTION_TARGET=25` candidates and generates a lede for each (plus critic-driven retries), then publishes the best `PUBLISH_TARGET=9` (#167) — so it's **~19–25 ledes per publish, not 10**, and this Opus row dominates the per-publish cost. **Measured 2026-06-10: $3.04/publish total** (66 Bedrock calls, Opus $3.02 + Haiku $0.02), via the `STAGE#spotlight_publish#GLOBAL` cost ledger (#187). |
| Spotlight critic | `spotlight/critic.py` | Haiku 4.5 | ~1.5K / ~200 | **~$0.002** | per lede critique | `max_tokens=200`. Runs after each lede; a critic reject triggers a lede regeneration (extra Opus call), so calls/publish exceed the candidate count. |
| Spotlight sensitive gate | `spotlight/sensitive_gate.py` | Haiku 4.5 | ~1K / ~50 | **~$0.0015** | per spotlight candidate | Runs before publish to catch sensitive topics. |

### What a full cold-start full-corpus rebuild costs (in-repo Bedrock only)

| Stage | Unit count | Unit cost | Subtotal |
|---|---|---|---|
| Screening | 6,200 PMIDs | $0.0035 | ~$22 |
| Scoring (survivors only, ~50%) | ~3,100 PMIDs | $0.009 | ~$28 |
| Subtopic assignment (~2 topics/PMID) | ~12,400 calls | $0.0035 | ~$43 |
| Subtopic discovery | 66 topics | $0.13 | ~$9 |
| Subtopic relabel | 1,541 subtopics | $0.07 | ~$108 |
| See-also generation | 1 | $0.50 | ~$1 |
| Taxonomy generation | 0 (only on version bump) | $10 | — |
| **Total cold-start in-repo Bedrock** | | | **~$210** |

This is the one-time number when you nuke and reload from scratch. Most real-world runs are not this — `STAGE#` skip-on-hash short-circuits anything already done.

### What a steady-state month costs (in-repo Bedrock only)

Assumes ~20 new PMIDs/day enter the corpus = ~600/month, no taxonomy version bump, monthly spotlight:

| Item | Unit count | Unit cost | Subtotal/mo |
|---|---|---|---|
| Screening + scoring (new PMIDs) | 600 | $0.013 | ~$8 |
| Subtopic assignment | ~1,200 | $0.0035 | ~$4 |
| Spotlight publish (Opus ledes) | ~19–25 candidates + retries | — | **~$3.02** (measured 2026-06-10) |
| Spotlight critic + sensitive gate (Haiku) | per candidate | — | ~$0.02 (measured) |
| **Total in-repo Bedrock per month** | | | **~$15** (assumes 1 publish/mo) |

> **Spotlight cadence caveat:** `docs/spotlight-contract.md` describes the publish as *weekly* (operator-run), while this table assumes one publish/month. At ~$3.04/publish, a weekly cadence adds **~$12/mo** (total ≈ **$24/mo**), not ~$3. Multiply $3.04 by your actual publish frequency — the cost ledger (`STAGE#spotlight_publish#GLOBAL`) is the source of truth for runs actually executed.

Subtopic discovery / relabel / see-also re-run cold; they don't enter steady-state cost unless input_hash changes.

## Per-stage cost — daily enrichment (`pipeline_enrichment`)

Now in-repo (#37 PRs 1–2, merged 2026-05-19); scheduled on ECS Fargate (#37 PR 4). Lives at `pipeline_enrichment/{synopsis,impact,daily_job}.py`; writes `reciterai_synopsis` + `reciterai_impact` rows to MariaDB and an `IMPACT#` row to DynamoDB per PMID. The happy path runs Bedrock Claude Sonnet 4.6 via the Converse API; on a content-filter block (Sonnet reproducibly filters WCM biomedical animal-model abstracts — see `sonnet-content-filter-on-dense-scoring.md`) the call falls back once to OpenAI gpt-5.1.

**Inputs:** title + abstract + (impact only) a handful of bibliometric fields. Not full text.

| Stage | Model | Tokens (avg) in / out | Cost per PMID | Confidence |
|---|---|---|---|---|
| Synopsis generation | Bedrock Claude Sonnet 4.6 (happy path) | ~1,990 / ~70 | **~$0.0067** | high (spike, n=15) |
| Impact scoring | Bedrock Claude Sonnet 4.6 (happy path) | ~1,990 / ~80 | **~$0.0074** | high (spike, n=15) |
| **Combined per PMID** (happy path) | Bedrock Sonnet 4.6 | — | **~$0.0141** | high (spike: $0.21222 / 15 PMIDs) |
| Synopsis + impact (content-filter fallback lane) | OpenAI gpt-5.1 — fallback only | ~1,650 / ~265 | **~$0.0094** | medium (POC-measured) |

The combined per-PMID figure is the canonical input to the cost guard (`pipeline_enrichment/cost_guard.py`, default $0.018/paper — a conservative round-up that carries headroom for the length-retry loops + the fallback lane).

The fallback row is the *additive* cost on the rare paper that Sonnet content-filters: that paper pays a Sonnet input-token cost (the original blocked call) **plus** a full gpt-5.1 call. Effective rate when the fallback fires ≈ $0.020/paper. Filter prevalence in the daily enrichment job is not yet measured — the §7-B Fargate smoke run (#37 PR 4) pins it.

For the current 6,200-PMID corpus, the daily-enrichment pipeline has paid ~$60–90 in OpenAI spend pre-rewire (POC laptop runs) and ≈$15 to enrich the #112 1,073-paper backlog forward-only on Sonnet (PR 4 deploy). For scope expansion, ~$0.0141 per new PMID applies on the happy path.

## What different scope changes cost (deltas)

| Change | Δ PMIDs | Daily-enrichment Δ (est.) | In-repo Bedrock Δ | DDB / infra Δ | Total Δ |
|---|---|---|---|---|---|
| Filter author rank to first/last only (corpus-wide) | −167 PMIDs | $0 (subtractive — saved spend was already paid) | ~$3 saved one-time | trivial | ~$3 saved |
| Gate ingestion on ≥1 first/last WCM FT author (#26 — rejected 2026-05-13) | −167 PMIDs going forward | ~$2/yr saved | <$1/yr saved | trivial | ~$3/yr saved — too small to bother |
| Add author-rank weighting (middle × 0.3, etc.) | 0 | $0 | $0 | $0 | $0 — pure arithmetic change |
| Drop pub-type filter (`Academic Article` only) | +20%? unknown | +$20–$50 one-time | +$40 one-time | trivial | $60–$90 one-time |
| Expand to pre-2020 (2010–2019) | +~30,000 PMIDs | ~$425 one-time | ~$1K one-time | trivial | **~$1,400 one-time** |
| Expand to all WCM faculty (not just full-time) | varies | depends on overlap | small | trivial | unknown |

With the Bedrock-grounded $0.0141/PMID daily-enrichment rate, the pre-2020 expansion is no longer the dollar lever it appeared to be in the earlier draft — a ~$1.4K one-time cost rather than the $4K–$10K from the original Sonnet-on-full-text figures. The signal-quality and ETL-coordination costs of expanding scope are probably larger than the dollar cost now.

## Infra cost (DynamoDB, S3, Step Functions)

At current scale these are well under $10/month total. PAY_PER_REQUEST DDB on a ~74 MiB table with the current write cadence ≈ a few dollars; S3 storage on `wcmc-reciterai-hierarchy` and `wcmc-reciterai-artifacts` is pennies. Listed here for completeness, not because they need management attention.

## Grounding the numbers

Anywhere a cell above is labeled "estimated," the path to a measured number is:

1. **In-repo Bedrock per-stage cost.** Bedrock emits invocation logs to CloudWatch (`/aws/bedrock/modelinvocations` log group when invocation logging is enabled) and CloudTrail. A two-week sample of `InvokeModel` calls, joined by `inferenceProfileArn` and aggregated by model, gives a real $/call. The in-repo `STAGE#` records also have a `cost_observed_usd` slot — and after #37 PR 2 the daily-enrichment job populates it per run via the model the call actually used (Sonnet, or gpt-5.1 on the fallback). Wiring the rest of the substrate up similarly would let `STAGE#` rows self-report and would obsolete most of this doc.
2. **Daily-enrichment synopsis + impact cost.** Now in-repo (`pipeline_enrichment`). Per-PMID figures above are from `scripts/debug/bedrock_synopsis_impact_spike.py` (n=15, 2026-05-19). The §7-B Fargate smoke run (#37 PR 4) pins the live operational rate against measured spend before the #112 backfill.
3. **Author-rank filter delta.** Already measured precisely in earlier analysis: 78,103 TOPIC#/SCORE# rows total → 37,096 first+last → 41,007 middle/empty; only 167 PMIDs (2.7%) lack any first/last anchor.

## Update cadence

This doc was written 2026-05-13 and last revised 2026-05-19. Bedrock and OpenAI model pricing changes periodically; the numbers here will drift. Re-check whenever:

- A model is swapped in `MODEL_IDS_BY_STAGE` (see `utils/bedrock_client.py`) or in the daily-enrichment substrate (`pipeline_enrichment/synopsis.py`, `impact.py`).
- The corpus grows by >2× or contracts.
- A new stage is added or `max_tokens` is bumped on an existing stage.
- AWS publishes a Bedrock price change, or OpenAI changes the gpt-5.1 price card (affects the fallback lane).
- The §7-B smoke run reveals the daily-enrichment Sonnet rate differs materially from the spike's $0.0141/PMID — pin the new rate here and in `pipeline_enrichment/cost_guard.py`.

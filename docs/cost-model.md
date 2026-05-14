# Cost model — what each pipeline job costs

> **Confidence level.** Numbers here are *reasoned estimates from prompt shape + published model pricing*, not measured spend. In-repo Bedrock numbers are within roughly ±2× of reality. Upstream synopsis + impact numbers were revised down on 2026-05-13 after grounding against the actual POC prompt shape (title + abstract, not full text) — earlier draft of this doc had them ~10× too high. To tighten any cell further, see [Grounding the numbers](#grounding-the-numbers) at the end.
>
> **Revision history.**
> - 2026-05-13: corrected upstream synopsis + impact figures (was Sonnet-on-full-text assumption; actually GPT-5.1 on title+abstract via OpenAI Batch). #37 will measure real spend and pin these cells.
>
> All $ figures assume the current corpus: **~6,200 PMIDs**, **66 topics**, **1,541 subtopics** across **~600 WCM full-time faculty**. PMIDs are filtered to `publicationTypeCanonical = 'Academic Article'`, `articleYear >= 2020`. Author scope today is *all* positions (first / middle / last) of WCM full-time faculty.

## Cost taxonomy

There are three pools that get conflated when people ask "what does it cost to run ReciterAI?":

1. **Upstream pipeline** — generates `reciterai_synopsis` and `reciterai_impact` rows in MariaDB. Lives outside this repo. This repo only *reads* those rows. **This is the expensive half.**
2. **In-repo Bedrock work** — `score_publications`, `assign_subtopics`, `discover_subtopics`, `relabel_subtopics`, `generate_taxonomy`, `generate_see_also`, spotlight lede + critic. Sonnet 4.6 / Haiku 4.5 / Opus 4.7 via Bedrock.
3. **In-repo infra** — DynamoDB (PAY_PER_REQUEST), S3 (artifact bucket + hierarchy bucket), Step Functions, Lambda. Effectively rounding error at current scale.

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
| Spotlight lede | `spotlight/lede_generator.py` | **Opus 4.7** | ~2K / ~300 | **~$0.05** | per featured subtopic | `max_tokens=300`. ~10 ledes per monthly spotlight publish (`SELECTION_SIZE=10` is a floor). |
| Spotlight critic | `spotlight/critic.py` | Haiku 4.5 | ~1.5K / ~200 | **~$0.002** | per lede critique | `max_tokens=200`. Runs after each lede. |
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
| Spotlight lede | 10 | $0.05 | ~$0.50 |
| Spotlight critic | 10 | $0.002 | ~$0.02 |
| Sensitive gate | 10 | $0.0015 | ~$0.02 |
| **Total in-repo Bedrock per month** | | | **~$13** |

Subtopic discovery / relabel / see-also re-run cold; they don't enter steady-state cost unless input_hash changes.

## Per-stage cost — upstream (will move in-repo via #37)

The synopsis and impact pipelines currently live in `wcmc-its/ReCiterAI-POC` (`core/synopsis.py`, `core/impact.py`) and write to MariaDB (`reciterai_synopsis`, `reciterai_impact`). Issue #37 ports them into a scheduled daily job, switches the sink to DynamoDB, and brings them under this repo's observability substrate.

**Inputs (corrected 2026-05-13):** title + abstract + (impact only) a handful of bibliometric fields. Not full text. Earlier estimates here assumed Sonnet on full text; that was wrong.

| Stage | Model | Tokens (est.) in / out | Cost per PMID | Confidence |
|---|---|---|---|---|
| Synopsis generation | GPT-5.1 via OpenAI Batch API (~50% off) | ~600 / ~100 | **~$0.005** | medium (grounded against POC prompt shape; price-card-derived) |
| Impact scoring | GPT-5.1 via OpenAI Batch API | ~700 / ~100 | **~$0.010** | medium |

Two daily cycles of real spend under #37 will pin the actual numbers and update this table.

For the current 6,200-PMID corpus, the upstream pipeline has paid ~$90 one-time (estimated; was ~$900–$2,800 in the wrong-input-shape version of this doc). For scope expansion, ~$0.015 per new PMID applies.

## What different scope changes cost (deltas)

| Change | Δ PMIDs | Upstream Δ (est.) | In-repo Bedrock Δ | DDB / infra Δ | Total Δ |
|---|---|---|---|---|---|
| Filter author rank to first/last only (corpus-wide) | −167 PMIDs | $0 (subtractive — saved spend was already paid) | ~$3 saved one-time | trivial | ~$3 saved |
| Gate ingestion on ≥1 first/last WCM FT author (#26 — rejected 2026-05-13) | −167 PMIDs going forward | ~$2/yr saved | <$1/yr saved | trivial | ~$3/yr saved — too small to bother |
| Add author-rank weighting (middle × 0.3, etc.) | 0 | $0 | $0 | $0 | $0 — pure arithmetic change |
| Drop pub-type filter (`Academic Article` only) | +20%? unknown | +$20–$60 one-time | +$40 one-time | trivial | $60–$100 one-time |
| Expand to pre-2020 (2010–2019) | +~30,000 PMIDs | ~$450 one-time | ~$1K one-time | trivial | **~$1,500 one-time** |
| Expand to all WCM faculty (not just full-time) | varies | depends on overlap | small | trivial | unknown |

With the corrected per-PMID figures, the pre-2020 expansion is no longer the dollar lever it appeared to be in the earlier draft — it's a ~$1.5K one-time cost rather than $4K–$10K. The signal-quality and ETL-coordination costs of expanding scope are probably larger than the dollar cost now.

## Infra cost (DynamoDB, S3, Step Functions)

At current scale these are well under $10/month total. PAY_PER_REQUEST DDB on a ~74 MiB table with the current write cadence ≈ a few dollars; S3 storage on `wcmc-reciterai-hierarchy` and `wcmc-reciterai-artifacts` is pennies. Listed here for completeness, not because they need management attention.

## Grounding the numbers

Anywhere a cell above is labeled "estimated," the path to a measured number is:

1. **In-repo Bedrock per-stage cost.** Bedrock emits invocation logs to CloudWatch (`/aws/bedrock/modelinvocations` log group when invocation logging is enabled) and CloudTrail. A two-week sample of `InvokeModel` calls, joined by `inferenceProfileArn` and aggregated by model, gives a real $/call. The in-repo `STAGE#` records also have a `cost_observed_usd` slot — but most rows currently carry `0` because the per-call cost-attribution code isn't wired through. Wiring that up would let `STAGE#` rows self-report and would obsolete most of this doc.
2. **Upstream synopsis + impact cost.** Owned by whoever runs the upstream pipeline. Ask them for: model ID, average tokens-in / tokens-out per record, and total monthly invocation count. Multiply against the published price card.
3. **Author-rank filter delta.** Already measured precisely in earlier analysis: 78,103 TOPIC#/SCORE# rows total → 37,096 first+last → 41,007 middle/empty; only 167 PMIDs (2.7%) lack any first/last anchor.

## Update cadence

This doc was written 2026-05-13. Bedrock model pricing changes periodically; the numbers here will drift. Re-check whenever:

- A model is swapped in `MODEL_IDS_BY_STAGE` (see `utils/bedrock_client.py`).
- The corpus grows by >2× or contracts.
- A new stage is added or `max_tokens` is bumped on an existing stage.
- AWS publishes a Bedrock price change.

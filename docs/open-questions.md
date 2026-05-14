# Open questions — v1.1 and beyond

**Audience:** anyone who hasn't been heads-down in this codebase. Read the [Background](#background) section first, then jump to whichever question cluster is in your court.

**Status (2026-05-13):** v1.0 has shipped. Hygiene cleanup that doesn't need design input is largely cleared. What's left is decisions that need a human — most of them are blocking the v1.1 orchestration story or the v2 tools axis. None are blocking anything currently in production.

---

## Background

### What ReciterAI is

ReciterAI is a pipeline that turns Weill Cornell Medicine's publication corpus into structured signal about *what each faculty member works on*. The output feeds the Scholars Profile System (SPS), which renders faculty profile pages, research-area cards, and editorial spotlights on the public-facing scholars.weill.cornell.edu site.

Concretely, ReciterAI produces three kinds of artifacts:

1. **A research-domain hierarchy.** ~67 high-level topics (Axis 1: "cardiovascular disease," "neurodegenerative disease," "epidemiology") and ~1,500 subtopics underneath them (Axis 1.5: "atherosclerosis," "Alzheimer's biomarkers"). Published as a versioned JSON artifact to S3.
2. **Per-publication scores.** For every paper in scope, a relevance score against every topic + subtopic. Plus a separate impact score (from an upstream pipeline) and tool/method mentions. Stored in DynamoDB.
3. **Faculty rollups + spotlights.** Per-faculty profiles aggregating their publications across topics and subtopics, plus curated editorial spotlights (short LLM-generated ledes for featured subtopics). Stored in DynamoDB + S3.

ReciterAI doesn't render anything itself. SPS reads from its outputs.

### Where the data comes from

The publication corpus and the per-paper synopses + impact scores live in MariaDB (the "ReciterDB" instance), populated by a *separate* upstream pipeline outside this repo. Today's scope:

- `publicationTypeCanonical = 'Academic Article'`
- `articleYear >= 2020`
- WCM full-time faculty as authors (all positions — first, middle, last — though there's an open question about narrowing this; see #26 below)

The pipeline in this repo reads from ReciterDB, runs LLM-based scoring + classification (Sonnet 4.6 / Haiku 4.5 / Opus 4.7 via AWS Bedrock), writes to DynamoDB + S3, and is consumed by SPS.

### Two paths

There are two execution modes:

- **Cold path** — Operator-driven full rebuild from a workstation. Used today. Walks every cold stage in order (score → assign → discover → relabel → rollup → spotlight → publish). Multi-hour wall time, runs on demand, no automation. *This is what's working in production right now.*
- **Hot path** — Automated incremental refresh. Scheduled (EventBridge cron), Step-Functions-orchestrated, scoped to deltas since the last successful run. *This is the v1.1 capability. Code exists but is not yet deployed; two design decisions block the wiring (see [#19](#19--phase-10-hot-path-design-decisions) below).*

### Current shape of the repo

- **Phases 1–12** of the original build are done. The numbered phase docs in `.planning/phases/` are historical context, not roadmap.
- **v1.0 milestone closed 2026-05-13.** The hierarchy is publishing to S3, spotlights are running, SPS is consuming.
- **v1.1** is the current cycle. Two themes:
  - **Orchestration** — finish the hot path so new publications surface within hours rather than waiting for the next operator-triggered cold run.
  - **Tool / methods axis (Axis 2)** — add a third scoring axis for tools, instruments, and methods alongside topics + subtopics. Schema-deferred at v1.0; needs four design decisions before implementation starts.

---

## Question clusters

The open work falls into four buckets. Pick the cluster that matches the kind of decision being asked of you; the order within each cluster is what I'd suggest tackling first.

### Cluster A — v1.1 freshness pipeline

This cluster collapsed on 2026-05-13 around **#37 — Recurring impact + synopsis + scoring daily job**. The original v1.1 plan was elaborate (rolling enrichment + hot-path Step Functions + Bedrock Batch + drift detector + incremental rollups + EventBridge wiring). It turned out to be massively over-engineered for the actual volume (1,200 papers/year, 5–15 papers/day).

**What #37 actually is.** Not "productionize a laptop script." It's the **prompt-port half of a repo migration that's been ~80% complete for a while.** The model code crossed from `wcmc-its/ReCiterAI-POC` into this repo at v1.0; the synopsis + impact **prompts** never made the trip. The daily-job work is the forcing function that finally drags them across. The cron is the easy part; the prompts have semantic risk (does the ported version produce the same scores the laptop version did?). Confirmed 2026-05-13 that the POC prompts haven't drifted from what the laptop script runs, so the port is mechanical — but equivalence still needs verification because GPT-5.1 isn't bit-deterministic.

**Repo decision.** All work lives in `wcmc-its/ReciterAI` (this repo). The earlier "schedule from POC" framing was a misread — POC's README explicitly marks it superseded, and the design conversation reconciled by following the README's intent. POC gets archived (via GitHub's repo-archive feature) once #37 completes; tracked at `ReCiterAI-POC#4`.

**Decisions made** (recorded in #37):

- Runtime: ECS Scheduled Task (batch-shaped workloads on ECS; application services stay on EKS — split-by-shape, not split-by-ecosystem-preference)
- Cadence: daily delta (5–15 papers) + annual full rescore (`--full` flag)
- Watermark: single DDB item, simple last-successful-run pattern
- Failure mode: leave watermark, retry next run, no manual intervention
- Sink: extend the existing `IMPACT#pmid_{pmid}` partition with synopsis attributes (one GetItem per pub for SPS)
- Alerting: Microsoft Teams Incoming Webhook (env var `RECITERAI_TEAMS_WEBHOOK_URL`)
- Corpus boundary: ≥1 WCM full-time faculty author, any position (#26 closed)
- Cost attribution scaffolding: built as step 1 of #37 (closes #35 scope for the daily job; broader retrofit is a follow-up)
- Equivalence check: 10–20 recent PMIDs; eyeball synopsis equivalence; impact within ±5 points on ≥18/20. One-shot, attached to step-1 PR.

**Open implementation questions** (small):

- Where the price table lives. `config/llm_prices.yaml` or similar — checked-in YAML, not Python.
- Per-paper cost ground-truth. Spec uses ~$0.035/paper conservative; two daily cycles of real spend will pin the actual number and update `docs/cost-model.md`.

**Sub-issues:**

- **#38** — ReciterAI's internal read-path switch from MariaDB to DynamoDB. Step 4 of #37's sequencing. Don't start until step 3 dual-write has burned in.
- **`ReCiterAI-POC#4`** — archive POC repo. Step 7 of #37's sequencing. Don't action until #37 has run cleanly for ≥4 weeks against the DDB sink.

**Issues closed 2026-05-13 by this collapse:**

- **#3** (parent tracker for hot-path orchestration) — daily job closes the freshness gap end-to-end at the actual volume.
- **#19** (Phase 10 design questions) — Slack → Teams; Bedrock Batch wait → moot.
- **#32** (`dateLastModified` missing column) — dormant `pipeline_hot/` module supersedes; will be deleted as part of #37 cleanup.

**Issue still relevant:**

- **#17 — ReCiter ETL cascade-wipe convergence verification.** Producer-side tracking for a downstream bug fixed in `wcmc-its/ReCiter-Publication-Manager#248`. Independent of #37. After the fix runs through a clean nightly cycle, verify `publication_topic` row count converges toward ~78K; close if it does, file a DDB↔MySQL drift issue if it doesn't. No ReciterAI code change required.

---

### Cluster B — Axis 2 / Tools (blocks v2 entry)

Four interlocking decisions. None of them is hard individually, but they constrain each other, so the order matters.

#### Recommended decision order

The four questions are tangled. Answer them in this sequence:

1. **#5 — Vocabulary source.** Which controlled vocabulary defines canonical tool IDs? (RRID / bioregistry / WCM-curated / hybrid.) Answer this first; it constrains everything else.
2. **#7 — Edge cardinality.** Is the parent→canonical edge 1:1 or 1:N? (Does a tool have exactly one parent ecosystem, or can it span multiple?) Best argued through concrete examples — see the issue.
3. **#6 — Discovery process.** How are tools discovered and assigned to publications? (LLM clustering analogous to subtopics / hand-curated seed + LLM extraction / rule-based from `reciterai_keyword_relevance`.) Often narrowed by the vocabulary choice in #5.
4. **#8 — Record schema.** What does a `TOOL#` DynamoDB record actually look like? Falls out of the first three once they're settled. The sketch in the issue is a starting point.

#### Why this is hard

The four questions aren't really about engineering — they're about *editorial* and *ontological* commitments. RRID gives you a curated, biomedical-flavored vocabulary with strong external metadata; bioregistry is broader but flatter; WCM-curated is the most control with the most maintenance.

The cardinality question is the philosophically thorniest: is a Python library shipped via Bioconductor "Python" or "Bioconductor"? Is Cox regression in SAS, R, and Python one method or three? The right answer depends on how downstream consumers want to query — and you have the most insight into that.

#### What's needed to decide

A focused thinking session, probably with someone domain-side. An hour at a whiteboard answering #5 and #7 produces enough constraint that #6 and #8 fall out in a follow-on session.

#### What unblocks when this lands

Phase 8 starts. ReciterAI gains a third scoring axis. Faculty profiles can answer "who at WCM uses [tool]?" — today that question is dead-ended by the hierarchy schema's TOOL# placeholder.

---

### Cluster C — Corpus boundary

**Resolved 2026-05-13.** Corpus stays at "≥1 WCM full-time faculty author, any position." #26 closed with rationale. Spotlight retains its independent first/last filter at the feature level. Documented in `docs/data-model-and-queries.md`.

---

### Cluster D — Hygiene (no decisions needed, just bandwidth)

These don't need design input from you. They're queued for whenever someone has the time.

| # | What | Size |
|---|---|---|
| **#10** | Lift pipeline threshold magic numbers into a config module | medium |
| **#12** | Reference and audit the AWS IAM pipeline-runner policy | small |
| **#13** | End-to-end integration test against fixtures | medium-large (writing new tests) |
| **#33** | `python3 utils/env_check.py` docstring + entrypoint mismatch | tiny |
| **#34** | Decouple `backfill_all.py` / `backfill_spotlight.py` from `hierarchy_full.json` artifact | small |
| **#35** | Wire real cost attribution into `STAGE#.cost_observed_usd` slot for in-repo Bedrock stages outside the daily job — the daily-job's calls are scaffolded in #37 step 1, so this is the broader-retrofit follow-up | medium |

---

## What I'd actually do next, if pressed

1. **Execute #37 step 1** — port the synopsis + impact prompts from POC into this repo, build the cost-attribution scaffolding (`utils/llm_cost.py` + `config/llm_prices.yaml`), wire the OpenAI client. Gate to step 2 is the equivalence sanity check on 10–20 PMIDs.
2. **Then #37 step 2** — scheduled ECS task writing to MariaDB only. Two clean cycles before step 3.
3. **Then #37 steps 3–7** in sequence (DDB dual-write, consumer read-switches, MariaDB decom, POC archive).
4. **In parallel with the above:**
   - Schedule the Axis 2 thinking session (different brain mode — needs domain input).
   - Verify #17 convergence opportunistically after the next clean nightly ETL cycle.
   - Hygiene cluster (#33, #12, #34, #10, #13) whenever bandwidth allows.

The v1.1 freshness story is a single ticket (#37) with a clear PR sequence rather than a four-cluster orchestration epic. That collapse is the most important strategic outcome of the 2026-05-13 design conversation.

---

## How this doc stays current

Whenever an issue in here closes, strike it off (or remove the section). When a new design question shows up, add a section. Treat this as a living index of "what would I have to explain to a colleague who walked in cold." If a question listed here goes more than a few months without anyone touching it, ask whether it actually matters or just got fossilized.

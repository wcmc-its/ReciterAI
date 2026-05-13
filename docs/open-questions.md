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

### Cluster A — Hot path orchestration (blocks v1.1 freshness story)

This is the highest-leverage cluster: every day the hot path doesn't deploy, new WCM publications are invisible to scholars.weill.cornell.edu until the next operator-triggered cold run.

#### #19 — Phase 10 hot-path design decisions

**What's blocked:** The hot-path Step Functions wiring can't enter plan-phase until these two questions have answers. Once answered, the wiring is implementable.

**Question 1: Which Slack channel receives WARN+ERROR alerts from the hot path, and what env var name does the alerter read the webhook URL from?**

- Options for channel:
  - Dedicated `#reciterai-pipeline` — clean separation, easy to mute/route, but yet another channel to monitor.
  - Piggyback on an existing WCM ITS infra channel — fewer channels, but alerts mix with unrelated noise.
- Options for env var name:
  - `SLACK_WEBHOOK_URL` — conventional but ambiguous if a second webhook ever arrives in this codebase.
  - `RECITERAI_SLACK_WEBHOOK_URL` — uglier but explicit and forward-compatible.

**What's needed to decide:** Just pick. Talk to whoever owns the WCM ITS Slack workspace if the answer is "piggyback."

**Question 2: How does the Step Functions state machine wait for a Bedrock Batch job to complete?**

- Option A — **Wait + Choice poll loop.** State machine sleeps for N minutes, polls Bedrock for job status, branches on `complete/failed/in_progress`. Simple to reason about; cost is N state transitions per job (transition billing on Standard workflows is ~$0.025 per 1K transitions).
- Option B — **EventBridge Pipes pattern.** Bedrock job completion fires an event, EventBridge resumes the state machine via callback token. More complex; cost is per-event rather than per-transition.

At the volume implied by hot-path cadence (likely daily or every few hours, batch sizes ~tens of jobs), the cost delta is probably <$5/month either way. Decision is more about operational complexity than spend.

**What's needed to decide:** A short comparison (~30 min of reading Bedrock + Step Functions docs) and a pick. Both options are well-trodden.

**Where it ends up:** Each answer either goes into a phase CONTEXT.md when the hot-path plan-phase starts, or into an inline note in `docs/hot-cold-paths.md`. Either way, `.planning/STATE.md` Deferred Items entries get removed once decided.

---

#### #3 — Parent tracker: end-to-end hot-path orchestration

Open since pre-v1.0 and intentionally a *tracker*, not an implementable issue. Once #19 is decided, this gets decomposed into sub-issues for: drift evaluator, incremental rollups, EventBridge cron, hot-path failure handling, input-boundary thresholds, etc. Don't try to action it directly — answer #19 first, then break this down.

---

#### #15 — Rolling synopsis + impact-score generation

**The upstream half of the orchestration story.** The pipeline in this repo reads synopses + impact scores from MariaDB tables that are populated by a *different* pipeline (outside this repo). That upstream pipeline runs in batches. Until it has a rolling mode, new publications can't enter ReciterAI's scope no matter how often the cold or hot path runs.

**Four design decisions in this issue:**

1. **Execution environment** — EKS CronJob, Lambda on EventBridge schedule, or a third Step Function alongside the cold/hot SFNs?
2. **Cadence** — daily (~18 pubs/run avg) or weekly (~125 pubs/run, aligns with current Monday cold-run cycle)?
3. **Watermark mechanism** — `analysis_summary_article.created_at`, auto-increment id, or `ENRICHMENT_RUN#` STAGE-style record?
4. **Failure-mode behavior** — explicit handling for Bedrock rate-limit, duplicate-key insert, mid-ETL reads, partial-run interruption.

**Why this matters more than the hot path:** the hot path is a scheduler. If there's nothing new in the upstream tables, even a perfect hot path is a no-op. Rolling enrichment is what produces new rows for the hot path to find.

**Coupled to #19:** the execution-environment choice will affect whether the hot path's state-machine wait pattern (Question 2 above) also serves the rolling enrichment workload.

---

#### #17 — Tracking: ReCiter ETL cascade-wipe convergence

Producer-side note about a downstream bug in the Publication Manager repo (now fixed in `wcmc-its/ReCiter-Publication-Manager#248`). The fix replaces a destructive `deleteMany()` with an `upsert` flow. After the fix runs through a clean nightly cycle, the `publication_topic` row count should converge back toward ~78K (matches the DynamoDB TOPIC# row count).

**What's needed to decide:** Verify convergence after a clean nightly cycle. If it stays meaningfully under 78K, file a separate DDB↔MySQL drift issue. If it's at 78K, close this one. No ReciterAI code change needed.

---

#### #32 — Hot-path delta query references a missing column

Discovered by the preflight in PR #31. `pipeline_hot/orchestrator.py:301` queries `WHERE dateLastModified >= :since` against a table that has no `dateLastModified` column. Dormant today (hot path isn't deployed); becomes a blocker when the hot path wires up.

**What's needed to decide:** Coordinate with the ReCiter ETL team to confirm which column actually carries "last modified" semantics. `datePublicationAddedToEntrez` is insertion time, not modification time — so this is a real semantic question, not just a rename. Either rename our SQL to a column that has the right meaning, or have upstream add a real `dateLastModified` column.

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

#### #26 — Gate corpus ingestion on ≥1 first/last WCM full-time faculty author

**Tiny issue. Just needs a yes/no/later from you.**

Today, a publication enters the corpus if it has *any* WCM full-time faculty author in any position (first, middle, or last). 167 of the 6,163 current PMIDs (2.7%) have only middle-author WCM links — no first or last author from WCM. The proposal is to add an `EXISTS` subquery to the corpus filter so those 167 get gated out going forward.

**What's needed to decide:** A judgment call. Cost analysis (see `docs/cost-model.md`) shows the dollar impact is rounding error. The signal-quality argument is the real lever — narrowing the corpus to papers WCM is at least somewhat *leading* on, versus papers WCM is *participating in*.

The spotlight feature already filters to first/last only. Adopting the same filter at the corpus level would unify the scope across all axes.

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
| **#35** | Wire real cost attribution into `STAGE#.cost_observed_usd` slot (would obsolete most of `docs/cost-model.md`) | medium |

---

## What I'd actually do next, if pressed

1. **Half an hour to decide #19.** Two questions, both well-trodden territory. Hot-path wiring unblocks.
2. **Then #15 design decisions.** Without rolling enrichment, the hot path is a no-op. This is the *actual* freshness gap.
3. **Decide #26.** Five minutes; it's been hanging since the cost-economics conversation.
4. **Schedule an hour-long Axis 2 session** (separate from the above — these are different brain modes).
5. **Verify #17 convergence** opportunistically (just a DB query after the next clean nightly).
6. Hygiene cluster runs in parallel whenever someone has cycles.

The orchestration cluster (#19 → #15 → #3 → hot-path wiring) is where the user-visible value lives. Axis 2 is bigger in code volume but smaller in immediate user impact. Everything else is below the fold.

---

## How this doc stays current

Whenever an issue in here closes, strike it off (or remove the section). When a new design question shows up, add a section. Treat this as a living index of "what would I have to explain to a colleague who walked in cold." If a question listed here goes more than a few months without anyone touching it, ask whether it actually matters or just got fossilized.

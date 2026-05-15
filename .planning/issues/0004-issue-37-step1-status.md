---
issue: 0004
github_issue: 37
title: Issue #37 — Step-1 status map + open questions for steps 2+
status: step-1 gate PASSED + all 7 questions resolved (Q1-Q5, Q7 on 2026-05-15; Q6 on 2026-05-15 fresh-eyes pass). Step 2 PR A fully unblocked.
filed: 2026-05-15
filed_by: end-of-session prep for tomorrow's #37 cycle
related_issues: [37, 38]
related_artifacts:
  - pipeline_enrichment/
  - scripts/equivalence_check.py
  - scripts/run_daily_enrichment.py
  - config/llm_prices.yaml
  - utils/openai_client.py
  - utils/llm_cost.py
---

# Issue #37 — Step-1 status + step-2-onward open questions

## What this doc is

Not a SPEC. The issue body is already a SPEC. This is a 5-minute orientation doc for tomorrow's session — what's built vs. what isn't, and what concrete questions still need an answer before steps 2+ can start.

## Step-1 status: essentially built

Issue #37 step 1 deliverables vs. current repo state:

| Step 1 deliverable | Status | Where |
|---|---|---|
| `pipeline_enrichment/` package | **built** | `pipeline_enrichment/__init__.py` + 8 modules, ~2000 LOC |
| `synopsis.py` + `impact.py` + prompts | **built** | `synopsis.py` (187), `impact.py` (232), `prompts.py` (364) |
| `utils/llm_cost.py` | **built** | confirmed present |
| `config/llm_prices.yaml` | **built** | confirmed present |
| OpenAI client wiring | **built** | `utils/openai_client.py` exposes `GPT5_MODEL = "gpt-5.1"`, `get_default_client()`, `call_with_retry()` |
| Equivalence test harness | **built** | `scripts/equivalence_check.py` (247 LOC) — reports gate verdict against the issue's exact acceptance criteria (impact within ±5 on ≥n−2 of n; synopsis eyeball) |

**The only step-1 work that hasn't happened is running the gate.** OpenAI key landed 2026-05-15 and smoke-tested green (`gpt-5.1` directly available, 130 models visible).

**UPDATE 2026-05-15:** gate run, PASSED. 19/20 impact within ±5; 20/20 synopsis clean; 0 errors. Cost ~$0.50, runtime ~3.5 min. One outlier (PMID 42103311) had Δ=10 with byte-identical synopsis and qualitatively-identical justification — bit-determinism noise as anticipated; below the >2/20 investigate trigger. Result posted as comment on issue #37. **Step 2 cleared.**

## Tomorrow's first move (5 minutes setup, 5–15 minutes runtime)

```bash
python3 -m scripts.equivalence_check --n 20 --output /tmp/eq.json
```

- Reads 20 PMIDs that already have synopsis + impact in MariaDB
- Runs the ported `pipeline_enrichment/synopsis.py` + `impact.py` via the live OpenAI key
- Reports: synopsis_ok / n; impact_pass (Δ ≤ 5) / n; per-PMID old-vs-new diff
- Gate: **≥18/20 impact pass + eyeball synopsis equivalence → step 2 cleared**

Estimated cost: ~20 × ~$0.025 ≈ $0.50 (synopsis + impact only, no Bedrock).

Per the issue's step-1 gate: attach `/tmp/eq.json` (or pasted output) to the step-1 PR description before opening step 2.

## Decisions for steps 2+ (recorded 2026-05-15)

All seven open questions resolved. Q1-Q5 and Q7 in the post-gate session; Q6 in a follow-on fresh-eyes pass the same evening after the throughput/wall-clock/economics inputs were re-checked against measurement.

### Q1 — OpenAI Batch API: when?
**DECIDED 2026-05-15: sync for daily delta. Defer Batch to annual-rescore decision (Q6).**

Why: daily savings = ~$22/yr against $45/yr daily-delta total — not worth Batch's async complexity (up to 24h return, separate code path, more failure modes). The annual rescore ($220/yr → $110/yr) is where Batch fits both economically (~$110 saved) and operationally (annual rescore doesn't need to land in minutes). Annual-rescore plumbing is part of Q6.

### Q2 — ECS Scheduled Task PR shape
**DECIDED 2026-05-15: SPLIT into two PRs.**

- **PR A:** Dockerfile + ECR push + ECS task definition + IAM. Operator can run the task manually via `aws ecs run-task` to validate image, IAM, and DB connectivity before any cron fires.
- **PR B:** EventBridge cron rule + scheduled trigger.

Why: smaller blast radius for PR A. If the image is broken or the IAM is wrong, the cron isn't already firing on bad code. Validates the riskiest pieces (container + IAM) in a manually-triggered context first.

### Q3 — EventBridge cron schedule
**DECIDED 2026-05-15: 04:00 UTC daily (`cron(0 4 * * ? *)`).**

Why: midnight ET = clearly off-hours for ET-based users. Finishes by ~04:15 UTC = before any ~05:00 ET (10:00 UTC) early-bird SPS reads and well before the ~07:00 ET (12:00 UTC) bulk of morning reads. Easy to remember. Bumps against no other ReciterAI scheduled work.

### Q4 — Teams webhook URL
**DONE 2026-05-15:** operator created the Incoming Webhook and added `RECITERAI_TEAMS_WEBHOOK_URL` to `~/.zshrc`. Verified present in shell (value not printed).

### Q5 — Cost guard threshold calibration
**DECIDED 2026-05-15: tighten to $3 estimated.**

Why: normal run = ~$0.18; $3 = 17× normal (still clearly anomalous). $30 = 167× normal — would let a "PMID query bug returns 200 papers" scenario (~$5–7 estimate) slip through and burn ~$5 silently. Tighter $3 catches that case while leaving plenty of headroom for organic growth (50 PMIDs would estimate ~$1.30, well under trip). Update target: `pipeline_enrichment/cost_guard.py` threshold constant.

### Q6 — Annual rescore wall-clock budget
**DECIDED 2026-05-15 (fresh-eyes pass): option (a) — single long-running ECS Fargate task, same handler with `--full`.**

The Q6 framing was built on three inputs that didn't survive the fresh-eyes pass:

1. **~30s/paper estimate was 3× too slow.** Step-2 bootstrap measured ~9s/paper sustained (1,815 papers in ~4h 33m). Recomputed annual rescore wall-clock: 6,200 × 9s ≈ **15.5 hours**, not 52h.
2. **"Fargate task default wall-clock is 14 hours" is not a real constraint.** Verified two ways:
   - The org's existing CDK (`ReCiter-CDK/.../ReCiterCDKECSStack.java:788-811`, `reCiterMachineLearningFargateTask`) defines a `ScheduledFargateTask` with no wall-clock / stop-timeout / attempt-duration field. AWS CDK requires the constraint to be set explicitly; absence = no cap.
   - AWS Fargate / ECS RunTask / EventBridge scheduled targets: no documented maximum task duration. Task runs until the container exits.
   - The "14h" figure most plausibly originated as a misremembering of AWS Batch's `attemptDurationSeconds` max (1,209,600s = **14 days**, not hours, and a different service).
3. **Q1's Batch-savings justification is stale.** Q1 chose Batch for annual rescore citing ~$110/yr saved. With observed $0.00943/paper, annual rescore is ~$58 sync; Batch saves at most ~$29/yr (and `docs/daily-enrichment.md:186-192` independently rejected Batch as "not worth the architectural overhead" for these economics, post-dating Q1). Q1's annual-rescore half is superseded by the doc; Q1's sync-for-daily half stands.

**Concrete shape:**
- Annual rescore = `python -m scripts.run_daily_enrichment --full` running once a year on a Fargate scheduled task.
- Reuses the same Docker image + ECS task definition from step-2 PR A.
- Second EventBridge rule (e.g. `cron(0 6 1 1 ? *)` — 06:00 UTC Jan 1; exact date TBD when step-2 PR B lands).
- Wall-clock: ~15.5h. Marginal compute: ~$1/yr Fargate at 1 vCPU / 2 GB. Total annual rescore cost: ~$58 OpenAI + ~$1 Fargate.
- Cost guard bypassed by `--full` (already implemented in `pipeline_enrichment/daily_job.py`).

**Why not (b) chunked.** The Q6 framing called (b) "least new infra," but that missed that the daily watermark advances *forward through new papers* and is the wrong primitive for revisiting *processed* papers. Chunking would require a separate annual-rescore cursor with its own persistence and recovery. Net-new state to design — not less infra than (a).

**Why not (c) Batch.** Already superseded by `docs/daily-enrichment.md`. Revisit trigger documented there: "If costs ever drift materially upward."

**Implication for Q1.** Q1's "Batch for annual rescore" half is overruled by Q6's decision (and was already overruled by `docs/daily-enrichment.md` before this pass). Sync everywhere. Q1's "sync for daily delta" half is unchanged.

### Q7 — MAX+1 workaround removal
**DECIDED 2026-05-15: standalone PR.** Implemented same session — branch `chore/42-followup-drop-max1-workaround`.

## Step ordering reminder (from issue)

1. Port prompts + scaffolding [**done**; gate not yet run]
2. Daily scheduled job → MariaDB only [blocked on Q1–Q6]
3. DDB dual-write
4. ReciterAI internal reads → DDB (tracked at #38)
5. SPS reader → DDB (tracked in SPS repo)
6. MariaDB write path decommissioned
7. Archive POC

Steps 4 and 5 can be non-simultaneous.

## What this doc does NOT do

- Does not pick answers to Q1–Q7 (that's tomorrow's decision pass)
- Does not run the equivalence gate (that's tomorrow's first action)
- Does not modify any code

Drop-in for tomorrow: read this top-to-bottom, run the equivalence check, then walk Q1→Q7 and decide.

# Grant→researcher ranking eval harness — reference

The loop you run to know whether a matcher change made recommendations **better**,
instead of eyeballing an 80 KB ranking dump. One number per run; diff it against a
saved baseline.

## Why it exists

Every quality judgment on the grant→researcher matcher was human eyeballing (the four
sanity questions in `grant-matching-verification-2026-06-28.md`). You cannot iterate on
ranking quality without a number that moves when a change helps. This produces that
number, and it does so without a ground-truth awardee set (which barely exists — see
back-test below).

## Architecture

```
grant (title + solicitation)
   │  LLM compiler (sign_grant.py) → require/penalize DSL + weighted query   [grant3 scratch]
   ▼
ranker on SPS ECS (match_v9b.ts) → ranks WCM faculty, full pool
   │  RANK_DUMP_URL branch serializes the whole pool → presigned PUT → S3
   ▼
ranking dump JSON  { grant, gate, engine, ranked:[{cwid,name,rank,fit,evidence[]}] }
   │  dump_grant.sh downloads it + enrich_dump() adds solicitation + past-winner awardees
   ▼
grant_eval.py
   ├─ LLM judge (BLIND to fit/rank/relevance) grades each top-k researcher 0–3
   │     → nDCG@k, precision@5, mean-fit
   └─ back-test: funding-DB past_recipients (email→cwid) → rank in pool
   ▼
aggregate number  +  per-grant breakdown  +  Δ vs saved baseline
```

## The ranking-dump contract (unit of input)

```json
{
  "grant": "worldquant", "gate": "esi", "engine": "match_v9b",
  "solicitation": "<focus text>",
  "ranked": [
    {"cwid": "qiz4006", "name": "…", "rank": 1, "fit": 4.55,
     "evidence": [{"pmid","title","year","subtopic","relevance"}, …]}
  ],
  "awardees": ["cwid", …]        // optional; else joined from the funding DB by title
}
```

Two ways to produce dumps:
- **ECS full-pool (the real path):** `scratch-matching/dump_grant.sh <key> <gate>` runs the
  ranker on staging and emits the whole ranked pool. Use this.
- **Markdown bootstrap (legacy):** `grant_eval_from_verification.py` parses the
  verification report into top-8 dumps. Only for the first baseline; the top-8 cap
  starves the back-test. Retire once every grant has an ECS dump.

## The two metrics

**A. LLM-judge nDCG@k / precision@5 / mean-fit** — every grant, scalable. A judge model,
blind to the ranker's own scores, grades each shortlisted researcher's *scientific* fit
0–3 from the grant text + paper titles, told to grade substance not keyword overlap
(so it flags lexical leakage instead of rubber-stamping — on the first run it caught the
known WorldQuant leaks, Boyraz/Arifuzzaman → fit 1). Bedrock Sonnet with an OpenAI gpt-5.1
fallback; temperature 0 for run-to-run stability.

> **Read all three metrics, not just nDCG.** nDCG measures *ordering* and can look fine
> when the whole top-k is off-topic (Progeria: nDCG 0.82 but mean-fit 0.25 — the top-8 are
> all tangential, they just happen to be ordered). `mean_fit` and `precision_at_5` reveal
> total-miss cases nDCG hides.

**B. Historical-winner back-test** — subset, honest. For the funding-DB grants with
`past_recipients`, awardee email-prefix → cwid → rank in the pool. **Structurally
data-capped:** awardees skew 2012–2019 against the 2020+ corpus floor, and many have left
or no longer first/last-author, so most never enter the pool (Hirschl: 24 awardees, 0 in a
67-person pool). Keck is the one grant where it works (3/5, one at rank 1). Treat it as a
directional sanity check ("did we keep known winners high?"), not the primary gate.
Expanding the benchmark does **not** fix this — only recent, still-active awardee data would.

## Run a loop

```bash
# 1. produce a full-pool dump for a grant (staging ECS; needs the infra config below)
cd ~/worktrees/reciterai-grant3
SUBNETS=<app-subnets> SG=<app-sg> bash scratch-matching/dump_grant.sh <key> <esi|ft>

# 2. score everything + diff vs the frozen baseline
cd ~/worktrees/reciterai-grant-eval
PYTHONPATH=. python3 -m pipeline_grants.grant_eval '<DUMPS>/*.json' -k 8 \
  --funding-db wcm_funding_db_2026-06-28.json --baseline <DUMPS>/baseline.json
#   → Δ mean_ndcg_at_k: +0.031  (0.863 -> 0.894)

# freeze a NEW baseline (once, or after accepting a gain):  add  --out <DUMPS>/baseline.json
# free/instant back-test-only pass (no LLM calls):           add  --no-judge
```

`pipeline_grants/run_eval.sh` wraps step 2 (`FREEZE=1` to re-freeze, `NOJUDGE=1` for the
free pass). It does **not** rebuild dumps from markdown unless `BUILD_FROM_MARKDOWN=1` —
that legacy step would clobber the full-pool dumps.

## ECS dump mechanics + infra config (load-bearing)

The ranker imports SPS `@/lib/ranking`, so it runs on `sps-cluster-staging`. `dump_grant.sh`
→ `run_s3.sh` stages the script in S3, runs it, and gets the dump out via a presigned PUT.

- **Task definition:** use the family name `sps-etl-staging` (latest active), NOT a pinned
  revision — old pins go inactive. `TASKDEF=` overrides.
- **Network:** the historical ETL subnets/SG drifted and no longer reach the DB (symptom:
  Prisma `pool timeout … active=0`). Run in the **app service's** network config, which
  provably reaches the DB — read it live: `aws ecs describe-services --cluster
  sps-cluster-staging --services sps-app-staging --query
  'services[0].deployments[0].networkConfiguration'`. Pass as `SUBNETS=…,… SG=…`.
- **Concurrency:** ≤3 concurrent Fargate tasks (DB-pool limit) — batch serially to be safe.
- **Batch loops must use `key:gate` + `${gg%%:*}`/`${gg##*:}`** — zsh does not word-split
  unquoted `$var`, so `set -- $pair` silently passes one arg.

## File inventory

**Committed (this repo / PR #284):** `pipeline_grants/grant_eval.py` (harness),
`grant_eval_from_verification.py` (markdown bootstrap + `enrich_dump`), `run_eval.sh`
(wrapper), `tests/test_grant_eval.py`, this doc, the handoff.

**Scratch — grant3 worktree (`~/worktrees/reciterai-grant3`, uncommitted by design):**
`scratch-matching/match_v9b.ts` + `match_v8.ts` (the `RANK_DUMP_URL` branch),
`dump_grant.sh`, `run_s3.sh` (task-def + network overrides), `sign_grant.py` +
`extra_grants.json` + `_expansion_shortlist.json` + compiled `<key>_{dsl,query,vocab}.json`.
**These would be lost if the worktree is removed — do not `git worktree remove` it.**

**Local artifacts (not committed — carry awardee cwids):** the dumps + `baseline.json` in
`scratchpad_eval_dumps/`. Regenerable; keep local.

## Known ceilings / gotchas

- **Judge variance.** Absolute fit calibration drifts run-to-run (same researcher can score
  3 then 2 in different batches); nDCG is set-relative so it's robust, but treat small
  cross-run mean deltas cautiously. For a borderline change, add a second judge (the OpenAI
  fallback path) and require both to agree on direction.
- **Pediatric gate.** v9b's pediatric admission gate cuts pools hard (Hartwell 266→5), so
  set `pediatric` only for grants that actually restrict to pediatric *research programs*,
  not merely pediatric-onset diseases.
- **The ranker never abstains.** For rare-disease/niche grants with no WCM coverage it still
  returns 8 tangential people (see the handoff's finding + next-step).

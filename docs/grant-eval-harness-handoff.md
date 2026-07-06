# Handoff — grant→researcher ranking eval harness

**Date:** 2026-07-06 · **For:** a fresh session continuing this work · **Status:** harness
built + committed (PR #284); full-pool ECS dump path built + proven; a **24-grant v9b
baseline is frozen** and has surfaced the ranker's first real quality finding. Remaining
work is the improvement loops the harness now makes measurable.

**Read order:** this → `docs/grant-eval-harness.md` (how the system works + run commands) →
the memory ledger `[[reciterai-grant-eval-harness]]` (every decision) → the matcher itself
`[[reciterai-grant-matching-reformulation]]` + `docs/grant-matching-architecture-handoff.md`.

---

## 1. TL;DR — where this is

The grant→researcher matcher had no way to measure ranking quality except human eyeballing.
This harness turns quality into **one loopable number**: an LLM judge (blind to the ranker's
scores) grades each shortlisted researcher's fit 0–3, and we score the ranker's order
(nDCG@k / precision / mean-fit), cross-checked against a historical-winner back-test. The
ranker emits its **full ranked pool** from staging ECS as a dump JSON the harness consumes.

**Current frozen baseline: 24 grants, mean nDCG@8 = 0.863, P@5 = 0.70, stdev 0.121,
range 0.526–1.00** (engine `match_v9b`, full pool). It's the reference every future change
diffs against.

## 2. THE finding so far — the ranker is not consistent

Expanding the benchmark 10→24 dropped the mean 0.915→0.863 and exposed a **fat tail of
failures the 10-grant set hid**. Every failure is the same shape: a **rare-disease /
niche-field grant where WCM has ~no matching faculty**, so the `require` substring-net pulls
in adjacent-but-off-topic people and the ranker returns them with confidence.

- **angelman** 0.526, mean-fit 0.38 — top-8 are general epilepsy/synaptic faculty, all fit-0
- **msts/sarcoma** 0.61 — one real hit (#5) drowning in general oncology
- **progeria** — nDCG 0.82 *but* mean-fit 0.25 (whole top-8 tangential; **nDCG alone lied**)

Strong archetypes work great (brightfocus, wcm_neurodegen, bca_yi, ada, keck all ≥0.95).
**The flaw: the ranker never abstains** — for a grant with no WCM coverage it still returns
8 tangential people, which is worse than returning none.

## 3. What's built + where

- **Committed — ReciterAI PR #284** (`feat/grant-eval-harness`, base `main`): `pipeline_grants/
  grant_eval.py`, `grant_eval_from_verification.py`, `run_eval.sh`, `tests/test_grant_eval.py`
  (7 pass), `docs/grant-eval-harness.md` + this handoff. Review-only, not merged.
- **Scratch — grant3 worktree (`~/worktrees/reciterai-grant3`, uncommitted by design, KEEP):**
  the `RANK_DUMP_URL` branch in `match_v9b.ts`/`match_v8.ts`, `dump_grant.sh`, `run_s3.sh`
  (task-def + `SUBNETS`/`SG` overrides), `sign_grant.py` + `extra_grants.json` (23 grants) +
  `_expansion_shortlist.json` + compiled `<key>_{dsl,query,vocab}.json` for all 24.
- **Local artifacts (not committed — carry awardee cwids):** 24 dumps + `baseline.json` in
  `scratchpad_eval_dumps/`. Regenerable.

## 4. Infra fixes required to run ANY ECS ranker (both drifted since 6/28)

1. Task def `sps-etl-staging:15` is **inactive** → `run_s3.sh` now uses the family name
   (latest active). `TASKDEF=` overrides.
2. The hardcoded ETL subnets/SG **can't reach the DB** (Prisma `pool timeout, active=0`). Run
   in the **app service's** network: `SUBNETS=subnet-0c6593fb9c9a165c3,subnet-070cbc242efbddc3c
   SG=sg-010c270a395b4854b` (read live from `describe-services sps-app-staging` if it drifts).

Both are env-overridable in `run_s3.sh`. Without them, no dump runs.

## 5. Next steps (prioritized)

1. **Abstention / absolute-fit floor — the finding's fix, and the highest-value loop.** When
   the top candidates' relevance/score is below a threshold, return "no strong WCM match"
   instead of 8 tangential people. Measurable directly: does it raise mean-fit on the broken
   tail (angelman/progeria/msts) **without** lowering the strong grants? Build behind a knob,
   sweep the threshold against the baseline.
2. **Rigorous v8-vs-v9b head-to-head.** The current baseline is v9b; v9b is a *mixed* change
   (helped keck 0.745→0.956, hurt worldquant 0.912→0.817). Dump v8 full-pool through the same
   pipeline (`RANKER=scratch-matching/match_v8.ts dump_grant.sh …`) and compare per-grant —
   decide whether v9's rank-discounted volume/recency is worth productionizing.
3. **Weak-middle tier** (worldquant, pew_stewart, sanofi, damon_runyon 0.78–0.82): the
   dense-relevance signal (#283/#1349, built + dark) is the named fix for the lexical leakage
   dragging these down. Land it, re-dump, measure.
4. **Back-test data.** It's data-capped (3/45 in pool). Only recent, still-active WCM
   awardee lists (operator-only) would give it teeth; not worth more benchmark grants.
5. **Expand further only if a knob sweep shows n=24 is too noisy.** The 207-scrape has more
   faculty-topical grants; the classifier + `dump_grant.sh` make it cheap. Diminishing returns
   past ~30–40.

## 6. Run mechanics / pointers

- **Score + freeze the current baseline:** `cd ~/worktrees/reciterai-grant-eval && PYTHONPATH=.
  python3 -m pipeline_grants.grant_eval '<DUMPS>/*.json' -k 8 --funding-db <db> --out <DUMPS>/baseline.json`
  (sandbox OFF; needs `AWS_BEARER_TOKEN_BEDROCK`).
- **Add a grant to the benchmark:** add `{pediatric,title,desc}` to grant3
  `extra_grants.json` (+ gate in `_expansion_shortlist.json`), `ln -sf _shared_vocab.json
  <key>_vocab.json`, `python3 sign_grant.py <key>` + `<key> query`, then `dump_grant.sh`.
- **Staging:** acct 665083158573, DDB `reciterai`. `aws sts get-caller-identity` before writes.
- **Batch dumps serially**, ≤3 concurrent, `key:gate` + `${gg%%:*}`/`${gg##*:}` (zsh).

## 7. Caveats carried

- Judge variance (absolute fit drifts run-to-run; nDCG is set-relative so robust). For
  borderline deltas, add the OpenAI-fallback judge as a second vote.
- Read mean-fit + P@5 alongside nDCG (progeria proved nDCG-alone misleads).
- Generated dumps carry past-winner cwids from the redacted funding DB → never commit them.
- Recompile DSL/query on taxonomy bumps (same obligation as the deployed matcher).

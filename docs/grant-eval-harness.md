# Grant→researcher ranking eval harness

The loop you run to know whether a matcher change made recommendations **better**,
instead of eyeballing an 80 KB ranking dump. One number per run; diff it against a
saved baseline.

## Why

Every quality judgment on the matcher was human eyeballing (the 4 sanity questions in
`grant-matching-verification-2026-06-28.md`). The past-awardee back-test alone is not
enough (awardees skew pre-2020 vs the 2020+ corpus floor; ≤1 recent winner per topical
grant). This harness pairs two cross-validating metrics so you can iterate:

- **A. LLM-judge nDCG / precision** (every grant, scalable). A judge model — blind to
  the ranker's fit/rank/relevance — grades each shortlisted researcher's *scientific*
  fit 0–3 from the grant text + their paper titles, told explicitly to grade substance
  not keyword overlap. We score the ranker's order against those grades. On the first
  run it independently re-flagged the two known WorldQuant leaks (Boyraz molecular
  pathology, Arifuzzaman microbiome → fit 1), i.e. it catches the lexical-substring
  failures instead of rubber-stamping them.
- **B. Historical-winner back-test** (subset, honest). For the 35 WCM funding-DB grants
  with `past_recipients`, awardee email-prefix → cwid → where does the winner land in
  the ranked pool. Coverage is always reported; partial by design.

## Run a loop

```bash
# 1. (once) build the benchmark dumps from the verification report
python -m pipeline_grants.grant_eval_from_verification \
  docs/grant-matching-verification-2026-06-28.md  <DUMPDIR> \
  --solicitations ~/worktrees/reciterai-grant3/scratch-matching/grant_solicitations.json \
  --extra-grants  ~/worktrees/reciterai-grant3/scratch-matching/extra_grants.json \
  --funding-db    wcm_funding_db_2026-06-28.json

# 2. score them → baseline (sandbox OFF; needs AWS_BEARER_TOKEN_BEDROCK)
python -m pipeline_grants.grant_eval '<DUMPDIR>/*.json' -k 8 \
  --funding-db wcm_funding_db_2026-06-28.json --out baseline.json

# 3. change the matcher, regenerate dumps, re-score, diff:
python -m pipeline_grants.grant_eval '<DUMPDIR>/*.json' -k 8 \
  --funding-db wcm_funding_db_2026-06-28.json --baseline baseline.json
#   → Δ mean_ndcg_at_k: +0.031  (0.863 -> 0.894)
```

`--no-judge` skips all LLM calls (back-test only) for a free, instant iteration.
Judge is temperature 0 for run-to-run stability; ~10 Bedrock calls for the 10-grant set.

## Baseline (2026-07-06, engine v8, dumps = verification report top-8)

mean nDCG@8 **0.863** · mean P@5 **0.70** · back-test 1/18 awardees in the top-8 pool.
Weakest grants (ranker order least aligned with scientific fit): pew_stewart 0.690,
keck 0.745, brf_sia 0.786. These are the first places a matcher change should move.

## The input contract (a "ranking dump")

```json
{"grant":"worldquant","gate":"esi","solicitation":"<focus text>",
 "ranked":[{"cwid","name","rank","fit","evidence":[{"pmid","title","year","subtopic","relevance"}]}],
 "awardees":["cwid", ...]}
```

The durable source of dumps is the **SPS ECS ranker emitting this JSON** (the
`dump_rankings` harness). The verification-markdown converter is a one-time bootstrap;
retire it once the ranker emits dumps directly.

## Known ceilings (upgrade paths)

- **Dumps are top-8 only** → back-test is hit@8 (percentile needs the full ranked pool).
  Emit the full pool from the ECS ranker → the back-test gets teeth (that's why in-pool
  rate is 5.6% now). This is the highest-value next data change.
- **Judge sees titles only.** Adding abstracts (ReciterDB) would sharpen borderline
  grades; titles already separated the WorldQuant leaks, so it's an upgrade not a fix.
- **Judge is one model.** For a change whose Δ is within noise, add a 2nd judge
  (OpenAI gpt-5.1, already the fallback path) and require both to agree on direction.
- Do NOT commit generated dumps: their `awardees` arrays carry past-winner cwids joined
  from the redacted funding DB. Code is safe to commit; dumps stay local.
```

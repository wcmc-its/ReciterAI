# Core 14 calibration pass (2026-10-06)

> **Measured config.** These numbers were measured with the s = 5 sliding-scale affinity only, BEFORE core 14's `affinity_min_confirms: 3` was restored (#424, 761fe0a). The identity decision does not depend on that, but re-run `scripts/measure_calibration.py` under the shipped config before setting a non-identity map.

Stacks on PR #424 (`feat/affinity-tenure-decay-shrinkage` at `0924002`). Everything below was produced on this branch's code: affinity rate `(n + s*p0) / (total + s)` with s = 5, refitted `aff:*` weights 1.18 / 3.49 / 3.49.

## Question

A signal can be redundant for ranking, so it adds no AUC, and still improve the accuracy of the probability. `combine.score()` cannot take advantage of that. It fits each weight marginally and adds up the log-odds (naive Bayes), so correlated evidence is counted twice and the probability comes out overconfident. Only a joint fit can use the redundancy. So the questions were:

1. How miscalibrated is the shipped probability?
2. Does a recalibration or a joint refit fix it?
3. Should core 14 get a per-core calibration now?

## Answer

- **The hook ships at identity.** `calibration: {intercept, slope}` is an optional per-core key in `core_dictionary.yaml`. `combine.calibrated_logit` applies `logit' = intercept + slope * logit` before the status bands, and the never-the-deciding-vote hold is calibrated with the same map. The global defaults are `DEFAULT_CALIBRATION_INTERCEPT = 0` and `DEFAULT_CALIBRATION_SLOPE = 1`. The loader rejects a slope of 0 or below, non-finite values and unknown keys. No core sets the key, so no score changes.
- **Core 14 stays at identity.** The gate was "held-out log-loss improves with a CI excluding zero". It passes on all 46 decided rows and on the 27 in-corpus rows. It fails on the 26 engine-scored rows, which are the rows a calibration would actually act on. The fitted map also breaks two of the anchors the thresholds were set against.
- **The user's point holds on the only panel big enough to show it.** On panel B, with the ack evidence set aside, the summed score is mildly overconfident (Platt slope 0.82 [0.70, 1.06]). A joint refit of the same features beats a Platt rescale by 0.0055 [0.0004, 0.0114] nats. Almost all of that comes from the affinity buckets: their joint weight is about +0.4 nats, against +1.18 / +3.49 when fitted marginally. Given staff and the LLM, the affinity prior is mostly a second copy of evidence the score already counts.
- **Adding redundant features to the joint fit gives nothing measurable on panel B.** The features tried were WCM first/last author, the INSIGHT CRN alias (#422) and the method tier. The change is -0.0007 [-0.0057, +0.0070]. On core 14 the gain is -0.15 [-0.32, -0.02] across all 46 rows, but it comes from the INSIGHT alias firing on 2 claimed rows and 0 rejected ones, and it vanishes on the in-corpus and engine-scored subsets.

## Method

`scripts/measure_calibration.py` runs in two steps.

```
# read-only: DynamoDB Scan of CORE#14, reciterdb SELECTs, S3 GETs (full-text cache through a
# wrapper whose put_object raises; A2 tools artifact), PubMed efetch, Bedrock triage only
# for the 19 core-14 claims with no stored and no cached LLM score
python3 scripts/measure_calibration.py collect --out inputs.json \
    --llm-cache <scratch>/tfidf/llm_cache.json --c14-llm-cache c14_llm.json --live-llm --cache-dir ft
# pure
python3 scripts/measure_calibration.py analyze --inputs inputs.json --out results.txt
```

**Models.** Every model is refitted leave-one-out without the scored row. CIs come from a 2,000-sample paired bootstrap over rows of the held-out predictions. They do not re-run the refit, so they understate model variance.

- **(1) score**: `combine.score()` as shipped.
- **(1b) offset**: an intercept-only shift, which is what a per-core prior would do.
- **(2) Platt**: logistic(a + b · logit(score)).
- **(3) joint**: L2 logistic on combine's own evidence. The columns are the ack chain-rule block (one column), staff, the three `aff:*` indicators, the LLM score and LLM-missing. C is chosen by an inner CV on the training fold.
- **(4) joint+**: (3) plus WCM first author, WCM last author (PubMed affiliations, the #428 regex), the INSIGHT CRN alias matched in full text (#422's two aliases, through production's matcher) and the method tier (strong / moderate / weak).

**Panel B** (core 2, imaging): 137 human "yes" labels against 421 random-corpus papers. The negatives all have a live `signals.llm_triage` score, cached from the #423 TF-IDF run, which used the same draw rule as `fit_evidence_weights.py`. The 100 labelled "no" rows are reported only as a foil. This is a case-control panel, so every metric and fit is weighted to give the positives 2% of the weight, the base rate `combine.PRIOR_LOGIT` assumes. Ack is computed from full text for 630 of the 658 papers. 133 of the 137 "yes" papers name the core, which makes the panel close to separable when ack is included (held-out AUC 0.998). The informative variant is therefore **panel B without ack** (staff + `aff:*` + LLM), which is the evidence panel B was used to price.

**Core 14**: the 26 claimed and 20 rejected rows. Signals were recomputed with production's functions: ack from full text, staff, method tier, and affinity under this branch's sliding scale. The LLM score is the stored one where the engine scored the row (27 rows). The 19 claims outside the WCM corpus, plus 1 in-corpus claim the engine never scored, were triaged live; 19 of those 20 scores match an earlier session's cache. "14w" adds the 25 engine-confirmed rows that carry ack or staff evidence as weak positives.

## Results

Held-out log-loss, with the difference from (1) and its 95% CI:

| panel | n (+/-) | score | offset | Platt | joint | joint+ |
|---|---|---|---|---|---|---|
| B, no ack (weighted to 2%) | 558 (137/421) | 0.0568 | 0.0466 (-0.010 [-0.020,-0.002]) | 0.0458 (-0.011 [-0.022,-0.002]) | 0.0403 (-0.017 [-0.032,-0.004]) | 0.0396 (-0.017 [-0.033,-0.004]) |
| B with ack (weighted) | 558 | 0.0354 | 0.0049 | 0.0055 | 0.0043 | 0.0044 |
| 14, all decided | 46 (26/20) | 0.918 | 0.673 (-0.25 [-0.56,+0.07]) | 0.586 (-0.33 [-0.60,-0.10]) | 0.333 (-0.59 [-1.05,-0.14]) | 0.182 (-0.74 [-1.18,-0.34]) |
| 14, in-corpus | 27 (7/20) | 1.526 | 0.903 (-0.62 [-1.21,-0.02]) | 0.678 (-0.85 [-1.32,-0.38]) | 0.397 (-1.13 [-1.78,-0.42]) | 0.405 (-1.12 [-1.78,-0.44]) |
| 14, engine-scored | 26 (6/20) | 1.389 | 0.591 (-0.80 [-1.37,-0.15]) | 0.845 (-0.54 [-1.30,+0.44]) | 0.457 (-0.93 [-1.49,-0.35]) | 0.453 (-0.94 [-1.48,-0.38]) |
| 14w, + weak positives | 71 (51/20) | 0.595 | 0.438 | 0.406 (-0.19 [-0.36,-0.04]) | 0.177 | 0.110 |

Pairwise comparisons:

| panel | joint − Platt | joint+ − joint | Platt − offset |
|---|---|---|---|
| B no ack | -0.0055 [-0.0114, -0.0004] | -0.0007 [-0.0057, +0.0070] | -0.0008 [-0.0031, +0.0010] |
| 14 all | -0.25 [-0.59, +0.10] | -0.15 [-0.32, -0.02] | -0.09 [-0.32, +0.11] |
| 14 in-corpus | -0.28 [-0.73, +0.12] | +0.01 [-0.12, +0.13] | -0.23 [-0.82, +0.21] |
| 14 engine-scored | -0.39 [-1.38, +0.22] | -0.00 [-0.10, +0.12] | +0.25 [+0.04, +0.62] |

Platt fits on all rows, with bootstrap CIs. A slope below 1 means the score is overconfident.

| panel | intercept | slope |
|---|---|---|
| B no ack | -1.67 [-2.16, -0.82] | 0.82 [0.70, 1.06] (unweighted 0.87) |
| B with ack | -4.37 [-13.5, -3.0] | 1.19 [0.95, 4.64] |
| 14 all | -0.99 [-4.32, +0.11] | 0.39 [0.16, 1.35] |
| 14 in-corpus | -1.46 (unidentified) | 0.18 [-0.40, 238] |
| 14 engine-scored | -2.14 (unidentified) | 0.35 [-0.14, 301] |

Joint per-unit weights, fitted on all rows of panel B without ack: staff +7.38, `aff:trace` +0.41, `aff:regular` +0.43, LLM +0.67 per point. The shipped marginal weights are 4.89, 1.18, 3.49 and 0.68.

Reliability on core 14, all 46 rows, held-out. Each cell is n, claimed, mean predicted and observed rate.

| bin | score | Platt |
|---|---|---|
| [0, 0.10) | 2, 2, 0.006, 1.00 | 2, 2, 0.011, 1.00 |
| [0.10, 0.30) | 2, 0, 0.221, 0.00 | 11, 2, 0.270, 0.18 |
| [0.30, 0.40) | none | 5, 0, 0.358, 0.00 |
| [0.40, 0.65) | 9, 2, 0.536, 0.22 | 10, 4, 0.583, 0.40 |
| [0.65, 0.90) | 5, 0, 0.704, 0.00 | 13, 13, 0.806, 1.00 |
| [0.90, 1.00] | 28, 22, 0.986, 0.79 | 5, 5, 0.981, 1.00 |

ECE is 0.31 for score, 0.20 for Platt, 0.09 for joint and 0.04 for joint+. The full reliability tables for every panel are in the script's output.

### Decisions at the cuts

On the 46 decided rows, using held-out P only (the hold rules are not applied):

| model | ≥ 0.30 triage | ≥ 0.40 SPS floor | ≥ 0.65 confirm |
|---|---|---|---|
| score | 42 (24 claimed / 18 rejected) | 42 (24/18) | 33 (22/11) |
| Platt | 33 (22/11) | 28 (22/6) | 18 (18/0) |
| joint | 25 (24/1) | 24 (23/1) | 21 (20/1) |
| joint+ | 28 (25/3) | 26 (24/2) | 24 (23/1) |

Next, the all-46 Platt map (a = -0.992, b = 0.391) was applied to all 5,365 stored core-14 rows. Each row's logit is its stored likelihood with its `aff:*` term swapped for this branch's, as `measure_affinity_prior_strength.rebanded` does, and the LLM and staff holds are applied. The rows split into bands as follows:

| band | identity | calibrated |
|---|---|---|
| candidate | 595 | 345 |
| confirmed | 12 | 11 |
| below | 4,738 | 4,989 |
| never engine-scored | 20 | 20 |

- 251 rows fall from candidate to below: 180 stored candidates, 57 stored below that this branch's affinity had raised to candidate, 7 stored confirmed and 7 rejected.
- At the SPS display floor of 0.40, 544 rows clear it under identity and 307 under the map.
- Single pieces of evidence under the map:
  - distinctive alias + home institution: 1.000 → 0.954
  - generic alias + home institution: 0.706 → 0.343 (no longer confirms)
  - staff alone: 0.731 → 0.354
  - LLM 9 alone: 0.591 → 0.300
  - LLM 8 + `aff:regular`: 0.960 → 0.563
  - no evidence: 0.020 → 0.075

## Why core 14 is not calibrated yet

- **The positives are mostly not rows the engine scores.** 19 of the 26 claims are outside the WCM corpus, so production never scores them. They tell us how the score function would rate a paper, but not how well calibrated the probabilities are on the queue. On the 26 engine-scored rows, Platt's CI includes zero and its fit is unidentified.
- **The panel is selected on the score.** Reviewers decided rows the engine had surfaced, 19 of the 20 rejected rows had LLM scores of 7–8, and the claims came from elsewhere. A joint fit on this panel gives the LLM a negative weight (about -0.5 to -0.9 per point) and gives `aff:core` a negative weight. That pattern is the selection, not how the corpus behaves. None of the joint fits on core 14 could be shipped, whatever their log-loss.
- **The map contradicts measured anchors.** A generic alias beside a home institution was the branch the institution audit said should confirm, and the map stops it confirming. The map also raises a paper with no evidence from 2% to 7.5%. A single global slope rescales every kind of evidence, so it is the wrong tool when the overconfidence is concentrated in a few features.
- **The direction is consistent, though.** Every core-14 variant prefers a downward shift. The intercept-only offset is -2.5 on all 46 rows and -3.9 on the engine-scored rows, and on those rows it beats Platt (+0.25 [+0.04, +0.62]). That fits the core's measured base rate: p0 = 79 / 82,203 = 0.1%, about 3 nats below the 2% `PRIOR_LOGIT` assumes. A per-core prior is likely the right first knob once the labels support it. It uses the same hook: set the slope to 1 and the intercept to the offset.

## What would change the decision

Re-run both steps once core 14 has at least 100 decided in-corpus rows, or once a random sample of the queue has been decided. A random sample would remove the selection problem. Then set `calibration: {intercept: a, slope: b}` for core 14 only if all of these hold:

- the engine-scored row's held-out log-loss delta has a CI that excludes zero;
- the slope CI is bounded;
- a distinctive alias still confirms and a lone LLM score still does not.

If a joint refit keeps beating Platt on panel B as panels grow, the durable fix is in `fit_evidence_weights.py`: fit the `aff:*` cells conditional on staff and the LLM, instead of adding a per-core calibration.

## Caveats

- **The CIs are optimistic.** Bootstrapping LOO predictions does not refit. With n = 26–46, read every core-14 interval as wider than printed.
- **Panel B is weighted to 2%.** Its absolute log-loss values depend on that choice. Its Platt slope does not: unweighted it is 0.87.
- **About 2% of the random-corpus negatives are probably true users**, which biases the weights down. This is the same contamination the fit script already accepts.
- **The core-14 band counts start from stored rows.** The stored likelihoods were scored with `main`'s weights (0.79 / 3.43 / 4.93), and only the `aff:*` term is swapped. They are not the output of a full pipeline run.

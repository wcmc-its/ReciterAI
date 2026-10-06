# Cores: lab-level usage as a signal (experiment, 2026-10-06)

## Verdict

Dead. No `combine.WEIGHTS` key, not even one at 0.00.

Lab-level usage is not independent of author affinity. It cannot be, by construction: the lab head is on the byline, and every core paper the lab head led is also one of that author's own core papers. So `lab_any > 0` implies `aff_se > 0`. Measured: on 0 of 77 rows where the lab signal fires is the self-excluded author affinity silent. The lab signal only re-scales the repeat-user count that affinity already carries. It never reaches a paper affinity misses.

On the panels it can be measured on, it adds no usable lift over staff + self-excluded affinity + LLM:

- **Core 2 (imaging), hard + LLM panel.** The only lift whose CI clears 0 is `lab_any`, +0.0040 [+0.0008, +0.0079], and its fitted weight is **negative** (w = -3.20). That is a correction to the affinity buckets, which over-price rows whose rate comes from a lab head. It is not positive evidence of usage. A presence key in `combine()` cannot carry a negative weight against the feature's own marginal direction. Against the #423 baseline (`aff_fit`) the same feature gives -0.0023 [-0.0192, +0.0070].
- **Core 14 (Research Informatics) human decisions.** Lab precision is at or below affinity precision: `lab_any` 9/21 = 0.43 vs `aff_se` 17/34 = 0.50 with the production core set; with the strict core set it is 5/17 = 0.29 vs 17/31 = 0.55.
- **Self-reference.** The lab rate is more self-referential than the author rate, not less. Of the panel papers that are themselves in the core set, removing the paper from its own numerator zeroes the lab rate on 2 of 9 (core 2) and 4 of 6 (core 14, production set), against 0 of 9 and 2 of 6 for author affinity. A lab's core count is a subset of its head's, so it is smaller and loses more when one paper comes out.
- **Small denominators** (the "new postdoc in a heavy-user lab" case). Affinity already handles it: it takes the MAX over the byline, so the PI's rate lifts the paper. On core 2, among the 52 hard-panel rows whose largest non-staff author has only 1 to 4 corpus papers, affinity fires on 9 (all 9 are yes) and the lab signal fires on 7 of those same 9.
- **Year window** (`lab_win`: the lab used the core in the 3 years before the paper). It fires on 6 core-2 rows (3 yes) and gives no lift: +0.0010 [-0.0017, +0.0038].

## What was measured

Script: `scripts/experiment_lab_affinity.py`. Every number here comes from one run of it:

```
python3 scripts/experiment_lab_affinity.py --llm-cache <#423 llm_cache.json> \
    --ddb-rows <read-only CORE# scan JSON> --out rows.json
```

The run was re-reported with `--from-json rows.json`, and the report output matched byte for byte.

**Lab head.** The lab head is the last resolved author on the byline (`analysis_summary_author_list.rank`, `personIdentifier` set) who is not one of the core's own staff (the #418 rule). The `sen_*` variants count only papers where that author is the true last author (rank equal to the paper's max rank). Corresponding author is not in reciterdb, so it is not used. Coverage: a lab head was found for 122 of 137 yes and 417 of 421 random papers on core 2; 212 of the 658 panel papers have the head as true last author. On core 14 the figure is 44 of 46.

**Features**, for scored paper p with head h. Every count excludes p.

- `lab_n`: core papers led by h, corpus-gated.
- `lab_N`: corpus papers led by h.
- `lab_rate = lab_n / (lab_N + 1)`.
- `lab_any = lab_n > 0`.
- `lab_win`: core papers led by h published in [year(p) - 3, year(p) - 1]. This one is not corpus-gated, because the window reaches back past 2020.

**Baseline.** The baseline is `combine.score()` on staff + `aff_se` + LLM. `aff_se` is author affinity (MAX over the non-staff byline) from the same core set as the lab feature, with p removed from both its numerator and its denominator. `ack` is left out of the hard-panel baseline because only the labelled side has cached full text. `aff_fit` is the #421/#423 affinity (confirms outside the whole label set), reported for comparability.

**Lift.** Lift is AUC of `base + w * feature` vs `base`, with w fitted leave-one-out and the baseline's coefficient fixed at 1. The CIs come from a 2,000-rep paired bootstrap. The machinery is the same as #423.

**Panels.**

1. **Core 2 hard + LLM.** The 137 labelled yes papers (`labeled_set.csv`) against the 421 random-corpus papers (fit draw, seed 11) that carry a live two-pass LLM score from #423's cache. The core set is the 68 signal-2 confirms unioned with 69 DynamoDB confirmed/claimed rows: 69 papers, all in the corpus.
2. **Core 2 labelled yes vs no.** Saturated: the baseline with `ack` scores 0.9989, so it is shown only for completeness.
3. **Core 14 claimed vs rejected.** These are the only human decisions in DynamoDB outside 1 core-2 claim, from a read-only Scan of every CORE# row: 26 claimed and 20 rejected on core 14. No other core has a claimed or rejected row, and every confirmed row on cores 1, 2, 3, 5, 9, 11 and 12 is a staff-coauthor confirm. Two core sets are used. `all` is the 72 confirmed + 26 claimed rows that production feeds affinity. `strict` is claimed plus confirmed rows with ack or staff evidence, which drops the 47 rows confirmed on affinity or LLM alone.

## Results

### Core 2, hard + LLM (n = 558: 137 yes / 421 random)

The baseline (staff + `aff_se` + LLM) scores AUC 0.9427.

| feature | fires (yes) | marginal AUC | lift, CI95 | w |
|---|---|---|---|---|
| lab_any | 37 (25) | 0.5770 | +0.0040 [+0.0008, +0.0079] | -3.20 |
| lab_rate | 37 (25) | 0.5786 | +0.0001 [-0.0012, +0.0013] | -7.64 |
| lab_win_any | 6 (3) | 0.5074 | +0.0010 [-0.0017, +0.0038] | -4.40 |
| sen_lab_any | 17 (10) | 0.5282 | +0.0026 [+0.0003, +0.0055] | -3.53 |
| sen_lab_rate | 17 (10) | 0.5285 | -0.0178 [-0.0362, -0.0049] | (degenerate) |
| sen_lab_win_any | 5 (2) | 0.5037 | +0.0002 [-0.0045, +0.0037] | -4.42 |

Every weight is negative. Among rows where affinity fires, the ones where `lab_any` also fires have a mean `aff_se` of 0.402 and a mean base logit of 3.90. The ones where it does not have a mean `aff_se` of 0.167 and a mean base logit of 1.09. Their yes rates are close: 25 of 37 (0.68) vs 30 of 52 (0.58). The fitted weight is pulling back an affinity bucket that already over-scores those rows.

| slice | n (yes) | baseline AUC | lab_any lift, CI95 |
|---|---|---|---|
| repeat users (aff_se > 0) | 89 (55) | 0.9543 | +0.0190 [-0.0039, +0.0511], w = -2.81 |
| affinity-silent (aff_se = 0) | 469 (82) | 0.9264 | not measurable: the lab signal is 0 on every row |
| date-matched (yes 2020+) | 459 (38) | 0.9359 | +0.0026 [-0.0008, +0.0068] |

Replacing affinity rather than adding to it, with each term fitted LOO on staff + LLM: `aff_se` +0.0029 [-0.0028, +0.0121], `lab_rate` -0.0006 [-0.0018, +0.0004], `lab_any` +0.0001 [-0.0008, +0.0010]. The lab signal is no better a stand-in for affinity than affinity itself.

Precision where each signal fires (Wilson CI95):

| signal | fires | yes / no | precision |
|---|---|---|---|
| aff_se | 89 | 55 / 34 | 0.618 [0.514, 0.712] |
| lab_any | 37 | 25 / 12 | 0.676 [0.515, 0.804] |
| lab_win_any | 6 | 3 / 3 | 0.500 [0.188, 0.812] |
| sen_lab_any | 17 | 10 / 7 | 0.588 [0.360, 0.784] |

### Core 14 claimed vs rejected (n = 46: 26 / 20)

This panel is selection-biased, and the AUCs here cannot be read as ranking quality. The claimed rows have a mean LLM score of 1.46: these are papers humans added that the engine scored low. The rejected rows average 7.35: the engine surfaced them and humans rejected them. As a result the baseline AUC is **0.2308**, inverted, and fitted lifts here mostly measure how well a feature undoes that selection. Precision is the readable number:

| core set | aff_se | lab_any | lab_win_any | sen_lab_any |
|---|---|---|---|---|
| all | 17/34 = 0.500 [0.341, 0.659] | 9/21 = 0.429 [0.245, 0.635] | 4/6 = 0.667 [0.300, 0.903] | 4/11 = 0.364 [0.152, 0.646] |
| strict | 17/31 = 0.548 [0.378, 0.708] | 5/17 = 0.294 [0.133, 0.531] | 2/4 = 0.500 [0.150, 0.850] | 1/8 = 0.125 [0.022, 0.471] |

The lab signal fires disproportionately on rejected rows. The year window fires on 4 to 6 rows, which is too few to read.

## Why it cannot work as specified

`lab_n(h) <= author_core(h)`, and h is on the byline. So wherever the lab signal is non-zero, the author signal is non-zero too, from the same papers. The lab definition narrows the count to the subset of h's core papers that h led, and narrows the denominator to the papers h led. The ratio moves, but the evidence is the same. That is why it brings no new rows and no independence from the self-confirmation loop: a lab's earlier confirmations are the head's earlier confirmations.

## What would reopen it

- A lab definition that does **not** put the head on the scored byline. One example is the mentor of a trainee first author, when the mentor is absent from the paper (reciterdb has no mentor table; SPS FRT mentee data might). Only that form could reach the affinity-silent rows.
- A labelled panel with hard negatives inside heavy-user labs: papers by a repeat-user lab that did not use the core. Panel B has 34 repeat-user random negatives, and core 14's rejected rows are selection-biased.

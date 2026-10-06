# Cores: TF-IDF text similarity as a signal (experiment, 2026-10-06)

## Verdict

Not wired. Neither variant earns a `combine.WEIGHTS` key, not even one held at 0.00.

- **Within-author** (the variant meant to rank one repeat user's papers) shows no incremental lift over the current score on any panel where lift is measurable. Panel B also cannot test the question it was built for: only **1** yes/no pair shares a top-affinity author.
- **Core-wide** adds a small lift over the full staff + affinity + LLM score on the hard panel: AUC 0.9377 to 0.9534, +0.0157, CI95 [+0.0066, +0.0261]. The lift turns negative inside the repeat-user subset: -0.0053, CI95 [-0.0118, -0.0008]. It is also confounded with what the LLM already reads (see "Why the core-wide lift is not core-usage evidence" below).

Script: `scripts/experiment_tfidf_similarity.py`. Every number below comes from one run of it:

```
python3 scripts/experiment_tfidf_similarity.py --out <rows.json> --llm-cache <llm_cache.json>
```

That run was then re-reported with `--from-json <rows.json>`, which gives byte-identical output.

## What was measured

**Core papers.** The core papers are the imaging core's (core 2) signal-2 confirms (`confirmed_pmids_by_core.json`, 68) unioned with its DynamoDB confirmed/claimed rows (69: 68 confirmed and 1 claimed, 0 rejected), gated to the corpus. That gives 69 papers, and 12 of them are in the label set. With no `rejected` rows there is nothing to contrast against, so "an author's other corpus papers" serves as the contrast set.

**Text and vectors.** Each paper is its title plus abstract. TF-IDF is a minimal pure-Python implementation (sublinear tf, smoothed idf, min df 2, L2-normalised), because scikit-learn is not a repo dependency. The vectors cover 35,994 documents: every corpus paper by the 1,846 byline authors on the panel, the core papers, and a random corpus sample of 3,000.

**Leave-one-out everywhere.** Each centroid has the candidate paper subtracted before the cosine, so a paper never scores against itself.

**Features:**
- `within`: computed over byline authors who have at least one core paper other than this one. For each such author, it is cos(paper, centroid of that author's core papers) minus cos(paper, centroid of that author's other corpus papers). The feature takes the MAX over those authors, the same MAX-over-byline rule as `author_affinity`.
- `within_core_only`: the same feature without the contrast term.
- `core_wide`: cos(paper, centroid of the core papers) minus cos(paper, centroid of the corpus sample).

**Lift.** Lift is measured in the deployment form, `logit + w*feature`. The baseline's coefficient is fixed at 1, `w` and a free intercept are fitted with that paper held out, and the intercept is then dropped. The fitted intercept absorbs panel prevalence. Dropping it keeps the per-fold intercept from breaking ties in the baseline against the held-out label. An earlier version re-fitted the baseline per fold, and that alone pulled a tie-heavy baseline from 0.8225 to 0.6552 and created a fake +0.27 "lift". The CIs come from a 2,000-rep paired bootstrap on the AUC difference.

**Panels:**
1. **Panel B as labelled.** 237 papers: 137 yes and 100 no. The baseline is the full `combine()` logit: staff, affinity (index built from confirms outside the label set, exactly as `fit_evidence_weights.panel_b`), the stored LLM score, and `ack` from cached full text (211 of 237 papers have it).
2. **Panel B hard.** The 137 labelled yes papers against the fit's own random-corpus draw (`random.seed(11)`, 1,200 papers, 1,199 left after removing core and labelled papers). The baseline is byline-only: staff plus affinity.
3. **Panel B hard + LLM.** The same 137 yes papers against the 421 random negatives that carry a live two-pass LLM score. That set is the fit's 400-paper LLM subset rule plus all 34 repeat-user negatives. The baseline is staff + affinity + LLM. `ack` is left out because the random side has no cached full text.

## Results

### Panel B as labelled: saturated, cannot show lift

The baseline AUC is **0.9989**, and the LLM alone scores 0.9325. The repeat-user subset (affinity > 0) has 91 papers, of which 88 are yes and only **3** are no. With no headroom and no negatives, every lift is between 0.0000 and +0.0004 (for example, core_wide 0.9989 to 0.9993, CI [+0.0000, +0.0014]).

| feature | marginal AUC | over the LLM alone (rho with LLM) |
|---|---|---|
| core_wide | 0.9007 | 0.9325 to 0.8956, -0.0369, CI [-0.0791, +0.0031] (rho 0.703) |
| within (defined on 95) | 0.6775 | 0.8533 to 0.8514, -0.0018 (rho 0.151) |
| within_core_only (defined on 95) | 0.8261 | 0.8533 to 0.8370, -0.0163 (rho 0.329) |

### Panel B hard (byline-only baseline)

The baseline AUC is 0.8225 overall and 0.9402 in the repeat-user subset (n=122: 88 yes, 34 random).

| feature | slice | marginal AUC | baseline to +feature | lift, CI95 | w |
|---|---|---|---|---|---|
| core_wide | all 1,336 | 0.8956 | 0.8225 to 0.9230 | +0.1005 [+0.0673, +0.1338] | 30.11 |
| core_wide | repeat users | 0.8596 | 0.9402 to 0.9582 | +0.0180 [+0.0014, +0.0365] | 18.04 |
| within | where defined (133) | 0.7998 | 0.9327 to 0.9091 | -0.0236 [-0.0401, -0.0105] | -0.85 |
| within | repeat users | 0.8112 | 0.9402 to 0.9465 | +0.0064 [-0.0127, +0.0250] | 2.01 |
| within_core_only | repeat users | 0.7503 | 0.9402 to 0.9552 | +0.0150 [+0.0009, +0.0320] | 4.42 |

Same-top-author pairs, the direct test of "ranks within one repeat user's papers": **1 pair from 1 author**. That is not measurable.

### Panel B hard + LLM (the like-for-like test against the current score)

The baseline AUC is 0.9377 overall, with the LLM alone at 0.9240. In the repeat-user subset (n=122) the baseline AUC is 0.9759.

| feature | slice | baseline to +feature | lift, CI95 | over the LLM alone (rho) |
|---|---|---|---|---|
| core_wide | all 558 | 0.9377 to 0.9534 | **+0.0157 [+0.0066, +0.0261]** | +0.0199 [+0.0029, +0.0362] (rho 0.577) |
| core_wide | repeat users | 0.9759 to 0.9706 | **-0.0053 [-0.0118, -0.0008]** | +0.0020 (rho 0.559) |
| within | where defined (126) | 0.9546 to 0.9514 | -0.0032 [-0.0128, +0.0046] | +0.0082 (rho 0.425) |
| within | repeat users | 0.9759 to 0.9729 | -0.0030 [-0.0077, +0.0000] | +0.0157 [-0.0072, +0.0399] (rho 0.403) |
| within_core_only | repeat users | 0.9759 to 0.9756 | -0.0003 [-0.0040, +0.0034] | -0.0040 (rho 0.486) |

## Why the core-wide lift is not core-usage evidence

1. **It measures imaging topicality, which the LLM already measures.** The LLM reads the same title and abstract (rho 0.58 to 0.70 with it). This is the same "two systems agreeing about one text" objection `combine.py` raises for `method:*`. The 2026-06 head-to-head found that text cannot tell a WCM-core MRI from an outside-scanner MRI. Neither panel contains that hard negative. Panel B's "no" papers are easy non-imaging papers, and the random corpus is mostly off-topic, so a topic centroid separates both well without saying anything about which scanner was used.
2. **The panel was sourced by a PMC search for the core's name.** The yes papers are the ones that acknowledge the core. Their topical coherence with 69 mostly staff-co-authored core papers says nothing about the silent majority the queue exists to find.
3. **It does not help where the queue needs help.** In the repeat-user subset the lift is negative with a CI that excludes 0, which is the opposite of the stated aim.
4. **A positive lift over a byline-only baseline (+0.10) just restates that the LLM is missing from that baseline.** Most of it disappears once the LLM is in (+0.016).

## Why the within-author variant fails

The variant needs a repeat user with both used-the-core and did-not papers that are labelled. Panel B gives one such pair. Among the 34 random repeat-user negatives, the feature reverses sign between slices (w from -0.85 to +2.01 to -1.96) and never clears 0 against the full baseline. Some of those "random" negatives are probably unlabelled positives, because a high-affinity author's random paper has a far higher chance than 2% of having used the core. That contamination biases any within-author signal toward zero. It cannot be fixed by re-running this, only by labelling.

## What would reopen this

- A labelled within-author panel: about 30 or more repeat users, each with at least 2 yes and 2 no papers, judged by the core owner. That is the only design that tests the within-author variant directly.
- Hard topical negatives for core-wide: imaging papers that did not use the core (outside scanner), so topicality and usage come apart. Without them, any text-similarity weight is a second copy of the LLM.
- The `client` / `aff:*` overlap rule in `combine.py` applies here too. Measure the overlap with `llm` before pricing anything.

## Caveats

- The IDF is fitted on all 35,994 documents, including the candidate itself. The centroids are leave-one-out, but the IDF is not. The effect of one document in about 36k is negligible.
- One core (imaging) and one label set.
- The live LLM scores are a fresh Bedrock run, not the scores the shipped slope was fitted on, so they may differ from what the fit saw. This run made Bedrock reads only, and nothing was persisted to any shared store.

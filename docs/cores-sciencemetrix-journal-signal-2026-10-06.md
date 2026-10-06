# Science-Metrix journal subfield as a core-usage signal (experiment, 2026-10-06)

Verdict: not wired. The journal subfield carries marginal signal, but on the panels where
the current score is complete it adds nothing measurable, and among repeat users it makes
the ranking worse. No key was added to `combine.WEIGHTS`. A `sm:*` key would fail the
doctrine either way: there is no fit worth shipping, and a 0.00 key with nothing behind
it is dead weight.

Script: `scripts/experiment_sciencemetrix_lift.py`. It is read-only: SELECTs against
reciterdb, a Scan and a GetItem against DynamoDB, and no Bedrock calls.

## Data

- **Journal to subfield:** `reciterdb.journal_science_metrix` (19,866 journals), joined on
  `analysis_summary_article.issn` = `issn`, with `eissn` as the fallback. It covers 81,155 of
  the 82,173 corpus pmids (98.8%). The same data is in the ReCiter repo
  (`src/main/resources/files/ScienceMetrix.json`).
- **Core profile:** the imaging core's (core 2) confirmed and claimed `CORE#` rows in
  DynamoDB. These are the rows production's affinity prior reads: 69 rows, all in the
  corpus. The top subfields are General Science & Technology (15 papers; core share 21.7%
  vs 8.78% of the corpus), Neurology & Neurosurgery (13), Orthopedics (12) and Nuclear
  Medicine & Medical Imaging (12; 17.4% vs 3.28%, shrunk lift 4.76).
- **Feature:** `log(lift)` of the paper's subfield. Lift is the core share over the corpus
  share, shrunk toward 1 with a pseudo-count of 10. A paper inside the profile is scored
  **leave-one-out**. The `--profile-excludes-labels` run instead drops all 12 labelled
  papers from the profile up front.
- **Candidate key:** `sm:enriched` means lift >= 2.0 (the edge was set before looking at
  the panel). It is priced by weight of evidence (WoE) with the same estimator as
  `fit_evidence_weights.weight()`. Each row's weight is fitted leave-one-out.
- **Continuous variant:** `base + beta * log-lift`, where beta comes from offset logistic
  regression fitted leave-one-out. The intervals are a paired bootstrap (1,000 resamples)
  on the LOO scores. The bootstrap does not refit beta, so the intervals are somewhat
  narrow.

## Panels

The panels are built the way `fit_evidence_weights.panel_b` builds them: `labeled_set.csv`
gives 137 yes and 100 no for the imaging core, and there are 1,200 random corpus negatives
(seed 11). The base score is `combine.score()` over the evidence that panel B can
reproduce: staff co-authorship, curated clients, and the affinity rate. The affinity rate
is built as in production since #418, so a core's own staff lend it no affinity. No panel
includes the acknowledgement signal.

- **P1:** yes vs random corpus. Base is staff + affinity. The LLM is left out on both sides
  because the negatives were never triaged.
- **P1c:** P1 with the positives restricted to the corpus. This control is needed because
  100 of the 137 yes papers are from before 2020 and every negative is a 2020+ corpus paper,
  so P1 also measures era.
- **P2:** yes vs labelled no. Base is staff + affinity + the stored LLM score. It is the
  only panel with the full score, but its negatives are easy ones.

## Results

Command: `python3 scripts/experiment_sciencemetrix_lift.py`. The LOO profile is the
primary result; the label-excluded profile is in brackets.

| panel | n (pos/neg) | marginal AUC | base AUC | + beta*log-lift (delta, 95% boot) | + sm:enriched (delta, 95% boot) |
|---|---|---|---|---|---|
| P1 | 137/1200 | 0.6885 [0.6989] | 0.8294 | +0.0059 [-0.0340, +0.0426] ([+0.0090]) | +0.0139 [-0.0039, +0.0329] ([+0.0249, CI +0.0032..+0.0471]) |
| P1 repeat users | 54/37 | 0.5636 [0.5793] | 0.8966 | **-0.0343** [-0.0616, -0.0099] ([-0.0258]) | -0.0193 [-0.0437, -0.0003] ([-0.0110]) |
| P1c date-matched | 37/1200 | 0.7381 [0.7527] | 0.8579 | +0.0597 [+0.0050, +0.1190] ([+0.0615]) | +0.0407 [+0.0068, +0.0769] ([+0.0467]) |
| P1c repeat users | 22/37 | 0.5393 [0.5749] | 0.8483 | -0.0276 [-0.0670, +0.0094] ([-0.0276]) | -0.0172 [-0.0520, +0.0176] ([-0.0025]) |
| P2 (with LLM) | 137/100 | 0.7013 [0.7106] | 0.9532 | +0.0097 [-0.0069, +0.0275] ([+0.0063]) | +0.0074 [-0.0071, +0.0244] ([+0.0126]) |
| P2 repeat users | 54/3 | too few negatives to measure | | | |

How often `sm:enriched` fired (LOO profile):

| panel | positives | negatives | full-panel WoE |
|---|---|---|---|
| P1 | 84/137 | 345/1200 | +0.76 |
| P1 repeat users | 34/54 | 20/37 | +0.15 |
| P1c | 28/37 | 345/1200 | +0.96 |
| P2 | 84/137 | 26/100 | +0.85 |

## Reading it

1. **There is marginal signal.** AUC alone is about 0.69–0.75 on every full panel. That is
   unlike `mesh:tree`, which bought nothing (0.6610 vs 0.6612 in the SPS measurement). The
   journal does say something about which core a paper used.
2. **Most of it is already in the score.** The only panel with a confidence interval clear
   of zero is P1c: 37 positives, no LLM in the base, and a lower bound of +0.005. With the
   LLM in the base (P2) the gain is +0.006 to +0.013, and every interval crosses zero. An
   LLM reading the title and abstract already knows the paper is about imaging.
3. **It hurts where the score makes decisions.** In the repeat-user subset (affinity > 0),
   the papers whose confirmation actually depends on the prior, the marginal AUC drops to
   0.54–0.58. Adding the feature lowers the AUC by 0.026–0.034, and in P1 the interval
   excludes zero. A plausible reading: a frequent user's off-subfield paper is still a
   core paper, and the journal penalises it.
4. **The era confound is real.** The first run scored the 100 pre-2020 positives as "no
   subfield" (log-lift 0) against corpus negatives that almost all have one. That
   manufactured a +0.067 delta on P1 that was pure missingness. The script now resolves
   subfields for out-of-corpus labelled papers. Any future fit on panel B needs the same
   care: the "yes" side is mostly pre-2020 and the negatives are not.
5. **The profile is small.** One core has 69 profile papers, and Biotechnology's 4 papers
   become a shrunk lift of 13.3. Per-core profiles for the other cores are smaller still:
   core 11 has 2 rows and core 12 has 6.

## What would change the verdict

- A panel with LLM scores on the random negatives. The decisive comparison is P1 with the
  full score, and it costs about 1,200 Bedrock calls.
- A labelled panel for a second core, ideally one whose usage is less topical than
  imaging, before the subfield could be argued to transfer.
- If the feature is ever revisited, gate it to affinity == 0. That is the only place it
  helps, and keeping it there would also keep it out of the repeat-user decisions it
  degrades.

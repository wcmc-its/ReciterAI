# Translational stage for publications: pilot (2026-11-16)

Nothing here is built. This records what a pilot found, so the decision to build can start from numbers.

## Question

Can each publication be labelled with a stage on the translational research spectrum well enough to offer as a search filter? Is the one-line synopsis enough input, or is the abstract needed? Which model, and at what cost?

## Answer

- **Four stages hold up; five do not.** Two models disagree too often on T3 against T4. With those two merged, Opus and Sonnet agree on 84–87% of papers.
- **The synopsis is enough.** Sonnet classifying from the synopsis alone matches Sonnet classifying from the abstract on 87.7% of papers with five stages and 90.0% with four.
- **Sonnet, not Haiku.** Haiku agrees with the other models on only 61–68% and over-assigns T0 and NA.
- **The stage is free if it rides the synopsis call.** Adding it to the synopsis prompt did not change the cost of that call.
- **No human has checked the labels.** Agreement between models stands in for a hand-check. It shows the labels are reproducible, not that they are right.

## Stage definitions used

- T0, discovery and basic research: lab work using cells, molecules or animal models.
- T1, early translation: translating lab models into initial human safety, proof of concept and early-phase clinical studies.
- T2, clinical efficacy and trials: testing interventions in larger patient groups to prove effectiveness.
- T3, practice integration: moving approved interventions into standard clinical practice.
- T4, population and public health impact: real-world outcomes, large-scale epidemiology, community health.
- NA: not on the spectrum (methods or software with no biomedical finding, education, case reports, policy, history).

The recommended scheme merges T3 and T4 into one stage, "practice and population health".

## Sample

300 Academic Articles from 2021 onward that iCite marks as research articles, each with an abstract and an existing synopsis, drawn with a fixed seed and stratified on the iCite triangle:

- 80 laboratory: animal plus molecular/cellular share at least 0.5, not flagged clinical.
- 80 flagged clinical by iCite (`is_clinical`).
- 140 with human share at least 0.5, not flagged clinical. This is where T2, T3 and T4 have to be told apart.

A first draw took any article in reciterdb and found that only about 1 in 9 had a synopsis, because enrichment covers papers with a full-time-faculty author. The sample above draws only from papers that have one.

## Results

Stage counts, Sonnet from the abstract:

| Stratum | T0 | T1 | T2 | T3 | T4 | NA | n |
|---|---|---|---|---|---|---|---|
| Laboratory | 57 | 14 | 0 | 3 | 2 | 4 | 80 |
| iCite clinical | 0 | 15 | 39 | 12 | 13 | 1 | 80 |
| Human, not clinical | 7 | 15 | 9 | 33 | 58 | 18 | 140 |
| All | 64 | 44 | 48 | 48 | 73 | 23 | 300 |

The iCite strata behave as expected: 71 of 80 laboratory papers land on T0 or T1, and no clinical-flagged paper lands on T0.

Agreement between raters:

| Comparison | Five stages + NA | T3 and T4 merged |
|---|---|---|
| Sonnet abstract vs. Sonnet synopsis | 87.7% | 90.0% |
| Opus abstract vs. Sonnet abstract | 76.6% | 87.3% |
| Opus abstract vs. Sonnet synopsis | 73.9% | 83.9% |
| Opus abstract vs. Haiku abstract | 61.2% | not computed |
| Sonnet abstract vs. Haiku abstract | 67.3% | not computed |

One Opus call errored, so its comparisons have 299 papers.

Where Opus and Sonnet (synopsis) differ, rows Opus, columns Sonnet:

| | T0 | T1 | T2 | T3 | T4 | NA |
|---|---|---|---|---|---|---|
| T0 | 56 | 7 | 0 | 0 | 0 | 3 |
| T1 | 8 | 25 | 1 | 1 | 4 | 2 |
| T2 | 0 | 6 | 41 | 3 | 1 | 0 |
| T3 | 0 | 3 | 5 | 46 | 30 | 3 |
| T4 | 0 | 0 | 0 | 0 | 37 | 0 |
| NA | 0 | 0 | 0 | 0 | 1 | 16 |

The 30 papers Opus calls T3 and Sonnet calls T4 are most of the five-stage gap. Opus assigns T3 to 87 papers and T4 to 37; Sonnet assigns about 50 and 73. The models read the line between "into practice" and "population impact" differently, and the definitions do not settle it.

Other observations:

- Sonnet rated its own confidence high on 94 of 300 from the abstract, medium on 177 and low on 29.
- NA is small: 23 of 300.
- Re-running Sonnet on 100 other papers gave the same four-stage label for 94–95 of them.

## Cost

Per paper, at `config/llm_prices.yaml` prices:

| Call | Input / output tokens | Cost |
|---|---|---|
| Sonnet, stage from the abstract | 1,057 / 91 | $0.0030 |
| Sonnet, stage from the synopsis | 448 / 92 | $0.0018 |
| Opus, stage from the abstract (effort high) | 1,046 / 203 | $0.0082 |
| Haiku, stage from the abstract | 1,055 / 132 | $0.0002 |
| Sonnet, synopsis and stage in one call | measured before the synopsis length fix; re-measure | — |

Corpus sizes in reciterdb on 2026-11-16: 287,274 Academic Articles; 268,628 with an abstract over 300 characters, of which iCite marks 267,810 as research articles; 80,041 of those from 2020 onward.

## If this is built

- Add `stage` and `stage_confidence` to the synopsis call's output and store them on the `IMPACT#` row beside `synopsis`. A missing or invalid stage should not fail the paper.
- Leave NA and low-confidence papers unlabelled downstream.
- A wider corpus (every research article from 2020, whatever the authors' rank) needs a synopsis-only path, since impact scoring is a separate and larger call.
- Clinical trials need no model: ClinicalTrials.gov phase and study type map onto the stages directly. Awarded grants would need their own pass over title and abstract.

## Limits

- No reviewer with domain knowledge has scored the sheet.
- The sample is 2021 onward and only papers that already had a synopsis.
- Reviews, case reports, preprints and other non-research types were excluded and not tested.
- The four-stage agreement was computed by merging labels after the fact. A prompt that offers only four stages was run on 100 papers for cost and stability, not for agreement with Opus.

## Method

Ad hoc scripts, not committed. Papers came from `analysis_nih` joined to `analysis_summary_article` and `reporting_abstracts`; synopses from `fetch_synopses_for_pmids`. Every call went through `call_with_fallback` and returned stage, confidence and a one-sentence reason as JSON. Nothing was written to DynamoDB or MariaDB.

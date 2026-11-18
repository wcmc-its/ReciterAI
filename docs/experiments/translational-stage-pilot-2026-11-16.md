# Translational stage for publications: pilot (2026-11-16)

This records what a pilot found. Since then the synopsis call has been changed to return the stage and store it on the `IMPACT#` row (see "Built so far" below); nothing downstream reads it yet.

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
| Sonnet, synopsis and stage in one call (shipped prompt) | 1,084 / 64 | $0.0028 |

Corpus sizes in reciterdb on 2026-11-16: 287,274 Academic Articles; 268,628 with an abstract over 300 characters, of which iCite marks 267,810 as research articles; 80,041 of those from 2020 onward.

## Built so far

- The publication synopsis prompt asks for the stage in the same call. On the 100-paper probe set that was 100 calls for 100 papers, no synopsis over 120 characters, a valid stage on every paper, and the same stage as the pilot run for 95.
- `translational_stage` (`T0`, `T1`, `T2`, `T3-4`, `NA`) and `translational_stage_confidence` are written to the `IMPACT#` row when present, and omitted when the model returns no valid label.
- Only papers enriched after this change get a stage. Existing rows, MariaDB, the `TOPIC#` rows and Scholars are untouched.

## Still to build

- Label the papers that already have a synopsis (from the synopsis alone).
- Leave NA and low-confidence papers unlabelled downstream.
- Run the wide backfill below with `--apply`; it is committed but has only been dry-run.
- Clinical trials need no model: ClinicalTrials.gov phase and study type map onto the stages directly. Awarded grants would need their own pass over title and abstract.

## Wide backfill (stage-only rows)

`scripts/backfill_synopsis_stage_wide.py` gives a synopsis and a stage to research articles the daily job never reaches, because the daily job only enriches papers with a full-time-faculty author.

- Work set: Academic Articles from 2020 onward that iCite marks as a research article (`analysis_nih.is_research_article = 'True'`) and that have an abstract of at least 50 characters once whitespace is collapsed, minus every pmid that already has an `IMPACT#` row, minus every `INVALID#` pmid. The length floor drops values that are not abstracts (a lone carriage return, "Not available.", "[Figure: see text].", one-symbol stubs); those papers get no model call and no row, since a stage read from the title alone would be mislabelled `translational_stage_source = abstract`. On 2026-11-18 that was 78,466 in scope, 9,732 with a row already, 957 invalid, 67,777 remaining.
- Each paper goes through `generate_synopsis`, the same call and prompt the daily job uses. A 20-paper dry run cost $0.0027 per paper, with a valid stage on all 20 and no synopsis over 120 characters.
- Each write is a PutItem conditional on `attribute_not_exists(PK)`, so it cannot overwrite a row the daily job wrote. If one of these papers later gains a faculty author, the daily job's unconditional write replaces the stage-only row with a full one.
- A paper whose call fails, or that gets no valid stage, gets no row and is retried by the next run. `--max-usd` (default 25) stops the run once measured spend reaches it; re-running continues from where it stopped.
- Each result is written to the table and the CSV as its own call finishes, so a slow call does not hold back finished ones. A failed chunk fetch, five failed writes in a row, or Ctrl-C stops the run: calls in flight are collected and written, the summary is printed, and the exit code is 1.

The row is not a normal `IMPACT#` row:

| Attribute | Value |
|---|---|
| `stage_synopsis` | the synopsis text |
| `stage_synopsis_model` | model that produced it |
| `translational_stage`, `translational_stage_confidence` | as on a daily-job row, omitted when not valid |
| `translational_stage_source` | `abstract` |
| `enrichment_scope` | `stage_only` |
| `enriched_at` | ISO timestamp |

There is no `synopsis` and no `impact_score` attribute, on purpose. Several readers take "the `IMPACT#` row has a `synopsis`" to mean "this paper is enriched":

- The weekly hot-path eligibility sweep (`scan_impact_pmids_with_synopsis`, used by `pipeline_hot/orchestrator.resolve_eligibility_sweep`) would send every such paper to topic scoring.
- `scan_faculty_publication_gaps` (through `fetch_synopses_for_pmids`) would report the paper as covered, so the nightly backstop would never give it an impact score.

Storing the text under `stage_synopsis` keeps these papers invisible to both. Bringing them into topic scoring later is a deliberate step: rename `stage_synopsis` to `synopsis` for the papers chosen, and decide first whether the topic-scoring and impact-scoring cost for that many papers is wanted. A reader that wants a stage for any paper can read `translational_stage` from either kind of row; one that wants to tell them apart reads `enrichment_scope`.

## Limits

- No reviewer with domain knowledge has scored the sheet.
- The sample is 2021 onward and only papers that already had a synopsis.
- Reviews, case reports, preprints and other non-research types were excluded and not tested.
- The four-stage agreement was computed by merging labels after the fact. A prompt that offers only four stages was run on 100 papers for cost and stability, not for agreement with Opus.

## Method

Ad hoc scripts, not committed. Papers came from `analysis_nih` joined to `analysis_summary_article` and `reporting_abstracts`; synopses from `fetch_synopses_for_pmids`. Every call went through `call_with_fallback` and returned stage, confidence and a one-sentence reason as JSON. Nothing was written to DynamoDB or MariaDB.

# Core usage: GEO deposit metadata as a signal (2026-10-06)

## Question

Does data-repository deposit metadata (GEO series/sample text) predict that a paper used a WCM sequencing core, with lift over a baseline that already includes the LLM score and author affinity, and ideally among repeat-user rows? The goal is a signal independent of author affinity, which was found to be a self-confirmation loop.

Verdict: the GEO signal is dead as a scoring feature. A by-product of the adversarial check, WCM first/last-author affiliation from PubMed, is promising but needs a fit before any weight.

## Data and coverage

- Universe: every `PUB#/CORE#` row in DynamoDB `reciterai` (21,922 rows, 13,588 PMIDs), scanned read-only (`s01`).
- Cores 1/3/5/9 rows are the 2026-06-22 legacy pass. Confirmed means a staff co-author. Candidates carry `screen_confidence` (the Sonnet title screen, used here as the LLM score) and a stored `author_affinity`. None of these cores has a human claimed or rejected decision. Core 14 has the only ones (26/20), and core 2 has 1.
- PubMed to GEO (`pubmed_gds`, `s02`): 911/13,588 PMIDs link. By core: core 5 has 37/74 confirmed and 592/4,334 candidates linked; core 3 has 21/46 and 512/2,841; core 1 has 25/51 and 451/2,497; core 9 has 8/31 and 247/1,617; core 2 (imaging) has 1/68 and 37/4,049.
- GEO text for 1,684 GSEs linked to core 1/3/5 papers, series plus first sample (`s03`).
- Label = the paper's own body and back-matter text names the core (`s04`/`s05`, Europe PMC full text with PMC efetch fallback; front matter, affiliations and references are stripped). 586/629 core 5 and 490/533 core 3 GEO-linked rows have full text. In a sample read, 15/16 label positives were correct. The one miss was "UCLA Sequencing Core" sitting near "WCM".

## Results

### GEO text names the WCM core (`g_core`)

| core | fires | label positive (paper names core, or staff) | rest of panel |
|---|---|---|---|
| 5 Genomics Resources | 14/586 | 12/14 = 0.86 [0.60, 0.96] | 102/572 = 0.18 |
| 3 Epigenomics | 13/490 | 13/13 = 1.00 [0.77, 1.00] | 57/477 = 0.12 |
| 1 Applied Bioinformatics | 0/438 | n/a | n/a |

The precision is real, but the signal fires on about 2.5% of GEO-linked papers, which is about 0.3% of candidates. Papers it finds that neither the paper text nor staff evidence already finds: 2 for core 5 and 0 for core 3. It fired on 0 of the 43 GEO-linked papers that have no full text, which is the one place it could have added something. The leave-one-out (LOO) AUC lift over LLM + affinity + authorship is +0.014 [+0.004, +0.025] for core 5 and +0.023 [+0.008, +0.042] for core 3. Most of that lift is tautological: the depositor copies the same "sequenced at the X Core" sentence into GEO and into the paper.

### GEO contact institute is WCM (`g_home`)

Alone, `g_home` lifts LLM + affinity by +0.148 [+0.099, +0.200] for core 5. That lift disappears once PubMed WCM first/last-author affiliation is in the baseline: +0.002 [-0.009, +0.013] for core 5 and -0.006 [-0.010, -0.003] for core 3. It is a proxy for "a WCM lab led this paper".

### GEO protocol names an outside provider (`g_external`)

`g_external` (Novogene, GENEWIZ, NYGC, IGO and similar) adds +0.001 for core 5 and +0.003 for core 3. Its label rate is 9/83 against 109/503, which is directionally right but adds nothing.

### By-product: WCM first/last author from PubMed affiliations

These three features (WCM first author, WCM last author, and the WCM share of the byline) use no confirmation history, so they are independent of affinity and cover every paper.

| panel | baseline | baseline AUC | with authorship | dAUC 95% CI (bootstrap) | repeat-user rows |
|---|---|---|---|---|---|
| core 5 GEO-linked candidates (n=552, 81 pos) | LLM + aff | 0.679 | 0.879 | [+0.143, +0.258] | 0.620 to 0.853 (n=457) |
| core 3 GEO-linked candidates (n=469, 51 pos) | LLM + aff | 0.694 | 0.820 | [+0.064, +0.194] | 0.619 to 0.779 (n=369) |
| panel B imaging, human labels (n=237, 137 yes) | LLM dense score | 0.890 | 0.926 | [+0.016, +0.061] | n/a |

On panel B, a WCM first author has a yes rate of 85/118, against 52/119 otherwise. A WCM last author has 53/79, against 84/158 otherwise.

## Caveats

- Affinity is the stored `author_affinity` on candidate rows. A candidate is not confirmed, so its own paper is not in its own numerator. A true leave-one-out recompute was not done, because the reciterdb read (bylines and totals) was not permitted in this session. Panel B has no affinity term at all.
- The core 3/5 labels are paper-text naming, not human decisions. "A WCM-led paper names a WCM core" could partly reflect acknowledgement habits rather than usage. Panel B (human labels) is the cleaner check, and it shows a smaller but real lift.
- The core 3/5 panels contain only GEO-linked sequencing papers.

## Not wired

No WEIGHTS key is added. The GEO features show no meaningful lift. The authorship features need a proper fit in `scripts/fit_evidence_weights.py` across all candidates, not just GEO-linked ones, and their overlap with `inst:home` and `prefilter_prior` has to be measured first.

## Reproduce

Run `scripts/experiments/geo_deposit_signal/s01` through `s10` in order from an empty scratch directory. All steps are read-only against DynamoDB, NCBI E-utilities, GEO and Europe PMC.

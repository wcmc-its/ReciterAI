# Cores: instrument model named in full text as a signal (experiment, 2026-10-06)

## Verdict

Not wired, and no `combine.WEIGHTS` key, not even one held at 0.00.

- **It adds no measurable lift over a baseline that includes the LLM.** On panel B (core 2, the only gold panel for an instrument core), the any-model feature fires on 20 yes and 1 no. Leave-one-out lift is +0.0000 over the full `combine()` logit (AUC 0.9989, saturated), +0.0031 CI95 [-0.0050, +0.0135] over staff + affinity + LLM, and -0.0000 CI95 [-0.0151, +0.0123] over the LLM alone. The repeat-user subset has 88 yes and only 3 no, so lift cannot be estimated there.
- **Most specific models measure the modality, not the core.** The outcome used here is the core's own alias named beside a home institution, which is a usage proxy that does not depend on the instrument string. For 17 of 22 specific models, that rate is no higher than for a generic instrument of the same modality. For the proteomics and microscopy models it is lower than for the generic control.
- **A few models do concentrate home-alias mentions.** These are Inveon (core 2), BD Influx and Sony MA900 (core 4), and Promethion and EchoMRI (core 7, no control). None survives a multiple-comparison correction except possibly Influx. Influx and MA900 also show competing ownership: where a flow-core alias appears beside an Influx mention, 18 are home and 9 are other, and 7 snippets name another institution's facility.
- **Incremental precision is unknown, not low.** 2,113 of 2,324 fired (paper, core) pairs carry no existing evidence. In a 30-paper sample read of the strongest strings, none named WCM or the core beside the instrument, 3 named another institution's facility (NYU, Salk/UCSD, Salk), 5 were a different instrument (the Oxford Nanopore PromethION, now excluded by a case-sensitive regex), and 22 did not say where the work was done.

So: **dead as a general signal; promising-needs-labels for a five-string shortlist** (see "What would reopen this").

Script: `scripts/experiment_instrument_models.py`. Every number below comes from one run of it:

```
python3 scripts/experiment_instrument_models.py --rows <core_rows.json> --cache <dir> --out <inst.json>
```

`<core_rows.json>` is a read-only Scan of every `CORE#` row in the `reciterai` table (21,922 rows).

## Data and coverage

- **Instrument list.** 40 (core, model) strings covering cores 1, 2, 4, 5, 7, 9, 10, 11, 12 and 13. They come from each core's public pages (cbic.weill.cornell.edu facility pages, research.weill.cornell.edu core pages, mpc.weill.cornell.edu), fetched 2026-10-06, and from the dictionary's `llm_description`. Six are generic controls, one per modality: IVIS Spectrum, LSRFortessa, Q Exactive, LSM 880, NovaSeq 6000 and Seahorse-class. Each string has a PMC phrase and a full-text regex. A paper fires only when the regex matches the cached PMC XML, which drops tokenised esearch false hits.
- **Firing set.** Each phrase is searched in PMC with `AND ("Weill Cornell" OR "Weill Medical College")`, mapped to PMIDs, and gated to the scoreable corpus (Academic Article, 2020+). For phrases with 2,500 or fewer global hits, the unrestricted set is also pulled and unioned in.
- **Coverage caveat.** The WCM clause found only **331 of 883** (37%) of the corpus papers that the unrestricted search found. Firing sets for the large phrases (Inveon, Cytek Aurora, EchoMRI, Promethion, MR750, the controls) are therefore a WCM-mention-biased subsample. Precision ratios are less affected than counts.
- **Labels.** Labels outside core 14 are thin:
  - DynamoDB human decisions exist only for core 14 (26 claimed, 20 rejected), plus 1 claim on core 2. No instrument core has any `rejected` row.
  - Engine `confirmed` rows on cores 1 to 13 are all staff co-authorship, and none carries `signal_ack`.
  - **No CORE# row on cores 1 to 13 carries an `llm_score`.** Lift over the LLM can therefore only be measured on panel B.
  - The usable outcomes are: panel B (core 2, 137 yes / 100 no; 137 / 97 with PMC text), engine `confirmed`, and the core alias named in full text beside a home institution (`signals.acknowledgement_signal`, `ack_institution == "home"`).
- **Affinity** is recomputed with the scored paper removed from its own numerator.

## Results

### Specificity and what each model fires on

| core | model | global PMC | WCM PMC | fired (regex) | home alias | rate | control rate | one-sided Fisher p |
|---|---|---|---|---|---|---|---|---|
| 2 | Biograph Vision Quadra | 355 | 7 | 4 | 0 | 0.000 | 0.052 (IVIS) | 1.000 |
| 2 | BioSpec 70/30 | 842 | 15 | 23 | 1 | 0.043 | 0.052 | 0.729 |
| 2 | **Inveon** | 4890 | 183 | 79 | 11 | **0.139** [0.080, 0.232] | 0.052 | **0.040** |
| 2 | Vevo 3100 | 2029 | 15 | 38 | 0 | 0.000 | 0.052 | 1.000 |
| 2 | Prisma Fit | 1497 | 18 | 24 | 1 | 0.042 | 0.052 | 0.743 |
| 2 | Discovery MR750 | 4505 | 78 | 40 | 1 | 0.025 | 0.052 | 0.880 |
| 4 | FACSymphony S6 | 387 | 26 | 40 | 5 | 0.125 | 0.093 (Fortessa) | 0.353 |
| 4 | **BD Influx** | 2166 | 74 | 73 | 18 | **0.247** [0.162, 0.356] | 0.093 | **0.002** |
| 4 | **Sony MA900** | 760 | 42 | 63 | 12 | **0.190** [0.112, 0.304] | 0.093 | **0.035** |
| 4 | FACSAria II SORP | 395 | 9 | 17 | 0 | 0.000 | 0.093 | 1.000 |
| 4 | FACSymphony A5 | 1237 | 36 | 72 | 8 | 0.111 | 0.093 | 0.404 |
| 4 | FACSCelesta | 2171 | 29 | 56 | 4 | 0.071 | 0.093 | 0.772 |
| 7 | **Promethion** (case-sensitive) | 5743 | 113 | 25 | 13 | **0.520** [0.335, 0.700] | none | |
| 7 | **EchoMRI** | 4354 | 54 | 23 | 7 | **0.304** [0.156, 0.509] | none | |
| 7 | Seahorse XFe24 | 2188 | 32 | 29 | 2 | 0.069 | none | |
| 9/13 | timsTOF Pro 2 | 581 | 18 | 18 | 0 / 1 | 0.000 / 0.056 | 0.109 (Q Exactive) | 0.875 |
| 13 | Orbitrap Fusion Lumos | 7696 | 91 | 60 | 3 | 0.050 | 0.109 | 0.956 |
| 13 | RapidFire | 539 | 17 | 9 | 0 | 0.000 | 0.109 | 1.000 |
| 10 | Cytek Aurora | 4427 | 161 | 105 | 1 | 0.010 | none | |
| 10 | Hyperion | 364 | 18 | 23 | 0 | 0.000 | none | |
| 10 | Helios CyTOF | 857 | 15 | 63 | 1 | 0.016 | none | |
| 11 | Incucyte SX5 / UltraMicroscope II / Stellaris 8 / AxioScan 7 / Axio Observer 7 | 576 to 2036 | 12 to 37 | 15 to 51 | 0 to 3 | 0.000 to 0.059 | 0.114 (LSM 880) | 0.93 to 1.00 |
| 12 | Avance III HD 600 / HD 500 / Inova 600 | 882 to 1971 | 2 to 19 | 5 to 6 | 0 to 1 | | none | |
| 1 | Xenium / Visium HD / CosMx | 791 to 1748 | 23 to 51 | 14 to 32 | 0 | 0.000 | none | |
| 2 | TR-19 cyclotron | 41 | 1 | 0 | | | | |

The table runs about 25 tests. With a Bonferroni correction, only Influx comes near 0.05, at 0.002 × 25 = 0.05.

### Silver precision per core

The share of fired papers that already carry usage evidence (engine confirmed, claimed, panel-B yes, or a home alias). These are lower bounds, because most real users never name the core.

| core | silver / fired | precision | CI95 |
|---|---|---|---|
| 2 | 24/305 | 0.079 | [0.053, 0.114] |
| 4 | 65/515 | 0.126 | [0.100, 0.158] |
| 7 | 22/78 | 0.282 | [0.194, 0.390] |
| 10 | 2/191 | 0.010 | [0.003, 0.037] |
| 11 | 23/306 | 0.075 | [0.051, 0.110] |
| 12 | 2/16 | 0.125 | [0.035, 0.360] |
| 13 | 27/298 | 0.091 | [0.063, 0.129] |
| 1 / 9 | 0/76, 0/18 | 0 | |
| 5 (NovaSeq control) | 41/521 | 0.079 | [0.059, 0.105] |

Gold negatives among fired papers: 2 in total, both core 2 panel B.

### Independence from affinity

This is the signal's one clear virtue. With self-excluded affinity, the fired papers that have any prior-user author are: 0 of 515 (core 4), 0 of 78 (core 7), 0 of 191 (core 10), 0 of 298 (core 13), and 51 of 305 (core 2). It is not part of the repeat-user loop. But for cores 4, 7, 10 and 13 there is also nothing to measure it against, because those cores have no affinity, no LLM scores and no human decisions.

## Why it is not wired

1. **No lift over the LLM where lift is measurable.** Panel B is saturated, and the over-LLM lift CI straddles 0.
2. **Within modality, the model string mostly restates "used this technique".** That is topicality, which is the same objection that sank TF-IDF and the journal subfield. A Q Exactive, LSM 880 or LSRFortessa paper names the WCM core at least as often as a paper naming the core's own distinctive model.
3. **These instruments are not unique to a core.** Tri-I neighbours and other collaborators run the same models: MSK has Inveon microPET for 89Zr immunoPET, and Salk/UCSD, NYU, Utah and OHSU appear in the sample. Many labs own Sony MA900, Seahorse, Incucyte and Cytek Aurora instruments. The methods sentence almost never says where the instrument sits.
4. **No gold negatives exist for any instrument core.** Pricing a weight needs rejected rows.

## What would reopen this

- A core-owner labelling pass on the shortlist's **incremental** papers, which have no alias, no staff and no confirmation: Inveon (68), BD Influx (55), Sony MA900 (51), Promethion (12) and EchoMRI (16). Each needs a yes/no "used our core". That is about 200 papers in total, 5 owners, roughly 40 each. With those labels, P(core | model, no alias) is directly measurable, and a WEIGHTS key could be fitted on it.
- LLM scores for cores 4 and 7 candidates, so lift over the LLM can be measured outside imaging.
- A pairing rule that may beat the bare string: a model mention AND a home-alias-free methods section AND a byline with no other-institution affiliation. That is untested here.

## Caveats

- The firing sets for large phrases come from the WCM-clause search (37% coverage, see above).
- The specific-model regexes were checked by eye on a 30-paper sample, and only the PromethION collision was fixed. Others may remain: "Influx" in non-sorter contexts is guarded by requiring sort/cytometer nearby or "BD".
- "Home alias" uses `classify_institution`'s window, so a generic alias ("Flow Cytometry Core") beside a WCM affiliation string counts as home.
- The run is read-only: a DynamoDB Scan, ReciterDB SELECTs, and NCBI E-utilities (keyed, paced). Nothing was written except the local JSON and the full-text cache.

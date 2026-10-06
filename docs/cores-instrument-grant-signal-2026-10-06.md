# S10 instrument grants and CTSC UL1 as a core-usage signal (experiment, 2026-10-06)

Verdict: not wired, and no `combine.WEIGHTS` key. Papers that cite a WCM S10 award
attached to a core do use that core, with 74–89% precision for the cores where the
mapping is solid. But the signal fires on 56 of the 82,173 corpus papers. On the one
panel with an LLM baseline it adds an AUC delta of +0.0000. The CTSC UL1 grant fires
broadly and does not separate claimed from rejected core-14 rows. Where it is worth
anything, the S10 signal works as a confirmer, the way acknowledgements do. It is not
a ranking feature. It also turned up an alias gap in the core-11 dictionary that is
probably worth more than the signal itself.

Script: `scripts/experiment_core_instrument_grants.py`. It is read-only. It calls NIH
RePORTER (projects and publications search) and NCBI esearch `[gr]`, and it fetches
PMC XML through `pipeline_cores.fulltext` with a disk-only cache that never touches the
S3 tier. It also runs reciterdb SELECTs and one DynamoDB Scan. There are no Bedrock calls.

    python3 scripts/experiment_core_instrument_grants.py --cache-dir out/core_grants

## What "validation found no shared core grant" covered

The comment comes from the 2026-06 pilot (`HEAD-TO-HEAD-REPORT.md` and
`match_deterministic.py` in the Projects folder). That pilot covered the imaging core
only, on the 237-paper panel. It counted the `<award-id>`s that co-occurred with a
core-name match in PMC full text. The co-occurring IDs were the papers' own R01s, so it
concluded that grant matching is "a dead end for this core", with a note to "test per
core". It never pulled the WCM S10 list. One panel paper cited an S10
(`S10OD030335`, labelled no), and that S10 belongs to no dictionary core. So S10s had
not been tested before this experiment.

## Data and coverage

- **Awards:** RePORTER lists 50 project-years and 36 distinct S10s under `WEILL MEDICAL
  COLL OF CORNELL UNIV`, from FY1986 to FY2023. No other WCM org name returns S10s.
- **Citing papers:** RePORTER links give 199 and PubMed `[gr]` gives 221 (several
  spellings of the serial), for a union of 201 pmids. Europe PMC full-text search returned a subset
  of the same papers and added nothing. Only 21 of the 36 awards are cited by any paper.
  The 2007 small-animal 7T MRI (`S10RR023020`), the 2021 7T human MRI (`S10OD027039`)
  and the 2023 PET/CT (`S10OD034271`) have zero citations.
- **Award to core mapping**, recorded in the script as `S10_CORE`:
  - Rule A covers 8 awards. The PI is a dictionary staff CWID: Ballon (core 2),
    Bracken (core 12), Xiang (core 5) and Anandasabapathy (core 10).
  - Rule B covers 12 awards. The PI is not on the staff list, but the instrument is one
    the core's `llm_description` names: Yi Wang's 3T/7T MRI (core 2), Gross's mass
    spectrometers (core 9), and Maxfield's EM and multiphoton (core 11).
  - 16 awards map to no core. They are cited by 57 papers.
- **Corpus reach:** the mapped awards fire on 56 corpus pmids in total: core 2 has 29,
  core 12 has 14, core 11 has 10, core 9 has 2, core 10 has 1 and core 5 has 0. The
  base rate is 0.001–0.035% of the corpus per core.
- **Labels:** human claimed and rejected rows exist only for core 14 (26 claimed, 20
  rejected) plus 1 claim on core 2. Cores 9, 10, 11 and 12 have none. For those cores,
  "independent evidence" stands in for a label (see below).

## Precision

A paper the signal fires on counts as **independent** when at least one of these holds:

- signal 3 matches a dictionary alias in the PMC text;
- a core staff CWID is on the byline;
- the engine confirmed the row on ack or staff evidence;
- a human claimed it.

Human rejections count against it, and there are none. Acknowledgements have near-zero
recall in the wild, so this undercounts. The **widened** column also counts the
core's name under a broader pattern that includes the `&` spelling (`WIDE_NAME`).

| core | rule | fires | independent | + widened name | in corpus |
|---|---|---|---|---|---|
| 2 Biomedical Imaging | A | 6 | 5/6 = 83% [44, 97] | 5/6 = 83% [44, 97] | 5 (3 candidate, 2 confirmed) |
| 2 Biomedical Imaging | B | 66 | 16/66 = 24% [16, 36] | 16/66 = 24% [16, 36] | 24 (all candidate) |
| 9 Adv. Biomolecular Analysis | B | 20 | 5/20 = 25% [11, 47] | 7/20 = 35% [18, 57] | 2 (both confirmed) |
| 10 Human Immune Monitoring | A | 3 | 0/3 | 1/3 | 1 (no row) |
| 11 Microscopy & Image Analysis | B | 19 | 7/19 = 37% [19, 59] | **17/19 = 89% [69, 97]** | 10 (no row) |
| 12 NMR | A | 31 | 23/31 = 74% [57, 86] | **25/31 = 81% [64, 91]** | 14 (5 confirmed, 7 candidate, 2 no row) |
| 5 Genomics Resources | A | 1 | 0/1 | 0/1 | 0 |

Intervals are 95% Wilson.

**Sample read.** I read the acknowledgement around the award number for 15 papers, all
from the in-corpus "no other evidence" rows:

- **Core 11:** 6 of 7 name "the Electron Microscopy & Histology services of the Weill
  Cornell Medicine Microscopy & Image Analysis Core" next to `S10RR027699`. The 7th
  (40753073, a cryo-EM paper) lists the award in a funder block without context. These
  are true uses, and signal 3 misses every one of them because of the ampersand.
- **Core 12:** 35013160 cites "S10OD016320 (William Clay Bracken, director of WCM NMR
  facility)". 36630286 and 37148884 carry the award only in PubMed metadata.
- **Core 2, rule B:** every row is Yi Wang's own lab, citing its 3T S10 next to its own
  R01s ("R01 NS090464, S10 OD021782"). Only 4 of 66 name the CBIC. The 16 "independent"
  hits are one staff co-author (dcs7001, 16 papers). All 24 in-corpus rows already have
  affinity 0.78–0.85. Here the S10 marks the author. It is not evidence that the paper
  used the instrument, and it duplicates the affinity prior instead of escaping it.

## Lift over a baseline that includes the LLM (leave-one-out)

The key is priced by WoE, refitted without each row (`loo_woe_scores`). The affinity
prior is rebuilt with every labelled paper excluded from its own numerator, as in
`fit_evidence_weights` and production since #418.

| panel | fires pos / neg | WoE | AUC base -> +key | delta (95% boot) |
|---|---|---|---|---|
| P2 core 2: yes vs labelled no; base = staff + affinity + LLM | 4/137 vs 0/100 | +1.89 | 0.9532 -> 0.9532 | +0.0000 [+0.0000, +0.0000] |
| P2 repeat users (aff > 0) | 3/54 vs 0/3 | | too few negatives | |
| P1 core 2: yes vs 1,200 random corpus; base = staff + affinity, no LLM | 4/137 vs 3/1200 | +2.41 | 0.8294 -> 0.8288 | -0.0006 [-0.0015, +0.0000] |
| P1 repeat users | 3/54 vs 3/37 | | | |
| core 14: claimed + confirmed/ack vs rejected; base = staff + LLM | UL1 5/19 vs 1/20 | +1.35 | 0.7132 -> 0.7132 | +0.0000 [-0.0428, +0.0448] |

The 4 panel-B positives it fires on are already ranked above every negative, so the
key cannot move the ranking. The 3 random-corpus "negatives" it fires on are unlabelled
corpus papers (Yi Wang's lab again), not known non-users. The core-14 panel leaves
affinity out of the base because the stored value on a confirmed row includes that
row's own confirmation.

## CTSC UL1 and core 14

UL1RR024996, UL1TR000457 and UL1TR002384 are linked to 2,970 pmids. 1,004 of them are
in the corpus (1.22%). UL1 rate among core-14 rows by status:

| status | UL1 |
|---|---|
| rejected (in corpus) | 1/20 = 5.0% [0.9, 23.6] |
| claimed (in corpus) | 1/7 = 14.3% [2.6, 51.3] |
| claimed (out of corpus, manual adds) | 15/19 = 78.9% [56.7, 91.5] |
| confirmed/ack | 4/12 = 33.3% [13.8, 60.9] |
| confirmed/staff | 5/13 = 38.5% [17.7, 64.5] |
| confirmed/prior-only | 3/47 = 6.4% |
| candidate | 65/486 = 13.4% |
| below_threshold | 259/4751 = 5.5% |

The 78.9% on manually added claims does not show the signal works. Those papers are
the informatics group's own work, and the CTSC funds that group, so UL1 is their home
grant. That makes it a staff signal under another name. On rows the engine surfaced,
it fires on 1 of 7 claimed and 1 of 20 rejected. UL1 tells you the paper used the
CTSC, which runs many services, and not which one. It is dead for core 14.

## Reading it

1. **Precision is real where the mapping is real.** NMR (rule A) and the EM/microscopy
   S10 (rule B) both run at about 80–90% once the core's name is matched the way
   authors actually write it.
2. **Coverage rules it out as a ranking feature.** 56 corpus papers across six cores.
   The citation record is sparse: 15 of 36 awards are never cited, including the
   flagship 7T and PET/CT. The four panel-B papers it fires on are already at the top.
   No lift is measurable, and none could be measured with these counts.
3. **It is independent of affinity only where it is small.** The new rows for cores 10,
   11 and 12 (11 papers) have affinity 0. Core 2, which has the largest firing set,
   gets it from one lab whose affinity is already 0.78–0.85.
4. **The useful finding is the alias gap.** WCM papers write "Microscopy & Image
   Analysis Core" and "Electron Microscopy & Histology". The dictionary has "Microscopy
   and Image Analysis Core", and `_alias_pattern` matches it literally, so it misses
   the ampersand form. 10 of 17 named core-11 papers in this set are missed for that
   reason alone. A PMC search for `"Electron Microscopy and Histology" AND "Weill
   Cornell"` returns 41 papers (count only, precision not measured).

## What would change the verdict

- An `&`/`and`-insensitive alias match, or explicit `&` aliases for core 11, measured
  with `refresh_alias_hits` and the usual panel-C institution check. This is a
  dictionary or `signals` change, not a new weight.
- If S10s are ever used, use them as a **confirmer-class** rule-A key: award PI on the
  core's staff, and the award number in the paper's own text. Price it like `ack`, on a
  panel of confirms. Today that panel would be about 37 papers across two cores, which
  is not enough.
- Award-level instrument→core facts from the cores themselves. Rule B rests on reading
  titles, and the core-2 3T case shows how a PI-owned S10 differs from a core-owned one.

# Research-area benchmark

This is the evidence behind the Scholars About page sentence (`#research-areas`):

> As an independent check, that map was benchmarked against authoritative institutional reference points: Weill Cornell's divisions and departments, its strategic research roadmap, and NIH research designations. It aligned cleanly with all three.

No artifact for that check was ever committed (it first appeared in the docs-only #235), so it was redone from scratch on 2026-10-10 against the live taxonomy. Everything needed to audit or rerun it is in this directory.

## Verdict

| Reference point | What was compared | Result | Does "aligned cleanly" hold? |
|---|---|---|---|
| A. WCM departments and divisions | Where each area's papers concentrate, from publication data | 62 of 67 areas have a clear home unit; the other 5 have an aligned unit with too few papers to call. 25 of 29 departments are the home of at least one area | Yes |
| B. CARE Strategic Plan 2026–2029 | The plan's 13 scientific items | All 13 correspond to at least one area. But the plan names capabilities (AI/data science, organoids and genomics, imaging, clinical research), not research domains, so only 11 of 67 areas touch it at all | Only as "the plan's priorities are all represented". It cannot validate the map |
| C1. NIH Institutes and Centers | The 24 grant-funding ICs | All 24 ICs have a corresponding area (NCCIH and NIDCR only weakly). 53 of 67 areas map to an IC; the 14 that don't are method and cross-cutting fields NIH doesn't organize ICs around | Yes, with an explainable gap |
| C2. NIH RCDC spending categories | All 331 categories with FY2025 funding | 58 of 67 areas match a category (28 exactly). 308 of 331 categories fall under some area | Yes |

The fair public claim is narrower than the current sentence. See [Proposed About-page copy](#proposed-about-page-copy).

## The crosswalk

`outputs/crosswalk.csv` is the single table to start from: 446 rows, one per entry in every reference source (29 departments, 42 divisions, 20 CARE plan items, 24 NIH ICs, 331 RCDC categories). Columns:

| Column | Meaning |
|---|---|
| `source` | Which reference list |
| `entry_id`, `entry` | The department, plan item, IC or category |
| `detail` | Plan pillar and section, or FY2025 RCDC funding |
| `how_represented` | Exact match, contained in a broader area, covered by narrower areas, partial overlap, not represented; for units, home unit / low volume / no papers; for operational plan items, out of scope |
| `match_basis` | How that was decided: publication data, mappers agreed, adjudicated, or gap review |
| `represented_by` | The research area(s) that represent it, with the relation (or lift and paper count for units) |

## Inputs (frozen 2026-10-10)

All in `inputs/`. Nothing here is per-person: unit-level counts only.

| File | Source |
|---|---|
| `research_areas.csv` | SPS prod `topic` table, source `reciterai-taxonomy_v2`: the 67 areas live on the site (matches `taxonomy_v2.json` on main after the #339 splits) |
| `wcm_departments.csv`, `wcm_divisions.csv` | SPS prod `department` / `division` (from the Enterprise Directory): 29 departments, 42 divisions |
| `frameA_*.csv` | SPS prod, read-only probe: distinct PMIDs per area × unit (see Frame A) |
| `care_plan_items.csv` | [CARE Strategic Plan brochure](https://weill.cornell.edu/sites/default/files/wcm_care_strategic_plan_brochure_2026-03-10_final.pdf) (2026-03-10). Every named item, with `in_scope=no` and a reason on the 7 operational ones (funding, recruitment, partnerships...) and on "Thematic areas", which the plan names but never lists |
| `nih_institutes_centers.csv` | The 24 grant-funding ICs from the NIH RePORT IC lookup (`report.nih.gov/reportweb/api/lookup/ic`). Excludes CC, OD and retired NCRR, which award no research grants |
| `nih_rcdc_categories.csv` | NIH RePORT categorical spending (`report.nih.gov/reportweb/api/CategoricalSpendings`): the 331 categories with a FY2025 amount ($M) |

**Corpus scope.** Area scores exist for academic articles from 2020 on where a WCM faculty member is first or last author (`utils/sql_queries.py`, the D4 cutoff). Frame A counts are therefore modest (e.g. 73 papers for Melanoma & Skin Cancer).

## Frame A: departments and divisions (`frame_a.py`)

No judgement involved. For each area `a` and unit `u` (a scholar's primary department or division):

- `n(a,u)` = distinct PMIDs scoring at or above the area's display threshold with an active scholar from `u` as author
- `share(a,u) = n(a,u) / n(a)`
- `lift(a,u) = share(a,u) / base(u)`, where `base(u)` is the unit's share of all area assignments. A lift above 1 means the area is over-represented in the unit.

An area has a **home** in a unit when lift ≥ 2 and n ≥ 25. **Low volume** means the best lift is ≥ 2 but on fewer than 25 papers: aligned, but too thin to call, so it is counted as neither a pass nor a gap.

| Lift | Min papers | Areas with a home | Departments covered | Divisions covered |
|---|---|---|---|---|
| 1.5 | 25 | 63/67 | 25/29 | 26/42 |
| **2.0** | **25** | **62/67** | **25/29** | **26/42** |
| 3.0 | 25 | 61/67 | 24/29 | 26/42 |
| 2.0 | 50 | 55/67 | 22/29 | 20/42 |

The headline result is stable across thresholds.

**Low-volume areas (5).** Each one's strongest unit is the expected one:
- Melanoma & Skin Cancer → Dermatology (lift 18)
- Sleep Medicine & Circadian Biology → Pulmonary Medicine (lift 12)
- Autoimmune & Rheumatologic Disease → no rheumatology unit in the directory feed (WCM rheumatology sits largely at HSS)
- Environmental & Planetary Health → Emergency Medicine / General Internal Medicine
- Research Infrastructure & Scientific Workforce → Library

**Departments that aren't the home of any area.**
- **Low volume (2):** Library, and Rehabilitation Medicine (Rehabilitation & Disability Medicine, lift 35 on 19 papers).
- **No scored papers (2):** Orthopaedic Surgery and Reproductive Medicine. No active SPS scholar with a primary appointment there has a first- or last-author 2020+ article in the scored corpus. That is a corpus-coverage fact, not a taxonomy gap: Musculoskeletal & Orthopedic Medicine and Women's Health & Reproductive Medicine both exist as areas.

Divisions follow the same pattern: 11 low volume (mostly Pediatrics subspecialties, each with the matching area on top) and 5 with no scored papers.

Outputs:
- `outputs/frame_a_forward.csv`: per area
- `outputs/frame_a_reverse.csv`: per unit
- `outputs/frame_a_area_unit.csv`: every area × unit cell

## Frames B, C1, C2: blind double mapping (`frames_bc.py`)

These need judgement, so the process was built to keep it honest.

The mappers, adjudicator and gap reviewer were independent LLM agent runs (Claude), each in a fresh context, not human annotators. The human check is the [spot check](#human-spot-check) below.

1. **Two independent mappers per frame.** Neither saw the other's output or the About-page claim, and both were told that "no counterpart" is a valid, expected answer. They traversed in opposite directions: one went reference entry by entry, the other area by area. For RCDC each direction was split into 3 slices.
2. **Relation types:**
   - `exact`: same field
   - `area_broader`: the reference entry sits inside the area
   - `area_narrower`: the area sits inside the reference entry
   - `overlap`
3. **Adjudication.** A third pass decided every pair where the mappers disagreed, without privileging either.
4. **Gap review.** A fourth pass re-checked every area and entry still unmatched, because pairs both mappers missed never reach adjudication. Its additions are labelled `gap_review` in the pairs files (2 for C1, 13 for C2) so they can be discounted.

**Agreement between the two blind mappers.** The grid is sparse (most cells are "none"), which flatters plain kappa, so positive agreement is the number to read.

| Frame | Mapper 1 pairs | Mapper 2 pairs | Positive agreement | κ (related / not) | κ (relation type) | Disagreements adjudicated |
|---|---|---|---|---|---|---|
| B CARE | 17 | 18 | 0.97 | 0.97 | 0.83 | 6 |
| C1 NIH ICs | 58 | 60 | 0.97 | 0.97 | 0.90 | 11 |
| C2 NIH RCDC | 366 | 291 | 0.82 | 0.82 | 0.78 | 144 |

Final pairs: B 18, C1 63, C2 389. Every pair carries its source (`agreed` / `adjudicated` / `gap_review`) and a one-line rationale in `outputs/frame_<k>_pairs.csv`.

### B. CARE Strategic Plan

All 13 scientific items correspond to an area, e.g.:
- Advanced Imaging → Radiology, Medical Imaging & Medical Physics
- Organoids and Genomics → Genetics/Genomics, Single-Cell & Spatial Biology, Stem Cell & Regenerative Medicine
- Implementation science → Health Services Research & Health Policy

Nine of the 13 are only `overlap`. That is expected: the plan names capabilities and missions, while the taxonomy names fields of inquiry. 56 of 67 areas have no counterpart in the plan, because the plan doesn't enumerate research domains (its "Thematic areas" are never listed).

### C1. NIH Institutes and Centers

Coverage:
- **ICs:** all 24 have a corresponding area. 6 are exact: NEI, NIA, NIEHS, NIMH, NIMHD, NCATS. NCCIH (via Pain Medicine) and NIDCR (via Otolaryngology & Head and Neck) were matched only by the gap review, as `overlap`.
- **Areas:** 53 of 67 map to an IC.

The 14 areas without an IC are:
- **Methods and quantitative fields:** Biostatistics, Epidemiology, Health Economics, Single-Cell & Spatial Biology
- **Care delivery and policy:** Health Services Research, Digital Health, Primary Care, Medical Education
- **Cross-organ specialties:** Surgery, Pathology
- **Cross-disease biology:** Gene & Cell Therapy, Stem Cell & Regenerative Medicine, Microbiome Research
- **Bioethics**

NIH organizes its ICs by disease, organ or population, so method and practice fields don't get one. That is a structural difference between the two schemes, not a sign the map is off.

The live taxonomy has no Oral & Craniofacial area (it was retired), which is why NIDCR is only weakly matched.

### C2. NIH RCDC categories

**Areas:** 58 of 67 match at least one category. 28 are exact, 23 contain categories, and the rest are narrower or overlapping. The 9 without a category are:
- Anesthesiology
- Biochemistry & Biophysics
- Cell & Molecular Biology
- Systems Biology
- Single-Cell & Spatial Biology
- Pathology & Laboratory Medicine
- Surgery & Perioperative Medicine
- Medical Education & Healthcare Workforce
- Bioethics, Medical Humanities & Clinician Wellbeing

RCDC is a disease-and-condition scheme, and these are disciplines or methods.

**Categories:** 308 of 331 fall under some area. That is 93.6% of summed category dollars, but categories overlap, so this is not a share of the NIH budget. The 23 unmatched categories are mostly:
- Violence research (Firearms, Youth Violence, Violence Against Women, Homicide)
- Broad behavioral and social science
- Rare syndromes too small for their own area (MPS, POTS, ME/CFS, Lymphedema)
- Administrative categories (Rare Diseases, Human Fetal Tissue, Arctic)

The full list is in `outputs/frame_C2_reverse.csv`. The first two groups are real gaps in WCM's map relative to NIH's.

## Human spot check

`outputs/spot_check_sample.csv` holds a seeded random 10% of final pairs (47 of 470, seed 20261010), with blank `reviewer_agrees` / `reviewer_note` columns. Fill it in and report the agreement rate here before the results are cited publicly.

## Proposed About-page copy

> As an independent check, we compared that map with three outside reference points. Against Weill Cornell's departments and divisions, 62 of 67 research areas are concentrated in a recognizable home unit. Against NIH's research, condition and disease categories, 58 areas have a matching category, and together the areas cover 308 of NIH's 331 categories. And each of the 13 scientific priorities named in Weill Cornell's CARE Strategic Plan corresponds to at least one area. The full comparison, including where the map and these references don't line up, is published [here].

## Rerun

```bash
python3 docs/benchmark/frame_a.py    # from inputs/ aggregates
python3 docs/benchmark/frames_bc.py  # from outputs/mapping_raw.json + gap_review.json
python3 docs/benchmark/crosswalk.py  # builds outputs/crosswalk.csv from the two above
```

Refreshing the inputs means:
- **Frame A:** rerun `mapping/sps_probe_frame_a.ts` with SPS `scripts/run-staging-probe.sh <file> prod`. It is read-only and returns aggregates only.
- **Frames B, C1, C2:** rerun the blind mapping. `mapping/blind_mapping.js` and `mapping/gap_review.js` are the exact Claude Code workflow scripts used, with every mapper, adjudicator and gap-review prompt.

Redo the benchmark whenever `taxonomy_version` changes.

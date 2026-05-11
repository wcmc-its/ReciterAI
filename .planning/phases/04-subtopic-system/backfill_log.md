# Per-topic Backfill Log

This file is appended to by `backfill_topic.py` and `backfill_all.py` as each
topic completes. It is the grep-able audit trail for the Plan 04-06 full
backfill (complement to `artifacts/backfill_log.md`, which records
`SKIP_REVIEW` audit rows only).

Initial template — rows are appended by the runners at execution time.

| topic_id | activity_count | cluster_count | coverage_pct | passes | sonnet_$ | haiku_$ | status |
| -------- | -------------- | ------------- | ------------ | ------ | -------- | ------- | ------ |
| epidemiology_population_health | 1887 | 15 | 27.3% | 1 |  |  | failed:assign |

<!-- === epidemiology_population_health === rc=1 -->
```

=== Pass 1 Discovery Results ===
  topic_id:         epidemiology_population_health
  topic_label:      Epidemiology & Population Health
  total_activities: 1887
  cluster_count:    15
  coverage_pct:     27.3%
  uncovered:        1394
  passes_executed:  2
  input_tokens:     156980
  output_tokens:    5645
  dry_run:          False

[output] Written to: .planning/phases/04-subtopic-system/hierarchy_draft_epidemiology_population_health.json

-- stderr --
O After dedup + score filter (≥0.3): 1887 unique activities
12:45:53 INFO Proceeding with 1887 activities (min_activities floor=30)
12:45:53 INFO Starting initial Sonnet discovery pass...
12:47:55 INFO Sonnet response: inputTokens=156980, outputTokens=5645
12:47:55 INFO Pass 1: 15 clusters, coverage=27.3%, uncovered=1394
12:47:55 INFO Coverage 27.3% < 85%. Running extension pass (cap=15 new clusters)...
12:49:17 INFO Extension pass Sonnet: inputTokens=117467, outputTokens=4096
12:49:17 ERROR JSON parse failure on extension pass: Expecting ',' delimiter: line 109 column 1631 (char 11879)
12:49:17 WARNING Continuing with initial pass results only.
12:49:17 INFO Pass 2: 15 clusters total, coverage=27.3%, uncovered=1394
12:49:17 INFO Wrote hierarchy draft to .planning/phases/04-subtopic-system/hierarchy_draft_epidemiology_population_health.json
12:49:17 INFO [pass 1: discover] complete
12:49:17 WARNING --skip-review: set review_status=auto_approved on /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_draft_epidemiology_population_health.json. Audit row appended to /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/artifacts/backfill_log.md.
12:49:17 INFO [pass 2: assign] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/assign_subtopics.py --topic epidemiology_population_health --resume
12:49:18 ERROR Hierarchy draft .planning/phases/04-subtopic-system/hierarchy_draft_epidemiology_population_health.json has review_status='auto_approved'; expected 'approved'. Pass 2 is gated on human review.
12:49:18 ERROR Pass 2 (assign) failed for epidemiology_population_health: Command '['/opt/homebrew/opt/python@3.14/bin/python3.14', '/Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/assign_subtopics.py', '--topic', 'epidemiology_population_health', '--resume']' returned non-zero exit status 2.

```
| biostatistics_quantitative_sciences | 1816 | 14 | 17.9% | 1 |  |  | failed:assign |

<!-- === biostatistics_quantitative_sciences === rc=1 -->
```

=== Pass 1 Discovery Results ===
  topic_id:         biostatistics_quantitative_sciences
  topic_label:      Biostatistics & Quantitative Health Sciences
  total_activities: 1816
  cluster_count:    14
  coverage_pct:     18.0%
  uncovered:        1500
  passes_executed:  2
  input_tokens:     154243
  output_tokens:    5818
  dry_run:          False

[output] Written to: .planning/phases/04-subtopic-system/hierarchy_draft_biostatistics_quantitative_sciences.json

-- stderr --
ities
12:49:22 INFO Proceeding with 1816 activities (min_activities floor=30)
12:49:22 INFO Starting initial Sonnet discovery pass...
12:51:25 INFO Sonnet response: inputTokens=154243, outputTokens=5818
12:51:25 INFO Pass 1: 14 clusters, coverage=18.0%, uncovered=1500
12:51:25 INFO Coverage 18.0% < 85%. Running extension pass (cap=14 new clusters)...
12:52:57 INFO Extension pass Sonnet: inputTokens=129347, outputTokens=4095
12:52:57 ERROR JSON parse failure on extension pass: Expecting property name enclosed in double quotes: line 90 column 272 (char 11365)
12:52:57 WARNING Continuing with initial pass results only.
12:52:57 INFO Pass 2: 14 clusters total, coverage=18.0%, uncovered=1500
12:52:57 INFO Wrote hierarchy draft to .planning/phases/04-subtopic-system/hierarchy_draft_biostatistics_quantitative_sciences.json
12:52:57 INFO [pass 1: discover] complete
12:52:57 WARNING --skip-review: set review_status=auto_approved on /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_draft_biostatistics_quantitative_sciences.json. Audit row appended to /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/artifacts/backfill_log.md.
12:52:57 INFO [pass 2: assign] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/assign_subtopics.py --topic biostatistics_quantitative_sciences --resume
12:52:57 ERROR Hierarchy draft .planning/phases/04-subtopic-system/hierarchy_draft_biostatistics_quantitative_sciences.json has review_status='auto_approved'; expected 'approved'. Pass 2 is gated on human review.
12:52:57 ERROR Pass 2 (assign) failed for biostatistics_quantitative_sciences: Command '['/opt/homebrew/opt/python@3.14/bin/python3.14', '/Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/assign_subtopics.py', '--topic', 'biostatistics_quantitative_sciences', '--resume']' returned non-zero exit status 2.

```
| epidemiology_population_health | 1887 | 15 | 27.3% | 3 |  |  | ok |

<!-- === epidemiology_population_health === rc=0 -->
```
pulation_health                     21.2212
epidemiology_vaccine_immunization_epidemiology                 15.8374

faculty touched: 711
subtopic count: 15
total subtopic score sum (all): 831.8142

=== Top-5 faculty per subtopic ===

  epidemiology_covid19_outcomes_surveillance:
    hsc2001                 19.3914
    lja2002                 19.3914
    pag9051                  4.9343
    mms9024                  4.2552
    yoz2009                  3.2243

  epidemiology_chronic_disease_risk_factors:
    mms9024                 11.3907
    pag9051                  6.4644
    liy9032                  4.9108
    jwp2001                  4.5915
    var4002                  4.5915

  epidemiology_racial_ethnic_health_disparities_outcomes:
    rsw9006                  3.5368
    mfg9004                  3.0536
    mms9024                  3.0505
    lcp2003                  2.6290
    syj7002                  1.7318

  epidemiology_cancer_screening_survivorship:
    rmt4001                  7.5097
    est2003                  2.6790
    baa2012                  2.5520
    euc4006                  2.4045
    kek4007                  1.6917

  epidemiology_stroke_cerebrovascular_disease:
    hok9010                 13.8680
    ban9003                  8.2505
    alm9097                  6.8732
    sam9200                  6.0912
    coi2001                  4.0572

  epidemiology_healthcare_utilization_fragmentation:
    lmk2003                  4.8341
    mms9024                  4.6619
    lcp2003                  3.3494
    luk9003                  2.2412
    jac9029                  2.0291

  epidemiology_infectious_disease_surveillance:
    lja2002                  9.6837
    hsc2001                  6.7668
    rnp2002                  3.9070
    myl2003                  3.6433
    ras9199                  2.0199

  epidemiology_social_determinants_disparities:
    mms9024                  5.6399
    lcp2003                  4.7774
    mrs9012                  3.3519
    pag9051                  2.7224
    erp2001                  2.5393

  epidemiology_maternal_perinatal_health:
    mos7003                  2.2465
    hlipkind                 2.0818
    rsw9006                  1.9658
    sea2003                  1.7277
    anm4001                  1.4705

  epidemiology_machine_learning_ehr_methods:
    few2001                  2.5526
    chz4001                  1.2649
    rak2007                  1.1194
    yoz2009                  1.1005
    yip4002                  1.0094

  epidemiology_elder_mistreatment_aging:
    mslachs                  3.1556
    aer2006                  2.8531
    esc4003                  2.4694
    dwh4001                  1.6846
    arj2005                  1.4490

  epidemiology_opioid_substance_use_policy:
    emm4010                  4.3624
    haw9006                  2.5873
    smm2010                  2.3845
    yub2003                  1.8455
    brs2006                  1.7978

  epidemiology_mental_health_suicide:
    yux4008                  4.6822
    emm4010                  1.6334
    sab2028                  1.4120
    yip4002                  0.8273
    nis2051                  0.7533

  epidemiology_global_lmic_population_health:
    jur9123                  2.1732
    rnp2002                  1.6870
    roh9005                  1.6577
    liy9032                  1.5355
    var4002                  1.3328

  epidemiology_vaccine_immunization_epidemiology:
    lja2002                  5.2872
    hsc2001                  5.1050
    mec2013                  0.4741
    zhz9010                  0.4741
    pek2003                  0.4741

=== backfill_topic.py complete ===
  topic:           epidemiology_population_health
  activity_count:  1887
  cluster_count:   15
  coverage_pct:    0.2729
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_epidemiology_population_health.json


-- stderr --
iled for pid=mpecker: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
13:04:37 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=jeb4033: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
13:04:37 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=rym4009: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
13:04:37 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=uqk9001: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
13:04:38 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=jas7013: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
13:04:39 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=cym2003: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
13:04:39 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=tmd9004: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
13:04:39 INFO Writes: cleared=255, written=711, dry_run=False
13:04:39 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_epidemiology_population_health.json
13:04:39 INFO [pass 3: aggregate] complete
13:04:40 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_epidemiology_population_health.json

```
| biostatistics_quantitative_sciences | 1816 | 14 | 17.9% | 3 |  |  | ok |

<!-- === biostatistics_quantitative_sciences === rc=0 -->
```
18.3772
biostatistics_survival_analysis_longitudinal                   17.6025
biostatistics_opioid_substance_use_policy                      10.4905
biostatistics_statistical_methods_methodology                   9.1555
biostatistics_cost_effectiveness_economic_evaluation            7.3635

faculty touched: 767
subtopic count: 14
total subtopic score sum (all): 542.5974

=== Top-5 faculty per subtopic ===

  biostatistics_prediction_risk_models:
    hok9010                  3.4510
    mms9024                  2.8813
    pag9051                  1.7645
    ban9003                  1.7486
    sam9200                  1.7427

  biostatistics_real_world_evidence_healthcare_utilization:
    mms9024                  2.7535
    yoz2009                  2.2793
    amb2036                  2.0474
    ars2013                  2.0346
    arj2005                  1.8512

  biostatistics_health_disparities_social_determinants:
    mms9024                  4.4160
    lcp2003                  3.4955
    yux4008                  1.9976
    rsw9006                  1.8644
    pag9051                  1.5872

  biostatistics_clinical_trial_design:
    mfg9004                  6.3174
    mmr2011                  5.1916
    lngirard                 3.7177
    mrd2006                  2.3028
    bjr4002                  1.7915

  biostatistics_diagnostic_accuracy_measurement:
    hgp2001                  1.5340
    all9188                  1.0504
    yrj9003                  0.9146
    mms9024                  0.7659
    anr2783                  0.6801

  biostatistics_epidemiology_infectious_disease:
    lja2002                 17.1664
    hsc2001                 14.7096
    nah2005                  0.9695
    mms9024                  0.9363
    jom2042                  0.7770

  biostatistics_machine_learning_ai_health:
    few2001                  3.8837
    yip4002                  2.2422
    sab2028                  1.1672
    yiz2014                  1.0515
    aaa4027                  0.7432

  biostatistics_causal_inference_comparative_effectiveness:
    mfg9004                  1.7280
    few2001                  1.5312
    chz4001                  1.5312
    lngirard                 1.1423
    ars2013                  0.8353

  biostatistics_omics_multiomics_biomarkers:
    kas2049                  2.6037
    jak2043                  1.6224
    him4004                  1.5672
    ole2001                  1.2421
    amh2025                  0.9632

  biostatistics_patient_subphenotyping_clustering:
    few2001                  2.5700
    chs4001                  1.0967
    ejs9005                  0.9013
    col2004                  0.8820
    yiz2014                  0.7124

  biostatistics_survival_analysis_longitudinal:
    juz4004                  1.1369
    mmr2011                  0.6901
    euc4006                  0.5764
    yus4011                  0.5540
    hok9010                  0.4874

  biostatistics_opioid_substance_use_policy:
    emm4010                  3.0582
    haw9006                  1.4280
    smm2010                  1.1653
    alj4004                  1.1125
    yub2003                  0.9367

  biostatistics_statistical_methods_methodology:
    wol4002                  1.8232
    yus4011                  1.2100
    mis4060                  0.5191
    hok9010                  0.3570
    jdvicto                  0.2693

  biostatistics_cost_effectiveness_economic_evaluation:
    smm2010                  1.1924
    alj4004                  1.0550
    brs2006                  0.6940
    jch9011                  0.3210
    jur9123                  0.3157

=== backfill_topic.py complete ===
  topic:           biostatistics_quantitative_sciences
  activity_count:  1816
  cluster_count:   14
  coverage_pct:    0.1795
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_biostatistics_quantitative_sciences.json


-- stderr --
id=chs9218: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
13:13:37 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=srj2003: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
13:13:38 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=kcb4002: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
13:13:39 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=tsa9005: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
13:13:39 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=did2005: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
13:13:39 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=sca2002: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
13:13:40 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=mfw4002: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
13:13:40 INFO Writes: cleared=578, written=767, dry_run=False
13:13:40 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_biostatistics_quantitative_sciences.json
13:13:41 INFO [pass 3: aggregate] complete
13:13:41 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_biostatistics_quantitative_sciences.json

```
| translational_clinical_science | 1741 | 15 | 16.8% | 3 |  |  | ok |

<!-- === translational_clinical_science === rc=0 -->
```
             16.3745
translational_wearables_digital_health                          9.0633
translational_research_workforce_ctsa                           6.9865

faculty touched: 825
subtopic count: 15
total subtopic score sum (all): 604.3699

=== Top-5 faculty per subtopic ===

  translational_precision_oncology_genomics:
    ole2001                  6.3562
    ggi9001                  3.5815
    ans2077                  3.5527
    jmm9018                  3.4423
    brr2006                  1.9574

  translational_oncology_clinical_trials:
    cns9006                  3.8896
    jdw2002                  3.6247
    gar2001                  3.2328
    mas9313                  2.4379
    formenti                 1.9432

  translational_imaging_biomarkers:
    yiwang                   3.7114
    ram2045                  2.6419
    tdn2001                  2.5060
    jik9027                  1.9738
    inp2002                  1.8639

  translational_clinical_trial_design_methodology:
    mfg9004                  3.3865
    mmr2011                  2.5627
    bjr4002                  2.2471
    jei9008                  1.6967
    thc2015                  1.6082

  translational_biomarker_liquid_biopsy:
    mac9795                  3.2726
    irm2224                  1.8963
    car4012                  1.6194
    dcl2001                  1.4958
    sel4002                  1.4896

  translational_covid19_therapeutics_vaccines:
    mwm9004                  2.2579
    zhz9010                  2.1539
    res2025                  1.8631
    mec2013                  1.8360
    hsc2001                  1.8286

  translational_machine_learning_ehr:
    few2001                  3.5811
    yip4002                  1.2649
    chz4001                  0.9987
    ole2001                  0.9896
    yiz2014                  0.9889

  translational_neurology_stroke_brain_injury:
    hok9010                  2.3365
    sut2006                  1.7370
    nds2001                  1.6147
    amk2012                  1.3389
    coi2001                  1.3173

  translational_infectious_disease_hiv:
    sap4017                  3.9947
    lndhlovu                 2.1735
    rbjones                  1.9820
    gef4003                  1.3473
    tap4002                  1.1390

  translational_alzheimers_neurodegeneration_biomarkers:
    yil4008                  2.2572
    gcc9004                  2.1021
    tab2006                  1.9895
    liz2018                  1.6655
    shh4006                  1.0473

  translational_gene_therapy_aav:
    rgcryst                  2.7265
    smkamins                 2.2827
    dos2011                  1.9583
    szk7001                  1.2323
    jpd2001                  0.9969

  translational_transplant_kidney_allograft:
    dmd2001                  3.0039
    msuthan                  2.9404
    mut9002                  2.7048
    ths9052                  1.1365
    sts9057                  0.9781

  translational_patient_reported_outcomes:
    mms9024                  1.1022
    kia9010                  0.6801
    hgp2001                  0.5703
    sjc7004                  0.5353
    nis2051                  0.5024

  translational_wearables_digital_health:
    aaa4027                  0.7009
    ara4013                  0.6055
    fgd2002                  0.5463
    jis2011                  0.5171
    sjc7004                  0.4434

  translational_research_workforce_ctsa:
    thc2015                  0.8656
    cem2009                  0.3689
    chm2042                  0.3689
    dop9054                  0.3689
    jak2043                  0.3689

=== backfill_topic.py complete ===
  topic:           translational_clinical_science
  activity_count:  1741
  cluster_count:   15
  coverage_pct:    0.1683
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_translational_clinical_science.json


-- stderr --
iled for pid=kab4035: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
13:26:37 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=cac2059: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
13:26:37 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=sckiang: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
13:26:38 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=jdk9007: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
13:26:38 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=yoh4003: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
13:26:39 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=bsg2001: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
13:26:40 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=las4011: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
13:26:41 INFO Writes: cleared=635, written=825, dry_run=False
13:26:41 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_translational_clinical_science.json
13:26:41 INFO [pass 3: aggregate] complete
13:26:42 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_translational_clinical_science.json

```
| health_services_policy | 1598 | 15 | 29.1% | 3 |  |  | ok |

<!-- === health_services_policy === rc=0 -->
```
ices                       19.7641
health_home_health_community_care_workforce                    17.4830
health_medicaid_insurance_coverage_expansion                   11.4697

faculty touched: 623
subtopic count: 15
total subtopic score sum (all): 605.2100

=== Top-5 faculty per subtopic ===

  health_hospital_utilization_quality_improvement:
    luk9003                  5.1958
    haw9006                  4.8717
    jac9029                  4.6202
    pag9051                  4.1167
    udk9001                  2.6399

  health_racial_ethnic_disparities_access:
    rsw9006                  4.3466
    mms9024                  3.3417
    syj7002                  2.4315
    sea2003                  2.4217
    lcp2003                  2.1811

  health_covid19_pandemic_health_systems:
    rak2007                  1.6094
    khd9010                  1.1560
    yoz2009                  1.0254
    mms9024                  1.0092
    pag9051                  0.8948

  health_opioid_substance_use_policy:
    emm4010                 10.3720
    smm2010                  6.4580
    brs2006                  4.4764
    shk9078                  4.2623
    yub2003                  3.4824

  health_healthcare_financing_payment_policy:
    amb2036                  7.7986
    khd9010                  5.1986
    rtb2003                  2.9461
    wls4001                  2.9450
    yoz2009                  2.4597

  health_care_coordination_fragmentation:
    lmk2003                  7.9453
    mms9024                  6.2263
    lcp2003                  5.4169
    acr2213                  1.7488
    sab2028                  0.9387

  health_cancer_screening_disparities_survivorship:
    est2003                  1.5953
    jch9011                  1.4694
    mkf2002                  1.4451
    ras9030                  1.3177
    lcp2003                  1.2817

  health_telehealth_digital_health:
    jiy4002                  3.0009
    ras2022                  1.8336
    arj2005                  1.4872
    jik9019                  1.0608
    mal9250                  1.0608

  health_social_determinants_health_outcomes:
    mms9024                  2.9010
    lcp2003                  2.2742
    mrs9012                  1.9331
    pag9051                  1.5949
    yoz2009                  1.5836

  health_global_health_lmic_systems:
    ras9199                  3.3895
    rnp2002                  2.0892
    liy9032                  1.9929
    jur9123                  1.8725
    var4002                  1.8419

  health_mental_health_behavioral_policy:
    emm4010                  5.7669
    das2043                  1.3734
    mcr2004                  1.3671
    sab2028                  1.0617
    yux4008                  0.9919

  health_nursing_home_long_term_care:
    arj2005                  5.8316
    mau2006                  5.3566
    rtb2003                  3.2255
    yoz2009                  1.1806
    mcr2004                  0.8110

  health_elder_mistreatment_aging_services:
    aer2006                  4.5835
    mslachs                  3.0130
    esc4003                  2.5756
    dwh4001                  1.8928
    als9138                  1.4503

  health_home_health_community_care_workforce:
    mrs9012                  5.0582
    mms9024                  2.3160
    lmk2003                  2.0793
    pag9051                  0.7424
    arj2005                  0.6139

  health_medicaid_insurance_coverage_expansion:
    mms9024                  0.9350
    rsb2005                  0.9350
    rur9017                  0.9350
    anm4001                  0.8678
    emm4010                  0.7421

=== backfill_topic.py complete ===
  topic:           health_services_policy
  activity_count:  1598
  cluster_count:   15
  coverage_pct:    0.291
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_health_services_policy.json


-- stderr --
es_for_topic failed for pid=jfmurray: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
13:37:29 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=mak9268: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
13:37:34 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=keh9003: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
13:37:34 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=rjm2002: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
13:37:37 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=alw3005: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
13:37:37 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=amr9094: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
13:37:46 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=alg4055: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
13:37:46 INFO Writes: cleared=553, written=623, dry_run=False
13:37:46 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_health_services_policy.json
13:37:46 INFO [pass 3: aggregate] complete
13:37:47 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_health_services_policy.json

```
| cell_molecular_biology | 1431 | 15 | 29.2% | 3 |  |  | ok |

<!-- === cell_molecular_biology === rc=0 -->
```
tability                               18.2294
cell_innate_lymphoid_mucosal_immunity                          17.2280
cell_gene_therapy_aav_vectors                                  14.2068
cell_cycle_regulation_signaling                                10.3075

faculty touched: 589
subtopic count: 15
total subtopic score sum (all): 895.9663

=== Top-5 faculty per subtopic ===

  cell_intracellular_signaling_pathways:
    khm2002                  3.0561
    lig2033                  3.0410
    jobuck                   2.7538
    llevin                   2.7538
    lif4001                  2.3003

  cell_cancer_genomics_molecular_oncology:
    ole2001                  9.3917
    ans2077                  4.6558
    jmm9018                  3.4894
    chm2042                  3.3027
    cem2009                  3.1860

  cell_epigenetic_chromatin_regulation:
    ole2001                  5.1367
    efa2001                  3.4292
    chm2042                  3.1738
    mrt2001                  2.9232
    dal3005                  2.6658

  cell_metabolism_bioenergetics:
    kyr9001                  2.4178
    qic2005                  2.1411
    alg2057                  2.1396
    job2064                  1.9577
    res2025                  1.8502

  cell_stem_cell_differentiation:
    jzx2002                  5.9433
    tre2003                  5.4924
    shc2034                  4.7079
    res2025                  4.4964
    dar2042                  4.0082

  cell_tumor_microenvironment_immunity:
    jdw2002                  3.5759
    tam2037                  3.1286
    sab4028                  2.2662
    ole2001                  2.2371
    roz4002                  2.0984

  cell_rna_biology_gene_expression:
    srj2003                 11.2240
    hut2006                  3.4209
    mer2005                  2.2268
    tmilner                  1.6668
    mut9002                  1.6206

  cell_membrane_protein_structure_function:
    sis2019                  4.3533
    crn2002                  3.3988
    olb2003                  3.2440
    ale4009                  3.0662
    slr4003                  2.3639

  cell_neuronal_synaptic_biology:
    tmilner                  2.0613
    fslee                    1.9746
    jab2058                  1.9617
    mas2189                  1.9617
    hchemmi                  1.3907

  cell_lipid_sterol_membrane_biology:
    and2039                  2.6307
    akm2003                  1.9202
    gek2009                  1.4002
    ole2001                  1.2565
    frmaxfie                 1.2108

  cell_gut_microbiome_host_interaction:
    chg4001                  3.4987
    ili2001                  2.2678
    daa2028                  2.2043
    moa4006                  2.1030
    ral2006                  1.2498

  cell_dna_repair_genome_stability:
    nflue                    2.1823
    euy2001                  1.4168
    wkhollo                  1.3311
    jet2021                  1.0950
    jeg2039                  1.0333

  cell_innate_lymphoid_mucosal_immunity:
    gfs2002                  2.8657
    wez4002                  2.1213
    daa2028                  1.9017
    maa4016                  1.6963
    mel4003                  1.5983

  cell_gene_therapy_aav_vectors:
    rgcryst                  2.0506
    shg3006                  1.3775
    smkamins                 1.3182
    lud2005                  1.2200
    dos2011                  0.9211

  cell_cycle_regulation_signaling:
    tom4003                  3.6381
    bek4011                  0.9446
    hol4006                  0.9446
    lca4001                  0.6066
    ros4015                  0.6066

=== backfill_topic.py complete ===
  topic:           cell_molecular_biology
  activity_count:  1431
  cluster_count:   15
  coverage_pct:    0.2921
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_cell_molecular_biology.json


-- stderr --
res_for_topic failed for pid=nan2017: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
13:53:24 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=vjk9004: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
13:53:24 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=btm9003: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
13:53:24 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=bek9059: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
13:53:24 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=bschwer: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
13:53:25 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=shm2662: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
13:53:25 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=stk2005: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
13:53:25 INFO Writes: cleared=462, written=589, dry_run=False
13:53:25 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_cell_molecular_biology.json
13:53:25 INFO [pass 3: aggregate] complete
13:53:26 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_cell_molecular_biology.json

```
| implementation_science | 1314 | 0 | - | 0 |  |  | failed:discover |

<!-- === implementation_science === rc=1 -->
```

-- stderr --
13:53:26 INFO Pre-counting qualifying activities for implementation_science (score >= 0.3)...
13:53:26 INFO implementation_science: 1314 qualifying activities
13:53:26 INFO [pass 1: discover] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/discover_subtopics.py --topic implementation_science --min-activities 30
13:53:26 INFO Topic: Implementation Science (id: implementation_science, prefix: implementation)
13:53:27 INFO Found credentials in environment variables.
13:53:27 INFO Querying DynamoDB partition: TOPIC#implementation_science
13:53:27 INFO Fetched 2758 raw SCORE# items for implementation_science
13:53:27 INFO After dedup + score filter (≥0.3): 1314 unique activities
13:53:27 INFO Proceeding with 1314 activities (min_activities floor=30)
13:53:27 INFO Starting initial Sonnet discovery pass...
13:56:25 INFO Sonnet response: inputTokens=107260, outputTokens=8192
13:56:25 ERROR JSON parse failure on initial pass: Expecting ',' delimiter: line 110 column 9013 (char 20188)
13:56:25 ERROR Pass 1 (discover) failed for implementation_science: Command '['/opt/homebrew/opt/python@3.14/bin/python3.14', '/Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/discover_subtopics.py', '--topic', 'implementation_science', '--min-activities', '30']' returned non-zero exit status 3.

```
| surgery_perioperative_medicine | 1019 | 30 | 79.1% | 3 |  |  | ok |

<!-- === surgery_perioperative_medicine === rc=0 -->
```
thesia:
    zat2002                  0.9692
    kap9009                  0.7973
    rsw9006                  0.7197
    kok4001                  0.6748
    man9026                  0.6147

  surgery_transcatheter_structural_heart:
    shc9182                  0.8988
    jac9029                  0.8191
    luk9003                  0.7480
    bjr4002                  0.7025
    blerman                  0.6895

  surgery_urologic_prostate:
    jch9011                  4.6449
    dss2001                  1.2657
    baa2012                  1.2385
    jim2012                  1.0569
    nap9055                  0.7814

  surgery_minimally_invasive_gi_pancreas:
    roc9045                  0.7984
    brn9034                  0.6992
    raz2002                  0.3995
    tjfahey                  0.3995
    bmf9002                  0.3995

  surgery_heart_transplant_mechanical_support:
    juf4007                  1.3486
    lok9031                  0.6879
    nakayos                  0.6744
    bmw2002                  0.3953
    udk9001                  0.3152

  surgery_postop_opioid_pain:
    haw9006                  2.5204
    ans9243                  0.5196
    atabaee                  0.3707
    dik2002                  0.3311
    klk9001                  0.3311

  surgery_nerve_decompression_headache:
    lig4013                  5.2089
    kdr9004                  0.5613
    roj9068                  0.4711
    mmsouwei                 0.1661
    bel9057                  0.1661

  surgery_global_surgical_education:
    roh9005                  0.6675
    lngirard                 0.5628
    mfg9004                  0.5628
    mmr2011                  0.5628
    dmo9004                  0.4928

  surgery_surgical_oncology_adrenal_endocrine:
    raz2002                  1.5762
    tjfahey                  1.5762
    bmf9002                  1.5762
    iqn9002                  0.3557
    cha2010                  0.3047

  surgery_orthopedic_joint_reconstruction:
    rsw9006                  0.8372
    ars2013                  0.5694
    jim2012                  0.5694
    tft9001                  0.4315
    dck7002                  0.4315

  surgery_craniofacial_facial_fractures:
    ans9243                  1.0516
    gsr9001                  0.7982
    ask9001                  0.5468
    mgs2002                  0.5468
    dmo9004                  0.4693

  surgery_pediatric_otolaryngology:
    aam9008                  0.9982
    ant9025                  0.3259
    man9026                  0.3203
    lig2002                  0.3203
    vkm2001                  0.3010

  surgery_intracerebral_hemorrhage_stroke:
    hok9010                  0.7819
    sam9200                  0.7378
    jak9030                  0.4956
    jmo9001                  0.4956
    rsz4001                  0.3362

  surgery_deep_brain_stimulation_movement:
    jjfins                   0.4272
    nds2001                  0.4272
    lig2002                  0.1950
    jdvicto                  0.1950
    sut2006                  0.1950

  surgery_male_reproductive_urology:
    jak9111                  0.8681
    mgoldst                  0.7059
    nap9055                  0.2103
    ars2013                  0.2103
    kab4035                  0.1977

  surgery_breast_implant_innovation:
    jas2037                  1.9498
    osc4001                  0.2494
    smukherj                 0.1145

  surgery_ophthalmic_surgical_outcomes:
    sjh2006                  0.6395
    kyk9011                  0.5768
    szk7001                  0.2797
    djd2003                  0.1772
    kcs2002                  0.1661

=== backfill_topic.py complete ===
  topic:           surgery_perioperative_medicine
  activity_count:  1019
  cluster_count:   30
  coverage_pct:    0.791
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_surgery_perioperative_medicine.json


-- stderr --
ailed for pid=gag2015: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
14:05:19 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=jel9064: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
14:05:20 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=jbg4001: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
14:05:25 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=sat9211: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
14:05:27 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=stk9005: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
14:05:28 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=rdayal: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
14:05:30 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=dal9209: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
14:05:33 INFO Writes: cleared=446, written=484, dry_run=False
14:05:33 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_surgery_perioperative_medicine.json
14:05:33 INFO [pass 3: aggregate] complete
14:05:33 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_surgery_perioperative_medicine.json

```
| health_equity_social_determinants | 963 | 14 | 26.3% | 3 |  |  | ok |

<!-- === health_equity_social_determinants === rc=0 -->
```
ealth_neighborhood_built_environment                          18.8075
health_sdoh_data_measurement                                   18.2667
health_medicaid_insurance_access                               15.9351
health_mental_health_disparities_policy                        13.7452
health_opioid_substance_use_equity                             12.3408
health_telehealth_access_disparities                            8.9111

faculty touched: 464
subtopic count: 14
total subtopic score sum (all): 429.1697

=== Top-5 faculty per subtopic ===

  health_racial_ethnic_disparities_outcomes:
    mms9024                  4.3674
    lcp2003                  2.8368
    alexisa                  2.4157
    lmk2003                  2.0537
    akg9010                  1.8817

  health_cancer_disparities_screening:
    rmt4001                  3.3810
    lan4002                  3.2358
    lcp2003                  2.9115
    kek4007                  2.2082
    baa2012                  2.0325

  health_global_lmic_disparities:
    ras9199                  5.0080
    jwp2001                  4.8070
    var4002                  4.7789
    liy9032                  4.4186
    dwf2001                  4.0942

  health_sdoh_cardiovascular_stroke:
    mms9024                  9.7130
    lcp2003                  6.8012
    mrs9012                  5.3266
    pag9051                  4.9380
    erp2001                  1.5692

  health_covid_racial_socioeconomic_disparities:
    rak2007                  2.3430
    yoz2009                  1.8719
    cjg7003                  1.7660
    mms9024                  1.7089
    khd9010                  1.5658

  health_maternal_reproductive_disparities:
    rsw9006                  4.8430
    sea2003                  4.2734
    syj7002                  2.6989
    coo9025                  1.1025
    chp4022                  0.8696

  health_sex_gender_disparities_cardiovascular:
    mfg9004                  1.9600
    jac9029                  1.2202
    luk9003                  1.2202
    rsw9006                  1.0645
    dnf9001                  1.0571

  health_workforce_diversity_equity:
    hey9002                  1.8084
    mar9462                  1.0017
    nil9053                  0.7028
    roh9005                  0.5304
    erp2001                  0.5186

  health_neighborhood_built_environment:
    yoz2009                  1.3590
    yux4008                  1.0929
    mms9024                  0.8762
    rtb2003                  0.8419
    khd9010                  0.7976

  health_sdoh_data_measurement:
    yip4002                  1.1018
    few2001                  1.0813
    yux4008                  0.9696
    khd9010                  0.9584
    thc2015                  0.9363

  health_medicaid_insurance_access:
    yub2003                  1.0174
    rur9017                  0.8895
    shk9078                  0.8466
    mms9024                  0.7869
    rsb2005                  0.7707

  health_mental_health_disparities_policy:
    emm4010                  2.3300
    yux4008                  2.2151
    sab2028                  0.8539
    haa2019                  0.6825
    juc4013                  0.6667

  health_opioid_substance_use_equity:
    shk9078                  2.3838
    smm2010                  1.9399
    brs2006                  1.7776
    alj4004                  1.5271
    markskr                  1.4227

  health_telehealth_access_disparities:
    jiy4002                  0.7517
    anr2783                  0.5905
    brd9088                  0.5206
    ama2006                  0.4833
    soc2005                  0.4833

=== backfill_topic.py complete ===
  topic:           health_equity_social_determinants
  activity_count:  963
  cluster_count:   14
  coverage_pct:    0.2627
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_health_equity_social_determinants.json


-- stderr --
r pid=dgh7001: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
14:13:48 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=geh9036: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
14:14:17 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=dam2034: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
14:14:48 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=lib9050: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
14:14:49 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=gstrong: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
14:14:57 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=efiguero: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
14:15:05 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=srm2001: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
14:15:20 INFO Writes: cleared=456, written=464, dry_run=False
14:15:20 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_health_equity_social_determinants.json
14:15:20 INFO [pass 3: aggregate] complete
14:15:21 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_health_equity_social_determinants.json

```
| neuroscience_neurology | 931 | 30 | 85.1% | 3 |  |  | ok |

<!-- === neuroscience_neurology === rc=0 -->
```
lndhlovu                 0.9971
    dic2009                  0.8153

  neuroscience_late_life_depression_psychiatry:
    fgd2002                  2.8069
    col2004                  2.3305
    liv3002                  2.1451
    nis2051                  1.8103
    cjl2007                  1.3749

  neuroscience_alzheimer_biomarkers:
    gcc9004                  1.8838
    tab2006                  1.7748
    yil4008                  1.6617
    liz2018                  1.2493
    shh4006                  1.1612

  neuroscience_movement_disorders_dbs:
    col2004                  1.3724
    has9059                  1.2960
    mik2002                  0.8897
    cjl2007                  0.8417
    ime4002                  0.8417

  neuroscience_headache_pain:
    lig4013                  3.7410
    mar9391                  1.4173
    sev9014                  0.6062
    all9188                  0.5573
    mod9040                  0.5568

  neuroscience_neurovascular_bbb:
    coi2001                  1.0993
    joa2006                  1.0993
    sua2018                  0.8554
    gif2004                  0.6352
    lig2021                  0.6352

  neuroscience_spinal_cord_spine:
    jpgreenf                 1.3628
    roh9005                  0.9579
    kdr9004                  0.9533
    ibh9004                  0.6192
    chl9077                  0.3841

  neuroscience_skull_base_surgery:
    anb2029                  1.6784
    ale2009                  1.5428
    pes2008                  0.8872
    jpgreenf                 0.7070
    ask9001                  0.2848

  neuroscience_neuroimmunology_ilc:
    cnp9004                  1.0311
    daa2028                  0.7910
    wez4002                  0.7488
    moa4006                  0.4438
    gfs2002                  0.3475

  neuroscience_cns_gene_therapy:
    dos2011                  0.6563
    rgcryst                  0.6563
    smkamins                 0.6563
    djb2001                  0.4109
    jpd2001                  0.4109

  neuroscience_meningioma_pet_mri:
    jai9018                  0.5560
    jro7001                  0.5343
    nak2032                  0.4862
    ror9068                  0.4645
    mir9146                  0.4645

  neuroscience_neurosteroids_reproductive:
    mjg2003                  1.0873
    tmilner                  1.0873
    gaw2001                  0.5694
    krp2013                  0.4401
    jag4016                  0.4401

  neuroscience_hiv_neurocognitive:
    lndhlovu                 0.7528
    tap4002                  0.3920
    evering                  0.2795
    cmd9008                  0.2782
    mag2005                  0.2782

  neuroscience_pediatric_brain_tumors:
    mmsouwei                 0.8384
    jpgreenf                 0.2099
    brm4007                  0.1955
    nad2639                  0.1955
    ceh2003                  0.1485

  neuroscience_delirium_pediatric_icu:
    chr9008                  0.6603
    lig2002                  0.4885
    man9026                  0.1578
    sut2006                  0.1254
    nds2001                  0.1254

  neuroscience_breast_reconstruction_reinnervation:
    lig4013                  0.7861
    dmo9004                  0.4257
    lec9030                  0.2816

  neuroscience_neurosurgery_education_technology:
    hab9075                  0.3148
    pes2008                  0.1976
    ale2009                  0.1976
    anb2029                  0.1976
    mmsouwei                 0.1962

  neuroscience_dysphagia_voice_disorders:
    yrj9003                  0.3185
    bas9049                  0.1573
    anr2783                  0.1486

=== backfill_topic.py complete ===
  topic:           neuroscience_neurology
  activity_count:  931
  cluster_count:   30
  coverage_pct:    0.8507
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_neuroscience_neurology.json


-- stderr --
in/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic neuroscience_neurology
14:24:39 INFO Querying DynamoDB partition: TOPIC#neuroscience_neurology
14:24:40 INFO Fetched 2263 SCORE# rows for neuroscience_neurology
14:24:40 INFO Aggregated: 2126 rows included, 137 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
14:24:40 INFO Faculty touched: 417
14:25:03 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=odb4002: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
14:25:08 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=amg4017: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
14:25:15 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=sac7008: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
14:25:45 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=jop4027: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
14:26:10 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=lmk9010: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
14:26:13 INFO Writes: cleared=412, written=417, dry_run=False
14:26:13 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_neuroscience_neurology.json
14:26:13 INFO [pass 3: aggregate] complete
14:26:13 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_neuroscience_neurology.json

```
| drug_discovery_pharmacology | 924 | 15 | 45.5% | 3 |  |  | ok |

<!-- === drug_discovery_pharmacology === rc=0 -->
```
                 6.9377
drug_drug_repurposing_computational_discovery                   5.2085
drug_dermatology_biologics_topical                              3.7773

faculty touched: 497
subtopic count: 15
total subtopic score sum (all): 350.0172

=== Top-5 faculty per subtopic ===

  drug_cancer_targeted_therapy_resistance:
    ole2001                  3.5036
    cns9006                  2.1948
    jmm9018                  1.9634
    lud2005                  1.8391
    ans2077                  1.8050

  drug_small_molecule_discovery_optimization:
    mog4005                  7.8703
    jobuck                   3.1890
    llevin                   3.1890
    ptm2001                  3.1449
    tre2003                  1.2032

  drug_hematologic_malignancy_treatment:
    ggi9001                  3.6118
    mrt2001                  2.5598
    lec2010                  2.4673
    gar2001                  2.3329
    cem2009                  1.8975

  drug_antimicrobial_target_identification:
    cnathan                  4.8303
    kyr9001                  3.4189
    gal2005                  3.3530
    lak9015                  2.5497
    ptm2001                  2.5148

  drug_neurodegeneration_cns_pharmacology:
    sus2044                  2.2991
    lig2033                  2.2850
    wel2009                  1.8458
    lif4001                  1.7526
    mog4005                  1.4656

  drug_immunotherapy_checkpoint_combinations:
    jdw2002                  2.6640
    mog4005                  1.8386
    roz4002                  1.6503
    tam2037                  1.6443
    sab4028                  1.1527

  drug_metabolic_disease_obesity_pharmacotherapy:
    ljaronne                 0.9635
    jis7016                  0.8179
    yuy2010                  0.7941
    jal2063                  0.7512
    ljgudas                  0.6308

  drug_covid19_antiviral_treatment:
    res2025                  2.4912
    mwm9004                  2.4844
    shc2034                  2.2926
    tre2003                  1.6407
    jzx2002                  1.4066

  drug_antibody_biologics_vaccine_development:
    sap4017                  3.7895
    gef4003                  1.2749
    pcw4001                  1.0588
    rbjones                  0.9988
    sic4001                  0.9523

  drug_gpcr_ion_channel_structural_pharmacology:
    xyhuang                  1.3645
    jtl2003                  1.2614
    kku4005                  1.0419
    crn2002                  0.7888
    khm2002                  0.5195

  drug_anesthesia_analgesic_cns_pharmacology:
    pag2014                  1.0551
    hchemmi                  0.8952
    fslee                    0.7606
    crn2002                  0.7402
    ala2022                  0.5015

  drug_gene_therapy_aav_delivery:
    rgcryst                  1.0763
    srj2003                  0.8748
    smkamins                 0.7769
    sbl2004                  0.7767
    szk7001                  0.7443

  drug_radiopharmaceutical_theranostics:
    jak2046                  1.4026
    ekf4001                  1.2375
    smc4002                  1.1610
    icm4001                  0.3455
    msb2006                  0.3137

  drug_drug_repurposing_computational_discovery:
    few2001                  1.5355
    chs4001                  0.7457
    ole2001                  0.7228
    dob2014                  0.3806
    chz4001                  0.3323

  drug_dermatology_biologics_topical:
    alexisa                  1.4402
    jhzippin                 0.5615
    src4005                  0.2405
    ole2001                  0.2357
    rdgranst                 0.2357

=== backfill_topic.py complete ===
  topic:           drug_discovery_pharmacology
  activity_count:  924
  cluster_count:   15
  coverage_pct:    0.4545
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_drug_discovery_pharmacology.json


-- stderr --
l is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
14:35:26 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
14:35:26 INFO [progress] 924/924 processed: assigned=775, unassigned=149, failed=0, elapsed=187s
14:35:26 INFO [pass 2: assign] complete
14:35:26 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic drug_discovery_pharmacology
14:35:26 INFO Querying DynamoDB partition: TOPIC#drug_discovery_pharmacology
14:35:27 INFO Fetched 1993 SCORE# rows for drug_discovery_pharmacology
14:35:27 INFO Aggregated: 1652 rows included, 341 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
14:35:27 INFO Faculty touched: 497
14:36:59 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=sap2015: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
14:36:59 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=hls9007: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
14:37:17 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=ams4007: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
14:37:17 INFO Writes: cleared=494, written=497, dry_run=False
14:37:17 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_drug_discovery_pharmacology.json
14:37:17 INFO [pass 3: aggregate] complete
14:37:18 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_drug_discovery_pharmacology.json

```
| infectious_disease_immunology | 923 | 15 | 39.6% | 3 |  |  | ok |

<!-- === infectious_disease_immunology === rc=0 -->
```
virus_vaccines                            12.0618
infectious_hiv_prevention_testing                              11.1346
infectious_fungal_gut_microbiome                                9.9192

faculty touched: 492
subtopic count: 15
total subtopic score sum (all): 567.4328

=== Top-5 faculty per subtopic ===

  infectious_covid19_clinical_outcomes:
    pag9051                  5.9655
    mms9024                  5.9245
    hsc2001                  3.5883
    lja2002                  3.5883
    ejs9005                  3.3582

  infectious_covid19_pathophysiology_organoids:
    res2025                  8.7469
    shc2034                  6.3637
    tre2003                  5.7112
    jzx2002                  5.2227
    dar2042                  2.5284

  infectious_sars_cov2_immunology_vaccines:
    hsc2001                 13.7659
    lja2002                 13.7659
    zhz9010                  3.4316
    srb9029                  2.6290
    mec2013                  2.4216

  infectious_tuberculosis_mycobacterium:
    kyr9001                  5.1912
    sae2004                  5.0354
    cnathan                  4.2068
    dis2003                  3.4285
    jwp2001                  1.9360

  infectious_antimicrobial_resistance_diagnostics:
    mjs9012                  5.3442
    law9067                  4.9876
    chm2042                  1.9544
    mec2013                  1.6142
    prv9013                  1.5835

  infectious_hiv_biology_treatment:
    rbjones                  4.9910
    sap4017                  4.5101
    lndhlovu                 3.1114
    gef4003                  2.8460
    gul4001                  2.7119

  infectious_sars_cov2_variants_epidemiology:
    hsc2001                  8.4814
    lja2002                  8.4814
    jom2042                  0.9884
    whr9001                  0.7926
    nah2005                  0.5948

  infectious_hiv_clinical_comorbidities:
    rnp2002                  4.0644
    lndhlovu                 3.1301
    myl2003                  2.7284
    mag2005                  2.4763
    cmd9008                  1.8267

  infectious_long_covid_post_acute:
    rak2007                  2.7415
    yoz2009                  2.7415
    few2001                  2.3187
    chz4001                  2.3187
    khd9010                  1.7401

  infectious_hepatitis_treatment_epidemiology:
    shk9078                  3.0488
    tak4011                  2.6276
    lja2002                  2.6195
    hsc2001                  2.2499
    markskr                  2.1693

  infectious_sexually_transmitted_infections:
    lja2002                  6.4176
    hsc2001                  3.7353
    jna2002                  0.6383
    sec4006                  0.4248
    gre9006                  0.3752

  infectious_malaria_parasitology:
    kwd2001                  2.9300
    lak9015                  2.8551
    gal2005                  2.5182
    cnathan                  1.5058
    ptm2001                  1.1001

  infectious_cytomegalovirus_vaccines:
    sap4017                  7.2497
    lig2002                  1.1711
    hut4001                  0.8716
    gef4003                  0.7810
    jub2029                  0.3858

  infectious_hiv_prevention_testing:
    ras9199                  1.9251
    dwf2001                  1.5302
    lir2020                  1.2720
    jwp2001                  0.8405
    var4002                  0.8405

  infectious_fungal_gut_microbiome:
    ili2001                  3.0245
    tak4005                  1.3165
    ral2006                  0.4563
    gfs2002                  0.4488
    dim2018                  0.3940

=== backfill_topic.py complete ===
  topic:           infectious_disease_immunology
  activity_count:  923
  cluster_count:   15
  coverage_pct:    0.3965
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_infectious_disease_immunology.json


-- stderr --
failed for pid=sbm4003: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
14:45:05 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=ngh9003: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
14:45:07 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=ald9111: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
14:45:12 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=dal9152: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
14:45:22 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=lsg2003: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
14:45:22 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=dbm9003: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
14:45:33 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=hhh9006: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
14:45:40 INFO Writes: cleared=480, written=492, dry_run=False
14:45:40 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_infectious_disease_immunology.json
14:45:40 INFO [pass 3: aggregate] complete
14:45:41 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_infectious_disease_immunology.json

```
| immunology_inflammation | 877 | 15 | 28.1% | 3 |  |  | ok |

<!-- === immunology_inflammation === rc=0 -->
```
_alloimmunity                             16.0772
immunology_b_cell_humoral_immunity                              9.9805
immunology_autoimmunity_autoreactivity                          9.9763

faculty touched: 465
subtopic count: 15
total subtopic score sum (all): 535.3145

=== Top-5 faculty per subtopic ===

  immunology_sars_cov2_immune_response:
    hsc2001                  5.7699
    lja2002                  5.7699
    zhz9010                  3.9749
    res2025                  3.3738
    srb9029                  3.0110

  immunology_tumor_immune_microenvironment:
    jdw2002                  7.8908
    tam2037                  5.7579
    sab4028                  4.5263
    ole2001                  4.3700
    szd3005                  3.5867

  immunology_neuroinflammation_neuroimmune:
    lig2033                  3.5704
    coi2001                  3.3909
    joa2006                  3.2248
    lif4001                  3.1672
    wel2009                  3.0853

  immunology_lymphoma_hematologic_immune:
    mrt2001                  4.5168
    achadbur                 3.3697
    ole2001                  3.2394
    ggi9001                  3.0153
    chm2042                  2.6707

  immunology_innate_lymphoid_mucosal_immunity:
    gfs2002                  5.7822
    daa2028                  3.8520
    wez4002                  3.4828
    maa4016                  2.8391
    mel4003                  2.5379

  immunology_gut_microbiome_immune_regulation:
    ili2001                  3.6613
    chg4001                  2.8077
    tak4005                  2.1832
    ral2006                  2.1633
    daa2028                  1.9631

  immunology_inflammatory_signaling_senescence:
    kas2049                  1.0577
    chm2042                  1.0118
    jal2063                  0.8972
    cem2009                  0.8147
    ole2001                  0.8000

  immunology_t_cell_biology_checkpoints:
    mog4005                  4.5940
    jdw2002                  1.5207
    roz4002                  1.3335
    jur2016                  1.2291
    tam2037                  1.2233

  immunology_hiv_immune_dysfunction:
    lndhlovu                 4.6737
    rbjones                  3.3518
    tap4002                  2.4394
    abd4001                  1.9350
    sap4017                  1.5130

  immunology_macrophage_myeloid_cell_biology:
    res2025                  1.4169
    tre2003                  1.1794
    hes2019                  0.9470
    sjc9006                  0.9470
    jzx2002                  0.8888

  immunology_vaccine_immunogenicity:
    sap4017                  7.5750
    gef4003                  2.0485
    pcw4001                  1.8100
    asn4002                  1.2049
    jpm2003                  1.0600

  immunology_cytokine_interferon_signaling:
    cnathan                  1.2634
    srj2003                  0.7979
    jom4010                  0.6424
    mtd4001                  0.6424
    ili2001                  0.6384

  immunology_transplant_alloimmunity:
    mut9002                  2.7309
    msuthan                  2.6591
    dmd2001                  2.5144
    sts9057                  1.0968
    ths9052                  1.0484

  immunology_b_cell_humoral_immunity:
    ole2001                  0.8565
    sap4017                  0.5718
    zuw4001                  0.5448
    emg4011                  0.5346
    cem2009                  0.4628

  immunology_autoimmunity_autoreactivity:
    las4011                  1.4793
    jig4003                  1.0040
    vip2021                  1.0040
    sic2011                  1.0040
    ccc4002                  0.8346

=== backfill_topic.py complete ===
  topic:           immunology_inflammation
  activity_count:  877
  cluster_count:   15
  coverage_pct:    0.2805
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_immunology_inflammation.json


-- stderr --
d=110s
14:51:11 INFO [progress] 750/877 processed: assigned=554, unassigned=196, failed=0, elapsed=117s
14:51:21 INFO [progress] 800/877 processed: assigned=604, unassigned=196, failed=0, elapsed=127s
14:51:32 INFO [progress] 850/877 processed: assigned=651, unassigned=199, failed=0, elapsed=138s
14:51:35 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
14:51:35 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
14:51:36 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
14:51:38 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
14:51:39 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
14:51:40 INFO [progress] 877/877 processed: assigned=677, unassigned=200, failed=0, elapsed=146s
14:51:40 INFO [pass 2: assign] complete
14:51:40 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic immunology_inflammation
14:51:40 INFO Querying DynamoDB partition: TOPIC#immunology_inflammation
14:51:41 INFO Fetched 2264 SCORE# rows for immunology_inflammation
14:51:41 INFO Aggregated: 1805 rows included, 459 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
14:51:41 INFO Faculty touched: 465
14:53:23 INFO Writes: cleared=465, written=465, dry_run=False
14:53:23 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_immunology_inflammation.json
14:53:24 INFO [pass 3: aggregate] complete
14:53:24 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_immunology_inflammation.json

```
| radiology_medical_imaging | 877 | 15 | 57.8% | 3 |  |  | ok |

<!-- === radiology_medical_imaging === rc=0 -->
```
iology_interventional_vascular                              16.7683
radiology_ct_imaging_applications                              16.2936
radiology_education_workforce                                  12.8588
radiology_radiation_therapy_oncology                           10.5559

faculty touched: 456
subtopic count: 15
total subtopic score sum (all): 461.0929

=== Top-5 faculty per subtopic ===

  radiology_mri_physics_quantitative:
    yiwang                  13.2421
    pas2018                 10.2414
    tdn2001                  8.2690
    ald2031                  4.0381
    map2008                  3.4540

  radiology_brain_mri_neuroimaging:
    tdn2001                  5.6768
    yiwang                   5.2953
    sag2015                  4.7912
    amk2012                  3.4240
    uwk9002                  3.0324

  radiology_cardiac_imaging:
    jik9027                  7.2986
    jww2001                  6.5411
    rbdevere                 4.3517
    lir9065                  2.3562
    mfg9004                  2.2098

  radiology_pet_nuclear_medicine:
    amg4017                  4.6601
    jro7001                  3.3128
    ekf4001                  3.1676
    nak2032                  3.1242
    jai9018                  3.0435

  radiology_alzheimer_neurodegeneration_pet_mri:
    gcc9004                  5.6295
    yil4008                  5.2990
    tab2006                  5.0680
    liz2018                  4.1603
    lig4005                  2.9371

  radiology_ai_deep_learning_imaging:
    ges9006                  6.0210
    yip4002                  5.5527
    map2008                  1.8380
    few2001                  1.8241
    yiwang                   1.2682

  radiology_neuro_spine_imaging:
    mmsouwei                 2.6025
    roh9005                  2.1606
    jpgreenf                 1.5275
    ibh9004                  1.4766
    mir9146                  1.3682

  radiology_breast_imaging:
    kad9090                  3.0311
    mid2011                  2.3757
    rmt4001                  2.2342
    jak9072                  1.6375
    all2017                  1.5002

  radiology_functional_mri_connectome:
    amk2012                  3.8351
    cjl2007                  2.3272
    col2004                  2.3020
    jdp9009                  1.7546
    fgd2002                  1.3562

  radiology_prostate_genitourinary:
    djm9016                  2.6070
    jch9011                  2.5275
    map2008                  2.4782
    tim9047                  1.1083
    baa2012                  1.0832

  radiology_ophthalmology_retinal:
    ram2045                  3.8654
    inp2002                  2.9913
    yip4002                  1.7521
    few2001                  1.4939
    kyk9011                  1.2461

  radiology_interventional_vascular:
    wfb9002                  0.9566
    adt9010                  0.8963
    ksl2001                  0.8963
    jrs9016                  0.8466
    bjm9002                  0.6438

  radiology_ct_imaging_applications:
    jgb9001                  1.6302
    acl9007                  1.3480
    ajp9012                  1.2998
    map2008                  1.2962
    srs9034                  0.8811

  radiology_education_workforce:
    lib9050                  2.0678
    rob9074                  0.7599
    kad9090                  0.7064
    rjm2002                  0.5793
    nil9053                  0.5508

  radiology_radiation_therapy_oncology:
    formenti                 1.9030
    kaz2004                  0.7405
    jon9024                  0.5016
    szd3005                  0.3669
    mlg2007                  0.3628

=== backfill_topic.py complete ===
  topic:           radiology_medical_imaging
  activity_count:  877
  cluster_count:   15
  coverage_pct:    0.5781
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_radiology_medical_imaging.json


-- stderr --
r_topic failed for pid=emb9053: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
15:06:15 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=frg9051: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
15:06:16 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=jjk9004: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
15:06:18 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=bjm9002: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
15:06:19 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=prg9018: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
15:06:19 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=spd9005: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
15:06:19 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=aaj4006: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
15:06:20 INFO Writes: cleared=429, written=456, dry_run=False
15:06:20 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_radiology_medical_imaging.json
15:06:20 INFO [pass 3: aggregate] complete
15:06:20 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_radiology_medical_imaging.json

```
| cardiovascular_disease | 844 | 28 | 65.3% | 3 |  |  | ok |

<!-- === cardiovascular_disease === rc=0 -->
```
.7365
    lngirard                 2.4366
    dxl9003                  1.7871

  cardiovascular_basic_cardiac_biology:
    and2039                  2.4901
    bek4011                  2.0486
    tre2003                  1.5489
    hol4006                  1.4187
    ole2001                  1.1582

  cardiovascular_stemi_pci_outcomes:
    bjr4002                  3.8317
    shc9182                  2.1548
    cha2022                  2.0710
    rharrington              1.5657
    zrm2001                  1.3480

  cardiovascular_sex_disparities_outcomes:
    jac9029                  1.1088
    luk9003                  1.1088
    dnf9001                  1.1088
    mfg9004                  1.0348
    rsw9006                  0.9482

  cardiovascular_cardio_oncology:
    mfg9004                  1.4088
    ban9003                  1.0165
    lngirard                 0.8127
    slmick                   0.8071
    hok9010                  0.6881

  cardiovascular_hiv_cardiovascular:
    rnp2002                  2.2488
    myl2003                  1.8915
    lndhlovu                 0.8360
    dwf2001                  0.7395
    rbdevere                 0.7395

  cardiovascular_diabetes_cardiometabolic:
    cha2022                  0.8887
    srk4008                  0.8505
    juz4004                  0.7655
    stp9039                  0.6532
    ram2045                  0.6367

  cardiovascular_pulmonary_embolism_vascular:
    rsz4001                  2.7715
    jbg4001                  0.9984
    mtd2002                  0.4251
    has4032                  0.2974
    rsw9004                  0.2732

  cardiovascular_long_covid_pasc:
    chz4001                  0.8215
    few2001                  0.8215
    rak2007                  0.8215
    yoz2009                  0.8215
    ejs9005                  0.7068

  cardiovascular_takotsubo_syndrome:
    bjr4002                  2.5645
    chl9077                  0.5346
    stp9039                  0.3099
    map2007                  0.3099
    alm9097                  0.2893

  cardiovascular_hypothalamic_hypertension_neurobiology:
    mjg2003                  1.6503
    tmilner                  1.6503
    gaw2001                  1.3375
    coi2001                  0.2535
    joa2006                  0.2535

  cardiovascular_heart_failure_home_care:
    mrs9012                  1.7380
    mms9024                  1.1600
    lmk2003                  1.0740
    pag9051                  0.4907
    sab2028                  0.4735

  cardiovascular_stroke_risk_factors_secondary_prevention:
    hok9010                  1.0714
    ban9003                  1.0618
    all9188                  0.7155
    haw9009                  0.6724
    alm9097                  0.3692

  cardiovascular_ckd_cardiorenal:
    myleswo                  2.6183
    mif4018                  0.6875
    map2008                  0.3582
    lct4001                  0.2828
    jik9027                  0.2226

  cardiovascular_heart_transplant_outcomes:
    lok9031                  1.4025
    rsz4001                  0.2653
    rsw9006                  0.2428
    horneve                  0.2428
    dtm9002                  0.2428

  cardiovascular_mitochondrial_ischemia_cardioprotection:
    bjr4002                  1.3516
    gim2004                  0.7944
    alg2057                  0.4247
    joa2006                  0.3887
    stp9039                  0.3055

  cardiovascular_rheumatoid_arthritis_cvd:
    yin9003                  1.3072
    mms9024                  0.7072
    mrs9012                  0.2113
    acl9007                  0.1976
    dag2017                  0.1976

=== backfill_topic.py complete ===
  topic:           cardiovascular_disease
  activity_count:  844
  cluster_count:   28
  coverage_pct:    0.6528
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_cardiovascular_disease.json


-- stderr --
800/844 processed: assigned=767, unassigned=33, failed=0, elapsed=126s
15:11:36 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
15:11:36 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
15:11:36 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
15:11:36 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
15:11:36 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
15:11:37 INFO [progress] 844/844 processed: assigned=811, unassigned=33, failed=0, elapsed=133s
15:11:37 INFO [pass 2: assign] complete
15:11:37 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic cardiovascular_disease
15:11:37 INFO Querying DynamoDB partition: TOPIC#cardiovascular_disease
15:11:38 INFO Fetched 2299 SCORE# rows for cardiovascular_disease
15:11:38 INFO Aggregated: 2232 rows included, 67 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
15:11:38 INFO Faculty touched: 410
15:13:11 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=ami4005: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
15:13:20 INFO Writes: cleared=409, written=410, dry_run=False
15:13:20 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_cardiovascular_disease.json
15:13:20 INFO [pass 3: aggregate] complete
15:13:20 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_cardiovascular_disease.json

```
| genetics_genomics_precision_medicine | 839 | 15 | 36.4% | 3 |  |  | ok |

<!-- === genetics_genomics_precision_medicine === rc=0 -->
```
     19.7431
genetics_spaceflight_omics                                     15.6842
genetics_gene_therapy_aav                                      14.1980
genetics_epigenetic_aging_clonal_hematopoiesis                  8.4885

faculty touched: 463
subtopic count: 15
total subtopic score sum (all): 463.8007

=== Top-5 faculty per subtopic ===

  genetics_cancer_genomics_somatic_mutations:
    ole2001                  7.2346
    jmm9018                  5.1306
    ans2077                  5.0627
    bmf9003                  2.6221
    emh9016                  1.9523

  genetics_lymphoma_leukemia_genomics:
    mrt2001                  4.4990
    chm2042                  3.6623
    ole2001                  3.4050
    cem2009                  3.3603
    ggi9001                  3.3063

  genetics_transcriptomics_rna_sequencing:
    jzx2002                  1.8523
    ole2001                  1.8183
    srj2003                  1.8072
    hut2006                  1.6949
    chm2042                  1.6018

  genetics_epigenomics_chromatin:
    efa2001                  2.7399
    app2006                  2.3393
    ole2001                  2.0122
    mas4011                  1.9028
    dal3005                  1.7446

  genetics_gwas_polygenic_risk:
    kas2049                  4.7956
    ole2001                  3.4176
    amh2025                  1.2830
    abb2013                  1.2480
    mer2005                  1.2358

  genetics_precision_oncology_biomarkers:
    ole2001                  3.2378
    jas9373                  1.3754
    jmm9018                  1.2492
    djp2002                  1.1609
    cns9006                  1.0071

  genetics_single_cell_multiomics:
    dal3005                  1.8209
    chz4007                  1.5083
    ole2001                  1.0937
    hut2006                  0.8877
    kas2049                  0.7546

  genetics_prostate_cancer_genomics:
    ans2077                  3.5915
    brr2006                  3.0063
    ole2001                  2.4331
    frk9007                  2.1079
    chb9074                  2.0636

  genetics_liquid_biopsy_ctdna:
    mac9795                  3.2728
    nkaltork                 1.9333
    jdw2002                  1.6359
    dal3005                  1.6359
    car4012                  1.4708

  genetics_neurological_disease_genomics:
    lig2033                  1.6568
    wel2009                  1.4299
    lif4001                  1.3325
    dic2009                  1.1337
    sus2044                  1.1073

  genetics_breast_cancer_genomics:
    mac9795                  1.9414
    lan4002                  1.7447
    rmt4001                  1.6670
    ole2001                  1.5449
    ans2077                  1.3077

  genetics_hereditary_cancer_genetic_counseling:
    mkf2002                  3.5281
    ras9030                  3.3140
    pac2001                  2.7435
    elc9120                  2.4998
    evc2005                  1.3077

  genetics_spaceflight_omics:
    chm2042                  3.7900
    cem2009                  2.6586
    irm2224                  2.0577
    dcl2001                  1.6434
    rdgranst                 1.0238

  genetics_gene_therapy_aav:
    rgcryst                  2.0555
    smkamins                 1.4256
    dos2011                  1.3277
    lud2005                  0.9227
    nhackett                 0.9158

  genetics_epigenetic_aging_clonal_hematopoiesis:
    lndhlovu                 2.1169
    res2025                  0.6349
    kmv4001                  0.5399
    jom2042                  0.4211
    amr2018                  0.4211

=== backfill_topic.py complete ===
  topic:           genetics_genomics_precision_medicine
  activity_count:  839
  cluster_count:   15
  coverage_pct:    0.3635
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_genetics_genomics_precision_medicine.json


-- stderr --
unassigned=199, failed=0, elapsed=127s
15:19:17 INFO [progress] 750/839 processed: assigned=550, unassigned=200, failed=0, elapsed=137s
15:19:26 INFO [progress] 800/839 processed: assigned=597, unassigned=203, failed=0, elapsed=146s
15:19:33 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
15:19:34 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
15:19:34 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
15:19:35 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
15:19:38 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
15:19:38 INFO [progress] 839/839 processed: assigned=635, unassigned=204, failed=0, elapsed=159s
15:19:38 INFO [pass 2: assign] complete
15:19:38 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic genetics_genomics_precision_medicine
15:19:39 INFO Querying DynamoDB partition: TOPIC#genetics_genomics_precision_medicine
15:19:39 INFO Fetched 2218 SCORE# rows for genetics_genomics_precision_medicine
15:19:39 INFO Aggregated: 1763 rows included, 455 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
15:19:39 INFO Faculty touched: 463
15:21:28 INFO Writes: cleared=463, written=463, dry_run=False
15:21:28 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_genetics_genomics_precision_medicine.json
15:21:28 INFO [pass 3: aggregate] complete
15:21:28 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_genetics_genomics_precision_medicine.json

```
| pathology_laboratory_medicine | 829 | 15 | 46.2% | 3 |  |  | ok |

<!-- === pathology_laboratory_medicine === rc=0 -->
```
51
pathology_corneal_confocal_neuropathy                          10.6044
pathology_breast_pathology_biomarkers                           9.2954
pathology_epigenomics_methylation                               3.1540

faculty touched: 508
subtopic count: 15
total subtopic score sum (all): 358.1627

=== Top-5 faculty per subtopic ===

  pathology_covid19_sars_pathology:
    mec2013                  4.3734
    law9067                  3.3838
    srb9029                  2.9558
    achadbur                 2.7533
    hey9012                  2.6723

  pathology_molecular_genomic_cancer:
    ole2001                  5.4569
    jmm9018                  3.6702
    ans2077                  3.6497
    emh9016                  2.0606
    joj9034                  1.6589

  pathology_clinical_laboratory_assays:
    zhz9010                  1.4938
    law9067                  1.1724
    hey9012                  1.0732
    mec2013                  1.0283
    mjs9012                  0.8688

  pathology_cytopathology_histopathology:
    mos9084                  3.6475
    abg9017                  3.1049
    nip9020                  2.1128
    jjh7002                  1.3492
    brr2006                  1.3356

  pathology_infectious_disease_microbiology_lab:
    law9067                  2.8921
    mjs9012                  2.6572
    gul4001                  1.0100
    myl2003                  0.9583
    mss9008                  0.8619

  pathology_liquid_biopsy_ctdna:
    mac9795                  3.0957
    car4012                  1.6053
    nkaltork                 1.4682
    dal3005                  1.1708
    jdw2002                  1.1708

  pathology_hematopathology_myeloid:
    ggi9001                  1.7434
    achadbur                 0.9180
    ole2001                  0.9054
    lec2010                  0.8653
    sap9151                  0.8610

  pathology_digital_computational_pathology:
    rud4004                  2.5271
    ole2001                  1.3806
    lum4003                  0.8889
    mal4005                  0.8889
    rmt4001                  0.8439

  pathology_prostate_cancer_pathology:
    jch9011                  1.4411
    ans2077                  1.3647
    mal4005                  1.3581
    chb9074                  1.3180
    brr2006                  1.2046

  pathology_transplant_allograft:
    msuthan                  3.4546
    dmd2001                  3.4512
    mut9002                  3.0692
    ths9052                  1.3474
    sts9057                  1.1486

  pathology_thyroid_endocrine_pathology:
    tjfahey                  2.2755
    raz2002                  1.8818
    bmf9002                  1.7419
    ths9004                  1.4690
    jjh7002                  0.9600

  pathology_extracellular_vesicles_biomarkers:
    irm2224                  1.2843
    haz2005                  1.1631
    dcl2001                  1.1250
    sel4002                  1.0675
    vip2021                  0.9463

  pathology_corneal_confocal_neuropathy:
    ram2045                  3.3589
    inp2002                  2.4084
    tdn2001                  0.6988
    yiwang                   0.6988
    ald2031                  0.6988

  pathology_breast_pathology_biomarkers:
    rmt4001                  1.9510
    bab4001                  1.0929
    lac4029                  0.6211
    lum4003                  0.4345
    tai9015                  0.3281

  pathology_epigenomics_methylation:
    barany                   0.6852
    mdb2005                  0.6852
    chm2042                  0.3171
    pvn4001                  0.3134
    aswistel                 0.2494

=== backfill_topic.py complete ===
  topic:           pathology_laboratory_medicine
  activity_count:  829
  cluster_count:   15
  coverage_pct:    0.462
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_pathology_laboratory_medicine.json


-- stderr --
pbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic pathology_laboratory_medicine
15:27:36 INFO Querying DynamoDB partition: TOPIC#pathology_laboratory_medicine
15:27:37 INFO Fetched 2231 SCORE# rows for pathology_laboratory_medicine
15:27:37 INFO Aggregated: 1881 rows included, 350 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
15:27:37 INFO Faculty touched: 508
15:28:06 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=vxm9002: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
15:28:20 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=jod2009: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
15:28:27 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=gip4011: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
15:29:21 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=dabehrm: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
15:29:24 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=evf2010: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
15:29:33 INFO Writes: cleared=503, written=508, dry_run=False
15:29:33 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_pathology_laboratory_medicine.json
15:29:33 INFO [pass 3: aggregate] complete
15:29:33 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_pathology_laboratory_medicine.json

```
| global_public_health | 811 | 30 | 57.7% | 3 |  |  | ok |

<!-- === global_public_health === rc=0 -->
```
    0.7991
    euc4006                  0.5685
    est2003                  0.4629
    vjb9003                  0.4507
    rmt4001                  0.3996

  global_covid19_population_immunity_modeling:
    lja2002                  2.7580
    hsc2001                  2.7580
    few2001                  0.5808
    mis4060                  0.4206
    nah2005                  0.3727

  global_ncd_qatar_gulf_region:
    soc2005                  1.4292
    kac2047                  1.1418
    ama2006                  1.0799
    zrm2001                  0.8774
    lja2002                  0.7137

  global_elder_abuse_aging:
    esc4003                  1.7009
    aer2006                  1.3801
    mslachs                  1.0031
    sjc7004                  0.6416
    dwh4001                  0.6241

  global_suicide_mental_health_epidemiology:
    yux4008                  2.3905
    yip4002                  0.8398
    tdb2002                  0.5035
    haa2019                  0.5001
    sab2028                  0.3209

  global_tuberculosis_diagnosis_treatment:
    jwp2001                  0.8438
    kfw2001                  0.8438
    jsm9009                  0.8179
    var4002                  0.7122
    dwf2001                  0.6500

  global_covid19_mental_health_psychosocial:
    yux4008                  0.6995
    emm4010                  0.6455
    juc4013                  0.5022
    jur9123                  0.3486
    cmg9004                  0.2855

  global_reproductive_abortion_policy:
    jna2002                  2.0078
    myl2003                  0.7218
    ras9199                  0.6392
    cen2004                  0.4803
    amg4013                  0.2741

  global_long_covid_post_acute_sequelae:
    yoz2009                  0.7909
    rak2007                  0.7909
    few2001                  0.5898
    chz4001                  0.5898
    khd9010                  0.4008

  global_covid19_diagnostics_testing:
    lja2002                  0.7460
    hsc2001                  0.7460
    chm2042                  0.6545
    prv9013                  0.4122
    mwm9004                  0.3634

  global_heat_climate_health:
    jur9123                  1.6977
    akg9010                  0.9261
    mrd2006                  0.3976
    mms9024                  0.2728
    jsirey                   0.1415

  global_antimicrobial_resistance_gram_negative:
    mjs9012                  0.7540
    chm2042                  0.4357
    cem2009                  0.4357
    imh2003                  0.4357
    sdp4001                  0.2229

  global_covid19_pediatric_outcomes:
    jih9033                  0.3263
    kpa9002                  0.3263
    lja2002                  0.2863
    hsc2001                  0.2863
    aip9008                  0.1745

  global_hiv_perinatal_transmission_bnab:
    sap4017                  0.8712
    gef4003                  0.5399
    rig4007                  0.2094
    ymd9002                  0.2093
    asn4002                  0.1271

  global_covid19_treatment_therapeutics:
    lja2002                  0.4383
    hsc2001                  0.4383
    rgulick                  0.2677
    mwm9004                  0.2209
    evering                  0.1878

  global_malaria_antimicrobial_drug_resistance:
    lak9015                  0.3994
    gal2005                  0.3363
    kaz4001                  0.2702
    cnathan                  0.2216
    als2026                  0.1659

  global_home_health_workforce_longterm_care:
    mrs9012                  1.0666
    rtb2003                  0.1098
    mms9024                  0.0826
    erp2001                  0.0826

=== backfill_topic.py complete ===
  topic:           global_public_health
  activity_count:  811
  cluster_count:   30
  coverage_pct:    0.5771
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_global_public_health.json


-- stderr --
ction pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
15:35:25 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
15:35:25 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
15:35:25 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
15:35:25 INFO [progress] 811/811 processed: assigned=777, unassigned=34, failed=0, elapsed=148s
15:35:25 INFO [pass 2: assign] complete
15:35:25 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic global_public_health
15:35:26 INFO Querying DynamoDB partition: TOPIC#global_public_health
15:35:27 INFO Fetched 1640 SCORE# rows for global_public_health
15:35:27 INFO Aggregated: 1577 rows included, 63 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
15:35:27 INFO Faculty touched: 371
15:36:11 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=dea2006: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
15:36:11 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=tha2002: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
15:36:48 INFO Writes: cleared=369, written=371, dry_run=False
15:36:48 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_global_public_health.json
15:36:48 INFO [pass 3: aggregate] complete
15:36:49 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_global_public_health.json

```
| cancer_biology_general | 638 | 30 | 72.4% | 3 |  |  | ok |

<!-- === cancer_biology_general === rc=0 -->
```
               0.4674

  cancer_breast_cancer_molecular_therapy:
    ole2001                  0.6507
    lum4003                  0.6404
    mac9795                  0.6307
    tai9015                  0.6241
    lan4002                  0.4794

  cancer_brain_tumor_glioma:
    haf9016                  1.2264
    djp2002                  0.8925
    ole2001                  0.7005
    ris2020                  0.6845
    smc2011                  0.6845

  cancer_melanoma_skin_cancer:
    jdw2002                  1.0201
    tam2037                  0.6262
    sab4028                  0.6262
    dah4023                  0.4733
    dar2042                  0.4733

  cancer_targeted_therapy_resistance:
    lud2005                  0.9512
    ole2001                  0.7797
    mas9313                  0.7109
    pag2015                  0.7109
    ekk2003                  0.4575

  cancer_cancer_associated_thrombosis_stroke:
    ban9003                  0.8789
    ahs9018                  0.5072
    stt2007                  0.5072
    ajo9001                  0.5072
    hok9010                  0.4941

  cancer_thyroid_cancer_diagnosis_treatment:
    tjfahey                  1.2870
    raz2002                  1.0894
    bmf9002                  0.9998
    ths9004                  0.4695
    bab4001                  0.2703

  cancer_nanoparticle_drug_delivery:
    smc4002                  0.7566
    ekf4001                  0.7520
    sel2013                  0.3493
    sbl2004                  0.3370
    ajo9001                  0.3263

  cancer_gastroesophageal_cancer_therapy:
    mas9313                  1.2080
    ajo9001                  0.2628
    rum9028                  0.2358
    emh9016                  0.1340
    ecp2002                  0.1340

  cancer_small_molecule_drug_discovery:
    mog4005                  1.9316
    jag4016                  0.3456
    ole2001                  0.3338
    jmm9018                  0.3338
    beh2020                  0.3338

  cancer_genitourinary_cancer_therapy:
    cns9006                  1.3451
    stt2007                  0.3790
    rkj4003                  0.3137
    ole2001                  0.2544
    bmf9003                  0.2544

  cancer_cell_cycle_proliferation:
    tom4003                  2.0322
    bpe9002                  0.2874
    nkaltork                 0.2496
    pez2001                  0.2496
    brm4007                  0.1705

  cancer_clonal_hematopoiesis_genomic_evolution:
    dal3005                  0.3888
    shn9035                  0.3888
    ths9004                  0.3122
    ljgudas                  0.3122
    mam2185                  0.3122

  cancer_ai_computational_pathology:
    ole2001                  0.8382
    lum4003                  0.6529
    jmm9018                  0.3510
    mal4005                  0.3437
    als2076                  0.2113

  cancer_breast_cancer_risk_epidemiology:
    rmt4001                  1.3520
    oaz4001                  0.2751
    lac4029                  0.2120
    bab4001                  0.1789
    jis2015                  0.1412

  cancer_hematologic_malignancy_therapy:
    gar2001                  0.8875
    pid9006                  0.3084
    ygg9005                  0.2960
    joa9069                  0.2487
    gal2005                  0.1777

  cancer_imaging_mri_pet:
    sgk4001                  0.6760
    jak2046                  0.3453
    sel2013                  0.2753
    jdw2002                  0.1777
    jik9027                  0.1536

  cancer_tumor_biology_microenvironment_mechanisms:
    asl4003                  0.6301
    dop9054                  0.1793

=== backfill_topic.py complete ===
  topic:           cancer_biology_general
  activity_count:  638
  cluster_count:   30
  coverage_pct:    0.7241
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_cancer_biology_general.json


-- stderr --
led=0, elapsed=73s
15:41:19 INFO [progress] 500/638 processed: assigned=467, unassigned=33, failed=0, elapsed=83s
15:41:28 INFO [progress] 550/638 processed: assigned=517, unassigned=33, failed=0, elapsed=92s
15:41:36 INFO [progress] 600/638 processed: assigned=567, unassigned=33, failed=0, elapsed=100s
15:41:42 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
15:41:42 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
15:41:42 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
15:41:42 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
15:41:42 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
15:41:43 INFO [progress] 638/638 processed: assigned=604, unassigned=34, failed=0, elapsed=107s
15:41:43 INFO [pass 2: assign] complete
15:41:43 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic cancer_biology_general
15:41:43 INFO Querying DynamoDB partition: TOPIC#cancer_biology_general
15:41:44 INFO Fetched 1683 SCORE# rows for cancer_biology_general
15:41:44 INFO Aggregated: 1603 rows included, 80 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
15:41:44 INFO Faculty touched: 368
15:43:03 INFO Writes: cleared=368, written=368, dry_run=False
15:43:03 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_cancer_biology_general.json
15:43:03 INFO [pass 3: aggregate] complete
15:43:03 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_cancer_biology_general.json

```
| biochemistry_biophysics | 620 | 30 | 71.8% | 3 |  |  | ok |

<!-- === biochemistry_biophysics === rc=0 -->
```
in                 0.8656
    qic2005                  0.7607

  biochemistry_dna_replication_repair_telomeres:
    nflue                    1.3289
    wkhollo                  1.0399
    euy2001                  0.7555
    jet2021                  0.6554
    jeg2039                  0.5451

  biochemistry_lipid_metabolism_cancer:
    mal4005                  0.9204
    qic2005                  0.5865
    ggi9001                  0.4758
    hup4002                  0.3995
    jur2016                  0.3607

  biochemistry_rna_modifications_m6a:
    srj2003                  3.5640
    slr4003                  0.5544
    shm2662                  0.3463
    res2025                  0.3014
    lec2010                  0.2744

  biochemistry_synaptic_vesicle_neurotransmission:
    dae2005                  1.3358
    jab2058                  1.2119
    mas2189                  1.2119
    jed2019                  0.9257
    vig9070                  0.7136

  biochemistry_glucose_transporter_metabolism_disease:
    temcgraw                 0.5549
    jup9003                  0.3899
    vij4004                  0.3899
    jobuck                   0.3489
    llevin                   0.3489

  biochemistry_extracellular_vesicles_cancer:
    haz2005                  0.7456
    dcl2001                  0.6803
    irm2224                  0.6803
    res2025                  0.5242
    sel4002                  0.4291

  biochemistry_innate_immunity_inflammasome_signaling:
    hes2019                  0.3767
    sjc9006                  0.3767
    jom4010                  0.2542
    jul4008                  0.2542
    mdu4003                  0.2542

  biochemistry_cell_migration_actin_cytoskeleton:
    tom4003                  1.1667
    slr4003                  0.5306
    ale4009                  0.5306
    mer2005                  0.2103
    stk2005                  0.2103

  biochemistry_sars_cov2_antibody_antiviral:
    pcw4001                  0.4196
    sic4001                  0.4196
    res2025                  0.4107
    shc2034                  0.4107
    srj2003                  0.3694

  biochemistry_cell_cycle_cdk_regulation:
    tom4003                  0.9441
    ole2001                  0.1631
    dsr2005                  0.1631
    jmm9018                  0.1631
    frk9007                  0.1631

  biochemistry_hiv_antibody_vaccine_immunology:
    jpm2003                  1.1282
    pek2003                  1.1282
    sap4017                  0.2521
    ggi9001                  0.1442
    pcw4001                  0.1442

  biochemistry_malaria_parasite_epigenetics:
    kwd2001                  0.7457
    kyr9001                  0.6821
    lak9015                  0.4046
    gal2005                  0.4046
    cnathan                  0.2756

  biochemistry_adipocyte_biology_lipid_metabolism:
    frs4001                  0.9173
    nam2016                  0.4200
    rac2017                  0.3393
    smr4005                  0.2743
    loc2008                  0.2437

  biochemistry_enac_kidney_sodium_transport:
    lgpalm                   0.9811
    jms2003                  0.1977
    trk2002                  0.1727

  biochemistry_nanoparticle_theranostics_drug_delivery:
    jak2046                  0.4002
    res2025                  0.1930
    msb2006                  0.1709
    sbl2004                  0.1492
    vab2008                  0.1492

  biochemistry_influenza_antibody_immunity:
    pcw4001                  0.6739
    sic4001                  0.4050

  biochemistry_cytomegalovirus_antibody_vaccine:
    sap4017                  0.8306
    lig2002                  0.1155

=== backfill_topic.py complete ===
  topic:           biochemistry_biophysics
  activity_count:  620
  cluster_count:   30
  coverage_pct:    0.7177
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_biochemistry_biophysics.json


-- stderr --
 elapsed=79s
15:47:49 INFO [progress] 500/620 processed: assigned=439, unassigned=61, failed=0, elapsed=88s
15:47:56 INFO [progress] 550/620 processed: assigned=489, unassigned=61, failed=0, elapsed=96s
15:48:05 INFO [progress] 600/620 processed: assigned=539, unassigned=61, failed=0, elapsed=105s
15:48:09 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
15:48:09 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
15:48:09 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
15:48:10 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
15:48:10 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
15:48:10 INFO [progress] 620/620 processed: assigned=559, unassigned=61, failed=0, elapsed=110s
15:48:10 INFO [pass 2: assign] complete
15:48:10 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic biochemistry_biophysics
15:48:10 INFO Querying DynamoDB partition: TOPIC#biochemistry_biophysics
15:48:11 INFO Fetched 1266 SCORE# rows for biochemistry_biophysics
15:48:11 INFO Aggregated: 1145 rows included, 121 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
15:48:11 INFO Faculty touched: 325
15:49:20 INFO Writes: cleared=325, written=325, dry_run=False
15:49:20 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_biochemistry_biophysics.json
15:49:20 INFO [pass 3: aggregate] complete
15:49:21 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_biochemistry_biophysics.json

```
| womens_health_reproductive_medicine | 563 | 28 | 84.4% | 3 |  |  | ok |

<!-- === womens_health_reproductive_medicine === rc=0 -->
```
2014                  0.5842
    roj9069                  0.3866
    alh9039                  0.3478

  womens_sex_differences_neurobiology:
    krp2013                  0.7050
    tmilner                  0.5240
    lif4001                  0.3017
    lig2033                  0.3017
    sus2044                  0.3017

  womens_pregnancy_covid_outcomes:
    hlipkind                 0.5109
    lar9110                  0.4618
    rbk9001                  0.3626
    dwskupsk                 0.3626
    coo9025                  0.3626

  womens_breast_cancer_treatment:
    mac9795                  1.2796
    car4012                  0.4803
    shr4009                  0.4582
    ves4007                  0.3781
    szd3005                  0.3566

  womens_sex_differences_surgical_vascular:
    mfg9004                  0.5045
    jrs9016                  0.4676
    mms9024                  0.2728
    mecharl                  0.2728
    jac9029                  0.2664

  womens_breast_cancer_survivorship_comorbidity:
    shr4009                  1.0761
    rmt4001                  0.6600
    ves4007                  0.4774
    eot9002                  0.3146
    lan4002                  0.2566

  womens_pregnancy_autoimmune_comorbidities:
    dls7001                  0.5375
    tak4011                  0.4893
    mtd2002                  0.4091
    aia9015                  0.3936
    nun9005                  0.3557

  womens_pregnancy_infectious_disease:
    tak4011                  1.4765
    jsm9009                  0.6485
    sap4017                  0.3645
    var4002                  0.2021
    emm4010                  0.1926

  womens_breast_reconstruction_surgery:
    dmo9004                  1.0265
    jas2037                  0.7258
    rmt4001                  0.3430
    vjb9003                  0.3430
    lan4002                  0.2327

  womens_hsv_sti_epidemiology:
    lja2002                  0.9699
    hsc2001                  0.6933
    jna2002                  0.5773
    dwf2001                  0.5100
    myl2003                  0.3025

  womens_tnbc_racial_disparities:
    lan4002                  0.8861
    ole2001                  0.3870
    ans2077                  0.3870
    esc9016                  0.3209
    aswistel                 0.2531

  womens_cervical_hpv_screening:
    abg9017                  0.9059
    jjh7002                  0.2716
    mos9084                  0.2716
    gre9006                  0.2607
    hlipkind                 0.2554

  womens_adolescent_reproductive_health:
    jac4017                  0.6767
    saf7007                  0.6744
    hak2012                  0.1442
    zag9005                  0.1388
    sharkom                  0.1371

  womens_gestational_diabetes:
    puc9005                  0.5777
    jsm9009                  0.4817
    mos7003                  0.2341
    mrd2006                  0.2283
    yiz2014                  0.2067

  womens_pregnancy_substance_use:
    anm4001                  0.3189
    tmilner                  0.1596
    fslee                    0.1596
    alj4004                  0.1346
    shk9078                  0.1346

  womens_pregnancy_penicillin_allergy:
    mos7003                  0.9654

  womens_maternal_cytomegalovirus:
    sap4017                  0.4283
    gef4003                  0.1271
    hut4001                  0.1098
    lig2002                  0.0743

  womens_breast_imaging_radiology:
    kad9090                  0.1395
    mjc9030                  0.1021
    jak9072                  0.0675
    lop9006                  0.0675
    shb9167                  0.0675

=== backfill_topic.py complete ===
  topic:           womens_health_reproductive_medicine
  activity_count:  563
  cluster_count:   28
  coverage_pct:    0.8437
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_womens_health_reproductive_medicine.json


-- stderr --
r pid=pakchu: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
15:54:29 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=ikligman: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
15:54:29 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=alm2036: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
15:54:30 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=rnt2001: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
15:54:30 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=chz2001: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
15:54:31 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=kpxu: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
15:54:31 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=moi9010: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
15:54:31 INFO Writes: cleared=389, written=399, dry_run=False
15:54:31 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_womens_health_reproductive_medicine.json
15:54:31 INFO [pass 3: aggregate] complete
15:54:31 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_womens_health_reproductive_medicine.json

```
| biomedical_informatics | 548 | 28 | 73.9% | 3 |  |  | ok |

<!-- === biomedical_informatics === rc=0 -->
```
2001                  1.0134
    chm2042                  0.3384
    gar2001                  0.3384

  biomedical_single_cell_spatial_omics:
    kas2049                  0.5275
    jak2043                  0.5275
    amh2025                  0.4351
    pcw4001                  0.3696
    sic4001                  0.3696

  biomedical_clinical_decision_support_ai:
    khd9010                  0.4096
    aaa4027                  0.3621
    mac9232                  0.3621
    djs2001                  0.3384
    yub2003                  0.3297

  biomedical_wearable_mhealth_digital_monitoring:
    aaa4027                  2.1935
    ara4013                  2.0140
    jis2011                  1.8073
    sab2028                  0.4614
    yux4008                  0.1849

  biomedical_radiology_imaging_ml_diagnosis:
    ges9006                  1.4077
    yip4002                  1.1770
    ara4013                  0.3288
    few2001                  0.2785
    map2008                  0.2739

  biomedical_liquid_biopsy_ctdna_cancer:
    dal3005                  0.6548
    jdw2002                  0.6548
    nkaltork                 0.6548
    mac9795                  0.5871
    ahs9018                  0.5319

  biomedical_ai_fairness_bias_equity:
    few2001                  1.0520
    yip4002                  1.0520
    juz4004                  0.8971
    ges9006                  0.7149
    alh4014                  0.4894

  biomedical_ai_ophthalmology_retinal:
    yip4002                  2.0444
    few2001                  1.7527
    sjh2006                  1.1663
    kyk9011                  0.4406
    cro9004                  0.3148

  biomedical_computational_pathology_histology:
    rud4004                  1.2449
    mal4005                  0.5853
    lum4003                  0.5853
    ole2001                  0.3807
    few2001                  0.3486

  biomedical_neuroimaging_brain_connectivity:
    amk2012                  1.8179
    qiz4006                  1.0158
    shh4006                  0.5182
    rmt4003                  0.3826
    qrr4001                  0.3326

  biomedical_telehealth_digital_patient_engagement:
    aaa4027                  0.7080
    jtg9003                  0.3284
    jee9054                  0.3284
    sharkom                  0.3284
    jac9009                  0.3284

  biomedical_health_informatics_infrastructure:
    mau2006                  0.8025
    ars2013                  0.6259
    arj2005                  0.4229
    jim2012                  0.3913
    thc2015                  0.3535

  biomedical_cancer_genomics_somatic_evolution:
    ole2001                  1.1188
    jmm9018                  0.6943
    ans2077                  0.5029
    dal3005                  0.4236
    dob2014                  0.2582

  biomedical_ngs_sequencing_methods:
    hut2006                  1.0007
    imh2003                  0.7591
    chm2042                  0.7208
    mut9002                  0.2894
    lec2010                  0.2726

  biomedical_ai_laryngology_voice:
    anr2783                  2.1292
    ole2001                  1.0685
    lus2005                  0.8749

  biomedical_federated_learning_data_privacy:
    few2001                  1.8673
    chs4001                  0.5405
    akg9010                  0.4820
    chz4001                  0.4575
    ara4013                  0.2183

  biomedical_elder_mistreatment_informatics:
    aer2006                  0.4755
    mslachs                  0.3263
    yiz2014                  0.2053
    yub2003                  0.2053

  biomedical_ml_surgical_outcomes:
    dmo9004                  0.6776

=== backfill_topic.py complete ===
  topic:           biomedical_informatics
  activity_count:  548
  cluster_count:   28
  coverage_pct:    0.7391
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_biomedical_informatics.json


-- stderr --
l is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
15:58:34 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
15:58:34 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
15:58:35 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
15:58:35 INFO [progress] 548/548 processed: assigned=530, unassigned=18, failed=0, elapsed=88s
15:58:35 INFO [pass 2: assign] complete
15:58:35 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic biomedical_informatics
15:58:36 INFO Querying DynamoDB partition: TOPIC#biomedical_informatics
15:58:36 INFO Fetched 1219 SCORE# rows for biomedical_informatics
15:58:36 INFO Aggregated: 1183 rows included, 36 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
15:58:36 INFO Faculty touched: 360
15:58:53 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=cestarr: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
15:59:51 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=paa2013: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
15:59:54 INFO Writes: cleared=358, written=360, dry_run=False
15:59:54 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_biomedical_informatics.json
15:59:54 INFO [pass 3: aggregate] complete
15:59:55 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_biomedical_informatics.json

```
| metabolic_endocrine_disease | 540 | 30 | 81.8% | 3 |  |  | ok |

<!-- === metabolic_endocrine_disease === rc=0 -->
```
fahey                  1.8000
    cha2022                  0.3222
    stp9039                  0.2027

  metabolic_diabetes_cancer_comorbidity:
    lcp2003                  2.3128
    mms9024                  2.0902
    lmk2003                  1.8132
    rmt4001                  0.7565
    est2003                  0.3972

  metabolic_sphingolipid_cholesterol_metabolism:
    and2039                  1.0703
    bae2008                  0.6604
    ole2001                  0.6298
    rharrington              0.5500
    lbm7002                  0.4448

  metabolic_glut1_nad_energy_metabolism:
    temcgraw                 1.0463
    jup9003                  1.0322
    vij4004                  0.7258
    yuy2010                  0.5873
    gim2004                  0.5570

  metabolic_cardiovascular_risk_disparities:
    mms9024                  0.9016
    jwp2001                  0.5330
    var4002                  0.5330
    rnp2002                  0.4757
    dwf2001                  0.4371

  metabolic_obesity_comorbidities_clinical:
    mac9232                  0.3262
    erp2001                  0.2873
    rsb2005                  0.2771
    keb9155                  0.2756
    cha2022                  0.2748

  metabolic_gut_microbiome_metabolism:
    moa4006                  1.1126
    chg4001                  0.7864
    daa2028                  0.7864
    ili2001                  0.6178
    srk4008                  0.4297

  metabolic_pregnancy_metabolic_outcomes:
    puc9005                  0.8083
    mos7003                  0.6707
    jsm9009                  0.5860
    mrd2006                  0.3718
    hlipkind                 0.3485

  metabolic_diabetes_microvascular_complications:
    ram2045                  1.2664
    inp2002                  1.0458
    zrm2001                  0.7847
    rnp2002                  0.4961
    cha2022                  0.2786

  metabolic_peer_support_diabetes_selfmanagement:
    mms9024                  1.9774
    dil9071                  0.3571
    mrs9012                  0.3334
    acr2213                  0.3334
    ank9177                  0.3334

  metabolic_alzheimers_metabolic_links:
    jak2043                  0.9555
    kas2049                  0.5386
    srk4008                  0.2650
    amh2025                  0.2422
    ldravdin                 0.2217

  metabolic_diabetes_wearable_digital:
    aaa4027                  0.6943
    ara4013                  0.6943
    jis2011                  0.6943
    ram2045                  0.5076

  metabolic_hiv_cardiometabolic:
    rnp2002                  0.5023
    lndhlovu                 0.4091
    mag2005                  0.2988
    jsm9009                  0.2617
    puc9005                  0.2617

  metabolic_diabetes_healthcare_utilization:
    liy9032                  0.7190
    djl9010                  0.3262
    lmk2003                  0.2865
    lct4001                  0.2090
    mms9024                  0.1767

  metabolic_fgf23_vitamin_d_mineral:
    myleswo                  0.5464
    mif4018                  0.3143
    lgpalm                   0.2783
    tac2007                  0.2229
    ceh2003                  0.0480

  metabolic_hyperlipidemia_rheumatoid_arthritis:
    mms9024                  0.2677
    yin9003                  0.2677
    tac2007                  0.1122
    say4011                  0.1024

  metabolic_obesity_brain_neurological:
    lig4005                  0.2809
    haw4011                  0.1976
    tab2006                  0.1356
    yil4008                  0.1356

  metabolic_glucagon_receptor_signaling:
    kku4005                  0.4619

=== backfill_topic.py complete ===
  topic:           metabolic_endocrine_disease
  activity_count:  540
  cluster_count:   30
  coverage_pct:    0.8185
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_metabolic_endocrine_disease.json


-- stderr --
3:20 INFO [progress] 400/540 processed: assigned=372, unassigned=28, failed=0, elapsed=61s
16:03:28 INFO [progress] 450/540 processed: assigned=422, unassigned=28, failed=0, elapsed=69s
16:03:37 INFO [progress] 500/540 processed: assigned=472, unassigned=28, failed=0, elapsed=78s
16:03:42 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
16:03:42 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
16:03:42 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
16:03:42 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
16:03:44 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
16:03:44 INFO [progress] 540/540 processed: assigned=512, unassigned=28, failed=0, elapsed=84s
16:03:44 INFO [pass 2: assign] complete
16:03:44 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic metabolic_endocrine_disease
16:03:44 INFO Querying DynamoDB partition: TOPIC#metabolic_endocrine_disease
16:03:45 INFO Fetched 1189 SCORE# rows for metabolic_endocrine_disease
16:03:45 INFO Aggregated: 1131 rows included, 58 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
16:03:45 INFO Faculty touched: 342
16:05:01 INFO Writes: cleared=342, written=342, dry_run=False
16:05:01 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_metabolic_endocrine_disease.json
16:05:01 INFO [pass 3: aggregate] complete
16:05:01 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_metabolic_endocrine_disease.json

```
| emergency_critical_care_medicine | 505 | 30 | 68.5% | 3 |  |  | ok |

<!-- === emergency_critical_care_medicine === rc=0 -->
```
logical_complications:
    alm9097                  0.6694
    ban9003                  0.6694
    azs2001                  0.5049
    sam9235                  0.4885
    jol9057                  0.4823

  emergency_pediatric_ed_acute_illness:
    del9096                  1.3199
    slp9001                  1.0144
    ams9191                  0.6159
    zag9005                  0.5646
    err9009                  0.4862

  emergency_resuscitation_cardiac_arrest:
    fet4003                  1.3642
    jur9123                  1.2807
    esj9020                  0.4820
    yip4002                  0.4437
    rkp9004                  0.4437

  emergency_global_emergency_care_lmic:
    jur9123                  3.0641
    mia2016                  0.4073
    ras9199                  0.3583
    roh9005                  0.2671
    slp9001                  0.2494

  emergency_icu_operations_staffing:
    haw9006                  2.5419
    khd9010                  0.2947
    rak2007                  0.2947
    abj9004                  0.2448
    rsz4001                  0.2360

  emergency_post_covid_long_covid_outcomes:
    pag9051                  0.9515
    mms9024                  0.4619
    lcp2003                  0.4619
    liw9021                  0.3972
    ejs9005                  0.3972

  emergency_covid19_cardiac_thrombotic_complications:
    pag9051                  0.3305
    mms9024                  0.3305
    kas3002                  0.2053
    dbm9003                  0.2053
    lndhlovu                 0.1863

  emergency_icu_data_informatics_ai:
    few2001                  0.8843
    yip4002                  0.4245
    haw9006                  0.3486
    thc2015                  0.3092
    ejs9005                  0.3092

  emergency_pediatric_transfusion_blood_management:
    man9026                  2.1412
    mec2013                  0.5946
    mrd2006                  0.4208
    did2005                  0.4208
    tsc9008                  0.1442

  emergency_antimicrobial_resistance_diagnostics:
    law9067                  0.5464
    mjs9012                  0.4214
    mss9008                  0.3667
    juc9107                  0.3037
    mag2005                  0.3037

  emergency_covid19_respiratory_mechanical_ventilation:
    kar9043                  0.3645
    ajs9039                  0.3301
    bmf9001                  0.3301
    djb9004                  0.3301
    ejs9005                  0.1932

  emergency_pulmonary_embolism_management:
    rsz4001                  2.2103
    jbg4001                  0.5956
    has4032                  0.2043

  emergency_palliative_care_ethics_end_of_life:
    jjfins                   0.7000
    hgp2001                  0.3262
    liw9021                  0.3262
    pam2056                  0.3262
    berlind                  0.2551

  emergency_cardiac_surgery_perioperative_outcomes:
    mfg9004                  0.3507
    mrd2006                  0.2815
    haw9006                  0.2021
    lir9065                  0.1930
    chl9077                  0.1781

  emergency_covid19_treatment_immunomodulation:
    mwm9004                  0.5561
    yin9003                  0.1713
    rgulick                  0.1577
    gam9044                  0.1548
    law9067                  0.0923

  emergency_opioid_substance_use_ed:
    smm2010                  0.5152
    alj4004                  0.3997
    yub2003                  0.3613
    emm4010                  0.1531
    tdb2002                  0.1353

  emergency_lung_transplant_ecmo_outcomes:
    juf4007                  0.4645
    lok9031                  0.0562

=== backfill_topic.py complete ===
  topic:           emergency_critical_care_medicine
  activity_count:  505
  cluster_count:   30
  coverage_pct:    0.6851
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_emergency_critical_care_medicine.json


-- stderr --
 for pid=nrr9001: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
16:12:00 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=jas2050: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
16:12:06 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=cmb9028: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
16:12:07 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=shl9115: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
16:12:12 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=ash9006: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
16:12:12 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=sus9079: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
16:12:15 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=jad9165: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
16:12:19 INFO Writes: cleared=393, written=402, dry_run=False
16:12:19 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_emergency_critical_care_medicine.json
16:12:19 INFO [pass 3: aggregate] complete
16:12:19 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_emergency_critical_care_medicine.json

```
| mental_health_psychiatry | 494 | 28 | 69.6% | 3 |  |  | ok |

<!-- === mental_health_psychiatry === rc=0 -->
```
chb9230                  0.5111

  mental_autism_neurodevelopment:
    col2004                  0.7680
    hab4003                  0.5757
    log4002                  0.5098
    fslee                    0.3871
    abt4002                  0.3219

  mental_cancer_psychosocial_survivorship:
    shr4009                  0.8827
    hgp2001                  0.8480
    lcp2003                  0.4946
    rmt4001                  0.4808
    elc9120                  0.3994

  mental_mental_health_services_policy:
    emm4010                  4.2988
    alj4004                  0.2701
    smm2010                  0.2701
    haa2019                  0.2653
    zrm2001                  0.2653

  mental_racial_ethnic_disparities_mental_health:
    lig2002                  0.4879
    mms9024                  0.4720
    haa2019                  0.3972
    rem4010                  0.3972
    juc4013                  0.3333

  mental_stress_neurobiology_circuits:
    krp2013                  0.8734
    fslee                    0.5365
    col2004                  0.5061
    mtoth                    0.2416
    ama2006                  0.1977

  mental_dementia_caregiver_support:
    sjc7004                  0.9606
    dwh4001                  0.5173
    mcr2004                  0.4493
    jhm2006                  0.4433
    rdadelma                 0.2947

  mental_social_isolation_loneliness_aging:
    sjc7004                  0.5382
    jhm2006                  0.3765
    fgd2002                  0.3219
    nis2051                  0.3219
    mcr2004                  0.2293

  mental_pediatric_delirium_icu:
    chr9008                  0.8332
    lig2002                  0.5845
    hgp2001                  0.3413
    pam2056                  0.3413
    liw9021                  0.3413

  mental_adolescent_inpatient_partial_hospital:
    smb9017                  0.8800
    awc9002                  0.8800
    wif4004                  0.4057
    jes9193                  0.3099
    mip2051                  0.2914

  mental_telepsychiatry_consultation_liaison:
    lsombrot                 0.4237
    chs9218                  0.4237
    evs2008                  0.2360
    sab2028                  0.2166
    jfmurray                 0.1876

  mental_chronic_pain_psychosocial:
    mms9024                  0.9981
    mcr2004                  0.3417
    sab2028                  0.2893
    yin9003                  0.2893
    gus2004                  0.2881

  mental_hiv_mental_health_aging:
    mag2005                  0.6961
    cmd9008                  0.5526
    yuz2002                  0.5526
    rnp2002                  0.2963
    mcr2004                  0.2633

  mental_eating_disorders_body_image:
    kap9161                  1.7287
    haw4011                  0.6026
    mas2187                  0.4233
    jec7100                  0.1853
    spk9004                  0.1446

  mental_substance_use_adolescent_young_adult:
    tdb2002                  0.7204
    krp2013                  0.5113
    juc4013                  0.2293
    qiz4006                  0.2244
    anm4001                  0.2159

  mental_physician_trainee_burnout:
    kad9090                  0.3752
    mrs9012                  0.2738
    hgp2001                  0.2522
    lsombrot                 0.2103
    jdifede                  0.2103

  mental_pediatric_behavioral_training:
    cmg9004                  0.5795
    dgh7001                  0.1885
    das2043                  0.1661
    tdb2002                  0.1271

  mental_bipolar_disorder_mechanisms_treatment:
    cag4010                  0.6115
    bxt9001                  0.2641

=== backfill_topic.py complete ===
  topic:           mental_health_psychiatry
  activity_count:  494
  cluster_count:   28
  coverage_pct:    0.6964
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_mental_health_psychiatry.json


-- stderr --
for_topic failed for pid=arl2017: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
16:16:41 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=cam4013: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
16:16:41 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=jaw9089: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
16:16:44 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=nas9172: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
16:17:01 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=htd9001: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
16:17:15 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=mas2187: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
16:17:15 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=spk9004: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
16:17:20 INFO Writes: cleared=270, written=279, dry_run=False
16:17:20 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_mental_health_psychiatry.json
16:17:20 INFO [pass 3: aggregate] complete
16:17:20 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_mental_health_psychiatry.json

```
| pediatrics_neonatology | 480 | 30 | 72.1% | 3 |  |  | ok |

<!-- === pediatrics_neonatology === rc=0 -->
```
008                  0.6034
    nik9015                  0.5974
    bmf9002                  0.4488
    raz2002                  0.4488

  pediatrics_pediatric_pulmonology_asthma:
    aam9008                  0.8820
    pep9004                  0.6548
    afh9005                  0.5565
    stw2006                  0.5324
    kag9148                  0.4410

  pediatrics_social_determinants_health_equity:
    yux4008                  0.9903
    err9009                  0.6760
    lcp2003                  0.6760
    lmk2003                  0.6760
    few2001                  0.3986

  pediatrics_neonatal_brain_imaging:
    zuz4001                  0.9074
    zag9005                  0.4345
    arr2014                  0.3148
    ald2031                  0.3148
    pas2018                  0.3148

  pediatrics_pediatric_gi_ibd:
    kac9091                  0.6531
    nik9015                  0.6531
    zag9005                  0.5396
    als9047                  0.3756
    chm2042                  0.3185

  pediatrics_child_mental_health_psychopathology:
    wif4004                  0.4259
    zag9005                  0.4123
    err9009                  0.4123
    cmg9004                  0.4123
    del9096                  0.4123

  pediatrics_perinatal_maternal_outcomes:
    mos7003                  1.4287
    anm4001                  0.5238
    hlipkind                 0.3712
    yub2003                  0.3263
    kmv4001                  0.3079

  pediatrics_chiari_craniosynostosis_neurosurgery:
    jpgreenf                 1.1623
    ceh2003                  0.8014
    mmsouwei                 0.7717
    cro9004                  0.2416
    jts2004                  0.2416

  pediatrics_congenital_cmv_vaccines:
    sap4017                  2.3876
    gef4003                  0.7968
    myz4001                  0.2863
    hut4001                  0.1417
    lig2002                  0.1056

  pediatrics_pediatric_infectious_disease_global:
    ymd9002                  0.6006
    rnp2002                  0.5274
    lir2020                  0.3802
    ras9199                  0.3620
    mia2016                  0.2093

  pediatrics_kidney_disease_growth:
    oma9005                  1.2778
    yuz2002                  0.5347
    hak2012                  0.4663
    edp2014                  0.3857
    dib2020                  0.3050

  pediatrics_pregnancy_maternal_infections:
    tak4011                  0.3720
    hes2011                  0.2372
    myz4001                  0.2372
    res2025                  0.2372
    has9032                  0.1996

  pediatrics_adolescent_sexual_reproductive_health:
    sharkom                  0.5713
    saf7007                  0.3011
    jac4017                  0.1995
    juc7016                  0.1976
    bac2003                  0.1962

  pediatrics_child_maltreatment_foster_care:
    anm4001                  0.9874
    emm4010                  0.8681
    haw9006                  0.1750

  pediatrics_congenital_heart_disease:
    bek4011                  0.5082
    hol4006                  0.5082
    osc4001                  0.2494
    man9026                  0.2147
    djd7003                  0.1552

  pediatrics_adolescent_substance_use_neurobiology:
    tdb2002                  1.0282
    qiz4006                  0.2113
    zrm2001                  0.1356
    fslee                    0.1353
    tmilner                  0.1353

  pediatrics_childhood_cancer_survivorship:
    est2003                  0.9804
    blk9001                  0.2492
    mam9706                  0.2492
    aim4006                  0.1552
    zoa9003                  0.0508

=== backfill_topic.py complete ===
  topic:           pediatrics_neonatology
  activity_count:  480
  cluster_count:   30
  coverage_pct:    0.7208
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_pediatrics_neonatology.json


-- stderr --
4/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic pediatrics_neonatology
16:21:09 INFO Querying DynamoDB partition: TOPIC#pediatrics_neonatology
16:21:10 INFO Fetched 924 SCORE# rows for pediatrics_neonatology
16:21:10 INFO Aggregated: 843 rows included, 81 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
16:21:10 INFO Faculty touched: 351
16:21:46 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=ker2007: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
16:22:08 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=erk9007: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
16:22:28 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=als9047: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
16:22:33 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=taz4005: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
16:22:33 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=zes9010: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
16:22:33 INFO Writes: cleared=346, written=351, dry_run=False
16:22:33 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_pediatrics_neonatology.json
16:22:33 INFO [pass 3: aggregate] complete
16:22:33 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_pediatrics_neonatology.json

```
| systems_biology | 473 | 30 | 72.5% | 3 |  |  | ok |

<!-- === systems_biology === rc=0 -->
```
   shg3006                  0.6417

  systems_multiomics_integration_methods:
    jak2043                  1.3049
    kas2049                  0.9059
    him4004                  0.8843
    amh2025                  0.7798
    sbz2002                  0.4488

  systems_transcriptome_disease:
    lum4003                  0.6923
    rmt4001                  0.4956
    jos9335                  0.4363
    tai9015                  0.3742
    yuc4019                  0.3209

  systems_gene_regulatory_networks:
    jzx2002                  0.4453
    srafii                   0.3464
    dar2042                  0.3464
    kwd2001                  0.3404
    app2006                  0.3261

  systems_brain_connectivity_network:
    amk2012                  1.6593
    col2004                  0.5410
    jyl9010                  0.3646
    ema2004                  0.3312
    mab4092                  0.3211

  systems_pancreatic_beta_cell_diabetes:
    shc2034                  0.8825
    chc2062                  0.7679
    jis7016                  0.5553
    jzx2002                  0.5191
    tre2003                  0.5191

  systems_kidney_transplant_omics:
    mut9002                  0.9880
    msuthan                  0.8388
    dmd2001                  0.7547
    sts9057                  0.5237
    ole2001                  0.4679

  systems_rna_modifications_splicing:
    srj2003                  0.5874
    stt2007                  0.3571
    ajo9001                  0.3571
    coi2001                  0.3571
    ahs9018                  0.3571

  systems_mycobacterium_tuberculosis_metabolism:
    kyr9001                  1.4983
    sae2004                  1.0331
    dis2003                  0.5840
    alg2053                  0.3124
    vis2032                  0.2905

  systems_endothelial_vascular_biology:
    srafii                   0.3707
    dar2042                  0.3707
    jmg2008                  0.3707
    yal4002                  0.3707
    ole2001                  0.3489

  systems_adipose_metabolic_dysfunction:
    mnt4002                  0.9170
    nam2016                  0.5221
    frs4001                  0.4952
    stp9039                  0.3084
    kas2049                  0.3002

  systems_airway_epithelium_lung_disease:
    mrr2006                  0.7284
    rgcryst                  0.6195
    rkaner                   0.4582
    pleopold                 0.4319
    hes2019                  0.2278

  systems_network_computational_methods:
    chs4001                  0.3964
    few2001                  0.3964
    kas2049                  0.3688
    jak2043                  0.3688
    haa2019                  0.3688

  systems_hematopoiesis_epigenetics:
    dal3005                  0.2968
    shn9035                  0.2968
    srafii                   0.2611
    dar2042                  0.1901
    jzx2002                  0.1901

  systems_drug_repurposing_knowledge_graph:
    chs4001                  0.4525
    few2001                  0.4525
    ole2001                  0.3192
    lum4003                  0.2021
    beh2020                  0.1750

  systems_cell_cycle_proliferation:
    tom4003                  1.2239
    jet2021                  0.2028

  systems_brain_organoids_neuropsychiatric:
    dic2009                  0.7964
    hut2006                  0.2260
    mer2005                  0.1246
    ral2020                  0.1246
    col2004                  0.0720

  systems_aging_epigenetic_clocks:
    dob2014                  0.2544
    lndhlovu                 0.2180
    res2025                  0.1930
    emh9016                  0.1930
    zhl4003                  0.1631

=== backfill_topic.py complete ===
  topic:           systems_biology
  activity_count:  473
  cluster_count:   30
  coverage_pct:    0.7252
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_systems_biology.json


-- stderr --
essed: assigned=291, unassigned=9, failed=0, elapsed=45s
16:26:56 INFO [progress] 350/473 processed: assigned=341, unassigned=9, failed=0, elapsed=52s
16:27:03 INFO [progress] 400/473 processed: assigned=390, unassigned=10, failed=0, elapsed=59s
16:27:10 INFO [progress] 450/473 processed: assigned=439, unassigned=11, failed=0, elapsed=66s
16:27:13 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
16:27:13 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
16:27:13 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
16:27:14 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
16:27:14 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
16:27:15 INFO [progress] 473/473 processed: assigned=462, unassigned=11, failed=0, elapsed=71s
16:27:15 INFO [pass 2: assign] complete
16:27:15 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic systems_biology
16:27:15 INFO Querying DynamoDB partition: TOPIC#systems_biology
16:27:16 INFO Fetched 1358 SCORE# rows for systems_biology
16:27:16 INFO Aggregated: 1344 rows included, 14 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
16:27:16 INFO Faculty touched: 369
16:28:42 INFO Writes: cleared=369, written=369, dry_run=False
16:28:42 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_systems_biology.json
16:28:42 INFO [pass 3: aggregate] complete
16:28:42 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_systems_biology.json

```
| gastroenterology_hepatology | 468 | 30 | 87.8% | 3 |  |  | ok |

<!-- === gastroenterology_hepatology === rc=0 -->
```
          0.4948
    ccl9009                  0.3913

  gastroenterology_fmt_ibd_microbiome_therapy:
    ral2006                  1.0326
    ejs2005                  1.0326
    chg4001                  0.6275
    cvc9002                  0.6275
    sdp4001                  0.6275

  gastroenterology_intestinal_stem_cells_epithelial_biology:
    jmg2008                  0.7922
    srafii                   0.7922
    jzx2002                  0.4517
    res2025                  0.4517
    dar2042                  0.4517

  gastroenterology_hepatic_metabolic_lipid:
    res2025                  0.5730
    dcl2001                  0.5730
    haz2005                  0.5730
    irm2224                  0.5730
    dop9054                  0.5730

  gastroenterology_gut_microbiome_metabolic_neurological:
    hok9010                  0.8426
    coi2001                  0.6560
    arz2002                  0.3829
    has9059                  0.3829
    sie9007                  0.3829

  gastroenterology_gut_microbiome_transplant_infection:
    law9067                  1.0977
    mjs9012                  1.0977
    dmd2001                  0.7888
    rsoave                   0.5066
    tbs2001                  0.5066

  gastroenterology_celiac_eosinophilic_functional:
    hmz7001                  1.2557
    nip9020                  0.6450
    cfrissor                 0.4719
    als9047                  0.3467
    chm2042                  0.2748

  gastroenterology_hepatocyte_models_gene_therapy:
    res2025                  1.0422
    ydj2001                  0.6008
    emh9016                  0.4533
    jzx2002                  0.2241
    dar2042                  0.2241

  gastroenterology_gastric_esophageal_cancer:
    mas9313                  1.1719
    rum9028                  0.2181
    dob2014                  0.2113
    pag2015                  0.2113
    emh9016                  0.1985

  gastroenterology_colorectal_surgical_outcomes:
    syc2005                  0.5617
    keg9034                  0.4394
    hey9002                  0.4305
    cmf2004                  0.3987
    ars2013                  0.3962

  gastroenterology_cancer_immunotherapy_microenvironment:
    jom4010                  0.3579
    jul4008                  0.3579
    mdu4003                  0.3579
    mtd4001                  0.3579
    gfs2002                  0.3480

  gastroenterology_liver_in_pregnancy:
    tak4011                  2.6020
    map2008                  0.1313
    arr2014                  0.1313
    gcl9003                  0.1313
    lim9120                  0.1313

  gastroenterology_small_bowel_obstruction_appendicitis:
    brp9018                  0.4017
    ars2013                  0.4017
    jwm2001                  0.4017
    ilw9002                  0.2893
    sat9211                  0.2093

  gastroenterology_pancreatitis_pancreatic_cysts:
    ejs2005                  0.3857
    rur9017                  0.3475
    cvc9002                  0.3475
    ada4017                  0.2823
    map2008                  0.2464

  gastroenterology_neonatal_pediatric_gi:
    vjk9004                  0.2211
    aam9008                  0.1813
    kag9148                  0.1813
    thc9032                  0.1813
    sap4017                  0.1561

  gastroenterology_advanced_endoscopy_novel_devices:
    jwm2001                  0.8472
    lel9080                  0.8472

  gastroenterology_schistosomiasis_parasitic_gi:
    jna2002                  0.5545
    myl2003                  0.3422
    khp9007                  0.2494
    drw2004                  0.1049
    lndhlovu                 0.1049

=== backfill_topic.py complete ===
  topic:           gastroenterology_hepatology
  activity_count:  468
  cluster_count:   30
  coverage_pct:    0.8782
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_gastroenterology_hepatology.json


-- stderr --
pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
16:32:42 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
16:32:42 INFO [progress] 468/468 processed: assigned=449, unassigned=19, failed=0, elapsed=96s
16:32:42 INFO [pass 2: assign] complete
16:32:42 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic gastroenterology_hepatology
16:32:43 INFO Querying DynamoDB partition: TOPIC#gastroenterology_hepatology
16:32:43 INFO Fetched 1245 SCORE# rows for gastroenterology_hepatology
16:32:43 INFO Aggregated: 1180 rows included, 65 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
16:32:43 INFO Faculty touched: 353
16:33:39 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=ams2041: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
16:34:07 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=qhm9001: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
16:34:07 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=mjm9042: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
16:34:08 INFO Writes: cleared=350, written=353, dry_run=False
16:34:08 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_gastroenterology_hepatology.json
16:34:08 INFO [pass 3: aggregate] complete
16:34:09 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_gastroenterology_hepatology.json

```
| medical_education_workforce | 440 | 28 | 81.6% | 3 |  |  | ok |

<!-- === medical_education_workforce === rc=0 -->
```
57
    amr2024                  0.3817

  medical_home_health_direct_care_workforce:
    mrs9012                  2.8262
    mms9024                  1.2056
    lmk2003                  0.8086
    pbl9001                  0.6601
    pag9051                  0.6451

  medical_medical_student_education:
    err9009                  0.6861
    sek9028                  0.6861
    lcs9005                  0.4542
    pes2008                  0.4246
    ale2009                  0.4246

  medical_physician_burnout_wellbeing:
    jeg9244                  0.5793
    lib9050                  0.5784
    lig2002                  0.5384
    kac9091                  0.5384
    slp9001                  0.5017

  medical_global_health_lmic_training:
    roh9005                  1.2223
    ibh9004                  0.7814
    ceh2003                  0.4409
    ras9199                  0.4231
    mia2016                  0.3484

  medical_interprofessional_peer_coach_training:
    mms9024                  0.4319
    yin9003                  0.4319
    err9009                  0.4248
    dao2002                  0.4248
    scm2009                  0.3262

  medical_healthcare_system_policy_workforce:
    mau2006                  0.9215
    arj2005                  0.4823
    amb2036                  0.3765
    fto2002                  0.1631
    haa4020                  0.1388

  medical_diagnostic_error_clinical_practice_gaps:
    mms9024                  0.2486
    juc9107                  0.2486
    shk9078                  0.1976
    cjg7003                  0.1976
    coo9025                  0.1499

  medical_pediatric_behavioral_mental_health_training:
    cmg9004                  1.4664
    tes9045                  0.5042
    tdb2002                  0.2731

  medical_nursing_home_workforce_burnout_turnover:
    mslachs                  0.4568
    arj2005                  0.4394
    mau2006                  0.4394
    zrm2001                  0.3262
    rtb2003                  0.1842

  medical_cpr_emergency_response_community_training:
    jur9123                  1.2404
    row9057                  0.2653
    cav7003                  0.2653

  medical_opioid_substance_use_clinician_attitudes:
    emm4010                  0.4574
    mfw4002                  0.2653
    pfi9001                  0.2653
    shk9078                  0.2652
    err9009                  0.1767

  medical_elder_mistreatment_detection_response:
    aer2006                  0.5285
    dwh4001                  0.3656
    esc4003                  0.3656
    als9138                  0.2568
    brd9088                  0.1089

  medical_health_equity_disparities_screening:
    bje9003                  0.1808
    jimperat                 0.1808
    meb7002                  0.1808
    chb9074                  0.1089
    dag9025                  0.1089

  medical_icustaffing_workforce_models:
    haw9006                  0.9111
    rsz4001                  0.1727
    nib9009                  0.0896
    mam9508                  0.0896

  medical_patient_provider_trust_engagement:
    dls7001                  0.2822
    mfg9004                  0.1809
    lir9065                  0.1809
    mms9024                  0.1510
    khd9010                  0.1147

  medical_student_mental_health_wellbeing:
    juc4013                  0.2136
    anm2119                  0.1703
    tcp2003                  0.1703
    amr2024                  0.1356
    ama2006                  0.0580

  medical_arts_humanities_reflective_practice:
    lib9050                  0.2759
    irz9006                  0.2731
    ibk9003                  0.0859

=== backfill_topic.py complete ===
  topic:           medical_education_workforce
  activity_count:  440
  cluster_count:   28
  coverage_pct:    0.8159
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_medical_education_workforce.json


-- stderr --
pic failed for pid=rab2029: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
16:38:44 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=jus9032: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
16:38:45 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=chz4014: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
16:38:46 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=irz9006: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
16:38:48 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=alt9071: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
16:38:48 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=sjt9003: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
16:38:50 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=nim9093: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
16:38:50 INFO Writes: cleared=351, written=366, dry_run=False
16:38:50 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_medical_education_workforce.json
16:38:50 INFO [pass 3: aggregate] complete
16:38:50 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_medical_education_workforce.json

```
| primary_care_general_medicine | 428 | 30 | 71.7% | 3 |  |  | ok |

<!-- === primary_care_general_medicine === rc=0 -->
```
 0.4463
    czb2002                  0.3809
    sab2028                  0.2129

  primary_hepatitis_infectious_disease_primary_care:
    shk9078                  1.0362
    markskr                  0.6746
    brs2006                  0.3744
    cjg7003                  0.2496
    ras9199                  0.2324

  primary_elder_abuse_mistreatment:
    aer2006                  0.5807
    mslachs                  0.5177
    dwh4001                  0.4502
    sjc7004                  0.4502
    esc4003                  0.4502

  primary_pain_management_chronic:
    mcr2004                  0.6531
    emm4010                  0.6102
    dkiosses                 0.3930
    ldravdin                 0.3930
    mms9024                  0.2439

  primary_obesity_weight_management:
    aps2004                  0.3162
    ljaronne                 0.3162
    erp2001                  0.2843
    mac9232                  0.2842
    bgt9001                  0.2582

  primary_cardiovascular_risk_prediction:
    mms9024                  0.8277
    yin9003                  0.3569
    lcp2003                  0.1271
    mrs9012                  0.1271
    pag9051                  0.1271

  primary_nursing_home_post_acute_care:
    arj2005                  0.9824
    mau2006                  0.8411
    yoz2009                  0.1442
    jiy4002                  0.1353
    mik9096                  0.0652

  primary_liver_disease_nafld_alcohol:
    dab4026                  0.5950
    res2025                  0.2352
    sok9028                  0.2352
    wha4002                  0.1750
    rsb2005                  0.1089

  primary_medical_education_residency_training:
    cmg9004                  0.1630
    zrm2001                  0.1532
    dea2006                  0.1532
    tha2002                  0.1532
    mar9391                  0.1122

  primary_community_health_workers_lay_health:
    mms9024                  0.2427
    yin9003                  0.2427
    ras9199                  0.2103
    dtw4001                  0.2043
    mrd2006                  0.1885

  primary_genetic_hereditary_cancer_risk:
    ras9030                  0.2898
    mkf2002                  0.2033
    pac2001                  0.2033
    aum9022                  0.1089
    deh3002                  0.1089

  primary_patient_trust_clinician_relationship:
    khd9010                  0.5312
    dls7001                  0.2131
    mms9024                  0.2093
    ezg9002                  0.0911
    keh4012                  0.0654

  primary_vaccine_hesitancy_uptake:
    cjg7003                  0.2360
    fpelzman                 0.2360
    jut9005                  0.2360
    brd9088                  0.1727
    jnh9006                  0.1670

  primary_smoking_cessation_tobacco:
    emm4010                  0.3083
    ban9003                  0.1397
    hok9010                  0.1397
    tdb2002                  0.1089
    err9009                  0.0743

  primary_high_cost_preventable_utilization:
    yoz2009                  0.2868
    khd9010                  0.2868
    rak2007                  0.2868
    mau2006                  0.1221
    zag9005                  0.1221

  primary_palliative_care_older_adults:
    mcr2004                  0.2822
    das2043                  0.1399
    puc9005                  0.1290
    sjc7004                  0.1089
    mau2006                  0.0707

  primary_care_transitions_discharge_planning:
    yiz2014                  0.1666
    djb9004                  0.1666
    err9009                  0.1254
    snm2001                  0.1254
    zrm2001                  0.1221

=== backfill_topic.py complete ===
  topic:           primary_care_general_medicine
  activity_count:  428
  cluster_count:   30
  coverage_pct:    0.7173
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_primary_care_general_medicine.json


-- stderr --
O [progress] 300/428 processed: assigned=282, unassigned=18, failed=0, elapsed=48s
16:43:26 INFO [progress] 350/428 processed: assigned=332, unassigned=18, failed=0, elapsed=55s
16:43:33 INFO [progress] 400/428 processed: assigned=380, unassigned=20, failed=0, elapsed=63s
16:43:37 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
16:43:37 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
16:43:38 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
16:43:39 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
16:43:40 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
16:43:41 INFO [progress] 428/428 processed: assigned=408, unassigned=20, failed=0, elapsed=70s
16:43:41 INFO [pass 2: assign] complete
16:43:41 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic primary_care_general_medicine
16:43:41 INFO Querying DynamoDB partition: TOPIC#primary_care_general_medicine
16:43:41 INFO Fetched 947 SCORE# rows for primary_care_general_medicine
16:43:41 INFO Aggregated: 908 rows included, 39 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
16:43:41 INFO Faculty touched: 240
16:44:36 INFO Writes: cleared=240, written=240, dry_run=False
16:44:36 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_primary_care_general_medicine.json
16:44:36 INFO [pass 3: aggregate] complete
16:44:36 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_primary_care_general_medicine.json

```
| hematology | 379 | 30 | 86.5% | 3 |  |  | ok |

<!-- === hematology === rc=0 -->
```
matopoiesis_somatic:
    dal3005                  1.1862
    shn9035                  0.5701
    mmo2002                  0.4741
    chm2042                  0.4437
    cem2009                  0.4437

  hematology_coagulation_anticoagulation:
    mtd2002                  0.6759
    mec2013                  0.5837
    map2007                  0.3650
    stp9039                  0.3650
    tsc9008                  0.2874

  hematology_endothelial_vascular_biology:
    srafii                   0.8758
    yal4002                  0.6270
    ggi9001                  0.5913
    dar2042                  0.4499
    jmg2008                  0.4499

  hematology_covid19_clinical_outcomes:
    pag9051                  0.3989
    mms9024                  0.3372
    law9067                  0.2487
    mjs9012                  0.2487
    jcooke                   0.2487

  hematology_venous_thromboembolism:
    mtd2002                  0.9577
    nik9015                  0.7229
    kac9091                  0.5704
    ole2001                  0.4342
    lig2002                  0.4109

  hematology_spaceflight_omics:
    chm2042                  1.4568
    irm2224                  0.7908
    cem2009                  0.7527
    jak2043                  0.3143
    qic2005                  0.3143

  hematology_iron_metabolism_therapy:
    myleswo                  1.5444
    oma9005                  0.7955
    edp2014                  0.5603
    shethsu                  0.3262
    dib2020                  0.3222

  hematology_lymphoma_targeted_therapy:
    rrfurman                 1.6906
    joa9069                  0.8726
    pac2001                  0.3232
    jruan                    0.3232
    ckg2001                  0.3232

  hematology_platelet_biology_disorders:
    jlaurenc                 0.6223
    cag9152                  0.4051
    mtd2002                  0.3448
    mec2013                  0.3134
    man9026                  0.3134

  hematology_multiple_myeloma:
    mab4033                  0.9486
    ggi9001                  0.4259
    mal4005                  0.4259
    run9001                  0.3413
    mam9823                  0.2550

  hematology_lymphoma_diagnosis_staging:
    sar2014                  0.9983
    achadbur                 0.5173
    cym2003                  0.2968
    zhc2006                  0.2360
    ljf2005                  0.2337

  hematology_aml_mds_therapy_biology:
    gar2001                  1.7584
    pid9006                  0.3055
    ritchie                  0.3055
    ptm2001                  0.2360

  hematology_ai_nlp_diagnostics:
    thc2015                  0.3290
    evs2008                  0.3290
    jms2003                  0.3290
    lum4003                  0.2947
    gfa2001                  0.2682

  hematology_lung_lymphatic_vascular:
    hho2001                  0.8001
    ral2020                  0.3321
    dar2042                  0.3027
    ccc4002                  0.3027
    las4011                  0.3027

  hematology_pulmonary_embolism_catheter:
    rsz4001                  1.2514
    jbg4001                  0.2778
    has4032                  0.1415
    noi7001                  0.0740
    ram9022                  0.0740

  hematology_sickle_cell_pain_management:
    has9032                  0.3050
    rnp2002                  0.2807
    rsw9006                  0.2226
    nem9015                  0.2226
    zat2002                  0.2226

  hematology_eosinophil_gene_therapy:
    rgcryst                  0.5073
    nhackett                 0.2099
    smkamins                 0.2099

  hematology_lung_transplant_perioperative:
    juf4007                  0.1882

=== backfill_topic.py complete ===
  topic:           hematology
  activity_count:  379
  cluster_count:   30
  coverage_pct:    0.8654
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_hematology.json


-- stderr --
, unassigned=24, failed=0, elapsed=53s
16:47:41 INFO [progress] 350/379 processed: assigned=326, unassigned=24, failed=0, elapsed=62s
16:47:45 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
16:47:45 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
16:47:45 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
16:47:45 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
16:47:46 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
16:47:46 INFO [progress] 379/379 processed: assigned=355, unassigned=24, failed=0, elapsed=67s
16:47:46 INFO [pass 2: assign] complete
16:47:46 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic hematology
16:47:47 INFO Querying DynamoDB partition: TOPIC#hematology
16:47:47 INFO Fetched 900 SCORE# rows for hematology
16:47:47 INFO Aggregated: 841 rows included, 59 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
16:47:47 INFO Faculty touched: 300
16:48:10 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=mdh4002: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
16:48:54 INFO Writes: cleared=299, written=300, dry_run=False
16:48:54 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_hematology.json
16:48:54 INFO [pass 3: aggregate] complete
16:48:54 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_hematology.json

```
| pulmonary_critical_care | 361 | 30 | 82.3% | 3 |  |  | ok |

<!-- === pulmonary_critical_care === rc=0 -->
```
1                  0.4838
    keg2007                  0.4838
    mgk2002                  0.4838
    ejs9005                  0.1915

  pulmonary_lung_cancer_immunotherapy:
    nkaltork                 0.6977
    vim2010                  0.5933
    ole2001                  0.4490
    temcgraw                 0.4490
    dig2009                  0.4258

  pulmonary_covid19_sars2_biology_models:
    res2025                  0.5118
    shc2034                  0.5118
    jzx2002                  0.3995
    tre2003                  0.3995
    mig2021                  0.2393

  pulmonary_lung_cancer_biology_treatment:
    nkaltork                 0.5816
    asl4003                  0.3944
    lud2005                  0.3630
    jlp2002                  0.3197
    swh9002                  0.2078

  pulmonary_pulmonary_embolism:
    rsz4001                  2.1083
    jbg4001                  0.6536
    has4032                  0.2732

  pulmonary_chest_imaging_diagnostics:
    ges9006                  0.6372
    yip4002                  0.3931
    ajp9012                  0.3413
    dag2017                  0.2802
    anr2783                  0.2554

  pulmonary_covid19_cardiac_complications:
    mms9024                  0.3039
    pag9051                  0.3039
    blerman                  0.3039
    jac9029                  0.3039
    kas3002                  0.1771

  pulmonary_icu_quality_safety:
    haw9006                  1.2610
    ejs9005                  0.2206
    evs2008                  0.2206
    thc2015                  0.2206
    jcooke                   0.1822

  pulmonary_ecmo_extracorporeal:
    ans9326                  0.3690
    bmw2002                  0.3329
    iog9001                  0.3329
    cmack                    0.2699
    hve9006                  0.2699

  pulmonary_sleep_apnea_respiratory_physiology:
    yrj9003                  0.3673
    anr2783                  0.2697
    cjl2007                  0.1631
    jdp9009                  0.1631
    mrd9035                  0.1631

  pulmonary_lung_cancer_surgical_periop:
    bel9026                  0.2310
    jlp2002                  0.2310
    nkaltork                 0.2310
    jov9069                  0.2310
    osc4001                  0.2310

  pulmonary_tuberculosis_mycobacterial:
    jwp2001                  0.3243
    kfw2001                  0.3243
    myl2003                  0.3243
    dwf2001                  0.2717
    kaz4001                  0.2717

  pulmonary_lung_transplantation:
    juf4007                  1.3731
    hho2001                  0.1661
    lok9031                  0.0992
    sap4017                  0.0896

  pulmonary_covid19_pediatric:
    err9009                  0.1919
    del9096                  0.1919
    jih9033                  0.1919
    kpa9002                  0.1919
    zag9005                  0.1919

  pulmonary_covid19_neurological_manifestations:
    alm9097                  0.1561
    azs2001                  0.1561
    ban9003                  0.1561
    dal2023                  0.1561
    haw9009                  0.1561

  pulmonary_respiratory_trauma_surgery:
    kok4001                  0.1950
    kdr9004                  0.1296
    mfg9004                  0.1089
    mmr2011                  0.1089
    chl9077                  0.1089

  pulmonary_smoking_vaping_occupational:
    zrm2001                  0.1435
    jur9123                  0.1188
    tdb2002                  0.1089
    dabehrm                  0.0896
    gsr9001                  0.0896

  pulmonary_antimicrobial_resistance:
    mjs9012                  0.2999
    dip9063                  0.2914

=== backfill_topic.py complete ===
  topic:           pulmonary_critical_care
  activity_count:  361
  cluster_count:   30
  coverage_pct:    0.8227
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_pulmonary_critical_care.json


-- stderr --
ed=0, elapsed=30s
16:51:23 INFO [progress] 250/361 processed: assigned=228, unassigned=22, failed=0, elapsed=37s
16:51:30 INFO [progress] 300/361 processed: assigned=277, unassigned=23, failed=0, elapsed=43s
16:51:37 INFO [progress] 350/361 processed: assigned=327, unassigned=23, failed=0, elapsed=50s
16:51:38 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
16:51:38 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
16:51:38 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
16:51:38 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
16:51:39 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
16:51:39 INFO [progress] 361/361 processed: assigned=338, unassigned=23, failed=0, elapsed=52s
16:51:39 INFO [pass 2: assign] complete
16:51:39 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic pulmonary_critical_care
16:51:40 INFO Querying DynamoDB partition: TOPIC#pulmonary_critical_care
16:51:40 INFO Fetched 999 SCORE# rows for pulmonary_critical_care
16:51:40 INFO Aggregated: 953 rows included, 46 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
16:51:40 INFO Faculty touched: 348
16:52:53 INFO Writes: cleared=348, written=348, dry_run=False
16:52:53 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_pulmonary_critical_care.json
16:52:53 INFO [pass 3: aggregate] complete
16:52:53 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_pulmonary_critical_care.json

```
| health_economics | 333 | 30 | 80.5% | 3 |  |  | ok |

<!-- === health_economics === rc=0 -->
```
ation:
    aer2006                  0.7468
    mslachs                  0.7468
    yub2003                  0.6514
    yiz2014                  0.6514
    dwh4001                  0.4687

  health_payment_model_innovation:
    yub2003                  0.6249
    amb2036                  0.4741
    rtb2003                  0.4741
    klk9001                  0.4741
    rharrington              0.3148

  health_telehealth_economics:
    jiy4002                  1.0249
    arj2005                  0.7092
    khd9010                  0.3737
    mau2006                  0.2486
    mrs9012                  0.2486

  health_nursing_home_ltc_economics:
    arj2005                  1.2810
    rtb2003                  0.8878
    mau2006                  0.8089
    jiy4002                  0.2113
    yoz2009                  0.0964

  health_epidemiological_modeling_economics:
    lja2002                  1.6166
    hsc2001                  0.8541
    brs2006                  0.1187
    rsb2005                  0.1187
    liy9032                  0.0812

  health_covid_pandemic_health_system_impact:
    emm4010                  0.2188
    elc9120                  0.1876
    mkf2002                  0.1876
    pac2001                  0.1876
    mrs9012                  0.1690

  health_mental_health_integration_policy:
    emm4010                  0.8895
    smm2010                  0.3207
    lcp2003                  0.1355
    lmk2003                  0.1355
    mms9024                  0.1355

  health_workforce_burnout_turnover:
    mau2006                  0.2734
    arj2005                  0.2593
    mrs9012                  0.1976
    alh4014                  0.1976
    jim2012                  0.1596

  health_neonatal_pediatric_care_policy:
    lig2002                  0.2863
    aam9008                  0.2863
    man9026                  0.2863
    yub2003                  0.1653
    anm4001                  0.1653

  health_global_lmic_health_systems:
    roh9005                  0.2894
    lct4001                  0.2650
    dmo9004                  0.2093
    anr2783                  0.1112
    odk9003                  0.1112

  health_opioid_prescribing_policy:
    emm4010                  0.6351
    yub2003                  0.2021
    shk9078                  0.2021
    haw9006                  0.0859

  health_cardiovascular_testing_value:
    vuk9003                  0.3587
    alg4055                  0.1290
    pid9006                  0.0787
    ars2013                  0.0768
    jim2012                  0.0768

  health_cannabis_policy_outcomes:
    emm4010                  0.3164
    anm4001                  0.2728
    mcr2004                  0.0846
    yub2003                  0.0846

  health_comorbidity_chronic_disease_utilization:
    rsw9006                  0.1089
    nem9015                  0.1089
    zat2002                  0.1089
    smm2010                  0.0924
    joa9070                  0.0924

  health_hospice_palliative_end_of_life:
    yoz2009                  0.5368
    hgp2001                  0.0704
    rtb2003                  0.0704

  health_health_information_technology_billing:
    mau2006                  0.1713
    kdr9004                  0.1321
    abj9004                  0.0692
    ccl9009                  0.0692
    rsb2005                  0.0692

  health_breast_cancer_reconstruction_disparities:
    dmo9004                  0.0885
    lan4002                  0.0544
    miz9022                  0.0544
    rmt4001                  0.0544
    vjb9003                  0.0544

  health_health_disparities_access_geography:
    alexisa                  0.0654

=== backfill_topic.py complete ===
  topic:           health_economics
  activity_count:  333
  cluster_count:   30
  coverage_pct:    0.8048
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_health_economics.json


-- stderr --
ress] 200/333 processed: assigned=184, unassigned=16, failed=0, elapsed=29s
16:55:25 INFO [progress] 250/333 processed: assigned=232, unassigned=18, failed=0, elapsed=37s
16:55:34 INFO [progress] 300/333 processed: assigned=281, unassigned=19, failed=0, elapsed=46s
16:55:39 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
16:55:39 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
16:55:40 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
16:55:40 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:00:36 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:00:36 WARNING Bedrock read timeout (attempt 1/3). Retrying in 1s...
17:00:39 INFO [progress] 333/333 processed: assigned=314, unassigned=19, failed=0, elapsed=351s
17:00:39 INFO [pass 2: assign] complete
17:00:39 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic health_economics
17:00:39 INFO Querying DynamoDB partition: TOPIC#health_economics
17:00:40 INFO Fetched 657 SCORE# rows for health_economics
17:00:40 INFO Aggregated: 632 rows included, 25 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
17:00:40 INFO Faculty touched: 206
17:01:25 INFO Writes: cleared=206, written=206, dry_run=False
17:01:25 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_health_economics.json
17:01:25 INFO [pass 3: aggregate] complete
17:01:25 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_health_economics.json

```
| nutrition_metabolism | 327 | 26 | 82.0% | 3 |  |  | ok |

<!-- === nutrition_metabolism === rc=0 -->
```
06                  0.2927

  nutrition_vitamin_micronutrient:
    myleswo                  0.6257
    cha2022                  0.4657
    mfm2003                  0.3467
    rbdevere                 0.3185
    als9047                  0.2910

  nutrition_renal_electrolyte_transport:
    lgpalm                   0.7111
    lig2002                  0.2637
    mms9024                  0.2637
    dwf2001                  0.2637
    jwp2001                  0.2637

  nutrition_mtor_lysosomal_nutrient_sensing:
    job2064                  0.7012
    min2015                  0.7012
    zhl4003                  0.5363
    ela9082                  0.5363
    loh2007                  0.5363

  nutrition_neurological_metabolic:
    jup9003                  1.0819
    vij4004                  0.7818
    jak2043                  0.5966
    zap4003                  0.4798
    kas2049                  0.3288

  nutrition_immune_metabolism_tcell:
    jur2016                  0.7397
    dim2018                  0.2315
    elc9120                  0.2315
    evc2005                  0.2315
    ole2001                  0.1902

  nutrition_nad_metabolism_mitochondria:
    yuy2010                  0.9915
    gim2004                  0.6141
    qic2005                  0.3571
    mad2003                  0.3571
    jobuck                   0.3520

  nutrition_mycobacterium_metabolism:
    kyr9001                  1.1761
    sae2004                  0.9385
    dis2003                  0.4408
    alg2053                  0.3941
    vis2032                  0.2299

  nutrition_sphingolipid_ceramide:
    and2039                  0.5332
    stw2006                  0.4211
    ole2001                  0.3384
    afh9005                  0.3207
    jig4003                  0.3207

  nutrition_breast_tissue_composition:
    rmt4001                  1.0978
    oaz4001                  0.8098
    sgk4001                  0.1790
    all2017                  0.1024
    kad9090                  0.1024

  nutrition_pediatric_neonatal_nutrition:
    afh9005                  0.3903
    lkb9003                  0.3903
    zes9010                  0.3903
    sap4017                  0.2147
    mos7003                  0.1891

  nutrition_metabolic_syndrome_cardiometabolic:
    pag9051                  0.4473
    srk4008                  0.1548
    mms9024                  0.1423
    lcp2003                  0.1423
    sab2028                  0.1423

  nutrition_tryptophan_kynurenine_metabolism:
    bjr4002                  0.2291
    gim2004                  0.2211
    qic2005                  0.2211
    hik2004                  0.2211
    hes2019                  0.2099

  nutrition_dietary_intervention_chronic_disease:
    jak2043                  0.4401
    zhl4003                  0.4170
    est2003                  0.4060
    aaa4027                  0.1640
    ara4013                  0.1640

  nutrition_obesity_disparities_behavioral:
    erp2001                  0.5439
    sat9137                  0.2021
    pac2001                  0.1822
    cjg7003                  0.1457
    jnh9006                  0.1187

  nutrition_obesity_cancer_survivors:
    est2003                  0.4591
    erp2001                  0.1682
    eot9002                  0.1636
    ves4007                  0.1526
    lan4002                  0.0768

  nutrition_adipokine_cardiometabolic_signaling:
    myleswo                  0.3312
    stp9039                  0.2099
    smr4005                  0.1064
    mif4018                  0.1004
    jal2063                  0.1002

  nutrition_hepatic_gene_expression_diet:
    yuc4019                  0.4934

=== backfill_topic.py complete ===
  topic:           nutrition_metabolism
  activity_count:  327
  cluster_count:   26
  coverage_pct:    0.8196
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_nutrition_metabolism.json


-- stderr --
signed=12, failed=0, elapsed=23s
17:03:46 INFO [progress] 200/327 processed: assigned=186, unassigned=14, failed=0, elapsed=30s
17:03:54 INFO [progress] 250/327 processed: assigned=235, unassigned=15, failed=0, elapsed=39s
17:04:02 INFO [progress] 300/327 processed: assigned=284, unassigned=16, failed=0, elapsed=46s
17:04:06 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:04:06 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:04:06 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:04:07 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:04:22 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:04:23 INFO [progress] 327/327 processed: assigned=311, unassigned=16, failed=0, elapsed=67s
17:04:23 INFO [pass 2: assign] complete
17:04:23 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic nutrition_metabolism
17:04:23 INFO Querying DynamoDB partition: TOPIC#nutrition_metabolism
17:04:24 INFO Fetched 793 SCORE# rows for nutrition_metabolism
17:04:24 INFO Aggregated: 748 rows included, 45 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
17:04:24 INFO Faculty touched: 291
17:05:27 INFO Writes: cleared=291, written=291, dry_run=False
17:05:27 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_nutrition_metabolism.json
17:05:27 INFO [pass 3: aggregate] complete
17:05:28 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_nutrition_metabolism.json

```
| maternal_child_health | 303 | 30 | 75.6% | 3 |  |  | ok |

<!-- === maternal_child_health === rc=0 -->
```
troke_vascular:
    ban9003                  0.6358
    hok9010                  0.6358
    lar9110                  0.3626
    alm9097                  0.3626
    mfink                    0.3626

  maternal_prenatal_diagnosis_genetic:
    stchasen                 0.9028
    trg2005                  0.5083
    mfm2003                  0.3467
    ceh2003                  0.2510
    mmsouwei                 0.2510

  maternal_hiv_vertical_transmission:
    sap4017                  1.1086
    gef4003                  0.6428
    rig4007                  0.3414
    ymd9002                  0.2618
    jsm9009                  0.2093

  maternal_prenatal_vaccines_immunization:
    sap4017                  0.9260
    hlipkind                 0.4303
    gef4003                  0.3382
    emm4010                  0.1926
    lar9110                  0.1926

  maternal_fetal_programming_neurobiology:
    mtoth                    0.5261
    kmv4001                  0.4856
    geh9036                  0.3262
    hes2011                  0.2696
    amg4013                  0.2372

  maternal_ivf_art_outcomes:
    zrosenw                  0.5039
    all9188                  0.2962
    ban9003                  0.2962
    haw9009                  0.2962
    hok9010                  0.2962

  maternal_family_planning_tanzania:
    jna2002                  1.1467
    myl2003                  0.4255
    cen2004                  0.2941
    ras9199                  0.2788
    lir2020                  0.1313

  maternal_pediatric_infectious_disease_lmic:
    ras9199                  0.4134
    hsc2001                  0.3191
    lja2002                  0.3191
    rnp2002                  0.2937
    lir2020                  0.2335

  maternal_adolescent_sexual_reproductive_health:
    saf7007                  0.6728
    sharkom                  0.3811
    yiz2014                  0.1229
    amg4013                  0.1224
    brs2006                  0.0953

  maternal_congenital_cmv_transmission:
    sap4017                  0.9843
    gef4003                  0.4907
    lig2002                  0.1224
    hut4001                  0.0885
    lal2018                  0.0617

  maternal_hepatitis_pregnancy:
    tak4011                  1.6650

  maternal_placenta_accreta_imaging:
    stchasen                 0.5444
    dwskupsk                 0.2787
    jes9188                  0.2787
    amk9080                  0.1932
    asq2001                  0.1853

  maternal_medicaid_insurance_child_welfare:
    anm4001                  0.8049
    emm4010                  0.6722

  maternal_neonatal_resuscitation_preterm_care:
    vjk9004                  0.2145
    ema9066                  0.1900
    afh9005                  0.1526
    lkb9003                  0.1526
    zes9010                  0.1526

  maternal_penicillin_allergy_obstetric:
    mos7003                  1.3414

  maternal_tb_pregnancy:
    jsm9009                  0.8824
    var4002                  0.3907

  maternal_pediatric_obesity_nutrition:
    mac9232                  0.2574
    cjg7003                  0.1629
    sjc7004                  0.1024
    ama2006                  0.0960
    kac2047                  0.0960

  maternal_neonatal_microbiome_immunity:
    sap4017                  0.2766
    kmv4001                  0.2699
    myz4001                  0.1623

  maternal_preterm_neurodevelopment:
    smb9040                  0.1399
    tes9045                  0.1262
    zuz4001                  0.0865

  maternal_uterine_fibroid_fertility:
    ana2348                  0.1246
    aim4006                  0.0846
    kjp9013                  0.0835

=== backfill_topic.py complete ===
  topic:           maternal_child_health
  activity_count:  303
  cluster_count:   30
  coverage_pct:    0.7558
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_maternal_child_health.json


-- stderr --
d=24, failed=0, elapsed=19s
17:07:46 INFO [progress] 200/303 processed: assigned=175, unassigned=25, failed=0, elapsed=25s
17:07:52 INFO [progress] 250/303 processed: assigned=224, unassigned=26, failed=0, elapsed=32s
17:08:02 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:08:02 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:08:02 INFO [progress] 300/303 processed: assigned=274, unassigned=26, failed=0, elapsed=41s
17:08:02 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:08:04 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:08:05 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:08:05 INFO [progress] 303/303 processed: assigned=277, unassigned=26, failed=0, elapsed=45s
17:08:05 INFO [pass 2: assign] complete
17:08:05 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic maternal_child_health
17:08:06 INFO Querying DynamoDB partition: TOPIC#maternal_child_health
17:08:06 INFO Fetched 516 SCORE# rows for maternal_child_health
17:08:06 INFO Aggregated: 471 rows included, 45 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
17:08:06 INFO Faculty touched: 186
17:08:47 INFO Writes: cleared=186, written=186, dry_run=False
17:08:47 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_maternal_child_health.json
17:08:47 INFO [pass 3: aggregate] complete
17:08:47 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_maternal_child_health.json

```
| pain_management_anesthesiology | 298 | 28 | 89.6% | 3 |  |  | ok |

<!-- === pain_management_anesthesiology === rc=0 -->
```
     0.3777
    mic2039                  0.3756
    efw9005                  0.3244

  pain_headache_nerve_decompression:
    lig4013                  3.8706
    roj9068                  0.4488
    mar9391                  0.1552
    sev9014                  0.1552
    jpgreenf                 0.0368

  pain_neuropathic_pain_mechanisms:
    pag2014                  0.6019
    lig4013                  0.5332
    ram2045                  0.4865
    chg4001                  0.3463
    cnp9004                  0.3463

  pain_pain_assessment_older_adults:
    mcr2004                  2.5698
    acr2213                  0.6742
    mrd2006                  0.3718
    dkiosses                 0.2147
    ldravdin                 0.2147

  pain_complementary_integrative_palliative:
    jrs9012                  0.7282
    roh9005                  0.4143
    nem9015                  0.4143
    ack2003                  0.4143
    jow9039                  0.4143

  pain_cancer_pain_opioids:
    yub2003                  0.9220
    mcr2004                  0.7107
    hmz7001                  0.5917
    lrw9003                  0.2113
    shr4009                  0.2103

  pain_migraine_pharmacotherapy:
    mar9391                  1.0489
    lig4013                  0.4283
    asn4004                  0.3564
    sev9014                  0.3493
    nib4005                  0.2027

  pain_opioid_biology_addiction:
    tmilner                  0.3363
    col2004                  0.3284
    fslee                    0.3284
    jtl2003                  0.3284
    krp2013                  0.3284

  pain_prostate_biopsy_urologic_procedures:
    jch9011                  0.5360
    djm9016                  0.3425
    tim9047                  0.3425
    dag9025                  0.1935
    gew9003                  0.1935

  pain_intraoperative_opioid_cancer_outcomes:
    jos9335                  0.6794
    jgc9012                  0.4349
    ban9003                  0.0846
    hok9010                  0.0846
    mfg9004                  0.0846

  pain_minimally_invasive_spine_surgery:
    kdr9004                  0.4352
    ibh9004                  0.4307
    roh9005                  0.4283

  pain_pelvic_pain_urologic:
    lvr9004                  0.9979
    kis2007                  0.1338
    rsw9004                  0.1338

  pain_breast_reconstruction_sensation:
    dmo9004                  0.6210
    lig4013                  0.2563
    lec9030                  0.1814

  pain_sleep_neurophysiology_pain:
    dpc2003                  0.3064
    sas9204                  0.2252
    zag9005                  0.2252
    prd2009                  0.1089
    nds2001                  0.0812

  pain_cervical_spine_surgery_outcomes:
    kdr9004                  0.8052
    roh9005                  0.0542

  pain_sickle_cell_musculoskeletal_comorbidity:
    mcr2004                  0.2863
    has9032                  0.1976
    ves4007                  0.1187
    nem9015                  0.0617
    rsw9006                  0.0617

  pain_pediatric_opioid_sedation:
    jrs7002                  0.2093
    haw9006                  0.1842
    hnm9004                  0.1251
    chr9008                  0.0986

  pain_intervertebral_disc_degeneration:
    roh9005                  0.2774
    jrs9012                  0.2188
    kdr9004                  0.0924

  pain_lung_transplant_perioperative:
    juf4007                  0.5835

  pain_perioperative_transfusion_blood_management:
    mec2013                  0.1262
    rod9096                  0.1262
    ceh2003                  0.1187
    man9026                  0.0885

=== backfill_topic.py complete ===
  topic:           pain_management_anesthesiology
  activity_count:  298
  cluster_count:   28
  coverage_pct:    0.896
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_pain_management_anesthesiology.json


-- stderr --
ock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:11:11 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:11:13 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:11:13 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:11:13 INFO [progress] 298/298 processed: assigned=288, unassigned=10, failed=0, elapsed=43s
17:11:14 INFO [pass 2: assign] complete
17:11:14 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic pain_management_anesthesiology
17:11:14 INFO Querying DynamoDB partition: TOPIC#pain_management_anesthesiology
17:11:14 INFO Fetched 501 SCORE# rows for pain_management_anesthesiology
17:11:14 INFO Aggregated: 483 rows included, 18 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
17:11:14 INFO Faculty touched: 191
17:11:35 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=aab9028: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
17:11:53 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=mic2039: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
17:11:55 INFO Writes: cleared=189, written=191, dry_run=False
17:11:55 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_pain_management_anesthesiology.json
17:11:55 INFO [pass 3: aggregate] complete
17:11:55 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_pain_management_anesthesiology.json

```
| digital_health_telemedicine | 276 | 29 | 90.9% | 3 |  |  | ok |

<!-- === digital_health_telemedicine === rc=0 -->
```
gital_caregiver_homecare_support:
    mrs9012                  0.9318
    ibk9003                  0.5377
    sjc7004                  0.4524
    did2005                  0.3937
    jiy4002                  0.3504

  digital_telehealth_equity_access:
    anr2783                  0.6070
    ras2022                  0.4724
    brd9088                  0.4724
    rharrington              0.3214
    jiy4002                  0.2618

  digital_vr_ar_simulation:
    alf9065                  0.9185
    wab4001                  0.6311
    sjc7004                  0.6311
    roj9068                  0.2463
    raz2002                  0.2027

  digital_older_adults_technology_adoption:
    wab4001                  1.6711
    sjc7004                  1.3713
    jhm2006                  0.8299

  digital_ai_ml_clinical_applications:
    ara4013                  0.5579
    aaa4027                  0.4683
    nem9015                  0.3487
    ram2045                  0.3263
    mac9232                  0.2964

  digital_cognitive_training_older_adults:
    wab4001                  2.3164
    sjc7004                  1.4878

  digital_patient_portals_health_records:
    aaa4027                  0.5705
    jac9009                  0.2846
    jee9054                  0.2846
    jtg9003                  0.2846
    sharkom                  0.2846

  digital_telehealth_surgical_specialty:
    jiy4002                  0.4628
    lig4013                  0.4437
    ras2022                  0.2823
    hah9020                  0.2823
    gjl9003                  0.2823

  digital_chatbots_conversational_agents:
    aaa4027                  1.7917
    ara4013                  0.8740

  digital_social_media_online_health:
    yiz2014                  0.5671
    mdkatz                   0.4119
    aaa4027                  0.1561
    ara4013                  0.1561
    mfg9004                  0.1552

  digital_harm_reduction_substance_use:
    czb2002                  0.5876
    brs2006                  0.5468
    shk9078                  0.5468
    emm4010                  0.3164
    jiy4002                  0.2268

  digital_elder_abuse_protection:
    jsirey                   0.3585
    sab2028                  0.3585
    nis2051                  0.2496
    aer2006                  0.1897
    esc4003                  0.1897

  digital_acoustic_biosignal_detection:
    jes4028                  0.3422
    bcl2004                  0.2535
    bom2008                  0.2535
    hew3001                  0.2535
    wfb9002                  0.2535

  digital_clinical_informatics_hospital_operations:
    thc2015                  0.2226
    jtg9003                  0.2226
    agm9023                  0.2147
    ras2022                  0.1767
    hah9020                  0.1767

  digital_telehealth_global_lmic:
    roh9005                  0.5934
    ibh9004                  0.5934

  digital_covid_pandemic_health_system_response:
    aaa4027                  0.2043
    keg2007                  0.1397
    liw9021                  0.1397
    mgk2002                  0.1397
    nib9009                  0.1397

  digital_mhealth_vulnerable_populations:
    lct4001                  0.2702
    jut4006                  0.2702
    alj4004                  0.1853
    smm2010                  0.1853

  digital_video_patient_education:
    hlipkind                 0.1221
    mos7003                  0.1221
    bmf9002                  0.0859
    cha9043                  0.0859
    cvc9002                  0.0859

  digital_college_student_mental_health:
    juc4013                  0.3711
    tdb2002                  0.1300

=== backfill_topic.py complete ===
  topic:           digital_health_telemedicine
  activity_count:  276
  cluster_count:   29
  coverage_pct:    0.9094
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_digital_health_telemedicine.json


-- stderr --
:13:57 INFO [progress] 150/276 processed: assigned=140, unassigned=10, failed=0, elapsed=22s
17:14:04 INFO [progress] 200/276 processed: assigned=190, unassigned=10, failed=0, elapsed=29s
17:14:11 INFO [progress] 250/276 processed: assigned=240, unassigned=10, failed=0, elapsed=36s
17:14:14 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:14:14 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:14:14 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:14:14 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:14:14 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:14:15 INFO [progress] 276/276 processed: assigned=266, unassigned=10, failed=0, elapsed=40s
17:14:15 INFO [pass 2: assign] complete
17:14:15 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic digital_health_telemedicine
17:14:15 INFO Querying DynamoDB partition: TOPIC#digital_health_telemedicine
17:14:16 INFO Fetched 590 SCORE# rows for digital_health_telemedicine
17:14:16 INFO Aggregated: 566 rows included, 24 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
17:14:16 INFO Faculty touched: 246
17:15:09 INFO Writes: cleared=246, written=246, dry_run=False
17:15:09 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_digital_health_telemedicine.json
17:15:09 INFO [pass 3: aggregate] complete
17:15:09 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_digital_health_telemedicine.json

```
| bioethics_medical_humanities | 275 | 28 | 88.7% | 3 |  |  | ok |

<!-- === bioethics_medical_humanities === rc=0 -->
```
          0.2337

  bioethics_research_ethics_consent:
    imd2001                  0.8489
    sharkom                  0.3510
    jur9123                  0.3379
    thc2015                  0.3109
    ccole                    0.2492

  bioethics_reproductive_ethics:
    imd2001                  0.5013
    dls7001                  0.4783
    cah4023                  0.2211
    jna2002                  0.2129
    ras9199                  0.2129

  bioethics_patient_perspectives_treatment:
    jdw2002                  0.1897
    pbc2001                  0.1897
    dam2034                  0.1571
    jak9060                  0.1571
    rsw9006                  0.1571

  bioethics_health_equity_access:
    jjfins                   0.1885
    dem9199                  0.1885
    jch9011                  0.1629
    chb9074                  0.1629
    dag9025                  0.1629

  bioethics_palliative_care_ethics:
    das2043                  0.5423
    mcr2004                  0.3617
    acr2213                  0.2772
    rdadelma                 0.1476
    jhm2006                  0.1296

  bioethics_disability_crisis_standards:
    haw9006                  0.4845
    jjfins                   0.4620
    roj9068                  0.1714
    rsw9006                  0.1714
    hgp2001                  0.1552

  bioethics_caregiver_support:
    acr2213                  0.4044
    sjc7004                  0.3722
    rdadelma                 0.2824
    dwh4001                  0.1890
    mcr2004                  0.1833

  bioethics_religion_culture_clinical_ethics:
    ezg9002                  0.6077
    jjfins                   0.4398
    haa2019                  0.2159
    and2033                  0.0923
    lig2002                  0.0923

  bioethics_gender_diversity_workforce:
    mfw4002                  0.3096
    pfi9001                  0.3096
    anr2783                  0.1398
    hey9002                  0.1259
    kad9090                  0.1221

  bioethics_ai_clinical_tools:
    alh9039                  0.1321
    roj9069                  0.1321
    yiz2014                  0.1321
    ras2022                  0.1105
    alf9065                  0.1105

  bioethics_physician_workload_practice:
    mau2006                  0.1480
    fto2002                  0.1480
    lib9050                  0.1388
    lig2002                  0.1271
    kac9091                  0.1271

  bioethics_ems_hospital_protocols:
    aer2006                  0.1529
    mslachs                  0.1529
    dwh4001                  0.1529
    lct4001                  0.1356
    shp9079                  0.0923

  bioethics_cognitive_bias_clinical_decision:
    dip4011                  0.2544
    thi9003                  0.2103
    juc9107                  0.1254
    mam2080                  0.1254

  bioethics_stigma_substance_use:
    emm4010                  0.2670
    joa9070                  0.1885
    jjfins                   0.1446
    tdb2002                  0.1122

  bioethics_nursing_leadership_resilience:
    zrm2001                  0.3081
    emh2002                  0.2067
    mrs9012                  0.0508
    mms9024                  0.0508
    pag9051                  0.0508

  bioethics_asylum_immigration_medicine:
    tcp2003                  0.2429
    anm2119                  0.2090
    gus2004                  0.0544

  bioethics_cancer_survivorship_communication:
    est2003                  0.1885
    shr4009                  0.1457

  bioethics_maid_end_of_life_practice:
    vuk9003                  0.2494

  bioethics_geriatric_care_aging:
    pag9051                  0.1172

=== backfill_topic.py complete ===
  topic:           bioethics_medical_humanities
  activity_count:  275
  cluster_count:   28
  coverage_pct:    0.8873
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_bioethics_medical_humanities.json


-- stderr --
:17:07 INFO [progress] 150/275 processed: assigned=149, unassigned=1, failed=0, elapsed=22s
17:17:13 INFO [progress] 200/275 processed: assigned=199, unassigned=1, failed=0, elapsed=29s
17:17:20 INFO [progress] 250/275 processed: assigned=248, unassigned=2, failed=0, elapsed=36s
17:17:23 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:17:23 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:17:23 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:17:24 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:17:24 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:17:24 INFO [progress] 275/275 processed: assigned=273, unassigned=2, failed=0, elapsed=40s
17:17:24 INFO [pass 2: assign] complete
17:17:24 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic bioethics_medical_humanities
17:17:24 INFO Querying DynamoDB partition: TOPIC#bioethics_medical_humanities
17:17:25 INFO Fetched 508 SCORE# rows for bioethics_medical_humanities
17:17:25 INFO Aggregated: 505 rows included, 3 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
17:17:25 INFO Faculty touched: 246
17:18:17 INFO Writes: cleared=246, written=246, dry_run=False
17:18:17 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_bioethics_medical_humanities.json
17:18:17 INFO [pass 3: aggregate] complete
17:18:17 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_bioethics_medical_humanities.json

```
| stem_cell_regenerative_medicine | 265 | 30 | 83.0% | 3 |  |  | ok |

<!-- === stem_cell_regenerative_medicine === rc=0 -->
```
pithelial_progenitors:
    rgcryst                  0.8417
    rkaner                   0.5678
    res2025                  0.3902
    shc2034                  0.3902
    jzx2002                  0.3902

  stem_zebrafish_heart_regeneration:
    jic4001                  1.7893
    mrh4003                  0.9124
    dob2014                  0.7288
    tre2003                  0.5060
    eah9008                  0.2564

  stem_aml_leukemia_stem_cell_therapy:
    cem2009                  1.0276
    chm2042                  1.0276
    gar2001                  0.5086
    mlg2007                  0.5086
    mrt2001                  0.4357

  stem_ovarian_follicle_theca_biology:
    zrosenw                  0.9522
    djj2001                  0.4924
    lim2030                  0.4924
    gdpalerm                 0.4598
    glschatt                 0.4316

  stem_crispr_genome_editing_models:
    duw2001                  0.5678
    lud2005                  0.5497
    ole2001                  0.4947
    elt4010                  0.3376
    varmus                   0.3371

  stem_intestinal_stem_cell_niche:
    nog4004                  0.5550
    jom4010                  0.4819
    mdu4003                  0.4819
    mtd4001                  0.4819
    ggi9001                  0.4211

  stem_tissue_engineering_scaffolds:
    jas2037                  2.9184
    smukherj                 0.1767
    dmo9004                  0.0915
    dik2002                  0.0542
    wkuhel                   0.0542

  stem_kras_oncogenic_cancer_models:
    lud2005                  0.9344
    chm2042                  0.2835
    ala2035                  0.1770
    des2025                  0.1770
    emh9016                  0.1770

  stem_nerve_regeneration_neuropathy:
    dob2014                  0.3134
    zhw4007                  0.3134
    inp2002                  0.3084
    ram2045                  0.3084
    zrm2001                  0.3084

  stem_tau_neurodegeneration_microglia:
    lig2033                  0.6667
    lif4001                  0.3984
    gim2004                  0.2683
    shg3006                  0.2683
    wel2009                  0.2683

  stem_hsc_transplantation_clinical:
    sca2002                  0.5667
    jub2029                  0.3329
    alg9117                  0.1274
    tsc9008                  0.1089
    fyh9001                  0.0766

  stem_adipocyte_differentiation_metabolism:
    mnt4002                  0.5895
    frs4001                  0.2236
    nam2016                  0.2115
    jom2042                  0.1423
    kas2049                  0.1423

  stem_wnt_signaling_development:
    tre2003                  0.3405
    app2006                  0.3405
    efa2001                  0.3405
    mas4011                  0.3405
    rin7007                  0.2630

  stem_hepatocyte_liver_regeneration:
    res2025                  0.6679
    ydj2001                  0.5916
    bae2008                  0.2159

  stem_wound_healing_regenerative_therapies:
    jas2037                  0.3298
    anf9193                  0.2670
    jrs9012                  0.1671
    dmo9004                  0.1236
    mic2039                  0.0807

  stem_intervertebral_disc_regeneration:
    roh9005                  0.5635
    jrs9012                  0.1901

  stem_breast_stem_cell_markers_risk:
    rmt4001                  0.6938

  stem_m6a_rna_modification:
    srj2003                  0.4266

  stem_patient_derived_xenograft_models:
    beh2020                  0.1573
    abd4001                  0.0885
    rbjones                  0.0885
    ydj2001                  0.0508

=== backfill_topic.py complete ===
  topic:           stem_cell_regenerative_medicine
  activity_count:  265
  cluster_count:   30
  coverage_pct:    0.8302
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_stem_cell_regenerative_medicine.json


-- stderr --
s] 150/265 processed: assigned=136, unassigned=14, failed=0, elapsed=21s
17:20:29 INFO [progress] 200/265 processed: assigned=186, unassigned=14, failed=0, elapsed=28s
17:20:36 INFO [progress] 250/265 processed: assigned=236, unassigned=14, failed=0, elapsed=35s
17:20:37 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:20:37 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:20:38 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:20:38 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:20:40 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:20:40 INFO [progress] 265/265 processed: assigned=251, unassigned=14, failed=0, elapsed=39s
17:20:40 INFO [pass 2: assign] complete
17:20:40 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic stem_cell_regenerative_medicine
17:20:40 INFO Querying DynamoDB partition: TOPIC#stem_cell_regenerative_medicine
17:20:41 INFO Fetched 652 SCORE# rows for stem_cell_regenerative_medicine
17:20:41 INFO Aggregated: 619 rows included, 33 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
17:20:41 INFO Faculty touched: 210
17:21:29 INFO Writes: cleared=210, written=210, dry_run=False
17:21:29 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_stem_cell_regenerative_medicine.json
17:21:29 INFO [pass 3: aggregate] complete
17:21:29 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_stem_cell_regenerative_medicine.json

```
| breast_cancer | 263 | 26 | 97.0% | 3 |  |  | ok |

<!-- === breast_cancer === rc=0 -->
```
               0.5186
    jas2037                  0.5045
    lig4013                  0.3004

  breast_survivorship_qol:
    shr4009                  1.2170
    rmt4001                  1.2019
    lcp2003                  0.8222
    eot9002                  0.7685
    lmk2003                  0.6497

  breast_metastasis_metabolism:
    vim2010                  1.0337
    amh2025                  0.5853
    kas2049                  0.5853
    dig2009                  0.5336
    mag3003                  0.5000

  breast_genetics_hereditary:
    mkf2002                  0.7473
    ras9030                  0.5990
    jmm9018                  0.4894
    ole2001                  0.4894
    shr4009                  0.4676

  breast_disparities_outcomes:
    lan4002                  1.3310
    vjb9003                  0.8553
    shr4009                  0.7457
    lcp2003                  0.6968
    kaz2004                  0.5669

  breast_imaging_diagnosis:
    mjc9030                  0.7818
    sgk4001                  0.7427
    jak9072                  0.6866
    kad9090                  0.6035
    mid2011                  0.4562

  breast_covid_imaging_impact:
    mid2011                  0.7281
    kad9090                  0.4890
    all2017                  0.3358
    keb2012                  0.3358
    jak9072                  0.3142

  breast_hr_positive_treatment:
    ves4007                  2.0748
    mac9795                  1.2597
    car4012                  0.3761
    shr4009                  0.3286
    rmt4001                  0.3286

  breast_aya_fertility_psychosocial:
    shr4009                  1.2730
    rmt4001                  0.9021
    lac4029                  0.9021
    ves4007                  0.1155
    glschatt                 0.0630

  breast_her2_pet_imaging_advanced:
    bab4001                  0.3467
    mac9795                  0.1972
    san2028                  0.1972
    ela9082                  0.1972
    anb9189                  0.1972

  breast_cancer_genomics_sv:
    jmm9018                  0.7184
    ole2001                  0.5955
    ans2077                  0.5955
    avp9009                  0.3992
    ekk2003                  0.1795

  breast_dcis_early_stage:
    shr4009                  0.9225
    rms2002                  0.5244
    kvy9001                  0.3286

  breast_patient_models_biomarkers:
    aswistel                 0.2786
    barany                   0.2786
    mdb2005                  0.2786
    mac9795                  0.1155
    beh2020                  0.0673

  breast_exercise_immune_microenvironment:
    szd3005                  0.4820
    rmt4001                  0.4060

  breast_cancer_screening_access_policy:
    gbm9002                  0.3092
    emm4010                  0.1412
    ban9003                  0.0992
    hok9010                  0.0992
    stt2007                  0.0992

  breast_global_health_disparities_support:
    baa2012                  0.2113
    lcp2003                  0.2067
    erp2001                  0.2067
    lan4002                  0.0781

  breast_hereditary_family_communication:
    elc9120                  0.1085
    evc2005                  0.1085
    mkf2002                  0.1085
    pac2001                  0.1085
    ras9030                  0.1085

  breast_ml_precision_oncology:
    ole2001                  0.2584
    lum4003                  0.1629
    yip4002                  0.1187

  breast_cardiotoxicity_cardiooncology:
    san2028                  0.1962
    lcp2003                  0.0812
    pag9051                  0.0812

  breast_cdk_cell_cycle_therapy:
    tom4003                  0.1105

=== backfill_topic.py complete ===
  topic:           breast_cancer
  activity_count:  263
  cluster_count:   26
  coverage_pct:    0.9696
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_breast_cancer.json


-- stderr --
ess] 100/263 processed: assigned=97, unassigned=3, failed=0, elapsed=14s
17:23:18 INFO [progress] 150/263 processed: assigned=147, unassigned=3, failed=0, elapsed=20s
17:23:24 INFO [progress] 200/263 processed: assigned=197, unassigned=3, failed=0, elapsed=26s
17:23:31 INFO [progress] 250/263 processed: assigned=247, unassigned=3, failed=0, elapsed=34s
17:23:32 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:23:32 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:23:32 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:23:32 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:23:35 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:23:36 INFO [progress] 263/263 processed: assigned=260, unassigned=3, failed=0, elapsed=38s
17:23:36 INFO [pass 2: assign] complete
17:23:36 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic breast_cancer
17:23:36 INFO Querying DynamoDB partition: TOPIC#breast_cancer
17:23:36 INFO Fetched 540 SCORE# rows for breast_cancer
17:23:36 INFO Aggregated: 535 rows included, 5 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
17:23:36 INFO Faculty touched: 179
17:24:15 INFO Writes: cleared=179, written=179, dry_run=False
17:24:15 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_breast_cancer.json
17:24:15 INFO [pass 3: aggregate] complete
17:24:15 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_breast_cancer.json

```
| rehabilitation_disability_medicine | 258 | 30 | 83.7% | 3 |  |  | ok |

<!-- === rehabilitation_disability_medicine === rc=0 -->
```
disorders_of_consciousness:
    jjfins                   1.2010
    nds2001                  0.7944
    jdvicto                  0.3728
    sut2006                  0.1354

  rehabilitation_multiple_sclerosis_disability:
    amk2012                  0.6140
    sag2015                  0.6140
    tdn2001                  0.3008
    uwk9002                  0.3008
    ram2045                  0.2099

  rehabilitation_spinal_injury_lmic:
    roh9005                  1.3633
    ibh9004                  0.2423
    dob2014                  0.1473
    zhw4007                  0.1473

  rehabilitation_orofacial_respiratory_strength:
    anr2783                  0.4972
    yrj9003                  0.4972
    mam4041                  0.1207

  rehabilitation_hearing_loss_communication:
    pag9051                  0.1659
    mrs9012                  0.1659
    est2003                  0.1571
    aer2006                  0.0846
    anr2783                  0.0846

  rehabilitation_voice_swallowing_disorders:
    anr2783                  0.3920
    lus2005                  0.1723
    odk9003                  0.1723
    aer2006                  0.0846
    mslachs                  0.0846

  rehabilitation_cancer_rehabilitation_neuro_oncology:
    nac9076                  0.2731
    krd4004                  0.2438
    hab9075                  0.1809
    est2003                  0.0846
    ves4007                  0.0720

  rehabilitation_lumbar_spine_surgery:
    kdr9004                  0.7371
    roh9005                  0.1004

  rehabilitation_autism_social_skills:
    hab4003                  0.1254
    tab2006                  0.1229
    abt4002                  0.0929
    bflye                    0.0929
    cos2006                  0.0929

  rehabilitation_fall_prevention_older_adults:
    mis4060                  0.1596
    mrd2006                  0.0953
    akg9010                  0.0953
    arj2005                  0.0953
    mau2006                  0.0953

  rehabilitation_older_adults_hiv_function:
    cmd9008                  0.2672
    mag2005                  0.2672
    yuz2002                  0.1915

  rehabilitation_osseointegration_prosthetics:
    dmo9004                  0.6698

  rehabilitation_myofascial_pain_interventions:
    jes9343                  0.1659
    kvy9001                  0.1659
    jrs9012                  0.1254
    roj9068                  0.1021
    efw9005                  0.1021

  rehabilitation_neuropsychology_neurosurgery:
    abj2006                  0.3380
    hab9075                  0.2338
    mmsouwei                 0.0675

  rehabilitation_pm_r_training_workforce:
    prd2009                  0.3250
    arj2005                  0.1529
    mau2006                  0.1529

  rehabilitation_telehealth_remote_care:
    brd9088                  0.1155
    ras2022                  0.1155
    ama2006                  0.1004
    soc2005                  0.1004
    arl2017                  0.0490

  rehabilitation_cervical_spine_surgery:
    kdr9004                  0.3794
    roh9005                  0.1288
    ibh9004                  0.0236

  rehabilitation_breast_reconstruction_sensation:
    dmo9004                  0.4156
    lig4013                  0.0846

  rehabilitation_parkinson_sleep_wearables:
    has9059                  0.1955
    aaa4027                  0.1355
    suy9023                  0.0654

  rehabilitation_intrathecal_neurostimulation:
    jpgreenf                 0.0853
    dmo9004                  0.0525
    ahmedsh                  0.0385
    nem9015                  0.0385
    roj9068                  0.0385

=== backfill_topic.py complete ===
  topic:           rehabilitation_disability_medicine
  activity_count:  258
  cluster_count:   30
  coverage_pct:    0.8372
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_rehabilitation_disability_medicine.json


-- stderr --
, elapsed=32s
17:26:25 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:26:25 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:26:25 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:26:27 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:26:28 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:26:28 INFO [progress] 258/258 processed: assigned=243, unassigned=15, failed=0, elapsed=36s
17:26:28 INFO [pass 2: assign] complete
17:26:28 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic rehabilitation_disability_medicine
17:26:29 INFO Querying DynamoDB partition: TOPIC#rehabilitation_disability_medicine
17:26:29 INFO Fetched 452 SCORE# rows for rehabilitation_disability_medicine
17:26:29 INFO Aggregated: 430 rows included, 22 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
17:26:29 INFO Faculty touched: 157
17:27:01 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=lqg9001: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
17:27:02 INFO Writes: cleared=156, written=157, dry_run=False
17:27:02 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_rehabilitation_disability_medicine.json
17:27:02 INFO [pass 3: aggregate] complete
17:27:03 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_rehabilitation_disability_medicine.json

```
| single_cell_spatial_biology | 252 | 30 | 82.9% | 3 |  |  | ok |

<!-- === single_cell_spatial_biology === rc=0 -->
```
 zrosenw                  1.3523
    lim2030                  1.3523
    glschatt                 1.2521
    nizanin                  1.2521

  single_hematopoiesis_clonal_evolution:
    dal3005                  1.8101
    shn9035                  1.1694
    chm2042                  0.6969
    cem2009                  0.6969
    ggi9001                  0.5780

  single_lymphoma_b_cell_genomics:
    ole2001                  0.8834
    mrt2001                  0.6310
    achadbur                 0.5959
    ggi9001                  0.5462
    zhc2006                  0.5021

  single_ilc_gut_immunity:
    gfs2002                  1.7726
    maa4016                  1.4762
    ros2023                  1.3994
    mel4003                  1.0081
    wez4002                  0.6145

  single_cancer_immunotherapy_combination:
    dig2009                  0.4268
    kaz2004                  0.3869
    dah4023                  0.2752
    jdw2002                  0.2752
    sab4028                  0.2752

  single_heart_regeneration_cardiomyocyte:
    jic4001                  0.9537
    dob2014                  0.6940
    res2025                  0.4580
    shc2034                  0.4580
    tre2003                  0.4580

  single_kidney_transplant_transcriptomics:
    mut9002                  1.0605
    dmd2001                  0.7468
    msuthan                  0.7468
    kas2049                  0.5866
    ala2035                  0.4542

  single_colorectal_cancer_microenvironment:
    ggi9001                  0.3562
    jom4010                  0.3562
    jul4008                  0.3562
    mdu4003                  0.3562
    mtd4001                  0.3562

  single_scrna_methods_tools:
    pcw4001                  0.7543
    sic4001                  0.7543
    him4004                  0.5005
    kwd2001                  0.4517
    elt4010                  0.3219

  single_proteomics_surfaceome:
    frs4001                  0.2837
    kas2049                  0.2293
    dcl2001                  0.2282
    haz2005                  0.2282
    irm2224                  0.2282

  single_chromosomal_instability_cancer_progression:
    asl4003                  0.8826
    ggi9001                  0.2252
    jom4010                  0.2252
    jul4008                  0.2252
    mdu4003                  0.2252

  single_liver_zonation_fibrosis:
    res2025                  0.5933
    emh9016                  0.3727
    mrr2006                  0.2952
    pleopold                 0.2952
    rgcryst                  0.2952

  single_lupus_autoimmune_innate:
    vip2021                  0.1950
    zuw4001                  0.1950
    jig4003                  0.1950
    sic2011                  0.1950
    jhzippin                 0.1562

  single_skeletal_bone_metastasis:
    mag3003                  0.5641
    seb4003                  0.3606
    vim2010                  0.2315
    djp2002                  0.1291
    ceh2003                  0.1291

  single_stem_cell_reprogramming_pluripotency:
    app2006                  0.5506
    efa2001                  0.3326
    dob2014                  0.1442
    tre2003                  0.1412
    mas4011                  0.1412

  single_adipocyte_differentiation_metabolism:
    mnt4002                  0.6789
    frs4001                  0.1769
    kas2049                  0.0904
    nam2016                  0.0904

  single_circulating_tumor_cells_liquid_biopsy:
    car4012                  0.5333
    mac9795                  0.3834

  single_cell_cycle_differentiation_commitment:
    tom4003                  0.4861
    mnt4002                  0.2113

=== backfill_topic.py complete ===
  topic:           single_cell_spatial_biology
  activity_count:  252
  cluster_count:   30
  coverage_pct:    0.8294
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_single_cell_spatial_biology.json


-- stderr --
s
17:29:18 INFO [progress] 150/252 processed: assigned=143, unassigned=7, failed=0, elapsed=26s
17:29:26 INFO [progress] 200/252 processed: assigned=192, unassigned=8, failed=0, elapsed=34s
17:29:34 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:29:34 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:29:34 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:29:34 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:29:34 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:29:34 INFO [progress] 250/252 processed: assigned=242, unassigned=8, failed=0, elapsed=42s
17:29:34 INFO [progress] 252/252 processed: assigned=244, unassigned=8, failed=0, elapsed=43s
17:29:35 INFO [pass 2: assign] complete
17:29:35 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic single_cell_spatial_biology
17:29:35 INFO Querying DynamoDB partition: TOPIC#single_cell_spatial_biology
17:29:35 INFO Fetched 848 SCORE# rows for single_cell_spatial_biology
17:29:35 INFO Aggregated: 827 rows included, 21 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
17:29:35 INFO Faculty touched: 271
17:30:33 INFO Writes: cleared=271, written=271, dry_run=False
17:30:33 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_single_cell_spatial_biology.json
17:30:33 INFO [pass 3: aggregate] complete
17:30:34 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_single_cell_spatial_biology.json

```
| neurodegenerative_disease | 251 | 29 | 91.2% | 3 |  |  | ok |

<!-- === neurodegenerative_disease === rc=0 -->
```
    slr4003                  1.7582
    dae2005                  0.8611
    jab2058                  0.5448
    mas2189                  0.5448

  neurodegenerative_alzheimer_subtyping_ml:
    few2001                  1.8727
    chs4001                  1.4332
    shh4006                  0.6573
    yiz2014                  0.4395
    lbm7002                  0.3099

  neurodegenerative_drug_repurposing_computational:
    few2001                  1.2735
    chz4001                  0.8214
    chs4001                  0.4521
    all9188                  0.4154
    yip4002                  0.4154

  neurodegenerative_single_cell_genomics_brain:
    lndhlovu                 0.5012
    hut2006                  0.5012
    tmilner                  0.5012
    jab2058                  0.4395
    mas2189                  0.4395

  neurodegenerative_pd_metabolism_synaptic:
    dae2005                  0.9019
    jab2058                  0.8111
    mas2189                  0.8111
    taryan                   0.5346
    mik2002                  0.5346

  neurodegenerative_alzheimer_metabolism_bioenergetics:
    jak2043                  1.5566
    dae2005                  0.5714
    lbm7002                  0.5384
    kas2049                  0.5042
    taryan                   0.1986

  neurodegenerative_parkinsons_dysphagia_clinical:
    has9059                  1.2995
    yrj9003                  0.8867
    aaa4027                  0.3301
    suy9023                  0.2812
    few2001                  0.2416

  neurodegenerative_peripheral_immune_ad:
    gcc9004                  0.4841
    liz2018                  0.4841
    tab2006                  0.4841
    yil4008                  0.4841
    lbm7002                  0.4841

  neurodegenerative_tbi_tau_neurodegeneration:
    djs4002                  0.2612
    ekf4001                  0.2218
    gcc9004                  0.2218
    lig4005                  0.2218
    liz2018                  0.2218

  neurodegenerative_dementia_caregiving_palliative:
    sjc7004                  0.3708
    mcr2004                  0.2876
    jhm2006                  0.2131
    yoz2009                  0.1842
    acr2213                  0.1808

  neurodegenerative_covid_neurological:
    lal2018                  0.5192
    res2025                  0.5192
    shc2034                  0.5192
    tre2003                  0.5192
    has9059                  0.1972

  neurodegenerative_brain_mri_methods:
    yiwang                   0.5125
    qiz4006                  0.3121
    pas2018                  0.2592
    tdn2001                  0.2210
    eje9005                  0.1246

  neurodegenerative_hiv_neurocognitive:
    lndhlovu                 0.2085
    cmd9008                  0.1694
    mag2005                  0.1694
    mec2025                  0.1694
    evering                  0.1103

  neurodegenerative_depression_cognitive_decline:
    jos9335                  0.2905
    col2004                  0.1246
    fgd2002                  0.1246
    liv3002                  0.1246
    juz4004                  0.1089

  neurodegenerative_cognitive_training_mci:
    sjc7004                  0.5937
    wab4001                  0.1773

  neurodegenerative_mitochondrial_complex_disease:
    alg2057                  0.1684
    gim2004                  0.1122
    joa2006                  0.1122
    jup9003                  0.0992
    vij4004                  0.0992

  neurodegenerative_aging_computational_transcriptomics:
    dob2014                  0.3477
    qiz4006                  0.0787

  neurodegenerative_niemann_pick_npc:
    frmaxfie                 0.1420

=== backfill_topic.py complete ===
  topic:           neurodegenerative_disease
  activity_count:  251
  cluster_count:   29
  coverage_pct:    0.9124
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_neurodegenerative_disease.json


-- stderr --
elapsed=16s
17:32:48 INFO [progress] 150/251 processed: assigned=145, unassigned=5, failed=0, elapsed=23s
17:32:56 INFO [progress] 200/251 processed: assigned=194, unassigned=6, failed=0, elapsed=31s
17:33:03 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:33:03 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:33:03 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:33:04 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:33:04 INFO [progress] 250/251 processed: assigned=244, unassigned=6, failed=0, elapsed=39s
17:33:07 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:33:07 INFO [progress] 251/251 processed: assigned=245, unassigned=6, failed=0, elapsed=42s
17:33:07 INFO [pass 2: assign] complete
17:33:07 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic neurodegenerative_disease
17:33:07 INFO Querying DynamoDB partition: TOPIC#neurodegenerative_disease
17:33:07 INFO Fetched 692 SCORE# rows for neurodegenerative_disease
17:33:07 INFO Aggregated: 682 rows included, 10 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
17:33:07 INFO Faculty touched: 188
17:33:46 INFO Writes: cleared=188, written=188, dry_run=False
17:33:46 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_neurodegenerative_disease.json
17:33:46 INFO [pass 3: aggregate] complete
17:33:46 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_neurodegenerative_disease.json

```
| biomedical_engineering | 242 | 30 | 83.5% | 3 |  |  | ok |

<!-- === biomedical_engineering === rc=0 -->
```
dical_breast_implant_reconstruction:
    jas2037                  2.1867
    dmo9004                  0.5802
    mdlieber                 0.1596
    osc4001                  0.1024
    lec9030                  0.0673

  biomedical_leadless_cardiac_devices:
    jei9008                  1.3734
    blerman                  0.2818
    chl7001                  0.2535
    get2007                  0.2059
    roj9068                  0.1389

  biomedical_ai_deep_learning_medical_imaging:
    ges9006                  0.3285
    map2008                  0.3285
    anr2783                  0.2856
    jop4027                  0.2822
    yiwang                   0.2680

  biomedical_acoustic_ultrasound_biomedical_tools:
    bcl2004                  0.3203
    bom2008                  0.3203
    hew3001                  0.3203
    wfb9002                  0.3203
    gdpalerm                 0.2618

  biomedical_ocular_biomechanics_devices:
    sjh2006                  0.4289
    kyk9011                  0.3430
    cah4016                  0.2677
    jom4032                  0.2677
    djd2003                  0.1529

  biomedical_orofacial_respiratory_strength:
    anr2783                  0.8617
    yrj9003                  0.7114
    jas2037                  0.0992

  biomedical_telemedicine_ar_remote_surgery:
    bom2008                  0.2702
    als2076                  0.2702
    alf9065                  0.2252
    ibh9004                  0.2153
    roh9005                  0.2153

  biomedical_spine_implants_fusion:
    roh9005                  0.5711
    kdr9004                  0.2698
    sbk9015                  0.1271
    mag3003                  0.0904
    seb4003                  0.0904

  biomedical_seizure_detection_neurological_monitoring:
    jes4028                  0.4523
    nds2001                  0.1604
    jdvicto                  0.1604
    jup9003                  0.1526
    vij4004                  0.1526

  biomedical_robotic_surgical_systems:
    cha9043                  0.2049
    grd9006                  0.2049
    jgc9012                  0.1649
    jdg4001                  0.1153
    mmr2011                  0.0812

  biomedical_minimally_invasive_spine_endoscopy:
    ibh9004                  0.6162
    roh9005                  0.1757
    sbk9015                  0.0924

  biomedical_cardiac_regeneration_cardiomyocytes:
    trk2002                  0.2021
    map2007                  0.1932
    stp9039                  0.1932
    mrh4003                  0.1075
    bek4011                  0.0859

  biomedical_point_of_care_diagnostics:
    chm2042                  0.2372
    ram2045                  0.1532
    inp2002                  0.1532
    ema9066                  0.1035

  biomedical_optogenetics_retinal_neural:
    boy2004                  0.2699
    shn2010                  0.2699
    mab4092                  0.0675

  biomedical_surgical_ergonomics_training:
    anr2783                  0.2012
    jrs9012                  0.1730
    efw9005                  0.0560
    jer9173                  0.0560
    roj9068                  0.0560

  biomedical_breast_reconstruction_techniques:
    lig4013                  0.1663
    rms2002                  0.1562
    dmo9004                  0.1074
    mrd2006                  0.1074

  biomedical_assistive_tech_cognitive_aging:
    wab4001                  0.2285
    sjc7004                  0.1074
    anf9193                  0.0580

  biomedical_osseointegrated_prostheses:
    dmo9004                  0.3316

  biomedical_brain_vascular_hemodynamics:
    sua2018                  0.2067
    osp9003                  0.0657

=== backfill_topic.py complete ===
  topic:           biomedical_engineering
  activity_count:  242
  cluster_count:   30
  coverage_pct:    0.8347
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_biomedical_engineering.json


-- stderr --
ned=2, failed=0, elapsed=7s
17:35:44 INFO [progress] 100/242 processed: assigned=94, unassigned=6, failed=0, elapsed=13s
17:35:50 INFO [progress] 150/242 processed: assigned=143, unassigned=7, failed=0, elapsed=19s
17:35:57 INFO [progress] 200/242 processed: assigned=193, unassigned=7, failed=0, elapsed=25s
17:36:01 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:36:01 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:36:01 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:36:01 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:36:02 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:36:02 INFO [progress] 242/242 processed: assigned=234, unassigned=8, failed=0, elapsed=31s
17:36:02 INFO [pass 2: assign] complete
17:36:02 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic biomedical_engineering
17:36:02 INFO Querying DynamoDB partition: TOPIC#biomedical_engineering
17:36:02 INFO Fetched 432 SCORE# rows for biomedical_engineering
17:36:02 INFO Aggregated: 420 rows included, 12 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
17:36:02 INFO Faculty touched: 188
17:36:42 INFO Writes: cleared=188, written=188, dry_run=False
17:36:42 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_biomedical_engineering.json
17:36:42 INFO [pass 3: aggregate] complete
17:36:42 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_biomedical_engineering.json

```
| urology_mens_health | 236 | 15 | 86.4% | 3 |  |  | ok |

<!-- === urology_mens_health === rc=0 -->
```
    2.7003
urology_urothelial_cytopathology                                2.2972
urology_urinary_tract_infection_microbiome                      2.2494
urology_psma_theranostics                                       1.6699

faculty touched: 131
subtopic count: 15
total subtopic score sum (all): 148.1804

=== Top-5 faculty per subtopic ===

  urology_bladder_urothelial_cancer:
    cns9006                  4.8481
    dss2001                  2.5824
    stt2007                  2.3749
    bmf9003                  2.1334
    rkj4003                  1.7909

  urology_prostate_cancer_molecular_biology:
    ans2077                  2.4275
    brr2006                  2.3637
    mal4005                  1.9761
    ole2001                  1.9341
    frk9007                  1.6491

  urology_prostate_cancer_imaging_diagnosis:
    jch9011                  3.5130
    djm9016                  2.3189
    tim9047                  1.0366
    baa2012                  0.8817
    dag9025                  0.8590

  urology_prostate_cancer_screening_disparities:
    jch9011                  3.9757
    kek4007                  2.0802
    baa2012                  1.9892
    jim2012                  1.3245
    nap9055                  0.9194

  urology_prostate_cancer_treatment_outcomes:
    jch9011                  3.8319
    cns9006                  1.7746
    stt2007                  1.2462
    baa2012                  1.0436
    tim9047                  0.9666

  urology_male_infertility_reproductive:
    mgoldst                  2.8084
    gdpalerm                 2.4039
    zrosenw                  2.2240
    jak9111                  1.0282
    psli                     0.4610

  urology_male_contraception_sperm_biology:
    jobuck                   2.6739
    llevin                   2.6739
    ptm2001                  1.3964
    car4008                  1.3026
    icm4001                  0.4603

  urology_prostate_cancer_biomarkers_liquid_biopsy:
    mal4005                  0.7915
    jmm9018                  0.6546
    chb9074                  0.4834
    dss2001                  0.4834
    bmf9003                  0.4833

  urology_renal_cell_carcinoma:
    cns9006                  0.7251
    kab4035                  0.5499
    jch9011                  0.4488
    tim9047                  0.3523
    jmm9018                  0.3505

  urology_bph_functional_urology:
    lvr9004                  1.3553
    jch9011                  0.9584
    ril9010                  0.9584
    jim2012                  0.6612
    ars2013                  0.6612

  urology_penile_erectile_prosthetic:
    jak9111                  1.4608
    ars2013                  0.6256
    kab4035                  0.5439
    keg9034                  0.3293
    jog4018                  0.3099

  urology_kidney_stones_endourology:
    kag4024                  1.6626
    pcs9008                  0.4443
    ara4013                  0.2346
    ald2031                  0.1485
    map2008                  0.1485

  urology_urothelial_cytopathology:
    mos9084                  0.6405
    brr2006                  0.4278
    frk9007                  0.2486
    jmm9018                  0.2486
    mal4005                  0.2486

  urology_urinary_tract_infection_microbiome:
    law9067                  0.3307
    mjs9012                  0.2399
    sas9303                  0.2060
    dmd2001                  0.2053
    lim9120                  0.2053

  urology_psma_theranostics:
    jak2046                  0.5447
    stt2007                  0.4608
    jro7001                  0.3322
    dnanus                   0.1661
    amm9052                  0.1661

=== backfill_topic.py complete ===
  topic:           urology_mens_health
  activity_count:  236
  cluster_count:   15
  coverage_pct:    0.8644
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_urology_mens_health.json


-- stderr --
ned=44, unassigned=6, failed=0, elapsed=8s
17:38:07 INFO [progress] 100/236 processed: assigned=94, unassigned=6, failed=0, elapsed=14s
17:38:13 INFO [progress] 150/236 processed: assigned=142, unassigned=8, failed=0, elapsed=20s
17:38:19 INFO [progress] 200/236 processed: assigned=192, unassigned=8, failed=0, elapsed=26s
17:38:23 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:38:23 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:38:23 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:38:23 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:38:24 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:38:24 INFO [progress] 236/236 processed: assigned=228, unassigned=8, failed=0, elapsed=31s
17:38:24 INFO [pass 2: assign] complete
17:38:24 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic urology_mens_health
17:38:24 INFO Querying DynamoDB partition: TOPIC#urology_mens_health
17:38:25 INFO Fetched 574 SCORE# rows for urology_mens_health
17:38:25 INFO Aggregated: 559 rows included, 15 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
17:38:25 INFO Faculty touched: 131
17:38:52 INFO Writes: cleared=131, written=131, dry_run=False
17:38:52 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_urology_mens_health.json
17:38:52 INFO [pass 3: aggregate] complete
17:38:53 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_urology_mens_health.json

```
| substance_use_addiction_medicine | 233 | 27 | 92.7% | 3 |  |  | ok |

<!-- === substance_use_addiction_medicine === rc=0 -->
```
0.3262

  substance_covid_pandemic_impact:
    emm4010                  1.1036
    shk9078                  0.9455
    jiy4002                  0.8486
    markskr                  0.5209
    brs2006                  0.4246

  substance_cannabis_policy_use:
    emm4010                  1.8407
    anm4001                  0.7022
    tdb2002                  0.4464
    tmilner                  0.4211
    fslee                    0.4211

  substance_stigma_treatment_barriers:
    shk9078                  1.0866
    emm4010                  0.8044
    joa9070                  0.5705
    tdb2002                  0.4542
    markskr                  0.4488

  substance_alcohol_liver_disease:
    rsb2005                  1.2586
    rur9017                  0.7394
    mms9024                  0.4184
    wha4002                  0.3487
    samstei                  0.3210

  substance_opioid_neurobiology:
    tmilner                  0.9800
    krp2013                  0.4628
    fslee                    0.4628
    col2004                  0.4628
    jtl2003                  0.4628

  substance_alcohol_neurobiology:
    krp2013                  1.5535
    jag4016                  0.4620
    amk2012                  0.3597
    qiz4006                  0.2372

  substance_suicide_mental_health_comorbidity:
    smm2010                  0.7312
    joa9070                  0.7312
    yux4008                  0.4182
    yip4002                  0.3203
    kat2022                  0.2573

  substance_opioid_monitoring_behavior:
    yub2003                  0.5194
    brs2006                  0.2812
    lrw9003                  0.2812
    mcr2004                  0.2812
    say4011                  0.2611

  substance_alcohol_molecular_pathology:
    ljgudas                  0.4857
    mam2185                  0.4857
    xit2001                  0.4857
    qic2005                  0.1872
    liq9005                  0.1764

  substance_youth_substance_use:
    qiz4006                  0.6455
    tdb2002                  0.5613
    juc4013                  0.2893
    yux4008                  0.2226
    krp2013                  0.1701

  substance_opioid_racial_disparities:
    yiz2014                  0.3979
    yub2003                  0.3979
    rmt4001                  0.3979
    mcr2004                  0.3979

  substance_safer_supply_policy:
    emm4010                  1.0389

  substance_medicaid_hcv_coverage:
    yub2003                  0.2756
    shk9078                  0.2756
    cjg7003                  0.2756
    brs2006                  0.0692
    czb2002                  0.0692

  substance_opioid_cancer_pain:
    yub2003                  0.3396
    hmz7001                  0.2814
    mcr2004                  0.2492
    lrw9003                  0.0904

  substance_integrated_behavioral_health_policy:
    emm4010                  0.3923
    das2043                  0.1043
    mcr2004                  0.1043
    rdadelma                 0.1043
    sjc7004                  0.1043

  substance_multimodal_analgesia:
    haw9006                  0.2283
    mar9462                  0.1321
    rbk9001                  0.1074
    rsw9006                  0.1074
    sea2003                  0.1074

  substance_pwid_hiv_prep:
    smm2010                  0.3112
    alj4004                  0.3112

  substance_sedative_benzodiazepine_prescribing:
    haw9006                  0.1296
    mis4060                  0.0904

  substance_alcohol_fetal_comorbidity:
    oma4009                  0.1025
    rmt4001                  0.0766

  substance_neuroimaging_ml:
    qiz4006                  0.1108

=== backfill_topic.py complete ===
  topic:           substance_use_addiction_medicine
  activity_count:  233
  cluster_count:   27
  coverage_pct:    0.927
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_substance_use_addiction_medicine.json


-- stderr --
00/233 processed: assigned=84, unassigned=16, failed=0, elapsed=13s
17:40:44 INFO [progress] 150/233 processed: assigned=134, unassigned=16, failed=0, elapsed=19s
17:40:50 INFO [progress] 200/233 processed: assigned=184, unassigned=16, failed=0, elapsed=26s
17:40:54 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:40:54 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:40:54 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:40:54 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:41:05 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:41:05 INFO [progress] 233/233 processed: assigned=215, unassigned=18, failed=0, elapsed=41s
17:41:05 INFO [pass 2: assign] complete
17:41:05 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic substance_use_addiction_medicine
17:41:05 INFO Querying DynamoDB partition: TOPIC#substance_use_addiction_medicine
17:41:05 INFO Fetched 404 SCORE# rows for substance_use_addiction_medicine
17:41:05 INFO Aggregated: 367 rows included, 37 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
17:41:05 INFO Faculty touched: 107
17:41:30 INFO Writes: cleared=107, written=107, dry_run=False
17:41:30 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_substance_use_addiction_medicine.json
17:41:30 INFO [pass 3: aggregate] complete
17:41:31 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_substance_use_addiction_medicine.json

```
| musculoskeletal_orthopedic_medicine | 226 | 26 | 90.3% | 3 |  |  | ok |

<!-- === musculoskeletal_orthopedic_medicine === rc=0 -->
```
eswo                  0.8130
    oma9005                  0.5117
    yuz2002                  0.5117
    dib2020                  0.3634
    edp2014                  0.3634

  musculoskeletal_orthopedic_infection_periprosthetic:
    ams9191                  0.5392
    kdr9004                  0.3885
    del9096                  0.3477
    slp9001                  0.3477
    jas2037                  0.3081

  musculoskeletal_spinal_deformity_pediatric:
    pjp9007                  0.7372
    jpgreenf                 0.6831
    ibh9004                  0.4848
    roh9005                  0.4848
    anb2029                  0.1977

  musculoskeletal_bone_health_metabolic:
    yil9015                  0.4211
    sinhana                  0.4170
    ves4007                  0.2752
    tac2007                  0.2525
    miy9030                  0.2337

  musculoskeletal_spinal_trauma_lmic:
    roh9005                  2.2744
    kdr9004                  0.2035
    ibh9004                  0.1300

  musculoskeletal_ai_digital_health_orthopedics:
    mag3003                  0.9150
    hey9012                  0.9150
    olc9018                  0.1727
    kdr9004                  0.1596
    nem9015                  0.1321

  musculoskeletal_spine_surgery_training_technology:
    roh9005                  1.1583
    ibh9004                  0.9485
    kdr9004                  0.1442

  musculoskeletal_spinal_tumor_metastasis:
    mag3003                  0.5181
    seb4003                  0.5181
    vim2010                  0.5181
    ibh9004                  0.3050
    kdr9004                  0.2670

  musculoskeletal_ankle_foot_ligament_fracture:
    ars2013                  0.4437
    jim2012                  0.4437
    tim9047                  0.4051
    cab9005                  0.2075
    dej9009                  0.1878

  musculoskeletal_sports_medicine_upper_extremity:
    jes9343                  0.8369
    kvy9001                  0.2416
    cab9005                  0.1832
    lqg9001                  0.1617
    anf9193                  0.1187

  musculoskeletal_facial_fracture_management:
    ans9243                  0.5104
    gsr9001                  0.4635
    ask9001                  0.2803
    mgs2002                  0.2803

  musculoskeletal_atlantoaxial_upper_cervical:
    kdr9004                  0.5310
    roh9005                  0.3850
    ibh9004                  0.2360
    jpgreenf                 0.2360
    ask9001                  0.0630

  musculoskeletal_psoriatic_arthritis_inflammatory:
    mcr2004                  0.2535
    mms9024                  0.1885
    yin9003                  0.1885
    mrd2006                  0.1885
    mgd2002                  0.1701

  musculoskeletal_cartilage_tissue_engineering:
    jas2037                  0.7154
    jrs9012                  0.2494

  musculoskeletal_spine_implant_economics:
    roh9005                  0.2532
    sbk9015                  0.2532
    kdr9004                  0.2440

  musculoskeletal_osseointegration_amputation:
    dmo9004                  0.6537

  musculoskeletal_geriatric_frailty_body_composition:
    oll9021                  0.1254
    lig2002                  0.0697
    mik9096                  0.0652
    tet9025                  0.0652
    arr2014                  0.0619

  musculoskeletal_occipital_neuralgia_headache_nerve:
    lig4013                  0.2963

  musculoskeletal_rib_sternal_chest_wall_trauma:
    kok4001                  0.2260
    dal9209                  0.0675

  musculoskeletal_nipple_reconstruction_tissue_engineering:
    jas2037                  0.1512

=== backfill_topic.py complete ===
  topic:           musculoskeletal_orthopedic_medicine
  activity_count:  226
  cluster_count:   26
  coverage_pct:    0.9027
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_musculoskeletal_orthopedic_medicine.json


-- stderr --
d: assigned=88, unassigned=12, failed=0, elapsed=14s
17:43:21 INFO [progress] 150/226 processed: assigned=138, unassigned=12, failed=0, elapsed=21s
17:43:27 INFO [progress] 200/226 processed: assigned=187, unassigned=13, failed=0, elapsed=27s
17:43:30 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:43:30 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:43:30 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:43:30 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:43:31 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:43:31 INFO [progress] 226/226 processed: assigned=213, unassigned=13, failed=0, elapsed=31s
17:43:31 INFO [pass 2: assign] complete
17:43:31 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic musculoskeletal_orthopedic_medicine
17:43:31 INFO Querying DynamoDB partition: TOPIC#musculoskeletal_orthopedic_medicine
17:43:32 INFO Fetched 342 SCORE# rows for musculoskeletal_orthopedic_medicine
17:43:32 INFO Aggregated: 317 rows included, 25 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
17:43:32 INFO Faculty touched: 103
17:43:53 INFO Writes: cleared=103, written=103, dry_run=False
17:43:53 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_musculoskeletal_orthopedic_medicine.json
17:43:53 INFO [pass 3: aggregate] complete
17:43:53 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_musculoskeletal_orthopedic_medicine.json

```
| nephrology_renal_disease | 224 | 28 | 86.6% | 3 |  |  | ok |

<!-- === nephrology_renal_disease === rc=0 -->
```
001                  0.6381
    var4002                  0.6381
    rnp2002                  0.4924

  nephrology_hemodialysis_management:
    myleswo                  0.8819
    lct4001                  0.3921
    bcl2004                  0.3913
    bom2008                  0.3913
    hew3001                  0.3913

  nephrology_ckd_cardiovascular:
    myleswo                  1.6347
    lok9031                  0.5294
    mif4018                  0.4674
    lct4001                  0.3122
    map2007                  0.2291

  nephrology_enac_electrolyte_transport:
    lgpalm                   2.8894
    jms2003                  0.2957
    trk2002                  0.2812

  nephrology_hiv_renal_cardiovascular:
    rnp2002                  0.9046
    myl2003                  0.5848
    lct4001                  0.4246
    cmd9008                  0.2933
    mag2005                  0.2933

  nephrology_transplant_infectious_complications:
    dmd2001                  1.1612
    mjs9012                  0.5510
    law9067                  0.5204
    lim9120                  0.2650
    msuthan                  0.2093

  nephrology_renal_cell_carcinoma:
    dnanus                   0.5470
    ljgudas                  0.5470
    frk9007                  0.3809
    qic2005                  0.3809
    lud2005                  0.2662

  nephrology_ckd_liver_disease_interactions:
    rsb2005                  0.3806
    cvc9002                  0.3806
    rur9017                  0.3806
    dyl9002                  0.3259
    dis2012                  0.1631

  nephrology_ckd_pediatric:
    oma9005                  1.0099
    hak2012                  0.5747
    yuz2002                  0.2699

  nephrology_hypertension_management_complications:
    rsb2005                  0.3099
    jur9123                  0.2770
    map2007                  0.2402
    stp9039                  0.2402
    cha2022                  0.1617

  nephrology_diabetes_cardiovascular_outcomes:
    cha2022                  1.0144
    rbdevere                 0.3157
    zrm2001                  0.2252

  nephrology_ckd_drug_therapy:
    myleswo                  0.4750
    sts9057                  0.3570
    hyg4001                  0.2103
    nip9020                  0.1791

  nephrology_aortic_surgery_renal_outcomes:
    lngirard                 0.2551
    mfg9004                  0.2551
    mmr2011                  0.2551
    jrs9016                  0.1122
    zrm2001                  0.0835

  nephrology_diabetic_microvascular_complications:
    inp2002                  0.2792
    ram2045                  0.2792
    rnp2002                  0.1915
    amr2018                  0.0882
    cha2022                  0.0882

  nephrology_apol1_novel_kidney_biomarkers:
    myleswo                  0.3761
    mif4018                  0.2550
    amh2025                  0.1890
    kas2049                  0.1890

  nephrology_hyponatremia_electrolyte_disorders:
    lct4001                  0.2974
    dil9070                  0.2437
    jlaurenc                 0.1666
    myleswo                  0.1371

  nephrology_kidney_stone_hyperparathyroid:
    jlaurenc                 0.6852
    pcs9008                  0.1561

  nephrology_kidney_quality_payment:
    lct4001                  0.6911
    myleswo                  0.0812

  nephrology_peritoneal_dialysis_management:
    dmd2001                  0.2536
    law9067                  0.2536
    ili2001                  0.2536

  nephrology_renal_pathology_imaging_ai:
    rud4004                  0.5465

  nephrology_transplant_pregnancy_outcomes:
    dls7001                  0.3869

=== backfill_topic.py complete ===
  topic:           nephrology_renal_disease
  activity_count:  224
  cluster_count:   28
  coverage_pct:    0.8661
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_nephrology_renal_disease.json


-- stderr --
0, elapsed=7s
17:45:44 INFO [progress] 100/224 processed: assigned=79, unassigned=21, failed=0, elapsed=12s
17:45:51 INFO [progress] 150/224 processed: assigned=129, unassigned=21, failed=0, elapsed=19s
17:45:57 INFO [progress] 200/224 processed: assigned=178, unassigned=22, failed=0, elapsed=25s
17:45:59 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:45:59 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:45:59 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:45:59 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:46:01 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:46:01 INFO [progress] 224/224 processed: assigned=202, unassigned=22, failed=0, elapsed=30s
17:46:01 INFO [pass 2: assign] complete
17:46:01 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic nephrology_renal_disease
17:46:02 INFO Querying DynamoDB partition: TOPIC#nephrology_renal_disease
17:46:02 INFO Fetched 518 SCORE# rows for nephrology_renal_disease
17:46:02 INFO Aggregated: 467 rows included, 51 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
17:46:02 INFO Faculty touched: 170
17:46:39 INFO Writes: cleared=170, written=170, dry_run=False
17:46:39 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_nephrology_renal_disease.json
17:46:39 INFO [pass 3: aggregate] complete
17:46:39 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_nephrology_renal_disease.json

```
| developmental_biology | 199 | 26 | 86.4% | 3 |  |  | ok |

<!-- === developmental_biology === rc=0 -->
```
                 0.8066
    lud2005                  0.6449
    ole2001                  0.5694
    varmus                   0.5133
    nog4004                  0.3874

  developmental_organoid_disease_modeling:
    dic2009                  1.4284
    hut2006                  0.4750
    shc2034                  0.4443
    tre2003                  0.4443
    jzx2002                  0.4443

  developmental_hematopoiesis_stem_cell_fate:
    dal3005                  0.9065
    shn9035                  0.5447
    srafii                   0.4926
    jzx2002                  0.3672
    szj2001                  0.3672

  developmental_neural_tube_spina_bifida:
    mer2005                  0.7248
    ole2001                  0.7248
    kas2049                  0.7248
    jhzippin                 0.3222
    mfm2003                  0.3185

  developmental_pancreatic_beta_cell_differentiation:
    shc2034                  0.4385
    jis7016                  0.4187
    res2025                  0.2822
    lal2018                  0.2822
    tre2003                  0.2822

  developmental_ivf_embryo_assessment:
    zrosenw                  0.3853
    ole2001                  0.3199
    imh2003                  0.3199
    jom2032                  0.3199
    nizanin                  0.3199

  developmental_endoderm_organogenesis_patterning:
    tre2003                  0.4741
    app2006                  0.4741
    efa2001                  0.4741
    mas4011                  0.4741
    mif4018                  0.3310

  developmental_glioma_stem_cell_microenvironment:
    haf9016                  0.6439
    ris2020                  0.5579
    smc2011                  0.5579
    bel9057                  0.1666
    djp2002                  0.1422

  developmental_maternal_offspring_programming:
    mtoth                    0.6174
    hes2011                  0.4181
    chg4001                  0.1208
    jak2043                  0.1208
    myz4001                  0.1208

  developmental_adipogenesis_differentiation:
    mnt4002                  0.8201
    frs4001                  0.1998
    smr4005                  0.1229
    jom2042                  0.1207
    kas2049                  0.1207

  developmental_b_cell_lymphoma_differentiation:
    chm2042                  0.3171
    achadbur                 0.2022
    cem2009                  0.2022
    dab2078                  0.2022
    mix2003                  0.2022

  developmental_airway_epithelial_differentiation:
    rgcryst                  0.3367
    rkaner                   0.3367
    mrr2006                  0.2113
    pleopold                 0.2113
    ole2001                  0.1950

  developmental_intestinal_epithelial_lineage:
    nog4004                  0.1950
    jom4010                  0.1002
    mdu4003                  0.1002
    mtd4001                  0.1002
    btm9003                  0.0896

  developmental_ipsc_genome_editing_tools:
    dar2042                  0.2021
    srafii                   0.2021
    ral2020                  0.2021
    yil4011                  0.1024
    shg3006                  0.0437

  developmental_microglia_brain_immunity:
    lif4001                  0.3420
    lig2033                  0.3420

  developmental_wnt_hedgehog_signaling:
    rin7007                  0.5078
    res2025                  0.0944

  developmental_cell_cycle_quiescence:
    mnt4002                  0.3262
    tom4003                  0.0924

  developmental_m6a_rna_modification:
    srj2003                  0.4072

  developmental_tissue_regeneration_sirtuin:
    roh9005                  0.1771
    tre2003                  0.1453

=== backfill_topic.py complete ===
  topic:           developmental_biology
  activity_count:  199
  cluster_count:   26
  coverage_pct:    0.8643
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_developmental_biology.json


-- stderr --
confidence_floor=0.3, dry_run=False
17:48:08 INFO [progress] 50/199 processed: assigned=42, unassigned=8, failed=0, elapsed=8s
17:48:16 INFO [progress] 100/199 processed: assigned=92, unassigned=8, failed=0, elapsed=15s
17:48:22 INFO [progress] 150/199 processed: assigned=142, unassigned=8, failed=0, elapsed=22s
17:48:28 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:48:29 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:48:29 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:48:30 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:48:44 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:48:44 INFO [progress] 199/199 processed: assigned=190, unassigned=9, failed=0, elapsed=43s
17:48:44 INFO [pass 2: assign] complete
17:48:44 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic developmental_biology
17:48:44 INFO Querying DynamoDB partition: TOPIC#developmental_biology
17:48:44 INFO Fetched 505 SCORE# rows for developmental_biology
17:48:44 INFO Aggregated: 488 rows included, 17 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
17:48:44 INFO Faculty touched: 175
17:49:23 INFO Writes: cleared=175, written=175, dry_run=False
17:49:23 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_developmental_biology.json
17:49:23 INFO [pass 3: aggregate] complete
17:49:24 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_developmental_biology.json

```
| palliative_end_of_life_care | 195 | 25 | 94.4% | 3 |  |  | ok |

<!-- === palliative_end_of_life_care === rc=0 -->
```
elivery:
    mis9202                  0.2874
    rkaner                   0.2617
    pag9051                  0.2617
    vep9012                  0.2617
    lma9012                  0.2020

  palliative_prolonged_grief_disorder:
    hgp2001                  2.4834
    pam2056                  0.7627
    joa9070                  0.2328

  palliative_icu_critical_care_eol:
    haw9006                  0.6604
    hgp2001                  0.3815
    pam2056                  0.3815
    berlind                  0.3815
    chr9008                  0.3815

  palliative_psychiatric_comorbidities_serious_illness:
    mcr2004                  0.7864
    das2043                  0.7864
    mrd2006                  0.4542
    mis9202                  0.3322
    chr9008                  0.1412

  palliative_pain_symptom_management:
    mcr2004                  0.6802
    hmz7001                  0.5792
    yub2003                  0.4280
    nem9015                  0.2297
    cob4016                  0.1399

  palliative_ethics_consultation_clinical:
    jjfins                   0.6451
    ezg9002                  0.2720
    imd2001                  0.2262
    bjh4001                  0.1499
    lig2002                  0.1499

  palliative_geriatric_serious_illness:
    mcr2004                  0.3091
    mrs9012                  0.1777
    arj2005                  0.1732
    mau2006                  0.1732
    pag9051                  0.1666

  palliative_interstitial_lung_disease_qol:
    kia9010                  0.5008
    mms9024                  0.5008
    ajp9012                  0.4525
    rkaner                   0.2426
    lcp2003                  0.1900

  palliative_spiritual_integrative_care:
    mcr2004                  0.6511
    ibk9003                  0.6171
    hgp2001                  0.4676
    ala9124                  0.1853
    haa2019                  0.0924

  palliative_caregiver_health_self_care:
    mcr2004                  0.3459
    acr2213                  0.2822
    rdadelma                 0.2241
    mms9024                  0.2241
    ank9177                  0.2241

  palliative_disparities_diverse_populations:
    lcp2003                  0.3832
    hgp2001                  0.3193
    pam2056                  0.3193
    shr4009                  0.2947
    erp2001                  0.0885

  palliative_disorders_of_consciousness_ethics:
    jjfins                   0.6864
    nds2001                  0.2420
    jai9018                  0.1074
    sut2006                  0.1074
    dem9199                  0.0766

  palliative_high_cost_medicare_spending:
    yoz2009                  0.6968
    khd9010                  0.1671
    rak2007                  0.1671
    zag9005                  0.0859
    mau2006                  0.0859

  palliative_cancer_survivorship_qol:
    ves4007                  0.2093
    est2003                  0.1974
    ritchie                  0.1807
    shr4009                  0.1089
    jdw2002                  0.0526

  palliative_eol_aggressiveness_social_support:
    hgp2001                  0.2823
    pam2056                  0.2823

  palliative_maid_deprescribing_eol_decisions:
    vuk9003                  0.2093
    mslachs                  0.1122
    pag9051                  0.1122

  palliative_crisis_standards_resource_allocation:
    jjfins                   0.1471
    ror9068                  0.1044
    haw9006                  0.0984
    roj9068                  0.0368
    rsw9006                  0.0368

  palliative_digital_health_symptom_monitoring:
    ves4007                  0.0692
    shr4009                  0.0617

=== backfill_topic.py complete ===
  topic:           palliative_end_of_life_care
  activity_count:  195
  cluster_count:   25
  coverage_pct:    0.9436
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_palliative_end_of_life_care.json


-- stderr --
False
17:50:48 INFO [progress] 50/195 processed: assigned=47, unassigned=3, failed=0, elapsed=7s
17:50:55 INFO [progress] 100/195 processed: assigned=96, unassigned=4, failed=0, elapsed=14s
17:51:01 INFO [progress] 150/195 processed: assigned=145, unassigned=5, failed=0, elapsed=20s
17:51:07 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:51:07 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:51:07 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:51:08 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:51:08 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:51:09 INFO [progress] 195/195 processed: assigned=190, unassigned=5, failed=0, elapsed=28s
17:51:09 INFO [pass 2: assign] complete
17:51:09 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic palliative_end_of_life_care
17:51:09 INFO Querying DynamoDB partition: TOPIC#palliative_end_of_life_care
17:51:09 INFO Fetched 413 SCORE# rows for palliative_end_of_life_care
17:51:09 INFO Aggregated: 403 rows included, 10 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
17:51:09 INFO Faculty touched: 124
17:51:36 INFO Writes: cleared=124, written=124, dry_run=False
17:51:36 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_palliative_end_of_life_care.json
17:51:36 INFO [pass 3: aggregate] complete
17:51:36 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_palliative_end_of_life_care.json

```
| otolaryngology_head_neck | 185 | 25 | 95.1% | 3 |  |  | ok |

<!-- === otolaryngology_head_neck === rc=0 -->
```
     0.2771
    sbs9011                  0.2771

  otolaryngology_thyroid_parathyroid_surgery:
    bmf9002                  1.3873
    raz2002                  1.3873
    tjfahey                  1.3873
    bpe9002                  0.4930
    pac2001                  0.4057

  otolaryngology_healthcare_disparities_access:
    anr2783                  1.9912
    mrd2006                  0.4211
    mgs2002                  0.4119
    aer2006                  0.4119
    mslachs                  0.4119

  otolaryngology_cough_airway_assessment:
    yrj9003                  1.9330
    anr2783                  1.7250
    aer2006                  0.5595
    mslachs                  0.5595
    few2001                  0.1349

  otolaryngology_pediatric_airway_aerodigestive:
    aam9008                  1.1427
    kag9148                  0.4182
    thc9032                  0.4182
    sdr9007                  0.2731
    vkm2001                  0.2731

  otolaryngology_covid_pediatric_otolaryngology:
    ant9025                  0.8878
    dik2002                  0.3099
    wkuhel                   0.3099
    ejs9005                  0.3047
    kar9043                  0.3047

  otolaryngology_facial_fractures_trauma:
    ans9243                  1.3613
    gsr9001                  0.8004
    ask9001                  0.6965
    mgs2002                  0.6965
    keh4012                  0.0675

  otolaryngology_salivary_gland_parotid:
    dik2002                  0.5912
    jov9069                  0.3887
    mmr2011                  0.3887
    osc4001                  0.3887
    bpe9002                  0.3596

  otolaryngology_skull_base_endoscopic_surgery:
    ale2009                  0.3331
    anb2029                  0.3331
    pes2008                  0.3331
    ask9001                  0.2995
    bpe9002                  0.2875

  otolaryngology_rhinology_sinusitis:
    ask9001                  0.5518
    atabaee                  0.5153
    anp2022                  0.3933
    ans9243                  0.3467
    aam9008                  0.2337

  otolaryngology_laryngoscopy_simulation_training:
    anr2783                  1.4655
    odk9003                  0.3903

  otolaryngology_anti_reflux_surgery_dysphagia:
    bmf9002                  0.3368
    raz2002                  0.3368
    tjfahey                  0.3368
    anr2783                  0.1509
    lus2005                  0.1509

  otolaryngology_head_neck_reconstruction_microsurgery:
    bpe9002                  0.2494
    jas2037                  0.2475
    dik2002                  0.2475
    gsr9001                  0.1582
    ceh2003                  0.1288

  otolaryngology_rhinoplasty_nasal_reconstruction:
    jas2037                  0.3404
    odk9003                  0.3099
    lig4013                  0.1737
    dik2002                  0.1582
    gsr9001                  0.1582

  otolaryngology_cartilage_tissue_engineering:
    jas2037                  0.7772

  otolaryngology_nerve_decompression_headache:
    lig4013                  0.7428

  otolaryngology_head_neck_nerve_imaging:
    bas9049                  0.3380
    lig4013                  0.2051
    frs4001                  0.1897

  otolaryngology_surgical_ergonomics_workforce:
    anr2783                  0.6540
    ans9243                  0.0531

  otolaryngology_chiari_craniocervical:
    jpgreenf                 0.1429
    ibh9004                  0.0812
    roh9005                  0.0812

  otolaryngology_sinonasal_papilloma_pathology:
    ask9001                  0.2731

  otolaryngology_sleep_apnea_oral_appliance:
    jrs9016                  0.0743

=== backfill_topic.py complete ===
  topic:           otolaryngology_head_neck
  activity_count:  185
  cluster_count:   25
  coverage_pct:    0.9514
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_otolaryngology_head_neck.json


-- stderr --
loor=0.3, dry_run=False
17:53:01 INFO [progress] 50/185 processed: assigned=45, unassigned=5, failed=0, elapsed=7s
17:53:08 INFO [progress] 100/185 processed: assigned=95, unassigned=5, failed=0, elapsed=13s
17:53:14 INFO [progress] 150/185 processed: assigned=142, unassigned=8, failed=0, elapsed=19s
17:53:17 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:53:17 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:53:17 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:53:18 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:53:23 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:53:23 INFO [progress] 185/185 processed: assigned=177, unassigned=8, failed=0, elapsed=29s
17:53:23 INFO [pass 2: assign] complete
17:53:23 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic otolaryngology_head_neck
17:53:24 INFO Querying DynamoDB partition: TOPIC#otolaryngology_head_neck
17:53:24 INFO Fetched 367 SCORE# rows for otolaryngology_head_neck
17:53:24 INFO Aggregated: 351 rows included, 16 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
17:53:24 INFO Faculty touched: 98
17:53:44 INFO Writes: cleared=98, written=98, dry_run=False
17:53:44 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_otolaryngology_head_neck.json
17:53:45 INFO [pass 3: aggregate] complete
17:53:45 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_otolaryngology_head_neck.json

```
| research_infrastructure_workforce | 180 | 25 | 93.3% | 3 |  |  | ok |

<!-- === research_infrastructure_workforce === rc=0 -->
```
     0.5839
    jch9011                  0.3964
    formenti                 0.1822
    varmus                   0.1659

  research_nih_funding_workforce_development:
    keg2002                  0.6286
    est2003                  0.5156
    sap4017                  0.3081
    kyr9001                  0.2913
    jsirey                   0.2874

  research_ai_nlp_biomedical_data:
    thc2015                  0.5822
    yip4002                  0.5693
    yiz2014                  0.2974
    aaa4027                  0.1876
    ara4013                  0.1876

  research_clinical_trial_design_conduct:
    mfg9004                  1.0349
    mmr2011                  1.0349
    lngirard                 0.2832
    mrd2006                  0.2447
    rbdevere                 0.1832

  research_institutional_repositories_knowledge_management:
    did2005                  0.5171
    drw2004                  0.5015
    tew2004                  0.4860
    paa2013                  0.3902
    mrd2006                  0.3142

  research_race_ethnicity_data_representation:
    syc2005                  0.3706
    hey9002                  0.3203
    alh4014                  0.3203
    thc2015                  0.2492
    ccole                    0.2492

  research_global_health_capacity_building:
    ama2006                  0.3802
    kac2047                  0.3802
    soc2005                  0.3802
    ras9199                  0.2799
    kaz4001                  0.1713

  research_laboratory_methods_biospecimen_prep:
    shg3006                  0.2474
    dcl2001                  0.2067
    haz2005                  0.2067
    irm2224                  0.2067
    sel4002                  0.2067

  research_clinical_trial_recruitment_participation:
    emm4010                  0.4077
    wab4001                  0.2428
    acr2213                  0.2387
    mcr2004                  0.2387
    mms9024                  0.2381

  research_medical_imaging_data_standards:
    yip4002                  0.4092
    mam4041                  0.3887
    few2001                  0.1442
    ges9006                  0.1442
    amk2012                  0.1442

  research_scholarly_training_residency_fellowship:
    err9009                  0.4038
    lig2002                  0.4038
    jil4035                  0.1617
    sol9005                  0.1024
    kim9036                  0.0720

  research_genomic_databases_bioinformatics_resources:
    dis2003                  0.1971
    kyr9001                  0.1971
    sae2004                  0.1971
    alm2069                  0.1727
    ekk2003                  0.1727

  research_academic_medicine_compensation_workforce:
    mfg9004                  0.2554
    gjl9003                  0.1799
    ask9001                  0.0560
    kad9090                  0.0544
    krc9028                  0.0544

  research_fellowship_program_director_demographics:
    haa4020                  0.1546
    nil9053                  0.1056
    emh2002                  0.0812
    rjm2002                  0.0812

  research_covid19_clinical_policy_response:
    rgulick                  0.1842
    lum4003                  0.1016
    cdp2001                  0.0654

  research_preclinical_tumor_models_drug_discovery:
    ggi9001                  0.1229
    col2004                  0.0766

  research_clinical_outcomes_nlp_datasets:
    yip4002                  0.1526

  research_genetic_variant_classification_counseling:
    yip4002                  0.0923
    mip9116                  0.0526

  research_specialty_society_awards_recognition:
    frk9007                  0.1155

=== backfill_topic.py complete ===
  topic:           research_infrastructure_workforce
  activity_count:  180
  cluster_count:   25
  coverage_pct:    0.9333
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_research_infrastructure_workforce.json


-- stderr --
] 50/180 processed: assigned=49, unassigned=1, failed=0, elapsed=8s
17:55:11 INFO [progress] 100/180 processed: assigned=97, unassigned=3, failed=0, elapsed=14s
17:55:18 INFO [progress] 150/180 processed: assigned=147, unassigned=3, failed=0, elapsed=20s
17:55:21 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:55:21 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:55:21 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:55:22 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:55:22 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:55:22 INFO [progress] 180/180 processed: assigned=177, unassigned=3, failed=0, elapsed=24s
17:55:22 INFO [pass 2: assign] complete
17:55:22 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic research_infrastructure_workforce
17:55:23 INFO Querying DynamoDB partition: TOPIC#research_infrastructure_workforce
17:55:23 INFO Fetched 342 SCORE# rows for research_infrastructure_workforce
17:55:23 INFO Aggregated: 335 rows included, 7 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
17:55:23 INFO Faculty touched: 173
17:56:00 INFO Writes: cleared=173, written=173, dry_run=False
17:56:00 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_research_infrastructure_workforce.json
17:56:00 INFO [pass 3: aggregate] complete
17:56:00 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_research_infrastructure_workforce.json

```
| prostate_urologic_cancer | 175 | 22 | 95.4% | 3 |  |  | ok |

<!-- === prostate_urologic_cancer === rc=0 -->
```
etection:
    jch9011                  3.3949
    djm9016                  1.9045
    tim9047                  1.1527
    baa2012                  0.9067
    dag9025                  0.7639

  prostate_pca_biomarkers_genomic_risk:
    mal4005                  1.9438
    jch9011                  1.2481
    kek4007                  1.1723
    chb9074                  0.9479
    brr2006                  0.9438

  prostate_active_surveillance_focal_therapy:
    jch9011                  3.5928
    baa2012                  1.4436
    nap9055                  1.2396
    tim9047                  1.0755
    djm9016                  0.7413

  prostate_psma_imaging_theranostics:
    jro7001                  1.8186
    stt2007                  1.2246
    shc4008                  1.1566
    jak2046                  0.8984
    djm9016                  0.7078

  prostate_advanced_pca_systemic_therapy:
    cns9006                  1.8854
    stt2007                  1.3173
    pvn4001                  0.6199
    jmm9018                  0.4303
    brr2006                  0.4303

  prostate_bladder_cancer_urothelial_systemic:
    cns9006                  4.1400
    rkj4003                  2.1826
    stt2007                  1.5361
    pvn4001                  0.2960
    ajo9001                  0.1950

  prostate_bladder_cancer_surgery_pathology:
    dss2001                  1.1510
    baa2012                  0.8287
    mos9084                  0.5748
    nap9055                  0.5327
    brr2006                  0.4778

  prostate_racial_disparities_screening:
    kek4007                  1.5144
    baa2012                  1.0528
    rmt4001                  0.6532
    jch9011                  0.6209
    dss2001                  0.5304

  prostate_upper_tract_urothelial_carcinoma:
    dss2001                  1.3755
    bmf9003                  0.8240
    baa2012                  0.6354
    jmm9018                  0.3802
    ans2077                  0.3802

  prostate_patient_experience_qol_survivorship:
    baa2012                  0.3966
    dnanus                   0.3688
    chb9074                  0.3688
    cns9006                  0.3688
    jch9011                  0.3688

  prostate_surgical_management_outcomes:
    jch9011                  1.7543
    baa2012                  0.5653
    frk9007                  0.3122
    brr2006                  0.3099
    dss2001                  0.3099

  prostate_lipid_metabolism_preclinical:
    mal4005                  1.2107
    hup4002                  0.8694
    jak2046                  0.1038

  prostate_racial_disparities_surgical_access:
    jim2012                  1.1032
    jch9011                  0.8749

  prostate_ai_precision_oncology_tools:
    ans2077                  0.3300
    cns9006                  0.3300
    stt2007                  0.2372
    lum4003                  0.1822

  prostate_patient_decision_support_literacy:
    lcp2003                  0.2593
    erp2001                  0.2593
    baa2012                  0.1849
    dnanus                   0.0526
    rmt4001                  0.0526

  prostate_cancer_comorbidity_survivorship:
    lcp2003                  0.2873
    lmk2003                  0.2061
    mms9024                  0.2061
    pag9051                  0.0812

  prostate_lncrna_epigenomics_regulation:
    yuz2002                  0.3222
    chb9074                  0.1838
    jmm9018                  0.1838

  prostate_immunotherapy_toxicity_biomarkers:
    cns9006                  0.4170
    pvn4001                  0.2510

  prostate_bladder_cancer_preclinical_biology:
    xyhuang                  0.3310

=== backfill_topic.py complete ===
  topic:           prostate_urologic_cancer
  activity_count:  175
  cluster_count:   22
  coverage_pct:    0.9543
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_prostate_urologic_cancer.json


-- stderr --
or=0.3, dry_run=False
17:57:17 INFO [progress] 50/175 processed: assigned=47, unassigned=3, failed=0, elapsed=8s
17:57:25 INFO [progress] 100/175 processed: assigned=97, unassigned=3, failed=0, elapsed=15s
17:57:31 INFO [progress] 150/175 processed: assigned=147, unassigned=3, failed=0, elapsed=22s
17:57:34 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:57:34 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:57:34 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:57:34 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:57:35 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:57:35 INFO [progress] 175/175 processed: assigned=172, unassigned=3, failed=0, elapsed=26s
17:57:35 INFO [pass 2: assign] complete
17:57:35 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic prostate_urologic_cancer
17:57:36 INFO Querying DynamoDB partition: TOPIC#prostate_urologic_cancer
17:57:36 INFO Fetched 463 SCORE# rows for prostate_urologic_cancer
17:57:36 INFO Aggregated: 456 rows included, 7 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
17:57:36 INFO Faculty touched: 100
17:57:57 INFO Writes: cleared=100, written=100, dry_run=False
17:57:57 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_prostate_urologic_cancer.json
17:57:58 INFO [pass 3: aggregate] complete
17:57:58 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_prostate_urologic_cancer.json

```
| gi_cancer | 159 | 22 | 97.5% | 3 |  |  | ok |

<!-- === gi_cancer === rc=0 -->
```
t_immunotherapy:
    jom4010                  0.7123
    jul4008                  0.7123
    mdu4003                  0.7123
    mtd4001                  0.7123
    ili2001                  0.5363

  gi_rectal_cancer_treatment_outcomes:
    syc2005                  0.6893
    hey9002                  0.5886
    cmf2004                  0.3371
    nim9131                  0.3142
    rum9028                  0.3099

  gi_esophageal_cancer_surgery_outcomes:
    nkaltork                 1.0327
    bel9026                  1.0327
    jlp2002                  1.0327
    swh9002                  1.0327
    mmr2011                  0.3061

  gi_gastric_colorectal_neuroendocrine_tumors:
    ans2077                  0.4462
    jmm9018                  0.4462
    ole2001                  0.4462
    joj9034                  0.4462
    bmf9002                  0.4028

  gi_cancer_preclinical_models_epigenetics:
    jom4010                  0.7689
    mdu4003                  0.7689
    mtd4001                  0.7689
    lud2005                  0.3188
    ydj2001                  0.2291

  gi_radioimmunotherapy_targeted_delivery:
    smc4002                  0.9424
    ekf4001                  0.7895
    csc9012                  0.3219
    msb2006                  0.2882
    icm4001                  0.2882

  gi_colorectal_cancer_screening_disparities:
    mvr2002                  0.3858
    jeb4033                  0.3197
    ras9030                  0.2880
    mkf2002                  0.2640
    evg9007                  0.2093

  gi_liquid_biopsy_molecular_diagnostics:
    nkaltork                 0.3681
    barany                   0.3015
    mdb2005                  0.3015
    dal3005                  0.2822
    jdw2002                  0.2822

  gi_pancreatic_cystic_biliary_pathology:
    mfw4002                  0.4303
    pfi9001                  0.4303
    abg9017                  0.4061
    nip9020                  0.2360
    cyt4001                  0.2143

  gi_colorectal_gastric_systemic_therapy_resistance:
    mas9313                  1.3427
    dob2014                  0.4342
    pag2015                  0.4342
    any4004                  0.2143

  gi_colorectal_endoscopic_surgical_management:
    ars2013                  0.4246
    srm9005                  0.2964
    nip9020                  0.2781
    hey9002                  0.2229
    sat9211                  0.2211

  gi_cancer_disparities_access_outcomes:
    abj9004                  0.3727
    ccl9009                  0.3727
    rur9017                  0.3727
    baa2012                  0.1747
    jim2012                  0.1526

  gi_cancer_treatment_delay_regionalization:
    pac2001                  0.3047
    bmf9002                  0.3047
    raz2002                  0.3047
    tjfahey                  0.3047
    formenti                 0.1649

  gi_cancer_genomics_structural_variation:
    gar2001                  0.2787
    pid9006                  0.2787
    ans2077                  0.1963
    jmm9018                  0.1963
    ole2001                  0.1963

  gi_metabolic_immune_reprogramming_cancer:
    jdw2002                  0.1448
    sab4028                  0.1448
    tam2037                  0.1448
    iss4007                  0.1448
    iss4009                  0.1448

  gi_comorbidity_management_cancer_survivors:
    lcp2003                  0.2715
    lmk2003                  0.2061
    mms9024                  0.2061
    pag9051                  0.0654

  gi_liver_transplant_viral_hepatitis:
    rsb2005                  0.0768
    hsc2001                  0.0692
    lja2002                  0.0692
    tak4011                  0.0673

=== backfill_topic.py complete ===
  topic:           gi_cancer
  activity_count:  159
  cluster_count:   22
  coverage_pct:    0.9748
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_gi_cancer.json


-- stderr --
ing
17:59:06 INFO Classifying 159 PMIDs with concurrency=15, confidence_floor=0.3, dry_run=False
17:59:13 INFO [progress] 50/159 processed: assigned=49, unassigned=1, failed=0, elapsed=7s
17:59:20 INFO [progress] 100/159 processed: assigned=99, unassigned=1, failed=0, elapsed=15s
17:59:26 INFO [progress] 150/159 processed: assigned=149, unassigned=1, failed=0, elapsed=21s
17:59:27 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:59:27 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:59:27 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:59:27 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:59:27 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
17:59:27 INFO [progress] 159/159 processed: assigned=158, unassigned=1, failed=0, elapsed=22s
17:59:28 INFO [pass 2: assign] complete
17:59:28 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic gi_cancer
17:59:28 INFO Querying DynamoDB partition: TOPIC#gi_cancer
17:59:28 INFO Fetched 432 SCORE# rows for gi_cancer
17:59:28 INFO Aggregated: 430 rows included, 2 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
17:59:28 INFO Faculty touched: 189
18:00:07 INFO Writes: cleared=189, written=189, dry_run=False
18:00:07 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_gi_cancer.json
18:00:07 INFO [pass 3: aggregate] complete
18:00:07 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_gi_cancer.json

```
| ophthalmology_vision_science | 144 | 19 | 95.1% | 3 |  |  | ok |

<!-- === ophthalmology_vision_science === rc=0 -->
```
ll): 72.7926

=== Top-5 faculty per subtopic ===

  ophthalmology_ccm_neurological_systemic_disease:
    ram2045                  4.8609
    inp2002                  3.5068
    zrm2001                  1.5072

  ophthalmology_deep_learning_retinal_imaging:
    yip4002                  3.0080
    few2001                  2.4366
    sjh2006                  1.3233
    kyk9011                  0.7428
    grs2003                  0.4641

  ophthalmology_retinal_imaging_diagnostics:
    kyk9011                  1.8832
    szk7001                  1.6637
    djd2003                  1.6637
    mhm9004                  0.7834
    mjk9017                  0.5042

  ophthalmology_retinal_degeneration_genetics:
    szk7001                  1.1882
    djd2003                  1.1882
    rgcryst                  0.7241
    kyk9011                  0.7241
    dos2011                  0.7241

  ophthalmology_corneal_confocal_microscopy_neuropathy:
    ram2045                  3.5906
    inp2002                  2.7982
    zrm2001                  0.3953
    cha2022                  0.1187
    amr2018                  0.1187

  ophthalmology_amd_treatment_outcomes:
    szk7001                  1.9008
    kyk9011                  0.6152
    djd2003                  0.6152
    chsung                   0.5504
    mnociari                 0.5244

  ophthalmology_ocular_gene_therapy:
    szk7001                  2.1377
    boy2004                  0.7659
    shn2010                  0.7659
    kyk9011                  0.4271

  ophthalmology_healthcare_delivery_equity:
    gjl9003                  0.7359
    grs2003                  0.4242
    rtb2003                  0.3486
    sjh2006                  0.3193
    asm2008                  0.3193

  ophthalmology_glaucoma_diagnosis_management:
    sjh2006                  0.9862
    lys2005                  0.4676
    few2001                  0.4051
    yip4002                  0.4051
    kyk9011                  0.3286

  ophthalmology_cataract_vitreoretinal_surgery:
    sjh2006                  0.9861
    kyk9011                  0.6386
    kcs2002                  0.3570
    szk7001                  0.3286
    djd2003                  0.3286

  ophthalmology_myopia_scleral_biomechanics:
    cah4016                  1.1809
    jom4032                  1.1809

  ophthalmology_diabetic_retinopathy_microvascular:
    rnp2002                  0.6422
    grs2003                  0.5430
    inp2002                  0.4111
    ram2045                  0.4111

  ophthalmology_oculoplastics_adnexal:
    kyg9004                  0.5017
    keh4012                  0.3964
    haa4020                  0.3134
    kyk9011                  0.2522
    gjl9003                  0.1659

  ophthalmology_intracranial_hypertension_neuroophthalmology:
    mjd2004                  0.6655
    ram2045                  0.3050
    cro9004                  0.0992
    jpgreenf                 0.0992
    jts2004                  0.0992

  ophthalmology_ccm_pediatric_metabolic_conditions:
    inp2002                  0.7671
    ram2045                  0.7671

  ophthalmology_diabetic_neuropathy_treatment_glycemic:
    inp2002                  0.4980
    ram2045                  0.4980
    zrm2001                  0.3957

  ophthalmology_vitamin_d_diabetes_complications:
    cha2022                  0.1974
    amr2018                  0.1254
    rgcryst                  0.1254

  ophthalmology_ccm_neurodegeneration_rare_disease:
    ram2045                  0.1999
    inp2002                  0.1103

  ophthalmology_uveal_melanoma_ocular_oncology:
    asl4003                  0.2964

=== backfill_topic.py complete ===
  topic:           ophthalmology_vision_science
  activity_count:  144
  cluster_count:   19
  coverage_pct:    0.9514
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_ophthalmology_vision_science.json


-- stderr --
ng
18:01:07 INFO Classifying 144 PMIDs with concurrency=15, confidence_floor=0.3, dry_run=False
18:01:15 INFO [progress] 50/144 processed: assigned=42, unassigned=8, failed=0, elapsed=8s
18:01:20 INFO [progress] 100/144 processed: assigned=90, unassigned=10, failed=0, elapsed=13s
18:01:26 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:01:26 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:01:26 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:01:26 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:01:31 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:01:31 INFO [progress] 144/144 processed: assigned=133, unassigned=11, failed=0, elapsed=24s
18:01:31 INFO [pass 2: assign] complete
18:01:31 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic ophthalmology_vision_science
18:01:32 INFO Querying DynamoDB partition: TOPIC#ophthalmology_vision_science
18:01:32 INFO Fetched 289 SCORE# rows for ophthalmology_vision_science
18:01:32 INFO Aggregated: 269 rows included, 20 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
18:01:32 INFO Faculty touched: 58
18:01:44 INFO Writes: cleared=58, written=58, dry_run=False
18:01:44 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_ophthalmology_vision_science.json
18:01:44 INFO [pass 3: aggregate] complete
18:01:44 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_ophthalmology_vision_science.json

```
| transplantation_medicine | 143 | 22 | 95.8% | 3 |  |  | ok |

<!-- === transplantation_medicine === rc=0 -->
```
    sak2009                  0.8744

  transplantation_infectious_complications:
    mjs9012                  0.7832
    dmd2001                  0.7694
    law9067                  0.7403
    tbs2001                  0.7403
    rsoave                   0.7403

  transplantation_heart_outcomes_complications:
    lok9031                  1.7950
    dis2012                  0.3446
    dyl9002                  0.3446
    jac9029                  0.3446
    luk9003                  0.3446

  transplantation_hct_donor_matching_access:
    jub2029                  1.4510
    eku9001                  0.7618
    alg9117                  0.5071
    gar2001                  0.3099
    tbs2001                  0.3099

  transplantation_kidney_donor_recipient:
    dmd2001                  0.7356
    dls7001                  0.6620
    sak2009                  0.6617
    rec9091                  0.5429
    koa4002                  0.3536

  transplantation_hct_gvhd_complications:
    jub2029                  1.8561
    sca2002                  0.7728
    eku9001                  0.3570
    bmgreen                  0.2952
    chr9008                  0.2952

  transplantation_liver_transplant_equity:
    rur9017                  1.3294
    rsb2005                  1.0103
    samstei                  1.0103
    abj9004                  0.4894
    ccl9009                  0.4894

  transplantation_islet_beta_cell_transplant:
    chc2062                  0.5052
    shc2034                  0.5052
    jzx2002                  0.3553
    lal2018                  0.3553
    tre2003                  0.3553

  transplantation_gut_microbiota:
    dmd2001                  0.9711
    law9067                  0.8781
    mjs9012                  0.8781
    lim9120                  0.3621
    msuthan                  0.2372

  transplantation_alcohol_liver_disease_cirrhosis:
    rsb2005                  1.0417
    rur9017                  0.5712
    abj9004                  0.3001
    ccl9009                  0.3001
    mms9024                  0.2594

  transplantation_lung_perioperative:
    juf4007                  2.4409
    hho2001                  0.3413

  transplantation_post_transplant_malignancy:
    bpe9002                  0.4090
    lum4003                  0.2364
    mut9002                  0.2098
    mea9008                  0.2098
    ths9052                  0.2098

  transplantation_hct_lymphoma_myeloma:
    eku9001                  0.3310
    fyh9001                  0.2337
    alg9117                  0.2183
    jdr9007                  0.1485
    ckg2001                  0.1485

  transplantation_cmv_immunity:
    sap4017                  0.7082
    dmd2001                  0.2785

  transplantation_myeloid_neoplasm_biology:
    dar2042                  0.1576
    jzx2002                  0.1576
    srafii                   0.1576
    szj2001                  0.1576
    yal4002                  0.1576

  transplantation_liver_allograft_diagnostics:
    mut9002                  0.3322
    msuthan                  0.3322
    alg9113                  0.0992
    ank9134                  0.0992
    spd9005                  0.0992

  transplantation_pregnancy_reproductive:
    dls7001                  0.7376

  transplantation_pediatric_hct_complications:
    aim4006                  0.2260
    jlaurenc                 0.2206
    man9026                  0.1485
    nik9015                  0.1024

  transplantation_aml_hematologic_malignancy_therapy:
    ygg9005                  0.2372
    gar2001                  0.1397

  transplantation_cellular_therapy_processing:
    tsc9008                  0.1442

=== backfill_topic.py complete ===
  topic:           transplantation_medicine
  activity_count:  143
  cluster_count:   22
  coverage_pct:    0.958
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_transplantation_medicine.json


-- stderr --
signed; 143 remaining
18:02:45 INFO Classifying 143 PMIDs with concurrency=15, confidence_floor=0.3, dry_run=False
18:02:51 INFO [progress] 50/143 processed: assigned=43, unassigned=7, failed=0, elapsed=7s
18:02:57 INFO [progress] 100/143 processed: assigned=93, unassigned=7, failed=0, elapsed=13s
18:03:02 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:03:02 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:03:02 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:03:02 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:03:03 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:03:03 INFO [progress] 143/143 processed: assigned=136, unassigned=7, failed=0, elapsed=19s
18:03:03 INFO [pass 2: assign] complete
18:03:03 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic transplantation_medicine
18:03:03 INFO Querying DynamoDB partition: TOPIC#transplantation_medicine
18:03:04 INFO Fetched 345 SCORE# rows for transplantation_medicine
18:03:04 INFO Aggregated: 317 rows included, 28 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
18:03:04 INFO Faculty touched: 120
18:03:29 INFO Writes: cleared=120, written=120, dry_run=False
18:03:29 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_transplantation_medicine.json
18:03:29 INFO [pass 3: aggregate] complete
18:03:30 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_transplantation_medicine.json

```
| autoimmune_rheumatologic_disease | 123 | 21 | 92.7% | 3 |  |  | ok |

<!-- === autoimmune_rheumatologic_disease === rc=0 -->
```
         0.9929
    mrs9012                  0.3571
    tac2007                  0.2732
    say4011                  0.2291

  autoimmune_autoreactivity_mechanisms:
    las4011                  1.3119
    ccc4002                  0.7342
    ppn4001                  0.7342
    zuw4001                  0.2863
    shc2034                  0.1842

  autoimmune_psoriasis_spondyloarthritis:
    djl9010                  0.5511
    ejs2005                  0.5511
    gam9044                  0.5511
    ral2006                  0.5511
    mgd2002                  0.3806

  autoimmune_ms_corneal_neurodegeneration:
    ram2045                  1.5848
    inp2002                  0.9649
    zrm2001                  0.3023

  autoimmune_innate_lymphoid_cells:
    gfs2002                  0.6918
    maa4016                  0.6918
    cnp9004                  0.5749
    mel4003                  0.2315
    ros2023                  0.2315

  autoimmune_covid_inflammatory_syndromes:
    mwm9004                  0.2905
    het9037                  0.1972
    khp9007                  0.1972
    mjs9012                  0.1972
    pag9051                  0.1972

  autoimmune_scleroderma_ild_pulmonary:
    kia9010                  0.3470
    mms9024                  0.3470
    ajp9012                  0.3122
    hho2001                  0.2328
    ral2020                  0.2328

  autoimmune_immune_thrombocytopenia_vasculitis:
    sts9057                  0.3207
    cym2003                  0.2701
    cag9152                  0.2129
    mpecker                  0.2113
    kaicker                  0.1137

  autoimmune_rorgt_tryptophan_immunotherapy:
    mog4005                  0.2670
    ccc4002                  0.1771
    formenti                 0.1771
    las4011                  0.1771
    szd3005                  0.1771

  autoimmune_ms_treatment_outcomes:
    jsp9007                  0.5500
    jel2049                  0.3334
    kab9238                  0.2422
    cag4010                  0.1573

  autoimmune_calcium_signaling_immunomodulation:
    khm2002                  0.2214
    msuthan                  0.2214
    fay2004                  0.1355
    rac2017                  0.1355
    stw2006                  0.1355

  autoimmune_autoimmune_gastritis_celiac:
    nip9020                  0.2256
    hmz7001                  0.1839
    inp2002                  0.1153
    ram2045                  0.1153
    mjm9042                  0.1089

  autoimmune_ici_immune_related_adverse_events:
    juz4004                  0.3014
    mjd2004                  0.1324
    eic9024                  0.1324
    rsz4001                  0.1293
    pvn4001                  0.0789

  autoimmune_iga_immunoglobulin_biology:
    lndhlovu                 0.1651
    amh2025                  0.1631
    kas2049                  0.1631
    myz4001                  0.1085
    tap4002                  0.0630

  autoimmune_graves_thyroid_autoimmune:
    nac9126                  0.1736
    bmf9002                  0.1112
    raz2002                  0.1112
    tjfahey                  0.1112
    bustilo                  0.0720

  autoimmune_ibd_clinical_management:
    djl9010                  0.1777
    ejs2005                  0.0953
    kac9091                  0.0865
    nik9015                  0.0865
    aia9015                  0.0617

  autoimmune_covid_neurological_vascular:
    mjd2004                  0.0868
    cym2003                  0.0768
    jlaurenc                 0.0768

  autoimmune_autoimmune_encephalitis_neuropsychiatric:
    bxt9001                  0.1120
    zag9005                  0.1112

=== backfill_topic.py complete ===
  topic:           autoimmune_rheumatologic_disease
  activity_count:  123
  cluster_count:   21
  coverage_pct:    0.9268
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_autoimmune_rheumatologic_disease.json


-- stderr --
ssifying 123 PMIDs with concurrency=15, confidence_floor=0.3, dry_run=False
18:04:38 INFO [progress] 50/123 processed: assigned=49, unassigned=1, failed=0, elapsed=8s
18:04:46 INFO [progress] 100/123 processed: assigned=98, unassigned=2, failed=0, elapsed=15s
18:04:48 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:04:49 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:04:49 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:04:51 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:04:54 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:04:55 INFO [progress] 123/123 processed: assigned=121, unassigned=2, failed=0, elapsed=24s
18:04:55 INFO [pass 2: assign] complete
18:04:55 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic autoimmune_rheumatologic_disease
18:04:55 INFO Querying DynamoDB partition: TOPIC#autoimmune_rheumatologic_disease
18:04:55 INFO Fetched 253 SCORE# rows for autoimmune_rheumatologic_disease
18:04:55 INFO Aggregated: 251 rows included, 2 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
18:04:55 INFO Faculty touched: 126
18:05:22 INFO Writes: cleared=126, written=126, dry_run=False
18:05:22 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_autoimmune_rheumatologic_disease.json
18:05:22 INFO [pass 3: aggregate] complete
18:05:22 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_autoimmune_rheumatologic_disease.json

```
| dermatology_skin_disease | 115 | 14 | 81.7% | 3 |  |  | ok |

<!-- === dermatology_skin_disease === rc=0 -->
```
== Subtopic total_weight table ===
subtopic_id                                               total_weight
------------------------------------------------------- --------------
dermatology_inflammatory_skin_immunology                        6.7486
dermatology_melanoma_immunotherapy                              4.5584
dermatology_melanoma_targeted_therapy_biology                   3.8976
dermatology_psoriasis_treatment_skin_of_color                   3.5813
dermatology_covid19_skin_manifestations                         3.5246
dermatology_hidradenitis_suppurativa                            2.8999
dermatology_atopic_dermatitis_therapeutics                      2.7526
dermatology_acne_skin_of_color                                  2.0844
dermatology_wound_healing_reconstruction                        1.8779
dermatology_melasma_pigmentation_disorders                      1.6775
dermatology_cutaneous_scc_and_rare_tumors                       1.1315
dermatology_cutaneous_lymphoma                                  0.9593
dermatology_infectious_skin_disease                             0.8635
dermatology_skin_cancer_ai_diagnostics                          0.6294

faculty touched: 64
subtopic count: 14
total subtopic score sum (all): 37.1865

=== Top-5 faculty per subtopic ===

  dermatology_inflammatory_skin_immunology:
    rdgranst                 0.9279
    jhzippin                 0.7710
    nia9069                  0.7138
    ole2001                  0.5871
    cem2009                  0.4803

  dermatology_melanoma_immunotherapy:
    jdw2002                  1.7502
    tam2037                  0.5838
    dah4023                  0.5165
    pbc2001                  0.4361
    sab4028                  0.3739

  dermatology_melanoma_targeted_therapy_biology:
    jhzippin                 0.8902
    qic2005                  0.7175
    pbc2001                  0.5011
    juz4004                  0.3765
    rsb4005                  0.2226

  dermatology_psoriasis_treatment_skin_of_color:
    alexisa                  3.5813

  dermatology_covid19_skin_manifestations:
    cym2003                  1.5564
    jlaurenc                 0.7295
    mtd2002                  0.3727
    berlind                  0.3017
    sts9057                  0.3017

  dermatology_hidradenitis_suppurativa:
    src4005                  2.8999

  dermatology_atopic_dermatitis_therapeutics:
    alexisa                  2.6613
    mrl4003                  0.0913

  dermatology_acne_skin_of_color:
    alexisa                  2.0844

  dermatology_wound_healing_reconstruction:
    jas2037                  0.6451
    kim9036                  0.5327
    dmo9004                  0.2137
    dik2002                  0.1021
    wkuhel                   0.1021

  dermatology_melasma_pigmentation_disorders:
    alexisa                  0.8401
    dmo9004                  0.3550
    lig4013                  0.3467
    kim9036                  0.1356

  dermatology_cutaneous_scc_and_rare_tumors:
    cad7015                  0.3940
    bpe9002                  0.3887
    ecesarm                  0.0882
    ole2001                  0.0882
    dal9152                  0.0625

  dermatology_cutaneous_lymphoma:
    cym2003                  0.6882
    cad7015                  0.2711

  dermatology_infectious_skin_disease:
    gre9006                  0.1832
    het9037                  0.1832
    mag2005                  0.1832
    ecesarm                  0.1147
    sap9151                  0.1147

  dermatology_skin_cancer_ai_diagnostics:
    juz4004                  0.2554
    rsb4005                  0.2554
    few2001                  0.1187

=== backfill_topic.py complete ===
  topic:           dermatology_skin_disease
  activity_count:  115
  cluster_count:   14
  coverage_pct:    0.8174
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_dermatology_skin_disease.json


-- stderr --
signed; 115 remaining
18:06:26 INFO Classifying 115 PMIDs with concurrency=15, confidence_floor=0.3, dry_run=False
18:06:33 INFO [progress] 50/115 processed: assigned=38, unassigned=12, failed=0, elapsed=7s
18:06:38 INFO [progress] 100/115 processed: assigned=86, unassigned=14, failed=0, elapsed=12s
18:06:40 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:06:40 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:06:40 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:06:40 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:06:41 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:06:41 INFO [progress] 115/115 processed: assigned=100, unassigned=15, failed=0, elapsed=15s
18:06:41 INFO [pass 2: assign] complete
18:06:41 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic dermatology_skin_disease
18:06:42 INFO Querying DynamoDB partition: TOPIC#dermatology_skin_disease
18:06:42 INFO Fetched 188 SCORE# rows for dermatology_skin_disease
18:06:42 INFO Aggregated: 150 rows included, 38 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
18:06:42 INFO Faculty touched: 64
18:06:56 INFO Writes: cleared=64, written=64, dry_run=False
18:06:56 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_dermatology_skin_disease.json
18:06:56 INFO [pass 3: aggregate] complete
18:06:56 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_dermatology_skin_disease.json

```
| gene_cell_therapy | 110 | 21 | 96.4% | 3 |  |  | ok |

<!-- === gene_cell_therapy === rc=0 -->
```
                 1.8937
    gar2001                  1.0697
    ggi9001                  0.9767
    eku9001                  0.8266
    formenti                 0.8240

  gene_aav_vector_design_safety:
    rgcryst                  2.1191
    smkamins                 1.8404
    dos2011                  1.1738
    mrr2006                  0.6073
    ekf4001                  0.4881

  gene_t_cell_immunotherapy:
    moh4006                  0.7043
    ole2001                  0.5616
    liy2010                  0.5616
    jdw2002                  0.4820
    tam2037                  0.4820

  gene_ocular_gene_therapy:
    szk7001                  2.1821
    boy2004                  0.6304
    shn2010                  0.6304
    kyk9011                  0.5816
    djd2003                  0.1849

  gene_beta_cell_diabetes_therapy:
    chc2062                  0.5301
    shc2034                  0.5301
    jzx2002                  0.2831
    lal2018                  0.2831
    tre2003                  0.2831

  gene_tumor_antigen_immunotherapy:
    ecesarm                  0.4122
    ole2001                  0.3221
    szd3005                  0.2908
    ala2035                  0.2492
    ccc4002                  0.2278

  gene_endothelial_vascular_biology:
    yal4002                  0.6347
    dar2042                  0.5343
    srafii                   0.5343
    jzx2002                  0.3750
    szj2001                  0.3750

  gene_hiv_reservoir_immunotherapy:
    rbjones                  0.9560
    abd4001                  0.6978
    lndhlovu                 0.1977
    tap4002                  0.1977
    cmd9008                  0.1631

  gene_ipsc_genome_editing:
    lud2005                  0.6294
    zrosenw                  0.2569
    elt4010                  0.2270
    djj2001                  0.1423
    jeg2039                  0.1423

  gene_cancer_immunotherapy_microenvironment:
    jdw2002                  0.5092
    dar2042                  0.3463
    tam2037                  0.3463
    dah4023                  0.3463
    iss4009                  0.3463

  gene_hepatocyte_liver_therapy:
    ydj2001                  0.8174
    rgcryst                  0.3286
    mrr2006                  0.3286
    pleopold                 0.3286
    res2025                  0.2021

  gene_pharmacological_chaperones:
    frmaxfie                 0.4294
    jab2058                  0.2728
    mas2189                  0.2728
    srj2003                  0.2387
    jad2033                  0.2387

  gene_eosinophil_gene_therapy:
    rgcryst                  0.7045
    smkamins                 0.3570
    nhackett                 0.3570

  gene_nanodelivery_platforms:
    sel2013                  0.3364
    sbl2004                  0.1703
    nad2012                  0.1661
    xim2002                  0.1661
    zhc2006                  0.1661

  gene_mrna_rna_therapeutics:
    srj2003                  0.8477
    sap4017                  0.1196

  gene_cardiac_disc_regeneration:
    roh9005                  0.2964
    map2007                  0.2584
    stp9039                  0.2584

  gene_gut_microbiome_genetic_tools:
    chg4001                  0.2481
    daa2028                  0.1576
    moa4006                  0.1576
    ral2006                  0.1576

  gene_cell_therapy_manufacturing:
    pid9006                  0.2129
    jas2037                  0.2078
    tsc9008                  0.1629

  gene_gene_therapy_ethics_policy:
    imd2001                  0.2651
    rgcryst                  0.2195

  gene_bac_vector_engineering:
    shg3006                  0.2556
    hut4001                  0.0984

=== backfill_topic.py complete ===
  topic:           gene_cell_therapy
  activity_count:  110
  cluster_count:   21
  coverage_pct:    0.9636
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_gene_cell_therapy.json


-- stderr --
ws for gene_cell_therapy
18:08:05 INFO After score floor (0.3): 313 qualified rows
18:08:05 INFO Unique PMIDs: 110
18:08:05 INFO --resume: skipping 0 PMIDs already assigned; 110 remaining
18:08:05 INFO Classifying 110 PMIDs with concurrency=15, confidence_floor=0.3, dry_run=False
18:08:13 INFO [progress] 50/110 processed: assigned=44, unassigned=6, failed=0, elapsed=8s
18:08:20 INFO [progress] 100/110 processed: assigned=94, unassigned=6, failed=0, elapsed=15s
18:08:21 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:08:21 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:08:21 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:08:21 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:08:22 INFO [progress] 110/110 processed: assigned=103, unassigned=7, failed=0, elapsed=17s
18:08:22 INFO [pass 2: assign] complete
18:08:22 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic gene_cell_therapy
18:08:22 INFO Querying DynamoDB partition: TOPIC#gene_cell_therapy
18:08:22 INFO Fetched 313 SCORE# rows for gene_cell_therapy
18:08:22 INFO Aggregated: 294 rows included, 19 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
18:08:22 INFO Faculty touched: 147
18:08:54 INFO Writes: cleared=147, written=147, dry_run=False
18:08:54 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_gene_cell_therapy.json
18:08:54 INFO [pass 3: aggregate] complete
18:08:54 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_gene_cell_therapy.json

```
| microbiome_research | 102 | 19 | 91.2% | 3 |  |  | ok |

<!-- === microbiome_research === rc=0 -->
```
  0.6590
    dic2009                  0.6590
    daa2028                  0.5432

  microbiome_bile_acids_metabolism:
    chg4001                  1.7906
    daa2028                  1.7906
    moa4006                  1.7906
    gfs2002                  0.6200
    mel4003                  0.6200

  microbiome_diet_nutrition:
    moa4006                  1.6124
    chg4001                  1.0817
    wez4002                  1.0817
    daa2028                  1.0817
    stw2006                  0.6301

  microbiome_ibd_gut_inflammation:
    ral2006                  1.5801
    djl9010                  1.2616
    ejs2005                  1.2616
    chg4001                  0.9236
    gam9044                  0.8625

  microbiome_gut_immune_interactions:
    chg4001                  1.1264
    myz4001                  1.0058
    mel4003                  0.5640
    wez4002                  0.5640
    daa2028                  0.5640

  microbiome_spaceflight_omics:
    chm2042                  1.5928
    cem2009                  1.3308
    rdgranst                 0.7843
    dcl2001                  0.7270
    irm2224                  0.7270

  microbiome_fmt_therapeutic:
    ral2006                  1.0031
    ejs2005                  1.0031
    chg4001                  0.6275
    cvc9002                  0.6275
    sdp4001                  0.6275

  microbiome_transplant_clinical:
    dmd2001                  1.1617
    law9067                  1.0144
    mjs9012                  1.0144
    lim9120                  0.4674
    wol4002                  0.2914

  microbiome_ilc3_mucosal_immunity:
    gfs2002                  1.4440
    maa4016                  0.9314
    ros2023                  0.8165
    mel4003                  0.6251
    lak4008                  0.3936

  microbiome_cancer_immunotherapy:
    gfs2002                  1.0476
    mas9313                  0.7603
    mel4003                  0.5181
    yus4011                  0.2963
    wol4002                  0.2914

  microbiome_statistical_computational:
    wol4002                  1.5628
    yus4011                  0.7741
    him4004                  0.6743
    bmf9002                  0.2364
    raz2002                  0.2364

  microbiome_tools_methods_collection:
    chg4001                  1.0722
    ral2006                  0.6380
    daa2028                  0.6380
    moa4006                  0.6380
    chm2042                  0.2963

  microbiome_antibiotic_resistance:
    chm2042                  1.4027
    cem2009                  0.5800
    imh2003                  0.5800
    sbm4003                  0.3696

  microbiome_schistosoma_parasitic:
    jna2002                  0.9274
    myl2003                  0.6052
    drw2004                  0.3222
    lndhlovu                 0.3222
    dwf2001                  0.2874

  microbiome_intestinal_barrier_neonatal:
    gfs2002                  0.3444
    ros2023                  0.3444
    vjk9004                  0.1356
    ljgudas                  0.0711
    mam2185                  0.0711

  microbiome_gut_brain_stroke:
    coi2001                  0.4533
    joa2006                  0.4533
    alm9097                  0.0730
    hok9010                  0.0730

  microbiome_respiratory_upper_airway:
    dip9063                  0.2612
    aam9008                  0.2611
    anp2022                  0.2611

  microbiome_reproductive_urogenital:
    liy2010                  0.2438
    dim2018                  0.0525
    sas9303                  0.0525
    tsa9005                  0.0525

  microbiome_engineered_bacteria_therapeutics:
    jur2016                  0.1725

=== backfill_topic.py complete ===
  topic:           microbiome_research
  activity_count:  102
  cluster_count:   19
  coverage_pct:    0.9118
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_microbiome_research.json


-- stderr --
kipping 0 PMIDs already assigned; 102 remaining
18:09:55 INFO Classifying 102 PMIDs with concurrency=15, confidence_floor=0.3, dry_run=False
18:10:02 INFO [progress] 50/102 processed: assigned=42, unassigned=8, failed=0, elapsed=7s
18:10:08 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:10:08 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:10:09 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:10:09 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:10:09 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:10:09 INFO [progress] 100/102 processed: assigned=92, unassigned=8, failed=0, elapsed=14s
18:10:09 INFO [progress] 102/102 processed: assigned=94, unassigned=8, failed=0, elapsed=14s
18:10:09 INFO [pass 2: assign] complete
18:10:09 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic microbiome_research
18:10:10 INFO Querying DynamoDB partition: TOPIC#microbiome_research
18:10:11 INFO Fetched 279 SCORE# rows for microbiome_research
18:10:11 INFO Aggregated: 253 rows included, 26 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
18:10:11 INFO Faculty touched: 105
18:10:34 INFO Writes: cleared=105, written=105, dry_run=False
18:10:34 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_microbiome_research.json
18:10:34 INFO [pass 3: aggregate] complete
18:10:34 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_microbiome_research.json

```
| neuro_oncology | 88 | 14 | 86.4% | 3 |  |  | ok |

<!-- === neuro_oncology === rc=0 -->
```
------------ --------------
neuro_meningioma_imaging_treatment                             20.5801
neuro_glioma_molecular_biology                                 13.4357
neuro_pediatric_cns_tumors                                      7.6629
neuro_glioma_molecular_diagnostics                              6.4720
neuro_glioma_treatment_clinical                                 4.6264
neuro_cns_lymphoma_rare_tumors                                  4.0162
neuro_dipg_ced                                                  2.7231
neuro_skull_base_surgery                                        2.5279
neuro_brain_metastases                                          2.3661
neuro_drug_delivery_bbb                                         2.1905
neuro_brain_tumor_mri_imaging                                   1.4343
neuro_rehabilitation_outcomes                                   0.8214
neuro_intraoperative_mapping                                    0.7913
neuro_glioma_organoids_models                                   0.2550

faculty touched: 76
subtopic count: 14
total subtopic score sum (all): 69.9029

=== Top-5 faculty per subtopic ===

  neuro_meningioma_imaging_treatment:
    jai9018                  2.7540
    ror9068                  2.6725
    jro7001                  2.3810
    mir9146                  2.3810
    nak2032                  2.0767

  neuro_glioma_molecular_biology:
    haf9016                  1.5238
    ris2020                  1.5238
    smc2011                  1.5238
    djp2002                  0.8216
    ole2001                  0.8216

  neuro_pediatric_cns_tumors:
    mmsouwei                 1.6762
    nad2639                  1.1149
    brm4007                  1.1149
    jpgreenf                 0.8904
    djp2002                  0.5613

  neuro_glioma_molecular_diagnostics:
    djp2002                  1.3014
    bel9057                  1.0099
    ram9116                  0.8656
    haf9016                  0.5465
    jas9373                  0.3061

  neuro_glioma_treatment_clinical:
    haf9016                  1.0712
    mog4005                  0.7109
    clv2002                  0.3570
    nad2639                  0.3475
    jpgreenf                 0.3475

  neuro_cns_lymphoma_rare_tumors:
    ged9047                  0.5786
    jai9018                  0.3644
    achadbur                 0.3286
    zhc2006                  0.3286
    sbs9011                  0.3133

  neuro_dipg_ced:
    mmsouwei                 2.0091
    sbl2004                  0.3570
    vab2008                  0.3570

  neuro_skull_base_surgery:
    ale2009                  0.9929
    anb2029                  0.9929
    pes2008                  0.5422

  neuro_brain_metastases:
    kab4027                  0.3585
    mac9795                  0.2463
    ans2077                  0.1064
    brr2006                  0.1064
    djp2002                  0.1064

  neuro_drug_delivery_bbb:
    jai9018                  0.4246
    jpgreenf                 0.4246
    jak9030                  0.4246
    nad2639                  0.3055
    mmsouwei                 0.3055

  neuro_brain_tumor_mri_imaging:
    sgk4001                  0.5850
    asm2008                  0.4246
    eas9018                  0.4246

  neuro_rehabilitation_outcomes:
    ror9068                  0.4862
    nac9076                  0.1962
    krd4004                  0.0760
    hab9075                  0.0630

  neuro_intraoperative_mapping:
    pes2008                  0.2223
    ror9068                  0.1897
    pak9012                  0.1897
    stk9005                  0.1897

  neuro_glioma_organoids_models:
    haf9016                  0.2550

=== backfill_topic.py complete ===
  topic:           neuro_oncology
  activity_count:  88
  cluster_count:   14
  coverage_pct:    0.8636
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_neuro_oncology.json


-- stderr --
_oncology
18:11:12 INFO After score floor (0.3): 256 qualified rows
18:11:12 INFO Unique PMIDs: 88
18:11:12 INFO --resume: skipping 0 PMIDs already assigned; 88 remaining
18:11:12 INFO Classifying 88 PMIDs with concurrency=15, confidence_floor=0.3, dry_run=False
18:11:19 INFO [progress] 50/88 processed: assigned=40, unassigned=10, failed=0, elapsed=7s
18:11:23 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:11:23 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:11:24 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:11:25 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:11:29 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:11:30 INFO [progress] 88/88 processed: assigned=78, unassigned=10, failed=0, elapsed=17s
18:11:30 INFO [pass 2: assign] complete
18:11:30 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic neuro_oncology
18:11:30 INFO Querying DynamoDB partition: TOPIC#neuro_oncology
18:11:30 INFO Fetched 256 SCORE# rows for neuro_oncology
18:11:30 INFO Aggregated: 237 rows included, 19 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
18:11:30 INFO Faculty touched: 76
18:11:46 INFO Writes: cleared=76, written=76, dry_run=False
18:11:46 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_neuro_oncology.json
18:11:46 INFO [pass 3: aggregate] complete
18:11:46 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_neuro_oncology.json

```
| lung_cancer | 86 | 18 | 95.3% | 3 |  |  | ok |

<!-- === lung_cancer === rc=0 -->
```
erence_access                          0.4118
lung_pan_cancer_biomarkers_diagnostics                          0.1615

faculty touched: 81
subtopic count: 18
total subtopic score sum (all): 79.5344

=== Top-5 faculty per subtopic ===

  lung_surgical_resection_extent:
    nkaltork                 4.5385
    jlp2002                  3.1263
    bel9026                  2.4478
    swh9002                  2.4478
    jov9069                  2.1098

  lung_neoadjuvant_immunotherapy:
    nkaltork                 1.6846
    bel9026                  1.6846
    jlp2002                  1.6846
    formenti                 1.3085
    ahs9018                  1.3085

  lung_tumor_microenvironment_immunity:
    ole2001                  1.1531
    vim2010                  0.9449
    nkaltork                 0.9449
    temcgraw                 0.9449
    anm4031                  0.5999

  lung_genomic_profiling_liquid_biopsy:
    ahs9018                  0.7460
    emh9016                  0.5534
    prv9013                  0.5534
    jas9373                  0.5195
    mos9084                  0.4234

  lung_surgical_outcomes_perioperative:
    nkaltork                 0.8625
    jgc9012                  0.7762
    jov9069                  0.5526
    bel9026                  0.5526
    jlp2002                  0.5526

  lung_metabolic_vulnerabilities:
    job2064                  0.9420
    jrf9008                  0.8755
    varmus                   0.8755
    min2015                  0.7214
    qic2005                  0.4674

  lung_molecular_drivers_targeted_therapy:
    lud2005                  0.9932
    cag9152                  0.4641
    ole2001                  0.4443
    ekk2003                  0.4443
    nkaltork                 0.3322

  lung_screening_disparities:
    erp2001                  0.6209
    rmt4001                  0.6209
    euc4006                  0.4956
    brp9018                  0.4342
    lcp2003                  0.3979

  lung_second_primary_cancer_surveillance:
    euc4006                  2.3146
    nkaltork                 0.4641
    eao9004                  0.2963

  lung_immunotherapy_biomarkers:
    nkaltork                 0.6630
    temcgraw                 0.6630
    few2001                  0.3858
    ole2001                  0.3137
    vim2010                  0.3137

  lung_combination_immunotherapy_strategies:
    vim2010                  0.7781
    nkaltork                 0.5111
    dig2009                  0.5111
    jov9069                  0.2670
    iss4009                  0.1492

  lung_sclc_histological_transformation:
    varmus                   1.1033
    asl4003                  0.6590
    ole2001                  0.4443

  lung_treatment_disparities_outcomes:
    jov9069                  0.4530
    nkaltork                 0.4530
    rmt4001                  0.4530

  lung_pulmonary_nodule_management:
    brp9018                  0.2841
    jgb9001                  0.1918
    acl9007                  0.1356
    frg9051                  0.1356
    srs9034                  0.1356

  lung_preclinical_models_drug_delivery:
    ggi9001                  0.2914
    msb2006                  0.2653
    ajo9001                  0.2260
    beh2020                  0.0673

  lung_treatment_delay_tumor_microbiome:
    ili2001                  0.1963
    formenti                 0.1649
    jon9024                  0.1649

  lung_cancer_screening_adherence_access:
    baa2012                  0.1596
    ban9003                  0.0841
    hok9010                  0.0841
    stt2007                  0.0841

  lung_pan_cancer_biomarkers_diagnostics:
    mac9795                  0.1615

=== backfill_topic.py complete ===
  topic:           lung_cancer
  activity_count:  86
  cluster_count:   18
  coverage_pct:    0.9535
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_lung_cancer.json


-- stderr --
SCORE# rows for lung_cancer
18:12:31 INFO After score floor (0.3): 241 qualified rows
18:12:31 INFO Unique PMIDs: 86
18:12:31 INFO --resume: skipping 0 PMIDs already assigned; 86 remaining
18:12:31 INFO Classifying 86 PMIDs with concurrency=15, confidence_floor=0.3, dry_run=False
18:12:38 INFO [progress] 50/86 processed: assigned=47, unassigned=3, failed=0, elapsed=7s
18:12:41 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:12:42 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:12:42 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:12:42 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:12:42 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:12:43 INFO [progress] 86/86 processed: assigned=83, unassigned=3, failed=0, elapsed=12s
18:12:43 INFO [pass 2: assign] complete
18:12:43 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic lung_cancer
18:12:43 INFO Querying DynamoDB partition: TOPIC#lung_cancer
18:12:43 INFO Fetched 241 SCORE# rows for lung_cancer
18:12:43 INFO Aggregated: 234 rows included, 7 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
18:12:43 INFO Faculty touched: 81
18:13:01 INFO Writes: cleared=81, written=81, dry_run=False
18:13:01 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_lung_cancer.json
18:13:01 INFO [pass 3: aggregate] complete
18:13:01 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_lung_cancer.json

```
| environmental_planetary_health | 68 | 14 | 89.7% | 3 |  |  | ok |

<!-- === environmental_planetary_health === rc=0 -->
```
              2.1725
environmental_9_11_pasc_environmental_risk                      2.0450
environmental_chemical_exposures_toxicology                     1.9431
environmental_tobacco_smoke_lung                                1.8015
environmental_urban_microbiome_amr                              1.6306
environmental_occupational_worker_health                        1.1145
environmental_temperature_cardiovascular                        0.8099
environmental_vector_borne_water_sanitation                     0.5764
environmental_epigenetic_aging_exposures                        0.4371

faculty touched: 80
subtopic count: 14
total subtopic score sum (all): 30.4145

=== Top-5 faculty per subtopic ===

  environmental_hurricane_disaster_health:
    akg9010                  1.8831
    mms9024                  0.7706
    mrd2006                  0.7001
    few2001                  0.4057
    arj2005                  0.3465

  environmental_spaceflight_health:
    chm2042                  0.9805
    cem2009                  0.7050
    irm2224                  0.4362
    num2008                  0.3602
    dop9054                  0.2919

  environmental_lead_toxic_metals_lmic:
    dwf2001                  0.5999
    jwp2001                  0.5999
    var4002                  0.5999
    liy9032                  0.4542
    kfw2001                  0.4542

  environmental_heat_illness_climate:
    jur9123                  1.9108
    akg9010                  0.6076
    mrd2006                  0.3639
    say4011                  0.2437

  environmental_air_pollution_respiratory:
    pep9004                  0.6130
    rkaner                   0.3557
    jwp2001                  0.2617
    var4002                  0.2617
    liy9032                  0.2617

  environmental_built_environment_health:
    yiz2014                  0.5413
    arr2014                  0.4159
    alh9039                  0.2554
    roj9069                  0.2554
    lcp2003                  0.1760

  environmental_9_11_pasc_environmental_risk:
    few2001                  0.4641
    chz4001                  0.4641
    rak2007                  0.4641
    yoz2009                  0.4641
    and2033                  0.1885

  environmental_chemical_exposures_toxicology:
    rmt4001                  0.5058
    rnp2002                  0.2702
    oaz4001                  0.2522
    bmf9002                  0.2291
    raz2002                  0.2291

  environmental_tobacco_smoke_lung:
    hho2001                  0.1849
    ljgudas                  0.1617
    mam2185                  0.1617
    ths9004                  0.1617
    dabehrm                  0.1187

  environmental_urban_microbiome_amr:
    chm2042                  0.5912
    cem2009                  0.3566
    imh2003                  0.3566
    sbm4003                  0.3262

  environmental_occupational_worker_health:
    mrs9012                  0.3990
    mms9024                  0.2180
    erp2001                  0.2180
    jur9123                  0.1065
    anr2783                  0.0923

  environmental_temperature_cardiovascular:
    few2001                  0.4363
    bjr4002                  0.1629
    shc9182                  0.1629
    dop9054                  0.0478

  environmental_vector_borne_water_sanitation:
    ras9199                  0.2268
    sdp4001                  0.1727
    hsc2001                  0.0885
    lja2002                  0.0885

  environmental_epigenetic_aging_exposures:
    kmv4001                  0.1561
    mfm2003                  0.1001
    krp2013                  0.0904
    mtoth                    0.0904

=== backfill_topic.py complete ===
  topic:           environmental_planetary_health
  activity_count:  68
  cluster_count:   14
  coverage_pct:    0.8971
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_environmental_planetary_health.json


-- stderr --
ned=46, unassigned=4, failed=0, elapsed=7s
18:13:43 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:13:43 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:13:43 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:13:43 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:13:43 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:13:44 INFO [progress] 68/68 processed: assigned=64, unassigned=4, failed=0, elapsed=9s
18:13:44 INFO [pass 2: assign] complete
18:13:44 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic environmental_planetary_health
18:13:44 INFO Querying DynamoDB partition: TOPIC#environmental_planetary_health
18:13:44 INFO Fetched 138 SCORE# rows for environmental_planetary_health
18:13:44 INFO Aggregated: 134 rows included, 4 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
18:13:44 INFO Faculty touched: 80
18:13:52 WARNING clear_faculty_subtopic_scores_for_topic failed for pid=mae2001: An error occurred (ValidationException) when calling the UpdateItem operation: The document path provided in the update expression is invalid for update
18:14:03 INFO Writes: cleared=79, written=80, dry_run=False
18:14:03 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_environmental_planetary_health.json
18:14:03 INFO [pass 3: aggregate] complete
18:14:04 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_environmental_planetary_health.json

```
| gynecologic_oncology | 58 | 13 | 96.5% | 3 |  |  | ok |

<!-- === gynecologic_oncology === rc=0 -->
```
8
gynecologic_ovarian_cancer_immunology_tumor_microenvironment         6.3416
gynecologic_disparities_access_care                             2.9285
gynecologic_ovarian_cancer_pathology_histology                  2.7899
gynecologic_ovarian_cancer_molecular_therapeutics               2.7720
gynecologic_ovarian_cancer_survivorship_psychosocial            2.7428
gynecologic_endometrial_cancer_molecular_pathology              2.5398
gynecologic_hpv_infection_vaccination                           1.4314
gynecologic_cervical_cancer_hpv_screening_cytology              1.4156
gynecologic_brca_somatic_germline_genomics                      0.7820
gynecologic_lynch_syndrome_endometrial_screening                0.7412
gynecologic_minimally_invasive_surgical_techniques              0.2343
gynecologic_vulvar_rare_tumor_pathology                         0.1388

faculty touched: 57
subtopic count: 13
total subtopic score sum (all): 34.7635

=== Top-5 faculty per subtopic ===

  gynecologic_hereditary_cancer_genetic_testing_cascade:
    mkf2002                  2.1728
    ras9030                  1.7630
    pac2001                  1.5202
    elc9120                  1.3380
    evc2005                  0.7348

  gynecologic_ovarian_cancer_immunology_tumor_microenvironment:
    jur2016                  1.7601
    elc9120                  0.8449
    evc2005                  0.8449
    dim2018                  0.8449
    xim2002                  0.5653

  gynecologic_disparities_access_care:
    elc9120                  0.5998
    mkf2002                  0.4342
    pac2001                  0.4342
    daf2037                  0.4342
    hic9014                  0.4342

  gynecologic_ovarian_cancer_pathology_histology:
    aim4006                  0.4060
    bab4001                  0.3293
    pac2001                  0.3047
    bmf9002                  0.3047
    raz2002                  0.3047

  gynecologic_ovarian_cancer_molecular_therapeutics:
    ole2001                  0.7842
    beh2020                  0.7842
    jmm9018                  0.4246
    jom2042                  0.3191
    jkw7001                  0.2337

  gynecologic_ovarian_cancer_survivorship_psychosocial:
    elc9120                  0.7328
    mkf2002                  0.7328
    pac2001                  0.5235
    evc2005                  0.4521
    ras9030                  0.2093

  gynecologic_endometrial_cancer_molecular_pathology:
    bab4001                  0.4711
    nfs9002                  0.3099
    nad2012                  0.3099
    zhc2006                  0.3099
    any4004                  0.2143

  gynecologic_hpv_infection_vaccination:
    gre9006                  0.3854
    jna2002                  0.2352
    myl2003                  0.2352
    als2026                  0.1832
    loc2008                  0.1832

  gynecologic_cervical_cancer_hpv_screening_cytology:
    abg9017                  0.9115
    jjh7002                  0.1499
    mos9084                  0.1499
    emm4010                  0.1412
    nfs9002                  0.0630

  gynecologic_brca_somatic_germline_genomics:
    ans2077                  0.1963
    jmm9018                  0.1963
    ole2001                  0.1963
    mac9795                  0.1930

  gynecologic_lynch_syndrome_endometrial_screening:
    mkf2002                  0.1962
    cfrissor                 0.1962
    fhs2001                  0.1962
    ras9030                  0.1526

  gynecologic_minimally_invasive_surgical_techniques:
    kjp9013                  0.1207
    jel9064                  0.1137

  gynecologic_vulvar_rare_tumor_pathology:
    bab4001                  0.1388

=== backfill_topic.py complete ===
  topic:           gynecologic_oncology
  activity_count:  58
  cluster_count:   13
  coverage_pct:    0.9655
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_gynecologic_oncology.json


-- stderr --
 DynamoDB partition: TOPIC#gynecologic_oncology
18:14:38 INFO Fetched 142 raw SCORE# rows for gynecologic_oncology
18:14:38 INFO After score floor (0.3): 142 qualified rows
18:14:38 INFO Unique PMIDs: 58
18:14:38 INFO --resume: skipping 0 PMIDs already assigned; 58 remaining
18:14:38 INFO Classifying 58 PMIDs with concurrency=15, confidence_floor=0.3, dry_run=False
18:14:46 INFO [progress] 50/58 processed: assigned=49, unassigned=1, failed=0, elapsed=8s
18:14:46 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:14:46 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:14:46 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:14:46 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:14:46 INFO [progress] 58/58 processed: assigned=57, unassigned=1, failed=0, elapsed=9s
18:14:47 INFO [pass 2: assign] complete
18:14:47 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic gynecologic_oncology
18:14:47 INFO Querying DynamoDB partition: TOPIC#gynecologic_oncology
18:14:47 INFO Fetched 142 SCORE# rows for gynecologic_oncology
18:14:47 INFO Aggregated: 140 rows included, 2 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
18:14:47 INFO Faculty touched: 57
18:14:59 INFO Writes: cleared=57, written=57, dry_run=False
18:14:59 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_gynecologic_oncology.json
18:14:59 INFO [pass 3: aggregate] complete
18:14:59 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_gynecologic_oncology.json

```
| sleep_medicine_circadian_biology | 54 | 14 | 85.2% | 3 |  |  | ok |

<!-- === sleep_medicine_circadian_biology === rc=0 -->
```
 total_weight
------------------------------------------------------- --------------
sleep_neurodegeneration_cognition                               3.8890
sleep_wearable_technology_monitoring                            3.3099
sleep_osa_clinical_populations                                  2.9187
sleep_covid_pandemic_disruption                                 2.2731
sleep_auditory_environmental_interventions                      1.7417
sleep_tinnitus_comorbidities                                    1.5887
sleep_circadian_molecular_mechanisms                            1.4431
sleep_parkinsons_rbd                                            1.2493
sleep_osa_cardiovascular_metabolic                              1.0769
sleep_mental_health_psychiatric                                 0.7171
sleep_fatigue_occupational                                      0.5907
sleep_hrv_autonomic_nocturnal                                   0.3897
sleep_palliative_care_education                                 0.3529
sleep_osa_treatment_devices                                     0.3007

faculty touched: 67
subtopic count: 14
total subtopic score sum (all): 21.8415

=== Top-5 faculty per subtopic ===

  sleep_neurodegeneration_cognition:
    liz2018                  0.5313
    tab2006                  0.5313
    yil4008                  0.5313
    ans7034                  0.3802
    gcc9004                  0.3802

  sleep_wearable_technology_monitoring:
    aaa4027                  0.6041
    ara4013                  0.6041
    jis2011                  0.6041
    ack2003                  0.2496
    abj9004                  0.2496

  sleep_osa_clinical_populations:
    rsw9006                  0.7843
    keb9155                  0.4711
    klk9001                  0.4437
    sea2003                  0.4437
    kap9009                  0.3405

  sleep_covid_pandemic_disruption:
    ans7034                  0.2325
    chz4001                  0.1371
    ejs9005                  0.1371
    few2001                  0.1371
    khd9010                  0.1371

  sleep_auditory_environmental_interventions:
    ack2003                  0.7174
    akg9010                  0.3756
    mrd2006                  0.3756
    mae2001                  0.2731

  sleep_tinnitus_comorbidities:
    chr9008                  0.3322
    lig2002                  0.3322
    nig9049                  0.3322
    jaw9031                  0.3047
    prd2009                  0.2874

  sleep_circadian_molecular_mechanisms:
    mnt4002                  0.7999
    dic2009                  0.3216
    dpc2003                  0.3216

  sleep_parkinsons_rbd:
    has9059                  0.5427
    suy9023                  0.3142
    few2001                  0.1962
    ack2003                  0.1962

  sleep_osa_cardiovascular_metabolic:
    ack2003                  0.2653
    jaw9031                  0.2371
    stp9039                  0.2371
    ask9001                  0.2352
    kyk9011                  0.1021

  sleep_mental_health_psychiatric:
    zrm2001                  0.2337
    haa2019                  0.2337
    ves4007                  0.1472
    qiz4006                  0.1024

  sleep_fatigue_occupational:
    arr2014                  0.4345
    mfw4002                  0.0781
    pfi9001                  0.0781

  sleep_hrv_autonomic_nocturnal:
    rnp2002                  0.1586
    lmo2003                  0.1187
    cpn2002                  0.0562
    myl2003                  0.0562

  sleep_palliative_care_education:
    ack2003                  0.3529

  sleep_osa_treatment_devices:
    jrs9016                  0.3007

=== backfill_topic.py complete ===
  topic:           sleep_medicine_circadian_biology
  activity_count:  54
  cluster_count:   14
  coverage_pct:    0.8519
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_sleep_medicine_circadian_biology.json


-- stderr --
e PMIDs: 54
18:15:34 INFO --resume: skipping 0 PMIDs already assigned; 54 remaining
18:15:34 INFO Classifying 54 PMIDs with concurrency=15, confidence_floor=0.3, dry_run=False
18:15:40 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:15:40 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:15:40 INFO [progress] 50/54 processed: assigned=42, unassigned=8, failed=0, elapsed=7s
18:15:41 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:15:41 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:15:47 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:15:47 INFO [progress] 54/54 processed: assigned=46, unassigned=8, failed=0, elapsed=13s
18:15:47 INFO [pass 2: assign] complete
18:15:47 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic sleep_medicine_circadian_biology
18:15:47 INFO Querying DynamoDB partition: TOPIC#sleep_medicine_circadian_biology
18:15:47 INFO Fetched 120 SCORE# rows for sleep_medicine_circadian_biology
18:15:47 INFO Aggregated: 97 rows included, 23 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
18:15:47 INFO Faculty touched: 67
18:16:03 INFO Writes: cleared=67, written=67, dry_run=False
18:16:03 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_sleep_medicine_circadian_biology.json
18:16:03 INFO [pass 3: aggregate] complete
18:16:03 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_sleep_medicine_circadian_biology.json

```
| melanoma_skin_cancer | 48 | 12 | 87.5% | 3 |  |  | ok |

<!-- === melanoma_skin_cancer === rc=0 -->
```
oma_skin_cancer.json

=== Pass 2 Assignment Results ===
  topic_id: melanoma_skin_cancer
  total_pmids_in_topic: 48
  skipped_resume: 0
  processed: 48
  assigned: 45
  unassigned: 3
  failed: 0
  failed_pmids: 0 (first 10: [])
  rows_written: 105
  coverage_pct_of_processed: 0.9375
  input_tokens: 75080
  output_tokens: 3914
  estimated_cost_usd: 0.0946
  elapsed_s: 7.0

=== Subtopic total_weight table ===
subtopic_id                                               total_weight
------------------------------------------------------- --------------
melanoma_checkpoint_inhibitor_outcomes                          5.7564
melanoma_combination_immunotherapy_preclinical                  5.4443
melanoma_car_t_immunotherapy                                    5.1559
melanoma_tumor_immunology_til                                   3.8863
melanoma_molecular_biology_metastasis                           3.6839
melanoma_liquid_biopsy_ctdna                                    3.4024
melanoma_braf_targeted_therapy                                  2.1416
melanoma_epidemiology_disparities                               1.3141
melanoma_cutaneous_lymphoma                                     0.9216
melanoma_cutaneous_scc_immunosuppression                        0.7244
melanoma_surgical_reconstruction                                0.5423
melanoma_ai_early_detection                                     0.1340

faculty touched: 40
subtopic count: 12
total subtopic score sum (all): 33.1071

=== Top-5 faculty per subtopic ===

  melanoma_checkpoint_inhibitor_outcomes:
    jdw2002                  3.0819
    pbc2001                  1.3984
    acp9008                  0.7192
    juz4004                  0.4248
    stm2006                  0.1321

  melanoma_combination_immunotherapy_preclinical:
    jdw2002                  1.1583
    sab4028                  1.1583
    tam2037                  1.1583
    roz4002                  0.8699
    dah4023                  0.6226

  melanoma_car_t_immunotherapy:
    jdw2002                  0.9317
    tam2037                  0.9317
    dah4023                  0.9317
    sab4028                  0.6110
    iss4009                  0.6110

  melanoma_tumor_immunology_til:
    ole2001                  0.7055
    kim9036                  0.4803
    nia9069                  0.4803
    juz4004                  0.4345
    rsb4005                  0.4345

  melanoma_molecular_biology_metastasis:
    asl4003                  0.6845
    cad7015                  0.4628
    dob2014                  0.3765
    lud2005                  0.3765
    shm2662                  0.3765

  melanoma_liquid_biopsy_ctdna:
    jdw2002                  0.6036
    ahs9018                  0.6036
    dal3005                  0.6036
    nkaltork                 0.6036
    jmm9018                  0.2470

  melanoma_braf_targeted_therapy:
    pbc2001                  0.8271
    jhzippin                 0.3286
    jdw2002                  0.3286
    sab4028                  0.3286
    tam2037                  0.3286

  melanoma_epidemiology_disparities:
    juz4004                  0.5146
    rsb4005                  0.3099
    jdw2002                  0.2448
    pbc2001                  0.2448

  melanoma_cutaneous_lymphoma:
    cym2003                  0.6624
    cad7015                  0.2592

  melanoma_cutaneous_scc_immunosuppression:
    bpe9002                  0.7244

  melanoma_surgical_reconstruction:
    kim9036                  0.1552
    dik2002                  0.1290
    jas2037                  0.1290
    wkuhel                   0.1290

  melanoma_ai_early_detection:
    few2001                  0.1340

=== backfill_topic.py complete ===
  topic:           melanoma_skin_cancer
  activity_count:  48
  cluster_count:   12
  coverage_pct:    0.875
  passes_executed: 3
  augmented_draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_melanoma_skin_cancer.json


-- stderr --
cancer
18:16:32 INFO Fetched 109 raw SCORE# rows for melanoma_skin_cancer
18:16:32 INFO After score floor (0.3): 109 qualified rows
18:16:32 INFO Unique PMIDs: 48
18:16:32 INFO --resume: skipping 0 PMIDs already assigned; 48 remaining
18:16:32 INFO Classifying 48 PMIDs with concurrency=15, confidence_floor=0.3, dry_run=False
18:16:38 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:16:38 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:16:38 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:16:38 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:16:38 WARNING Connection pool is full, discarding connection: bedrock-runtime.us-east-1.amazonaws.com. Connection pool size: 10
18:16:39 INFO [progress] 48/48 processed: assigned=45, unassigned=3, failed=0, elapsed=7s
18:16:39 INFO [pass 2: assign] complete
18:16:39 INFO [pass 3: aggregate] invoking: /opt/homebrew/opt/python@3.14/bin/python3.14 /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/aggregate_subtopic_scores.py --topic melanoma_skin_cancer
18:16:39 INFO Querying DynamoDB partition: TOPIC#melanoma_skin_cancer
18:16:39 INFO Fetched 109 SCORE# rows for melanoma_skin_cancer
18:16:39 INFO Aggregated: 105 rows included, 4 unassigned (no primary_subtopic_id), 0 skipped (missing fields)
18:16:39 INFO Faculty touched: 40
18:16:48 INFO Writes: cleared=40, written=40, dry_run=False
18:16:48 INFO Wrote total_weights to .planning/phases/04-subtopic-system/total_weights_melanoma_skin_cancer.json
18:16:48 INFO [pass 3: aggregate] complete
18:16:48 INFO Wrote augmented draft: /Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration/.planning/phases/04-subtopic-system/hierarchy_augmented_melanoma_skin_cancer.json

```
| oral_craniofacial_health | 25 | 0 | - | 0 |  |  | excluded:below_cold_start_floor |

<!-- === oral_craniofacial_health === rc=0 -->
```
EXCLUDED:oral_craniofacial_health:25:below_cold_start_floor

-- stderr --
18:16:48 INFO Pre-counting qualifying activities for oral_craniofacial_health (score >= 0.3)...
18:16:49 INFO oral_craniofacial_health: 25 qualifying activities
18:16:49 WARNING Topic oral_craniofacial_health has 25 activities, below cold-start floor 30. Skipping.

```

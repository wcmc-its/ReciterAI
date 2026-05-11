# WEIGHT_FLOOR Calibration Notes (SUB-17)

## Method
Knee-point (largest relative drop in descending weight sequence)

## Aging weight distribution

| subtopic_id                                            | total_weight |
| ------------------------------------------------------ | ------------ |
| aging_elder_mistreatment                                |    33.2217 |
| aging_alzheimers_neurodegeneration                      |    26.6815 |
| aging_brain_imaging_cerebrovascular                     |    17.9975 |
| aging_late_life_depression_mental_health                |    17.9606 |
| aging_social_determinants_health_disparities            |    14.0114 |
| aging_cellular_senescence_molecular                     |    13.8008 |
| aging_stroke_cerebrovascular_outcomes                   |    13.4265 |
| aging_heart_failure_cardiovascular_geriatrics           |    13.2265 |
| aging_nursing_home_ltc                                  |    10.6403 |
| aging_technology_cognitive_training                     |     9.6441 |
| aging_cancer_aging_intersection                         |     9.5047 |
| aging_hiv_older_adults                                  |     9.4570 |
| aging_covid19_outcomes_aging                            |     8.6227 |
| aging_frailty_geriatric_syndromes                       |     8.5294 |
| aging_caregiver_dementia_support                        |     8.3697 |
| aging_geriatric_emergency_trauma                        |     5.0271 |
| aging_hospice_palliative_end_of_life                    |     4.8925 |
| aging_liver_fibrosis_metabolic_cognition                |     4.8419 |
| aging_epigenetic_clocks_biological_aging                |     4.0333 |
| aging_pain_management_older_adults                      |     3.9836 |
| aging_blood_brain_barrier_neurovascular                 |     3.9581 |
| aging_social_isolation_loneliness                       |     3.7712 |
| aging_cardiovascular_procedures_outcomes                |     3.6093 |
| aging_covid19_neurological_rehabilitation               |     3.4182 |
| aging_hypothalamic_neuroendocrine_aging                 |     3.3258 |
| aging_age_related_lung_immunity                         |     3.2130 |
| aging_high_cost_medicare_utilization                    |     1.9632 |
| aging_telehealth_home_care_delivery                     |     1.9304 |
| aging_spinal_orthopedic_surgery_elderly                 |     1.2540 |
| aging_diabetes_metabolic_disease_aging                  |     0.8721 |

### Method: Knee-point

Knee-point detected at total_weight = 5.0271 — candidate WEIGHT_FLOOR for v1 (locked during Plan 08 config update).

Sort order: descending. Largest relative drop identifies the knee separating dense subtopics (above) from sparse subtopics (below).

## Chosen value
<!-- Freeze as WEIGHT_FLOOR constant in ReCiter-Publication-Manager/config/chatbot.ts during Plan 08 -->

## Rationale
<!-- Why this value. Cite D-13 (v1 assumption pending calibration). -->

## Overlap observations (validates v1 assumption: threshold 0.40)

Pairwise Jaccard overlap (|A∩B| / min(|A|, |B|)) from Aging pilot run.
A7 assumption: no pair should exceed 0.40. Review empirical values below
to calibrate or confirm the threshold before full backfill.

| subtopic_a | subtopic_b | overlap |
| ---------- | ---------- | ------- |
| aging_age_related_lung_immunity | aging_alzheimers_neurodegeneration | 0.0000 |
| aging_age_related_lung_immunity | aging_blood_brain_barrier_neurovascular | 0.0000 |
| aging_age_related_lung_immunity | aging_brain_imaging_cerebrovascular | 0.0000 |
| aging_age_related_lung_immunity | aging_cancer_aging_intersection | 0.0000 |
| aging_age_related_lung_immunity | aging_cardiovascular_procedures_outcomes | 0.0000 |
| aging_age_related_lung_immunity | aging_caregiver_dementia_support | 0.0000 |
| aging_age_related_lung_immunity | aging_cellular_senescence_molecular | 0.0000 |
| aging_age_related_lung_immunity | aging_covid19_neurological_rehabilitation | 0.0000 |
| aging_age_related_lung_immunity | aging_covid19_outcomes_aging | 0.0000 |
| aging_age_related_lung_immunity | aging_diabetes_metabolic_disease_aging | 0.0000 |
| aging_age_related_lung_immunity | aging_elder_mistreatment | 0.0000 |
| aging_age_related_lung_immunity | aging_epigenetic_clocks_biological_aging | 0.0000 |
| aging_age_related_lung_immunity | aging_frailty_geriatric_syndromes | 0.0000 |
| aging_age_related_lung_immunity | aging_geriatric_emergency_trauma | 0.0000 |
| aging_age_related_lung_immunity | aging_heart_failure_cardiovascular_geriatrics | 0.0000 |
| aging_age_related_lung_immunity | aging_high_cost_medicare_utilization | 0.0000 |
| aging_age_related_lung_immunity | aging_hiv_older_adults | 0.0000 |
| aging_age_related_lung_immunity | aging_hospice_palliative_end_of_life | 0.0000 |
| aging_age_related_lung_immunity | aging_hypothalamic_neuroendocrine_aging | 0.0000 |
| aging_age_related_lung_immunity | aging_late_life_depression_mental_health | 0.0000 |
| aging_age_related_lung_immunity | aging_liver_fibrosis_metabolic_cognition | 0.0000 |
| aging_age_related_lung_immunity | aging_nursing_home_ltc | 0.0000 |
| aging_age_related_lung_immunity | aging_pain_management_older_adults | 0.0000 |
| aging_age_related_lung_immunity | aging_social_determinants_health_disparities | 0.0000 |
| aging_age_related_lung_immunity | aging_social_isolation_loneliness | 0.0000 |
| aging_age_related_lung_immunity | aging_spinal_orthopedic_surgery_elderly | 0.0000 |
| aging_age_related_lung_immunity | aging_stroke_cerebrovascular_outcomes | 0.0000 |
| aging_age_related_lung_immunity | aging_technology_cognitive_training | 0.0000 |
| aging_age_related_lung_immunity | aging_telehealth_home_care_delivery | 0.0000 |
| aging_alzheimers_neurodegeneration | aging_blood_brain_barrier_neurovascular | 0.0000 |
| aging_alzheimers_neurodegeneration | aging_brain_imaging_cerebrovascular | 0.0000 |
| aging_alzheimers_neurodegeneration | aging_cancer_aging_intersection | 0.0000 |
| aging_alzheimers_neurodegeneration | aging_cardiovascular_procedures_outcomes | 0.0000 |
| aging_alzheimers_neurodegeneration | aging_caregiver_dementia_support | 0.0000 |
| aging_alzheimers_neurodegeneration | aging_cellular_senescence_molecular | 0.0000 |
| aging_alzheimers_neurodegeneration | aging_covid19_neurological_rehabilitation | 0.0000 |
| aging_alzheimers_neurodegeneration | aging_covid19_outcomes_aging | 0.0000 |
| aging_alzheimers_neurodegeneration | aging_diabetes_metabolic_disease_aging | 0.0000 |
| aging_alzheimers_neurodegeneration | aging_elder_mistreatment | 0.0000 |
| aging_alzheimers_neurodegeneration | aging_epigenetic_clocks_biological_aging | 0.0000 |
| aging_alzheimers_neurodegeneration | aging_frailty_geriatric_syndromes | 0.0000 |
| aging_alzheimers_neurodegeneration | aging_geriatric_emergency_trauma | 0.0000 |
| aging_alzheimers_neurodegeneration | aging_heart_failure_cardiovascular_geriatrics | 0.0000 |
| aging_alzheimers_neurodegeneration | aging_high_cost_medicare_utilization | 0.0000 |
| aging_alzheimers_neurodegeneration | aging_hiv_older_adults | 0.0000 |
| aging_alzheimers_neurodegeneration | aging_hospice_palliative_end_of_life | 0.0000 |
| aging_alzheimers_neurodegeneration | aging_hypothalamic_neuroendocrine_aging | 0.0000 |
| aging_alzheimers_neurodegeneration | aging_late_life_depression_mental_health | 0.0000 |
| aging_alzheimers_neurodegeneration | aging_liver_fibrosis_metabolic_cognition | 0.0000 |
| aging_alzheimers_neurodegeneration | aging_nursing_home_ltc | 0.0000 |
| aging_alzheimers_neurodegeneration | aging_pain_management_older_adults | 0.0000 |
| aging_alzheimers_neurodegeneration | aging_social_determinants_health_disparities | 0.0000 |
| aging_alzheimers_neurodegeneration | aging_social_isolation_loneliness | 0.0000 |
| aging_alzheimers_neurodegeneration | aging_spinal_orthopedic_surgery_elderly | 0.0000 |
| aging_alzheimers_neurodegeneration | aging_stroke_cerebrovascular_outcomes | 0.0000 |
| aging_alzheimers_neurodegeneration | aging_technology_cognitive_training | 0.0000 |
| aging_alzheimers_neurodegeneration | aging_telehealth_home_care_delivery | 0.0000 |
| aging_blood_brain_barrier_neurovascular | aging_brain_imaging_cerebrovascular | 0.0000 |
| aging_blood_brain_barrier_neurovascular | aging_cancer_aging_intersection | 0.0000 |
| aging_blood_brain_barrier_neurovascular | aging_cardiovascular_procedures_outcomes | 0.0000 |
| aging_blood_brain_barrier_neurovascular | aging_caregiver_dementia_support | 0.0000 |
| aging_blood_brain_barrier_neurovascular | aging_cellular_senescence_molecular | 0.0000 |
| aging_blood_brain_barrier_neurovascular | aging_covid19_neurological_rehabilitation | 0.0000 |
| aging_blood_brain_barrier_neurovascular | aging_covid19_outcomes_aging | 0.0000 |
| aging_blood_brain_barrier_neurovascular | aging_diabetes_metabolic_disease_aging | 0.0000 |
| aging_blood_brain_barrier_neurovascular | aging_elder_mistreatment | 0.0000 |
| aging_blood_brain_barrier_neurovascular | aging_epigenetic_clocks_biological_aging | 0.0000 |
| aging_blood_brain_barrier_neurovascular | aging_frailty_geriatric_syndromes | 0.0000 |
| aging_blood_brain_barrier_neurovascular | aging_geriatric_emergency_trauma | 0.0000 |
| aging_blood_brain_barrier_neurovascular | aging_heart_failure_cardiovascular_geriatrics | 0.0000 |
| aging_blood_brain_barrier_neurovascular | aging_high_cost_medicare_utilization | 0.0000 |
| aging_blood_brain_barrier_neurovascular | aging_hiv_older_adults | 0.0000 |
| aging_blood_brain_barrier_neurovascular | aging_hospice_palliative_end_of_life | 0.0000 |
| aging_blood_brain_barrier_neurovascular | aging_hypothalamic_neuroendocrine_aging | 0.0000 |
| aging_blood_brain_barrier_neurovascular | aging_late_life_depression_mental_health | 0.0000 |
| aging_blood_brain_barrier_neurovascular | aging_liver_fibrosis_metabolic_cognition | 0.0000 |
| aging_blood_brain_barrier_neurovascular | aging_nursing_home_ltc | 0.0000 |
| aging_blood_brain_barrier_neurovascular | aging_pain_management_older_adults | 0.0000 |
| aging_blood_brain_barrier_neurovascular | aging_social_determinants_health_disparities | 0.0000 |
| aging_blood_brain_barrier_neurovascular | aging_social_isolation_loneliness | 0.0000 |
| aging_blood_brain_barrier_neurovascular | aging_spinal_orthopedic_surgery_elderly | 0.0000 |
| aging_blood_brain_barrier_neurovascular | aging_stroke_cerebrovascular_outcomes | 0.0000 |
| aging_blood_brain_barrier_neurovascular | aging_technology_cognitive_training | 0.0000 |
| aging_blood_brain_barrier_neurovascular | aging_telehealth_home_care_delivery | 0.0000 |
| aging_brain_imaging_cerebrovascular | aging_cancer_aging_intersection | 0.0000 |
| aging_brain_imaging_cerebrovascular | aging_cardiovascular_procedures_outcomes | 0.0000 |
| aging_brain_imaging_cerebrovascular | aging_caregiver_dementia_support | 0.0000 |
| aging_brain_imaging_cerebrovascular | aging_cellular_senescence_molecular | 0.0000 |
| aging_brain_imaging_cerebrovascular | aging_covid19_neurological_rehabilitation | 0.0000 |
| aging_brain_imaging_cerebrovascular | aging_covid19_outcomes_aging | 0.0000 |
| aging_brain_imaging_cerebrovascular | aging_diabetes_metabolic_disease_aging | 0.0000 |
| aging_brain_imaging_cerebrovascular | aging_elder_mistreatment | 0.0000 |
| aging_brain_imaging_cerebrovascular | aging_epigenetic_clocks_biological_aging | 0.0000 |
| aging_brain_imaging_cerebrovascular | aging_frailty_geriatric_syndromes | 0.0000 |
| aging_brain_imaging_cerebrovascular | aging_geriatric_emergency_trauma | 0.0000 |
| aging_brain_imaging_cerebrovascular | aging_heart_failure_cardiovascular_geriatrics | 0.0000 |
| aging_brain_imaging_cerebrovascular | aging_high_cost_medicare_utilization | 0.0000 |
| aging_brain_imaging_cerebrovascular | aging_hiv_older_adults | 0.0000 |
| aging_brain_imaging_cerebrovascular | aging_hospice_palliative_end_of_life | 0.0000 |
| aging_brain_imaging_cerebrovascular | aging_hypothalamic_neuroendocrine_aging | 0.0000 |
| aging_brain_imaging_cerebrovascular | aging_late_life_depression_mental_health | 0.0000 |
| aging_brain_imaging_cerebrovascular | aging_liver_fibrosis_metabolic_cognition | 0.0000 |
| aging_brain_imaging_cerebrovascular | aging_nursing_home_ltc | 0.0000 |
| aging_brain_imaging_cerebrovascular | aging_pain_management_older_adults | 0.0000 |
| aging_brain_imaging_cerebrovascular | aging_social_determinants_health_disparities | 0.0000 |
| aging_brain_imaging_cerebrovascular | aging_social_isolation_loneliness | 0.0000 |
| aging_brain_imaging_cerebrovascular | aging_spinal_orthopedic_surgery_elderly | 0.0000 |
| aging_brain_imaging_cerebrovascular | aging_stroke_cerebrovascular_outcomes | 0.0000 |
| aging_brain_imaging_cerebrovascular | aging_technology_cognitive_training | 0.0000 |
| aging_brain_imaging_cerebrovascular | aging_telehealth_home_care_delivery | 0.0000 |
| aging_cancer_aging_intersection | aging_cardiovascular_procedures_outcomes | 0.0000 |
| aging_cancer_aging_intersection | aging_caregiver_dementia_support | 0.0000 |
| aging_cancer_aging_intersection | aging_cellular_senescence_molecular | 0.0000 |
| aging_cancer_aging_intersection | aging_covid19_neurological_rehabilitation | 0.0000 |
| aging_cancer_aging_intersection | aging_covid19_outcomes_aging | 0.0000 |
| aging_cancer_aging_intersection | aging_diabetes_metabolic_disease_aging | 0.0000 |
| aging_cancer_aging_intersection | aging_elder_mistreatment | 0.0000 |
| aging_cancer_aging_intersection | aging_epigenetic_clocks_biological_aging | 0.0000 |
| aging_cancer_aging_intersection | aging_frailty_geriatric_syndromes | 0.0000 |
| aging_cancer_aging_intersection | aging_geriatric_emergency_trauma | 0.0000 |
| aging_cancer_aging_intersection | aging_heart_failure_cardiovascular_geriatrics | 0.0000 |
| aging_cancer_aging_intersection | aging_high_cost_medicare_utilization | 0.0000 |
| aging_cancer_aging_intersection | aging_hiv_older_adults | 0.0000 |
| aging_cancer_aging_intersection | aging_hospice_palliative_end_of_life | 0.0000 |
| aging_cancer_aging_intersection | aging_hypothalamic_neuroendocrine_aging | 0.0000 |
| aging_cancer_aging_intersection | aging_late_life_depression_mental_health | 0.0000 |
| aging_cancer_aging_intersection | aging_liver_fibrosis_metabolic_cognition | 0.0000 |
| aging_cancer_aging_intersection | aging_nursing_home_ltc | 0.0000 |
| aging_cancer_aging_intersection | aging_pain_management_older_adults | 0.0000 |
| aging_cancer_aging_intersection | aging_social_determinants_health_disparities | 0.0000 |
| aging_cancer_aging_intersection | aging_social_isolation_loneliness | 0.0000 |
| aging_cancer_aging_intersection | aging_spinal_orthopedic_surgery_elderly | 0.0000 |
| aging_cancer_aging_intersection | aging_stroke_cerebrovascular_outcomes | 0.0000 |
| aging_cancer_aging_intersection | aging_technology_cognitive_training | 0.0000 |
| aging_cancer_aging_intersection | aging_telehealth_home_care_delivery | 0.0000 |
| aging_cardiovascular_procedures_outcomes | aging_caregiver_dementia_support | 0.0000 |
| aging_cardiovascular_procedures_outcomes | aging_cellular_senescence_molecular | 0.0000 |
| aging_cardiovascular_procedures_outcomes | aging_covid19_neurological_rehabilitation | 0.0000 |
| aging_cardiovascular_procedures_outcomes | aging_covid19_outcomes_aging | 0.0000 |
| aging_cardiovascular_procedures_outcomes | aging_diabetes_metabolic_disease_aging | 0.0000 |
| aging_cardiovascular_procedures_outcomes | aging_elder_mistreatment | 0.0000 |
| aging_cardiovascular_procedures_outcomes | aging_epigenetic_clocks_biological_aging | 0.0000 |
| aging_cardiovascular_procedures_outcomes | aging_frailty_geriatric_syndromes | 0.0000 |
| aging_cardiovascular_procedures_outcomes | aging_geriatric_emergency_trauma | 0.0000 |
| aging_cardiovascular_procedures_outcomes | aging_heart_failure_cardiovascular_geriatrics | 0.0000 |
| aging_cardiovascular_procedures_outcomes | aging_high_cost_medicare_utilization | 0.0000 |
| aging_cardiovascular_procedures_outcomes | aging_hiv_older_adults | 0.0000 |
| aging_cardiovascular_procedures_outcomes | aging_hospice_palliative_end_of_life | 0.0000 |
| aging_cardiovascular_procedures_outcomes | aging_hypothalamic_neuroendocrine_aging | 0.0000 |
| aging_cardiovascular_procedures_outcomes | aging_late_life_depression_mental_health | 0.0000 |
| aging_cardiovascular_procedures_outcomes | aging_liver_fibrosis_metabolic_cognition | 0.0000 |
| aging_cardiovascular_procedures_outcomes | aging_nursing_home_ltc | 0.0000 |
| aging_cardiovascular_procedures_outcomes | aging_pain_management_older_adults | 0.0000 |
| aging_cardiovascular_procedures_outcomes | aging_social_determinants_health_disparities | 0.0000 |
| aging_cardiovascular_procedures_outcomes | aging_social_isolation_loneliness | 0.0000 |
| aging_cardiovascular_procedures_outcomes | aging_spinal_orthopedic_surgery_elderly | 0.0000 |
| aging_cardiovascular_procedures_outcomes | aging_stroke_cerebrovascular_outcomes | 0.0000 |
| aging_cardiovascular_procedures_outcomes | aging_technology_cognitive_training | 0.0000 |
| aging_cardiovascular_procedures_outcomes | aging_telehealth_home_care_delivery | 0.0000 |
| aging_caregiver_dementia_support | aging_cellular_senescence_molecular | 0.0000 |
| aging_caregiver_dementia_support | aging_covid19_neurological_rehabilitation | 0.0000 |
| aging_caregiver_dementia_support | aging_covid19_outcomes_aging | 0.0000 |
| aging_caregiver_dementia_support | aging_diabetes_metabolic_disease_aging | 0.0000 |
| aging_caregiver_dementia_support | aging_elder_mistreatment | 0.0000 |
| aging_caregiver_dementia_support | aging_epigenetic_clocks_biological_aging | 0.0000 |
| aging_caregiver_dementia_support | aging_frailty_geriatric_syndromes | 0.0000 |
| aging_caregiver_dementia_support | aging_geriatric_emergency_trauma | 0.0000 |
| aging_caregiver_dementia_support | aging_heart_failure_cardiovascular_geriatrics | 0.0000 |
| aging_caregiver_dementia_support | aging_high_cost_medicare_utilization | 0.0000 |
| aging_caregiver_dementia_support | aging_hiv_older_adults | 0.0000 |
| aging_caregiver_dementia_support | aging_hospice_palliative_end_of_life | 0.0000 |
| aging_caregiver_dementia_support | aging_hypothalamic_neuroendocrine_aging | 0.0000 |
| aging_caregiver_dementia_support | aging_late_life_depression_mental_health | 0.0000 |
| aging_caregiver_dementia_support | aging_liver_fibrosis_metabolic_cognition | 0.0000 |
| aging_caregiver_dementia_support | aging_nursing_home_ltc | 0.0000 |
| aging_caregiver_dementia_support | aging_pain_management_older_adults | 0.0000 |
| aging_caregiver_dementia_support | aging_social_determinants_health_disparities | 0.0000 |
| aging_caregiver_dementia_support | aging_social_isolation_loneliness | 0.0000 |
| aging_caregiver_dementia_support | aging_spinal_orthopedic_surgery_elderly | 0.0000 |
| aging_caregiver_dementia_support | aging_stroke_cerebrovascular_outcomes | 0.0000 |
| aging_caregiver_dementia_support | aging_technology_cognitive_training | 0.0000 |
| aging_caregiver_dementia_support | aging_telehealth_home_care_delivery | 0.0000 |
| aging_cellular_senescence_molecular | aging_covid19_neurological_rehabilitation | 0.0000 |
| aging_cellular_senescence_molecular | aging_covid19_outcomes_aging | 0.0000 |
| aging_cellular_senescence_molecular | aging_diabetes_metabolic_disease_aging | 0.0000 |
| aging_cellular_senescence_molecular | aging_elder_mistreatment | 0.0000 |
| aging_cellular_senescence_molecular | aging_epigenetic_clocks_biological_aging | 0.0000 |
| aging_cellular_senescence_molecular | aging_frailty_geriatric_syndromes | 0.0000 |
| aging_cellular_senescence_molecular | aging_geriatric_emergency_trauma | 0.0000 |
| aging_cellular_senescence_molecular | aging_heart_failure_cardiovascular_geriatrics | 0.0000 |
| aging_cellular_senescence_molecular | aging_high_cost_medicare_utilization | 0.0000 |
| aging_cellular_senescence_molecular | aging_hiv_older_adults | 0.0000 |
| aging_cellular_senescence_molecular | aging_hospice_palliative_end_of_life | 0.0000 |
| aging_cellular_senescence_molecular | aging_hypothalamic_neuroendocrine_aging | 0.0000 |
| aging_cellular_senescence_molecular | aging_late_life_depression_mental_health | 0.0000 |
| aging_cellular_senescence_molecular | aging_liver_fibrosis_metabolic_cognition | 0.0000 |
| aging_cellular_senescence_molecular | aging_nursing_home_ltc | 0.0000 |
| aging_cellular_senescence_molecular | aging_pain_management_older_adults | 0.0000 |
| aging_cellular_senescence_molecular | aging_social_determinants_health_disparities | 0.0000 |
| aging_cellular_senescence_molecular | aging_social_isolation_loneliness | 0.0000 |
| aging_cellular_senescence_molecular | aging_spinal_orthopedic_surgery_elderly | 0.0000 |
| aging_cellular_senescence_molecular | aging_stroke_cerebrovascular_outcomes | 0.0000 |
| aging_cellular_senescence_molecular | aging_technology_cognitive_training | 0.0000 |
| aging_cellular_senescence_molecular | aging_telehealth_home_care_delivery | 0.0000 |
| aging_covid19_neurological_rehabilitation | aging_covid19_outcomes_aging | 0.0000 |
| aging_covid19_neurological_rehabilitation | aging_diabetes_metabolic_disease_aging | 0.0000 |
| aging_covid19_neurological_rehabilitation | aging_elder_mistreatment | 0.0000 |
| aging_covid19_neurological_rehabilitation | aging_epigenetic_clocks_biological_aging | 0.0000 |
| aging_covid19_neurological_rehabilitation | aging_frailty_geriatric_syndromes | 0.0000 |
| aging_covid19_neurological_rehabilitation | aging_geriatric_emergency_trauma | 0.0000 |
| aging_covid19_neurological_rehabilitation | aging_heart_failure_cardiovascular_geriatrics | 0.0000 |
| aging_covid19_neurological_rehabilitation | aging_high_cost_medicare_utilization | 0.0000 |
| aging_covid19_neurological_rehabilitation | aging_hiv_older_adults | 0.0000 |
| aging_covid19_neurological_rehabilitation | aging_hospice_palliative_end_of_life | 0.0000 |
| aging_covid19_neurological_rehabilitation | aging_hypothalamic_neuroendocrine_aging | 0.0000 |
| aging_covid19_neurological_rehabilitation | aging_late_life_depression_mental_health | 0.0000 |
| aging_covid19_neurological_rehabilitation | aging_liver_fibrosis_metabolic_cognition | 0.0000 |
| aging_covid19_neurological_rehabilitation | aging_nursing_home_ltc | 0.0000 |
| aging_covid19_neurological_rehabilitation | aging_pain_management_older_adults | 0.0000 |
| aging_covid19_neurological_rehabilitation | aging_social_determinants_health_disparities | 0.0000 |
| aging_covid19_neurological_rehabilitation | aging_social_isolation_loneliness | 0.0000 |
| aging_covid19_neurological_rehabilitation | aging_spinal_orthopedic_surgery_elderly | 0.0000 |
| aging_covid19_neurological_rehabilitation | aging_stroke_cerebrovascular_outcomes | 0.0000 |
| aging_covid19_neurological_rehabilitation | aging_technology_cognitive_training | 0.0000 |
| aging_covid19_neurological_rehabilitation | aging_telehealth_home_care_delivery | 0.0000 |
| aging_covid19_outcomes_aging | aging_diabetes_metabolic_disease_aging | 0.0000 |
| aging_covid19_outcomes_aging | aging_elder_mistreatment | 0.0000 |
| aging_covid19_outcomes_aging | aging_epigenetic_clocks_biological_aging | 0.0000 |
| aging_covid19_outcomes_aging | aging_frailty_geriatric_syndromes | 0.0000 |
| aging_covid19_outcomes_aging | aging_geriatric_emergency_trauma | 0.0000 |
| aging_covid19_outcomes_aging | aging_heart_failure_cardiovascular_geriatrics | 0.0000 |
| aging_covid19_outcomes_aging | aging_high_cost_medicare_utilization | 0.0000 |
| aging_covid19_outcomes_aging | aging_hiv_older_adults | 0.0000 |
| aging_covid19_outcomes_aging | aging_hospice_palliative_end_of_life | 0.0000 |
| aging_covid19_outcomes_aging | aging_hypothalamic_neuroendocrine_aging | 0.0000 |
| aging_covid19_outcomes_aging | aging_late_life_depression_mental_health | 0.0000 |
| aging_covid19_outcomes_aging | aging_liver_fibrosis_metabolic_cognition | 0.0000 |
| aging_covid19_outcomes_aging | aging_nursing_home_ltc | 0.0000 |
| aging_covid19_outcomes_aging | aging_pain_management_older_adults | 0.0000 |
| aging_covid19_outcomes_aging | aging_social_determinants_health_disparities | 0.0000 |
| aging_covid19_outcomes_aging | aging_social_isolation_loneliness | 0.0000 |
| aging_covid19_outcomes_aging | aging_spinal_orthopedic_surgery_elderly | 0.0000 |
| aging_covid19_outcomes_aging | aging_stroke_cerebrovascular_outcomes | 0.0000 |
| aging_covid19_outcomes_aging | aging_technology_cognitive_training | 0.0000 |
| aging_covid19_outcomes_aging | aging_telehealth_home_care_delivery | 0.0000 |
| aging_diabetes_metabolic_disease_aging | aging_elder_mistreatment | 0.0000 |
| aging_diabetes_metabolic_disease_aging | aging_epigenetic_clocks_biological_aging | 0.0000 |
| aging_diabetes_metabolic_disease_aging | aging_frailty_geriatric_syndromes | 0.0000 |
| aging_diabetes_metabolic_disease_aging | aging_geriatric_emergency_trauma | 0.0000 |
| aging_diabetes_metabolic_disease_aging | aging_heart_failure_cardiovascular_geriatrics | 0.0000 |
| aging_diabetes_metabolic_disease_aging | aging_high_cost_medicare_utilization | 0.0000 |
| aging_diabetes_metabolic_disease_aging | aging_hiv_older_adults | 0.0000 |
| aging_diabetes_metabolic_disease_aging | aging_hospice_palliative_end_of_life | 0.0000 |
| aging_diabetes_metabolic_disease_aging | aging_hypothalamic_neuroendocrine_aging | 0.0000 |
| aging_diabetes_metabolic_disease_aging | aging_late_life_depression_mental_health | 0.0000 |
| aging_diabetes_metabolic_disease_aging | aging_liver_fibrosis_metabolic_cognition | 0.0000 |
| aging_diabetes_metabolic_disease_aging | aging_nursing_home_ltc | 0.0000 |
| aging_diabetes_metabolic_disease_aging | aging_pain_management_older_adults | 0.0000 |
| aging_diabetes_metabolic_disease_aging | aging_social_determinants_health_disparities | 0.0000 |
| aging_diabetes_metabolic_disease_aging | aging_social_isolation_loneliness | 0.0000 |
| aging_diabetes_metabolic_disease_aging | aging_spinal_orthopedic_surgery_elderly | 0.0000 |
| aging_diabetes_metabolic_disease_aging | aging_stroke_cerebrovascular_outcomes | 0.0000 |
| aging_diabetes_metabolic_disease_aging | aging_technology_cognitive_training | 0.0000 |
| aging_diabetes_metabolic_disease_aging | aging_telehealth_home_care_delivery | 0.0000 |
| aging_elder_mistreatment | aging_epigenetic_clocks_biological_aging | 0.0000 |
| aging_elder_mistreatment | aging_frailty_geriatric_syndromes | 0.0000 |
| aging_elder_mistreatment | aging_geriatric_emergency_trauma | 0.0000 |
| aging_elder_mistreatment | aging_heart_failure_cardiovascular_geriatrics | 0.0000 |
| aging_elder_mistreatment | aging_high_cost_medicare_utilization | 0.0000 |
| aging_elder_mistreatment | aging_hiv_older_adults | 0.0000 |
| aging_elder_mistreatment | aging_hospice_palliative_end_of_life | 0.0000 |
| aging_elder_mistreatment | aging_hypothalamic_neuroendocrine_aging | 0.0000 |
| aging_elder_mistreatment | aging_late_life_depression_mental_health | 0.0000 |
| aging_elder_mistreatment | aging_liver_fibrosis_metabolic_cognition | 0.0000 |
| aging_elder_mistreatment | aging_nursing_home_ltc | 0.0000 |
| aging_elder_mistreatment | aging_pain_management_older_adults | 0.0000 |
| aging_elder_mistreatment | aging_social_determinants_health_disparities | 0.0000 |
| aging_elder_mistreatment | aging_social_isolation_loneliness | 0.0000 |
| aging_elder_mistreatment | aging_spinal_orthopedic_surgery_elderly | 0.0000 |
| aging_elder_mistreatment | aging_stroke_cerebrovascular_outcomes | 0.0000 |
| aging_elder_mistreatment | aging_technology_cognitive_training | 0.0000 |
| aging_elder_mistreatment | aging_telehealth_home_care_delivery | 0.0000 |
| aging_epigenetic_clocks_biological_aging | aging_frailty_geriatric_syndromes | 0.0000 |
| aging_epigenetic_clocks_biological_aging | aging_geriatric_emergency_trauma | 0.0000 |
| aging_epigenetic_clocks_biological_aging | aging_heart_failure_cardiovascular_geriatrics | 0.0000 |
| aging_epigenetic_clocks_biological_aging | aging_high_cost_medicare_utilization | 0.0000 |
| aging_epigenetic_clocks_biological_aging | aging_hiv_older_adults | 0.0000 |
| aging_epigenetic_clocks_biological_aging | aging_hospice_palliative_end_of_life | 0.0000 |
| aging_epigenetic_clocks_biological_aging | aging_hypothalamic_neuroendocrine_aging | 0.0000 |
| aging_epigenetic_clocks_biological_aging | aging_late_life_depression_mental_health | 0.0000 |
| aging_epigenetic_clocks_biological_aging | aging_liver_fibrosis_metabolic_cognition | 0.0000 |
| aging_epigenetic_clocks_biological_aging | aging_nursing_home_ltc | 0.0000 |
| aging_epigenetic_clocks_biological_aging | aging_pain_management_older_adults | 0.0000 |
| aging_epigenetic_clocks_biological_aging | aging_social_determinants_health_disparities | 0.0000 |
| aging_epigenetic_clocks_biological_aging | aging_social_isolation_loneliness | 0.0000 |
| aging_epigenetic_clocks_biological_aging | aging_spinal_orthopedic_surgery_elderly | 0.0000 |
| aging_epigenetic_clocks_biological_aging | aging_stroke_cerebrovascular_outcomes | 0.0000 |
| aging_epigenetic_clocks_biological_aging | aging_technology_cognitive_training | 0.0000 |
| aging_epigenetic_clocks_biological_aging | aging_telehealth_home_care_delivery | 0.0000 |
| aging_frailty_geriatric_syndromes | aging_geriatric_emergency_trauma | 0.0000 |
| aging_frailty_geriatric_syndromes | aging_heart_failure_cardiovascular_geriatrics | 0.0000 |
| aging_frailty_geriatric_syndromes | aging_high_cost_medicare_utilization | 0.0000 |
| aging_frailty_geriatric_syndromes | aging_hiv_older_adults | 0.0000 |
| aging_frailty_geriatric_syndromes | aging_hospice_palliative_end_of_life | 0.0000 |
| aging_frailty_geriatric_syndromes | aging_hypothalamic_neuroendocrine_aging | 0.0000 |
| aging_frailty_geriatric_syndromes | aging_late_life_depression_mental_health | 0.0000 |
| aging_frailty_geriatric_syndromes | aging_liver_fibrosis_metabolic_cognition | 0.0000 |
| aging_frailty_geriatric_syndromes | aging_nursing_home_ltc | 0.0000 |
| aging_frailty_geriatric_syndromes | aging_pain_management_older_adults | 0.0000 |
| aging_frailty_geriatric_syndromes | aging_social_determinants_health_disparities | 0.0000 |
| aging_frailty_geriatric_syndromes | aging_social_isolation_loneliness | 0.0000 |
| aging_frailty_geriatric_syndromes | aging_spinal_orthopedic_surgery_elderly | 0.0000 |
| aging_frailty_geriatric_syndromes | aging_stroke_cerebrovascular_outcomes | 0.0000 |
| aging_frailty_geriatric_syndromes | aging_technology_cognitive_training | 0.0000 |
| aging_frailty_geriatric_syndromes | aging_telehealth_home_care_delivery | 0.0000 |
| aging_geriatric_emergency_trauma | aging_heart_failure_cardiovascular_geriatrics | 0.0000 |
| aging_geriatric_emergency_trauma | aging_high_cost_medicare_utilization | 0.0000 |
| aging_geriatric_emergency_trauma | aging_hiv_older_adults | 0.0000 |
| aging_geriatric_emergency_trauma | aging_hospice_palliative_end_of_life | 0.0000 |
| aging_geriatric_emergency_trauma | aging_hypothalamic_neuroendocrine_aging | 0.0000 |
| aging_geriatric_emergency_trauma | aging_late_life_depression_mental_health | 0.0000 |
| aging_geriatric_emergency_trauma | aging_liver_fibrosis_metabolic_cognition | 0.0000 |
| aging_geriatric_emergency_trauma | aging_nursing_home_ltc | 0.0000 |
| aging_geriatric_emergency_trauma | aging_pain_management_older_adults | 0.0000 |
| aging_geriatric_emergency_trauma | aging_social_determinants_health_disparities | 0.0000 |
| aging_geriatric_emergency_trauma | aging_social_isolation_loneliness | 0.0000 |
| aging_geriatric_emergency_trauma | aging_spinal_orthopedic_surgery_elderly | 0.0000 |
| aging_geriatric_emergency_trauma | aging_stroke_cerebrovascular_outcomes | 0.0000 |
| aging_geriatric_emergency_trauma | aging_technology_cognitive_training | 0.0000 |
| aging_geriatric_emergency_trauma | aging_telehealth_home_care_delivery | 0.0000 |
| aging_heart_failure_cardiovascular_geriatrics | aging_high_cost_medicare_utilization | 0.0000 |
| aging_heart_failure_cardiovascular_geriatrics | aging_hiv_older_adults | 0.0000 |
| aging_heart_failure_cardiovascular_geriatrics | aging_hospice_palliative_end_of_life | 0.0000 |
| aging_heart_failure_cardiovascular_geriatrics | aging_hypothalamic_neuroendocrine_aging | 0.0000 |
| aging_heart_failure_cardiovascular_geriatrics | aging_late_life_depression_mental_health | 0.0000 |
| aging_heart_failure_cardiovascular_geriatrics | aging_liver_fibrosis_metabolic_cognition | 0.0000 |
| aging_heart_failure_cardiovascular_geriatrics | aging_nursing_home_ltc | 0.0000 |
| aging_heart_failure_cardiovascular_geriatrics | aging_pain_management_older_adults | 0.0000 |
| aging_heart_failure_cardiovascular_geriatrics | aging_social_determinants_health_disparities | 0.0000 |
| aging_heart_failure_cardiovascular_geriatrics | aging_social_isolation_loneliness | 0.0000 |
| aging_heart_failure_cardiovascular_geriatrics | aging_spinal_orthopedic_surgery_elderly | 0.0000 |
| aging_heart_failure_cardiovascular_geriatrics | aging_stroke_cerebrovascular_outcomes | 0.0000 |
| aging_heart_failure_cardiovascular_geriatrics | aging_technology_cognitive_training | 0.0000 |
| aging_heart_failure_cardiovascular_geriatrics | aging_telehealth_home_care_delivery | 0.0000 |
| aging_high_cost_medicare_utilization | aging_hiv_older_adults | 0.0000 |
| aging_high_cost_medicare_utilization | aging_hospice_palliative_end_of_life | 0.0000 |
| aging_high_cost_medicare_utilization | aging_hypothalamic_neuroendocrine_aging | 0.0000 |
| aging_high_cost_medicare_utilization | aging_late_life_depression_mental_health | 0.0000 |
| aging_high_cost_medicare_utilization | aging_liver_fibrosis_metabolic_cognition | 0.0000 |
| aging_high_cost_medicare_utilization | aging_nursing_home_ltc | 0.0000 |
| aging_high_cost_medicare_utilization | aging_pain_management_older_adults | 0.0000 |
| aging_high_cost_medicare_utilization | aging_social_determinants_health_disparities | 0.0000 |
| aging_high_cost_medicare_utilization | aging_social_isolation_loneliness | 0.0000 |
| aging_high_cost_medicare_utilization | aging_spinal_orthopedic_surgery_elderly | 0.0000 |
| aging_high_cost_medicare_utilization | aging_stroke_cerebrovascular_outcomes | 0.0000 |
| aging_high_cost_medicare_utilization | aging_technology_cognitive_training | 0.0000 |
| aging_high_cost_medicare_utilization | aging_telehealth_home_care_delivery | 0.0000 |
| aging_hiv_older_adults | aging_hospice_palliative_end_of_life | 0.0000 |
| aging_hiv_older_adults | aging_hypothalamic_neuroendocrine_aging | 0.0000 |
| aging_hiv_older_adults | aging_late_life_depression_mental_health | 0.0000 |
| aging_hiv_older_adults | aging_liver_fibrosis_metabolic_cognition | 0.0000 |
| aging_hiv_older_adults | aging_nursing_home_ltc | 0.0000 |
| aging_hiv_older_adults | aging_pain_management_older_adults | 0.0000 |
| aging_hiv_older_adults | aging_social_determinants_health_disparities | 0.0000 |
| aging_hiv_older_adults | aging_social_isolation_loneliness | 0.0000 |
| aging_hiv_older_adults | aging_spinal_orthopedic_surgery_elderly | 0.0000 |
| aging_hiv_older_adults | aging_stroke_cerebrovascular_outcomes | 0.0000 |
| aging_hiv_older_adults | aging_technology_cognitive_training | 0.0000 |
| aging_hiv_older_adults | aging_telehealth_home_care_delivery | 0.0000 |
| aging_hospice_palliative_end_of_life | aging_hypothalamic_neuroendocrine_aging | 0.0000 |
| aging_hospice_palliative_end_of_life | aging_late_life_depression_mental_health | 0.0000 |
| aging_hospice_palliative_end_of_life | aging_liver_fibrosis_metabolic_cognition | 0.0000 |
| aging_hospice_palliative_end_of_life | aging_nursing_home_ltc | 0.0000 |
| aging_hospice_palliative_end_of_life | aging_pain_management_older_adults | 0.0000 |
| aging_hospice_palliative_end_of_life | aging_social_determinants_health_disparities | 0.0000 |
| aging_hospice_palliative_end_of_life | aging_social_isolation_loneliness | 0.0000 |
| aging_hospice_palliative_end_of_life | aging_spinal_orthopedic_surgery_elderly | 0.0000 |
| aging_hospice_palliative_end_of_life | aging_stroke_cerebrovascular_outcomes | 0.0000 |
| aging_hospice_palliative_end_of_life | aging_technology_cognitive_training | 0.0000 |
| aging_hospice_palliative_end_of_life | aging_telehealth_home_care_delivery | 0.0000 |
| aging_hypothalamic_neuroendocrine_aging | aging_late_life_depression_mental_health | 0.0000 |
| aging_hypothalamic_neuroendocrine_aging | aging_liver_fibrosis_metabolic_cognition | 0.0000 |
| aging_hypothalamic_neuroendocrine_aging | aging_nursing_home_ltc | 0.0000 |
| aging_hypothalamic_neuroendocrine_aging | aging_pain_management_older_adults | 0.0000 |
| aging_hypothalamic_neuroendocrine_aging | aging_social_determinants_health_disparities | 0.0000 |
| aging_hypothalamic_neuroendocrine_aging | aging_social_isolation_loneliness | 0.0000 |
| aging_hypothalamic_neuroendocrine_aging | aging_spinal_orthopedic_surgery_elderly | 0.0000 |
| aging_hypothalamic_neuroendocrine_aging | aging_stroke_cerebrovascular_outcomes | 0.0000 |
| aging_hypothalamic_neuroendocrine_aging | aging_technology_cognitive_training | 0.0000 |
| aging_hypothalamic_neuroendocrine_aging | aging_telehealth_home_care_delivery | 0.0000 |
| aging_late_life_depression_mental_health | aging_liver_fibrosis_metabolic_cognition | 0.0000 |
| aging_late_life_depression_mental_health | aging_nursing_home_ltc | 0.0000 |
| aging_late_life_depression_mental_health | aging_pain_management_older_adults | 0.0000 |
| aging_late_life_depression_mental_health | aging_social_determinants_health_disparities | 0.0000 |
| aging_late_life_depression_mental_health | aging_social_isolation_loneliness | 0.0000 |
| aging_late_life_depression_mental_health | aging_spinal_orthopedic_surgery_elderly | 0.0000 |
| aging_late_life_depression_mental_health | aging_stroke_cerebrovascular_outcomes | 0.0000 |
| aging_late_life_depression_mental_health | aging_technology_cognitive_training | 0.0000 |
| aging_late_life_depression_mental_health | aging_telehealth_home_care_delivery | 0.0000 |
| aging_liver_fibrosis_metabolic_cognition | aging_nursing_home_ltc | 0.0000 |
| aging_liver_fibrosis_metabolic_cognition | aging_pain_management_older_adults | 0.0000 |
| aging_liver_fibrosis_metabolic_cognition | aging_social_determinants_health_disparities | 0.0000 |
| aging_liver_fibrosis_metabolic_cognition | aging_social_isolation_loneliness | 0.0000 |
| aging_liver_fibrosis_metabolic_cognition | aging_spinal_orthopedic_surgery_elderly | 0.0000 |
| aging_liver_fibrosis_metabolic_cognition | aging_stroke_cerebrovascular_outcomes | 0.0000 |
| aging_liver_fibrosis_metabolic_cognition | aging_technology_cognitive_training | 0.0000 |
| aging_liver_fibrosis_metabolic_cognition | aging_telehealth_home_care_delivery | 0.0000 |
| aging_nursing_home_ltc | aging_pain_management_older_adults | 0.0000 |
| aging_nursing_home_ltc | aging_social_determinants_health_disparities | 0.0000 |
| aging_nursing_home_ltc | aging_social_isolation_loneliness | 0.0000 |
| aging_nursing_home_ltc | aging_spinal_orthopedic_surgery_elderly | 0.0000 |
| aging_nursing_home_ltc | aging_stroke_cerebrovascular_outcomes | 0.0000 |
| aging_nursing_home_ltc | aging_technology_cognitive_training | 0.0000 |
| aging_nursing_home_ltc | aging_telehealth_home_care_delivery | 0.0000 |
| aging_pain_management_older_adults | aging_social_determinants_health_disparities | 0.0000 |
| aging_pain_management_older_adults | aging_social_isolation_loneliness | 0.0000 |
| aging_pain_management_older_adults | aging_spinal_orthopedic_surgery_elderly | 0.0000 |
| aging_pain_management_older_adults | aging_stroke_cerebrovascular_outcomes | 0.0000 |
| aging_pain_management_older_adults | aging_technology_cognitive_training | 0.0000 |
| aging_pain_management_older_adults | aging_telehealth_home_care_delivery | 0.0000 |
| aging_social_determinants_health_disparities | aging_social_isolation_loneliness | 0.0000 |
| aging_social_determinants_health_disparities | aging_spinal_orthopedic_surgery_elderly | 0.0000 |
| aging_social_determinants_health_disparities | aging_stroke_cerebrovascular_outcomes | 0.0000 |
| aging_social_determinants_health_disparities | aging_technology_cognitive_training | 0.0000 |
| aging_social_determinants_health_disparities | aging_telehealth_home_care_delivery | 0.0000 |
| aging_social_isolation_loneliness | aging_spinal_orthopedic_surgery_elderly | 0.0000 |
| aging_social_isolation_loneliness | aging_stroke_cerebrovascular_outcomes | 0.0000 |
| aging_social_isolation_loneliness | aging_technology_cognitive_training | 0.0000 |
| aging_social_isolation_loneliness | aging_telehealth_home_care_delivery | 0.0000 |
| aging_spinal_orthopedic_surgery_elderly | aging_stroke_cerebrovascular_outcomes | 0.0000 |
| aging_spinal_orthopedic_surgery_elderly | aging_technology_cognitive_training | 0.0000 |
| aging_spinal_orthopedic_surgery_elderly | aging_telehealth_home_care_delivery | 0.0000 |
| aging_stroke_cerebrovascular_outcomes | aging_technology_cognitive_training | 0.0000 |
| aging_stroke_cerebrovascular_outcomes | aging_telehealth_home_care_delivery | 0.0000 |
| aging_technology_cognitive_training | aging_telehealth_home_care_delivery | 0.0000 |

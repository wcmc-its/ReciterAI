# config/thresholds.json — Tunable Reference

Every key in `config/thresholds.json` is documented here. JSON carries no comments; this sibling file centralizes meaning alongside values. Phase 12 establishes this pattern per D-26: future phases that add tunables update both `thresholds.json` and `thresholds.md`.

Schema validation: `config/thresholds.schema.json` validates this file at pipeline startup via `utils/env_check.py` (D-27). A missing key, wrong type, or misspelled key fails fast at boot rather than at the stage that first reads it.

---

## Existing keys (pre-Phase 12)

### `uncovered_score_floor` (float, default 0.4)

A publication's top topic score below this floor produces an `UNCOVERED_PMID#` event (written by `score_publications`). These events feed the feedback sweep's candidate-topic discovery (Phase 12 §9).

If you raise this: more publications are flagged as uncovered; the UNCOVERED_PMID# pool grows; the feedback sweep workload and Sonnet inference cost increases.

### `low_confidence_floor` (float, default 0.35)

Subtopic-assignment confidence below this floor produces a `LOW_CONFIDENCE_ASSIGNMENT#` event (written by `assign_subtopics`). These events feed the drift evaluator's per-topic low-confidence counts and the recluster recommendation trigger.

**Distinct from `confidence_floor`** (D-25): `low_confidence_floor` is the event-emission threshold — it decides whether to *record* that a low-confidence assignment happened. `confidence_floor` is the membership threshold — it decides whether the subtopic appears in the publication's `subtopic_ids[]` at all. A publication can have a subtopic in `subtopic_ids[]` (cleared `confidence_floor`) but still emit a low-confidence event (did not clear `low_confidence_floor`). They are currently set to 0.3 and 0.35 respectively and represent genuinely different decisions.

If you raise this: more assignments trigger LOW_CONFIDENCE_ASSIGNMENT# events; drift evaluators see higher per-topic low-confidence counts; RECLUSTER_RECOMMENDATION# rows may fire sooner.

### `drift_uncovered_rate_alert` (float, default 0.05)

When the uncovered-PMID rate within the drift window exceeds this fraction of evaluated publications, the drift evaluator raises a severity-tagged alert (surfaced via `pipeline_common.alert`).

If you raise this: alerts fire less aggressively; small-scale topic coverage gaps pass without notice.

### `drift_low_confidence_topic_max` (int, default 50)

When `LOW_CONFIDENCE_ASSIGNMENT#` counts under a single topic exceed this value within the drift window, that count contributes to the recluster recommendation trigger (D-07).

If you raise this: individual topics tolerate more low-confidence assignments before contributing to a recluster recommendation; intermittent topic mismatches are tolerated longer.

### `drift_window_days` (int, default 14)

Rolling window in days over which drift evaluations are computed. The drift evaluator queries event rows written in the past N days.

If you raise this: drift evaluations reflect a longer history; short-term anomalies are smoothed out; cold-run recommendations may lag behind real-world topic coverage changes.

### `spotlight_dirty_subtopic_min` (int, default 3)

Minimum number of dirty subtopics required before a spotlight run is triggered.

If you raise this: spotlight runs fire less frequently; batching effect means each run covers more subtopics.

### `spotlight_dirty_pubs_per_subtopic_min` (int, default 5)

Minimum number of dirty publications per subtopic required for that subtopic to be included in a spotlight run.

If you raise this: subtopics with few dirty publications are skipped; spotlight runs focus on subtopics with substantial accumulated changes.

---

## Phase 12 additions

### `tie_epsilon` (float, default 0.001)

Confidence values within this epsilon are considered tied when selecting the primary subtopic assignment in `assign_subtopics.py`. When two candidate subtopics have confidences within `tie_epsilon` of each other, the tie-breaking rule applies rather than picking the higher one deterministically. Lifted from `assign_subtopics.py:98` `TIE_EPSILON` per G-18.

If you raise this: more pairs of subtopic candidates are treated as tied; tie-breaking logic governs more assignments; primary subtopic selection becomes less sensitive to small LLM confidence differences.

### `confidence_floor` (float, default 0.3)

Assignment-time membership floor: subtopics whose assignment confidence is below this value are excluded from a publication's `subtopic_ids[]` list. Lifted from `assign_subtopics.py:95` `DEFAULT_CONFIDENCE_FLOOR` per G-18.

**Distinct from `low_confidence_floor`** (D-25): `confidence_floor` decides whether a subtopic *appears at all* in `subtopic_ids[]` — it is the gating decision for membership. `low_confidence_floor` decides whether to *emit a monitoring event* about a low-confidence assignment. These are two separate operational decisions at different points in the pipeline. They currently have different values (0.3 vs 0.35) and must be preserved as distinct keys.

Open tuning question: whether 0.3 is the correct operational target is tracked in `.planning/issues/0001-confidence-floor-target.md`. Phase 12 ships 0.3 as the default to preserve current behavior.

The CLI flag `--confidence-floor` overrides this value per-invocation (D-23). No per-topic override mechanism exists (D-24); the key has no `_default` suffix.

If you raise this: publications with moderate confidence for secondary subtopics are excluded from `subtopic_ids[]`; the inclusive aggregation counts decrease; UNCOVERED_PMID# rates may increase if fewer subtopics absorb each publication.

### `score_floor` (float, default 0.3)

Minimum relevance score for a publication to qualify as an activity row in `assign_subtopics.py`. Publications with a relevance score below this value are skipped entirely and do not appear in any aggregation. Lifted from `assign_subtopics.py:94` `SCORE_FLOOR` per G-18.

If you raise this: low-relevance publications are excluded from subtopic assignments and all downstream aggregations; coverage narrows but precision may improve.

### `display_threshold_default` (float, default 0.5)

Global default for the topic-page display threshold (#69). The per-topic value lives on each topic in `taxonomy_v2.json` (and is republished in `hierarchy.json`); this key in `thresholds.json` is the fallback applied to any topic that has not yet been individually tuned.

**Floor vs display — two different decisions** (#69):

- `score_floor` (0.3) governs *qualification*: whether a paper's score for a topic is high enough for the paper to be persisted as a topic-activity row at all. Recall-oriented screening threshold.
- `display_threshold` (per-topic, default 0.5) governs *default-view inclusion* on the SPS Scholars Topic page (`/topics/<topic_id>`): papers with `score[topic] >= display_threshold[topic]` render in the strongly-relevant section; papers with `score_floor <= score[topic] < display_threshold[topic]` render only behind the "View additional articles that are relevant" affordance.

`score_floor` is unchanged by this addition. `display_threshold` is consumed at SPS read time only — no producer behavior changes, no rollup arithmetic changes, no qualification semantics change.

Per-topic tuning is a separate, paced workstream (head-of-distribution topics first; see `docs/topic-page-inclusion-threshold.md`). v1 ships the global default and the per-topic field plumbing; individual topics keep 0.5 until tuned.

If you raise this: more papers fall below the default-view bar for any topic that still carries the default value; the wider-net affordance carries a larger pool. Per-topic overrides should be the tuning lever; this key is only a fallback.

### `feedback_sweep_max_pmids` (int, default 200)

Maximum number of `UNCOVERED_PMID#` rows the feedback sweep processes per run. The sweep sorts unprocessed PMIDs by `top_topic_score` ascending (worst-fitting first per D-06) before applying the cap, ensuring the most-uncovered cases are not preferentially dropped. When the cap is hit, the sweep's output record carries `truncated: true` and `total_unprocessed_remaining: N`.

If you raise this: each sweep run covers more PMIDs; Sonnet inference cost per sweep run increases; operator should increase sweep cadence if the cap fires regularly.

### `recluster_persistence_days` (int, default 7)

Number of consecutive days `LOW_CONFIDENCE_ASSIGNMENT#` counts under a single topic must exceed `drift_low_confidence_topic_max` before a `RECLUSTER_RECOMMENDATION#{topic_id}` is written per D-07. Expressed in days (not evaluation count) so the threshold is independent of the drift evaluator's run cadence.

If you raise this: topics must sustain elevated low-confidence counts for longer before a recluster recommendation fires; intermittent spikes are tolerated; persistent problems take longer to surface as actionable recommendations.

### `critic_reject_persistence_days` (int, default 90)

Rolling window in days over which `CRITIC_REJECT#` rows are aggregated per subtopic for the `SPOTLIGHT_DIAGNOSTIC#` trigger. 90 days covers approximately 3 monthly spotlight cycles per D-11. A shorter window (e.g., 14 days) would contain at most 1 spotlight attempt per subtopic, making per-subtopic count thresholds meaningless.

If you raise this: more historical spotlight cycles contribute to the diagnostic count; structural failures are visible over a longer history; recent fixes take longer to "clear" from the diagnostic window.

### `critic_reject_subtopic_max` (int, default 2)

Maximum distinct `(publish_id, pmid_set_hash)` pairs within the `critic_reject_persistence_days` window for a single subtopic before a `SPOTLIGHT_DIAGNOSTIC#` row is written per D-11 (renamed from `critic_reject_cwid_max` per D-08 re-keying to per-subtopic grain). Default 2 means "2 of the last 3 monthly spotlights for this subtopic had a critic-failed pmid_set" — strong evidence of a structural pool-construction or lede-generation issue for that subtopic. Lower would over-fire on intermittent failures; higher would miss subtopics that fail every other cycle.

If you raise this: more consistent critic failures are required before a diagnostic is written; subtopics that occasionally fail go unreported.

### `feedback_diagnostic_max_underlying` (int, default 20)

Maximum number of underlying `CRITIC_REJECT#` PK suffixes stored in a `SPOTLIGHT_DIAGNOSTIC#` row's `underlying_rejects` list per D-32. When the actual count exceeds this cap, `underlying_rejects_truncated: true` and `total_underlying: N` are set so the truncation is visible. Bounds DDB item size; today's ~65-topic taxonomy is well under DynamoDB's 400KB item limit either way, but sparse-by-default is the correct contract for future taxonomy growth.

If you raise this: more underlying reject provenance links are stored per diagnostic row; operators have more drill-down paths without making a secondary query; DDB row size grows.

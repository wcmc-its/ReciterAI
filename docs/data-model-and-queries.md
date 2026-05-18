# Data Model & Query Architecture

## DynamoDB Table: `reciterai`

### Record Types

| Type | PK Pattern | SK Pattern | Count | Source |
|------|-----------|------------|-------|--------|
| TOPIC# | `TOPIC#{topic_id}` | `SCORE#NNNN#ACTIVITY#pmid_{pmid}#cwid_{cwid}` | ~78K | LLM-scored (Haiku screen + Sonnet dense). Carries `hierarchy_version` (Phase 11 D-01). Rows produced before the Phase 11 writer rollout were backfilled with the sentinel `v0-legacy` on 2026-05-13 (issue #16). |
| TOOL# | `TOOL#{tool_name}` | `SCORE#NNNN#ACTIVITY#pmid_{pmid}#cwid_{cwid}` | ~15K | reciterai_tools (LLM-extracted) |
| FACULTY# | `FACULTY#cwid_{cwid}` | `PROFILE` | ~1.5K | ReciterDB analysis_summary_person |
| IMPACT# | `IMPACT#pmid_{pmid}` | `SCORE` | ~7K | reciterai_impact (GPT-5.1 scored) |
| TOOL_INDEX# | `TOOL_INDEX#{functional_category}` | `META` | 15 | Sonnet canonicalization of top tools |
| TAXONOMY# | `TAXONOMY#{version}` | `META` | 1 | 67 domain topics, versioned |
| DEEPDIVE# | `DEEPDIVE#{domain}` | `META` | 1 | Deep dive placeholder |
| PROCESSING# | `PROCESSING#pmid_{pmid}` | `STATUS` | ~6.5K | Scoring pipeline tracker |
| STAGE# | `STAGE#{stage_name}#{scope}` | `RUN#{started_at}` | grows over time | Phase 9 substrate (content-addressed completion records) |
| STAGE#hot_run | `STAGE#hot_run#GLOBAL` | `RUN#{started_at}` | one per hot-path tick | Phase 10: hot-path orchestrator audit row (anchors `last_successful_hot_run_at`) |
| UNCOVERED_PMID# | `UNCOVERED_PMID#{pmid}` | `GLOBAL` | event-volume | Phase 10: PMID whose top topic score is below the uncovered floor |
| LOW_CONFIDENCE_ASSIGNMENT# | `LOW_CONFIDENCE_ASSIGNMENT#{pmid}` | `GLOBAL` | event-volume | Phase 10: PMID whose subtopic-assignment confidence is below floor across all candidates |
| DRIFT# | `DRIFT#evaluation` | `DAY#YYYY-MM-DD` | one per day | Phase 10: drift evaluator output (rolling-window thresholds, severity, cold-run recommendation) |
| STAGE#onboarding | `STAGE#onboarding#cwid:{cwid}` | `RUN#{started_at}` | one per onboarding run | #80 Phase 2: new-researcher onboarding workflow row — 5-state terminal status |
| STAGE#onboarding_detector | `STAGE#onboarding_detector#GLOBAL` | `RUN#{started_at}` | one per detector run | #80 Phase 2: daily onboarding detector — faculty publication-gap scan + ReCiter churn |

### Global Secondary Indexes

| GSI | HASH | RANGE | Projection | Purpose |
|-----|------|-------|------------|---------|
| FacultyIndex | `faculty_uid` | `PK` | ALL | Faculty-centric queries |
| ProcessingByVersionIndex | `taxonomy_version` | `status` | KEYS_ONLY | Scoring audit/ops |
| PmidIndex | `pmid` | `PK` | ALL | Publication-centric queries |

### Three Scoring Axes

The system provides three independent dimensions for evaluating publications:

1. **Domain relevance** (TOPIC#) — What research area is this paper about? 67 topics, LLM-scored 0.0-1.0 with rationale.
2. **Tools & methods** (TOOL#) — What instruments, software, reagents, models does it use? Extracted from full text by LLM.
3. **Impact** (IMPACT#) — How significant is this paper? Scored 0-100 with justification via separate rubric.

---

## Supported Query Patterns

### Single-Dimension Queries

#### "Who works on [topic]?"
- **Access**: Query `PK = TOPIC#{topic_id}`, sort descending by SK (highest scores first)
- **Resolving topic_id**: Chat LLM reads TAXONOMY# record, matches user query to topic(s)
- **Returns**: Faculty UIDs, scores, rationales, PMIDs
- **Strength**: Fast, single partition query. Rationales explain why each faculty member matched.
- **Weakness**: Faculty who work on a topic but whose publications predate 2020 are invisible.

#### "Who uses [tool/instrument/method]?"
- **Access**: Resolve tool name via TOOL_INDEX#{category}, then query `PK = TOOL#{canonical_name}`
- **Resolving tool name**: Chat LLM reads relevant TOOL_INDEX# record(s), finds matching canonical name
- **Returns**: Faculty UIDs, PMIDs, tool_category, context
- **Strength**: Rich tool metadata. Functional categories enable browsing ("what imaging tools?").
- **Weakness**: Tools used in < 3 publications are not in TOOL_INDEX (not discoverable by browsing, but still queryable by exact PK and visible via faculty/PMID lookups). Tool name dedup is imperfect — some rare tools may have variant names.

#### "Tell me about [faculty member]"
- **Access**: Query FacultyIndex `faculty_uid = cwid_{cwid}`
- **Returns**: All TOPIC#, TOOL#, and FACULTY# records for that person
- **Filter by PK prefix**: `begins_with(PK, 'TOPIC#')` for research areas, `begins_with(PK, 'TOOL#')` for tools
- **Enrich with**: FACULTY# PROFILE record (name, department, h-index, article counts, top_topics vector)
- **Strength**: One GSI query returns everything about a faculty member across all dimensions.
- **Weakness**: Impact scores are per-publication (IMPACT#), not per-faculty. Requires a second lookup by PMID to get impact data for their papers.

#### "Tell me about [publication]"
- **Access**: Query PmidIndex `pmid = {pmid}`
- **Returns**: All TOPIC#, TOOL#, and IMPACT# records for that PMID
- **Strength**: Single query returns domain scores + tools + impact for one publication.
- **Weakness**: Does not return article metadata (title, journal, year). That comes from ReciterDB at query time.

#### "What are the highest-impact papers in [topic]?"
- **Access**: Query `PK = TOPIC#{topic_id}` to get PMIDs, then batch get `IMPACT#pmid_{pmid}` for each
- **Sort by**: impact_score descending
- **Strength**: Combines domain relevance with impact weighting.
- **Weakness**: Two-step query (topic → PMIDs → impact lookup). Could be slow for large topics with thousands of publications. Consider caching or pre-computing.

### Multi-Dimension Queries

#### "Who uses [tool] for [topic] research?"
- **Access**: Query TOOL#{tool_name} for PMIDs/faculty, query TOPIC#{topic_id} for PMIDs/faculty, intersect
- **Intersection**: Faculty UIDs or PMIDs that appear in both result sets
- **Example**: "Who uses cryo-EM for structural biology?" → TOOL#Cryo-EM ∩ TOPIC#biochemistry_biophysics
- **Strength**: True multi-axis query leveraging both scoring dimensions.
- **Weakness**: Requires client-side intersection. No server-side join in DynamoDB.

#### "Build me a P01 team for [topic]"
- **Access**: Query TOPIC#{topic_id} for faculty, get FACULTY# profiles for each, get IMPACT# for their PMIDs
- **Rank by**: Combination of topic score, impact scores, h-index, publication count
- **Diversity**: Ensure team spans departments, career stages, author positions
- **Strength**: Rich data for team assembly — scores, rationales, impact, faculty metadata all available.
- **Weakness**: Team assembly logic is complex and lives entirely in the chat LLM. No pre-computed team recommendations.

#### "Compare [faculty A] vs [faculty B] on [topic]"
- **Access**: FacultyIndex for each, filter to TOPIC#{topic_id}, compare scores
- **Enrich with**: IMPACT# for their respective PMIDs in that topic
- **Strength**: Direct comparison with rationales explaining each person's relevance.
- **Weakness**: Comparison logic is in the chat LLM. No pre-computed rankings.

#### "What tools are commonly used in [topic] research?"
- **Access**: Query TOPIC#{topic_id} for PMIDs, then PmidIndex for each to get TOOL# records, aggregate
- **Strength**: Discovers tool-topic co-occurrence from actual publication data.
- **Weakness**: Scatter-gather across many PMIDs. Expensive for large topics. Consider pre-computing tool-topic associations.

### Browse / Exploration Queries

#### "What research areas does our institution cover?"
- **Access**: Read TAXONOMY# record
- **Returns**: 67 domain topics with labels and descriptions
- **Strength**: Complete institutional research landscape in one read.

#### "What kinds of research tools does our institution use?"
- **Access**: Read all TOOL_INDEX# records (15 functional categories)
- **Returns**: 175 canonical tools grouped by function (imaging, sequencing, computational, etc.)
- **Strength**: Browsable catalog by functional category.
- **Weakness**: Only tools with >= 3 publications. Rare/specialized tools not listed.

#### "How many publications do we have on [topic]?"
- **Access**: Query `PK = TOPIC#{topic_id}`, count distinct PMIDs
- **Note**: Count reflects publications >= 2020 with synopses that scored >= 0.3 on dense scoring. Not a complete publication count.

---

## STAGE# Substrate Records (Phase 9)

Per [`docs/RECITERAI-SPEC.md` §5](RECITERAI-SPEC.md#5-decision-4--content-addressed-stage-completion). Every pipeline stage that integrates with the substrate writes a `STAGE#` row carrying a content-addressed `input_hash`. A subsequent run with the same `input_hash` short-circuits — writing a `skipped` row that still carries `duration_ms` and `cost_observed_usd = Decimal("0")` (Phase 10 D-09: skips are first-class zeros for `SUM(cost_observed_usd)` aggregation; DDB GetItem lookup cost is not modeled per row).

| Field | Type | Notes |
|---|---|---|
| `PK` | string | `STAGE#{stage_name}#{scope}` — scope is `"GLOBAL"` for whole-pipeline stages, topic-specific for per-topic stages (subtopic discovery, assignment). |
| `SK` | string | `RUN#{started_at}` ISO 8601, lex order = chronological. |
| `stage` | string | Same as the stage segment of PK; denormalized for filtering. |
| `scope` | string | Same as the scope segment of PK; denormalized for filtering. |
| `input_hash` | string (hex sha256) | Computed by `utils.stage_records.compute_input_hash(stage, inputs)`. The schema of `inputs` is per-stage; documented in each integrating stage's plan. |
| `status` | string enum | `complete` \| `skipped` \| `failed`. Only `complete` rows anchor future skips. |
| `skip_reason` | string \| null | Set only when `status == "skipped"`. |
| `started_at`, `completed_at` | ISO 8601 strings | |
| `duration_ms` | number | Wall clock from start to completion (including hash compute + lookup for skips). |
| `cost_observed_usd` | Decimal | `Decimal("0")` for skipped rows (no work performed); observed Bedrock/AWS cost for complete and failed rows. Never omitted. |
| `output_pointer` | string \| null | `s3://...` or `ddb://...`; format per-stage. |
| `records_written` | integer \| null | |
| `model_ids_snapshot` | list[string] \| null | The Bedrock model IDs that contributed to `input_hash` (see `utils.bedrock_client.MODEL_IDS_BY_STAGE`). |
| `force_reason` | string \| null | Populated when a `block`-severity gate was overridden via `python -m gates --force --force-reason "..."`. |
| `error_code`, `error_message`, `failure_details` | mixed \| null | Set only when `status == "failed"`. |

### Query patterns

| Question | Access |
|---|---|
| "Has this exact input ever been processed by stage X under scope Y?" | Query `PK = STAGE#{stage}#{scope}`, filter on `input_hash == target` and `status == complete` in Python. See `utils.stage_records.find_existing_complete`. |
| "What's the most recent run of stage X under scope Y?" | Same query, `ScanIndexForward=False`, take the first row. |
| "How much did pipeline runs cost in the last 7 days?" | Scan with `started_at >= cutoff`, sum `cost_observed_usd` (split by `status` for the real-work vs skip-detection split). |

A GSI keyed on `input_hash` is deferred to Phase 10 — at one consumer (`publish_hierarchy`), the row count per PK is tiny and the in-Python filter on `input_hash` is cheap. Phase 10 should reassess when score/assignment/rollup/spotlight stages start writing.

### Why a separate item type instead of a separate table

Single-table design is the existing convention in `reciterai` (see Record Types table above). Adding a new table introduces a second connection, a second IAM policy slice, and a second cost line for no benefit — `STAGE#` items share zero schema with the others and live under their own PK namespace anyway.

---

## Phase 10 Records

Four new record types land in Phase 10 (hot/cold path split). Each uses the existing `reciterai` table; no new table or GSI was needed.

### `STAGE#hot_run#GLOBAL` — hot-path orchestrator audit row

Per spec §6. Written by `pipeline_hot.orchestrator` on every hot-path tick. Anchors `last_successful_hot_run_at` so the next tick can compute the delta PMID set.

| Field | Type | Notes |
|---|---|---|
| `PK` | string | `STAGE#hot_run#GLOBAL` (constant). |
| `SK` | string | `RUN#{started_at}` — same shape as the generic STAGE# substrate. |
| `stage` | string | `"hot_run"`. |
| `scope` | string | `"GLOBAL"`. |
| `status` | string enum | `complete` \| `skipped` \| `failed`. `skipped` with `skip_reason: "prior_run_in_progress"` is the lock-collision sentinel (Open Q 10.5). |
| `started_at`, `completed_at` | ISO 8601 | Wall-clock execution window of the Step Functions run. |
| `duration_ms` | number | |
| `cost_observed_usd` | Decimal | Aggregated cost from all stage envelopes (score + assign + rollup). |
| `delta_pmid_count` | integer | Number of PMIDs the orchestrator handed to the score stage. |
| `state_machine_execution_arn` | string \| null | The Step Functions execution that wrote this row. Useful for cross-referencing the AWS console. |

**Query patterns**

| Question | Access |
|---|---|
| "When was the last successful hot run?" | Query `PK = STAGE#hot_run#GLOBAL` with `ScanIndexForward=False`; first row where `status == complete` is the cutoff (see `pipeline_hot.orchestrator.find_last_successful_hot_run_at`). |
| "Did any recent hot run fail or skip?" | Same query, inspect status of the top-N rows. |
| "How much has the hot path cost this quarter?" | Scan `PK = STAGE#hot_run#GLOBAL` with `started_at >= cutoff`, sum `cost_observed_usd`. |

### `UNCOVERED_PMID#{pmid}` — uncovered-PMID event record

Per spec §9 (replaces v1 G-12's silent force-fit). Written by `score_publications` when a PMID's top topic score is below `config.uncovered_score_floor` (default 0.4). The drift evaluator consumes these.

| Field | Type | Notes |
|---|---|---|
| `PK` | string | `UNCOVERED_PMID#{pmid}`. |
| `SK` | string | `GLOBAL` (constant — one row per PMID; idempotent overwrite on retry). |
| `record_type` | string | `"UNCOVERED_PMID"`. |
| `pmid` | string | Same as the PK segment; denormalized for GSI / filtering. |
| `taxonomy_version` | string | The taxonomy version at evaluation time. Drift signal is *per-taxonomy*; a taxonomy bump invalidates the back-window. |
| `top_topic_score` | Decimal | The highest score seen across all topics for this PMID. |
| `top_topics` | list[map] | Top-3 closest topics: `[{topic_id, score}]` ordered descending by score. Operator triage data — narrows the "why uncovered" question. |
| `created_at` | ISO 8601 | When the event was written. |
| `source_stage` | string | `"score_publications"`. |

**Query patterns**

| Question | Access |
|---|---|
| "How many PMIDs are uncovered in the last 14 days?" | Scan `begins_with(PK, "UNCOVERED_PMID#")` with `created_at >= cutoff`. The drift evaluator does exactly this. |
| "Which topics keep almost-matching the uncovered PMIDs?" | Same scan, aggregate `top_topics[].topic_id` counts to find candidates for taxonomy expansion. |

### `LOW_CONFIDENCE_ASSIGNMENT#{pmid}` — low-confidence subtopic event record

Per spec §9. Written by `assign_subtopics` when every candidate subtopic confidence under the PMID's top topic falls below `config.low_confidence_floor` (default 0.35). Stored *per-topic* (the field carries `topic_id`) so the drift evaluator can roll up "any single topic accumulates >50" per spec §9.

| Field | Type | Notes |
|---|---|---|
| `PK` | string | `LOW_CONFIDENCE_ASSIGNMENT#{pmid}`. |
| `SK` | string | `GLOBAL`. |
| `record_type` | string | `"LOW_CONFIDENCE_ASSIGNMENT"`. |
| `pmid` | string | |
| `topic_id` | string | The PMID's top topic. Pivots the per-topic drift aggregation. |
| `max_confidence` | Decimal | Highest confidence across all candidate subtopics — the headroom before the floor. |
| `candidate_confidences` | map[subtopic_id → Decimal] | Full classifier output for the candidates considered. |
| `created_at` | ISO 8601 | |
| `source_stage` | string | `"assign_subtopics"`. |

**Query patterns**

| Question | Access |
|---|---|
| "Which topic has the most low-confidence assignments?" | Scan `begins_with(PK, "LOW_CONFIDENCE_ASSIGNMENT#")` with `created_at >= cutoff`, group by `topic_id`. The drift evaluator persists `low_confidence_max_topic` + `low_confidence_max_count` on its DRIFT# row so the dashboard doesn't have to repeat this. |

### `DRIFT#evaluation` — daily drift evaluator output

Per spec §9. Written by `pipeline_drift.evaluator` once per daily evaluation. Single row per evaluation day (idempotent overwrite if the cron fires twice the same day).

| Field | Type | Notes |
|---|---|---|
| `PK` | string | `DRIFT#evaluation` (constant). |
| `SK` | string | `DAY#YYYY-MM-DD` from the window-end date — gives natural daily idempotency. |
| `record_type` | string | `"DRIFT_EVALUATION"`. |
| `window_start`, `window_end` | ISO 8601 | The rolling-N-day window the evaluation covered. |
| `drift_window_days` | integer | Window size (default 14 — from `config.drift_window_days`). |
| `uncovered_count` | integer | Count of UNCOVERED_PMID# events in window. |
| `low_confidence_count` | integer | Count of LOW_CONFIDENCE_ASSIGNMENT# events in window. |
| `stage_failed_count` | integer | Count of `STAGE#…#failed` rows in window. |
| `new_pmid_count` | integer | Denominator for `uncovered_rate` (total PMIDs landed in window). |
| `uncovered_rate` | Decimal | `uncovered_count / new_pmid_count` rounded to 6 places. Zero when `new_pmid_count == 0`. |
| `low_confidence_max_topic` | string \| null | Topic with the most low-confidence events. |
| `low_confidence_max_count` | integer | Count for that topic. |
| `triggered_thresholds` | list[string] | Names of thresholds that fired: `uncovered_rate_alert`, `low_confidence_topic_max`, `stage_failures_in_window`. Drives the severity-table mapping in `pipeline_drift.severity`. |
| `cold_run_recommended` | boolean | `true` iff severity is `ERROR` and one of the cold-run-trigger conditions fired (uncovered_rate_alert or low_confidence_topic_max). |
| `severity` | string enum | `OK` \| `WARN` \| `ERROR`. Source of truth for the dispatcher in `pipeline_common.alert`. |

**Query patterns**

| Question | Access |
|---|---|
| "What does the most recent drift evaluation say?" | Query `PK = DRIFT#evaluation` with `ScanIndexForward=False`, limit=1. |
| "What's the drift trend over the last N days?" | Same query, no limit; iterate `triggered_thresholds` + `uncovered_rate` per day for a quick chart. |
| "Did any evaluation in the last week recommend a cold run?" | Query `PK = DRIFT#evaluation` with `SK >= DAY#{cutoff}`, filter `cold_run_recommended == true`. |

The four Phase 10 record types are write-only this phase. Phase 12 wires consumption: the UNCOVERED_PMID# events feed a Sonnet sweep for taxonomy expansion, and the LOW_CONFIDENCE_ASSIGNMENT# events feed a subtopic split/merge decision.

---

## Onboarding Records (#80 Phase 2)

Two record types land with the new-researcher onboarding workflow
(`pipeline_onboarding`). Both use the existing `reciterai` table; no new
table or GSI. See `docs/hot-cold-paths.md` for the operator guide.

### `STAGE#onboarding#cwid:{cwid}` — onboarding workflow row

One row per onboarding run, written by the `reciterai-onboarding` state
machine — by the orchestrator (`deferred` / `skipped` / cost-guard
`failed`), by `Finalize` (`complete` / `partial`), or by the inline
catch-all (`failed`). Built by `pipeline_onboarding.build_onboarding_record`.

| Field | Type | Notes |
|---|---|---|
| `PK` | string | `STAGE#onboarding#cwid:{cwid}`. |
| `SK` | string | `RUN#{started_at}` for clean terminals; `RUN#FAILED#{started_at}` for the catch-all `failed` writer. |
| `stage` | string | `"onboarding"`. |
| `scope` | string | `cwid:{cwid}`. |
| `status` | string enum | The 5-state terminal taxonomy (R7): `complete` \| `partial` \| `deferred` \| `skipped` \| `failed`. Exactly one per run. |
| `cwid`, `run_id` | string | `run_id` is the Step Functions execution name — the correct key for locating a specific run's row (never the highest SK). |
| `input_hash` | string | Content hash over `{cwid, pmid set}`; labels the run, not a skip gate. |
| `started_at`, `completed_at` | ISO 8601 | |
| `duration_ms` | number | |
| `cost_observed_usd` | Decimal | Summed observed per-stage cost; `0` for the non-`ready` terminals. |
| `pmid_count`, `net_work_count` | number \| null | Accepted-set size; net work after the `PROCESSING#` cull. |
| `projected_cost_usd` | string \| null | Pre-formatted cost estimate (display string, not measured spend). |
| `partial_failure_count`, `failed_pmids` | number / list \| null | Set on `partial` — PMIDs that did not reach a scored state. |
| `deferred_reason`, `deferred_pmids` | string / list \| null | Set on `deferred` — the synopsis-gap PMIDs. |
| `skip_reason` | string \| null | Set on `skipped`. |
| `error_code`, `error_message`, `failure_details` | mixed \| null | Set on `failed`. A cost-guard trip is `error_code=CostGuardExceeded`, `failure_details` carrying `{net_work_count, projected_cost_usd, threshold}` (D-COSTSTATUS). |
| `stage_input_hashes` | map \| null | Per-stage `input_hash` (score / assign / top_topic / rollup), recorded by `Finalize`. |

**Query patterns**

| Question | Access |
|---|---|
| "What happened on onboarding run X?" | Query `PK = STAGE#onboarding#cwid:{cwid}`, filter `run_id == X`. Scripted: `scripts/smoke_onboarding.sh`. |
| "What is this CWID's latest onboarding outcome?" | Same query, `ScanIndexForward=False` — but a stale `RUN#FAILED#...` SK sorts above clean `RUN#{iso}` rows, so prefer the `run_id` filter when a specific run is meant. |
| "Which CWIDs ended `partial` / `failed` recently?" | Scan `begins_with(PK, "STAGE#onboarding#cwid:")`, filter `status` + `started_at >= cutoff`. |

### `STAGE#onboarding_detector#GLOBAL` — daily detector run row

One row per detector run, written by `reciterai-onboarding-detector` (R8) —
on every run, including quiet days. Built by
`DetectorEvaluation.to_stage_record`.

| Field | Type | Notes |
|---|---|---|
| `PK` | string | `STAGE#onboarding_detector#GLOBAL` (constant). |
| `SK` | string | `RUN#{started_at}`. |
| `record_type` | string | `"ONBOARDING_DETECTOR_RUN"`. |
| `stage`, `scope` | string | `"onboarding_detector"`, `"GLOBAL"`. |
| `status` | string | `complete` on a normal run; a crash writes a `failed` row via `write_failed`. |
| `run_id`, `input_hash` | string | |
| `started_at`, `completed_at` | ISO 8601 | |
| `duration_ms` | number | |
| `cost_observed_usd` | Decimal | Pinned `0` — the detector spends no model budget. |
| `scanned_cwid_count` | number | Faculty CWIDs scanned. |
| `flagged_cwid_count` | number | CWIDs with a gap and/or attribution churn. |
| `gap_cwid_count`, `churn_cwid_count` | number | Flagged CWIDs by reason (a CWID can be both). |
| `total_missing_synopsis`, `total_missing_score` | number | Gapped-PMID totals across all flagged CWIDs. |
| `total_churn_added`, `total_churn_removed` | number | ReCiter attribution-drift PMID totals. |
| `flagged_cwids` | list[string] \| null | The flagged CWIDs (capped at 200); omitted when none flagged. |
| `cold_start_mode` | bool | True when the cold-start guard fired — a digest issue was filed instead of per-CWID issues (#106). |
| `digest_issue_number` | number | The `[onboarding] Detector backlog digest` issue number; present only on a cold-start run that filed or refreshed it. |

**Query patterns**

| Question | Access |
|---|---|
| "What did the most recent detector run find?" | Query `PK = STAGE#onboarding_detector#GLOBAL`, `ScanIndexForward=False`, limit 1. |
| "Is the onboarding backlog growing or shrinking?" | Same query, no limit; trend `flagged_cwid_count` across runs. |

The GitHub issues the detector files (labelled `onboarding`) carry the full
per-CWID PMID detail; these rows are the run-level summary.

---

## Known Weaknesses & Limitations

### Data Coverage
- **Temporal**: Only publications from 2020 onward with synopses. Earlier work is invisible.
- **Synopsis dependency**: Publications without synopses in reciterai_synopsis are excluded from scoring. Synopsis coverage depends on upstream ReciterAI pipeline runs.
- **Author scope**: Currently includes all author positions from analysis_summary_author (first, middle, last) joined with fullTimeFaculty = 'yes'. Expandable to non-faculty without rescoring.
- **Tool extraction**: Tools are LLM-extracted with confidence >= 7. Lower-confidence extractions are excluded. Tool names are raw (not all canonicalized).

### Scoring Accuracy
- **Topic scoring**: Two-pass LLM scoring (Haiku screening + Sonnet dense). Screening is calibrated for recall (err on inclusion). Some cross-cutting topics like `biostatistics_quantitative_sciences` appear on ~50% of publications — these are noisy at lower score thresholds.
- **Impact scoring**: Scored by a separate rubric (GPT-5.1). Independent of topic taxonomy. Being evaluated for migration to Claude.
- **Tool confidence**: Confidence 0-10 from LLM extraction. Threshold at 7 filters low-quality entries but may miss some legitimate tools.

### Query Limitations
- **No server-side joins**: Multi-dimension queries (topic + tool, topic + impact) require client-side intersection. DynamoDB has no join capability.
- **Tool name resolution**: User's query ("cryo-EM") must be resolved to exact PK string ("TOOL#Cryo-EM"). TOOL_INDEX helps for top 175 tools but rare tools require fuzzy matching.
- **Article metadata**: Title, journal, year, abstract are not stored in DynamoDB. Must be looked up from ReciterDB at query time. This is by design (metadata changes, scores don't) but adds latency.
- **No pre-computed rankings**: Faculty rankings per topic, tool-topic associations, and team recommendations are computed at query time by the chat LLM. For frequently asked queries, consider pre-computing and caching.

### Scaling Considerations
- **Record volume**: ~100K records at current scale (WCM, 2020+). Expandable to other campuses (Cornell Ithaca) and earlier years. DynamoDB scales automatically with PAY_PER_REQUEST.
- **GSI cost**: ALL projection on FacultyIndex and PmidIndex means full record duplication. At current scale (~100K records) this is negligible. At 10x scale, monitor storage costs.
- **Taxonomy changes**: Re-scoring with a new taxonomy requires a full pipeline re-run (~$65, ~55 minutes). Taxonomy version is tracked on every record for traceability. Old and new scores can coexist (different taxonomy_version values).

---

## Future Improvements

### Pre-Computed Aggregations
- **Faculty-topic rankings**: Pre-compute "top 10 faculty per topic" records to avoid scanning thousands of TOPIC# records per query.
- **Tool-topic co-occurrence**: Pre-compute which tools are commonly used in each topic area. Eliminates scatter-gather for "what tools are used in [topic]?" queries.
- **Department-level profiles**: Aggregate topic scores by department for "what does the Department of Medicine focus on?" queries.

### Data Quality
- **Tool canonicalization**: Populate `family_canonical` in reciterai_tools upstream. Currently only 175 of 4,830 tools are canonicalized via the TOOL_INDEX pass.
- **Tool deduplication**: Some tools appear with slight name variations. Upstream dedup would reduce TOOL# record count and improve query precision.
- **Synopsis coverage**: Expand synopsis generation to cover pre-2020 publications for historical research landscape queries.

### Additional Record Types
- **GRANT#**: Grant data (if available) linked to faculty and topics.
- **CLINICAL_TRIAL#**: Clinical trial data linked to faculty and therapeutic areas.
- **COLLABORATION#**: Pre-computed co-authorship and collaboration networks.

---

*Last updated: 2026-04-09*
*Taxonomy version: taxonomy_v2 (67 topics)*
*Scoring pipeline: Bedrock Haiku 4.5 (screening) + Sonnet 4.6 (dense scoring)*

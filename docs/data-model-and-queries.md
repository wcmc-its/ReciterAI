# Data Model & Query Architecture

## DynamoDB Table: `reciterai-chatbot`

### Record Types

| Type | PK Pattern | SK Pattern | Count | Source |
|------|-----------|------------|-------|--------|
| TOPIC# | `TOPIC#{topic_id}` | `SCORE#NNNN#ACTIVITY#pmid_{pmid}#cwid_{cwid}` | ~78K | LLM-scored (Haiku screen + Sonnet dense) |
| TOOL# | `TOOL#{tool_name}` | `SCORE#NNNN#ACTIVITY#pmid_{pmid}#cwid_{cwid}` | ~15K | reciterai_tools (LLM-extracted) |
| FACULTY# | `FACULTY#cwid_{cwid}` | `PROFILE` | ~1.5K | ReciterDB analysis_summary_person |
| IMPACT# | `IMPACT#pmid_{pmid}` | `SCORE` | ~7K | reciterai_impact (GPT-5.1 scored) |
| TOOL_INDEX# | `TOOL_INDEX#{functional_category}` | `META` | 15 | Sonnet canonicalization of top tools |
| TAXONOMY# | `TAXONOMY#{version}` | `META` | 1 | 67 domain topics, versioned |
| DEEPDIVE# | `DEEPDIVE#{domain}` | `META` | 1 | Deep dive placeholder |
| PROCESSING# | `PROCESSING#pmid_{pmid}` | `STATUS` | ~6.5K | Scoring pipeline tracker |
| STAGE# | `STAGE#{stage_name}#{scope}` | `RUN#{started_at}` | grows over time | Phase 9 substrate (content-addressed completion records) |

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

Per [`docs/RECITERAI-SPEC.md` §5](RECITERAI-SPEC.md#5-decision-4--content-addressed-stage-completion). Every pipeline stage that integrates with the substrate writes a `STAGE#` row carrying a content-addressed `input_hash`. A subsequent run with the same `input_hash` short-circuits — writing a `skipped` row that still carries `duration_ms` and a pinned `SKIP_COST_USD = 0.0000003` (spec invariant: skips MUST emit cost rows so dashboards split real-work cost from skip-detection cost).

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
| `cost_estimate_usd` | Decimal | `SKIP_COST_USD` for skipped rows; per-stage estimate for complete/failed rows. |
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
| "How much did pipeline runs cost in the last 7 days?" | Scan with `started_at >= cutoff`, sum `cost_estimate_usd` (split by `status` for the real-work vs skip-detection split). |

A GSI keyed on `input_hash` is deferred to Phase 10 — at one consumer (`publish_hierarchy`), the row count per PK is tiny and the in-Python filter on `input_hash` is cheap. Phase 10 should reassess when score/assignment/rollup/spotlight stages start writing.

### Why a separate item type instead of a separate table

Single-table design is the existing convention in `reciterai-chatbot` (see Record Types table above). Adding a new table introduces a second connection, a second IAM policy slice, and a second cost line for no benefit — `STAGE#` items share zero schema with the others and live under their own PK namespace anyway.

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

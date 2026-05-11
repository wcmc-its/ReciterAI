# ReCiter AI Chatbot — Design Spec

> **Status**: Approved design — ready for implementation planning
> **Date**: 2026-04-08
> **Target**: Demo-ready prototype for CIO and Library Director
> **Timeline**: ~1 week (demo-ready subset); ~3 weeks (full v1)
> **Integration**: Embedded in ReCiter Publication Manager (Next.js 14)

### What this spec covers

- **Sections 1-13**: Durable system design — architecture, schema, pipeline, query patterns, UI. Hand this to Sumanth as the implementation reference.
- **Section 14**: Capability roadmap — what's demo-ready vs. full v1 vs. v2.
- **Sections 15-17**: Demo-specific — cost model, critical path, and demo script.

---

## 1. Problem Statement

WCM needs a conversational interface for faculty expertise discovery. Research deans and grants officers currently rely on manual knowledge and ad-hoc searches to answer questions like "who works on aging?", "who should we nominate for this award?", or "build me a team for this P01." The chatbot turns pre-computed research intelligence into an interactive tool embedded in the application faculty and administrators already use.

The audience for this demo is the CIO and Library Director, who control funding decisions. The demo must look polished AND demonstrate real intelligence.

---

## 2. Architecture Overview

Two decoupled systems share DynamoDB as the interface:

### Offline Pipeline (ReciterAI repo, Python)
- Extracts publication data from ReciterDB (MariaDB)
- Scores publications against a 50-topic taxonomy via AWS Bedrock
- Loads scored results into DynamoDB
- Runs infrequently (initial load + periodic refresh)

### Chat Runtime (Publication Manager, Next.js 14)
- New `/chat` page with guided entry and freeform input
- API route orchestrates: query routing -> retrieval -> synthesis
- All LLM calls via AWS Bedrock (costs on library AWS account)
- Streams responses via Server-Sent Events

### System Diagram

```
OFFLINE                              RUNTIME (Publication Manager)

ReciterDB ──> Scoring Script         User ──> /chat page
(MariaDB)     (Python)                          |
                |                          POST /api/chat
                |                               |
         Bedrock Haiku              +-----------+-----------+
         (screening)                |           |           |
                |                 Router    Retrieval    Synthesis
         Bedrock Sonnet          (Bedrock   (DynamoDB   (Bedrock
         (dense scoring)          Haiku)    + ReciterDB)  Sonnet)
                |                               |
                v                          Streamed response
            DynamoDB  <-------- reads ------+
```

### Data Scope (v1)
- **Publications only** — grants, clinical trials, and patents are punted to v2
- **WCM full-time faculty, first or last author only** — ~10,000 publications
- **50 topics** — generated taxonomy validated against institutional priorities
- **All LLM calls via AWS Bedrock** — costs on the library's AWS account

---

## 3. DynamoDB Schema

Single table: `reciterai-chatbot`

### Record Type 1: Topic Scores

| Key | Example | Purpose |
|-----|---------|---------|
| PK | `TOPIC#aging_geroscience` | Topic partition |
| SK | `SCORE#0950#ACTIVITY#pmid_38765432` | Zero-padded score + activity ID |

Attributes: `faculty_uid`, `score` (numeric), `synopsis`, `impact_score`, `year`, `journal`, `title`, `topic_scores_version`

Access pattern: `PK = TOPIC#X AND SK >= SCORE#0600` returns all activities scoring 0.6+ for a topic, sorted best-first by DynamoDB's lexicographic sort on the SK. Note: the table stores all scores >= 0.3 (the screening threshold), but runtime retrieval uses higher thresholds (0.6 direct, 0.4 adjacent) to keep candidate sets tight. The lower storage threshold allows threshold tuning without re-scoring.

### Record Type 2: Faculty Index

| Key | Example | Purpose |
|-----|---------|---------|
| PK | `FACULTY#cwid_jsmith` | Faculty partition |
| SK | `PROFILE` | Faculty metadata |

Attributes: `name`, `department`, `title`, `h_index`, `article_count`, `first_author_count`, `last_author_count`, `top_topics` (list of strongest topic scores as a vector for similarity search)

Access pattern: Person-centric queries and enriching synthesis results with faculty metadata.

### Record Type 3: Tool Scores

| Key | Example | Purpose |
|-----|---------|---------|
| PK | `TOOL#whole_genome_sequencing` | Tool partition |
| SK | `SCORE#0910#ACTIVITY#pmid_38765432` | Zero-padded score + activity ID |

Same attributes as topic scores. Enables intersection queries ("who combines aging expertise with genomics methods").

### Record Type 4: Canned Deep Dives

| Key | Example | Purpose |
|-----|---------|---------|
| PK | `DEEPDIVE#aging_geroscience` | Domain partition |
| SK | `META` | Deep dive metadata and content |

Attributes: `domain`, `generated_at`, `taxonomy_version`, `subtopics` (inductively derived), `content` (structured document), `reviewed_by`, `review_status`

Access pattern: "Tell me about WCM's aging research" retrieves the pre-computed deep dive.

### Record Type 5: Evaluation Records

| Key | Example | Purpose |
|-----|---------|---------|
| PK | `EVAL#resp_xyz789` | Response evaluation |
| SK | `META` | Evaluation metadata |

Attributes: `eval_type` (faculty_accuracy | strategic_utility), `faculty_uid` (if applicable), `topic`, `rating` (1-5), `narrative`, `tags` (structured feedback), `rated_at`, `taxonomy_version`

Faculty accuracy aggregates stored as:

| Key | Example |
|-----|---------|
| PK | `FACULTY#cwid_jsmith` |
| SK | `ACCURACY` |

Attributes: `topic_ratings` (map of topic -> {mean, count, last_rated}), `overall_mean`

### Record Type 6: Processing Tracker

| Key | Example | Purpose |
|-----|---------|---------|
| PK | `PROCESSING#pmid_38765432` | Activity processing state |
| SK | `STATUS` | Single record per activity |

Attributes: `status` (pending/screened/complete/failed), `taxonomy_version`, `screened_at`, `scored_at`, `screening_passed_topics`, `retry_count`, `error`

Foundation for automated refresh pipeline. For the demo, this is bookkeeping from the initial load. The processing tracker is audit state only — it records what happened, not what needs to happen. For future automation, the work queue (SQS) determines what to process next; the tracker records the outcome.

### GSI 1: Faculty-to-Topics

| GSI PK | GSI SK |
|--------|--------|
| `faculty_uid` | Main table PK |

Projection: `faculty_uid`, `PK`, `SK`, `score`, `synopsis`, `impact_score`, `year`, `department`, `title`, `top_topics`. Projects only the attributes needed for similarity search and person-centric queries — avoids projecting full record content at scale.

Enables: "What topics does this faculty member score highly on?" and similarity search across faculty topic vectors.

### GSI 2: Processing-by-Version

| GSI PK | GSI SK |
|--------|--------|
| `taxonomy_version` | `status` |

Projection: KEYS_ONLY.

Enables: "Find all activities scored under taxonomy_v1 that need reprocessing" — query `taxonomy_version = v1 AND status = needs_reprocess`. This avoids a full table scan for reprocessing queries. Not used for the demo but required for the automation glidepath.

### Scale Estimate

- ~10,000 publications x ~35% pass rate x ~3 topics = ~10,000-15,000 topic score records
- ~2,500 faculty profile records
- ~10,000 processing tracker records
- ~10,000-15,000 tool score records
- Deep dive records: ~10-50 (one per domain)
- Well within DynamoDB free tier for storage and demo-level read volumes

---

## 4. Topic Taxonomy

### Generation Process

1. Sample ~500 publication abstracts across departments from ReciterDB
2. Feed to Bedrock Sonnet: "Derive 40-60 mid-level research domains that cover this institution's output"
3. Curate: merge overlaps, add strategically important domains, remove overly specific ones
4. Validate against 10-15 sample queries a research dean would actually ask
5. Freeze as `taxonomy_v1` — version field on every score record

### Design Principles

- Stable enough that terminology doesn't shift every two years
- Broad enough to avoid the "tau-pathology brittleness problem" — mid-level granularity
- Clustered: disease areas + methodological topics + career-stage topics
- Versioned: version bumps trigger targeted recomputation

### Cost

Taxonomy generation: ~$1-2 in Bedrock. Full scoring run against 10K publications: ~$35 total (screening + dense scoring).

---

## 5. Data Pipeline (One-Time Scoring Script)

Lives in the ReciterAI repo as a new script. Three phases:

### Phase 1: Extract

Query ReciterDB for publications where:
- Faculty is WCM full-time (`person_person_type`)
- Author position is first or last (`analysis_summary_author`)
- Publication is accepted/asserted

Pull: pmid, title, journal, year, abstract (from `reporting_abstracts`), author info. Also pull faculty metadata from `analysis_summary_person` (h-index, article counts, department).

Reuse existing ReciterAI pipeline output where available (synopses, impact scores, tools from `reciterai_*` tables).

### Phase 2: Score via Bedrock

For each publication, one Haiku call scores against all 50 topics simultaneously — a single prompt contains the taxonomy and the publication's title + abstract + synopsis, and returns a JSON object `{topic_id: score}` for all 50 topics in one response. This is one API call per publication, not one per topic.

Publications scoring 0.3+ on any topic go to dense Sonnet pass for rigorous scoring. The Sonnet call receives only the topics that passed screening (not all 50) and produces calibrated final scores.

**Score threshold flow:** Haiku screening (0.3+ stored) -> Sonnet dense scoring (recalibrates) -> DynamoDB stores all scores >= 0.3 -> Runtime retrieval uses 0.6+ (direct) or 0.4+ (adjacent) thresholds. Storing at 0.3 allows future threshold tuning without re-scoring.

Bedrock specifics:
- `boto3` with `bedrock-runtime` client
- Pin to specific model versions for reproducible scoring: `anthropic.claude-haiku-4-5` (screening, routing) and `anthropic.claude-sonnet-4-6` (dense scoring, synthesis). Bedrock aliases can shift — pinning ensures score consistency across runs.
- No native batch API on Bedrock — use asyncio with semaphore (10-20 concurrent calls)
- Estimated wall-clock time: 2-4 hours for 10K publications
- Request Bedrock quota increases if needed before starting
- Error handling: retry failed calls up to 3 times with exponential backoff. Log failures to processing tracker as `status: failed`. Target: <1% failure rate at demo scale.

### Phase 3: Load DynamoDB

Write scored results using `boto3` DynamoDB `batch_write_item`:
- Topic score records (scores >= 0.3 only)
- Tool score records (from existing ReciterAI tool extraction)
- Faculty profile records (from ReciterDB `analysis_summary_person` + `identity`)
- Processing tracker records
- Canned deep dive records (aging domain from existing pilot work)

### Canned Deep Dive Review

Deep dives are not surfaced to users until `review_status: approved` on the DEEPDIVE# record. A research dean or domain expert should review each generated deep dive for accuracy before publication. The pipeline sets `review_status: pending` on creation; a manual update (or future admin UI) flips it to `approved`.

### Glidepath to Automation

The processing tracker is audit state (what happened); SQS is the work queue (what to do next). Future automation path:
- Use GSI 2 (`taxonomy_version` + `status`) to identify activities scored under an old taxonomy version: query `taxonomy_version = v1 AND status = needs_reprocess`
- New publications enter via SQS queue — Lambda picks them up, scores them, writes results to DynamoDB, updates the processing tracker
- The same scoring script logic wraps into a Lambda handler
- Not built for the demo, but the schema and GSI support it

---

## 6. Query Types and Retrieval Patterns

### Six Retrieval Patterns

| Pattern | Example Query | Backend | Logic |
|---------|--------------|---------|-------|
| Topic match | "Who works on aging?" | DynamoDB | Query topic partition, rank by score |
| Person-centric | "What has Dr. Smith published?" | DynamoDB + ReciterDB | Faculty GSI + publication list |
| Publication report | "Dept of Medicine 2024 output" | DynamoDB + ReciterDB | SQL with smart defaults + topic enrichment |
| Nomination synthesis | "Draft a summary of Dr. Chen for an award" | DynamoDB + ReciterDB | Skip candidate retrieval, synthesize over one person's full record |
| Gap/inverse query | "Where are we thin?" | DynamoDB | Iterate topics, count faculty above threshold, sort ascending |
| Team assembly | "Build a P01 team for cardiovascular" | DynamoDB + ReciterDB | Diversity-aware: decompose into roles, top candidates per role, cross-departmental spread |

### Additional Capabilities

| Capability | Approach |
|------------|----------|
| Tool/method queries | Same as topic match, query TOOL# partitions |
| Intersection (topic + tool) | Query each partition, intersect faculty sets in application code |
| Similarity search ("someone like Dr. X") | Load faculty topic vectors from GSI, compute cosine similarity in-memory (~2,500 vectors, trivial) |
| Network/co-authorship | Query ReciterDB for shared pmids between faculty |
| Canned deep dives | Direct DynamoDB lookup of DEEPDIVE# records |
| Evaluation/feedback | Write EVAL# records to DynamoDB after each response |

### Comprehensive Query Taxonomy

**Recommendation queries** — matching people to opportunities:
- "Who would be strong candidates for an R01 on Alzheimer's biomarkers?"
- "I need nominees for the Lasker Award in basic science"
- "Who would be a good external reviewer for a cardiovascular imaging grant?"

**Exploration queries** — understanding the landscape:
- "What does WCM's research in AI and machine learning look like?"
- "How deep is our bench in health equity research?"
- "Which departments are most active in cancer genomics?"

**Person-centric queries:**
- "What has Dr. Smith been working on recently?"
- "Is Dr. Chen a good fit for this NIH program officer's call on gut microbiome?"
- "Compare Dr. Smith and Dr. Chen's work in neurodegeneration"

**Publication report queries:**
- "Show me Dr. Smith's publications from 2020-2024"
- "How many publications did WCM produce last year in Nature, Science, and Cell?"
- "Department of Medicine first-author output in 2025"

**Nomination package queries:**
- "Draft a summary of Dr. Chen's contributions to aging research"
- "What are Dr. Smith's three most impactful publications in the last 5 years?"
- "Bullet points on Dr. Patel's qualifications for an early career award"

**Team assembly queries:**
- "Build a team for a P01 on cardiovascular disease — basic scientist, clinician, population data"
- "Who would complement Dr. Smith on an aging grant? She covers the clinical side"
- "Natural collaborators for a gut microbiome study across departments"

**Gap/strategic queries:**
- "Are there NCI priority areas where we have no meaningful representation?"
- "Which of our departments has the thinnest research profile?"
- "Where are we thin in areas that matter for NIH funding trends?"

**Tool/method queries:**
- "Who uses CRISPR in their research?"
- "Who has experience with large-scale population cohort studies?"
- "Who combines machine learning with clinical data?"

**Similarity queries:**
- "Find me someone like Dr. Smith but more junior"
- "Who has a similar research profile to Dr. Chen?"

**Network/collaboration queries:**
- "Who has Dr. Smith actually collaborated with before?"
- "Are there existing collaborations between cardiology and biomedical informatics?"
- "Who bridges basic science and clinical research?"

**Deep dive queries:**
- "Tell me about WCM's aging research"
- "What are the subtopics within our oncology portfolio?"

### Conversational Follow-Ups

The system must handle mid-conversation refinements:
- "Narrow that to Department of Medicine" — filter the in-memory candidate set from the previous turn (no re-retrieval from DynamoDB). The API route caches the last retrieval result for this purpose.
- "Anyone more junior?" — constraint on existing candidates, filtered in-memory using bibliometric data (h-index, career stage)
- "Tell me more about the second person" — drill into a specific result from the cached candidate set
- "Why did you include Dr. Jones?" — explanation retrieval, re-synthesize with emphasis on that candidate's evidence
- "Can you rank differently — I care more about recent work" — re-ranking the cached candidate set with different weighting
- "Start over" — session reset, clear cached candidates, return to guided entry
- Turn limit: suggest new query after 6-7 turns
- Context management: each turn, pass the prior turn's *synthesized answer* (~600 tokens) to the next synthesis call — NOT the full candidate list. However, the raw candidate set is cached server-side so follow-ups can filter/re-rank without re-retrieval.

---

## 7. Router Design

### Input
User message + conversation history + active mode (if guided entry was used)

### Output (Structured JSON from Bedrock Haiku)
```json
{
  "query_type": "topic_match | person_centric | publication_report | nomination | gap_query | team_assembly | similarity | network | deep_dive",
  "topics": [
    { "id": "aging_geroscience", "confidence": 0.9, "match_type": "direct" },
    { "id": "neurodegeneration", "confidence": 0.7, "match_type": "adjacent" }
  ],
  "tools": [
    { "id": "longitudinal_cohort", "confidence": 0.8 }
  ],
  "faculty_targets": ["cwid_jsmith"],  // populated only for person-centric, nomination, similarity, and network queries; empty array for topic-match/exploration/report/gap queries
  "roles": [  // populated only for team_assembly queries; Haiku dynamically decomposes the user's request into roles based on the grant type and stated needs
    { "label": "basic scientist", "topics": ["cardiovascular_biology"], "tools": [] }
  ],
  "filters": {
    "department": null,
    "year_min": null,
    "year_max": null,
    "author_position": null
  },
  "answerability": "high | medium | low",
  "concern": null,
  "residual": "specificity the topic layer cannot capture"
}
```

### Threshold Tuning

| Match type | Score threshold |
|------------|---------------|
| Direct topic | 0.6+ |
| Adjacent topic | 0.4+ |
| Tool match | 0.5+ |

### Answerability Gate

If answerability is low, stream back a clarifying question immediately. No retrieval, no synthesis cost.

If answerability is medium, proceed but hedge the synthesis prompt ("results may be limited").

---

## 8. Synthesis Design

### Model
Bedrock Sonnet, streaming enabled.

### System Prompt Includes
- Query mode and expected output format
- Residual specificity from the router
- Smart defaults applied (academic articles, WCM full-time, first/last author)
- Instruction: "If the candidate set does not contain faculty with strong relevant expertise, say so directly. Do not surface weak matches as if they were good ones."

### Input Context
- Original question
- Candidate faculty with scored activities and synopses
- Faculty metadata (department, h-index, title, article counts)
- Co-authorship data when relevant (team assembly, network queries)
- Prior conversation turns (synthesized answers only, not raw candidates)

### Output Format by Mode

**Recommendation/Topic match:** Ranked list with rationale per faculty member
**Exploration:** Summary narrative + faculty list organized by subtopic
**Person-centric:** Profile narrative with evidence
**Publication report:** Structured data with counts, lists, and context. Smart defaults confirmed in header.
**Nomination:** Prose suitable for a nomination letter
**Team assembly:** Composed team with role assignments, rationale for each member, alternatives, and why the combination works
**Gap query:** Topic coverage map with thin areas highlighted
**Deep dive:** Retrieved pre-computed deep dive content, framed conversationally

### Token Budget
- Input: ~10,000-12,000 tokens (candidates + system prompt + query + prior turns)
- Output: ~600-1,000 tokens
- Per query cost: ~$0.04-0.05

### Conversation Cost Management
- 5-turn conversation: ~$0.15-0.20 with context pruning
- Turn limit at 6-7 turns: suggest starting a new query
- Pass prior synthesized answers (~600 tokens each) to LLM context — NOT raw candidate lists. Raw candidates cached server-side for follow-up filtering/re-ranking.
- If follow-up doesn't change the topic, reuse cached candidate set rather than re-retrieving from DynamoDB

---

## 9. Smart Defaults and Filter Confirmation

Every query applies default filters unless the user explicitly overrides:
- Academic articles only
- WCM full-time faculty only
- First or last author only

The response always surfaces active defaults in a visible badge/header:

> **Applied filters:** Academic articles | WCM full-time | First/last author

Users can modify defaults conversationally ("include all author positions") or via a clickable filter badge.

When the user asks a report-style question without specifying filters, the system confirms what it assumed:

> "For this report I'm using: academic articles only, WCM full-time faculty, first or last author, 2025. Want me to adjust any of these?"

### Filter Responsibility

Defaults are applied at the **retrieval layer**, not the router or synthesis:
- The **router** detects any explicit filter overrides in the user's message and includes them in its output
- The **retrieval layer** merges explicit overrides with defaults, then applies them to DynamoDB/ReciterDB queries
- The **synthesis layer** receives the active filter set and includes it in the response header for transparency
- The **UI** renders the filter badge from the active filter set returned with each response

---

## 10. Guardrails and Policy

### Refuse
- HR-adjacent queries: "who hasn't published in 2 years", tenure status, availability
- Queries about specific individuals' personal circumstances

### Reframe
- Overly broad: "who's the best researcher" -> "Best in what domain, for what purpose?"
- Ambiguous scope: "who publishes the most" -> "In what area? Across all of WCM?" (answerable but should prompt for domain context before returning raw volume numbers)
- Ranking by a single metric: "who has the best h-index" -> "H-index varies by field. In what area are you looking for strong researchers?"

### Scope Transparency
- When a query touches out-of-scope data: "This version covers publications. Grant data is planned for a future release."
- When a topic isn't in the taxonomy: "This isn't a tracked research domain yet. I can search publication titles and abstracts, but results may be less precise."

### Faculty Engagement Caveat
A faculty member who hasn't logged into Publication Manager recently may have a thinner profile even if they're actively publishing — their newer publications may not be asserted. The synthesis prompt must include: when surfacing a candidate with few attributed publications relative to their career stage, flag "profile may be incomplete — recent publications may not yet be attributed" rather than implying thin research activity.

### Hedge on Bibliometrics
- Surface h-index as context within a recommendation, not as a ranking criterion
- "Dr. Smith (h-index: 45) has strong depth in..." not "Here are the top 10 by h-index"

### Null Result Handling
- Zero candidates: "WCM doesn't appear to have strong expertise in [topic] based on recent publications. You might consider: broadening to [adjacent areas]..."
- Weak candidates: synthesis prompt instructed to say so directly, suggest adjacent areas
- Never dead-end the user: always offer a next step

### Decision-Support Framing
All outputs must be framed as decision-support, not authoritative ranking. This is especially important for gap queries and department-level reports, which could land awkwardly with a department chair in the room. The synthesis prompt includes: "Present findings as informational input for decision-makers, not definitive judgments about departments or individuals."

---

## 11. Chat UI Design

### Page: `/chat` in Publication Manager

Follows PM's existing design system (Bootstrap 5, CSS modules). No new UI library.

### Initial State: Guided Entry

```
Research Intelligence Assistant

What would you like to do?

[ Find people for an opportunity      ]
[ Explore expertise in an area        ]
[ Look up a faculty member            ]
[ Publication report                  ]
[ Research landscape deep dive        ]
[ Ask something else                  ]

Try: "Who works on aging research?"  [input field]
```

Selecting a mode sets system prompt context, shows relevant suggestion chips:

| Mode | Chips |
|------|-------|
| Find people | "for an R01 in...", "for an award in...", "as a reviewer for..." |
| Explore expertise | "in aging", "in oncology", "in AI/ML", "by department" |
| Look up faculty | Type-ahead search by name |
| Publication report | "Dept of Medicine 2024", "Dr. Smith's biosketch", "top journals" |
| Deep dive | List of available domains |
| Ask something else | Empty text box |

### Conversation State

```
You: Who would be strong candidates for an R01 on
     Alzheimer's biomarkers?

[Applied filters: Academic articles | WCM full-time | First/last author]
Assistant: Based on WCM's publication record, here are
the strongest candidates:

1. **Dr. Jane Smith** — Neurology
   H-index: 45 · 12 relevant publications
   Strongest match: longitudinal study of amyloid-beta
   biomarkers in preclinical Alzheimer's...

2. **Dr. Robert Chen** — Pathology & Cell Biology
   H-index: 38 · 8 relevant publications
   Key work: tau PET imaging validation study...

[Narrow to Dept of Medicine]  [Anyone more junior?]
[Compare top two]             [Draft nomination for #1]
```

### Key UI Behaviors

- **Filter badge** always visible — shows defaults applied, clickable to modify
- **Streaming** — response appears word-by-word via SSE
- **Follow-up chips** — contextual suggestions based on the response type
- **Turn counter** — subtle indicator; after 6 turns, suggest starting a new query
- **Faculty names link** to their PM profile page (existing routes)
- **"Start over" button** — always accessible, resets to guided entry
- **Filter badge is interactive** — each default filter (Academic articles | WCM full-time | First/last author) is a toggleable pill. Clicking one opens a dropdown to change it (e.g., "All author positions" instead of "First/last author"). Changing a filter re-runs the current query with the updated filter. Users can also modify filters conversationally ("include middle authors too").

### Evaluation UI (Inline)

After each response, a subtle feedback prompt:

```
Was this helpful?  [thumbs up] [thumbs down]

[Rate accuracy: 1-5 stars]
[What could be better?  text field]
[Tags: Right people | Wrong people | Weak rationale |
        Missing someone | Strong rationale | Complete]
```

Feedback writes EVAL# records to DynamoDB. Lightweight enough that users actually use it; structured enough for quantitative analysis.

---

## 12. Team Assembly — Detailed Flow

Team assembly is a distinct retrieval pattern worth detailing because it's a potential demo highlight.

### Router Decomposition

The router breaks a team query into roles with topic/tool requirements:

```json
{
  "query_type": "team_assembly",
  "roles": [
    {
      "label": "basic scientist",
      "topics": ["cardiovascular_biology", "vascular_biology"],
      "tools": [],
      "constraints": {}
    },
    {
      "label": "clinician",
      "topics": ["cardiovascular_disease", "clinical_cardiology"],
      "tools": ["clinical_trials"],
      "constraints": {}
    },
    {
      "label": "population data",
      "topics": ["epidemiology", "cardiovascular_disease"],
      "tools": ["cohort_studies", "biostatistics"],
      "constraints": {}
    }
  ],
  "team_constraints": {
    "cross_departmental": true,
    "grant_type": "P01"
  }
}
```

### Per-Role Retrieval

1. Query each role's topic/tool partitions from DynamoDB
2. Aggregate to faculty level: for each faculty member within a role, take the **single highest-scoring publication per topic**, then average across the role's topics. This gives a per-faculty "role fit" score that rewards depth (high single-paper score) across the role's topic coverage.
3. Enrich with bibliometrics from ReciterDB (`analysis_summary_person`): h-index, career stage, article counts, first/last author counts
4. Top 5 candidates per role

### Diversity-Aware Selection

- If a faculty member ranks highly for multiple roles, assign to strongest and backfill
- Prefer cross-departmental spread when `cross_departmental: true`
- Check co-authorship data — existing collaborations are a positive signal for team viability
- Pass all role-candidate sets to synthesis together

### Synthesis Output

```
Suggested team for a P01 on Cardiovascular Disease:

Basic Science Lead — Dr. Jane Smith (Cell Biology, h-index: 52)
8 publications on vascular endothelial signaling...

Clinical Lead — Dr. Robert Chen (Cardiology, h-index: 38)
Active in clinical trials for heart failure therapeutics...

Population/Data — Dr. Maria Patel (Epidemiology, h-index: 41)
Leads cohort work in cardiovascular risk modeling...

Why this combination works: Covers bench-to-bedside, three
departments, complementary methods. Smith and Chen have
co-authored once (2022, JAHA).

Alternatives: For basic science, Dr. Lee (Physiology) is a
close second. For population data, Dr. Nguyen brings genomic
epidemiology if you want an -omics angle.
```

---

## 13. Evaluation Strategy

### Two Axes
1. **Rating**: 1-5 stars
2. **Narrative**: short freeform description

### Two Evaluator Types

| Source | Best for | Limitation |
|--------|----------|------------|
| Research deans / grants officers | Strategic utility — was this useful? | May not know individual faculty deeply |
| Faculty (self-evaluation) | Accuracy — did the system represent my work correctly? | Self-interest; can't evaluate others |

### Structured Feedback Tags

```
What made it good or not? (pick all that apply)
[ ] Right people, missed some
[ ] Wrong people surfaced
[ ] Good people but weak rationale
[ ] Missing someone obvious
[ ] Rationale was strong
[ ] Correct and complete
```

### Benchmark Query Set

Build 20-30 canonical queries with known-good answers before launch. Run this set:
- Before and after any taxonomy version change
- Before and after model updates
- Monthly to track drift

Target: ratings trending toward 4+ on grant recommendation queries.

### Feedback Loop

Low accuracy ratings trigger investigation:
- Faculty rates aging_geroscience accuracy 2/5
- Note: "missing 2023 Nature Aging paper"
- Check: is that paper attributed and asserted in the system?
- Faculty become QA reviewers for their own profiles

### Where Faculty Self-Evaluation Surfaces

Faculty accuracy evaluation appears in Publication Manager on the faculty member's own profile page — a lightweight prompt when they're reviewing their attributed publications. This is the natural touchpoint: faculty are already in PM asserting/rejecting publications, so a "Does this reflect your expertise?" prompt fits the existing workflow. The chatbot's inline evaluation (thumbs up/down + stars) captures strategic utility from deans and grants officers; the profile-page evaluation captures accuracy from the faculty themselves.

---

## 14. Capability Roadmap

### Demo-Ready (~1 week)

The demo script (Section 17) requires these capabilities. This is what ships in week 1.

| Capability | Backend | Demo query |
|------------|---------|------------|
| Topic/expertise matching | DynamoDB | "Who works on aging?" |
| Person-centric lookup | DynamoDB + ReciterDB | (follow-up: "Tell me more about #2") |
| Publication reports | DynamoDB + ReciterDB | "Dept of Medicine 2024 output" |
| Team assembly | DynamoDB + ReciterDB | "Build a P01 team for cardiovascular" |
| Gap/inverse queries | DynamoDB | "Where are we thin?" |
| Canned deep dives (aging domain) | DynamoDB | "Tell me about WCM's aging research" |
| Guided entry + freeform | — | All demo queries |
| Smart defaults + filter confirmation | — | Publication report demo |
| Conversational follow-ups | — | Follow-ups after each demo query |
| Unanswerable detection + guardrails | — | Tested but not demoed |
| Evaluation/feedback capture | DynamoDB | Shown after a response |

### Full v1 (~3 weeks)

Everything above plus these capabilities, which are architecturally designed but not required for the demo:

| Capability | Backend |
|------------|---------|
| Tool/method queries | DynamoDB |
| Intersection queries (topic + tool) | DynamoDB |
| Nomination package synthesis | DynamoDB + ReciterDB |
| Network/co-authorship | ReciterDB |
| Similarity search ("someone like Dr. X") | DynamoDB (computed at query time) |
| Canned deep dives (all domains) | DynamoDB + ReciterAI pipeline |
| Interactive filter badge (toggle pills) | — |

### v2 Roadmap

| Capability | Backend | Notes |
|------------|---------|-------|
| Grant history & funding | NIH Reporter integration + DynamoDB | Upgrades team assembly, nomination, person-centric |
| Competitive/peer comparison | External data source TBD (OpenAlex?) | Needs scoping |
| Opportunity-first / RFA matching | RFA ingestion pipeline + existing DynamoDB | Workflow trigger |

### Capabilities That Improve Significantly With Grants Data (v2)

- **Team assembly** — PI/co-I history, funded collaborations, R01 renewal track record
- **Network queries** — co-investigator relationships stronger signal than co-authorship
- **Nomination packages** — "Dr. Chen has 3 active R01s, $4.2M in NIH funding"
- **Person-centric** — "Is Dr. Smith a good fit for this R01?" with actual grant history

---

## 15. Cost Summary

### Pre-Computation (One-Time)

| Item | Cost |
|------|------|
| Taxonomy generation | ~$1-2 |
| Screening (Haiku, 10K pubs) | ~$5 |
| Dense scoring (Sonnet, ~3.5K pubs) | ~$30 |
| **Total initial build** | **~$35** |

### Per Query (Runtime)

| Step | Model | Cost |
|------|-------|------|
| Router | Bedrock Haiku | ~$0.001 |
| DynamoDB reads | — | ~$0 |
| ReciterDB query (if needed) | — | ~$0 |
| Synthesis | Bedrock Sonnet | ~$0.04-0.05 |
| **Total per query** | | **~$0.05** |

### Conversation (5 turns)
- Without pruning: ~$0.30-0.35
- With context pruning: ~$0.15-0.20

### Incremental (Per New Publication)
- Screen: ~$0.0005
- Dense score: ~$0.008
- **Per new publication: ~$0.01**

### Monthly Operating (Estimated)
- Demo usage (50 conversations): ~$5/month
- Production (1,000 conversations): ~$300/month

---

## 16. Critical Path and Timeline

### Day 1: Taxonomy + Start Scoring
- Generate topic taxonomy from publication abstracts via Bedrock
- Curate and validate against sample queries
- Start the scoring pipeline (runs 2-4 hours in background)
- Create DynamoDB table

### Days 1-3: Data Pipeline
- Build extract script (ReciterDB -> publications dataset)
- Build DynamoDB load script
- Load scored results + faculty profiles + processing records
- Load existing canned deep dive (aging domain)

### Days 2-5: Chat Runtime in PM
- Add AWS Bedrock + DynamoDB clients to PM
- Build `/api/chat` route with router -> retrieval -> synthesis flow
- Implement the six retrieval patterns
- Build ReciterDB query layer for publication reports and bibliometrics
- Implement streaming SSE responses

### Days 3-6: Chat UI
- Build `/chat` page with guided entry
- Suggestion chips and mode selection
- Streaming response display
- Filter badge
- Follow-up chips
- Evaluation UI (inline feedback)

### Days 6-7: Polish and Demo Prep
- Test with benchmark queries
- Refine router and synthesis prompts
- Polish UI for demo (loading states, error handling, transitions)
- Prepare demo script: 3-5 queries that showcase different capabilities
- Test team assembly and gap queries specifically (high demo value)

### Parallelizable Work
- Scoring pipeline (Day 1) runs in background while app is being built
- DynamoDB schema creation can happen while scoring runs
- UI work can proceed with mock data while API routes are built

---

## 17. Demo Script (Suggested)

Five queries that showcase the range of capabilities:

1. **Recommendation**: "Who would be strong candidates for an R01 on Alzheimer's biomarkers?"
   — Shows ranked faculty with rationale, filter badge, follow-up chips

2. **Team assembly**: "Help me build a team for a P01 on cardiovascular disease — I need a basic scientist, a clinician, and someone with population data"
   — Shows composed team with diversity, co-authorship signal, alternatives

3. **Deep dive**: "Tell me about WCM's aging research"
   — Shows pre-computed landscape overview with subtopics

4. **Publication report**: "How many publications did the Department of Medicine produce last year?"
   — Shows smart defaults, structured data, filter confirmation

5. **Gap query**: "Are there areas where we have one or two people but no real depth?"
   — Shows strategic intelligence, topic coverage map

Follow each with a conversational follow-up to demonstrate multi-turn capability.

**Framing note:** When presenting the demo, frame the system as decision-support — "here are candidates worth considering" — not authoritative ranking. This is especially important for query #5 (gap query), which could land awkwardly if a department chair is in the room. The system surfaces information; the humans make the decisions.

---

*This design spec was generated from a collaborative brainstorming session. All cost estimates are based on current AWS Bedrock pricing and should be validated against actual usage.*
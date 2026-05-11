
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
2. Aggregate to faculty level (max score across activities per topic)
3. Enrich with bibliometrics from ReciterDB (`analysis_summary_person`): h-index, career stage, article counts
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

---

## 14. v1 Capability Summary

| Capability | Backend | Status |
|------------|---------|--------|
| Topic/expertise matching | DynamoDB | v1 |
| Tool/method queries | DynamoDB | v1 |
| Intersection queries (topic + tool) | DynamoDB | v1 |
| Person-centric lookup | DynamoDB + ReciterDB | v1 |
| Nomination package synthesis | DynamoDB + ReciterDB | v1 |
| Publication reports | DynamoDB + ReciterDB | v1 |
| Gap/inverse queries | DynamoDB | v1 |
| Team assembly | DynamoDB + ReciterDB | v1 |
| Network/co-authorship | ReciterDB | v1 |
| Canned deep dives | DynamoDB + ReciterAI pipeline | v1 |
| Evaluation/feedback capture | DynamoDB | v1 |
| Similarity search ("someone like Dr. X") | DynamoDB (computed at query time) | v1 |
| Guided entry + freeform | — | v1 |
| Smart defaults + filter confirmation | — | v1 |
| Conversational follow-ups | — | v1 |
| Unanswerable detection + guardrails | — | v1 |

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

---

*This design spec was generated from a collaborative brainstorming session. All cost estimates are based on current AWS Bedrock pricing and should be validated against actual usage.*
ENDOFAPPEND
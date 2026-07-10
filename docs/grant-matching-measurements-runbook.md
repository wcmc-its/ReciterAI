# Runbook — grant-matcher measurements (2026-07-09 baselines)

How the three 2026-07-09 grant-matcher measurements were produced, their baseline
numbers, and how to reproduce each one. All three are **read-only** against live data
(SELECT/GET/Scan only). The generated datasets are point-in-time snapshots of grant
text and — for the back-test — real awardee names/cwids; they stay outside this repo
(see "Data hygiene" at the end).

| # | Measurement | Question | Code lives |
|---|---|---|---|
| 1 | Exposure concentration | Do a few senior researchers dominate the reverse matcher's top-8 lists? | SPS `scripts/measure-researcher-concentration.ts` (branch `feat/grant-measurement-scripts`) |
| 2 | Eligibility capture audit | How much of the eligibility prose do the SPS regex flags actually capture? | `scripts/eligibility_audit/` (this repo) |
| 3 | Awardee back-test | Would the matcher have surfaced the person who actually won? | ground-truth dataset build (method below) + `pipeline_grants/grant_eval.py` |

---

## 1. Exposure concentration (reverse matcher)

**What it measures.** On the SPS `/edit/find-researchers` admin surface, whether a
handful of (senior) researchers dominate the top-8 recommendation lists across all
opportunities, or whether up-and-comers surface. Concentration is reported as coverage
(distinct slot-holders / eligible faculty), Gini over slot counts, top-1% / top-decile
slot shares, and rank/ESI crosstabs.

**Where the code lives.** Scholars-Profile-System
`scripts/measure-researcher-concentration.ts` on branch `feat/grant-measurement-scripts`;
tracking issue: <https://github.com/wcmc-its/Scholars-Profile-System/issues/1611>.

**Run recipe (prose).** One one-off read-only ECS `run-task` of `sps-etl-staging`
(cluster `sps-cluster-staging`), network config lifted live from the
`scholars-nightly-staging` Step Function definition, script staged via S3 presigned GET
and executed in-container with `npx tsx`. The script imports
`rankResearchersForOpportunity` from `@/lib/api/match-researchers` — i.e. the ranking
logic **is** the deployed admin surface, not a reimplementation — and calls it for
**all** indexed opportunities (no sampling) with the admin route defaults
(`sort:"fit"`, `stageLens` off, `esiOnly` off, `limit: 8`). Matcher flags are
replicated from the live staging app task def (`GRANT_MATCHER_SUBTOPIC_GRAIN=on`,
dense-rel on, IP signal off) and echoed by the task's own START line as proof.
Eligible-faculty denominator = the matcher's own candidate gate (active, non-deleted,
`full_time_faculty`). ESI is derived per scholar via the deployed `deriveGrantSignals`.
Aggregates are computed in-task **and** recomputed locally from the raw per-opportunity
ROW lines — they must match exactly. 2026-07-09 run: 1,151 opportunities, 13m12s, exit
0, zero per-opportunity errors, one ECS task total.

**2026-07-09 staging baseline** (default admin view; top-8 window):

| Metric | Value |
|---|---|
| Opportunities ranked / with results | 1,151 / 1,061 (90 empty, fail-closed/off-domain) |
| Distinct slot-holders (coverage) | 575 = **24.1%** of 2,390 eligible FT faculty |
| Gini (all eligible faculty, zeros included) | **0.923** |
| Top 1% of faculty (24) — share of slots | **29.5%** |
| Top decile of faculty (239) — share of slots | **90.6%** |
| Professors — share of slots vs share of faculty | **61.7%** vs 15.8% |
| ESI-eligible — slot share vs faculty baseline | **5.7%** vs 21.5% (~3.8x under-represented) |

Path split worth re-checking on any rerun: the subtopic-grain path (n=998) is markedly
*less* concentrated (top-1% share 28.3%) than the legacy topicVector fallback (n=63,
top-1% share 74.2%, only 90 distinct holders). Caveat: measured with staging flags —
prod holds `match_dsl=0`, so prod today would behave like the more-concentrated legacy
path.

---

## 2. Eligibility capture audit

**What it measures.** The SPS ETL derives UI eligibility flags from `eligibility_raw`
prose via regexes (`deriveEligibilityFlags` in SPS
`etl/dynamodb/grant-opportunity-mapper.ts`). This audit measures how much of the prose
those regexes capture, against a Sonnet reference extraction. Tracking issues: #290
(structured eligibility capture), #293 (off-domain gating context).

**Reproduce:** `scripts/eligibility_audit/` — three scripts, run in order
(scan → extract → score), fixed seed `20260709`, ~100 Bedrock Sonnet calls, outputs to
a gitignored `out/`. Full recipe + cost in that directory's README.

**Sampling design.** Full scan of `GRANT#`/`META` items in the `reciterai` table
(us-east-1); 100 items stratified by `source`, preferring items with non-empty
`eligibility_raw`, seeded RNG. Reference labels: `us.anthropic.claude-sonnet-4-6`
(Bedrock Converse, temperature 0.0, one call per item), extracting org types, career
stages, degrees, citizenship, ESI targeting, limited submission, cost sharing, and
individual-vs-institutional — with explicit "not stated" values required (no guessing).

**2026-07-09 results.** Corpus: 1,122 `GRANT#` META items (grants_gov 634, wcm_curated
374, spin 113, manual_url 1), 1,050 with eligibility prose. Headline canary: for
**97.2%** of prose-bearing items **zero regexes fired** — the prose contributes nothing
and every such item gets the identical default
`["us_eligible", "faculty_eligible", "postdoc_eligible"]`.

Per-facet agreement, regex flags vs reference extraction (n=100):

| Facet (SPS flag) | Agreement | Dominant failure mode |
|---|---:|---|
| `us_eligible` | 100% | Vacuous — flag is default-true and fired 0 times corpus-wide; 22/100 items explicitly say foreign orgs are *ineligible*, a direction the flag can't represent |
| `student_only` | 94% | Misses "enrolled in master's or Ph.D. programs" phrasing; false-positives on any "predoctoral"/"dissertation" mention (e.g. F99/K00 transition awards) |
| `faculty_eligible` | 88% | Postdoc-only / trainee-only fellowships shown as faculty-eligible — no postdoc-only concept exists |
| `postdoc_eligible` | **68%** | Worst facet. Default-true; `faculty_only` patterns almost never fire on real phrasing ("Junior Faculty", K-award language) |
| `internal_limited_submission` | 86% | Misses NIH "Limited Competition:", "Only one application is allowed per institution", "limited to submitting 1 nominee" variants |

What the prose contains that the flag layer cannot represent at all (prevalence in the
100-item sample):

| Facet (no regex counterpart) | Items |
|---|---:|
| Applicant org types restricted (≠ unrestricted) | **61** |
| Individual award (fellowship / career development — award follows a person) | **46** |
| Citizenship / foreign-eligibility explicitly stated | **42** |
| Degree requirement stated | 18 |
| ESI / new-investigator targeted | 13 |
| Small-business only / government only / cost sharing required | 1 each |

### Proposed structured schema

One `eligibility` map attribute on the `GRANT#` item, emitted by the ingest judge
(below) and persisted by `pipeline_grants/persist.py` alongside `eligibility_raw`
(which stays, as evidence). The SPS mapper then *reads* this map and derives its UI
flags from structured fields — the prose regexes retire to a fallback for items that
predate backfill. No speculative fields: every field occurred in the sample and
extracted reliably.

```jsonc
"eligibility": {
  // Who may APPLY (the entity submitting). Empty list = prose doesn't say.
  // "unrestricted" alone = prose explicitly says anyone may apply.
  "applicant_org_types": ["higher_ed", "nonprofit", "for_profit", "small_business",
                          "state_government", "local_government", "tribal_government",
                          "federal_agency", "hospital", "foreign_org", "individual",
                          "other", "unrestricted"],   // subset

  // Career stage(s) the funded PERSON must be in. Empty = no person-level restriction.
  "career_stages": ["undergraduate", "graduate_student", "postdoc",
                    "early_career_faculty", "mid_career_faculty", "senior_faculty",
                    "any_faculty", "clinician"],      // subset

  // Degree(s) the PI/candidate must hold. Empty = none stated.
  "degree_required": ["phd", "md", "md_or_phd_either", "other_doctoral",
                      "nursing_degree", "other_clinical_doctorate"],  // subset

  // One enum value; "not_stated" is the honest default.
  "citizenship_requirement": "us_citizen_or_permanent_resident_required"
                           | "visa_holders_eligible"
                           | "foreign_institutions_eligible"
                           | "foreign_institutions_ineligible"
                           | "not_stated",

  "esi_targeted": false,           // ESI/new-investigator targeted or restricted
  "limited_submission": false,     // institution capped at N applications / internal competition
  "cost_sharing_required": false,  // required (not "encouraged")
  "individual_award": false,       // award follows a named person (fellowship/K-style)

  // Provenance — required so stale extractions are detectable and re-runnable.
  "extracted_by": "us.anthropic.claude-sonnet-4-6",
  "extracted_at": "2026-07-09T00:00:00Z"
}
```

Deliberate exclusions:

- **No `small_business_only` / `government_only` booleans.** Both derivable from
  `applicant_org_types`; persisting them twice invites drift. (The audit carried them
  as separate extraction fields only to measure prevalence — 1 each.)
- **No `us_eligible` equivalent.** Replaced by `citizenship_requirement` + `foreign_org`
  in org types, which represent all four directions the prose actually takes (the old
  flag represents one, and fired zero times).
- **No award-amount / deadline fields** — already first-class attributes on the item.

SPS derivation from the structured map (replaces the prose regexes):

- `student_only` ⇔ `career_stages` non-empty and ⊆ {undergraduate, graduate_student}
- `faculty_eligible` ⇔ `career_stages` empty or intersects {early/mid/senior/any_faculty, clinician}
- `postdoc_eligible` ⇔ `career_stages` empty or contains postdoc
- `internal_limited_submission` ⇔ `limited_submission`
- plus new facets the UI can filter on: org-type, citizenship, degree, ESI,
  individual-vs-institutional.

Post-rollout canary (SPS `opportunity` table) — should COLLAPSE from the 97.2% baseline:

```sql
SELECT COUNT(*) FROM opportunity
WHERE JSON_LENGTH(eligibilityFlags) = 3
  AND JSON_CONTAINS(eligibilityFlags, '"us_eligible"')
  AND JSON_CONTAINS(eligibilityFlags, '"faculty_eligible"')
  AND JSON_CONTAINS(eligibilityFlags, '"postdoc_eligible"')
  AND eligibilityRaw <> '';
```

### Draft replacement judge prompt (`pipeline_grants/denoise.py`)

Today `judge_opportunity` makes one `call_json` on `HAIKU_MODEL` emitting
`{is_research, reason, appeal_by_stage}`. The extension emits the eligibility block
from the **same call** (same input fields — title/sponsor/ceiling/eligibility/synopsis
— no new plumbing). Two integration gates:

1. **Model parity (pre-ship gate).** The audit validated extraction quality on
   `SONNET_MODEL`, not the judge's current `HAIKU_MODEL`. Either move the judge call to
   Sonnet (~1,122 items corpus-wide + daily deltas; well under the 2-Sonnet-calls-per-
   opportunity `--compile-match` path that already exists), or run a Haiku-vs-Sonnet
   parity check on the audit's 100-item sample **before** shipping Haiku.
2. **Fail-open.** Persist `eligibility` only when the block parses and every enum value
   validates; on failure persist nothing (SPS falls back to the legacy regexes).
   Mirrors the `match_attrs` fail-closed contract on `match_dsl`/`match_query`.

Proposed `_JUDGE_SYSTEM` replacement:

```python
_JUDGE_SYSTEM = (
    "You triage research funding opportunities for a medical college. "
    "Decide if an opportunity is a substantive research opportunity (not a travel/conference/"
    "prize/equipment award), rate how appealing it is to each career stage, and extract "
    "structured eligibility facts FROM THE ELIGIBILITY TEXT (title/synopsis are context only; "
    "when the text does not state a fact, use the empty list or 'not_stated' — never guess). "
    "Respond ONLY with JSON:\n"
    "{\n"
    '  "is_research": bool,\n'
    '  "reason": str,\n'
    '  "appeal_by_stage": {"grad": 0-1, "postdoc": 0-1, "early": 0-1, "mid": 0-1, "senior": 0-1},\n'
    '  "eligibility": {\n'
    '    "applicant_org_types": [subset of: "higher_ed","nonprofit","for_profit","small_business",'
    '"state_government","local_government","tribal_government","federal_agency","hospital",'
    '"foreign_org","individual","other","unrestricted"],  // who may APPLY; [] = not described;'
    ' ["unrestricted"] = explicitly open to all\n'
    '    "career_stages": [subset of: "undergraduate","graduate_student","postdoc",'
    '"early_career_faculty","mid_career_faculty","senior_faculty","any_faculty","clinician"],'
    '  // stages the funded PERSON must be in; [] = no person-level restriction\n'
    '    "degree_required": [subset of: "phd","md","md_or_phd_either","other_doctoral",'
    '"nursing_degree","other_clinical_doctorate"],  // [] = none stated\n'
    '    "citizenship_requirement": "us_citizen_or_permanent_resident_required" | '
    '"visa_holders_eligible" | "foreign_institutions_eligible" | '
    '"foreign_institutions_ineligible" | "not_stated",\n'
    '    "esi_targeted": bool,        // ESI/new investigators explicitly targeted or required\n'
    '    "limited_submission": bool,  // institution capped at N applications / internal competition\n'
    '    "cost_sharing_required": bool,  // required, not merely encouraged\n'
    '    "individual_award": bool     // award follows a named person (fellowship/career award)\n'
    "  }\n"
    "}\n"
    "Small awards should score high for trainees and low for senior PIs. "
    "A stated PRIORITY for a group (e.g. ESI) is esi_targeted=true but is NOT a career_stages "
    "restriction; only hard requirements restrict career_stages."
)
```

`judge_opportunity` post-processing contract:

- Validate `eligibility.*` against the enum sets above; drop the whole block on any
  violation.
- Dedupe + sort the list fields for byte-stable persistence.
- Add `extracted_by` (the model id actually used) and `extracted_at` (`now_iso()`) at
  persist time, not in the prompt.
- Persist as a native DynamoDB `M` on the `GRANT#` item (not a compact-JSON string —
  the SPS mapper's `parseJsonAttr` already passes native maps through).
- Backfill path: same pattern as `backfill_prestige.py` / `backfill_match.py` —
  one-shot scan of `GRANT#` items lacking `eligibility`, judge-extract, `UpdateItem`.

**Method caveats.** Reference labels are single-model (Sonnet 4.6, temp 0.0), not
human-adjudicated — agreement is "regex vs strong-LLM", not "regex vs ground truth"
(spot-checks of every disagreement row favored the LLM reading). SPIN items carry
semi-structured pick-list prose ("Junior Faculty; Postdoctoral | United States") the
regexes never match — 10% of the corpus, and the misses are real product-facing errors
either way.

---

## 3. Awardee back-test

**What it measures.** Whether the grant→researcher matcher would have surfaced the
person who actually won an award — real WCM award facts as ground truth for the eval
harness (`pipeline_grants/grant_eval.py`, dump contract in `docs/grant-eval-harness.md`).

### Method chain (how the dataset was built)

1. **Corpus inventory.** Read-only DynamoDB scan of `GRANT#`/`META` items projecting
   `opportunity_id, title, sponsor, source` → sponsor→opportunities map. 2026-07-09:
   1,122 items, 311 distinct sponsor strings, 57 canonical foundation sponsors covering
   178 opportunity rows (Hartwell, Keck, Damon Runyon, Pew, ACS, AHA, BWF, …).
2. **Awardee announcements (web + archive.org).** For foundation programs actually in
   the corpus, search the live web for recent WCM-affiliated awardees (the WCM newsroom
   was the productive source). For foundation award pages, WebFetch-style access is
   blocked for `web.archive.org` — fall back to the **archive.org CDX API + snapshot
   fetch via `curl -4 --compressed`** (`…/cdx/search/cdx?url=<page>&from=2025&to=2026&
   filter=statuscode:200`, then fetch a returned snapshot). Expect thin recent signal:
   archived foundation awardee lists skew pre-2020.
3. **Ground truth from the field of record.** SPS staging grants table via a one-off
   **read-only in-VPC ECS run-task** on `sps-etl-staging` (network config lifted from
   the `scholars-nightly-staging` Step Function; `DATABASE_URL` baked into the task
   def; inline `node -e` with the mariadb driver; SELECT-only). Query: `grant` JOIN
   `scholar`, `start_date >= 2025-01-01`, sponsor fuzzy-matched against ~48
   corpus-foundation LIKE patterns. Keep fuzzy false-positives visible (raw sponsor
   field, null corpus link) rather than silently dropping them.
4. **Join → JSONL rows**, one per (award, person), schema:

   ```
   {sponsor, program_title, corpus_opportunity_id, awardee_name, cwid,
    award_date, evidence_url, evidence_kind, role}
   ```

   `evidence_kind` ∈ {`web`, `archive`, `sps_db`}. `corpus_opportunity_id` is set
   **only** on a confident program-level match — sponsor-level rows stay null, because
   a funded project's title ≠ a corpus solicitation title and every foundation sponsor
   maps to multiple programs; guessing would fabricate the link.

2026-07-09 build (aggregates): 123 rows — 114 `sps_db`, 7 `web`, 2 `archive`; cwid
resolved 121/123; 5 rows corpus-linked. Only 3 rows are simultaneously in-window,
in-corpus, and cwid-resolved — the recent, named, in-corpus awardee signal is thin, so
sponsor-level joins (below, §3b) carry most of the weight.

### Plugging the rows into `grant_eval.py`

The harness scores a **ranking dump** per corpus grant and back-tests it against
awardee cwids; `awardees_for(dump, db)` takes awardees from an explicit
`dump["awardees"]` list (preferred) or a funding-DB title join.

**3a. Program-level (corpus-linked rows) — inject `awardees` on the dump:**

```bash
# group the dataset by corpus opportunity → {opp_id: [cwid,...]}
python3 - <<'PY'
import json, collections
rows=[json.loads(l) for l in open('backtest-awardees.jsonl')]
by=collections.defaultdict(set)
for r in rows:
    if r['corpus_opportunity_id'] and r['cwid']:
        by[r['corpus_opportunity_id']].add(r['cwid'])
json.dump({k:sorted(v) for k,v in by.items()}, open('awardees_by_opp.json','w'), indent=1)
PY
# then, when building each ranking dump, set dump["awardees"] = awardees_by_opp[opp_id]
# and run:  python3 -m pipeline_grants.grant_eval '<DUMPS>/*.json' -k 8 --no-judge
```

**3b. Funding-DB format (all rows, sponsor- or title-level) — `--funding-db`:**
`load_funding_db_awardees(path)` reads a JSON list shaped like the WCM funding-DB
scrape and keys `{normalized_title -> {cwid}}` off `past_recipients[].email` (email
prefix = cwid). Emit that shape with a synthetic email:

```python
import json, collections
rows=[json.loads(l) for l in open('backtest-awardees.jsonl')]
recs=collections.defaultdict(list)
for r in rows:
    if r['cwid']:
        recs[r['program_title']].append({'email': f"{r['cwid']}@med.cornell.edu"})
out=[{'title':t,'past_recipients':pr} for t,pr in recs.items()]
json.dump(out, open('backtest_funding_db.json','w'), indent=1)
# grant_eval.py joins a dump to a record when the dump's solicitation_title
# (normalized) contains, or is contained by, the record title — name the dump's
# solicitation_title to match the program (or the sponsor for a looser check).
```

Then: `python3 -m pipeline_grants.grant_eval '<DUMPS>/*.json' --funding-db
backtest_funding_db.json --no-judge`. `--no-judge` skips the LLM (back-test only, no
Bedrock cost). This path is how the sponsor-level `sps_db` rows become useful:
title-join a sponsor's corpus dump to that sponsor's real WCM awardees and check
whether the ranker's pool surfaces them.

Format contract reminders:

- Dump schema: `{grant, gate, engine, solicitation, ranked:[{cwid,name,rank,fit,
  evidence[]}], awardees?}` (`docs/grant-eval-harness.md`, "ranking-dump contract").
- `backtest_ranks` needs the **full ranked pool** (not top-8) for `percentiles` to mean
  anything — generate dumps via the ECS full-pool path, not the markdown bootstrap.
- Filter the sponsor-fuzz rows (NIH/passthrough rides on broad LIKE patterns; raw
  sponsor, null corpus link) before scoring.

### PII warning

**The generated back-test datasets contain PII** — real awardee names and cwids
(`backtest-awardees.jsonl` and its scratch intermediates). They live under
`~/Dropbox/Projects/` only and are **never committed** to this repo or to
Scholars-Profile-System. Only aggregates (counts, coverage, ranks/percentiles) leave
that directory.

---

## Data hygiene (applies to all three)

- **No generated data files in the repo.** Eligibility-audit outputs (grant-text
  snapshots) land in the gitignored `scripts/eligibility_audit/out/`; back-test rows
  (PII) stay under `~/Dropbox/Projects/`; concentration raw ROW dumps (per-opportunity
  CWID lists) stay local to the SPS-side run.
- **Read-only everywhere.** Every recipe above is Scan/SELECT/GET only; the in-VPC
  tasks reuse existing task definitions and write nothing.
- **AWS account ids are scrubbed** from this doc and the audit scripts; environment is
  identified by name (staging) only.

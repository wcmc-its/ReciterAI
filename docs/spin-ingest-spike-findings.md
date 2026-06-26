# SPIN Ingest Spike — Findings

**Date:** 2026-06-26. **Verdict:** conditional GO — noise is tractable; two real caveats.
Spike code (throwaway): `scratchpad/spin-spike/` (`spin_client.py`, `measure.py`, `probe2.py`).

## API contract (confirmed working)
- **Endpoint:** `GET https://spin.infoedglobal.com/Service/ProgramSearch`
- **Auth:** query params `PublicKey` / `signature` / `InstCode` = our three env vars. **Auth succeeded.**
- **Query:** `keywords` (SOLR syntax supported, e.g. `[SOLR]spon_name:"…"`), `columns`, `pageSize`, `pageNumber`, `responseFormat=JSON`. Response: `{Programs:[…], PageNumber, NumberOfPages}`.
- **Fields:** `id, prog_title, spon_name, sponsor_type, applicant_type, geographic, project_type, project_location, keyword, objective, synopsis, deadline_date, programurl, spon_prog, target, cfda` (+ contact fields). `geographic`/`project_type`/`applicant_type` are **lists**.

## Finding 1 — no award-amount field (the predicted risk, confirmed)
Every candidate amount column (`award_amount`, `funding_amount`, …) was silently dropped; SPIN's `ProgramSearch` exposes none. **A structured size floor is impossible.** Mitigation: most small-dollar noise ($500 travel) is caught by `project_type`/`geographic` anyway (below); residual tiny *research* grants can't be size-filtered without parsing `synopsis` text. **Downstream impact:** prestige `size_bucket` abstains (null) for SPIN opps — fine, since these are foundations where `sponsor_tier` + mechanism carry the score.

## Finding 2 — noise IS tractable with structured gates (the key result)
Broad biomed sample (300 opps, `cancer or oncology or immunology`):

| gate | removes |
|---|--:|
| non-US-eligible (`geographic`) | 28% |
| honorific (title) | 31% |
| travel/workshop (`project_type`/title) | 13% |
| **survive structural gates** | **42%** |

`project_type` is the **clean structured lever** (better than title regex): `Research Grant` / `RFA (NIH)` vs droppable `Prize or Award`, `Conference Attendance`, `Student Scholarship`, `Artistic or Cultural Performance`, `Seed Money or Start-up`. On a focused `immunology` pull, gates kept **63%**, and survivors were genuine, material, US-eligible research opportunities (CRI Irvington Fellowships, amfAR Target Grants, NMSS Harry Weaver Scholar, NIH RFAs, Multiple Myeloma Research Foundation Scholars). Dropped rows were genuine noise (European lecture awards, Australian society prizes, UK engagement grants, Conference Attendance).

**Residual imperfections in survivors** (→ extra gates / topic-match cleans them):
- A few non-US slipped when `geographic="No Restrictions"` (an Australian award). Geography gate needs refinement.
- Suspended programs ("Ramsey Research Fund (Temporarily Suspended)") → need a **status gate**.
- ReciterAI's per-opp **topic-match** is the final biomed-relevance filter on the survivors (stronger than any funder-level signal).

## Finding 3 — gap funders are in SPIN, but under canonical names (coverage caveat)
Exact `spon_name` match is brittle: `"Michael J. Fox Foundation"` → 0, but keyword `"Michael J. Fox"` → **4 opps** under SPIN's name *"Fox (Michael J.) Foundation for Parkinson's Research"*. Damon Runyon → present. **Doris Duke → 0 even by keyword** (likely absent, or a very different name). So:
- Coverage of the ~28 high-relevance gap funders is **partial**, and reaching them needs **sponsor-name resolution** (SPIN's canonical names / the `synonyms` the funder-profiler already generates), not exact-string match.
- Don't assume SPIN fills every gap — some marquee funders (Doris Duke) may simply not be in it.

## Recommendation — GO to a scoped full build
Noise is tractable without an amount field; survivors are real. The full source is moderate:
1. `pipeline_grants/spin.py` — the client (spike version works as-is, ~40 lines).
2. `normalize_spin()` — SPIN row → existing `Opportunity` (amount stays null).
3. **New gates:** `project_type` allow-list + `geographic` US-eligibility + status (drop suspended/closed). Reuse existing `is_honorific` + topic-match + prestige.
4. **Funder targeting:** seed which funders/keywords to pull from the AI funder list (≥ relevance bar), resolved to SPIN canonical names via `synonyms`.
5. Then it flows through normalize→topic→honorific→prestige into `GRANT#` like grants_gov.

**Open decisions for the build:** (a) accept no size floor, or add a synopsis-text amount parse; (b) human-review the AI funder list before it gates ingest; (c) confirm SPIN ToS permits scheduled bulk pulls.

**No-go trigger not hit:** survivors were not a flood of junk and the amount-field gap is survivable. If you'd rather not take on a third source, the fallback stands: keep grants_gov + curated, funder list stays a sanity artifact.

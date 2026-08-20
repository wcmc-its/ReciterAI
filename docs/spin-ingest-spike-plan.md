# SPIN Ingest — Measurement Spike Plan

**Status:** proposed, awaiting approval. **Owner:** Paul. **Date:** 2026-06-26.

## Why this is a spike, not a build
SPIN (InfoEd, ~40k opportunities worldwide) would add the ~28 high-relevance
foundation funders we have zero coverage of (Michael J. Fox, Doris Duke, Pew,
Gates…) — funders that don't post to grants.gov. But SPIN is **noisy** (Australian
honorary awards, $500 travel grants), and a full source (SPIN API client + new
filter gates + a third `pipeline_grants` source) is real work. We do **not** build
that until we've measured whether the noise is tractable.

**The spike answers one question:** for high-relevance funders, what % of SPIN
opportunities survive ReciterAI's existing gates plus two new ones — and is that
clean enough to justify the full build? Output = a numbers-backed go/no-go.

## What we already know (don't re-derive)
- **Creds exist** (`SPIN_PUBLIC_KEY`, `SPIN_SIGNATURE`, `SPIN_INSTITUTION_CODE` in
  `.zshrc`, confirmed set). No SPIN API code exists in any repo yet.
- **Open-source reference client:** `harvard-vpal/spin-search` uses these exact
  three creds. Search by `keywords`, `max_results`, `how` (or/and), `columns`.
  Response = list of dicts: `id, prog_title, cfda, synopsis, objective, spon_name,
  applicant_type, geographic, keyword, deadline_date, contact_email, sponsor_type`.
- **The noise recipe is already proven** in the GrantRecs 3.0 prototype
  (`funderOpportunities1.py`): funder-relevance gate + `opportunity_type` (drop
  Travel/Workshop) + `appeal_to_senior_researcher` (size-weighted materiality).
- **ReciterAI already filters** via `is_honorific` (honorary awards) + per-opp
  topic-vector scoring (biomed relevance, stronger than the funder list).

## Field mapping (SPIN → ReciterAI `Opportunity`)
| SPIN field | Opportunity | notes |
|---|---|---|
| `prog_title` | `title` | |
| `synopsis` / `objective` | `synopsis` | |
| `spon_name` | `sponsor` | |
| `sponsor_type` | (funder type) | |
| `applicant_type` | `eligibility_raw` | |
| `geographic` | — | **geography gate** (drop non-US-eligible) |
| `deadline_date` | `due_date` | |
| `cfda` | `cfda_list` | |
| `id` | `source_id` | `source="spin"` |
| **award amount** | `award_ceiling`/`estimated_funding` | **UNKNOWN — must confirm SPIN exposes it via `columns`; size floor depends on it** |

## Spike steps (throwaway code, scratchpad — NOT `pipeline_grants/`)
1. **Nail the contract.** Read `harvard-vpal/spin-search` source (or the SPIN widget
   S3 docs) for the exact endpoint + how the 3 creds authenticate (headers vs query
   vs request signing). ~30 min.
2. **Minimal read-only client.** Adapt the client or ~40 lines of `requests`, authed
   from the env vars. No writes anywhere.
3. **Pull a sample.** 3 high-relevance gap funders (Michael J. Fox, Doris Duke, Pew)
   by sponsor/keyword. Capture raw rows to a CSV in scratchpad.
4. **Confirm award amount.** Probe `columns` for an amount field — this decides
   whether a size floor is even possible. Record yes/no.
5. **Measure the funnel.** For the sample, count what each gate removes:
   - geography (`geographic` ≠ US-eligible) → Australian/intl awards
   - `is_honorific` (existing) → medals/prizes
   - `opportunity_type` + size floor → travel / sub-$10k (if amount available)
   - topic-match (existing, run on a subset — it's the expensive one) → off-biomed
   - funder-relevance pre-filter (AI list) → coarse, applied first
6. **Report.** Markdown: total pulled, % removed per gate, % surviving all gates,
   field-completeness gaps, + go/no-go recommendation.

## Explicitly OUT of scope (the full build, only if go)
- DDB writes, `pipeline_grants/spin.py` source, `normalize_spin`, production
  `opportunity_type` classifier + size floor, scheduler, client hardening,
  human-review pass of the AI funder list. None of this until the numbers justify it.

## Decision gate (after spike)
- **Survivors mostly relevant + material (≈<20% junk):** proceed to full build.
  Scope then: `spin.py` fetcher + `normalize_spin` + `opportunity_type` + size floor
  + geography gate, reusing the existing normalize→topic→honorific→prestige pipeline.
- **Still noisy after gates, or no award-amount field:** park SPIN; keep grants_gov +
  curated. The funder list stays a sanity-check artifact.

## Risks / unknowns the spike resolves or surfaces
- SPIN auth transmission + endpoint not publicly documented (harvard-vpal client may
  be stale → may need SPIN widget S3 docs or an InfoEd contact).
- **Award amount may not be exposed** → size floor becomes title/keyword-heuristic
  only, weaker against the $500 travel awards.
- SPIN ToS on programmatic/bulk pulls — confirm institutional API access permits it.
- AI-generated funder-relevance list needs a human pass before it gates production.

## Effort
~Half a day. Throwaway. The only durable output is the measurement report + decision.

# Opportunity URL submissions — drain runbook

**What:** Development-office staff paste funding-opportunity URLs into the Scholars
Profile System (`/edit/find-researchers`, flag `OPPORTUNITY_URL_INTAKE`). SPS appends
`{PK: "SUBMISSION", SK: "<ISO ts>#<uuid8>"}` items to the shared `reciterai` table
(status `pending`) — it does no fetching or scoring. This drain is the other half:
it turns pending submissions into ordinary `GRANT#manual_url:*` corpus items using the
SAME stages as every other source (LLM judge, production topic scorer, prestige/
honorific, `build_grant_item`/`put_grants`), then writes the outcome back onto the
submission so SPS can show the submitter what happened. SPS's nightly `etl:dynamodb`
projects the new rows like any other `GRANT#` item. Spec of record:
`docs/opportunity-url-intake-spec.md` in the Scholars-Profile-System repo.

## Run

```bash
# staging credentials first, always
python -m pipeline_grants.ingest_submissions --dry-run   # fetch + extract + dedup report, no writes
python -m pipeline_grants.ingest_submissions             # the real thing
```

No scheduler exists yet (#269) — run it manually alongside the other ingests until
that lands, then fold it into the daily schedule.

🔴 **Run from post-#337 `main`.** The per-program `build_grant_item`/`put_grants` re-writes the
**whole** `GRANT#` item. Post-#337 `main` carries any backfilled structured `eligibility` map
through the re-put via `PRESERVED_ATTRS` (`pipeline_grants/persist.py`); pre-#337 code drops it,
and the Scholars Profile System silently falls back to prose-regex eligibility flags. Confirm
`git log --oneline -1` is at or after #337 (`c08b753`) first; repair a stale clobber with
`python -m pipeline_grants.backfill_eligibility --overwrite`.

## Per-submission flow

1. **Guarded fetch** (`safe_fetch.fetch_page_text`): https-only, public-IP-only DNS
   (rejects loopback/RFC1918/link-local/metadata/CGNAT), redirects re-validated per
   hop (max 3), 15 s / 2 MB caps, text content only, tags stripped. JS-rendered pages
   come back too thin and reject as `content_too_thin` — that's the known ceiling
   (no headless browser in v1); the submitter sees the reason and can escalate.
2. **Extraction** (Sonnet): one page → 1..N named programs (title, sponsor, synopsis,
   amounts, dates, eligibility). Instructed to never invent values and to treat
   page-embedded instructions as content, not directives.
3. **Per program:** expired-deadline drop → token-identical-title dedup vs the whole
   `GRANT#` corpus (the funding-DB runbook measure; near-dups kept by design) →
   `judge_opportunity` (drops non-research with its reason) → `score_grant_text`
   (production scorer, so topic vectors are calibrated with publication vectors) →
   `build_grant_item` (prestige + `is_honorific` attach here as usual).
   **No regex type-gate** — mirrors the curated path's trust posture; staff submit
   prize pages legitimately and `is_honorific` handles them downstream.
4. **Outcome write-back:** `processed` + `produced_opportunity_ids`, or `rejected` +
   a human-readable `reject_reason` (truncated to 500 chars). A crashed submission
   stays `pending` and is retried next run (per-item try/except, same as `ingest`).

## Verify after a run

```bash
aws dynamodb scan --table-name reciterai \
  --filter-expression "begins_with(PK, :p)" \
  --expression-attribute-values '{":p":{"S":"SUBMISSION"}}' \
  --query 'Items[].{sk:SK.S,status:status.S,url:url.S,reason:reject_reason.S}'
```

Then on the SPS side: the submissions list on `/edit/find-researchers` shows the new
statuses immediately (it reads this table live); the produced opportunities appear in
the matcher after SPS's next nightly `etl:dynamodb` run.

## Gotchas

- The SPS app's IAM can only touch the `SUBMISSION` partition (`dynamodb:LeadingKeys`
  condition) — if the drain sees malformed submission items, suspect this pipeline's
  own writes, not SPS.
- `manual_url` ids are deterministic over (title, url) — re-running the drain after a
  crash upserts in place, and the same award submitted from a different page gets a
  different id on purpose (the cross-page case is caught by title dedup instead).
- Dry-run skips judge + scorer entirely (no Bedrock spend) — its "kept" count is an
  upper bound; the judge may still drop programs on the real run.

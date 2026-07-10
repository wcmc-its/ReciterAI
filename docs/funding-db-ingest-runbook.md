# Runbook — ingesting a WCM funding-opportunity-DB scrape

How to fold a Playwright crawl of the WCM research-funding portal
(`research.weill.cornell.edu/funding`) into the GrantRecs corpus. The scrape is an
**external artifact** (a JSON list, e.g. `wcm_funding_db_2026-06-28.json`); the crawler
itself is not in this repo. The opportunities land as `GRANT#` items with
`source=wcm_curated`, indistinguishable downstream from the hand-curated awards (SPS
GrantRecs) — provenance is recoverable via `source_url` (the `research.weill.cornell.edu`
domain) and the specific opportunities.

## Pipeline

```
scrape.json ──► funding_db_to_csv ──► dedup vs corpus ──► ingest_curated ──► (later) backfill_match
   (external)      (pure transform)     (corpus-aware)      (score+persist)     (match DSL/query)
```

1. **Convert** the scrape JSON to the curated CSV schema (`wcm_curated.py` `H_*` headers
   + a `synopsis` passthrough). Pure file transform; maps `focus_description → synopsis`
   (the scorable abstract) and folds `eligibility_text` + `application_req` into the
   `Career Stage` column (which drives both appeal-by-stage and `eligibility_raw`):

   ```
   python -m pipeline_grants.funding_db_to_csv wcm_funding_db_<date>.json \
       -o pipeline_grants/data/wcm_funding_db_enriched.csv
   ```

2. **Dedup against the existing corpus** (see "Overlap" — this is the load-bearing step).
   Drop rows whose normalized title is token-identical to an existing `GRANT#` item, so a
   re-listed opportunity (esp. the matcher's gold-standard test grants) is not duplicated.
   Snippet in "Overlap" below → writes `…_enriched_deduped.csv`.

3. **Ingest** through the existing curated path (`normalize → denoise → score → persist`).
   No `--compile-match` here — let `backfill_match` populate match fields uniformly across
   the whole corpus in one later pass.

   ```
   python -m pipeline_grants.ingest_curated --csv pipeline_grants/data/wcm_funding_db_enriched_deduped.csv
   ```
   Staging account **665083158573**, table `reciterai`. Needs AWS creds + Bedrock
   (`AWS_BEARER_TOKEN_BEDROCK`). Cost: one scoring call per opportunity.

4. **(later) Match-compile.** `python -m pipeline_grants.backfill_match` (PR #280) adds
   `match_dsl`/`match_query` to every `GRANT#` item that lacks them — covers these new
   rows together with the rest of the corpus (~2 Sonnet calls/grant).

5. **SPS side** (operator): `etl:dynamodb` + reindex projects the new `GRANT#` items into
   MySQL/OpenSearch so they appear in GrantRecs.

## Provenance & identity

- `opportunity_id = wcm_curated:slug(name, sponsor)-<sha1[:6]>` — **deterministic**, so a
  re-ingest of the same scrape **upserts in place, never duplicates**.
- The scrape JSON carries a stable per-item `slug` — the natural source-side key to diff
  crawl-over-crawl (new / changed / dropped).

## Overlap (READ THIS — the PK does not catch it)

The deterministic `opportunity_id` only dedups **exact** `name+sponsor` matches. Measured
on the 2026-06-28 scrape: **0** of 207 exact-PK-matched the existing corpus. The real
overlap is:

- **Near-duplicates** — the same opportunity re-listed with slightly different title text
  (e.g. `"Hartwell Foundation - Individual Biomedical Research Award"` vs the existing
  `"Hartwell Foundation Individual Biomedical Research Award"`) → different slug → a true
  duplicate the PK misses. On the 2026-06-28 scrape this hit the matcher's **gold-standard
  test grants (Hartwell, WorldQuant, Keck)** plus a cross-source NRSA.
- **Cross-source** — the same opportunity present under another source (`grants_gov`,
  `spin`); different source prefix, so no PK overlap.

**Mitigation (step 2):** drop scrape rows token-identical to an existing corpus title.
Conservative (Jaccard 1.0 on stopword-stripped title tokens) so distinct programs from the
same funder (e.g. St. Baldrick's *Infrastructure* vs *Fellows*) are kept:

```python
import boto3, re, csv
from pipeline_grants import wcm_curated
STOP = set("the a an of for and in to research award awards grant grants program "
           "fellowship foundation fund".split())
norm = lambda s: frozenset(set(re.sub(r'[^a-z0-9 ]',' ',(s or '').lower()).split()) - STOP)
jac  = lambda a,b: len(a&b)/len(a|b) if (a|b) else 0
c = boto3.client("dynamodb")
corpus = [norm(it.get("title",{}).get("S","")) for page in c.get_paginator("scan").paginate(
    TableName="reciterai", FilterExpression="begins_with(PK,:p) AND SK=:m",
    ExpressionAttributeValues={":p":{"S":"GRANT#"},":m":{"S":"META"}},
    ProjectionExpression="title") for it in page.get("Items",[])]
# keep a CSV row only if no corpus title is token-identical to it (jac < 1.0)
```

**Residual:** fuzzier near-dups (Jaccard 0.6–0.9) are **kept** by design (too uncertain
to auto-drop). Cross-source duplicates now have a dedicated pass —
`python -m pipeline_grants.dedupe` (dry-run report by default; `--apply` deletes the
losing `GRANT#` items). Keyed on normalized `title+sponsor` (blank sponsors group on
title alone), source-priority `grants_gov > spin > wcm_curated > manual_url`, ties keep
the most recently ingested. The ingest paths consult the same key index
(`dedupe.load_corpus_key_index`) so re-runs skip incoming items whose key an
equal-or-higher-priority source already holds. **Review the dry-run before `--apply`:**
the measured overlap includes the matcher's gold-standard grants, and deleting a
`GRANT#` row does NOT remove an already-projected SPS Opportunity/OpenSearch row
(upsert-only projection) — sweep those on the SPS side.

## Doing it incrementally (next time)

The first ingest is unavoidably full. Re-runs are wasteful today because `ingest_curated`
**re-scores every row** — its dedup only collapses within-run duplicates; it does not
GetItem-check and skip already-persisted/unchanged opportunities, and there is **no
per-item content hash** to compare (the `sha256` in `persist.py` hashes the S3 artifact,
not each item). The scrape JSON also has **no `last_modified`** field, so there is no
change signal from the source today.

**Planned `--skip-unchanged` path** (mirrors `backfill_match`'s "skip if already present"):
stamp a content hash (`title + synopsis + key fields`) on each `GRANT#` item at ingest;
on re-run, `GetItem` by `opportunity_id` and skip scoring/writing when the hash matches.
New + changed opportunities process; unchanged ones cost nothing. Optionally prune items
whose `slug` disappeared from the latest crawl. (Not built yet — this is the next-time
cost lever.)

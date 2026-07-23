"""Backfill structured `eligibility` on EXISTING GRANT# items (schema_version-keyed, idempotent).

The judge extracts structured eligibility at ingest (#290, extended v2), but ~1,122 pre-existing
GRANT# items predate the extraction or carry an older `schema_version`. This scans the corpus and
writes ONLY the `eligibility` attribute via an additive UpdateItem (1 Sonnet call/item; no re-scrape,
no re-score, no other field touched), so it's safe to run once the SPS consumer has shipped.

Idempotent + resumable + re-runnable on a schema change: items already carrying `eligibility` at the
CURRENT `ELIGIBILITY_SCHEMA_VERSION` are skipped, so a version bump re-runs ONLY the now-stale items
(tens of $ on Sonnet) rather than the whole corpus. Fail-open items (the judge returns `eligibility
is None`) are left untouched — SPS falls back to the legacy prose regexes. Cost: 1 Sonnet call per
candidate. `--dry-run` gives a FREE candidate count (hence the exact spend) before committing;
`--limit N` runs a small canary first; `--overwrite` re-extracts even current-version items.

Run: python -m pipeline_grants.backfill_eligibility [--dry-run] [--limit N] [--overwrite]
"""
import argparse
import logging

from pipeline_grants.denoise import ELIGIBILITY_SCHEMA_VERSION, judge_opportunity
from pipeline_grants.models import Opportunity
from pipeline_grants.persist import _eligibility_attr
from utils.bedrock_client import BedrockClient
from utils.dynamodb_helpers import TABLE_NAME, get_dynamo_client

log = logging.getLogger("pipeline_grants.backfill_eligibility")


def _s(item, key):
    """Read a DynamoDB String attribute (low-level client format); '' when absent."""
    return (item.get(key) or {}).get("S", "")


def _existing_schema_version(item):
    """schema_version of the item's existing eligibility map, or '' if the item has none."""
    elig_map = (item.get("eligibility") or {}).get("M") or {}
    return (elig_map.get("schema_version") or {}).get("S", "")


def _opp_from_item(item):
    """Reconstruct the minimal Opportunity the judge reads (title/sponsor/award_ceiling/
    eligibility_raw/synopsis). Other Opportunity fields don't affect the judge verdict."""
    ceiling = (item.get("award_ceiling") or {}).get("N")
    return Opportunity(
        opportunity_id=_s(item, "opportunity_id"),
        source=_s(item, "source"),
        source_id="",
        source_url=_s(item, "source_url"),
        sponsor=_s(item, "sponsor"),
        title=_s(item, "title"),
        synopsis=_s(item, "synopsis"),
        award_ceiling=int(ceiling) if ceiling is not None else None,
        eligibility_raw=_s(item, "eligibility_raw"),
    )


def run(*, table_name=TABLE_NAME, limit=None, overwrite=False, dry_run=False) -> dict:
    """Scan GRANT#/META items and (re-)extract eligibility on those missing the current version."""
    client = get_dynamo_client()
    bedrock = None if dry_run else BedrockClient(read_timeout=90)

    scanned = candidates = written = skipped = failed = 0
    done = False
    pages = client.get_paginator("scan").paginate(
        TableName=table_name,
        FilterExpression="begins_with(PK, :p) AND SK = :m",
        ExpressionAttributeValues={":p": {"S": "GRANT#"}, ":m": {"S": "META"}},
    )
    for page in pages:
        for item in page.get("Items", []):
            scanned += 1
            if not overwrite and _existing_schema_version(item) == ELIGIBILITY_SCHEMA_VERSION:
                skipped += 1
                continue
            candidates += 1
            if dry_run:
                continue
            try:
                elig = judge_opportunity(_opp_from_item(item), bedrock).get("eligibility")
                if not elig:  # fail-open -> leave the item alone (SPS uses the prose regexes)
                    skipped += 1
                    continue
                client.update_item(
                    TableName=table_name,
                    Key={"PK": item["PK"], "SK": item["SK"]},
                    UpdateExpression="SET eligibility = :e",
                    ExpressionAttributeValues={":e": {"M": _eligibility_attr(elig)}},
                )
                written += 1
            except Exception as exc:  # noqa: BLE001 - one bad item must not abort the backfill
                failed += 1
                log.warning("backfill failed for %s: %s", _s(item, "PK"), exc)
            if limit is not None and written >= limit:
                done = True
                break
        if done:
            break

    summary = {"scanned": scanned, "candidates": candidates, "written": written,
               "skipped": skipped, "failed": failed, "dry_run": dry_run}
    log.info("backfill summary: %s", summary)
    return summary


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Backfill structured eligibility on GRANT# items (schema_version-keyed).")
    p.add_argument("--dry-run", action="store_true", help="count candidates only; no Bedrock, no writes")
    p.add_argument("--limit", type=int, default=None, help="cap the number of items written (canary run)")
    p.add_argument("--overwrite", action="store_true", help="re-extract even items already at the current schema_version")
    args = p.parse_args(argv)
    run(limit=args.limit, overwrite=args.overwrite, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

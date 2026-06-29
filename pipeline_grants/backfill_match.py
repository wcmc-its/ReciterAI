"""Compile-only backfill of the grant->researcher matcher fields on EXISTING GRANT# items.

`ingest --compile-match` is a FRESH-ingest path: it re-scrapes grants.gov "posted" results and
only compiles match_dsl/match_query on newly fetched grants. It will NOT populate the GRANT#
items already in the table. This backfill scans the existing corpus and writes ONLY the two
match attributes via an additive UpdateItem (no re-scrape, no re-score, no other field touched),
so it's safe to run once the SPS consumer has shipped.

Idempotent + resumable: items that already carry `match_dsl` are skipped (use --overwrite to
recompile). Cost: 2 Sonnet calls per compiled grant. `--dry-run` gives a FREE candidate count
(hence the exact spend) before committing; `--limit N` runs a small canary first.

Run: python -m pipeline_grants.backfill_match [--dry-run] [--limit N] [--overwrite]
"""
import argparse
import logging

from pipeline_grants.match_compile import compile_match, load_vocab_or_disable, match_attrs
from utils.bedrock_client import BedrockClient
from utils.dynamodb_helpers import TABLE_NAME, get_dynamo_client

log = logging.getLogger("pipeline_grants.backfill_match")


def _s(item, key):
    """Read a DynamoDB String attribute (low-level client format); '' when absent."""
    return (item.get(key) or {}).get("S", "")


def run(*, table_name=TABLE_NAME, limit=None, overwrite=False, dry_run=False) -> dict:
    """Scan GRANT#/META items and add match_dsl/match_query to those missing it. Returns counts."""
    client = get_dynamo_client()
    vocab = load_vocab_or_disable(log)
    if not vocab:
        log.error("subtopic vocab empty — compilation disabled; aborting backfill")
        return {"error": "no_vocab"}
    bedrock = None if dry_run else BedrockClient(read_timeout=90)

    scanned = candidates = compiled = skipped = failed = 0
    done = False
    pages = client.get_paginator("scan").paginate(
        TableName=table_name,
        FilterExpression="begins_with(PK, :p) AND SK = :m",
        ExpressionAttributeValues={":p": {"S": "GRANT#"}, ":m": {"S": "META"}},
    )
    for page in pages:
        for item in page.get("Items", []):
            scanned += 1
            if not overwrite and "match_dsl" in item:
                skipped += 1
                continue
            title, synopsis = _s(item, "title"), _s(item, "synopsis")
            if not title or not synopsis:
                skipped += 1
                continue
            candidates += 1
            if dry_run:
                continue
            try:
                dsl, query = compile_match(title, synopsis, vocab, bedrock=bedrock)
                attrs = match_attrs(dsl, query)
                if not attrs:  # fail-open compile (None,None) -> nothing to write
                    skipped += 1
                    continue
                client.update_item(
                    TableName=table_name,
                    Key={"PK": item["PK"], "SK": item["SK"]},
                    UpdateExpression="SET match_dsl = :d, match_query = :q",
                    ExpressionAttributeValues={":d": attrs["match_dsl"], ":q": attrs["match_query"]},
                )
                compiled += 1
            except Exception as exc:  # noqa: BLE001 - one bad item must not abort the backfill
                failed += 1
                log.warning("backfill failed for %s: %s", _s(item, "PK"), exc)
            if limit is not None and compiled >= limit:
                done = True
                break
        if done:
            break

    summary = {"scanned": scanned, "candidates": candidates, "compiled": compiled,
               "skipped": skipped, "failed": failed, "dry_run": dry_run}
    log.info("backfill summary: %s", summary)
    return summary


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Compile-only backfill of match_dsl/match_query on GRANT# items.")
    p.add_argument("--dry-run", action="store_true", help="count candidates only; no Bedrock, no writes")
    p.add_argument("--limit", type=int, default=None, help="cap the number of grants compiled (canary run)")
    p.add_argument("--overwrite", action="store_true", help="recompile items that already have match_dsl")
    args = p.parse_args(argv)
    run(limit=args.limit, overwrite=args.overwrite, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Baseline synopsis provenance on existing scores (#150 item 2, "baseline as current").

Part A of item 2 stamps `scored_enriched_at` / `scored_synopsis_model` onto the
`PROCESSING#` row at score time, so a later drift sweep can tell when a synopsis
was regenerated after scoring. Scores written *before* that stamp shipped carry
no provenance — this one-time, re-runnable backfill sets each such score's
provenance to the PMID's *current* `IMPACT#` values, i.e. "assume it was scored
against the synopsis that's there now." Effect: no existing score is spuriously
flagged as drifted, and every synopsis change from here on is detectable.

Idempotent: the UpdateItem is guarded by `attribute_not_exists(scored_enriched_at)`,
so it only touches un-stamped rows and never overwrites a genuine score-time
stamp (or a prior backfill).

Usage:
  scripts/backfill_score_provenance.py --dry-run
  scripts/backfill_score_provenance.py
  scripts/backfill_score_provenance.py --taxonomy-version taxonomy_v2

Env: RECITERAI_TABLE (default "reciterai"), AWS_REGION (default "us-east-1").
Run with PYTHONPATH=<repo-root> so utils.* imports resolve.
"""
from __future__ import annotations

import argparse
import os
import sys

TABLE = os.environ.get("RECITERAI_TABLE", "reciterai")
REGION = os.environ.get("AWS_REGION", "us-east-1")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--taxonomy-version", default="taxonomy_v2")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change, write nothing")
    args = ap.parse_args()

    import boto3
    from botocore.exceptions import ClientError
    from utils.dynamodb_helpers import (
        query_pmids_by_status, fetch_synopsis_records,
    )

    client = boto3.client("dynamodb", region_name=REGION)

    scored = query_pmids_by_status(client, TABLE, args.taxonomy_version, "complete")
    print(f"scored (complete) PMIDs under {args.taxonomy_version}: {len(scored)}")
    if not scored:
        return 0

    # Current IMPACT# provenance for the scored set (only PMIDs with a synopsis).
    records = fetch_synopsis_records(client, scored)
    print(f"of those, {len(records)} have an IMPACT# synopsis row to baseline from")

    if args.dry_run:
        sample = list(records.items())[:5]
        for pmid, rec in sample:
            print(f"  [dry-run] pmid {pmid} -> scored_enriched_at={rec['enriched_at']!r} "
                  f"scored_synopsis_model={rec['synopsis_model']!r}")
        print(f"[dry-run] would baseline up to {len(records)} PROCESSING# rows "
              f"(only those lacking scored_enriched_at) in {TABLE} ({REGION})")
        return 0

    stamped = skipped = 0
    for pmid, rec in records.items():
        try:
            client.update_item(
                TableName=TABLE,
                Key={"PK": {"S": f"PROCESSING#pmid_{pmid}"}, "SK": {"S": "STATUS"}},
                UpdateExpression="SET scored_enriched_at = :ea, scored_synopsis_model = :sm",
                ConditionExpression="attribute_exists(PK) AND attribute_not_exists(scored_enriched_at)",
                ExpressionAttributeValues={
                    ":ea": {"S": rec["enriched_at"]},
                    ":sm": {"S": rec["synopsis_model"]},
                },
            )
            stamped += 1
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                skipped += 1  # already stamped, or no PROCESSING# row — leave it
            else:
                raise
    print(f"baselined {stamped} PROCESSING# rows; skipped {skipped} "
          f"(already stamped or no checkpoint row) in {TABLE} ({REGION})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

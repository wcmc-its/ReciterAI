#!/usr/bin/env python3
"""Load the invalid-PMID exclude list into DynamoDB as INVALID#pmid_{pmid} rows (#150 item 3).

These rows are the *runtime* source of truth for the consume-time invalid cull
in the hot-path eligibility sweep (`pipeline_hot.orchestrator.resolve_eligibility_sweep`)
and the scorer (`score_publications.cull_invalid_publications`), read via
`utils.dynamodb_helpers.scan_invalid_pmids`. DDB is used rather than the txt
file directly because the Lambda/Fargate runtime cannot read the ReciterDB
repo, and the upstream `DELETE FROM reporting_abstracts` does not clean the
`IMPACT#` rows the eligibility sweep scans.

Source: a plain-text file of PMIDs, one per line. Lines may carry a trailing
verdict token (e.g. `PREFIX_CORRUPTED`); the first whitespace/comma-delimited
all-digit token on each line is taken. Default is the canonical list ReciterDB
maintains at ../ReciterDB/invalid_pmids.txt (relative to this repo). Lines
starting with `#` are ignored.

Re-runnable / idempotent — each row is a PutItem upsert. Re-run whenever
ReciterDB updates the list (this loader is operator-run for now; ReciterDB
cleanup is the long-term owner of writing these rows).

Usage:
  scripts/load_invalid_pmids.py --dry-run
  scripts/load_invalid_pmids.py
  scripts/load_invalid_pmids.py --source /path/to/invalid_pmids.txt

Env: RECITERAI_TABLE (default "reciterai"), AWS_REGION (default "us-east-1").
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SOURCE = os.path.normpath(
    os.path.join(REPO_ROOT, "..", "ReciterDB", "invalid_pmids.txt")
)
TABLE = os.environ.get("RECITERAI_TABLE", "reciterai")
REGION = os.environ.get("AWS_REGION", "us-east-1")


def parse_pmids(path: str) -> list[str]:
    """Return the sorted, de-duplicated PMIDs from the source file."""
    pmids: set[str] = set()
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            tok = re.split(r"[,\s]+", line)[0]
            if tok.isdigit():
                pmids.add(tok)
    return sorted(pmids, key=int)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default=DEFAULT_SOURCE,
                    help=f"invalid-PMID list (default: {DEFAULT_SOURCE})")
    ap.add_argument("--dry-run", action="store_true",
                    help="parse + report, write nothing")
    args = ap.parse_args()

    if not os.path.exists(args.source):
        print(f"load_invalid_pmids: source not found: {args.source}", file=sys.stderr)
        return 1

    pmids = parse_pmids(args.source)
    print(f"parsed {len(pmids)} invalid PMIDs from {args.source}")
    if not pmids:
        print("nothing to load.")
        return 0

    if args.dry_run:
        print(f"[dry-run] sample: {pmids[:10]}")
        print(f"[dry-run] would upsert {len(pmids)} INVALID#pmid_ rows "
              f"into {TABLE} ({REGION})")
        return 0

    import boto3

    table = boto3.resource("dynamodb", region_name=REGION).Table(TABLE)
    loaded_at = datetime.now(timezone.utc).isoformat()
    source_name = os.path.basename(args.source)
    n = 0
    with table.batch_writer() as bw:
        for pmid in pmids:
            bw.put_item(Item={
                "PK": f"INVALID#pmid_{pmid}",
                "SK": "METADATA",
                "pmid": pmid,
                "source": source_name,
                "loaded_at": loaded_at,
            })
            n += 1
    print(f"upserted {n} INVALID#pmid_ rows into {TABLE} ({REGION})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

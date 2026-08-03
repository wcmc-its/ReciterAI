#!/usr/bin/env python3
"""Check DynamoDB's `TOPIC#` partition set against the bundled taxonomy (ADR D5 layer 2).

The operator/debug surface for `pipeline_taxonomy_drift.checker`, which also runs
daily as a Lambda. Read-only unless `--write-row` is passed.

Reports two directions:
  - ORPHAN   topics with `TOPIC#` rows but absent from taxonomy_v2.json. Something
             is scoring against a taxonomy we no longer ship (the 2026-07-10 incident).
  - UNSCORED taxonomy topics with no `TOPIC#` rows. A new topic that never reached prod.

Exit code: 0 clean, 1 drift detected.

Usage:
  scripts/check_taxonomy_data_drift.py                  # read-only report
  scripts/check_taxonomy_data_drift.py --write-row      # also persist DRIFT#taxonomy + alert

Env: RECITERAI_TABLE (default "reciterai"), AWS_REGION (default "us-east-1").
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline_taxonomy_drift.checker import evaluate, run_check, scan_topic_partitions  # noqa: E402
from utils.dynamodb_helpers import get_table  # noqa: E402
from utils.iso_clock import now_iso  # noqa: E402
from utils.taxonomy import current_content_hash, topic_ids  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--table", default=os.environ.get("RECITERAI_TABLE", "reciterai"))
    ap.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    ap.add_argument(
        "--write-row",
        action="store_true",
        help="persist a DRIFT#taxonomy row and fire a Teams card (default: report only)",
    )
    ap.add_argument("--json", action="store_true", help="emit the raw result as JSON")
    args = ap.parse_args()

    table = get_table(args.table, args.region)
    taxonomy = topic_ids()

    if args.write_row:
        result = run_check(
            table, taxonomy, taxonomy_hash=current_content_hash(), day=now_iso()[:10]
        )
    else:
        result = evaluate(scan_topic_partitions(table).keys(), taxonomy)
        result["taxonomy_hash"] = current_content_hash()

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"severity:  {result['severity']}")
        print(f"taxonomy:  {result['taxonomy_topic_count']} topics, hash {result['taxonomy_hash'][:12]}")
        print(f"partitions: {result['partition_count']} TOPIC# partitions in {args.table}")
        for label, key in (("ORPHAN", "orphan_topics"), ("UNSCORED", "unscored_topics")):
            for topic in result[key]:
                print(f"  {label:9} {topic}")

    return 0 if result["severity"] == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())

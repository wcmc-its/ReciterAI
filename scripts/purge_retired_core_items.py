#!/usr/bin/env python3
"""One-off: delete the DynamoDB items left behind by cores retired from the dictionary.

On 2026-10-29 the WCM cores director confirmed that two cores in
`config/core_dictionary.yaml` do not exist as WCM cores, and their entries were
removed: core_id "9" (Advanced Biomolecular Analysis Core) and "12" (Nuclear
Magnetic Resonance). Nothing in the pipeline deletes their items on its own:

- `run.py --reconcile` only walks the cores still in the dictionary, and it DEMOTES
  (sets `status = below_threshold`), never deletes. A retired core is never visited.
- The STAFF_DICT publish loop in `run.py main()` re-asserts the counts for every core
  still in the dictionary. It stops refreshing 9 and 12 but never removes their items.

So this script removes both item shapes keyed on a retired core id in the shared
`reciterai` table:

- usage rows, `PK=PUB#{pmid}` / `SK=CORE#{id}`: written by `persist.put_core_usage`
  (run.py) and `persist.put_candidate` (batch_screen), with `status` updated by SPS
  claim-writeback. Found with ONE paginated Scan, filtered on the exact SK, because the
  table has no index on SK. The SK filter is exact, so `CORE#1` never matches `CORE#12`.
- config items, `PK=CORE#{id}` / any SK: `STAFF_DICT` (written here by
  `persist.put_core_staff_dict_counts`) and the SPS-written `CLIENTS` / `STAFF` items.
  Found with a Query on the partition, which returns every SK.

Human `claimed` / `rejected` rows are deleted too: the core they were a decision about
no longer exists, and the sibling SPS change deletes those cores' MySQL rows. The
dry-run prints a per-core count by status so that can be read before applying.

Safety:
- **Dry-run by default.** Nothing is written without `--apply`.
- **Refuses a live core.** Any requested id still in `config/core_dictionary.yaml` is
  refused before any read, so a typo'd `--core 1` cannot purge a real core.
- **Idempotent.** DeleteItem on an absent key is a no-op, so a re-run after an
  interruption finishes the job and a third run finds nothing.

Usage (PYTHONPATH=<repo-root> so `pipeline_cores.*` / `utils.*` resolve):
  scripts/purge_retired_core_items.py                 # dry-run: scan + count, write nothing
  scripts/purge_retired_core_items.py --apply         # delete what the dry-run listed
  scripts/purge_retired_core_items.py --core 9        # restrict to one retired core

Env: RECITERAI_TABLE (default "reciterai"), AWS_REGION (default "us-east-1").
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

TABLE = os.environ.get("RECITERAI_TABLE", "reciterai")
REGION = os.environ.get("AWS_REGION", "us-east-1")

# Retired 2026-10-29 (cores director). Kept here, not derived, because "every id not in
# the dictionary" would also match an id that was never a core and a typo'd SK.
RETIRED_CORE_IDS = ("9", "12")


def refuse_live_cores(core_ids, dictionary_ids) -> None:
    """Raise if any requested id is still a core in the dictionary."""
    live = sorted(set(core_ids) & set(dictionary_ids), key=int)
    if live:
        raise SystemExit(
            f"refusing: core_id(s) {live} are still in config/core_dictionary.yaml; "
            "this script only purges retired cores")


def scan_usage_rows(client, table: str, core_ids) -> list:
    """Every `PK=PUB#*` / `SK=CORE#{id}` item for `core_ids`: [{PK, SK, status}]."""
    sk_vals = {f":k{i}": {"S": f"CORE#{c}"} for i, c in enumerate(core_ids)}
    eav = {**sk_vals, ":pub": {"S": "PUB#"}}
    kwargs = {
        "TableName": table,
        "ProjectionExpression": "PK, SK, #st",
        "FilterExpression": f"SK IN ({', '.join(sk_vals)}) AND begins_with(PK, :pub)",
        "ExpressionAttributeNames": {"#st": "status"},
        "ExpressionAttributeValues": eav,
    }
    out = []
    while True:
        resp = client.scan(**kwargs)
        for it in resp.get("Items", []):
            out.append({"PK": it["PK"]["S"], "SK": it["SK"]["S"],
                        "status": it.get("status", {}).get("S", "")})
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            return out
        kwargs["ExclusiveStartKey"] = lek


def query_config_items(client, table: str, core_id: str) -> list:
    """Every item in partition `CORE#{core_id}`: [{PK, SK}]."""
    kwargs = {
        "TableName": table,
        "KeyConditionExpression": "PK = :pk",
        "ProjectionExpression": "PK, SK",
        "ExpressionAttributeValues": {":pk": {"S": f"CORE#{core_id}"}},
    }
    out = []
    while True:
        resp = client.query(**kwargs)
        out.extend({"PK": it["PK"]["S"], "SK": it["SK"]["S"]} for it in resp.get("Items", []))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            return out
        kwargs["ExclusiveStartKey"] = lek


def summarize(usage: list, config: list, core_ids) -> dict:
    by_status = {c: collections.Counter() for c in core_ids}
    for r in usage:
        by_status[r["SK"][len("CORE#"):]][r["status"] or "(none)"] += 1
    return {
        "core_ids": list(core_ids),
        "usage_rows": {c: dict(sorted(by_status[c].items())) for c in core_ids},
        "usage_rows_total": len(usage),
        "config_items": sorted(f"{r['PK']}/{r['SK']}" for r in config),
    }


def purge(client, table: str, core_ids, *, apply: bool) -> dict:
    usage = scan_usage_rows(client, table, core_ids)
    config = [it for c in core_ids for it in query_config_items(client, table, c)]
    report = summarize(usage, config, core_ids)
    report["applied"] = apply
    if apply:
        for r in usage + config:
            client.delete_item(TableName=table,
                               Key={"PK": {"S": r["PK"]}, "SK": {"S": r["SK"]}})
        report["deleted"] = len(usage) + len(config)
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--core", action="append", choices=RETIRED_CORE_IDS,
                    help="retired core_id to purge (repeatable; default: all retired)")
    ap.add_argument("--apply", action="store_true",
                    help="delete the items (default is a dry-run that writes nothing)")
    args = ap.parse_args(argv)
    core_ids = tuple(args.core or RETIRED_CORE_IDS)

    from pipeline_cores.dictionary import load_cores
    refuse_live_cores(core_ids, [c.core_id for c in load_cores()])

    from utils.dynamodb_helpers import get_dynamo_client
    client = get_dynamo_client(REGION)
    report = purge(client, TABLE, core_ids, apply=args.apply)
    if not args.apply:
        print(f"[dry-run] would delete {report['usage_rows_total']} usage row(s) and "
              f"{len(report['config_items'])} config item(s); re-run with --apply",
              file=sys.stderr)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

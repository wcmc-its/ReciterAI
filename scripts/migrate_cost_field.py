"""
One-off migration for Phase 10 T2 / D-09: rename `cost_estimate_usd` →
`cost_observed_usd` on existing STAGE# rows.

Behavior:
    - Scans `STAGE#*` partition keys via begins_with(PK, "STAGE#").
    - For each row with `cost_estimate_usd` set:
        - `complete` / `failed` rows: copy value to `cost_observed_usd`,
          remove `cost_estimate_usd`.
        - `skipped` rows: set `cost_observed_usd = Decimal("0")` (D-09
          semantic — skips are first-class zeros), remove old field.
    - Idempotent: rows that already carry `cost_observed_usd` and no
      `cost_estimate_usd` are skipped.

Run order:
    python -m scripts.migrate_cost_field --dry-run   # preview counts
    python -m scripts.migrate_cost_field             # apply

This is a one-shot script. Delete after Phase 10 lands and the prod
table has been migrated. Background: docs/RECITERAI-SPEC.md §5 framed
skip cost as the DDB GetItem cost ($0.0000003); Phase 10 D-09 changed
the field name AND the skip semantics — skips now carry observed cost
of zero (no work performed). Phase 9 STAGE# rows from publish_hierarchy
are the only ones in prod at migration time.
"""
from __future__ import annotations

import argparse
import sys
from decimal import Decimal

from utils.dynamodb_helpers import get_table


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print counts; do not write.",
    )
    args = parser.parse_args()

    table = get_table()
    scan_kwargs = {
        "FilterExpression": "begins_with(PK, :prefix)",
        "ExpressionAttributeValues": {":prefix": "STAGE#"},
    }

    n_total = 0
    n_already_migrated = 0
    n_migrate_complete = 0
    n_migrate_failed = 0
    n_migrate_skipped = 0

    while True:
        resp = table.scan(**scan_kwargs)
        for item in resp.get("Items", []):
            n_total += 1
            has_old = "cost_estimate_usd" in item
            has_new = "cost_observed_usd" in item
            if has_new and not has_old:
                n_already_migrated += 1
                continue
            if not has_old:
                # Pre-cost-field rows or schema oddities — leave alone.
                continue

            status = item.get("status")
            if status == "skipped":
                new_value = Decimal("0")
                n_migrate_skipped += 1
            else:
                # complete or failed — preserve observed cost as-was.
                new_value = item["cost_estimate_usd"]
                if status == "complete":
                    n_migrate_complete += 1
                elif status == "failed":
                    n_migrate_failed += 1

            if not args.dry_run:
                table.update_item(
                    Key={"PK": item["PK"], "SK": item["SK"]},
                    UpdateExpression=(
                        "SET cost_observed_usd = :v REMOVE cost_estimate_usd"
                    ),
                    ExpressionAttributeValues={":v": new_value},
                )

        if "LastEvaluatedKey" not in resp:
            break
        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    print(f"STAGE# rows scanned:           {n_total}")
    print(f"Already migrated (skipped):    {n_already_migrated}")
    print(f"Migrated `complete`:           {n_migrate_complete}")
    print(f"Migrated `failed`:             {n_migrate_failed}")
    print(f"Migrated `skipped` (→ 0):      {n_migrate_skipped}")
    if args.dry_run:
        print("\n(dry-run — no writes performed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
One-off migration for Phase 11 D-02 / D-17: stamp `hierarchy_version`
on every activity record that already carries subtopic fields.

Per D-17 (research follow-up to D-02): pre-Phase-11 PROCESSING# rows
do NOT carry hierarchy_version, so a PROCESSING# join would yield
100% orphans. This script uses a single sentinel value:
`v0.0.0-pre-phase-11` — parses as valid pre-release semver per D-03,
sorts before every real v{ISO-date} version.

Idempotent: rows already carrying hierarchy_version are filtered out
by the scan's FilterExpression. Reports counts at end.

Run order:
    python -m scripts.migrate_activity_hierarchy_version --dry-run
    python -m scripts.migrate_activity_hierarchy_version

This is a one-shot script. Delete after Phase 11 ships.
"""
from __future__ import annotations

import argparse
import sys

from boto3.dynamodb.conditions import Attr

from utils.dynamodb_helpers import get_table

# Sentinel value for pre-Phase-11 activity rows (D-17, D-03).
# Parses as valid pre-release semver; sorts before every real v{ISO-date} version.
LEGACY_SENTINEL = "v0.0.0-pre-phase-11"

TABLE_NAME = "reciterai"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scripts.migrate_activity_hierarchy_version",
        description=__doc__,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and count; do not write.",
    )
    args = parser.parse_args(argv)

    table = get_table(TABLE_NAME)

    # Scan for rows that:
    #  - have primary_subtopic_id (they carry subtopic data)
    #  - do NOT yet have hierarchy_version (not yet stamped)
    filter_expr = Attr("primary_subtopic_id").exists() & Attr("hierarchy_version").not_exists()

    scan_kwargs: dict = {"FilterExpression": filter_expr}

    n_total = 0
    n_stamped = 0
    # n_already_stamped is always 0 because the FilterExpression excludes them;
    # kept for symmetry with dry-run reporting.
    n_already_stamped = 0

    while True:
        resp = table.scan(**scan_kwargs)
        for item in resp.get("Items", []):
            n_total += 1
            if not args.dry_run:
                table.update_item(
                    Key={"PK": item["PK"], "SK": item["SK"]},
                    UpdateExpression="SET hierarchy_version = :v",
                    ExpressionAttributeValues={":v": LEGACY_SENTINEL},
                )
            n_stamped += 1

        if "LastEvaluatedKey" not in resp:
            break
        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    print(f"Rows scanned:      {n_total}")
    print(f"Newly stamped:     {n_stamped}")
    print(f"Already stamped:   {n_already_stamped}")
    if args.dry_run:
        print("\n(dry-run — no writes performed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
One-off migration for Phase 11 D-04 / D-05: rewrite every
SPOTLIGHT_HISTORY# row's PK from
    SPOTLIGHT_HISTORY#{subtopic_id}
to
    SPOTLIGHT_HISTORY#{hierarchy_version}#{subtopic_id}

# Operational coordination (D-15):
# Run this migration during an operational lull — the Phase 10 spotlight
# cadence is monthly. Schedule for immediately after a fresh rotation
# publish so the history is fresh and the new PK shape is consistent.

Inference rule:
  - last_shown_publish_id matches ^v[0-9]{4}-[0-9]{2}-[0-9]{2}$ → use that
  - last_shown_publish_id missing → "0.0.0-orphan" (never_spotlighted_count)
  - last_shown_publish_id present but unparseable → "0.0.0-orphan"
    (malformed_publish_id_count) AND log to diff file

PK changes mean PutItem + DeleteItem (NOT UpdateItem).

Default mode is dry-run. `--commit` required to write. Confirm-and-commit
prompt at start of commit phase. Per-row before/after diff written to
spotlight_history_migration_<timestamp>.log for operator review.

This is a one-shot script. Delete after Phase 11 ships.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from boto3.dynamodb.conditions import Attr

from utils.dynamodb_helpers import get_table

TABLE_NAME = "reciterai-chatbot"
_SPOTLIGHT_PREFIX = "SPOTLIGHT_HISTORY#"

# Valid publish_id pattern: v{YYYY}-{MM}-{DD}
_PUBLISH_ID_PATTERN = re.compile(r"^v\d{4}-\d{2}-\d{2}$")

# Sentinel for rows without a parseable publish_id (D-05, D-03).
_ORPHAN_SENTINEL = "0.0.0-orphan"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scripts.migrate_spotlight_history_pk",
        description=__doc__,
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help=(
            "Apply rewrites. Default is dry-run (scan + count, no writes). "
            "Requires interactive 'yes' confirmation before writes begin."
        ),
    )
    args = parser.parse_args(argv)

    table = get_table(TABLE_NAME)

    # Scan all SPOTLIGHT_HISTORY# rows
    filter_expr = Attr("PK").begins_with(_SPOTLIGHT_PREFIX)
    scan_kwargs: dict = {"FilterExpression": filter_expr}

    # Collect rows to process (we need to scan first for the confirm gate)
    all_rows: list[dict] = []
    while True:
        resp = table.scan(**scan_kwargs)
        all_rows.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    # Counters
    n_real = 0
    n_never_spotlighted = 0
    n_malformed = 0
    n_skipped_already_migrated = 0

    # Classify rows and build rewrite plan
    rewrite_plan: list[tuple[dict, str]] = []  # (item, new_pk)
    malformed_log_lines: list[str] = []

    for item in all_rows:
        pk = item.get("PK", "")

        # Idempotent guard: already migrated if PK has more than one '#' after prefix
        if pk.count("#") > 1:
            n_skipped_already_migrated += 1
            continue

        old_sid = pk.split("#", 1)[1] if "#" in pk else pk
        last_shown = item.get("last_shown_publish_id")

        if last_shown is None:
            new_version = _ORPHAN_SENTINEL
            n_never_spotlighted += 1
        elif _PUBLISH_ID_PATTERN.match(last_shown):
            new_version = last_shown
            n_real += 1
        else:
            new_version = _ORPHAN_SENTINEL
            n_malformed += 1
            malformed_log_lines.append(
                f"MALFORMED PK={pk} last_shown={last_shown!r}\n"
            )

        new_pk = f"{_SPOTLIGHT_PREFIX}{new_version}#{old_sid}"
        rewrite_plan.append((item, new_pk))

    n_total_rewrites = len(rewrite_plan)

    # Open diff log for writing (always, even in dry-run mode, for operator review)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = f"spotlight_history_migration_{ts}.log"
    with open(log_path, "w") as diff_log:
        for item, new_pk in rewrite_plan:
            old_pk = item["PK"]
            diff_log.write(f"REWRITE {old_pk} -> {new_pk}\n")

        for line in malformed_log_lines:
            diff_log.write(line)

    if not args.commit:
        # Dry-run summary
        print(f"Rows scanned:                   {len(all_rows)}")
        print(f"Already migrated (skipped):     {n_skipped_already_migrated}")
        print(f"Real (real hierarchy_version inferred):  {n_real}")
        print(f"Never spotlighted (orphan):     {n_never_spotlighted}")
        print(f"Malformed publish_id (orphan):  {n_malformed}")
        print(f"Total rewrites planned:         {n_total_rewrites}")
        print(f"\nDiff log: {log_path}")
        print("\n(dry-run — no writes performed; use --commit to apply)")
        return 0

    # Confirm-and-commit gate (backfill_spotlight.py:687-693 pattern)
    confirm = input(
        "WARNING: this will rewrite every SPOTLIGHT_HISTORY# row's PK in the "
        "reciterai-chatbot DynamoDB table. Type 'yes' to confirm: "
    )
    if confirm.strip().lower() != "yes":
        print("Aborted.")
        return 1

    # Apply rewrites: PutItem + DeleteItem per row
    for item, new_pk in rewrite_plan:
        new_item = {**item, "PK": new_pk}
        table.put_item(Item=new_item)
        table.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})

    # Final summary
    print(f"Rows scanned:                   {len(all_rows)}")
    print(f"Already migrated (skipped):     {n_skipped_already_migrated}")
    print(f"Real (real hierarchy_version inferred):  {n_real}")
    print(f"Never spotlighted (orphan):     {n_never_spotlighted}")
    print(f"Malformed publish_id (orphan):  {n_malformed}")
    print(f"Total rewrites applied:         {n_total_rewrites}")
    print(f"Diff log: {log_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

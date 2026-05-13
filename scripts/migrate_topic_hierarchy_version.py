"""
Backfill `hierarchy_version` on legacy TOPIC# / SCORE# rows that predate
the Phase 11 writer (issue #16).

Phase 11 D-01 made `hierarchy_version` a required first-class attribute on
TOPIC# / SCORE# rows produced by `update_activity_subtopics`. Rows written
before that change (the bulk of the corpus) lack the attribute entirely.
SPS-side ETL today does not filter by `hierarchy_version`, so this is a
latent correctness gap — but any future consumer that wants to scope
queries by hierarchy version will see zero matching rows from the legacy
slice.

This script scans every `TOPIC#` partition row with `SK` starting `SCORE#`
and stamps `hierarchy_version = "v0-legacy"` on each row that lacks the
attribute. The sentinel value is deliberately outside the live
`v{YYYY}-{MM}-{DD}` format so consumers can detect it.

Why a sentinel rather than the current published version?
  - The legacy rows were produced across many cold runs in Phases 1-10,
    each with a different effective taxonomy state. Stamping all of them
    with today's `v{date}` would falsify history.
  - Reconstructing the actual originating version per-row is not
    feasible — the substrate `STAGE#` records that would carry that
    information didn't exist yet.
  - `v0-legacy` is honest about the unknown and keeps the door open
    for a future consumer to special-case the bucket.

Mode: default dry-run, `--commit` to apply. Confirm-and-commit prompt.
Idempotent: re-runs are no-ops because every UpdateItem uses
`attribute_not_exists(hierarchy_version)` as a ConditionExpression.
Per-row diff entries are written to
`topic_hierarchy_version_backfill_<timestamp>.log` for operator review.

Run:
    python3 -m scripts.migrate_topic_hierarchy_version            # dry-run
    python3 -m scripts.migrate_topic_hierarchy_version --commit   # apply
"""
from __future__ import annotations

import argparse
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

from utils.dynamodb_helpers import get_table

TABLE_NAME = "reciterai"
LEGACY_SENTINEL = "v0-legacy"
SCAN_SEGMENTS = 8


def scan_target_rows(table) -> list[dict]:
    """Parallel scan for TOPIC#/SCORE# rows lacking hierarchy_version."""
    counter_lock = threading.Lock()
    total_scanned = 0

    def scan_segment(seg: int) -> list[dict]:
        nonlocal total_scanned
        kwargs = dict(
            Segment=seg,
            TotalSegments=SCAN_SEGMENTS,
            FilterExpression=(
                "begins_with(PK, :pk_prefix) AND begins_with(SK, :sk_prefix) "
                "AND attribute_not_exists(hierarchy_version)"
            ),
            ExpressionAttributeValues={
                ":pk_prefix": "TOPIC#",
                ":sk_prefix": "SCORE#",
            },
            ProjectionExpression="PK, SK",
        )
        out = []
        while True:
            resp = table.scan(**kwargs)
            out.extend(resp.get("Items", []))
            with counter_lock:
                total_scanned += len(resp.get("Items", []))
            last = resp.get("LastEvaluatedKey")
            if not last:
                break
            kwargs["ExclusiveStartKey"] = last
        return out

    all_rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=SCAN_SEGMENTS) as pool:
        for fut in as_completed([pool.submit(scan_segment, s) for s in range(SCAN_SEGMENTS)]):
            all_rows.extend(fut.result())
    return all_rows


def apply_updates(table, rows: list[dict], diff_log) -> tuple[int, int, int]:
    """Apply UpdateItem to each row. Returns (updated, already_set, errors).

    UpdateExpression uses ConditionExpression=attribute_not_exists so a
    concurrent writer that stamped hierarchy_version in the gap between
    scan and update is detected and counted as already_set rather than
    being clobbered.
    """
    updated = 0
    already_set = 0
    errors = 0
    counter_lock = threading.Lock()

    def update_one(item: dict) -> tuple[str, str, str]:
        pk = item["PK"]
        sk = item["SK"]
        try:
            table.update_item(
                Key={"PK": pk, "SK": sk},
                UpdateExpression="SET hierarchy_version = :hv",
                ConditionExpression="attribute_not_exists(hierarchy_version)",
                ExpressionAttributeValues={":hv": LEGACY_SENTINEL},
            )
            return (pk, sk, "updated")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return (pk, sk, "already_set")
            return (pk, sk, f"error:{e.response['Error']['Code']}")

    with ThreadPoolExecutor(max_workers=32) as pool:
        futures = [pool.submit(update_one, r) for r in rows]
        for i, fut in enumerate(as_completed(futures)):
            pk, sk, outcome = fut.result()
            with counter_lock:
                if outcome == "updated":
                    updated += 1
                    diff_log.write(f"STAMP {pk} | {sk}\n")
                elif outcome == "already_set":
                    already_set += 1
                else:
                    errors += 1
                    diff_log.write(f"ERROR {pk} | {sk} | {outcome}\n")
                if (i + 1) % 5000 == 0:
                    print(f"  ... {i+1:,} updates processed (updated={updated:,} already_set={already_set:,} errors={errors:,})", flush=True)
    return updated, already_set, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scripts.migrate_topic_hierarchy_version",
        description=__doc__,
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help=(
            "Apply UpdateItem to each legacy row. Default is dry-run (scan "
            "+ count, no writes). Requires interactive 'yes' confirmation."
        ),
    )
    args = parser.parse_args(argv)

    table = get_table(TABLE_NAME)

    print(f"Scanning TOPIC#/SCORE# rows lacking hierarchy_version in {TABLE_NAME}...", flush=True)
    rows = scan_target_rows(table)
    print(f"  found {len(rows):,} rows to stamp with {LEGACY_SENTINEL!r}", flush=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = f"topic_hierarchy_version_backfill_{ts}.log"

    if not args.commit:
        with open(log_path, "w") as diff_log:
            for item in rows:
                diff_log.write(f"STAMP {item['PK']} | {item['SK']}\n")
        print()
        print(f"Rows to stamp:        {len(rows):,}")
        print(f"Sentinel value:       {LEGACY_SENTINEL}")
        print(f"Diff log:             {log_path}")
        print()
        print("(dry-run — no writes performed; use --commit to apply)")
        return 0

    # Confirm-and-commit gate
    confirm = input(
        f"WARNING: this will UpdateItem {len(rows):,} TOPIC#/SCORE# rows in the "
        f"{TABLE_NAME} DynamoDB table, setting hierarchy_version = {LEGACY_SENTINEL!r} "
        f"where it is currently absent. Type 'yes' to confirm: "
    )
    if confirm.strip().lower() != "yes":
        print("Aborted.")
        return 1

    with open(log_path, "w") as diff_log:
        diff_log.write(f"# Migration started at {ts}\n")
        diff_log.write(f"# Target: hierarchy_version = {LEGACY_SENTINEL}\n\n")
        updated, already_set, errors = apply_updates(table, rows, diff_log)
        diff_log.write(f"\n# Summary: updated={updated} already_set={already_set} errors={errors}\n")

    print()
    print(f"Rows scanned:         {len(rows):,}")
    print(f"Updated:              {updated:,}")
    print(f"Already set (skipped): {already_set:,}")
    print(f"Errors:               {errors:,}")
    print(f"Diff log:             {log_path}")
    return 0 if errors == 0 else 2


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Find the smallest unscored work set among detector-flagged CWIDs.

One-shot helper for picking a §7-A smoke target (#37 PR 4 operator handoff).
Reads:
  - Latest STAGE#onboarding_detector#GLOBAL row from DDB → flagged CWIDs
  - scan_faculty_publication_gaps() (MariaDB + DDB cross-store join post-#142)
    → per-PMID has_synopsis flag

Aggregates: for each flagged CWID, counts PMIDs where has_synopsis=False,
then prints the top-20 smallest unscored work sets.

This is a proxy for the orchestrator's net_work_count (which subtracts
already-COMPLETE PMIDs from the candidate set via the DDB PROCESSING# tracker).
has_synopsis=False == needs scoring, so for flagged CWIDs the two sets should
coincide closely. Worst case the smoke runs a slightly larger work set than
this script reports.
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO_ROOT)

import boto3

from utils import secrets_loader
from utils.sql_queries import scan_faculty_publication_gaps


def main() -> int:
    secrets_loader.load_db_credentials_from_secret()
    for key in ("DB_HOST", "DB_USERNAME", "DB_PASSWORD", "DB_NAME"):
        if not os.environ.get(key):
            print(f"ERROR: {key} not set after secrets_loader run", file=sys.stderr)
            return 1

    region = (
        os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "us-east-1"
    )
    ddb = boto3.client("dynamodb", region_name=region)
    resp = ddb.query(
        TableName="reciterai",
        KeyConditionExpression="PK = :pk",
        ExpressionAttributeValues={":pk": {"S": "STAGE#onboarding_detector#GLOBAL"}},
        ScanIndexForward=False,
        Limit=1,
    )
    items = resp.get("Items", [])
    if not items:
        print("ERROR: no detector row in DDB", file=sys.stderr)
        return 1
    detector_sk = items[0]["SK"]["S"]
    flagged = {entry["S"] for entry in items[0]["flagged_cwids"]["L"]}
    print(
        f"loaded {len(flagged)} flagged CWIDs from {detector_sk}",
        file=sys.stderr,
    )

    gap_rows = scan_faculty_publication_gaps(client=ddb)

    total = defaultdict(int)
    unscored = defaultdict(int)
    for row in gap_rows:
        if row["cwid"] not in flagged:
            continue
        total[row["cwid"]] += 1
        if not row["has_synopsis"]:
            unscored[row["cwid"]] += 1

    candidates = sorted(
        ((cwid, unscored[cwid], total[cwid]) for cwid in unscored if unscored[cwid] > 0),
        key=lambda r: (r[1], r[2]),
    )

    print("\nTop-20 smallest unscored work sets among flagged CWIDs:")
    print(f"{'CWID':<12} {'unscored':>10} {'total':>8} {'est_cost_usd':>14}")
    print("-" * 48)
    for cwid, u, t in candidates[:20]:
        est = u * 0.018
        print(f"{cwid:<12} {u:>10} {t:>8} {est:>13.3f}")

    print(f"\nFlagged CWIDs with >0 unscored: {len(candidates)} / {len(flagged)}")
    if candidates:
        c, u, t = candidates[0]
        print(f"Smallest candidate: {c}  unscored={u}  total={t}  est_cost=${u * 0.018:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

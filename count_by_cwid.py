"""Scan reciterai TOPIC# records and produce per-CWID activity counts
broken down by topic and primary_subtopic_id.

Phase 12 D-13 note: as of Phase 12, this script writes THREE subtopic CSVs:
  - faculty_subtopic_counts_exclusive.csv  (new canonical name)
  - faculty_subtopic_counts_inclusive.csv  (new inclusive aggregation)
  - cwid_subtopic_counts.csv              (legacy dual-write for one cycle)

The legacy file is byte-identical to the exclusive file so that any existing
reader that looks for cwid_subtopic_counts.csv continues to work during the
deprecation window. It will be removed in a later phase.

The cwid_topic_counts.csv emission is unchanged (D-13 scope is subtopic-level only).
"""
import csv
from collections import defaultdict
from pathlib import Path

import boto3

# Phase 12 D-13 CSV name constants.
NEW_EXCLUSIVE_CSV = "faculty_subtopic_counts_exclusive.csv"
NEW_INCLUSIVE_CSV = "faculty_subtopic_counts_inclusive.csv"
LEGACY_CSV = "cwid_subtopic_counts.csv"   # Phase 12 D-13 dual-write; remove in a later phase

TABLE = "reciterai"


def write_subtopic_csvs(
    exclusive_counts: dict[tuple[str, str], int],
    inclusive_counts: dict[tuple[str, str], int],
    dest_dir: Path | None = None,
) -> None:
    """Write exclusive, inclusive, and legacy subtopic CSV files.

    Args:
        exclusive_counts: {(personIdentifier, primary_subtopic_id): n_activities}
            Counts derived from primary_subtopic_id only.
        inclusive_counts: {(personIdentifier, subtopic_id): n_activities}
            Counts derived from all above-floor subtopic assignments (inclusive).
        dest_dir: Directory to write CSV files (default: current working directory).

    Phase 12 D-13: writes faculty_subtopic_counts_exclusive.csv (canonical),
    faculty_subtopic_counts_inclusive.csv (inclusive), and cwid_subtopic_counts.csv
    (legacy dual-write, byte-identical to exclusive). The legacy file will be dropped
    in a later phase once all readers have migrated to the new name.
    """
    if dest_dir is None:
        dest_dir = Path(".")

    # Sort rows for deterministic output.
    exclusive_rows = sorted(exclusive_counts.items(), key=lambda kv: (kv[0][0], -kv[1]))
    inclusive_rows = sorted(inclusive_counts.items(), key=lambda kv: (kv[0][0], -kv[1]))

    exclusive_path = dest_dir / NEW_EXCLUSIVE_CSV
    inclusive_path = dest_dir / NEW_INCLUSIVE_CSV
    legacy_path = dest_dir / LEGACY_CSV

    # Write exclusive CSV (new canonical name).
    with open(exclusive_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["personIdentifier", "primary_subtopic_id", "n_activities"])
        for (cwid, s), n in exclusive_rows:
            w.writerow([cwid, s, n])

    # Write inclusive CSV.
    with open(inclusive_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["personIdentifier", "subtopic_id", "n_activities"])
        for (cwid, s), n in inclusive_rows:
            w.writerow([cwid, s, n])

    # Phase 12 D-13 dual-write: legacy file is byte-identical to exclusive.
    exclusive_bytes = exclusive_path.read_bytes()
    legacy_path.write_bytes(exclusive_bytes)


if __name__ == "__main__":
    session = boto3.Session()
    client = session.client("dynamodb")

    topic_counts: dict[tuple[str, str], int] = defaultdict(int)   # (cwid, topic_id) -> n
    # CR-03: split into two distinct dicts so the inclusive CSV actually reflects
    # the inclusive contract (counts every above-floor subtopic_id, not just
    # primary_subtopic_id). Previously both dicts were the same data, making
    # faculty_subtopic_counts_inclusive.csv byte-identical to
    # faculty_subtopic_counts_exclusive.csv.
    subtopic_counts: dict[tuple[str, str], int] = defaultdict(int)            # (cwid, primary_subtopic_id) -> n
    inclusive_subtopic_counts: dict[tuple[str, str], int] = defaultdict(int)  # (cwid, subtopic_id) -> n

    paginator = client.get_paginator("scan")
    # CR-03: project subtopic_ids as well so the inclusive aggregation can see
    # every above-floor subtopic per activity row (D-13/D-15 contract). The
    # projection cost is a single string-set attribute per row.
    pages = paginator.paginate(
        TableName=TABLE,
        ProjectionExpression="#PK, faculty_uid, primary_subtopic_id, subtopic_ids",
        ExpressionAttributeNames={"#PK": "PK"},
    )

    scanned = 0
    topic_rows = 0
    subtopic_rows = 0
    inclusive_rows = 0
    for page in pages:
        for item in page.get("Items", []):
            scanned += 1
            pk = item.get("PK", {}).get("S", "")
            if not pk.startswith("TOPIC#"):
                continue
            topic_id = pk[len("TOPIC#"):]
            cwid_raw = item.get("faculty_uid", {}).get("S", "")
            if not cwid_raw:
                continue
            cwid = cwid_raw[len("cwid_"):] if cwid_raw.startswith("cwid_") else cwid_raw
            topic_counts[(cwid, topic_id)] += 1
            topic_rows += 1
            sub = item.get("primary_subtopic_id", {}).get("S")
            if sub:
                subtopic_counts[(cwid, sub)] += 1
                subtopic_rows += 1
            # CR-03: inclusive aggregation — scan subtopic_ids list (D-15 uniform full
            # weight per above-floor subtopic). assign_subtopics already filters
            # subtopic_ids[] to above-floor (D-16), so no extra confidence check here.
            # subtopic_ids may arrive as either a DynamoDB list (L of S) or string-set
            # (SS) depending on the producer; handle both shapes defensively.
            sids_attr = item.get("subtopic_ids", {})
            sid_list_raw = sids_attr.get("L") or sids_attr.get("SS") or []
            for sid_entry in sid_list_raw:
                if isinstance(sid_entry, dict):
                    sid_val = sid_entry.get("S")
                else:
                    sid_val = sid_entry
                if sid_val:
                    inclusive_subtopic_counts[(cwid, sid_val)] += 1
                    inclusive_rows += 1
        if scanned % 20000 < 1000:
            print(
                f"  scanned={scanned:,} topic_rows={topic_rows:,} "
                f"subtopic_rows={subtopic_rows:,} inclusive_rows={inclusive_rows:,}"
            )

    print(f"\nTotal scanned: {scanned:,}")
    print(f"TOPIC# activity rows: {topic_rows:,}")
    print(f"Activity rows with primary_subtopic_id: {subtopic_rows:,}")
    print(f"Inclusive (cwid, subtopic) increments: {inclusive_rows:,}")
    print(f"Distinct (cwid, topic) pairs:              {len(topic_counts):,}")
    print(f"Distinct (cwid, primary_subtopic) pairs:   {len(subtopic_counts):,}")
    print(f"Distinct (cwid, subtopic) pairs inclusive: {len(inclusive_subtopic_counts):,}")

    with open("cwid_topic_counts.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["personIdentifier", "topic_id", "n_activities"])
        for (cwid, t), n in sorted(topic_counts.items(), key=lambda x: (x[0][0], -x[1])):
            w.writerow([cwid, t, n])

    # Write the three subtopic CSVs (exclusive + inclusive + legacy dual-write).
    # CR-03: inclusive_subtopic_counts is a TRUE inclusive aggregation derived from
    # subtopic_ids[] on each activity row (not a copy of exclusive). The legacy
    # CSV remains byte-identical to exclusive per D-13 dual-write contract.
    write_subtopic_csvs(dict(subtopic_counts), dict(inclusive_subtopic_counts))

    print(
        f"\nWrote cwid_topic_counts.csv, {NEW_EXCLUSIVE_CSV}, {NEW_INCLUSIVE_CSV}, "
        f"and {LEGACY_CSV} (legacy dual-write, Phase 12 D-13 deprecation window)"
    )

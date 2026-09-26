"""Backfill ``author_position`` onto ``TOPIC#`` rows from ``analysis_summary_author``.

## Why

SPS ranks "Scholars in this area" on first/senior authorships, and
``spotlight.pool_ranker`` prefers them for the lede. Both read
``author_position`` off the TOPIC# row. Only a one-off enrichment script ever
wrote it (2026-05-06, deleted in #143), so every row minted since — onboarding
re-scores, cold rebuilds, ``score_new_topics`` — lacks it. As of 2026-09-26
SPS prod saw 69% of rows blank and four topics 100% blank.
``utils.topic_records`` now writes it at birth; this repairs existing rows.

## Behavior

1. Read the (pmid, cwid) -> position map from ``analysis_summary_author``,
   resolved by ``utils.topic_records.author_positions_by_cwid`` so a backfilled
   row matches what a fresh mint would write. Unlike the mint's
   ``AUTHOR_MAPPING_SQL`` there is no full-time-faculty filter: a row minted
   while its CWID was full-time faculty still gets its position after the
   person's status changes.
2. Find TOPIC# rows whose ``author_position`` is missing or ``""`` (a scan, or
   a per-topic query with ``--topics``).
3. ``UpdateItem SET author_position``, conditional on the row still existing
   and still being blank. Never ``PutItem``: that would wipe the subtopic and
   impact attributes written by other stages. A non-blank value is never
   overwritten.

Rows with no (pmid, cwid) match in the author map are counted, not touched.

## Usage

    # dry-run, whole table
    python -m cli.backfill_topic_author_position --all
    # dry-run, just the topics SPS reported 100% blank
    python -m cli.backfill_topic_author_position \\
        --topics anesthesiology,basic_neuroscience,pain_medicine,clinical_neurology
    # execute
    python -m cli.backfill_topic_author_position --all --apply
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import boto3
from botocore.exceptions import ClientError

from utils.topic_records import author_positions_by_cwid

TABLE_NAME = "reciterai"
REGION = "us-east-1"
TOPIC_PK_PREFIX = "TOPIC#"
_BLANK = "attribute_not_exists(author_position) OR author_position = :e"
POSITIONS_SQL = """
SELECT pmid, personIdentifier AS cwid, authorPosition
FROM analysis_summary_author
"""


def read_author_mapping() -> dict:
    """``{pmid: [{cwid, position}]}`` for every authorship, faculty or not."""
    from sqlalchemy import text

    from utils.sql_queries import get_db_connection

    conn = get_db_connection()
    try:
        mapping: dict = {}
        for row in conn.execute(text(POSITIONS_SQL)).mappings():
            mapping.setdefault(str(row["pmid"]), []).append(
                {"cwid": str(row["cwid"]), "position": row["authorPosition"]})
        return mapping
    finally:
        conn.close()


def build_position_map(author_mapping: dict) -> dict:
    """``{(pmid, cwid): position}`` from ``{pmid: [{cwid, position}]}``."""
    return {
        (str(pmid), cwid): pos
        for pmid, authors in author_mapping.items()
        for cwid, pos in author_positions_by_cwid(authors).items()
    }


def _paginate(call, **kw):
    ek = None
    while True:
        if ek:
            kw["ExclusiveStartKey"] = ek
        r = call(**kw)
        yield from r.get("Items", [])
        ek = r.get("LastEvaluatedKey")
        if not ek:
            return


def blank_topic_rows(client, topics: list[str] | None):
    """TOPIC# rows with a missing or ``""`` author_position."""
    base = dict(
        TableName=TABLE_NAME,
        ProjectionExpression="PK, SK, pmid, faculty_uid",
    )
    if topics is None:
        yield from _paginate(
            client.scan, **base,
            FilterExpression=f"begins_with(PK, :t) AND ({_BLANK})",
            ExpressionAttributeValues={":t": {"S": TOPIC_PK_PREFIX}, ":e": {"S": ""}},
        )
        return
    for topic in topics:
        yield from _paginate(
            client.query, **base,
            KeyConditionExpression="PK = :pk",
            FilterExpression=_BLANK,
            ExpressionAttributeValues={":pk": {"S": f"{TOPIC_PK_PREFIX}{topic}"}, ":e": {"S": ""}},
        )


def backfill(client, rows, position_map: dict, *, apply: bool) -> dict:
    """Returns ``{"blank", "written", "unmatched", "raced", "by_topic"}``;
    ``by_topic`` is ``{topic: Counter(blank=, unmatched=)}``."""
    tot = Counter()
    by_topic: dict = {}
    for r in rows:
        topic = r["PK"]["S"].removeprefix(TOPIC_PK_PREFIX)
        per = by_topic.setdefault(topic, Counter())
        tot["blank"] += 1
        per["blank"] += 1
        pmid = r.get("pmid", {}).get("S", "")
        cwid = r.get("faculty_uid", {}).get("S", "").removeprefix("cwid_")
        pos = position_map.get((pmid, cwid))
        if pos is None:
            tot["unmatched"] += 1
            per["unmatched"] += 1
            continue
        if not apply:
            continue
        try:
            client.update_item(
                TableName=TABLE_NAME,
                Key={"PK": r["PK"], "SK": r["SK"]},
                UpdateExpression="SET author_position = :p",
                # attribute_exists(PK): UpdateItem upserts, so a row deleted
                # mid-run (onboarding re-score) must not be resurrected as a stub.
                ConditionExpression=f"attribute_exists(PK) AND ({_BLANK})",
                ExpressionAttributeValues={":p": {"S": pos}, ":e": {"S": ""}},
            )
            tot["written"] += 1
        except ClientError as e:
            if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            tot["raced"] += 1
    return {**{k: tot[k] for k in ("blank", "written", "unmatched", "raced")},
            "by_topic": by_topic}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true", help="Scan every TOPIC# row.")
    g.add_argument("--topics", help="Comma-separated topic ids (per-topic Query).")
    ap.add_argument("--apply", action="store_true", help="Execute writes. Default is dry-run.")
    args = ap.parse_args(argv)

    print("[1/3] Reading author positions from ReciterDB ...")
    position_map = build_position_map(read_author_mapping())
    if not position_map:
        raise SystemExit("analysis_summary_author returned no rows; refusing to run")
    topics = None if args.all else [t.strip() for t in args.topics.split(",") if t.strip()]
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[2/3] Finding blank TOPIC# rows ({'all' if topics is None else ', '.join(topics)}) ...")
    client = boto3.client("dynamodb", region_name=REGION)
    res = backfill(client, blank_topic_rows(client, topics), position_map, apply=args.apply)

    print(f"[3/3] === TOPIC# author_position backfill — {mode} ===")
    print(f"{'topic':<40} {'blank':>8} {'unmatched':>10}")
    for topic, c in sorted(res["by_topic"].items(), key=lambda kv: -kv[1]["blank"]):
        print(f"{topic:<40} {c['blank']:>8} {c['unmatched']:>10}")
    print()
    print(f"Rows blank (missing or \"\"):                {res['blank']}")
    print(f"Rows with no (pmid, cwid) in ReciterDB:     {res['unmatched']}")
    if args.apply:
        print(f"Rows WRITTEN this run:                      {res['written']}")
        print(f"Rows conditional-skipped (raced/deleted):   {res['raced']}")
    else:
        print(f"Rows that WOULD be written (--apply):       {res['blank'] - res['unmatched']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

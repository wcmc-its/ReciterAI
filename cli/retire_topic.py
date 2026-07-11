#!/usr/bin/env python3
"""Delete a retired taxonomy topic's DynamoDB footprint.

Removes the topic's `TOPIC#{id}` partition and any
`SUBTOPIC_SCORE#{id}#*` / `SUBTOPIC_SCORE_INCLUSIVE#{id}#*` partitions. No other
ReciterAI code deletes a fully-removed topic's rows: every pipeline stage upserts,
and `aggregate_subtopic_scores`'s orphan-prune only enumerates *live* topics, so a
retired topic is never reached. This is that missing tool (#307).

Ordering guard: refuses to run while the topic is still defined in taxonomy_v2.json.
Deleting a live topic's rows is pointless — the next cold-run re-mints them from the
taxonomy. Remove the topic from taxonomy_v2.json first, then run this.

Dry-run by default; pass --execute to delete. Point at an environment with --table.

  cli/retire_topic.py --topic hematology_medical_oncology              # dry-run
  cli/retire_topic.py --topic hematology_medical_oncology --execute    # delete
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

TOPIC_PK = "TOPIC#{topic}"
SUBTOPIC_PREFIX_TEMPLATES = (
    "SUBTOPIC_SCORE#{topic}#",
    "SUBTOPIC_SCORE_INCLUSIVE#{topic}#",
)


def subtopic_prefixes(topic: str) -> list[str]:
    return [t.format(topic=topic) for t in SUBTOPIC_PREFIX_TEMPLATES]


def belongs_to_topic(pk: str, topic: str) -> bool:
    """True if a SUBTOPIC_SCORE[_INCLUSIVE]# PK belongs to `topic`.

    The trailing '#' in each prefix is the slug boundary: it is what keeps
    `hematology_medical_oncology` from ever matching the separate benign
    `hematology` topic (or vice versa).
    """
    return any(pk.startswith(p) for p in subtopic_prefixes(topic))


def topic_in_taxonomy(topic: str, taxonomy_path: str | Path) -> bool:
    data = json.loads(Path(taxonomy_path).read_text())
    return topic in {t["id"] for t in data["topics"]}


def _collect_and_delete(keys: list[tuple[str, str]], table, dry_run: bool) -> int:
    if not dry_run and keys:
        with table.batch_writer() as bw:
            for pk, sk in keys:
                bw.delete_item(Key={"PK": pk, "SK": sk})
    return len(keys)


def delete_topic_partition(table, topic: str, dry_run: bool) -> int:
    from boto3.dynamodb.conditions import Key

    pk = TOPIC_PK.format(topic=topic)
    keys: list[tuple[str, str]] = []
    kw = {"KeyConditionExpression": Key("PK").eq(pk), "ProjectionExpression": "PK, SK"}
    while True:
        r = table.query(**kw)
        keys += [(i["PK"], i["SK"]) for i in r["Items"]]
        if "LastEvaluatedKey" not in r:
            break
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    return _collect_and_delete(keys, table, dry_run)


def delete_subtopic_score_partitions(table, topic: str, dry_run: bool) -> int:
    # ponytail: full-table scan with a PK-prefix filter. This is a one-shot
    # retirement tool, not a hot path; a GSI would be overkill for a rare op.
    from boto3.dynamodb.conditions import Attr

    filt = None
    for p in subtopic_prefixes(topic):
        cond = Attr("PK").begins_with(p)
        filt = cond if filt is None else (filt | cond)
    keys: list[tuple[str, str]] = []
    kw = {"FilterExpression": filt, "ProjectionExpression": "PK, SK"}
    while True:
        r = table.scan(**kw)
        keys += [(i["PK"], i["SK"]) for i in r["Items"]]
        if "LastEvaluatedKey" not in r:
            break
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    return _collect_and_delete(keys, table, dry_run)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--topic", required=True, help="exact topic id, e.g. hematology_medical_oncology")
    ap.add_argument("--table", default=os.environ.get("RECITERAI_TABLE", "reciterai"))
    ap.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    ap.add_argument("--taxonomy", default="taxonomy_v2.json", help="path to taxonomy_v2.json for the ordering guard")
    ap.add_argument("--execute", action="store_true", help="actually delete (default: dry-run)")
    ap.add_argument("--allow-live-topic", action="store_true",
                    help="override the taxonomy-presence guard (unsafe: a cold-run will re-mint the rows)")
    args = ap.parse_args()
    dry_run = not args.execute

    if topic_in_taxonomy(args.topic, args.taxonomy) and not args.allow_live_topic:
        raise SystemExit(
            f"REFUSING: '{args.topic}' is still defined in {args.taxonomy}. Remove it there "
            f"first (#307 ordering) or a cold-run will re-mint these rows. "
            f"Override with --allow-live-topic only if you know why."
        )

    import boto3

    table = boto3.resource("dynamodb", region_name=args.region).Table(args.table)
    topic_rows = delete_topic_partition(table, args.topic, dry_run)
    subtopic_rows = delete_subtopic_score_partitions(table, args.topic, dry_run)
    print(json.dumps({
        "topic": args.topic,
        "table": args.table,
        "dry_run": dry_run,
        "topic_rows": topic_rows,
        "subtopic_score_rows": subtopic_rows,
        "action": "would delete" if dry_run else "deleted",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

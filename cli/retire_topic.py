#!/usr/bin/env python3
"""Delete a retired taxonomy topic's DynamoDB footprint.

Removes the topic's `TOPIC#{id}` partition and any
`SUBTOPIC_SCORE#{id}#*` / `SUBTOPIC_SCORE_INCLUSIVE#{id}#*` partitions. No other
ReciterAI code deletes a fully-removed topic's rows: every pipeline stage upserts,
and `aggregate_subtopic_scores`'s orphan-prune only enumerates *live* topics, so a
retired topic is never reached. This is that missing tool (#307).

Ordering guard: refuses to run while the topic is still defined in taxonomy_v2.json.
Deleting a live topic's rows is pointless — they get re-minted from the taxonomy.
Remove the topic from taxonomy_v2.json first, then run this.

DELETING ROWS IS NOT THE LAST STEP. The re-mint vector is not only the cold run:
the WEEKLY HOT PATH (`cron(0 12 ? * MON *)`) scores against the taxonomy baked
into the `reciterai-hot-*` Lambda ZIPS. Editing the repo does not change them.
Retiring `hematology_medical_oncology` on 2026-07-10 without redeploying those
Lambdas re-created its rows every Monday for three weeks — 07-13, 07-20, 07-27 —
and nothing reported it (#352). Deleting rows before redeploying is the specific
mistake that cost those three weeks.

Full ordering is `docs/adr-taxonomy-change-propagation.md` (D1). The short form:

  1. remove the topic from taxonomy_v2.json
  2. REBUILD + REDEPLOY every artifact bundling it — the three hot-path Lambda
     zips (scripts/build_lambda_zips.sh) and the Docker image
  3. VERIFY each deployed artifact's taxonomy actually changed
  4. only then run this tool (it deletes the rows AND refreshes the
     TAXONOMY#{version}/META catalog record downstream consumers read)
  5. republish the hierarchy (operator-gated cold run)
  6. notify SPS (Aurora DELETE + etl:dynamodb)

Steps 2-3 are the ones that get skipped. `scripts/check_taxonomy_data_drift.py`
detects the result within a day; it is a backstop, not a substitute for step 2.

Dry-run by default; pass --execute to delete. Point at an environment with --table.

  cli/retire_topic.py --topic hematology_medical_oncology              # dry-run
  cli/retire_topic.py --topic hematology_medical_oncology --execute    # delete
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Run directly (`cli/retire_topic.py ...`) sys.path[0] is cli/, not the repo
# root, so `from cli.load_dynamodb import ...` fails. Under pytest the root is
# already on the path, which is why the unit tests cannot see this.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Imported at module scope on purpose. As a function-local import it sat AFTER
# the row deletions, so an import failure would delete the rows and then skip
# the catalog refresh — reintroducing the exact desync this tool now prevents.
# Module scope also means `--help` exercises it, so the import path is covered.
from cli.load_dynamodb import build_taxonomy_record  # noqa: E402

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


def refresh_taxonomy_meta(
    table_name: str, region: str, *, taxonomy_path: str | Path, dry_run: bool
) -> dict:
    """Rewrite `TAXONOMY#{version}/META` from the on-disk taxonomy.

    That record is the *catalog* — a single item listing every topic's id,
    label and description — and it is what downstream consumers read to
    enumerate topics. The Scholars Profile System builds its `topic` table from
    it nightly.

    Deleting a retired topic's rows without refreshing it leaves the catalog
    advertising a topic that no longer exists. That is not hypothetical: on
    2026-08-01 `score_new_topics.py` refreshed this record when #346 added two
    topics, then #348 retired `neuroscience_neurology` and this tool deleted its
    rows but left the record at 70 topics, still listing it. SPS kept upserting
    it and could not prune it — its prune only fires for topics *absent* from
    the catalog — so it showed 68 research areas against a published 67, and the
    retired one was headed for a permanent zero-count tile once its stale
    publication rows drained.

    Idempotent: rewrites the whole item from the taxonomy file, so running it
    twice is the same as running it once, and running it when nothing changed
    is a no-op in content.
    """
    import boto3

    taxonomy = json.loads(Path(taxonomy_path).read_text())
    item = build_taxonomy_record(taxonomy)
    version = taxonomy["taxonomy_version"]
    summary = {
        "record": f"TAXONOMY#{version}/META",
        "topic_count": len(taxonomy["topics"]),
        "written": not dry_run,
    }
    if not dry_run:
        boto3.client("dynamodb", region_name=region).put_item(
            TableName=table_name, Item=item
        )
    return summary


def main() -> int:
    # Full docstring, not just its first line: the ordering below is the whole
    # point of the tool, and a `--help` that hides it is how #352 happened.
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
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
    meta = refresh_taxonomy_meta(
        args.table, args.region, taxonomy_path=args.taxonomy, dry_run=dry_run
    )
    print(json.dumps({
        "topic": args.topic,
        "table": args.table,
        "dry_run": dry_run,
        "topic_rows": topic_rows,
        "subtopic_score_rows": subtopic_rows,
        "taxonomy_meta": meta,
        "action": "would delete" if dry_run else "deleted",
    }, indent=2))

    # Terminating on "deleted" is what taught the wrong causal model (ADR trap 2).
    # stderr so it never contaminates the JSON on stdout.
    if not dry_run:
        print(
            "\nDeleting rows is NOT the last step. Still to do:\n"
            "  - rebuild + redeploy every artifact bundling taxonomy_v2.json\n"
            "    (scripts/build_lambda_zips.sh -> the three reciterai-hot-* zips; Docker image)\n"
            "  - verify each deployed artifact's taxonomy actually changed\n"
            "  - republish the hierarchy (operator-gated cold run)\n"
            "  - notify SPS (Aurora DELETE + etl:dynamodb)\n"
            "If the hot-path Lambdas are not redeployed, the Monday run re-mints\n"
            "these rows. Ordering: docs/adr-taxonomy-change-propagation.md (D1).\n"
            "Check the result: scripts/check_taxonomy_data_drift.py\n",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

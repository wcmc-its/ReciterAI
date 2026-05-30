"""Repopulate synopsis/title/journal/year onto bare TOPIC# activity rows.

Root cause (2026-05-26): a batch of PMIDs was topic-scored before their
synopsis existed, so `_materialize_topic_rows` wrote bare TOPIC# rows
(PK, SK, faculty_uid, pmid, rationale, score). The synopsis later landed on
the IMPACT# row but was never propagated back. assign_subtopics reads
title/synopsis OFF the TOPIC# row, so on bare rows it feeds Haiku an empty
prompt and assigns nothing.

This backfill copies synopsis from the authoritative IMPACT# row and
title/journal/year from MariaDB onto every TOPIC# row missing a synopsis,
unblocking subtopic assignment. Idempotent: only touches rows whose
`synopsis` attribute is absent.

Usage:
    python scripts/debug/repopulate_topic_text.py --topic melanoma_skin_cancer --dry-run
    python scripts/debug/repopulate_topic_text.py --topic melanoma_skin_cancer
    python scripts/debug/repopulate_topic_text.py            # all topics
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from boto3.dynamodb.conditions import Attr, Key  # noqa: E402

from utils.dynamodb_helpers import (  # noqa: E402
    TABLE_NAME, get_table, get_dynamo_client, fetch_synopsis_records,
)
from utils.db import get_engine  # noqa: E402
from utils.sql_queries import fetch_publications_for_enrichment  # noqa: E402


def scan_bare_rows(table, topic: str | None) -> list[dict]:
    """TOPIC# rows missing a synopsis attribute. Returns [{PK, SK, pmid}]."""
    rows, lek = [], None
    while True:
        if topic:
            kw = dict(
                KeyConditionExpression=Key("PK").eq(f"TOPIC#{topic}")
                & Key("SK").begins_with("SCORE#"),
                FilterExpression=Attr("synopsis").not_exists(),
                ProjectionExpression="PK, SK, pmid",
            )
            if lek:
                kw["ExclusiveStartKey"] = lek
            resp = table.query(**kw)
        else:
            kw = dict(
                FilterExpression=Attr("PK").begins_with("TOPIC#")
                & Attr("synopsis").not_exists(),
                ProjectionExpression="PK, SK, pmid",
            )
            if lek:
                kw["ExclusiveStartKey"] = lek
            resp = table.scan(**kw)
        rows.extend(resp.get("Items", []))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", default=None, help="Limit to one topic_id.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    table = get_table(TABLE_NAME)
    bare = scan_bare_rows(table, args.topic)
    pmids = sorted({str(r["pmid"]) for r in bare if r.get("pmid")})
    print(f"bare TOPIC# rows: {len(bare)} across {len(pmids)} distinct PMIDs"
          + (f" (topic={args.topic})" if args.topic else ""))
    if not bare:
        return 0

    # synopsis from IMPACT#, title/journal/year from MariaDB
    syn = fetch_synopsis_records(get_dynamo_client(), pmids)
    pubs_rows = fetch_publications_for_enrichment(get_engine(), pmids)
    pubs = {str(r["pmid"]): r for r in pubs_rows}
    print(f"resolved: synopsis for {len(syn)} PMIDs, "
          f"MariaDB metadata for {len(pubs)} PMIDs")

    updated = skipped_no_syn = 0
    for r in bare:
        pmid = str(r["pmid"])
        s = (syn.get(pmid) or {}).get("synopsis")
        if not s:
            skipped_no_syn += 1
            continue
        meta = pubs.get(pmid, {})
        names = {"#syn": "synopsis", "#ti": "title", "#jr": "journal", "#yr": "year"}
        vals = {":syn": s}
        sets = ["#syn = :syn"]
        if meta.get("articleTitle"):
            sets.append("#ti = :ti"); vals[":ti"] = str(meta["articleTitle"])
        if meta.get("journalTitleVerbose"):
            sets.append("#jr = :jr"); vals[":jr"] = str(meta["journalTitleVerbose"])
        if meta.get("articleYear") is not None:
            sets.append("#yr = :yr"); vals[":yr"] = str(meta["articleYear"])
        used_names = {k: v for k, v in names.items()
                      if any(k in s_ for s_ in sets)}
        if args.dry_run:
            updated += 1
            continue
        table.update_item(
            Key={"PK": r["PK"], "SK": r["SK"]},
            UpdateExpression="SET " + ", ".join(sets),
            ExpressionAttributeNames=used_names,
            ExpressionAttributeValues=vals,
        )
        updated += 1

    verb = "would update" if args.dry_run else "updated"
    print(f"{verb} {updated} rows; skipped {skipped_no_syn} (no IMPACT# synopsis)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

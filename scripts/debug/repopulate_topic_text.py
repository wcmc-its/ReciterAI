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
    """TOPIC# rows missing any of synopsis / title / journal / year.

    Returns [{PK, SK, pmid, <whichever of the four are present>}] — the
    caller writes only the attributes a row actually lacks.

    `year` joined the filter after SPS found five partitions (4,765 rows)
    at 0% year coverage: those rows all HAVE a synopsis, so the original
    synopsis-only filter matched none of them. `year` was never written by
    utils.topic_records at all until that was fixed; the rows that do carry
    it are the ones this script repaired in its 2026-05 run.
    """
    names = {"#yr": "year"}  # `year` is a DynamoDB reserved word
    projection = "PK, SK, pmid, synopsis, title, journal, #yr"
    missing_any = (
        Attr("synopsis").not_exists()
        | Attr("title").not_exists()
        | Attr("journal").not_exists()
        | Attr("year").not_exists()
        # This script's own 2026-05 run wrote `str(articleYear)`, which the
        # resource client stores as S. spotlight.pool_ranker reads year via
        # .get("N", "0"), so an S-typed year is just as invisible to it as a
        # missing one. Re-type those rows to N.
        | Attr("year").attribute_type("S")
    )
    rows, lek = [], None
    while True:
        if topic:
            kw = dict(
                KeyConditionExpression=Key("PK").eq(f"TOPIC#{topic}")
                & Key("SK").begins_with("SCORE#"),
                FilterExpression=missing_any,
                ProjectionExpression=projection,
                ExpressionAttributeNames=names,
            )
            if lek:
                kw["ExclusiveStartKey"] = lek
            resp = table.query(**kw)
        else:
            kw = dict(
                FilterExpression=Attr("PK").begins_with("TOPIC#") & missing_any,
                ProjectionExpression=projection,
                ExpressionAttributeNames=names,
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

    updated = nothing_to_do = 0
    filled = {"synopsis": 0, "title": 0, "journal": 0, "year": 0}
    for r in bare:
        pmid = str(r["pmid"])
        meta = pubs.get(pmid, {})
        names = {"#syn": "synopsis", "#ti": "title", "#jr": "journal", "#yr": "year"}
        vals: dict = {}
        sets: list[str] = []
        # Only write attributes the row actually lacks — a row already
        # carrying a good synopsis must not be overwritten from a stale or
        # missing IMPACT# read, and re-writing the rest buys nothing.
        s = (syn.get(pmid) or {}).get("synopsis")
        if not r.get("synopsis") and s:
            sets.append("#syn = :syn"); vals[":syn"] = s
        if not r.get("title") and meta.get("articleTitle"):
            sets.append("#ti = :ti"); vals[":ti"] = str(meta["articleTitle"])
        if not r.get("journal") and meta.get("journalTitleVerbose"):
            sets.append("#jr = :jr"); vals[":jr"] = str(meta["journalTitleVerbose"])
        # A str here is an S-typed year from the 2026-05 run; rewrite it as an
        # int so it lands as N. int() also normalizes the resource client's
        # Decimal for already-correct rows, which simply no-ops.
        year_now = r.get("year")
        if (year_now is None or isinstance(year_now, str)) and meta.get("articleYear") is not None:
            sets.append("#yr = :yr"); vals[":yr"] = int(meta["articleYear"])
        if not sets:
            nothing_to_do += 1
            continue
        for tok, attr in names.items():
            if any(tok in s_ for s_ in sets):
                filled[attr] += 1
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
    print(f"{verb} {updated} rows; {nothing_to_do} had no resolvable gap")
    print("  attributes filled: " + ", ".join(f"{k}={v}" for k, v in filled.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

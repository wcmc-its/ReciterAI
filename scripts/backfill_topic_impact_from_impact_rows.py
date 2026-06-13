#!/usr/bin/env python3
"""Backfill ``impact_score`` / ``impact_justification`` onto ``TOPIC#`` rows from
the authoritative ``IMPACT#pmid_<pmid>`` rows.

## Why

``impact_score`` is enriched once per PMID and stored on the
``IMPACT#pmid_<pmid>`` (SK=``SCORE``) row by the daily job (#37 dual-write).
The per-(topic, pmid, cwid) ``TOPIC#`` rows carry a *copy* of that score so
downstream readers (``spotlight/pool_ranker``, ``cli/aggregate_subtopic_scores``)
don't have to join. But the copy is only written when the impact score already
existed at TOPIC#-row build time. Recent PMIDs whose ``TOPIC#`` rows were
written before their impact enrichment landed end up with **no ``impact_score``
attribute** — ~29.5% of all ``TOPIC#`` rows as of 2026-06.

This was historically tolerated because ``pool_ranker`` defaulted a missing
``impact_score`` to 0. That default is no longer harmless: once representative-
paper ranking blends impact with relevance (``utils.scoring.article_score``), an
``impact_score`` of 0 forces ``article_score`` to 0 and the paper is dropped from
the spotlight pool entirely — so genuinely on-topic papers vanish from cards
purely because of this join gap.

This script repairs the gap by copying the authoritative impact values from the
``IMPACT#`` row onto every ``TOPIC#`` row for the PMID that is missing them.

## Behavior (per PMID)

1. Read ``IMPACT#pmid_<pmid>`` (SK=``SCORE``). If it has no ``impact_score``,
   the PMID is genuinely un-enriched — **skip** (out of scope; the daily job
   owns enrichment). Counted as ``no_impact_source``.
2. Find every ``TOPIC#`` row for the PMID via the ``PmidIndex`` GSI.
3. For each ``TOPIC#`` row **missing** ``impact_score``, ``UpdateItem`` SET
   ``impact_score`` (+ ``impact_justification`` when the IMPACT# row has a
   ``justification``), conditional on ``attribute_not_exists(impact_score)``.

Idempotent: the conditional makes a re-run a no-op, and a row that already has
an impact score is never overwritten. ``ConditionalCheckFailedException`` is
caught and counted as "already covered".

## Usage

    # the 29 EHR-card PMIDs (default dry-run)
    python3 scripts/backfill_topic_impact_from_impact_rows.py --pmids-file pmids.txt
    python3 scripts/backfill_topic_impact_from_impact_rows.py --pmids 41790468,36999191
    # execute
    python3 scripts/backfill_topic_impact_from_impact_rows.py --pmids-file pmids.txt --apply
    # whole table (systemic gap) — scan TOPIC# rows for missing impact_score first
    python3 scripts/backfill_topic_impact_from_impact_rows.py --all --apply
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

TABLE_NAME = "reciterai"
REGION = "us-east-1"
IMPACT_PK_PREFIX = "IMPACT#pmid_"
IMPACT_SK = "SCORE"
TOPIC_PK_PREFIX = "TOPIC#"


def _client():
    return boto3.client("dynamodb", region_name=REGION)


def read_impact(client, pmid: str) -> tuple[str, str] | None:
    """Return ``(impact_score_N, justification_S)`` from the IMPACT# row, or None
    if the row is absent or carries no ``impact_score``."""
    r = client.get_item(
        TableName=TABLE_NAME,
        Key={"PK": {"S": f"{IMPACT_PK_PREFIX}{pmid}"}, "SK": {"S": IMPACT_SK}},
        ProjectionExpression="impact_score, justification",
    )
    it = r.get("Item")
    if not it or "impact_score" not in it:
        return None
    score = it["impact_score"].get("N")
    just = it.get("justification", {}).get("S", "")
    return score, just


def topic_rows_for_pmid(client, pmid: str) -> list[dict]:
    """All ``TOPIC#`` rows (PK+SK+impact_score presence) for a PMID via PmidIndex."""
    rows: list[dict] = []
    ek = None
    while True:
        kw = dict(
            TableName=TABLE_NAME,
            IndexName="PmidIndex",
            KeyConditionExpression="pmid = :p",
            ExpressionAttributeValues={":p": {"S": pmid}},
            ProjectionExpression="PK, SK, impact_score",
        )
        if ek:
            kw["ExclusiveStartKey"] = ek
        r = client.query(**kw)
        for it in r.get("Items", []):
            if it.get("PK", {}).get("S", "").startswith(TOPIC_PK_PREFIX):
                rows.append(it)
        ek = r.get("LastEvaluatedKey")
        if not ek:
            break
    return rows


def scan_pmids_missing_impact(client) -> list[str]:
    """Distinct PMIDs that have at least one ``TOPIC#`` row missing impact_score."""
    pmids: set[str] = set()
    ek = None
    while True:
        kw = dict(
            TableName=TABLE_NAME,
            FilterExpression="begins_with(PK,:p) AND attribute_not_exists(impact_score)",
            ExpressionAttributeValues={":p": {"S": TOPIC_PK_PREFIX}},
            ProjectionExpression="pmid",
        )
        if ek:
            kw["ExclusiveStartKey"] = ek
        r = client.scan(**kw)
        for it in r.get("Items", []):
            p = it.get("pmid", {}).get("S")
            if p:
                pmids.add(p)
        ek = r.get("LastEvaluatedKey")
        if not ek:
            break
    return sorted(pmids)


def backfill_pmid(client, pmid: str, *, apply: bool) -> dict:
    """Backfill one PMID. Returns a per-PMID result dict."""
    src = read_impact(client, pmid)
    if src is None:
        return {"pmid": pmid, "status": "no_impact_source", "rows_written": 0,
                "rows_skipped": 0, "rows_already": 0}
    score, just = src
    rows = topic_rows_for_pmid(client, pmid)
    missing = [r for r in rows if "impact_score" not in r]
    already = len(rows) - len(missing)
    written = 0
    cond_skips = 0
    if apply:
        expr = "SET impact_score = :s"
        vals = {":s": {"N": str(score)}}
        if just:
            expr += ", impact_justification = :j"
            vals[":j"] = {"S": just}
        for r in missing:
            try:
                client.update_item(
                    TableName=TABLE_NAME,
                    Key={"PK": r["PK"], "SK": r["SK"]},
                    UpdateExpression=expr,
                    ConditionExpression="attribute_not_exists(impact_score)",
                    ExpressionAttributeValues=vals,
                )
                written += 1
            except ClientError as e:
                if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                    cond_skips += 1
                else:
                    raise
    return {"pmid": pmid, "status": "ok", "impact_score": score,
            "rows_total": len(rows), "rows_missing": len(missing),
            "rows_written": written, "rows_already": already,
            "rows_cond_skip": cond_skips}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--pmids", help="Comma-separated PMIDs.")
    g.add_argument("--pmids-file", help="File with one PMID per line.")
    g.add_argument("--all", action="store_true",
                   help="Scan all TOPIC# rows for missing impact_score (systemic).")
    ap.add_argument("--apply", action="store_true",
                    help="Execute writes. Default is dry-run.")
    ap.add_argument("--limit", type=int, default=None, help="Cap to first N PMIDs.")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")

    client = _client()
    if args.all:
        print("[1/3] Scanning TOPIC# rows for missing impact_score ...")
        pmids = scan_pmids_missing_impact(client)
    elif args.pmids_file:
        pmids = [ln.strip() for ln in Path(args.pmids_file).read_text().splitlines() if ln.strip()]
    else:
        pmids = [p.strip() for p in args.pmids.split(",") if p.strip()]
    if args.limit:
        pmids = pmids[:args.limit]

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"=== TOPIC# impact backfill — {mode} — {len(pmids)} PMID(s) ===")

    tot_missing = tot_written = tot_already = tot_cond = no_src = 0
    for i, pmid in enumerate(pmids, 1):
        res = backfill_pmid(client, pmid, apply=args.apply)
        if res["status"] == "no_impact_source":
            no_src += 1
        else:
            tot_missing += res["rows_missing"]
            tot_written += res["rows_written"]
            tot_already += res["rows_already"]
            tot_cond += res.get("rows_cond_skip", 0)
        if i % 100 == 0:
            print(f"  ... {i}/{len(pmids)} PMIDs processed")

    print()
    print(f"PMIDs with no IMPACT# score (skipped, un-enriched): {no_src}")
    print(f"TOPIC# rows already had impact_score:               {tot_already}")
    print(f"TOPIC# rows MISSING impact_score:                   {tot_missing}")
    if args.apply:
        print(f"TOPIC# rows WRITTEN this run:                       {tot_written}")
        print(f"TOPIC# rows conditional-skipped (raced):            {tot_cond}")
    else:
        print(f"TOPIC# rows that WOULD be written (--apply):        {tot_missing}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

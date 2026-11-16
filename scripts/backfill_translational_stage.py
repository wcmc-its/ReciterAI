#!/usr/bin/env python3
"""One-off backfill: label existing IMPACT# rows with a translational stage.

The synopsis call returns a stage for newly enriched papers. Rows enriched
before that carry a synopsis and no stage. This script classifies each of
those from its title and existing synopsis (no abstract, no new synopsis)
and sets ``translational_stage`` / ``translational_stage_confidence`` on
the row, plus ``translational_stage_source='synopsis'`` so these labels can
be told apart from ones produced with the abstract in hand.

Work set: IMPACT# rows with a non-empty ``synopsis`` and no
``translational_stage``. A row whose paper gets no valid label is left
untouched and will be picked up by a later run.

Idempotency: each write is conditional on the row existing and still having
no ``translational_stage``, so a re-run only spends on rows that are still
unlabelled and never overwrites a label from the daily job.

Usage:
    python3 scripts/backfill_translational_stage.py                 # dry run: classify, write nothing
    python3 scripts/backfill_translational_stage.py --limit 200     # dry run on the first 200
    python3 scripts/backfill_translational_stage.py --apply         # write
    python3 scripts/backfill_translational_stage.py --out labels.csv  # also save every label

A dry run still calls the model (that is the point: it shows the labels and
the cost). Use --limit to keep it small.
"""

from __future__ import annotations

import argparse
import collections
import csv
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from botocore.exceptions import ClientError
from sqlalchemy import bindparam, text

from pipeline_enrichment.llm_call import call_with_fallback, parse_json_lenient
from pipeline_enrichment.prompts import STAGE_FROM_SYNOPSIS_SYSTEM, build_stage_user_content
from pipeline_enrichment.synopsis import _parse_stage
from utils.db import get_engine
from utils.dynamodb_helpers import get_dynamo_client
from utils.llm_cost import cost_for

logger = logging.getLogger(__name__)

TABLE_NAME = "reciterai"
IMPACT_PK_PREFIX = "IMPACT#pmid_"
IMPACT_SK = "SCORE"
STAGE_SOURCE = "synopsis"
TITLE_CHUNK = 1000
WORKERS = 8


def scan_unlabelled(client) -> list[dict[str, str]]:
    """IMPACT# rows with a synopsis and no stage, as {pmid, synopsis}, sorted by pmid."""
    kwargs: dict[str, Any] = {
        "TableName": TABLE_NAME,
        "FilterExpression": "begins_with(PK, :p) AND attribute_exists(synopsis) "
                            "AND attribute_not_exists(translational_stage)",
        "ExpressionAttributeValues": {":p": {"S": IMPACT_PK_PREFIX}},
        "ProjectionExpression": "PK, synopsis",
    }
    rows = []
    while True:
        resp = client.scan(**kwargs)
        for it in resp["Items"]:
            syn = it.get("synopsis", {}).get("S", "").strip()
            if syn:
                rows.append({"pmid": it["PK"]["S"][len(IMPACT_PK_PREFIX):], "synopsis": syn})
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return sorted(rows, key=lambda r: int(r["pmid"]) if r["pmid"].isdigit() else 0)


def fetch_titles(engine, pmids: list[str]) -> dict[str, tuple[str, Optional[str]]]:
    """{pmid: (title, journal)} from reciterdb. Read only."""
    stmt = text(
        "SELECT pmid, articleTitle, journalTitleVerbose FROM analysis_summary_article "
        "WHERE pmid IN :pmids"
    ).bindparams(bindparam("pmids", expanding=True))
    out: dict[str, tuple[str, Optional[str]]] = {}
    numeric = [int(p) for p in pmids if p.isdigit()]
    with engine.connect() as conn:
        for i in range(0, len(numeric), TITLE_CHUNK):
            for pmid, title, journal in conn.execute(stmt, {"pmids": numeric[i:i + TITLE_CHUNK]}):
                out[str(pmid)] = (title or "", journal)
    return out


def classify(row: dict[str, str], title: str, journal: Optional[str]) -> dict[str, Any]:
    """One model call. Never raises: a failure comes back with stage None."""
    out: dict[str, Any] = {"pmid": row["pmid"], "stage": None, "confidence": None,
                           "usd": 0.0, "model": "", "error": ""}
    try:
        res = call_with_fallback(
            system_prompt=STAGE_FROM_SYNOPSIS_SYSTEM,
            user_prompt=build_stage_user_content(title=title, journal=journal, synopsis=row["synopsis"]),
            max_tokens=200,
        )
        out["model"] = res.model
        out["usd"] = float(cost_for(res.model, res.input_tokens, res.output_tokens))
        out["stage"], out["confidence"] = _parse_stage(parse_json_lenient(res.text))
        if out["stage"] is None:
            out["error"] = "no valid stage in response"
    except Exception as e:  # noqa: BLE001 - one bad paper must not stop the backfill
        out["error"] = str(e)
    return out


def build_update(pmid: str, stage: str, confidence: Optional[str]) -> dict[str, Any]:
    """UpdateItem kwargs for one row. Pure; conditional so it cannot overwrite a label."""
    expr = "SET translational_stage = :s, translational_stage_source = :src"
    values = {":s": {"S": stage}, ":src": {"S": STAGE_SOURCE}}
    if confidence:
        expr += ", translational_stage_confidence = :c"
        values[":c"] = {"S": confidence}
    return {
        "TableName": TABLE_NAME,
        "Key": {"PK": {"S": f"{IMPACT_PK_PREFIX}{pmid}"}, "SK": {"S": IMPACT_SK}},
        "UpdateExpression": expr,
        "ConditionExpression": "attribute_exists(PK) AND attribute_not_exists(translational_stage)",
        "ExpressionAttributeValues": values,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true",
                        help="Write the labels. Without this flag nothing is written.")
    parser.add_argument("--limit", type=int, default=None, help="Only the first N unlabelled rows (by pmid).")
    parser.add_argument("--out", default=None, help="Write every label to this CSV.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    client = get_dynamo_client()
    rows = scan_unlabelled(client)
    total = len(rows)
    if args.limit is not None:
        rows = rows[:args.limit]
    titles = fetch_titles(get_engine(), [r["pmid"] for r in rows])
    print(f"unlabelled rows with a synopsis: {total}; this run: {len(rows)}; "
          f"titles found: {sum(r['pmid'] in titles for r in rows)}; mode: {'APPLY' if args.apply else 'dry run'}")

    def work(row):
        title, journal = titles.get(row["pmid"], ("", None))
        return classify(row, title, journal)

    with ThreadPoolExecutor(WORKERS) as ex:
        results = list(ex.map(work, rows))

    written = already = write_errors = 0
    if args.apply:
        for r in results:
            if r["stage"] is None:
                continue
            try:
                client.update_item(**build_update(r["pmid"], r["stage"], r["confidence"]))
                written += 1
            except ClientError as e:
                if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                    already += 1  # labelled (or deleted) since the scan
                else:
                    write_errors += 1
                    logger.error("update failed for pmid=%s: %s", r["pmid"], e)

    if args.out:
        syn = {r["pmid"]: r["synopsis"] for r in rows}
        with open(args.out, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["pmid", "title", "synopsis", "stage", "confidence", "model", "error"])
            for r in results:
                w.writerow([r["pmid"], titles.get(r["pmid"], ("", None))[0], syn[r["pmid"]],
                            r["stage"] or "", r["confidence"] or "", r["model"], r["error"]])

    labelled = [r for r in results if r["stage"]]
    usd = sum(r["usd"] for r in results)
    print(f"labelled: {len(labelled)}/{len(results)}; no label: {len(results) - len(labelled)}")
    print("stages: " + ", ".join(f"{k}={v}" for k, v in sorted(collections.Counter(r['stage'] for r in labelled).items())))
    print("confidence: " + ", ".join(f"{k}={v}" for k, v in collections.Counter(r['confidence'] for r in labelled).most_common()))
    print("models: " + ", ".join(f"{k or 'none'}={v}" for k, v in collections.Counter(r['model'] for r in results).most_common()))
    print(f"cost: ${usd:.2f} (${usd / max(len(results), 1):.5f} per paper)")
    if args.apply:
        print(f"written: {written}; already labelled: {already}; write errors: {write_errors}")
    else:
        print("dry run: nothing written")
    return 1 if write_errors else 0


if __name__ == "__main__":
    sys.exit(main())

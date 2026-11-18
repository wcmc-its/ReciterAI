#!/usr/bin/env python3
"""Wide backfill: a synopsis and a translational stage for research articles
the daily enrichment job never covers.

The daily job only enriches papers with a full-time-faculty author. This
script runs the same synopsis call (``generate_synopsis``: one Sonnet call
returning synopsis + stage + confidence) over every other research article
from 2020 onward and writes a stage-only ``IMPACT#`` row for each.

Work set, from reciterdb: Academic Articles, ``articleYear >= 2020``, that
NIH iCite marks as a research article and that have a non-empty abstract;
minus any pmid that already has an ``IMPACT#`` row, minus any ``INVALID#``
pmid.

The row deliberately has NO ``synopsis`` and NO ``impact_score`` attribute.
Several readers treat "the IMPACT# row has a ``synopsis``" as "this paper is
enriched" (the hot-path eligibility sweep would topic-score it; the faculty
gap scan would stop the backstop from enriching it). The text is stored as
``stage_synopsis`` instead, with ``enrichment_scope='stage_only'``.

Idempotency: each write is a PutItem conditional on the row not existing, so
it can never overwrite a row the daily job wrote, and a re-run only spends on
papers that still have no row. A paper whose call failed or returned no valid
stage gets no row and is retried by a later run.

Usage:
    python3 scripts/backfill_synopsis_stage_wide.py --limit 20            # dry run: call the model, write nothing
    python3 scripts/backfill_synopsis_stage_wide.py --limit 20 --out x.csv
    python3 scripts/backfill_synopsis_stage_wide.py --apply --max-usd 50  # write, stop at $50 measured spend

A dry run still calls the model (that is the point: it shows the output and
the cost). Use --limit to keep it small. --max-usd stops submitting new
papers once measured spend reaches it; calls already in flight finish.
"""

from __future__ import annotations

import argparse
import collections
import csv
import logging
import statistics
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from botocore.exceptions import ClientError
from sqlalchemy import text

from pipeline_enrichment.synopsis import SYNOPSIS_MAX_CHARS, generate_synopsis
from utils.db import get_engine
from utils.dynamodb_helpers import get_dynamo_client, scan_invalid_pmids
from utils.iso_clock import now_iso
from utils.llm_cost import cost_for
from utils.sql_queries import fetch_publications_for_enrichment

logger = logging.getLogger(__name__)

TABLE_NAME = "reciterai"
IMPACT_PK_PREFIX = "IMPACT#pmid_"
IMPACT_SK = "SCORE"
STAGE_SOURCE = "abstract"
ENRICHMENT_SCOPE = "stage_only"
FETCH_CHUNK = 500
WORKERS = 8

# Same Academic Article / 2020 invariant as the daily job, widened from
# "has a full-time-faculty author" to "iCite research article with an abstract".
WORK_SET_SQL = """
SELECT a.pmid
FROM analysis_summary_article a
JOIN analysis_nih n ON n.pmid = a.pmid
JOIN reporting_abstracts r ON r.pmid = a.pmid
WHERE a.publicationTypeCanonical = 'Academic Article'
  AND a.articleYear >= 2020
  AND n.is_research_article = 'True'
  AND TRIM(COALESCE(r.abstractVarchar, '')) <> ''
ORDER BY a.pmid DESC
"""


def fetch_in_scope_pmids(engine) -> list[str]:
    """Every in-scope pmid from reciterdb, newest (highest pmid) first. Read only."""
    with engine.connect() as conn:
        return [str(r[0]) for r in conn.execute(text(WORK_SET_SQL))]


def scan_impact_pmids(client) -> set[str]:
    """The pmid of every IMPACT# row, whatever attributes it carries."""
    kwargs: dict[str, Any] = {
        "TableName": TABLE_NAME,
        "FilterExpression": "begins_with(PK, :p)",
        "ExpressionAttributeValues": {":p": {"S": IMPACT_PK_PREFIX}},
        "ProjectionExpression": "PK",
    }
    pmids: set[str] = set()
    while True:
        resp = client.scan(**kwargs)
        for it in resp["Items"]:
            pmids.add(it["PK"]["S"][len(IMPACT_PK_PREFIX):])
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return pmids


def select_work_set(in_scope: Iterable[str], have_row: set[str], invalid: set[str],
                    limit: Optional[int] = None) -> list[str]:
    """In-scope pmids with no IMPACT# row and no INVALID# row, pmid descending. Pure."""
    keep = sorted({str(p) for p in in_scope} - set(have_row) - set(invalid), key=int, reverse=True)
    return keep if limit is None else keep[:limit]


def summarize(row: dict[str, Any]) -> dict[str, Any]:
    """One paper through the production synopsis call, exactly as the daily job
    calls it. Never raises: a failure comes back with synopsis None."""
    pmid = str(row["pmid"])
    out: dict[str, Any] = {"pmid": pmid, "title": row.get("articleTitle") or "", "synopsis": None,
                           "stage": None, "confidence": None, "model": "", "usd": 0.0, "error": ""}
    try:
        syn = generate_synopsis(
            pmid=pmid,
            title=out["title"],
            journal=row.get("journalTitleVerbose"),
            year=row.get("articleYear"),
            abstract=row.get("abstractVarchar"),
        )
        out.update(synopsis=syn.synopsis, stage=syn.stage, confidence=syn.stage_confidence,
                   model=syn.model, error=syn.error or "")
        if syn.input_tokens or syn.output_tokens:
            out["usd"] = float(cost_for(syn.model, syn.input_tokens, syn.output_tokens))
        if syn.synopsis and not syn.stage and not out["error"]:
            out["error"] = "no valid stage in response"
    except Exception as e:  # noqa: BLE001 - one bad paper must not stop the backfill
        out["error"] = str(e)
    return out


def build_put(*, pmid: str, synopsis: str, model: str, stage: Optional[str],
              confidence: Optional[str], enriched_at: str) -> dict[str, Any]:
    """PutItem kwargs for one stage-only row. Pure.

    Never emits 'synopsis' or 'impact_score' (see the module docstring), and is
    conditional on the row not existing so it cannot overwrite the daily job.
    Stage and confidence follow ddb_writer.build_impact_item's omit rule.
    """
    item = {
        "PK": {"S": f"{IMPACT_PK_PREFIX}{pmid}"},
        "SK": {"S": IMPACT_SK},
        "pmid": {"S": str(pmid)},
        "stage_synopsis": {"S": synopsis},
        "stage_synopsis_model": {"S": model},
        "translational_stage_source": {"S": STAGE_SOURCE},
        "enrichment_scope": {"S": ENRICHMENT_SCOPE},
        "enriched_at": {"S": enriched_at},
    }
    if stage:
        item["translational_stage"] = {"S": stage}
        if confidence:
            item["translational_stage_confidence"] = {"S": confidence}
    return {"TableName": TABLE_NAME, "Item": item, "ConditionExpression": "attribute_not_exists(PK)"}


def run(pmids: list[str], fetch_rows: Callable[[list[str]], list[dict]], *, max_usd: float,
        client=None, apply: bool = False, workers: int = WORKERS,
        summarize_fn: Callable[[dict], dict] = summarize,
        on_result: Optional[Callable[[dict], None]] = None) -> dict[str, Any]:
    """Process the work set newest first; write each row as its result arrives.

    Stops submitting once measured spend reaches ``max_usd`` (calls already in
    flight finish, so spend can pass the cap by up to ``workers - 1`` papers).
    Without ``apply`` nothing is written.
    """
    s: dict[str, Any] = {"results": [], "usd": 0.0, "written": 0, "already": 0, "write_errors": 0,
                         "not_found": 0, "not_submitted": 0, "budget_hit": False}
    lock = threading.Lock()

    def work(row):
        with lock:
            if s["usd"] >= max_usd:
                s["budget_hit"] = True
                return None
        r = summarize_fn(row)
        with lock:
            s["usd"] += r["usd"]
        return r

    with ThreadPoolExecutor(workers) as ex:
        for i in range(0, len(pmids), FETCH_CHUNK):
            chunk = pmids[i:i + FETCH_CHUNK]
            if s["usd"] >= max_usd:
                s["budget_hit"] = True
                s["not_submitted"] += len(pmids) - i
                break
            rows = sorted(fetch_rows(chunk), key=lambda r: int(r["pmid"]), reverse=True)
            s["not_found"] += len(chunk) - len(rows)
            for r in ex.map(work, rows):
                if r is None:
                    s["not_submitted"] += 1
                    continue
                s["results"].append(r)
                if on_result:
                    on_result(r)
                if not (apply and r["synopsis"] and r["stage"]):
                    continue
                try:
                    client.put_item(**build_put(
                        pmid=r["pmid"], synopsis=r["synopsis"], model=r["model"], stage=r["stage"],
                        confidence=r["confidence"], enriched_at=now_iso()))
                    s["written"] += 1
                except ClientError as e:
                    if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                        s["already"] += 1  # the daily job (or another run) wrote it since the scan
                    else:
                        s["write_errors"] += 1
                        logger.error("put failed for pmid=%s: %s", r["pmid"], e)
    return s


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true",
                        help="Write the rows. Without this flag nothing is written.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only the first N papers of the work set (by pmid descending, newest first).")
    parser.add_argument("--out", default=None, help="Write every result to this CSV.")
    parser.add_argument("--max-usd", type=float, default=25.0,
                        help="Stop submitting new papers once measured spend reaches this (default 25).")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    engine = get_engine()
    client = get_dynamo_client()
    in_scope = fetch_in_scope_pmids(engine)
    have_row = scan_impact_pmids(client)
    invalid = set(scan_invalid_pmids(client, TABLE_NAME))
    remaining = select_work_set(in_scope, have_row, invalid)
    pmids = remaining if args.limit is None else remaining[:args.limit]
    scope = set(in_scope)
    print(f"in scope: {len(scope)}; already have an IMPACT# row: {len(scope & have_row)}; "
          f"invalid: {len((scope - have_row) & invalid)}; remaining: {len(remaining)}; "
          f"this run: {len(pmids)}; max spend: ${args.max_usd:.2f}; mode: {'APPLY' if args.apply else 'dry run'}")

    out_file = open(args.out, "w", newline="") if args.out else None
    writer = csv.writer(out_file) if out_file else None
    if writer:
        writer.writerow(["pmid", "title", "synopsis", "stage", "confidence", "model", "usd", "error"])

    def on_result(r):
        if writer:
            writer.writerow([r["pmid"], r["title"], r["synopsis"] or "", r["stage"] or "",
                             r["confidence"] or "", r["model"], f"{r['usd']:.6f}", r["error"]])
            out_file.flush()

    try:
        s = run(pmids, lambda chunk: fetch_publications_for_enrichment(engine, chunk),
                max_usd=args.max_usd, client=client, apply=args.apply, on_result=on_result)
    finally:
        if out_file:
            out_file.close()

    results = s["results"]
    usable = [r for r in results if r["synopsis"] and r["stage"]]
    lengths = [len(r["synopsis"]) for r in results if r["synopsis"]]
    print(f"processed: {len(results)}/{len(pmids)}; usable (synopsis and stage): {len(usable)}; "
          f"no synopsis: {sum(not r['synopsis'] for r in results)}; "
          f"synopsis but no stage: {sum(bool(r['synopsis']) and not r['stage'] for r in results)}; "
          f"not in reciterdb fetch: {s['not_found']}")
    if s["budget_hit"]:
        print(f"STOPPED at --max-usd ${args.max_usd:.2f}: {s['not_submitted']} papers of this run not submitted")
    print("stages: " + ", ".join(f"{k}={v}" for k, v in sorted(collections.Counter(r['stage'] for r in usable).items())))
    print("confidence: " + ", ".join(f"{k}={v}" for k, v in collections.Counter(r['confidence'] for r in usable).most_common()))
    print("models: " + ", ".join(f"{k or 'none'}={v}" for k, v in collections.Counter(r['model'] for r in results).most_common()))
    if lengths:
        print(f"synopsis length: min={min(lengths)}, median={statistics.median(lengths):g}, max={max(lengths)}; "
              f"over {SYNOPSIS_MAX_CHARS}: {sum(n > SYNOPSIS_MAX_CHARS for n in lengths)}")
    print(f"cost: ${s['usd']:.2f} (${s['usd'] / max(len(results), 1):.5f} per paper)")
    if args.apply:
        print(f"written: {s['written']}; already present: {s['already']}; write errors: {s['write_errors']}")
    else:
        print("dry run: nothing written")
    return 1 if s["write_errors"] else 0


if __name__ == "__main__":
    sys.exit(main())

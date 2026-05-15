"""Compute per-paper `top_topic_id` (#68).

For each PMID, gathers all `TOPIC#` activity rows from DynamoDB (via the
`PmidIndex` GSI), computes the argmax of `score` across topics that cleared
`score_floor`, and writes `top_topic_id` back to every activity row for
that PMID.

Tiebreak (when the top-2 topic scores fall within `tie_epsilon`):

  1. Higher `sum(subtopic_confidences[topic])` wins (signal density at
     the topic level — mirrors the per-subtopic pattern at
     `assign_subtopics.py:_resolve_primary_on_tie`).
  2. Alphabetically lowest `topic_id` wins.

`top_topic_id` is observational, not a designation. It does not change
qualification, `subtopic_ids[]`, or any rollup arithmetic. See
`docs/topic-cross-listing-display.md` and `docs/topic-subtopic-assignment.md`.

Usage:
    # Targeted (hot-path / ad-hoc):
    python compute_top_topic.py --pmid 12345 --pmid 67890

    # Backfill across the entire corpus (cold-path / one-shot operator):
    python compute_top_topic.py --all

    # Hot-path Lambda variant — emits STAGE# envelope to stdout instead of
    # writing the STAGE# row directly:
    python compute_top_topic.py --pmid 12345 --emit-envelope

Exit codes:
    0  success
    1  unexpected error
    2  invalid arguments
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config

sys.path.insert(0, str(Path(__file__).parent))

from utils.dynamodb_helpers import get_table, to_decimal, TABLE_NAME
from utils.env_check import load_thresholds
from utils.iso_clock import now_iso
from utils.stage_records import (
    build_complete_record,
    compute_input_hash,
    write_complete,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)
logging.getLogger("botocore").setLevel(logging.WARNING)
logging.getLogger("boto3").setLevel(logging.WARNING)


STAGE_NAME = "compute_top_topic"
STAGE_SCOPE_GLOBAL = "GLOBAL"


# ---------------------------------------------------------------------------
# Pure tiebreak (unit-testable; no DDB)
# ---------------------------------------------------------------------------


def resolve_top_topic(
    topic_scores: Mapping[str, float],
    *,
    subtopic_confidence_sums: Mapping[str, float] | None = None,
    score_floor: float,
    tie_epsilon: float,
) -> str | None:
    """Return the argmax `topic_id` across `topic_scores`, or None.

    Tiebreak when the top-2 scores fall within `tie_epsilon`:
      1. Higher `subtopic_confidence_sums[topic]` wins.
      2. Alphabetically lowest `topic_id` wins.

    Topics with `score < score_floor` are excluded. Returns None if no
    topic clears the floor (the paper carries no `top_topic_id` in
    that case).

    Pure function: no I/O. Mirrors the pattern at
    `assign_subtopics.py:_resolve_primary_on_tie`.
    """
    if subtopic_confidence_sums is None:
        subtopic_confidence_sums = {}

    eligible = [
        (tid, float(s))
        for tid, s in topic_scores.items()
        if float(s) >= score_floor
    ]
    if not eligible:
        return None

    eligible.sort(key=lambda kv: (-kv[1], kv[0]))
    top_id, top_score = eligible[0]
    if len(eligible) == 1:
        return top_id

    second_score = eligible[1][1]
    if (top_score - second_score) > tie_epsilon:
        return top_id

    contenders = [tid for tid, s in eligible if (top_score - s) <= tie_epsilon]
    # Tiebreak: higher sum(subtopic_confidences) wins; ties broken alphabetically.
    contenders.sort(
        key=lambda tid: (-float(subtopic_confidence_sums.get(tid, 0.0)), tid)
    )
    return contenders[0]


# ---------------------------------------------------------------------------
# DynamoDB I/O
# ---------------------------------------------------------------------------


_TOPIC_PK_PREFIX = "TOPIC#"
_ACTIVITY_SK_PREFIX = "SCORE#"


def _topic_id_from_pk(pk: str) -> str | None:
    if not pk.startswith(_TOPIC_PK_PREFIX):
        return None
    return pk[len(_TOPIC_PK_PREFIX):]


def _sum_subtopic_confidences(row: Mapping[str, Any]) -> float:
    confs = row.get("subtopic_confidences") or {}
    if not isinstance(confs, Mapping):
        return 0.0
    total = 0.0
    for v in confs.values():
        if isinstance(v, (int, float, Decimal)):
            total += float(v)
    return total


def fetch_activity_rows_for_pmid(table: Any, pmid: str) -> list[dict]:
    """Return every `TOPIC#` activity row for the given PMID via PmidIndex.

    Filters to PKs that begin with `TOPIC#` and SKs that begin with
    `SCORE#` — drops faculty / stage / processing rows the GSI also
    surfaces.
    """
    rows: list[dict] = []
    last_key = None
    while True:
        kwargs = {
            "IndexName": "PmidIndex",
            "KeyConditionExpression": (
                Key("pmid").eq(str(pmid))
                & Key("PK").begins_with(_TOPIC_PK_PREFIX)
            ),
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        resp = table.query(**kwargs)
        for item in resp.get("Items", []):
            sk = item.get("SK", "")
            if isinstance(sk, str) and sk.startswith(_ACTIVITY_SK_PREFIX):
                rows.append(item)
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
    return rows


def derive_top_topic_inputs(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, float], dict[str, float]]:
    """Collapse activity rows into per-topic `score` and confidence-sum maps.

    Multiple rows can share a (PMID, topic) pair — one per author. They
    share the same `score` and `subtopic_confidences`, so we take the
    first observed value per topic.
    """
    topic_scores: dict[str, float] = {}
    confidence_sums: dict[str, float] = {}
    for row in rows:
        tid = _topic_id_from_pk(row.get("PK", ""))
        if tid is None or tid in topic_scores:
            continue
        raw_score = row.get("score")
        if raw_score is None:
            continue
        topic_scores[tid] = float(raw_score)
        confidence_sums[tid] = _sum_subtopic_confidences(row)
    return topic_scores, confidence_sums


def write_top_topic_for_rows(
    table: Any,
    rows: Iterable[Mapping[str, Any]],
    top_topic_id: str | None,
) -> int:
    """SET (or REMOVE) `top_topic_id` on every row in `rows`. Returns count."""
    written = 0
    for row in rows:
        pk, sk = row.get("PK"), row.get("SK")
        if not pk or not sk:
            continue
        if top_topic_id is None:
            table.update_item(
                Key={"PK": pk, "SK": sk},
                UpdateExpression="REMOVE top_topic_id",
            )
        else:
            table.update_item(
                Key={"PK": pk, "SK": sk},
                UpdateExpression="SET top_topic_id = :tid",
                ExpressionAttributeValues={":tid": top_topic_id},
            )
        written += 1
    return written


def process_pmid(
    table: Any,
    pmid: str,
    *,
    score_floor: float,
    tie_epsilon: float,
) -> tuple[str | None, int]:
    """Compute `top_topic_id` for one PMID and write it to every activity row.

    Returns `(top_topic_id_or_None, rows_written)`.
    """
    rows = fetch_activity_rows_for_pmid(table, pmid)
    if not rows:
        return None, 0
    topic_scores, confidence_sums = derive_top_topic_inputs(rows)
    top_topic_id = resolve_top_topic(
        topic_scores,
        subtopic_confidence_sums=confidence_sums,
        score_floor=score_floor,
        tie_epsilon=tie_epsilon,
    )
    written = write_top_topic_for_rows(table, rows, top_topic_id)
    return top_topic_id, written


# ---------------------------------------------------------------------------
# Backfill mode
# ---------------------------------------------------------------------------


def _iter_all_pmids_from_topic_partitions(table: Any) -> Iterable[str]:
    """Yield every distinct PMID present in any `TOPIC#<...>` partition.

    Scan is bounded to activity rows by projecting `pmid` only. A single
    scan is acceptable here: backfill is a one-shot operator action,
    not a periodic job.
    """
    seen: set[str] = set()
    last_key = None
    while True:
        kwargs = {
            "FilterExpression": (
                Key("PK").begins_with(_TOPIC_PK_PREFIX)
                & Key("SK").begins_with(_ACTIVITY_SK_PREFIX)
            ),
            "ProjectionExpression": "pmid",
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        resp = table.scan(**kwargs)
        for item in resp.get("Items", []):
            pmid = item.get("pmid")
            if pmid and pmid not in seen:
                seen.add(pmid)
                yield pmid
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            return


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _resolve_pmids(args: argparse.Namespace, table: Any) -> Iterable[str]:
    if args.all:
        return _iter_all_pmids_from_topic_partitions(table)
    pmids: list[str] = list(args.pmid or [])
    if args.pmids_file:
        for line in Path(args.pmids_file).read_text().splitlines():
            line = line.strip()
            if line:
                pmids.append(line)
    return pmids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pmid", action="append", default=[],
        help="PMID to process. May be repeated.",
    )
    parser.add_argument(
        "--pmids-file",
        help="Path to a newline-delimited file of PMIDs to process.",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Backfill mode: scan every TOPIC# activity row and process "
             "every distinct PMID. One-shot operator command.",
    )
    parser.add_argument(
        "--emit-envelope", action="store_true",
        help="Hot-path mode: emit the STAGE# envelope to stdout instead "
             "of writing the STAGE# row directly to DynamoDB.",
    )
    parser.add_argument(
        "--workers", type=int, default=1,
        help="Number of parallel worker threads for per-PMID processing. "
             "Default 1 (serial). Higher values speed up --all backfills by "
             "parallelizing DynamoDB I/O round-trips. Suggested 10-20 for "
             "laptop->us-east-1 runs; keep at 1 for hot-path Lambda use.",
    )
    args = parser.parse_args(argv)

    if not (args.pmid or args.pmids_file or args.all):
        parser.error("must pass at least one of --pmid / --pmids-file / --all")

    cfg = load_thresholds()
    score_floor = float(cfg["score_floor"])
    tie_epsilon = float(cfg["tie_epsilon"])

    if args.workers > 1:
        # Bump boto3 HTTP pool to match worker count; default 10 connections
        # would otherwise queue threads on TLS connection reuse.
        region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        boto_cfg = Config(max_pool_connections=args.workers + 5)
        table = boto3.resource(
            "dynamodb", region_name=region, config=boto_cfg
        ).Table(TABLE_NAME)
    else:
        table = get_table(TABLE_NAME)
    stage_started_at = now_iso()
    t_start = time.monotonic()

    pmids = list(_resolve_pmids(args, table))
    if not pmids:
        logger.info("No PMIDs to process.")
    else:
        logger.info(f"Processing {len(pmids)} PMID(s) (score_floor={score_floor}, tie_epsilon={tie_epsilon})")

    rows_written = 0
    pmids_assigned = 0
    pmids_no_above_floor = 0

    def _process(pmid: str) -> tuple[str | None, int]:
        return process_pmid(
            table, str(pmid),
            score_floor=score_floor,
            tie_epsilon=tie_epsilon,
        )

    if args.workers > 1 and pmids:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(_process, p): p for p in pmids}
            for i, fut in enumerate(as_completed(futures), 1):
                try:
                    top_topic_id, n = fut.result()
                except Exception as e:
                    logger.error(f"failed pmid {futures[fut]}: {e}")
                    continue
                rows_written += n
                if top_topic_id is not None:
                    pmids_assigned += 1
                elif n > 0:
                    pmids_no_above_floor += 1
                if i % 1000 == 0:
                    logger.info(f"  processed {i}/{len(pmids)} pmids")
    else:
        for i, pmid in enumerate(pmids, 1):
            top_topic_id, n = _process(pmid)
            rows_written += n
            if top_topic_id is not None:
                pmids_assigned += 1
            elif n > 0:
                # Rows exist but none cleared the floor → top_topic_id removed.
                pmids_no_above_floor += 1
            if i % 1000 == 0:
                logger.info(f"  processed {i}/{len(pmids)} pmids")

    logger.info(
        f"Done. pmids_total={len(pmids)} pmids_assigned={pmids_assigned} "
        f"pmids_no_above_floor={pmids_no_above_floor} rows_written={rows_written}"
    )

    completed_at = now_iso()
    duration_ms = int((time.monotonic() - t_start) * 1000)
    input_hash = compute_input_hash(
        STAGE_NAME,
        {
            "mode": "all" if args.all else "targeted",
            "pmid_count": len(pmids),
            "score_floor": score_floor,
            "tie_epsilon": tie_epsilon,
        },
    )
    complete_kwargs = dict(
        stage=STAGE_NAME,
        scope=STAGE_SCOPE_GLOBAL,
        input_hash=input_hash,
        started_at=stage_started_at,
        completed_at=completed_at,
        duration_ms=duration_ms,
        cost_observed_usd=Decimal("0.0"),
        records_written=rows_written,
    )
    if args.emit_envelope:
        print(json.dumps(build_complete_record(**complete_kwargs), default=str))
    else:
        write_complete(table, **complete_kwargs)

    return 0


if __name__ == "__main__":
    sys.exit(main())

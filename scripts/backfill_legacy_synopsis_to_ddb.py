#!/usr/bin/env python3
"""One-off backfill: lift MariaDB ``reciterai_synopsis`` text onto DDB ``IMPACT#`` rows.

Precondition for #38 (consumer-side read switch from MariaDB to DDB). Without
this, switching ``PUBLICATION_EXTRACTION_SQL`` to a DDB query silently drops
~99% of papers from cold-run scoring — their IMPACT# rows have ``impact_score``
but no ``synopsis`` attribute, because they predate the #37 dual-write
contract.

Per-PMID behavior, classified by an upfront DDB ``BatchGetItem``:

- IMPACT# row exists with a non-empty ``synopsis`` attribute → **skip.**
  Never overwrites a Sonnet-generated dual-write with the older gpt-5.1 text.
- IMPACT# row exists without ``synopsis`` → ``UpdateItem`` set ``synopsis`` +
  ``synopsis_model='gpt-5.1'`` + ``enriched_at``. Other attributes
  (``impact_score`` etc.) are left untouched.
- IMPACT# row does **not** exist → ``PutItem`` with ``synopsis`` attributes
  only (no ``impact_score``). The missing impact scores for these PMIDs are
  tracked separately on #138 as out-of-scope here.

Idempotency: every write uses a conditional expression so a re-run is a
no-op — the update path requires ``attribute_not_exists(synopsis)``, the put
path requires ``attribute_not_exists(PK)``. ConditionalCheckFailedException
is caught and counted as "already covered" rather than re-classifying.

Usage:
    python3 scripts/backfill_legacy_synopsis_to_ddb.py            # dry-run default
    python3 scripts/backfill_legacy_synopsis_to_ddb.py --apply    # real run
    python3 scripts/backfill_legacy_synopsis_to_ddb.py --apply --limit 50
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from botocore.exceptions import ClientError
from sqlalchemy import text

from utils.dynamodb_helpers import get_dynamo_client, get_table
from utils.iso_clock import now_iso
from utils.sql_queries import get_db_connection

logger = logging.getLogger(__name__)

TABLE_NAME = "reciterai"
IMPACT_PK_PREFIX = "IMPACT#pmid_"
IMPACT_SK = "SCORE"
LEGACY_SYNOPSIS_MODEL = "gpt-5.1"

SYNOPSIS_QUERY = (
    "SELECT external_id AS pmid, synopsis "
    "FROM reciterai_synopsis "
    "WHERE entity_type = 'publication' "
    "  AND synopsis IS NOT NULL "
    "  AND synopsis != ''"
)


# ---------------------------------------------------------------------------
# Step 1 — read MariaDB
# ---------------------------------------------------------------------------

def load_mariadb_synopses(limit: int | None = None) -> dict[str, str]:
    """Return ``{pmid: synopsis}`` for every non-empty publication synopsis.

    PMIDs are stringified to match the IMPACT# PK format.
    """
    conn = get_db_connection()
    try:
        rows = conn.execute(text(SYNOPSIS_QUERY)).fetchall()
    finally:
        conn.close()
    result: dict[str, str] = {}
    for row in rows:
        pmid = str(row[0])
        if pmid:
            result[pmid] = str(row[1])
    if limit is not None:
        sliced = dict(list(result.items())[:limit])
        return sliced
    return result


# ---------------------------------------------------------------------------
# Step 2 — classify against current DDB state
# ---------------------------------------------------------------------------

def _chunked(seq: list[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def classify_pmids(
    pmids: list[str],
    *,
    dynamo_client,
) -> tuple[set[str], set[str], set[str]]:
    """Partition ``pmids`` by their current DDB state.

    Uses one ``BatchGetItem`` per 100 PMIDs (DDB API max) projecting only
    ``PK`` + ``synopsis`` — the attributes the classification needs.

    Returns ``(skip, update, create)`` sets:
      - ``skip``: row exists AND has a non-empty ``synopsis`` attribute
      - ``update``: row exists AND ``synopsis`` is missing/empty
      - ``create``: row does not exist
    """
    skip: set[str] = set()
    update: set[str] = set()
    create: set[str] = set()
    found: set[str] = set()

    for chunk in _chunked(pmids, 100):
        keys = [
            {"PK": {"S": f"{IMPACT_PK_PREFIX}{p}"}, "SK": {"S": IMPACT_SK}}
            for p in chunk
        ]
        request = {
            TABLE_NAME: {
                "Keys": keys,
                "ProjectionExpression": "PK, synopsis",
            }
        }
        while request:
            resp = dynamo_client.batch_get_item(RequestItems=request)
            for item in resp.get("Responses", {}).get(TABLE_NAME, []):
                pk = item.get("PK", {}).get("S", "")
                if not pk.startswith(IMPACT_PK_PREFIX):
                    continue
                pmid = pk[len(IMPACT_PK_PREFIX):]
                found.add(pmid)
                syn = item.get("synopsis", {}).get("S", "")
                if syn:
                    skip.add(pmid)
                else:
                    update.add(pmid)
            request = resp.get("UnprocessedKeys") or None

    for pmid in pmids:
        if pmid not in found:
            create.add(pmid)

    return skip, update, create


# ---------------------------------------------------------------------------
# Step 3 — execute (UpdateItem + PutItem, both conditional)
# ---------------------------------------------------------------------------

class ApplyResult:
    """Counters for a single ``--apply`` pass."""

    def __init__(self) -> None:
        self.updated = 0
        self.created = 0
        self.skipped_conditional = 0  # condition failed → already covered

    def total_writes(self) -> int:
        return self.updated + self.created

    def __str__(self) -> str:
        return (
            f"updated={self.updated}, created={self.created}, "
            f"skipped_conditional={self.skipped_conditional}"
        )


def apply_updates(
    pmid_to_synopsis: dict[str, str],
    *,
    enriched_at: str,
    table,
    log_every: int = 500,
) -> int:
    """``UpdateItem`` synopsis attributes onto existing IMPACT# rows.

    Conditional on ``attribute_not_exists(synopsis)`` — a row that already
    has a synopsis (e.g. a Sonnet dual-write that landed between classify
    and apply) is left alone, counted by the caller via the
    ``ConditionalCheckFailedException`` return.
    """
    written = 0
    conditional_skips = 0
    for i, (pmid, synopsis) in enumerate(pmid_to_synopsis.items(), start=1):
        try:
            table.update_item(
                Key={"PK": f"{IMPACT_PK_PREFIX}{pmid}", "SK": IMPACT_SK},
                UpdateExpression=(
                    "SET synopsis = :s, synopsis_model = :m, "
                    "enriched_at = :t"
                ),
                ConditionExpression="attribute_not_exists(synopsis)",
                ExpressionAttributeValues={
                    ":s": synopsis,
                    ":m": LEGACY_SYNOPSIS_MODEL,
                    ":t": enriched_at,
                },
            )
            written += 1
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                conditional_skips += 1
            else:
                raise
        if i % log_every == 0:
            logger.info("update: %d/%d processed", i, len(pmid_to_synopsis))
    logger.info(
        "update phase done: written=%d, conditional_skips=%d",
        written, conditional_skips,
    )
    return written, conditional_skips


def apply_puts(
    pmid_to_synopsis: dict[str, str],
    *,
    enriched_at: str,
    table,
    log_every: int = 500,
) -> int:
    """``PutItem`` synopsis-only IMPACT# rows for PMIDs without an existing row.

    Conditional on ``attribute_not_exists(PK)`` — re-runs are no-ops; a row
    that was created between classify and apply (e.g. by the daily job
    racing this script) is left alone.

    No ``impact_score`` / ``justification`` are written here. Per the #138
    scope, those PMIDs are out-of-scope; ``spotlight/pool_ranker.py:115``
    safely defaults missing ``impact_score`` to 0.
    """
    written = 0
    conditional_skips = 0
    for i, (pmid, synopsis) in enumerate(pmid_to_synopsis.items(), start=1):
        try:
            table.put_item(
                Item={
                    "PK": f"{IMPACT_PK_PREFIX}{pmid}",
                    "SK": IMPACT_SK,
                    "pmid": str(pmid),
                    "synopsis": synopsis,
                    "synopsis_model": LEGACY_SYNOPSIS_MODEL,
                    "enriched_at": enriched_at,
                },
                ConditionExpression="attribute_not_exists(PK)",
            )
            written += 1
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                conditional_skips += 1
            else:
                raise
        if i % log_every == 0:
            logger.info("put: %d/%d processed", i, len(pmid_to_synopsis))
    logger.info(
        "put phase done: written=%d, conditional_skips=%d",
        written, conditional_skips,
    )
    return written, conditional_skips


# ---------------------------------------------------------------------------
# Step 4 — verification recount
# ---------------------------------------------------------------------------

def count_impact_rows_with_synopsis(dynamo_client) -> int:
    """Scan ``IMPACT#`` rows and count those with a non-empty ``synopsis``."""
    count = 0
    kwargs = {
        "TableName": TABLE_NAME,
        "FilterExpression": (
            "begins_with(PK, :p) AND attribute_exists(synopsis)"
        ),
        "ExpressionAttributeValues": {":p": {"S": "IMPACT#"}},
        "Select": "COUNT",
    }
    while True:
        resp = dynamo_client.scan(**kwargs)
        count += resp.get("Count", 0)
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek
    return count


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute writes. Without this flag the script runs in dry-run mode.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap MariaDB rows to the first N (testing only).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Log per-batch progress at INFO level.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    print("=== #138 — MariaDB reciterai_synopsis → DDB IMPACT# synopsis lift ===")
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"Mode: {mode}")
    if args.limit is not None:
        print(f"Limit: first {args.limit} MariaDB rows")
    print()

    print("[1/4] Reading MariaDB reciterai_synopsis...")
    pmid_to_synopsis = load_mariadb_synopses(limit=args.limit)
    print(f"      Loaded {len(pmid_to_synopsis)} non-empty synopsis rows.")
    if not pmid_to_synopsis:
        print("Nothing to do.")
        return 0

    print("\n[2/4] Classifying against DDB state (BatchGetItem)...")
    dynamo_client = get_dynamo_client()
    skip, update, create = classify_pmids(
        list(pmid_to_synopsis.keys()),
        dynamo_client=dynamo_client,
    )
    print(
        f"      Plan: skip={len(skip)} (already has synopsis), "
        f"update={len(update)} (row exists, no synopsis), "
        f"create={len(create)} (no row)"
    )

    if not args.apply:
        print("\n[3/4] Dry-run — no writes.")
        print(
            f"      Would write {len(update) + len(create)} rows "
            f"({len(update)} UpdateItem + {len(create)} PutItem)."
        )
        print("      Re-run with --apply to execute.")
        return 0

    print("\n[3/4] Applying writes...")
    enriched_at = now_iso()
    table = get_table(TABLE_NAME)

    update_payload = {p: pmid_to_synopsis[p] for p in update}
    create_payload = {p: pmid_to_synopsis[p] for p in create}

    upd_written, upd_skipped = apply_updates(
        update_payload, enriched_at=enriched_at, table=table,
    )
    put_written, put_skipped = apply_puts(
        create_payload, enriched_at=enriched_at, table=table,
    )

    print(
        f"      UpdateItem: written={upd_written}, "
        f"conditional_skips={upd_skipped}"
    )
    print(
        f"      PutItem:    written={put_written}, "
        f"conditional_skips={put_skipped}"
    )

    print("\n[4/4] Verifying — counting IMPACT# rows with synopsis...")
    total_with_synopsis = count_impact_rows_with_synopsis(dynamo_client)
    print(f"      IMPACT# rows with synopsis attribute now: {total_with_synopsis}")
    expected_minimum = len(skip) + upd_written + put_written
    print(
        f"      Expected ≥ {expected_minimum} "
        f"(prior_with_synopsis + writes_this_run)"
    )

    if total_with_synopsis < expected_minimum:
        print(
            "\nWARNING: verification count below expected. "
            "Investigate before treating #138 as complete."
        )
        return 1

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

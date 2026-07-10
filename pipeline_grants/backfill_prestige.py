"""Backfill prestige + is_honorific (+ recovered mechanism) onto existing GRANT# items — no re-score.

The prestige producer (pipeline_grants.prestige, wired into build_grant_item)
only stamps prestige on items written by a NEW ingest. Existing GRANT# items
predate it. Re-running the full ingest to populate them would re-score every opp
through Bedrock (slow + $$). This backfill instead recomputes prestige from
fields ALREADY on each item (mechanism, award_ceiling/estimated_funding, sponsor,
title) and updates just those attributes — no model calls.

Mechanism (#288): items ingested before #275 never stored `mechanism` (100% null
in the staging matcher output), even though the activity code is recoverable from
the title — prestige.rationale already carries it. When the stored mechanism is
empty/absent AND the title yields a code, the update also SETs `mechanism`; a
non-empty stored mechanism is never overwritten. One --apply re-run of this
backfill doubles as the one-time migration. The SPS side reads `mechanism`
through the nightly etl:dynamodb projection, so no SPS change is needed —
just let the next nightly run pick it up.

Idempotent: recompute + overwrite (cheap). Dry-run by DEFAULT — prints the
coverage/label report without writing; pass --apply to write. Use STAGING
credentials first (writes to the shared `reciterai` table).

Run:
    python -m pipeline_grants.backfill_prestige            # dry-run report
    python -m pipeline_grants.backfill_prestige --apply    # write
"""
import argparse
import logging

from pipeline_grants.models import Opportunity
from pipeline_grants.normalize import _activity_code
from pipeline_grants.prestige import prestige_item_attrs
from utils.dynamodb_helpers import TABLE_NAME, get_dynamo_client

log = logging.getLogger("pipeline_grants.backfill_prestige")

_GRANT_PK_PREFIX = "GRANT#"


def _s(item, key, default=""):
    return item.get(key, {}).get("S", default)


def _int(item, key):
    raw = item.get(key, {}).get("N")
    return int(float(raw)) if raw is not None else None


def opp_from_item(item: dict) -> Opportunity:
    """Reconstruct the minimal Opportunity prestige needs from a GRANT# item.

    Only the fields prestige reads (title, sponsor, mechanism, ceilings) are
    load-bearing; the rest default. program_type isn't stored on the item, so
    rationale loses its "award" fallback — cosmetic only.

    Items ingested before #275 never stored `mechanism`; recover it from the
    title (same fallback the producer now uses) so prestige isn't floored to
    Standard for every legacy opp.
    """
    return Opportunity(
        opportunity_id=_s(item, "opportunity_id"),
        source=_s(item, "source"),
        source_id="",
        source_url=_s(item, "source_url"),
        sponsor=_s(item, "sponsor"),
        title=_s(item, "title"),
        synopsis=_s(item, "synopsis"),
        mechanism=_s(item, "mechanism") or _activity_code(_s(item, "title")),
        award_ceiling=_int(item, "award_ceiling"),
        estimated_funding=_int(item, "estimated_funding"),
        award_floor=_int(item, "award_floor"),
    )


def scan_grant_items(client, table_name: str = TABLE_NAME):
    """Yield every GRANT# item (one META row per opportunity). Paginated scan."""
    kwargs = {
        "TableName": table_name,
        "FilterExpression": "begins_with(PK, :p)",
        "ExpressionAttributeValues": {":p": {"S": _GRANT_PK_PREFIX}},
    }
    while True:
        resp = client.scan(**kwargs)
        for item in resp.get("Items", []):
            if item.get("PK", {}).get("S", "").startswith(_GRANT_PK_PREFIX):
                yield item
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek


def backfill(client, *, apply: bool = False, limit: int = None, table_name: str = TABLE_NAME) -> dict:
    """Recompute prestige/is_honorific (+ title-recovered mechanism) for every GRANT# item. Writes iff apply."""
    seen = honorific = written = mech_backfilled = 0
    labels = {"Flagship": 0, "Major": 0, "Standard": 0}
    for item in scan_grant_items(client, table_name):
        if limit is not None and seen >= limit:
            break
        seen += 1
        opp = opp_from_item(item)
        attrs = prestige_item_attrs(opp)
        labels[attrs["prestige"]["M"]["label"]["S"]] += 1
        if attrs["is_honorific"]["BOOL"]:
            honorific += 1
        # opp.mechanism is stored-or-recovered (opp_from_item); write it back only
        # when the item had none — never overwrite a non-empty stored mechanism.
        recovered_mech = "" if _s(item, "mechanism") else opp.mechanism
        if recovered_mech:
            mech_backfilled += 1
        if apply:
            update = "SET prestige = :p, is_honorific = :h"
            values = {":p": attrs["prestige"], ":h": attrs["is_honorific"]}
            if recovered_mech:
                update += ", mechanism = :m"
                values[":m"] = {"S": recovered_mech}
            client.update_item(
                TableName=table_name,
                Key={"PK": item["PK"], "SK": item["SK"]},
                UpdateExpression=update,
                ExpressionAttributeValues=values,
            )
            written += 1
    summary = {"scanned": seen, "honorific": honorific, "labels": labels,
               "mechanism_backfilled": mech_backfilled, "written": written, "applied": apply}
    log.info("prestige backfill %s: %s", "APPLIED" if apply else "dry-run", summary)
    return summary


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    p = argparse.ArgumentParser(description="Backfill prestige + is_honorific + recovered mechanism onto GRANT# items (no re-score)")
    p.add_argument("--apply", action="store_true", help="write to DynamoDB (default: dry-run report only)")
    p.add_argument("--limit", type=int, default=None, help="cap items processed (smoke testing)")
    args = p.parse_args(argv)
    if not args.apply:
        log.info("DRY-RUN — no writes. Re-run with --apply to persist (STAGING creds first).")
    backfill(get_dynamo_client(), apply=args.apply, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

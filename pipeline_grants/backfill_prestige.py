"""Backfill prestige + is_honorific onto existing GRANT# items — no re-score.

The prestige producer (pipeline_grants.prestige, wired into build_grant_item)
only stamps prestige on items written by a NEW ingest. Existing GRANT# items
predate it. Re-running the full ingest to populate them would re-score every opp
through Bedrock (slow + $$). This backfill instead recomputes prestige from
fields ALREADY on each item (mechanism, award_ceiling/estimated_funding, sponsor,
title) and updates just the two attributes — no model calls.

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
    """
    source = _s(item, "source")
    title = _s(item, "title")
    mechanism = _s(item, "mechanism")
    # Existing items predate title-based activity-code parsing — the grants.gov
    # FON rarely embeds the code; the title does ("…(R01 Clinical Trial
    # Optional)"). Recover it for research grants so prestige differentiates;
    # leave curated awards mechanism-less (they're prizes, scored as honorific).
    if not mechanism and source == "grants_gov":
        mechanism = _activity_code(title)
    return Opportunity(
        opportunity_id=_s(item, "opportunity_id"),
        source=source,
        source_id="",
        source_url=_s(item, "source_url"),
        sponsor=_s(item, "sponsor"),
        title=title,
        synopsis=_s(item, "synopsis"),
        mechanism=mechanism,
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
    """Recompute prestige/is_honorific for every GRANT# item. Writes iff apply."""
    seen = honorific = written = 0
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
        if apply:
            client.update_item(
                TableName=table_name,
                Key={"PK": item["PK"], "SK": item["SK"]},
                UpdateExpression="SET prestige = :p, is_honorific = :h",
                ExpressionAttributeValues={":p": attrs["prestige"], ":h": attrs["is_honorific"]},
            )
            written += 1
    summary = {"scanned": seen, "honorific": honorific, "labels": labels,
               "written": written, "applied": apply}
    log.info("prestige backfill %s: %s", "APPLIED" if apply else "dry-run", summary)
    return summary


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    p = argparse.ArgumentParser(description="Backfill prestige + is_honorific onto GRANT# items (no re-score)")
    p.add_argument("--apply", action="store_true", help="write to DynamoDB (default: dry-run report only)")
    p.add_argument("--limit", type=int, default=None, help="cap items processed (smoke testing)")
    args = p.parse_args(argv)
    if not args.apply:
        log.info("DRY-RUN — no writes. Re-run with --apply to persist (STAGING creds first).")
    backfill(get_dynamo_client(), apply=args.apply, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

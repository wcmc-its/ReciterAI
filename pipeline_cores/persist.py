"""Persist (publication, core) usage records to DynamoDB.

One item per (pub, core): PK=PUB#{pmid}, SK=CORE#{core_id} in the shared
`reciterai` table. SPS ingests these via a `publication-core-mapper`
(parallel to its existing `publication-topic-mapper`) into MySQL for display;
human claims are written separately through SPS's ADR-005 manual-override layer
and take read-time precedence (the engine never writes claims).

No new ReciterDB MySQL table is created here: input is read-only from ReciterDB,
output is DynamoDB. The claim store lives in SPS.
"""
from __future__ import annotations

from utils.dynamodb_helpers import TABLE_NAME, batch_write, get_dynamo_client, to_decimal
from utils.iso_clock import now_iso

from pipeline_cores.models import CoreUsageRecord


def _n(value) -> dict:
    return {"N": str(to_decimal(value))}


def build_core_item(rec: CoreUsageRecord) -> dict:
    """Attribute-format DynamoDB item for one (publication, core) pair."""
    s = rec.signals
    item = {
        "PK": {"S": f"PUB#{rec.pmid}"},
        "SK": {"S": f"CORE#{rec.core_id}"},
        "pmid": {"S": str(rec.pmid)},
        "core_id": {"S": rec.core_id},
        "likelihood": _n(rec.likelihood),
        "status": {"S": rec.status},
        "scored_at": {"S": rec.scored_at or now_iso()},
        "signal_coauthors": {"L": [{"S": c} for c in s.coauthor_cwids]},
        "signal_ack": {"BOOL": bool(s.ack_matched)},
    }
    if s.ack_matched:
        item["ack_alias"] = {"S": s.ack_alias}
        item["ack_snippet"] = {"S": s.ack_snippet[:500]}
    if s.llm_score is not None:
        item["llm_score"] = _n(s.llm_score)
    if s.llm_rationale:
        item["llm_rationale"] = {"S": s.llm_rationale}
    if s.author_affinity:
        item["author_affinity"] = _n(s.author_affinity)
    return item


def put_core_usage(records: list, *, client=None, table_name: str = TABLE_NAME) -> int:
    """Batch-write (publication, core) items. Returns the count written."""
    client = client or get_dynamo_client()
    items = [build_core_item(r) for r in records]
    if items:
        batch_write(client, table_name, items)
    return len(items)


# Statuses that establish a (pub, core) usage for the affinity prior. 'claimed'
# (human-confirmed in SPS) and engine 'confirmed' both count; candidates do not.
_USER_STATUSES = {"confirmed", "claimed"}


def scan_prior_core_usage(core_id: str = None, *, client=None, table_name: str = TABLE_NAME) -> list:
    """Return prior [{pmid, core_id, status}] for confirmed/claimed CORE# rows.

    Full-table paginated Scan with a server-side FilterExpression (same pattern as
    scan_invalid_pmids). Used to seed the cross-run repeat-user affinity prior.
    Returns [] on any error (e.g. table absent on first run) so the pipeline still
    runs on this run's own confirmations.
    """
    client = client or get_dynamo_client()
    statuses = list(_USER_STATUSES)
    eav = {":sk": {"S": "CORE#"}}
    filt = "begins_with(SK, :sk) AND (" + " OR ".join(f"#st = :s{i}" for i in range(len(statuses))) + ")"
    for i, s in enumerate(statuses):
        eav[f":s{i}"] = {"S": s}
    if core_id is not None:
        filt += " AND core_id = :cid"
        eav[":cid"] = {"S": str(core_id)}
    kwargs = {
        "TableName": table_name,
        "ProjectionExpression": "pmid, core_id, #st",
        "FilterExpression": filt,
        "ExpressionAttributeNames": {"#st": "status"},
        "ExpressionAttributeValues": eav,
    }
    out: list = []
    try:
        while True:
            resp = client.scan(**kwargs)
            for it in resp.get("Items", []):
                out.append({
                    "pmid": it.get("pmid", {}).get("S", ""),
                    "core_id": it.get("core_id", {}).get("S", ""),
                    "status": it.get("status", {}).get("S", ""),
                })
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                break
            kwargs["ExclusiveStartKey"] = lek
    except Exception:
        return []
    return out

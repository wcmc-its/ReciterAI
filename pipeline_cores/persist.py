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

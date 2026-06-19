"""Persist scored opportunities: GRANT# DynamoDB rows + a versioned S3 artifact."""
from utils.dynamodb_helpers import TABLE_NAME, batch_write, to_decimal


def _n(value) -> dict:
    return {"N": str(to_decimal(value))}


def build_grant_item(opp, dense_scores: dict, *, taxonomy_version: str, judge: dict) -> dict:
    """Low-level attribute-format DynamoDB item for one opportunity (PK=GRANT#{id}, SK=META)."""
    ranked = sorted(dense_scores.items(), key=lambda kv: kv[1].get("score", 0.0), reverse=True)
    topic_vector = [
        {"M": {"topic_id": {"S": tid},
               "score": _n(d.get("score", 0.0)),
               "rationale": {"S": d.get("rationale", "") or ""}}}
        for tid, d in ranked
    ]
    appeal = (judge or {}).get("appeal_by_stage", {}) or {}
    item = {
        "PK": {"S": f"GRANT#{opp.opportunity_id}"},
        "SK": {"S": "META"},
        "opportunity_id": {"S": opp.opportunity_id},
        "source": {"S": opp.source},
        "source_url": {"S": opp.source_url},
        "sponsor": {"S": opp.sponsor},
        "title": {"S": opp.title},
        "synopsis": {"S": opp.synopsis},
        "status": {"S": opp.status},
        "due_date": {"S": opp.due_date},
        "open_date": {"S": opp.open_date},
        "eligibility_raw": {"S": opp.eligibility_raw},
        "cfda_list": {"L": [{"S": c} for c in opp.cfda_list]},
        "taxonomy_version": {"S": taxonomy_version},
        "ingested_at": {"S": opp.ingested_at},
        "topic_vector": {"L": topic_vector},
        "primary_topic_id": {"S": ranked[0][0] if ranked else ""},
        "is_research": {"BOOL": bool((judge or {}).get("is_research", False))},
        "appeal_by_stage": {"M": {k: _n(v) for k, v in appeal.items()}},
    }
    for field_name, value in (("award_ceiling", opp.award_ceiling),
                              ("award_floor", opp.award_floor),
                              ("estimated_funding", opp.estimated_funding),
                              ("number_of_awards", opp.number_of_awards)):
        if value is not None:
            item[field_name] = _n(value)
    if opp.mechanism:
        item["mechanism"] = {"S": opp.mechanism}
    return item


def put_grants(client, items: list, table_name: str = TABLE_NAME) -> int:
    """Batch-write GRANT# items. Returns the count written."""
    if items:
        batch_write(client, table_name, items)
    return len(items)

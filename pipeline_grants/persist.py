"""Persist scored opportunities: GRANT# DynamoDB rows + a versioned S3 artifact."""
import hashlib
import json as _json

from pipeline_grants.prestige import prestige_item_attrs
from utils.dynamodb_helpers import TABLE_NAME, batch_write, to_decimal
from utils.iso_clock import now_iso
from utils.s3_client import ARTIFACTS_BUCKET, S3HierarchyClient


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
    item.update(prestige_item_attrs(opp))   # prestige (M) + is_honorific (BOOL)
    return item


def put_grants(client, items: list, table_name: str = TABLE_NAME) -> int:
    """Batch-write GRANT# items. Returns the count written."""
    if items:
        batch_write(client, table_name, items)
    return len(items)


_ARTIFACT_PREFIX = "grants"


def publish_opportunities_artifact(opportunities: list, *, s3=None, version: str = None) -> dict:
    """Publish opportunities.json + manifest (version-pinned + latest pointer). Returns the manifest."""
    s3 = s3 or S3HierarchyClient(bucket=ARTIFACTS_BUCKET)
    generated_at = now_iso()
    version = version or f"v{generated_at[:10]}"
    body = _json.dumps(opportunities, sort_keys=True, separators=(",", ":")).encode("utf-8")
    manifest = {
        "schema_version": "1.0.0",
        "artifact": "opportunities",
        "version": version,
        "generated_at": generated_at,
        "sha256": hashlib.sha256(body).hexdigest(),
        "artifact_bytes": len(body),
        "count": len(opportunities),
    }
    manifest_body = _json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    s3.put_object(f"{_ARTIFACT_PREFIX}/{version}/opportunities.json", body)
    s3.put_object(f"{_ARTIFACT_PREFIX}/{version}/manifest.json", manifest_body)
    s3.put_object(f"{_ARTIFACT_PREFIX}/latest/manifest.json", manifest_body,
                  cache_control="max-age=60, must-revalidate")
    return manifest

"""Persist scored opportunities: GRANT# DynamoDB rows + a versioned S3 artifact."""
import hashlib
import json as _json

from pipeline_grants.match_compile import match_attrs
from pipeline_grants.prestige import prestige_item_attrs
from utils.dynamodb_helpers import TABLE_NAME, batch_write, to_decimal
from utils.iso_clock import now_iso
from utils.s3_client import ARTIFACTS_BUCKET, S3HierarchyClient


def _n(value) -> dict:
    return {"N": str(to_decimal(value))}


def _eligibility_attr(elig: dict) -> dict:
    """Native DynamoDB map for the structured eligibility block (#290): list fields -> L of S,
    booleans -> BOOL, everything else (citizenship + provenance) -> S. The SPS mapper's
    parseJsonAttr reads the native map directly, so it is NOT stored as a compact-JSON string."""
    out = {}
    for key, value in elig.items():
        if isinstance(value, bool):
            out[key] = {"BOOL": value}
        elif isinstance(value, list):
            out[key] = {"L": [{"S": str(v)} for v in value]}
        else:
            out[key] = {"S": str(value)}
    return out


def build_grant_item(opp, dense_scores: dict, *, taxonomy_version: str, judge: dict,
                     match_dsl=None, match_query=None) -> dict:
    """Low-level attribute-format DynamoDB item for one opportunity (PK=GRANT#{id}, SK=META).

    ``match_dsl`` / ``match_query`` (the compiled grant->researcher matcher inputs) are persisted
    as compact-JSON ``S`` blobs when present, and omitted entirely when compilation was off or
    failed — the SPS matcher is fail-closed on missing fields. Default ``None`` keeps every
    existing caller (incl. the SPIN path) byte-identical."""
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
    item.update(match_attrs(match_dsl, match_query))   # match_dsl / match_query (S JSON) when compiled
    eligibility = (judge or {}).get("eligibility")   # structured eligibility (M) when the judge extracted it (#290)
    if isinstance(eligibility, dict):
        item["eligibility"] = {"M": _eligibility_attr(eligibility)}
    return item


# Backfill-only / conditionally-compiled GRANT# attributes a plain re-ingest must not destroy.
# batch_write PutRequests REPLACE the whole item, and these are the expensive Bedrock products
# the ingest paths do NOT recompute: match_dsl/match_query are only built under --compile-match
# (default OFF; backfill_match.py populates the corpus) and match_rel is backfill-ONLY
# (backfill_rel.py — never compiled at ingest). Everything else on the item is recomputed from
# fresh source data every ingest (incl. prestige/is_honorific), so new-wins is correct there.
# Future backfill-only fields (e.g. structured eligibility, #290) ride this list.
PRESERVED_ATTRS = ("match_dsl", "match_query", "match_rel")


def fetch_preserved_attrs(client, keys: list, table_name: str = TABLE_NAME) -> dict:
    """``{(PK, SK): {attr: value}}`` for existing items carrying any ``PRESERVED_ATTRS``.

    BatchGetItem in 100-key chunks (the API max), re-queuing ``UnprocessedKeys`` — the same
    pattern as ``utils.dynamodb_helpers.fetch_scored_provenance``. Keys with no existing item
    (fresh ingests) or none of the preserved attrs are omitted."""
    names = {f"#a{i}": attr for i, attr in enumerate(PRESERVED_ATTRS)}
    projection = "PK, SK, " + ", ".join(names)
    result: dict = {}
    for i in range(0, len(keys), 100):
        request = {table_name: {
            "Keys": keys[i:i + 100],
            "ProjectionExpression": projection,
            "ExpressionAttributeNames": names,
        }}
        while request:
            response = client.batch_get_item(RequestItems=request)
            for item in response.get("Responses", {}).get(table_name, []):
                preserved = {attr: item[attr] for attr in PRESERVED_ATTRS if attr in item}
                if preserved:
                    result[(item["PK"]["S"], item["SK"]["S"])] = preserved
            request = response.get("UnprocessedKeys") or None
    return result


def put_grants(client, items: list, table_name: str = TABLE_NAME) -> int:
    """Batch-write GRANT# items, preserving backfill-only attrs on re-put. Returns the count.

    Before writing, existing values of ``PRESERVED_ATTRS`` are merged into any outgoing item
    that lacks them (a whole-item PutRequest would otherwise silently un-match re-ingested
    grants). An item that already carries its own value — compile_match ON — wins."""
    if not items:
        return 0
    unique_keys = {(i["PK"]["S"], i["SK"]["S"]): {"PK": i["PK"], "SK": i["SK"]} for i in items}
    existing = fetch_preserved_attrs(client, list(unique_keys.values()), table_name)
    for item in items:
        for attr, value in existing.get((item["PK"]["S"], item["SK"]["S"]), {}).items():
            item.setdefault(attr, value)
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

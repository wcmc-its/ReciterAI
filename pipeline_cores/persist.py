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

import logging

from utils.dynamodb_helpers import TABLE_NAME, get_dynamo_client, to_decimal
from utils.iso_clock import now_iso

from pipeline_cores.models import (
    STATUS_BELOW,
    STATUS_CANDIDATE,
    CoreUsageRecord,
    SignalResult,
)

logger = logging.getLogger(__name__)


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


# Every attribute build_core_item can emit, probed from build_core_item itself with
# all the optional signals populated rather than restated as a second list here — a
# hand-maintained copy of the attribute set is exactly how put_core_usage came to
# clobber attributes it does not own. PK/SK are the key, never part of an update.
_OWNED_ATTRS = frozenset(build_core_item(CoreUsageRecord(
    pmid="0", core_id="0", likelihood=0.0, status="", scored_at="0",
    signals=SignalResult(ack_matched=True, ack_alias="a", ack_snippet="a",
                         llm_score=0, llm_rationale="a", author_affinity=1.0),
))) - {"PK", "SK"}


def put_core_usage(records: list, *, client=None, table_name: str = TABLE_NAME) -> int:
    """Write one (publication, core) item per record. Returns the count written.

    UpdateItem, not the BatchWriteItem PutRequest this used to be. put_candidate
    below writes the SAME item from the batch_screen path, and a PutRequest is a
    FULL ITEM REPLACE: every run.py write over a screened pair silently destroyed
    the six attributes only batch_screen sets (prefilter_prior, screen_confidence,
    screen_band, screen_version, prefilter_version, run_mode). SPS reads
    prefilter_prior as `topicalPrior` — the review queue's fifth evidence chip — and
    a 2026-09-04 table scan found it on 8,656 live rows (cores 1/2/4/5; core 14, the
    only core ever scored through run.py, had 0).

    SET what this run produced and REMOVE the run.py-owned optionals it did NOT
    (ack_alias, ack_snippet, llm_score, llm_rationale, author_affinity): a previous
    run's llm_rationale surviving on a pair scored without the LLM this time is
    stale evidence reading as fresh. Everything outside _OWNED_ATTRS is left alone.

    Unconditional on purpose — put_candidate's never-downgrade ConditionExpression is
    deliberately NOT copied here, because a re-score has to be able to move a pair.

    ponytail: one UpdateItem per surfaced row, no batching. Ceiling is ~16.9k
    round-trips for a full all-cores run (~186 for core 14's last one) against the 25
    items/call BatchWriteItem gave up — the price of not clobbering. If a full-corpus
    run ever makes it hurt, hand the loop to a ThreadPoolExecutor.
    """
    client = client or get_dynamo_client()
    for rec in records:
        item = build_core_item(rec)
        key = {"PK": item.pop("PK"), "SK": item.pop("SK")}
        attrs = list(item)
        absent = sorted(_OWNED_ATTRS - set(attrs))
        expr = "SET " + ", ".join(f"#a{i} = :a{i}" for i in range(len(attrs)))
        if absent:
            expr += " REMOVE " + ", ".join(f"#r{i}" for i in range(len(absent)))
        names = {f"#a{i}": a for i, a in enumerate(attrs)}
        names.update({f"#r{i}": a for i, a in enumerate(absent)})
        client.update_item(
            TableName=table_name,
            Key=key,
            UpdateExpression=expr,
            # Every name goes through a placeholder: `status` is a reserved word.
            ExpressionAttributeNames=names,
            ExpressionAttributeValues={f":a{i}": item[a] for i, a in enumerate(attrs)},
        )
    return len(records)


def put_candidate(pmid, core_id, *, confidence, band, prior, likelihood,
                  scored_at: str = "", screen_version: str = "", prefilter_version: str = "",
                  client=None, table_name: str = TABLE_NAME) -> bool:
    """Idempotent, never-downgrade conditional write of one batch_screen candidate row.

    Always writes status='candidate' (both the high-confidence 'candidate' and the
    mid-confidence 'curator' screen bands route to the claim queue; `band` distinguishes
    them). Conditional UpdateItem: write only if the row is ABSENT or already engine-owned
    (status candidate/below_threshold). It therefore NEVER overwrites a deterministic
    'confirmed' or a human 'claimed'/'rejected' decision. Returns True if written, False if the
    condition protected a stronger existing status.

    Idempotent for the decision attributes (re-running with identical inputs converges to the
    same status/band/likelihood; only `scored_at` is fresh provenance). CAVEAT: drop-band pairs
    are skipped by the caller, so a pair that was 'candidate' in a prior run and bands to 'drop'
    in a later run RETAINS its stale candidate row (no demote in v1). A reconciliation pass that
    demotes out-of-band engine rows to below_threshold is a follow-up before the calibration flip.
    """
    client = client or get_dynamo_client()
    try:
        client.update_item(
            TableName=table_name,
            Key={"PK": {"S": f"PUB#{pmid}"}, "SK": {"S": f"CORE#{core_id}"}},
            UpdateExpression=(
                "SET #st = :st, pmid = :pmid, core_id = :cid, screen_confidence = :conf, "
                "screen_band = :band, prefilter_prior = :prior, likelihood = :lik, "
                "scored_at = :sat, screen_version = :sv, prefilter_version = :pv, "
                "run_mode = :mode"
            ),
            ConditionExpression="attribute_not_exists(#st) OR #st IN (:st, :below)",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={
                ":st": {"S": STATUS_CANDIDATE},
                ":below": {"S": STATUS_BELOW},
                ":pmid": {"S": str(pmid)},
                ":cid": {"S": str(core_id)},
                ":conf": _n(confidence),
                ":band": {"S": band},
                ":prior": _n(prior),
                ":lik": _n(likelihood),
                ":sat": {"S": scored_at or now_iso()},
                ":sv": {"S": screen_version},
                ":pv": {"S": prefilter_version},
                ":mode": {"S": "batch_screen"},
            },
        )
        return True
    except client.exceptions.ConditionalCheckFailedException:
        return False


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
        # A mid-run throttle here silently zeros the affinity prior and degrades
        # ranking — log loudly so the operator sees it rather than swallowing.
        logger.exception(
            "scan_prior_core_usage failed (core_id=%s) — affinity prior degraded "
            "to empty for this run", core_id,
        )
        return []
    return out

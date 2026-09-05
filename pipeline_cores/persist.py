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
    STATUS_CLAIMED,
    STATUS_REJECTED,
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
    if s.method_families:
        # All four together, or none: the tier, tool and sentence are only meaningful
        # as the top of the ranked list, and a tier with no families behind it would
        # read as evidence nobody can check.
        item["method_families"] = {"L": [{"S": f} for f in s.method_families]}
        item["method_tier"] = {"S": s.method_tier}
        item["method_tool"] = {"S": s.method_tool}
        item["method_snippet"] = {"S": s.method_snippet[:500]}   # capped like ack_snippet
    return item


# Every attribute build_core_item can emit, probed from build_core_item itself with
# all the optional signals populated rather than restated as a second list here — a
# hand-maintained copy of the attribute set is exactly how put_core_usage came to
# clobber attributes it does not own. PK/SK are the key, never part of an update.
_OWNED_ATTRS = frozenset(build_core_item(CoreUsageRecord(
    pmid="0", core_id="0", likelihood=0.0, status="", scored_at="0",
    signals=SignalResult(ack_matched=True, ack_alias="a", ack_snippet="a",
                         llm_score=0, llm_rationale="a", author_affinity=1.0,
                         # Populated for the same reason as everything else here: an
                         # attribute build_core_item can emit but this probe cannot see
                         # falls OUTSIDE _OWNED_ATTRS, so put_core_usage never sweeps it
                         # into REMOVE and a stale method family survives on a row the
                         # run no longer supports.
                         method_families=["a"], method_tier="strong",
                         method_tool="a", method_snippet="a"),
))) - {"PK", "SK"}


# The two statuses a human owns. Everything else on a CORE# row is engine-owned and
# stays fully mutable — see put_core_usage's guard.
_HUMAN_STATUSES = (STATUS_CLAIMED, STATUS_REJECTED)


def _update_args(item: dict, attrs: list) -> tuple:
    """(UpdateExpression, ExpressionAttributeNames, ExpressionAttributeValues) for `attrs`.

    Split out of put_core_usage because the human-status guard has to build the SAME
    UpdateItem twice — once with `status` in the SET list, once without — and DynamoDB
    rejects an ExpressionAttributeNames entry no expression references, so the second
    build has to drop the placeholder, not merely the assignment.

    The REMOVE list is derived from `item` (everything build_core_item produced this
    run), NEVER from `attrs`: on the second build `attrs` is missing `status`, and
    deriving from it would move `status` into the REMOVE clause — deleting the very
    human decision the guard just protected.
    """
    absent = sorted(_OWNED_ATTRS - set(item))
    expr = "SET " + ", ".join(f"#a{i} = :a{i}" for i in range(len(attrs)))
    if absent:
        expr += " REMOVE " + ", ".join(f"#r{i}" for i in range(len(absent)))
    # Every name goes through a placeholder: `status` is a reserved word.
    names = {f"#a{i}": a for i, a in enumerate(attrs)}
    names.update({f"#r{i}": a for i, a in enumerate(absent)})
    values = {f":a{i}": item[a] for i, a in enumerate(attrs)}
    return expr, names, values


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
    (ack_alias, ack_snippet, llm_score, llm_rationale, author_affinity, and the four
    method_* attributes): a previous run's llm_rationale surviving on a pair scored
    without the LLM this time is stale evidence reading as fresh. Everything outside
    _OWNED_ATTRS is left alone.

    The write is conditional on the row NOT holding a HUMAN status (ReciterAI #386
    recommendation 1). Nothing about a re-score is frozen by that: an engine
    'confirmed' can still be walked back to 'candidate' or 'below_threshold', which is
    exactly why put_candidate's broader never-downgrade condition is still not copied
    here. Only 'claimed' and 'rejected' — the two statuses the engine never writes and
    a reviewer in SPS does — are off limits.

    This matters because of the CADENCE, not the semantics. As an operator-run job the
    old unconditional write was harmless; on a NIGHTLY schedule it means the engine
    forgets every claim and rejection made that day, every night, on core 14 — the only
    core that has reviewers. SPS's own display is unaffected either way (its `core_claim`
    table is authoritative and takes read-time precedence), but the cross-run affinity
    prior reads status straight out of DynamoDB, so a wiped 'claimed' silently degrades
    the prior with nothing visible to say so.

    On ConditionalCheckFailedException the same UpdateItem is re-issued with `status`
    dropped from the SET list: likelihood, scored_at and the evidence attributes still
    refresh on a claimed/rejected row (a reviewer's decision should not also freeze the
    evidence under it), only the decision itself survives.

    ponytail: one UpdateItem per surfaced row, no batching, and a guarded row costs
    TWO (the failed conditional write plus the retry). Worst-case ceiling is therefore
    ~33.8k round-trips for a full all-cores run and ~372 for core 14, against the 25
    items/call BatchWriteItem gave up — the price of not clobbering. Note the guarded
    path becomes the COMMON path on the one core with reviewers, not the exception:
    claimed/rejected rows only accumulate, and every one of them pays both writes every
    night. If a full-corpus run ever makes it hurt, hand the loop to a ThreadPoolExecutor.
    """
    client = client or get_dynamo_client()
    guarded = 0
    for rec in records:
        item = build_core_item(rec)
        key = {"PK": item.pop("PK"), "SK": item.pop("SK")}
        attrs = list(item)
        expr, names, values = _update_args(item, attrs)
        # Reuse the placeholder the SET clause already minted for `status` rather than
        # binding a second name to the same attribute.
        st = next(n for n, a in names.items() if a == "status")
        try:
            client.update_item(
                TableName=table_name,
                Key=key,
                UpdateExpression=expr,
                ConditionExpression=f"attribute_not_exists({st}) OR NOT {st} IN (:claimed, :rejected)",
                ExpressionAttributeNames=names,
                ExpressionAttributeValues={
                    **values,
                    ":claimed": {"S": STATUS_CLAIMED},
                    ":rejected": {"S": STATUS_REJECTED},
                },
            )
        except client.exceptions.ConditionalCheckFailedException:
            guarded += 1
            keep = [a for a in attrs if a != "status"]
            expr, names, values = _update_args(item, keep)
            client.update_item(
                TableName=table_name,
                Key=key,
                UpdateExpression=expr,
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
    if guarded:
        # One line, at the end: an operator reading the first nightly tick has to be
        # able to see the guard firing at all, and a per-row log would bury it.
        logger.info(
            "put_core_usage: %d of %d rows already held a human status (%s) — refreshed "
            "their evidence but left the decision alone",
            guarded, len(records), "/".join(_HUMAN_STATUSES),
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


def get_curated_clients(core_id: str, *, client=None, table_name: str = TABLE_NAME) -> set:
    """CWIDs SPS's "Known clients" panel has curated for this core, lowercased.

    GetItem on PK=CORE#{core_id}, SK=CLIENTS, attribute client_cwids (a DynamoDB
    List of String the SPS DocumentClient writes already lowercased and sorted).

    SK is "CLIENTS", not "CORE#{core_id}": both scan_prior_core_usage (above) and
    the SPS ETL select (pub, core) usage rows with begins_with(SK, "CORE#") — if
    this config item used that prefix it would surface as a fake usage row (a
    phantom publication) in both places instead of the per-core setting it is.

    Resilient by design, never fatal to the run: a missing item (no clients
    curated for this core yet — the normal state for 9 of 10 cores), a
    missing/empty client_cwids attribute, or any botocore ClientError or other
    exception while reading all degrade to an empty set, and the caller
    proceeds unaffected. Only the exception path is unusual enough to warrant a
    warning log line; a missing item or attribute is silent (logger.debug).
    """
    client = client or get_dynamo_client()
    try:
        resp = client.get_item(
            TableName=table_name,
            Key={"PK": {"S": f"CORE#{core_id}"}, "SK": {"S": "CLIENTS"}},
            ProjectionExpression="client_cwids",
        )
    except Exception as exc:
        logger.warning(
            "get_curated_clients(core_id=%s) failed (%s: %s) — treating as no "
            "curated clients for this run", core_id, type(exc).__name__, exc,
        )
        return set()

    item = resp.get("Item")
    if not item:
        logger.debug(
            "get_curated_clients(core_id=%s): no CORE#%s/CLIENTS item found — "
            "treating as no curated clients for this run", core_id, core_id,
        )
        return set()

    raw = item.get("client_cwids", {}).get("L")
    if not raw:
        logger.debug(
            "get_curated_clients(core_id=%s): item has no client_cwids attribute — "
            "treating as no curated clients for this run", core_id,
        )
        return set()

    return {v["S"].strip().lower() for v in raw if v.get("S", "").strip()}


# Statuses that establish a (pub, core) usage for the affinity prior. 'claimed'
# (human-confirmed in SPS) and engine 'confirmed' both count; candidates do not.
_USER_STATUSES = {"confirmed", "claimed"}


def scan_prior_core_usage(core_id: str = None, *, strict: bool = False, client=None,
                          table_name: str = TABLE_NAME) -> list:
    """Return prior [{pmid, core_id, status}] for confirmed/claimed CORE# rows.

    Full-table paginated Scan with a server-side FilterExpression (same pattern as
    scan_invalid_pmids). Used to seed the cross-run repeat-user affinity prior.
    Returns [] on any error (e.g. table absent on first run) so the pipeline still
    runs on this run's own confirmations — UNLESS `strict`, which re-raises instead.
    Pass strict=True from any caller that PERSISTS what it read; see the raise below.
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
        if strict:
            # strict=True is for the callers that WRITE what they read. An empty prior
            # is not a worse ranking there, it is a wrong one that gets persisted:
            # author_affinity computes to 0.0, build_core_item omits a falsy affinity,
            # put_core_usage sweeps it into REMOVE, and every row carried by affinity
            # alone demotes confirmed -> candidate. Degrading is fine for the two
            # analysis callers (batch_screen, suggest_aliases) whose output nobody
            # stores; run.py opts in to failing instead. Same reasoning as
            # scan_core_llm_scores below, which has only the writing caller and so
            # raises unconditionally.
            raise
        return []
    return out


def scan_core_llm_scores(core_id: str = None, *, client=None, table_name: str = TABLE_NAME) -> dict:
    """Return {core_id: {pmid: {"score": int, "rationale": str}}} for stored LLM evidence.

    Full-table paginated Scan, same shape as scan_prior_core_usage above: server-side
    FilterExpression on the CORE# rows, narrowed to core_id when one is given, and only
    the four attributes the carry-forward needs projected. The value shape matches what
    signals.llm_triage returns for the keys run_core reads (`score`, `rationale`), so a
    carried-forward entry and a freshly triaged one are interchangeable at the call site.

    RAISES on error. It does NOT degrade to {} — and that is the whole difference from
    scan_prior_core_usage, which returns [] on failure because an empty affinity prior
    only degrades RANKING. An empty result here is not a degraded answer, it is a WRONG
    one: the caller would read it as "no row has an LLM score", pass nothing forward,
    and put_core_usage would then REMOVE llm_score/llm_rationale from every row it
    rewrites (#384's owned-attribute sweep). A throttled Scan would silently WIPE the
    evidence this function exists to preserve — on core 14 that is the chip all 62 open
    review-queue rows carry. Failing the run is the cheap outcome; the operator re-runs.
    """
    client = client or get_dynamo_client()
    eav = {":sk": {"S": "CORE#"}}
    filt = "begins_with(SK, :sk) AND attribute_exists(llm_score)"
    if core_id is not None:
        filt += " AND core_id = :cid"
        eav[":cid"] = {"S": str(core_id)}
    kwargs = {
        "TableName": table_name,
        "ProjectionExpression": "pmid, core_id, llm_score, llm_rationale",
        "FilterExpression": filt,
        "ExpressionAttributeValues": eav,
    }
    out: dict = {}
    rows = 0
    while True:
        resp = client.scan(**kwargs)
        for it in resp.get("Items", []):
            pmid = it.get("pmid", {}).get("S", "")
            cid = it.get("core_id", {}).get("S", "")
            raw = it.get("llm_score", {}).get("N")
            if not (pmid and cid and raw is not None):
                # RAISE, do not skip. Every row this Scan can see was written by
                # build_core_item, which always emits pmid, core_id and llm_score as N —
                # so a row that matched `attribute_exists(llm_score)` and still fails
                # this check means something ELSE is writing CORE# rows in a shape this
                # function cannot read. Skipping it silently is how a full table of
                # unreadable rows becomes an empty carry-forward and then a mass REMOVE:
                # the same wrong-not-degraded answer the docstring above refuses, one
                # level down. `rows` counts accepted rows only, so a per-row skip would
                # not even be visible in the summary line.
                raise ValueError(
                    f"scan_core_llm_scores: CORE# row {it.get('pmid')} / "
                    f"{it.get('core_id')} carries llm_score in an unreadable shape "
                    f"({it.get('llm_score')}) — refusing to return a partial carry-forward"
                )
            # Stored through to_decimal, so a whole number arrives as "8" — but read it
            # through float first, because a Decimal that ever round-trips as "8.0"
            # would make int() raise mid-scan and take the run down for a formatting
            # detail, on the one code path whose job is to not lose data.
            out.setdefault(cid, {})[pmid] = {
                "score": int(float(raw)),
                "rationale": it.get("llm_rationale", {}).get("S", ""),
            }
            rows += 1
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek
    logger.info("scan_core_llm_scores(core_id=%s): %d stored LLM scores across %d core(s)",
                core_id, rows, len(out))
    return out

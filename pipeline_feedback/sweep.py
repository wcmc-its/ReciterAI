"""Phase 12 §9 feedback sweep — consumes UNCOVERED_PMID#, DRIFT#evaluation
per_topic_low_confidence (Plan drift-extension), CRITIC_REJECT# (Plan
feedback-producer, per-publish/per-subtopic keyed per D-08); produces three
finding records (Plan feedback-consumer Task 1).

D-01: triggered_by ∈ {"operator", "cold_run"}; both paths share this code.
D-02: run_sweep never raises on findings; only DDB/Bedrock errors propagate.
D-05: one run_id per invocation; all writes carry it; idempotent overwrite per PK.
D-06: uncovered PMIDs sorted ascending by top_topic_score (worst-fitting first)
      and capped at feedback_sweep_max_pmids; overflow sets truncated+count.
D-07: recluster trigger reads per_topic_low_confidence from DRIFT#evaluation rows
      (Plan drift-extension D-34 materialized counter); persistence check over
      recluster_persistence_days days.
D-08: SPOTLIGHT_DIAGNOSTIC aggregation keyed by subtopic_id (CONTEXT CR-2026-05-12);
      per-faculty drill-down via author_cwids on underlying CRITIC_REJECT# rows.
D-09: distinct_pmid_set_count = distinct (publish_id, pmid_set_hash) pairs per subtopic.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from pipeline_feedback.finding_records import (
    write_candidate_topic,
    write_recluster_recommendation,
    write_spotlight_diagnostic,
)
from utils.env_check import load_thresholds

logger = logging.getLogger(__name__)

# Prompt file for Sonnet uncovered-PMID sweep
_PROMPT_PATH = Path(__file__).parent / "prompts" / "uncovered_pmid_sonnet_v0.md"

# Model ID for Sonnet (Bedrock)
_SONNET_MODEL_ID = "anthropic.claude-sonnet-4-5"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_iso(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def _in_window(ts_str: str, *, since: datetime, until: datetime) -> bool:
    """Return True if ts_str (ISO 8601) falls within [since, until]."""
    try:
        ts = _parse_iso(str(ts_str))
    except (ValueError, AttributeError):
        return False
    return since <= ts <= until


# ---------------------------------------------------------------------------
# WR-05: Sonnet parse status sentinels — surface degraded sweeps explicitly
# ---------------------------------------------------------------------------
SONNET_PARSE_OK = "ok"
SONNET_PARSE_NOT_JSON = "not_json"
SONNET_PARSE_NO_TEXT = "no_text"

# Module-level latch updated by _invoke_sonnet on each call. The sweep
# orchestrator reads this after invocation and copies it onto the
# FeedbackSweepRun so callers can distinguish a degraded sweep from
# a clean "no candidates" run.
_last_sonnet_parse_status: str = SONNET_PARSE_OK


# ---------------------------------------------------------------------------
# FeedbackSweepRun result dataclass
# ---------------------------------------------------------------------------


@dataclass
class FeedbackSweepRun:
    source_sweep_run_id: str
    triggered_by: str            # "operator" | "cold_run"
    started_at: str
    since: str
    candidate_topics: list[dict] = field(default_factory=list)
    recluster_recommendations: list[dict] = field(default_factory=list)
    spotlight_diagnostics: list[dict] = field(default_factory=list)
    # WR-05: surfaces _invoke_sonnet's parse status so a degraded sweep is
    # distinguishable from a clean "no candidates" run. One of
    # SONNET_PARSE_OK / SONNET_PARSE_NOT_JSON / SONNET_PARSE_NO_TEXT.
    sonnet_parse_status: str = SONNET_PARSE_OK


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_sweep(
    *,
    table: Any,
    since: datetime,
    triggered_by: str,
    run_id: str | None = None,
    max_pmids: int | None = None,
    bedrock_client: Any = None,       # injected for testability; None → boto3 lazy-import
    thresholds: dict | None = None,   # injected for testability; None → load from disk
) -> FeedbackSweepRun:
    """Single entry point for both the operator CLI and the cold-stage subprocess.

    D-01: triggered_by ∈ {"operator", "cold_run"}; both paths share this code.
    D-02: this function never raises on findings; only DDB/Bedrock errors propagate.
    D-05: one run_id per invocation; all writes carry it; idempotent overwrite per PK.
    D-08: SPOTLIGHT_DIAGNOSTIC aggregation is keyed by subtopic_id (per CONTEXT
          CR-2026-05-12 re-framing); per-faculty drill-down via author_cwids on
          underlying CRITIC_REJECT# rows.
    """
    cfg = thresholds if thresholds is not None else load_thresholds()
    max_pmids_eff = max_pmids if max_pmids is not None else int(cfg["feedback_sweep_max_pmids"])
    run_id = run_id or str(uuid.uuid4())
    started_at = _now_iso()

    result = FeedbackSweepRun(
        source_sweep_run_id=run_id,
        triggered_by=triggered_by,
        started_at=started_at,
        since=since.isoformat(),
    )

    # --- 1. Uncovered-PMID Sonnet sweep (D-06) ---
    # WR-05: reset the parse-status latch before invocation so a clean run
    # cannot inherit a stale degraded marker from a prior call.
    global _last_sonnet_parse_status
    _last_sonnet_parse_status = SONNET_PARSE_OK
    result.candidate_topics = _run_uncovered_pmid_sweep(
        table=table,
        since=since,
        max_pmids=max_pmids_eff,
        bedrock_client=bedrock_client,
        run_id=run_id,
        triggered_by=triggered_by,
    )
    result.sonnet_parse_status = _last_sonnet_parse_status

    # --- 2. Recluster trigger from DRIFT#evaluation per_topic_low_confidence (D-07 + D-34) ---
    result.recluster_recommendations = _run_recluster_trigger(
        table=table,
        cfg=cfg,
        run_id=run_id,
        triggered_by=triggered_by,
    )

    # --- 3. SPOTLIGHT_DIAGNOSTIC#{subtopic_id} from CRITIC_REJECT# aggregation (D-08 + D-09 + D-30 + D-32) ---
    result.spotlight_diagnostics = _run_diagnostic_aggregation(
        table=table,
        cfg=cfg,
        since=since,
        run_id=run_id,
        triggered_by=triggered_by,
    )

    logger.info(
        "feedback_sweep complete: run_id=%s triggered_by=%s "
        "candidates=%d reclusters=%d diagnostics=%d sonnet_parse_status=%s",
        run_id,
        triggered_by,
        len(result.candidate_topics),
        len(result.recluster_recommendations),
        len(result.spotlight_diagnostics),
        result.sonnet_parse_status,
    )
    return result


# ---------------------------------------------------------------------------
# 1. Uncovered-PMID Sonnet sweep (D-06)
# ---------------------------------------------------------------------------


def _scan_uncovered_pmids(table: Any, *, since: datetime) -> list[dict]:
    """Scan UNCOVERED_PMID# rows created at or after since.

    Returns all rows; the caller sorts and caps.
    """
    from boto3.dynamodb.conditions import Attr
    since_iso = since.isoformat(timespec="seconds").replace("+00:00", "Z")
    scan_kwargs: dict[str, Any] = {
        "FilterExpression": (
            Attr("PK").begins_with("UNCOVERED_PMID#") & Attr("created_at").gte(since_iso)
        )
    }
    rows: list[dict] = []
    while True:
        resp = table.scan(**scan_kwargs)
        rows.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return rows


def _invoke_sonnet(bedrock_client: Any, uncovered_rows: list[dict]) -> list[dict]:
    """Call Bedrock Sonnet with the uncovered PMIDs payload.

    Returns a list of candidate_topic dicts per the prompt schema. On a JSON
    parse failure of the text response, returns [] and sets
    ``_last_sonnet_parse_status`` to SONNET_PARSE_NOT_JSON so the caller
    (and any operator inspecting the sweep summary) can distinguish a degraded
    sweep from a clean "no candidates" run (WR-05).
    """
    global _last_sonnet_parse_status
    _last_sonnet_parse_status = SONNET_PARSE_OK
    prompt_text = _PROMPT_PATH.read_text(encoding="utf-8")

    payload = {
        "uncovered_pmids": [
            {
                "pmid": str(r.get("pmid", "")),
                "top_topics": r.get("top_topics", []),
            }
            for r in uncovered_rows
        ]
    }

    messages_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4096,
        "messages": [
            {
                "role": "user",
                "content": (
                    f"{prompt_text}\n\n"
                    f"Input:\n{json.dumps(payload, default=str)}"
                ),
            }
        ],
    }

    response = bedrock_client.invoke_model(
        modelId=_SONNET_MODEL_ID,
        body=json.dumps(messages_body).encode("utf-8"),
        contentType="application/json",
        accept="application/json",
    )
    body_bytes = response["body"].read()
    body_json = json.loads(body_bytes)

    # Extract text from Bedrock response
    text = ""
    if "content" in body_json:
        for block in body_json["content"]:
            if block.get("type") == "text":
                text += block.get("text", "")
    elif "candidate_topics" in body_json:
        # Direct JSON response (from mock)
        return body_json["candidate_topics"]

    # Parse the JSON out of the text response
    if not text:
        _last_sonnet_parse_status = SONNET_PARSE_NO_TEXT
        logger.warning(
            "feedback_sweep: Sonnet response contained no text blocks; "
            "marking sweep degraded (parse_status=%s)",
            SONNET_PARSE_NO_TEXT,
        )
        return []
    try:
        parsed = json.loads(text)
        return parsed.get("candidate_topics", [])
    except (json.JSONDecodeError, AttributeError):
        _last_sonnet_parse_status = SONNET_PARSE_NOT_JSON
        logger.warning(
            "feedback_sweep: Sonnet response was not valid JSON; skipping candidate "
            "topics (parse_status=%s). Inspect bedrock invocation logs to diagnose.",
            SONNET_PARSE_NOT_JSON,
        )
        return []


def _run_uncovered_pmid_sweep(
    *,
    table: Any,
    since: datetime,
    max_pmids: int,
    bedrock_client: Any,
    run_id: str,
    triggered_by: str,
) -> list[dict]:
    """D-06: scan UNCOVERED_PMID# rows, sort worst-fitting first, cap, call Sonnet."""
    rows = _scan_uncovered_pmids(table, since=since)
    if not rows:
        return []

    # Sort ascending by top_topic_score (worst-fitting first per D-06)
    def _score(r: dict) -> float:
        s = r.get("top_topic_score", 0)
        try:
            return float(s)
        except (TypeError, ValueError):
            return 0.0

    sorted_rows = sorted(rows, key=_score)
    total = len(sorted_rows)
    truncated = total > max_pmids
    capped = sorted_rows[:max_pmids]
    remaining = total - max_pmids if truncated else 0

    # Get bedrock client if not injected
    if bedrock_client is None:
        import boto3
        bedrock_client = boto3.client("bedrock-runtime")

    candidate_topics_raw = _invoke_sonnet(bedrock_client, capped)
    if not candidate_topics_raw:
        return []

    written: list[dict] = []
    for ct in candidate_topics_raw:
        slug = ct.get("slug", "")
        if not slug:
            continue
        item = write_candidate_topic(
            table,
            slug=slug,
            proposed_label=ct.get("proposed_label", slug),
            source_pmids=[str(p) for p in ct.get("evidence_pmids", [])],
            sonnet_rationale=ct.get("rationale", ""),
            source_sweep_run_id=run_id,
            triggered_by=triggered_by,
            truncated=truncated,
            total_unprocessed_remaining=remaining if truncated else None,
        )
        written.append(item)
    return written


# ---------------------------------------------------------------------------
# 2. Recluster trigger (D-07 + D-34)
# ---------------------------------------------------------------------------


def _query_drift_rows(table: Any, *, persistence_days: int) -> list[dict]:
    """Query the last persistence_days DRIFT#evaluation rows (ordered SK desc)."""
    from boto3.dynamodb.conditions import Key
    resp = table.query(
        KeyConditionExpression=(
            Key("PK").eq("DRIFT#evaluation") & Key("SK").begins_with("DAY#")
        ),
        ScanIndexForward=False,
        Limit=persistence_days * 3,  # over-fetch to cover sparse days
    )
    return resp.get("Items", [])


def _run_recluster_trigger(
    *,
    table: Any,
    cfg: dict,
    run_id: str,
    triggered_by: str,
) -> list[dict]:
    """D-07: check each topic's per_topic_low_confidence over recluster_persistence_days.

    Reads materialized per_topic_low_confidence from DRIFT#evaluation rows
    (Plan drift-extension D-34). Treats absent field as empty (pre-D-34 rows).
    Emits RECLUSTER_RECOMMENDATION#{topic_id} when a topic exceeded
    drift_low_confidence_topic_max every day in the window.
    """
    persistence_days = int(cfg.get("recluster_persistence_days", 7))
    topic_max = int(cfg.get("drift_low_confidence_topic_max", 50))

    rows = _query_drift_rows(table, persistence_days=persistence_days)
    if not rows:
        return []

    # Sort rows by SK ascending to get chronological order, take last persistence_days
    rows_sorted = sorted(rows, key=lambda r: r.get("SK", ""))
    window_rows = rows_sorted[-persistence_days:]

    if len(window_rows) < persistence_days:
        # Not enough history yet; no triggers
        return []

    # Build per-topic history across the window
    # per_topic_days[topic_id] = list of {date, count} for days count > topic_max
    all_topics: set[str] = set()
    for row in window_rows:
        per_topic = row.get("per_topic_low_confidence") or {}
        all_topics.update(per_topic.keys())

    written: list[dict] = []
    for topic_id in sorted(all_topics):
        qualifying_days: list[dict] = []
        for row in window_rows:
            per_topic = row.get("per_topic_low_confidence") or {}
            count = per_topic.get(topic_id, 0)
            if count > topic_max:
                # Extract date from SK "DAY#YYYY-MM-DD"
                sk = row.get("SK", "")
                date_str = sk[4:] if sk.startswith("DAY#") else sk
                qualifying_days.append({"date": date_str, "count": count})

        if len(qualifying_days) == persistence_days:
            item = write_recluster_recommendation(
                table,
                topic_id=topic_id,
                evaluation_history=qualifying_days,
                source_sweep_run_id=run_id,
                triggered_by=triggered_by,
            )
            written.append(item)

    return written


# ---------------------------------------------------------------------------
# 3. SPOTLIGHT_DIAGNOSTIC aggregation (D-08 + D-09 + D-30 + D-32)
# ---------------------------------------------------------------------------


def _scan_critic_rejects(table: Any, *, since: datetime) -> list[dict]:
    """Scan CRITIC_REJECT# rows created at or after since."""
    from boto3.dynamodb.conditions import Attr
    since_iso = since.isoformat(timespec="seconds").replace("+00:00", "Z")
    scan_kwargs: dict[str, Any] = {
        "FilterExpression": (
            Attr("PK").begins_with("CRITIC_REJECT#") & Attr("created_at").gte(since_iso)
        )
    }
    rows: list[dict] = []
    while True:
        resp = table.scan(**scan_kwargs)
        rows.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return rows


def _run_diagnostic_aggregation(
    *,
    table: Any,
    cfg: dict,
    since: datetime,
    run_id: str,
    triggered_by: str,
) -> list[dict]:
    """D-08 + D-09 + D-30 + D-32: aggregate CRITIC_REJECT# rows per subtopic_id.

    1. Query CRITIC_REJECT# rows in the critic_reject_persistence_days window.
    2. Group by subtopic_id (NOT cwid — D-08 grain change).
    3. Count distinct (publish_id, pmid_set_hash) pairs per subtopic (D-09 semantics).
    4. Aggregate reason_code distribution across all rows per subtopic.
    5. Build underlying_rejects as {publish_id}#{subtopic_id}#{pmid_set_hash} suffixes (D-08 PK shape).
    6. Cap underlying_rejects at feedback_diagnostic_max_underlying (D-32).
    7. Emit SPOTLIGHT_DIAGNOSTIC#{subtopic_id} if distinct_pmid_set_count >= critic_reject_subtopic_max.
    """
    persistence_days = int(cfg.get("critic_reject_persistence_days", 90))
    subtopic_max = int(cfg.get("critic_reject_subtopic_max", 2))
    max_underlying = int(cfg.get("feedback_diagnostic_max_underlying", 20))

    # The window for critic rejects: since passed in (already computed by caller)
    # but we also need to apply the persistence_days window for diagnostic
    now = datetime.now(timezone.utc)
    diag_since = now - timedelta(days=persistence_days)
    # Use the more recent of the two since bounds
    effective_since = max(since, diag_since)

    rows = _scan_critic_rejects(table, since=effective_since)
    if not rows:
        return []

    # Filter rows to the window (belt-and-suspenders — the scan already filters by created_at)
    until = now
    in_window_rows = [
        r for r in rows
        if _in_window(r.get("created_at", ""), since=effective_since, until=until)
    ]

    # Group by subtopic_id
    by_subtopic: dict[str, list[dict]] = {}
    for row in in_window_rows:
        subtopic_id = row.get("subtopic_id", "")
        if not subtopic_id:
            continue
        by_subtopic.setdefault(subtopic_id, []).append(row)

    written: list[dict] = []
    for subtopic_id, sub_rows in sorted(by_subtopic.items()):
        # D-09: count distinct (publish_id, pmid_set_hash) pairs
        distinct_pairs: set[tuple[str, str]] = set()
        for row in sub_rows:
            pid = row.get("publish_id", "")
            psh = row.get("pmid_set_hash", "")
            if pid and psh:
                distinct_pairs.add((pid, psh))

        distinct_count = len(distinct_pairs)
        if distinct_count < subtopic_max:
            continue

        # D-30: aggregate reason_code distribution (one entry per CRITIC_REJECT# row)
        reason_dist: dict[str, int] = {}
        for row in sub_rows:
            rc = str(row.get("reason_code", "unknown"))
            reason_dist[rc] = reason_dist.get(rc, 0) + 1

        # D-32: build underlying_rejects as {publish_id}#{subtopic_id}#{pmid_set_hash}
        # Use distinct (publish_id, pmid_set_hash) pairs to avoid duplicating the same
        # rejection event across multiple CRITIC_REJECT# rows with the same pair.
        # Order is stable (sorted by the tuple) for determinism.
        underlying_rejects = [
            f"{pid}#{subtopic_id}#{psh}"
            for pid, psh in sorted(distinct_pairs)
        ]

        item = write_spotlight_diagnostic(
            table,
            subtopic_id=subtopic_id,
            reason_code_distribution=reason_dist,
            distinct_pmid_set_count=distinct_count,
            underlying_rejects=underlying_rejects,
            max_underlying=max_underlying,
            window_days=persistence_days,
            source_sweep_run_id=run_id,
            triggered_by=triggered_by,
        )
        written.append(item)

    return written

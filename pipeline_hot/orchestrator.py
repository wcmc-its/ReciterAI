"""Hot-path orchestrator: state-machine entrypoint.

Responsibilities:

1. Resolve `last_successful_hot_run_at` by querying the latest
   `STAGE#hot_run#GLOBAL` `complete` row (D-06).
2. Compute the delta PMID set by asking ReciterDB for publications
   added or modified since that timestamp.
3. Detect a concurrent hot-path execution (per Open Q5). If one is
   running, persist a `STAGE#hot_run#GLOBAL` `skipped` row with
   `skip_reason: "prior_run_in_progress"` and short-circuit.
4. Return the initial state-machine input: the delta PMID set, the
   resolved cutoff, and the run identifiers the downstream handlers
   need to compose their envelopes.

The Lambda entry point is `handler(event, context)` and is registered
in `infra/eventbridge.json` (T12) as the target of the weekly cron
rule. It is invoked by Step Functions as the first Task state of
`state_machine.asl.json`.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Ensure repo root is importable regardless of cwd
sys.path.insert(0, str(Path(__file__).parent.parent))

# Populate DB_* env vars from Secrets Manager when running in Lambda.
# Local dev / tests rely on `~/.zshrc` and skip the fetch. Must precede
# `utils.sql_queries` so the SQLAlchemy engine factory finds creds.
import utils.secrets_loader  # noqa: F401

from utils.dynamodb_helpers import (
    get_dynamo_client,
    get_processing_rows,
    get_table,
    quarantine_pmid,
    query_failed_pmids,
    TABLE_NAME,
)
from utils.env_check import load_thresholds
from utils.stage_records import (
    STATUS_COMPLETE,
    build_skipped_record,
    write_skipped,
)
from pipeline_common import alert
from pipeline_enrichment import alerting as teams_alerting
from utils.iso_clock import now_iso

logger = logging.getLogger(__name__)

HOT_RUN_STAGE = "hot_run"
HOT_RUN_SCOPE = "GLOBAL"
SKIP_REASON_LOCKED = "prior_run_in_progress"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# D-06: last successful hot run
# ---------------------------------------------------------------------------


def resolve_last_successful_hot_run(table: Any) -> str | None:
    """Return the `started_at` of the most recent `STAGE#hot_run#GLOBAL`
    `complete` row, or None if no such row exists.

    Implementation per D-06: ScanIndexForward=false, walk newest first,
    return the first complete row encountered. We do not stop at
    skipped/failed rows because the question is "when did we last
    *successfully* process the corpus", not "when did we last run".
    """
    pk = f"STAGE#{HOT_RUN_STAGE}#{HOT_RUN_SCOPE}"
    resp = table.query(
        KeyConditionExpression="#pk = :pk",
        ExpressionAttributeNames={"#pk": "PK"},
        ExpressionAttributeValues={":pk": pk},
        ScanIndexForward=False,
    )
    for item in resp.get("Items", []):
        if item.get("status") == STATUS_COMPLETE:
            return item.get("started_at")
    return None


# ---------------------------------------------------------------------------
# Delta PMID resolution
# ---------------------------------------------------------------------------


# Bootstrap fallback when no prior successful hot run exists. The cold
# path is the right way to populate the corpus from scratch; the hot
# path should never be asked to score "all of history" via cron. We
# refuse to fall back further than this look-back window to keep the
# delta-set query bounded. Lifted to config/thresholds.json
# `bootstrap_lookback_days` per #57 Tier B-1.
_BOOTSTRAP_LOOKBACK_DAYS = int(load_thresholds()["bootstrap_lookback_days"])


def resolve_delta_pmids(
    last_run_at: str | None,
    *,
    query_fn=None,
) -> list[str]:
    """Resolve the delta PMID set: new publications since `last_run_at`.

    `query_fn(since_iso) -> list[str]` is injected for testability — in
    production it queries ReciterDB; in tests it is mocked. The query
    contract: return PMIDs added or modified at or after `since_iso`.

    When `last_run_at` is None (no prior successful hot run), we look
    back `_BOOTSTRAP_LOOKBACK_DAYS` rather than asking ReciterDB for
    the entire corpus — the cold path owns full-corpus runs.
    """
    if query_fn is None:
        raise ValueError(
            "resolve_delta_pmids requires a query_fn; production wires this to "
            "ReciterDB. Tests should inject a stub."
        )

    if last_run_at is None:
        # Bootstrap window. We refuse to fall back further to keep
        # delta-set sizes bounded for state-machine cost.
        lookback = (
            datetime.now(timezone.utc).replace(microsecond=0)
            - timedelta(days=_BOOTSTRAP_LOOKBACK_DAYS)
        )
        since_iso = lookback.isoformat(timespec="seconds").replace("+00:00", "Z")
        logger.warning(
            "No prior successful hot run found; bootstrapping with "
            f"{_BOOTSTRAP_LOOKBACK_DAYS}-day lookback (since={since_iso}). "
            "If the corpus is empty, run the cold path first."
        )
    else:
        since_iso = last_run_at

    pmids = list(query_fn(since_iso))
    # Deduplicate + stringify defensively.
    return sorted({str(p) for p in pmids})


# ---------------------------------------------------------------------------
# State-based retry sweep
# ---------------------------------------------------------------------------

# The current taxonomy version is the GSI hash key for the failed-PMID
# query. taxonomy_v2.json is bundled into the orchestrator Lambda zip for
# this reason — see the orchestrator row in scripts/build_lambda_zips.sh.
_TAXONOMY_FILE = Path(__file__).parent.parent / "taxonomy_v2.json"


def current_taxonomy_version() -> str:
    """Return the active `taxonomy_version` string from taxonomy_v2.json."""
    with open(_TAXONOMY_FILE) as f:
        return json.load(f)["taxonomy_version"]


def resolve_retry_sweep(
    client: Any,
    *,
    table_name: str,
    taxonomy_version: str,
    thresholds: dict,
    now_dt: datetime | None = None,
) -> dict:
    """Recover failed PMIDs that aged out of the date delta; quarantine the
    un-fixable ones.

    Each weekly orchestrator pass runs this after the publication-date
    delta is computed. Without it, recovery of a failed PMID depends on an
    operator noticing before the PMID falls out of the delta window — which
    does not scale.

    Every `status='failed'` PROCESSING# row under `taxonomy_version` is
    partitioned:

    - `retry_count >= retry_sweep_quarantine_after` → **quarantined**: a
      QUARANTINE# row is written and the PROCESSING# row's status flips to
      'quarantined', so it leaves the failed-GSI partition permanently and
      no future sweep sees it. Returned in `quarantined_pmids` for the
      caller to alert on. Prevents infinite retry loops on content that
      both Sonnet and the OpenAI fallback filter.
    - `failed_at` older than `retry_sweep_min_age_days`, retry_count below
      the quarantine threshold → **retry candidate**.
    - `failed_at` within the min-age window → skipped this week, so the
      normal pipeline gets a chance to settle before a forced retry.

    Retry candidates are sorted oldest-failure-first and capped at
    `retry_sweep_max_pmids`; the overflow waits for the next sweep so one
    bad week cannot blow up scoring cost.

    Returns ``{"retry_pmids": [...], "quarantined_pmids": [...]}``.
    """
    max_pmids = int(thresholds["retry_sweep_max_pmids"])
    quarantine_after = int(thresholds["retry_sweep_quarantine_after"])
    min_age_days = int(thresholds["retry_sweep_min_age_days"])
    now_dt = now_dt or datetime.now(timezone.utc)

    failed_pmids = query_failed_pmids(client, table_name, taxonomy_version)
    if not failed_pmids:
        return {"retry_pmids": [], "quarantined_pmids": []}

    rows = get_processing_rows(client, table_name, failed_pmids)

    retry_candidates: list[tuple[str, str]] = []  # (sort_key, pmid)
    quarantined: list[str] = []

    for pmid in failed_pmids:
        row = rows.get(pmid, {})
        retry_count = int(row.get("retry_count", 0))
        failed_at = row.get("failed_at")

        if retry_count >= quarantine_after:
            quarantine_pmid(
                client, table_name, pmid,
                retry_count=retry_count,
                last_error=row.get("error", ""),
                taxonomy_version=taxonomy_version,
            )
            quarantined.append(pmid)
            continue

        # Age filter. A legacy failed row carries no `failed_at` (the field
        # predates mark_processing_failed) — it is certainly stale, so treat
        # it as eligible and sort it first ("" sorts before any timestamp).
        if not failed_at:
            retry_candidates.append(("", pmid))
            continue
        try:
            fa = datetime.fromisoformat(failed_at.replace("Z", "+00:00"))
        except ValueError:
            retry_candidates.append(("", pmid))
            continue
        if (now_dt - fa) >= timedelta(days=min_age_days):
            retry_candidates.append((failed_at, pmid))

    # Oldest failure first, then cap.
    retry_candidates.sort()
    retry_pmids = [pmid for _, pmid in retry_candidates[:max_pmids]]

    logger.info(
        "Retry sweep: %d failed row(s) — %d retry candidate(s) "
        "(%d after the %d cap), %d quarantined.",
        len(failed_pmids), len(retry_candidates),
        len(retry_pmids), max_pmids, len(quarantined),
    )
    return {"retry_pmids": retry_pmids, "quarantined_pmids": quarantined}


def _alert_quarantined(pmids: list[str], *, started_at: str) -> None:
    """Surface newly-quarantined PMIDs to the operator via one batched Teams
    alert (not one alert per PMID).

    Teams is the operator-review channel. `pipeline_enrichment.alerting` is
    best-effort and never raises, so a webhook outage cannot break the
    orchestrator.
    """
    teams_alerting.alert(
        "WARN",
        "Retry sweep quarantined PMIDs",
        f"{len(pmids)} PMID(s) exceeded the retry budget and were "
        "quarantined. They are excluded from all future retry sweeps and "
        "need manual review — e.g. both Sonnet and the OpenAI fallback "
        "content-filtered the publication, or its synopsis is malformed.",
        {
            "quarantined_pmids": ", ".join(pmids),
            "count": len(pmids),
            "started_at": started_at,
        },
    )


# ---------------------------------------------------------------------------
# Open Q5: prior-run-in-progress lock
# ---------------------------------------------------------------------------


def is_state_machine_running(
    sfn_client: Any,
    *,
    state_machine_arn: str,
    self_execution_arn: str | None = None,
) -> bool:
    """Return True iff any execution OTHER THAN this one is currently RUNNING.

    Open Q5 resolution: skip-with-warn. The orchestrator is invoked as
    the first Task of the very state machine it's checking, so it must
    exclude its own execution from the conflict check.
    """
    resp = sfn_client.list_executions(
        stateMachineArn=state_machine_arn,
        statusFilter="RUNNING",
    )
    for execution in resp.get("executions", []):
        if execution.get("executionArn") != self_execution_arn:
            return True
    return False


def write_skipped_hot_run_locked(
    table: Any,
    *,
    started_at: str,
    duration_ms: int,
    input_hash: str = "",
) -> dict:
    """Emit the `STAGE#hot_run#GLOBAL` skipped row for the lock-collision case.

    Carries `skip_reason: "prior_run_in_progress"` so the drift evaluator
    can decide whether collisions are frequent enough to revisit Open Q5.
    """
    return write_skipped(
        table,
        stage=HOT_RUN_STAGE,
        scope=HOT_RUN_SCOPE,
        input_hash=input_hash or "lock-collision",
        skip_reason=SKIP_REASON_LOCKED,
        started_at=started_at,
        completed_at=now_iso(),
        duration_ms=duration_ms,
    )


# ---------------------------------------------------------------------------
# Initial state-machine input shape
# ---------------------------------------------------------------------------


def build_state_machine_input(
    *,
    pmids: list[str],
    last_run_at: str | None,
    started_at: str,
    run_id: str,
    retry_pmids: list[str] | None = None,
) -> dict:
    """Produce the dict the state machine's first Task receives.

    Downstream Task states read this via JSONPath to compose their
    envelopes. Kept small: the corpus itself stays in DynamoDB / S3;
    we pass identifiers, not bytes.

    `retry_pmids` is the hot-path retry sweep's recovered-PMID list. It is
    kept distinct from the date-delta `pmids` so:

    - `delta.size` stays a pure date-delta count (the hot_run STAGE# row
      reads it), and `delta.retry_size` carries the sweep count separately;
    - the Score handler can pass exactly the retry list to
      `score_publications --pmids … --additive`;
    - `delta.all_pmids` (the union) is what TopTopic consumes, so a retry
      PMID that scores successfully also gets its top topic recomputed.

    `run_kind` tags the run for the hot_run STAGE# row — "delta" vs.
    "delta+retry" — so a retry-sweep run is distinguishable from a pure
    delta run when auditing the substrate.
    """
    retry_pmids = list(retry_pmids or [])
    all_pmids = sorted(set(pmids) | set(retry_pmids))
    return {
        "run_id": run_id,
        "started_at": started_at,
        "last_successful_hot_run_at": last_run_at,
        "run_kind": "delta+retry" if retry_pmids else "delta",
        "delta": {
            "pmids": pmids,
            "size": len(pmids),
            "retry_pmids": retry_pmids,
            "retry_size": len(retry_pmids),
            "all_pmids": all_pmids,
            # T7 placeholders. The state machine's `CheckAssignNeeded`
            # and `CheckRollupNeeded` Choice gates route around the
            # Assign / Rollup tasks via Pass states that inject stub
            # envelopes when these lists are empty. Real per-topic and
            # per-CWID dirty-set computation is a follow-up tracked on
            # issue #72; until then both lists stay empty and the
            # smoke / weekly run exercises Score → TopTopic only.
            "assign_topics": [],
            "dirty_cwids": [],
        },
        "trace": {
            "orchestrator_version": "0.2.0",
        },
    }


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------


def handler(event: dict, context: Any = None) -> dict:
    """Orchestrator Lambda handler.

    Expected event:
        {
          "state_machine_arn": "arn:aws:states:...:stateMachine:reciterai-hot",
          "execution_arn":     "arn:aws:states:...:execution:reciterai-hot:...",
          "run_id":            "2026-05-13T12:00:00Z-abcdef"
        }

    Returns either:
        {"status": "ready", "input": <state_machine_input>}  — proceed to Score
        {"status": "skipped", "skip_reason": "prior_run_in_progress"} — short-circuit
    """
    started_at = now_iso()
    state_machine_arn = event.get("state_machine_arn") or os.environ.get(
        "RECITERAI_HOT_STATE_MACHINE_ARN"
    )
    self_execution_arn = event.get("execution_arn")
    run_id = event.get("run_id") or started_at

    table = get_table(TABLE_NAME)

    # 1. Lock check.
    if state_machine_arn:
        import boto3

        sfn = boto3.client("stepfunctions")
        if is_state_machine_running(
            sfn,
            state_machine_arn=state_machine_arn,
            self_execution_arn=self_execution_arn,
        ):
            write_skipped_hot_run_locked(
                table, started_at=started_at, duration_ms=0
            )
            logger.warning(
                "Hot path skipped — prior execution still RUNNING. "
                f"state_machine_arn={state_machine_arn}"
            )
            # D-11: WARN alert on lock collision so repeated collisions
            # become visible. Single-collision noise is acceptable; the
            # severity table marks this WARN (Slack-only, no GH issue).
            alert.dispatch(
                "WARN",
                "Hot path skipped — prior execution still RUNNING",
                {
                    "source": "pipeline_hot.orchestrator",
                    "skip_reason": SKIP_REASON_LOCKED,
                    "state_machine_arn": state_machine_arn,
                    "started_at": started_at,
                },
            )
            return {
                "status": "skipped",
                "skip_reason": SKIP_REASON_LOCKED,
                "started_at": started_at,
            }

    # 2. Resolve last successful hot run + delta PMID set.
    last_run_at = resolve_last_successful_hot_run(table)
    from utils.sql_queries import get_db_connection  # local import: tests stub

    def _ddb_to_pmid_query(since_iso: str) -> list[str]:
        # Delta PMID resolution against ReciterDB. `analysis_summary_article`
        # carries `datePublicationAddedToEntrez` as the only "added since"
        # signal — there is no row-level last-modified column. The cold-path
        # ETL uses the same column for the daily-enrichment watermark
        # (utils.sql_queries.NEW_PUBLICATIONS_FOR_ENRICHMENT_SQL).
        # Article-year + canonical-type filters mirror the cold path so the
        # delta sees the same "scoreable" surface the cold corpus did.
        from sqlalchemy import text

        conn = get_db_connection()
        try:
            sql = text(
                "SELECT DISTINCT pmid FROM analysis_summary_article "
                "WHERE datePublicationAddedToEntrez >= :since "
                "  AND publicationTypeCanonical = 'Academic Article' "
                "  AND articleYear >= 2020 "
                "ORDER BY pmid DESC"
            )
            return [str(row[0]) for row in conn.execute(sql, {"since": since_iso})]
        finally:
            conn.close()

    pmids = resolve_delta_pmids(last_run_at, query_fn=_ddb_to_pmid_query)

    # 3. State-based retry sweep. Best-effort by design: the weekly delta
    # scoring is the primary job, the sweep is recovery. A sweep failure
    # (GSI query error, missing taxonomy file) must not block delta
    # scoring — on failure we log, WARN-alert, and proceed delta-only.
    retry_pmids: list[str] = []
    try:
        sweep = resolve_retry_sweep(
            get_dynamo_client(),
            table_name=TABLE_NAME,
            taxonomy_version=current_taxonomy_version(),
            thresholds=load_thresholds(),
        )
        retry_pmids = sweep["retry_pmids"]
        if sweep["quarantined_pmids"]:
            _alert_quarantined(sweep["quarantined_pmids"], started_at=started_at)
    except Exception as exc:  # noqa: BLE001 — sweep is non-critical recovery
        logger.exception("Retry sweep failed; proceeding with delta only.")
        alert.dispatch(
            "WARN",
            "Hot path retry sweep failed — delta scoring proceeded",
            {
                "source": "pipeline_hot.orchestrator",
                "error": str(exc),
                "started_at": started_at,
            },
        )

    return {
        "status": "ready",
        "input": build_state_machine_input(
            pmids=pmids,
            last_run_at=last_run_at,
            started_at=started_at,
            run_id=run_id,
            retry_pmids=retry_pmids,
        ),
    }

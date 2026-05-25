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
    fetch_scored_provenance,
    fetch_synopsis_records,
    get_dynamo_client,
    get_processing_rows,
    get_table,
    invalidate_stale_score,
    quarantine_pmid,
    query_failed_pmids,
    query_pmids_by_status,
    scan_impact_pmids_with_synopsis,
    scan_invalid_pmids,
    TABLE_NAME,
)
from utils.env_check import load_thresholds
from utils.stage_records import (
    STATUS_COMPLETE,
    build_skipped_record,
    compute_input_hash,
    write_skipped,
)
from pipeline_common import alert
from pipeline_enrichment import alerting as teams_alerting
from utils.iso_clock import now_iso

logger = logging.getLogger(__name__)

HOT_RUN_STAGE = "hot_run"
HOT_RUN_SCOPE = "GLOBAL"
SKIP_REASON_LOCKED = "prior_run_in_progress"

# #150 1a — operator-override safety valve. When the SFn execution input
# carries `initiated_by` on this allowlist AND a non-empty `pmids` list, the
# orchestrator bypasses the date-delta + sweeps and scores exactly that set
# with `--force` (cache-bypassing). Scheduled runs (`initiated_by:scheduled`)
# never match, so the weekly cron is unaffected.
OVERRIDE_INITIATORS = frozenset({"manual_catchup", "operator_rerun"})


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
# #150 1b: eligibility sweep (enriched-but-unscored recovery)
# ---------------------------------------------------------------------------


def resolve_eligibility_sweep(
    client: Any,
    *,
    table_name: str,
    taxonomy_version: str,
    thresholds: dict,
    scoreable_filter_fn: Any = None,
) -> list[str]:
    """Recover enriched-but-unscored PMIDs the date-delta misses (#150 1b).

    Eligible = a PMID with an IMPACT# row carrying a synopsis (enriched) but
    **no scored PROCESSING# row** under the active taxonomy, excluding `failed`
    (the retry sweep owns those) and `quarantined` (deliberately parked). These
    are PMIDs the daily enrichment synopsized *after* their Entrez-arrival date
    fell out of the weekly date-delta window — invisible to both the date delta
    and the cache-respecting `--additive` path, so without this sweep they never
    get a TOPIC# row (the #150 gap).

    Relies on the Part-1 invariant (`complete` ⟺ TOPIC# persisted): a scored
    PROCESSING# row now reliably means a topic verdict was reached, so excluding
    them does not skip genuinely-unscored work.

    #157: `enriched` (IMPACT# carries a synopsis) is a *looser* population than
    the scorer's corpus — the synopsis pipeline also synopsizes Reviews,
    pre-2020, and not-in-corpus PMIDs the scorer never scores. When
    `scoreable_filter_fn` is supplied (production wires
    `utils.sql_queries.filter_scoreable_pmids`), candidates are intersected with
    the scorer's corpus BEFORE the sort/cap, so the sweep never hands over a
    work set the scorer drops to 0 and the cap budget is spent on real work.
    Without it (the unit-test default) no corpus filter is applied.

    Cost guard (D-150): capped at `eligibility_sweep_max_pmids` and gated by
    `eligibility_sweep_enabled`. A large enrichment backfill could otherwise
    hand the scorer an unbounded work set; the cap directly bounds per-run
    Bedrock spend and drains a backlog over successive weekly runs (the
    overflow waits). (The scorer's onboarding cost guard does NOT apply here —
    it fires only for plain `--pmids` runs, not the `--additive` path this
    set rides; the cap is the sole bound on the eligibility work set.)
    """
    if not thresholds.get("eligibility_sweep_enabled", True):
        logger.info("Eligibility sweep disabled (eligibility_sweep_enabled=false).")
        return []
    max_pmids = int(thresholds.get("eligibility_sweep_max_pmids", 200))

    enriched = set(scan_impact_pmids_with_synopsis(client, table_name))
    scored = set(query_pmids_by_status(client, table_name, taxonomy_version, "complete"))
    failed = set(query_pmids_by_status(client, table_name, taxonomy_version, "failed"))
    quarantined = set(
        query_pmids_by_status(client, table_name, taxonomy_version, "quarantined")
    )
    # #150 item 3: PMIDs ReciterDB flagged invalid. The enrichment cron may have
    # synopsized them before the upstream DELETE, so their IMPACT# rows show up
    # in `enriched`; cull them here so the sweep never hands the scorer a
    # known-invalid PMID (the SQL DELETE does not clean these DDB rows).
    invalid = set(scan_invalid_pmids(client, table_name))

    candidates = enriched - scored - failed - quarantined - invalid
    # #157: restrict to the scorer's corpus BEFORE the sort/cap. Filtering here
    # (not post-cap) keeps the per-run cap spending its budget on PMIDs the
    # scorer will actually accept, instead of an un-scoreable prefix that gets
    # reselected every run and starves the scoreable tail.
    n_uncorpus = 0
    if scoreable_filter_fn is not None and candidates:
        scoreable = set(scoreable_filter_fn(sorted(candidates)))
        n_uncorpus = len(candidates - scoreable)
        candidates &= scoreable
    eligible = sorted(candidates)
    capped = len(eligible) > max_pmids
    logger.info(
        "Eligibility sweep: %d enriched, %d invalid-culled, %d non-corpus-culled, "
        "%d eligible%s.",
        len(enriched), len(enriched & invalid), n_uncorpus, len(eligible),
        f" — capping to {max_pmids} (overflow waits for the next run)"
        if capped else "",
    )
    if capped:
        eligible = eligible[:max_pmids]
    return eligible


def resolve_drift_sweep(
    client: Any,
    *,
    table_name: str,
    taxonomy_version: str,
    thresholds: dict,
) -> list[str]:
    """Re-score PMIDs whose synopsis was regenerated after they were scored
    (#150 item 2).

    Drifted = a ``complete`` PROCESSING# row whose stamped ``scored_enriched_at``
    lags the current ``IMPACT#.enriched_at`` (the synopsis was re-enriched after
    scoring — mangled-abstract fix, content-filter survivor), or whose
    ``scored_synopsis_model`` differs from the current one (model upgrade).
    Detection relies on the Part-A provenance stamp; an un-stamped score (no
    ``scored_enriched_at``) is treated as baseline, not drifted — so this never
    fires until stamps exist (forward scores + the one-time backfill).

    Drifted PMIDs are ``complete``, so the cache-respecting ``--additive`` path
    would skip them. This sweep INVALIDATES their checkpoint (``complete`` ->
    ``stale``) so the scorer re-runs them, and returns them to be folded into
    the additive set. Capped at ``drift_sweep_max_pmids`` (per-run Bedrock spend
    bound; overflow waits for the next run), gated by ``drift_sweep_enabled``.

    Order matters: the handler runs this AFTER the eligibility sweep, which
    reads the ``complete`` snapshot — so a PMID demoted here is not also counted
    as a (genuinely-unscored) eligibility PMID in the same run.
    """
    if not thresholds.get("drift_sweep_enabled", True):
        logger.info("Drift sweep disabled (drift_sweep_enabled=false).")
        return []
    max_pmids = int(thresholds.get("drift_sweep_max_pmids", 200))

    scored = query_pmids_by_status(client, table_name, taxonomy_version, "complete")
    if not scored:
        return []
    prov = fetch_scored_provenance(client, table_name, scored)
    impact = fetch_synopsis_records(client, scored)

    drifted: list[str] = []
    for pmid in scored:
        p = prov.get(pmid)
        i = impact.get(pmid)
        if not p or not i:
            continue
        scored_ea = p.get("scored_enriched_at") or ""
        if not scored_ea:
            continue  # un-stamped → baseline (not drifted)
        # ISO8601 Z-suffixed timestamps compare lexicographically.
        re_enriched = i.get("enriched_at", "") > scored_ea
        scored_sm = p.get("scored_synopsis_model") or ""
        cur_sm = i.get("synopsis_model") or ""
        model_changed = bool(scored_sm and cur_sm and cur_sm != scored_sm)
        if re_enriched or model_changed:
            drifted.append(pmid)

    drifted = sorted(drifted)
    if len(drifted) > max_pmids:
        logger.info(
            "Drift sweep: %d drifted — capping to %d (overflow waits).",
            len(drifted), max_pmids,
        )
        drifted = drifted[:max_pmids]
    else:
        logger.info("Drift sweep: %d drifted.", len(drifted))

    # Invalidate each drifted checkpoint so the --additive re-score reaches it.
    demoted = [pmid for pmid in drifted if invalidate_stale_score(client, table_name, pmid)]
    if len(demoted) != len(drifted):
        logger.info(
            "Drift sweep: demoted %d/%d (the rest moved off 'complete' concurrently).",
            len(demoted), len(drifted),
        )
    return demoted


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
    eligibility_pmids: list[str] | None = None,
    drift_pmids: list[str] | None = None,
    override_pmids: list[str] | None = None,
    dirty_cwids: list[str] | None = None,
) -> dict:
    """Produce the dict the state machine's first Task receives.

    Downstream Task states read this via JSONPath to compose their
    envelopes. Kept small: the corpus itself stays in DynamoDB / S3;
    we pass identifiers, not bytes.

    Three sources feed the work set, surfaced as distinct `delta.*` lists so
    the hot_run STAGE# row and the Score handler can tell them apart:

    - `pmids` — the date-delta (publications added to Entrez since the last
      successful run). `delta.size` stays a pure date-delta count.
    - `retry_pmids` — the retry sweep's recovered failures (#119/D-11).
    - `eligibility_pmids` — enriched-but-unscored PMIDs the date-delta misses
      (#150 1b): IMPACT# w/ synopsis and no scored PROCESSING# row.
    - `override_pmids` — an explicit operator work set (#150 1a), present only
      when the SFn input carries `initiated_by ∈ OVERRIDE_INITIATORS` + `pmids`.

    The Score handler reads two derived lists:

    - `additive_pmids` = retry ∪ eligibility — passed as `--pmids … --additive`
      (cache-respecting), unioned onto the `--delta-since` date delta.
    - `force_pmids` = override — passed as `--pmids … --force` (cache-bypassing,
      no `--delta-since`), so cache-poisoned/`complete` PMIDs are re-scored. An
      override run REPLACES the date-delta + sweeps entirely.

    `delta.all_pmids` (everything that will be scored) is what DeriveDirtyTopics
    and TopTopic consume; `dirty_cwids` (computed by the caller from all_pmids)
    is the RollupFanOut ItemsPath, so recovered/override PMIDs flow through the
    full Assign → TopTopic → Rollup chain — not just Score (the gap that left
    the 05-20 manual catchup's rollups stale).

    `run_kind` tags the run for the hot_run STAGE# row.
    """
    retry_pmids = list(retry_pmids or [])
    eligibility_pmids = list(eligibility_pmids or [])
    drift_pmids = list(drift_pmids or [])
    override_pmids = list(override_pmids or [])
    dirty_cwids = list(dirty_cwids or [])

    if override_pmids:
        # 1a operator override: explicit set, bypass date-delta + sweeps, --force.
        all_pmids = sorted(set(override_pmids))
        date_pmids: list[str] = []
        additive_pmids: list[str] = []
        force_pmids = all_pmids
        run_kind = "override"
    else:
        date_pmids = pmids
        # Drift PMIDs (#150 item 2) ride --additive too: their checkpoint was
        # demoted complete->stale by resolve_drift_sweep, so --additive re-scores
        # them (it skips only 'complete'). dedup absorbs any overlap.
        additive_pmids = sorted(
            set(retry_pmids) | set(eligibility_pmids) | set(drift_pmids)
        )
        force_pmids = []
        all_pmids = sorted(set(date_pmids) | set(additive_pmids))
        parts = ["delta"]
        if retry_pmids:
            parts.append("retry")
        if eligibility_pmids:
            parts.append("eligibility")
        if drift_pmids:
            parts.append("drift")
        run_kind = "+".join(parts)

    return {
        "run_id": run_id,
        "started_at": started_at,
        "last_successful_hot_run_at": last_run_at,
        "run_kind": run_kind,
        "delta": {
            "pmids": date_pmids,
            "size": len(date_pmids),
            "retry_pmids": retry_pmids,
            "retry_size": len(retry_pmids),
            "eligibility_pmids": eligibility_pmids,
            "eligibility_size": len(eligibility_pmids),
            "drift_pmids": drift_pmids,
            "drift_size": len(drift_pmids),
            # Score handler inputs: additive (cache-respecting) vs force
            # (cache-bypassing). Mutually exclusive by construction — an
            # override run clears additive; a normal run clears force.
            "additive_pmids": additive_pmids,
            "force_pmids": force_pmids,
            "force_size": len(force_pmids),
            "all_pmids": all_pmids,
            # `dirty_cwids` — CWIDs whose first/last-author work intersects
            # `all_pmids` (#119). `CheckRollupNeeded` routes an empty list
            # past the Rollup fan-out; a non-empty one is the `RollupFanOut`
            # Map's ItemsPath. `assign_topics` is NOT here: it needs the
            # TOPIC# rows the Score stage writes; DeriveDirtyTopics computes
            # it post-Score (the ASL reads `$.assign.assign_topics`).
            "dirty_cwids": dirty_cwids,
            "rollup_input_hash": compute_input_hash(
                "rollup_fanout", {"dirty_cwids": sorted(dirty_cwids)}
            ),
        },
        "trace": {
            "orchestrator_version": "0.4.0",
        },
    }


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------


def handler(event: dict, context: Any = None) -> dict:
    """Orchestrator Lambda handler.

    Expected event (the ASL Orchestrate Task supplies these — context fields
    plus `sfn_input`, the verbatim Step Functions execution input):
        {
          "state_machine_arn": "arn:aws:states:...:stateMachine:reciterai-hot",
          "execution_arn":     "arn:aws:states:...:execution:reciterai-hot:...",
          "run_id":            "2026-05-13T12:00:00Z-abcdef",
          "sfn_input":         {"initiated_by": "scheduled" | "manual_catchup"
                                 | "operator_rerun", "pmids": [...]?}
        }

    #150 1a — operator override: when `sfn_input.initiated_by` is on
    OVERRIDE_INITIATORS and `sfn_input.pmids` is non-empty, the date-delta +
    retry/eligibility sweeps are bypassed and exactly that PMID set is scored
    with `--force` (re-scoring cache-poisoned/`complete` PMIDs the normal,
    cache-respecting path would skip).

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

    # #150 1a — operator override from the SFn execution input (the ASL passes
    # it through as `sfn_input`). Scheduled cron runs carry
    # `initiated_by:scheduled` and no `pmids`, so they never match.
    sfn_input = event.get("sfn_input") or {}
    initiated_by = sfn_input.get("initiated_by")
    override_pmids = [str(p) for p in (sfn_input.get("pmids") or [])]
    is_override = initiated_by in OVERRIDE_INITIATORS and bool(override_pmids)

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

    # Both branches need last_run_at (the hot_run STAGE# row records it) and
    # get_cwids_for_pmids (the Rollup fan-out's dirty set).
    last_run_at = resolve_last_successful_hot_run(table)
    from utils.sql_queries import (  # local import: tests stub
        filter_scoreable_pmids,
        get_cwids_for_pmids,
        get_db_connection,
    )

    # 2a. #150 1a — operator override. Score exactly `override_pmids`, bypassing
    # the date-delta + both sweeps, with --force (see build_state_machine_input).
    # Still routes through the full Assign → TopTopic → Rollup chain via
    # all_pmids/dirty_cwids, so an override catchup does not leave rollups stale.
    if is_override:
        logger.warning(
            "Hot path OVERRIDE: initiated_by=%s, %d explicit PMID(s) — bypassing "
            "date-delta + retry/eligibility sweeps, scoring with --force.",
            initiated_by, len(override_pmids),
        )
        dirty_cwids = get_cwids_for_pmids(sorted(set(override_pmids)))
        return {
            "status": "ready",
            "input": build_state_machine_input(
                pmids=[],
                last_run_at=last_run_at,
                started_at=started_at,
                run_id=run_id,
                override_pmids=override_pmids,
                dirty_cwids=dirty_cwids,
            ),
        }

    # 2b. Normal weekly run: date-delta PMID set.
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

    # 3b. #150 1b — eligibility sweep: enriched-but-unscored PMIDs the
    # date-delta misses (IMPACT# w/ synopsis and no scored PROCESSING# row).
    # Best-effort like the retry sweep — a DDB scan failure must not block the
    # weekly delta scoring. Capped in resolve_eligibility_sweep (cost guard).
    eligibility_pmids: list[str] = []
    try:
        eligibility_pmids = resolve_eligibility_sweep(
            get_dynamo_client(),
            table_name=TABLE_NAME,
            taxonomy_version=current_taxonomy_version(),
            thresholds=load_thresholds(),
            # #157: drop synopsized-but-unscoreable PMIDs (Reviews, pre-2020,
            # not-in-corpus) so the sweep never reselects a starving prefix.
            scoreable_filter_fn=filter_scoreable_pmids,
        )
    except Exception as exc:  # noqa: BLE001 — eligibility recovery is non-critical
        logger.exception("Eligibility sweep failed; proceeding without it.")
        alert.dispatch(
            "WARN",
            "Hot path eligibility sweep failed — delta+retry scoring proceeded",
            {
                "source": "pipeline_hot.orchestrator",
                "error": str(exc),
                "started_at": started_at,
            },
        )

    # 3c. #150 item 2 — drift sweep: scores whose synopsis was regenerated after
    # scoring. Best-effort like the other sweeps. Runs AFTER the eligibility
    # sweep (which read the 'complete' snapshot), and demotes drifted checkpoints
    # complete->stale so the cache-respecting --additive re-score reaches them.
    drift_pmids: list[str] = []
    try:
        drift_pmids = resolve_drift_sweep(
            get_dynamo_client(),
            table_name=TABLE_NAME,
            taxonomy_version=current_taxonomy_version(),
            thresholds=load_thresholds(),
        )
    except Exception as exc:  # noqa: BLE001 — drift recovery is non-critical
        logger.exception("Drift sweep failed; proceeding without it.")
        alert.dispatch(
            "WARN",
            "Hot path drift sweep failed — delta+retry+eligibility scoring proceeded",
            {
                "source": "pipeline_hot.orchestrator",
                "error": str(exc),
                "started_at": started_at,
            },
        )

    # 4. Dirty-CWID set for the Rollup fan-out (#119). Unlike the sweeps, this
    # is a core stage input, not best-effort recovery — a failure here
    # propagates and the state machine's Orchestrate Catch writes the failed
    # hot_run row.
    all_pmids = sorted(
        set(pmids) | set(retry_pmids) | set(eligibility_pmids) | set(drift_pmids)
    )
    dirty_cwids = get_cwids_for_pmids(all_pmids)

    return {
        "status": "ready",
        "input": build_state_machine_input(
            pmids=pmids,
            last_run_at=last_run_at,
            started_at=started_at,
            run_id=run_id,
            retry_pmids=retry_pmids,
            eligibility_pmids=eligibility_pmids,
            drift_pmids=drift_pmids,
            dirty_cwids=dirty_cwids,
        ),
    }

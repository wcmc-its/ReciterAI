"""Hot-path stage-skip evaluation for the alert dispatcher (#121).

A run that skipped Assign or Rollup despite a non-empty delta is flagged
by the hot state machine's `CheckStageSkipAnomaly` Choice, which invokes
the alert dispatcher with `source: hot_path.stage_skip`. This module is
the dispatcher's evaluation step for that source: it reads the recent
`STAGE#hot_run#GLOBAL` history and decides the severity —

- WARN  — this run skipped a stage. A single skip can be legitimate (a
          small delta scoring into no draft-covered topic, or carrying
          no WCM first/last author), so the per-run signal is a
          dismissible notification.
- ERROR — the skip is a *streak*: the last K work-present runs have all
          skipped the same stage. A sustained pattern is a structural
          regression in the hot orchestrator / state machine, not delta
          noise — it must page someone.

The per-run WARN catches the first bad run; the streak ERROR escalates
once it is clearly systemic. Runs with no work (`delta_size +
retry_size == 0`) skip a stage legitimately and are transparent to the
streak — neither counted toward it nor breaking it.

No new Lambda (POLICIES #2): this runs inside the existing
`reciterai-alert-dispatcher`, whose execution role already grants
`dynamodb:Query` on the `reciterai` table.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_TABLE = "reciterai"
_HOT_RUN_PK = "STAGE#hot_run#GLOBAL"
_SKIP_SENTINEL_PREFIX = "skipped:"
_STAGES = ("assign", "rollup")

# A stage-skip ERROR fires when this many consecutive work-present hot
# runs have all skipped the same stage. Three weekly runs ~= three
# weeks; the WARN tier already fired on run 1, so the escalation can be
# unhurried. A module constant, not a config/thresholds.json key: that
# file is bundled into the zip, so tuning K needs a rebuild either way —
# a constant keeps the lean dispatcher zip free of the thresholds chain.
_STREAK_K = 3

# How far back to read hot_run history — a query bound, not a streak
# bound. 60 weekly rows is over a year; ample to resolve a K-run streak
# even with work-empty runs interleaved.
_HISTORY_LIMIT = 60


def _int(value: Any) -> int:
    """Coerce a DynamoDB numeric (Decimal, from `table.query`) to int."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _work_present(run: dict) -> bool:
    return _int(run.get("delta_size")) + _int(run.get("retry_size")) > 0


def _skipped(run: dict, stage: str) -> bool:
    h = run.get(f"{stage}_input_hash") or ""
    return isinstance(h, str) and h.startswith(_SKIP_SENTINEL_PREFIX)


# ---------------------------------------------------------------------------
# Pure classification
# ---------------------------------------------------------------------------


def classify_stage_skip(
    *, current: dict, prior_runs: list[dict], streak_k: int = _STREAK_K
) -> dict:
    """Classify a stage-skip event. Pure — unit-tested with injected rows.

    `current` is the run that triggered the alert; `prior_runs` is the
    `STAGE#hot_run#GLOBAL` `complete` history, newest-first, excluding
    `current`. For each stage `current` skipped, the streak is `current`
    plus the run of consecutive *work-present* prior runs that also
    skipped that stage, stopping at the first work-present run that ran
    it. Work-empty runs are transparent (skipped over).

    Returns ``{"severity", "skipped_stages", "assign_skip_streak",
    "rollup_skip_streak", "streak_k"}``. `severity` is ERROR if any
    skipped stage's streak reaches `streak_k`, else WARN.
    """
    skipped_stages = [s for s in _STAGES if _skipped(current, s)]
    streaks: dict[str, int] = {"assign": 0, "rollup": 0}

    for stage in skipped_stages:
        streak = 1  # the current run
        for run in prior_runs:
            if not _work_present(run):
                continue  # a legitimate empty-delta skip — transparent
            if _skipped(run, stage):
                streak += 1
            else:
                break  # a work-present run ran the stage — streak ends
        streaks[stage] = streak

    severity = "ERROR" if max(streaks.values()) >= streak_k else "WARN"
    return {
        "severity": severity,
        "skipped_stages": skipped_stages,
        "assign_skip_streak": streaks["assign"],
        "rollup_skip_streak": streaks["rollup"],
        "streak_k": streak_k,
    }


# ---------------------------------------------------------------------------
# DynamoDB history read
# ---------------------------------------------------------------------------


def _recent_hot_runs(*, exclude_run_id: str | None) -> list[dict]:
    """Recent `STAGE#hot_run#GLOBAL` `complete` rows, newest-first.

    `RUN#FAILED#...` SKs sort above dated `RUN#{iso}` SKs, so the
    `begins_with(SK, "RUN#2")` key condition keeps the query to dated
    rows; `status == "complete"` is then enforced in code (a `skipped`
    lock row can also be dated). The triggering run is excluded by
    `run_id` — it may or may not be query-visible yet (its putItem
    raced this read), and either way it is the `current` argument to
    `classify_stage_skip`, not a prior run.
    """
    import boto3
    from boto3.dynamodb.conditions import Key

    table = boto3.resource("dynamodb").Table(_TABLE)
    resp = table.query(
        KeyConditionExpression=(
            Key("PK").eq(_HOT_RUN_PK) & Key("SK").begins_with("RUN#2")
        ),
        ScanIndexForward=False,
        Limit=_HISTORY_LIMIT,
    )
    rows: list[dict] = []
    for row in resp.get("Items", []):
        if row.get("status") != "complete":
            continue
        if exclude_run_id and row.get("run_id") == exclude_run_id:
            continue
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Dispatcher entry — turn a stage-skip event into a standard alert event
# ---------------------------------------------------------------------------


def resolve(event: dict) -> dict:
    """Turn a `hot_path.stage_skip` dispatcher event into a standard alert.

    The hot state machine invokes the dispatcher with the triggering run
    under `event["hot_run"]`. This reads the run history, classifies
    WARN vs ERROR, and returns an event the dispatcher sends verbatim
    (`severity` / `title` / `message` / `context`).

    Best-effort: a DynamoDB read failure falls back to WARN — the ASL
    gate already established this run really did skip a stage, so WARN
    is the safe non-escalated floor — and never raises.
    """
    hot_run = dict(event.get("hot_run") or {})
    run_id = hot_run.get("run_id") or "<unknown>"
    delta_size = _int(hot_run.get("delta_size"))
    retry_size = _int(hot_run.get("retry_size"))

    try:
        prior = _recent_hot_runs(exclude_run_id=hot_run.get("run_id"))
        result = classify_stage_skip(current=hot_run, prior_runs=prior)
    except Exception:  # noqa: BLE001 — best-effort; never fail the dispatcher
        logger.exception("[stage-skip] history read failed; falling back to WARN")
        result = {
            "severity": "WARN",
            "skipped_stages": [s for s in _STAGES if _skipped(hot_run, s)],
            "assign_skip_streak": 0,
            "rollup_skip_streak": 0,
            "streak_k": _STREAK_K,
        }

    stages_str = " and ".join(result["skipped_stages"] or ["a stage"])
    severity = result["severity"]

    if severity == "ERROR":
        streak = max(result["assign_skip_streak"], result["rollup_skip_streak"])
        title = "Hot path stage-skip streak"
        message = (
            f"Hot run {run_id} skipped {stages_str} — {streak} consecutive "
            "work-present hot runs have now skipped. The hot path is silently "
            "degrading; investigate the orchestrator / state machine (a code "
            "regression — a cold run does not fix it)."
        )
    else:
        title = "Hot path stage skip"
        message = (
            f"Hot run {run_id} completed but skipped {stages_str} despite a "
            f"non-empty delta (delta_size={delta_size}, retry_size={retry_size})."
        )

    return {
        "severity": severity,
        "source": "hot_path.stage_skip",
        "title": title,
        "message": message,
        "execution_arn": event.get("execution_arn"),
        "context": {
            "run_id": run_id,
            "delta_size": delta_size,
            "retry_size": retry_size,
            "assign_input_hash": hot_run.get("assign_input_hash"),
            "rollup_input_hash": hot_run.get("rollup_input_hash"),
            "assign_skip_streak": result["assign_skip_streak"],
            "rollup_skip_streak": result["rollup_skip_streak"],
            "streak_threshold": result["streak_k"],
        },
    }

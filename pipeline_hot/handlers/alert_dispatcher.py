"""Hot-path AlertDispatcher Lambda (#121).

Invoked by the state machine: `NotifyError` (ERROR, on hot-path failure)
and `NotifyStageSkipAnomaly` (on a silent stage skip). Sends a Teams
Adaptive Card via `pipeline_enrichment.alerting.alert` — a Workflows
webhook over urllib, with no CLI dependency, so it runs cleanly in the
Lambda runtime.

For the `hot_path.stage_skip` source the event carries the triggering
run rather than a finished alert; `stage_skip.resolve` reads the recent
`STAGE#hot_run#GLOBAL` history and classifies it WARN (this run skipped
a stage) or ERROR (a streak of work-present runs all skipped) before
this handler dispatches it.

Best-effort: `alerting.alert` and `stage_skip.resolve` log and recover
rather than raising, and this handler additionally guards the dispatch —
an alerting fault must never fail the state machine's terminal state.

Replaces the first-deploy stub (its real-dispatch follow-up was tracked
on the now-closed #72; #121 carries it).
"""

from __future__ import annotations

import logging
from typing import Any

from pipeline_enrichment import alerting
from pipeline_hot.handlers import stage_skip

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_STAGE_SKIP_SOURCE = "hot_path.stage_skip"


def handler(event: dict, context: Any = None) -> dict:
    """Dispatch a hot-path alert to Teams.

    Generic event (e.g. from `NotifyError`):
        {severity, source, message, execution_arn, title?, context?, mention?}

    For `source == "hot_path.stage_skip"` the event instead carries
    `hot_run` (the triggering run's fields); `stage_skip.resolve` turns
    it into the generic shape above, computing the severity from the
    recent run history.

    `severity` defaults to ERROR — a malformed event should escalate,
    not silently downgrade. `title` defaults to `source`.

    The state machine ignores the return value (`ResultPath: null`); the
    envelope is returned for `aws lambda invoke` smoke tests.
    """
    event = event or {}
    if event.get("source") == _STAGE_SKIP_SOURCE:
        event = stage_skip.resolve(event)

    severity = (event.get("severity") or "ERROR").upper()
    source = event.get("source") or "hot_path"
    message = event.get("message") or ""
    title = event.get("title") or source
    mention = bool(event.get("mention", True))

    # The card's key/value detail block: the caller's context plus the
    # routing fields, so an operator sees source + execution on the card.
    ctx: dict[str, Any] = dict(event.get("context") or {})
    ctx.setdefault("source", source)
    if event.get("execution_arn"):
        ctx.setdefault("execution_arn", event["execution_arn"])

    logger.info(
        "[alert-dispatcher] severity=%s source=%s message=%s",
        severity, source, message,
    )

    delivered = False
    try:
        delivered = alerting.alert(severity, title, message, ctx, mention=mention)
    except Exception:  # noqa: BLE001 — alerting is best-effort, must not raise
        logger.exception("[alert-dispatcher] Teams dispatch raised unexpectedly")

    return {"status": "dispatched", "delivered": bool(delivered), "transport": "teams"}

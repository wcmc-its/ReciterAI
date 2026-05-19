"""Hot-path AlertDispatcher Lambda (#121).

Invoked by the state machine: `NotifyError` (ERROR, on hot-path failure)
and `NotifyStageSkipAnomaly` (WARN, on a silent stage skip). Sends a
Teams Adaptive Card via `pipeline_enrichment.alerting.alert` — a
Workflows webhook over urllib, with no CLI dependency, so it runs
cleanly in the Lambda runtime.

Best-effort: `alerting.alert` logs and returns False (never raises) when
`RECITERAI_TEAMS_WEBHOOK_URL` is unset or the POST fails, and this
handler additionally guards the call — an alerting fault must never
fail the state machine's terminal state.

Replaces the first-deploy stub. The stub's deferred "real dispatch"
follow-up was tracked on the now-closed #72; #121 carries it.
"""

from __future__ import annotations

import logging
from typing import Any

from pipeline_enrichment import alerting

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event: dict, context: Any = None) -> dict:
    """Dispatch a hot-path alert to Teams.

    Expected event (from `pipeline_hot/state_machine.asl.json`):
        {
          "severity":      "ERROR" | "WARN",
          "source":        "hot_path" | "hot_path.stage_skip",
          "message":       "<headline body>",
          "execution_arn": "<Step Functions execution ARN>",
          "title":         "<optional card headline>",
          "context":       {<optional key/value detail block>},
          "mention":       <optional bool, default True>
        }

    `severity` defaults to ERROR — a malformed event should escalate,
    not silently downgrade. `title` defaults to `source`.

    The state machine ignores the return value (`ResultPath: null`); the
    envelope is returned for `aws lambda invoke` smoke tests.
    """
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

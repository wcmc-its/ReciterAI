"""Hot-path AlertDispatcher Lambda — stub for first deploy.

Invoked by the state machine's `NotifyError` state on hot-path failure
(see `pipeline_hot/state_machine.asl.json`). This stub logs the event
so the failure signal still lands in CloudWatch Logs, then returns a
success envelope so the state machine's terminal state succeeds.

Real Slack + GitHub dispatch is deferred to a follow-up: the existing
`pipeline_common.alert.dispatch()` shells out to the `gh` CLI, which is
not available in the Lambda runtime. That refactor (gh REST via urllib
+ Slack webhook from Secrets Manager) lands in a separate PR before
this stub is replaced. Tracking on issue #72 (gap 2).
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event: dict, context: Any = None) -> dict:
    """Log the alert event and return a success envelope.

    Expected event (from `state_machine.asl.json` `NotifyError`):
        {
          "severity":      "ERROR",
          "source":        "hot_path",
          "message":       "<state machine error.Cause>",
          "execution_arn": "<Step Functions execution ARN>"
        }

    The state machine ignores the return value (`ResultPath: null`), but
    we still return a structured envelope so the function is
    introspectable from `aws lambda invoke` during smoke tests.
    """
    logger.warning(
        "[alert-dispatcher-stub] severity=%s source=%s message=%s execution_arn=%s",
        event.get("severity"),
        event.get("source"),
        event.get("message"),
        event.get("execution_arn"),
    )
    logger.info("[alert-dispatcher-stub] full event=%s", json.dumps(event, default=str))
    return {"status": "logged", "delivered": False, "transport": "stub"}

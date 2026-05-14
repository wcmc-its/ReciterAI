"""Teams alert dispatcher for the daily enrichment job (#37 step 2).

Sends Adaptive Card messages to a Teams channel via a Workflows-generated
webhook (the modern replacement for the deprecated Office 365 Incoming
Webhook connector). Best-effort: failures are logged, never raised —
alerts are observability, not flow control.

Config (env vars):
- ``RECITERAI_TEAMS_WEBHOOK_URL``  — destination webhook. Absent → alerts
  are skipped (and logged so local runs don't blow up).
- ``RECITERAI_ALERT_MENTION_UPN``  — UPN to @mention on actionable alerts.
- ``RECITERAI_ALERT_MENTION_NAME`` — display name; first token is used as
  the at-tag (e.g. ``Paul Albert`` → ``<at>Paul</at>``).

Both mention env vars must be set together; either alone disables the
mention.

Why a new module, not ``pipeline_common/alert.py``: that one is wired to
Slack for the Phase 10 drift evaluator and uses a different payload
shape. The daily-job alerts go to Teams, which needs the Adaptive Card
``type: message`` envelope + ``msteams.entities`` mentions. A future
refactor can merge them when other pipelines also migrate to Teams.

Payload pattern verified against the live ReCiter team's webhook on
2026-05-14: HTTP 202 on accept; @-tag renders as a real clickable
mention with a Teams notification.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Optional

logger = logging.getLogger(__name__)

WEBHOOK_ENV = "RECITERAI_TEAMS_WEBHOOK_URL"
MENTION_UPN_ENV = "RECITERAI_ALERT_MENTION_UPN"
MENTION_NAME_ENV = "RECITERAI_ALERT_MENTION_NAME"
POST_TIMEOUT_SECONDS = 5

VALID_SEVERITIES = ("WARN", "ERROR")
_TITLE_PREFIX = {
    "WARN": "[WARN] ReciterAI",
    "ERROR": "[ERROR] ReciterAI",
}


def _resolve_mention() -> Optional[dict]:
    """Return mention metadata if both env vars are set, else None.

    Returns a dict shaped {"tag": "<at>Paul</at>", "entity": {...}} where
    `entity` is the object that goes in `msteams.entities[]`.
    """
    upn = os.environ.get(MENTION_UPN_ENV, "").strip()
    name = os.environ.get(MENTION_NAME_ENV, "").strip()
    if not upn or not name:
        return None
    first = name.split()[0]
    tag = f"<at>{first}</at>"
    return {
        "tag": tag,
        "entity": {
            "type": "mention",
            "text": tag,
            "mentioned": {"id": upn, "name": name},
        },
    }


def build_card(
    severity: str,
    title: str,
    message: str,
    context: Optional[dict[str, Any]] = None,
    *,
    mention: bool = True,
) -> dict:
    """Construct the full webhook POST body (envelope + Adaptive Card).

    Args:
        severity: ``WARN`` or ``ERROR``.
        title: short headline shown bold at the top of the card.
        message: body paragraph; the @-tag is prepended if mention=True
            and the mention env vars are set.
        context: optional dict rendered as a key/value block under the
            message body (for run_id, delta size, estimated cost, etc.).
        mention: set False on alerts that don't require operator action.

    Returns:
        Dict suitable for ``json.dumps`` + POST to the webhook URL.
    """
    if severity not in VALID_SEVERITIES:
        raise ValueError(f"invalid severity {severity!r}; expected {VALID_SEVERITIES}")

    m = _resolve_mention() if mention else None

    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": f"{_TITLE_PREFIX[severity]}: {title}",
            "weight": "Bolder",
            "size": "Medium",
        },
        {
            "type": "TextBlock",
            "text": f"{m['tag']} {message}" if m else message,
            "wrap": True,
        },
    ]

    if context:
        body.append(
            {
                "type": "TextBlock",
                "text": "\n".join(f"**{k}**: {v}" for k, v in context.items()),
                "wrap": True,
                "isSubtle": True,
            }
        )

    card: dict[str, Any] = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body,
    }
    if m:
        card["msteams"] = {"entities": [m["entity"]]}

    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": card,
            }
        ],
    }


def _post(payload: dict, *, webhook_url: str) -> bool:
    """POST `payload` to `webhook_url`. Returns True on 2xx, False otherwise.

    Catches network errors so the caller never has to. Logs the cause on
    failure so operators can debug via CloudWatch.
    """
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=POST_TIMEOUT_SECONDS) as resp:
            if 200 <= resp.status < 300:
                return True
            logger.warning("Teams webhook returned HTTP %s", resp.status)
            return False
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning("Teams webhook POST failed: %s", exc)
        return False


def alert(
    severity: str,
    title: str,
    message: str,
    context: Optional[dict[str, Any]] = None,
    *,
    mention: bool = True,
    webhook_url: Optional[str] = None,
) -> bool:
    """Send a Teams alert. Returns True on 2xx, False otherwise. Never raises.

    No-op (logged) when ``RECITERAI_TEAMS_WEBHOOK_URL`` is unset, so local
    runs without Teams credentials don't fail.

    Args:
        severity: ``WARN`` or ``ERROR``.
        title: short headline.
        message: body paragraph.
        context: optional key/value rendering.
        mention: prepend the configured @-tag (no-op if env vars unset).
        webhook_url: optional override for ``RECITERAI_TEAMS_WEBHOOK_URL``.
    """
    url = webhook_url if webhook_url is not None else os.environ.get(WEBHOOK_ENV, "").strip()
    if not url:
        logger.info(
            "Teams alert skipped (%s not set): [%s] %s — %s",
            WEBHOOK_ENV, severity, title, message,
        )
        return False

    payload = build_card(severity, title, message, context, mention=mention)
    return _post(payload, webhook_url=url)

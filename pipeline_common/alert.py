"""Severity-tagged alert dispatcher (Phase 10 T11).

`dispatch(severity, message, context, open_issue=False)` routes alerts:

- `WARN`  → Slack only (POST to `RECITERAI_SLACK_WEBHOOK_URL`).
- `ERROR` → Slack + `gh issue create` (or comment on an existing open
  issue labelled `drift-alert`, to avoid duplicate noise on consecutive
  daily evaluations).

Both transports are best-effort: a Slack POST failure logs and continues
rather than failing the calling Lambda. The intent is observability, not
flow control — the upstream STAGE# / DRIFT# rows are the source of
truth; alerts are derivative.

Slack webhook URL comes from env var `RECITERAI_SLACK_WEBHOOK_URL`
(disambiguated per Open Q 10.2). Absent webhook → Slack post is logged
and skipped (so local runs don't blow up).

GitHub issue creation/lookup uses the `gh` CLI. Absent `gh` on PATH →
issue creation is logged and skipped. The CLI is assumed to be
authenticated in the deployment environment (Lambda layer ships
`gh` + token via env).

The `context` dict is rendered into the Slack message body as a
key=value block and into the issue body as a fenced JSON block, so
operators can see the triggered thresholds + evaluation window at a
glance.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from typing import Any, Literal

logger = logging.getLogger(__name__)

Severity = Literal["WARN", "ERROR"]
VALID_SEVERITIES: tuple[Severity, ...] = ("WARN", "ERROR")

SLACK_ENV = "RECITERAI_SLACK_WEBHOOK_URL"
ISSUE_LABEL = "drift-alert"
SLACK_TIMEOUT_SECONDS = 5
GH_TIMEOUT_SECONDS = 15


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------


def _format_slack_payload(severity: Severity, message: str, context: dict) -> dict:
    icon = ":rotating_light:" if severity == "ERROR" else ":warning:"
    lines = [f"{icon} *[{severity}] ReciterAI*: {message}"]
    if context:
        lines.append("```")
        for k, v in context.items():
            lines.append(f"{k}: {v}")
        lines.append("```")
    return {"text": "\n".join(lines)}


def _post_slack(payload: dict, *, webhook_url: str) -> bool:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=SLACK_TIMEOUT_SECONDS) as resp:
            if 200 <= resp.status < 300:
                return True
            logger.warning("Slack webhook returned HTTP %s", resp.status)
            return False
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning("Slack webhook POST failed: %s", exc)
        return False


def post_to_slack(severity: Severity, message: str, context: dict) -> bool:
    """Post a formatted Slack message; returns True on 2xx, False otherwise."""
    webhook = os.environ.get(SLACK_ENV)
    if not webhook:
        logger.info(
            "Slack alert skipped (%s not set): [%s] %s", SLACK_ENV, severity, message
        )
        return False
    payload = _format_slack_payload(severity, message, context)
    return _post_slack(payload, webhook_url=webhook)


# ---------------------------------------------------------------------------
# GitHub issues
# ---------------------------------------------------------------------------


def _gh_available() -> bool:
    return shutil.which("gh") is not None


def _run_gh(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        timeout=GH_TIMEOUT_SECONDS,
        check=False,
    )


def _find_open_issue(label: str) -> int | None:
    """Return the issue number of the most recent open issue with `label`."""
    result = _run_gh(
        ["issue", "list", "--label", label, "--state", "open", "--json", "number", "--limit", "1"]
    )
    if result.returncode != 0:
        logger.warning("gh issue list failed: %s", result.stderr.strip())
        return None
    try:
        rows = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return None
    if not rows:
        return None
    return int(rows[0]["number"])


def _issue_body(message: str, context: dict) -> str:
    lines = [message, "", "```json", json.dumps(context, indent=2, default=str), "```"]
    return "\n".join(lines)


def open_or_comment_issue(message: str, context: dict, *, label: str = ISSUE_LABEL) -> bool:
    """Create a new labelled issue, or comment on the existing open one.

    Returns True on success, False on any failure (logged, never raised —
    alerting is best-effort). Existing-issue lookup keeps noise low: the
    daily drift evaluator can fire repeatedly without spawning duplicate
    issues.
    """
    if not _gh_available():
        logger.info("gh CLI not on PATH; skipping issue creation for: %s", message)
        return False

    body = _issue_body(message, context)
    existing = _find_open_issue(label)
    if existing is not None:
        result = _run_gh(["issue", "comment", str(existing), "--body", body])
        if result.returncode != 0:
            logger.warning("gh issue comment failed: %s", result.stderr.strip())
            return False
        return True

    title = f"[drift-alert] {message[:80]}"
    result = _run_gh(["issue", "create", "--label", label, "--title", title, "--body", body])
    if result.returncode != 0:
        logger.warning("gh issue create failed: %s", result.stderr.strip())
        return False
    return True


# ---------------------------------------------------------------------------
# Public dispatch
# ---------------------------------------------------------------------------


def dispatch(
    severity: Severity,
    message: str,
    context: dict[str, Any] | None = None,
    *,
    open_issue: bool = False,
) -> dict[str, bool]:
    """Route an alert by severity.

    - `WARN`  → Slack only.
    - `ERROR` → Slack + GitHub issue (created or commented).
    - `open_issue=True` forces issue creation even at WARN severity (used
      by callers that want a paper trail for tunable thresholds).

    Returns a dict `{"slack": bool, "issue": bool}` reflecting transport
    success. Always returns; never raises — alerting failures must not
    cascade into pipeline failures.
    """
    if severity not in VALID_SEVERITIES:
        raise ValueError(
            f"invalid severity {severity!r}; expected one of {VALID_SEVERITIES}"
        )

    ctx = dict(context or {})
    result = {"slack": False, "issue": False}

    try:
        result["slack"] = post_to_slack(severity, message, ctx)
    except Exception as exc:  # defensive: never raise from dispatch
        logger.warning("Slack dispatch raised unexpectedly: %s", exc)

    should_open_issue = open_issue or severity == "ERROR"
    if should_open_issue:
        try:
            result["issue"] = open_or_comment_issue(message, ctx)
        except Exception as exc:
            logger.warning("Issue dispatch raised unexpectedly: %s", exc)

    return result

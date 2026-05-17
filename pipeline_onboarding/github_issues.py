"""GitHub Issues REST client for the onboarding detector (#80 Phase 2, PR 5).

The daily onboarding detector files one GitHub issue per CWID that needs a
backfill or has ReCiter attribution drift (spec T1). It runs as a Lambda, so
it cannot shell out to the `gh` CLI the way `pipeline_common/alert.py` does —
`gh` is not in the Lambda runtime (the hot-path `alert_dispatcher.py` stub
flags exactly this gap, #72). This module talks to the GitHub REST API
directly over `urllib`, the same transport `pipeline_enrichment/alerting.py`
uses for the Teams webhook.

Auth: a fine-grained personal access token scoped to `wcmc-its/ReciterAI`
with Issues read+write only (PLAN R-3 — narrower blast radius than a classic
`repo`-scoped PAT). The token is resolved, in order, from:

  1. the `RECITERAI_GITHUB_TOKEN` env var (local dev / tests), then
  2. AWS Secrets Manager secret `reciterai/github-token` (the Lambda runtime).

The Secrets Manager value may be the bare token string, or a JSON object
with a `token` (or `RECITERAI_GITHUB_TOKEN`) key. PR 6 provisions the secret
and the `secretsmanager:GetSecretValue` IAM grant. Migration to a GitHub App
is tracked in #94 and is not a PR 5 blocker.

Idempotency / conflict path (spec T1): the detector matches open issues
labelled `onboarding` whose title carries the literal `CWID {cwid}`. Exactly
one match -> update it; zero -> create; more than one (should not happen) ->
log a warning and update the most recently updated one.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Any, Iterable

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
DEFAULT_REPO = "wcmc-its/ReciterAI"
ONBOARDING_LABEL = "onboarding"

REPO_ENV = "RECITERAI_GITHUB_REPO"
TOKEN_ENV = "RECITERAI_GITHUB_TOKEN"
TOKEN_SECRET_ID = "reciterai/github-token"

HTTP_TIMEOUT_SECONDS = 10
_PER_PAGE = 100
_MAX_PAGES = 30  # 3000 rows — far above any realistic onboarding-issue volume.

# Sentinel embedded in the OQ-3 "safe to close" comment so the detector posts
# it exactly once, not every day a resolved CWID's issue stays open.
RESOLUTION_MARKER = "<!-- onboarding-detector:resolved -->"

# CWID extraction from an onboarding issue title. The \b anchors stop
# `CWID abc1234` from matching an issue about `CWID abc12345`.
_CWID_IN_TITLE = re.compile(r"\bCWID\s+([A-Za-z0-9]+)\b")


class GithubApiError(RuntimeError):
    """A GitHub REST call failed — a transport error or a non-success status."""


class GithubAuthError(GithubApiError):
    """No GitHub token could be resolved from env or Secrets Manager."""


# ---------------------------------------------------------------------------
# Config + auth resolution
# ---------------------------------------------------------------------------

_token_cache: str | None = None
_token_resolved = False


def get_repo() -> str:
    """The `owner/repo` the detector files against (default: wcmc-its/ReciterAI)."""
    return os.environ.get(REPO_ENV, "").strip() or DEFAULT_REPO


def _fetch_token_from_secret() -> str:
    """Fetch the GitHub token from Secrets Manager; return "" on any failure."""
    try:
        import boto3  # local import: tests stay offline; keeps cold start lean

        client = boto3.client("secretsmanager")
        raw = client.get_secret_value(SecretId=TOKEN_SECRET_ID)["SecretString"].strip()
    except Exception as exc:  # noqa: BLE001 — a missing secret degrades, never crashes
        logger.warning(
            "github_issues: could not load %s from Secrets Manager: %s",
            TOKEN_SECRET_ID, exc,
        )
        return ""
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("github_issues: %s is not valid JSON", TOKEN_SECRET_ID)
            return ""
        for key in ("token", TOKEN_ENV, "github_token", "GITHUB_TOKEN"):
            value = data.get(key)
            if value:
                return str(value).strip()
        logger.warning(
            "github_issues: %s JSON has no recognized token key", TOKEN_SECRET_ID
        )
        return ""
    return raw


def resolve_token(*, refresh: bool = False) -> str | None:
    """Return the GitHub token, or None when none is configured.

    Env var first (local dev / tests), then Secrets Manager (Lambda runtime).
    Cached for the process; `refresh=True` forces a re-resolve (tests).
    """
    global _token_cache, _token_resolved
    if _token_resolved and not refresh:
        return _token_cache
    token = os.environ.get(TOKEN_ENV, "").strip() or _fetch_token_from_secret()
    _token_cache = token or None
    _token_resolved = True
    return _token_cache


def _require_token(token: str | None) -> str:
    """Return `token` if given, else the resolved token, else raise."""
    resolved = token or resolve_token()
    if not resolved:
        raise GithubAuthError(
            f"no GitHub token: set {TOKEN_ENV} or provision the "
            f"{TOKEN_SECRET_ID} Secrets Manager secret"
        )
    return resolved


# ---------------------------------------------------------------------------
# Low-level HTTP seam (tests monkeypatch `_request`)
# ---------------------------------------------------------------------------


def _parse_json(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return None


def _request(
    method: str,
    path: str,
    *,
    token: str,
    body: dict | None = None,
) -> tuple[int, Any, dict[str, str]]:
    """One GitHub REST call. Returns (status, parsed_json|None, headers).

    Raises `GithubApiError` only on a transport failure (DNS, timeout, reset).
    HTTP error *statuses* (404, 422, 5xx) are returned, not raised, so callers
    can treat e.g. a 422 duplicate-label POST as success.
    """
    url = path if path.startswith("http") else f"{GITHUB_API}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "reciterai-onboarding-detector")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            return resp.status, _parse_json(resp.read()), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, _parse_json(exc.read()), dict(exc.headers or {})
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GithubApiError(f"{method} {url}: transport failure: {exc}") from exc


# ---------------------------------------------------------------------------
# Issue + label + comment operations
# ---------------------------------------------------------------------------


def list_open_issues(
    *,
    repo: str | None = None,
    label: str = ONBOARDING_LABEL,
    token: str | None = None,
) -> list[dict]:
    """Every open issue carrying `label`, across all pages.

    GitHub's issues endpoint also returns pull requests; those are filtered
    out (a PR carries a `pull_request` key, a plain issue does not).
    """
    repo = repo or get_repo()
    token = _require_token(token)
    out: list[dict] = []
    for page in range(1, _MAX_PAGES + 1):
        status, data, _ = _request(
            "GET",
            f"/repos/{repo}/issues"
            f"?state=open&labels={label}&per_page={_PER_PAGE}&page={page}",
            token=token,
        )
        if status != 200:
            raise GithubApiError(
                f"list issues for {repo} returned HTTP {status}: {data}"
            )
        rows = data or []
        out.extend(row for row in rows if "pull_request" not in row)
        if len(rows) < _PER_PAGE:
            break
    return out


def ensure_label(
    label: str = ONBOARDING_LABEL,
    *,
    repo: str | None = None,
    token: str | None = None,
    color: str = "1d76db",
    description: str = "Tracks a CWID flagged by the onboarding detector (#80).",
) -> bool:
    """Create `label` if absent. Returns True if created, False if it existed.

    A fine-grained PAT with Issues write can manage labels. GitHub answers a
    duplicate-name POST with 422, which is the success-equivalent here. Call
    once per detector run before any `create_issue`, not once per CWID.
    """
    repo = repo or get_repo()
    token = _require_token(token)
    status, data, _ = _request(
        "POST",
        f"/repos/{repo}/labels",
        token=token,
        body={"name": label, "color": color, "description": description},
    )
    if status == 201:
        return True
    if status == 422:
        return False
    raise GithubApiError(f"ensure_label({label}) returned HTTP {status}: {data}")


def create_issue(
    title: str,
    body: str,
    *,
    labels: Iterable[str] = (ONBOARDING_LABEL,),
    repo: str | None = None,
    token: str | None = None,
) -> dict:
    """Open a new issue. Returns the created issue dict (carries `number`, `html_url`)."""
    repo = repo or get_repo()
    token = _require_token(token)
    status, data, _ = _request(
        "POST",
        f"/repos/{repo}/issues",
        token=token,
        body={"title": title, "body": body, "labels": list(labels)},
    )
    if status != 201:
        raise GithubApiError(f"create_issue returned HTTP {status}: {data}")
    return data or {}


def update_issue(
    number: int,
    *,
    title: str | None = None,
    body: str | None = None,
    repo: str | None = None,
    token: str | None = None,
) -> dict:
    """Patch an issue's title and/or body. Returns the updated issue dict."""
    repo = repo or get_repo()
    token = _require_token(token)
    payload: dict[str, str] = {}
    if title is not None:
        payload["title"] = title
    if body is not None:
        payload["body"] = body
    if not payload:
        raise ValueError("update_issue: pass at least one of title / body")
    status, data, _ = _request(
        "PATCH", f"/repos/{repo}/issues/{number}", token=token, body=payload
    )
    if status != 200:
        raise GithubApiError(f"update_issue(#{number}) returned HTTP {status}: {data}")
    return data or {}


def add_comment(
    number: int, body: str, *, repo: str | None = None, token: str | None = None
) -> dict:
    """Add a comment to an issue. Returns the created comment dict."""
    repo = repo or get_repo()
    token = _require_token(token)
    status, data, _ = _request(
        "POST",
        f"/repos/{repo}/issues/{number}/comments",
        token=token,
        body={"body": body},
    )
    if status != 201:
        raise GithubApiError(f"add_comment(#{number}) returned HTTP {status}: {data}")
    return data or {}


def list_comments(
    number: int, *, repo: str | None = None, token: str | None = None
) -> list[dict]:
    """Every comment on an issue, across all pages."""
    repo = repo or get_repo()
    token = _require_token(token)
    out: list[dict] = []
    for page in range(1, _MAX_PAGES + 1):
        status, data, _ = _request(
            "GET",
            f"/repos/{repo}/issues/{number}/comments?per_page={_PER_PAGE}&page={page}",
            token=token,
        )
        if status != 200:
            raise GithubApiError(
                f"list comments for #{number} returned HTTP {status}: {data}"
            )
        rows = data or []
        out.extend(rows)
        if len(rows) < _PER_PAGE:
            break
    return out


# ---------------------------------------------------------------------------
# CWID <-> issue matching (pure)
# ---------------------------------------------------------------------------


def cwid_from_title(title: str) -> str | None:
    """Extract the CWID from an onboarding issue title, or None when absent."""
    match = _CWID_IN_TITLE.search(title or "")
    return match.group(1) if match else None


def find_issues_for_cwid(issues: Iterable[dict], cwid: str) -> list[dict]:
    """Open onboarding issues whose title carries the literal `CWID {cwid}`.

    The idempotency key from spec T1: a word-boundary match so `CWID abc1234`
    cannot collide with an issue for `CWID abc12345`.
    """
    pattern = re.compile(rf"\bCWID\s+{re.escape(cwid)}\b")
    return [i for i in issues if pattern.search(i.get("title") or "")]


# ---------------------------------------------------------------------------
# High-level: upsert + resolution comment
# ---------------------------------------------------------------------------


def upsert_onboarding_issue(
    cwid: str,
    title: str,
    body: str,
    *,
    open_issues: Iterable[dict],
    refresh_comment: str | None = None,
    repo: str | None = None,
    token: str | None = None,
) -> dict:
    """File a new onboarding issue for `cwid`, or refresh the existing one.

    `open_issues` is the pre-fetched `list_open_issues()` result — passed in
    so the detector fetches the issue list once per run, not once per CWID.

    Conflict path (spec T1):
      - zero matches  -> create a new issue;
      - exactly one   -> update its title + body, then add `refresh_comment`;
      - more than one -> log a warning, update the most recently updated one.

    Returns ``{"action": "created"|"updated", "issue": <issue dict>}``.
    """
    repo = repo or get_repo()
    token = _require_token(token)
    matches = find_issues_for_cwid(open_issues, cwid)

    if not matches:
        issue = create_issue(title, body, repo=repo, token=token)
        logger.info(
            "onboarding detector: filed issue #%s for CWID %s",
            issue.get("number"), cwid,
        )
        return {"action": "created", "issue": issue}

    if len(matches) > 1:
        logger.warning(
            "onboarding detector: %d open onboarding issues match CWID %s "
            "(expected 1: %s); updating the most recently updated",
            len(matches), cwid,
            ", ".join(f"#{m.get('number')}" for m in matches),
        )
    issue = max(matches, key=lambda i: i.get("updated_at") or "")
    number = issue["number"]
    updated = update_issue(number, title=title, body=body, repo=repo, token=token)
    if refresh_comment:
        add_comment(number, refresh_comment, repo=repo, token=token)
    logger.info("onboarding detector: refreshed issue #%s for CWID %s", number, cwid)
    return {"action": "updated", "issue": updated or issue}


def post_resolution_comment(
    issue: dict, body: str, *, repo: str | None = None, token: str | None = None
) -> bool:
    """Post the OQ-3 'safe to close' comment on a now-clean CWID's issue.

    Idempotent: skipped if any existing comment already carries
    `RESOLUTION_MARKER`, so a resolved CWID's issue is commented exactly once
    however many days it stays open. The operator still closes the issue
    manually (OQ-3 — true auto-close stays deferred).

    Returns True if a comment was posted, False if one already existed.
    """
    repo = repo or get_repo()
    token = _require_token(token)
    number = issue["number"]
    for comment in list_comments(number, repo=repo, token=token):
        if RESOLUTION_MARKER in (comment.get("body") or ""):
            return False
    add_comment(number, f"{body}\n\n{RESOLUTION_MARKER}", repo=repo, token=token)
    logger.info("onboarding detector: posted resolution comment on issue #%s", number)
    return True


__all__ = [
    "GithubApiError",
    "GithubAuthError",
    "ONBOARDING_LABEL",
    "RESOLUTION_MARKER",
    "get_repo",
    "resolve_token",
    "list_open_issues",
    "ensure_label",
    "create_issue",
    "update_issue",
    "add_comment",
    "list_comments",
    "cwid_from_title",
    "find_issues_for_cwid",
    "upsert_onboarding_issue",
    "post_resolution_comment",
]

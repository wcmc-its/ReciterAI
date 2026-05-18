"""#80 Phase 2 (PR 5) — pipeline_onboarding.github_issues tests.

Covers the GitHub REST client the onboarding detector files issues with:
the `_request` transport seam, the issue / label / comment operations, the
CWID <-> title matching, and the spec-T1 conflict path (create / update /
warn-and-update-most-recent) plus the OQ-3 safe-to-close comment.
"""

from __future__ import annotations

import io
import urllib.error

import pytest

import pipeline_onboarding.github_issues as gi


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stub_request(monkeypatch, responses):
    """Install a fake `gi._request`. `responses` is a list of (status, data);
    each call consumes the next. Returns the recorded-calls list."""
    calls: list[dict] = []
    seq = iter(responses)

    def fake(method, path, *, token, body=None):
        calls.append({"method": method, "path": path, "body": body, "token": token})
        status, data = next(seq)
        return status, data, {}

    monkeypatch.setattr(gi, "_request", fake)
    return calls


class _FakeResponse:
    """Minimal urlopen() return value for the `_request` transport tests."""

    def __init__(self, status: int, body: str):
        self.status = status
        self._body = body.encode("utf-8")
        self.headers = {}

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# ---------------------------------------------------------------------------
# get_repo / resolve_token
# ---------------------------------------------------------------------------


def test_get_repo_defaults_to_wcmc_reciterai(monkeypatch):
    monkeypatch.delenv(gi.REPO_ENV, raising=False)
    assert gi.get_repo() == "wcmc-its/ReciterAI"


def test_get_repo_env_override(monkeypatch):
    monkeypatch.setenv(gi.REPO_ENV, "acme/widgets")
    assert gi.get_repo() == "acme/widgets"


def test_resolve_token_from_env(monkeypatch):
    monkeypatch.setenv(gi.TOKEN_ENV, "ghp_envtoken")
    assert gi.resolve_token(refresh=True) == "ghp_envtoken"


def test_resolve_token_falls_back_to_secret(monkeypatch):
    monkeypatch.delenv(gi.TOKEN_ENV, raising=False)
    monkeypatch.setattr(gi, "_fetch_token_from_secret", lambda: "ghp_secrettoken")
    assert gi.resolve_token(refresh=True) == "ghp_secrettoken"


def test_resolve_token_none_when_unconfigured(monkeypatch):
    monkeypatch.delenv(gi.TOKEN_ENV, raising=False)
    monkeypatch.setattr(gi, "_fetch_token_from_secret", lambda: "")
    assert gi.resolve_token(refresh=True) is None


def test_operations_raise_github_auth_error_without_token(monkeypatch):
    monkeypatch.delenv(gi.TOKEN_ENV, raising=False)
    monkeypatch.setattr(gi, "_fetch_token_from_secret", lambda: "")
    gi.resolve_token(refresh=True)  # prime the cache to None
    with pytest.raises(gi.GithubAuthError):
        gi.list_open_issues(repo="o/r")


# ---------------------------------------------------------------------------
# _request transport seam
# ---------------------------------------------------------------------------


def test_request_success_builds_authorized_request(monkeypatch):
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["headers"] = dict(req.header_items())
        return _FakeResponse(201, '{"number": 7}')

    monkeypatch.setattr(gi.urllib.request, "urlopen", fake_urlopen)
    status, data, _ = gi._request(
        "POST", "/repos/o/r/issues", token="tok", body={"title": "x"}
    )
    assert status == 201
    assert data == {"number": 7}
    assert captured["method"] == "POST"
    assert captured["url"] == "https://api.github.com/repos/o/r/issues"
    assert "Bearer tok" in captured["headers"].values()


def test_request_returns_http_error_status_without_raising(monkeypatch):
    """A 422 (e.g. duplicate label) is returned, not raised — callers decide."""

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 422, "Unprocessable", {},
            io.BytesIO(b'{"message": "already_exists"}'),
        )

    monkeypatch.setattr(gi.urllib.request, "urlopen", fake_urlopen)
    status, data, _ = gi._request("POST", "/repos/o/r/labels", token="tok", body={})
    assert status == 422
    assert data == {"message": "already_exists"}


def test_request_raises_github_api_error_on_transport_failure(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(gi.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(gi.GithubApiError):
        gi._request("GET", "/repos/o/r/issues", token="tok")


# ---------------------------------------------------------------------------
# list_open_issues
# ---------------------------------------------------------------------------


def test_list_open_issues_filters_pull_requests(monkeypatch):
    _stub_request(monkeypatch, [
        (200, [
            {"number": 1, "title": "a real issue"},
            {"number": 2, "title": "a PR", "pull_request": {"url": "..."}},
        ]),
    ])
    issues = gi.list_open_issues(repo="o/r", token="tok")
    assert [i["number"] for i in issues] == [1]


def test_list_open_issues_paginates(monkeypatch):
    full_page = [{"number": n, "title": f"i{n}"} for n in range(100)]
    short_page = [{"number": 100, "title": "i100"}]
    _stub_request(monkeypatch, [(200, full_page), (200, short_page)])
    issues = gi.list_open_issues(repo="o/r", token="tok")
    assert len(issues) == 101


def test_list_open_issues_raises_on_error_status(monkeypatch):
    _stub_request(monkeypatch, [(403, {"message": "forbidden"})])
    with pytest.raises(gi.GithubApiError):
        gi.list_open_issues(repo="o/r", token="tok")


# ---------------------------------------------------------------------------
# ensure_label
# ---------------------------------------------------------------------------


def test_ensure_label_created(monkeypatch):
    _stub_request(monkeypatch, [(201, {"name": "onboarding"})])
    assert gi.ensure_label(repo="o/r", token="tok") is True


def test_ensure_label_already_exists_is_not_an_error(monkeypatch):
    _stub_request(monkeypatch, [(422, {"message": "already_exists"})])
    assert gi.ensure_label(repo="o/r", token="tok") is False


def test_ensure_label_raises_on_unexpected_status(monkeypatch):
    _stub_request(monkeypatch, [(500, {"message": "boom"})])
    with pytest.raises(gi.GithubApiError):
        gi.ensure_label(repo="o/r", token="tok")


# ---------------------------------------------------------------------------
# create_issue / update_issue / add_comment / list_comments
# ---------------------------------------------------------------------------


def test_create_issue_posts_with_label(monkeypatch):
    calls = _stub_request(monkeypatch, [(201, {"number": 42, "html_url": "u"})])
    issue = gi.create_issue("title", "body", repo="o/r", token="tok")
    assert issue["number"] == 42
    assert calls[0]["method"] == "POST"
    assert calls[0]["path"] == "/repos/o/r/issues"
    assert calls[0]["body"]["labels"] == ["onboarding"]


def test_create_issue_raises_on_failure(monkeypatch):
    _stub_request(monkeypatch, [(422, {"message": "bad"})])
    with pytest.raises(gi.GithubApiError):
        gi.create_issue("t", "b", repo="o/r", token="tok")


def test_update_issue_patches_title_and_body(monkeypatch):
    calls = _stub_request(monkeypatch, [(200, {"number": 5})])
    gi.update_issue(5, title="new", body="nb", repo="o/r", token="tok")
    assert calls[0]["method"] == "PATCH"
    assert calls[0]["path"] == "/repos/o/r/issues/5"
    assert calls[0]["body"] == {"title": "new", "body": "nb"}


def test_update_issue_requires_a_field():
    with pytest.raises(ValueError):
        gi.update_issue(5, repo="o/r", token="tok")


def test_add_comment_posts(monkeypatch):
    calls = _stub_request(monkeypatch, [(201, {"id": 9})])
    gi.add_comment(5, "hello", repo="o/r", token="tok")
    assert calls[0]["path"] == "/repos/o/r/issues/5/comments"
    assert calls[0]["body"] == {"body": "hello"}


def test_list_comments_paginates(monkeypatch):
    full_page = [{"id": i, "body": f"c{i}"} for i in range(100)]
    short_page = [{"id": 100, "body": "last"}]
    _stub_request(monkeypatch, [(200, full_page), (200, short_page)])
    assert len(gi.list_comments(7, repo="o/r", token="tok")) == 101


# ---------------------------------------------------------------------------
# CWID <-> title matching
# ---------------------------------------------------------------------------


def test_cwid_from_title_backfill_shape():
    title = "[onboarding] CWID abc1234 needs backfill (5 PMIDs)"
    assert gi.cwid_from_title(title) == "abc1234"


def test_cwid_from_title_drift_shape():
    title = "[onboarding] CWID xyz9 attribution drift (+1, -0 PMIDs)"
    assert gi.cwid_from_title(title) == "xyz9"


def test_cwid_from_title_none_when_absent():
    assert gi.cwid_from_title("a random unrelated issue") is None


def test_find_issues_for_cwid_matches_by_title():
    issues = [
        {"number": 1, "title": "[onboarding] CWID abc1234 needs backfill (3 PMIDs)"},
        {"number": 2, "title": "[onboarding] CWID zzz0001 needs backfill (1 PMIDs)"},
    ]
    assert [i["number"] for i in gi.find_issues_for_cwid(issues, "abc1234")] == [1]


def test_find_issues_for_cwid_word_boundary_guards_prefix_collision():
    """`CWID abc1234` must not match an issue about `CWID abc12345`."""
    issues = [
        {"number": 9, "title": "[onboarding] CWID abc12345 needs backfill (2 PMIDs)"}
    ]
    assert gi.find_issues_for_cwid(issues, "abc1234") == []


def test_find_issues_for_cwid_no_match():
    issues = [{"number": 1, "title": "[onboarding] CWID other1 needs backfill (1 PMIDs)"}]
    assert gi.find_issues_for_cwid(issues, "abc1234") == []


def test_find_issue_by_title_exact_match():
    """The cold-start digest issue (#106) is matched by its fixed title."""
    issues = [
        {"number": 1, "title": "[onboarding] Detector backlog digest"},
        {"number": 2, "title": "[onboarding] CWID abc1234 needs backfill (1 PMIDs)"},
    ]
    found = gi.find_issue_by_title(issues, "[onboarding] Detector backlog digest")
    assert [i["number"] for i in found] == [1]


def test_find_issue_by_title_requires_exact_not_partial():
    """A title that merely contains the string is not a match."""
    issues = [{"number": 5, "title": "[onboarding] Detector backlog digest — old"}]
    assert gi.find_issue_by_title(issues, "[onboarding] Detector backlog digest") == []


def test_find_issue_by_title_tolerates_surrounding_whitespace():
    issues = [{"number": 7, "title": "  [onboarding] Detector backlog digest  "}]
    found = gi.find_issue_by_title(issues, "[onboarding] Detector backlog digest")
    assert [i["number"] for i in found] == [7]


# ---------------------------------------------------------------------------
# upsert_onboarding_issue — the spec-T1 conflict path
# ---------------------------------------------------------------------------


def test_upsert_creates_when_zero_matches(monkeypatch):
    calls = _stub_request(monkeypatch, [(201, {"number": 50, "html_url": "u"})])
    result = gi.upsert_onboarding_issue(
        "abc1234",
        "[onboarding] CWID abc1234 needs backfill (2 PMIDs)",
        "body",
        open_issues=[],
        repo="o/r",
        token="tok",
    )
    assert result["action"] == "created"
    assert result["issue"]["number"] == 50
    assert calls[0]["method"] == "POST"
    assert calls[0]["path"] == "/repos/o/r/issues"


def test_upsert_updates_and_comments_when_one_match(monkeypatch):
    existing = [{
        "number": 7,
        "title": "[onboarding] CWID abc1234 needs backfill (9 PMIDs)",
        "updated_at": "2026-05-10T00:00:00Z",
    }]
    calls = _stub_request(monkeypatch, [
        (200, {"number": 7}),  # PATCH
        (201, {"id": 1}),      # refresh comment
    ])
    result = gi.upsert_onboarding_issue(
        "abc1234",
        "[onboarding] CWID abc1234 needs backfill (2 PMIDs)",
        "newbody",
        open_issues=existing,
        refresh_comment="daily refresh",
        repo="o/r",
        token="tok",
    )
    assert result["action"] == "updated"
    assert calls[0]["method"] == "PATCH"
    assert calls[0]["path"] == "/repos/o/r/issues/7"
    assert calls[1]["path"] == "/repos/o/r/issues/7/comments"
    assert calls[1]["body"] == {"body": "daily refresh"}


def test_upsert_skips_comment_when_refresh_comment_is_none(monkeypatch):
    existing = [{
        "number": 7,
        "title": "[onboarding] CWID abc1234 needs backfill (9 PMIDs)",
        "updated_at": "2026-05-10T00:00:00Z",
    }]
    calls = _stub_request(monkeypatch, [(200, {"number": 7})])  # PATCH only
    gi.upsert_onboarding_issue(
        "abc1234", "t", "b", open_issues=existing, repo="o/r", token="tok"
    )
    assert len(calls) == 1


def test_upsert_multiple_matches_updates_most_recently_updated(monkeypatch):
    existing = [
        {"number": 7, "title": "[onboarding] CWID abc1234 older",
         "updated_at": "2026-05-01T00:00:00Z"},
        {"number": 8, "title": "[onboarding] CWID abc1234 newer",
         "updated_at": "2026-05-15T00:00:00Z"},
    ]
    calls = _stub_request(monkeypatch, [(200, {"number": 8}), (201, {"id": 1})])
    result = gi.upsert_onboarding_issue(
        "abc1234", "t", "b", open_issues=existing,
        refresh_comment="r", repo="o/r", token="tok",
    )
    assert result["action"] == "updated"
    assert calls[0]["path"] == "/repos/o/r/issues/8"  # the 2026-05-15 one


# ---------------------------------------------------------------------------
# post_resolution_comment — OQ-3 safe-to-close
# ---------------------------------------------------------------------------


def test_post_resolution_comment_posts_when_no_marker(monkeypatch):
    calls = _stub_request(monkeypatch, [
        (200, [{"id": 1, "body": "an unrelated comment"}]),  # list_comments
        (201, {"id": 99}),                                   # add_comment
    ])
    posted = gi.post_resolution_comment(
        {"number": 7}, "all clear", repo="o/r", token="tok"
    )
    assert posted is True
    assert calls[1]["path"] == "/repos/o/r/issues/7/comments"
    assert gi.RESOLUTION_MARKER in calls[1]["body"]["body"]


def test_post_resolution_comment_skips_when_marker_already_present(monkeypatch):
    calls = _stub_request(monkeypatch, [
        (200, [{"id": 1, "body": f"earlier note {gi.RESOLUTION_MARKER}"}]),
    ])
    posted = gi.post_resolution_comment(
        {"number": 7}, "all clear", repo="o/r", token="tok"
    )
    assert posted is False
    assert len(calls) == 1  # listed comments, posted nothing

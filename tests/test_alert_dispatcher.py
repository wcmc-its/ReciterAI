"""Phase 10 T11 — pipeline_common.alert + pipeline_drift.severity.

Covers:
- dispatch routes WARN → Slack only, ERROR → Slack + gh issue create.
- open_issue=True forces issue even at WARN severity.
- Slack failure does not raise from dispatch (best-effort transport).
- Slack is skipped (logged) when RECITERAI_SLACK_WEBHOOK_URL is unset.
- gh issue is skipped when gh CLI is absent.
- Existing open drift-alert issue is *commented* on rather than
  creating a duplicate.
- severity.classify_drift_evaluation maps facts → conditions correctly.
- severity_for / cold_run_recommended honor the D-11 table.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from pipeline_common import alert
from pipeline_drift import severity
from pipeline_drift.evaluator import DriftEvaluation


# ---------------------------------------------------------------------------
# dispatch routing
# ---------------------------------------------------------------------------


@pytest.fixture
def slack_env(monkeypatch):
    monkeypatch.setenv(alert.SLACK_ENV, "https://hooks.slack.example/T/B/X")
    return "https://hooks.slack.example/T/B/X"


@pytest.fixture
def gh_present(monkeypatch):
    monkeypatch.setattr(alert.shutil, "which", lambda name: "/usr/local/bin/gh")


def _ok_response():
    resp = MagicMock()
    resp.__enter__ = lambda self: self
    resp.__exit__ = lambda *a, **kw: None
    resp.status = 200
    return resp


def test_dispatch_warn_routes_to_slack_only(slack_env, gh_present):
    with patch.object(alert.urllib.request, "urlopen", return_value=_ok_response()) as urlopen, \
         patch.object(alert, "_run_gh") as run_gh:
        result = alert.dispatch("WARN", "uncovered rate trending up", {"rate": 0.04})

    assert result == {"slack": True, "issue": False}
    assert urlopen.call_count == 1
    run_gh.assert_not_called()


def test_dispatch_error_routes_to_slack_and_gh(slack_env, gh_present):
    list_proc = MagicMock(returncode=0, stdout="[]", stderr="")
    create_proc = MagicMock(returncode=0, stdout="", stderr="")

    def fake_run_gh(args):
        if args[0] == "issue" and args[1] == "list":
            return list_proc
        if args[0] == "issue" and args[1] == "create":
            return create_proc
        raise AssertionError(f"unexpected gh args: {args}")

    with patch.object(alert.urllib.request, "urlopen", return_value=_ok_response()) as urlopen, \
         patch.object(alert, "_run_gh", side_effect=fake_run_gh) as run_gh:
        result = alert.dispatch(
            "ERROR",
            "uncovered_rate exceeded 5%",
            {"rate": 0.06, "window_days": 14},
        )

    assert result == {"slack": True, "issue": True}
    assert urlopen.call_count == 1
    # one list, one create
    assert run_gh.call_count == 2
    assert run_gh.call_args_list[0].args[0][:2] == ["issue", "list"]
    assert run_gh.call_args_list[1].args[0][:2] == ["issue", "create"]


def test_dispatch_open_issue_flag_forces_issue_at_warn(slack_env, gh_present):
    list_proc = MagicMock(returncode=0, stdout="[]", stderr="")
    create_proc = MagicMock(returncode=0, stdout="", stderr="")

    def fake_run_gh(args):
        if args[1] == "list":
            return list_proc
        return create_proc

    with patch.object(alert.urllib.request, "urlopen", return_value=_ok_response()), \
         patch.object(alert, "_run_gh", side_effect=fake_run_gh) as run_gh:
        result = alert.dispatch("WARN", "tunable threshold", {}, open_issue=True)

    assert result["issue"] is True
    assert run_gh.call_count == 2


def test_dispatch_invalid_severity_raises():
    with pytest.raises(ValueError):
        alert.dispatch("FATAL", "x", {})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Slack transport edge cases
# ---------------------------------------------------------------------------


def test_slack_skipped_when_env_unset(monkeypatch, gh_present):
    monkeypatch.delenv(alert.SLACK_ENV, raising=False)
    with patch.object(alert.urllib.request, "urlopen") as urlopen:
        result = alert.dispatch("WARN", "no webhook", {})
    assert result["slack"] is False
    urlopen.assert_not_called()


def test_slack_failure_does_not_raise_from_dispatch(slack_env, gh_present):
    import urllib.error

    boom = urllib.error.URLError("connection refused")
    with patch.object(alert.urllib.request, "urlopen", side_effect=boom):
        result = alert.dispatch("WARN", "transport down", {})
    assert result == {"slack": False, "issue": False}


def test_slack_non_2xx_returns_false(slack_env, gh_present):
    resp = MagicMock()
    resp.__enter__ = lambda self: self
    resp.__exit__ = lambda *a, **kw: None
    resp.status = 500
    with patch.object(alert.urllib.request, "urlopen", return_value=resp):
        result = alert.dispatch("WARN", "5xx", {})
    assert result["slack"] is False


def test_slack_payload_contains_severity_and_context(slack_env, gh_present):
    captured = {}

    def fake_urlopen(req, timeout):  # noqa: ARG001
        captured["body"] = req.data
        return _ok_response()

    with patch.object(alert.urllib.request, "urlopen", side_effect=fake_urlopen):
        alert.dispatch("WARN", "drift trending", {"rate": 0.04, "topic": "cardio"})

    payload = json.loads(captured["body"])
    text = payload["text"]
    assert "[WARN]" in text
    assert "drift trending" in text
    assert "rate: 0.04" in text
    assert "topic: cardio" in text


# ---------------------------------------------------------------------------
# GitHub-issue transport edge cases
# ---------------------------------------------------------------------------


def test_issue_skipped_when_gh_absent(slack_env, monkeypatch):
    monkeypatch.setattr(alert.shutil, "which", lambda name: None)
    with patch.object(alert.urllib.request, "urlopen", return_value=_ok_response()), \
         patch.object(alert, "_run_gh") as run_gh:
        result = alert.dispatch("ERROR", "no gh available", {})

    assert result["slack"] is True
    assert result["issue"] is False
    run_gh.assert_not_called()


def test_existing_open_issue_is_commented_not_duplicated(slack_env, gh_present):
    list_proc = MagicMock(
        returncode=0, stdout=json.dumps([{"number": 42}]), stderr=""
    )
    comment_proc = MagicMock(returncode=0, stdout="", stderr="")

    calls: list[list[str]] = []

    def fake_run_gh(args):
        calls.append(args)
        if args[1] == "list":
            return list_proc
        if args[1] == "comment":
            return comment_proc
        raise AssertionError(f"unexpected gh args: {args}")

    with patch.object(alert.urllib.request, "urlopen", return_value=_ok_response()), \
         patch.object(alert, "_run_gh", side_effect=fake_run_gh):
        result = alert.dispatch("ERROR", "still drifting", {"rate": 0.07})

    assert result["issue"] is True
    # list + comment, no create
    assert [c[1] for c in calls] == ["list", "comment"]
    # Comment targets issue 42
    assert "42" in calls[1]


def test_issue_create_failure_returns_false(slack_env, gh_present):
    list_proc = MagicMock(returncode=0, stdout="[]", stderr="")
    create_proc = MagicMock(returncode=1, stdout="", stderr="gh: auth required")

    def fake_run_gh(args):
        return list_proc if args[1] == "list" else create_proc

    with patch.object(alert.urllib.request, "urlopen", return_value=_ok_response()), \
         patch.object(alert, "_run_gh", side_effect=fake_run_gh):
        result = alert.dispatch("ERROR", "auth broken", {})

    assert result["slack"] is True
    assert result["issue"] is False


def test_issue_body_includes_json_context(slack_env, gh_present):
    list_proc = MagicMock(returncode=0, stdout="[]", stderr="")
    create_proc = MagicMock(returncode=0, stdout="", stderr="")
    captured_args: list[list[str]] = []

    def fake_run_gh(args):
        captured_args.append(args)
        return list_proc if args[1] == "list" else create_proc

    with patch.object(alert.urllib.request, "urlopen", return_value=_ok_response()), \
         patch.object(alert, "_run_gh", side_effect=fake_run_gh):
        alert.dispatch("ERROR", "rate alert", {"rate": 0.07, "topic_max": 60})

    create_call = captured_args[1]
    body_idx = create_call.index("--body") + 1
    body = create_call[body_idx]
    assert "rate alert" in body
    assert '"rate": 0.07' in body
    assert '"topic_max": 60' in body
    # Title carries the message prefix
    title_idx = create_call.index("--title") + 1
    assert "rate alert" in create_call[title_idx]


# ---------------------------------------------------------------------------
# Severity table mapping
# ---------------------------------------------------------------------------


def test_severity_table_covers_all_conditions():
    """Every Condition must have a mapped severity."""
    for cond in severity.Condition:
        assert cond in severity.SEVERITY_TABLE
        assert severity.SEVERITY_TABLE[cond] in {"WARN", "ERROR"}


def test_severity_for_table_d11_values():
    assert severity.severity_for(severity.Condition.UNCOVERED_RATE_ALERT) == "ERROR"
    assert severity.severity_for(severity.Condition.UNCOVERED_RATE_WARN) == "WARN"
    assert severity.severity_for(severity.Condition.LOW_CONFIDENCE_TOPIC_MAX) == "ERROR"
    assert severity.severity_for(severity.Condition.HOT_PATH_LOCK_COLLISION) == "WARN"
    assert severity.severity_for(severity.Condition.BEDROCK_SERVICE_OUTAGE) == "ERROR"
    assert severity.severity_for(severity.Condition.STAGE_FAILED_RETRY_EXHAUSTED) == "ERROR"


def test_cold_run_recommended_only_for_spec_section9_conditions():
    assert severity.cold_run_recommended(
        [severity.Condition.UNCOVERED_RATE_ALERT]
    ) is True
    assert severity.cold_run_recommended(
        [severity.Condition.LOW_CONFIDENCE_TOPIC_MAX]
    ) is True
    # ERROR but not cold-run-triggering
    assert severity.cold_run_recommended(
        [severity.Condition.STAGE_FAILED_RETRY_EXHAUSTED]
    ) is False
    assert severity.cold_run_recommended([]) is False


# ---------------------------------------------------------------------------
# classify_drift_evaluation: facts → conditions
# ---------------------------------------------------------------------------


def _eval(
    *,
    uncovered_rate: float = 0.0,
    max_count: int = 0,
    triggered: list[str] | None = None,
) -> DriftEvaluation:
    return DriftEvaluation(
        window_start="2026-04-28T12:00:00Z",
        window_end="2026-05-12T12:00:00Z",
        drift_window_days=14,
        uncovered_count=0,
        low_confidence_count=0,
        stage_failed_count=0,
        new_pmid_count=100,
        uncovered_rate=uncovered_rate,
        low_confidence_max_topic=None,
        low_confidence_max_count=max_count,
        triggered_thresholds=triggered or [],
    )


def test_classify_uncovered_alert_band():
    ev = _eval(uncovered_rate=0.07, triggered=["uncovered_rate_alert"])
    conds = severity.classify_drift_evaluation(ev)
    assert severity.Condition.UNCOVERED_RATE_ALERT in conds
    assert severity.Condition.UNCOVERED_RATE_WARN not in conds


def test_classify_uncovered_warn_band():
    # 4% — above warn floor (3%), below alert floor (5%)
    ev = _eval(uncovered_rate=0.04)
    conds = severity.classify_drift_evaluation(ev)
    assert severity.Condition.UNCOVERED_RATE_WARN in conds
    assert severity.Condition.UNCOVERED_RATE_ALERT not in conds


def test_classify_low_confidence_alert_band():
    ev = _eval(max_count=55, triggered=["low_confidence_topic_max"])
    conds = severity.classify_drift_evaluation(ev)
    assert severity.Condition.LOW_CONFIDENCE_TOPIC_MAX in conds


def test_classify_low_confidence_warn_band():
    ev = _eval(max_count=35)
    conds = severity.classify_drift_evaluation(ev)
    assert severity.Condition.LOW_CONFIDENCE_TOPIC_WARN in conds
    assert severity.Condition.LOW_CONFIDENCE_TOPIC_MAX not in conds


def test_classify_stage_failures_in_window():
    ev = _eval(triggered=["stage_failures_in_window"])
    conds = severity.classify_drift_evaluation(ev)
    assert severity.Condition.STAGE_FAILED_RETRY_EXHAUSTED in conds


def test_classify_clean_window_returns_empty():
    ev = _eval()
    assert severity.classify_drift_evaluation(ev) == []

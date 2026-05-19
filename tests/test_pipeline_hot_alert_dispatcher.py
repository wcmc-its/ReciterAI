"""Unit tests for the hot-path AlertDispatcher Lambda (#121).

`pipeline_hot/handlers/alert_dispatcher.py` is invoked by the state
machine's `NotifyError` and `NotifyStageSkipAnomaly` states. It maps the
event onto the Teams transport `pipeline_enrichment.alerting.alert`; for
the `hot_path.stage_skip` source it first runs `stage_skip.resolve` to
classify WARN vs ERROR. Best-effort, never raising.
"""

from __future__ import annotations

import pytest

import pipeline_hot.handlers.alert_dispatcher as ad


@pytest.fixture
def captured(monkeypatch):
    """Replace alerting.alert with a recorder; return the call log."""
    calls: list[dict] = []

    def fake_alert(severity, title, message, context=None, *, mention=True):
        calls.append(
            {
                "severity": severity,
                "title": title,
                "message": message,
                "context": context,
                "mention": mention,
            }
        )
        return True

    monkeypatch.setattr(ad.alerting, "alert", fake_alert)
    return calls


def test_minimal_event_defaults_all_optional_fields(captured):
    """An event with no title / context / mention — the NotifyError shape —
    dispatches with sane defaults (title falls back to source)."""
    event = {
        "severity": "ERROR",
        "source": "hot_path",
        "message": "States.TaskFailed in Score",
        "execution_arn": "arn:aws:states:::execution:reciterai-hot-path:R9",
    }
    result = ad.handler(event)

    call = captured[0]
    assert call["severity"] == "ERROR"
    assert call["title"] == "hot_path"
    assert call["mention"] is True
    assert call["context"] == {
        "source": "hot_path",
        "execution_arn": "arn:aws:states:::execution:reciterai-hot-path:R9",
    }
    assert result["delivered"] is True


def test_severity_defaults_to_error_on_a_malformed_event(captured):
    """A missing severity escalates to ERROR rather than silently downgrading."""
    ad.handler({"message": "no severity given"})
    assert captured[0]["severity"] == "ERROR"


def test_mention_passes_through(captured):
    ad.handler({"severity": "WARN", "message": "m", "mention": False})
    assert captured[0]["mention"] is False


def test_delivered_reflects_the_transport_result(monkeypatch):
    monkeypatch.setattr(ad.alerting, "alert", lambda *a, **k: False)
    result = ad.handler({"severity": "WARN", "message": "m"})
    assert result == {"status": "dispatched", "delivered": False, "transport": "teams"}


def test_handler_never_raises_when_the_transport_raises(monkeypatch):
    """Alerting is best-effort: a transport exception is swallowed so the
    state machine's terminal state still succeeds."""

    def boom(*a, **k):
        raise RuntimeError("teams webhook exploded")

    monkeypatch.setattr(ad.alerting, "alert", boom)
    result = ad.handler({"severity": "WARN", "message": "m"})
    assert result == {"status": "dispatched", "delivered": False, "transport": "teams"}


def test_stage_skip_source_is_resolved_then_dispatched(captured, monkeypatch):
    """A hot_path.stage_skip event is run through stage_skip.resolve first;
    the resolved severity/title/message is what gets dispatched."""
    resolved = {
        "severity": "ERROR",
        "source": "hot_path.stage_skip",
        "title": "Hot path stage-skip streak",
        "message": "Hot run R3 skipped assign — 3 consecutive work-present runs.",
        "execution_arn": "arn:aws:states:::execution:reciterai-hot-path:R3",
        "context": {"run_id": "R3", "assign_skip_streak": 3},
    }
    monkeypatch.setattr(ad.stage_skip, "resolve", lambda event: resolved)

    result = ad.handler({"source": "hot_path.stage_skip", "hot_run": {"run_id": "R3"}})

    call = captured[0]
    assert call["severity"] == "ERROR"
    assert call["title"] == "Hot path stage-skip streak"
    assert "3 consecutive" in call["message"]
    assert call["context"]["assign_skip_streak"] == 3
    assert result["delivered"] is True

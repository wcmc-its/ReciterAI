"""#80 Phase 2 (PR 3) — pipeline_onboarding.finalize tests.

Covers the complete-vs-partial decision, the Finalize Task handler, and the
shared notify handler.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pipeline_onboarding.finalize as fin


# ---------------------------------------------------------------------------
# decide_terminal_status — pure
# ---------------------------------------------------------------------------


def test_all_complete_is_complete():
    status, incomplete = fin.decide_terminal_status(
        ["1", "2"], {"1": "complete", "2": "complete"}
    )
    assert status == "complete"
    assert incomplete == []


def test_any_failed_is_partial():
    status, incomplete = fin.decide_terminal_status(
        ["1", "2", "3"], {"1": "complete", "2": "failed", "3": "complete"}
    )
    assert status == "partial"
    assert incomplete == ["2"]


def test_missing_processing_row_counts_as_incomplete():
    status, incomplete = fin.decide_terminal_status(
        ["1", "2"], {"1": "complete"}  # 2 has no PROCESSING# row
    )
    assert status == "partial"
    assert incomplete == ["2"]


def test_quarantined_counts_as_incomplete():
    status, incomplete = fin.decide_terminal_status(["1"], {"1": "quarantined"})
    assert status == "partial"
    assert incomplete == ["1"]


# ---------------------------------------------------------------------------
# DDB-typed envelope readers
# ---------------------------------------------------------------------------


def test_ddb_str_reads_and_defaults():
    assert fin._ddb_str({"k": {"S": "v"}}, "k") == "v"
    assert fin._ddb_str({}, "missing") == ""
    # A non-string attribute returns the default rather than mis-reading.
    assert fin._ddb_str({"k": {"N": "5"}}, "k") == ""


def test_ddb_decimal_reads_and_defaults():
    assert fin._ddb_decimal({"c": {"N": "0.25"}}, "c") == Decimal("0.25")
    assert fin._ddb_decimal({}, "c") == Decimal("0")
    assert fin._ddb_decimal({"c": {"S": "x"}}, "c") == Decimal("0")


# ---------------------------------------------------------------------------
# handler — Finalize Task
# ---------------------------------------------------------------------------

_STARTED = "2026-05-17T00:00:00Z"


def _finalize_event(pmids):
    return {
        "cwid": "abc1234",
        "pmids": pmids,
        "run_id": "run-1",
        "started_at": _STARTED,
        "input_hash": "ih-123",
        "net_work_count": len(pmids),
        "projected_cost_usd": "~$0.10",
        "score_envelope": {
            "input_hash": {"S": "score-ih"},
            "cost_observed_usd": {"N": "0.01"},
        },
        "assign_envelope": {
            "input_hash": {"S": "skipped:onboarding-assign-stub"},
        },
        "top_topic_envelope": {
            "input_hash": {"S": "tt-ih"},
            "cost_observed_usd": {"N": "0.02"},
        },
        "rollup_envelope": {
            "input_hash": {"S": "ru-ih"},
            "cost_observed_usd": {"N": "0"},
        },
    }


def _wire_finalize(monkeypatch, status_map):
    monkeypatch.setattr(fin, "get_dynamo_client", lambda: MagicMock())
    monkeypatch.setattr(
        fin, "get_processing_status", lambda *a, **k: dict(status_map)
    )
    calls: list = []
    monkeypatch.setattr(
        fin.alerting, "alert", lambda *a, **k: calls.append(a) or True
    )
    return calls


def test_finalize_complete_no_alert(monkeypatch):
    alerts = _wire_finalize(monkeypatch, {"1": "complete", "2": "complete"})
    env = fin.handler(_finalize_event(["1", "2"]))
    assert env["PK"] == {"S": "STAGE#onboarding#cwid:abc1234"}
    assert env["status"] == {"S": "complete"}
    assert env["pmid_count"] == {"N": "2"}
    # A complete run does not alert.
    assert alerts == []


def test_finalize_partial_alerts(monkeypatch):
    alerts = _wire_finalize(monkeypatch, {"1": "complete", "2": "failed"})
    env = fin.handler(_finalize_event(["1", "2"]))
    assert env["status"] == {"S": "partial"}
    assert env["partial_failure_count"] == {"N": "1"}
    assert env["failed_pmids"] == {"L": [{"S": "2"}]}
    assert len(alerts) == 1
    assert alerts[0][0] == "WARN"


def test_finalize_sums_stage_cost(monkeypatch):
    _wire_finalize(monkeypatch, {"1": "complete"})
    env = fin.handler(_finalize_event(["1"]))
    # 0.01 (score) + 0.02 (top_topic) + 0 (rollup); the assign stub none.
    assert env["cost_observed_usd"] == {"N": "0.03"}


def test_finalize_records_stage_input_hashes(monkeypatch):
    _wire_finalize(monkeypatch, {"1": "complete"})
    env = fin.handler(_finalize_event(["1"]))
    hashes = env["stage_input_hashes"]["M"]
    assert hashes["score"] == {"S": "score-ih"}
    assert hashes["rollup"] == {"S": "ru-ih"}
    assert hashes["assign"] == {"S": "skipped:onboarding-assign-stub"}


def test_finalize_returns_ddb_typed_envelope(monkeypatch):
    """The return must be DDB attribute-typed so WriteOnboardingFinal's
    Item.$ can consume it directly."""
    _wire_finalize(monkeypatch, {"1": "complete"})
    env = fin.handler(_finalize_event(["1"]))
    assert env["SK"] == {"S": f"RUN#{_STARTED}"}
    assert env["stage"] == {"S": "onboarding"}
    assert env["run_id"] == {"S": "run-1"}
    assert env["input_hash"] == {"S": "ih-123"}


# ---------------------------------------------------------------------------
# notify_handler
# ---------------------------------------------------------------------------


def _wire_notify(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        fin.alerting, "alert",
        lambda sev, title, msg, ctx=None, **k: calls.append(
            (sev, title, msg, ctx)
        ) or True,
    )
    return calls


def test_notify_deferred_warns(monkeypatch):
    calls = _wire_notify(monkeypatch)
    fin.notify_handler(
        {
            "kind": "deferred",
            "cwid": "abc",
            "reason": "2 of 3 missing synopsis",
            "run_id": "r",
        }
    )
    assert calls[0][0] == "WARN"
    assert "deferred" in calls[0][1].lower()


def test_notify_cost_exceeded_embeds_override(monkeypatch):
    monkeypatch.delenv("RECITERAI_ONBOARDING_STATE_MACHINE_ARN", raising=False)
    calls = _wire_notify(monkeypatch)
    fin.notify_handler(
        {
            "kind": "cost_exceeded",
            "cwid": "abc",
            "reason": "320 PMIDs",
            "run_id": "r",
        }
    )
    assert calls[0][0] == "WARN"
    assert "allow_cost_override" in calls[0][2]


def test_notify_failed_is_error_severity(monkeypatch):
    calls = _wire_notify(monkeypatch)
    fin.notify_handler(
        {
            "kind": "failed",
            "cwid": "abc",
            "reason": "States.TaskFailed",
            "run_id": "r",
        }
    )
    assert calls[0][0] == "ERROR"


def test_notify_returns_status(monkeypatch):
    _wire_notify(monkeypatch)
    out = fin.notify_handler({"kind": "deferred", "cwid": "abc"})
    assert out["kind"] == "deferred"
    assert out["cwid"] == "abc"


def test_override_hint_with_arn(monkeypatch):
    monkeypatch.setenv(
        "RECITERAI_ONBOARDING_STATE_MACHINE_ARN",
        "arn:aws:states:us-east-1:665083158573:stateMachine:reciterai-onboarding",
    )
    hint = fin._override_hint("abc1234")
    assert "start-execution" in hint
    assert "abc1234" in hint
    assert "allow_cost_override" in hint


def test_override_hint_without_arn(monkeypatch):
    monkeypatch.delenv("RECITERAI_ONBOARDING_STATE_MACHINE_ARN", raising=False)
    hint = fin._override_hint("abc1234")
    assert "allow_cost_override" in hint

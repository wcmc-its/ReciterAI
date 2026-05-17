"""#80 Phase 2 (PR 3) — pipeline_onboarding.orchestrator tests.

Covers the pure routing decision (`evaluate_onboarding`), the input-hash
and cost-estimate helpers, and the Lambda handler's I/O wiring.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

import pipeline_onboarding.orchestrator as orch
import score_publications

# Shared kwargs for the decision-logic tests (allow_cost_override False).
_BASE = dict(
    cwid="abc1234",
    run_id="run-1",
    started_at="2026-05-17T00:00:00Z",
    allow_cost_override=False,
)


# ---------------------------------------------------------------------------
# evaluate_onboarding — pure routing decision
# ---------------------------------------------------------------------------


def test_no_pmids_routes_skipped():
    result = orch.evaluate_onboarding(
        **_BASE, pmids=[], synopsis_missing=[], processing_status={},
    )
    assert result["status"] == orch.ROUTE_SKIPPED
    assert result["input"] == {}
    env = result["terminal_envelope"]
    assert env["status"] == {"S": "skipped"}
    assert "no accepted publications" in env["skip_reason"]["S"]


def test_synopsis_missing_routes_deferred():
    result = orch.evaluate_onboarding(
        **_BASE,
        pmids=["1", "2", "3"],
        synopsis_missing=["2"],
        processing_status={},
    )
    assert result["status"] == orch.ROUTE_DEFERRED
    env = result["terminal_envelope"]
    assert env["status"] == {"S": "deferred"}
    assert env["deferred_pmids"] == {"L": [{"S": "2"}]}
    assert "synopsis" in env["deferred_reason"]["S"].lower()


def test_all_scored_routes_skipped():
    result = orch.evaluate_onboarding(
        **_BASE,
        pmids=["1", "2"],
        synopsis_missing=[],
        processing_status={"1": "complete", "2": "complete"},
    )
    assert result["status"] == orch.ROUTE_SKIPPED
    assert "already scored" in result["terminal_envelope"]["skip_reason"]["S"]


def test_cost_guard_trips_above_threshold():
    big = [str(i) for i in range(score_publications.ONBOARDING_COST_GUARD_MAX_PMIDS + 1)]
    result = orch.evaluate_onboarding(
        **_BASE, pmids=big, synopsis_missing=[], processing_status={},
    )
    assert result["status"] == orch.ROUTE_COST_EXCEEDED
    env = result["terminal_envelope"]
    # Modelled as status=failed + error_code (D-COSTSTATUS), not a 6th status.
    assert env["status"] == {"S": "failed"}
    assert env["error_code"] == {"S": orch.ERROR_CODE_COST_GUARD}
    assert "failure_details" in env


def test_cost_guard_override_routes_ready():
    big = [str(i) for i in range(score_publications.ONBOARDING_COST_GUARD_MAX_PMIDS + 1)]
    result = orch.evaluate_onboarding(
        cwid="abc1234",
        run_id="run-1",
        started_at="2026-05-17T00:00:00Z",
        allow_cost_override=True,
        pmids=big,
        synopsis_missing=[],
        processing_status={},
    )
    assert result["status"] == orch.ROUTE_READY


def test_ready_returns_work_payload():
    result = orch.evaluate_onboarding(
        **_BASE,
        pmids=["1", "2", "3"],
        synopsis_missing=[],
        processing_status={"1": "complete"},  # 2 of 3 are net work
    )
    assert result["status"] == orch.ROUTE_READY
    work = result["input"]
    assert work["cwid"] == "abc1234"
    assert work["pmids"] == ["1", "2", "3"]
    assert work["net_work_count"] == 2
    assert work["allow_cost_override"] is False
    assert work["input_hash"]
    assert work["projected_cost_usd"].startswith("~$")
    # The ready path leaves terminal_envelope unused.
    assert result["terminal_envelope"] == {}


def test_synopsis_check_precedes_cost_guard():
    """A run that is both synopsis-incomplete and oversized defers — the
    synopsis precondition is evaluated before the cost guard."""
    big = [str(i) for i in range(score_publications.ONBOARDING_COST_GUARD_MAX_PMIDS + 50)]
    result = orch.evaluate_onboarding(
        **_BASE, pmids=big, synopsis_missing=["7"], processing_status={},
    )
    assert result["status"] == orch.ROUTE_DEFERRED


# ---------------------------------------------------------------------------
# compute_onboarding_input_hash
# ---------------------------------------------------------------------------


def test_input_hash_is_order_independent():
    a = orch.compute_onboarding_input_hash("cwid1", ["3", "1", "2"])
    b = orch.compute_onboarding_input_hash("cwid1", ["1", "2", "3"])
    assert a == b


def test_input_hash_varies_by_cwid_and_set():
    base = orch.compute_onboarding_input_hash("cwid1", ["1", "2"])
    assert base != orch.compute_onboarding_input_hash("cwid2", ["1", "2"])
    assert base != orch.compute_onboarding_input_hash("cwid1", ["1", "2", "3"])


# ---------------------------------------------------------------------------
# estimate_onboarding_cost
# ---------------------------------------------------------------------------


def test_cost_estimate_zero_for_no_work():
    assert orch.estimate_onboarding_cost(0) == Decimal("0")


def test_cost_estimate_positive_and_scales():
    one = orch.estimate_onboarding_cost(1)
    hundred = orch.estimate_onboarding_cost(100)
    assert one is not None and one > 0
    assert hundred > one


# ---------------------------------------------------------------------------
# handler — I/O wiring
# ---------------------------------------------------------------------------


def _wire(monkeypatch, *, pmids, missing=None, status_map=None):
    """Stub the orchestrator's ReciterDB / DynamoDB / Teams dependencies."""
    monkeypatch.setattr(orch, "get_pmids_for_cwid", lambda cwid: list(pmids))
    monkeypatch.setattr(
        orch, "check_synopsis_coverage",
        lambda ps: {"present": [], "missing": list(missing or [])},
    )
    monkeypatch.setattr(orch, "get_dynamo_client", lambda: MagicMock())
    monkeypatch.setattr(
        orch, "get_processing_status", lambda *a, **k: dict(status_map or {}),
    )
    calls: list = []
    monkeypatch.setattr(
        orch.alerting, "alert", lambda *a, **k: calls.append((a, k)) or True,
    )
    return calls


def test_handler_requires_cwid():
    with pytest.raises(ValueError, match="no 'cwid'"):
        orch.handler({"execution_input": {}, "run_id": "r"})


def test_handler_ready_path_and_cost_preview(monkeypatch):
    alert_calls = _wire(monkeypatch, pmids=["1", "2", "3"])
    result = orch.handler(
        {"execution_input": {"cwid": "abc1234"}, "run_id": "run-9"}
    )
    assert result["status"] == orch.ROUTE_READY
    assert result["input"]["pmids"] == ["1", "2", "3"]
    assert result["input"]["run_id"] == "run-9"
    # Ready + net work > 0 emits exactly one informational cost preview.
    assert len(alert_calls) == 1
    assert alert_calls[0][0][0] == "WARN"
    assert alert_calls[0][1].get("mention") is False


def test_handler_deferred_path_emits_no_orchestrator_alert(monkeypatch):
    alert_calls = _wire(monkeypatch, pmids=["1", "2"], missing=["2"])
    result = orch.handler(
        {"execution_input": {"cwid": "abc1234"}, "run_id": "r"}
    )
    assert result["status"] == orch.ROUTE_DEFERRED
    # The deferred operator alert is the state machine's NotifyDeferred Task,
    # not the orchestrator — the orchestrator must not also alert.
    assert alert_calls == []


def test_handler_strips_cwid_whitespace(monkeypatch):
    _wire(monkeypatch, pmids=[])
    result = orch.handler(
        {"execution_input": {"cwid": "  abc1234  "}, "run_id": "r"}
    )
    # A real CWID (stripped) with no publications routes to skipped, and the
    # terminal row's PK carries the trimmed CWID.
    assert result["status"] == orch.ROUTE_SKIPPED
    assert result["terminal_envelope"]["PK"] == {
        "S": "STAGE#onboarding#cwid:abc1234"
    }

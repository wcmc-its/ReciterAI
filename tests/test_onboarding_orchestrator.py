"""#80 Phase 2 — pipeline_onboarding.orchestrator tests.

Covers the pure routing decision (`evaluate_onboarding`), the input-hash,
cost-estimate, and rollup-completeness helpers, and the Lambda handler's
I/O wiring. The synopsis precondition is retired (#112) — onboarding
generates synopses inline in the Enrich stage — so the orchestrator routes
only `ready` / `skipped` / `cost_exceeded`. A net-work-empty run routes
`ready` (a #115 recovery re-run) rather than `skipped` when the CWID's
rollup is missing or stale.
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
        **_BASE, pmids=[], processing_status={}, rollup_input_pmid_set=None
    )
    assert result["status"] == orch.ROUTE_SKIPPED
    assert result["input"] == {}
    env = result["terminal_envelope"]
    assert env["status"] == {"S": "skipped"}
    assert "no accepted publications" in env["skip_reason"]["S"]


def test_all_scored_and_rolled_up_routes_skipped():
    """Net work empty AND the rollup covers the exact accepted set -> a
    true idempotent no-op."""
    result = orch.evaluate_onboarding(
        **_BASE,
        pmids=["1", "2"],
        processing_status={"1": "complete", "2": "complete"},
        rollup_input_pmid_set={"1", "2"},
    )
    assert result["status"] == orch.ROUTE_SKIPPED
    assert (
        "already scored and rolled up"
        in result["terminal_envelope"]["skip_reason"]["S"]
    )


def test_all_scored_but_rollup_missing_routes_ready_recovery():
    """#115: every PMID is scored but the CWID has no rollup row at all —
    a prior cascade died before Rollup, or the hot path scored the
    publications and onboarding never ran. Route `ready` for a recovery
    re-run, not `skipped`."""
    result = orch.evaluate_onboarding(
        **_BASE,
        pmids=["1", "2"],
        processing_status={"1": "complete", "2": "complete"},
        rollup_input_pmid_set=None,
    )
    assert result["status"] == orch.ROUTE_READY
    work = result["input"]
    assert work["recovery"] is True
    assert work["net_work_count"] == 0
    assert work["pmids"] == ["1", "2"]
    # The ready path leaves terminal_envelope unused.
    assert result["terminal_envelope"] == {}


def test_all_scored_but_rollup_stale_routes_ready_recovery():
    """#115: every PMID is scored and a rollup exists, but its
    input_pmid_set != the live accepted set (ReCiter churn, or a prior
    rollup over a different set). Route `ready` for a recovery re-run."""
    result = orch.evaluate_onboarding(
        **_BASE,
        pmids=["1", "2", "3"],
        processing_status={
            "1": "complete", "2": "complete", "3": "complete",
        },
        rollup_input_pmid_set={"1", "2"},  # missing "3" — stale
    )
    assert result["status"] == orch.ROUTE_READY
    assert result["input"]["recovery"] is True
    assert result["input"]["net_work_count"] == 0


def test_recovery_ready_payload_reports_zero_cost():
    """A recovery re-run does no scoring — its projected cost is ~$0, and
    the work payload still carries the full PMID set for the cascade."""
    result = orch.evaluate_onboarding(
        **_BASE,
        pmids=["1", "2"],
        processing_status={"1": "complete", "2": "complete"},
        rollup_input_pmid_set=None,
    )
    work = result["input"]
    assert work["projected_cost_usd"] == "~$0"
    assert work["cwid"] == "abc1234"
    assert work["input_hash"]


def test_cost_guard_trips_above_threshold():
    big = [str(i) for i in range(score_publications.ONBOARDING_COST_GUARD_MAX_PMIDS + 1)]
    result = orch.evaluate_onboarding(
        **_BASE, pmids=big, processing_status={}, rollup_input_pmid_set=None
    )
    assert result["status"] == orch.ROUTE_COST_EXCEEDED
    env = result["terminal_envelope"]
    # Modelled as status=failed + error_code (D-COSTSTATUS), not a 5th status.
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
        processing_status={},
        rollup_input_pmid_set=None,
    )
    assert result["status"] == orch.ROUTE_READY


def test_ready_returns_work_payload():
    result = orch.evaluate_onboarding(
        **_BASE,
        pmids=["1", "2", "3"],
        processing_status={"1": "complete"},  # 2 of 3 are net work
        rollup_input_pmid_set=None,
    )
    assert result["status"] == orch.ROUTE_READY
    work = result["input"]
    assert work["cwid"] == "abc1234"
    assert work["pmids"] == ["1", "2", "3"]
    assert work["net_work_count"] == 2
    assert work["allow_cost_override"] is False
    assert work["input_hash"]
    assert work["projected_cost_usd"].startswith("~$")
    # Net work > 0 is the normal ready path — not a #115 recovery.
    assert work["recovery"] is False
    # The ready path leaves terminal_envelope unused.
    assert result["terminal_envelope"] == {}


def test_unscored_pmids_with_missing_rollup_are_not_a_recovery():
    """The recovery branch is the net-work-empty branch only: when some
    PMIDs are unscored the run routes `ready` through the normal path even
    if no rollup exists — recovery=False, and the cost guard still applies."""
    result = orch.evaluate_onboarding(
        **_BASE,
        pmids=["1", "2"],
        processing_status={"1": "complete"},  # "2" unscored -> net work
        rollup_input_pmid_set=None,
    )
    assert result["status"] == orch.ROUTE_READY
    assert result["input"]["recovery"] is False
    assert result["input"]["net_work_count"] == 1


def test_un_synopsized_pmids_route_ready_not_deferred():
    """The synopsis precondition is retired: a CWID whose PMIDs lack a
    synopsis still routes `ready` — the Enrich stage generates them. The
    orchestrator no longer inspects synopsis coverage at all."""
    result = orch.evaluate_onboarding(
        **_BASE,
        pmids=["1", "2", "3"],
        processing_status={},
        rollup_input_pmid_set=None,
    )
    assert result["status"] == orch.ROUTE_READY
    assert not hasattr(orch, "ROUTE_DEFERRED")


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
# latest_rollup_pmid_set — the #115 rollup-completeness probe
# ---------------------------------------------------------------------------


def _rollup_table(items: list[dict]) -> MagicMock:
    """Mock boto3 Table whose `query` returns `items` regardless of args."""
    t = MagicMock()
    t.query.return_value = {"Items": items}
    return t


def test_latest_rollup_pmid_set_reads_input_pmid_set():
    table = _rollup_table([
        {
            "PK": "STAGE#rollup_by_cwid#cwid:abc1234",
            "SK": "RUN#2026-05-18T00:00:00Z",
            "status": "complete",
            "input_pmid_set": ["1", "2", "3"],
        },
    ])
    assert orch.latest_rollup_pmid_set(table, "abc1234") == {"1", "2", "3"}


def test_latest_rollup_pmid_set_none_when_no_complete_row():
    assert orch.latest_rollup_pmid_set(_rollup_table([]), "abc1234") is None


def test_latest_rollup_pmid_set_none_when_row_lacks_input_pmid_set():
    """A malformed complete rollup row with no input_pmid_set -> None, so
    the recovery check re-runs the cascade rather than wrongly skipping."""
    table = _rollup_table([
        {
            "PK": "STAGE#rollup_by_cwid#cwid:abc1234",
            "SK": "RUN#1",
            "status": "complete",
        },
    ])
    assert orch.latest_rollup_pmid_set(table, "abc1234") is None


def test_latest_rollup_pmid_set_coerces_pmids_to_str():
    """input_pmid_set values are coerced to str so the comparison against
    the accepted set is type-safe."""
    table = _rollup_table([
        {
            "PK": "STAGE#rollup_by_cwid#cwid:abc1234",
            "SK": "RUN#1",
            "status": "complete",
            "input_pmid_set": [1, 2, 3],
        },
    ])
    assert orch.latest_rollup_pmid_set(table, "abc1234") == {"1", "2", "3"}


def test_latest_rollup_pmid_set_queries_cwid_scoped_pk():
    """Regression guard: reads the per-CWID rollup partition, not GLOBAL."""
    table = _rollup_table([])
    orch.latest_rollup_pmid_set(table, "abc1234")
    kwargs = table.query.call_args.kwargs
    assert kwargs["ExpressionAttributeValues"][":pk"] == (
        "STAGE#rollup_by_cwid#cwid:abc1234"
    )


# ---------------------------------------------------------------------------
# handler — I/O wiring
# ---------------------------------------------------------------------------


def _wire(monkeypatch, *, pmids, status_map=None, rollup_pmid_set=None):
    """Stub the orchestrator's ReciterDB / DynamoDB / Teams dependencies."""
    monkeypatch.setattr(orch, "get_pmids_for_cwid", lambda cwid: list(pmids))
    monkeypatch.setattr(orch, "get_dynamo_client", lambda: MagicMock())
    monkeypatch.setattr(orch, "get_table", lambda *a, **k: MagicMock())
    monkeypatch.setattr(
        orch, "get_processing_status", lambda *a, **k: dict(status_map or {}),
    )
    monkeypatch.setattr(
        orch, "latest_rollup_pmid_set", lambda table, cwid: rollup_pmid_set,
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
    assert result["input"]["recovery"] is False
    # Ready + net work > 0 emits exactly one informational cost preview.
    assert len(alert_calls) == 1
    assert alert_calls[0][0][0] == "WARN"
    assert "cost preview" in alert_calls[0][0][1]
    assert alert_calls[0][1].get("mention") is False


def test_handler_recovery_path_alerts_no_model_cost(monkeypatch):
    """#115: a CWID whose PMIDs are all scored but whose rollup is missing
    routes `ready` with recovery set; the Teams note is worded as a
    recovery (no model cost), not a fresh-scoring cost preview."""
    alert_calls = _wire(
        monkeypatch,
        pmids=["1", "2"],
        status_map={"1": "complete", "2": "complete"},
        rollup_pmid_set=None,  # no rollup -> recover
    )
    result = orch.handler(
        {"execution_input": {"cwid": "abc1234"}, "run_id": "run-r"}
    )
    assert result["status"] == orch.ROUTE_READY
    assert result["input"]["recovery"] is True
    assert len(alert_calls) == 1
    title, message = alert_calls[0][0][1], alert_calls[0][0][2]
    assert "recovery re-run" in title
    assert "no model cost" in message
    assert alert_calls[0][1].get("mention") is False


def test_handler_skips_when_rollup_is_current(monkeypatch):
    """The handler threads the rollup snapshot into evaluate_onboarding:
    an all-scored CWID with a current rollup routes `skipped`, silently."""
    alert_calls = _wire(
        monkeypatch,
        pmids=["1", "2"],
        status_map={"1": "complete", "2": "complete"},
        rollup_pmid_set={"1", "2"},
    )
    result = orch.handler(
        {"execution_input": {"cwid": "abc1234"}, "run_id": "r"}
    )
    assert result["status"] == orch.ROUTE_SKIPPED
    assert alert_calls == []  # a skipped run is silent


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

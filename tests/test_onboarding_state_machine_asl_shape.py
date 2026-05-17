"""#80 Phase 2 (PR 3) — pipeline_onboarding/state_machine.asl.json shape contract.

Mirrors `tests/test_state_machine_asl_shape.py` for the onboarding state
machine. Pins the deploy-script ``${...}`` placeholders, the cascade
states, the Catch routing that guarantees a terminal `STAGE#onboarding#cwid`
row, and the PR-3 `AssignFanOut` stub so a future edit cannot regress them
silently — the leftover-placeholder check otherwise only fires at deploy.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ASL_PATH = REPO_ROOT / "pipeline_onboarding" / "state_machine.asl.json"


@pytest.fixture(scope="module")
def asl_raw() -> str:
    return ASL_PATH.read_text()


@pytest.fixture(scope="module")
def asl(asl_raw) -> dict:
    return json.loads(asl_raw)


# The deploy script (PR 6) substitutes exactly these. PR 3's Assign stage is
# a Pass-state stub, so there is deliberately NO ${AssignLambdaArn} — PR 4
# adds it with the real per-topic fan-out.
EXPECTED_PLACEHOLDERS = {
    "${OnboardingOrchestratorLambdaArn}",
    "${ScoreLambdaArn}",
    "${TopTopicLambdaArn}",
    "${RollupLambdaArn}",
    "${OnboardingFinalizeLambdaArn}",
    "${OnboardingNotifyLambdaArn}",
}

# Lambda Task states — each must Retry on Lambda service errors and Catch
# States.ALL to the terminal failed-row writer.
LAMBDA_TASK_STATES = ("Orchestrate", "Score", "TopTopic", "Rollup", "Finalize")


def test_asl_placeholders_match_deploy_script(asl_raw):
    found = set(re.findall(r"\$\{[A-Za-z0-9_]+\}", asl_raw))
    assert found == EXPECTED_PLACEHOLDERS, (
        f"placeholder drift: expected={EXPECTED_PLACEHOLDERS}, found={found}"
    )


def test_no_assign_lambda_placeholder(asl_raw):
    """Assign is a Pass-state stub in PR 3; the real per-topic Lambda
    fan-out (and its placeholder) is PR 4."""
    assert "${AssignLambdaArn}" not in asl_raw


def test_asl_is_valid_json(asl):
    assert "StartAt" in asl
    assert "States" in asl


def test_state_machine_starts_at_orchestrate(asl):
    assert asl["StartAt"] == "Orchestrate"


def test_required_states_present(asl):
    states = asl["States"]
    for required in (
        "Orchestrate",
        "CheckProceed",
        "WriteOnboardingDeferred",
        "NotifyDeferred",
        "WriteOnboardingSkipped",
        "WriteOnboardingCostExceeded",
        "NotifyCostExceeded",
        "Score",
        "WriteScoreStageRow",
        "AssignFanOut",
        "TopTopic",
        "WriteTopTopicStageRow",
        "Rollup",
        "WriteRollupStageRow",
        "Finalize",
        "WriteOnboardingFinal",
        "WriteOnboardingFailed",
        "NotifyFailed",
        "End",
    ):
        assert required in states, f"missing state: {required}"


def test_every_lambda_task_catches_to_write_onboarding_failed(asl):
    """A crash mid-handler must not lose the terminal signal: every Lambda
    Task Catches States.ALL to WriteOnboardingFailed."""
    for name in LAMBDA_TASK_STATES:
        state = asl["States"][name]
        assert state["Type"] == "Task"
        catch = state.get("Catch") or []
        targets = [c.get("Next") for c in catch]
        assert "WriteOnboardingFailed" in targets, (
            f"{name} Catch does not route to WriteOnboardingFailed: {targets}"
        )


def test_every_lambda_task_retries_service_errors(asl):
    for name in LAMBDA_TASK_STATES:
        retry = asl["States"][name].get("Retry") or []
        assert retry, f"{name} has no Retry block"
        assert "Lambda.ServiceException" in retry[0]["ErrorEquals"]


def test_check_proceed_routes_three_terminals(asl):
    """CheckProceed routes deferred/skipped/cost_exceeded to their writers;
    Default (ready) proceeds into the Score cascade."""
    choice = asl["States"]["CheckProceed"]
    assert choice["Type"] == "Choice"
    assert choice["Default"] == "Score"
    routes = {c["StringEquals"]: c["Next"] for c in choice["Choices"]}
    assert routes == {
        "deferred": "WriteOnboardingDeferred",
        "skipped": "WriteOnboardingSkipped",
        "cost_exceeded": "WriteOnboardingCostExceeded",
    }
    for c in choice["Choices"]:
        assert c["Variable"] == "$.orchestrate.status"


def test_known_status_writers_consume_orchestrator_envelope(asl):
    """The deferred/skipped/cost-exceeded rows are written from the
    orchestrator's pre-typed terminal_envelope (D-07 crash-safety)."""
    for name in (
        "WriteOnboardingDeferred",
        "WriteOnboardingSkipped",
        "WriteOnboardingCostExceeded",
    ):
        state = asl["States"][name]
        assert state["Resource"] == "arn:aws:states:::dynamodb:putItem"
        assert state["Parameters"]["Item.$"] == "$.orchestrate.terminal_envelope"


def test_skipped_terminal_is_silent(asl):
    """skipped is an idempotent no-op — straight to End, no notification."""
    assert asl["States"]["WriteOnboardingSkipped"]["Next"] == "End"


def test_deferred_and_cost_exceeded_route_to_notify(asl):
    assert asl["States"]["WriteOnboardingDeferred"]["Next"] == "NotifyDeferred"
    assert (
        asl["States"]["WriteOnboardingCostExceeded"]["Next"]
        == "NotifyCostExceeded"
    )


def test_assign_fan_out_is_pass_stub(asl):
    """PR 3 ships Assign as a Pass-state stub injecting a typed skipped
    envelope; PR 4 replaces it with a per-topic Map."""
    state = asl["States"]["AssignFanOut"]
    assert state["Type"] == "Pass"
    assert state["ResultPath"] == "$.assign_envelope"
    assert state["Next"] == "TopTopic"
    result = state["Result"]
    assert result["status"] == {"S": "skipped"}
    assert result["input_hash"]["S"] == "skipped:onboarding-assign-stub"


def test_score_consumes_orchestrate_input(asl):
    """Score receives the orchestrator's work payload; its {pmids} key
    routes the reused hot handler to score_publications --pmids."""
    state = asl["States"]["Score"]
    assert state["Resource"] == "${ScoreLambdaArn}"
    assert state["InputPath"] == "$.orchestrate.input"
    assert state["ResultPath"] == "$.score_envelope"


def test_top_topic_consumes_pmids(asl):
    params = asl["States"]["TopTopic"]["Parameters"]
    assert params["delta_pmids.$"] == "$.orchestrate.input.pmids"


def test_rollup_consumes_cwid(asl):
    """Rollup's {cwid} event routes the reused hot handler to the
    PMID-aware rollup_by_cwid --cwid path (PR 2 / #90)."""
    params = asl["States"]["Rollup"]["Parameters"]
    assert params["cwid.$"] == "$.orchestrate.input.cwid"


def test_stage_row_writers_pass_typed_envelopes(asl):
    """The per-stage rows are the handler envelopes, fed straight in."""
    states = asl["States"]
    assert states["WriteScoreStageRow"]["Parameters"]["Item.$"] == "$.score_envelope"
    assert (
        states["WriteTopTopicStageRow"]["Parameters"]["Item.$"]
        == "$.top_topic_envelope"
    )
    assert states["WriteRollupStageRow"]["Parameters"]["Item.$"] == "$.rollup_envelope"
    assert (
        states["WriteOnboardingFinal"]["Parameters"]["Item.$"]
        == "$.finalize_envelope"
    )


def test_cascade_order(asl):
    """The ready cascade: Score -> stage row -> AssignFanOut -> TopTopic ->
    stage row -> Rollup -> stage row -> Finalize -> final row."""
    states = asl["States"]
    assert states["Score"]["Next"] == "WriteScoreStageRow"
    assert states["WriteScoreStageRow"]["Next"] == "AssignFanOut"
    assert states["AssignFanOut"]["Next"] == "TopTopic"
    assert states["TopTopic"]["Next"] == "WriteTopTopicStageRow"
    assert states["WriteTopTopicStageRow"]["Next"] == "Rollup"
    assert states["Rollup"]["Next"] == "WriteRollupStageRow"
    assert states["WriteRollupStageRow"]["Next"] == "Finalize"
    assert states["Finalize"]["Next"] == "WriteOnboardingFinal"


def test_write_onboarding_failed_builds_inline_row(asl):
    """The Catch target builds the failed `STAGE#onboarding#cwid` row inline
    from the surviving execution input ($.cwid outlives Orchestrate's
    ResultPath) plus the JSON-stringified Catch payload — the orchestrator's
    pre-typed envelope is unavailable when Orchestrate itself crashed."""
    state = asl["States"]["WriteOnboardingFailed"]
    assert state["Resource"] == "arn:aws:states:::dynamodb:putItem"
    item = state["Parameters"]["Item"]
    assert item["PK.$"] == "States.Format('STAGE#onboarding#cwid:{}', $.cwid)"
    assert item["stage"] == {"S": "onboarding"}
    assert item["status"] == {"S": "failed"}
    assert item["error_code"] == {"S": "WorkflowTaskFailed"}
    assert "JsonToString" in item["error"]["S.$"]
    assert state["Next"] == "NotifyFailed"


def test_dynamodb_writes_target_reciterai_table(asl):
    """Every inline DynamoDB:PutItem state writes to the production table."""
    for name, state in asl["States"].items():
        if state.get("Resource") == "arn:aws:states:::dynamodb:putItem":
            assert state["Parameters"]["TableName"] == "reciterai", (
                f"{name} writes to a non-canonical table"
            )


def test_notify_tasks_invoke_notify_lambda(asl):
    for name, kind in (
        ("NotifyDeferred", "deferred"),
        ("NotifyCostExceeded", "cost_exceeded"),
        ("NotifyFailed", "failed"),
    ):
        state = asl["States"][name]
        assert state["Resource"] == "${OnboardingNotifyLambdaArn}"
        assert state["Parameters"]["kind"] == kind


def test_final_states_terminate(asl):
    states = asl["States"]
    for name in (
        "WriteOnboardingFinal",
        "NotifyDeferred",
        "NotifyCostExceeded",
        "NotifyFailed",
    ):
        assert states[name].get("End") is True, f"{name} must be terminal"
    assert states["End"]["Type"] == "Succeed"

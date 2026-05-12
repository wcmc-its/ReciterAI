"""Phase 10 T14 — pipeline_hot/state_machine.asl.json shape contract.

The deploy script (scripts/deploy_state_machine.sh) substitutes a fixed
set of ${...} placeholders into the ASL template. This test pins those
placeholders so future edits to the ASL can't add unsubstituted vars
that would slip past the deploy script's leftover-placeholder check
only at deploy time.

We also verify the state machine writes the four STAGE# rows the
operator runbook (docs/hot-cold-paths.md) and the verification clause
in PLAN T14 promise:

- STAGE#score_publications#…       (Score → WriteScoreStageRow)
- STAGE#assign_subtopics#…         (Assign → WriteAssignStageRow)
- STAGE#rollup_by_cwid#…           (Rollup → WriteRollupStageRow)
- STAGE#hot_run#GLOBAL  status=complete (WriteHotRunComplete)
- STAGE#hot_run#GLOBAL  status=failed   (WriteHotRunFailed via Catch)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ASL_PATH = REPO_ROOT / "pipeline_hot" / "state_machine.asl.json"


@pytest.fixture(scope="module")
def asl_raw() -> str:
    return ASL_PATH.read_text()


@pytest.fixture(scope="module")
def asl(asl_raw) -> dict:
    return json.loads(asl_raw)


EXPECTED_PLACEHOLDERS = {
    "${OrchestratorLambdaArn}",
    "${ScoreLambdaArn}",
    "${AssignLambdaArn}",
    "${RollupLambdaArn}",
    "${AlertDispatcherLambdaArn}",
}


def test_asl_placeholders_match_deploy_script(asl_raw):
    """deploy_state_machine.sh substitutes exactly this set of vars.

    Adding a new placeholder to the ASL without updating the deploy
    script will be caught here, not at deploy time.
    """
    found = set(re.findall(r"\$\{[A-Za-z0-9_]+\}", asl_raw))
    assert found == EXPECTED_PLACEHOLDERS, (
        f"placeholder drift: expected={EXPECTED_PLACEHOLDERS}, found={found}"
    )


def test_asl_is_valid_json(asl):
    assert "StartAt" in asl
    assert "States" in asl


def test_state_machine_starts_at_orchestrate(asl):
    assert asl["StartAt"] == "Orchestrate"


def test_required_states_present(asl):
    states = asl["States"]
    for required in (
        "Orchestrate",
        "CheckLockOrProceed",
        "Score",
        "WriteScoreStageRow",
        "Assign",
        "WriteAssignStageRow",
        "Rollup",
        "WriteRollupStageRow",
        "WriteHotRunComplete",
        "WriteHotRunFailed",
        "NotifyError",
        "End",
    ):
        assert required in states, f"missing state: {required}"


def test_every_task_state_has_catch_to_write_hot_run_failed(asl):
    """A Python crash mid-handler must NOT lose the completion signal:
    every Lambda Task state has a Catch routing to WriteHotRunFailed."""
    task_lambda_states = ("Orchestrate", "Score", "Assign", "Rollup")
    for name in task_lambda_states:
        state = asl["States"][name]
        catch = state.get("Catch") or []
        assert catch, f"{name} has no Catch — failures would leak"
        targets = [c.get("Next") for c in catch]
        assert "WriteHotRunFailed" in targets, (
            f"{name} Catch does not route to WriteHotRunFailed: {targets}"
        )


def test_hot_run_complete_row_uses_correct_pk(asl):
    write = asl["States"]["WriteHotRunComplete"]
    item = write["Parameters"]["Item"]
    assert item["PK"] == {"S": "STAGE#hot_run#GLOBAL"}
    assert item["stage"] == {"S": "hot_run"}
    assert item["scope"] == {"S": "GLOBAL"}
    assert item["status"] == {"S": "complete"}


def test_hot_run_failed_row_uses_correct_pk_and_carries_error(asl):
    write = asl["States"]["WriteHotRunFailed"]
    item = write["Parameters"]["Item"]
    assert item["PK"] == {"S": "STAGE#hot_run#GLOBAL"}
    assert item["status"] == {"S": "failed"}
    assert "error.$" in item, "failed row must carry the Catch error payload"


def test_notify_error_calls_alert_dispatcher_with_severity_error(asl):
    notify = asl["States"]["NotifyError"]
    assert notify["Resource"] == "${AlertDispatcherLambdaArn}"
    params = notify["Parameters"]
    assert params["severity"] == "ERROR"
    assert params["source"] == "hot_path"


def test_dynamodb_writes_target_reciterai_chatbot_table(asl):
    """All inline DynamoDB:PutItem states write to the production table."""
    for name, state in asl["States"].items():
        if state.get("Resource") == "arn:aws:states:::dynamodb:putItem":
            assert state["Parameters"]["TableName"] == "reciterai-chatbot", (
                f"{name} writes to a non-canonical table"
            )


def test_check_lock_skip_branch_routes_to_end(asl):
    """Orchestrator may emit status=skipped on prior_run_in_progress."""
    choice = asl["States"]["CheckLockOrProceed"]
    skip_choice = next(
        c for c in choice["Choices"]
        if c.get("StringEquals") == "skipped"
    )
    assert skip_choice["Next"] == "End"


def test_final_states_terminate(asl):
    """WriteHotRunComplete, NotifyError, and End are terminal."""
    assert asl["States"]["WriteHotRunComplete"].get("End") is True
    assert asl["States"]["NotifyError"].get("End") is True
    assert asl["States"]["End"]["Type"] == "Succeed"

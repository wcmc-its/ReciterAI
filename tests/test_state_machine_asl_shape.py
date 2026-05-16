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
    "${TopTopicLambdaArn}",
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
        "CheckAssignNeeded",
        "AssignSkipped",
        "Assign",
        "WriteAssignStageRow",
        "TopTopic",
        "WriteTopTopicStageRow",
        "CheckRollupNeeded",
        "RollupSkipped",
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
    task_lambda_states = ("Orchestrate", "Score", "Assign", "TopTopic", "Rollup")
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
    # The Catch payload at $.error is a Map ({Error, Cause}); the
    # optimized DynamoDB integration will not auto-wrap a Map under
    # an `error.$` top-level key (verified empirically — fails with
    # "field Error is not supported by Step Functions"). Wrap as a
    # JSON-stringified S attribute via States.JsonToString.
    assert "error" in item, "failed row must carry the Catch error payload"
    assert isinstance(item["error"], dict) and "S.$" in item["error"], (
        "error must be wrapped as a typed DDB attribute (S.$)"
    )
    assert "JsonToString" in item["error"]["S.$"], (
        "error must be JSON-stringified so the Map serializes to one S value"
    )


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
            assert state["Parameters"]["TableName"] == "reciterai", (
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


def test_check_assign_needed_short_circuits_when_topics_empty(asl):
    """When delta.assign_topics is empty the gate must route to AssignSkipped."""
    choice = asl["States"]["CheckAssignNeeded"]
    assert choice["Type"] == "Choice"
    assert choice["Default"] == "Assign"
    rule = choice["Choices"][0]
    assert rule["IsPresent"] is False
    assert rule["Variable"] == "$.orchestrate.input.delta.assign_topics[0]"
    assert rule["Next"] == "AssignSkipped"


def test_check_rollup_needed_short_circuits_when_dirty_cwids_empty(asl):
    choice = asl["States"]["CheckRollupNeeded"]
    assert choice["Type"] == "Choice"
    assert choice["Default"] == "Rollup"
    rule = choice["Choices"][0]
    assert rule["IsPresent"] is False
    assert rule["Variable"] == "$.orchestrate.input.delta.dirty_cwids[0]"
    assert rule["Next"] == "RollupSkipped"


def test_assign_skipped_injects_typed_envelope(asl):
    """Pass state must inject a DDB attribute-typed stub so
    WriteHotRunComplete's $.assign_envelope.input_hash.S extraction works.
    """
    state = asl["States"]["AssignSkipped"]
    assert state["Type"] == "Pass"
    assert state["ResultPath"] == "$.assign_envelope"
    assert state["Next"] == "TopTopic"
    result = state["Result"]
    assert result["status"] == {"S": "skipped"}
    assert result["input_hash"]["S"] == "skipped:no-assign-topics"


def test_rollup_skipped_injects_typed_envelope(asl):
    state = asl["States"]["RollupSkipped"]
    assert state["Type"] == "Pass"
    assert state["ResultPath"] == "$.rollup_envelope"
    assert state["Next"] == "WriteHotRunComplete"
    result = state["Result"]
    assert result["status"] == {"S": "skipped"}
    assert result["input_hash"]["S"] == "skipped:no-dirty-cwids"


def test_write_hot_run_complete_uses_typed_numeric_for_delta_size(asl):
    """delta_size is an int; the optimized DDB integration won't auto-type
    integers (only strings). Must wrap as N via States.Format coercion.
    """
    item = asl["States"]["WriteHotRunComplete"]["Parameters"]["Item"]
    assert "delta_size" in item
    assert "N.$" in item["delta_size"], (
        "delta_size must be N-typed; bare delta_size.$ would inject a raw int"
    )
    assert "States.Format" in item["delta_size"]["N.$"]


def test_write_hot_run_complete_extracts_input_hashes_from_typed_envelopes(asl):
    """After the handler-side typing change, $.X_envelope is itself
    DDB-typed (e.g. {"input_hash": {"S": "abc"}}). To extract the
    string for storage on the hot_run row we must read .S, not the
    bare path (which would inject a Map and fail).
    """
    item = asl["States"]["WriteHotRunComplete"]["Parameters"]["Item"]
    for key in ("score_input_hash", "assign_input_hash",
                "top_topic_input_hash", "rollup_input_hash"):
        assert key in item, f"missing {key}"
        assert "S.$" in item[key], f"{key} must be wrapped as S.$"
        assert item[key]["S.$"].endswith(".input_hash.S"), (
            f"{key} must extract .S from the typed envelope"
        )


def test_write_score_stage_row_passes_typed_envelope_directly(asl):
    """Lambda handler returns a typed envelope; Item.$ feeds it straight in."""
    state = asl["States"]["WriteScoreStageRow"]
    assert state["Parameters"]["Item.$"] == "$.score_envelope"


def test_final_states_terminate(asl):
    """WriteHotRunComplete, NotifyError, and End are terminal."""
    assert asl["States"]["WriteHotRunComplete"].get("End") is True
    assert asl["States"]["NotifyError"].get("End") is True
    assert asl["States"]["End"]["Type"] == "Succeed"

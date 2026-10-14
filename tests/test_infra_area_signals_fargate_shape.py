"""infra/{area_signals_task_iam_policy,area_signals_task_definition}.json and the
`reciterai-area-signals-daily` rule: shape contracts.

Mirrors test_infra_grants_fargate_shape.py. Area-signals-specific pins:

- DynamoDB is pinned by `dynamodb:LeadingKeys` to the three partitions the
  pipeline touches, with only the four actions it calls (no Scan/Delete/Batch).
- Three secrets: the Bedrock token, the OpenAI key (content-filter fallback in
  both the synopsis generator and the dense scorer) and the Teams webhook.
- The taxonomy preflight baseline is injected as an env var, because the image
  has no .git and the preflight otherwise fails closed every run.
- The rule fires after SPS's 07:00 UTC nightly, which writes the queue.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TASK_POLICY_PATH = REPO_ROOT / "infra" / "area_signals_task_iam_policy.json"
TASK_DEF_PATH = REPO_ROOT / "infra" / "area_signals_task_definition.json"
EB_PATH = REPO_ROOT / "infra" / "eventbridge.json"


@pytest.fixture(scope="module")
def task_policy() -> dict:
    return json.loads(TASK_POLICY_PATH.read_text())


@pytest.fixture(scope="module")
def task_def() -> dict:
    return json.loads(TASK_DEF_PATH.read_text())


@pytest.fixture(scope="module")
def container(task_def) -> dict:
    (c,) = task_def["containerDefinitions"]
    return c


@pytest.fixture(scope="module")
def rule() -> dict:
    cfg = json.loads(EB_PATH.read_text())
    return next(r for r in cfg["rules"] if r["name"] == "reciterai-area-signals-daily")


def _actions(stmt: dict) -> list[str]:
    return stmt["Action"] if isinstance(stmt["Action"], list) else [stmt["Action"]]


def _all_actions(policy: dict) -> set[str]:
    return {a for s in policy["Statement"] for a in _actions(s)}


# --- task role policy -------------------------------------------------------------

def test_policy_is_valid_v2012(task_policy):
    assert task_policy["Version"] == "2012-10-17"


def test_dynamodb_is_exactly_what_the_pipeline_calls(task_policy):
    ddb = {a for a in _all_actions(task_policy) if a.startswith("dynamodb:")}
    assert ddb == {"dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
                   "dynamodb:Query"}


def test_dynamodb_is_pinned_to_the_area_signal_partitions(task_policy):
    (stmt,) = [s for s in task_policy["Statement"]
               if any(a.startswith("dynamodb:") for a in _actions(s))]
    assert stmt["Resource"] == ["arn:aws:dynamodb:*:*:table/reciterai"]
    keys = stmt["Condition"]["ForAllValues:StringLike"]["dynamodb:LeadingKeys"]
    assert keys == ["AREAINPUT", "AREASIGNAL#*", "STAGE#area_signals#GLOBAL"]


def test_partition_names_match_the_code(task_policy):
    from pipeline_area_signals import run, signals

    assert signals.INPUT_PK == "AREAINPUT"
    assert signals.signal_pk("grant", "x").startswith("AREASIGNAL#")
    assert f"STAGE#{run.STAGE}#{run.SCOPE}" == "STAGE#area_signals#GLOBAL"


def test_no_bedrock_secrets_or_s3(task_policy):
    prefixes = {a.split(":")[0] for a in _all_actions(task_policy)}
    assert prefixes == {"dynamodb", "logs"}


def test_logs_scoped_to_own_group(task_policy):
    (stmt,) = [s for s in task_policy["Statement"]
               if any(a.startswith("logs:") for a in _actions(s))]
    assert stmt["Resource"] == "arn:aws:logs:*:*:log-group:/ecs/reciterai-area-signals:*"


# --- task definition ------------------------------------------------------------

def test_family_and_fargate_shape(task_def, container):
    assert task_def["family"] == container["name"] == "reciterai-area-signals"
    assert task_def["networkMode"] == "awsvpc"
    assert task_def["requiresCompatibilities"] == ["FARGATE"]
    assert task_def["runtimePlatform"]["cpuArchitecture"] == "X86_64"
    assert (task_def["cpu"], task_def["memory"]) == ("512", "2048")


def test_command_is_the_run_module(container):
    assert container["command"] == ["python", "-m", "pipeline_area_signals.run"]


def test_secrets(container):
    secrets = {s["name"]: s["valueFrom"] for s in container["secrets"]}
    assert secrets == {
        "AWS_BEARER_TOKEN_BEDROCK": "{BEDROCK_API_KEY_SECRET_ARN}",
        "OPENAI_API_KEY": "{OPENAI_SECRET_ARN}:OPENAI_API_KEY::",
        "RECITERAI_TEAMS_WEBHOOK_URL": "{TEAMS_WEBHOOK_SECRET_ARN}",
    }


def test_env_carries_preflight_baseline_and_cost_ceiling(container):
    from utils.taxonomy_preflight import EXPECTED_HASH_ENV
    from pipeline_area_signals.run import MAX_COST_ENV

    env = {e["name"]: e["value"] for e in container["environment"]}
    assert env["AWS_DEFAULT_REGION"] == "us-east-1"
    assert env[EXPECTED_HASH_ENV] == "{EXPECTED_TAXONOMY_HASH}"
    assert float(env[MAX_COST_ENV]) > 0


def test_log_group_never_auto_created(container):
    opts = container["logConfiguration"]["options"]
    assert opts["awslogs-group"] == "/ecs/reciterai-area-signals"
    assert "awslogs-create-group" not in opts


def test_placeholders(task_def, container):
    assert container["image"] == "{IMAGE_URI}"
    assert task_def["taskRoleArn"] == "{TASK_ROLE_ARN}"
    assert task_def["executionRoleArn"] == "{EXECUTION_ROLE_ARN}"


# --- EventBridge rule ------------------------------------------------------------

def test_rule_targets_the_family_via_the_invoke_ecs_role(rule):
    target = rule["target"]
    assert target["kind"] == "ecs"
    assert target["task_definition_template"].endswith("task-definition/reciterai-area-signals")
    assert target["role_arn_template"].endswith("role/reciterai-eventbridge-invoke-ecs")


def test_rule_fires_after_the_sps_nightly(rule):
    assert rule["schedule_expression"] == "cron(0 9 * * ? *)"
    assert rule["state"] == "ENABLED"


def test_invoke_role_notes_name_the_new_family():
    notes = json.loads(EB_PATH.read_text())["deploy_notes"]["iam"]
    assert "area-signals" in notes and "ALL FIVE" in notes

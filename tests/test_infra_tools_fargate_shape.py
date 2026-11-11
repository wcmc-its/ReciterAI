"""infra/{tools_task_iam_policy,tools_task_definition}.json and the
`reciterai-tools-weekly` rule: shape contracts.

Mirrors test_infra_area_signals_fargate_shape.py. Tools-specific pins:

- S3 is read+write on `wcmc-reciterai-artifacts/tools/*` only, and every key
  prefix the maintain run touches lives under it.
- `s3:ListBucket` is present (key_exists is a HeadObject that must see 404, not
  403, for an absent optional state file).
- DynamoDB is pinned by `dynamodb:LeadingKeys` to the run's own ledger
  partition, which must equal `maintain.STAGE_PK`.
- No Bedrock IAM and no Secrets Manager: four secrets are execution-role-injected.
- The rule ships DISABLED with a state_comment naming the precondition.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TASK_POLICY_PATH = REPO_ROOT / "infra" / "tools_task_iam_policy.json"
TASK_DEF_PATH = REPO_ROOT / "infra" / "tools_task_definition.json"
EB_PATH = REPO_ROOT / "infra" / "eventbridge.json"
BUCKET = "wcmc-reciterai-artifacts"


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
    return next(r for r in cfg["rules"] if r["name"] == "reciterai-tools-weekly")


def _actions(stmt: dict) -> list[str]:
    return stmt["Action"] if isinstance(stmt["Action"], list) else [stmt["Action"]]


def _all_actions(policy: dict) -> set[str]:
    return {a for s in policy["Statement"] for a in _actions(s)}


def _stmts(policy: dict, prefix: str) -> list[dict]:
    return [s for s in policy["Statement"] if any(a.startswith(prefix) for a in _actions(s))]


# --- task role policy -------------------------------------------------------------

def test_policy_is_valid_v2012(task_policy):
    assert task_policy["Version"] == "2012-10-17"


def test_only_s3_dynamodb_and_logs(task_policy):
    prefixes = {a.split(":")[0] for a in _all_actions(task_policy)}
    assert prefixes == {"s3", "dynamodb", "logs"}, "no bedrock / secretsmanager on the task role"


def test_s3_object_access_is_scoped_to_tools_prefix(task_policy):
    obj = [s for s in _stmts(task_policy, "s3:") if "s3:ListBucket" not in _actions(s)]
    (stmt,) = obj
    assert set(_actions(stmt)) == {"s3:GetObject", "s3:PutObject"}
    assert stmt["Resource"] == [f"arn:aws:s3:::{BUCKET}/tools/*"]


def test_list_bucket_present_for_key_exists(task_policy):
    (stmt,) = [s for s in _stmts(task_policy, "s3:") if "s3:ListBucket" in _actions(s)]
    assert _actions(stmt) == ["s3:ListBucket"]
    assert stmt["Resource"] == [f"arn:aws:s3:::{BUCKET}"]


def test_every_key_the_run_touches_is_under_tools(task_policy):
    from pipeline_tools import checkpoint, maintain, publish, state

    assert maintain.read_live_s3  # live reads tools/latest/
    for key in (
        checkpoint.DEFAULT_S3_KEY,
        state.STATE_S3_PREFIX + state.LATEST_POINTER,
        maintain.REVIEW_PREFIX + "x.json",
        maintain.VERSIONS_PREFIX + "x/tools.json",
        publish.S3_PREFIX + "latest/manifest.json",
    ):
        assert key.startswith("tools/"), key


def test_dynamodb_is_pinned_to_the_ledger_partition(task_policy):
    from pipeline_tools import maintain

    (stmt,) = _stmts(task_policy, "dynamodb:")
    assert set(_actions(stmt)) == {"dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query"}
    assert stmt["Resource"] == ["arn:aws:dynamodb:*:*:table/reciterai"]
    keys = stmt["Condition"]["ForAllValues:StringLike"]["dynamodb:LeadingKeys"]
    assert keys == [maintain.STAGE_PK] == ["STAGE#tools_maintain#GLOBAL"]


def test_logs_scoped_to_own_group(task_policy):
    (stmt,) = _stmts(task_policy, "logs:")
    assert stmt["Resource"] == "arn:aws:logs:*:*:log-group:/ecs/reciterai-tools:*"
    assert "logs:CreateLogGroup" not in _actions(stmt)


# --- task definition ------------------------------------------------------------

def test_family_and_fargate_shape(task_def, container):
    assert task_def["family"] == container["name"] == "reciterai-tools"
    assert task_def["networkMode"] == "awsvpc"
    assert task_def["requiresCompatibilities"] == ["FARGATE"]
    assert task_def["runtimePlatform"]["cpuArchitecture"] == "X86_64"
    assert (task_def["cpu"], task_def["memory"]) == ("1024", "8192")


def test_scheduled_command_publishes_without_first_run_flags(container):
    cmd = container["command"]
    assert cmd == ["python", "-m", "pipeline_tools.maintain", "--publish"]
    for flag in ("--seed-incorporated-from-checkpoint", "--relabel", "--allow-cold-checkpoint",
                 "--hard-cap-usd"):
        assert flag not in cmd


def test_flags_the_scheduled_and_first_runs_use_exist_on_the_cli(capsys):
    from pipeline_tools import maintain

    with pytest.raises(SystemExit) as e:
        maintain.main(["--help"])
    assert e.value.code == 0
    help_text = capsys.readouterr().out
    for flag in ("--publish", "--preflight-only", "--seed-incorporated-from-checkpoint",
                 "--hard-cap-usd", "--no-extract"):
        assert flag in help_text, flag


def test_secrets(container):
    from pipeline_enrichment.alerting import WEBHOOK_ENV

    secrets = {s["name"]: s["valueFrom"] for s in container["secrets"]}
    assert secrets == {
        "DB_HOST": "{DB_SECRET_ARN}:DB_HOST::",
        "DB_USERNAME": "{DB_SECRET_ARN}:DB_USERNAME::",
        "DB_PASSWORD": "{DB_SECRET_ARN}:DB_PASSWORD::",
        "DB_NAME": "{DB_SECRET_ARN}:DB_NAME::",
        "OPENAI_API_KEY": "{OPENAI_SECRET_ARN}:OPENAI_API_KEY::",
        "AWS_BEARER_TOKEN_BEDROCK": "{BEDROCK_API_KEY_SECRET_ARN}",
        WEBHOOK_ENV: "{TEAMS_WEBHOOK_SECRET_ARN}",
    }


def test_log_group_never_auto_created(container):
    opts = container["logConfiguration"]["options"]
    assert opts["awslogs-group"] == "/ecs/reciterai-tools"
    assert "awslogs-create-group" not in opts


def test_placeholders(task_def, container):
    assert container["image"] == "{IMAGE_URI}"
    assert task_def["taskRoleArn"] == "{TASK_ROLE_ARN}"
    assert task_def["executionRoleArn"] == "{EXECUTION_ROLE_ARN}"
    placeholders = task_def["deploy_notes"]["placeholders"]
    used = {"{TASK_ROLE_ARN}", "{EXECUTION_ROLE_ARN}", "{IMAGE_URI}", "{DB_SECRET_ARN}",
            "{OPENAI_SECRET_ARN}", "{BEDROCK_API_KEY_SECRET_ARN}", "{TEAMS_WEBHOOK_SECRET_ARN}"}
    assert set(placeholders) == used


# --- EventBridge rule ------------------------------------------------------------

def test_rule_targets_the_family_via_the_invoke_ecs_role(rule):
    target = rule["target"]
    assert target["kind"] == "ecs"
    assert target["task_definition_template"].endswith("task-definition/reciterai-tools")
    assert target["role_arn_template"].endswith("role/reciterai-eventbridge-invoke-ecs")


def test_rule_is_weekly_and_deployed_disabled(rule):
    assert rule["schedule_expression"] == "cron(30 3 ? * TUE *)"
    assert rule["state"] == "DISABLED"
    assert "--seed-incorporated-from-checkpoint" in rule["state_comment"]
    assert len(rule["description"]) <= 512


def test_invoke_role_notes_name_the_new_family():
    notes = json.loads(EB_PATH.read_text())["deploy_notes"]["iam"]
    assert "tools" in notes and "ALL SIX" in notes
    assert "tools_task_iam_policy.json" in notes

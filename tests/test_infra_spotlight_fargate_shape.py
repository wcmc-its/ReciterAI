"""#329 shape a2 — infra/spotlight_task_definition.json + the ECS spotlight rule.

Mirrors `test_infra_cold_run_fargate_shape.py`. Pins the monthly spotlight
Fargate launch path so a future edit that breaks the contract trips here.
Spotlight-specific pins (vs the cold-run task):

- The default command is the ORCHESTRATOR (`pipeline_spotlight.orchestrator`),
  not `pipeline_cold.run` — deploy_cron.sh's ecs branch launches the task
  definition's default command as-is (EventBridge Input is dead for direct
  RunTask), so this command IS the monthly invocation.
- The Teams webhook secret IS injected (as the legacy
  `RECITERAI_SLACK_WEBHOOK_URL` env `pipeline_common.alert` reads): the task
  runs unattended, so a missing webhook silently downgrades ERROR alerts to
  log lines — the #361 failure family. The cold run omits it on purpose
  (operator-gated); this task must not.
- The eventbridge.json spotlight rule targets kind=ecs and the
  `reciterai-spotlight` task-definition family.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TASK_DEF_PATH = REPO_ROOT / "infra" / "spotlight_task_definition.json"
EB_PATH = REPO_ROOT / "infra" / "eventbridge.json"


@pytest.fixture(scope="module")
def task_def() -> dict:
    return json.loads(TASK_DEF_PATH.read_text())


@pytest.fixture(scope="module")
def spotlight_rule() -> dict:
    cfg = json.loads(EB_PATH.read_text())
    return next(r for r in cfg["rules"] if r["name"] == "reciterai-spotlight-monthly")


@pytest.fixture(scope="module")
def container(task_def) -> dict:
    (c,) = task_def["containerDefinitions"]
    return c


# ---------------------------------------------------------------------------
# spotlight_task_definition.json
# ---------------------------------------------------------------------------


def test_task_def_family_and_fargate_shape(task_def):
    assert task_def["family"] == "reciterai-spotlight"
    assert task_def["networkMode"] == "awsvpc"
    assert task_def["requiresCompatibilities"] == ["FARGATE"]
    assert task_def["runtimePlatform"]["cpuArchitecture"] == "X86_64"


def test_default_command_is_the_orchestrator(container):
    assert container["command"] == ["python", "-m", "pipeline_spotlight.orchestrator"]


def test_roles_are_placeholders(task_def):
    assert task_def["taskRoleArn"] == "{TASK_ROLE_ARN}"
    assert task_def["executionRoleArn"] == "{EXECUTION_ROLE_ARN}"


def test_teams_webhook_is_injected_for_unattended_alerting(container):
    """pipeline_common.alert.dispatch reads RECITERAI_SLACK_WEBHOOK_URL and
    silently skips the POST when it is absent. An unattended monthly task
    must carry it or ERROR alerts never leave CloudWatch."""
    names = {s["name"] for s in container["secrets"]}
    assert "RECITERAI_SLACK_WEBHOOK_URL" in names


def test_secrets_cover_the_regen_dependency_set(container):
    """The dirty-path regen is cli.backfill_spotlight: Bedrock (bearer token),
    OpenAI fallback, and DB creds — same set as the cold run's stage 9."""
    names = {s["name"] for s in container["secrets"]}
    assert {"DB_HOST", "DB_USERNAME", "DB_PASSWORD", "DB_NAME",
            "OPENAI_API_KEY", "AWS_BEARER_TOKEN_BEDROCK"}.issubset(names)


def test_log_group_is_spotlight_scoped_and_never_auto_created(container):
    """`ecsTaskExecutionRole` lacks `logs:CreateLogGroup`, and ECS rejects
    `awslogs-create-group: false` (only `true`-or-omit) — the group must be
    pre-created and the option must stay ABSENT. Same pin as the cold run."""
    opts = container["logConfiguration"]["options"]
    assert opts["awslogs-group"] == "/ecs/reciterai-spotlight"
    assert "awslogs-create-group" not in opts


def test_container_name_matches_family(container, task_def):
    assert container["name"] == task_def["family"] == "reciterai-spotlight"


def test_sizing_matches_the_cold_run_envelope(task_def):
    """1 vCPU / 4 GB — the dirty-path regen IS the cold run's stage 9."""
    assert task_def["cpu"] == "1024"
    assert task_def["memory"] == "4096"


def test_db_secrets_use_per_key_injection(container):
    """The DB secret is a JSON SecretString: each key needs the trailing
    `:KEY::` selector or ECS injects the whole JSON blob as the value."""
    by_name = {s["name"]: s["valueFrom"] for s in container["secrets"]}
    for key in ("DB_HOST", "DB_USERNAME", "DB_PASSWORD", "DB_NAME"):
        assert by_name[key].endswith(f":{key}::"), by_name[key]
    # Plain-SecretString secrets must NOT carry a key selector.
    assert by_name["AWS_BEARER_TOKEN_BEDROCK"] == "{BEDROCK_API_KEY_SECRET_ARN}"
    assert by_name["RECITERAI_SLACK_WEBHOOK_URL"] == "{TEAMS_WEBHOOK_SECRET_ARN}"


def test_image_is_a_placeholder(container):
    assert container["image"] == "{IMAGE_URI}"


# ---------------------------------------------------------------------------
# eventbridge.json — the spotlight rule's ECS target
# ---------------------------------------------------------------------------


def test_spotlight_rule_is_an_ecs_target(spotlight_rule):
    assert spotlight_rule["target"]["kind"] == "ecs"


def test_spotlight_rule_targets_the_spotlight_task_family(spotlight_rule):
    tmpl = spotlight_rule["target"]["task_definition_template"]
    assert tmpl.endswith("task-definition/reciterai-spotlight")


def test_spotlight_rule_reuses_the_deployed_invoke_ecs_role(spotlight_rule):
    assert spotlight_rule["target"]["role_arn_template"].endswith(
        "role/reciterai-eventbridge-invoke-ecs"
    )

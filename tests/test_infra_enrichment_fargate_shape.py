"""#37 PR 4 — infra/{enrichment_task_iam_policy,ecs_task_definition}.json shape contracts.

Mirrors `test_infra_eventbridge_shape.py`. Pins:

- The task role IAM policy does NOT carry `bedrock:InvokeModel` (PR-4 D-Q1:
  Bedrock authenticates via `AWS_BEARER_TOKEN_BEDROCK`; a SigV4 fallback
  against the task role would mask a missing/stale bearer token). Adding
  it back is a deliberate choice that requires touching this test.
- The task role grants DDB write on the `reciterai` table, Secrets Manager
  read on the four enrichment secrets, and CloudWatch Logs write on the
  `/ecs/reciterai-enrichment` log group.
- The ECS task definition runs `python -m scripts.run_daily_enrichment` by
  default, with the four secret-derived env vars and the two plain env vars
  the enrichment job needs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TASK_POLICY_PATH = REPO_ROOT / "infra" / "enrichment_task_iam_policy.json"
TASK_DEF_PATH = REPO_ROOT / "infra" / "ecs_task_definition.json"


@pytest.fixture(scope="module")
def task_policy() -> dict:
    return json.loads(TASK_POLICY_PATH.read_text())


@pytest.fixture(scope="module")
def task_def() -> dict:
    return json.loads(TASK_DEF_PATH.read_text())


def _all_actions(policy: dict) -> set[str]:
    out: set[str] = set()
    for stmt in policy["Statement"]:
        action = stmt["Action"]
        if isinstance(action, str):
            out.add(action)
        else:
            out.update(action)
    return out


# ---------------------------------------------------------------------------
# enrichment_task_iam_policy.json
# ---------------------------------------------------------------------------


def test_task_policy_is_valid_v2012(task_policy):
    assert task_policy["Version"] == "2012-10-17"
    assert isinstance(task_policy["Statement"], list)
    assert len(task_policy["Statement"]) >= 1


def test_task_policy_does_not_grant_bedrock_invoke(task_policy):
    """PR-4 D-Q1: Bedrock authenticates via AWS_BEARER_TOKEN_BEDROCK, not
    SigV4 IAM. Dropping bedrock:InvokeModel forces loud failure on a
    missing/stale bearer token. Adding it back is a deliberate choice."""
    actions = _all_actions(task_policy)
    bedrock_actions = {a for a in actions if a.startswith("bedrock:")}
    assert bedrock_actions == set(), (
        "enrichment task role MUST NOT carry bedrock:* actions per "
        f"plan D8 / PR-4 D-Q1; found: {bedrock_actions}"
    )


def test_task_policy_grants_dynamodb_write_on_reciterai(task_policy):
    """Watermark + IMPACT# row writes."""
    actions = _all_actions(task_policy)
    assert "dynamodb:PutItem" in actions
    assert "dynamodb:BatchWriteItem" in actions
    ddb_resources: set[str] = set()
    for stmt in task_policy["Statement"]:
        if not any(a.startswith("dynamodb:") for a in (
            stmt["Action"] if isinstance(stmt["Action"], list) else [stmt["Action"]]
        )):
            continue
        resources = (
            stmt["Resource"]
            if isinstance(stmt["Resource"], list)
            else [stmt["Resource"]]
        )
        ddb_resources.update(resources)
    assert any("table/reciterai" in r for r in ddb_resources), (
        f"DynamoDB scope missing reciterai table: {sorted(ddb_resources)}"
    )
    for r in ddb_resources:
        assert "reciterai" in r, f"DynamoDB leak outside reciterai: {r}"


def test_task_policy_grants_secrets_manager_read_on_four_enrichment_secrets(task_policy):
    """The task definition's `secrets` block pulls four secrets — the
    policy must grant SecretsManager:GetSecretValue on each."""
    secret_resources: set[str] = set()
    for stmt in task_policy["Statement"]:
        actions = (
            stmt["Action"] if isinstance(stmt["Action"], list) else [stmt["Action"]]
        )
        if "secretsmanager:GetSecretValue" not in actions:
            continue
        resources = (
            stmt["Resource"]
            if isinstance(stmt["Resource"], list)
            else [stmt["Resource"]]
        )
        secret_resources.update(resources)
    for expected in (
        "reciterai/reciter-analysis-db",
        "reciterai/openai-api-key",
        "reciterai/teams-webhook-url",
        "reciterai/bedrock-api-key",
    ):
        assert any(expected in r for r in secret_resources), (
            f"task policy missing SecretsManager grant for {expected!r}; "
            f"granted: {sorted(secret_resources)}"
        )


def test_task_policy_grants_cloudwatch_logs_on_enrichment_log_group(task_policy):
    """CloudWatch Logs scoped to /ecs/reciterai-enrichment, not /aws/lambda/*."""
    actions = _all_actions(task_policy)
    assert "logs:CreateLogStream" in actions
    assert "logs:PutLogEvents" in actions
    log_resources: set[str] = set()
    for stmt in task_policy["Statement"]:
        if not any(a.startswith("logs:") for a in (
            stmt["Action"] if isinstance(stmt["Action"], list) else [stmt["Action"]]
        )):
            continue
        resources = (
            stmt["Resource"]
            if isinstance(stmt["Resource"], list)
            else [stmt["Resource"]]
        )
        log_resources.update(resources)
    assert any("reciterai-enrichment" in r for r in log_resources), (
        f"CloudWatch Logs scope missing /ecs/reciterai-enrichment: {sorted(log_resources)}"
    )


# ---------------------------------------------------------------------------
# ecs_task_definition.json
# ---------------------------------------------------------------------------


def test_task_def_targets_fargate_awsvpc(task_def):
    assert task_def["family"] == "reciterai-enrichment"
    assert task_def["networkMode"] == "awsvpc"
    assert task_def["requiresCompatibilities"] == ["FARGATE"]


def test_task_def_has_one_container_named_reciterai_enrichment(task_def):
    containers = task_def["containerDefinitions"]
    assert len(containers) == 1
    assert containers[0]["name"] == "reciterai-enrichment"


def test_task_def_default_command_runs_daily_enrichment(task_def):
    """Scheduled invocations use this default; backfill/rescore override
    it via containerOverrides[].command."""
    cmd = task_def["containerDefinitions"][0]["command"]
    assert cmd == ["python", "-m", "scripts.run_daily_enrichment"]


def test_task_def_secrets_cover_four_expected_env_vars(task_def):
    """The container needs DB creds, the OpenAI fallback key, the Teams
    webhook, and the Bedrock bearer token — all from Secrets Manager."""
    secrets = task_def["containerDefinitions"][0]["secrets"]
    env_var_names = {s["name"] for s in secrets}
    for expected in (
        "DB_HOST", "DB_USERNAME", "DB_PASSWORD", "DB_NAME",
        "OPENAI_API_KEY",
        "RECITERAI_TEAMS_WEBHOOK_URL",
        "AWS_BEARER_TOKEN_BEDROCK",
    ):
        assert expected in env_var_names, (
            f"task def `secrets` missing {expected!r}; has: {sorted(env_var_names)}"
        )


def test_task_def_db_secret_is_resolved_per_json_key(task_def):
    """DB creds are stored as a JSON SecretString; ECS pulls individual
    keys via the trailing `:KEY::` syntax."""
    secrets = task_def["containerDefinitions"][0]["secrets"]
    db_secrets = [s for s in secrets if s["name"].startswith("DB_")]
    assert len(db_secrets) == 4
    for s in db_secrets:
        vf = s["valueFrom"]
        # `{DB_SECRET_ARN}:DB_HOST::` etc. — placeholder + JSON-key trailer.
        assert vf.endswith(f":{s['name']}::"), (
            f"{s['name']} valueFrom does not extract a JSON key: {vf}"
        )


def test_task_def_logs_to_enrichment_log_group(task_def):
    log_cfg = task_def["containerDefinitions"][0]["logConfiguration"]
    assert log_cfg["logDriver"] == "awslogs"
    assert log_cfg["options"]["awslogs-group"] == "/ecs/reciterai-enrichment"


def test_task_def_uses_placeholders_for_account_specific_arns(task_def):
    """Image URI, task role, execution role are deploy-time placeholders —
    must not be hard-coded in the committed file."""
    container = task_def["containerDefinitions"][0]
    assert container["image"].startswith("{") and container["image"].endswith("}")
    assert task_def["taskRoleArn"].startswith("{")
    assert task_def["executionRoleArn"].startswith("{")

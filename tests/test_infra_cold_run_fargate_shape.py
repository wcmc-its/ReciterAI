"""#240 — infra/{cold_run_task_iam_policy,cold_run_task_definition}.json shape contracts.

Mirrors `test_infra_enrichment_fargate_shape.py`. Pins the cold-run Fargate
launch path so a future edit that breaks the contract trips here. Cold-run-
specific pins (vs the enrichment task):

- The task role ADDS an S3 read/write statement on the two artifact buckets
  (`wcmc-reciterai-hierarchy` + `wcmc-reciterai-artifacts`) — the cold-run is
  the only ReciterAI task that publishes those artifacts — and grants nothing
  outside them.
- Still NO `bedrock:InvokeModel` (bearer-token auth, same as enrichment).
- Secrets are the true 2-secret minimum (DB creds + OpenAI). The cold path does
  not alert, so `teams-webhook-url` is intentionally absent; `bedrock-api-key`
  is injected as `AWS_BEARER_TOKEN_BEDROCK` by the EXECUTION role, not fetched by
  the task role, so it is absent from the task-role policy too.
- The task def runs `python -m pipeline_cold.run` by default, sized 1 vCPU / 4 GB,
  logging to `/ecs/reciterai-cold`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TASK_POLICY_PATH = REPO_ROOT / "infra" / "cold_run_task_iam_policy.json"
TASK_DEF_PATH = REPO_ROOT / "infra" / "cold_run_task_definition.json"


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


def _resources_for_prefix(policy: dict, prefix: str) -> set[str]:
    """Union of Resource ARNs across statements that carry any `prefix:` action."""
    out: set[str] = set()
    for stmt in policy["Statement"]:
        actions = stmt["Action"] if isinstance(stmt["Action"], list) else [stmt["Action"]]
        if not any(a.startswith(prefix) for a in actions):
            continue
        resources = stmt["Resource"] if isinstance(stmt["Resource"], list) else [stmt["Resource"]]
        out.update(resources)
    return out


# ---------------------------------------------------------------------------
# cold_run_task_iam_policy.json
# ---------------------------------------------------------------------------


def test_task_policy_is_valid_v2012(task_policy):
    assert task_policy["Version"] == "2012-10-17"
    assert isinstance(task_policy["Statement"], list)
    assert len(task_policy["Statement"]) >= 1


def test_task_policy_does_not_grant_bedrock_invoke(task_policy):
    """Bedrock authenticates via AWS_BEARER_TOKEN_BEDROCK, not SigV4 IAM —
    same posture as the enrichment task role."""
    bedrock_actions = {a for a in _all_actions(task_policy) if a.startswith("bedrock:")}
    assert bedrock_actions == set(), (
        f"cold-run task role MUST NOT carry bedrock:* actions; found: {bedrock_actions}"
    )


def test_task_policy_grants_dynamodb_crud_on_reciterai_only(task_policy):
    actions = _all_actions(task_policy)
    assert "dynamodb:PutItem" in actions
    assert "dynamodb:BatchWriteItem" in actions
    ddb_resources = _resources_for_prefix(task_policy, "dynamodb:")
    assert any("table/reciterai" in r for r in ddb_resources)
    for r in ddb_resources:
        assert "reciterai" in r, f"DynamoDB leak outside reciterai: {r}"


def test_task_policy_adds_s3_readwrite_on_both_artifact_buckets(task_policy):
    """The cold-run's distinguishing grant: it PUBLISHES hierarchy + spotlight."""
    actions = _all_actions(task_policy)
    assert "s3:GetObject" in actions, "cold-run reads prior artifacts (diff + skip-logic)"
    assert "s3:PutObject" in actions, "cold-run publishes hierarchy + spotlight"
    s3_resources = _resources_for_prefix(task_policy, "s3:")
    assert any("wcmc-reciterai-hierarchy" in r for r in s3_resources), (
        f"missing hierarchy bucket grant: {sorted(s3_resources)}"
    )
    assert any("wcmc-reciterai-artifacts" in r for r in s3_resources), (
        f"missing artifacts bucket grant: {sorted(s3_resources)}"
    )
    # No S3 leak outside the two ReciterAI artifact buckets.
    for r in s3_resources:
        assert "wcmc-reciterai-hierarchy" in r or "wcmc-reciterai-artifacts" in r, (
            f"S3 grant outside the ReciterAI artifact buckets: {r}"
        )


def test_task_policy_secrets_are_the_two_secret_minimum(task_policy):
    """DB creds + OpenAI only. teams-webhook (no cold-path alerting) and
    bedrock-api-key (execution-role-injected env var) are intentionally absent."""
    secret_resources = _resources_for_prefix(task_policy, "secretsmanager:")
    assert "secretsmanager:GetSecretValue" in _all_actions(task_policy)
    for expected in ("reciterai/reciter-analysis-db", "reciterai/openai-api-key"):
        assert any(expected in r for r in secret_resources), (
            f"cold-run task policy missing SecretsManager grant for {expected!r}"
        )
    for absent in ("reciterai/teams-webhook-url", "reciterai/bedrock-api-key"):
        assert not any(absent in r for r in secret_resources), (
            f"cold-run task role should NOT grant {absent!r} (see docstring); "
            f"granted: {sorted(secret_resources)}"
        )


def test_task_policy_logs_scoped_to_cold_log_group(task_policy):
    actions = _all_actions(task_policy)
    assert "logs:CreateLogStream" in actions
    assert "logs:PutLogEvents" in actions
    log_resources = _resources_for_prefix(task_policy, "logs:")
    assert any("reciterai-cold" in r for r in log_resources), (
        f"CloudWatch Logs scope missing /ecs/reciterai-cold: {sorted(log_resources)}"
    )


# ---------------------------------------------------------------------------
# cold_run_task_definition.json
# ---------------------------------------------------------------------------


def test_task_def_targets_fargate_awsvpc(task_def):
    assert task_def["family"] == "reciterai-cold"
    assert task_def["networkMode"] == "awsvpc"
    assert task_def["requiresCompatibilities"] == ["FARGATE"]


def test_task_def_is_sized_one_vcpu_four_gb(task_def):
    """1 vCPU / 4 GB — the cold-run is Bedrock/IO-bound but runs 15-way concurrent
    fan-out and full-corpus scans; heavier than enrichment's 0.25 vCPU / 1 GB."""
    assert task_def["cpu"] == "1024"
    assert task_def["memory"] == "4096"


def test_task_def_has_one_container_named_reciterai_cold(task_def):
    containers = task_def["containerDefinitions"]
    assert len(containers) == 1
    assert containers[0]["name"] == "reciterai-cold"


def test_task_def_default_command_runs_cold_run(task_def):
    """The default command is the full cold-run; dry-run / resume override it
    via containerOverrides[].command."""
    cmd = task_def["containerDefinitions"][0]["command"]
    assert cmd == ["python", "-m", "pipeline_cold.run"]


def test_task_def_secrets_cover_db_openai_bedrock(task_def):
    secrets = task_def["containerDefinitions"][0]["secrets"]
    env_var_names = {s["name"] for s in secrets}
    for expected in (
        "DB_HOST", "DB_USERNAME", "DB_PASSWORD", "DB_NAME",
        "OPENAI_API_KEY",
        "AWS_BEARER_TOKEN_BEDROCK",
    ):
        assert expected in env_var_names, (
            f"task def `secrets` missing {expected!r}; has: {sorted(env_var_names)}"
        )
    # cold path does not alert → no Teams webhook
    assert "RECITERAI_TEAMS_WEBHOOK_URL" not in env_var_names


def test_task_def_db_secret_is_resolved_per_json_key(task_def):
    secrets = task_def["containerDefinitions"][0]["secrets"]
    db_secrets = [s for s in secrets if s["name"].startswith("DB_")]
    assert len(db_secrets) == 4
    for s in db_secrets:
        assert s["valueFrom"].endswith(f":{s['name']}::")


def test_task_def_logs_to_cold_log_group(task_def):
    log_cfg = task_def["containerDefinitions"][0]["logConfiguration"]
    assert log_cfg["logDriver"] == "awslogs"
    assert log_cfg["options"]["awslogs-group"] == "/ecs/reciterai-cold"


def test_task_def_uses_placeholders_for_account_specific_arns(task_def):
    container = task_def["containerDefinitions"][0]
    assert container["image"].startswith("{") and container["image"].endswith("}")
    assert task_def["taskRoleArn"].startswith("{")
    assert task_def["executionRoleArn"].startswith("{")

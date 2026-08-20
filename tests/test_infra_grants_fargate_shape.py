"""#269 — infra/{grants_task_iam_policy,grants_task_definition}.json shape contracts.

Mirrors `test_infra_cold_run_fargate_shape.py` / `test_infra_spotlight_fargate_shape.py`.
Pins the daily grant-opportunity ingest Fargate launch path so a future edit that
breaks the contract trips here. Grants-specific pins (vs the other tasks):

- The default command chains BOTH ingests with `;` (not `&&`): a grants.gov
  failure must not strand the SPS submission queue — `ingest_submissions` runs
  regardless of `ingest`'s exit status. deploy_cron.sh's ecs branch launches the
  task definition's default command as-is (EventBridge Input is dead for direct
  RunTask), so this command IS the daily invocation.
- SPIN ingest is deliberately ABSENT from the command (`ingest_spin` docstring
  decision #5 — no scheduler until the SPIN ToS check on scheduled bulk pulls
  clears).
- The ONLY secret is the Bedrock bearer token: pipeline_grants reads no DB and
  has no OpenAI fallback, so DB_*/OPENAI_API_KEY are absent — and so is any
  SecretsManager statement on the task role (the execution role injects the one
  secret).
- S3 is scoped to `wcmc-reciterai-artifacts/grants/*` only (the ingest never
  writes hierarchy or spotlight artifacts), plus `s3:ListBucket` on the bucket
  for `persist.key_exists` (without it the shrink guard's prior-manifest read
  AccessDenies and the guard silently fails open every run).
- Sized 0.5 vCPU / 2 GB — serial Bedrock-I/O loops, no concurrent fan-out.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TASK_POLICY_PATH = REPO_ROOT / "infra" / "grants_task_iam_policy.json"
TASK_DEF_PATH = REPO_ROOT / "infra" / "grants_task_definition.json"
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
def grants_rule() -> dict:
    cfg = json.loads(EB_PATH.read_text())
    return next(r for r in cfg["rules"] if r["name"] == "reciterai-grants-daily")


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
# grants_task_iam_policy.json
# ---------------------------------------------------------------------------


def test_task_policy_is_valid_v2012(task_policy):
    assert task_policy["Version"] == "2012-10-17"
    assert isinstance(task_policy["Statement"], list)
    assert len(task_policy["Statement"]) >= 1


def test_task_policy_does_not_grant_bedrock_invoke(task_policy):
    """Bedrock authenticates via AWS_BEARER_TOKEN_BEDROCK, not SigV4 IAM —
    same posture as the enrichment/cold/spotlight task roles."""
    bedrock_actions = {a for a in _all_actions(task_policy) if a.startswith("bedrock:")}
    assert bedrock_actions == set(), (
        f"grants task role MUST NOT carry bedrock:* actions; found: {bedrock_actions}"
    )


def test_task_policy_has_no_secretsmanager_statement(task_policy):
    """The only secret (bedrock-api-key) is execution-role-injected as an env
    var; pipeline_grants reads no DB and has no OpenAI fallback, so the task
    role fetches nothing from Secrets Manager."""
    sm_actions = {a for a in _all_actions(task_policy) if a.startswith("secretsmanager:")}
    assert sm_actions == set(), (
        f"grants task role should carry NO secretsmanager:* actions; found: {sm_actions}"
    )


def test_task_policy_grants_dynamodb_crud_on_reciterai_only(task_policy):
    actions = _all_actions(task_policy)
    for expected in (
        "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
        "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:Scan",
        "dynamodb:BatchGetItem", "dynamodb:BatchWriteItem", "dynamodb:DescribeTable",
    ):
        assert expected in actions, f"missing {expected!r}"
    ddb_resources = _resources_for_prefix(task_policy, "dynamodb:")
    assert any("table/reciterai" in r for r in ddb_resources)
    assert any("table/reciterai/index/" in r for r in ddb_resources)
    for r in ddb_resources:
        assert "reciterai" in r, f"DynamoDB leak outside reciterai: {r}"


def test_task_policy_s3_objects_scoped_to_grants_prefix(task_policy):
    """The grants ingest publishes grants/{version}/ + grants/latest/ and reads
    the prior grants/latest/manifest.json (shrink guard) — nothing else."""
    actions = _all_actions(task_policy)
    assert "s3:GetObject" in actions, "shrink guard reads the prior grants/latest/manifest.json"
    assert "s3:PutObject" in actions, "the ingest publishes the grants/ artifact"
    object_resources = {
        r for r in _resources_for_prefix(task_policy, "s3:") if "/" in r.split(":::")[-1]
    }
    assert object_resources == {"arn:aws:s3:::wcmc-reciterai-artifacts/grants/*"}, (
        f"S3 object grants must be exactly grants/*: {sorted(object_resources)}"
    )


def test_task_policy_grants_listbucket_for_key_exists(task_policy):
    """persist._prior_opportunity_count -> S3HierarchyClient.key_exists needs
    s3:ListBucket on the BUCKET arn; without it the shrink guard's read
    AccessDenies and the guard silently fails open every run."""
    assert "s3:ListBucket" in _all_actions(task_policy)
    s3_resources = _resources_for_prefix(task_policy, "s3:")
    assert "arn:aws:s3:::wcmc-reciterai-artifacts" in s3_resources
    # No S3 leak outside the artifacts bucket.
    for r in s3_resources:
        assert "wcmc-reciterai-artifacts" in r, f"S3 grant outside the artifacts bucket: {r}"


def test_task_policy_logs_scoped_to_grants_log_group(task_policy):
    actions = _all_actions(task_policy)
    assert "logs:CreateLogStream" in actions
    assert "logs:PutLogEvents" in actions
    log_resources = _resources_for_prefix(task_policy, "logs:")
    assert any("reciterai-grants" in r for r in log_resources), (
        f"CloudWatch Logs scope missing /ecs/reciterai-grants: {sorted(log_resources)}"
    )


# ---------------------------------------------------------------------------
# grants_task_definition.json
# ---------------------------------------------------------------------------


def test_task_def_family_and_fargate_shape(task_def):
    assert task_def["family"] == "reciterai-grants"
    assert task_def["networkMode"] == "awsvpc"
    assert task_def["requiresCompatibilities"] == ["FARGATE"]
    assert task_def["runtimePlatform"]["cpuArchitecture"] == "X86_64"


def test_task_def_is_sized_half_vcpu_two_gb(task_def):
    """0.5 vCPU / 2 GB — both ingests are serial Bedrock/HTTP-I/O loops with no
    concurrent fan-out and no corpus load; the cold run's 1-vCPU/4-GB envelope
    does not apply."""
    assert task_def["cpu"] == "512"
    assert task_def["memory"] == "2048"


def test_container_name_matches_family(container, task_def):
    assert container["name"] == task_def["family"] == "reciterai-grants"


def test_default_command_chains_both_ingests_with_semicolon(container):
    """';' not '&&': a grants.gov failure must not strand the SPS submission
    queue. This command IS the daily invocation (EventBridge Input is dead for
    direct RunTask launches)."""
    assert container["command"] == [
        "sh",
        "-c",
        "python -m pipeline_grants.ingest; python -m pipeline_grants.ingest_submissions",
    ]


def test_spin_ingest_is_not_scheduled(container):
    """ingest_spin docstring decision #5: manual CLI only — no scheduler until
    the SPIN ToS check on scheduled bulk pulls clears."""
    assert "ingest_spin" not in " ".join(container["command"])


def test_secrets_are_the_bedrock_token_only(container):
    """pipeline_grants needs no DB creds and has no OpenAI fallback — the
    bearer token is the whole secret surface."""
    assert [s["name"] for s in container["secrets"]] == ["AWS_BEARER_TOKEN_BEDROCK"]
    # Plain-SecretString secret: no trailing :KEY:: selector.
    assert container["secrets"][0]["valueFrom"] == "{BEDROCK_API_KEY_SECRET_ARN}"


def test_log_group_is_grants_scoped_and_never_auto_created(container):
    """`ecsTaskExecutionRole` lacks `logs:CreateLogGroup`, and ECS rejects
    `awslogs-create-group: false` (only `true`-or-omit) — the group must be
    pre-created and the option must stay ABSENT. Same pin as cold/spotlight."""
    log_cfg = container["logConfiguration"]
    assert log_cfg["logDriver"] == "awslogs"
    opts = log_cfg["options"]
    assert opts["awslogs-group"] == "/ecs/reciterai-grants"
    assert "awslogs-create-group" not in opts


def test_task_def_uses_placeholders_for_account_specific_arns(container, task_def):
    assert container["image"] == "{IMAGE_URI}"
    assert task_def["taskRoleArn"] == "{TASK_ROLE_ARN}"
    assert task_def["executionRoleArn"] == "{EXECUTION_ROLE_ARN}"


def test_default_region_env_is_set(container):
    env = {e["name"]: e["value"] for e in container["environment"]}
    assert env["AWS_DEFAULT_REGION"] == "us-east-1"


# ---------------------------------------------------------------------------
# eventbridge.json — the grants rule's ECS target
# ---------------------------------------------------------------------------


def test_grants_rule_is_an_ecs_target(grants_rule):
    assert grants_rule["target"]["kind"] == "ecs"


def test_grants_rule_targets_the_grants_task_family(grants_rule):
    tmpl = grants_rule["target"]["task_definition_template"]
    assert tmpl.endswith("task-definition/reciterai-grants")


def test_grants_rule_reuses_the_deployed_invoke_ecs_role(grants_rule):
    assert grants_rule["target"]["role_arn_template"].endswith(
        "role/reciterai-eventbridge-invoke-ecs"
    )


def test_grants_rule_fires_before_sps_projection_and_enrichment(grants_rule):
    """03:00 UTC: the sweep LAUNCHES 4h before SPS's 07:00 nightly
    projection reads the GRANT# rows and before the 11:00 UTC enrichment tick."""
    assert grants_rule["schedule_expression"] == "cron(0 3 * * ? *)"
    assert grants_rule["state"] == "ENABLED"

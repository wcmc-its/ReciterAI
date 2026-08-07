"""Phase 10 T12 — infra/eventbridge.json shape contract.

The deploy script (scripts/deploy_cron.sh) reads fields by jq path.
This test pins those paths so future edits to the config can't
silently break deploy_cron without a test failure.

We also verify:
- The five expected rules are present (hot weekly, spotlight monthly,
  drift daily, onboarding-detector daily, enrichment daily — #37 PR 4)
  with the cron expressions locked in plan-phase (Open Q 10.1 → hot path
  = `cron(0 12 ? * MON *)`; onboarding detector daily = `cron(0 13 * * ? *)`,
  #80 Phase 2; enrichment daily = `cron(0 11 * * ? *)`, #37 PR 4).
- Each rule's target_arn template uses {account_id} + {region}
  placeholders so the deploy script's substitution is the only place
  account/region are bound.
- The Lambda IAM policy doc has the actions called out in PLAN T12:
  DynamoDB RW on reciterai, S3 RW on wcmc-reciterai-*, bedrock invoke
  + batch, states:StartExecution, events:PutEvents.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
EB_PATH = REPO_ROOT / "infra" / "eventbridge.json"
IAM_PATH = REPO_ROOT / "infra" / "lambda_iam_policy.json"


@pytest.fixture(scope="module")
def eventbridge_config() -> dict:
    return json.loads(EB_PATH.read_text())


@pytest.fixture(scope="module")
def iam_policy() -> dict:
    return json.loads(IAM_PATH.read_text())


# ---------------------------------------------------------------------------
# eventbridge.json — top-level + rule shape
# ---------------------------------------------------------------------------


def test_eventbridge_has_region(eventbridge_config):
    assert eventbridge_config["region"]


def test_eventbridge_defines_six_rules(eventbridge_config):
    names = [r["name"] for r in eventbridge_config["rules"]]
    assert names == [
        "reciterai-hot-weekly",
        "reciterai-spotlight-monthly",
        "reciterai-drift-daily",
        "reciterai-onboarding-detector-daily",
        "reciterai-enrichment-daily",
        "reciterai-taxonomy-drift-daily",
    ]


@pytest.mark.parametrize(
    "rule_name, expected_schedule",
    [
        ("reciterai-hot-weekly", "cron(0 12 ? * MON *)"),
        ("reciterai-spotlight-monthly", "cron(0 13 1 * ? *)"),
        ("reciterai-drift-daily", "cron(0 14 * * ? *)"),
        ("reciterai-onboarding-detector-daily", "cron(0 13 * * ? *)"),
        ("reciterai-enrichment-daily", "cron(0 11 * * ? *)"),
        ("reciterai-taxonomy-drift-daily", "cron(0 15 * * ? *)"),
    ],
)
def test_cron_expressions_match_plan(eventbridge_config, rule_name, expected_schedule):
    rule = next(r for r in eventbridge_config["rules"] if r["name"] == rule_name)
    assert rule["schedule_expression"] == expected_schedule


def test_every_rule_has_required_jq_paths(eventbridge_config):
    """deploy_cron.sh reads these exact paths via jq. Missing one => script fails."""
    required = {
        "name", "description", "schedule_expression", "state", "target",
    }
    # All targets need id/kind/arn_template; `input` is required for
    # step_functions + lambda targets (EventBridge forwards it to the target),
    # but ECS targets do NOT use Input — the task definition's default
    # command runs and the EventBridge Input field is dead for direct
    # RunTask launches (no input transformer wired). See deploy_cron.sh's
    # ecs branch comment.
    target_required = {"id", "kind", "arn_template"}
    for rule in eventbridge_config["rules"]:
        assert required.issubset(rule.keys()), (
            f"rule {rule.get('name')} missing keys: {required - set(rule.keys())}"
        )
        assert target_required.issubset(rule["target"].keys())
        if rule["target"]["kind"] in {"step_functions", "lambda"}:
            assert "input" in rule["target"], (
                f"{rule['name']}: {rule['target']['kind']} target needs input"
            )


def test_step_functions_target_has_role_arn(eventbridge_config):
    """Step Functions targets need RoleArn for EventBridge to start execution."""
    sf_rules = [r for r in eventbridge_config["rules"] if r["target"]["kind"] == "step_functions"]
    assert sf_rules, "expected at least one step_functions target (hot path)"
    for rule in sf_rules:
        assert "role_arn_template" in rule["target"]


def test_ecs_target_has_required_runtask_fields(eventbridge_config):
    """ECS targets need role_arn_template (for RunTask invocation),
    task_definition_template (passed in EcsParameters), launch_type,
    and a network_configuration block."""
    ecs_rules = [r for r in eventbridge_config["rules"] if r["target"]["kind"] == "ecs"]
    assert ecs_rules, "expected at least one ecs target (enrichment, #37 PR 4)"
    for rule in ecs_rules:
        target = rule["target"]
        assert "role_arn_template" in target
        assert "task_definition_template" in target
        assert "launch_type" in target
        assert "network_configuration" in target


def test_target_arn_templates_use_placeholders(eventbridge_config):
    """Account/region must NOT be hard-coded; deploy_cron.sh substitutes."""
    for rule in eventbridge_config["rules"]:
        arn = rule["target"]["arn_template"]
        assert "{account_id}" in arn, f"{rule['name']}: arn_template missing {{account_id}}"
        assert "{region}" in arn, f"{rule['name']}: arn_template missing {{region}}"


def test_rule_states_are_valid(eventbridge_config):
    for rule in eventbridge_config["rules"]:
        assert rule["state"] in {"ENABLED", "DISABLED"}


def test_rule_descriptions_fit_eventbridge_512_cap(eventbridge_config):
    """PutRule rejects descriptions over 512 chars (ValidationException) —
    found live 2026-08-07 deploying the spotlight rule. Rationale prose
    belongs in schedule_comment or the task-def $schema_note, which
    EventBridge never sees."""
    for rule in eventbridge_config["rules"]:
        assert len(rule["description"]) <= 512, (
            f"{rule['name']}: description is {len(rule['description'])} chars"
        )


def test_target_kinds_are_supported_by_deploy_script(eventbridge_config):
    supported = {"step_functions", "lambda", "ecs"}
    for rule in eventbridge_config["rules"]:
        assert rule["target"]["kind"] in supported


# ---------------------------------------------------------------------------
# lambda_iam_policy.json — required actions per PLAN T12
# ---------------------------------------------------------------------------


def _all_actions(policy: dict) -> set[str]:
    out: set[str] = set()
    for stmt in policy["Statement"]:
        action = stmt["Action"]
        if isinstance(action, str):
            out.add(action)
        else:
            out.update(action)
    return out


def test_iam_policy_is_valid_v2012(iam_policy):
    assert iam_policy["Version"] == "2012-10-17"
    assert isinstance(iam_policy["Statement"], list)
    assert len(iam_policy["Statement"]) >= 1


def test_iam_policy_includes_plan_t12_actions(iam_policy):
    actions = _all_actions(iam_policy)
    # PLAN §T12 explicit list:
    assert "dynamodb:PutItem" in actions
    assert "dynamodb:Query" in actions
    assert "s3:PutObject" in actions
    assert "s3:GetObject" in actions
    assert "bedrock:InvokeModel" in actions
    assert "bedrock:CreateModelInvocationJob" in actions
    assert "states:StartExecution" in actions
    assert "events:PutEvents" in actions


def test_iam_policy_scopes_dynamodb_to_reciterai_table(iam_policy):
    ddb_stmts = [
        s for s in iam_policy["Statement"]
        if any(a.startswith("dynamodb:") for a in (
            s["Action"] if isinstance(s["Action"], list) else [s["Action"]]
        ))
    ]
    assert ddb_stmts, "expected at least one DynamoDB statement"
    for stmt in ddb_stmts:
        resources = stmt["Resource"] if isinstance(stmt["Resource"], list) else [stmt["Resource"]]
        assert all("reciterai" in r for r in resources), (
            f"DynamoDB statement leaks beyond reciterai: {resources}"
        )


def test_iam_policy_scopes_s3_to_wcmc_reciterai_buckets(iam_policy):
    s3_stmts = [
        s for s in iam_policy["Statement"]
        if any(a.startswith("s3:") for a in (
            s["Action"] if isinstance(s["Action"], list) else [s["Action"]]
        ))
    ]
    assert s3_stmts, "expected at least one S3 statement"
    for stmt in s3_stmts:
        resources = stmt["Resource"] if isinstance(stmt["Resource"], list) else [stmt["Resource"]]
        assert all("wcmc-reciterai-" in r for r in resources), (
            f"S3 statement leaks beyond wcmc-reciterai-*: {resources}"
        )


def test_iam_policy_grants_github_token_secret(iam_policy):
    """The onboarding detector reads its GitHub PAT from the
    reciterai/github-token Secrets Manager secret (#80 Phase 2 / PR 6)."""
    secret_resources: set[str] = set()
    for stmt in iam_policy["Statement"]:
        actions = (
            stmt["Action"] if isinstance(stmt["Action"], list) else [stmt["Action"]]
        )
        if not any(a.startswith("secretsmanager:") for a in actions):
            continue
        resources = (
            stmt["Resource"]
            if isinstance(stmt["Resource"], list)
            else [stmt["Resource"]]
        )
        secret_resources.update(resources)
    assert any("reciterai/github-token" in r for r in secret_resources), (
        "no Secrets Manager resource grants reciterai/github-token "
        f"(the detector's PAT secret): {sorted(secret_resources)}"
    )

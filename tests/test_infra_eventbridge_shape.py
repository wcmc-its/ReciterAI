"""Phase 10 T12 — infra/eventbridge.json shape contract.

The deploy script (scripts/deploy_cron.sh) reads fields by jq path.
This test pins those paths so future edits to the config can't
silently break deploy_cron without a test failure.

We also verify:
- The three expected rules are present (hot weekly, spotlight monthly,
  drift daily) with the cron expressions locked in plan-phase (Open Q
  10.1 → hot path = `cron(0 12 ? * MON *)`).
- Each rule's target_arn template uses {account_id} + {region}
  placeholders so the deploy script's substitution is the only place
  account/region are bound.
- The IAM policy doc has the actions called out in PLAN T12: DynamoDB
  RW on reciterai, S3 RW on wcmc-reciterai-*, bedrock invoke +
  batch, states:StartExecution, events:PutEvents.
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


def test_eventbridge_defines_three_rules(eventbridge_config):
    names = [r["name"] for r in eventbridge_config["rules"]]
    assert names == [
        "reciterai-hot-weekly",
        "reciterai-spotlight-monthly",
        "reciterai-drift-daily",
    ]


@pytest.mark.parametrize(
    "rule_name, expected_schedule",
    [
        ("reciterai-hot-weekly", "cron(0 12 ? * MON *)"),
        ("reciterai-spotlight-monthly", "cron(0 13 1 * ? *)"),
        ("reciterai-drift-daily", "cron(0 14 * * ? *)"),
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
    target_required = {"id", "kind", "arn_template", "input"}
    for rule in eventbridge_config["rules"]:
        assert required.issubset(rule.keys()), (
            f"rule {rule.get('name')} missing keys: {required - set(rule.keys())}"
        )
        assert target_required.issubset(rule["target"].keys())


def test_step_functions_target_has_role_arn(eventbridge_config):
    """Step Functions targets need RoleArn for EventBridge to start execution."""
    sf_rules = [r for r in eventbridge_config["rules"] if r["target"]["kind"] == "step_functions"]
    assert sf_rules, "expected at least one step_functions target (hot path)"
    for rule in sf_rules:
        assert "role_arn_template" in rule["target"]


def test_target_arn_templates_use_placeholders(eventbridge_config):
    """Account/region must NOT be hard-coded; deploy_cron.sh substitutes."""
    for rule in eventbridge_config["rules"]:
        arn = rule["target"]["arn_template"]
        assert "{account_id}" in arn, f"{rule['name']}: arn_template missing {{account_id}}"
        assert "{region}" in arn, f"{rule['name']}: arn_template missing {{region}}"


def test_rule_states_are_valid(eventbridge_config):
    for rule in eventbridge_config["rules"]:
        assert rule["state"] in {"ENABLED", "DISABLED"}


def test_target_kinds_are_supported_by_deploy_script(eventbridge_config):
    supported = {"step_functions", "lambda"}
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

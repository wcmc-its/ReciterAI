"""infra/{cores_task_iam_policy,cores_task_definition}.json shape contracts.

Mirrors `test_infra_grants_fargate_shape.py` / `test_infra_cold_run_fargate_shape.py`.
Pins the daily core-facility usage-inference launch path. Cores-specific pins (vs the
other tasks), each of which is a decision that is silently reversible by a one-word
command edit:

- `--core 14` is the whole scope. The nightly re-scores ONE core; the other nine stay
  operator-run. Widening it is a real decision (nine more cores' rows rewritten every
  night, on cores with no reviewer to catch a regression), not a tidy-up.
- `--with-llm` is deliberately ABSENT. `run.py` persists only confirmed/candidate rows,
  so 79,860 of the 80,203 corpus publications carry no stored `llm_score` on any given
  night — a corpus-wide LLM pass is ~80k Haiku screens EVERY night, not just the first.
  How much Bedrock a nightly may spend is an open decision in its own issue.
- `--llm-carry-forward` is REQUIRED. `persist.put_core_usage` REMOVEs every owned
  optional the run did not produce (#384), so a nightly without it strips
  `llm_score`/`llm_rationale` off every row it re-surfaces — on core 14 that is the chip
  all 62 open review-queue rows carry, and the run still exits green.
- `--all-cores-screen` must stay out: experimental, and it lost 7-11% screen recall on
  the 237-pilot.
- The task role is DynamoDB + Logs + a READ-ONLY grant on the tools artifact prefix.
  pipeline_cores publishes no artifact, so unlike cold/grants there is no S3 write; the
  read exists solely because `--with-method-families` makes load_family_index fetch it.
- Sized 1 vCPU / 4 GB on the CORPUS LOAD (80,203 titles+abstracts held for the life of
  the run), not on concurrency — the nightly command has no Bedrock fan-out at all.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TASK_POLICY_PATH = REPO_ROOT / "infra" / "cores_task_iam_policy.json"
TASK_DEF_PATH = REPO_ROOT / "infra" / "cores_task_definition.json"
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
def command(container) -> str:
    return " ".join(container["command"])


@pytest.fixture(scope="module")
def cores_rule() -> dict:
    cfg = json.loads(EB_PATH.read_text())
    return next(r for r in cfg["rules"] if r["name"] == "reciterai-cores-daily")


def _all_actions(policy: dict) -> set:
    out = set()
    for stmt in policy["Statement"]:
        action = stmt["Action"]
        if isinstance(action, str):
            out.add(action)
        else:
            out.update(action)
    return out


def _resources_for_prefix(policy: dict, prefix: str) -> set:
    """Union of Resource ARNs across statements that carry any `prefix:` action."""
    out = set()
    for stmt in policy["Statement"]:
        actions = stmt["Action"] if isinstance(stmt["Action"], list) else [stmt["Action"]]
        if not any(a.startswith(prefix) for a in actions):
            continue
        resources = stmt["Resource"] if isinstance(stmt["Resource"], list) else [stmt["Resource"]]
        out.update(resources)
    return out


# ---------------------------------------------------------------------------
# cores_task_iam_policy.json
# ---------------------------------------------------------------------------


def test_task_policy_is_valid_v2012(task_policy):
    assert task_policy["Version"] == "2012-10-17"
    assert isinstance(task_policy["Statement"], list)
    assert len(task_policy["Statement"]) >= 1


def test_task_policy_does_not_grant_bedrock_invoke(task_policy):
    """Doubly absent: every bedrock-runtime call authenticates via the
    AWS_BEARER_TOKEN_BEDROCK bearer token rather than SigV4 (same posture as every
    sibling family), AND the nightly command makes no Bedrock call at all."""
    bedrock_actions = {a for a in _all_actions(task_policy) if a.startswith("bedrock:")}
    assert bedrock_actions == set(), (
        f"cores task role MUST NOT carry bedrock:* actions; found: {bedrock_actions}"
    )


def test_task_policy_has_no_secretsmanager_statement(task_policy):
    """The DB_* keys and the Bedrock token are injected as env vars by the EXECUTION
    role at container start; nothing in the runtime path calls GetSecretValue."""
    sm_actions = {a for a in _all_actions(task_policy) if a.startswith("secretsmanager:")}
    assert sm_actions == set(), (
        f"cores task role should carry NO secretsmanager:* actions; found: {sm_actions}"
    )


def test_task_policy_covers_the_artifact_keys_the_runtime_actually_reads(task_policy):
    """The keys come from the CODE, not from a copy of them here.

    This replaces a test that asserted the policy carried NO s3:* actions. That was
    true until `reciterai-cores:4` added `--with-method-families`, which makes
    `method_families.load_family_index` read the tools artifact on every run. The
    grant was never added, and on 2026-09-07 the 05:00 run died 3ms in on
    AccessDenied — taking the WHOLE scoring pass with it, because
    `load_family_index` raises rather than degrading to an empty index that
    `put_core_usage` would turn into a REMOVE of `method_tier`/`method_evidence` on
    every previously scored row.

    Reading TOOLS_KEY/TOOL_CONTEXT_KEY from the module is the point: a future key
    move, or a new artifact read, fails here instead of at 05:00.
    """
    from pipeline_cores.method_families import TOOL_CONTEXT_KEY, TOOLS_KEY
    from utils.s3_client import ARTIFACTS_BUCKET

    reads = _resources_for_prefix(task_policy, "s3:")
    assert reads, "policy grants no s3 access, but load_family_index reads the artifact"
    for key in (TOOLS_KEY, TOOL_CONTEXT_KEY):
        full = f"arn:aws:s3:::{ARTIFACTS_BUCKET}/{key}"
        assert any(
            full.startswith(r.rstrip("*")) for r in reads
        ), f"no s3 Resource covers {full!r}; granted: {reads}"


def test_task_policy_grants_no_s3_write(task_policy):
    """Read-only by design: pipeline_cores publishes no artifact.

    The one WRITE surface is the shared PMC full-text cache, reached only via
    `--fulltext-s3`, which is not in the nightly command — so it is not running at
    all, rather than silently degrading. Enabling that flag needs GetObject +
    PutObject on `cores/fulltext/*` and ListBucket; without them it fails SILENTLY,
    re-fetching from NCBI at full price.
    """
    writes = {
        a
        for a in _all_actions(task_policy)
        if a.startswith("s3:") and a not in {"s3:GetObject"}
    }
    assert writes == set(), f"cores task role should be S3 read-only; found: {writes}"


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
    for r in ddb_resources:
        assert "reciterai" in r, f"DynamoDB leak outside reciterai: {r}"


def test_task_policy_covers_every_dynamodb_call_the_runtime_makes(task_policy):
    """Three call shapes are the whole surface: UpdateItem (put_core_usage),
    Scan (scan_prior_core_usage for the affinity prior + the carry-forward read of
    stored LLM evidence) and GetItem (get_curated_clients on CORE#14/CLIENTS)."""
    actions = _all_actions(task_policy)
    assert {"dynamodb:UpdateItem", "dynamodb:Scan", "dynamodb:GetItem"} <= actions


def test_task_policy_logs_scoped_to_cores_log_group(task_policy):
    actions = _all_actions(task_policy)
    assert "logs:CreateLogStream" in actions
    assert "logs:PutLogEvents" in actions
    assert "logs:CreateLogGroup" not in actions, (
        "the log group is pre-created by hand (README step 1); granting CreateLogGroup "
        "here would mask a missing group instead of failing the deploy"
    )
    log_resources = _resources_for_prefix(task_policy, "logs:")
    assert any("reciterai-cores" in r for r in log_resources), (
        f"CloudWatch Logs scope missing /ecs/reciterai-cores: {sorted(log_resources)}"
    )


# ---------------------------------------------------------------------------
# cores_task_definition.json
# ---------------------------------------------------------------------------


def test_task_def_family_and_fargate_shape(task_def):
    assert task_def["family"] == "reciterai-cores"
    assert task_def["networkMode"] == "awsvpc"
    assert task_def["requiresCompatibilities"] == ["FARGATE"]
    assert task_def["runtimePlatform"]["cpuArchitecture"] == "X86_64"


def test_task_def_is_sized_one_vcpu_four_gb(task_def):
    """Sized on the corpus load, not concurrency: with no --pmids-file the pool is
    None, so fetch_publications holds all 80,203 titles+abstracts for the life of the
    run. NOT 2 GB: run.py writes nothing until every core has finished, so an OOM kill
    costs the whole night's scoring and EventBridge does not retry a task that died."""
    assert task_def["cpu"] == "1024"
    assert task_def["memory"] == "4096"


def test_container_name_matches_family(container, task_def):
    assert container["name"] == task_def["family"] == "reciterai-cores"


def test_default_command_is_the_nightly_invocation(container):
    """EventBridge Input is dead for direct RunTask launches, so the task
    definition's default command IS the daily invocation."""
    assert container["command"] == [
        "sh",
        "-c",
        "python -m pipeline_cores.run --core 14 --with-affinity --alias-search "
        "--llm-carry-forward --with-method-families",
    ]


def test_the_nightly_asks_for_the_method_family_signal(command):
    """#395 ships the signal; without this flag run.py never produces `method_tier`
    or `method_evidence`, so the merged signal is dark and SPS's queue can never
    show a method chip. Worse than dark: both attributes are run.py-owned, so a run
    WITHOUT the flag REMOVEs them from every row it re-surfaces — the #384 trap
    `--llm-carry-forward` exists to close, with no carry-forward of its own here."""
    assert "--with-method-families" in command


def test_the_nightly_is_scoped_to_core_14(command):
    """One core. Widening this rewrites nine more cores' rows every night, on cores
    with no reviewer to catch a regression."""
    assert "--core 14" in command


def test_the_nightly_makes_no_bedrock_call(command):
    """--with-llm over the unscoped corpus is ~80k Haiku screens EVERY night: run.py
    persists only confirmed/candidate rows, so 79,860 of 80,203 corpus publications
    carry no stored llm_score on any given night and --llm-carry-forward cannot bound
    it. The nightly LLM budget is an open decision in its own issue — until it is
    settled this flag stays out, and adding it should trip this test first."""
    assert "--with-llm" not in command


def test_the_nightly_carries_the_stored_llm_evidence_forward(command):
    """The flag this whole design rests on. put_core_usage REMOVEs every owned optional
    the run did not produce (#384), so without --llm-carry-forward the nightly strips
    llm_score/llm_rationale off every row it re-surfaces — all 62 open core-14 rows —
    and still exits green. Dropping it does not fail the run; it empties the queue's
    strongest evidence overnight and looks like a working night."""
    assert "--llm-carry-forward" in command


def test_the_experimental_all_cores_screen_is_not_scheduled(command):
    """--all-cores-screen cuts screen calls ~13x but lost 7-11% screen recall on the
    237-pilot (borderline true positives collapse to score 1). Opt-in until
    re-calibrated to per-core parity."""
    assert "--all-cores-screen" not in command


def test_the_nightly_is_not_scoped_to_a_pmid_pool(command):
    """run.py's own --pmids-file help: a scoped run writes only those rows, so it can
    neither surface a pair outside the set nor demote one. A nightly that cannot find a
    paper it has never seen is not a nightly."""
    assert "--pmids-file" not in command


def test_secrets_are_the_db_credentials_plus_the_bedrock_token(container):
    """The four DB_* keys because utils.db.get_engine reads ReciterDB. The Bedrock
    token despite the nightly making no Bedrock call: it is execution-role-injected at
    no cost, and it is what lets an operator override the command to --with-llm without
    re-registering a task definition. NO OPENAI_API_KEY — pipeline_cores has no OpenAI
    path (unlike the cold run this file was cloned from)."""
    names = [s["name"] for s in container["secrets"]]
    assert names == ["DB_HOST", "DB_USERNAME", "DB_PASSWORD", "DB_NAME",
                     "AWS_BEARER_TOKEN_BEDROCK"]
    assert "OPENAI_API_KEY" not in names
    # Plain-SecretString secret: no trailing :KEY:: selector, unlike the DB keys.
    assert container["secrets"][-1]["valueFrom"] == "{BEDROCK_API_KEY_SECRET_ARN}"


def test_log_group_is_cores_scoped_and_never_auto_created(container):
    """`ecsTaskExecutionRole` lacks `logs:CreateLogGroup`, and ECS rejects
    `awslogs-create-group: false` (only `true`-or-omit) — the group must be
    pre-created and the option must stay ABSENT. Same pin as cold/spotlight/grants."""
    log_cfg = container["logConfiguration"]
    assert log_cfg["logDriver"] == "awslogs"
    opts = log_cfg["options"]
    assert opts["awslogs-group"] == "/ecs/reciterai-cores"
    assert "awslogs-create-group" not in opts


def test_task_def_uses_placeholders_for_account_specific_arns(container, task_def):
    assert container["image"] == "{IMAGE_URI}"
    assert task_def["taskRoleArn"] == "{TASK_ROLE_ARN}"
    assert task_def["executionRoleArn"] == "{EXECUTION_ROLE_ARN}"


def test_default_region_env_is_set(container):
    env = {e["name"]: e["value"] for e in container["environment"]}
    assert env["AWS_DEFAULT_REGION"] == "us-east-1"


def test_task_def_carries_no_key_ecs_would_reject(task_def, container):
    """register-task-definition is run on this file with $schema_note + deploy_notes
    stripped (README step 4). Everything else must be a real RegisterTaskDefinition
    key, so pin the top-level and container key sets against the sibling families."""
    doc_keys = {"$schema_note", "deploy_notes"}
    assert set(task_def) - doc_keys == {
        "family", "networkMode", "requiresCompatibilities", "cpu", "memory",
        "runtimePlatform", "taskRoleArn", "executionRoleArn", "containerDefinitions",
    }
    assert set(container) == {
        "name", "image", "essential", "command", "environment", "secrets",
        "logConfiguration",
    }


# ---------------------------------------------------------------------------
# eventbridge.json — the cores rule's ECS target
# ---------------------------------------------------------------------------


def test_cores_rule_is_an_ecs_target(cores_rule):
    assert cores_rule["target"]["kind"] == "ecs"


def test_cores_rule_targets_the_cores_task_family(cores_rule):
    tmpl = cores_rule["target"]["task_definition_template"]
    assert tmpl.endswith("task-definition/reciterai-cores")


def test_cores_rule_reuses_the_deployed_invoke_ecs_role(cores_rule):
    """That role's policy must be EXTENDED with ecs:RunTask on this new family and
    iam:PassRole for its task/execution roles — the per-family omission has bitten on
    every new family (README step 3)."""
    assert cores_rule["target"]["role_arn_template"].endswith(
        "role/reciterai-eventbridge-invoke-ecs"
    )


def test_cores_rule_fires_two_hours_before_the_sps_projection(cores_rule):
    """05:00 UTC = 01:00 EDT. SPS's own cron(0 7 * * ? *) nightly is what projects the
    CORE# rows into MySQL for display, so a night's scoring has to land before SPS reads
    it. If the run overruns, move THIS cron earlier — never the SPS nightly."""
    assert cores_rule["schedule_expression"] == "cron(0 5 * * ? *)"
    assert cores_rule["state"] == "ENABLED"


def test_cores_rule_description_fits_the_eventbridge_cap(cores_rule):
    """PutRule.Description caps at 512 and deploy_cron.sh passes the field straight
    through; botocore does not enforce it client-side, so an oversized description
    survives --dry-run and fails only on the real deploy, aborting before put-targets.
    The overflow prose lives in `description_note`, which is never sent."""
    assert len(cores_rule["description"]) <= 512
    assert cores_rule["description_note"]

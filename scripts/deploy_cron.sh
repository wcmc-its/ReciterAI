#!/usr/bin/env bash
#
# deploy_cron.sh — Phase 10 T12 / D-10
#
# Single-file IaC deploy script: applies the EventBridge rules and target
# bindings from infra/eventbridge.json. Thin wrapper around the AWS CLI;
# no Terraform, no CDK. Migration trigger to a real IaC tool is
# documented in infra/README.md.
#
# Usage:
#   scripts/deploy_cron.sh --dry-run     # print AWS CLI calls without executing
#   scripts/deploy_cron.sh                # execute deploys
#   scripts/deploy_cron.sh --rule <name>  # restrict to a single rule
#
# Required env:
#   AWS_ACCOUNT_ID  — 12-digit account number used in ARN templating
#   AWS_REGION      — overrides region in eventbridge.json if set
#
# Required env for any rule with target.kind == "ecs":
#   ECS_CLUSTER_NAME                         — the cluster to RunTask in
#   RECITERAI_ENRICHMENT_SUBNETS             — comma-separated subnet IDs
#                                              (must reach ReciterDB; reuse
#                                              the hot-path Lambdas' VPC layout)
#   RECITERAI_ENRICHMENT_SECURITY_GROUPS     — comma-separated security group
#                                              IDs (egress to Bedrock + OpenAI
#                                              + MariaDB + Secrets Manager)
#
# What it does, per rule:
#   1. aws events put-rule  (schedule_expression, state, description)
#   2. aws events put-targets  (target ARN with input JSON or EcsParameters)
#   3. For Lambda targets: aws lambda add-permission  (allow eventbridge to invoke)
#
# IAM roles for targets are NOT created here — they must already exist.
# The minimum-privilege policy for Lambda execution roles is documented
# in infra/lambda_iam_policy.json; the Fargate task-role policy in
# infra/enrichment_task_iam_policy.json (#37 PR 4). Attach them manually
# (or via your preferred IAM workflow) before running this script.
#
# Acceptance: `scripts/deploy_cron.sh --dry-run` prints every aws call
# without invoking AWS. Post-deploy, `aws events list-rules` shows the
# three rules.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG="${REPO_ROOT}/infra/eventbridge.json"

DRY_RUN=0
ONLY_RULE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --rule)    ONLY_RULE="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,30p' "$0"
      exit 0
      ;;
    *)
      echo "deploy_cron.sh: unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------

if [[ ! -f "$CONFIG" ]]; then
  echo "deploy_cron.sh: config not found: $CONFIG" >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "deploy_cron.sh: requires jq on PATH" >&2
  exit 1
fi

if [[ -z "${AWS_ACCOUNT_ID:-}" ]]; then
  echo "deploy_cron.sh: AWS_ACCOUNT_ID must be set (12-digit account id)" >&2
  exit 1
fi

REGION="${AWS_REGION:-$(jq -r '.region' "$CONFIG")}"
if [[ -z "$REGION" || "$REGION" == "null" ]]; then
  echo "deploy_cron.sh: region not set (AWS_REGION env or top-level region in eventbridge.json)" >&2
  exit 1
fi

ACCOUNT_ID="$AWS_ACCOUNT_ID"

# Run a command, or echo it if --dry-run.
run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '[dry-run] '
    printf '%q ' "$@"
    printf '\n'
  else
    "$@"
  fi
}

# Substitute {region} and {account_id} placeholders.
expand() {
  local raw="$1"
  echo "${raw//\{region\}/$REGION}" | sed "s|{account_id}|$ACCOUNT_ID|g"
}

# ---------------------------------------------------------------------------
# Per-rule deploy
# ---------------------------------------------------------------------------

rule_count="$(jq '.rules | length' "$CONFIG")"
if [[ "$rule_count" -eq 0 ]]; then
  echo "deploy_cron.sh: no rules defined in $CONFIG" >&2
  exit 1
fi

for i in $(seq 0 $((rule_count - 1))); do
  name="$(jq -r ".rules[$i].name" "$CONFIG")"
  if [[ -n "$ONLY_RULE" && "$name" != "$ONLY_RULE" ]]; then
    continue
  fi

  description="$(jq -r ".rules[$i].description" "$CONFIG")"
  schedule="$(jq -r ".rules[$i].schedule_expression" "$CONFIG")"
  state="$(jq -r ".rules[$i].state" "$CONFIG")"

  target_id="$(jq -r ".rules[$i].target.id" "$CONFIG")"
  target_kind="$(jq -r ".rules[$i].target.kind" "$CONFIG")"
  target_arn_template="$(jq -r ".rules[$i].target.arn_template" "$CONFIG")"
  target_arn="$(expand "$target_arn_template")"

  echo ">> deploying rule: $name ($schedule)"

  run aws events put-rule \
    --region "$REGION" \
    --name "$name" \
    --schedule-expression "$schedule" \
    --state "$state" \
    --description "$description"

  case "$target_kind" in
    step_functions)
      input_json="$(jq -c ".rules[$i].target.input" "$CONFIG")"
      role_arn_template="$(jq -r ".rules[$i].target.role_arn_template" "$CONFIG")"
      role_arn="$(expand "$role_arn_template")"
      targets_json="$(jq -nc \
        --arg id "$target_id" \
        --arg arn "$target_arn" \
        --arg role "$role_arn" \
        --arg input "$input_json" \
        '[{Id: $id, Arn: $arn, RoleArn: $role, Input: $input}]')"
      run aws events put-targets \
        --region "$REGION" \
        --rule "$name" \
        --targets "$targets_json"
      ;;
    lambda)
      input_json="$(jq -c ".rules[$i].target.input" "$CONFIG")"
      targets_json="$(jq -nc \
        --arg id "$target_id" \
        --arg arn "$target_arn" \
        --arg input "$input_json" \
        '[{Id: $id, Arn: $arn, Input: $input}]')"
      run aws events put-targets \
        --region "$REGION" \
        --rule "$name" \
        --targets "$targets_json"

      # Permit EventBridge to invoke the Lambda. add-permission is
      # idempotent only by statement-id, so we use a deterministic id
      # derived from the rule name. Failure on duplicate is non-fatal.
      stmt_id="allow-eventbridge-${name}"
      rule_arn="arn:aws:events:${REGION}:${ACCOUNT_ID}:rule/${name}"
      if [[ "$DRY_RUN" -eq 1 ]]; then
        run aws lambda add-permission \
          --region "$REGION" \
          --function-name "$target_arn" \
          --statement-id "$stmt_id" \
          --action lambda:InvokeFunction \
          --principal events.amazonaws.com \
          --source-arn "$rule_arn"
      else
        aws lambda add-permission \
          --region "$REGION" \
          --function-name "$target_arn" \
          --statement-id "$stmt_id" \
          --action lambda:InvokeFunction \
          --principal events.amazonaws.com \
          --source-arn "$rule_arn" \
          >/dev/null 2>&1 || echo "  (lambda permission already present for $stmt_id)"
      fi
      ;;
    ecs)
      # EventBridge -> ECS RunTask. Unlike Lambda/Step Functions, the
      # `Input` field on the EventBridge target is NOT forwarded to the
      # container (ECS uses input transformers for that, which we don't
      # need — the task definition's default command runs as-is). So
      # `Input` from eventbridge.json is intentionally skipped here.
      #
      # `target.arn_template` resolves to the ECS CLUSTER ARN (not the
      # task definition ARN); the task definition is passed inside
      # EcsParameters.
      if [[ -z "${RECITERAI_ENRICHMENT_SUBNETS:-}" || -z "${RECITERAI_ENRICHMENT_SECURITY_GROUPS:-}" ]]; then
        echo "deploy_cron.sh: ecs target '$name' needs RECITERAI_ENRICHMENT_SUBNETS and RECITERAI_ENRICHMENT_SECURITY_GROUPS env vars (comma-separated)." >&2
        exit 1
      fi

      role_arn_template="$(jq -r ".rules[$i].target.role_arn_template" "$CONFIG")"
      role_arn="$(expand "$role_arn_template")"
      task_def_template="$(jq -r ".rules[$i].target.task_definition_template" "$CONFIG")"
      task_def_arn="$(expand "$task_def_template")"
      launch_type="$(jq -r ".rules[$i].target.launch_type // \"FARGATE\"" "$CONFIG")"
      platform_version="$(jq -r ".rules[$i].target.platform_version // \"LATEST\"" "$CONFIG")"
      assign_public_ip="$(jq -r ".rules[$i].target.network_configuration.assign_public_ip // \"DISABLED\"" "$CONFIG")"

      # Comma-separated -> JSON array.
      subnets_json="$(printf '%s' "$RECITERAI_ENRICHMENT_SUBNETS" | jq -Rc 'split(",") | map(gsub("^\\s+|\\s+$"; ""))')"
      sgs_json="$(printf '%s' "$RECITERAI_ENRICHMENT_SECURITY_GROUPS" | jq -Rc 'split(",") | map(gsub("^\\s+|\\s+$"; ""))')"

      targets_json="$(jq -nc \
        --arg id "$target_id" \
        --arg arn "$target_arn" \
        --arg role "$role_arn" \
        --arg task_def "$task_def_arn" \
        --arg launch_type "$launch_type" \
        --arg platform_version "$platform_version" \
        --arg assign_public_ip "$assign_public_ip" \
        --argjson subnets "$subnets_json" \
        --argjson sgs "$sgs_json" \
        '[{
          Id: $id,
          Arn: $arn,
          RoleArn: $role,
          EcsParameters: {
            TaskDefinitionArn: $task_def,
            TaskCount: 1,
            LaunchType: $launch_type,
            PlatformVersion: $platform_version,
            NetworkConfiguration: {
              awsvpcConfiguration: {
                Subnets: $subnets,
                SecurityGroups: $sgs,
                AssignPublicIp: $assign_public_ip
              }
            }
          }
        }]')"
      run aws events put-targets \
        --region "$REGION" \
        --rule "$name" \
        --targets "$targets_json"
      ;;
    *)
      echo "deploy_cron.sh: unknown target kind: $target_kind" >&2
      exit 1
      ;;
  esac
done

echo
echo "deploy_cron.sh: done."
if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "(dry-run: no AWS calls were made.)"
fi

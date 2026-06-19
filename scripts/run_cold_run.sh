#!/usr/bin/env bash
#
# run_cold_run.sh — #240 cold-run Fargate launcher
#
# Launches the ReciterAI taxonomy COLD-RUN (`python -m pipeline_cold.run`) on
# AWS Fargate via `aws ecs run-task`, on the `reciterai-cold` task definition.
# Two modes — they do different things:
#
#   dry-run  — plumbing gate. Overrides the command to `pipeline_cold.run
#              --dry-run`: prints the stage plan and exits 0 without invoking
#              any stage. Proves the task def launches, the image is right,
#              secrets land in env, the task role reaches AWS, and CloudWatch
#              logging works. Fast, zero Bedrock cost. This mode POLLS to a
#              terminal state and asserts exit 0 (suitable as a deploy gate).
#
#   run      — the real, multi-hour, PUBLISHING cold-run (full-corpus rebuild
#              ending in the spotlight + hierarchy S3 publishes). This mode
#              does NOT block: it launches the task, prints the task ARN and
#              the CloudWatch / describe-tasks monitoring commands, and exits.
#              Optionally resume a died-mid-way run with COLD_FROM_STAGE.
#
# This is operator-gated infrastructure: the cold-run re-clusters the taxonomy
# and overwrites the live `latest/` pointers SPS consumes. Run the dry-run gate
# first; never point this at prod without intending a real publish.
#
# Usage:
#   AWS_REGION=us-east-1 \
#   ECS_CLUSTER_NAME=reciterai-cluster \
#   TASK_DEFINITION=reciterai-cold \
#   RECITERAI_COLD_SUBNETS=subnet-...,subnet-... \
#   RECITERAI_COLD_SECURITY_GROUPS=sg-... \
#   COLD_MODE=dry-run \
#   scripts/run_cold_run.sh
#
#   # Real run:
#   COLD_MODE=run scripts/run_cold_run.sh
#
#   # Resume a died-mid-way run from a stage:
#   COLD_MODE=run COLD_FROM_STAGE=publish_hierarchy scripts/run_cold_run.sh
#
# Required env:
#   AWS_REGION                       — defaults to us-east-1
#   ECS_CLUSTER_NAME                 — defaults to reciterai-cluster
#   TASK_DEFINITION                  — defaults to reciterai-cold
#   COLD_MODE                        — "dry-run" or "run"
#   RECITERAI_COLD_SUBNETS           — comma-separated subnet IDs (falls back to
#                                      RECITERAI_ENRICHMENT_SUBNETS — same VPC layout)
#   RECITERAI_COLD_SECURITY_GROUPS   — comma-separated SG IDs (falls back to
#                                      RECITERAI_ENRICHMENT_SECURITY_GROUPS)
# Optional env:
#   COLD_FROM_STAGE                  — resume from this stage (run mode only)
#   COLD_DRYRUN_TIMEOUT_SECONDS      — dry-run poll timeout (default 600)

set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
CLUSTER="${ECS_CLUSTER_NAME:-reciterai-cluster}"
TASK_DEF="${TASK_DEFINITION:-reciterai-cold}"
CONTAINER="reciterai-cold"
MODE="${COLD_MODE:-}"
FROM_STAGE="${COLD_FROM_STAGE:-}"
DRYRUN_TIMEOUT="${COLD_DRYRUN_TIMEOUT_SECONDS:-600}"
POLL_INTERVAL="${COLD_POLL_INTERVAL:-15}"
LOG_GROUP="/ecs/reciterai-cold"

SUBNETS="${RECITERAI_COLD_SUBNETS:-${RECITERAI_ENRICHMENT_SUBNETS:-}}"
SECURITY_GROUPS="${RECITERAI_COLD_SECURITY_GROUPS:-${RECITERAI_ENRICHMENT_SECURITY_GROUPS:-}}"

if [[ "$MODE" != "dry-run" && "$MODE" != "run" ]]; then
  echo "run_cold_run.sh: COLD_MODE must be 'dry-run' or 'run' (got: '${MODE}')." >&2
  echo "  dry-run  — plumbing gate; --dry-run, prints the plan, polls to exit 0." >&2
  echo "  run      — the real multi-hour publishing cold-run; launches and returns." >&2
  exit 1
fi
if [[ -z "$SUBNETS" || -z "$SECURITY_GROUPS" ]]; then
  echo "run_cold_run.sh: set RECITERAI_COLD_SUBNETS + RECITERAI_COLD_SECURITY_GROUPS" >&2
  echo "  (or the RECITERAI_ENRICHMENT_* equivalents — same VPC layout), comma-separated." >&2
  exit 1
fi
if [[ -n "$FROM_STAGE" && "$MODE" != "run" ]]; then
  echo "run_cold_run.sh: COLD_FROM_STAGE is only valid with COLD_MODE=run." >&2
  exit 1
fi

# Comma-separated -> JSON array.
subnets_json="$(printf '%s' "$SUBNETS" | jq -Rc 'split(",") | map(gsub("^\\s+|\\s+$"; ""))')"
sgs_json="$(printf '%s' "$SECURITY_GROUPS" | jq -Rc 'split(",") | map(gsub("^\\s+|\\s+$"; ""))')"

# Build the command override. dry-run always overrides; a real run uses the task
# def default command (full cold-run) unless resuming from a stage.
overrides_args=()
if [[ "$MODE" == "dry-run" ]]; then
  cmd_json='["python","-m","pipeline_cold.run","--dry-run"]'
  echo ">> cold-run (dry-run plumbing gate) on task def $TASK_DEF"
elif [[ -n "$FROM_STAGE" ]]; then
  cmd_json="$(jq -nc --arg s "$FROM_STAGE" '["python","-m","pipeline_cold.run","--from-stage",$s]')"
  echo ">> cold-run (REAL, resume from stage '$FROM_STAGE') on task def $TASK_DEF"
else
  cmd_json=""
  echo ">> cold-run (REAL, full multi-hour publishing run) on task def $TASK_DEF"
fi

if [[ -n "$cmd_json" ]]; then
  overrides_json="$(jq -nc --argjson cmd "$cmd_json" \
    --arg name "$CONTAINER" \
    '{containerOverrides:[{name:$name, command:$cmd}]}')"
  overrides_args=(--overrides "$overrides_json")
fi

network_json="$(jq -nc \
  --argjson subnets "$subnets_json" \
  --argjson sgs "$sgs_json" \
  '{awsvpcConfiguration:{subnets:$subnets,securityGroups:$sgs,assignPublicIp:"DISABLED"}}')"

TASK_ARN="$(
  aws ecs run-task \
    --region "$REGION" \
    --cluster "$CLUSTER" \
    --task-definition "$TASK_DEF" \
    --launch-type FARGATE \
    --platform-version LATEST \
    --network-configuration "$network_json" \
    "${overrides_args[@]}" \
    --started-by "run_cold_run-$(date -u +%Y%m%dT%H%M%SZ)" \
    --query 'tasks[0].taskArn' \
    --output text
)"
if [[ -z "$TASK_ARN" || "$TASK_ARN" == "None" ]]; then
  echo "run_cold_run.sh: run-task returned no taskArn — image pull / IAM / network misconfig?" >&2
  exit 1
fi
echo "   task arn: $TASK_ARN"
TASK_ID="${TASK_ARN##*/}"

# Real run: do NOT block for hours. Print the monitoring handles and exit.
if [[ "$MODE" == "run" ]]; then
  echo
  echo "run_cold_run.sh: LAUNCHED (real cold-run, multi-hour). Not blocking."
  echo "  Monitor logs:   aws logs tail $LOG_GROUP --region $REGION --follow --since 5m"
  echo "  Task status:    aws ecs describe-tasks --region $REGION --cluster $CLUSTER --tasks $TASK_ARN \\"
  echo "                    --query 'tasks[0].{status:lastStatus,stopped:stoppedReason,exit:containers[0].exitCode}'"
  echo "  Expect STAGE# rows in DynamoDB ('reciterai') per stage; the final publishes overwrite"
  echo "  spotlight/latest + hierarchy latest/manifest.json. Validate after STOPPED + exitCode 0."
  exit 0
fi

# Dry-run: poll to a terminal state and assert clean exit (deploy gate).
echo "   polling for terminal state (timeout ${DRYRUN_TIMEOUT}s)..."
deadline=$(( $(date +%s) + DRYRUN_TIMEOUT ))
last_status=""
task_json=""
while :; do
  if [[ $(date +%s) -ge $deadline ]]; then
    echo "run_cold_run.sh: timeout after ${DRYRUN_TIMEOUT}s. Last status: $last_status." >&2
    exit 1
  fi
  task_json="$(
    aws ecs describe-tasks \
      --region "$REGION" \
      --cluster "$CLUSTER" \
      --tasks "$TASK_ARN" \
      --output json
  )"
  last_status="$(echo "$task_json" | jq -r '.tasks[0].lastStatus // "UNKNOWN"')"
  echo "   status: $last_status"
  if [[ "$last_status" == "STOPPED" ]]; then
    break
  fi
  sleep "$POLL_INTERVAL"
done

exit_code="$(echo "$task_json" | jq -r '.tasks[0].containers[0].exitCode // "null"')"
stopped_reason="$(echo "$task_json" | jq -r '.tasks[0].stoppedReason // ""')"
container_reason="$(echo "$task_json" | jq -r '.tasks[0].containers[0].reason // ""')"
echo "   stoppedReason: $stopped_reason"
echo "   exitCode:      $exit_code"
if [[ -n "$container_reason" ]]; then
  echo "   container reason: $container_reason"
fi
if [[ "$exit_code" != "0" ]]; then
  echo "run_cold_run.sh: dry-run task exited $exit_code." >&2
  echo "  Inspect CloudWatch logs: aws logs tail $LOG_GROUP --region $REGION --since 15m" >&2
  exit 1
fi

echo
echo "run_cold_run.sh: PASS (dry-run plumbing gate)"
echo "  task arn: $TASK_ARN"
echo "  The task def launches, secrets/role/logging are wired. Ready for COLD_MODE=run."

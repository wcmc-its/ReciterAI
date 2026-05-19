#!/usr/bin/env bash
#
# smoke_enrichment.sh — #37 PR 4 / §7-B post-deploy gate
#
# Launches the reciterai-enrichment Fargate task via `aws ecs run-task` and
# verifies it completes cleanly. Two modes — they test different things:
#
#   dry-run  — fast, zero-Bedrock-cost deployment-plumbing check. Runs the
#              task with `--from-gap-scan --dry-run`: the work set + cost
#              preview is resolved, no model calls fire, no writes happen.
#              Proves the task definition launches, the bearer-token-driven
#              Bedrock auth is wired (well — at least secrets land in env),
#              CloudWatch logging works, the task role can reach DDB +
#              MariaDB + Secrets Manager.
#
#   real     — a tiny one-PMID run: `--pmids <PMID>`. The end-to-end gate.
#              Generates real synopsis + impact through Bedrock Sonnet 4.6
#              (or the OpenAI fallback if Sonnet content-filters), writes
#              MariaDB rows, and writes an IMPACT# row to DynamoDB. The
#              smoke verifies the IMPACT# row landed. One-shot per PMID —
#              the next run is a no-op (the PMID already has both rows
#              unless you pass `--force`).
#
# Both modes exit 0 only on full success. Suitable as a release gate.
#
# Usage:
#   AWS_REGION=us-east-1 \
#   ECS_CLUSTER_NAME=reciterai-cluster \
#   TASK_DEFINITION=reciterai-enrichment \
#   RECITERAI_ENRICHMENT_SUBNETS=subnet-...,subnet-... \
#   RECITERAI_ENRICHMENT_SECURITY_GROUPS=sg-... \
#   SMOKE_EXPECT=dry-run \
#   scripts/smoke_enrichment.sh
#
#   # Real mode also needs a PMID to enrich:
#   ENRICHMENT_SMOKE_PMID=39001234 SMOKE_EXPECT=real scripts/smoke_enrichment.sh
#
# Required env:
#   AWS_REGION                            — defaults to us-east-1
#   ECS_CLUSTER_NAME                      — defaults to reciterai-cluster
#   TASK_DEFINITION                       — defaults to reciterai-enrichment
#   RECITERAI_ENRICHMENT_SUBNETS          — comma-separated subnet IDs
#   RECITERAI_ENRICHMENT_SECURITY_GROUPS  — comma-separated security group IDs
#   SMOKE_EXPECT                          — "dry-run" or "real"
#   ENRICHMENT_SMOKE_PMID                 — required when SMOKE_EXPECT=real;
#                                           a real PMID with no synopsis/impact
#                                           rows yet, in the WCM-faculty corpus

set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
CLUSTER="${ECS_CLUSTER_NAME:-reciterai-cluster}"
TASK_DEF="${TASK_DEFINITION:-reciterai-enrichment}"
TABLE="${RECITERAI_TABLE:-reciterai}"
EXPECT="${SMOKE_EXPECT:-}"
PMID="${ENRICHMENT_SMOKE_PMID:-}"
TIMEOUT_SECONDS="${SMOKE_TIMEOUT_SECONDS:-900}"
POLL_INTERVAL="${SMOKE_POLL_INTERVAL:-15}"

if [[ "$EXPECT" != "dry-run" && "$EXPECT" != "real" ]]; then
  echo "smoke_enrichment.sh: SMOKE_EXPECT must be 'dry-run' or 'real' (got: '${EXPECT}')." >&2
  echo "  dry-run  — fast plumbing check; --from-gap-scan --dry-run, no model calls." >&2
  echo "  real     — end-to-end gate; --pmids \$ENRICHMENT_SMOKE_PMID, real Bedrock writes." >&2
  exit 1
fi
if [[ "$EXPECT" == "real" && -z "$PMID" ]]; then
  echo "smoke_enrichment.sh: SMOKE_EXPECT=real requires ENRICHMENT_SMOKE_PMID (a real PMID with no synopsis/impact rows yet)." >&2
  exit 1
fi
if [[ -z "${RECITERAI_ENRICHMENT_SUBNETS:-}" || -z "${RECITERAI_ENRICHMENT_SECURITY_GROUPS:-}" ]]; then
  echo "smoke_enrichment.sh: RECITERAI_ENRICHMENT_SUBNETS and RECITERAI_ENRICHMENT_SECURITY_GROUPS must be set (comma-separated)." >&2
  exit 1
fi

# Comma-separated -> JSON array.
subnets_json="$(printf '%s' "$RECITERAI_ENRICHMENT_SUBNETS" | jq -Rc 'split(",") | map(gsub("^\\s+|\\s+$"; ""))')"
sgs_json="$(printf '%s' "$RECITERAI_ENRICHMENT_SECURITY_GROUPS" | jq -Rc 'split(",") | map(gsub("^\\s+|\\s+$"; ""))')"

if [[ "$EXPECT" == "dry-run" ]]; then
  cmd_json='["python","-m","scripts.run_daily_enrichment","--from-gap-scan","--dry-run","--verbose"]'
  echo ">> smoke (dry-run): --from-gap-scan --dry-run on task def $TASK_DEF"
else
  cmd_json="$(jq -nc --arg pmid "$PMID" '["python","-m","scripts.run_daily_enrichment","--pmids",$pmid,"--verbose"]')"
  echo ">> smoke (real): --pmids $PMID on task def $TASK_DEF"
fi

overrides_json="$(jq -nc \
  --argjson cmd "$cmd_json" \
  '{containerOverrides:[{name:"reciterai-enrichment", command:$cmd}]}')"

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
    --overrides "$overrides_json" \
    --started-by "smoke_enrichment-$(date -u +%Y%m%dT%H%M%SZ)" \
    --query 'tasks[0].taskArn' \
    --output text
)"
if [[ -z "$TASK_ARN" || "$TASK_ARN" == "None" ]]; then
  echo "smoke_enrichment.sh: run-task returned no taskArn — image pull / IAM / network misconfig?" >&2
  exit 1
fi
echo "   task arn: $TASK_ARN"

# Poll for terminal state.
deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
last_status=""
while :; do
  if [[ $(date +%s) -ge $deadline ]]; then
    echo "smoke_enrichment.sh: timeout after ${TIMEOUT_SECONDS}s. Last status: $last_status." >&2
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

stopped_reason="$(echo "$task_json" | jq -r '.tasks[0].stoppedReason // ""')"
exit_code="$(echo "$task_json" | jq -r '.tasks[0].containers[0].exitCode // "null"')"
container_reason="$(echo "$task_json" | jq -r '.tasks[0].containers[0].reason // ""')"

echo "   stoppedReason: $stopped_reason"
echo "   exitCode:      $exit_code"
if [[ -n "$container_reason" ]]; then
  echo "   container reason: $container_reason"
fi

if [[ "$exit_code" != "0" ]]; then
  echo "smoke_enrichment.sh: task exited $exit_code." >&2
  echo "  Inspect CloudWatch logs: aws logs tail /ecs/reciterai-enrichment --since 30m" >&2
  exit 1
fi

if [[ "$EXPECT" == "real" ]]; then
  echo ">> verifying IMPACT#pmid_${PMID} row in DynamoDB"
  ROW=$(
    aws dynamodb get-item \
      --region "$REGION" \
      --table-name "$TABLE" \
      --key "{\"PK\":{\"S\":\"IMPACT#pmid_${PMID}\"},\"SK\":{\"S\":\"SCORE\"}}" \
      --output json
  )
  has_item="$(echo "$ROW" | jq -r 'if .Item then "yes" else "no" end')"
  if [[ "$has_item" != "yes" ]]; then
    echo "smoke_enrichment.sh: no IMPACT# row landed for PMID $PMID — task exited 0 but wrote nothing." >&2
    echo "  CloudWatch logs for the task should show the failure reason." >&2
    exit 1
  fi
  model="$(echo "$ROW" | jq -r '.Item.model.S // "?"')"
  syn_model="$(echo "$ROW" | jq -r '.Item.synopsis_model.S // "?"')"
  echo "   IMPACT# row present: model=$model synopsis_model=$syn_model"
fi

echo
echo "smoke_enrichment.sh: PASS"
echo "  task arn: $TASK_ARN"
echo "  mode:     $EXPECT"
if [[ "$EXPECT" == "real" ]]; then
  echo "  pmid:     $PMID"
fi

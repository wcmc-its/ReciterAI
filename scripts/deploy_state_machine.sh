#!/usr/bin/env bash
#
# deploy_state_machine.sh — Phase 10 T14
#
# Registers (or updates) the Step Functions state machine
# `reciterai-hot-path` from pipeline_hot/state_machine.asl.json.
#
# The ASL file contains ${LambdaName} placeholders for the per-stage
# Lambda ARNs. This script substitutes them from env vars before
# uploading. Missing env var → script aborts. The state machine role
# (which lets SFN invoke Lambda + write DynamoDB) is not created here;
# it must already exist per infra/lambda_iam_policy.json.
#
# Usage:
#   scripts/deploy_state_machine.sh --dry-run        # print substituted ASL + AWS calls
#   scripts/deploy_state_machine.sh                  # create or update the state machine
#
# Required env vars (all are full Lambda function ARNs):
#   ORCHESTRATOR_LAMBDA_ARN      → resolves ${OrchestratorLambdaArn}
#   SCORE_LAMBDA_ARN             → resolves ${ScoreLambdaArn}
#   ASSIGN_LAMBDA_ARN            → resolves ${AssignLambdaArn}
#   ROLLUP_LAMBDA_ARN            → resolves ${RollupLambdaArn}
#   ALERT_DISPATCHER_LAMBDA_ARN  → resolves ${AlertDispatcherLambdaArn}
#   STATE_MACHINE_ROLE_ARN       → IAM role assumed by Step Functions itself
#
# Optional:
#   AWS_REGION (default us-east-1)
#   STATE_MACHINE_NAME (default reciterai-hot-path)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ASL_TEMPLATE="${REPO_ROOT}/pipeline_hot/state_machine.asl.json"
REGION="${AWS_REGION:-us-east-1}"
NAME="${STATE_MACHINE_NAME:-reciterai-hot-path}"

DRY_RUN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "deploy_state_machine.sh: unknown arg: $1" >&2; exit 2 ;;
  esac
done

# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------

if [[ ! -f "$ASL_TEMPLATE" ]]; then
  echo "deploy_state_machine.sh: template not found: $ASL_TEMPLATE" >&2
  exit 1
fi

REQUIRED=(
  ORCHESTRATOR_LAMBDA_ARN
  SCORE_LAMBDA_ARN
  ASSIGN_LAMBDA_ARN
  ROLLUP_LAMBDA_ARN
  ALERT_DISPATCHER_LAMBDA_ARN
  STATE_MACHINE_ROLE_ARN
)
missing=()
for v in "${REQUIRED[@]}"; do
  if [[ -z "${!v:-}" ]]; then
    missing+=("$v")
  fi
done
if [[ ${#missing[@]} -gt 0 ]]; then
  echo "deploy_state_machine.sh: missing required env vars: ${missing[*]}" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Render ASL (substitute ${...} placeholders)
# ---------------------------------------------------------------------------

render_asl() {
  python3 - "$ASL_TEMPLATE" <<'PY'
import json, os, sys
src = open(sys.argv[1]).read()
mapping = {
    "OrchestratorLambdaArn":     os.environ["ORCHESTRATOR_LAMBDA_ARN"],
    "ScoreLambdaArn":            os.environ["SCORE_LAMBDA_ARN"],
    "AssignLambdaArn":           os.environ["ASSIGN_LAMBDA_ARN"],
    "RollupLambdaArn":           os.environ["ROLLUP_LAMBDA_ARN"],
    "AlertDispatcherLambdaArn":  os.environ["ALERT_DISPATCHER_LAMBDA_ARN"],
}
for key, val in mapping.items():
    src = src.replace("${" + key + "}", val)
# Validate: no remaining ${...} placeholders
import re
leftover = re.findall(r"\$\{[A-Za-z0-9_]+\}", src)
if leftover:
    sys.stderr.write("deploy_state_machine.sh: unresolved placeholders: %s\n" % ",".join(sorted(set(leftover))))
    sys.exit(2)
# Re-parse to confirm valid JSON post-substitution
json.loads(src)
sys.stdout.write(src)
PY
}

RENDERED="$(render_asl)"

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "----- rendered state_machine.asl.json -----"
  echo "$RENDERED" | python3 -m json.tool | head -40
  echo "(...truncated)"
  echo "-------------------------------------------"
fi

# ---------------------------------------------------------------------------
# Create or update
# ---------------------------------------------------------------------------

# Account id from STS so we can build the state-machine ARN ourselves.
if [[ -z "${AWS_ACCOUNT_ID:-}" ]]; then
  AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text 2>/dev/null || true)"
fi
if [[ -z "$AWS_ACCOUNT_ID" ]]; then
  echo "deploy_state_machine.sh: AWS_ACCOUNT_ID not set and STS lookup failed." >&2
  exit 1
fi
SM_ARN="arn:aws:states:${REGION}:${AWS_ACCOUNT_ID}:stateMachine:${NAME}"

run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '[dry-run] '
    printf '%q ' "$@"
    printf '\n'
  else
    "$@"
  fi
}

# Probe: does the state machine already exist?
exists=0
if aws stepfunctions describe-state-machine \
     --region "$REGION" \
     --state-machine-arn "$SM_ARN" \
     >/dev/null 2>&1; then
  exists=1
fi

if [[ "$exists" -eq 1 ]]; then
  echo ">> updating state machine: $SM_ARN"
  run aws stepfunctions update-state-machine \
    --region "$REGION" \
    --state-machine-arn "$SM_ARN" \
    --definition "$RENDERED" \
    --role-arn "$STATE_MACHINE_ROLE_ARN"
else
  echo ">> creating state machine: $NAME"
  run aws stepfunctions create-state-machine \
    --region "$REGION" \
    --name "$NAME" \
    --type STANDARD \
    --definition "$RENDERED" \
    --role-arn "$STATE_MACHINE_ROLE_ARN"
fi

echo
echo "deploy_state_machine.sh: done."
echo "ARN: $SM_ARN"
if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "(dry-run: no AWS calls were made.)"
fi

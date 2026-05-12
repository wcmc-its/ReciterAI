#!/usr/bin/env bash
#
# smoke_hot_path.sh — Phase 10 T14 smoke test
#
# Starts a Step Functions execution of reciterai-hot-path against a
# sandbox environment, polls for completion, and verifies:
#
#   1. Execution status == SUCCEEDED.
#   2. A STAGE#hot_run#GLOBAL row with status="complete" exists for
#      this execution's run_id.
#
# Inputs: a tiny synthetic PMID set ([99999001, 99999002]). The handlers
# detect synthetic mode via initiated_by=="smoke" and use stub Bedrock
# responses (handler-level branch — see pipeline_hot/handlers/*.py).
#
# Usage:
#   AWS_REGION=us-east-1 \
#   STATE_MACHINE_ARN=arn:aws:states:us-east-1:$ACCT:stateMachine:reciterai-hot-path \
#   scripts/smoke_hot_path.sh
#
# Exits non-zero on any verification failure; suitable as a CI gate.

set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
SM_ARN="${STATE_MACHINE_ARN:-}"
TABLE="${RECITERAI_TABLE:-reciterai-chatbot}"
TIMEOUT_SECONDS="${SMOKE_TIMEOUT_SECONDS:-600}"
POLL_INTERVAL="${SMOKE_POLL_INTERVAL:-10}"

if [[ -z "$SM_ARN" ]]; then
  echo "smoke_hot_path.sh: STATE_MACHINE_ARN must be set." >&2
  exit 1
fi

RUN_ID="smoke-$(date -u +%Y%m%dT%H%M%SZ)"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

INPUT=$(cat <<JSON
{
  "initiated_by": "smoke",
  "run_id": "${RUN_ID}",
  "started_at": "${STARTED_AT}",
  "synthetic_pmids": ["99999001", "99999002"]
}
JSON
)

echo ">> starting execution: $RUN_ID"
EXEC_ARN="$(
  aws stepfunctions start-execution \
    --region "$REGION" \
    --state-machine-arn "$SM_ARN" \
    --name "$RUN_ID" \
    --input "$INPUT" \
    --query executionArn \
    --output text
)"
echo "   execution arn: $EXEC_ARN"

# Poll for terminal state
deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
status="RUNNING"
while [[ "$status" == "RUNNING" ]]; do
  if [[ $(date +%s) -ge $deadline ]]; then
    echo "smoke_hot_path.sh: timeout after ${TIMEOUT_SECONDS}s. Last status: $status." >&2
    exit 1
  fi
  sleep "$POLL_INTERVAL"
  status="$(
    aws stepfunctions describe-execution \
      --region "$REGION" \
      --execution-arn "$EXEC_ARN" \
      --query status --output text
  )"
  echo "   status: $status"
done

if [[ "$status" != "SUCCEEDED" ]]; then
  echo "smoke_hot_path.sh: execution finished $status — fetching history" >&2
  aws stepfunctions get-execution-history \
    --region "$REGION" \
    --execution-arn "$EXEC_ARN" \
    --max-items 50 \
    --query 'events[?type==`ExecutionFailed` || type==`TaskFailed`]' >&2
  exit 1
fi

echo ">> verifying STAGE#hot_run#GLOBAL row"

# Query the most recent hot_run row; verify it matches this run.
LATEST=$(
  aws dynamodb query \
    --region "$REGION" \
    --table-name "$TABLE" \
    --key-condition-expression "PK = :pk" \
    --expression-attribute-values '{":pk":{"S":"STAGE#hot_run#GLOBAL"}}' \
    --no-scan-index-forward \
    --limit 1 \
    --output json
)

status_val="$(echo "$LATEST" | python3 -c '
import json, sys
data = json.load(sys.stdin)
items = data.get("Items", [])
if not items:
    print("MISSING"); sys.exit(0)
row = items[0]
print(row.get("status", {}).get("S", ""))
')"
run_id_val="$(echo "$LATEST" | python3 -c '
import json, sys
data = json.load(sys.stdin)
items = data.get("Items", [])
if not items:
    sys.exit(0)
row = items[0]
sk = row.get("SK", {}).get("S", "")
print(sk)
')"

if [[ "$status_val" != "complete" ]]; then
  echo "smoke_hot_path.sh: latest STAGE#hot_run#GLOBAL row has status='$status_val' (expected 'complete'). SK='$run_id_val'." >&2
  exit 1
fi

# SK is RUN#{started_at}; require our started_at to be the prefix.
if [[ "$run_id_val" != "RUN#${STARTED_AT}"* ]]; then
  echo "smoke_hot_path.sh: latest STAGE#hot_run#GLOBAL SK '$run_id_val' does not match expected RUN#${STARTED_AT}*." >&2
  echo "  (Another hot run may have written more recently; re-run smoke test.)" >&2
  exit 1
fi

echo
echo "smoke_hot_path.sh: PASS"
echo "  execution arn: $EXEC_ARN"
echo "  STAGE# row:    PK=STAGE#hot_run#GLOBAL  SK=$run_id_val  status=complete"

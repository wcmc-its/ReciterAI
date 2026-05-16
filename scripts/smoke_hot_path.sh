#!/usr/bin/env bash
#
# smoke_hot_path.sh — Phase 10 T14 smoke test
#
# Starts a Step Functions execution of reciterai-hot-path, polls for
# completion, and verifies:
#
#   1. Execution status == SUCCEEDED.
#   2. The STAGE#hot_run#GLOBAL row written by THIS execution (matched on
#      run_id) has status="complete".
#
# Inputs: the execution input carries run_id / started_at, but no
# synthetic PMID set is honored — the Orchestrate task is passed only
# state_machine_arn / execution_arn / run_id, so the orchestrator always
# computes the real publication-date delta from ReciterDB and runs the
# retry sweep. A smoke is therefore a REAL hot-path run (real Bedrock
# scoring, real DynamoDB writes); when the delta window holds no new
# publications it still completes, against zero PMIDs.
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
TABLE="${RECITERAI_TABLE:-reciterai}"
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

echo ">> verifying STAGE#hot_run#GLOBAL row for run_id=$RUN_ID"

# Locate THIS run's hot_run row by run_id — never by "highest SK".
# The state machine writes the execution name (== this smoke's RUN_ID)
# as `run_id` on both the complete and the failed hot_run rows, so it
# uniquely identifies this execution's outcome.
#
# Reading the lexicographically-highest SK instead is wrong: a failed
# row's SK is `RUN#FAILED#{ts}` and a successful row's is `RUN#{ts}`,
# and 'F' (0x46) > '2' (0x32) — so every `RUN#FAILED#...` sorts above
# every `RUN#{iso-timestamp}`. One stale failed row from any past run
# would therefore mask this run's real result. A filter on run_id is
# immune to that.
ROW=$(
  aws dynamodb query \
    --region "$REGION" \
    --table-name "$TABLE" \
    --key-condition-expression "PK = :pk" \
    --filter-expression "run_id = :rid" \
    --expression-attribute-values "{\":pk\":{\"S\":\"STAGE#hot_run#GLOBAL\"},\":rid\":{\"S\":\"${RUN_ID}\"}}" \
    --output json
)

verdict="$(echo "$ROW" | python3 -c '
import json, sys
items = json.load(sys.stdin).get("Items", [])
if not items:
    print("MISSING|"); sys.exit(0)
row = items[0]
print(row.get("status", {}).get("S", "") + "|" + row.get("SK", {}).get("S", ""))
')"
status_val="${verdict%%|*}"
sk_val="${verdict#*|}"

if [[ "$status_val" == "MISSING" ]]; then
  echo "smoke_hot_path.sh: no STAGE#hot_run#GLOBAL row found with run_id='$RUN_ID'." >&2
  echo "  The execution succeeded but wrote no hot_run row — inspect the state machine." >&2
  exit 1
fi

if [[ "$status_val" != "complete" ]]; then
  echo "smoke_hot_path.sh: this run's STAGE#hot_run#GLOBAL row has status='$status_val' (expected 'complete'). SK='$sk_val'." >&2
  exit 1
fi

echo
echo "smoke_hot_path.sh: PASS"
echo "  execution arn: $EXEC_ARN"
echo "  STAGE# row:    PK=STAGE#hot_run#GLOBAL  SK=$sk_val  run_id=$RUN_ID  status=complete"

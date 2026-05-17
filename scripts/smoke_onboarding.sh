#!/usr/bin/env bash
#
# smoke_onboarding.sh — #80 Phase 2 / PR 6
#
# Starts a Step Functions execution of reciterai-onboarding for one CWID,
# polls for completion, and verifies:
#
#   1. Execution status == SUCCEEDED.
#   2. The STAGE#onboarding#cwid:{cwid} row written by THIS execution
#      (matched on run_id) has status == $SMOKE_EXPECT.
#
# SMOKE_EXPECT has two modes — they test different things:
#
#   skipped  — a fast, zero-Bedrock-cost deployment-plumbing check. Point it
#              at a CWID whose accepted publications are ALREADY fully scored
#              (every PMID `complete` in PROCESSING#), or which has no
#              accepted 2020+ publications: the orchestrator short-circuits
#              at CheckProceed and routes `skipped`. Proves the state machine
#              deploys and Orchestrate/CheckProceed work — but does NOT
#              exercise the Score -> DeriveDirtyTopics -> AssignFanOut(Map)
#              -> TopTopic -> Rollup -> Finalize cascade.
#
#   complete — the Phase-2 closing gate. Point it at a CWID with a SMALL set
#              of synopsis-ready, UNSCORED PMIDs: the full cascade runs. This
#              is the integrative test — the first real onboarding. Cost is
#              real but bounded (a few PMIDs, previewed by the orchestrator's
#              Teams cost note, capped by the 300-PMID cost guard). One-shot
#              per CWID: a second run nets empty and routes `skipped`.
#
# The STAGE# row is located by run_id (the execution name), never by the
# highest SK — a prior failed run's SK is `RUN#FAILED#{ts}` and 'F' (0x46) >
# '2' (0x32), so it would sort above this run's `RUN#{iso}` row.
#
# Usage:
#   AWS_REGION=us-east-1 \
#   STATE_MACHINE_ARN=arn:aws:states:us-east-1:$ACCT:stateMachine:reciterai-onboarding \
#   ONBOARDING_SMOKE_CWID=abc1234 \
#   SMOKE_EXPECT=skipped \
#   scripts/smoke_onboarding.sh
#
# Exits non-zero on any verification failure; suitable as a release gate.

set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
SM_ARN="${STATE_MACHINE_ARN:-}"
TABLE="${RECITERAI_TABLE:-reciterai}"
CWID="${ONBOARDING_SMOKE_CWID:-}"
EXPECT="${SMOKE_EXPECT:-}"
TIMEOUT_SECONDS="${SMOKE_TIMEOUT_SECONDS:-600}"
POLL_INTERVAL="${SMOKE_POLL_INTERVAL:-10}"

if [[ -z "$SM_ARN" ]]; then
  echo "smoke_onboarding.sh: STATE_MACHINE_ARN must be set." >&2
  exit 1
fi
if [[ -z "$CWID" ]]; then
  echo "smoke_onboarding.sh: ONBOARDING_SMOKE_CWID must be set." >&2
  exit 1
fi
if [[ "$EXPECT" != "skipped" && "$EXPECT" != "complete" ]]; then
  echo "smoke_onboarding.sh: SMOKE_EXPECT must be 'skipped' or 'complete' (got: '${EXPECT}')." >&2
  echo "  skipped  — fast plumbing check; a CWID whose PMIDs are all already scored." >&2
  echo "  complete — the Phase-2 closing gate; a CWID with a small unscored work set." >&2
  exit 1
fi

if [[ "$EXPECT" == "complete" ]]; then
  echo ">> SMOKE_EXPECT=complete — this runs a REAL onboarding (real Bedrock"
  echo "   scoring of cwid=${CWID}'s unscored PMIDs). The orchestrator posts a"
  echo "   Teams cost preview before any model call; the 300-PMID cost guard caps it."
fi

RUN_ID="onboarding-smoke-$(date -u +%Y%m%dT%H%M%SZ)"

INPUT=$(cat <<JSON
{
  "cwid": "${CWID}",
  "allow_cost_override": false
}
JSON
)

echo ">> starting execution: $RUN_ID (cwid=$CWID, expect=$EXPECT)"
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

# Poll for terminal state.
deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
status="RUNNING"
while [[ "$status" == "RUNNING" ]]; do
  if [[ $(date +%s) -ge $deadline ]]; then
    echo "smoke_onboarding.sh: timeout after ${TIMEOUT_SECONDS}s. Last status: $status." >&2
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
  echo "smoke_onboarding.sh: execution finished $status — fetching history" >&2
  aws stepfunctions get-execution-history \
    --region "$REGION" \
    --execution-arn "$EXEC_ARN" \
    --max-items 50 \
    --query 'events[?type==`ExecutionFailed` || type==`TaskFailed`]' >&2
  exit 1
fi

echo ">> verifying STAGE#onboarding#cwid:${CWID} row for run_id=$RUN_ID"

# Locate THIS run's onboarding row by run_id — never by "highest SK".
# Every terminal writer (the orchestrator's deferred/skipped/cost-exceeded
# rows, Finalize's complete/partial row, and the inline WriteOnboardingFailed)
# stamps run_id == the execution name. Reading the lexicographically-highest
# SK instead is wrong: a failed row's SK is `RUN#FAILED#{ts}` and a clean
# row's is `RUN#{ts}`, and 'F' (0x46) > '2' (0x32) — so any stale
# `RUN#FAILED#...` row would mask this run's result. A run_id filter is immune.
ROW=$(
  aws dynamodb query \
    --region "$REGION" \
    --table-name "$TABLE" \
    --key-condition-expression "PK = :pk" \
    --filter-expression "run_id = :rid" \
    --expression-attribute-values "{\":pk\":{\"S\":\"STAGE#onboarding#cwid:${CWID}\"},\":rid\":{\"S\":\"${RUN_ID}\"}}" \
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
  echo "smoke_onboarding.sh: no STAGE#onboarding#cwid:${CWID} row found with run_id='$RUN_ID'." >&2
  echo "  The execution succeeded but wrote no onboarding row — inspect the state machine." >&2
  exit 1
fi

if [[ "$status_val" != "$EXPECT" ]]; then
  echo "smoke_onboarding.sh: this run's STAGE#onboarding#cwid row has status='$status_val' (expected '$EXPECT'). SK='$sk_val'." >&2
  if [[ "$EXPECT" == "skipped" && "$status_val" == "complete" ]]; then
    echo "  The CWID had unscored work — pick a fully-scored CWID for a skipped-mode smoke." >&2
  elif [[ "$EXPECT" == "complete" && "$status_val" == "skipped" ]]; then
    echo "  The CWID had no net work — pick a CWID with synopsis-ready, unscored PMIDs." >&2
  elif [[ "$status_val" == "deferred" ]]; then
    echo "  The CWID has PMIDs missing a synopsis — onboarding deferred pending synopsis backfill." >&2
  fi
  exit 1
fi

echo
echo "smoke_onboarding.sh: PASS"
echo "  execution arn: $EXEC_ARN"
echo "  STAGE# row:    PK=STAGE#onboarding#cwid:${CWID}  SK=$sk_val  run_id=$RUN_ID  status=$status_val"

#!/usr/bin/env bash
#
# apply_backup_config.sh — #223 backups / disaster recovery
#
# Idempotently applies the ReciterAI durability posture:
#   1. DynamoDB PITR (point-in-time recovery) on `reciterai`
#   2. DynamoDB deletion protection on `reciterai`
#   3. S3 versioning on wcmc-reciterai-artifacts + wcmc-reciterai-hierarchy
#   4. S3 noncurrent-version expiration lifecycle (infra/s3_lifecycle_noncurrent.json)
#      on both buckets
#
# Single-file IaC convention (infra/README.md, D-10): a thin AWS CLI wrapper,
# mirroring scripts/deploy_cron.sh. This is NOT a cron — it is a one-time apply
# plus a re-runnable drift check (`--verify`). Re-run it after any PITR restore
# (a restored table comes back with PITR OFF). See docs/dr-runbook.md.
#
# Usage:
#   scripts/apply_backup_config.sh --dry-run   # print the AWS calls, mutate nothing
#   scripts/apply_backup_config.sh             # apply, then verify each control
#   scripts/apply_backup_config.sh --verify    # only print current state, no mutation
#
# Env:
#   AWS_REGION  — defaults to us-east-1 (both buckets + the table are us-east-1)
#
# Every control is verified with a describe-*/get-* call after apply, per the
# data-integrity rule that a command's exit-0 is not evidence the control is on.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REGION="${AWS_REGION:-us-east-1}"
TABLE="reciterai"
BUCKETS=("wcmc-reciterai-artifacts" "wcmc-reciterai-hierarchy")
LIFECYCLE_FILE="${REPO_ROOT}/infra/s3_lifecycle_noncurrent.json"

MODE="apply"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) MODE="dry-run"; shift ;;
    --verify)  MODE="verify";  shift ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "apply_backup_config.sh: unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ ! -f "$LIFECYCLE_FILE" ]]; then
  echo "apply_backup_config.sh: lifecycle config not found: $LIFECYCLE_FILE" >&2
  exit 1
fi

# Run a mutating command, or echo it if --dry-run.
run() {
  if [[ "$MODE" == "dry-run" ]]; then
    printf '[dry-run] '; printf '%q ' "$@"; printf '\n'
  else
    "$@"
  fi
}

# Read-only verification; skipped in --dry-run so dry-run makes zero AWS calls.
verify() {
  [[ "$MODE" == "dry-run" ]] && return 0
  "$@"
}

echo "== ReciterAI backup/DR config (mode=$MODE, region=$REGION) =="

# 1. DynamoDB PITR ----------------------------------------------------------
if [[ "$MODE" != "verify" ]]; then
  echo ">> [1/4] DynamoDB PITR on $TABLE"
  run aws dynamodb update-continuous-backups \
    --table-name "$TABLE" \
    --point-in-time-recovery-specification PointInTimeRecoveryEnabled=true \
    --region "$REGION"
fi
echo "   PITR status:"
verify aws dynamodb describe-continuous-backups --table-name "$TABLE" --region "$REGION" \
  --query 'ContinuousBackupsDescription.PointInTimeRecoveryDescription.PointInTimeRecoveryStatus' \
  --output text

# 2. DynamoDB deletion protection -------------------------------------------
if [[ "$MODE" != "verify" ]]; then
  echo ">> [2/4] DynamoDB deletion protection on $TABLE"
  run aws dynamodb update-table \
    --table-name "$TABLE" \
    --deletion-protection-enabled \
    --region "$REGION"
fi
echo "   DeletionProtectionEnabled:"
verify aws dynamodb describe-table --table-name "$TABLE" --region "$REGION" \
  --query 'Table.DeletionProtectionEnabled' --output text

# 3 + 4. S3 versioning + noncurrent-version lifecycle, per bucket ------------
for bucket in "${BUCKETS[@]}"; do
  if [[ "$MODE" != "verify" ]]; then
    echo ">> [3/4] S3 versioning on $bucket"
    run aws s3api put-bucket-versioning \
      --bucket "$bucket" \
      --versioning-configuration Status=Enabled \
      --region "$REGION"
    echo ">> [4/4] S3 noncurrent-version lifecycle on $bucket"
    run aws s3api put-bucket-lifecycle-configuration \
      --bucket "$bucket" \
      --lifecycle-configuration "file://${LIFECYCLE_FILE}" \
      --region "$REGION"
  fi
  echo "   versioning ($bucket):"
  verify aws s3api get-bucket-versioning --bucket "$bucket" --region "$REGION" \
    --query 'Status' --output text
  echo "   lifecycle rule IDs ($bucket):"
  verify aws s3api get-bucket-lifecycle-configuration --bucket "$bucket" --region "$REGION" \
    --query 'Rules[].ID' --output text
done

echo
echo "apply_backup_config.sh: done (mode=$MODE)."
if [[ "$MODE" == "dry-run" ]]; then
  echo "(dry-run: no AWS calls were made.)"
fi

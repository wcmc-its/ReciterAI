#!/usr/bin/env bash
#
# verify_98_topic_rows.sh — one-shot production check for issue #98.
#
# #98: the hot path's Score stage must materialize TOPIC# activity rows
# for every scored delta PMID (PR #103 — score_publications.py gates
# persist_topic_rows on --emit-envelope). The redeploy and an end-to-end
# smoke are already verified; what this script adds is the production
# observation — that a real hot run with a non-empty delta actually
# writes TOPIC# rows.
#
# What it does:
#   1. Picks a hot run — the most recent STAGE#hot_run#GLOBAL row by
#      timestamp (NOT by SK: a RUN#FAILED#<ts> SK sorts above every
#      RUN#<iso> SK, so "highest SK" is the wrong selector), or the run
#      named by --run-id.
#   2. Reads its status / delta_size. If the run is not `complete`, or
#      delta_size == 0, #98 cannot be verified from it — exits 2
#      (pending), not a failure.
#   3. Scans the reciterai-hot-path Step Functions execution history for
#      the run's delta PMID set (the delta.pmids / delta_pmids carried by
#      the Score and TopTopic stage payloads).
#   4. Queries the PmidIndex GSI for each PMID's TOPIC# rows.
#
# Verdict: a delta PMID legitimately has zero TOPIC# rows when it has no
# faculty authors or no topic scored at/above the dense-score floor — so
# the pass condition is "at least one delta PMID carries TOPIC# rows",
# which proves the materialization path ran and wrote. Zero across the
# whole delta set is a fail.
#
# Exit codes: 0 = #98 verified, 1 = failed / error, 2 = pending (no
# clean non-empty run to verify against yet).
#
# Usage:
#   scripts/verify_98_topic_rows.sh                 # verify the latest hot run
#   scripts/verify_98_topic_rows.sh --run-id RUN    # verify a specific run
#
# Env: AWS_REGION (us-east-1), RECITERAI_TABLE (reciterai),
#      STATE_MACHINE_NAME (reciterai-hot-path), MAX_PMIDS_CHECK (25).
#
# One-shot — delete when #98 closes (scripts/ lifecycle rule).

set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
TABLE="${RECITERAI_TABLE:-reciterai}"
SM_NAME="${STATE_MACHINE_NAME:-reciterai-hot-path}"
MAX_PMIDS_CHECK="${MAX_PMIDS_CHECK:-25}"

RUN_ID_ARG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-id) RUN_ID_ARG="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
    *) echo "verify_98_topic_rows.sh: unknown arg: $1" >&2; exit 1 ;;
  esac
done

# ---------------------------------------------------------------------------
# 1. Select the hot run
# ---------------------------------------------------------------------------
echo ">> selecting hot run"
ROWS_JSON=$(
  aws dynamodb query \
    --region "$REGION" \
    --table-name "$TABLE" \
    --key-condition-expression "PK = :pk" \
    --expression-attribute-values '{":pk":{"S":"STAGE#hot_run#GLOBAL"}}' \
    --output json
)

SUMMARY=$(echo "$ROWS_JSON" | RUN_ID_ARG="$RUN_ID_ARG" python3 -c '
import json, os, sys
items = json.load(sys.stdin).get("Items", [])
want = os.environ.get("RUN_ID_ARG", "")

def ts(it):
    # Compare on the bare timestamp, stripping the RUN# / RUN#FAILED#
    # prefix — comparing raw SKs is wrong (RUN#FAILED# sorts above RUN#).
    sk = it.get("SK", {}).get("S", "")
    for p in ("RUN#FAILED#", "RUN#"):
        if sk.startswith(p):
            return sk[len(p):]
    return sk

if want:
    cand = [it for it in items if it.get("run_id", {}).get("S", "") == want]
    if not cand:
        print("NOTFOUND|||||"); sys.exit(0)
    row = max(cand, key=ts)
elif not items:
    print("NONE|||||"); sys.exit(0)
else:
    row = max(items, key=ts)

print("|".join([
    row.get("run_id", {}).get("S", ""),
    row.get("status", {}).get("S", ""),
    row.get("delta_size", {}).get("N", "") or "0",
    row.get("SK", {}).get("S", ""),
    row.get("run_kind", {}).get("S", ""),
    row.get("retry_size", {}).get("N", "") or "0",
]))
')
IFS="|" read -r RUN_ID STATUS DELTA_SIZE SK RUN_KIND RETRY_SIZE <<< "$SUMMARY"

if [[ "$RUN_ID" == "NONE" ]]; then
  echo "verify_98_topic_rows.sh: no STAGE#hot_run#GLOBAL rows exist." >&2
  exit 2
fi
if [[ "$RUN_ID" == "NOTFOUND" ]]; then
  echo "verify_98_topic_rows.sh: no hot run with run_id='$RUN_ID_ARG'." >&2
  exit 1
fi

echo "   run_id=$RUN_ID"
echo "   SK=$SK"
echo "   status=$STATUS  run_kind=$RUN_KIND  delta_size=$DELTA_SIZE  retry_size=$RETRY_SIZE"

# ---------------------------------------------------------------------------
# 2. Gate — only a `complete` run with a non-empty delta can verify #98
# ---------------------------------------------------------------------------
if [[ "$STATUS" == "failed" ]]; then
  echo
  echo "verify_98_topic_rows.sh: PENDING — the selected hot run FAILED."
  echo "  #98 cannot be verified against a failed run; inspect it, then"
  echo "  re-check after the next clean tick."
  exit 2
fi
if [[ "$STATUS" != "complete" ]]; then
  echo
  echo "verify_98_topic_rows.sh: PENDING — run status is '$STATUS' (need 'complete')."
  exit 2
fi
if [[ "${DELTA_SIZE:-0}" -eq 0 ]]; then
  echo
  echo "verify_98_topic_rows.sh: PENDING — delta_size=0 (empty delta window)."
  echo "  The Score stage had no PMIDs to score, so #98's _materialize_topic_rows"
  echo "  did not run. Re-check after a hot tick that picks up new publications."
  exit 2
fi

# ---------------------------------------------------------------------------
# 3. Enumerate the run's delta PMID set from the execution history
# ---------------------------------------------------------------------------
echo
echo ">> non-empty delta — enumerating PMIDs from the execution history"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
EXEC_ARN="arn:aws:states:${REGION}:${ACCOUNT_ID}:execution:${SM_NAME}:${RUN_ID}"

HIST_JSON="$(aws stepfunctions get-execution-history \
  --region "$REGION" --execution-arn "$EXEC_ARN" \
  --output json 2>/dev/null || true)"

if [[ -z "$HIST_JSON" ]]; then
  echo "verify_98_topic_rows.sh: could not read execution history for" >&2
  echo "  $EXEC_ARN" >&2
  echo "  (run_id may not match the Step Functions execution name, or the" >&2
  echo "  history aged out). Enumerate delta PMIDs from the Orchestrate" >&2
  echo "  output manually and re-run per-PMID PmidIndex queries." >&2
  exit 1
fi

PMIDS="$(echo "$HIST_JSON" | python3 -c '
import json, sys
events = json.load(sys.stdin).get("events", [])
acc = set()
def collect(obj):
    # Recursively gather any pmids / all_pmids / delta_pmids list — the
    # delta set rides in the Score input as delta.pmids and the TopTopic
    # input as delta_pmids; the exact shape shifts across run vintages,
    # so scan for the key wherever it appears.
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("pmids", "all_pmids", "delta_pmids") and isinstance(v, list):
                for x in v:
                    if isinstance(x, (str, int)):
                        acc.add(str(x))
            else:
                collect(v)
    elif isinstance(obj, list):
        for x in obj:
            collect(x)
for ev in events:
    for v in ev.values():
        if not isinstance(v, dict):
            continue
        for field in ("input", "output"):
            s = v.get(field)
            if isinstance(s, str) and s:
                try:
                    collect(json.loads(s))
                except Exception:
                    pass
if acc:
    print(" ".join(sorted(acc)))
')"

if [[ -z "${PMIDS// /}" ]]; then
  echo "verify_98_topic_rows.sh: no delta PMID list found in the execution" >&2
  echo "  history for $EXEC_ARN — inspect the Score stage input manually." >&2
  exit 1
fi

N_PMIDS=$(echo "$PMIDS" | wc -w | tr -d ' ')
echo "   delta PMID set: $N_PMIDS PMID(s)"

# ---------------------------------------------------------------------------
# 4. Check each PMID for TOPIC# rows via the PmidIndex GSI
# ---------------------------------------------------------------------------
echo
echo ">> querying PmidIndex for TOPIC# rows (up to $MAX_PMIDS_CHECK PMIDs)"
checked=0
with_rows=0
for pmid in $PMIDS; do
  if [[ "$checked" -ge "$MAX_PMIDS_CHECK" ]]; then
    break
  fi
  checked=$((checked + 1))
  cnt="$(aws dynamodb query \
    --region "$REGION" --table-name "$TABLE" --index-name PmidIndex \
    --key-condition-expression "pmid = :p AND begins_with(PK, :t)" \
    --expression-attribute-values "{\":p\":{\"S\":\"${pmid}\"},\":t\":{\"S\":\"TOPIC#\"}}" \
    --select COUNT --query Count --output text 2>/dev/null || echo ERR)"
  if [[ "$cnt" == "ERR" || -z "$cnt" ]]; then
    echo "   pmid=$pmid  TOPIC#_rows=query-error"
    continue
  fi
  if [[ "$cnt" -gt 0 ]]; then
    with_rows=$((with_rows + 1))
  fi
  echo "   pmid=$pmid  TOPIC#_rows=$cnt"
done

# ---------------------------------------------------------------------------
# 5. Verdict
# ---------------------------------------------------------------------------
echo
echo "=========================================="
if [[ "$with_rows" -gt 0 ]]; then
  echo "verify_98_topic_rows.sh: PASS — #98 verified"
  echo "  $with_rows of $checked checked delta PMIDs (of $N_PMIDS total) carry"
  echo "  TOPIC# rows.  run_id=$RUN_ID  delta_size=$DELTA_SIZE"
  echo "  (A checked PMID with 0 rows is expected when it has no faculty"
  echo "   authors or no topic scored at/above the dense-score floor.)"
  echo "  -> #98 can be closed; delete this script (scripts/ one-shot rule)."
  exit 0
else
  echo "verify_98_topic_rows.sh: FAIL — no TOPIC# rows found"
  echo "  None of the $checked checked delta PMIDs (run_id=$RUN_ID) carry"
  echo "  TOPIC# rows. Either the #98 deploy is not in effect on"
  echo "  reciterai-hot-score, or none of these PMIDs has a faculty-author"
  echo "  x scored-topic pair. Investigate before closing #98."
  exit 1
fi

#!/usr/bin/env bash
# Invoke the grant-ranking eval loop: (re)build benchmark dumps -> score (LLM judge +
# historical-winner back-test) -> diff vs the saved baseline. See docs/grant-eval-harness.md.
#
#   bash pipeline_grants/run_eval.sh            # score + diff vs baseline (the loop)
#   FREEZE=1 bash pipeline_grants/run_eval.sh   # write a NEW baseline instead of diffing
#   NOJUDGE=1 bash pipeline_grants/run_eval.sh  # back-test only, no LLM calls (free/instant)
#
# Bedrock needs AWS_BEARER_TOKEN_BEDROCK and the Bash sandbox OFF. Paths default to the
# operator's local layout; override any via env (REPO/GRANT3/DUMPS/FUNDING_DB/REPORT/BASELINE/K).
set -euo pipefail

REPO="${REPO:-$HOME/Dropbox/GitHub/ReciterAI}"
GRANT3="${GRANT3:-$HOME/worktrees/reciterai-grant3/scratch-matching}"
DUMPS="${DUMPS:-$REPO/scratchpad_eval_dumps}"
FUNDING_DB="${FUNDING_DB:-$REPO/wcm_funding_db_2026-06-28.json}"
REPORT="${REPORT:-$REPO/docs/grant-matching-verification-2026-06-28.md}"
BASELINE="${BASELINE:-$DUMPS/baseline.json}"
K="${K:-8}"

cd "$(dirname "$0")/.."  # repo root, so `python3 -m pipeline_grants...` and `utils` resolve

# 1. Build dumps from the verification markdown ONLY if asked (BUILD_FROM_MARKDOWN=1). This is the
#    legacy top-8 bootstrap and would CLOBBER full-pool dumps produced by scratch-matching/dump_grant.sh.
#    Normal loop: dumps already exist (ECS full-pool from dump_grant.sh) -> skip straight to scoring.
if [[ "${BUILD_FROM_MARKDOWN:-}" ]]; then
  python3 -m pipeline_grants.grant_eval_from_verification "$REPORT" "$DUMPS" \
    --solicitations "$GRANT3/grant_solicitations.json" \
    --extra-grants  "$GRANT3/extra_grants.json" \
    --funding-db    "$FUNDING_DB"
fi

# 2. Score, then either diff vs baseline or freeze a new baseline.
ARGS=("$DUMPS/*.json" -k "$K" --funding-db "$FUNDING_DB")
[[ "${NOJUDGE:-}" ]] && ARGS+=(--no-judge)
if [[ "${FREEZE:-}" ]]; then
  python3 -m pipeline_grants.grant_eval "${ARGS[@]}" --out "$BASELINE"
  echo "froze baseline -> $BASELINE"
else
  python3 -m pipeline_grants.grant_eval "${ARGS[@]}" --baseline "$BASELINE"
fi

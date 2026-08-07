#!/usr/bin/env bash
# Build the 12 ReciterAI Lambda zips — 6 hot-path + 5 new-researcher
# onboarding (#80 Phase 2) + 1 daily drift evaluator (Phase 10, deployed
# 2026-05-25).
#
# Each zip is built in a clean staging dir under build/, with pip deps
# installed via the public.ecr.aws/lambda/python:3.12 image so any
# compiled extensions match Lambda's runtime ABI. Pure-Python deps
# would work without docker, but using the image is safer and makes
# this script reusable for Lambdas that pull in C extensions.
#
# Usage:
#   scripts/build_lambda_zips.sh                                # build all 12
#   scripts/build_lambda_zips.sh reciterai-onboarding-detector  # build just one
#
# Output: build/<zip_basename>.zip for each Lambda.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_DIR="${REPO_ROOT}/build"
LAMBDA_IMAGE="public.ecr.aws/lambda/python:3.12"

mkdir -p "$BUILD_DIR"

# ---------------------------------------------------------------------------
# Per-Lambda specs
# ---------------------------------------------------------------------------
# Spec encoding (one row per Lambda, pipe-separated, 5 fields):
#   zip_basename | handler_module | pip_deps | first_party_paths | extra_imports
#
# - zip_basename: the full zip / function name, e.g. reciterai-hot-score or
#   reciterai-onboarding-orchestrator. Output is build/<zip_basename>.zip; the
#   `ONLY` positional arg matches this exactly.
# - handler_module: the importable module the import-check loads and asserts a
#   `handler` attribute on (e.g. pipeline_hot.handlers.score).
# - pip_deps: space-separated. Empty = no extras beyond the Lambda runtime.
# - first_party_paths: space-separated, repo-relative. Files copied as-is,
#   directories copied recursively (excluding __pycache__, .pytest_cache).
# - extra_imports: space-separated extra modules the import-check also loads.
#   For Lambdas whose runtime deps are reached only via *function-local*
#   imports — a plain `import <handler_module>` would not load them, so a zip
#   missing their wheels builds green and crashes at runtime. The detector and
#   the hot rollup both `from utils.sql_queries import ...` inside a function.
#   Empty for most rows (note the trailing `|`).
#
# All Lambdas (except reciterai-hot-alert-dispatcher) get utils/ +
# pipeline_common/ + config/ as common baselines. alert-dispatcher needs
# no DB / thresholds / SQL, so it takes only pipeline_common/; it bundles
# its Teams transport (pipeline_enrichment/alerting.py) as first-party.

LAMBDAS=(
  # ---- Hot path (6) -------------------------------------------------------
  "reciterai-hot-orchestrator|pipeline_hot.orchestrator|pymysql>=1.1.0 sqlalchemy>=2.0.0|pipeline_hot/__init__.py pipeline_hot/orchestrator.py pipeline_hot/taxonomy_handshake.py pipeline_enrichment/__init__.py pipeline_enrichment/alerting.py taxonomy_v2.json|"
  "reciterai-hot-score|pipeline_hot.handlers.score|pymysql>=1.1.0 sqlalchemy>=2.0.0 tqdm>=4.67.0 openai>=2.0.0|pipeline_hot/__init__.py pipeline_hot/handlers/__init__.py pipeline_hot/handlers/score.py score_publications.py taxonomy_v2.json|"
  # assign also bundles the approved hierarchy_draft_*.json files (step 3b
  # below): both the onboarding Assign fan-out (#80 Phase 2 / PR 4) and the
  # hot path's AssignFanOut Map (#119) run assign_subtopics per topic, which
  # loads hierarchy_draft_<topic>.json.
  "reciterai-hot-assign|pipeline_hot.handlers.assign||pipeline_hot/__init__.py pipeline_hot/handlers/__init__.py pipeline_hot/handlers/assign.py assign_subtopics.py taxonomy_v2.json prompts|"
  "reciterai-hot-top-topic|pipeline_hot.handlers.top_topic||pipeline_hot/__init__.py pipeline_hot/handlers/__init__.py pipeline_hot/handlers/top_topic.py compute_top_topic.py|"
  # rollup gets pymysql + sqlalchemy as of #80 Phase 2 / #90: the onboarding
  # `--cwid` rollup path queries ReciterDB (get_pmids_for_cwid). That
  # ReciterDB import is function-local, so extra_imports=utils.sql_queries
  # makes the import-check load it — otherwise a missing pymysql wheel would
  # build green and crash only on the first `--cwid` invocation.
  "reciterai-hot-rollup|pipeline_hot.handlers.rollup|pymysql>=1.1.0 sqlalchemy>=2.0.0|pipeline_hot/__init__.py pipeline_hot/handlers/__init__.py pipeline_hot/handlers/rollup.py rollup_by_cwid.py|utils.sql_queries"
  # alert-dispatcher sends Teams alerts via pipeline_enrichment.alerting and
  # classifies stage-skip streaks via stage_skip.py (#121); bundle both
  # modules + the pipeline_enrichment package marker as first-party files.
  "reciterai-hot-alert-dispatcher|pipeline_hot.handlers.alert_dispatcher||pipeline_hot/__init__.py pipeline_hot/handlers/__init__.py pipeline_hot/handlers/alert_dispatcher.py pipeline_hot/handlers/stage_skip.py pipeline_enrichment/__init__.py pipeline_enrichment/alerting.py|"
  # ---- New-researcher onboarding (5) — #80 Phase 2 ----------------------
  # orchestrator.py imports score_publications inside evaluate_onboarding —
  # function-local since #102, so importing the orchestrator module stays
  # lightweight (that is what lets the lean detector zip below bundle
  # orchestrator.py). The orchestrator Lambda still runs evaluate_onboarding,
  # so this zip still bundles score_publications.py + its full scoring dep set
  # (tqdm + openai + pymysql/sqlalchemy). score_publications AND the cost
  # preview's utils.llm_cost are now reached only via function-local imports —
  # extra_imports build-verifies both (a plain handler-module import loads
  # neither). pyyaml: utils.llm_cost parses config/llm_prices.yaml. No
  # taxonomy_v2.json: no onboarding code path reads the taxonomy.
  "reciterai-onboarding-orchestrator|pipeline_onboarding.orchestrator|pymysql>=1.1.0 sqlalchemy>=2.0.0 tqdm>=4.67.0 openai>=2.0.0 pyyaml>=6.0.1|pipeline_onboarding/__init__.py pipeline_onboarding/orchestrator.py score_publications.py pipeline_enrichment/__init__.py pipeline_enrichment/alerting.py|score_publications utils.llm_cost"
  # finalize.py hosts two handlers — `handler` (Finalize) and `notify_handler`
  # (Notify). One zip, deployed as TWO Lambda functions —
  # reciterai-onboarding-finalize and reciterai-onboarding-notify — with
  # different --handler. DynamoDB + urllib alerting only, so no pip deps.
  "reciterai-onboarding-finalize|pipeline_onboarding.finalize||pipeline_onboarding/__init__.py pipeline_onboarding/finalize.py pipeline_enrichment/__init__.py pipeline_enrichment/alerting.py|"
  # The detector reaches two dependency chains only through function-local
  # imports — both build-verified by extra_imports, since a plain `import
  # pipeline_onboarding.detector` loads neither:
  #   - faculty gap scan -> `from utils.sql_queries import ...` (pymysql/sqlalchemy)
  #   - issue-body cost preview -> pipeline_onboarding.orchestrator ->
  #     utils.llm_cost (parses config/llm_prices.yaml — needs pyyaml).
  # The cost preview is why orchestrator.py is bundled here: #102
  # (D-DETECTOR-COST option C) made `import pipeline_onboarding.orchestrator`
  # lightweight — it no longer drags in the score_publications tree — so this
  # lean zip can carry it. score_publications.py itself is NOT bundled.
  "reciterai-onboarding-detector|pipeline_onboarding.detector|pymysql>=1.1.0 sqlalchemy>=2.0.0 pyyaml>=6.0.1|pipeline_onboarding/__init__.py pipeline_onboarding/detector.py pipeline_onboarding/github_issues.py pipeline_onboarding/orchestrator.py pipeline_enrichment/__init__.py pipeline_enrichment/alerting.py|utils.sql_queries pipeline_onboarding.orchestrator utils.llm_cost"
  # derive-topics bundles rollup_by_cwid.py for its two TOPIC#-activity
  # readers — fetch_cwid_topic_activity (onboarding {cwid}) and
  # fetch_topic_activity_for_pmids (hot path {pmids}, #119). Both are
  # DynamoDB-only (no ReciterDB call on either path), so no pip deps.
  "reciterai-onboarding-derive-topics|pipeline_onboarding.assign_fanout||pipeline_onboarding/__init__.py pipeline_onboarding/assign_fanout.py rollup_by_cwid.py|"
  # enrich.py runs run_enrichment_backfill (#112) — synopsis + impact for the
  # CWID's PMID set, the onboarding cascade's Enrich stage. It imports
  # pipeline_enrichment.daily_job at module scope, so the whole
  # pipeline_enrichment/ package is bundled and a plain
  # `import pipeline_onboarding.enrich` loads the full dep tree (openai,
  # sqlalchemy, pyyaml) — no extra_imports needed. No score_publications.py:
  # daily_job does not import it, so this zip skips the scoring tree.
  "reciterai-onboarding-enrich|pipeline_onboarding.enrich|pymysql>=1.1.0 sqlalchemy>=2.0.0 openai>=2.0.0 pyyaml>=6.0.1|pipeline_onboarding/__init__.py pipeline_onboarding/enrich.py pipeline_enrichment|"
  # ---- Drift (1) — Phase 10 daily DRIFT# evaluator, deployed 2026-05-25 ---
  # DynamoDB scans + Teams alerting only (no SQL, no scoring tree) → no pip
  # deps; boto3 is in the runtime, thresholds come from the bundled
  # config/thresholds.json, and the Teams transport is pure urllib. The
  # handler reaches utils.dynamodb_helpers, utils.event_records, and
  # pipeline_enrichment.alerting only via function-local imports, so a plain
  # `import pipeline_drift.evaluator` would load none of the three — they are
  # listed in extra_imports so the build's import-check verifies the bundle
  # actually carries them (a missing pipeline_enrichment/ would otherwise build
  # green and crash on the first WARN/ERROR dispatch). Alerting was migrated
  # off pipeline_common.alert (Slack/`gh`, dead in the Lambda runtime) to the
  # Teams transport on this deploy, matching the hot path + enrichment.
  "reciterai-drift-evaluator|pipeline_drift.evaluator||pipeline_drift/__init__.py pipeline_drift/evaluator.py pipeline_drift/severity.py pipeline_enrichment/__init__.py pipeline_enrichment/alerting.py|utils.dynamodb_helpers utils.event_records pipeline_enrichment.alerting"
  # ---- Taxonomy drift (ADR D5 layer 2) ------------------------------------
  # NOTE: this bundles taxonomy_v2.json, making it a FIFTH replication site of
  # the artifact whose replication the ADR exists to control. It must be: it
  # cannot compare data against the current topic set without carrying it. It
  # is therefore in scope for the ADR's D1 build step and D3 handshake like any
  # other taxonomy-bearing artifact — not exempt because it is the checker.
  "reciterai-taxonomy-drift|pipeline_taxonomy_drift.checker||pipeline_taxonomy_drift/__init__.py pipeline_taxonomy_drift/checker.py pipeline_enrichment/__init__.py pipeline_enrichment/alerting.py utils/taxonomy.py taxonomy_v2.json|utils.dynamodb_helpers utils.taxonomy pipeline_enrichment.alerting"
)

# Common dirs included in every zip except where commented otherwise.
COMMON_DIRS_DEFAULT=("utils" "pipeline_common" "config")
# Override for alert-dispatcher: only pipeline_common (no DB, no thresholds, no SQL).
COMMON_DIRS_ALERT=("pipeline_common")

ONLY="${1:-}"

build_one() {
  local zip_basename="$1"
  local handler_module="$2"
  local pip_deps="$3"
  local first_party="$4"
  local extra_imports="$5"

  local stage="${BUILD_DIR}/${zip_basename}"
  local zip_out="${BUILD_DIR}/${zip_basename}.zip"

  echo "=========================================="
  echo "Building ${zip_basename} → ${zip_out}"
  echo "=========================================="

  rm -rf "$stage"
  mkdir -p "$stage"

  # 1. Pip install (in docker for runtime parity).
  # `--platform linux/amd64` is load-bearing on Apple Silicon hosts:
  # without it, Docker runs the image as ARM64 and pip installs the
  # aarch64 wheels of any package with a compiled extension (notably
  # pydantic_core, which openai 2.x depends on). Lambda functions in
  # this account are x86_64, so an ARM-built zip crashes at cold
  # start with `ModuleNotFoundError: No module named
  # 'pydantic_core._pydantic_core'`. Caught in smoke 7 (2026-05-16).
  if [[ -n "$pip_deps" ]]; then
    echo ">> pip install: $pip_deps"
    docker run --rm \
      --platform linux/amd64 \
      -v "$stage":/var/task \
      --entrypoint /var/lang/bin/pip \
      "$LAMBDA_IMAGE" \
      install --no-compile --target /var/task $pip_deps \
      >/dev/null
  else
    echo ">> no pip deps"
  fi

  # 2. Copy common dirs.
  local common_dirs=("${COMMON_DIRS_DEFAULT[@]}")
  if [[ "$zip_basename" == "reciterai-hot-alert-dispatcher" ]]; then
    common_dirs=("${COMMON_DIRS_ALERT[@]}")
  fi
  for d in "${common_dirs[@]}"; do
    [[ -d "${REPO_ROOT}/${d}" ]] || { echo "common dir missing: $d" >&2; exit 1; }
    rsync -a --exclude='__pycache__' --exclude='.pytest_cache' \
      --exclude='*.pyc' --exclude='test_*.py' \
      "${REPO_ROOT}/${d}/" "${stage}/${d}/"
  done

  # 3. Copy first-party files / dirs.
  for src in $first_party; do
    [[ -e "${REPO_ROOT}/${src}" ]] || { echo "first-party src missing: $src" >&2; exit 1; }
    if [[ -d "${REPO_ROOT}/${src}" ]]; then
      rsync -a --exclude='__pycache__' --exclude='.pytest_cache' \
        --exclude='*.pyc' --exclude='test_*.py' \
        "${REPO_ROOT}/${src}/" "${stage}/${src}/"
    else
      mkdir -p "${stage}/$(dirname "${src}")"
      cp "${REPO_ROOT}/${src}" "${stage}/${src}"
    fi
  done

  # 3b. hot-assign only — bundle the approved hierarchy drafts (#80 PR 4).
  #     assign_subtopics resolves .planning/phases/04-subtopic-system/
  #     hierarchy_draft_<topic>.json relative to cwd (= the zip root, the
  #     handler's REPO_ROOT), so the drafts are copied preserving that path.
  #     Only the hierarchy_draft_*.json files are copied — not the 130+
  #     PLAN/SUMMARY .md siblings in that planning directory.
  if [[ "$zip_basename" == "reciterai-hot-assign" ]]; then
    local draft_rel=".planning/phases/04-subtopic-system"
    [[ -d "${REPO_ROOT}/${draft_rel}" ]] \
      || { echo "hierarchy draft dir missing: ${draft_rel}" >&2; exit 1; }
    mkdir -p "${stage}/${draft_rel}"
    rsync -a --include='hierarchy_draft_*.json' --exclude='*' \
      "${REPO_ROOT}/${draft_rel}/" "${stage}/${draft_rel}/"
    local n_drafts
    n_drafts=$(find "${stage}/${draft_rel}" -name 'hierarchy_draft_*.json' | wc -l | tr -d ' ')
    [[ "$n_drafts" -gt 0 ]] \
      || { echo "no hierarchy drafts bundled into assign zip" >&2; exit 1; }
    echo ">> bundled ${n_drafts} hierarchy drafts"
  fi

  # 4. Strip pre-compiled bytecode from staging (smaller zip + cleaner).
  find "$stage" -name '__pycache__' -prune -exec rm -rf {} +
  find "$stage" -name '*.dist-info' -prune -exec rm -rf {} + 2>/dev/null || true

  # 5. Build zip.
  rm -f "$zip_out"
  (cd "$stage" && zip -qr "$zip_out" . -x "*.DS_Store")

  # 6. Verify the handler module imports cleanly under python3.12 in the
  #    Lambda image (catches missing transitive deps before upload).
  #    `extra_imports` additionally loads modules the handler reaches only
  #    via function-local imports — invisible to a plain `import
  #    <handler_module>`, so a zip missing their wheels would build green and
  #    crash at runtime (the detector / hot rollup both do a function-local
  #    `from utils.sql_queries import ...`).
  local extra_check=""
  for m in $extra_imports; do
    extra_check+="import ${m}; "
  done
  if [[ -n "$extra_imports" ]]; then
    echo ">> import-check: $handler_module (+ extra: $extra_imports)"
  else
    echo ">> import-check: $handler_module"
  fi
  # Same `--platform linux/amd64` rationale as the pip install above —
  # if we ran the import-check on the host arch, it would mask the
  # ARM-vs-x86 wheel mismatch by happily importing under the wrong
  # interpreter arch.
  docker run --rm \
    --platform linux/amd64 \
    -v "$stage":/var/task \
    --entrypoint /var/lang/bin/python3 \
    "$LAMBDA_IMAGE" \
    -c "import sys; sys.path.insert(0, '/var/task'); import ${handler_module} as h; ${extra_check}print('OK', h.__name__, 'handler=' + (h.handler.__qualname__ if hasattr(h, 'handler') else '<missing>'))"

  local size=$(du -h "$zip_out" | cut -f1)
  echo ">> built ${zip_out} (${size})"
}

# Iterate.
for spec in "${LAMBDAS[@]}"; do
  IFS='|' read -r zip_basename handler_module pip_deps first_party extra_imports <<< "$spec"
  if [[ -n "$ONLY" && "$ONLY" != "$zip_basename" ]]; then
    continue
  fi
  build_one "$zip_basename" "$handler_module" "$pip_deps" "$first_party" "$extra_imports"
done

echo ""
echo "=========================================="
echo "All zips built. Inventory:"
echo "=========================================="
ls -lh "$BUILD_DIR"/*.zip 2>/dev/null

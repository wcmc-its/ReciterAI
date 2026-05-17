#!/usr/bin/env bash
# Build the 6 hot-path Lambda zips per the issue-72 addendum manifests.
#
# Each zip is built in a clean staging dir under build/, with pip deps
# installed via the public.ecr.aws/lambda/python:3.12 image so any
# compiled extensions match Lambda's runtime ABI. Pure-Python deps
# would work without docker, but using the image is safer and makes
# this script reusable for future Lambdas that pull in C extensions.
#
# Usage:
#   scripts/build_lambda_zips.sh                # build all 6
#   scripts/build_lambda_zips.sh orchestrator   # build just one
#
# Output: build/<lambda-name>.zip for each Lambda.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_DIR="${REPO_ROOT}/build"
LAMBDA_IMAGE="public.ecr.aws/lambda/python:3.12"

mkdir -p "$BUILD_DIR"

# ---------------------------------------------------------------------------
# Per-Lambda specs
# ---------------------------------------------------------------------------
# Spec encoding (one row per Lambda, pipe-separated):
#   name | pip_deps | first_party_paths
# - pip_deps: space-separated. Empty = no extras beyond Lambda runtime.
# - first_party_paths: space-separated, repo-relative. Files copied as-is,
#   directories copied recursively (excluding __pycache__, .pytest_cache).
#
# All Lambdas (except alert-dispatcher) get utils/ + pipeline_common/ +
# config/ as common baselines. alert-dispatcher is the stub — only
# pipeline_common/ for the eventual real-dispatch swap.

LAMBDAS=(
  "orchestrator|pymysql>=1.1.0 sqlalchemy>=2.0.0|pipeline_hot/__init__.py pipeline_hot/orchestrator.py pipeline_enrichment/__init__.py pipeline_enrichment/alerting.py taxonomy_v2.json"
  "score|pymysql>=1.1.0 sqlalchemy>=2.0.0 tqdm>=4.67.0 openai>=2.0.0|pipeline_hot/__init__.py pipeline_hot/handlers/__init__.py pipeline_hot/handlers/score.py score_publications.py taxonomy_v2.json"
  # assign also bundles the approved hierarchy_draft_*.json files (step 3b
  # below) as of #80 Phase 2 / PR 4: the onboarding Assign fan-out runs
  # assign_subtopics per topic, which loads hierarchy_draft_<topic>.json. The
  # hot path's own assign is still a Pass stub, so the drafts are additive —
  # the zip carries data it does not yet exercise. Redeployed in PR 6.
  "assign||pipeline_hot/__init__.py pipeline_hot/handlers/__init__.py pipeline_hot/handlers/assign.py assign_subtopics.py taxonomy_v2.json prompts"
  "top-topic||pipeline_hot/__init__.py pipeline_hot/handlers/__init__.py pipeline_hot/handlers/top_topic.py compute_top_topic.py"
  # rollup gets pymysql + sqlalchemy as of #80 Phase 2 / #90: the onboarding
  # `--cwid` rollup path queries ReciterDB (get_pmids_for_cwid) for the
  # CWID's accepted PMID set. The hot-path `--cwids` CSV rollup never opens a
  # DB connection, but the one Lambda serves both modes. Redeployed in PR 6.
  "rollup|pymysql>=1.1.0 sqlalchemy>=2.0.0|pipeline_hot/__init__.py pipeline_hot/handlers/__init__.py pipeline_hot/handlers/rollup.py rollup_by_cwid.py"
  "alert-dispatcher||pipeline_hot/__init__.py pipeline_hot/handlers/__init__.py pipeline_hot/handlers/alert_dispatcher.py"
)

# Common dirs included in every zip except where commented otherwise.
COMMON_DIRS_DEFAULT=("utils" "pipeline_common" "config")
# Override for alert-dispatcher: only pipeline_common (no DB, no thresholds, no SQL).
COMMON_DIRS_ALERT=("pipeline_common")

ONLY="${1:-}"

build_one() {
  local name="$1"
  local pip_deps="$2"
  local first_party="$3"

  local stage="${BUILD_DIR}/${name}"
  local zip_out="${BUILD_DIR}/${name}.zip"

  echo "=========================================="
  echo "Building reciterai-hot-${name} → ${zip_out}"
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
  if [[ "$name" == "alert-dispatcher" ]]; then
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

  # 3b. assign Lambda only — bundle the approved hierarchy drafts (#80 PR 4).
  #     assign_subtopics resolves .planning/phases/04-subtopic-system/
  #     hierarchy_draft_<topic>.json relative to cwd (= the zip root, the
  #     handler's REPO_ROOT), so the drafts are copied preserving that path.
  #     Only the hierarchy_draft_*.json files are copied — not the 130+
  #     PLAN/SUMMARY .md siblings in that planning directory.
  if [[ "$name" == "assign" ]]; then
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
  local handler_module
  case "$name" in
    orchestrator) handler_module="pipeline_hot.orchestrator" ;;
    score)        handler_module="pipeline_hot.handlers.score" ;;
    assign)       handler_module="pipeline_hot.handlers.assign" ;;
    top-topic)    handler_module="pipeline_hot.handlers.top_topic" ;;
    rollup)       handler_module="pipeline_hot.handlers.rollup" ;;
    alert-dispatcher) handler_module="pipeline_hot.handlers.alert_dispatcher" ;;
  esac
  echo ">> import-check: $handler_module"
  # Same `--platform linux/amd64` rationale as the pip install above —
  # if we ran the import-check on the host arch, it would mask the
  # ARM-vs-x86 wheel mismatch by happily importing under the wrong
  # interpreter arch.
  docker run --rm \
    --platform linux/amd64 \
    -v "$stage":/var/task \
    --entrypoint /var/lang/bin/python3 \
    "$LAMBDA_IMAGE" \
    -c "import sys; sys.path.insert(0, '/var/task'); import ${handler_module} as h; print('OK', h.__name__, 'handler=' + (h.handler.__qualname__ if hasattr(h, 'handler') else '<missing>'))"

  local size=$(du -h "$zip_out" | cut -f1)
  echo ">> built ${zip_out} (${size})"
}

# Iterate.
for spec in "${LAMBDAS[@]}"; do
  IFS='|' read -r name pip_deps first_party <<< "$spec"
  if [[ -n "$ONLY" && "$ONLY" != "$name" ]]; then
    continue
  fi
  build_one "$name" "$pip_deps" "$first_party"
done

echo ""
echo "=========================================="
echo "All zips built. Inventory:"
echo "=========================================="
ls -lh "$BUILD_DIR"/*.zip 2>/dev/null

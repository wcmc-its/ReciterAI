"""build_lambda_zips.sh spec-shape contract (#80 Phase 2 / PR 6).

`scripts/build_lambda_zips.sh` carries one pipe-separated spec row per Lambda
zip. This test pins the shape so an edit to the `LAMBDAS` array cannot
silently drop a zip or strip the `extra_imports` field.

`extra_imports` is load-bearing: the onboarding detector and the hot rollup
reach `utils.sql_queries` (pymysql/sqlalchemy) only through *function-local*
imports, so a plain `import <handler_module>` import-check would not load
them — without `extra_imports` listing `utils.sql_queries`, a zip missing the
pymysql wheel would build green and crash on the first real invocation.

Same text-shape approach as `test_state_machine_asl_shape.py` /
`test_infra_eventbridge_shape.py`. It guards the spec rows; that `build_one`
still *honors* `extra_imports` is covered by the build's own import-check.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "build_lambda_zips.sh"

# zip_basename | handler_module | pip_deps | first_party | extra_imports
_FIELDS = ("zip_basename", "handler_module", "pip_deps", "first_party", "extra_imports")

EXPECTED_ZIPS = {
    "reciterai-hot-orchestrator",
    "reciterai-hot-score",
    "reciterai-hot-assign",
    "reciterai-hot-top-topic",
    "reciterai-hot-rollup",
    "reciterai-hot-alert-dispatcher",
    "reciterai-onboarding-orchestrator",
    "reciterai-onboarding-finalize",
    "reciterai-onboarding-detector",
    "reciterai-onboarding-derive-topics",
}


def _parse_specs() -> dict[str, dict[str, str]]:
    """Extract the LAMBDAS array spec rows → {zip_basename: {field: value}}."""
    rows: dict[str, dict[str, str]] = {}
    for line in SCRIPT.read_text().splitlines():
        m = re.match(r'\s*"(reciterai-[^"]*)"\s*$', line)
        if not m:
            continue
        parts = m.group(1).split("|")
        assert len(parts) == len(_FIELDS), (
            f"spec row has {len(parts)} fields, expected {len(_FIELDS)}: {line!r}"
        )
        row = dict(zip(_FIELDS, parts))
        rows[row["zip_basename"]] = row
    return rows


@pytest.fixture(scope="module")
def specs() -> dict[str, dict[str, str]]:
    return _parse_specs()


def test_all_ten_zips_present(specs):
    assert set(specs) == EXPECTED_ZIPS, (
        f"zip drift: missing={EXPECTED_ZIPS - set(specs)}, "
        f"unexpected={set(specs) - EXPECTED_ZIPS}"
    )


def test_every_row_has_a_dotted_handler_module(specs):
    for name, row in specs.items():
        assert "." in row["handler_module"], (
            f"{name}: handler_module {row['handler_module']!r} is not a dotted path"
        )


@pytest.mark.parametrize(
    "zip_basename", ["reciterai-onboarding-detector", "reciterai-hot-rollup"]
)
def test_function_local_sql_dep_is_in_extra_imports(specs, zip_basename):
    """The detector + hot rollup reach utils.sql_queries via a function-local
    import; the import-check verifies it only if extra_imports lists it."""
    assert "utils.sql_queries" in specs[zip_basename]["extra_imports"].split()


def test_onboarding_finalize_has_no_pip_deps(specs):
    """The finalize zip (DynamoDB + urllib alerting only) carries no pip deps;
    it is deployed as both reciterai-onboarding-finalize and -notify."""
    assert specs["reciterai-onboarding-finalize"]["pip_deps"] == ""


def test_onboarding_orchestrator_bundles_score_publications(specs):
    """orchestrator.py imports score_publications function-locally (inside
    evaluate_onboarding, since #102); the orchestrator Lambda still runs that
    path, so its zip still bundles score_publications.py + the full scoring
    dep set."""
    row = specs["reciterai-onboarding-orchestrator"]
    assert "score_publications.py" in row["first_party"].split()
    for dep in ("tqdm", "openai", "pymysql", "sqlalchemy"):
        assert dep in row["pip_deps"], f"orchestrator pip_deps missing {dep}"


def test_onboarding_orchestrator_build_verifies_function_local_deps(specs):
    """#102: orchestrator.py reaches score_publications + the cost preview's
    utils.llm_cost only via function-local imports — a plain import of the
    handler module loads neither — so both must be in extra_imports for the
    build-check to verify them, and pyyaml must be a pip dep (utils.llm_cost
    parses config/llm_prices.yaml)."""
    row = specs["reciterai-onboarding-orchestrator"]
    extra = row["extra_imports"].split()
    assert "score_publications" in extra
    assert "utils.llm_cost" in extra
    assert "pyyaml" in row["pip_deps"]


def test_onboarding_detector_bundles_lightweight_cost_preview_chain(specs):
    """#102 (D-DETECTOR-COST option C): the detector's issue-body cost preview
    reaches utils.llm_cost via pipeline_onboarding.orchestrator. The zip
    bundles orchestrator.py + pyyaml and extra_imports build-verifies the
    chain — but score_publications.py stays unbundled, which is option C's
    whole point: orchestrator.py is lightweight, so the lean detector zip
    carries it without the score_publications scoring tree."""
    row = specs["reciterai-onboarding-detector"]
    first_party = row["first_party"].split()
    extra = row["extra_imports"].split()
    assert "pipeline_onboarding/orchestrator.py" in first_party
    assert "score_publications.py" not in first_party
    assert "pyyaml" in row["pip_deps"]
    assert "pipeline_onboarding.orchestrator" in extra
    assert "utils.llm_cost" in extra

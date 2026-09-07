"""The no-live-AWS guard in the repo-root conftest.py, tested.

The guard exists because `run.main()` grew an unconditional DynamoDB publish and one
existing unit test drove it with no --dry-run and no stub, so a default `pytest tests/`
issued fourteen live UpdateItems against the shared production table -- green, every
run. A guard against that which is itself unverified would be the same class of bug one
level up: declared, never connected, nobody notices until it matters.

So every property this file asserts is asserted from an UNMARKED test, and therefore
runs on the default `python -m pytest -q` that CI issues. Two of those properties can
only be observed from a differently-configured pytest run -- the `aws` carve-out needs
`-m aws`, which pytest.ini's `addopts` excludes by default, and the repo-wide reach
needs a run that collects outside `tests/` -- so those two drive a nested pytest via
`pytester` rather than being marked and quietly deselected.

Nothing here touches the network. The guard raises before it delegates, so it is called
directly with a dummy `self`; no client is ever constructed. The nested runs assert on
the installed call seam by inspection and make no AWS call either.
"""
from __future__ import annotations

from pathlib import Path

import botocore.client
import pytest

from conftest import UnstubbedAWSCall

REPO_ROOT = Path(__file__).resolve().parents[1]


def _installed_guard():
    return botocore.client.BaseClient._make_api_call


def test_the_guard_is_installed_for_an_unmarked_test():
    """This test is not marked `aws`, so botocore's call seam is the guard right now."""
    assert _installed_guard().__name__ == "_blocked"


def test_the_guard_raises_on_an_unstubbed_call_and_names_the_target():
    with pytest.raises(UnstubbedAWSCall) as excinfo:
        _installed_guard()(object(), "UpdateItem", {"TableName": "reciterai"})
    msg = str(excinfo.value)
    assert "NOT marked `aws`" in msg
    assert "UpdateItem" in msg and "reciterai" in msg
    # It has to say what to DO, or the next author marks the test `aws` to make the red
    # go away and quietly deselects it from CI forever.
    assert "Fix the TEST, not this fixture" in msg


def test_the_guard_is_not_an_exception_so_never_fatal_wrappers_cannot_swallow_it():
    """The load-bearing property. `persist.put_core_staff_dict_counts` and
    `pipeline_cold.run._read_prev_version_from_latest_manifest` both wrap their AWS call
    in `except Exception:` -> warn -> carry on. An Exception here would be swallowed by
    exactly the code paths this guard exists to police, and the test would go green
    having still tried to write to production."""
    assert issubclass(UnstubbedAWSCall, BaseException)
    assert not issubclass(UnstubbedAWSCall, Exception)


def test_the_re_export_is_the_class_the_installed_guard_actually_raises():
    """`tests/conftest.py` re-exports the root conftest's class; it must be THE class.

    pytest imports the root conftest and `tests/conftest.py` under the same module name
    ("conftest" -- neither directory is a package), so the re-export cannot spell itself
    `from conftest import ...` and instead names the `_reciterai_root_conftest` alias the
    root conftest publishes. The failure mode of getting that wrong is silent and total:
    re-executing the root conftest under a second name would mint a SECOND, unrelated
    `UnstubbedAWSCall`, `pytest.raises(UnstubbedAWSCall)` above would stop catching what
    the fixture raises, and the guard's own tests would start passing vacuously.
    """
    import _reciterai_root_conftest as root_conftest
    import conftest as tests_conftest

    # The bare name `conftest` resolves to tests/conftest.py from inside tests/ ...
    assert Path(tests_conftest.__file__).resolve() == REPO_ROOT / "tests" / "conftest.py"
    # ... and the class it hands out is the root conftest's one object, not a copy.
    assert tests_conftest.UnstubbedAWSCall is UnstubbedAWSCall
    assert root_conftest.UnstubbedAWSCall is UnstubbedAWSCall
    assert root_conftest.UnstubbedAWSCall.__module__ == tests_conftest.UnstubbedAWSCall.__module__


def test_the_guard_covers_test_modules_outside_the_tests_tree(pytester):
    """The reason the guard lives at the repo root: CI collects more than `tests/`.

    `.github/workflows/pytest.yml` runs a bare `python -m pytest -q` from the repo root,
    which also collects `utils/test_dynamodb_subtopic_migration.py` and
    `utils/test_s3_client.py`. While the guard was an autouse fixture in
    `tests/conftest.py` those 13 tests ran against botocore's REAL `_make_api_call`, and
    nothing said so -- they happen to be MagicMock-driven, so the hole was invisible.

    Asserted by running a `utils/` module in a nested pytest under a probe plugin that
    records the installed call seam for every node. The set of seams observed must be
    exactly {`_blocked`}: a single unguarded node would add `_make_api_call` to it and
    the exact match below would fail.
    """
    pytester.makepyfile(
        _guard_seam_probe='''
        import botocore.client
        import pytest

        _seams = set()

        # `wrapper=`, not the deprecated `hookwrapper=`: available since pytest 8.0,
        # which requirements-dev.txt already pins, and it survives pytest 9.
        @pytest.hookimpl(wrapper=True)
        def pytest_runtest_call(item):
            _seams.add(getattr(botocore.client.BaseClient._make_api_call, "__name__", "?"))
            return (yield)

        def pytest_sessionfinish(session, exitstatus):
            print("GUARD-SEAMS:<" + ",".join(sorted(_seams)) + ">")
        '''
    )
    result = pytester.runpytest_subprocess(
        str(REPO_ROOT / "utils" / "test_s3_client.py"),
        str(REPO_ROOT / "utils" / "test_dynamodb_subtopic_migration.py"),
        "-p",
        "_guard_seam_probe",
        "-q",
        "-s",
    )
    outcomes = result.parseoutcomes()
    assert result.ret == 0, "the nested run over utils/ must be green"
    assert outcomes.get("failed", 0) == 0
    assert outcomes.get("passed", 0) > 0, "the nested run must have executed nodes"
    # Delimited with <> rather than [], which fnmatch would read as a character class.
    result.stdout.fnmatch_lines(["*GUARD-SEAMS:<_blocked>*"])


def test_the_guard_steps_aside_for_an_aws_marked_test_and_that_is_checked_by_default(
    pytester,
):
    """The carve-out, asserted from an UNMARKED test so CI actually checks it.

    `test_the_guard_steps_aside_for_an_aws_marked_test` below is the real assertion, but
    it is itself `@pytest.mark.aws` and pytest.ini's `addopts = -m "not aws and not
    mariadb"` deselects it, so on its own it never runs in CI: an edit that made the
    guard swallow `aws`-marked tests too would go green. This drives it in a nested run
    with `-m aws` (the command-line `-m` overrides the one in `addopts`), so the property
    is verified on every default run.

    Not vacuous if the guard vanished entirely: the nested run reaches the marked test
    only through `tests/conftest.py` -> the root conftest, so a root conftest that failed
    to load is an ImportError here, and a guard fixture that was deleted outright is
    caught by `test_the_guard_is_installed_for_an_unmarked_test` above.
    """
    node = f"{Path(__file__).resolve()}::test_the_guard_steps_aside_for_an_aws_marked_test"
    result = pytester.runpytest_subprocess(node, "-m", "aws", "-q")
    assert result.ret == 0, "the `aws`-marked carve-out test must pass under `-m aws`"
    result.assert_outcomes(passed=1)


@pytest.mark.aws
def test_the_guard_steps_aside_for_an_aws_marked_test():
    """Deselected by default; driven under `-m aws` by the nested run above.

    The `aws` marker means "requires live credentials and a writable TEST table", so
    those tests must reach the real botocore call seam. Asserted by inspection only --
    this makes no AWS call of its own.
    """
    assert _installed_guard().__name__ != "_blocked"

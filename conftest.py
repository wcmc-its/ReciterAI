"""Repo-root pytest config: the no-live-AWS guard, installed for the WHOLE collection.

This file exists at the repo root, rather than in `tests/`, because of what CI
actually runs. `.github/workflows/pytest.yml` runs a bare `python -m pytest -q` from
the repo root, and the repo root collects more than `tests/`: `utils/` ships two of
its own test modules, `utils/test_dynamodb_subtopic_migration.py` (7 tests) and
`utils/test_s3_client.py` (6). Stated here as a DELTA rather than as two absolute
totals: absolute totals rot the moment anyone adds a test, and they are this file's
load-bearing justification, so a stale pair reads as a regression to whoever
re-measures. It reproduces as bare `pytest -q` minus `pytest tests/ -q` = 13, which
matches `pytest utils/ -q` exactly. That the difference is those two modules is the
invariant; the totals on the day are not.

While the guard lived in `tests/conftest.py`, an autouse fixture there could only
reach nodes under `tests/`, so those 13 CI tests ran with botocore's real
`_make_api_call` still in place -- the fixture's own docstring promised coverage of
"a default pytest run" that it did not deliver for the command CI issues. Neither
`utils/` test attempts an AWS call today (both drive MagicMocks), so nothing was
escaping; the hole was that the guard's blast radius was narrower than the collection
it was written to police, and nothing would have said so.

A root conftest's autouse fixtures apply to every collected node beneath it, so the
guard now covers any test module added anywhere in the repo, not just ones a future
author happens to put under `tests/`.
"""
from __future__ import annotations

import sys

import pytest

# `pytester` powers tests/test_conftest_aws_guard.py's check that the guard steps aside
# for `aws`-marked tests. That property can only be observed from a run with `-m aws`,
# which pytest.ini's `addopts = -m "not aws and not mariadb"` excludes by default, so
# the check has to drive a nested pytest run. `pytest_plugins` is only honoured in the
# ROOT conftest (pytest 8 errors on it in a non-root conftest), which is here.
pytest_plugins = ["pytester"]


class UnstubbedAWSCall(BaseException):
    """A test not marked `aws` tried to reach live AWS.

    Deliberately a BaseException and NOT an Exception. The callers this guard exists
    to catch are frequently wrapped in a never-fatal `except Exception:` that logs a
    warning and carries on -- `persist.put_core_staff_dict_counts` and
    `pipeline_cold.run._read_prev_version_from_latest_manifest` are both shaped exactly
    like that. An Exception would be swallowed there and the test would still go green,
    so the guard would prevent the damage while preserving the silence that let the
    damage ship. This walks straight out to pytest and fails the test.
    """


# pytest imports BOTH this file and `tests/conftest.py` under the module name
# "conftest" -- neither directory is a package, so each conftest's own directory is
# what gets prepended to sys.path and `conftest` is the resulting module name for
# both. This module is imported first (rootdir down), then `tests/conftest.py`
# REPLACES `sys.modules["conftest"]` with itself. So `tests/conftest.py` cannot
# re-export the guard with `from conftest import ...`: at that moment the name refers
# to the partially-initialised tests/conftest module, i.e. itself.
#
# Publishing an unambiguous alias is what lets `tests/conftest.py` name THIS module and
# get THIS class object, instead of re-executing the file into a second, unrelated
# `UnstubbedAWSCall` that `pytest.raises` would not catch. Plain assignment, not a
# try/except: if this module ever fails to load, the re-export raises ImportError at
# collection time and the suite goes red, which is the correct outcome -- a silently
# absent guard is the exact failure mode this file exists to prevent.
# `tests/test_conftest_aws_guard.py` asserts the identity holds.
sys.modules["_reciterai_root_conftest"] = sys.modules[__name__]


def _moto_is_intercepting() -> bool:
    """True while moto's in-process mock is active, so the call never leaves the box.

    moto registers its stubber on botocore's `before-send` event, which fires INSIDE
    `Endpoint.make_request` -- i.e. below `_make_api_call`. So a moto-backed call looks
    identical to a live one at the layer this guard patches, and blocking it would
    delete real offline coverage (`test_put_candidate_real_condition_on_moto` exercises
    the actual ConditionExpression against a moto table). This is the one narrow carve
    out, and it is a positive check on moto's own flag rather than a test-name
    allowlist.

    Fails CLOSED on purpose: if moto is absent, or moves this symbol in a future
    release, this returns False and the call is blocked. A red moto test is a five
    minute fix; a guard that fails open is how the production write happens again.
    """
    try:
        from moto.core.models import botocore_stubber
    except Exception:
        return False
    return bool(getattr(botocore_stubber, "enabled", False))


@pytest.fixture(autouse=True)
def _no_live_aws_unless_marked(request, monkeypatch):
    """Fail any test that reaches AWS unless it is marked `aws`. (Guards pytest.ini.)

    pytest.ini's `aws` marker means "requires live AWS credentials and a writable
    DynamoDB TEST table" and is deselected by default, so a plain `pytest` run is
    contractually a mock-only run that touches no AWS at all. Nothing enforced that.
    It was enforced by every author remembering to stub every seam, on a repo whose
    entrypoints construct their own clients lazily and deep inside `main()`.

    That failed exactly as you would expect. `run.main()` grew an unconditional publish
    block, one existing `main()`-level test drove it with no --dry-run and no stub, and
    a default `pytest tests/` silently issued fourteen live UpdateItems against the
    shared production `reciterai` table on any machine with credentials in the shell --
    green, on every run, with the never-fatal wrapper swallowing anything that went
    wrong. Stubbing that one test fixes today. This fixture is what makes the next one
    red instead of silent.

    Patched at `BaseClient._make_api_call`: below every boto3 client, above the
    network, and it catches a client the test never sees because some `main()` built
    it three frames down. Constructing a client is still free -- only an actual API
    call trips this -- so the many tests that build a MagicMock or a real-but-unused
    client are unaffected.

    Autouse from the ROOT conftest, so it covers every collected node in the repo --
    including `utils/`'s own test modules, which the bare `python -m pytest -q` that CI
    runs collects and a `tests/`-scoped fixture could never have reached.
    """
    if request.node.get_closest_marker("aws"):
        return

    import botocore.client

    real_make_api_call = botocore.client.BaseClient._make_api_call

    def _blocked(self, operation_name, api_params):
        if _moto_is_intercepting():
            return real_make_api_call(self, operation_name, api_params)
        try:
            service = self.meta.service_model.service_name
        except Exception:  # pragma: no cover - defensive
            service = "aws"
        target = api_params.get("TableName") or api_params.get("Bucket") or ""
        raise UnstubbedAWSCall(
            f"{request.node.nodeid} is NOT marked `aws`, but it tried to call "
            f"{service}.{operation_name}"
            + (f" on {target!r}" if target else "")
            + ". A default `pytest` run must never reach AWS -- it runs on "
            "developer machines and in CI with real credentials in the environment, "
            "so an unstubbed write lands in the SHARED PRODUCTION table, and an "
            "unstubbed read passes green while quietly depending on the network.\n"
            "Fix the TEST, not this fixture: stub the client the code under test "
            "builds (pass `client=`, or monkeypatch the persist/publish function that "
            "constructs it -- note the entrypoints import theirs lazily inside "
            "`main()`, so the seam is usually the module attribute, not an argument).\n"
            "Mark it `@pytest.mark.aws` ONLY if it genuinely needs live credentials "
            "and a writable TEST table; that marker is deselected by default and the "
            "test will stop running in CI."
        )

    monkeypatch.setattr(botocore.client.BaseClient, "_make_api_call", _blocked)

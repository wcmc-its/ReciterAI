"""The no-live-AWS guard in conftest.py, tested.

The guard exists because `run.main()` grew an unconditional DynamoDB publish and one
existing unit test drove it with no --dry-run and no stub, so a default `pytest tests/`
issued fourteen live UpdateItems against the shared production table -- green, every
run. A guard against that which is itself unverified would be the same class of bug one
level up: declared, never connected, nobody notices until it matters.

Nothing here touches the network. The guard raises before it delegates, so it is called
directly with a dummy `self`; no client is ever constructed.
"""
from __future__ import annotations

import botocore.client
import pytest

from conftest import UnstubbedAWSCall


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


@pytest.mark.aws
def test_the_guard_steps_aside_for_an_aws_marked_test():
    """Deselected by default; run with `pytest tests/test_conftest_aws_guard.py -m aws`.

    The `aws` marker means "requires live credentials and a writable TEST table", so
    those tests must reach the real botocore call seam. Asserted by inspection only --
    this makes no AWS call of its own.
    """
    assert _installed_guard().__name__ != "_blocked"

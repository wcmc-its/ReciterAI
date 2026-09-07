"""Shared pytest fixtures for the ReciterAI test suite."""
from __future__ import annotations

import sys

import pytest


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
    DynamoDB TEST table" and is deselected by default, so a plain `pytest tests/` is
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
            + ". A default `pytest tests/` run must never reach AWS -- it runs on "
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


@pytest.fixture(autouse=True)
def _taxonomy_preflight_baseline(monkeypatch):
    """Pin the ADR-D3 taxonomy preflight baseline to this checkout's own taxonomy.

    `pipeline_cold.run.main()` and `pipeline_hierarchy.publish.main()` run a
    blocking preflight whose git fallback fetches origin/main — network, and a
    moving target. Tests validate against the bundled taxonomy itself so the
    preflight passes deterministically offline. Preflight tests override the
    env var (or call the git helper directly) to exercise the failure paths.
    """
    from utils.taxonomy import current_content_hash

    monkeypatch.setenv("RECITERAI_EXPECTED_TAXONOMY_HASH", current_content_hash())


@pytest.fixture(autouse=True)
def _stub_hot_taxonomy_handshake(monkeypatch):
    """Default the ADR-D3 hot-path taxonomy handshake to OK across the suite.

    `pipeline_hot.orchestrator.handler()` now runs the handshake first, and
    the real implementation downloads deployed peer zips via boto3 — network
    and AWS, neither of which unit tests may touch. Handshake tests exercise
    `pipeline_hot.taxonomy_handshake` directly with injected clients and
    re-patch this seam where they drive the orchestrator. Only patches the
    module if already imported (same posture as `_stub_scan_invalid_pmids`).
    """
    mod = sys.modules.get("pipeline_hot.orchestrator")
    if mod is not None and hasattr(mod, "run_taxonomy_handshake"):
        monkeypatch.setattr(
            mod, "run_taxonomy_handshake", lambda: {"status": "ok"}
        )


@pytest.fixture(autouse=True)
def _stub_scan_invalid_pmids(monkeypatch):
    """Default `scan_invalid_pmids` to an empty list across the suite (#150 item 3).

    `scan_invalid_pmids` does a full-table DynamoDB Scan with a paginating
    `while True` loop. The many `score_publications.main()` and orchestrator
    tests drive a bare `MagicMock` DynamoDB client, against which the paginator
    never terminates (`resp.get('LastEvaluatedKey')` is a truthy mock), so the
    invalid cull would hang. Defaulting it to `[]` makes the cull a no-op for
    tests that don't care about it. Tests that exercise the invalid exclude
    (the eligibility-sweep and `cull_invalid_publications` tests) re-patch or
    call the function directly, overriding this default.

    Only patches modules already imported, so it never forces an import or
    touches tests in unrelated trees.
    """
    for mod_name in ("score_publications", "pipeline_hot.orchestrator"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "scan_invalid_pmids"):
            monkeypatch.setattr(mod, "scan_invalid_pmids", lambda *a, **k: [])

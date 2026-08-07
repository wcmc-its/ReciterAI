"""Shared pytest fixtures for the ReciterAI test suite."""
from __future__ import annotations

import sys

import pytest


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

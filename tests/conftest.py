"""Shared pytest fixtures for the `tests/` tree.

The no-live-AWS guard (`UnstubbedAWSCall` + the autouse `_no_live_aws_unless_marked`
fixture) moved to the REPO-ROOT `conftest.py`. It had to: CI runs a bare
`python -m pytest -q` from the repo root, which also collects the 13 tests in
`utils/`'s own two test modules, and an autouse fixture in this file can only reach
nodes under `tests/`. See the root conftest's docstring for how to reproduce that gap.

`UnstubbedAWSCall` is re-exported here so `from conftest import UnstubbedAWSCall` --
which resolves to THIS module for anything under `tests/` -- keeps working.
"""
from __future__ import annotations

import sys

import pytest

# Not `from conftest import ...`: pytest imports the root conftest and this file under
# the SAME module name "conftest" (neither directory is a package), and this file's
# entry has already replaced the root one in sys.modules by the time this line runs, so
# that spelling would be a circular self-import. `_reciterai_root_conftest` is the alias
# the root conftest publishes for exactly this purpose, and it resolves to the SAME
# class object the autouse guard raises -- asserted in tests/test_conftest_aws_guard.py.
#
# Left deliberately brittle, and this is the one-hop answer for whoever hits it: any
# invocation that stops pytest loading the ROOT conftest fails LOUDLY here and runs 0
# tests. `--confcutdir=tests` raises `ModuleNotFoundError: No module named
# '_reciterai_root_conftest'` and collects nothing; `--noconftest` ends in a collection
# ERROR and `Interrupted`. Different mechanisms, same outcome -- neither is a silent
# pass, which is the only property that matters here.
#
# Measured, so nobody re-derives it: under both `--rootdir=tests` and a plain `pytest`
# run from inside `tests/`, the root conftest still loads and the guard still installs
# -- pytest.ini keeps rootdir at the repo root and confcutdir defaults to rootdir. (A
# plain run from inside `tests/` is not otherwise green: ~6 failures and ~14 errors in
# test_spotlight_publish / test_spotlight_or_preflight / test_feedback_cli come from
# cwd-relative fixture paths. Pre-existing, unrelated to the guard, and all pass from
# the repo root -- which is what CI does.)
#
# Making the import survive would be a downgrade, not a repair. `--confcutdir=tests`
# does not merely hide the alias; it stops the root conftest loading AT ALL, autouse
# guard fixture included. A "resilient" import therefore buys a GREEN run with
# botocore's real `_make_api_call` still installed -- verified by pre-seeding the alias
# in sys.modules and re-running a tests/ module under `--confcutdir=tests`: all green,
# with the installed seam `_make_api_call` rather than the guard's `_blocked`. That is
# the silent unguarded run this whole arrangement exists to prevent, wearing robustness
# as a costume. Loud beats silent; run pytest from the repo root, which is what CI's
# `python -m pytest -q` does.
from _reciterai_root_conftest import UnstubbedAWSCall  # noqa: F401  (re-export)


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

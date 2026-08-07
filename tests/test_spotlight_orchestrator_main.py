"""#329 shape a2 — pipeline_spotlight.orchestrator.main() exit-code contract.

The monthly ECS task's success signal is this exit code: both gate outcomes
(skipped and complete) are 0; any failure is non-zero AFTER handler() has
already written the failed STAGE# row and dispatched the ERROR alert.
"""

from __future__ import annotations

import pytest

from pipeline_spotlight import orchestrator


def test_main_returns_zero_on_skipped(monkeypatch, capsys):
    monkeypatch.setattr(
        orchestrator, "handler", lambda event, context=None: {"status": "skipped"}
    )
    assert orchestrator.main() == 0
    assert '"skipped"' in capsys.readouterr().out


def test_main_returns_zero_on_complete(monkeypatch):
    monkeypatch.setattr(
        orchestrator,
        "handler",
        lambda event, context=None: {"status": "complete", "regenerated": True},
    )
    assert orchestrator.main() == 0


def test_main_returns_nonzero_when_handler_raises(monkeypatch):
    def boom(event, context=None):
        raise RuntimeError("backfill exited 1")

    monkeypatch.setattr(orchestrator, "handler", boom)
    assert orchestrator.main() == 1


def test_main_invokes_handler_with_empty_event(monkeypatch):
    """EventBridge Input is not forwarded to ECS targets, so the production
    invocation is exactly handler({})."""
    seen = {}

    def spy(event, context=None):
        seen["event"] = event
        return {"status": "skipped"}

    monkeypatch.setattr(orchestrator, "handler", spy)
    orchestrator.main()
    assert seen["event"] == {}

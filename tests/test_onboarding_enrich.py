"""#80 Phase 2 — pipeline_onboarding.enrich tests.

The Enrich Lambda runs `run_enrichment_backfill` for the CWID's PMID set and
maps the result to a `STAGE#enrichment_backfill#cwid:{cwid}` envelope that
`WriteEnrichStageRow` persists.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

import pipeline_onboarding.enrich as enrich
from pipeline_enrichment.daily_job import EnrichmentBackfillResult


def _result(**kw):
    """An EnrichmentBackfillResult with sensible defaults; override per test."""
    base = dict(
        status="complete",
        requested=3,
        already_complete=0,
        unresolved=0,
        attempted=3,
        succeeded=3,
        failed=0,
        cost_observed_usd=Decimal("0.05"),
        impact_rows_written=3,
    )
    base.update(kw)
    return EnrichmentBackfillResult(**base)


def _wire(monkeypatch, result):
    """Patch enrich's run_enrichment_backfill + get_engine; capture the call."""
    calls: list = []

    def fake_backfill(**kwargs):
        calls.append(kwargs)
        return result

    monkeypatch.setattr(enrich, "run_enrichment_backfill", fake_backfill)
    monkeypatch.setattr(enrich, "get_engine", lambda: MagicMock(name="engine"))
    return calls


_EVENT = {"cwid": "abc1234", "pmids": ["1", "2", "3"]}


def test_complete_returns_complete_stage_row(monkeypatch):
    _wire(monkeypatch, _result(status="complete"))
    env = enrich.handler(dict(_EVENT))
    assert env["PK"] == {"S": "STAGE#enrichment_backfill#cwid:abc1234"}
    assert env["stage"] == {"S": "enrichment_backfill"}
    assert env["scope"] == {"S": "cwid:abc1234"}
    assert env["status"] == {"S": "complete"}
    assert env["records_written"] == {"N": "3"}
    assert env["enrichment_status"] == {"S": "complete"}
    assert env["succeeded"] == {"N": "3"}


def test_partial_is_recorded_as_a_complete_stage_row(monkeypatch):
    """A partial enrichment still ran — the STAGE# row is `complete`; the
    per-PMID gaps live in enrichment_status + failed and surface in the
    onboarding run's own terminal status."""
    _wire(monkeypatch, _result(status="partial", succeeded=2, failed=1))
    env = enrich.handler(dict(_EVENT))
    assert env["status"] == {"S": "complete"}
    assert env["enrichment_status"] == {"S": "partial"}
    assert env["failed"] == {"N": "1"}


def test_no_op_returns_a_skipped_stage_row(monkeypatch):
    _wire(monkeypatch, _result(
        status="no_op", requested=3, already_complete=3,
        attempted=0, succeeded=0, cost_observed_usd=None,
        impact_rows_written=None,
    ))
    env = enrich.handler(dict(_EVENT))
    assert env["status"] == {"S": "skipped"}
    assert "already carry synopsis" in env["skip_reason"]["S"]


def test_failed_returns_a_failed_stage_row(monkeypatch):
    _wire(monkeypatch, _result(
        status="failed", succeeded=0, failed=3,
        failure_reason="all 3 PMID(s) failed",
    ))
    env = enrich.handler(dict(_EVENT))
    assert env["status"] == {"S": "failed"}
    assert env["error_code"] == {"S": "EnrichmentFailed"}
    assert "failed" in env["error_message"]["S"]


def test_ddb_batch_failed_uses_a_distinct_error_code(monkeypatch):
    _wire(monkeypatch, _result(
        status="ddb_batch_failed", succeeded=3, failed=0,
        failure_reason="IMPACT# batch failed: throttled",
    ))
    env = enrich.handler(dict(_EVENT))
    assert env["status"] == {"S": "failed"}
    assert env["error_code"] == {"S": "EnrichmentImpactBatchFailed"}


def test_envelope_is_ddb_attribute_typed(monkeypatch):
    """Every value is a wrapped AttributeValue so WriteEnrichStageRow's
    Item.$ can consume it directly."""
    _wire(monkeypatch, _result())
    env = enrich.handler(dict(_EVENT))
    for k, v in env.items():
        assert isinstance(v, dict) and len(v) == 1, f"{k} not DDB-typed: {v!r}"
    # cost_observed_usd must serialize as a Number, not a String.
    assert "N" in env["cost_observed_usd"]


def test_handler_requires_cwid(monkeypatch):
    _wire(monkeypatch, _result())
    with pytest.raises(ValueError, match="no 'cwid'"):
        enrich.handler({"pmids": ["1"]})


def test_backfill_called_with_pmids_and_a_silent_alert(monkeypatch):
    calls = _wire(monkeypatch, _result())
    enrich.handler(dict(_EVENT))
    assert len(calls) == 1
    assert calls[0]["pmids"] == ["1", "2", "3"]
    assert "engine" in calls[0]
    # Alerts are silenced — the onboarding workflow's finalize owns operator
    # notification (spec R10); the enrich stage does not fire its own.
    assert calls[0]["alert_fn"] is enrich._silent_alert
    assert enrich._silent_alert() is False

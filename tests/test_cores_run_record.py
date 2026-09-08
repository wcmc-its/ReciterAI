"""The `STAGE#cores_run#GLOBAL` liveness row `pipeline_cores.run.main()` writes.

SPS's /edit/etl-status board grades a producer on its STAGE# row. Without one it
falls back to max(scored_at) over the PUB#/CORE# rows this job writes, which is DATA
RECENCY rather than liveness — a nightly that legitimately re-scores nothing new
reads as a dead producer. Two properties are pinned here: the key shape that board
queries, and that a dry run never fabricates the row.
"""
from __future__ import annotations

import time
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

import utils.db as db_mod
import utils.dynamodb_helpers as ddb

from pipeline_cores import ingest, persist, run


def _stub_a_scoring_run(monkeypatch, *, written=7):
    """Everything main() touches apart from the record write, stubbed at the module
    attribute each lazy import resolves through (the seams tests/test_cores_staff_count.py
    already uses). Returns the fake resource Table the record write should reach."""
    monkeypatch.setattr(db_mod, "get_engine", lambda: MagicMock(name="engine"))
    monkeypatch.setattr(ingest, "fetch_publications",
                        lambda e, pmids=None, limit=None: [])
    monkeypatch.setattr(ingest, "fetch_author_bylines", lambda e, p: {})
    monkeypatch.setattr(run, "run_core", lambda *a, **k: [])
    monkeypatch.setattr(persist, "put_core_staff_dict_counts", lambda *a, **k: True)
    monkeypatch.setattr(persist, "put_core_usage", lambda recs: written)

    table = MagicMock(name="reciterai")
    monkeypatch.setattr(ddb, "get_table", lambda *a, **k: table)
    return table


def test_a_real_run_writes_one_cores_run_liveness_row(monkeypatch):
    table = _stub_a_scoring_run(monkeypatch, written=7)

    run.main(["--core", "14"])

    assert table.put_item.call_count == 1
    item = table.put_item.call_args.kwargs["Item"]
    # The key SPS queries, both halves of it: the board only mirrors GLOBAL scope.
    assert item["PK"] == "STAGE#cores_run#GLOBAL"
    assert item["SK"].startswith("RUN#")
    assert item["status"] == "complete"
    # Whatever put_core_usage REPORTED writing, taken from its return value
    # rather than recomputed here — the ledger's job is to report the write that
    # happened, so the two must not be able to drift apart.
    assert item["records_written"] == 7
    assert isinstance(item["duration_ms"], int)
    # Decimal, not float: DynamoDB rejects floats, and this field is SUMmed.
    assert isinstance(item["cost_observed_usd"], Decimal)
    assert item["input_hash"]


def test_duration_spans_the_run_not_just_the_write(monkeypatch):
    """The near-miss this pins: a clock started just before put_item still yields an
    int >= 0, so `isinstance(int)` alone cannot tell a measurement of the RUN from a
    measurement of the row-write that closes it. Burn real time inside the scoring
    work and require the reported duration to contain it."""
    table = _stub_a_scoring_run(monkeypatch)

    def _slow_run_core(*a, **k):
        time.sleep(0.25)
        return []
    monkeypatch.setattr(run, "run_core", _slow_run_core)

    run.main(["--core", "14"])

    item = table.put_item.call_args.kwargs["Item"]
    assert item["duration_ms"] >= 250, item["duration_ms"]


def test_a_failed_record_write_does_not_fail_the_run(monkeypatch):
    """The scoring output is already committed by the time the liveness row is
    written, so a throttle on THAT write must not turn a good night into a failed ECS
    task and an alarm about a run that did its job. Untested, this guard is one
    refactor away from disappearing."""
    table = _stub_a_scoring_run(monkeypatch)
    table.put_item.side_effect = RuntimeError("ProvisionedThroughputExceeded")

    run.main(["--core", "14"])  # must return normally

    assert table.put_item.call_count == 1


def test_no_liveness_row_when_the_data_write_itself_failed(monkeypatch):
    """The row asserts "this run finished". A run that dies inside put_core_usage
    must leave none behind, or the board reads a failed night as a good one."""
    table = _stub_a_scoring_run(monkeypatch)
    monkeypatch.setattr(persist, "put_core_usage",
                        MagicMock(side_effect=RuntimeError("write failed")))

    with pytest.raises(RuntimeError):
        run.main(["--core", "14"])

    assert table.put_item.call_count == 0


def test_dry_run_writes_no_liveness_row(monkeypatch):
    """--dry-run means no DynamoDB writes at all, and this is the one write whose
    presence would be worse than its absence: it would tell the board the nightly ran
    and persisted on a run that persisted nothing."""
    table = _stub_a_scoring_run(monkeypatch)

    def _boom(*a, **k):
        raise AssertionError("put_core_usage must not be called under --dry-run")
    monkeypatch.setattr(persist, "put_core_usage", _boom)

    run.main(["--core", "14", "--dry-run"])

    assert table.put_item.call_count == 0

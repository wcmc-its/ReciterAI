"""The `STAGE#cores_run#GLOBAL` liveness row `pipeline_cores.run.main()` writes.

SPS's /edit/etl-status board grades a producer on its STAGE# row. Without one it
falls back to max(scored_at) over the PUB#/CORE# rows this job writes, which is DATA
RECENCY rather than liveness — a nightly that legitimately re-scores nothing new
reads as a dead producer. Two properties are pinned here: the key shape that board
queries, and that a dry run never fabricates the row.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

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
    # What put_core_usage actually persisted, not len(records) — the two differ
    # whenever a row is guarded, and the ledger has to report the write.
    assert item["records_written"] == 7
    assert isinstance(item["duration_ms"], int) and item["duration_ms"] >= 0
    # Decimal, not float: DynamoDB rejects floats, and this field is SUMmed.
    assert isinstance(item["cost_observed_usd"], Decimal)
    assert item["input_hash"]


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

"""Tests for scripts/purge_retired_core_items.py (cores 9 and 12, retired 2026-10-29).

A fake low-level client stands in for DynamoDB; it evaluates just enough of the Scan
filter (exact SK membership + PUB# prefix) and the partition Query to prove the script
finds both item shapes, never touches a live core, and writes nothing without --apply.
"""
from __future__ import annotations

import pytest

from pipeline_cores.dictionary import load_cores
from scripts import purge_retired_core_items as P


class _FakeClient:
    def __init__(self, items, page=2):
        self.store = {(i["PK"], i["SK"]): i for i in items}
        self.deleted = []
        self.page = page

    @staticmethod
    def _ser(it):
        out = {"PK": {"S": it["PK"]}, "SK": {"S": it["SK"]}}
        if "status" in it:
            out["status"] = {"S": it["status"]}
        return out

    def _paged(self, rows, kwargs):
        start = kwargs.get("ExclusiveStartKey", 0)
        chunk = rows[start:start + self.page]
        resp = {"Items": [self._ser(r) for r in chunk]}
        if start + self.page < len(rows):
            resp["LastEvaluatedKey"] = start + self.page
        return resp

    def scan(self, **kw):
        eav = kw["ExpressionAttributeValues"]
        sks = {v["S"] for k, v in eav.items() if k.startswith(":k")}
        rows = [r for r in self.store.values()
                if r["SK"] in sks and r["PK"].startswith(eav[":pub"]["S"])]
        return self._paged(rows, kw)

    def query(self, **kw):
        pk = kw["ExpressionAttributeValues"][":pk"]["S"]
        return self._paged([r for r in self.store.values() if r["PK"] == pk], kw)

    def delete_item(self, TableName, Key):
        k = (Key["PK"]["S"], Key["SK"]["S"])
        self.deleted.append(k)
        self.store.pop(k, None)


_ITEMS = [
    {"PK": "PUB#1", "SK": "CORE#9", "status": "candidate"},
    {"PK": "PUB#2", "SK": "CORE#9", "status": "claimed"},
    {"PK": "PUB#3", "SK": "CORE#12", "status": "confirmed"},
    {"PK": "PUB#4", "SK": "CORE#12", "status": "below_threshold"},
    {"PK": "PUB#5", "SK": "CORE#12", "status": "rejected"},
    {"PK": "CORE#9", "SK": "STAFF_DICT"},
    {"PK": "CORE#12", "SK": "STAFF_DICT"},
    {"PK": "CORE#12", "SK": "CLIENTS"},
    # must survive: live cores, including the CORE#1 / CORE#1x prefix neighbours
    {"PK": "PUB#3", "SK": "CORE#1", "status": "confirmed"},
    {"PK": "PUB#6", "SK": "CORE#14", "status": "candidate"},
    {"PK": "CORE#14", "SK": "STAFF_DICT"},
    {"PK": "CORE#1", "SK": "STAFF_DICT"},
]
_RETIRED_KEYS = {(i["PK"], i["SK"]) for i in _ITEMS[:8]}


def test_retired_ids_are_not_in_the_dictionary():
    """The script's own constant must agree with the shipped dictionary."""
    live = {c.core_id for c in load_cores()}
    assert not set(P.RETIRED_CORE_IDS) & live
    P.refuse_live_cores(P.RETIRED_CORE_IDS, live)          # does not raise


def test_a_live_core_is_refused():
    with pytest.raises(SystemExit, match="still in config/core_dictionary.yaml"):
        P.refuse_live_cores(["9", "14"], [c.core_id for c in load_cores()])


def test_dry_run_counts_both_shapes_and_writes_nothing():
    db = _FakeClient(_ITEMS)
    rep = P.purge(db, "t", ("9", "12"), apply=False)
    assert db.deleted == []
    assert rep["usage_rows_total"] == 5
    assert rep["usage_rows"] == {"9": {"candidate": 1, "claimed": 1},
                                 "12": {"below_threshold": 1, "confirmed": 1, "rejected": 1}}
    assert rep["config_items"] == ["CORE#12/CLIENTS", "CORE#12/STAFF_DICT", "CORE#9/STAFF_DICT"]
    assert "deleted" not in rep


def test_apply_deletes_exactly_the_retired_items_and_is_idempotent():
    db = _FakeClient(_ITEMS)
    rep = P.purge(db, "t", ("9", "12"), apply=True)
    assert set(db.deleted) == _RETIRED_KEYS and rep["deleted"] == 8
    assert set(db.store) == {(i["PK"], i["SK"]) for i in _ITEMS[8:]}
    again = P.purge(db, "t", ("9", "12"), apply=True)
    assert again["deleted"] == 0


def test_one_core_only():
    db = _FakeClient(_ITEMS)
    P.purge(db, "t", ("9",), apply=True)
    assert set(db.deleted) == {("PUB#1", "CORE#9"), ("PUB#2", "CORE#9"), ("CORE#9", "STAFF_DICT")}


def test_main_defaults_to_dry_run(monkeypatch, capsys):
    db = _FakeClient(_ITEMS)
    import utils.dynamodb_helpers as h
    monkeypatch.setattr(h, "get_dynamo_client", lambda *a, **k: db)
    assert P.main([]) == 0
    assert db.deleted == []
    assert "[dry-run] would delete 5 usage row(s) and 3 config item(s)" in capsys.readouterr().err

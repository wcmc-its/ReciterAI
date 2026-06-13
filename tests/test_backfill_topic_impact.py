"""Tests for scripts/backfill_topic_impact_from_impact_rows.py core logic.

Uses a fake DDB client (no AWS) to pin the per-PMID backfill contract:
- a PMID whose IMPACT# row has no impact_score is skipped (un-enriched);
- TOPIC# rows missing impact_score are written from the IMPACT# source;
- TOPIC# rows that already have impact_score are never touched.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "backfill_topic_impact",
    Path(__file__).resolve().parents[1] / "scripts" / "backfill_topic_impact_from_impact_rows.py",
)
bt = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bt)


class FakeClient:
    """Minimal DDB stand-in: get_item (IMPACT#), query (PmidIndex), update_item."""

    def __init__(self, impact: dict, topic_rows: list[dict]):
        self._impact = impact          # {pmid: {"impact_score": "55", "justification": "j"} or {}}
        self._topic = topic_rows       # list of low-level TOPIC# items
        self.updates: list[dict] = []

    def get_item(self, *, TableName, Key, ProjectionExpression=None):
        pmid = Key["PK"]["S"].removeprefix(bt.IMPACT_PK_PREFIX)
        data = self._impact.get(pmid)
        if data is None:
            return {}
        item = {"PK": Key["PK"], "SK": Key["SK"]}
        item.update(data)
        return {"Item": item}

    def query(self, **kw):
        pmid = kw["ExpressionAttributeValues"][":p"]["S"]
        items = [r for r in self._topic if r.get("pmid", {}).get("S") == pmid]
        return {"Items": items}

    def update_item(self, *, TableName, Key, UpdateExpression, ConditionExpression, ExpressionAttributeValues):
        self.updates.append({"Key": Key, "expr": UpdateExpression, "vals": ExpressionAttributeValues})


def _trow(pmid, sk, impact=None):
    it = {"PK": {"S": "TOPIC#topic_x"}, "SK": {"S": sk}, "pmid": {"S": pmid}}
    if impact is not None:
        it["impact_score"] = {"N": str(impact)}
    return it


def test_writes_missing_rows_from_impact_source():
    c = FakeClient(
        impact={"111": {"impact_score": {"N": "55"}, "justification": {"S": "good"}}},
        topic_rows=[_trow("111", "a"), _trow("111", "b")],
    )
    res = bt.backfill_pmid(c, "111", apply=True)
    assert res["status"] == "ok"
    assert res["rows_missing"] == 2 and res["rows_written"] == 2
    assert len(c.updates) == 2
    # both impact_score and impact_justification propagated
    assert c.updates[0]["vals"][":s"] == {"N": "55"}
    assert c.updates[0]["vals"][":j"] == {"S": "good"}


def test_skips_rows_that_already_have_impact():
    c = FakeClient(
        impact={"222": {"impact_score": {"N": "40"}}},
        topic_rows=[_trow("222", "a", impact=40), _trow("222", "b")],
    )
    res = bt.backfill_pmid(c, "222", apply=True)
    assert res["rows_already"] == 1
    assert res["rows_missing"] == 1 and res["rows_written"] == 1
    assert len(c.updates) == 1  # only the missing row


def test_skips_pmid_with_no_impact_source():
    c = FakeClient(impact={"333": {}}, topic_rows=[_trow("333", "a")])
    res = bt.backfill_pmid(c, "333", apply=True)
    assert res["status"] == "no_impact_source"
    assert res["rows_written"] == 0
    assert c.updates == []


def test_dry_run_writes_nothing():
    c = FakeClient(
        impact={"444": {"impact_score": {"N": "70"}}},
        topic_rows=[_trow("444", "a"), _trow("444", "b")],
    )
    res = bt.backfill_pmid(c, "444", apply=False)
    assert res["rows_missing"] == 2 and res["rows_written"] == 0
    assert c.updates == []

"""Unit tests for pipeline_enrichment.ddb_writer (#37 step 3 PR 3.1)."""
from __future__ import annotations

import sys
import types
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from pipeline_enrichment import ddb_writer


# ---------------------------------------------------------------------------
# build_impact_item — shape contract
# ---------------------------------------------------------------------------

def _kwargs(**overrides):
    base = dict(
        pmid="42",
        impact_score=87,
        justification="cited by 3 papers",
        impact_model="gpt-5.1-2025-11-13",
        synopsis="A study of X in Y.",
        synopsis_model="gpt-5.1-2025-11-13",
        enriched_at="2026-05-14T12:34:56Z",
    )
    base.update(overrides)
    return base


def test_build_impact_item_pk_sk_format():
    item = ddb_writer.build_impact_item(**_kwargs(pmid="12345"))
    assert item["PK"] == "IMPACT#pmid_12345"
    assert item["SK"] == "SCORE"


def test_build_impact_item_carries_all_contract_attributes():
    item = ddb_writer.build_impact_item(**_kwargs())
    expected_keys = {
        "PK", "SK", "pmid", "impact_score", "justification", "model",
        "synopsis", "synopsis_model", "enriched_at",
    }
    assert set(item.keys()) == expected_keys


def test_build_impact_item_omits_hierarchy_version():
    """Dropped during 3.1 — daily_enrichment has no hierarchy concept."""
    item = ddb_writer.build_impact_item(**_kwargs())
    assert "hierarchy_version" not in item


def test_build_impact_item_pmid_coerced_to_string():
    """Caller may pass pmid as int from a DB row; the DDB attribute
    must always be a string for stable PK formatting."""
    item = ddb_writer.build_impact_item(**_kwargs(pmid=42))
    assert item["pmid"] == "42"
    assert item["PK"] == "IMPACT#pmid_42"


def test_build_impact_item_impact_score_is_int():
    """DDB Number type accepts int natively; no Decimal coercion needed
    because impact_score is whole 0–100."""
    item = ddb_writer.build_impact_item(**_kwargs(impact_score=99))
    assert item["impact_score"] == 99
    assert isinstance(item["impact_score"], int)


# ---------------------------------------------------------------------------
# write_impact_batch — uses Table.batch_writer context manager
# ---------------------------------------------------------------------------

def test_write_impact_batch_empty_list_is_noop():
    table = MagicMock()
    n = ddb_writer.write_impact_batch(table, [])
    assert n == 0
    table.batch_writer.assert_not_called()


def test_write_impact_batch_calls_batch_writer_once_per_item():
    table = MagicMock()
    items = [ddb_writer.build_impact_item(**_kwargs(pmid=str(i))) for i in range(3)]
    n = ddb_writer.write_impact_batch(table, items)
    assert n == 3
    # batch_writer() context entered once.
    table.batch_writer.assert_called_once()
    # put_item invoked once per item on the entered batch object.
    batch = table.batch_writer.return_value.__enter__.return_value
    assert batch.put_item.call_count == 3
    # Each put_item carried the corresponding item dict.
    written = [c.kwargs["Item"] for c in batch.put_item.call_args_list]
    assert written == items


def test_write_impact_batch_propagates_exceptions():
    """Caller (orchestrator) decides how to surface failure — writer
    just lets the exception bubble."""
    table = MagicMock()
    table.batch_writer.return_value.__enter__.side_effect = RuntimeError("boom")
    with pytest.raises(RuntimeError, match="boom"):
        ddb_writer.write_impact_batch(table, [{"PK": "x", "SK": "y"}])


# ---------------------------------------------------------------------------
# propagate_impact_to_topic_rows — #212 Part B back-propagation hook
# ---------------------------------------------------------------------------

def _install_fake_fetch(monkeypatch, rows_by_pmid):
    """Inject a fake compute_top_topic so the lazy
    `from compute_top_topic import fetch_activity_rows_for_pmid` inside
    propagate_impact_to_topic_rows resolves to a stub — no boto3, no GSI."""
    fake = types.ModuleType("compute_top_topic")

    def fetch_activity_rows_for_pmid(table, pmid):  # noqa: ARG001 — table unused in stub
        return rows_by_pmid.get(str(pmid), [])

    fake.fetch_activity_rows_for_pmid = fetch_activity_rows_for_pmid
    monkeypatch.setitem(sys.modules, "compute_top_topic", fake)


def _impact(pmid="111", score=55, justification="cited by 3"):
    d = {"pmid": pmid, "impact_score": score}
    if justification is not None:
        d["justification"] = justification
    return d


def _trow(sk="SCORE#0900#ACTIVITY#pmid_111#cwid_a", impact=None):
    # Table-resource shape: plain dict, Numbers as Decimal.
    r = {"PK": "TOPIC#cardio", "SK": sk}
    if impact is not None:
        r["impact_score"] = Decimal(str(impact))
    return r


def test_propagate_writes_missing_impact(monkeypatch):
    _install_fake_fetch(monkeypatch, {"111": [_trow("SCORE#a#cwid_a"), _trow("SCORE#b#cwid_b")]})
    table = MagicMock()
    n = ddb_writer.propagate_impact_to_topic_rows(table, [_impact("111", 55, "good")])
    assert n == 2
    assert table.update_item.call_count == 2
    kw = table.update_item.call_args_list[0].kwargs
    assert kw["ExpressionAttributeValues"][":s"] == 55
    assert kw["ExpressionAttributeValues"][":j"] == "good"
    assert "impact_justification" in kw["UpdateExpression"]
    assert "ConditionExpression" in kw  # overwrite-if-different guard


def test_propagate_skips_rows_already_at_value(monkeypatch):
    _install_fake_fetch(monkeypatch, {"222": [_trow("SCORE#a#cwid_a", impact=40), _trow("SCORE#b#cwid_b")]})
    table = MagicMock()
    n = ddb_writer.propagate_impact_to_topic_rows(table, [_impact("222", 40, "j")])
    assert n == 1                       # only the missing row written
    assert table.update_item.call_count == 1


def test_propagate_overwrites_stale_value(monkeypatch):
    """Annual --full rescore: a different value must overwrite, not be skipped."""
    _install_fake_fetch(monkeypatch, {"333": [_trow("SCORE#a#cwid_a", impact=10)]})
    table = MagicMock()
    n = ddb_writer.propagate_impact_to_topic_rows(table, [_impact("333", 88, "j")])
    assert n == 1
    assert table.update_item.call_args_list[0].kwargs["ExpressionAttributeValues"][":s"] == 88


def test_propagate_justification_omitted_when_blank(monkeypatch):
    _install_fake_fetch(monkeypatch, {"444": [_trow("SCORE#a#cwid_a")]})
    table = MagicMock()
    n = ddb_writer.propagate_impact_to_topic_rows(table, [_impact("444", 70, justification="")])
    assert n == 1
    kw = table.update_item.call_args_list[0].kwargs
    assert ":j" not in kw["ExpressionAttributeValues"]
    assert "impact_justification" not in kw["UpdateExpression"]


def test_propagate_empty_items_is_noop(monkeypatch):
    _install_fake_fetch(monkeypatch, {})
    table = MagicMock()
    assert ddb_writer.propagate_impact_to_topic_rows(table, []) == 0
    table.update_item.assert_not_called()


def test_propagate_idempotent_full_skip(monkeypatch):
    """All rows already correct -> zero writes (idempotent re-run)."""
    _install_fake_fetch(monkeypatch, {"555": [_trow("SCORE#a#cwid_a", impact=60), _trow("SCORE#b#cwid_b", impact=60)]})
    table = MagicMock()
    assert ddb_writer.propagate_impact_to_topic_rows(table, [_impact("555", 60, "j")]) == 0
    table.update_item.assert_not_called()

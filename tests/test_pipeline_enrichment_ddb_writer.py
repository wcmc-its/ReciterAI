"""Unit tests for pipeline_enrichment.ddb_writer (#37 step 3 PR 3.1)."""
from __future__ import annotations

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

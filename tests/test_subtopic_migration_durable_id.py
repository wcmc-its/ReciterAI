"""Brick D3 (#191): durable_id propagation onto TOPIC# activity rows.

Covers the ADDITIVE `primary_subtopic_durable_id` companion threaded through
`utils.dynamodb_subtopic_migration.update_activity_subtopics` and the bool-safe
`assign_subtopics._get_flag` gate reader.

Contract under test:
  - gate off / unresolved (durable id None) -> the UpdateExpression is
    character-for-character identical to the pre-D3 literal and ":pdid" never
    appears in ExpressionAttributeValues (the byte-stability proof);
  - gate on / resolved -> the SET expression gains the durable clause and the
    companion flows through as a plain string (no Decimal coercion).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import assign_subtopics
import utils.dynamodb_subtopic_migration as migration


# The pre-D3 literal — what the function must produce verbatim when no durable
# id is supplied. If this drifts, the gate-off write is no longer byte-stable.
_TODAY_SET_EXPR = (
    "SET subtopic_ids = :sids, "
    "primary_subtopic_id = :pid, "
    "subtopic_confidences = :confs, "
    "hierarchy_version = :hv"
)


def _patch_table(monkeypatch) -> MagicMock:
    """Replace migration.get_table so update_item is captured, not sent."""
    table = MagicMock()
    table.update_item.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}}
    monkeypatch.setattr(migration, "get_table", lambda *a, **k: table)
    return table


# ---------------------------------------------------------------------------
# update_activity_subtopics — gate-off byte-stability
# ---------------------------------------------------------------------------

def test_durable_id_none_is_byte_identical_to_today(monkeypatch):
    """No durable id -> SET expression equals the pre-D3 literal and ":pdid"
    is absent from ExpressionAttributeValues."""
    table = _patch_table(monkeypatch)

    migration.update_activity_subtopics(
        pk="TOPIC#aging_geroscience",
        sk="SCORE#0850#ACTIVITY#pmid_12345#cwid_abc",
        subtopic_ids=["sub_a", "sub_b"],
        primary_subtopic_id="sub_a",
        confidences={"sub_a": 0.9, "sub_b": 0.4},
        hierarchy_version="v2026-06-01",
    )

    _, kwargs = table.update_item.call_args
    assert kwargs["UpdateExpression"] == _TODAY_SET_EXPR
    assert ":pdid" not in kwargs["ExpressionAttributeValues"]


def test_durable_id_omitted_default_is_byte_identical(monkeypatch):
    """Calling WITHOUT the kwarg at all (existing callers) is byte-identical."""
    table = _patch_table(monkeypatch)

    migration.update_activity_subtopics(
        pk="TOPIC#t",
        sk="SCORE#0500#ACTIVITY#pmid_1#cwid_x",
        subtopic_ids=["sub_a"],
        primary_subtopic_id="sub_a",
        confidences={"sub_a": 0.7},
        hierarchy_version="v2026-06-01",
        primary_subtopic_durable_id=None,
    )

    _, kwargs = table.update_item.call_args
    assert kwargs["UpdateExpression"] == _TODAY_SET_EXPR
    assert ":pdid" not in kwargs["ExpressionAttributeValues"]


# ---------------------------------------------------------------------------
# update_activity_subtopics — gate-on append
# ---------------------------------------------------------------------------

def test_durable_id_present_appends_clause_and_value(monkeypatch):
    """Resolved durable id -> SET gains the durable clause and ":pdid" carries
    the plain string verbatim (no Decimal coercion)."""
    table = _patch_table(monkeypatch)

    migration.update_activity_subtopics(
        pk="TOPIC#t",
        sk="SCORE#0900#ACTIVITY#pmid_9#cwid_y",
        subtopic_ids=["sub_a", "sub_b"],
        primary_subtopic_id="sub_a",
        confidences={"sub_a": 0.9, "sub_b": 0.4},
        hierarchy_version="v2026-06-01",
        primary_subtopic_durable_id="SUBTOPIC_ID#abc123",
    )

    _, kwargs = table.update_item.call_args
    assert kwargs["UpdateExpression"] == (
        _TODAY_SET_EXPR + ", primary_subtopic_durable_id = :pdid"
    )
    values = kwargs["ExpressionAttributeValues"]
    assert values[":pdid"] == "SUBTOPIC_ID#abc123"
    # Plain string — not coerced to Decimal or wrapped.
    assert isinstance(values[":pdid"], str)


# ---------------------------------------------------------------------------
# assign_subtopics._get_flag — bool-safe gate reader
# ---------------------------------------------------------------------------

def test_get_flag_missing_key_returns_default(monkeypatch):
    """Missing key -> default (False), no KeyError (unlike _get_threshold)."""
    monkeypatch.setattr(assign_subtopics, "_CFG", {"score_floor": 0.3})
    assert assign_subtopics._get_flag("propagate_durable_ids") is False
    assert assign_subtopics._get_flag("propagate_durable_ids", default=True) is True


def test_get_flag_reflects_config_value(monkeypatch):
    """Present key -> coerced to bool."""
    monkeypatch.setattr(assign_subtopics, "_CFG", {"propagate_durable_ids": True})
    assert assign_subtopics._get_flag("propagate_durable_ids") is True

    monkeypatch.setattr(assign_subtopics, "_CFG", {"propagate_durable_ids": False})
    assert assign_subtopics._get_flag("propagate_durable_ids") is False

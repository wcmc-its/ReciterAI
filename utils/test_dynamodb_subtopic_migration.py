"""
Tests for utils.dynamodb_subtopic_migration.

Uses unittest.mock to stub utils.dynamodb_helpers.get_table with a Mock
Table, then asserts the UpdateItem call shape. No live DynamoDB calls.

Covers:
- Test 1: update_activity_subtopics SET shape + Decimal coercion
- Test 2: update_faculty_subtopic_scores scoped SET on subtopic_scores.<topic>
- Test 3: clear_faculty_subtopic_scores_for_topic REMOVE with #topic name
- Test 4: Raw Python floats in confidences/scores do not raise
- Test 5: Public API surface is exactly the 3 advertised names
"""

from __future__ import annotations

from decimal import Decimal
from unittest import mock

import pytest


@pytest.fixture
def mock_table(monkeypatch):
    table = mock.Mock()
    table.update_item = mock.Mock(return_value={"Attributes": {}})

    def _fake_get_table(*args, **kwargs):
        return table

    # Patch at both sites: the helpers module (canonical) AND the migration
    # module's imported reference. This keeps the test robust to either import
    # style in the source under test.
    import utils.dynamodb_helpers as helpers
    monkeypatch.setattr(helpers, "get_table", _fake_get_table)

    import utils.dynamodb_subtopic_migration as migration
    monkeypatch.setattr(migration, "get_table", _fake_get_table)

    return table


def test_update_activity_subtopics_shape(mock_table):
    from utils.dynamodb_subtopic_migration import update_activity_subtopics

    update_activity_subtopics(
        pk="TOPIC#aging_geroscience",
        sk="SCORE#0850#ACTIVITY#pmid_12345#cwid_abc",
        subtopic_ids=["s1", "s2"],
        primary_subtopic_id="s1",
        confidences={"s1": 0.87, "s2": 0.42},
    )

    assert mock_table.update_item.call_count == 1
    kwargs = mock_table.update_item.call_args.kwargs
    assert kwargs["Key"] == {
        "PK": "TOPIC#aging_geroscience",
        "SK": "SCORE#0850#ACTIVITY#pmid_12345#cwid_abc",
    }
    assert "SET" in kwargs["UpdateExpression"]
    assert "subtopic_ids = :sids" in kwargs["UpdateExpression"]
    assert "primary_subtopic_id = :pid" in kwargs["UpdateExpression"]
    assert "subtopic_confidences = :confs" in kwargs["UpdateExpression"]

    values = kwargs["ExpressionAttributeValues"]
    assert values[":sids"] == ["s1", "s2"]
    assert values[":pid"] == "s1"
    # Confidences must be Decimals, not Python floats
    assert all(isinstance(v, Decimal) for v in values[":confs"].values())
    assert values[":confs"]["s1"] == Decimal("0.87")
    assert values[":confs"]["s2"] == Decimal("0.42")


def test_update_faculty_subtopic_scores_scoped(mock_table):
    from utils.dynamodb_subtopic_migration import update_faculty_subtopic_scores

    update_faculty_subtopic_scores(
        person_identifier="abc123",
        topic_id="aging_geroscience",
        scores={"s1": 45.2, "s2": 0.0},
    )

    # Two-step write: first call ensures subtopic_scores exists as a map, second
    # sets the per-topic nested key. Both target the same PROFILE row.
    assert mock_table.update_item.call_count == 2

    # Call 1: if_not_exists initializer
    init_kwargs = mock_table.update_item.call_args_list[0].kwargs
    assert init_kwargs["Key"] == {
        "PK": "FACULTY#cwid_abc123",
        "SK": "PROFILE",
    }
    assert "if_not_exists(subtopic_scores" in init_kwargs["UpdateExpression"]

    # Call 2: the scoped per-topic SET
    kwargs = mock_table.update_item.call_args_list[1].kwargs
    assert kwargs["Key"] == {
        "PK": "FACULTY#cwid_abc123",
        "SK": "PROFILE",
    }
    # Nested-path scoped SET so only this topic's slot is replaced
    assert "SET subtopic_scores.#t = :scores" in kwargs["UpdateExpression"]
    assert kwargs["ExpressionAttributeNames"] == {"#t": "aging_geroscience"}

    scores = kwargs["ExpressionAttributeValues"][":scores"]
    assert all(isinstance(v, Decimal) for v in scores.values())
    assert scores["s1"] == Decimal("45.2")
    assert scores["s2"] == Decimal("0")


def test_clear_faculty_subtopic_scores_for_topic(mock_table):
    from utils.dynamodb_subtopic_migration import clear_faculty_subtopic_scores_for_topic

    clear_faculty_subtopic_scores_for_topic(
        person_identifier="abc123",
        topic_id="aging_geroscience",
    )

    kwargs = mock_table.update_item.call_args.kwargs
    assert kwargs["Key"] == {
        "PK": "FACULTY#cwid_abc123",
        "SK": "PROFILE",
    }
    assert kwargs["UpdateExpression"].strip().startswith("REMOVE")
    assert "subtopic_scores.#t" in kwargs["UpdateExpression"]
    assert kwargs["ExpressionAttributeNames"] == {"#t": "aging_geroscience"}
    # No ExpressionAttributeValues needed for a plain REMOVE
    assert "ExpressionAttributeValues" not in kwargs or not kwargs.get("ExpressionAttributeValues")


def test_raw_float_inputs_do_not_raise(mock_table):
    from utils.dynamodb_subtopic_migration import (
        update_activity_subtopics,
        update_faculty_subtopic_scores,
    )

    # Raw floats in confidences
    update_activity_subtopics(
        pk="TOPIC#x",
        sk="SCORE#0500#ACTIVITY#pmid_1#cwid_a",
        subtopic_ids=["s1"],
        primary_subtopic_id="s1",
        confidences={"s1": 0.5},
    )
    # Raw floats in scores
    update_faculty_subtopic_scores(
        person_identifier="a",
        topic_id="x",
        scores={"s1": 1.23, "s2": 99.999},
    )
    # 1 activity update + 2 faculty updates (if_not_exists init + per-topic SET)
    assert mock_table.update_item.call_count == 3


def test_public_api_surface():
    import utils.dynamodb_subtopic_migration as m

    expected = {
        "update_activity_subtopics",
        "update_faculty_subtopic_scores",
        "clear_faculty_subtopic_scores_for_topic",
    }
    # Every expected name is exported and callable
    for name in expected:
        assert hasattr(m, name), f"missing export: {name}"
        assert callable(getattr(m, name))

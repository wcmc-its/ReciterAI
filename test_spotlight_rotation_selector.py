"""Tests for spotlight/rotation_selector.py + spotlight/history_writer.py — Plan 06-03.

Behaviors per PLAN.md ``<behavior>`` block:
  rotation_selector (Task 1):
    1. selection_score cold-start (last_shown_at=None) returns full pool_score
    2. selection_score 1-week-ago decay multiplier ~= 0.080
    3. selection_score 12-weeks-ago decay multiplier ~= 0.632
    4. selection_score 39-weeks-ago decay multiplier ~= 0.961
    5. select_with_diversity: 10 entries / 10 distinct parents → 10 selections
    6. select_with_diversity: selection floor failure raises ValueError
    7. select_with_diversity: deterministic tiebreaker on tied sel_scores
    8. fetch_history: BatchGetItem chunked at 25 (30 IDs → 2 calls)
    9. fetch_history: returns dict keyed by subtopic_id with last_shown_at

  history_writer (Task 2):
   10. update_history calls update_item once per Selection
   11. UpdateExpression contains 'ADD shown_count :one' + SET clauses
   12. ExpressionAttributeValues carries :one, :now (S), :pid (S)
   13. :now timestamp matches ISO 8601 UTC Z regex (no microseconds)
   14. publish_id is NOT interpolated into UpdateExpression string

All tests use synthetic injected boto3 stubs; no AWS calls.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone

import pytest


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _iso_z(dt: datetime) -> str:
    """ISO 8601 UTC with Z suffix, second-precision."""
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _make_pool_entry(subtopic_id: str, parent_topic: str, pool_score: float):
    from spotlight.types import PoolEntry

    return PoolEntry(
        subtopic_id=subtopic_id,
        pool_score=pool_score,
        parent_topic=parent_topic,
        papers=(),
    )


# ---------------------------------------------------------------------------
# Stub DynamoDB client for fetch_history / update_history tests
# ---------------------------------------------------------------------------


class StubBatchGetClient:
    """Stub that records batch_get_item calls and returns canned items.

    items_by_pk: dict mapping ``SPOTLIGHT_HISTORY#{sid}`` → AttributeValue dict.
    Items absent from the map are silently dropped (cold-start behavior).
    """

    def __init__(self, items_by_pk=None, unprocessed_first_call=False):
        self.items_by_pk = items_by_pk or {}
        self.unprocessed_first_call = unprocessed_first_call
        self.calls = []  # list of RequestItems dicts

    def batch_get_item(self, RequestItems=None, **_):
        self.calls.append(RequestItems)
        # Determine which table the keys are for (only one table in scope)
        table = next(iter(RequestItems))
        keys = RequestItems[table]["Keys"]

        responses = []
        unprocessed_keys = {}
        if self.unprocessed_first_call and len(self.calls) == 1:
            # Simulate UnprocessedKeys on the first call
            unprocessed_keys = {table: {"Keys": keys}}
        else:
            for k in keys:
                pk = k["PK"]["S"]
                if pk in self.items_by_pk:
                    responses.append(self.items_by_pk[pk])

        result = {"Responses": {table: responses}}
        if unprocessed_keys:
            result["UnprocessedKeys"] = unprocessed_keys
        else:
            result["UnprocessedKeys"] = {}
        return result


class StubUpdateClient:
    """Stub that records update_item kwargs."""

    def __init__(self):
        self.update_calls = []  # list of kwargs dicts

    def update_item(self, **kwargs):
        self.update_calls.append(kwargs)
        return {}


# ---------------------------------------------------------------------------
# Task 1 tests — rotation_selector
# ---------------------------------------------------------------------------


def test_01_selection_score_cold_start_returns_full_pool_score():
    from spotlight.rotation_selector import selection_score

    assert selection_score(100.0, None) == 100.0
    # Different scores propagate
    assert selection_score(42.5, None) == 42.5


def test_02_selection_score_one_week_ago_decay():
    from spotlight.rotation_selector import selection_score

    one_week_ago = _iso_z(datetime.now(timezone.utc) - timedelta(weeks=1))
    score = selection_score(100.0, one_week_ago)
    # Expected ~= 100 * (1 - exp(-1/12)) = ~7.99
    expected = 100.0 * (1.0 - math.exp(-1.0 / 12.0))
    assert abs(score - expected) < 0.5
    assert abs(score - 7.99) < 0.5


def test_03_selection_score_twelve_weeks_ago_decay():
    from spotlight.rotation_selector import selection_score

    twelve_weeks_ago = _iso_z(datetime.now(timezone.utc) - timedelta(weeks=12))
    score = selection_score(100.0, twelve_weeks_ago)
    # Expected ~= 100 * (1 - exp(-12/12)) = 100 * (1 - 1/e) ~= 63.21
    expected = 100.0 * (1.0 - math.exp(-1.0))
    assert abs(score - expected) < 1.0
    assert abs(score - 63.21) < 1.0


def test_04_selection_score_thirty_nine_weeks_ago_decay():
    from spotlight.rotation_selector import selection_score

    thirty_nine_weeks_ago = _iso_z(
        datetime.now(timezone.utc) - timedelta(weeks=39)
    )
    score = selection_score(100.0, thirty_nine_weeks_ago)
    # Expected ~= 100 * (1 - exp(-39/12)) ~= 96.13
    expected = 100.0 * (1.0 - math.exp(-39.0 / 12.0))
    assert abs(score - expected) < 1.0
    assert abs(score - 96.13) < 1.0


def test_05_select_with_diversity_ten_distinct_parents_returns_ten():
    from spotlight.rotation_selector import select_with_diversity

    pool = [
        _make_pool_entry(f"parent_{i:03d}_001", f"parent_{i:03d}", 100.0 - i)
        for i in range(10)
    ]
    selected = select_with_diversity(pool, history={}, n=10)
    assert len(selected) == 10
    # All 10 distinct parent_topics represented
    parents = {s.entry.parent_topic for s in selected}
    assert len(parents) == 10
    # Cold-start ⇒ sel_score == pool_score
    for s in selected:
        assert s.sel_score == s.entry.pool_score
        assert s.last_shown_at is None


def test_06_select_with_diversity_selection_floor_failure_raises():
    from spotlight.rotation_selector import select_with_diversity

    # 5 parents, 3 subtopics each = 15 entries but only 5 distinct parents
    pool = []
    for p in range(5):
        for s in range(3):
            pool.append(
                _make_pool_entry(
                    f"parent_{p:03d}_{s:03d}",
                    f"parent_{p:03d}",
                    100.0 - (p * 3 + s),
                )
            )
    with pytest.raises(ValueError, match="selection floor"):
        select_with_diversity(pool, history={}, n=10)


def test_07_select_with_diversity_deterministic_tiebreaker_on_tied_scores():
    from spotlight.rotation_selector import select_with_diversity

    # All same score, different subtopic_ids; tiebreaker should pick the
    # lexicographically-smallest subtopic_id per parent.
    pool = [
        _make_pool_entry("parent_a_002", "parent_a", 50.0),
        _make_pool_entry("parent_a_001", "parent_a", 50.0),
        _make_pool_entry("parent_b_001", "parent_b", 50.0),
    ]
    selected = select_with_diversity(pool, history={}, n=2)
    assert len(selected) == 2
    # Among parent_a entries (tied), the lex-smallest subtopic_id wins.
    parent_a_pick = next(s for s in selected if s.entry.parent_topic == "parent_a")
    assert parent_a_pick.entry.subtopic_id == "parent_a_001"


def test_07b_select_with_diversity_does_not_mutate_input():
    from spotlight.rotation_selector import select_with_diversity

    pool = [
        _make_pool_entry(f"parent_{i:03d}_001", f"parent_{i:03d}", 100.0 - i)
        for i in range(10)
    ]
    snapshot = list(pool)
    select_with_diversity(pool, history={}, n=10)
    assert pool == snapshot


def test_08_fetch_history_chunks_at_25():
    from spotlight.rotation_selector import fetch_history

    client = StubBatchGetClient(items_by_pk={})
    ids = [f"sub_{i:03d}" for i in range(30)]
    result = fetch_history(client, ids)

    # 30 IDs split into batches of 25 + 5 → 2 BatchGetItem calls
    assert len(client.calls) == 2
    # First call: 25 keys
    first_table = next(iter(client.calls[0]))
    assert len(client.calls[0][first_table]["Keys"]) == 25
    # Second call: 5 keys
    second_table = next(iter(client.calls[1]))
    assert len(client.calls[1][second_table]["Keys"]) == 5
    # No matching items in stub → cold-start (empty / None entries)
    assert isinstance(result, dict)


def test_09_fetch_history_returns_dict_keyed_by_subtopic_id():
    from spotlight.rotation_selector import fetch_history

    items_by_pk = {
        "SPOTLIGHT_HISTORY#sub_a": {
            "PK": {"S": "SPOTLIGHT_HISTORY#sub_a"},
            "SK": {"S": "STATE"},
            "subtopic_id": {"S": "sub_a"},
            "last_shown_at": {"S": "2026-01-15T12:00:00Z"},
            "shown_count": {"N": "3"},
        },
        # sub_b absent → cold-start
        "SPOTLIGHT_HISTORY#sub_c": {
            "PK": {"S": "SPOTLIGHT_HISTORY#sub_c"},
            "SK": {"S": "STATE"},
            "subtopic_id": {"S": "sub_c"},
            "last_shown_at": {"S": "2026-04-01T08:30:00Z"},
            "shown_count": {"N": "1"},
        },
    }
    client = StubBatchGetClient(items_by_pk=items_by_pk)
    result = fetch_history(client, ["sub_a", "sub_b", "sub_c"])

    assert result.get("sub_a") == "2026-01-15T12:00:00Z"
    assert result.get("sub_c") == "2026-04-01T08:30:00Z"
    # sub_b: either absent from dict or value is None (caller treats both as cold-start)
    assert result.get("sub_b") is None


def test_09b_fetch_history_retries_unprocessed_keys():
    """T-06-03-03 mitigation: one retry on UnprocessedKeys."""
    from spotlight.rotation_selector import fetch_history

    items_by_pk = {
        "SPOTLIGHT_HISTORY#sub_a": {
            "PK": {"S": "SPOTLIGHT_HISTORY#sub_a"},
            "SK": {"S": "STATE"},
            "subtopic_id": {"S": "sub_a"},
            "last_shown_at": {"S": "2026-01-15T12:00:00Z"},
        },
    }
    client = StubBatchGetClient(
        items_by_pk=items_by_pk, unprocessed_first_call=True
    )
    result = fetch_history(client, ["sub_a"])
    # First call empty (UnprocessedKeys), second call returns the item.
    assert len(client.calls) >= 2
    assert result.get("sub_a") == "2026-01-15T12:00:00Z"


# ---------------------------------------------------------------------------
# Task 2 tests — history_writer
# ---------------------------------------------------------------------------


def test_10_update_history_calls_update_item_per_selection():
    from spotlight.history_writer import update_history
    from spotlight.rotation_selector import Selection

    selections = [
        Selection(
            entry=_make_pool_entry(f"sub_{i:03d}", f"parent_{i:03d}", 50.0),
            sel_score=42.0,
            last_shown_at=None,
        )
        for i in range(3)
    ]
    client = StubUpdateClient()
    update_history(client, selections, publish_id="v2026-05-14")
    assert len(client.update_calls) == 3
    pks = [c["Key"]["PK"]["S"] for c in client.update_calls]
    assert pks == [
        "SPOTLIGHT_HISTORY#sub_000",
        "SPOTLIGHT_HISTORY#sub_001",
        "SPOTLIGHT_HISTORY#sub_002",
    ]
    for call in client.update_calls:
        assert call["Key"]["SK"] == {"S": "STATE"}


def test_11_update_history_update_expression_clauses_present():
    from spotlight.history_writer import update_history
    from spotlight.rotation_selector import Selection

    selections = [
        Selection(
            entry=_make_pool_entry("sub_001", "parent_001", 80.0),
            sel_score=80.0,
            last_shown_at=None,
        )
    ]
    client = StubUpdateClient()
    update_history(client, selections, publish_id="v2026-05-14")
    expr = client.update_calls[0]["UpdateExpression"]
    assert "ADD shown_count :one" in expr
    assert "SET last_shown_at = :now" in expr
    assert "last_shown_publish_id = :pid" in expr


def test_12_update_history_expression_attribute_values():
    from spotlight.history_writer import update_history
    from spotlight.rotation_selector import Selection

    selections = [
        Selection(
            entry=_make_pool_entry("sub_001", "parent_001", 80.0),
            sel_score=80.0,
            last_shown_at=None,
        )
    ]
    client = StubUpdateClient()
    update_history(client, selections, publish_id="v2026-05-14")
    eav = client.update_calls[0]["ExpressionAttributeValues"]
    assert eav[":one"] == {"N": "1"}
    assert ":now" in eav and "S" in eav[":now"]
    assert eav[":pid"] == {"S": "v2026-05-14"}


def test_13_update_history_now_timestamp_iso8601_z_format():
    from spotlight.history_writer import update_history
    from spotlight.rotation_selector import Selection

    selections = [
        Selection(
            entry=_make_pool_entry("sub_001", "parent_001", 80.0),
            sel_score=80.0,
            last_shown_at=None,
        )
    ]
    client = StubUpdateClient()
    update_history(client, selections, publish_id="v2026-05-14")
    now_val = client.update_calls[0]["ExpressionAttributeValues"][":now"]["S"]
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", now_val), (
        f"bad ISO format: {now_val}"
    )


def test_14_update_history_no_publish_id_interpolation_into_expression():
    """T-06-03-01 mitigation: publish_id flows through ExpressionAttributeValues only."""
    from spotlight.history_writer import update_history
    from spotlight.rotation_selector import Selection

    # publish_id with characters that would be visible if interpolated
    suspect = "v2026-05-14--MARKER--"
    selections = [
        Selection(
            entry=_make_pool_entry("sub_001", "parent_001", 80.0),
            sel_score=80.0,
            last_shown_at=None,
        )
    ]
    client = StubUpdateClient()
    update_history(client, selections, publish_id=suspect)
    expr = client.update_calls[0]["UpdateExpression"]
    # The marker must NOT appear in the UpdateExpression string.
    assert "MARKER" not in expr
    assert suspect not in expr
    # But it MUST appear in ExpressionAttributeValues.
    eav = client.update_calls[0]["ExpressionAttributeValues"]
    assert eav[":pid"] == {"S": suspect}

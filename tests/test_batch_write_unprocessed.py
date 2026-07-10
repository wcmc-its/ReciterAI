"""Tests for batch_write's UnprocessedItems handling.

DynamoDB returns UnprocessedItems inside a *200* response, so boto3's retry
machinery never sees them. If batch_write swallows them, a partially-applied
chunk is reported as a full success — and callers like
score_publications._materialize_topic_rows (which deletes the old TOPIC# rows
*before* writing the new ones) then stamp a 'complete' checkpoint over silently
missing data.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from utils.dynamodb_helpers import BatchWriteError, batch_write

TABLE = "reciterai"


def _item(pk: str) -> dict:
    return {"PK": {"S": pk}, "SK": {"S": "GLOBAL"}}


def test_raises_when_items_remain_unprocessed_after_retries(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    client = MagicMock()
    # Every attempt hands one item back as unprocessed.
    stuck = {TABLE: [{"PutRequest": {"Item": _item("A")}}]}
    client.batch_write_item.return_value = {"UnprocessedItems": stuck}

    with pytest.raises(BatchWriteError, match="still unprocessed"):
        batch_write(client, TABLE, [_item("A"), _item("B")])


def test_succeeds_when_retry_eventually_drains(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    client = MagicMock()
    client.batch_write_item.side_effect = [
        {"UnprocessedItems": {TABLE: [{"PutRequest": {"Item": _item("A")}}]}},
        {"UnprocessedItems": {}},
    ]

    batch_write(client, TABLE, [_item("A")])

    assert client.batch_write_item.call_count == 2


def test_clean_write_makes_one_call_per_chunk():
    client = MagicMock()
    client.batch_write_item.return_value = {"UnprocessedItems": {}}

    batch_write(client, TABLE, [_item(str(i)) for i in range(30)])

    # 30 items -> chunks of 25 -> 2 calls, no retries.
    assert client.batch_write_item.call_count == 2

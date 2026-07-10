"""The spotlight dirty gate's `created_at` filter must match rows the producer writes.

`resolve_new_pmid_assignments` scopes to activity that landed since the last
publish with `created_at >= :since`. DynamoDB comparisons against a *missing*
attribute match nothing, so if the TOPIC# row builder omits `created_at`, the
gate sees zero new PMIDs on every run after the first and the monthly spotlight
silently skips forever.

This is a producer/consumer contract test: it drives the real builder's output
through a fake DynamoDB scan that enforces the gate's real FilterExpression.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from pipeline_spotlight import orchestrator as orch
from utils.topic_records import build_topic_rows_for_pmid

SINCE = "2026-07-01T00:00:00Z"


def _build_rows(pmid: str, created_at: str) -> list[dict]:
    return build_topic_rows_for_pmid(
        pmid=pmid,
        dense_scores={"aging_geroscience": {"score": 0.9, "rationale": "r"}},
        authors=[{"cwid": "abc1234", "position": "first"}],
        taxonomy_version="taxonomy_v2",
        min_score=0.3,
        created_at=created_at,
    )


def _deserialize(row: dict) -> dict:
    """DDB attribute-typed item -> the plain dict `table.scan` yields."""
    return {k: next(iter(v.values())) for k, v in row.items()}


def _fake_table(rows: list[dict]) -> MagicMock:
    """A table whose scan honors `created_at >= :since`, as DynamoDB would.

    A row missing `created_at` never matches the comparison — the exact
    semantics that made the real gate a no-op.
    """
    table = MagicMock()

    def scan(**kwargs):
        vals = kwargs.get("ExpressionAttributeValues", {})
        since = vals.get(":since")
        items = [_deserialize(r) for r in rows]
        if since is not None:
            items = [i for i in items if "created_at" in i and i["created_at"] >= since]
        return {"Items": items}

    table.scan.side_effect = scan
    return table


def test_producer_rows_are_visible_to_the_gate_since_filter():
    rows = _build_rows("111", created_at="2026-07-05T12:00:00Z")
    # The row must carry primary_subtopic_id for the gate to bucket it; the
    # assign stage adds it after scoring.
    rows[0]["primary_subtopic_id"] = {"S": "aging_epigenetic_clocks_biological_aging"}

    out = orch.resolve_new_pmid_assignments(_fake_table(rows), since_iso=SINCE)

    assert out == {"111": ["aging_epigenetic_clocks_biological_aging"]}


def test_rows_older_than_since_are_excluded():
    rows = _build_rows("222", created_at="2026-06-01T00:00:00Z")
    rows[0]["primary_subtopic_id"] = {"S": "sub_a"}

    out = orch.resolve_new_pmid_assignments(_fake_table(rows), since_iso=SINCE)

    assert out == {}


def test_builder_stamps_created_at_on_every_row():
    rows = build_topic_rows_for_pmid(
        pmid="333",
        dense_scores={"t1": {"score": 0.9}, "t2": {"score": 0.8}},
        authors=[{"cwid": "abc1234"}, {"cwid": "def5678"}],
        taxonomy_version="taxonomy_v2",
        min_score=0.3,
    )

    assert len(rows) == 4
    assert all(r["created_at"]["S"] for r in rows)
    # One build call -> one consistent stamp across the PMID's rows.
    assert len({r["created_at"]["S"] for r in rows}) == 1

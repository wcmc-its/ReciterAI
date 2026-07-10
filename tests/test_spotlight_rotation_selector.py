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

import logging
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


def _make_pool_entry(subtopic_id: str, parent_topic: str, pool_score: float, full_pmids=()):
    from spotlight.types import PoolEntry

    return PoolEntry(
        subtopic_id=subtopic_id,
        pool_score=pool_score,
        parent_topic=parent_topic,
        papers=(),
        full_pmids=frozenset(full_pmids),
    )


def test_overlap_gate_stops_equivalent_subtopics_co_featuring():
    """#164: three differently-parented subtopics that share most of their
    papers (the spaceflight/disparities case) must not all be featured.

    Drives the article-overlap signal through the real
    rotation_selector.select_with_diversity gate. Without the gate all three
    rank highest and get picked; with the overlap adjacency only one survives.
    """
    from spotlight.rotation_selector import select_with_diversity
    from pipeline_hierarchy.subtopic_dedup import overlap_adjacency, union_adjacency

    shared = set(range(1, 41))  # the same astronaut/spaceflight corpus
    pool = [
        _make_pool_entry("sf1", "genetics", 10.0, shared),
        _make_pool_entry("sf2", "systems_biology", 9.9, shared),
        _make_pool_entry("sf3", "single_cell", 9.8, shared),
        _make_pool_entry("d1", "cardio", 5.0, range(100, 111)),
        _make_pool_entry("d2", "neuro", 4.9, range(200, 211)),
        _make_pool_entry("d3", "renal", 4.8, range(300, 311)),
    ]
    history = {e.subtopic_id: None for e in pool}  # cold start

    # Without the gate: the three top-scored clones all get featured.
    no_gate = select_with_diversity(pool, history, n=3, near_clones={})
    assert {s.entry.subtopic_id for s in no_gate} == {"sf1", "sf2", "sf3"}

    # With the overlap gate: at most one of the clones survives.
    pmid_sets = {e.subtopic_id: set(e.full_pmids) for e in pool}
    adj = union_adjacency({}, overlap_adjacency(pmid_sets, article_overlap_min=0.4))
    gated = select_with_diversity(pool, history, n=3, near_clones=adj)
    sf_selected = {s.entry.subtopic_id for s in gated} & {"sf1", "sf2", "sf3"}
    assert len(sf_selected) == 1, f"expected 1 spaceflight clone, got {sf_selected}"
    assert len(gated) == 3  # backfilled with distinct subtopics


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


def test_select_up_to_target_returns_target_when_pool_is_rich():
    """#164: with >= SELECTION_TARGET distinct parents, the selector returns 25."""
    from spotlight.rotation_selector import SELECTION_TARGET, select_with_diversity

    assert SELECTION_TARGET == 25
    pool = [_make_pool_entry(f"s{i}", f"parent_{i}", float(100 - i)) for i in range(30)]
    selected = select_with_diversity(pool, history={}, n=SELECTION_TARGET)
    assert len(selected) == 25
    assert len({s.entry.parent_topic for s in selected}) == 25


def test_clean_over_count_does_not_pad_with_clones_above_floor():
    """#164: with n_floor < n, Pass 2 does NOT re-admit near-clones to hit the
    ceiling — a thin pool publishes the CLEAN set, not a padded count.

    8 clean subtopics (distinct parents) + 4 clones (each a near-clone of a
    clean one, on their own parents). Ceiling 25, floor 8: Pass 1 takes the 8
    clean, the 4 clones gate out, and since 8 >= floor, Pass 2 stays off.
    """
    from spotlight.rotation_selector import select_with_diversity

    clean = [_make_pool_entry(f"c{i}", f"pc{i}", float(100 - i)) for i in range(8)]
    clones = [_make_pool_entry(f"k{i}", f"pk{i}", float(90 - i)) for i in range(4)]
    near_clones = {f"c{i}": set() for i in range(8)}
    for i in range(4):
        near_clones[f"k{i}"] = {f"c{i}"}
        near_clones[f"c{i}"] = {f"k{i}"}

    selected = select_with_diversity(
        clean + clones, history={}, n=25, n_floor=8, near_clones=near_clones
    )
    sids = {s.entry.subtopic_id for s in selected}
    assert len(selected) == 8  # clean set only, NOT padded to 12
    assert sids == {f"c{i}" for i in range(8)}
    assert not (sids & {f"k{i}" for i in range(4)})  # no clones admitted


def test_safety_floor_still_forces_clones_when_below_it():
    """#164: below n_floor, Pass 2 still force-admits clones (the safety net)."""
    from spotlight.rotation_selector import select_with_diversity

    clean = [_make_pool_entry(f"c{i}", f"pc{i}", float(100 - i)) for i in range(5)]
    clones = [_make_pool_entry(f"k{i}", f"pk{i}", float(90 - i)) for i in range(4)]
    near_clones = {f"c{i}": set() for i in range(5)}
    for i in range(4):
        near_clones[f"k{i}"] = {f"c{i % 5}"}
        near_clones.setdefault(f"c{i % 5}", set()).add(f"k{i}")

    # Only 5 clean; floor is 8 -> Pass 2 admits 3 clones to reach 8.
    selected = select_with_diversity(
        clean + clones, history={}, n=25, n_floor=8, near_clones=near_clones
    )
    assert len(selected) == 8


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

    # Phase 11: PK shape is now SPOTLIGHT_HISTORY#{version}#{subtopic_id}
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


# ---------------------------------------------------------------------------
# Rotation-history PK tests — unversioned key
#
# History used to be keyed SPOTLIGHT_HISTORY#{publish_date}#{sid}, so each
# publish wrote a fresh partition and every read came back empty: decay never
# applied and the same subtopics were re-featured forever. The key carries no
# version segment now.
# ---------------------------------------------------------------------------


def test_fetch_history_builds_unversioned_pk():
    from spotlight.rotation_selector import fetch_history

    items_by_pk = {
        "SPOTLIGHT_HISTORY#sub_a": {
            "PK": {"S": "SPOTLIGHT_HISTORY#sub_a"},
            "SK": {"S": "STATE"},
            "last_shown_at": {"S": "2026-05-01T10:00:00Z"},
        },
        "SPOTLIGHT_HISTORY#sub_b": {
            "PK": {"S": "SPOTLIGHT_HISTORY#sub_b"},
            "SK": {"S": "STATE"},
            "last_shown_at": {"S": "2026-04-01T10:00:00Z"},
        },
    }
    client = StubBatchGetClient(items_by_pk=items_by_pk)
    result = fetch_history(client, ["sub_a", "sub_b"])

    assert len(client.calls) == 1
    from spotlight.rotation_selector import TABLE_NAME
    pks_sent = [k["PK"]["S"] for k in client.calls[0][TABLE_NAME]["Keys"]]
    assert pks_sent == ["SPOTLIGHT_HISTORY#sub_a", "SPOTLIGHT_HISTORY#sub_b"]

    assert result.get("sub_a") == "2026-05-01T10:00:00Z"
    assert result.get("sub_b") == "2026-04-01T10:00:00Z"


def test_history_written_then_read_back_round_trips():
    """The write and read halves must agree on the PK, or decay silently no-ops."""
    from spotlight.history_writer import update_history
    from spotlight.rotation_selector import Selection, fetch_history, TABLE_NAME

    selections = [
        Selection(entry=_make_pool_entry("sub_a", "parent_a", 80.0), sel_score=80.0, last_shown_at=None),
    ]
    writer = StubUpdateClient()
    update_history(writer, selections, publish_id="v2026-06-01")
    written_pk = writer.update_calls[0]["Key"]["PK"]["S"]
    assert written_pk == "SPOTLIGHT_HISTORY#sub_a"

    reader = StubBatchGetClient(items_by_pk={
        written_pk: {
            "PK": {"S": written_pk},
            "SK": {"S": "STATE"},
            "last_shown_at": {"S": "2026-06-01T10:00:00Z"},
        }
    })
    assert fetch_history(reader, ["sub_a"]).get("sub_a") == "2026-06-01T10:00:00Z"
    assert [k["PK"]["S"] for k in reader.calls[0][TABLE_NAME]["Keys"]] == [written_pk]


def test_ingest_responses_strips_prefix_to_get_subtopic_id():
    from spotlight.rotation_selector import _ingest_responses, TABLE_NAME

    resp = {
        "Responses": {
            TABLE_NAME: [
                {
                    "PK": {"S": "SPOTLIGHT_HISTORY#sub_a"},
                    "SK": {"S": "STATE"},
                    "last_shown_at": {"S": "2026-05-01T10:00:00Z"},
                },
                {
                    "PK": {"S": "SPOTLIGHT_HISTORY#aging_geroscience_cognition"},
                    "SK": {"S": "STATE"},
                    "last_shown_at": {"S": "2026-04-15T10:00:00Z"},
                },
                {"PK": {"S": "TOPIC#cardio"}, "SK": {"S": "STATE"}},  # ignored
            ]
        }
    }
    result: dict = {}
    _ingest_responses(resp, result)

    assert result == {
        "sub_a": "2026-05-01T10:00:00Z",
        "aging_geroscience_cognition": "2026-04-15T10:00:00Z",
    }


# ---------------------------------------------------------------------------
# #91 — near-clone gate tests (select_with_diversity near_clones param)
# ---------------------------------------------------------------------------


def test_91_near_clones_none_matches_legacy_parent_only():
    """near_clones=None (and omitted) → exactly the original parent gate."""
    from spotlight.rotation_selector import select_with_diversity

    pool = [
        _make_pool_entry(f"parent_{i:03d}_001", f"parent_{i:03d}", 100.0 - i)
        for i in range(10)
    ]
    omitted = select_with_diversity(pool, history={}, n=10)
    explicit = select_with_diversity(pool, history={}, n=10, near_clones=None)
    assert [s.entry.subtopic_id for s in omitted] == [
        s.entry.subtopic_id for s in explicit
    ]
    assert len(omitted) == 10


def test_91_three_mutual_near_clones_collapse_to_one_selection():
    """3 mutually near-clone, distinct-parent subtopics → only 1 is selected;
    the freed slots backfill from the rest of the pool."""
    from spotlight.rotation_selector import select_with_diversity

    # 3 near-clones (the top 3 sel_scores) + 9 distinct-parent fillers.
    clones = [
        _make_pool_entry("astro_a_001", "genetics", 100.0),
        _make_pool_entry("astro_b_001", "single_cell", 99.0),
        _make_pool_entry("astro_c_001", "systems_bio", 98.0),
    ]
    fillers = [
        _make_pool_entry(f"fill_{i:03d}_001", f"fill_parent_{i:03d}", 50.0 - i)
        for i in range(9)
    ]
    pool = clones + fillers
    near_clones = {
        "astro_a_001": {"astro_b_001", "astro_c_001"},
        "astro_b_001": {"astro_a_001", "astro_c_001"},
        "astro_c_001": {"astro_a_001", "astro_b_001"},
    }
    selected = select_with_diversity(
        pool, history={}, n=10, near_clones=near_clones
    )
    selected_sids = {s.entry.subtopic_id for s in selected}
    assert len(selected) == 10
    # Exactly one of the three near-clones made the cut...
    assert len(selected_sids & {"astro_a_001", "astro_b_001", "astro_c_001"}) == 1
    # ...and it is the highest-sel_score one.
    assert "astro_a_001" in selected_sids
    # The 9 fillers backfilled the slots the clone gate freed.
    for filler in fillers:
        assert filler.subtopic_id in selected_sids


def test_91_pairwise_chain_keeps_both_ends():
    """An A-B-C near-clone CHAIN (A~B, B~C, A NOT~ C): the pairwise gate keeps
    A and C — it is not transitive, so it does not over-suppress the way a
    clustering approach would."""
    from spotlight.rotation_selector import select_with_diversity

    pool = [
        _make_pool_entry("chain_a_001", "parent_a", 100.0),
        _make_pool_entry("chain_b_001", "parent_b", 90.0),
        _make_pool_entry("chain_c_001", "parent_c", 80.0),
    ]
    near_clones = {
        "chain_a_001": {"chain_b_001"},
        "chain_b_001": {"chain_a_001", "chain_c_001"},
        "chain_c_001": {"chain_b_001"},
    }
    selected = select_with_diversity(
        pool, history={}, n=2, near_clones=near_clones
    )
    selected_sids = {s.entry.subtopic_id for s in selected}
    # A picked first; B gated (clone of A); C picked (clone of B, but B is not
    # selected). A clustering approach would have merged all three.
    assert selected_sids == {"chain_a_001", "chain_c_001"}


def test_91_pass_2_fills_least_cloned_first(caplog):
    """When Pass 1 stalls, Pass 2 fills by fewest clone-overlap first — NOT by
    raw sel_score — and logs each forced near-clone admission at WARNING."""
    from spotlight.rotation_selector import select_with_diversity

    # nc_a is a near-clone of b/c/d/e, so once a is picked they are all gated
    # in Pass 1. nc_f has no clone. Pass 1 → [a, f]; Pass 2 must add 2 more.
    pool = [
        _make_pool_entry("nc_a_001", "parent_a", 100.0),
        _make_pool_entry("nc_b_001", "parent_b", 95.0),
        _make_pool_entry("nc_c_001", "parent_c", 90.0),
        _make_pool_entry("nc_d_001", "parent_d", 85.0),
        _make_pool_entry("nc_e_001", "parent_e", 80.0),
        _make_pool_entry("nc_f_001", "parent_f", 75.0),
    ]
    near_clones = {
        "nc_a_001": {"nc_b_001", "nc_c_001", "nc_d_001", "nc_e_001"},
        "nc_b_001": {"nc_a_001", "nc_c_001", "nc_d_001"},
        "nc_c_001": {"nc_a_001", "nc_b_001"},
        "nc_d_001": {"nc_a_001", "nc_b_001"},
        "nc_e_001": {"nc_a_001"},
        "nc_f_001": set(),
    }
    with caplog.at_level(logging.WARNING):
        selected = select_with_diversity(
            pool, history={}, n=4, near_clones=near_clones
        )
    picked = [s.entry.subtopic_id for s in selected]
    # Pass 1 → [a, f]. Pass 2 round 1: b/c/d/e each overlap {a} (count 1); the
    # tie breaks on sel_score → b. Pass 2 round 2: c and d now overlap {a, b}
    # (count 2) while e still overlaps only {a} (count 1) → e wins, even
    # though c and d out-score it.
    assert picked[:2] == ["nc_a_001", "nc_f_001"]
    assert set(picked) == {"nc_a_001", "nc_f_001", "nc_b_001", "nc_e_001"}
    # e beat the higher-scored c and d purely on lower clone overlap.
    assert "nc_c_001" not in picked
    assert "nc_d_001" not in picked
    # Each forced near-clone admission was logged.
    assert "Pass 2" in caplog.text


def test_91_pass_1_result_is_pairwise_clone_free():
    """When Pass 1 alone fills n, the selected set has no near-clone pair, and
    the in-function Pass-1 self-check assertion does not fire."""
    from spotlight.rotation_selector import select_with_diversity

    pool = [
        _make_pool_entry("dup_a_001", "parent_a", 100.0),
        _make_pool_entry("dup_b_001", "parent_b", 99.0),
        _make_pool_entry("solo_c_001", "parent_c", 98.0),
    ]
    near_clones = {
        "dup_a_001": {"dup_b_001"},
        "dup_b_001": {"dup_a_001"},
        "solo_c_001": set(),
    }
    selected = select_with_diversity(
        pool, history={}, n=2, near_clones=near_clones
    )
    selected_sids = {s.entry.subtopic_id for s in selected}
    # dup_a (higher score) + solo_c; dup_b is gated as a clone of dup_a.
    assert selected_sids == {"dup_a_001", "solo_c_001"}
    # No two selected subtopics are near-clones of each other.
    for sid in selected_sids:
        assert not (near_clones[sid] & selected_sids)

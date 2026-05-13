"""Tests for build_critic_reject_record + write_critic_reject in utils.event_records.

Phase 12 D-08 spec §9 producer: CRITIC_REJECT#{publish_id}#{subtopic_id}#{pmid_set_hash}

Covers:
- Record shape: required fields, PK four-segment format, SK, record_type, source_stage
- pmid_set_hash is stable and order-invariant (sorted before hashing per D-09)
- pmid_set_hash changes with different PMID sets
- PK changes with publish_id (separate publish cycles = separate rows per D-09)
- PK changes with subtopic_id
- author_cwids are sorted and deduplicated at the builder boundary
- pmid_set field carries the full sorted list (not just the hash)
- unknown reason_code path: round-trips raw_failed_constraint
- pre_llm_gate path: pre_llm_constraint field present
- PII boundary (Pattern H): no lede_text key or any key containing 'lede'
- Writer calls table.put_item exactly once
- Idempotent overwrite: same (publish_id, subtopic_id, pmid_set) → identical PK
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from utils.event_records import build_critic_reject_record, write_critic_reject


FIXED_AT = "2026-05-12T12:00:00Z"


# ---------------------------------------------------------------------------
# Record shape
# ---------------------------------------------------------------------------


def test_record_shape_required_fields():
    record = build_critic_reject_record(
        publish_id="2026-05-12-001",
        subtopic_id="aging_geroscience",
        pmids=["123", "456"],
        author_cwids=["abc1", "def2"],
        reason_code="active_verb",
        regen_count=3,
        reason="LLM said no",
        created_at=FIXED_AT,
    )
    # PK four-segment shape: CRITIC_REJECT#<publish_id>#<subtopic_id>#<hash>
    assert record["PK"].startswith("CRITIC_REJECT#2026-05-12-001#aging_geroscience#")
    assert record["PK"].count("#") == 3
    assert record["SK"] == "GLOBAL"
    assert record["record_type"] == "CRITIC_REJECT"
    assert record["source_stage"] == "spotlight.critic"
    assert record["reason_code"] == "active_verb"
    assert record["regen_count"] == 3
    assert record["author_cwids"] == ["abc1", "def2"]
    assert record["created_at"] == FIXED_AT


# ---------------------------------------------------------------------------
# pmid_set_hash stability / invariance
# ---------------------------------------------------------------------------


def test_pmid_set_hash_is_stable_and_order_invariant():
    """Same PMID set in different order must produce identical PK (D-09)."""
    r1 = build_critic_reject_record(
        publish_id="pub1",
        subtopic_id="s1",
        pmids=["a", "b", "c"],
        author_cwids=["x"],
        reason_code="active_verb",
        regen_count=1,
        created_at=FIXED_AT,
    )
    r2 = build_critic_reject_record(
        publish_id="pub1",
        subtopic_id="s1",
        pmids=["c", "b", "a"],
        author_cwids=["x"],
        reason_code="active_verb",
        regen_count=1,
        created_at=FIXED_AT,
    )
    assert r1["PK"] == r2["PK"]


def test_pmid_set_hash_changes_with_set():
    """Different PMID lists must produce different PK suffixes."""
    r1 = build_critic_reject_record(
        publish_id="pub1",
        subtopic_id="s1",
        pmids=["10", "20"],
        author_cwids=["x"],
        reason_code="active_verb",
        regen_count=1,
        created_at=FIXED_AT,
    )
    r2 = build_critic_reject_record(
        publish_id="pub1",
        subtopic_id="s1",
        pmids=["10", "30"],
        author_cwids=["x"],
        reason_code="active_verb",
        regen_count=1,
        created_at=FIXED_AT,
    )
    assert r1["PK"] != r2["PK"]


# ---------------------------------------------------------------------------
# PK varies with publish_id / subtopic_id (D-08 per-(publish, subtopic, pmid_set))
# ---------------------------------------------------------------------------


def test_pk_changes_with_publish_id():
    """Same subtopic/pmid_set but different publish_id → distinct PK (D-09)."""
    r1 = build_critic_reject_record(
        publish_id="2026-04-01",
        subtopic_id="s1",
        pmids=["1", "2"],
        author_cwids=["x"],
        reason_code="active_verb",
        regen_count=1,
        created_at=FIXED_AT,
    )
    r2 = build_critic_reject_record(
        publish_id="2026-05-01",
        subtopic_id="s1",
        pmids=["1", "2"],
        author_cwids=["x"],
        reason_code="active_verb",
        regen_count=1,
        created_at=FIXED_AT,
    )
    assert r1["PK"] != r2["PK"]


def test_pk_changes_with_subtopic_id():
    """Same publish_id/pmid_set but different subtopic_id → distinct PK."""
    r1 = build_critic_reject_record(
        publish_id="pub1",
        subtopic_id="aging",
        pmids=["1", "2"],
        author_cwids=["x"],
        reason_code="active_verb",
        regen_count=1,
        created_at=FIXED_AT,
    )
    r2 = build_critic_reject_record(
        publish_id="pub1",
        subtopic_id="cardio",
        pmids=["1", "2"],
        author_cwids=["x"],
        reason_code="active_verb",
        regen_count=1,
        created_at=FIXED_AT,
    )
    assert r1["PK"] != r2["PK"]


# ---------------------------------------------------------------------------
# author_cwids
# ---------------------------------------------------------------------------


def test_author_cwids_sorted_and_deduplicated():
    """author_cwids must be sorted and deduplicated at the builder boundary."""
    record = build_critic_reject_record(
        publish_id="pub1",
        subtopic_id="s1",
        pmids=["1"],
        author_cwids=["c", "a", "b", "a"],
        reason_code="active_verb",
        regen_count=1,
        created_at=FIXED_AT,
    )
    assert record["author_cwids"] == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# pmid_set field
# ---------------------------------------------------------------------------


def test_pmid_set_field_carries_full_list():
    """record['pmid_set'] must be the sorted full list, not just the hash."""
    record = build_critic_reject_record(
        publish_id="pub1",
        subtopic_id="s1",
        pmids=["30", "10", "20"],
        author_cwids=["x"],
        reason_code="active_verb",
        regen_count=1,
        created_at=FIXED_AT,
    )
    assert record["pmid_set"] == ["10", "20", "30"]


# ---------------------------------------------------------------------------
# Vocabulary drift (unknown reason_code)
# ---------------------------------------------------------------------------


def test_unknown_reason_code_path():
    """reason_code='unknown' + raw_failed_constraint round-trips the raw value."""
    record = build_critic_reject_record(
        publish_id="pub1",
        subtopic_id="s1",
        pmids=["1"],
        author_cwids=["x"],
        reason_code="unknown",
        raw_failed_constraint="something_new_from_llm",
        regen_count=1,
        created_at=FIXED_AT,
    )
    assert record["reason_code"] == "unknown"
    assert record["raw_failed_constraint"] == "something_new_from_llm"


# ---------------------------------------------------------------------------
# Pre-LLM gate path
# ---------------------------------------------------------------------------


def test_pre_llm_gate_path():
    """reason_code='pre_llm_gate' + pre_llm_constraint must both appear."""
    record = build_critic_reject_record(
        publish_id="pub1",
        subtopic_id="s1",
        pmids=["1"],
        author_cwids=["x"],
        reason_code="pre_llm_gate",
        pre_llm_constraint="active_verb",
        regen_count=1,
        created_at=FIXED_AT,
    )
    assert record["reason_code"] == "pre_llm_gate"
    assert record["pre_llm_constraint"] == "active_verb"


# ---------------------------------------------------------------------------
# PII boundary (Pattern H)
# ---------------------------------------------------------------------------


def test_no_lede_text_field():
    """CRITIC_REJECT# must carry no lede content (D-30 / Pattern H PII boundary)."""
    record = build_critic_reject_record(
        publish_id="pub1",
        subtopic_id="s1",
        pmids=["1"],
        author_cwids=["x"],
        reason_code="active_verb",
        regen_count=1,
        created_at=FIXED_AT,
    )
    assert "lede_text" not in record
    assert not any("lede" in k.lower() for k in record.keys())


# ---------------------------------------------------------------------------
# Writer behaviour
# ---------------------------------------------------------------------------


def test_writer_calls_put_item():
    """write_critic_reject must call table.put_item(Item=record) exactly once."""
    table = MagicMock()
    write_critic_reject(
        table,
        publish_id="pub1",
        subtopic_id="s1",
        pmids=["1"],
        author_cwids=["x"],
        reason_code="active_verb",
        regen_count=1,
        created_at=FIXED_AT,
    )
    assert table.put_item.call_count == 1
    call_kwargs = table.put_item.call_args[1]
    assert "Item" in call_kwargs
    item = call_kwargs["Item"]
    assert item["PK"].startswith("CRITIC_REJECT#pub1#s1#")


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_idempotent_overwrite_same_triplet():
    """Two writes with identical (publish_id, subtopic_id, pmid_set) → identical PK.

    DynamoDB put_item with the same PK overwrites in place; the producer
    relies on this for retry dedup (D-09).
    """
    kwargs = dict(
        publish_id="pub1",
        subtopic_id="s1",
        pmids=["1", "2"],
        author_cwids=["x"],
        reason_code="active_verb",
        regen_count=1,
        created_at=FIXED_AT,
    )
    r1 = build_critic_reject_record(**kwargs)
    r2 = build_critic_reject_record(**kwargs)
    assert r1["PK"] == r2["PK"]

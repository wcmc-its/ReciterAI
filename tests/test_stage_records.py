"""Tests for utils.stage_records — Phase 9 task 2 substrate."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from utils import stage_records as sr


# ---------- compute_input_hash ----------


def test_compute_input_hash_is_deterministic():
    a = sr.compute_input_hash("publish_hierarchy", {"k": "v", "n": 1})
    b = sr.compute_input_hash("publish_hierarchy", {"k": "v", "n": 1})
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_compute_input_hash_ignores_key_order():
    a = sr.compute_input_hash("publish_hierarchy", {"alpha": 1, "beta": 2})
    b = sr.compute_input_hash("publish_hierarchy", {"beta": 2, "alpha": 1})
    assert a == b


def test_compute_input_hash_is_sensitive_to_value_changes():
    a = sr.compute_input_hash("publish_hierarchy", {"k": "v1"})
    b = sr.compute_input_hash("publish_hierarchy", {"k": "v2"})
    assert a != b


def test_compute_input_hash_is_namespaced_by_stage():
    """Two stages with structurally-identical inputs MUST NOT collide."""
    a = sr.compute_input_hash("stage_a", {"k": "v"})
    b = sr.compute_input_hash("stage_b", {"k": "v"})
    assert a != b


def test_compute_input_hash_handles_nested_structures():
    a = sr.compute_input_hash(
        "publish_hierarchy",
        {"models": ["haiku", "sonnet"], "config": {"x": 1}},
    )
    b = sr.compute_input_hash(
        "publish_hierarchy",
        {"models": ["haiku", "sonnet"], "config": {"x": 1}},
    )
    assert a == b


# ---------- should_skip / find_existing_complete ----------


def _make_mock_table(items_under_pk: list[dict]) -> MagicMock:
    """Mock boto3 Table whose `query` returns `items_under_pk` regardless of args."""
    t = MagicMock()
    t.query.return_value = {"Items": items_under_pk}
    return t


def test_should_skip_returns_true_on_matching_complete_row():
    prior = {
        "PK": "STAGE#publish_hierarchy#GLOBAL",
        "SK": "RUN#2026-05-10T12:00:00Z",
        "status": "complete",
        "input_hash": "abc123",
    }
    table = _make_mock_table([prior])
    skip, row = sr.should_skip(
        table, stage="publish_hierarchy", scope="GLOBAL", input_hash="abc123"
    )
    assert skip is True
    assert row is prior


def test_should_skip_returns_false_when_no_rows():
    table = _make_mock_table([])
    skip, row = sr.should_skip(
        table, stage="publish_hierarchy", scope="GLOBAL", input_hash="abc123"
    )
    assert skip is False
    assert row is None


def test_should_skip_ignores_failed_rows_even_with_matching_hash():
    """A failed prior run must NOT short-circuit a re-run."""
    prior_failed = {
        "PK": "STAGE#publish_hierarchy#GLOBAL",
        "SK": "RUN#2026-05-10T12:00:00Z",
        "status": "failed",
        "input_hash": "abc123",
    }
    table = _make_mock_table([prior_failed])
    skip, row = sr.should_skip(
        table, stage="publish_hierarchy", scope="GLOBAL", input_hash="abc123"
    )
    assert skip is False
    assert row is None


def test_should_skip_ignores_skipped_rows_even_with_matching_hash():
    """Skipped rows don't anchor future skips — only real completions do."""
    prior_skipped = {
        "PK": "STAGE#publish_hierarchy#GLOBAL",
        "SK": "RUN#2026-05-10T12:00:00Z",
        "status": "skipped",
        "input_hash": "abc123",
    }
    table = _make_mock_table([prior_skipped])
    skip, row = sr.should_skip(
        table, stage="publish_hierarchy", scope="GLOBAL", input_hash="abc123"
    )
    assert skip is False


def test_should_skip_filters_on_input_hash_when_pk_has_multiple_rows():
    """The PK query returns everything; the hash filter is what makes the skip safe."""
    other = {
        "PK": "STAGE#publish_hierarchy#GLOBAL",
        "SK": "RUN#2026-05-09T12:00:00Z",
        "status": "complete",
        "input_hash": "different_hash",
    }
    match = {
        "PK": "STAGE#publish_hierarchy#GLOBAL",
        "SK": "RUN#2026-05-10T12:00:00Z",
        "status": "complete",
        "input_hash": "target",
    }
    table = _make_mock_table([match, other])
    skip, row = sr.should_skip(
        table, stage="publish_hierarchy", scope="GLOBAL", input_hash="target"
    )
    assert skip is True
    assert row is match


def test_should_skip_query_uses_correct_pk():
    """Regression guard for the PK shape."""
    table = _make_mock_table([])
    sr.should_skip(
        table, stage="publish_hierarchy", scope="GLOBAL", input_hash="x"
    )
    kwargs = table.query.call_args.kwargs
    assert kwargs["ExpressionAttributeValues"][":pk"] == "STAGE#publish_hierarchy#GLOBAL"


# ---------- write helpers ----------


def test_write_complete_minimal_shape():
    table = MagicMock()
    item = sr.write_complete(
        table,
        stage="publish_hierarchy",
        scope="GLOBAL",
        input_hash="abc",
        started_at="2026-05-12T00:00:00Z",
        completed_at="2026-05-12T00:00:05Z",
        duration_ms=5000,
        cost_observed_usd=Decimal("0.01"),
    )
    assert item["PK"] == "STAGE#publish_hierarchy#GLOBAL"
    assert item["SK"] == "RUN#2026-05-12T00:00:00Z"
    assert item["status"] == "complete"
    assert item["input_hash"] == "abc"
    assert item["duration_ms"] == 5000
    assert item["cost_observed_usd"] == Decimal("0.01")
    # Optional fields should be absent when not passed.
    assert "output_pointer" not in item
    assert "records_written" not in item
    assert "force_reason" not in item
    table.put_item.assert_called_once_with(Item=item)


def test_write_complete_with_optional_fields():
    table = MagicMock()
    item = sr.write_complete(
        table,
        stage="publish_hierarchy",
        scope="GLOBAL",
        input_hash="abc",
        started_at="2026-05-12T00:00:00Z",
        duration_ms=5000,
        cost_observed_usd=Decimal("0.01"),
        output_pointer="s3://wcmc-reciterai-hierarchy/v2026-05-12/",
        records_written=1526,
        model_ids_snapshot=["haiku-4-5", "sonnet-4-6"],
        force_reason="emergency rollback override",
    )
    assert item["output_pointer"] == "s3://wcmc-reciterai-hierarchy/v2026-05-12/"
    assert item["records_written"] == 1526
    assert item["model_ids_snapshot"] == ["haiku-4-5", "sonnet-4-6"]
    assert item["force_reason"] == "emergency rollback override"


def test_write_skipped_pins_cost_to_skip_constant():
    """Phase 10 D-09: skipped rows MUST carry cost_observed_usd = 0
    (no work performed); skips are first-class zeros for aggregation."""
    table = MagicMock()
    item = sr.write_skipped(
        table,
        stage="publish_hierarchy",
        scope="GLOBAL",
        input_hash="abc",
        skip_reason="input_hash unchanged since 2026-05-10T12:00:00Z",
        started_at="2026-05-12T00:00:00Z",
        duration_ms=42,
    )
    assert item["status"] == "skipped"
    assert item["cost_observed_usd"] == sr.SKIP_COST_OBSERVED_USD
    assert item["cost_observed_usd"] == Decimal("0")
    assert item["skip_reason"] == "input_hash unchanged since 2026-05-10T12:00:00Z"
    assert item["duration_ms"] == 42
    table.put_item.assert_called_once_with(Item=item)


def test_write_skipped_emits_a_row_per_skip():
    """Spec §5 regression guard: skips MUST NOT be invisible — they emit rows."""
    table = MagicMock()
    sr.write_skipped(
        table,
        stage="publish_hierarchy",
        scope="GLOBAL",
        input_hash="abc",
        skip_reason="hash match",
        started_at="2026-05-12T00:00:00Z",
        duration_ms=10,
    )
    assert table.put_item.call_count == 1


def test_write_failed_includes_error_and_details():
    table = MagicMock()
    item = sr.write_failed(
        table,
        stage="publish_hierarchy",
        scope="GLOBAL",
        input_hash="abc",
        error_code="GATE_BLOCK_parent_prefix",
        error_message="3 subtopics violated parent-prefix gate",
        started_at="2026-05-12T00:00:00Z",
        duration_ms=1200,
        cost_observed_usd=Decimal("0"),
        failure_details={
            "violations": [
                {"topic_id": "microbiome_research", "subtopic_id": "x", "first_word": "microbiome"}
            ]
        },
    )
    assert item["status"] == "failed"
    assert item["error_code"] == "GATE_BLOCK_parent_prefix"
    assert "3 subtopics" in item["error_message"]
    assert item["failure_details"]["violations"][0]["first_word"] == "microbiome"


def test_round_trip_skip_after_complete():
    """End-to-end: complete row appears in query, should_skip returns True
    with a matching input_hash."""
    written_items = []

    def fake_put(Item):
        written_items.append(Item)

    def fake_query(**_kwargs):
        return {"Items": list(reversed(written_items))}

    table = MagicMock()
    table.put_item.side_effect = fake_put
    table.query.side_effect = fake_query

    sr.write_complete(
        table,
        stage="publish_hierarchy",
        scope="GLOBAL",
        input_hash="abc",
        started_at="2026-05-12T00:00:00Z",
        duration_ms=5000,
        cost_observed_usd=Decimal("0.01"),
    )

    skip, prior = sr.should_skip(
        table, stage="publish_hierarchy", scope="GLOBAL", input_hash="abc"
    )
    assert skip is True
    assert prior["status"] == "complete"

    # A different hash MUST NOT short-circuit.
    skip2, _ = sr.should_skip(
        table, stage="publish_hierarchy", scope="GLOBAL", input_hash="other"
    )
    assert skip2 is False


# ---------- enum / constant guards ----------


def test_skip_cost_is_pinned_to_zero():
    """Phase 10 D-09: SKIP_COST_OBSERVED_USD = 0 (no work performed on skip).
    DDB GetItem lookup cost is below noise floor and not modeled per-row."""
    assert sr.SKIP_COST_OBSERVED_USD == Decimal("0")


def test_status_enum_values_are_stable():
    """Strings, not IntEnum — written to DynamoDB and human-scanned."""
    assert sr.STATUS_COMPLETE == "complete"
    assert sr.STATUS_SKIPPED == "skipped"
    assert sr.STATUS_FAILED == "failed"


# ---------- Phase 10 (D-07): pure builders ----------


def test_build_complete_record_does_no_io():
    """Builder must be a pure function — no DynamoDB call, no implicit state."""
    item = sr.build_complete_record(
        stage="score_publications",
        scope="GLOBAL",
        input_hash="abc",
        started_at="2026-05-12T00:00:00Z",
        completed_at="2026-05-12T00:01:00Z",
        duration_ms=60_000,
        cost_observed_usd=Decimal("1.23"),
        output_pointer="ddb://reciterai-chatbot",
        records_written=10_000,
        model_ids_snapshot=["haiku-4-5", "sonnet-4-6"],
    )
    assert item["PK"] == "STAGE#score_publications#GLOBAL"
    assert item["SK"] == "RUN#2026-05-12T00:00:00Z"
    assert item["status"] == "complete"
    assert item["cost_observed_usd"] == Decimal("1.23")
    assert item["records_written"] == 10_000


def test_build_skipped_record_pins_cost_and_carries_skip_reason():
    item = sr.build_skipped_record(
        stage="assign_subtopics",
        scope="topic:aging_geroscience",
        input_hash="def",
        skip_reason="input_hash unchanged since 2026-05-10",
        started_at="2026-05-12T00:00:00Z",
        duration_ms=42,
    )
    assert item["status"] == "skipped"
    assert item["cost_observed_usd"] == sr.SKIP_COST_OBSERVED_USD
    assert item["skip_reason"] == "input_hash unchanged since 2026-05-10"
    assert item["PK"] == "STAGE#assign_subtopics#topic:aging_geroscience"


def test_build_failed_record_carries_error_payload():
    item = sr.build_failed_record(
        stage="score_publications",
        scope="GLOBAL",
        input_hash="ghi",
        error_code="BEDROCK_PARSE_FAIL",
        error_message="haiku returned malformed json",
        started_at="2026-05-12T00:00:00Z",
        duration_ms=15_000,
        cost_observed_usd=Decimal("0.42"),
        failure_details={"pmids_affected": [12345, 67890]},
    )
    assert item["status"] == "failed"
    assert item["error_code"] == "BEDROCK_PARSE_FAIL"
    assert item["failure_details"]["pmids_affected"] == [12345, 67890]


def test_write_complete_delegates_to_builder():
    """Phase 10 D-07: writer is build + put_item. Verify the equivalence."""
    table = MagicMock()
    written = sr.write_complete(
        table,
        stage="score_publications",
        scope="GLOBAL",
        input_hash="abc",
        started_at="2026-05-12T00:00:00Z",
        completed_at="2026-05-12T00:01:00Z",
        duration_ms=60_000,
        cost_observed_usd=Decimal("1.23"),
    )
    built = sr.build_complete_record(
        stage="score_publications",
        scope="GLOBAL",
        input_hash="abc",
        started_at="2026-05-12T00:00:00Z",
        completed_at="2026-05-12T00:01:00Z",
        duration_ms=60_000,
        cost_observed_usd=Decimal("1.23"),
    )
    assert written == built
    table.put_item.assert_called_once_with(Item=built)


# ---------- Phase 11 D-13: run_id substrate ----------


def test_build_complete_record_accepts_run_id():
    """D-13: build_complete_record accepts optional run_id kwarg and embeds it."""
    item = sr.build_complete_record(
        stage="assign_subtopics",
        scope="topic:aging_geroscience",
        input_hash="abc",
        started_at="2026-05-12T00:00:00Z",
        completed_at="2026-05-12T00:01:00Z",
        duration_ms=60_000,
        cost_observed_usd=Decimal("0"),
        run_id="abc-123",
    )
    assert item["run_id"] == "abc-123"


def test_build_complete_record_without_run_id_omits_field():
    """D-13: calling without run_id must not produce a run_id key (backwards-compat)."""
    item = sr.build_complete_record(
        stage="assign_subtopics",
        scope="topic:aging_geroscience",
        input_hash="abc",
        started_at="2026-05-12T00:00:00Z",
        completed_at="2026-05-12T00:01:00Z",
        duration_ms=60_000,
        cost_observed_usd=Decimal("0"),
    )
    assert "run_id" not in item, "run_id must be absent when not passed"


def test_build_skipped_record_accepts_run_id():
    """D-13: build_skipped_record accepts optional run_id."""
    item = sr.build_skipped_record(
        stage="assign_subtopics",
        scope="GLOBAL",
        input_hash="abc",
        skip_reason="unchanged",
        started_at="2026-05-12T00:00:00Z",
        duration_ms=42,
        run_id="abc-123",
    )
    assert item["run_id"] == "abc-123"


def test_build_skipped_record_without_run_id_omits_field():
    """D-13: calling without run_id must not produce a run_id key."""
    item = sr.build_skipped_record(
        stage="assign_subtopics",
        scope="GLOBAL",
        input_hash="abc",
        skip_reason="unchanged",
        started_at="2026-05-12T00:00:00Z",
        duration_ms=42,
    )
    assert "run_id" not in item


def test_build_failed_record_accepts_run_id():
    """D-13: build_failed_record accepts optional run_id."""
    item = sr.build_failed_record(
        stage="assign_subtopics",
        scope="GLOBAL",
        input_hash="abc",
        error_code="SOME_ERROR",
        error_message="something failed",
        started_at="2026-05-12T00:00:00Z",
        duration_ms=5_000,
        cost_observed_usd=Decimal("0"),
        run_id="abc-123",
    )
    assert item["run_id"] == "abc-123"


def test_build_failed_record_without_run_id_omits_field():
    """D-13: calling without run_id must not produce a run_id key."""
    item = sr.build_failed_record(
        stage="assign_subtopics",
        scope="GLOBAL",
        input_hash="abc",
        error_code="SOME_ERROR",
        error_message="something failed",
        started_at="2026-05-12T00:00:00Z",
        duration_ms=5_000,
        cost_observed_usd=Decimal("0"),
    )
    assert "run_id" not in item


def test_builders_are_idempotent():
    """Two identical builds produce equal dicts (modulo `completed_at` default)."""
    kwargs = dict(
        stage="score_publications",
        scope="GLOBAL",
        input_hash="abc",
        started_at="2026-05-12T00:00:00Z",
        completed_at="2026-05-12T00:01:00Z",
        duration_ms=60_000,
        cost_observed_usd=Decimal("1.23"),
    )
    a = sr.build_complete_record(**kwargs)
    b = sr.build_complete_record(**kwargs)
    assert a == b

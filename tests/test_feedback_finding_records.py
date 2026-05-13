"""Tests for pipeline_feedback.finding_records — Phase 12 §9 feedback consumer.

Covers:
- CANDIDATE_TOPIC# record shape + truncation fields
- RECLUSTER_RECOMMENDATION# record shape + evaluation_history
- SPOTLIGHT_DIAGNOSTIC# record shape keyed by subtopic_id per D-08
- D-08 underlying_rejects three-segment suffix shape (publish_id#subtopic_id#pmid_set_hash)
- D-32 underlying_rejects truncation (feedback_diagnostic_max_underlying cap)
- PII boundary: no author_cwids on diagnostic record; no lede_text
- Idempotency: same inputs → same PK
- Write helpers: each calls table.put_item exactly once
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock

import pytest

from pipeline_feedback.finding_records import (
    build_candidate_topic_record,
    build_recluster_recommendation_record,
    build_spotlight_diagnostic_record,
    write_candidate_topic,
    write_recluster_recommendation,
    write_spotlight_diagnostic,
)


# ---------------------------------------------------------------------------
# CANDIDATE_TOPIC# tests
# ---------------------------------------------------------------------------


def test_candidate_topic_record_shape():
    record = build_candidate_topic_record(
        slug="sars_cov2_long_term_effects",
        proposed_label="SARS-CoV-2 Long-Term Effects",
        source_pmids=["12345", "67890"],
        sonnet_rationale="Emerging research area with distinct publication cluster.",
        source_sweep_run_id="run-001",
        triggered_by="operator",
        created_at="2026-05-12T12:00:00Z",
    )
    assert record["PK"] == "CANDIDATE_TOPIC#sars_cov2_long_term_effects"
    assert record["SK"] == "GLOBAL"
    assert record["record_type"] == "CANDIDATE_TOPIC"
    assert record["source_stage"] == "feedback.sweep"
    assert record["source_sweep_run_id"] == "run-001"
    assert record["slug"] == "sars_cov2_long_term_effects"
    assert record["proposed_label"] == "SARS-CoV-2 Long-Term Effects"
    assert record["source_pmids"] == ["12345", "67890"]
    assert record["sonnet_rationale"] == "Emerging research area with distinct publication cluster."
    assert record["triggered_by"] == "operator"
    assert record["truncated"] is False
    assert record["created_at"] == "2026-05-12T12:00:00Z"


def test_candidate_topic_with_truncation():
    record = build_candidate_topic_record(
        slug="sars_cov2_long_term_effects",
        proposed_label="SARS-CoV-2 Long-Term Effects",
        source_pmids=["12345"],
        sonnet_rationale="Rationale.",
        source_sweep_run_id="run-001",
        triggered_by="operator",
        truncated=True,
        total_unprocessed_remaining=350,
        created_at="2026-05-12T12:00:00Z",
    )
    assert record["truncated"] is True
    assert record["total_unprocessed_remaining"] == 350


def test_candidate_topic_no_truncation_fields_when_not_truncated():
    record = build_candidate_topic_record(
        slug="some_slug",
        proposed_label="Some Label",
        source_pmids=[],
        sonnet_rationale="r",
        source_sweep_run_id="run-001",
        triggered_by="operator",
    )
    # total_unprocessed_remaining should not be present when truncated=False
    # and no total_unprocessed_remaining was passed
    assert record["truncated"] is False
    assert "total_unprocessed_remaining" not in record


# ---------------------------------------------------------------------------
# RECLUSTER_RECOMMENDATION# tests
# ---------------------------------------------------------------------------


def test_recluster_recommendation_shape():
    history = [
        {"date": "2026-05-06", "count": 55},
        {"date": "2026-05-07", "count": 62},
    ]
    record = build_recluster_recommendation_record(
        topic_id="aging_geroscience",
        evaluation_history=history,
        source_sweep_run_id="run-002",
        triggered_by="operator",
        created_at="2026-05-12T12:00:00Z",
    )
    assert record["PK"] == "RECLUSTER_RECOMMENDATION#aging_geroscience"
    assert record["SK"] == "GLOBAL"
    assert record["record_type"] == "RECLUSTER_RECOMMENDATION"
    assert record["topic_id"] == "aging_geroscience"
    assert record["source_sweep_run_id"] == "run-002"
    assert isinstance(record["evaluation_history"], list)
    assert len(record["evaluation_history"]) == 2
    assert record["evaluation_history"][0] == {"date": "2026-05-06", "count": 55}
    assert record["evaluation_history"][1] == {"date": "2026-05-07", "count": 62}
    assert record["source_stage"] == "feedback.sweep"
    assert record["created_at"] == "2026-05-12T12:00:00Z"


# ---------------------------------------------------------------------------
# SPOTLIGHT_DIAGNOSTIC# tests (D-08 re-keyed per-subtopic)
# ---------------------------------------------------------------------------


def test_spotlight_diagnostic_shape_per_subtopic():
    """D-08: SPOTLIGHT_DIAGNOSTIC# is keyed by subtopic_id, NOT cwid."""
    record = build_spotlight_diagnostic_record(
        subtopic_id="aging_geroscience",
        reason_code_distribution={"active_verb": 2, "institutional_voice": 1},
        distinct_pmid_set_count=3,
        underlying_rejects=[
            "pub1#aging_geroscience#abc1234567890def",
            "pub2#aging_geroscience#def1234567890abc",
        ],
        max_underlying=20,
        window_days=90,
        source_sweep_run_id="run-003",
        triggered_by="operator",
        created_at="2026-05-12T12:00:00Z",
    )
    assert record["PK"] == "SPOTLIGHT_DIAGNOSTIC#aging_geroscience"
    assert record["SK"] == "GLOBAL"
    assert record["record_type"] == "SPOTLIGHT_DIAGNOSTIC"
    assert record["subtopic_id"] == "aging_geroscience"
    assert record["source_stage"] == "feedback.sweep"
    assert record["source_sweep_run_id"] == "run-003"
    assert isinstance(record["reason_code_distribution"], dict)
    assert record["reason_code_distribution"]["active_verb"] == 2
    assert record["reason_code_distribution"]["institutional_voice"] == 1
    assert isinstance(record["distinct_pmid_set_count"], int)
    assert record["distinct_pmid_set_count"] == 3
    assert isinstance(record["underlying_rejects"], list)
    assert record["window_days"] == 90
    assert record["created_at"] == "2026-05-12T12:00:00Z"


def test_spotlight_diagnostic_underlying_rejects_suffix_shape():
    """D-08 PK shape: each entry in underlying_rejects must be {publish_id}#{subtopic_id}#{pmid_set_hash}.
    Three segments separated by '#'; third segment is 16 hex chars.
    """
    suffix_pattern = re.compile(r"^[^#]+#[^#]+#[a-f0-9]{16}$")
    record = build_spotlight_diagnostic_record(
        subtopic_id="aging_geroscience",
        reason_code_distribution={"active_verb": 1},
        distinct_pmid_set_count=2,
        underlying_rejects=[
            "pub1#aging_geroscience#abc1234567890de1",
            "pub2#aging_geroscience#def0987654321abc",
        ],
        max_underlying=20,
        window_days=90,
        source_sweep_run_id="run-003",
        triggered_by="operator",
    )
    for suffix in record["underlying_rejects"]:
        assert suffix_pattern.match(suffix), (
            f"Expected 3-segment pattern publish_id#subtopic_id#16hex_hash, got: {suffix!r}"
        )


def test_spotlight_diagnostic_underlying_rejects_truncation():
    """D-32: underlying_rejects capped at max_underlying; overflow sets truncated flags."""
    # Build 30 reject suffixes
    rejects = [f"pub{i}#sub1#{str(i).zfill(16)}" for i in range(30)]
    record = build_spotlight_diagnostic_record(
        subtopic_id="sub1",
        reason_code_distribution={"active_verb": 30},
        distinct_pmid_set_count=30,
        underlying_rejects=rejects,
        max_underlying=20,
        window_days=90,
        source_sweep_run_id="run-004",
        triggered_by="operator",
    )
    assert len(record["underlying_rejects"]) == 20
    assert record["underlying_rejects_truncated"] is True
    assert record["total_underlying"] == 30


def test_spotlight_diagnostic_no_truncation_flags_when_within_cap():
    """No truncation flags when underlying_rejects <= max_underlying."""
    rejects = [f"pub{i}#sub1#{str(i).zfill(16)}" for i in range(5)]
    record = build_spotlight_diagnostic_record(
        subtopic_id="sub1",
        reason_code_distribution={"active_verb": 5},
        distinct_pmid_set_count=5,
        underlying_rejects=rejects,
        max_underlying=20,
        window_days=90,
        source_sweep_run_id="run-005",
        triggered_by="operator",
    )
    assert len(record["underlying_rejects"]) == 5
    assert "underlying_rejects_truncated" not in record
    assert "total_underlying" not in record


def test_spotlight_diagnostic_does_not_carry_author_cwids():
    """D-08 + D-32: per-faculty drill-down lives on underlying CRITIC_REJECT# rows.
    The diagnostic row must NOT carry author_cwids.
    """
    record = build_spotlight_diagnostic_record(
        subtopic_id="sub1",
        reason_code_distribution={"active_verb": 1},
        distinct_pmid_set_count=1,
        underlying_rejects=["pub1#sub1#abc1234567890de1"],
        max_underlying=20,
        window_days=90,
        source_sweep_run_id="run-006",
        triggered_by="operator",
    )
    assert "author_cwids" not in record


def test_no_lede_text_in_diagnostic():
    """Pattern H PII boundary: no lede_text or 'lede'-keyed fields on diagnostic record."""
    record = build_spotlight_diagnostic_record(
        subtopic_id="sub1",
        reason_code_distribution={"active_verb": 1},
        distinct_pmid_set_count=1,
        underlying_rejects=["pub1#sub1#abc1234567890de1"],
        max_underlying=20,
        window_days=90,
        source_sweep_run_id="run-007",
        triggered_by="operator",
    )
    assert "lede_text" not in record
    for key in record:
        assert "lede" not in key.lower(), f"Unexpected lede-related key: {key!r}"


def test_spotlight_diagnostic_no_cwid_key():
    """D-08: no 'cwid' field on the diagnostic record (per-cwid keying fully replaced)."""
    record = build_spotlight_diagnostic_record(
        subtopic_id="sub1",
        reason_code_distribution={"active_verb": 1},
        distinct_pmid_set_count=1,
        underlying_rejects=["pub1#sub1#abc1234567890de1"],
        max_underlying=20,
        window_days=90,
        source_sweep_run_id="run-008",
        triggered_by="operator",
    )
    assert "cwid" not in record


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_idempotent_overwrite_same_pk_for_same_inputs():
    """Building twice with identical kwargs produces the same PK."""
    kwargs = dict(
        subtopic_id="sub1",
        reason_code_distribution={"active_verb": 1},
        distinct_pmid_set_count=1,
        underlying_rejects=["pub1#sub1#abc1234567890de1"],
        max_underlying=20,
        window_days=90,
        source_sweep_run_id="run-001",
        triggered_by="operator",
        created_at="2026-05-12T12:00:00Z",
    )
    r1 = build_spotlight_diagnostic_record(**kwargs)
    r2 = build_spotlight_diagnostic_record(**kwargs)
    assert r1["PK"] == r2["PK"]
    assert r1 == r2


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------


def test_writers_call_put_item():
    """Each write_* helper invokes table.put_item exactly once."""
    table = MagicMock()

    write_candidate_topic(
        table,
        slug="test_slug",
        proposed_label="Test Label",
        source_pmids=["123"],
        sonnet_rationale="rationale",
        source_sweep_run_id="r1",
        triggered_by="operator",
        created_at="2026-05-12T12:00:00Z",
    )
    assert table.put_item.call_count == 1
    table.reset_mock()

    write_recluster_recommendation(
        table,
        topic_id="topic_a",
        evaluation_history=[{"date": "2026-05-12", "count": 55}],
        source_sweep_run_id="r2",
        triggered_by="operator",
        created_at="2026-05-12T12:00:00Z",
    )
    assert table.put_item.call_count == 1
    table.reset_mock()

    write_spotlight_diagnostic(
        table,
        subtopic_id="sub1",
        reason_code_distribution={"active_verb": 1},
        distinct_pmid_set_count=1,
        underlying_rejects=["pub1#sub1#abc1234567890de1"],
        max_underlying=20,
        window_days=90,
        source_sweep_run_id="r3",
        triggered_by="operator",
        created_at="2026-05-12T12:00:00Z",
    )
    assert table.put_item.call_count == 1

"""Tests for pipeline_feedback.markdown_render — deterministic byte-identical rendering.

Phase 12 Task 3 — D-03 determinism + D-08 subtopic_id rendering.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Sample row factory helpers
# ---------------------------------------------------------------------------

def _candidate(slug: str = "test_topic") -> dict:
    return {
        "PK": f"CANDIDATE_TOPIC#{slug}",
        "SK": "GLOBAL",
        "record_type": "CANDIDATE_TOPIC",
        "slug": slug,
        "proposed_label": f"Label for {slug}",
        "source_pmids": ["11111", "22222"],
        "sonnet_rationale": "Strong biomedical signal.",
        "source_sweep_run_id": "run-abc",
        "triggered_by": "operator",
        "truncated": False,
        "created_at": "2026-01-01T00:00:00Z",
        "source_stage": "feedback.sweep",
    }


def _recluster(topic_id: str = "aging_geroscience") -> dict:
    return {
        "PK": f"RECLUSTER_RECOMMENDATION#{topic_id}",
        "SK": "GLOBAL",
        "record_type": "RECLUSTER_RECOMMENDATION",
        "topic_id": topic_id,
        "evaluation_history": [
            {"date": "2026-01-01", "count": 75},
            {"date": "2026-01-02", "count": 80},
        ],
        "source_sweep_run_id": "run-abc",
        "triggered_by": "operator",
        "created_at": "2026-01-01T00:00:00Z",
        "source_stage": "feedback.sweep",
    }


def _diagnostic(subtopic_id: str = "cardiology_basics") -> dict:
    return {
        "PK": f"SPOTLIGHT_DIAGNOSTIC#{subtopic_id}",
        "SK": "GLOBAL",
        "record_type": "SPOTLIGHT_DIAGNOSTIC",
        "subtopic_id": subtopic_id,
        "reason_code_distribution": {"active_verb": 3, "institutional_voice": 1},
        "distinct_pmid_set_count": 4,
        "underlying_rejects": [
            f"pub1#{subtopic_id}#abc123def456ab12",
            f"pub2#{subtopic_id}#def456abc123ef34",
        ],
        "window_days": 90,
        "source_sweep_run_id": "run-abc",
        "triggered_by": "operator",
        "created_at": "2026-01-01T00:00:00Z",
        "source_stage": "feedback.sweep",
    }


# ---------------------------------------------------------------------------
# test_render_byte_identical_two_runs  (D-03 determinism)
# ---------------------------------------------------------------------------

def test_render_byte_identical_two_runs():
    from pipeline_feedback.markdown_render import render_sweep_markdown

    rows = [_candidate(), _recluster(), _diagnostic()]
    run_id = "run-determinism-test"

    out1 = render_sweep_markdown(run_id, rows)
    out2 = render_sweep_markdown(run_id, rows)

    assert out1 == out2, "render_sweep_markdown must return identical bytes for identical inputs"
    assert isinstance(out1, bytes)


# ---------------------------------------------------------------------------
# test_render_no_generated_at_in_body  (G-29 lesson, D-03)
# ---------------------------------------------------------------------------

def test_render_no_generated_at_in_body():
    from pipeline_feedback.markdown_render import render_sweep_markdown

    rows = [_candidate()]
    out = render_sweep_markdown("run-xyz", rows)

    assert b"generated_at" not in out, (
        "rendered markdown must NOT contain 'generated_at' — timestamp belongs in filename (D-03)"
    )


# ---------------------------------------------------------------------------
# test_render_sorts_records_deterministically
# ---------------------------------------------------------------------------

def test_render_sorts_records_deterministically():
    from pipeline_feedback.markdown_render import render_sweep_markdown

    rows_a = [_candidate("zzz_topic"), _candidate("aaa_topic"), _recluster()]
    rows_b = [_recluster(), _candidate("aaa_topic"), _candidate("zzz_topic")]
    run_id = "run-sort-test"

    out_a = render_sweep_markdown(run_id, rows_a)
    out_b = render_sweep_markdown(run_id, rows_b)

    assert out_a == out_b, "sort order of input rows must not affect output bytes"


# ---------------------------------------------------------------------------
# test_render_groups_by_record_type
# ---------------------------------------------------------------------------

def test_render_groups_by_record_type():
    from pipeline_feedback.markdown_render import render_sweep_markdown

    rows = [_candidate(), _recluster(), _diagnostic()]
    out = render_sweep_markdown("run-sections", rows).decode("utf-8")

    assert "Candidate Topics" in out
    assert "Recluster Recommendations" in out
    assert "Spotlight Diagnostics" in out


# ---------------------------------------------------------------------------
# test_render_diagnostic_shows_subtopic_id  (D-08)
# ---------------------------------------------------------------------------

def test_render_diagnostic_shows_subtopic_id():
    from pipeline_feedback.markdown_render import render_sweep_markdown

    row = _diagnostic("cardiology_basics")
    out = render_sweep_markdown("run-d08-check", [row]).decode("utf-8")

    assert "subtopic_id" in out, "rendered diagnostic must surface 'subtopic_id' per D-08"
    assert "cwid" not in out, "rendered diagnostic must NOT contain 'cwid' (D-08 grain change)"


# ---------------------------------------------------------------------------
# test_render_empty_rows_returns_valid_markdown
# ---------------------------------------------------------------------------

def test_render_empty_rows_returns_valid_markdown():
    from pipeline_feedback.markdown_render import render_sweep_markdown

    out = render_sweep_markdown("run-empty", [])
    assert isinstance(out, bytes)
    assert len(out) > 0
    decoded = out.decode("utf-8")
    assert "Candidate Topics" in decoded
    assert "Recluster Recommendations" in decoded
    assert "Spotlight Diagnostics" in decoded

"""Tests for Phase 10 T4 — STAGE# wiring in assign_subtopics.

Covers:
- compute_assign_input_hash determinism, hierarchy-sensitivity, model-id-sensitivity
- envelope shape parity with build_complete_record / build_skipped_record
- per-PMID failed STAGE# rows on Bedrock parse failure with scope = pmid:{pmid}
- --delta-pmids parsing (inline list and @file)
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import assign_subtopics as ast_mod
from utils import stage_records as sr
from utils.bedrock_client import MODEL_IDS_BY_STAGE


HIERARCHY_DRAFT = {
    "topic_id": "cardiovascular_disease",
    "subtopics": [
        {"id": "atherosclerosis", "label": "Atherosclerosis", "description": "..."},
        {"id": "heart_failure", "label": "Heart Failure", "description": "..."},
    ],
    "review_status": "approved",
    "generated_at": "2026-05-01T10:00:00Z",
}


# ---------- input hash ----------


def test_assign_input_hash_is_deterministic():
    a = ast_mod.compute_assign_input_hash(
        hierarchy_draft=HIERARCHY_DRAFT, pmids=["1", "2"]
    )
    b = ast_mod.compute_assign_input_hash(
        hierarchy_draft=HIERARCHY_DRAFT, pmids=["2", "1"]
    )
    assert a == b
    assert len(a) == 64


def test_assign_input_hash_ignores_generated_at_and_review_status():
    """generated_at and review_status re-stamp on rerun without changing intent."""
    h1 = dict(HIERARCHY_DRAFT, generated_at="2026-05-01T10:00:00Z")
    h2 = dict(HIERARCHY_DRAFT, generated_at="2026-05-12T22:00:00Z",
              review_status="auto_approved")
    assert ast_mod.compute_assign_input_hash(hierarchy_draft=h1, pmids=["1"]) == \
        ast_mod.compute_assign_input_hash(hierarchy_draft=h2, pmids=["1"])


def test_assign_input_hash_changes_when_subtopics_change():
    h2 = json.loads(json.dumps(HIERARCHY_DRAFT))
    h2["subtopics"].append({"id": "arrhythmia", "label": "Arrhythmia",
                            "description": "..."})
    a = ast_mod.compute_assign_input_hash(hierarchy_draft=HIERARCHY_DRAFT, pmids=["1"])
    b = ast_mod.compute_assign_input_hash(hierarchy_draft=h2, pmids=["1"])
    assert a != b


def test_assign_input_hash_namespaced_to_stage():
    payload_hash = ast_mod.compute_assign_input_hash(
        hierarchy_draft=HIERARCHY_DRAFT, pmids=["1"]
    )
    other = sr.compute_input_hash(
        "score_publications",
        {
            "hierarchy_draft_sha256": ast_mod._hierarchy_canonical_sha256(HIERARCHY_DRAFT),
            "pmid_set_sha256": hashlib.sha256("1".encode()).hexdigest(),
            "model_ids": ast_mod.STAGE_MODEL_IDS,
        },
    )
    assert payload_hash != other


# ---------- envelope shape ----------


def test_envelope_complete_shape():
    input_hash = ast_mod.compute_assign_input_hash(
        hierarchy_draft=HIERARCHY_DRAFT, pmids=["1", "2"]
    )
    envelope = sr.build_complete_record(
        stage=ast_mod.STAGE_NAME,
        scope=ast_mod._topic_scope("cardiovascular_disease"),
        input_hash=input_hash,
        started_at="2026-05-12T12:00:00Z",
        completed_at="2026-05-12T12:00:10Z",
        duration_ms=10000,
        cost_observed_usd=ast_mod.ASSIGN_COST_USD,
        records_written=42,
        model_ids_snapshot=ast_mod.STAGE_MODEL_IDS,
    )
    assert envelope["PK"] == "STAGE#assign_subtopics#topic:cardiovascular_disease"
    assert envelope["SK"] == "RUN#2026-05-12T12:00:00Z"
    assert envelope["stage"] == ast_mod.STAGE_NAME
    assert envelope["scope"] == "topic:cardiovascular_disease"
    assert envelope["status"] == sr.STATUS_COMPLETE
    assert envelope["input_hash"] == input_hash
    assert envelope["cost_observed_usd"] == Decimal("0")
    assert envelope["records_written"] == 42
    assert envelope["model_ids_snapshot"] == [
        MODEL_IDS_BY_STAGE["subtopic_assignment"]
    ]


def test_envelope_skipped_carries_zero_cost_and_skip_reason():
    envelope = sr.build_skipped_record(
        stage=ast_mod.STAGE_NAME,
        scope=ast_mod._topic_scope("aging_geroscience"),
        input_hash="ab" * 32,
        skip_reason="input_hash unchanged since prior complete run at 2026-05-12T00:00:00Z",
        started_at="2026-05-12T12:00:00Z",
        completed_at="2026-05-12T12:00:00Z",
        duration_ms=5,
        model_ids_snapshot=ast_mod.STAGE_MODEL_IDS,
    )
    assert envelope["status"] == sr.STATUS_SKIPPED
    assert envelope["cost_observed_usd"] == Decimal("0")
    assert "skip_reason" in envelope


# ---------- per-PMID failed row ----------


def _make_args_for_process_pmid():
    return dict(
        pmid="12345",
        group={
            "activity": {"pmid": "12345", "title": "t", "synopsis": "s"},
            "rows": [],
            "has_primary": False,
        },
        client=MagicMock(),
        topic_meta={"id": "cardio", "label": "Cardio", "description": "..."},
        subtopic_defs=[{"id": "x", "label": "X", "description": "..."}],
        valid_subtopic_ids={"x"},
        hierarchy_draft=HIERARCHY_DRAFT,
        confidence_floor=0.3,
        dry_run=False,
    )


def test_per_pmid_failed_stage_row_written_on_classify_exception(monkeypatch):
    captured = []
    fake_stage_table = MagicMock()
    fake_stage_table.put_item.side_effect = lambda Item: captured.append(Item) or {}

    def boom(*args, **kwargs):
        raise RuntimeError("Bedrock returned non-JSON content")

    monkeypatch.setattr(ast_mod, "_classify_activity", boom)

    kwargs = _make_args_for_process_pmid()
    kwargs["stage_table"] = fake_stage_table

    result = ast_mod._process_pmid(**kwargs)

    assert result["status"] == "failed"
    pmid_rows = [
        item for item in captured
        if item.get("PK", "").startswith(f"STAGE#{ast_mod.STAGE_NAME}#pmid:")
    ]
    assert len(pmid_rows) == 1
    row = pmid_rows[0]
    assert row["PK"] == f"STAGE#{ast_mod.STAGE_NAME}#pmid:12345"
    assert row["scope"] == "pmid:12345"
    assert row["status"] == sr.STATUS_FAILED
    assert row["error_code"] == "RuntimeError"
    assert row["cost_observed_usd"] == ast_mod.ASSIGN_COST_USD
    assert row["model_ids_snapshot"] == ast_mod.STAGE_MODEL_IDS


def test_per_pmid_failed_row_skipped_when_no_stage_table(monkeypatch):
    """When stage_table is None (e.g., --dry-run), do not attempt STAGE# writes."""
    def boom(*args, **kwargs):
        raise RuntimeError("nope")

    monkeypatch.setattr(ast_mod, "_classify_activity", boom)
    kwargs = _make_args_for_process_pmid()
    kwargs["dry_run"] = True
    kwargs["stage_table"] = None
    result = ast_mod._process_pmid(**kwargs)
    assert result["status"] == "failed"


# ---------- --delta-pmids parser ----------


def test_parse_pmid_list_inline():
    assert ast_mod._parse_pmid_list_arg("1,2,3") == ["1", "2", "3"]
    assert ast_mod._parse_pmid_list_arg("1, 2 ,3") == ["1", "2", "3"]


def test_parse_pmid_list_none():
    assert ast_mod._parse_pmid_list_arg(None) is None


def test_parse_pmid_list_from_file(tmp_path: Path):
    f = tmp_path / "pmids.txt"
    f.write_text("# header comment\n1\n2\n\n3\n")
    assert ast_mod._parse_pmid_list_arg(f"@{f}") == ["1", "2", "3"]


def test_parse_pmid_list_missing_file_raises(tmp_path: Path):
    with pytest.raises(SystemExit):
        ast_mod._parse_pmid_list_arg(f"@{tmp_path}/does-not-exist.txt")


# ---------- --force bypasses the should_skip gate (#210 re-assignment) ----------


def _drive_run_with_skip_gate(monkeypatch, tmp_path, *, force):
    """Drive run() with a stage_table present and should_skip → (True, ...) over an
    EMPTY topic (no classify/update side-effects). Returns (result, should_skip_mock).

    Empty topic keeps it isolated: the only seams exercised are the STAGE# gate and
    the terminal complete/skipped write, all stubbed — no DynamoDB, no Bedrock.
    """
    monkeypatch.setenv("RECITERAI_HIERARCHY_VERSION", "v2026-06-01")
    monkeypatch.setattr(ast_mod, "get_table", lambda *a, **k: MagicMock())
    monkeypatch.setattr(ast_mod, "_get_flag", lambda *a, **k: False)  # no durable-id snapshot
    monkeypatch.setattr(ast_mod, "_query_topic_activity_rows", lambda tid: [])
    monkeypatch.setattr(ast_mod, "_dedupe_by_pmid", lambda rows: {})
    skip_mock = MagicMock(return_value=(True, {"started_at": "2026-01-01T00:00:00Z"}))
    monkeypatch.setattr(ast_mod, "should_skip", skip_mock)
    monkeypatch.setattr(ast_mod, "write_complete", lambda *a, **k: None)
    monkeypatch.setattr(ast_mod, "write_skipped", lambda *a, **k: None)

    draft = {
        "topic_id": "cardiovascular_disease",
        "subtopics": [{"id": "atherosclerosis", "label": "A", "description": "..."}],
        "review_status": "approved",
    }
    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps(draft))

    result = ast_mod.run(
        topic_id="cardiovascular_disease", draft_path=draft_path,
        concurrency=1, confidence_floor=0.3, limit=None, resume=False,
        dry_run=False, force=force,
    )
    return result, skip_mock


def test_force_bypasses_should_skip_gate(monkeypatch, tmp_path):
    """--force re-runs even when a prior complete STAGE# row would skip — the gate
    is bypassed, not even consulted."""
    result, skip_mock = _drive_run_with_skip_gate(monkeypatch, tmp_path, force=True)
    skip_mock.assert_not_called()
    assert result.get("stage_status") != "skipped"


def test_without_force_a_prior_complete_row_skips(monkeypatch, tmp_path):
    """Control: without --force, a should_skip verdict skips the re-run."""
    result, skip_mock = _drive_run_with_skip_gate(monkeypatch, tmp_path, force=False)
    skip_mock.assert_called_once()
    assert result.get("stage_status") == "skipped"


# ---------------------------------------------------------------------------
# Phase 11 Task 1 — Test 5: hierarchy_version propagated through run()
# ---------------------------------------------------------------------------


def test_p11_run_propagates_hierarchy_version_to_update_activity(monkeypatch):
    """Test 5: assign_subtopics.run() reads hierarchy_version from env and
    propagates it to update_activity_subtopics via mock assertion."""
    import os
    import assign_subtopics as ast_mod

    # Patch env to supply hierarchy_version
    monkeypatch.setenv("RECITERAI_HIERARCHY_VERSION", "v2026-06-01")

    captured_calls = []

    def fake_update_activity_subtopics(**kwargs):
        captured_calls.append(kwargs)
        return {}

    monkeypatch.setattr(ast_mod, "update_activity_subtopics", fake_update_activity_subtopics)

    # Minimal hierarchy with one subtopic
    hierarchy_draft = {
        "topic_id": "cardiovascular_disease",
        "subtopics": [{"id": "atherosclerosis", "label": "A", "description": "..."}],
        "review_status": "approved",
    }

    # Patch _classify_activity to return a confident assignment in the correct tuple format.
    # _classify_activity returns (raw_assignments_list, usage_dict).
    # raw_assignments_list items are {"subtopic_id": ..., "confidence": ...} dicts.
    def fake_classify(client, activity, topic_meta, subtopic_defs):
        return ([{"subtopic_id": "atherosclerosis", "confidence": 0.9}], {})

    monkeypatch.setattr(ast_mod, "_classify_activity", fake_classify)

    # Patch _query_topic_activity_rows to return one row with a PMID
    fake_row = {
        "PK": "TOPIC#cardiovascular_disease",
        "SK": "SCORE#0850#ACTIVITY#pmid_99#cwid_abc",
        "pmid": "99",
        "impact_score": "0.85",
    }
    monkeypatch.setattr(ast_mod, "_query_topic_activity_rows", lambda tid: [fake_row])

    # Patch _dedupe_by_pmid to return a pre-built grouped dict
    grouped = {
        "99": {
            "activity": {"pmid": "99", "title": "t", "synopsis": "s"},
            "rows": [fake_row],
            "has_primary": False,
        }
    }
    monkeypatch.setattr(ast_mod, "_dedupe_by_pmid", lambda rows: grouped)

    # Patch should_skip to never skip
    monkeypatch.setattr(ast_mod, "should_skip", lambda *a, **k: (False, {}))

    # Patch get_table to return None-like mock (dry_run=True skips stage writes)
    import tempfile, json as _json
    from pathlib import Path

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as f:
        _json.dump(hierarchy_draft, f)
        draft_path = Path(f.name)

    try:
        result = ast_mod.run(
            topic_id="cardiovascular_disease",
            draft_path=draft_path,
            concurrency=1,
            confidence_floor=0.3,
            limit=None,
            resume=False,
            dry_run=False,
        )
    finally:
        draft_path.unlink(missing_ok=True)

    # The update call must have been made with hierarchy_version
    assert len(captured_calls) >= 1, "update_activity_subtopics was never called"
    for call in captured_calls:
        assert call.get("hierarchy_version") == "v2026-06-01", (
            f"hierarchy_version missing or wrong: {call}"
        )


def test_p11_run_raises_if_hierarchy_version_env_absent(monkeypatch):
    """run() raises RuntimeError if RECITERAI_HIERARCHY_VERSION is not set."""
    import assign_subtopics as ast_mod

    monkeypatch.delenv("RECITERAI_HIERARCHY_VERSION", raising=False)

    import tempfile, json as _json
    from pathlib import Path

    hierarchy_draft = {
        "topic_id": "cardiovascular_disease",
        "subtopics": [{"id": "atherosclerosis", "label": "A", "description": "..."}],
        "review_status": "approved",
    }
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as f:
        _json.dump(hierarchy_draft, f)
        draft_path = Path(f.name)

    try:
        with pytest.raises(RuntimeError, match="RECITERAI_HIERARCHY_VERSION"):
            ast_mod.run(
                topic_id="cardiovascular_disease",
                draft_path=draft_path,
                concurrency=1,
                confidence_floor=0.3,
                limit=None,
                resume=False,
                dry_run=False,
            )
    finally:
        draft_path.unlink(missing_ok=True)

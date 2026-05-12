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

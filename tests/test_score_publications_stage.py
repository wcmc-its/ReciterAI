"""Tests for Phase 10 T3 — STAGE# wiring in score_publications.

Covers:
- compute_score_input_hash determinism + sensitivity
- envelope shape (build_complete_record / build_skipped_record) matches the
  substrate contract under emit-envelope mode
- per-PMID failed STAGE# rows are written on Bedrock parse failure with
  scope = pmid:{pmid}
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

import score_publications as sp
from utils import stage_records as sr
from utils.bedrock_client import MODEL_IDS_BY_STAGE


# ---------- compute_score_input_hash ----------


def test_score_input_hash_is_deterministic():
    a = sp.compute_score_input_hash(
        taxonomy_version="taxonomy_v2", pmids=["1", "2", "3"]
    )
    b = sp.compute_score_input_hash(
        taxonomy_version="taxonomy_v2", pmids=["1", "2", "3"]
    )
    assert a == b
    assert len(a) == 64


def test_score_input_hash_ignores_pmid_order_and_dupes():
    a = sp.compute_score_input_hash(
        taxonomy_version="taxonomy_v2", pmids=["3", "1", "2"]
    )
    b = sp.compute_score_input_hash(
        taxonomy_version="taxonomy_v2", pmids=["1", "2", "3", "1"]
    )
    assert a == b


def test_score_input_hash_changes_with_taxonomy_version():
    a = sp.compute_score_input_hash(taxonomy_version="v1", pmids=["1"])
    b = sp.compute_score_input_hash(taxonomy_version="v2", pmids=["1"])
    assert a != b


def test_score_input_hash_namespaced_to_score_publications_stage():
    """A hash from another stage with the same payload must not collide."""
    h_score = sp.compute_score_input_hash(
        taxonomy_version="taxonomy_v2", pmids=["1", "2"]
    )
    # Reconstruct the same canonical inputs against a different stage name.
    pmid_set_hash_inputs = sr.compute_input_hash(
        "publish_hierarchy",
        {
            "taxonomy_version": "taxonomy_v2",
            "pmid_set_sha256": __import__("hashlib")
            .sha256(",".join(sorted({"1", "2"})).encode("utf-8"))
            .hexdigest(),
            "model_ids": sp.STAGE_MODEL_IDS,
        },
    )
    assert h_score != pmid_set_hash_inputs


# ---------- envelope shape ----------


def test_envelope_complete_matches_build_complete_record_contract():
    input_hash = sp.compute_score_input_hash(
        taxonomy_version="taxonomy_v2", pmids=["1", "2"]
    )
    envelope = sr.build_complete_record(
        stage=sp.STAGE_NAME,
        scope=sp.STAGE_SCOPE_GLOBAL,
        input_hash=input_hash,
        started_at="2026-05-12T12:00:00Z",
        completed_at="2026-05-12T12:00:01Z",
        duration_ms=1000,
        cost_observed_usd=sp.SCORE_COST_USD,
        output_pointer="/tmp/scoring_results.json",
        records_written=2,
        model_ids_snapshot=sp.STAGE_MODEL_IDS,
    )
    assert envelope["PK"] == f"STAGE#{sp.STAGE_NAME}#{sp.STAGE_SCOPE_GLOBAL}"
    assert envelope["SK"] == "RUN#2026-05-12T12:00:00Z"
    assert envelope["stage"] == sp.STAGE_NAME
    assert envelope["scope"] == sp.STAGE_SCOPE_GLOBAL
    assert envelope["status"] == sr.STATUS_COMPLETE
    assert envelope["input_hash"] == input_hash
    assert envelope["cost_observed_usd"] == Decimal("0")
    assert envelope["records_written"] == 2
    assert envelope["model_ids_snapshot"] == [
        MODEL_IDS_BY_STAGE["screening"],
        MODEL_IDS_BY_STAGE["scoring"],
    ]
    # content_filter_count is additive — omitted when not supplied.
    assert "content_filter_count" not in envelope


def test_envelope_complete_carries_content_filter_count():
    """When supplied, content_filter_count rides on the complete record so a
    content-filter rate spike is visible on the score STAGE# row."""
    envelope = sr.build_complete_record(
        stage=sp.STAGE_NAME,
        scope=sp.STAGE_SCOPE_GLOBAL,
        input_hash="abc",
        started_at="2026-05-16T12:00:00Z",
        duration_ms=1000,
        cost_observed_usd=sp.SCORE_COST_USD,
        records_written=10,
        content_filter_count=3,
    )
    assert envelope["content_filter_count"] == 3


def test_envelope_skipped_carries_zero_cost_and_skip_reason():
    envelope = sr.build_skipped_record(
        stage=sp.STAGE_NAME,
        scope=sp.STAGE_SCOPE_GLOBAL,
        input_hash="deadbeef" * 8,
        skip_reason="input_hash unchanged since prior complete run at 2026-05-12T00:00:00Z",
        started_at="2026-05-12T12:00:00Z",
        completed_at="2026-05-12T12:00:00Z",
        duration_ms=15,
        model_ids_snapshot=sp.STAGE_MODEL_IDS,
    )
    assert envelope["status"] == sr.STATUS_SKIPPED
    assert envelope["cost_observed_usd"] == sr.SKIP_COST_OBSERVED_USD == Decimal("0")
    assert "skip_reason" in envelope
    assert envelope["model_ids_snapshot"] == sp.STAGE_MODEL_IDS


# ---------- per-PMID failed STAGE# rows ----------


class _FakeBedrockExploding:
    """Raises on every Bedrock call — simulates per-PMID Bedrock parse fail."""

    def call_json(self, *args, **kwargs):
        raise ValueError("Bedrock returned non-JSON: ...")


def test_per_pmid_failed_stage_row_written_on_bedrock_exception(monkeypatch):
    # Silence the legacy PROCESSING# mark_processing path — we are only
    # asserting against the new STAGE# substrate writes here.
    monkeypatch.setattr(sp, "mark_processing", lambda *a, **k: None)

    captured_puts = []

    fake_stage_table = MagicMock()
    fake_stage_table.put_item.side_effect = (
        lambda Item: captured_puts.append(Item) or {}
    )

    fake_dynamo_client = MagicMock()

    taxonomy = {
        "taxonomy_version": "taxonomy_v2",
        "topics": [{"id": "t1", "label": "T1", "description": "d1"}],
    }
    int_to_id = {"0": "t1"}
    id_to_int = {"t1": "0"}
    pub = {"pmid": "99999", "synopsis": "x", "abstract": "y"}

    result = sp.score_one_publication(
        pub,
        _FakeBedrockExploding(),
        taxonomy,
        fake_dynamo_client,
        "reciterai",
        int_to_id,
        id_to_int,
        stage_table=fake_stage_table,
    )

    assert result.status == "failed"
    # Exactly one STAGE# row written for this PMID, scoped to pmid:99999
    pmid_rows = [
        item for item in captured_puts
        if item.get("PK", "").startswith(f"STAGE#{sp.STAGE_NAME}#pmid:")
    ]
    assert len(pmid_rows) == 1
    row = pmid_rows[0]
    assert row["PK"] == f"STAGE#{sp.STAGE_NAME}#pmid:99999"
    assert row["status"] == sr.STATUS_FAILED
    assert row["scope"] == "pmid:99999"
    assert row["error_code"] == "ValueError"
    assert row["cost_observed_usd"] == sp.SCORE_COST_USD
    assert row["model_ids_snapshot"] == sp.STAGE_MODEL_IDS


def test_per_pmid_input_hash_is_pmid_sensitive():
    h1 = sp._per_pmid_input_hash("taxonomy_v2", "1")
    h2 = sp._per_pmid_input_hash("taxonomy_v2", "2")
    assert h1 != h2
    # Determinism
    assert h1 == sp._per_pmid_input_hash("taxonomy_v2", "1")


# ---------- #310: total failure must not write a `complete` terminal row -----


def _run_main_and_capture_terminal_record(monkeypatch, capsys, results):
    """Drive main() in --emit-envelope mode over a one-PMID --pmids --force run
    whose scoring produces `results`, and return the emitted GLOBAL STAGE#
    record dict. --emit-envelope prints the terminal record instead of writing
    it, and skips every file/DynamoDB side effect."""
    import asyncio
    import json as _json
    import sys

    monkeypatch.setattr(sys, "argv", [
        "score_publications.py", "--pmids", "99999", "--force", "--emit-envelope",
    ])
    monkeypatch.setattr(sp, "get_table", lambda *a, **k: MagicMock())
    monkeypatch.setattr(sp, "load_thresholds", lambda: {})
    monkeypatch.setattr(sp, "BedrockClient", lambda *a, **k: MagicMock())
    monkeypatch.setattr(sp, "get_dynamo_client", lambda: MagicMock())
    monkeypatch.setattr(
        sp, "extract_publications_by_pmids",
        lambda pmids: [{"pmid": "99999", "title": "t",
                        "abstract": "a", "synopsis": "s"}],
    )
    monkeypatch.setattr(sp, "scan_invalid_pmids", lambda *a, **k: [])
    monkeypatch.setattr(sp, "extract_author_mapping", lambda: {})
    monkeypatch.setattr(sp, "extract_faculty_metadata", lambda: {})

    async def fake_score_batch(*a, **k):
        return results

    monkeypatch.setattr(sp, "score_batch_async", fake_score_batch)

    asyncio.run(sp.main())

    target_pk = f"STAGE#{sp.STAGE_NAME}#{sp.STAGE_SCOPE_GLOBAL}"
    for line in capsys.readouterr().out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            rec = _json.loads(line)
        except ValueError:
            continue
        if rec.get("PK") == target_pk:
            return rec
    return None


def test_total_failure_emits_failed_terminal_record_not_complete(monkeypatch, capsys):
    """#310: a --pmids run where every PMID fails must emit a `failed` terminal
    STAGE# record, not a `complete` one. A `complete` row (records_written=0)
    collides on input_hash with the re-run and makes the outage permanent."""
    results = [sp.ScoringResult(pmid="99999", synopsis="s", abstract="a",
                                status="failed")]
    rec = _run_main_and_capture_terminal_record(monkeypatch, capsys, results)
    assert rec is not None
    assert rec["status"] == sr.STATUS_FAILED


def test_partial_success_still_emits_complete_terminal_record(monkeypatch, capsys):
    """Control: at least one scored PMID keeps the terminal record `complete`."""
    results = [
        sp.ScoringResult(pmid="99999", synopsis="s", abstract="a",
                         status="complete", dense_scores={"t1": {"score": 0.9}}),
        sp.ScoringResult(pmid="88888", synopsis="s", abstract="a",
                         status="failed"),
    ]
    rec = _run_main_and_capture_terminal_record(monkeypatch, capsys, results)
    assert rec is not None
    assert rec["status"] == sr.STATUS_COMPLETE

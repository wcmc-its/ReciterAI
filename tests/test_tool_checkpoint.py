"""Unit tests for pipeline_tools.checkpoint (resumable A2 extraction log)."""
from __future__ import annotations

from decimal import Decimal

from pipeline_tools.checkpoint import ExtractionCheckpoint
from utils.bedrock_client import HAIKU_MODEL


def _mentions(pmid: str, *names: str) -> list[dict]:
    return [{"raw_name": n, "pmid": pmid, "context": None} for n in names]


def test_record_then_resume_sees_done_and_mentions(tmp_path):
    path = tmp_path / "ckpt.jsonl"
    cp = ExtractionCheckpoint.load(path)
    assert len(cp) == 0
    cp.record("100", _mentions("100", "scRNA-seq", "10x Chromium"),
              model=HAIKU_MODEL, input_tokens=1200, output_tokens=300)
    cp.record("200", _mentions("200", "patch-clamp"),
              model=HAIKU_MODEL, input_tokens=900, output_tokens=120)

    # A fresh load (simulating a resume) replays the on-disk log.
    resumed = ExtractionCheckpoint.load(path)
    assert len(resumed) == 2
    assert resumed.is_done("100") and resumed.is_done("200")
    assert not resumed.is_done("300")
    assert resumed.done_pmids() == {"100", "200"}
    # all_mentions flattens in stable PMID order.
    names = [m["raw_name"] for m in resumed.all_mentions()]
    assert names == ["scRNA-seq", "10x Chromium", "patch-clamp"]


def test_prior_cost_reconstructs_for_ceiling_seed(tmp_path):
    path = tmp_path / "ckpt.jsonl"
    cp = ExtractionCheckpoint.load(path)
    # Two PMIDs, each a $1.00-equivalent Haiku call (1M input tokens).
    cp.record("100", _mentions("100", "A"), model=HAIKU_MODEL, input_tokens=1_000_000, output_tokens=0)
    cp.record("200", _mentions("200", "B"), model=HAIKU_MODEL, input_tokens=1_000_000, output_tokens=0)
    usd, calls = ExtractionCheckpoint.load(path).prior_cost()
    assert usd == Decimal("2.00")
    assert calls == 2


def test_prior_cost_tolerates_unknown_model(tmp_path):
    path = tmp_path / "ckpt.jsonl"
    cp = ExtractionCheckpoint.load(path)
    cp.record("100", _mentions("100", "A"), model="some-removed-model", input_tokens=5, output_tokens=5)
    usd, calls = ExtractionCheckpoint.load(path).prior_cost()
    assert usd == Decimal("0")   # unknown model contributes $0, not a crash
    assert calls == 1


def test_load_tolerates_partial_final_line(tmp_path):
    path = tmp_path / "ckpt.jsonl"
    cp = ExtractionCheckpoint.load(path)
    cp.record("100", _mentions("100", "A"), model=HAIKU_MODEL, input_tokens=10, output_tokens=10)
    # Simulate a hard kill mid-write: append a truncated/garbage line.
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"pmid": "200", "mentions": [  ')   # no newline, unterminated
    resumed = ExtractionCheckpoint.load(path)
    assert resumed.done_pmids() == {"100"}   # good line kept, bad line skipped


def test_missing_file_is_empty_checkpoint(tmp_path):
    cp = ExtractionCheckpoint.load(tmp_path / "does-not-exist.jsonl")
    assert len(cp) == 0
    assert cp.all_mentions() == []
    assert cp.prior_cost() == (Decimal("0"), 0)

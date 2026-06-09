"""Tests for the resumable classify checkpoint in the corpus orchestrator."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline_tools.corpus_run import _classify_resumable


def _inputs(n):
    return [{"raw_name": f"Tool {i}", "tool_category": None, "context": None, "pub_count": 1} for i in range(n)]


def _counting_call_json(counter):
    def call_json(system, user):
        counter["calls"] += 1
        items = json.loads(user[user.index("["):])
        counter["classified"] += len(items)
        return {"classifications": [
            {"raw_name": it["raw_name"], "disposition": "method_tool", "kind": "method",
             "supercategory": "computational_statistical", "attributes": {}, "confidence": "high"}
            for it in items
        ]}
    return call_json


def test_no_checkpoint_classifies_all():
    c = {"calls": 0, "classified": 0}
    out = _classify_resumable(_inputs(5), call_json=_counting_call_json(c), batch_size=2, checkpoint_path=None)
    assert len(out) == 5 and c["classified"] == 5


def test_checkpoint_written_and_full_resume_skips_llm(tmp_path):
    ckpt = tmp_path / "classify_cache.jsonl"
    c = {"calls": 0, "classified": 0}
    out1 = _classify_resumable(_inputs(5), call_json=_counting_call_json(c), batch_size=2, checkpoint_path=ckpt)
    assert len(out1) == 5 and ckpt.exists()
    assert ckpt.read_text().count("\n") == 5  # one cached record per form

    def boom(system, user):
        raise AssertionError("full resume must not call the classifier")

    out2 = _classify_resumable(_inputs(5), call_json=boom, batch_size=2, checkpoint_path=ckpt)
    assert set(out2) == set(out1)


def test_partial_resume_only_classifies_missing(tmp_path):
    ckpt = tmp_path / "classify_cache.jsonl"
    # seed the cache with 3 of 5 forms already done.
    pre = {"calls": 0, "classified": 0}
    _classify_resumable(_inputs(3), call_json=_counting_call_json(pre), batch_size=2, checkpoint_path=ckpt)
    c = {"calls": 0, "classified": 0}
    out = _classify_resumable(_inputs(5), call_json=_counting_call_json(c), batch_size=2, checkpoint_path=ckpt)
    assert len(out) == 5
    assert c["classified"] == 2  # only the 2 new forms re-classified


def test_torn_final_line_tolerated(tmp_path):
    ckpt = tmp_path / "classify_cache.jsonl"
    _classify_resumable(_inputs(2), call_json=_counting_call_json({"calls": 0, "classified": 0}),
                        batch_size=2, checkpoint_path=ckpt)
    with ckpt.open("a") as fh:
        fh.write('{"raw_name": "Tool 9", "disp')  # torn write (hard kill mid-line)
    c = {"calls": 0, "classified": 0}
    out = _classify_resumable(_inputs(2), call_json=lambda s, u: (_ for _ in ()).throw(AssertionError("no LLM")),
                              batch_size=2, checkpoint_path=ckpt)
    assert len(out) == 2  # torn line skipped, the 2 good records still resume cleanly

"""Tests for the resumable classify checkpoint in the corpus orchestrator."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline_tools.corpus_run import _classify_resumable, _load_classify_cache
from pipeline_tools.registry import norm_name


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


def test_llm_error_placeholders_not_checkpointed(tmp_path):
    # A batch whose LLM call fails leaves its forms UNCLASSIFIED (disposition None). Those
    # placeholders must NOT persist to the cache, or a resume treats them as done and never
    # retries — a transient failure would drop the tool permanently.
    ckpt = tmp_path / "classify_cache.jsonl"

    def flaky(system, user):
        items = json.loads(user[user.index("["):])
        if any(it["raw_name"] == "Tool 0" for it in items):  # this batch -> unclassified
            raise RuntimeError("content filter")
        return {"classifications": [
            {"raw_name": it["raw_name"], "disposition": "method_tool", "kind": "method",
             "supercategory": "computational_statistical", "attributes": {}, "confidence": "high"}
            for it in items
        ]}

    out = _classify_resumable(_inputs(4), call_json=flaky, batch_size=2, checkpoint_path=ckpt)
    assert len(out) == 4  # all forms returned in-memory (failures flagged unclassified)
    cached = _load_classify_cache(ckpt)
    assert all(rec.get("disposition") is not None for rec in cached.values())  # no placeholders on disk
    assert norm_name("Tool 0") not in cached  # the failed form is absent -> a resume re-classifies it
    assert norm_name("Tool 2") in cached      # the successful batch persisted


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

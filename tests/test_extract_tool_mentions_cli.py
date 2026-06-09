"""Offline smoke test for cli.extract_tool_mentions.

Injects a stub call_llm so the whole CLI wiring — corpus load → preflight guard
→ derived ceiling → checkpointed sweep → mentions output — runs with NO AWS/
OpenAI and NO spend. The live Haiku seam is never constructed (call_llm injected).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal

from cli.extract_tool_mentions import main
from utils.bedrock_client import HAIKU_MODEL


@dataclass
class FakeResult:
    text: str
    input_tokens: int = 100
    output_tokens: int = 50
    model: str = HAIKU_MODEL


def _corpus(tmp_path, n=3):
    rows = [
        {"pmid": str(100 + i), "articleTitle": f"Study {100 + i}",
         "abstractVarchar": f"We used method-{100 + i}.",
         "authors": [{"cwid": f"cw{i}", "author_role": "lead"}]}
        for i in range(n)
    ]
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def _stub(*, input_tokens=100, output_tokens=50):
    def _call(system, user):
        m = re.search(r'"pmid": "(\w+)"', user)
        pmid = m.group(1) if m else "?"
        text = json.dumps({"mentions": [{"raw_name": f"method-{pmid}", "context": "used it"}]})
        return FakeResult(text, input_tokens=input_tokens, output_tokens=output_tokens)
    return _call


def test_cli_happy_path_writes_mentions_and_checkpoint(tmp_path):
    corpus = _corpus(tmp_path, n=3)
    out = tmp_path / "mentions.json"
    ckpt = tmp_path / "ckpt.jsonl"
    rc = main(
        ["--input", str(corpus), "--out", str(out), "--checkpoint", str(ckpt)],
        call_llm=_stub(),
    )
    assert rc == 0
    payload = json.loads(out.read_text())
    assert len(payload["mentions"]) == 3
    assert payload["telemetry"]["n_extracted"] == 3
    assert ckpt.exists()
    # The checkpoint is durable + resumable: a second run is an all-skip no-op.
    rc2 = main(
        ["--input", str(corpus), "--out", str(out), "--checkpoint", str(ckpt)],
        call_llm=_stub(),
    )
    assert rc2 == 0
    assert json.loads(out.read_text())["telemetry"]["n_skipped_resumed"] == 3


def test_cli_preflight_refuses_then_full_bypasses(tmp_path):
    corpus = _corpus(tmp_path, n=3)
    out = tmp_path / "mentions.json"
    ckpt = tmp_path / "ckpt.jsonl"
    # A near-zero threshold trips preflight (3 PMIDs × $0.006 = $0.02 > $0.001).
    rc = main(
        ["--input", str(corpus), "--out", str(out), "--checkpoint", str(ckpt),
         "--threshold-usd", "0.001"],
        call_llm=_stub(),
    )
    assert rc == 2   # refused, no spend
    assert not ckpt.exists()
    # --full bypasses and proceeds.
    rc2 = main(
        ["--input", str(corpus), "--out", str(out), "--checkpoint", str(ckpt),
         "--threshold-usd", "0.001", "--full"],
        call_llm=_stub(),
    )
    assert rc2 == 0
    assert ckpt.exists()


def test_cli_halts_and_exits_nonzero_on_cost_ceiling(tmp_path):
    corpus = _corpus(tmp_path, n=5)
    out = tmp_path / "mentions.json"
    ckpt = tmp_path / "ckpt.jsonl"
    # $1/call (1M input tokens) with a $1.50 hard cap → halts after the 2nd PMID.
    rc = main(
        ["--input", str(corpus), "--out", str(out), "--checkpoint", str(ckpt),
         "--full", "--hard-cap-usd", "1.50"],
        call_llm=_stub(input_tokens=1_000_000, output_tokens=0),
    )
    assert rc == 3   # halted on ceiling
    # Durable progress survives the halt; a resume with a raised cap finishes.
    assert json.loads(out.read_text())["telemetry"]["halted_on_ceiling"] is True
    rc2 = main(
        ["--input", str(corpus), "--out", str(out), "--checkpoint", str(ckpt),
         "--full", "--hard-cap-usd", "100"],
        call_llm=_stub(input_tokens=1_000_000, output_tokens=0),
    )
    assert rc2 == 0
    assert json.loads(out.read_text())["telemetry"]["total_mentions"] == 5


def test_cli_limit_caps_corpus(tmp_path):
    corpus = _corpus(tmp_path, n=10)
    out = tmp_path / "mentions.json"
    ckpt = tmp_path / "ckpt.jsonl"
    rc = main(
        ["--input", str(corpus), "--out", str(out), "--checkpoint", str(ckpt), "--limit", "2"],
        call_llm=_stub(),
    )
    assert rc == 0
    assert json.loads(out.read_text())["telemetry"]["n_extracted"] == 2

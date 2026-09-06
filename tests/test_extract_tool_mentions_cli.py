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


# --- externalised (S3) checkpoint: the scheduled-run path -------------------
class _FakeS3:
    """Duck-typed S3HierarchyClient (key_exists / get_object_bytes / put_object)."""

    def __init__(self, bucket="fake-artifacts"):
        self.bucket = bucket
        self.store = {}
        self.writes = 0

    def key_exists(self, key):
        return key in self.store

    def get_object_bytes(self, key):
        return self.store[key]

    def put_object(self, key, body, content_type=None):
        self.writes += 1
        self.store[key] = body


def test_cli_checkpoint_s3_resumes_across_a_fresh_container(tmp_path):
    # A scheduled Fargate run: run 2 gets a new container with an EMPTY disk, so
    # only the S3 log can tell it what run 1 already extracted.
    corpus = _corpus(tmp_path, n=3)
    s3 = _FakeS3()
    key = "tools/_checkpoint/cli-test.jsonl"

    rc = main(
        ["--input", str(corpus), "--out", str(tmp_path / "m1.json"),
         "--checkpoint", str(tmp_path / "run1" / "ckpt.jsonl"), "--checkpoint-s3", key],
        call_llm=_stub(), s3=s3,
    )
    assert rc == 0
    assert key in s3.store                                   # the log went to S3…
    assert not (tmp_path / "run1" / "ckpt.jsonl").exists()   # …and not to disk

    out2 = tmp_path / "m2.json"
    rc2 = main(
        ["--input", str(corpus), "--out", str(out2),
         "--checkpoint", str(tmp_path / "run2" / "ckpt.jsonl"), "--checkpoint-s3", key],
        call_llm=_stub(), s3=s3,
    )
    assert rc2 == 0
    telemetry = json.loads(out2.read_text())["telemetry"]
    assert telemetry["n_skipped_resumed"] == 3   # all three resumed — no re-extraction
    assert telemetry["n_extracted"] == 0


def test_cli_bare_checkpoint_s3_flag_uses_the_default_key(tmp_path):
    from pipeline_tools.checkpoint import DEFAULT_S3_KEY

    s3 = _FakeS3()
    rc = main(
        ["--input", str(_corpus(tmp_path, n=2)), "--out", str(tmp_path / "m.json"),
         "--checkpoint", str(tmp_path / "ckpt.jsonl"), "--checkpoint-s3"],
        call_llm=_stub(), s3=s3,
    )
    assert rc == 0 and DEFAULT_S3_KEY in s3.store


def test_cli_flushes_the_checkpoint_when_the_ceiling_halts_the_sweep(tmp_path):
    # A halted sweep must still leave its progress durable, or the next tick pays
    # for the same PMIDs again.
    s3 = _FakeS3()
    key = "tools/_checkpoint/halt.jsonl"
    rc = main(
        ["--input", str(_corpus(tmp_path, n=5)), "--out", str(tmp_path / "m.json"),
         "--checkpoint", str(tmp_path / "ckpt.jsonl"), "--checkpoint-s3", key,
         "--checkpoint-flush-every", "1000",     # nothing flushes on the record path
         "--full", "--hard-cap-usd", "1.50"],
        call_llm=_stub(input_tokens=1_000_000, output_tokens=0), s3=s3,
    )
    assert rc == 3                               # halted on the ceiling
    assert s3.writes == 1                        # the exit flush ran anyway
    assert len(s3.store[key].decode("utf-8").splitlines()) == 2   # both paid-for PMIDs durable

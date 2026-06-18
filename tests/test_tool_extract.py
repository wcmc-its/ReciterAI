"""Unit tests for pipeline_tools.extract (A2 per-PMID extraction harness).

Stub call_llm — no AWS/OpenAI. Exercises normalize_mention, extract_mentions,
and the checkpointed/ceiling-bounded/partial-failure-tolerant corpus sweep.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal

from pipeline_tools.checkpoint import ExtractionCheckpoint
from pipeline_tools.cost_guard import CostCeiling
from pipeline_tools.extract import (
    _truncate,
    extract_mentions,
    normalize_mention,
    run_extraction,
)
from prompts.tool_extract import CONTEXT_MAX_CHARS
from utils.bedrock_client import HAIKU_MODEL


@dataclass
class FakeResult:
    """Mimics pipeline_enrichment.llm_call.LLMCallResult."""
    text: str
    input_tokens: int = 100
    output_tokens: int = 50
    model: str = HAIKU_MODEL


def _mentions_json(*mentions: dict) -> str:
    return json.dumps({"mentions": list(mentions)})


def _row(pmid: str, **extra) -> dict:
    row = {"pmid": pmid, "articleTitle": f"Study {pmid}", "abstractVarchar": f"Abstract {pmid}."}
    row.update(extra)
    return row


def _stub(by_pmid: dict, *, default: FakeResult | None = None):
    """A call_llm stub that returns a canned FakeResult keyed on the prompt's pmid."""
    def _call(system: str, user: str):
        m = re.search(r'"pmid": "(\w+)"', user)
        pmid = m.group(1) if m else ""
        result = by_pmid.get(pmid, default)
        if isinstance(result, Exception):
            raise result
        assert result is not None, f"no stub result for pmid={pmid}"
        return result
    return _call


# ---------------------------------------------------------------------------
# normalize_mention
# ---------------------------------------------------------------------------

def test_normalize_drops_blank_name():
    assert normalize_mention({"raw_name": "  "}, _row("1")) is None


def test_normalize_nulls_out_of_vocab_hint_but_keeps_valid():
    valid = normalize_mention({"raw_name": "scRNA-seq", "tool_category_hint": "computational_method"}, _row("1"))
    assert valid["tool_category"] == "computational_method"
    bad = normalize_mention({"raw_name": "scRNA-seq", "tool_category_hint": "nonsense"}, _row("1"))
    assert bad["tool_category"] is None   # weak prior nulled, mention kept


def test_normalize_truncates_context_and_tags_author_fields():
    long_ctx = "x" * 500
    row = _row("42", cwid="abc2001", author_role="lead", authors=[{"cwid": "abc2001", "author_role": "lead"}])
    m = normalize_mention({"raw_name": "patch-clamp", "context": long_ctx, "confidence": "LOW"}, row)
    assert len(m["context"]) <= CONTEXT_MAX_CHARS
    assert m["pmid"] == "42"
    assert m["cwid"] == "abc2001"
    assert m["author_role"] == "lead"
    assert m["authors"] == [{"cwid": "abc2001", "author_role": "lead"}]
    assert m["confidence"] == "low"


def test_truncate_prefers_sentence_terminator_on_overflow():
    # Two sentences; the second pushes past the limit -> keep the first, ending at its period.
    head = "We used patch-clamp to record currents from CA1 pyramidal neurons."
    text = head + " " + "A second sentence that should be dropped because it overflows the budget by a lot." * 4
    out = _truncate(text, limit=len(head) + 30)
    assert out == head
    assert out.endswith(".")


def test_truncate_backs_off_to_word_boundary_never_mid_word():
    # One long sentence, no internal terminator -> back off to a space, not mid-word.
    text = "magnetic resonance imaging acquired diffusion weighted volumes across the whole cohort longitudinally"
    out = _truncate(text, limit=40)
    assert out == "magnetic resonance imaging acquired"  # clean word boundary, no split token


def test_truncate_hard_cuts_unbroken_token_as_last_resort():
    assert _truncate("x" * 100, limit=20) == "x" * 20


def test_normalize_source_kind_defaults_publication_and_honors_grant():
    pub = normalize_mention({"raw_name": "scRNA-seq"}, _row("1"))
    assert pub["source_kind"] == "publication"
    grant = normalize_mention({"raw_name": "scRNA-seq"}, _row("G1", source_kind="grant"))
    assert grant["source_kind"] == "grant"


# ---------------------------------------------------------------------------
# extract_mentions (one PMID)
# ---------------------------------------------------------------------------

def test_extract_success_returns_tagged_mentions_and_usage():
    text = _mentions_json(
        {"raw_name": "single-cell RNA-seq", "tool_category_hint": "computational_method", "context": "used scRNA-seq"},
        {"raw_name": "SRTR registry", "tool_category_hint": "dataset_public", "context": "linked to SRTR"},
    )
    call = _stub({"100": FakeResult(text, input_tokens=1200, output_tokens=300)})
    out = extract_mentions(_row("100", cwid="xyz1"), call_llm=call)
    assert out.ok and out.pmid == "100"
    assert [m["raw_name"] for m in out.mentions] == ["single-cell RNA-seq", "SRTR registry"]
    assert all(m["cwid"] == "xyz1" for m in out.mentions)
    assert out.input_tokens == 1200 and out.output_tokens == 300 and out.model == HAIKU_MODEL


def test_extract_empty_mentions_is_ok():
    call = _stub({"100": FakeResult(_mentions_json())})
    out = extract_mentions(_row("100"), call_llm=call)
    assert out.ok and out.mentions == []


def test_extract_parse_failure_records_usage_but_not_ok():
    """A malformed response still cost tokens — usage is reported so the ceiling sees it."""
    call = _stub({"100": FakeResult("not json at all", input_tokens=900, output_tokens=10)})
    out = extract_mentions(_row("100"), call_llm=call)
    assert not out.ok and "parse" in out.error
    assert out.input_tokens == 900   # paid for, must count toward the ceiling


def test_extract_call_failure_is_captured_with_zero_usage():
    call = _stub({"100": RuntimeError("bedrock down")})
    out = extract_mentions(_row("100"), call_llm=call)
    assert not out.ok and "llm_call" in out.error
    assert out.input_tokens == 0 and out.output_tokens == 0


# ---------------------------------------------------------------------------
# run_extraction (corpus sweep)
# ---------------------------------------------------------------------------

def _one_mention_result(pmid: str) -> FakeResult:
    return FakeResult(_mentions_json({"raw_name": f"tool-{pmid}", "context": "used it"}), input_tokens=100, output_tokens=50)


def test_sweep_happy_path_checkpoints_all(tmp_path):
    rows = [_row("1"), _row("2"), _row("3")]
    call = _stub({p: _one_mention_result(p) for p in ("1", "2", "3")})
    cp = ExtractionCheckpoint.load(tmp_path / "c.jsonl")
    ceiling = CostCeiling(cap_usd=Decimal("100"))
    res = run_extraction(rows, call_llm=call, checkpoint=cp, ceiling=ceiling)
    assert res.n_done == 3 and res.n_failed == 0 and res.n_skipped == 0
    assert len(res.mentions) == 3
    assert cp.done_pmids() == {"1", "2", "3"}
    assert ceiling.observed_usd > Decimal("0")
    assert res.telemetry["mentions_per_paper"] == 1.0


def test_sweep_skips_already_done(tmp_path):
    cp = ExtractionCheckpoint.load(tmp_path / "c.jsonl")
    cp.record("1", [{"raw_name": "pre", "pmid": "1"}], model=HAIKU_MODEL, input_tokens=10, output_tokens=10)
    rows = [_row("1"), _row("2")]
    call = _stub({"2": _one_mention_result("2")})   # "1" must NOT be called
    res = run_extraction(rows, call_llm=call, checkpoint=cp, ceiling=CostCeiling(cap_usd=Decimal("100")))
    assert res.n_skipped == 1 and res.n_done == 1
    assert cp.done_pmids() == {"1", "2"}


def test_sweep_is_partial_failure_tolerant(tmp_path):
    rows = [_row("1"), _row("2"), _row("3")]
    call = _stub({
        "1": _one_mention_result("1"),
        "2": FakeResult("garbage", input_tokens=50, output_tokens=5),   # parse fail
        "3": _one_mention_result("3"),
    })
    cp = ExtractionCheckpoint.load(tmp_path / "c.jsonl")
    res = run_extraction(rows, call_llm=call, checkpoint=cp, ceiling=CostCeiling(cap_usd=Decimal("100")))
    assert res.n_done == 2 and res.n_failed == 1
    assert res.failed[0]["pmid"] == "2"
    assert cp.done_pmids() == {"1", "3"}     # failed PMID NOT checkpointed → retried on resume


def test_sweep_halts_on_cost_ceiling_keeping_durable_progress(tmp_path):
    # Each call is a $1 Haiku call; cap $1.50 → first OK, second crosses and halts.
    rows = [_row("1"), _row("2"), _row("3")]
    big = lambda p: FakeResult(_mentions_json({"raw_name": f"t-{p}"}), input_tokens=1_000_000, output_tokens=0)
    call = _stub({p: big(p) for p in ("1", "2", "3")})
    cp = ExtractionCheckpoint.load(tmp_path / "c.jsonl")
    ceiling = CostCeiling(cap_usd=Decimal("1.50"))
    res = run_extraction(rows, call_llm=call, checkpoint=cp, ceiling=ceiling)
    assert res.halted is not None
    assert res.halted.reason == "cost"
    # Both attempted PMIDs are durable (checkpointed before the ceiling fired);
    # the third never ran.
    assert cp.done_pmids() == {"1", "2"}
    assert res.telemetry["halted_on_ceiling"] is True


def test_sweep_resumes_with_seeded_prior_cost(tmp_path):
    """A resumed sweep seeds the ceiling from the checkpoint's prior spend."""
    path = tmp_path / "c.jsonl"
    cp1 = ExtractionCheckpoint.load(path)
    cp1.record("1", [{"raw_name": "a", "pmid": "1"}], model=HAIKU_MODEL, input_tokens=1_000_000, output_tokens=0)
    prior_usd, prior_calls = ExtractionCheckpoint.load(path).prior_cost()
    assert prior_usd == Decimal("1.00")

    cp2 = ExtractionCheckpoint.load(path)
    ceiling = CostCeiling(cap_usd=Decimal("1.50"), prior_usd=prior_usd, prior_calls=prior_calls)
    call = _stub({"2": FakeResult(_mentions_json({"raw_name": "b"}), input_tokens=1_000_000, output_tokens=0)})
    res = run_extraction([_row("1"), _row("2")], call_llm=call, checkpoint=cp2, ceiling=ceiling)
    # "1" skipped (done); "2" runs, prior $1 + $1 = $2 > $1.50 → halts on the new PMID.
    assert res.n_skipped == 1
    assert res.halted is not None and ceiling.observed_usd == Decimal("2.00")

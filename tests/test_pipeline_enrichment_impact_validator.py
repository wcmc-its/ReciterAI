"""Unit tests for impact scoring on the Bedrock path (#37 / #50).

`score_impact` now runs on AWS Bedrock Sonnet 4.6 via
`pipeline_enrichment.llm_call`, with an OpenAI gpt-5.1 content-filter
fallback. These tests inject a fake Bedrock client and exercise:
- a justification ≤120 chars AND ≤10 words → returned cleanly;
- a char or word overrun on the first attempt → reinforcement retry;
- both attempts overrun → returned with an error flag, the model's actual
  output preserved (NEVER truncated — #50 / feedback_no_arbitrary_truncation);
- the impactScore clamp + JSON parse-error paths;
- the content-filter fallback to gpt-5.1.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from pipeline_enrichment import llm_call as llm_call_mod
from pipeline_enrichment.impact import (
    JUSTIF_MAX_CHARS,
    JUSTIF_MAX_WORDS,
    score_impact,
)
from utils.bedrock_client import BedrockCallResult, BedrockEmptyContentError, SONNET_MODEL
from utils.openai_client import GPT5_MODEL


class _FakeBedrock:
    """Stand-in BedrockClient: each call_with_usage consumes one queued
    item — a BedrockCallResult is returned, an Exception is raised."""

    def __init__(self, items):
        self._items = list(items)
        self.call_count = 0
        self.calls = []

    def call_with_usage(self, *, model, messages, system, max_tokens):
        self.call_count += 1
        self.calls.append({"model": model, "messages": messages, "system": system})
        item = self._items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _bedrock_impact(score, justification, *, input_tokens=200,
                    output_tokens=80) -> BedrockCallResult:
    """A Bedrock response whose text is the impact JSON object."""
    return BedrockCallResult(
        text=json.dumps({"impactScore": score, "justification": justification}),
        input_tokens=input_tokens, output_tokens=output_tokens,
        stop_reason="end_turn",
    )


def _openai_completion(content: str, *, prompt_tokens=150, completion_tokens=30):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )


_PUB = {"pmid": "12345", "articleTitle": "Some title", "abstractVarchar": "Abstract."}


def test_impact_first_attempt_within_limits_returns_clean():
    j = "Solid methodology and modest impact"  # 5 words, 35 chars
    fake = _FakeBedrock([_bedrock_impact(55, j)])
    result = score_impact(pub_data=_PUB, client=fake)

    assert fake.call_count == 1  # no retry
    assert result.impact_score == 55
    assert result.justification == j
    assert result.error is None
    assert result.model == SONNET_MODEL
    assert result.prompt_version == "v2"
    assert result.input_tokens == 200
    assert result.output_tokens == 80


def test_impact_word_overrun_retries_and_succeeds():
    over = ("Strong methods, clear novelty, broad applicability and excellent "
            "translational potential demonstrated")
    assert len(over.split()) > JUSTIF_MAX_WORDS
    under = "Strong methods, clear novelty, broad translational potential"  # ≤10 words

    fake = _FakeBedrock([_bedrock_impact(70, over), _bedrock_impact(70, under)])
    result = score_impact(pub_data=_PUB, client=fake)

    assert fake.call_count == 2
    assert result.justification == under
    assert result.error is None
    # Token usage accumulates across both attempts.
    assert result.input_tokens == 400
    assert result.output_tokens == 160
    # The retry augmented the user prompt with the violation hint.
    second_user = fake.calls[1]["messages"][0]["content"]
    assert "words" in second_user
    assert f"≤{JUSTIF_MAX_WORDS} words" in second_user


def test_impact_char_overrun_retries_and_succeeds():
    # Many chars but few words: blows the char budget, not the word budget.
    over_chars = "a" * (JUSTIF_MAX_CHARS + 10) + " final"
    assert len(over_chars) > JUSTIF_MAX_CHARS
    under = "Compact justification within both limits"

    fake = _FakeBedrock([_bedrock_impact(40, over_chars), _bedrock_impact(40, under)])
    result = score_impact(pub_data=_PUB, client=fake)

    assert fake.call_count == 2
    assert result.justification == under
    assert result.error is None


def test_impact_two_overruns_returns_violation_flag_preserves_output():
    """Critical regression guard for #50: when both attempts overrun, the
    model's actual output is preserved (not truncated) and the error field
    flags the violation precisely."""
    first = "Novel MRI perfusion method with strong ex vivo validation; potential influence."  # 11w
    second = ("Novel imaging perfusion method with strong validation and "
              "translational promise overall today.")  # also >10w

    fake = _FakeBedrock([_bedrock_impact(48, first), _bedrock_impact(48, second)])
    result = score_impact(pub_data=_PUB, client=fake)

    assert fake.call_count == 2
    # The actual second model output is preserved verbatim.
    assert result.justification == second
    assert result.impact_score == 48
    assert result.error is not None
    assert "length violation" in result.error
    assert f"{len(second.split())} words" in result.error
    assert f"limit {JUSTIF_MAX_WORDS}" in result.error


def test_impact_no_silent_truncation_at_120_chars():
    """Regression guard: a two-attempt char overrun must surface as a
    flagged result with the full model output intact — NOT a 120-char
    prefix (#50 / feedback_no_arbitrary_truncation)."""
    long_just = "x" * 200  # 1 word but 200 chars
    again = "x" * 180

    fake = _FakeBedrock([_bedrock_impact(30, long_just), _bedrock_impact(30, again)])
    result = score_impact(pub_data=_PUB, client=fake)

    # Critical: length preserved, NOT clipped to 120.
    assert result.justification == again
    assert len(result.justification) == 180
    assert result.error is not None
    assert "length violation" in result.error
    assert f"{len(again)} chars" in result.error


def test_impact_score_clamped_to_0_100():
    """The POC's 0..100 clamp is preserved on the Bedrock path."""
    fake_high = _FakeBedrock([_bedrock_impact(150, "ok")])
    result_high = score_impact(pub_data=_PUB, client=fake_high)
    fake_low = _FakeBedrock([_bedrock_impact(-10, "ok")])
    result_low = score_impact(pub_data=_PUB, client=fake_low)

    assert result_high.impact_score == 100
    assert result_low.impact_score == 0


def test_impact_call_error_returns_immediately():
    fake = _FakeBedrock([RuntimeError("upstream 500")])
    result = score_impact(pub_data=_PUB, client=fake)

    assert fake.call_count == 1
    assert result.impact_score is None
    assert "upstream 500" in result.error


def test_impact_unparseable_json_returns_error():
    fake = _FakeBedrock([BedrockCallResult(
        text="not json at all", input_tokens=10, output_tokens=5,
        stop_reason="end_turn",
    )])
    result = score_impact(pub_data=_PUB, client=fake)

    assert fake.call_count == 1
    assert result.impact_score is None
    assert "json decode" in result.error


def test_impact_missing_impact_score_returns_error():
    """Valid JSON but no impactScore key — a distinct, flagged failure."""
    fake = _FakeBedrock([BedrockCallResult(
        text=json.dumps({"justification": "no score present"}),
        input_tokens=10, output_tokens=5, stop_reason="end_turn",
    )])
    result = score_impact(pub_data=_PUB, client=fake)

    assert fake.call_count == 1
    assert result.impact_score is None
    assert "missing impactScore" in result.error


def test_impact_content_filter_falls_back_to_openai():
    """A Bedrock content-filter block recovers via the OpenAI fallback; the
    result records gpt-5.1 as the model actually used."""
    fake = _FakeBedrock([
        BedrockEmptyContentError(stop_reason="content_filtered", model=SONNET_MODEL),
    ])
    with patch.object(llm_call_mod, "get_default_openai_client", return_value=object()), \
         patch.object(llm_call_mod, "openai_call_with_retry",
                      return_value=_openai_completion(
                          json.dumps({"impactScore": 62, "justification": "ok"}))):
        result = score_impact(pub_data=_PUB, client=fake)

    assert result.impact_score == 62
    assert result.justification == "ok"
    assert result.error is None
    assert result.model == GPT5_MODEL
    assert result.input_tokens == 150
    assert result.output_tokens == 30


def test_impact_non_filter_empty_retries_bedrock_then_succeeds():
    """A non-content-filter empty Bedrock return retries once inside
    llm_call, transparently to score_impact."""
    fake = _FakeBedrock([
        BedrockEmptyContentError(stop_reason="end_turn", model=SONNET_MODEL),
        _bedrock_impact(48, "Solid incremental contribution"),
    ])
    result = score_impact(pub_data=_PUB, client=fake)

    assert fake.call_count == 2
    assert result.impact_score == 48
    assert result.error is None
    assert result.model == SONNET_MODEL

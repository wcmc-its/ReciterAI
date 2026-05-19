"""Unit tests for synopsis generation on the Bedrock path (#37 / #50).

`generate_synopsis` now runs on AWS Bedrock Sonnet 4.6 via
`pipeline_enrichment.llm_call`, with an OpenAI gpt-5.1 content-filter
fallback. These tests inject a fake Bedrock client and exercise:
- a ≤95-char first attempt → returned cleanly;
- an overrun → reinforcement retry; the loop is the initial call + 2 retries;
- every attempt overruns → returned with an error flag, the model's actual
  output preserved (NEVER truncated — #50 / feedback_no_arbitrary_truncation);
- empty / unparseable returns;
- the content-filter fallback to gpt-5.1.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from pipeline_enrichment import llm_call as llm_call_mod
from pipeline_enrichment.synopsis import (
    SYNOPSIS_MAX_ATTEMPTS,
    SYNOPSIS_MAX_CHARS,
    generate_synopsis,
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


def _bedrock_synopsis(text: str, *, input_tokens=100,
                      output_tokens=20) -> BedrockCallResult:
    """A Bedrock response whose text is the synopsis JSON object."""
    return BedrockCallResult(
        text=json.dumps({"synopsis": text}),
        input_tokens=input_tokens, output_tokens=output_tokens,
        stop_reason="end_turn",
    )


def _openai_completion(content: str, *, prompt_tokens=150, completion_tokens=30):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )


def test_synopsis_first_attempt_within_limit_returns_clean():
    syn = "A 70-character synopsis that fits comfortably under the limit oh yes"
    assert len(syn) <= SYNOPSIS_MAX_CHARS

    fake = _FakeBedrock([_bedrock_synopsis(syn)])
    result = generate_synopsis(pmid="1", title="T", abstract="A", client=fake)

    assert fake.call_count == 1  # no retry
    assert result.synopsis == syn
    assert result.error is None
    assert result.model == SONNET_MODEL
    assert result.input_tokens == 100
    assert result.output_tokens == 20


def test_synopsis_first_overrun_retries_and_succeeds():
    over = "x" * 110  # over the 95 limit
    under = "x" * 80
    fake = _FakeBedrock([_bedrock_synopsis(over), _bedrock_synopsis(under)])
    result = generate_synopsis(pmid="2", title="T", abstract="A", client=fake)

    assert fake.call_count == 2
    assert result.synopsis == under
    assert result.error is None
    # Token usage accumulates across both attempts.
    assert result.input_tokens == 200
    assert result.output_tokens == 40
    # The retry augmented the user prompt with the violation hint.
    second_user = fake.calls[1]["messages"][0]["content"]
    assert "previous attempt was 110 characters" in second_user
    assert "Count characters" in second_user


def test_synopsis_uses_three_attempts_before_giving_up():
    """D4: the loop is the initial call + 2 reinforcement retries."""
    assert SYNOPSIS_MAX_ATTEMPTS == 3
    over1, over2 = "x" * 110, "x" * 105
    under = "x" * 60
    fake = _FakeBedrock([
        _bedrock_synopsis(over1), _bedrock_synopsis(over2), _bedrock_synopsis(under),
    ])
    result = generate_synopsis(pmid="3", title="T", abstract="A", client=fake)

    assert fake.call_count == 3
    assert result.synopsis == under
    assert result.error is None


def test_synopsis_all_attempts_overrun_returns_violation_flag_preserves_output():
    """Every attempt overruns: the model's actual output is preserved (NOT
    truncated) and the error field flags the length violation."""
    a, b, c = "x" * 110, "x" * 108, "x" * 100  # all over 95
    fake = _FakeBedrock([
        _bedrock_synopsis(a), _bedrock_synopsis(b), _bedrock_synopsis(c),
    ])
    result = generate_synopsis(pmid="4", title="T", abstract="A", client=fake)

    assert fake.call_count == 3
    # Critical: actual output preserved, not clipped to 95.
    assert result.synopsis == c
    assert len(result.synopsis) == 100
    assert result.error is not None
    assert "length violation" in result.error
    assert "100 chars" in result.error
    assert "limit 95" in result.error


def test_synopsis_empty_field_short_circuits_no_retry():
    """An empty `synopsis` field is a distinct failure mode — return
    immediately, don't burn a retry trying to shorten an empty string."""
    fake = _FakeBedrock([_bedrock_synopsis("")])
    result = generate_synopsis(pmid="5", title="T", abstract="A", client=fake)

    assert fake.call_count == 1
    assert result.synopsis is None
    assert result.error == "empty synopsis in response"


def test_synopsis_unparseable_json_returns_error():
    fake = _FakeBedrock([BedrockCallResult(
        text="not json at all", input_tokens=10, output_tokens=5,
        stop_reason="end_turn",
    )])
    result = generate_synopsis(pmid="6", title="T", abstract="A", client=fake)

    assert fake.call_count == 1
    assert result.synopsis is None
    assert "json decode" in result.error


def test_synopsis_call_error_returns_immediately():
    fake = _FakeBedrock([RuntimeError("bedrock exploded")])
    result = generate_synopsis(pmid="7", title="T", abstract="A", client=fake)

    assert fake.call_count == 1
    assert result.synopsis is None
    assert "bedrock exploded" in result.error


def test_synopsis_exactly_at_limit_is_accepted():
    """The boundary case: exactly 95 chars is allowed (≤ not <)."""
    exactly = "x" * SYNOPSIS_MAX_CHARS
    fake = _FakeBedrock([_bedrock_synopsis(exactly)])
    result = generate_synopsis(pmid="8", title="T", abstract="A", client=fake)

    assert fake.call_count == 1
    assert result.synopsis == exactly
    assert result.error is None


def test_synopsis_content_filter_falls_back_to_openai():
    """A Bedrock content-filter block recovers via the OpenAI fallback; the
    result records gpt-5.1 as the model actually used."""
    syn = "Sonnet was filtered; this synopsis came from the gpt-5.1 fallback"
    assert len(syn) <= SYNOPSIS_MAX_CHARS

    fake = _FakeBedrock([
        BedrockEmptyContentError(stop_reason="content_filtered", model=SONNET_MODEL),
    ])
    with patch.object(llm_call_mod, "get_default_openai_client", return_value=object()), \
         patch.object(llm_call_mod, "openai_call_with_retry",
                      return_value=_openai_completion(json.dumps({"synopsis": syn}))):
        result = generate_synopsis(pmid="9", title="T", abstract="A", client=fake)

    assert result.synopsis == syn
    assert result.error is None
    assert result.model == GPT5_MODEL
    assert result.input_tokens == 150
    assert result.output_tokens == 30


def test_synopsis_non_filter_empty_retries_bedrock_then_succeeds():
    """A non-content-filter empty Bedrock return retries once inside
    llm_call, transparently to generate_synopsis."""
    syn = "Recovered on the Bedrock retry after a transient empty return"
    fake = _FakeBedrock([
        BedrockEmptyContentError(stop_reason="end_turn", model=SONNET_MODEL),
        _bedrock_synopsis(syn),
    ])
    result = generate_synopsis(pmid="10", title="T", abstract="A", client=fake)

    assert fake.call_count == 2
    assert result.synopsis == syn
    assert result.error is None
    assert result.model == SONNET_MODEL

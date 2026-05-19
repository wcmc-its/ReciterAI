"""Tests for pipeline_enrichment.llm_call — the shared Bedrock + OpenAI
content-filter fallback call path for the daily-enrichment workers.

Covers: lenient JSON parsing, the Bedrock happy path, the OpenAI fallback
on a content-filter block, and the single Bedrock retry on a
non-content-filter empty return.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from pipeline_enrichment import llm_call as llm_call_mod
from pipeline_enrichment.llm_call import (
    LLMCallResult,
    call_with_fallback,
    parse_json_lenient,
)
from utils.bedrock_client import BedrockCallResult, BedrockEmptyContentError, SONNET_MODEL
from utils.openai_client import GPT5_MODEL


class _FakeBedrock:
    """Stand-in BedrockClient. `results` is consumed one per call_with_usage
    call — a BedrockCallResult is returned, an Exception is raised."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def call_with_usage(self, *, model, messages, system, max_tokens):
        self.calls.append(
            {"model": model, "messages": messages, "system": system, "max_tokens": max_tokens}
        )
        item = self._results.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _bedrock_result(text="{}", *, input_tokens=120, output_tokens=15,
                    stop_reason="end_turn") -> BedrockCallResult:
    return BedrockCallResult(
        text=text, input_tokens=input_tokens,
        output_tokens=output_tokens, stop_reason=stop_reason,
    )


def _openai_completion(content='{"synopsis": "from openai"}', *,
                       prompt_tokens=200, completion_tokens=25):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )


# --- parse_json_lenient ------------------------------------------------------


def test_parse_json_lenient_plain():
    assert parse_json_lenient('{"a": 1}') == {"a": 1}


def test_parse_json_lenient_strips_fences():
    assert parse_json_lenient('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_json_lenient_extracts_object_from_prose():
    assert parse_json_lenient('Here is the result: {"a": 1} — done.') == {"a": 1}


def test_parse_json_lenient_raises_on_unrecoverable():
    with pytest.raises(json.JSONDecodeError):
        parse_json_lenient("no json object here at all")


# --- call_with_fallback: Bedrock happy path ---------------------------------


def test_call_with_fallback_bedrock_happy_path():
    fake = _FakeBedrock([_bedrock_result('{"synopsis": "ok"}')])
    result = call_with_fallback(
        system_prompt="SYS", user_prompt="USER",
        model=SONNET_MODEL, max_tokens=512, bedrock_client=fake,
    )

    assert isinstance(result, LLMCallResult)
    assert result.text == '{"synopsis": "ok"}'
    assert result.input_tokens == 120
    assert result.output_tokens == 15
    assert result.model == SONNET_MODEL
    # Bedrock called once; the prompt is passed through verbatim.
    assert len(fake.calls) == 1
    assert fake.calls[0]["system"] == "SYS"
    assert fake.calls[0]["messages"] == [{"role": "user", "content": "USER"}]
    assert fake.calls[0]["max_tokens"] == 512


# --- call_with_fallback: content-filter → OpenAI fallback -------------------


def test_call_with_fallback_content_filter_falls_back_to_openai():
    fake = _FakeBedrock([
        BedrockEmptyContentError(stop_reason="content_filtered", model=SONNET_MODEL),
    ])
    with patch.object(llm_call_mod, "openai_call_with_retry",
                      return_value=_openai_completion()) as mock_openai:
        result = call_with_fallback(
            system_prompt="SYS", user_prompt="USER",
            bedrock_client=fake, openai_client=object(),
        )

    assert result.model == GPT5_MODEL
    assert result.text == '{"synopsis": "from openai"}'
    assert result.input_tokens == 200
    assert result.output_tokens == 25
    # The OpenAI fallback got the same system + user prompt, on gpt-5.1.
    kwargs = mock_openai.call_args.kwargs
    assert kwargs["system_prompt"] == "SYS"
    assert kwargs["user_prompt"] == "USER"
    assert kwargs["model"] == GPT5_MODEL


def test_call_with_fallback_guardrail_intervened_also_falls_back():
    fake = _FakeBedrock([
        BedrockEmptyContentError(stop_reason="guardrail_intervened", model=SONNET_MODEL),
    ])
    with patch.object(llm_call_mod, "openai_call_with_retry",
                      return_value=_openai_completion()):
        result = call_with_fallback(
            system_prompt="SYS", user_prompt="USER",
            bedrock_client=fake, openai_client=object(),
        )

    assert result.model == GPT5_MODEL


def test_call_with_fallback_lazy_defaults_openai_client():
    """openai_client=None lazily resolves the default client only when the
    fallback actually fires (it needs OPENAI_API_KEY)."""
    fake = _FakeBedrock([
        BedrockEmptyContentError(stop_reason="content_filtered", model=SONNET_MODEL),
    ])
    with patch.object(llm_call_mod, "get_default_openai_client",
                      return_value=object()) as mock_default, \
         patch.object(llm_call_mod, "openai_call_with_retry",
                      return_value=_openai_completion()):
        call_with_fallback(system_prompt="SYS", user_prompt="USER", bedrock_client=fake)

    mock_default.assert_called_once()


def test_call_with_fallback_raises_when_openai_fallback_also_fails():
    fake = _FakeBedrock([
        BedrockEmptyContentError(stop_reason="content_filtered", model=SONNET_MODEL),
    ])
    with patch.object(llm_call_mod, "openai_call_with_retry",
                      side_effect=RuntimeError("openai down")):
        with pytest.raises(RuntimeError) as exc_info:
            call_with_fallback(
                system_prompt="SYS", user_prompt="USER",
                bedrock_client=fake, openai_client=object(),
            )

    msg = str(exc_info.value)
    assert "content_filtered" in msg
    assert "openai down" in msg


# --- call_with_fallback: non-content-filter empty → one Bedrock retry -------


def test_call_with_fallback_retries_once_on_non_filter_empty():
    fake = _FakeBedrock([
        BedrockEmptyContentError(stop_reason="max_tokens", model=SONNET_MODEL),
        _bedrock_result('{"synopsis": "second try"}'),
    ])
    result = call_with_fallback(
        system_prompt="SYS", user_prompt="USER", bedrock_client=fake,
    )

    assert result.text == '{"synopsis": "second try"}'
    assert result.model == SONNET_MODEL
    assert len(fake.calls) == 2


def test_call_with_fallback_raises_when_retry_also_empty():
    fake = _FakeBedrock([
        BedrockEmptyContentError(stop_reason="max_tokens", model=SONNET_MODEL),
        BedrockEmptyContentError(stop_reason="max_tokens", model=SONNET_MODEL),
    ])
    with pytest.raises(BedrockEmptyContentError):
        call_with_fallback(system_prompt="SYS", user_prompt="USER", bedrock_client=fake)


def test_call_with_fallback_non_filter_empty_does_not_call_openai():
    """A non-content-filter empty must retry Bedrock, NOT the OpenAI fallback."""
    fake = _FakeBedrock([
        BedrockEmptyContentError(stop_reason="end_turn", model=SONNET_MODEL),
        _bedrock_result('{"ok": 1}'),
    ])
    with patch.object(llm_call_mod, "openai_call_with_retry") as mock_openai:
        call_with_fallback(system_prompt="SYS", user_prompt="USER", bedrock_client=fake)

    mock_openai.assert_not_called()


# --- default client ----------------------------------------------------------


def test_get_default_bedrock_client_is_cached():
    a = llm_call_mod.get_default_bedrock_client()
    b = llm_call_mod.get_default_bedrock_client()
    assert a is b

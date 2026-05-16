"""Tests for utils.bedrock_client.BedrockClient response handling.

Focus: Bedrock Converse responses with empty `content` lists (triggered by
the safety filter when stopReason='content_filtered') must surface as a
structured BedrockEmptyContentError, NOT as IndexError from `content[0]`.

Regression covers wcmc-its/ReciterAI#72 smoke 5: Sonnet 4.6 content-filtered
on biomedical mouse-study abstracts, every dense pass crashed with
`list index out of range`, and 100% of PMIDs in the delta window failed.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from utils.bedrock_client import (
    BedrockClient,
    BedrockEmptyContentError,
    SONNET_MODEL,
)


def _content_filtered_response() -> dict:
    """Synthetic Bedrock Converse response shape mirroring smoke 5 failure.

    Captured from a live Sonnet 4.6 call on PMID 41198049 (NSG mouse study)
    on 2026-05-16. Safety filter blocked the response; `output.message.content`
    is an empty list and stopReason is 'content_filtered'.
    """
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [],
            }
        },
        "stopReason": "content_filtered",
        "usage": {
            "inputTokens": 1309,
            "outputTokens": 8,
            "totalTokens": 1317,
            "cacheReadInputTokens": 0,
            "cacheWriteInputTokens": 0,
        },
        "metrics": {"latencyMs": 2475},
    }


def _happy_response(text: str = '{"0": 0.85}') -> dict:
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"text": text}],
            }
        },
        "stopReason": "end_turn",
        "usage": {"inputTokens": 100, "outputTokens": 10, "totalTokens": 110},
    }


def test_call_raises_structured_error_on_empty_content_filtered():
    """Empty content + content_filtered must raise BedrockEmptyContentError, not IndexError."""
    client = BedrockClient()
    with patch.object(client, "_call_with_retry", return_value=_content_filtered_response()):
        with pytest.raises(BedrockEmptyContentError) as exc_info:
            client.call(model=SONNET_MODEL, messages=[{"role": "user", "content": "..."}])

    assert exc_info.value.stop_reason == "content_filtered"
    assert exc_info.value.model == SONNET_MODEL
    assert "content_filtered" in str(exc_info.value)
    assert SONNET_MODEL in str(exc_info.value)


def test_call_raises_structured_error_on_guardrail_intervened():
    """Same defensive handling for guardrail_intervened stopReason."""
    resp = _content_filtered_response()
    resp["stopReason"] = "guardrail_intervened"
    client = BedrockClient()
    with patch.object(client, "_call_with_retry", return_value=resp):
        with pytest.raises(BedrockEmptyContentError) as exc_info:
            client.call(model=SONNET_MODEL, messages=[{"role": "user", "content": "..."}])

    assert exc_info.value.stop_reason == "guardrail_intervened"


def test_call_raises_structured_error_when_content_key_missing():
    """A malformed response with no `content` key still raises a structured error, not KeyError/IndexError."""
    client = BedrockClient()
    resp = {"output": {"message": {"role": "assistant"}}, "stopReason": "max_tokens"}
    with patch.object(client, "_call_with_retry", return_value=resp):
        with pytest.raises(BedrockEmptyContentError) as exc_info:
            client.call(model=SONNET_MODEL, messages=[{"role": "user", "content": "..."}])

    assert exc_info.value.stop_reason == "max_tokens"


def test_call_returns_text_on_normal_response():
    """Happy path: non-empty content returns the first text block."""
    client = BedrockClient()
    with patch.object(client, "_call_with_retry", return_value=_happy_response('{"0": 0.85}')):
        text = client.call(model=SONNET_MODEL, messages=[{"role": "user", "content": "..."}])

    assert text == '{"0": 0.85}'


def test_call_json_propagates_empty_content_error():
    """call_json's outer wrapper must propagate the BedrockEmptyContentError,
    not retry indefinitely or swallow the failure."""
    client = BedrockClient()
    with patch.object(client, "_call_with_retry", return_value=_content_filtered_response()):
        with pytest.raises(BedrockEmptyContentError):
            client.call_json(model=SONNET_MODEL, messages=[{"role": "user", "content": "..."}])


def test_empty_content_error_is_runtime_error_subclass():
    """Existing `except Exception` handlers (e.g. score_one_publication) catch this cleanly."""
    err = BedrockEmptyContentError(stop_reason="content_filtered", model=SONNET_MODEL)
    assert isinstance(err, RuntimeError)
    assert isinstance(err, Exception)

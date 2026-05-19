"""Tests for utils.bedrock_client.BedrockClient response handling.

Focus: Bedrock Converse responses with empty `content` lists (triggered by
the safety filter when stopReason='content_filtered') must surface as a
structured BedrockEmptyContentError, NOT as IndexError from `content[0]`.

Regression covers wcmc-its/ReciterAI#72 smoke 5: Sonnet 4.6 content-filtered
on biomedical mouse-study abstracts, every dense pass crashed with
`list index out of range`, and 100% of PMIDs in the delta window failed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from utils.bedrock_client import (
    BedrockCallResult,
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


# ---------------------------------------------------------------------------
# call_with_usage — the usage-returning substrate method (#37 Bedrock rewire)
# ---------------------------------------------------------------------------


def test_call_with_usage_returns_text_and_token_counts():
    """call_with_usage returns a BedrockCallResult: text + usage + stopReason."""
    client = BedrockClient()
    with patch.object(client, "_call_with_retry",
                       return_value=_happy_response('{"synopsis": "ok"}')):
        result = client.call_with_usage(
            model=SONNET_MODEL, messages=[{"role": "user", "content": "..."}],
        )

    assert isinstance(result, BedrockCallResult)
    assert result.text == '{"synopsis": "ok"}'
    assert result.input_tokens == 100
    assert result.output_tokens == 10
    assert result.stop_reason == "end_turn"


def test_call_with_usage_raises_structured_error_on_content_filter():
    """An empty content block raises BedrockEmptyContentError carrying the
    stopReason, so the enrichment helper can branch to the OpenAI fallback."""
    client = BedrockClient()
    with patch.object(client, "_call_with_retry",
                       return_value=_content_filtered_response()):
        with pytest.raises(BedrockEmptyContentError) as exc_info:
            client.call_with_usage(
                model=SONNET_MODEL, messages=[{"role": "user", "content": "..."}],
            )

    assert exc_info.value.stop_reason == "content_filtered"
    assert exc_info.value.model == SONNET_MODEL


def test_call_with_usage_defaults_missing_usage_block_to_zero():
    """A response with content but no `usage` key yields zero token counts,
    not a KeyError."""
    client = BedrockClient()
    resp = {
        "output": {"message": {"role": "assistant", "content": [{"text": "hi"}]}},
        "stopReason": "end_turn",
    }
    with patch.object(client, "_call_with_retry", return_value=resp):
        result = client.call_with_usage(
            model=SONNET_MODEL, messages=[{"role": "user", "content": "..."}],
        )

    assert result.text == "hi"
    assert result.input_tokens == 0
    assert result.output_tokens == 0


def test_call_with_usage_retries_on_throttling_then_succeeds():
    """call_with_usage inherits _call_with_retry's throttle backoff."""
    from botocore.exceptions import ClientError

    throttle = ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "slow down"}},
        "Converse",
    )
    fake_boto = MagicMock()
    fake_boto.converse.side_effect = [throttle, _happy_response('{"ok": 1}')]

    client = BedrockClient()
    with patch.object(client, "_get_client", return_value=fake_boto), \
         patch("utils.bedrock_client.time.sleep"):
        result = client.call_with_usage(
            model=SONNET_MODEL, messages=[{"role": "user", "content": "..."}],
        )

    assert result.text == '{"ok": 1}'
    assert fake_boto.converse.call_count == 2

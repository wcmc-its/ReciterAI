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
    DEFAULT_EFFORT_BY_MODEL,
    HAIKU_MODEL,
    OPUS_MODEL,
    SONNET_MODEL,
    THINKING_HEADROOM_BY_EFFORT,
    is_gen5_model,
    response_text,
    set_cost_accumulator,
)
from utils.llm_cost import CostAccumulator


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
# Per-run cost capture (set_cost_accumulator hook) — opt-in + best-effort.
# Used by backfill_spotlight to measure each publish's Bedrock spend.
# ---------------------------------------------------------------------------

@pytest.fixture
def cost_capture():
    """Activate cost capture for one test; always deactivate after."""
    acc = CostAccumulator()
    set_cost_accumulator(acc)
    try:
        yield acc
    finally:
        set_cost_accumulator(None)


def test_call_records_cost_when_accumulator_active(cost_capture):
    client = BedrockClient()
    with patch.object(client, "_call_with_retry", return_value=_happy_response("hi")):
        client.call(model=HAIKU_MODEL, messages=[{"role": "user", "content": "..."}])
    assert cost_capture.call_count == 1
    assert cost_capture.total_usd > 0
    assert cost_capture.total_input_tokens == 100
    assert cost_capture.total_output_tokens == 10
    assert HAIKU_MODEL in cost_capture.by_model


def test_call_records_content_filtered_input_cost(cost_capture):
    """A content-filtered call still billed its input tokens — record them
    even though call() then raises BedrockEmptyContentError."""
    client = BedrockClient()
    with patch.object(client, "_call_with_retry", return_value=_content_filtered_response()):
        with pytest.raises(BedrockEmptyContentError):
            client.call(model=HAIKU_MODEL, messages=[{"role": "user", "content": "..."}])
    assert cost_capture.call_count == 1
    assert cost_capture.total_input_tokens == 1309


def test_cost_capture_is_noop_when_not_activated():
    """With no accumulator set, call() must not error and must not track."""
    set_cost_accumulator(None)
    client = BedrockClient()
    with patch.object(client, "_call_with_retry", return_value=_happy_response("hi")):
        text = client.call(model=HAIKU_MODEL, messages=[{"role": "user", "content": "..."}])
    assert text == "hi"


def test_cost_capture_never_raises_on_unknown_model(cost_capture):
    """An unpriced model must not abort the Bedrock call — capture is swallowed,
    nothing recorded, and the call still returns normally."""
    client = BedrockClient()
    with patch.object(client, "_call_with_retry", return_value=_happy_response("ok")):
        text = client.call(
            model="us.anthropic.not-a-real-model",
            messages=[{"role": "user", "content": "..."}],
        )
    assert text == "ok"
    assert cost_capture.call_count == 0


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


def test_call_with_usage_raises_on_whitespace_only_text():
    """Non-empty content_blocks whose text is empty/whitespace is functionally
    a content-filter equivalent: the lenient JSON parser can't recover. Treat
    it as the same error path so call_with_fallback's transient-empty retry
    fires (2026-05-20 11:00 UTC tick: PMID 42119587 impact call returned text
    that was effectively empty after Bedrock truncated mid-response, dooming
    the 36-PMID delta until this catch was added)."""
    client = BedrockClient()
    for whitespace in ("", "   ", "\n\n", "\t \n"):
        resp = {
            "output": {"message": {"role": "assistant", "content": [{"text": whitespace}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 100, "outputTokens": 0},
        }
        with patch.object(client, "_call_with_retry", return_value=resp):
            with pytest.raises(BedrockEmptyContentError) as exc_info:
                client.call_with_usage(
                    model=SONNET_MODEL, messages=[{"role": "user", "content": "..."}],
                )
        assert exc_info.value.stop_reason == "end_turn"
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


def test_translate_messages_appends_cachepoint_only_when_opted_in():
    """cache_system=True adds a Bedrock cachePoint after the system text (so the
    prefix is prompt-cached); default keeps the plain single-block system list."""
    client = BedrockClient()
    msgs = [{"role": "user", "content": "hello"}]

    _, plain = client._translate_messages(msgs, system="SYS")
    assert plain == [{"text": "SYS"}]   # no cachePoint by default

    _, cached = client._translate_messages(msgs, system="SYS", cache_system=True)
    assert cached == [{"text": "SYS"}, {"cachePoint": {"type": "default"}}]

    # No system text -> nothing to cache, no stray cachePoint block.
    _, none = client._translate_messages(msgs, system=None, cache_system=True)
    assert none == []


# ---------------------------------------------------------------------------
# Claude 5.x request shape: no sampling params, central effort, thinking
# headroom, and text read past reasoning blocks.
# ---------------------------------------------------------------------------

_OLD_SONNET = "us.anthropic.claude-sonnet-4-6"


def _sent_request(model, **kw):
    """Run one call through _call_with_retry against a fake boto3 client and
    return the kwargs it sent to converse()."""
    client = BedrockClient()
    fake = MagicMock()
    fake.converse.return_value = _happy_response()
    client._client = fake
    client._call_with_retry(
        model=model,
        messages_converse=[{"role": "user", "content": [{"text": "hi"}]}],
        system_list=[],
        max_tokens=kw.pop("max_tokens", 400),
        temperature=kw.pop("temperature", 0.0),
        **kw,
    )
    return fake.converse.call_args.kwargs


def _reasoning_block():
    return {"reasoningContent": {"reasoningText": {"text": "thinking", "signature": "sig"}}}


def test_model_constants_are_the_5_5_profiles():
    assert HAIKU_MODEL == "us.anthropic.claude-haiku-5-5"
    assert SONNET_MODEL == "us.anthropic.claude-sonnet-5-5"
    assert OPUS_MODEL == "us.anthropic.claude-opus-5-5"
    assert all(is_gen5_model(m) for m in (HAIKU_MODEL, SONNET_MODEL, OPUS_MODEL))
    assert not is_gen5_model(_OLD_SONNET)
    assert not is_gen5_model("us.anthropic.claude-haiku-4-5-20251001-v1:0")


def test_central_effort_choice():
    assert DEFAULT_EFFORT_BY_MODEL == {HAIKU_MODEL: "low", SONNET_MODEL: "low", OPUS_MODEL: "high"}


@pytest.mark.parametrize("model", [HAIKU_MODEL, SONNET_MODEL, OPUS_MODEL])
def test_gen5_request_drops_temperature_and_adds_effort_and_headroom(model):
    sent = _sent_request(model, max_tokens=400, temperature=0.0)
    effort = DEFAULT_EFFORT_BY_MODEL[model]
    assert sent["inferenceConfig"] == {"maxTokens": 400 + THINKING_HEADROOM_BY_EFFORT[effort]}
    assert sent["additionalModelRequestFields"] == {"output_config": {"effort": effort}}


def test_caller_output_config_wins():
    sent = _sent_request(SONNET_MODEL,
                         additional_model_request_fields={"output_config": {"effort": "medium"}})
    assert sent["additionalModelRequestFields"] == {"output_config": {"effort": "medium"}}
    assert sent["inferenceConfig"] == {"maxTokens": 400 + THINKING_HEADROOM_BY_EFFORT["medium"]}


def test_thinking_disabled_request_is_left_as_the_caller_sent_it():
    fields = {"thinking": {"type": "disabled"}}
    sent = _sent_request(HAIKU_MODEL, temperature=None, additional_model_request_fields=fields)
    assert sent["inferenceConfig"] == {"maxTokens": 400}
    assert sent["additionalModelRequestFields"] == {"thinking": {"type": "disabled"}}
    assert fields == {"thinking": {"type": "disabled"}}   # caller's dict not mutated


def test_old_model_request_is_unchanged():
    """Rollback safety: a pre-5.x model ID keeps temperature and gets no extra fields."""
    sent = _sent_request(_OLD_SONNET, max_tokens=400, temperature=0.0)
    assert sent["inferenceConfig"] == {"maxTokens": 400, "temperature": 0.0}
    assert "additionalModelRequestFields" not in sent


def test_call_skips_a_leading_reasoning_block():
    resp = _happy_response('{"0": 0.85}')
    resp["output"]["message"]["content"].insert(0, _reasoning_block())
    client = BedrockClient()
    with patch.object(client, "_call_with_retry", return_value=resp):
        text = client.call(model=SONNET_MODEL, messages=[{"role": "user", "content": "..."}])
    assert text == '{"0": 0.85}'
    with patch.object(client, "_call_with_retry", return_value=resp):
        result = client.call_with_usage(model=SONNET_MODEL, messages=[{"role": "user", "content": "..."}])
    assert result.text == '{"0": 0.85}'


def test_reasoning_only_reply_raises_structured_error():
    resp = _happy_response()
    resp["output"]["message"]["content"] = [_reasoning_block()]
    resp["stopReason"] = "max_tokens"
    client = BedrockClient()
    msgs = [{"role": "user", "content": "..."}]
    with patch.object(client, "_call_with_retry", return_value=resp), \
            pytest.raises(BedrockEmptyContentError) as exc_info:
        client.call(model=SONNET_MODEL, messages=msgs)
    assert exc_info.value.stop_reason == "max_tokens"
    with patch.object(client, "_call_with_retry", return_value=resp), \
            pytest.raises(BedrockEmptyContentError):
        client.call_with_usage(model=SONNET_MODEL, messages=msgs)


def test_response_text_joins_text_blocks():
    resp = {"output": {"message": {"content": [_reasoning_block(), {"text": "a"}, {"text": "b"}]}}}
    assert response_text(resp) == "ab"

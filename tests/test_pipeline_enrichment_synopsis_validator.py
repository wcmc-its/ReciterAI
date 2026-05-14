"""Unit tests for the synopsis length validator (#50).

Validates the retry-and-flag behavior added 2026-05-14:
- First call returns ≤95 chars → returned cleanly.
- First call >95 chars → reinforcement retry → if retry returns ≤95, cleanly.
- Both calls >95 chars → returned with error flag, model's actual output
  preserved (NEVER truncated — see #50 / feedback_no_arbitrary_truncation).
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from pipeline_enrichment import synopsis as synopsis_mod
from pipeline_enrichment.synopsis import SYNOPSIS_MAX_CHARS, generate_synopsis


def _make_response(text: str, *, model: str = "gpt-5.1-2025-11-13",
                   prompt_tokens: int = 100, completion_tokens: int = 20):
    """Build a stand-in OpenAI ChatCompletion response object."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps({"synopsis": text})
        ))],
        model=model,
        usage=SimpleNamespace(prompt_tokens=prompt_tokens,
                              completion_tokens=completion_tokens),
    )


def test_synopsis_first_attempt_within_limit_returns_clean():
    syn = "A 70-character synopsis that fits comfortably under the limit oh yes"  # ~70 chars
    assert len(syn) <= SYNOPSIS_MAX_CHARS

    with patch.object(synopsis_mod, "call_with_retry") as mock_call, \
         patch.object(synopsis_mod, "get_default_client", return_value=object()):
        mock_call.return_value = _make_response(syn)
        result = generate_synopsis(pmid="1", title="T", abstract="A")

    assert mock_call.call_count == 1  # no retry
    assert result.synopsis == syn
    assert result.error is None
    assert result.input_tokens == 100
    assert result.output_tokens == 20


def test_synopsis_first_overrun_retries_and_succeeds():
    over = "x" * 110  # 110 chars, over the 95 limit
    under = "x" * 80
    with patch.object(synopsis_mod, "call_with_retry") as mock_call, \
         patch.object(synopsis_mod, "get_default_client", return_value=object()):
        mock_call.side_effect = [_make_response(over), _make_response(under)]
        result = generate_synopsis(pmid="2", title="T", abstract="A")

    assert mock_call.call_count == 2
    assert result.synopsis == under
    assert result.error is None
    # Token usage accumulates across both attempts.
    assert result.input_tokens == 200
    assert result.output_tokens == 40

    # The retry must have augmented the user prompt with the violation hint.
    second_user = mock_call.call_args_list[1].kwargs["user_prompt"]
    assert "previous attempt was 110 characters" in second_user
    assert "Count characters" in second_user


def test_synopsis_two_overruns_returns_violation_flag_preserves_output():
    """Both attempts overrun. The model's actual output is preserved (NOT
    truncated) and the error field flags the length violation."""
    first = "x" * 110
    second = "x" * 100  # still over 95

    with patch.object(synopsis_mod, "call_with_retry") as mock_call, \
         patch.object(synopsis_mod, "get_default_client", return_value=object()):
        mock_call.side_effect = [_make_response(first), _make_response(second)]
        result = generate_synopsis(pmid="3", title="T", abstract="A")

    assert mock_call.call_count == 2
    # Critical: actual output preserved, not truncated.
    assert result.synopsis == second
    assert len(result.synopsis) == 100  # NOT clipped to 95
    assert result.error is not None
    assert "length violation" in result.error
    assert "100 chars" in result.error
    assert "limit 95" in result.error


def test_synopsis_empty_response_short_circuits_no_retry():
    """Empty-string synopsis is a different failure mode — return immediately,
    don't burn a retry trying to shorten an empty string."""
    with patch.object(synopsis_mod, "call_with_retry") as mock_call, \
         patch.object(synopsis_mod, "get_default_client", return_value=object()):
        mock_call.return_value = _make_response("")
        result = generate_synopsis(pmid="4", title="T", abstract="A")

    assert mock_call.call_count == 1
    assert result.synopsis is None
    assert result.error == "empty synopsis in response"


def test_synopsis_api_error_returns_immediately():
    with patch.object(synopsis_mod, "call_with_retry") as mock_call, \
         patch.object(synopsis_mod, "get_default_client", return_value=object()):
        mock_call.side_effect = RuntimeError("rate limit")
        result = generate_synopsis(pmid="5", title="T", abstract="A")

    assert mock_call.call_count == 1
    assert result.synopsis is None
    assert "rate limit" in result.error


def test_synopsis_exactly_at_limit_is_accepted():
    """The boundary case: exactly 95 chars is allowed (≤ not <)."""
    exactly = "x" * SYNOPSIS_MAX_CHARS
    with patch.object(synopsis_mod, "call_with_retry") as mock_call, \
         patch.object(synopsis_mod, "get_default_client", return_value=object()):
        mock_call.return_value = _make_response(exactly)
        result = generate_synopsis(pmid="6", title="T", abstract="A")

    assert mock_call.call_count == 1
    assert result.synopsis == exactly
    assert result.error is None

"""Unit tests for the impact justification length validator (#50).

Validates the retry-and-flag behavior added 2026-05-14:
- justification ≤120 chars AND ≤10 words → returned cleanly.
- char or word overrun on first attempt → reinforcement retry.
- both attempts violate → returned with error flag, actual output preserved.

Critically replaces the prior silent-truncation block at impact.py:155-160
(removed in this PR). NEVER truncate — see #50 /
feedback_no_arbitrary_truncation.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from pipeline_enrichment import impact as impact_mod
from pipeline_enrichment.impact import (
    JUSTIF_MAX_CHARS,
    JUSTIF_MAX_WORDS,
    score_impact,
)


def _make_response(*, score: int, justification: str,
                   model: str = "gpt-5.1-2025-11-13",
                   prompt_tokens: int = 500, completion_tokens: int = 40):
    body = {"impactScore": score, "justification": justification}
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(body)))],
        model=model,
        usage=SimpleNamespace(prompt_tokens=prompt_tokens,
                              completion_tokens=completion_tokens),
    )


_PUB = {"pmid": "12345", "articleTitle": "Some title", "abstractVarchar": "Abstract."}


def test_impact_first_attempt_within_limits_returns_clean():
    j = "Solid methodology and modest impact"  # 5 words, 35 chars
    with patch.object(impact_mod, "call_with_retry") as mock_call, \
         patch.object(impact_mod, "get_default_client", return_value=object()):
        mock_call.return_value = _make_response(score=55, justification=j)
        result = score_impact(pub_data=_PUB)

    assert mock_call.call_count == 1
    assert result.impact_score == 55
    assert result.justification == j
    assert result.error is None


def test_impact_word_overrun_retries_and_succeeds():
    over = "Strong methods, clear novelty, broad applicability and excellent translational potential demonstrated"
    assert len(over.split()) > JUSTIF_MAX_WORDS
    under = "Strong methods, clear novelty, broad translational potential"  # ≤10 words

    with patch.object(impact_mod, "call_with_retry") as mock_call, \
         patch.object(impact_mod, "get_default_client", return_value=object()):
        mock_call.side_effect = [
            _make_response(score=70, justification=over),
            _make_response(score=70, justification=under),
        ]
        result = score_impact(pub_data=_PUB)

    assert mock_call.call_count == 2
    assert result.justification == under
    assert result.error is None
    # Retry must have included the violation hint and the word count.
    second_user = mock_call.call_args_list[1].kwargs["user_prompt"]
    assert "words" in second_user
    assert f"≤{JUSTIF_MAX_WORDS} words" in second_user


def test_impact_char_overrun_retries_and_succeeds():
    # 5 words but blown character budget.
    over_chars = "a" * (JUSTIF_MAX_CHARS + 10) + " final"
    assert len(over_chars) > JUSTIF_MAX_CHARS
    under = "Compact justification within both limits"

    with patch.object(impact_mod, "call_with_retry") as mock_call, \
         patch.object(impact_mod, "get_default_client", return_value=object()):
        mock_call.side_effect = [
            _make_response(score=40, justification=over_chars),
            _make_response(score=40, justification=under),
        ]
        result = score_impact(pub_data=_PUB)

    assert mock_call.call_count == 2
    assert result.justification == under
    assert result.error is None


def test_impact_two_overruns_returns_violation_flag_preserves_output():
    """Critical regression guard for #50: when retries fail, the model's
    actual output is preserved (not truncated) and the error field flags
    the violation precisely."""
    first = "Novel MRI perfusion method with strong ex vivo validation; potential influence."  # 11w
    second = "Novel imaging perfusion method with strong validation and translational promise overall today."  # also >10w

    with patch.object(impact_mod, "call_with_retry") as mock_call, \
         patch.object(impact_mod, "get_default_client", return_value=object()):
        mock_call.side_effect = [
            _make_response(score=48, justification=first),
            _make_response(score=48, justification=second),
        ]
        result = score_impact(pub_data=_PUB)

    assert mock_call.call_count == 2
    # The actual second model output is preserved verbatim.
    assert result.justification == second
    assert result.impact_score == 48
    assert result.error is not None
    assert "length violation" in result.error
    assert f"{len(second.split())} words" in result.error
    assert f"limit {JUSTIF_MAX_WORDS}" in result.error


def test_impact_no_silent_truncation_at_120_chars():
    """Regression guard: prior code at impact.py:155-160 silently truncated
    justifications >120 chars. This PR removed that. A two-attempt
    char-overrun must now surface as a flagged result, with the full
    model output intact (NOT a 120-char prefix)."""
    long_just = "x" * 200  # 1 word but 200 chars
    again = "x" * 180

    with patch.object(impact_mod, "call_with_retry") as mock_call, \
         patch.object(impact_mod, "get_default_client", return_value=object()):
        mock_call.side_effect = [
            _make_response(score=30, justification=long_just),
            _make_response(score=30, justification=again),
        ]
        result = score_impact(pub_data=_PUB)

    # Critical: length preserved, NOT clipped to 120.
    assert result.justification == again
    assert len(result.justification) == 180
    assert result.error is not None
    assert "length violation" in result.error
    assert f"{len(again)} chars" in result.error


def test_impact_score_clamped_to_0_100():
    # Mirror the POC clamping behavior.
    with patch.object(impact_mod, "call_with_retry") as mock_call, \
         patch.object(impact_mod, "get_default_client", return_value=object()):
        mock_call.return_value = _make_response(score=150, justification="ok")
        result_high = score_impact(pub_data=_PUB)
        mock_call.return_value = _make_response(score=-10, justification="ok")
        result_low = score_impact(pub_data=_PUB)

    assert result_high.impact_score == 100
    assert result_low.impact_score == 0


def test_impact_api_error_returns_immediately():
    with patch.object(impact_mod, "call_with_retry") as mock_call, \
         patch.object(impact_mod, "get_default_client", return_value=object()):
        mock_call.side_effect = RuntimeError("upstream 500")
        result = score_impact(pub_data=_PUB)

    assert mock_call.call_count == 1
    assert result.impact_score is None
    assert "upstream 500" in result.error

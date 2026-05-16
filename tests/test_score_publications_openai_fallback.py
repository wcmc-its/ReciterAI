"""Tests for OpenAI gpt-5.1 fallback on Bedrock content_filter.

Background: Anthropic Sonnet 4.6's safety filter returns empty content
for some biomedical animal-model abstracts (e.g. PMID 41198049, the
smoke 5 cohort). PR #77 surfaced this as a structured
BedrockEmptyContentError instead of an opaque IndexError. This module
verifies that the dense-scoring path now retries via OpenAI gpt-5.1
when that exception fires, that the fallback model ID is captured on
the result, and that the happy path (Sonnet succeeds) is unchanged.

Full investigation: `Projects/ReciterAI - Planning/sonnet-content-filter-on-dense-scoring.md`
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import score_publications as sp
from utils.bedrock_client import BedrockEmptyContentError, SONNET_MODEL


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

TAXONOMY = {
    "taxonomy_version": "taxonomy_v2",
    "topics": [
        {"id": "topic_a", "label": "Topic A", "description": "desc A"},
        {"id": "topic_b", "label": "Topic B", "description": "desc B"},
    ],
}
INT_TO_ID = {"0": "topic_a", "1": "topic_b"}
ID_TO_INT = {"topic_a": "0", "topic_b": "1"}


def _make_pub(pmid="41198049"):
    return {
        "pmid": pmid,
        "synopsis": "Amoxicillin improves C. bovis signs in NSG mice",
        "abstract": "Infection with Corynebacterium bovis...",
    }


def _make_openai_completion(payload: dict):
    """Build a minimal mock OpenAI ChatCompletion shape."""
    completion = MagicMock()
    completion.choices = [MagicMock()]
    completion.choices[0].message.content = json.dumps(payload)
    return completion


class _FakeBedrockSuccessThenSonnetSucceeds:
    """Bedrock that screens fine and dense-scores fine. Happy path."""

    def __init__(self):
        self.calls = []

    def call_json(self, model, messages, **kwargs):
        self.calls.append(model)
        if model == sp.HAIKU_MODEL:
            return {"0": 0.85, "1": 0.45}
        if model == SONNET_MODEL:
            return {
                "0": {"score": 0.9, "rationale": "Primary focus on topic A"},
                "1": {"score": 0.5, "rationale": "Secondary mention"},
            }
        raise RuntimeError(f"Unexpected model: {model}")


class _FakeBedrockScreenOKDenseFilters:
    """Bedrock that screens fine but content-filters on dense (Sonnet)."""

    def __init__(self):
        self.calls = []

    def call_json(self, model, messages, **kwargs):
        self.calls.append(model)
        if model == sp.HAIKU_MODEL:
            return {"0": 0.85, "1": 0.45}
        if model == SONNET_MODEL:
            raise BedrockEmptyContentError(
                stop_reason="content_filtered", model=SONNET_MODEL,
            )
        raise RuntimeError(f"Unexpected model: {model}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_sonnet_succeeds_no_fallback_invoked(monkeypatch):
    """Happy path: Sonnet returns valid dense scores; OpenAI is never called."""
    monkeypatch.setattr(sp, "mark_processing", lambda *a, **k: None)
    bedrock = _FakeBedrockSuccessThenSonnetSucceeds()
    openai_called = MagicMock(side_effect=AssertionError("OpenAI must not be called on Sonnet success"))

    with patch.object(sp, "openai_call_with_retry", openai_called):
        result = sp.score_one_publication(
            _make_pub(), bedrock, TAXONOMY,
            MagicMock(), "reciterai", INT_TO_ID, ID_TO_INT,
        )

    assert result.status == "complete"
    assert result.fallback_model is None
    assert result.dense_scores
    assert openai_called.call_count == 0


def test_content_filter_falls_back_to_openai(monkeypatch):
    """Sonnet raises BedrockEmptyContentError; OpenAI gpt-5.1 produces dense scores."""
    monkeypatch.setattr(sp, "mark_processing", lambda *a, **k: None)
    bedrock = _FakeBedrockScreenOKDenseFilters()

    fake_openai_payload = {
        "0": {"score": 0.92, "rationale": "Antibiotic study"},
        "1": {"score": 0.55, "rationale": "Gut microbiota mention"},
    }
    fake_completion = _make_openai_completion(fake_openai_payload)
    fake_openai_client = MagicMock()

    with patch.object(sp, "get_openai_client", return_value=fake_openai_client), \
         patch.object(sp, "openai_call_with_retry", return_value=fake_completion) as openai_mock:
        result = sp.score_one_publication(
            _make_pub(), bedrock, TAXONOMY,
            MagicMock(), "reciterai", INT_TO_ID, ID_TO_INT,
        )

    assert result.status == "complete"
    assert result.fallback_model == sp.OPENAI_FALLBACK_MODEL == "gpt-5.1"
    assert result.dense_scores
    assert result.dense_scores["topic_a"]["score"] == 0.92
    assert result.dense_scores["topic_a"]["rationale"] == "Antibiotic study"
    assert result.dense_scores["topic_b"]["score"] == 0.55
    # OpenAI invoked exactly once with gpt-5.1 and the JSON-object format
    assert openai_mock.call_count == 1
    call_kwargs = openai_mock.call_args.kwargs
    assert call_kwargs["model"] == "gpt-5.1"
    assert call_kwargs["response_format"] == {"type": "json_object"}


def test_openai_fallback_strips_markdown_fences(monkeypatch):
    """gpt-5.1 occasionally returns ```json ... ``` fences; helper must parse anyway."""
    monkeypatch.setattr(sp, "mark_processing", lambda *a, **k: None)
    bedrock = _FakeBedrockScreenOKDenseFilters()

    fenced = '```json\n{"0": {"score": 0.7, "rationale": "fenced"}, "1": {"score": 0.3, "rationale": "fenced"}}\n```'
    fake_completion = MagicMock()
    fake_completion.choices = [MagicMock()]
    fake_completion.choices[0].message.content = fenced

    with patch.object(sp, "get_openai_client", return_value=MagicMock()), \
         patch.object(sp, "openai_call_with_retry", return_value=fake_completion):
        result = sp.score_one_publication(
            _make_pub(), bedrock, TAXONOMY,
            MagicMock(), "reciterai", INT_TO_ID, ID_TO_INT,
        )

    assert result.status == "complete"
    assert result.fallback_model == "gpt-5.1"
    assert result.dense_scores["topic_a"]["score"] == 0.7


def test_openai_fallback_failure_marks_pmid_failed(monkeypatch):
    """If OpenAI ALSO fails after Sonnet content-filter, the PMID marks failed
    with a structured error (no opaque crash) and no dense_scores."""
    monkeypatch.setattr(sp, "mark_processing", lambda *a, **k: None)
    bedrock = _FakeBedrockScreenOKDenseFilters()

    with patch.object(sp, "get_openai_client", return_value=MagicMock()), \
         patch.object(sp, "openai_call_with_retry", side_effect=RuntimeError("openai temporarily unavailable")):
        result = sp.score_one_publication(
            _make_pub(), bedrock, TAXONOMY,
            MagicMock(), "reciterai", INT_TO_ID, ID_TO_INT,
        )

    assert result.status == "failed"
    assert result.dense_scores == {}
    assert "openai temporarily unavailable" in result.error
    assert result.fallback_model is None  # Never set — fallback didn't complete


def test_openai_fallback_malformed_json_marks_pmid_failed(monkeypatch):
    """OpenAI returns content that isn't valid JSON after fence-stripping;
    PMID marks failed with JSONDecodeError as error_code."""
    monkeypatch.setattr(sp, "mark_processing", lambda *a, **k: None)
    bedrock = _FakeBedrockScreenOKDenseFilters()

    fake_completion = MagicMock()
    fake_completion.choices = [MagicMock()]
    fake_completion.choices[0].message.content = "not even close to JSON"

    with patch.object(sp, "get_openai_client", return_value=MagicMock()), \
         patch.object(sp, "openai_call_with_retry", return_value=fake_completion):
        result = sp.score_one_publication(
            _make_pub(), bedrock, TAXONOMY,
            MagicMock(), "reciterai", INT_TO_ID, ID_TO_INT,
        )

    assert result.status == "failed"
    assert result.fallback_model is None


def test_serialize_results_surfaces_fallback_model():
    """When fallback was used, the per-PMID output dict carries fallback_model;
    when Sonnet handled the call, the key is absent (no false positive)."""
    r1 = sp.ScoringResult(pmid="1", synopsis="", abstract="")
    r1.status = "complete"
    r1.dense_scores = {"t1": {"score": 0.8, "rationale": "x"}}
    r1.fallback_model = "gpt-5.1"

    r2 = sp.ScoringResult(pmid="2", synopsis="", abstract="")
    r2.status = "complete"
    r2.dense_scores = {"t1": {"score": 0.7, "rationale": "y"}}
    # no fallback_model set

    out = sp.serialize_results([r1, r2])
    assert len(out) == 2
    by_pmid = {entry["pmid"]: entry for entry in out}
    assert by_pmid["1"]["fallback_model"] == "gpt-5.1"
    assert "fallback_model" not in by_pmid["2"]


def test_mark_processing_complete_records_fallback_model(monkeypatch):
    """The DDB PROCESSING# write on success carries fallback_model when set,
    so downstream queries can identify fallback-scored PMIDs."""
    captured_calls = []
    monkeypatch.setattr(
        sp, "mark_processing",
        lambda *a, **kw: captured_calls.append((a, kw)),
    )

    bedrock = _FakeBedrockScreenOKDenseFilters()
    fake_completion = _make_openai_completion({
        "0": {"score": 0.9, "rationale": "ok"},
        "1": {"score": 0.4, "rationale": "ok"},
    })

    with patch.object(sp, "get_openai_client", return_value=MagicMock()), \
         patch.object(sp, "openai_call_with_retry", return_value=fake_completion):
        sp.score_one_publication(
            _make_pub(), bedrock, TAXONOMY,
            MagicMock(), "reciterai", INT_TO_ID, ID_TO_INT,
        )

    # mark_processing called multiple times; find the 'complete' call
    complete_calls = [
        (a, kw) for (a, kw) in captured_calls
        if len(a) >= 4 and a[3] == "complete"
    ]
    assert len(complete_calls) >= 1
    a, kw = complete_calls[-1]
    assert kw.get("fallback_model") == "gpt-5.1"

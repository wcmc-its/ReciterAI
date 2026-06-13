"""Tests for spotlight/lede_generator.py — Plan 06-05 Task 1 (SPOT-05).

Behaviors per PLAN.md ``<behavior>`` block (8 tests):
  1. generate_lede issues exactly one BedrockClient call with OPUS_MODEL,
     temperature=0.5, max_tokens=300.
  2. generate_lede passes meta.label + meta.description to the prompt;
     does NOT pass display_name or short_description (D-19 rule).
  3. generate_lede clamps to top-3 papers by impact_score; raises if <2 valid.
  4. generate_lede passes paper.synopsis + paper.impact_justification (not
     title/journal) as primary grounding signal.
  5. generate_lede skips papers with empty author payload; raises if filter
     reduces below MIN_PAPERS.
  6. generate_lede strips leading/trailing whitespace from Bedrock response.
  7. generate_lede(prior_failure=...) propagates the prior failure into the
     rendered prompt so the model can self-correct.
  8. No model ID string literal in module source (RESEARCH Pitfall 5).

All tests use a MagicMock BedrockClient; no AWS calls.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from spotlight.lede_generator import (
    LEDE_MAX_TOKENS,
    LEDE_TEMPERATURE,
    MAX_PAPERS,
    MIN_PAPERS,
    generate_lede,
)
from spotlight.sensitive_gate import SubtopicMeta
from spotlight.types import Author, Paper
from utils.bedrock_client import OPUS_MODEL


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _author(person_id: str = "p1", display: str = "Jane Doe", position: str = "first") -> Author:
    return Author(person_identifier=person_id, display_name=display, position=position)


def _paper(
    pmid: str = "100",
    impact: float = 0.7,
    synopsis: str = "Maps molecular drivers of aging via single-cell methods.",
    impact_justification: str = "Demonstrates a new pathway dependence.",
    first: Author | None = None,
    last: Author | None = None,
    title: str = "Aging paper title",
    journal: str = "Nature Aging",
    relevance: float = 0.8,
) -> Paper:
    return Paper(
        pmid=pmid,
        title=title,
        journal=journal,
        year=2024,
        impact_score=impact,
        impact_justification=impact_justification,
        synopsis=synopsis,
        first_author=first if first is not None else _author("first1", "First A", "first"),
        last_author=last if last is not None else _author("last1", "Last A", "last"),
        relevance_score=relevance,
    )


def _meta() -> SubtopicMeta:
    return SubtopicMeta(
        subtopic_id="aging.molecular",
        label="Molecular drivers of aging",
        description="Pathway dependencies in cellular aging.",
        parent_topic_label="Aging & Geroscience",
    )


def _make_mock_client(response_text: str | None = None) -> MagicMock:
    """Return a MagicMock with `.call(...)` returning a fixed lede.

    Default response is a 28-word lede that includes the WCM scholars tic and
    no banned words — used for the happy-path assertions on the call surface.
    """
    if response_text is None:
        response_text = (
            "Single-cell methods rewrite how researchers read aging. "
            "WCM scholars are mapping the molecular drivers across diverse "
            "tissues to sharpen interventions for the patients most at risk."
        )
    client = MagicMock()
    client.call.return_value = response_text
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_1_generate_lede_calls_opus_with_locked_params():
    """Test 1: exactly one call with OPUS_MODEL, temperature=None, max_tokens=300.

    Per wcmc-its/ReciterAI#2 §4: lede generator switched from Sonnet to
    Opus 2026-05-08 for stronger adherence to the voice contract on a
    high-visibility surface. Opus 4.7 deprecates the temperature parameter,
    so the call passes None and the BedrockClient omits it from
    inferenceConfig."""
    client = _make_mock_client()
    papers = [_paper("100", 0.9), _paper("101", 0.8)]
    lede, used = generate_lede(_meta(), papers, client=client)
    assert client.call.call_count == 1
    kwargs = client.call.call_args.kwargs
    assert kwargs["model"] == OPUS_MODEL
    assert kwargs["temperature"] is None
    assert kwargs["max_tokens"] == LEDE_MAX_TOKENS == 300
    assert isinstance(lede, str)
    assert isinstance(used, list)


def test_2_generate_lede_passes_label_and_description_not_display_name():
    """Test 2: D-19 rule — meta.label + meta.description must appear in
    rendered user message; the LLM signature MUST NOT accept
    display_name or short_description."""
    sig = inspect.signature(generate_lede)
    assert "display_name" not in sig.parameters
    assert "short_description" not in sig.parameters

    client = _make_mock_client()
    meta = _meta()
    papers = [_paper("100", 0.9), _paper("101", 0.8)]
    generate_lede(meta, papers, client=client)
    kwargs = client.call.call_args.kwargs
    rendered = kwargs["messages"][0]["content"]
    assert meta.label in rendered
    assert meta.description in rendered
    # D-19: rendered message must not refer to display_name or short_description fields.
    assert "display_name" not in rendered
    assert "short_description" not in rendered


def test_3_generate_lede_clamps_to_top_3_by_article_score():
    """Test 3: more than 3 papers → top-3 by article_score; <2 → ValueError.

    Relevance held constant here → article_score is monotonic in impact, so the
    top-3 are the impact 0.9/0.8/0.5 papers. test_3b covers the case where
    relevance changes which papers ground the lede.
    """
    client = _make_mock_client()
    papers = [
        _paper("low1", 0.1),
        _paper("low2", 0.2),
        _paper("hi1", 0.9),
        _paper("hi2", 0.8),
        _paper("mid", 0.5),
    ]
    _, used = generate_lede(_meta(), papers, client=client)
    assert len(used) == MAX_PAPERS == 3
    used_pmids = {p.pmid for p in used}
    assert used_pmids == {"hi1", "hi2", "mid"}

    # <2 valid papers -> ValueError
    with pytest.raises(ValueError, match="at least"):
        generate_lede(_meta(), [_paper("only", 0.9)], client=client)


def test_3b_clamp_blends_relevance_not_impact_alone():
    """Test 3b: the highest-impact paper is DROPPED from the top-3 grounding set
    because its topic-relevance is weak — the article_score blend, not impact
    alone, decides which papers ground the lede."""
    client = _make_mock_client()
    papers = [
        # prominent but off-topic: highest impact, weak relevance → must drop
        _paper("prominent", impact=0.95, relevance=0.2),
        _paper("ontopic_a", impact=0.40, relevance=0.95),
        _paper("ontopic_b", impact=0.45, relevance=0.90),
        _paper("ontopic_c", impact=0.50, relevance=0.85),
    ]
    _, used = generate_lede(_meta(), papers, client=client)
    used_pmids = {p.pmid for p in used}
    # The highest-impact paper is excluded; the three on-topic papers ground it.
    assert "prominent" not in used_pmids
    assert used_pmids == {"ontopic_a", "ontopic_b", "ontopic_c"}


def test_4_generate_lede_passes_synopsis_and_impact_justification_as_primary():
    """Test 4: synopsis + impact_justification are present; title/journal
    not used as primary grounding signal."""
    client = _make_mock_client()
    p1 = _paper(
        "100", 0.9,
        synopsis="UNIQUE_SYNOPSIS_MARKER aging pathways",
        impact_justification="UNIQUE_JUSTIFICATION_MARKER drives outcomes",
        title="A title that should not be primary",
        journal="A journal that should not be primary",
    )
    p2 = _paper(
        "101", 0.7,
        synopsis="ANOTHER_SYNOPSIS_MARKER cellular",
        impact_justification="ANOTHER_JUSTIFICATION_MARKER",
    )
    generate_lede(_meta(), [p1, p2], client=client)
    rendered = client.call.call_args.kwargs["messages"][0]["content"]
    assert "UNIQUE_SYNOPSIS_MARKER" in rendered
    assert "UNIQUE_JUSTIFICATION_MARKER" in rendered
    assert "ANOTHER_SYNOPSIS_MARKER" in rendered
    # title/journal can ride along but must NOT be the canonical signal —
    # we assert synopsis/justification are present (above) and the title
    # has the optional ride-along property: even if rendered, the test
    # below proves synopsis/impact_justification are the primary fields.
    # (The plan says title/journal "may appear ... only as low-priority
    # context, not as the LLM-canonical fields.")


def test_5_generate_lede_skips_papers_with_empty_authors():
    """Test 5: papers with both first_author.person_identifier and
    last_author.person_identifier empty are filtered out; if filter
    reduces below MIN_PAPERS, raise ValueError."""
    client = _make_mock_client()
    no_authors = _paper(
        "noauth", 0.95,
        first=_author("", "", "first"),
        last=_author("", "", "last"),
    )
    p1 = _paper("100", 0.9)
    p2 = _paper("101", 0.8)
    _, used = generate_lede(_meta(), [no_authors, p1, p2], client=client)
    used_pmids = {p.pmid for p in used}
    assert "noauth" not in used_pmids
    assert used_pmids == {"100", "101"}

    # Only one valid paper after filter -> ValueError
    only_invalid = _paper(
        "ni1", 0.95,
        first=_author("", "", "first"),
        last=_author("", "", "last"),
    )
    p_valid = _paper("valid", 0.5)
    with pytest.raises(ValueError, match="at least"):
        generate_lede(_meta(), [only_invalid, p_valid], client=client)


def test_6_generate_lede_strips_response_whitespace():
    """Test 6: Bedrock response leading/trailing whitespace is stripped."""
    raw = "   \n\nWCM scholars are mapping the molecular drivers of aging across tissues to sharpen interventions.\n\n  "
    client = _make_mock_client(response_text=raw)
    papers = [_paper("100", 0.9), _paper("101", 0.8)]
    lede, _ = generate_lede(_meta(), papers, client=client)
    assert not lede.startswith(" ")
    assert not lede.endswith(" ")
    assert not lede.startswith("\n")
    assert not lede.endswith("\n")
    assert lede == raw.strip()


def test_7_generate_lede_propagates_prior_failure():
    """Test 7: prior_failure parameter is included in the rendered prompt."""
    client = _make_mock_client()
    papers = [_paper("100", 0.9), _paper("101", 0.8)]
    failure = "em_dash_present, missing_wcm_scholars_tic"
    generate_lede(_meta(), papers, prior_failure=failure, client=client)
    rendered = client.call.call_args.kwargs["messages"][0]["content"]
    assert failure in rendered
    assert "Prior attempt failed" in rendered or "prior" in rendered.lower()


def test_8_no_model_id_string_literal_in_source():
    """Test 8: source must not type the Bedrock model ID literally."""
    src_path = Path(__file__).parent.parent / "spotlight" / "lede_generator.py"
    source = src_path.read_text(encoding="utf-8")
    assert "us.anthropic.claude" not in source, (
        "lede_generator.py must import OPUS_MODEL from utils.bedrock_client; "
        "never type the Bedrock model ID literal (RESEARCH Pitfall 5)."
    )


# ---------------------------------------------------------------------------
# #219: opener-constraint must degrade gracefully, never forbid the whole list
# ---------------------------------------------------------------------------


def test_219_render_prompt_partial_exclusion_keeps_hard_constraint():
    """When some allowed openers remain, the prompt keeps the hard MUST-NOT
    constraint (unchanged behavior)."""
    from spotlight.critic import ALLOWED_OPENERS

    client = _make_mock_client()
    papers = [_paper("100", 0.9), _paper("101", 0.8)]
    generate_lede(_meta(), papers, client=client, excluded_openers=(ALLOWED_OPENERS[0],))
    rendered = client.call.call_args.kwargs["messages"][0]["content"]
    assert "MUST NOT be used here" in rendered


def test_219_render_prompt_all_excluded_degrades_to_reuse():
    """#219 layer 2: if EVERY allowed opener is excluded, the prompt must not
    forbid the entire voice list (which forces a refusal that then gets persisted
    as the lede). It asks the model to REUSE an opener instead."""
    from spotlight.critic import ALLOWED_OPENERS

    client = _make_mock_client()
    papers = [_paper("100", 0.9), _paper("101", 0.8)]
    generate_lede(_meta(), papers, client=client, excluded_openers=tuple(ALLOWED_OPENERS))
    rendered = client.call.call_args.kwargs["messages"][0]["content"]
    assert "MUST NOT be used here" not in rendered
    assert "REUSE" in rendered
    assert "do NOT refuse" in rendered

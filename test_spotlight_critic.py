"""Tests for spotlight/critic.py — Plan 06-05 Task 2 (SPOT-06 + SPOT-07).

Behaviors per PLAN.md ``<behavior>`` block (15 tests):
  1. dead word "novel" -> failed
  2. em-dash U+2014 rejected
  3. en-dash U+2013 rejected
  4. time-bound "currently" rejected
  5. marketing word "pioneering" rejected
  6. too-short lede (<22 words) rejected
  7. too-long lede (>38 words) rejected
  8. missing "WCM scholars are X-ing" tic rejected
  9. clean valid lede passes
 10. TIC_RE is case-sensitive (lowercase "wcm" doesn't match)
 11. run_critic_loop early-exit on first-attempt pass (1 generate, 1 llm)
 12. run_critic_loop persistent failure -> 4 generate calls + write_review_entry
 13. run_critic_loop persistent failure -> regen_count=3, flag_reason="critic"
 14. run_critic_loop deterministic-fail-then-pass returns status="pass" attempts.length=2
 15. prompts/spotlight_critic_v0.md exists with fenced markdown body

All tests use injected mocks; no Bedrock or DynamoDB calls.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from spotlight.critic import (
    CRITIC_MAX_TOKENS,
    CRITIC_TEMPERATURE,
    DEAD_WORDS_RE,
    EM_DASH_RE,
    LENGTH_MAX,
    LENGTH_MIN,
    MARKETING_RE,
    MAX_RETRIES,
    TIC_RE,
    TIME_BOUND_RE,
    DeterministicVerdict,
    LLMVerdict,
    ValidatedLede,
    run_critic_loop,
    run_deterministic_checks,
    run_llm_critic,
)
from spotlight.sensitive_gate import SubtopicMeta
from spotlight.types import Author, Paper


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _author(person_id: str = "p1", display: str = "Jane Doe", position: str = "first") -> Author:
    return Author(person_identifier=person_id, display_name=display, position=position)


def _paper(pmid: str = "100", impact: float = 0.7) -> Paper:
    return Paper(
        pmid=pmid,
        title="Aging paper",
        journal="Nature Aging",
        year=2024,
        impact_score=impact,
        impact_justification="Demonstrates a new pathway dependence.",
        synopsis="Maps molecular drivers of aging via single-cell methods.",
        first_author=_author("first1"),
        last_author=_author("last1", position="last"),
    )


def _meta() -> SubtopicMeta:
    return SubtopicMeta(
        subtopic_id="aging.molecular",
        label="Molecular drivers of aging",
        description="Pathway dependencies in cellular aging.",
        parent_topic_label="Aging & Geroscience",
    )


# A 28-word lede that satisfies the regex bundle (passes deterministic checks).
CLEAN_LEDE = (
    "Single-cell methods rewrite how researchers read the biology of aging. "
    "WCM scholars are mapping the molecular drivers across diverse tissues "
    "to sharpen interventions for patients."
)


# ---------------------------------------------------------------------------
# Tests 1-10: deterministic regex bundle
# ---------------------------------------------------------------------------


def test_1_deterministic_rejects_dead_word_novel():
    bad = (
        "WCM scholars are mapping aging mechanisms across diverse cell "
        "populations using novel single-cell techniques to chart pathway "
        "dynamics in this twenty-eight word lede with extra padding words."
    )
    v = run_deterministic_checks(bad)
    assert v.passed is False
    # The dead-word constraint fires; the test assertion is on the
    # presence of the dead-word marker.
    joined = " ".join(v.failed_constraints)
    assert "dead_word" in joined or "dead" in joined


def test_2_deterministic_rejects_em_dash_u2014():
    bad = (
        "WCM scholars are mapping — aging mechanisms across diverse cell "
        "populations using single-cell techniques to chart pathway dynamics "
        "for the patients most at risk in this lede."
    )
    v = run_deterministic_checks(bad)
    assert v.passed is False
    assert "em_dash_present" in v.failed_constraints


def test_3_deterministic_rejects_en_dash_u2013():
    bad = (
        "WCM scholars are mapping – aging mechanisms across diverse cell "
        "populations using single-cell techniques to chart pathway dynamics "
        "for the patients most at risk in this lede."
    )
    v = run_deterministic_checks(bad)
    assert v.passed is False
    assert "em_dash_present" in v.failed_constraints


def test_4_deterministic_rejects_time_bound_currently():
    bad = (
        "Currently, WCM scholars are mapping aging mechanisms across diverse "
        "cell populations using single-cell techniques to chart pathway "
        "dynamics for the patients most at risk in this lede."
    )
    v = run_deterministic_checks(bad)
    assert v.passed is False
    assert "time_bound_language" in v.failed_constraints


def test_5_deterministic_rejects_marketing_pioneering():
    bad = (
        "WCM scholars are pioneering aging mechanisms across diverse cell "
        "populations using single-cell techniques to chart pathway dynamics "
        "for the patients most at risk in this lede."
    )
    v = run_deterministic_checks(bad)
    assert v.passed is False
    assert "marketing_language" in v.failed_constraints


def test_6_deterministic_rejects_too_short():
    bad = "WCM scholars are mapping aging across tissues to sharpen interventions."
    v = run_deterministic_checks(bad)
    assert v.passed is False
    assert any(c.startswith("length_out_of_band:") for c in v.failed_constraints)
    # word_count is 10 here — assert it's below LENGTH_MIN.
    assert v.word_count < LENGTH_MIN
    # The length tag carries the count.
    assert f"length_out_of_band:{v.word_count}" in v.failed_constraints


def test_7_deterministic_rejects_too_long():
    bad = (
        "WCM scholars are mapping " + ("aging " * 50) + "tissues."
    )
    v = run_deterministic_checks(bad)
    assert v.passed is False
    assert any(c.startswith("length_out_of_band:") for c in v.failed_constraints)
    assert v.word_count > LENGTH_MAX
    assert f"length_out_of_band:{v.word_count}" in v.failed_constraints


def test_8_deterministic_rejects_missing_tic():
    bad = (
        "Researchers are studying aging mechanisms across diverse cell "
        "populations using single-cell techniques to chart pathway dynamics "
        "for the patients most at risk in this lede sentence."
    )
    v = run_deterministic_checks(bad)
    assert v.passed is False
    assert "missing_wcm_scholars_tic" in v.failed_constraints


def test_9_deterministic_accepts_clean_lede():
    v = run_deterministic_checks(CLEAN_LEDE)
    assert v.passed is True, f"unexpected failures: {v.failed_constraints}"
    assert v.failed_constraints == ()
    assert LENGTH_MIN <= v.word_count <= LENGTH_MAX


def test_10_tic_re_is_case_sensitive():
    """Lowercase 'wcm scholars' must NOT match TIC_RE."""
    bad = (
        "Single-cell methods rewrite how researchers read the biology. "
        "wcm scholars are mapping the molecular drivers across diverse "
        "tissues to sharpen interventions for the patients."
    )
    v = run_deterministic_checks(bad)
    assert v.passed is False
    assert "missing_wcm_scholars_tic" in v.failed_constraints


# ---------------------------------------------------------------------------
# Tests 10a-10d: meta-language + preamble + cherry-pick (issues #1, #2)
# ---------------------------------------------------------------------------


def test_10a_deterministic_rejects_meta_language_papers_provided():
    """'The papers provided' phrasing leaks model input metadata into
    the lede. Per wcmc-its/ReciterAI#2 §2."""
    bad = (
        "The papers provided map sex-based gaps in cardiac surgery mortality. "
        "WCM scholars are tracing which disparities are modifiable and what "
        "structural changes would close them for patients most at risk."
    )
    v = run_deterministic_checks(bad)
    assert v.passed is False
    assert any(c.startswith("meta_language:") for c in v.failed_constraints)


def test_10b_deterministic_rejects_preamble_chain_of_thought():
    """Lede starting with 'Looking at the provided papers, I notice...' is
    chain-of-thought leakage. Per wcmc-its/ReciterAI#1."""
    bad = (
        "Looking at the provided papers, I notice the papers cover sex and "
        "gender disparities. WCM scholars are tracing which disparities are "
        "modifiable for the patients most at risk in those populations."
    )
    v = run_deterministic_checks(bad)
    assert v.passed is False
    assert "preamble_chain_of_thought" in v.failed_constraints


def test_10c_deterministic_rejects_cherry_picked_pairs():
    """'from {mechanism} in {disease} to/and {mechanism} in {disease}' reads
    as exclusionary to faculty in adjacent areas. Per wcmc-its/ReciterAI#2 §1."""
    bad = (
        "Targeted therapies can silence a driver mutation, yet tumors find "
        "detours, from alveolar cell reprogramming in lung cancer to "
        "translational rewiring in prostate cancer. WCM scholars are tracing "
        "the resistance circuits."
    )
    v = run_deterministic_checks(bad)
    assert v.passed is False
    assert "cherry_picked_mechanism_disease_pairs" in v.failed_constraints


def test_10d_deterministic_rejects_multiple_new_violations():
    """A lede that hits preamble + meta-language + cherry-pick should
    accumulate ALL three failure markers (verdict.failed is order-agnostic)."""
    bad = (
        "Looking at the papers provided, the source material covers KRAS "
        "resistance from EGFR signaling in lung cancer to AR rewiring in "
        "prostate cancer. WCM scholars are mapping the resistance circuits."
    )
    v = run_deterministic_checks(bad)
    assert v.passed is False
    assert "preamble_chain_of_thought" in v.failed_constraints
    assert any(c.startswith("meta_language:") for c in v.failed_constraints)
    assert "cherry_picked_mechanism_disease_pairs" in v.failed_constraints


def test_10f_all_allowed_openers_match_tic_re():
    """All 7 institutional-voice variants in ALLOWED_OPENERS must satisfy
    TIC_RE when followed by an active -ing verb. Per wcmc-its/ReciterAI#2 §3."""
    from spotlight.critic import ALLOWED_OPENERS, TIC_RE
    for opener in ALLOWED_OPENERS:
        sample = (
            f"Single-cell methods rewrite how researchers read aging biology. "
            f"{opener} mapping the molecular drivers across diverse tissues "
            f"to sharpen interventions for the patients."
        )
        assert TIC_RE.search(sample), f"opener {opener!r} did not match TIC_RE"


def test_10g_find_duplicate_openers_flags_second_occurrence_only():
    """First use of an opener stays; subsequent duplicates are flagged.
    Per wcmc-its/ReciterAI#2 §3."""
    from spotlight.critic import find_duplicate_openers
    ledes = [
        "Cells signal in cascades. WCM scholars are mapping the misfires for therapy.",
        "Tumors evolve under pressure. WCM scholars are tracing the resistance circuits.",
        "Disparities shape outcomes. Researchers at WCM are testing structural fixes.",
    ]
    dupes = find_duplicate_openers(ledes)
    assert dupes == {1: "WCM scholars are"}, f"unexpected: {dupes}"


def test_10h_find_duplicate_openers_returns_empty_when_all_distinct():
    from spotlight.critic import find_duplicate_openers
    ledes = [
        "Cells signal. WCM scholars are mapping the misfires for therapy.",
        "Tumors evolve. Weill Cornell scholars are tracing the resistance circuits.",
        "Disparities shape outcomes. Researchers at WCM are testing structural fixes.",
    ]
    assert find_duplicate_openers(ledes) == {}


def test_10i_find_duplicate_openers_skips_ledes_without_opener():
    """A lede without any allowed opener is silently skipped (the per-spotlight
    `missing_wcm_scholars_tic` check catches it elsewhere)."""
    from spotlight.critic import find_duplicate_openers
    ledes = [
        "Cells signal. WCM scholars are mapping the misfires.",
        "No opener at all in this one.",
        "Tumors evolve. WCM scholars are tracing resistance.",
    ]
    # Lede 0 and lede 2 share the opener; lede 1 has none.
    dupes = find_duplicate_openers(ledes)
    assert dupes == {2: "WCM scholars are"}


def test_10e_clean_lede_with_geographic_from_to_does_not_false_positive():
    """The cherry-pick regex must NOT fire on innocent constructions like
    'WCM scholars are working from bench to bedside in oncology'."""
    clean = (
        "Tumors rewrite their identity to escape targeted drugs. WCM scholars "
        "are tracing the resistance circuits, working from bench to bedside "
        "in oncology to find the next intervention point."
    )
    v = run_deterministic_checks(clean)
    # Should pass on cherry-pick (one 'in' clause, not two separated by to/and).
    assert "cherry_picked_mechanism_disease_pairs" not in v.failed_constraints


# ---------------------------------------------------------------------------
# Tests 11-14: run_critic_loop
# ---------------------------------------------------------------------------


def _llm_pass_response() -> str:
    return '{"verdict":"pass","failed_constraint":"","reason":""}'


def _llm_fail_response(constraint: str = "active_verb", reason: str = "passive voice") -> str:
    return f'{{"verdict":"fail","failed_constraint":"{constraint}","reason":"{reason}"}}'


def test_11_run_critic_loop_first_attempt_pass():
    """Test 11: first lede passes both deterministic and LLM —
    1 generate call, 1 critic call, status="pass"."""
    lede_client = MagicMock()
    critic_client = MagicMock()
    critic_client.call.return_value = _llm_pass_response()
    dynamo_client = MagicMock()

    papers = [_paper("100", 0.9), _paper("101", 0.8)]

    with patch("spotlight.critic.generate_lede") as mock_gen:
        mock_gen.return_value = (CLEAN_LEDE, papers)
        result = run_critic_loop(
            meta=_meta(),
            papers=papers,
            publish_id="pub1",
            parent_topic="Aging & Geroscience",
            lede_client=lede_client,
            critic_client=critic_client,
            dynamo_client=dynamo_client,
        )

    assert mock_gen.call_count == 1
    assert critic_client.call.call_count == 1
    assert result.status == "pass"
    assert result.lede == CLEAN_LEDE
    assert len(result.attempts) == 1
    assert result.papers_used == ("100", "101")
    # No review entry written on success.
    assert dynamo_client.put_item.call_count == 0


def test_12_run_critic_loop_persistent_failure_4_generate_calls():
    """Test 12: persistent failure -> exactly 4 generate calls (initial + 3 retries),
    then write_review_entry, status="needs_review"."""
    lede_client = MagicMock()
    critic_client = MagicMock()
    critic_client.call.return_value = _llm_fail_response()
    dynamo_client = MagicMock()

    # All generated ledes fail deterministic ("novel" word triggers dead-word check).
    bad_lede = (
        "WCM scholars are mapping aging using novel single-cell methods across "
        "diverse cell populations to chart pathway dynamics for patients most at risk."
    )
    papers = [_paper("100", 0.9), _paper("101", 0.8)]

    with patch("spotlight.critic.generate_lede") as mock_gen:
        mock_gen.return_value = (bad_lede, papers)
        result = run_critic_loop(
            meta=_meta(),
            papers=papers,
            publish_id="pub1",
            parent_topic="Aging & Geroscience",
            lede_client=lede_client,
            critic_client=critic_client,
            dynamo_client=dynamo_client,
        )

    # 4 attempts = initial + MAX_RETRIES retries
    assert mock_gen.call_count == MAX_RETRIES + 1 == 4
    assert result.status == "needs_review"
    assert len(result.attempts) == 4
    # write_review_entry routes through dynamo_client.put_item.
    assert dynamo_client.put_item.call_count == 1


def test_13_run_critic_loop_persistent_failure_review_entry_shape():
    """Test 13: persistent failure -> write_review_entry called once with
    regen_count=3, flag_reason="critic", status forced to "pending"."""
    lede_client = MagicMock()
    critic_client = MagicMock()
    critic_client.call.return_value = _llm_fail_response("active_verb", "passive")
    dynamo_client = MagicMock()

    papers = [_paper("100", 0.9), _paper("101", 0.8)]

    # Generate ledes that pass deterministic but fail LLM critic, so we
    # also hit the LLM critic path on each attempt and the failure
    # routing carries the LLM verdict.
    with patch("spotlight.critic.generate_lede") as mock_gen:
        mock_gen.return_value = (CLEAN_LEDE, papers)
        result = run_critic_loop(
            meta=_meta(),
            papers=papers,
            publish_id="pub1",
            parent_topic="Aging & Geroscience",
            lede_client=lede_client,
            critic_client=critic_client,
            dynamo_client=dynamo_client,
        )

    assert dynamo_client.put_item.call_count == 1
    put_kwargs = dynamo_client.put_item.call_args.kwargs
    item = put_kwargs["Item"]
    # PK / SK shape from review_queue.write_review_entry.
    assert item["PK"]["S"] == "SPOTLIGHT_REVIEW#pub1"
    assert item["SK"]["S"] == "SUBTOPIC#aging.molecular"
    assert item["flag_reason"]["S"] == "critic"
    assert item["status"]["S"] == "pending"  # FORCED by review_queue
    assert int(item["regen_count"]["N"]) == MAX_RETRIES == 3
    # attempts list populated with 4 entries.
    assert len(item["attempts"]["L"]) == 4
    assert result.status == "needs_review"
    assert len(result.attempts) == 4


def test_14_run_critic_loop_deterministic_fail_then_pass():
    """Test 14: deterministic failure on attempt 1, pass on attempt 2 →
    status="pass", attempts.length=2, no review_entry."""
    lede_client = MagicMock()
    critic_client = MagicMock()
    critic_client.call.return_value = _llm_pass_response()
    dynamo_client = MagicMock()

    bad_lede = (
        "WCM scholars are mapping aging mechanisms across diverse cell "
        "populations using novel single-cell techniques to chart pathway "
        "dynamics for the patients most at risk in this lede sentence."
    )

    papers = [_paper("100", 0.9), _paper("101", 0.8)]

    sequence = [(bad_lede, papers), (CLEAN_LEDE, papers)]

    with patch("spotlight.critic.generate_lede") as mock_gen:
        mock_gen.side_effect = sequence
        result = run_critic_loop(
            meta=_meta(),
            papers=papers,
            publish_id="pub1",
            parent_topic="Aging & Geroscience",
            lede_client=lede_client,
            critic_client=critic_client,
            dynamo_client=dynamo_client,
        )

    assert mock_gen.call_count == 2
    assert result.status == "pass"
    assert result.lede == CLEAN_LEDE
    assert len(result.attempts) == 2
    # First attempt was deterministic-fail; LLM critic should have been
    # invoked only on the second attempt.
    assert critic_client.call.call_count == 1
    assert dynamo_client.put_item.call_count == 0


# ---------------------------------------------------------------------------
# Test 15: prompt artifact
# ---------------------------------------------------------------------------


def test_15_critic_prompt_file_exists_with_fenced_body():
    """Test 15: prompts/spotlight_critic_v0.md exists with operator
    notes header and a fenced ```markdown ... ``` prompt body."""
    path = Path(__file__).parent / "prompts" / "spotlight_critic_v0.md"
    assert path.is_file(), f"missing critic prompt: {path}"
    content = path.read_text(encoding="utf-8")
    # Header + fenced body (mirror spotlight_synopsis_v0.md structure).
    assert "```markdown" in content
    # Three template variables that critic.py renders against.
    assert "{lede}" in content
    assert "{subtopic_name}" in content
    assert "{papers_brief}" in content
    # JSON-only output mandated.
    assert "JSON" in content or "json" in content
    assert "verdict" in content


# ---------------------------------------------------------------------------
# Misc invariants exercised inline (no separate test number)
# ---------------------------------------------------------------------------


def test_critic_constants_locked():
    """MAX_RETRIES=3, CRITIC_TEMPERATURE=0.0, length bounds 22-38."""
    assert MAX_RETRIES == 3
    assert CRITIC_TEMPERATURE == 0.0
    assert CRITIC_MAX_TOKENS == 200
    assert LENGTH_MIN == 22
    assert LENGTH_MAX == 38


def test_em_dash_re_covers_both_unicode_dashes():
    """U+2014 (em-dash) and U+2013 (en-dash) both match EM_DASH_RE."""
    assert EM_DASH_RE.search("a—b") is not None
    assert EM_DASH_RE.search("a–b") is not None


def test_run_llm_critic_call_shape():
    """run_llm_critic uses HAIKU_MODEL, temperature=0.0, max_tokens=200,
    and parses the JSON verdict (with markdown-fence tolerance)."""
    from utils.bedrock_client import HAIKU_MODEL

    client = MagicMock()
    # Tolerate fence wrapping (Phase 2 stripJsonFences pattern).
    client.call.return_value = (
        '```json\n{"verdict":"pass","failed_constraint":"","reason":""}\n```'
    )
    papers = [_paper("100", 0.9), _paper("101", 0.8)]
    verdict = run_llm_critic(CLEAN_LEDE, _meta(), papers, client=client)
    assert verdict.passed is True
    kwargs = client.call.call_args.kwargs
    assert kwargs["model"] == HAIKU_MODEL
    assert kwargs["temperature"] == 0.0
    assert kwargs["max_tokens"] == CRITIC_MAX_TOKENS

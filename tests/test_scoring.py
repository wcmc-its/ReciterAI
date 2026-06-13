"""Tests for utils/scoring.py — the canonical article_score blend.

The formula is LOCKED to the Publication Manager's shared.ts (P-10):
    Math.pow(impactScore / 100, 1.2) * Math.pow(relevanceScore, 1.4)
These tests pin that contract and the multiplicative zero behavior.
"""

from __future__ import annotations

from utils.scoring import article_score


def test_matches_locked_formula():
    """article_score equals the P-10 expression for representative inputs."""
    for impact, relevance in [(90.0, 0.2), (40.0, 0.95), (100.0, 1.0), (12.5, 0.6)]:
        expected = (impact / 100) ** 1.2 * relevance ** 1.4
        assert article_score(impact, relevance) == expected


def test_zero_impact_is_zero():
    """Zero impact → zero article_score regardless of relevance."""
    assert article_score(0.0, 1.0) == 0.0


def test_zero_relevance_is_zero():
    """Zero topic-relevance → zero article_score regardless of impact."""
    assert article_score(100.0, 0.0) == 0.0


def test_relevance_can_outweigh_impact():
    """A low-impact, on-topic paper can outrank a high-impact, off-topic one.

    This is the property the spotlight ranker relies on: relevance is not a
    tiebreaker, it materially reorders papers within a topic.
    """
    prominent_off_topic = article_score(90.0, 0.2)
    modest_on_topic = article_score(40.0, 0.95)
    assert modest_on_topic > prominent_off_topic


def test_monotonic_in_impact_at_fixed_relevance():
    """At fixed relevance, higher impact → higher score (ordering preserved)."""
    assert article_score(50.0, 0.7) < article_score(60.0, 0.7)


def test_monotonic_in_relevance_at_fixed_impact():
    """At fixed impact, higher relevance → higher score."""
    assert article_score(50.0, 0.5) < article_score(50.0, 0.9)

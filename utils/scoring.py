"""Canonical article-score blend.

``article_score`` combines a publication's two independent signals into one
ranking number:

- ``impact_score`` — how prominent/important the publication is (enrichment
  layer; journal, novelty, citations). Independent of any one topic.
- ``relevance_score`` — how well the publication fits a *specific* topic (the
  dense topic-relevance ``score`` on the ``TOPIC#`` row).

A high-impact paper that merely *mentions* a topic should not outrank an
on-topic paper inside that topic's surfaces, which is exactly what happens when
ranking on impact alone. The blend below is the project's single source of
truth for "how good is this paper *for this topic*", used by:

- ``cli/aggregate_subtopic_scores.py`` — faculty/subtopic weight rollups.
- ``spotlight/pool_ranker.py`` — which papers represent a subtopic card.
- ``spotlight/lede_generator.py`` — which papers ground the editorial lede.

The exponents are LOCKED: the formula MUST match the Publication Manager's
``shared.ts`` byte-for-byte (P-10) so Python rollups and the PM/SPS UI agree on
article ranking. Do NOT lift the exponents to config — they are a cross-repo
contract, not a tunable.
"""

from __future__ import annotations


def article_score(impact_score: float, relevance_score: float) -> float:
    """Blend impact and topic-relevance into one article score for a topic.

    MUST match PM ``shared.ts`` byte-for-byte (P-10):
        TS: Math.pow(impactScore / 100, 1.2) * Math.pow(relevanceScore, 1.4)

    Both factors are multiplicative, so an article scores zero for a topic if
    *either* its impact is zero or its relevance to that topic is zero — a
    paper must be both prominent AND on-topic to rank.
    """
    return (impact_score / 100) ** 1.2 * relevance_score ** 1.4

"""Near-clone subtopic detection for the spotlight rotation selector (#91).

The rotation selector's only diversity gate is one-subtopic-per-parent_topic.
Three subtopics that paraphrase the same theme under three *different*
parents — e.g. "spaceflight omics" facets filed under Genetics, Single-Cell,
and Systems Biology — each clear that gate independently, so all three can
land in one ten-card monthly publish. On the SPS home page the spotlight
rotates one card at a time, so two near-clones surfacing read as a duplicated
card (#91: three spaceflight + three disparities subtopics in v2026-05-16).

This module embeds each pooled subtopic's ``short_description`` and computes
all-pairs cosine similarity. A pair at or above a tunable threshold
(``spotlight_theme_similarity_max`` in ``config/thresholds.json``) is a
near-clone. ``rotation_selector.select_with_diversity`` consumes the
resulting adjacency as plain data and skips a candidate that is a near-clone
of an already-selected subtopic.

Why embeddings and not lexical similarity: the taxonomy deliberately
paraphrases ("disparities" vs "differences" vs "gaps"; "responses" vs
"characterization"), so a bag-of-words cosine cannot separate the #91
near-clones from ordinary subtopic pairs without a flood of false positives
(verified during the #91 investigation — five of the six flagged pairs scored
inside the lexical-similarity baseline tail). A semantic text embedding is
required.

Embedding provider: Bedrock Titan Text Embeddings v2
(``amazon.titan-embed-text-v2:0``). The spotlight pipeline already depends on
Bedrock and ``boto3``, so this adds no new PyPI dependency. Titan v2 embeds
one text per ``invoke_model`` call — roughly 50 sequential calls per monthly
run, a few seconds and negligible cost. The ``embed`` callable is injectable
so tests run on canned vectors and never touch AWS.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from typing import Callable

import boto3

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

TITAN_EMBED_MODEL = "amazon.titan-embed-text-v2:0"
REGION = "us-east-1"

# ``ranked_pairs`` also reports near-miss pairs this far BELOW the threshold,
# so an operator calibrating ``spotlight_theme_similarity_max`` via
# ``backfill_spotlight --dry-run`` sees the pairs sitting just under the cut,
# not only the ones already suppressed (#91 plan §7).
RANKED_PAIR_FLOOR_MARGIN = 0.10


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class NearClones:
    """Result of an all-pairs near-clone scan over a set of descriptions.

    Not frozen (unlike the hashable dataclasses in ``spotlight/types.py``):
    it carries mutable collections and is a one-shot result bag, never used
    as a dict key or set member.

    Attributes:
        adjacency: symmetric map ``subtopic_id -> {subtopic_ids it is at or
            above the threshold similar to}``. This is what the rotation
            selector gates on. NON-TRANSITIVE by construction: if
            cos(A,B) >= t and cos(B,C) >= t but cos(A,C) < t, then
            ``adjacency[A] == {B}`` — NOT ``{B, C}``. Every scanned
            subtopic_id has an entry; a subtopic with a blank description,
            or one with no near-clone, maps to an empty set.
        ranked_pairs: every distinct ``(sid_a, sid_b, cosine)`` whose cosine
            is at or above ``threshold - RANKED_PAIR_FLOOR_MARGIN``, sorted
            by cosine descending (ties broken by the sid pair, ascending).
            ``sid_a < sid_b`` in every tuple. The ``--dry-run``
            threshold-calibration surface.
        threshold: the cutoff this scan ran at, echoed back so the
            ``--dry-run`` report can label itself without a second lookup.
    """

    adjacency: dict[str, set[str]]
    ranked_pairs: list[tuple[str, str, float]]
    threshold: float


# ---------------------------------------------------------------------------
# Lazy Bedrock client + Titan v2 embedding helper
# ---------------------------------------------------------------------------

_default_bedrock_client = None


def _get_default_bedrock_client():
    """Get or create the module-level Bedrock Runtime client.

    Lazy — no boto3 client and no credential resolution at import time.
    Mirrors ``spotlight/pool_ranker.py:_get_default_client``. Tests inject
    their own ``embed`` callable and never reach this path.
    """
    global _default_bedrock_client
    if _default_bedrock_client is None:
        _default_bedrock_client = boto3.client(
            "bedrock-runtime", region_name=REGION
        )
    return _default_bedrock_client


def _titan_embed(texts: list[str]) -> list[list[float]]:
    """Embed each text with Bedrock Titan Text Embeddings v2.

    Titan v2 embeds one text per ``invoke_model`` call, so this issues one
    call per text — roughly 50 per monthly spotlight run. Vectors are
    returned in input order, one per text. Titan v2 normalizes its output by
    default, so the vectors are unit length; ``_cosine`` does not rely on it.

    Any boto3 / Bedrock failure (throttle, timeout, 5xx, AccessDenied)
    propagates to the caller. ``backfill_spotlight._run_pipeline`` wraps the
    ``find_near_clones`` call and degrades to parent-only selection on any
    exception, so a failure here never blocks the monthly publish (#91 §9.6).
    """
    client = _get_default_bedrock_client()
    vectors: list[list[float]] = []
    for text in texts:
        response = client.invoke_model(
            modelId=TITAN_EMBED_MODEL,
            body=json.dumps({"inputText": text}),
            accept="application/json",
            contentType="application/json",
        )
        payload = json.loads(response["body"].read())
        vectors.append(payload["embedding"])
    return vectors


def _cosine(vec_a: list[float], vec_b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors.

    Returns 0.0 if either vector has zero magnitude, so a degenerate
    (e.g. all-zero) embedding cannot raise ZeroDivisionError.
    """
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def find_near_clones(
    descriptions: dict[str, str],
    threshold: float,
    *,
    embed: Callable[[list[str]], list[list[float]]] | None = None,
) -> NearClones:
    """Scan a set of subtopic descriptions for near-clone pairs.

    Args:
        descriptions: map ``subtopic_id -> short_description``. Insertion
            order is not significant. A blank or whitespace-only description
            is NOT embedded — Titan rejects empty input, and a blank
            description carries no signal anyway; that subtopic gets an
            empty adjacency entry and can never be gated out of selection.
        threshold: cosine-similarity cutoff. A pair whose cosine is at or
            above this value is a near-clone. Data- and model-dependent;
            calibrate via ``backfill_spotlight.py --dry-run`` (#91 plan §7).
        embed: callable mapping a list of texts to a list of embedding
            vectors — one vector per text, in input order. Injected by
            tests; defaults to the Bedrock Titan v2 helper ``_titan_embed``.

    Returns:
        A ``NearClones`` with the symmetric ``adjacency``, the
        ``ranked_pairs`` calibration list, and ``threshold`` echoed back.

    Raises:
        ValueError: if ``embed`` returns a number of vectors that does not
            match the number of (non-blank) input texts.
    """
    embed = embed or _titan_embed

    # Sort sids so adjacency construction, ranked_pairs ordering, and the
    # text list handed to ``embed`` are all deterministic regardless of the
    # caller's dict insertion order.
    sids = sorted(descriptions)
    adjacency: dict[str, set[str]] = {sid: set() for sid in sids}

    # Blank descriptions are skipped (see the docstring). ``embeddable``
    # stays sorted — it is a filtered sorted list — so embeddable[i] <
    # embeddable[j] for i < j, which keeps every ranked_pairs tuple
    # sid-ordered (sid_a < sid_b).
    embeddable = [sid for sid in sids if descriptions[sid].strip()]
    if len(embeddable) < 2:
        # Fewer than two embeddable descriptions -> no pair to compare.
        return NearClones(
            adjacency=adjacency, ranked_pairs=[], threshold=threshold
        )

    vectors = embed([descriptions[sid] for sid in embeddable])
    if len(vectors) != len(embeddable):
        raise ValueError(
            f"embed returned {len(vectors)} vectors for {len(embeddable)} "
            "input texts; it must return exactly one vector per text"
        )

    print_floor = threshold - RANKED_PAIR_FLOOR_MARGIN
    ranked_pairs: list[tuple[str, str, float]] = []
    for i in range(len(embeddable)):
        for j in range(i + 1, len(embeddable)):
            cosine = _cosine(vectors[i], vectors[j])
            sid_a, sid_b = embeddable[i], embeddable[j]
            if cosine >= threshold:
                adjacency[sid_a].add(sid_b)
                adjacency[sid_b].add(sid_a)
            if cosine >= print_floor:
                ranked_pairs.append((sid_a, sid_b, cosine))

    # Cosine descending; the (sid_a, sid_b) secondary key makes ties stable.
    ranked_pairs.sort(key=lambda pair: (-pair[2], pair[0], pair[1]))

    clone_pairs = sum(1 for _, _, cos in ranked_pairs if cos >= threshold)
    logger.info(
        "theme dedup: embedded %d subtopic description(s); %d near-clone "
        "pair(s) at threshold %.3f",
        len(embeddable),
        clone_pairs,
        threshold,
    )
    return NearClones(
        adjacency=adjacency, ranked_pairs=ranked_pairs, threshold=threshold
    )

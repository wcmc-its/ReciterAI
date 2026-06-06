"""Embedding nearest-match for registry match-or-mint (§7, §8).

The two registries resolve a new mention to an existing canonical tool / family
by **alias-or-name + embedding NN** (§8 step 1, §7 step 1). That is a one-vs-many
*retrieval* (nearest existing record above a threshold), distinct from the
all-pairs symmetric dedup in ``spotlight.theme_dedup.find_near_clones``. This
module provides that retrieval primitive.

Embedding provider is Bedrock Titan Text Embeddings v2
(``amazon.titan-embed-text-v2:0``) — the same model the spotlight/hierarchy
dedup already uses, so no new dependency and one consistent vector space across
the codebase. ``embed`` is injectable; tests pass canned vectors and never reach
AWS (mirrors ``find_near_clones``'s injectable ``embed``). A process-level cache
keys vectors by text so the registry is not re-embedded on every mention.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from typing import Callable

import boto3

logger = logging.getLogger(__name__)

# Same model + region as spotlight/theme_dedup — one vector space for the repo.
TITAN_EMBED_MODEL = "amazon.titan-embed-text-v2:0"
REGION = "us-east-1"

EmbedFn = Callable[[list[str]], list[list[float]]]

_default_bedrock_client = None


def _get_default_bedrock_client():
    """Lazy module-level Bedrock Runtime client — no boto3/credentials at import time."""
    global _default_bedrock_client
    if _default_bedrock_client is None:
        _default_bedrock_client = boto3.client("bedrock-runtime", region_name=REGION)
    return _default_bedrock_client


def titan_embed(texts: list[str]) -> list[list[float]]:
    """Embed each text with Bedrock Titan v2 (one invoke per text), in input order.

    Titan v2 returns unit-length vectors; ``cosine`` does not rely on that.
    Any boto3/Bedrock failure propagates to the caller (the seed orchestrator
    decides whether to degrade — name-only matching — or abort).
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


def cosine(vec_a: list[float], vec_b: list[float]) -> float:
    """Cosine similarity; 0.0 if either vector has zero magnitude (no ZeroDivisionError)."""
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class EmbeddingCache:
    """Text -> vector cache over an injectable ``embed`` fn (batches the misses).

    Keeps a registry of hundreds-to-thousands of canonical names embedded once
    and reused across every incoming mention in a batch. Deterministic: identical
    text always returns the identical cached vector.
    """

    def __init__(self, embed: EmbedFn | None = None):
        self._embed = embed or titan_embed
        self._cache: dict[str, list[float]] = {}

    def get_many(self, texts: list[str]) -> list[list[float]]:
        """Vectors for ``texts`` (input order); only cache-misses hit ``embed``."""
        missing = [t for t in dict.fromkeys(texts) if t not in self._cache]
        if missing:
            vectors = self._embed(missing)
            if len(vectors) != len(missing):
                raise ValueError(
                    f"embed returned {len(vectors)} vectors for {len(missing)} texts; "
                    "it must return exactly one vector per text"
                )
            self._cache.update(zip(missing, vectors))
        return [self._cache[t] for t in texts]

    def get(self, text: str) -> list[float]:
        return self.get_many([text])[0]


@dataclass(frozen=True)
class Match:
    """A nearest-neighbour retrieval result.

    Attributes:
        key: the candidate key (e.g. a canonical_tool_id / family_id) that won.
        score: cosine of the query against that candidate's best text.
        matched_text: the specific candidate text (display_name/alias) that scored.
    """

    key: str
    score: float
    matched_text: str


def nearest_match(
    query: str,
    candidates: dict[str, list[str]],
    *,
    threshold: float,
    cache: EmbeddingCache,
) -> Match | None:
    """Best candidate whose cosine to ``query`` is >= ``threshold``, else None.

    Args:
        query: the incoming mention text to resolve.
        candidates: ``key -> [texts]`` — each registry record's matchable surface
            forms (display_name + aliases). A record matches on its best-scoring
            text. Empty/blank texts are skipped.
        threshold: cosine cutoff for a confident match (calibratable). Below it,
            the caller mints a new record.
        cache: shared ``EmbeddingCache`` so the candidate corpus is embedded once.

    Returns:
        The single best ``Match`` at/above ``threshold``, or None to signal a mint.
        Ties on score break deterministically by candidate key, then text.
    """
    texts: list[str] = []
    owners: list[tuple[str, str]] = []  # (key, text) parallel to `texts`
    for key, forms in candidates.items():
        for form in forms:
            if form and form.strip():
                texts.append(form)
                owners.append((key, form))
    if not texts:
        return None

    qvec = cache.get(query)
    cvecs = cache.get_many(texts)

    best: Match | None = None
    for (key, text), cvec in zip(owners, cvecs):
        score = cosine(qvec, cvec)
        if score < threshold:
            continue
        # Highest score wins; ties break deterministically by (key, text) ascending.
        if best is None or score > best.score or (
            score == best.score and (key, text) < (best.key, best.matched_text)
        ):
            best = Match(key=key, score=score, matched_text=text)
    return best

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
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable

import boto3
import numpy as np
from botocore.config import Config

logger = logging.getLogger(__name__)

# Same model + region as spotlight/theme_dedup — one vector space for the repo.
TITAN_EMBED_MODEL = "amazon.titan-embed-text-v2:0"
REGION = "us-east-1"
# Titan invoke is one HTTP call per text; at A2 scale (tens of thousands of
# unique texts) sequential embedding is the wall-clock bottleneck. boto3 low-level
# clients are thread-safe for calls, so embed the batch over a small thread pool.
TITAN_EMBED_MAX_WORKERS = 16
# Titan occasionally returns a transient server-side ModelErrorException ("try
# again") under load. Across a ~tens-of-thousands-call batch that is near-certain
# to hit at least once, and one un-retried blip would abort the whole pre-warm —
# so each call retries with bounded backoff before giving up.
TITAN_EMBED_MAX_RETRIES = 5

EmbedFn = Callable[[list[str]], list[list[float]]]

_default_bedrock_client = None


def _get_default_bedrock_client():
    """Lazy module-level Bedrock Runtime client — no boto3/credentials at import time."""
    global _default_bedrock_client
    if _default_bedrock_client is None:
        # Adaptive retries with generous attempts: the A2 pre-warm fans ~tens of
        # thousands of embeds across a thread pool, so client-side throttle
        # backoff keeps a burst from failing the whole run. The connection pool is
        # sized to the embed thread pool so all workers get a connection instead
        # of churning (default pool is 10 < TITAN_EMBED_MAX_WORKERS).
        _default_bedrock_client = boto3.client(
            "bedrock-runtime", region_name=REGION,
            config=Config(
                retries={"max_attempts": 8, "mode": "adaptive"},
                max_pool_connections=max(TITAN_EMBED_MAX_WORKERS + 4, 10),
            ),
        )
    return _default_bedrock_client


def titan_embed(texts: list[str], *, max_workers: int = TITAN_EMBED_MAX_WORKERS) -> list[list[float]]:
    """Embed each text with Bedrock Titan v2 (one invoke per text), in input order.

    Parallelised over a thread pool because each text is an independent HTTP call
    (the calls are I/O-bound; boto3 clients are thread-safe for invoke). Output
    order matches input order. Titan v2 returns unit-length vectors; ``cosine``
    does not rely on that. Any boto3/Bedrock failure propagates to the caller (the
    orchestrator decides whether to degrade — name-only matching — or abort).
    """
    if not texts:
        return []
    client = _get_default_bedrock_client()  # built once, outside the threads.

    def _one(text: str) -> list[float]:
        for attempt in range(TITAN_EMBED_MAX_RETRIES):
            try:
                response = client.invoke_model(
                    modelId=TITAN_EMBED_MODEL,
                    body=json.dumps({"inputText": text}),
                    accept="application/json",
                    contentType="application/json",
                )
                return json.loads(response["body"].read())["embedding"]
            except Exception:  # noqa: BLE001 — transient Titan/throttle/connection blip
                if attempt == TITAN_EMBED_MAX_RETRIES - 1:
                    raise
                time.sleep(min(0.5 * (2 ** attempt), 8.0))

    workers = min(max_workers, len(texts))
    if workers <= 1:
        return [_one(t) for t in texts]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_one, texts))  # map preserves input order


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
        # Vectors stored as float64 ndarrays so the registry matcher can stack them
        # into a candidate matrix with a C memcpy (np.asarray of a list of ndarrays)
        # instead of re-parsing Python lists on every match call (the A2 hot path).
        self._cache: dict[str, np.ndarray] = {}

    def get_many(self, texts: list[str]) -> list[np.ndarray]:
        """Vectors for ``texts`` (input order); only cache-misses hit ``embed``.

        Misses are embedded in one ``embed`` call (parallelised for the live Titan
        seam), so warming the cache with the full text set up front turns the
        per-call match loop into pure in-memory matrix math.
        """
        missing = [t for t in dict.fromkeys(texts) if t not in self._cache]
        if missing:
            vectors = self._embed(missing)
            if len(vectors) != len(missing):
                raise ValueError(
                    f"embed returned {len(vectors)} vectors for {len(missing)} texts; "
                    "it must return exactly one vector per text"
                )
            for text, vec in zip(missing, vectors):
                self._cache[text] = np.asarray(vec, dtype=np.float64)
        return [self._cache[t] for t in texts]

    def get(self, text: str) -> np.ndarray:
        return self.get_many([text])[0]

    def prewarm(self, texts: list[str]) -> None:
        """Embed every not-yet-cached text in one batch (warms the match loop)."""
        self.get_many(texts)


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

    # Vectorised cosine: a single matmul against the candidate matrix instead of a
    # per-candidate Python loop (the registry-match hot path — O(forms x registry)
    # at A2 scale). float64 + the same zero-magnitude guard as ``cosine`` keep this
    # numerically equivalent to the scalar version, so the threshold/tie-break
    # decisions are identical.
    q = np.asarray(qvec, dtype=np.float64)
    q_norm = float(np.linalg.norm(q))
    if q_norm == 0.0:
        return None  # zero query vector -> cosine 0 everywhere -> clears no positive threshold
    matrix = np.asarray(cvecs, dtype=np.float64)
    row_norms = np.linalg.norm(matrix, axis=1)
    sims = np.zeros(len(texts), dtype=np.float64)
    nz = row_norms > 0.0
    sims[nz] = (matrix[nz] @ q) / (row_norms[nz] * q_norm)

    above = np.nonzero(sims >= threshold)[0]
    if above.size == 0:
        return None
    # Highest score wins; ties break deterministically by (key, text) ascending —
    # identical to the prior scalar loop.
    best_i = None
    best_score = -1.0
    for i in above:
        score = float(sims[i])
        if best_i is None or score > best_score or (score == best_score and owners[i] < owners[best_i]):
            best_i, best_score = int(i), score
    key, text = owners[best_i]
    return Match(key=key, score=best_score, matched_text=text)

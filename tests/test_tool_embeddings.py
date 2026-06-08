"""Unit tests for the registry embedding matcher (pipeline_tools.embeddings).

Canned ``embed`` only — never touches AWS (mirrors theme_dedup's injectable embed).
"""

import json

import pytest

import pipeline_tools.embeddings as emb
from pipeline_tools.embeddings import EmbeddingCache, nearest_match


# A tiny 3-d vector space. Near-duplicate names share a near-parallel vector
# (cosine ~0.99); unrelated names are orthogonal (cosine 0).
VECTORS = {
    "MRI scanner": [1.0, 0.0, 0.0],
    "magnetic resonance imaging": [0.99, 0.141, 0.0],   # cos ~0.99 with MRI scanner
    "flow cytometer": [0.0, 1.0, 0.0],                  # orthogonal to MRI
    "FACS analyzer": [0.0, 0.99, 0.141],                # cos ~0.99 with flow cytometer
}


def make_embed(calls: list | None = None):
    def embed(texts):
        if calls is not None:
            calls.append(list(texts))
        return [VECTORS[t] for t in texts]
    return embed


def test_nearest_match_returns_best_above_threshold():
    cache = EmbeddingCache(embed=make_embed())
    candidates = {"t1": ["MRI scanner"], "t2": ["flow cytometer"]}
    hit = nearest_match("magnetic resonance imaging", candidates, threshold=0.86, cache=cache)
    assert hit is not None
    assert hit.key == "t1"
    assert hit.score > 0.98


def test_nearest_match_returns_none_below_threshold():
    cache = EmbeddingCache(embed=make_embed())
    candidates = {"t2": ["flow cytometer"]}  # orthogonal to the query
    hit = nearest_match("MRI scanner", candidates, threshold=0.86, cache=cache)
    assert hit is None


def test_nearest_match_picks_the_right_bucket_among_many():
    cache = EmbeddingCache(embed=make_embed())
    candidates = {"t1": ["MRI scanner"], "t2": ["flow cytometer"]}
    hit = nearest_match("FACS analyzer", candidates, threshold=0.86, cache=cache)
    assert hit.key == "t2"


def test_embedding_cache_embeds_each_unique_text_once():
    calls: list = []
    cache = EmbeddingCache(embed=make_embed(calls))
    # Query + two candidates across two nearest_match calls; "MRI scanner" recurs.
    nearest_match("magnetic resonance imaging", {"t1": ["MRI scanner"]}, threshold=0.86, cache=cache)
    nearest_match("MRI scanner", {"t1": ["MRI scanner"]}, threshold=0.86, cache=cache)
    embedded = [t for batch in calls for t in batch]
    # Each distinct text embedded exactly once despite recurring across calls.
    assert sorted(set(embedded)) == sorted(embedded)
    assert "MRI scanner" in embedded and "magnetic resonance imaging" in embedded


def test_embedding_cache_raises_on_count_mismatch():
    bad_embed = lambda texts: [[1.0, 0.0, 0.0]]  # always one vector
    cache = EmbeddingCache(embed=bad_embed)
    with pytest.raises(ValueError):
        cache.get_many(["a", "b"])


def test_cache_stores_numpy_arrays():
    import numpy as np
    cache = EmbeddingCache(embed=lambda ts: [[1.0, 0.0, 0.0] for _ in ts])
    v = cache.get("a")
    assert isinstance(v, np.ndarray) and v.dtype == np.float64


def test_nearest_match_zero_query_returns_none():
    # a zero query vector clears no positive threshold (matches cosine()==0 semantics).
    cache = EmbeddingCache(embed=lambda ts: [[0.0, 0.0, 0.0] for _ in ts])
    assert nearest_match("q", {"t1": ["name"]}, threshold=0.86, cache=cache) is None


def test_nearest_match_skips_zero_magnitude_candidate():
    # candidate with a zero vector never matches; the non-zero parallel one does.
    V2 = {"q": [1.0, 0.0], "zero": [0.0, 0.0], "good": [1.0, 0.0]}
    cache = EmbeddingCache(embed=lambda ts: [V2[t] for t in ts])
    hit = nearest_match("q", {"z": ["zero"], "g": ["good"]}, threshold=0.86, cache=cache)
    assert hit is not None and hit.key == "g"


def test_prewarm_embeds_once_then_match_is_cache_only():
    calls: list = []
    cache = EmbeddingCache(embed=make_embed(calls))
    cache.prewarm(["MRI scanner", "flow cytometer", "magnetic resonance imaging"])
    n_before = sum(len(b) for b in calls)
    nearest_match("magnetic resonance imaging",
                  {"t1": ["MRI scanner"], "t2": ["flow cytometer"]}, threshold=0.86, cache=cache)
    # all texts pre-warmed -> the match call embeds nothing new.
    assert sum(len(b) for b in calls) == n_before


class _FakeBody:
    def __init__(self, vec):
        self._vec = vec

    def read(self):
        return json.dumps({"embedding": self._vec}).encode()


def test_titan_embed_retries_transient_then_succeeds(monkeypatch):
    calls = {"n": 0}

    class FakeClient:
        def invoke_model(self, **kw):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("transient ModelErrorException: try again")
            return {"body": _FakeBody([1.0, 2.0])}

    monkeypatch.setattr(emb, "_get_default_bedrock_client", lambda: FakeClient())
    monkeypatch.setattr(emb.time, "sleep", lambda s: None)
    out = emb.titan_embed(["x"], max_workers=1)
    assert out == [[1.0, 2.0]]
    assert calls["n"] == 3  # two transient failures, succeeded on the third attempt


def test_titan_embed_raises_after_max_retries(monkeypatch):
    class FakeClient:
        def invoke_model(self, **kw):
            raise RuntimeError("persistent failure")

    monkeypatch.setattr(emb, "_get_default_bedrock_client", lambda: FakeClient())
    monkeypatch.setattr(emb.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError):
        emb.titan_embed(["x"], max_workers=1)

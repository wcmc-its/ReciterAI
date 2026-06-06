"""Unit tests for the registry embedding matcher (pipeline_tools.embeddings).

Canned ``embed`` only — never touches AWS (mirrors theme_dedup's injectable embed).
"""

import pytest

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

"""Tests for spotlight/pool_ranker.py — Plan 06-02.

Behaviors per the PLAN.md ``<behavior>`` block:
  1. 24-month cutoff filtering (year < cutoff_year excluded)
  2. Score aggregation (sum impact_score per primary_subtopic_id)
  3. Deterministic tiebreaker (subtopic_id ASC on equal score) — SPOT-02
  4. Top-50 cap (pool_size)
  5. Zero-impact rows excluded
  6. PMID dedup per subtopic
  7. Missing primary_subtopic_id rows skipped (no raise)
  8. ``_parent_of`` regex behavior
  9. Lazy boto3 init invariant (``_default_client is None`` at import)

All tests use a synthetic injected boto3 stub; no AWS calls.
"""

from __future__ import annotations

from datetime import date


# ---------------------------------------------------------------------------
# Synthetic DynamoDB client stub
# ---------------------------------------------------------------------------


class _StubPaginator:
    """Mimics boto3 paginator: ``paginate(**kwargs) -> iterable of pages``."""

    def __init__(self, pages):
        self._pages = pages

    def paginate(self, **_kwargs):
        return iter(self._pages)


class StubDynamoClient:
    """Minimal stub: only ``get_paginator('scan')`` is implemented."""

    def __init__(self, items):
        # Single page is sufficient for these tests; multi-page iteration
        # is exercised implicitly by the for-loop over pages in rank_pool.
        self._pages = [{"Items": items}]

    def get_paginator(self, name):
        assert name == "scan", f"unexpected paginator: {name}"
        return _StubPaginator(self._pages)


def _topic_item(
    pmid: str,
    subtopic_id: str | None,
    impact_score: float,
    year: int,
    title: str = "Title",
    journal: str = "Journal",
):
    """Build a TOPIC# item in DynamoDB low-level shape."""
    item = {
        "pmid": {"S": pmid},
        "impact_score": {"N": str(impact_score)},
        "year": {"N": str(year)},
        "title": {"S": title},
        "journal": {"S": journal},
        "impact_justification": {"S": ""},
        "synopsis": {"S": ""},
        "first_author_person_identifier": {"S": ""},
        "first_author_display_name": {"S": ""},
        "last_author_person_identifier": {"S": ""},
        "last_author_display_name": {"S": ""},
    }
    if subtopic_id is not None:
        item["primary_subtopic_id"] = {"S": subtopic_id}
    return item


# ---------------------------------------------------------------------------
# Test 1: 24-month cutoff filtering
# ---------------------------------------------------------------------------


def test_cutoff_excludes_old_publications():
    """Items with year < (today.year - 2) MUST be excluded from aggregation."""
    from spotlight.pool_ranker import rank_pool

    cutoff_year = date.today().year - 2
    items = [
        _topic_item(pmid="100", subtopic_id="aging_001", impact_score=10.0, year=cutoff_year - 2),  # too old
        _topic_item(pmid="101", subtopic_id="aging_001", impact_score=20.0, year=cutoff_year - 1),  # too old
        _topic_item(pmid="102", subtopic_id="aging_001", impact_score=30.0, year=cutoff_year),       # in window
        _topic_item(pmid="103", subtopic_id="aging_001", impact_score=40.0, year=cutoff_year + 1),   # in window
    ]
    result = rank_pool(client=StubDynamoClient(items))

    assert len(result) == 1
    assert result[0].subtopic_id == "aging_001"
    # Only 30 + 40 = 70 from in-window publications
    assert result[0].pool_score == 70.0
    pmids = {p.pmid for p in result[0].papers}
    assert pmids == {"102", "103"}


# ---------------------------------------------------------------------------
# Test 2: Score aggregation per subtopic
# ---------------------------------------------------------------------------


def test_score_aggregation_sums_per_subtopic():
    """Two items same subtopic, scores 30.0 + 20.0, result has pool_score 50.0."""
    from spotlight.pool_ranker import rank_pool

    year = date.today().year
    items = [
        _topic_item(pmid="200", subtopic_id="aging_001", impact_score=30.0, year=year),
        _topic_item(pmid="201", subtopic_id="aging_001", impact_score=20.0, year=year),
    ]
    result = rank_pool(client=StubDynamoClient(items))

    assert len(result) == 1
    assert result[0].subtopic_id == "aging_001"
    assert result[0].pool_score == 50.0


def test_score_uses_top_n_papers_per_subtopic():
    """Subtopic with 10 papers: pool_score = sum of top-7 impact_scores, NOT all 10.

    Regression test for the live-data smoke finding where summing every
    paper's impact_score let high-volume subtopics dominate over high-quality
    ones. With default ``top_papers_per_subtopic=7`` (#49 raised from 6),
    10 papers of impact 100..91 yield pool_score = 100+99+98+97+96+95+94 = 679.
    """
    from spotlight.pool_ranker import rank_pool

    year = date.today().year
    items = [
        _topic_item(pmid=f"7{i:02d}", subtopic_id="aging_001",
                    impact_score=float(100 - i), year=year)
        for i in range(10)
    ]
    result = rank_pool(client=StubDynamoClient(items))

    assert len(result) == 1
    assert result[0].pool_score == sum(100 - i for i in range(7))  # 679.0
    assert len(result[0].papers) == 7
    # Top-7 papers ordered by impact_score DESC (PMID 700..706)
    assert [p.pmid for p in result[0].papers] == [f"7{i:02d}" for i in range(7)]


def test_parent_lookup_overrides_regex():
    """parent_lookup dict takes precedence; regex fallback for unknown ids."""
    from spotlight.pool_ranker import rank_pool

    year = date.today().year
    items = [
        _topic_item(pmid="800", subtopic_id="cardiovascular_tavr",
                    impact_score=50.0, year=year),
        _topic_item(pmid="801", subtopic_id="aging_001",
                    impact_score=40.0, year=year),
    ]
    result = rank_pool(
        client=StubDynamoClient(items),
        parent_lookup={"cardiovascular_tavr": "cardiovascular_disease"},
    )

    by_sid = {e.subtopic_id: e for e in result}
    assert by_sid["cardiovascular_tavr"].parent_topic == "cardiovascular_disease"
    # aging_001 not in lookup → regex fallback strips trailing _001
    assert by_sid["aging_001"].parent_topic == "aging"


# ---------------------------------------------------------------------------
# Test 3: Deterministic tiebreaker (SPOT-02)
# ---------------------------------------------------------------------------


def test_deterministic_tiebreaker_on_equal_scores():
    """Equal pool_score → lexicographically smaller subtopic_id sorts first."""
    from spotlight.pool_ranker import rank_pool

    year = date.today().year
    items = [
        _topic_item(pmid="300", subtopic_id="zebra_001", impact_score=100.0, year=year),
        _topic_item(pmid="301", subtopic_id="alpha_001", impact_score=100.0, year=year),
    ]
    result = rank_pool(client=StubDynamoClient(items))

    assert len(result) == 2
    assert result[0].subtopic_id == "alpha_001"
    assert result[1].subtopic_id == "zebra_001"


# ---------------------------------------------------------------------------
# Test 4: Top-50 cap (pool_size)
# ---------------------------------------------------------------------------


def test_top_50_cap():
    """60 distinct qualifying subtopics → result length == 50."""
    from spotlight.pool_ranker import rank_pool

    year = date.today().year
    items = []
    for i in range(60):
        sid = f"topic_{i:03d}"
        items.append(_topic_item(pmid=f"4{i:03d}", subtopic_id=sid, impact_score=float(i + 1), year=year))
    result = rank_pool(client=StubDynamoClient(items))

    assert len(result) == 50


# ---------------------------------------------------------------------------
# Test 5: Zero-impact rows excluded
# ---------------------------------------------------------------------------


def test_zero_impact_rows_excluded():
    """Item with impact_score == 0.0 does NOT contribute to any PoolEntry."""
    from spotlight.pool_ranker import rank_pool

    year = date.today().year
    items = [
        _topic_item(pmid="500", subtopic_id="aging_001", impact_score=0.0, year=year),
        _topic_item(pmid="501", subtopic_id="aging_002", impact_score=15.0, year=year),
    ]
    result = rank_pool(client=StubDynamoClient(items))

    # aging_001 was zero-only and must be absent from results.
    sids = {pe.subtopic_id for pe in result}
    assert "aging_001" not in sids
    assert "aging_002" in sids


# ---------------------------------------------------------------------------
# Test 6: PMID dedup per subtopic
# ---------------------------------------------------------------------------


def test_pmid_dedup_per_subtopic():
    """Same PMID under same subtopic across 3 TOPIC# rows → counted ONCE."""
    from spotlight.pool_ranker import rank_pool

    year = date.today().year
    # Same pmid+subtopic_id appears 3 times with the same impact_score
    items = [
        _topic_item(pmid="600", subtopic_id="aging_001", impact_score=10.0, year=year),
        _topic_item(pmid="600", subtopic_id="aging_001", impact_score=10.0, year=year),
        _topic_item(pmid="600", subtopic_id="aging_001", impact_score=10.0, year=year),
    ]
    result = rank_pool(client=StubDynamoClient(items))

    assert len(result) == 1
    assert result[0].pool_score == 10.0  # not 30
    assert len(result[0].papers) == 1


# ---------------------------------------------------------------------------
# Test 7: Missing primary_subtopic_id row skipped
# ---------------------------------------------------------------------------


def test_missing_subtopic_id_skipped_without_raise():
    """Row without primary_subtopic_id is skipped silently."""
    from spotlight.pool_ranker import rank_pool

    year = date.today().year
    items = [
        _topic_item(pmid="700", subtopic_id=None, impact_score=99.0, year=year),
        _topic_item(pmid="701", subtopic_id="aging_001", impact_score=5.0, year=year),
    ]
    # MUST NOT raise.
    result = rank_pool(client=StubDynamoClient(items))

    assert len(result) == 1
    assert result[0].subtopic_id == "aging_001"
    assert result[0].pool_score == 5.0


# ---------------------------------------------------------------------------
# Test 8: _parent_of regex
# ---------------------------------------------------------------------------


def test_parent_of_strips_trailing_three_digit_segment():
    """``_parent_of('aging_geroscience_001')`` returns ``'aging_geroscience'``."""
    from spotlight.pool_ranker import _parent_of

    assert _parent_of("aging_geroscience_001") == "aging_geroscience"
    assert _parent_of("breast_cancer_042") == "breast_cancer"
    # No trailing _NNN — return as-is.
    assert _parent_of("standalone") == "standalone"
    # Two-digit suffix should not be stripped (regex requires exactly 3).
    assert _parent_of("foo_42") == "foo_42"


# ---------------------------------------------------------------------------
# Test 9: Lazy boto3 init invariant
# ---------------------------------------------------------------------------


def test_default_client_is_none_after_import():
    """Importing pool_ranker MUST NOT trigger a boto3 client construction."""
    import spotlight.pool_ranker as m

    assert m._default_client is None, (
        "pool_ranker._default_client must be None at import time (lazy init). "
        "Found a non-None client, which means boto3.client() was called eagerly."
    )


# ---------------------------------------------------------------------------
# #49 tiebreak determinism — (-impact_score, int(pmid), -year)
# ---------------------------------------------------------------------------


def test_intra_subtopic_tiebreak_pmid_then_year():
    """Equal impact_score within a subtopic: ascending int(pmid) wins, then year DESC.

    Builds 4 papers with the same impact_score where deterministic ordering
    requires both PMID-asc and year-desc:

      PMID      year   expected order key
      ---------------- -----------------
      "1000"    2024   (-50, 1000, -2024)
      "999"     2024   (-50, 999,  -2024)  ← smaller PMID wins
      "999"     —      (deduped against itself)
      "1000"    2025   would beat "1000" 2024 on year DESC

    The dedup at (subtopic, pmid) level means duplicate-PMID rows can't
    co-occur in the output, so the year tiebreaker exercises a different
    contract: distinct PMIDs with identical impact_score order by
    int(pmid) ascending; year is the third-level tiebreak (DESC).
    """
    from spotlight.pool_ranker import rank_pool

    year = date.today().year
    # Three distinct PMIDs, identical impact_score, different years.
    # Order should be int(pmid) ASC: 999, 1000, 10000 — even though
    # "10000" lexicographically sorts before "999".
    items = [
        _topic_item(pmid="10000", subtopic_id="aging_001", impact_score=50.0, year=year),
        _topic_item(pmid="999", subtopic_id="aging_001", impact_score=50.0, year=year - 1),
        _topic_item(pmid="1000", subtopic_id="aging_001", impact_score=50.0, year=year),
    ]
    result = rank_pool(client=StubDynamoClient(items))

    assert len(result) == 1
    pmids = [p.pmid for p in result[0].papers]
    # int cast: 999 < 1000 < 10000 (NOT lexicographic, which would be 10000 < 1000 < 999)
    assert pmids == ["999", "1000", "10000"], (
        f"int(pmid) ascending expected; got lexicographic-or-other order: {pmids}"
    )


def test_intra_subtopic_tiebreak_permutation_invariant():
    """Permuting input order MUST NOT change the output paper ordering.

    Builds 5 papers with overlapping impact_scores and runs the ranker
    twice with the items list in reversed order. Output must be identical
    bytes-for-bytes (PMID-list comparison is sufficient).
    """
    from spotlight.pool_ranker import rank_pool

    year = date.today().year
    items = [
        _topic_item(pmid="2001", subtopic_id="aging_001", impact_score=80.0, year=year),
        _topic_item(pmid="2002", subtopic_id="aging_001", impact_score=80.0, year=year),  # tie
        _topic_item(pmid="2003", subtopic_id="aging_001", impact_score=75.0, year=year - 1),
        _topic_item(pmid="2004", subtopic_id="aging_001", impact_score=75.0, year=year),  # tie + newer
        _topic_item(pmid="2005", subtopic_id="aging_001", impact_score=70.0, year=year),
    ]
    forward = rank_pool(client=StubDynamoClient(items))
    reversed_ = rank_pool(client=StubDynamoClient(list(reversed(items))))

    assert [p.pmid for p in forward[0].papers] == [p.pmid for p in reversed_[0].papers]


def test_non_digit_pmid_raises_at_sort():
    """Non-digit PMID surfaces as a ValueError when int() casts the sort key.

    The schema bans non-digit PMIDs (`pattern: "^[0-9]+$"`), so any malformed
    PMID upstream is a contract violation that should fail loud at ranker time
    rather than silently producing a non-deterministic ordering.
    """
    from spotlight.pool_ranker import rank_pool

    year = date.today().year
    items = [
        _topic_item(pmid="PMC1234567", subtopic_id="aging_001", impact_score=50.0, year=year),
        _topic_item(pmid="1234567", subtopic_id="aging_001", impact_score=50.0, year=year),
    ]
    try:
        rank_pool(client=StubDynamoClient(items))
    except ValueError:
        return
    raise AssertionError("non-digit PMID must raise ValueError at int() cast")

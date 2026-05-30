"""
Tests for aging_pilot_gate.py — Plan 04-05.

Covers the 4 behaviors specified in the PLAN.md <behavior> block.
All tests use synthetic inputs: no AWS calls, no DynamoDB, no file I/O.

TDD RED phase: these tests are written before the implementation exists.
"""

import pytest
import random


# ---------------------------------------------------------------------------
# Test 1: compute_coverage
# ---------------------------------------------------------------------------

def test_compute_coverage_pass():
    """
    100 items, 90 have primary_subtopic_id -> coverage = 0.9 -> PASS
    """
    from cli.aging_pilot_gate import compute_coverage

    items = []
    for i in range(90):
        items.append({"pmid": str(i), "score": 0.5, "primary_subtopic_id": "sub_a"})
    for i in range(90, 100):
        items.append({"pmid": str(i), "score": 0.5})

    coverage = compute_coverage(items)
    assert abs(coverage - 0.9) < 1e-9, f"Expected 0.9, got {coverage}"


def test_compute_coverage_fail():
    """
    5 items with primary_subtopic_id out of 10 -> coverage = 0.5 -> FAIL at 0.85 threshold
    """
    from cli.aging_pilot_gate import compute_coverage

    items = []
    for i in range(5):
        items.append({"pmid": str(i), "score": 0.5, "primary_subtopic_id": "sub_a"})
    for i in range(5, 10):
        items.append({"pmid": str(i), "score": 0.5})

    coverage = compute_coverage(items)
    assert abs(coverage - 0.5) < 1e-9, f"Expected 0.5, got {coverage}"


def test_compute_coverage_empty():
    """Empty items -> coverage = 0.0"""
    from cli.aging_pilot_gate import compute_coverage

    coverage = compute_coverage([])
    assert coverage == 0.0


def test_compute_coverage_all_assigned():
    """All items assigned -> coverage = 1.0"""
    from cli.aging_pilot_gate import compute_coverage

    items = [{"pmid": str(i), "score": 0.5, "primary_subtopic_id": "sub_x"} for i in range(20)]
    coverage = compute_coverage(items)
    assert abs(coverage - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# Test 2: compute_pairwise_overlap (Jaccard over min-cardinality)
# ---------------------------------------------------------------------------

def test_compute_pairwise_overlap_fail():
    """
    s1={1,2,3,4}, s2={3,4} -> intersection={3,4}=2, min(|s1|,|s2|)=min(4,2)=2
    overlap = 2/2 = 1.0 -> FAIL at 40% threshold
    """
    from cli.aging_pilot_gate import compute_pairwise_overlap

    pmid_sets = {
        "s1": {1, 2, 3, 4},
        "s2": {3, 4},
    }
    overlaps = compute_pairwise_overlap(pmid_sets)
    # Should return dict of (a, b) -> overlap_value
    assert len(overlaps) == 1
    key = list(overlaps.keys())[0]
    assert abs(overlaps[key] - 1.0) < 1e-9, f"Expected 1.0, got {overlaps[key]}"


def test_compute_pairwise_overlap_pass():
    """
    No overlap between subtopics -> all overlap = 0.0 -> PASS at 40% threshold
    """
    from cli.aging_pilot_gate import compute_pairwise_overlap

    pmid_sets = {
        "s1": {1, 2, 3},
        "s2": {4, 5, 6},
        "s3": {7, 8, 9},
    }
    overlaps = compute_pairwise_overlap(pmid_sets)
    assert len(overlaps) == 3  # C(3,2) = 3 pairs
    for key, val in overlaps.items():
        assert val == 0.0, f"Expected 0.0 for pair {key}, got {val}"


def test_compute_pairwise_overlap_partial():
    """
    s1={1,2,3,4}, s2={3,4,5,6} -> intersection={3,4}=2, min(4,4)=4
    overlap = 2/4 = 0.5
    """
    from cli.aging_pilot_gate import compute_pairwise_overlap

    pmid_sets = {
        "s1": {1, 2, 3, 4},
        "s2": {3, 4, 5, 6},
    }
    overlaps = compute_pairwise_overlap(pmid_sets)
    assert len(overlaps) == 1
    val = list(overlaps.values())[0]
    assert abs(val - 0.5) < 1e-9, f"Expected 0.5, got {val}"


def test_compute_pairwise_overlap_empty_subtopic():
    """Subtopic with no pmids: skip that pair to avoid division by zero."""
    from cli.aging_pilot_gate import compute_pairwise_overlap

    pmid_sets = {
        "s1": {1, 2, 3},
        "s2": set(),
    }
    # Should not raise; either skip or return 0 for pairs with empty sets
    overlaps = compute_pairwise_overlap(pmid_sets)
    # With empty s2, min-cardinality=0 -> overlap=0 or pair is skipped
    for val in overlaps.values():
        assert val == 0.0 or val is not None  # just must not raise


def test_compute_pairwise_overlap_single_subtopic():
    """Only one subtopic -> no pairs -> empty result."""
    from cli.aging_pilot_gate import compute_pairwise_overlap

    pmid_sets = {"s1": {1, 2, 3}}
    overlaps = compute_pairwise_overlap(pmid_sets)
    assert overlaps == {}


# ---------------------------------------------------------------------------
# Test 3: build_blind_check_worksheet
# ---------------------------------------------------------------------------

def test_build_blind_check_worksheet_count():
    """
    build_blind_check_worksheet returns exactly 10 dicts with n=10, seed=42
    """
    from cli.aging_pilot_gate import build_blind_check_worksheet

    activities = []
    for i in range(50):
        activities.append({
            "pmid": str(1000 + i),
            "title": f"Paper title {i}",
            "synopsis": f"Synopsis text {i}",
            "primary_subtopic_id": "sub_a",
        })

    subtopics = [
        {"id": "sub_a", "label": "Subtopic A", "description": "About A"},
        {"id": "sub_b", "label": "Subtopic B", "description": "About B"},
    ]

    worksheet = build_blind_check_worksheet(activities, subtopics, n=10, seed=42)
    assert len(worksheet) == 10, f"Expected 10 rows, got {len(worksheet)}"


def test_build_blind_check_worksheet_fields():
    """
    Each row must have pmid, title, synopsis, candidate_subtopics, reviewer_picks
    """
    from cli.aging_pilot_gate import build_blind_check_worksheet

    activities = [
        {"pmid": str(i), "title": f"Title {i}", "synopsis": f"Synopsis {i}",
         "primary_subtopic_id": "sub_a"}
        for i in range(20)
    ]

    subtopics = [
        {"id": "sub_a", "label": "Subtopic A", "description": "About A"},
    ]

    worksheet = build_blind_check_worksheet(activities, subtopics, n=5, seed=42)

    required_fields = {"pmid", "title", "synopsis", "candidate_subtopics", "reviewer_picks"}
    for row in worksheet:
        assert required_fields <= set(row.keys()), (
            f"Row missing fields. Got: {set(row.keys())}, expected: {required_fields}"
        )


def test_build_blind_check_worksheet_reviewer_picks_empty():
    """reviewer_picks must be empty (for reviewer to fill in)"""
    from cli.aging_pilot_gate import build_blind_check_worksheet

    activities = [
        {"pmid": str(i), "title": f"Title {i}", "synopsis": f"Synopsis {i}"}
        for i in range(20)
    ]
    subtopics = [{"id": "sub_a", "label": "A", "description": "desc"}]

    worksheet = build_blind_check_worksheet(activities, subtopics, n=5, seed=42)
    for row in worksheet:
        assert row["reviewer_picks"] == "" or row["reviewer_picks"] == [] or row["reviewer_picks"] is None, (
            f"reviewer_picks should be empty, got: {row['reviewer_picks']}"
        )


def test_build_blind_check_worksheet_reproducible():
    """Same seed => same sample (reproducible via random.Random(seed))"""
    from cli.aging_pilot_gate import build_blind_check_worksheet

    activities = [
        {"pmid": str(i), "title": f"Title {i}", "synopsis": f"Synopsis {i}"}
        for i in range(100)
    ]
    subtopics = [{"id": "sub_a", "label": "A", "description": "desc"}]

    ws1 = build_blind_check_worksheet(activities, subtopics, n=10, seed=42)
    ws2 = build_blind_check_worksheet(activities, subtopics, n=10, seed=42)

    pmids1 = [row["pmid"] for row in ws1]
    pmids2 = [row["pmid"] for row in ws2]
    assert pmids1 == pmids2, "Same seed must produce same sample"


def test_build_blind_check_worksheet_different_seeds():
    """Different seeds should produce different samples (probabilistically)"""
    from cli.aging_pilot_gate import build_blind_check_worksheet

    activities = [
        {"pmid": str(i), "title": f"Title {i}", "synopsis": f"Synopsis {i}"}
        for i in range(100)
    ]
    subtopics = [{"id": "sub_a", "label": "A", "description": "desc"}]

    ws1 = build_blind_check_worksheet(activities, subtopics, n=10, seed=42)
    ws2 = build_blind_check_worksheet(activities, subtopics, n=10, seed=99)

    pmids1 = [row["pmid"] for row in ws1]
    pmids2 = [row["pmid"] for row in ws2]
    # With 100 items and n=10, different seeds almost certainly produce different samples
    assert pmids1 != pmids2, "Different seeds should produce different samples"


def test_build_blind_check_worksheet_fewer_than_n():
    """If fewer than n activities, return all of them"""
    from cli.aging_pilot_gate import build_blind_check_worksheet

    activities = [
        {"pmid": str(i), "title": f"Title {i}", "synopsis": f"Synopsis {i}"}
        for i in range(5)
    ]
    subtopics = [{"id": "sub_a", "label": "A", "description": "desc"}]

    worksheet = build_blind_check_worksheet(activities, subtopics, n=10, seed=42)
    assert len(worksheet) == 5, f"Expected 5 (all available), got {len(worksheet)}"


# ---------------------------------------------------------------------------
# Test 4: knee_point
# ---------------------------------------------------------------------------

def test_knee_point_detects_drop():
    """
    weights_dict = {a:1000, b:900, c:800, d:100, e:50}
    Largest relative drop is between c=800 and d=100 (ratio 8x).
    knee_point returns d (=100) as the candidate floor — value in (50, 800).
    """
    from cli.aging_pilot_gate import knee_point

    weights = {"a": 1000, "b": 900, "c": 800, "d": 100, "e": 50}
    result = knee_point(weights)
    # Plan spec: return w[i+1] where i is the position of largest relative drop
    # Largest drop: 800->100 (ratio=8). Returns w[i+1]=100.
    assert 50 < result < 800, f"knee_point should return value in (50, 800), got {result}"
    assert result == 100.0, f"Expected 100.0 (d's value), got {result}"


def test_knee_point_uniform_weights():
    """Uniform weights have no knee — return the second-to-last or last value"""
    from cli.aging_pilot_gate import knee_point

    weights = {"a": 100, "b": 100, "c": 100, "d": 100}
    result = knee_point(weights)
    # With uniform weights, all ratios are 1.0 — knee is at i=0, return w[1]=100
    assert result == 100, f"Expected 100 for uniform weights, got {result}"


def test_knee_point_single_value():
    """Single entry — return that value"""
    from cli.aging_pilot_gate import knee_point

    weights = {"only": 500}
    result = knee_point(weights)
    assert result == 500, f"Expected 500, got {result}"


def test_knee_point_two_values():
    """Two entries — knee is between them; return the second"""
    from cli.aging_pilot_gate import knee_point

    weights = {"a": 1000, "b": 10}
    result = knee_point(weights)
    assert result == 10, f"Expected 10, got {result}"


def test_knee_point_returns_float():
    """Return type should be numeric (int or float)"""
    from cli.aging_pilot_gate import knee_point

    weights = {"a": 1000.5, "b": 500.5, "c": 50.5}
    result = knee_point(weights)
    assert isinstance(result, (int, float)), f"Expected numeric, got {type(result)}"

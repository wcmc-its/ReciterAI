"""Tests for the #222 read-time live-intersection filter and its shared
predicate (utils.attribution), plus the aggregate_subtopic_scores insertion.

These pin the non-destructive prevention slice: stale (faculty, pmid) rows are
EXCLUDED from reads, no rows are deleted, and the predicate is derived at the
all-positions mint scope (so middle-author rows are never wrongly dropped).
"""

from __future__ import annotations

from cli import aggregate_subtopic_scores as agg
from utils.attribution import (
    is_stale,
    load_live_attribution_pairs,
    pairs_from_author_mapping,
    strip_faculty_prefix,
)

# A live mapping with first, last, middle, and NULL-position authors — the
# all-positions mint scope. pmid "100" has three faculty; pmid "200" has one.
_LIVE_MAPPING = {
    "100": [
        {"cwid": "first01", "position": "first"},
        {"cwid": "mid0001", "position": "middle"},
        {"cwid": "null0001", "position": ""},
    ],
    "200": [{"cwid": "last001", "position": "last"}],
}
_LIVE = pairs_from_author_mapping(_LIVE_MAPPING)


# --- shared predicate -------------------------------------------------------

def test_pairs_from_author_mapping_keeps_all_positions():
    assert _LIVE == {
        ("first01", "100"),
        ("mid0001", "100"),
        ("null0001", "100"),
        ("last001", "200"),
    }


def test_strip_faculty_prefix():
    assert strip_faculty_prefix("cwid_abc1234") == "abc1234"
    assert strip_faculty_prefix("abc1234") == "abc1234"
    assert strip_faculty_prefix("") == ""


def test_is_stale_true_for_departed_pair():
    # Author no longer attributed to this pmid.
    assert is_stale(_LIVE, "cwid_first01", "999") is True
    assert is_stale(_LIVE, "cwid_gone999", "100") is True


def test_is_stale_false_for_live_pair_including_middle_author():
    assert is_stale(_LIVE, "cwid_first01", "100") is False
    # Middle and NULL-position authors are live — the load-bearing case a
    # first/last-scoped predicate would get wrong.
    assert is_stale(_LIVE, "cwid_mid0001", "100") is False
    assert is_stale(_LIVE, "cwid_null0001", "100") is False


def test_is_stale_false_for_unresolvable_row():
    # No clean (cwid, pmid) -> never asserted stale (kept on read, never culled).
    assert is_stale(_LIVE, "", "100") is False
    assert is_stale(_LIVE, "cwid_first01", "") is False


def test_load_live_attribution_pairs_accepts_injected_mapping():
    # Injected mapping path does no DB I/O.
    assert load_live_attribution_pairs(_LIVE_MAPPING) == _LIVE


# --- aggregate_subtopic_scores insertion ------------------------------------

def _row(faculty_uid, pmid, primary="sub_a", score="0.9", impact="50"):
    return {
        "faculty_uid": faculty_uid,
        "pmid": pmid,
        "primary_subtopic_id": primary,
        "score": score,
        "impact_score": impact,
    }


def test_filter_rows_to_live_drops_only_stale():
    rows = [
        _row("cwid_first01", "100"),   # live
        _row("cwid_mid0001", "100"),   # live (middle author)
        _row("cwid_first01", "999"),   # stale (pmid departed)
        _row("cwid_gone999", "100"),   # stale (faculty departed)
        _row("cwid_last001", "200"),   # live
    ]
    kept = agg._filter_rows_to_live(rows, live_pairs=_LIVE)
    assert len(kept) == 3
    assert {(r["faculty_uid"], r["pmid"]) for r in kept} == {
        ("cwid_first01", "100"),
        ("cwid_mid0001", "100"),
        ("cwid_last001", "200"),
    }


def test_filter_rows_to_live_keeps_unresolvable_rows():
    rows = [
        _row("", "100"),               # no faculty_uid -> kept (unresolvable)
        _row("cwid_first01", ""),      # no pmid -> kept (unresolvable)
        _row("cwid_first01", "999"),   # stale -> dropped
    ]
    kept = agg._filter_rows_to_live(rows, live_pairs=_LIVE)
    assert len(kept) == 2


def test_filter_rows_to_live_is_nondestructive_passthrough_when_all_live():
    rows = [_row("cwid_first01", "100"), _row("cwid_last001", "200")]
    kept = agg._filter_rows_to_live(rows, live_pairs=_LIVE)
    assert kept == rows


def test_filtered_rows_feed_aggregation_with_correct_lower_sum():
    """End-to-end: a stale row's article_score is excluded from the subtopic
    total, yielding the correct lower sum (the user-facing harm fix)."""
    rows = [
        _row("cwid_first01", "100", primary="sub_a", score="0.9", impact="50"),
        _row("cwid_gone999", "100", primary="sub_a", score="0.9", impact="50"),  # stale
    ]
    kept = agg._filter_rows_to_live(rows, live_pairs=_LIVE)
    _, totals = agg._aggregate_exclusive(kept)
    _, totals_unfiltered = agg._aggregate_exclusive(rows)
    # The stale row would have doubled sub_a's weight; filtering halves it.
    assert totals["sub_a"] < totals_unfiltered["sub_a"]
    assert abs(totals["sub_a"] - totals_unfiltered["sub_a"] / 2) < 1e-9

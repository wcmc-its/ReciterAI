"""Unit tests for pipeline_tools.corpus (A2 corpus grouping/merge — pure, no DB)."""
from __future__ import annotations

from pipeline_tools.corpus import group_authorship_rows, merge_corpus


def test_group_maps_positions_to_roles_and_dedups():
    rows = [
        {"pmid": "100", "cwid": "aaa1", "author_position": "first"},
        {"pmid": "100", "cwid": "bbb2", "author_position": "last"},
        {"pmid": "100", "cwid": "aaa1", "author_position": "first"},   # dup
        {"pmid": "200", "cwid": "ccc3", "author_position": "last"},
    ]
    grouped = group_authorship_rows(rows)
    # Sorted by (role, cwid): ('lead','aaa1') < ('senior','bbb2'); dup collapsed.
    assert grouped["100"] == [
        {"cwid": "aaa1", "author_role": "lead"},
        {"cwid": "bbb2", "author_role": "senior"},
    ]
    assert grouped["200"] == [{"cwid": "ccc3", "author_role": "senior"}]


def test_group_is_stable_order():
    rows = [
        {"pmid": "1", "cwid": "z9", "author_position": "last"},
        {"pmid": "1", "cwid": "a1", "author_position": "first"},
    ]
    # Sorted by (role, cwid): ('lead','a1') < ('senior','z9').
    assert group_authorship_rows(rows)["1"] == [
        {"cwid": "a1", "author_role": "lead"},
        {"cwid": "z9", "author_role": "senior"},
    ]


def test_group_skips_unmapped_position_and_blanks():
    rows = [
        {"pmid": "1", "cwid": "a1", "author_position": "middle"},   # not first/last
        {"pmid": "", "cwid": "a1", "author_position": "first"},     # blank pmid
        {"pmid": "2", "cwid": "", "author_position": "first"},      # blank cwid
        {"pmid": "3", "cwid": "ok", "author_position": "FIRST"},    # case-insensitive
    ]
    grouped = group_authorship_rows(rows)
    assert grouped == {"3": [{"cwid": "ok", "author_role": "lead"}]}


def test_merge_attaches_authors_by_pmid():
    pub_rows = [
        {"pmid": "100", "articleTitle": "T1", "abstractVarchar": "A1"},
        {"pmid": "200", "articleTitle": "T2", "abstractVarchar": "A2"},
    ]
    authorship = {
        "100": [{"cwid": "aaa1", "author_role": "lead"}],
        "200": [{"cwid": "ccc3", "author_role": "senior"}],
    }
    merged = merge_corpus(pub_rows, authorship)
    assert merged[0]["authors"] == [{"cwid": "aaa1", "author_role": "lead"}]
    assert merged[0]["articleTitle"] == "T1"   # original columns preserved
    assert merged[1]["authors"] == [{"cwid": "ccc3", "author_role": "senior"}]


def test_merge_carries_missing_authorship_as_empty_not_dropped():
    pub_rows = [{"pmid": "999", "articleTitle": "orphan"}]
    merged = merge_corpus(pub_rows, {})
    assert len(merged) == 1
    assert merged[0]["authors"] == []

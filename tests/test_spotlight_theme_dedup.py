"""Tests for spotlight/theme_dedup.py — #91 near-clone subtopic detection.

Every test injects a fake ``embed`` callable returning canned vectors, so no
AWS / Bedrock call ever happens. ``_cosine`` of the canned vectors is fully
predictable, which makes the expected adjacency exact.

Behaviors:
  1. find_near_clones builds a symmetric adjacency; the v2026-05-16
     astro-omics x3 and disparities x3 clusters each link mutually with no
     cross-cluster link (the #91 reproduction).
  2. adjacency is NON-transitive: cos(A,B) >= t and cos(B,C) >= t but
     cos(A,C) < t  =>  adjacency == {A:{B}, B:{A,C}, C:{B}}.
  3. a blank / whitespace-only description gets an empty adjacency entry and
     never appears in a pair.
  4. the threshold is inclusive (cosine == threshold counts as a near-clone).
  5. ranked_pairs is sorted by cosine descending and floored at
     threshold - RANKED_PAIR_FLOOR_MARGIN.
  6. find_near_clones raises ValueError when embed returns the wrong count.
  7. fewer than two embeddable descriptions -> an empty scan, embed not
     called.
"""

from __future__ import annotations

import math

import pytest

from spotlight.theme_dedup import (
    NearClones,
    RANKED_PAIR_FLOOR_MARGIN,
    _cosine,
    find_near_clones,
)


# ---------------------------------------------------------------------------
# Helpers / canned vectors
# ---------------------------------------------------------------------------


def _fake_embed(vectors_by_text):
    """Build an ``embed`` callable mapping each text to a canned vector.

    A KeyError here means the test wired ``descriptions`` and
    ``vectors_by_text`` inconsistently — surfacing it is intentional.
    """

    def embed(texts):
        return [vectors_by_text[t] for t in texts]

    return embed


# astro-omics cluster: three unit vectors mutually >= 0.92 cosine.
_A1 = [1.0, 0.0, 0.0, 0.0]
_A2 = [0.96, 0.28, 0.0, 0.0]
_A3 = [0.96, 0.0, 0.28, 0.0]
# disparities cluster: three unit vectors clustered on a different axis;
# every astro/disparities cross-pair is <= 0.28 cosine.
_D1 = [0.0, 0.0, 0.0, 1.0]
_D2 = [0.0, 0.0, 0.28, 0.96]
_D3 = [0.28, 0.0, 0.0, 0.96]


# ---------------------------------------------------------------------------
# 1. v2026-05-16 reproduction — two near-clone clusters
# ---------------------------------------------------------------------------


def test_v2026_05_16_reproduction_two_clusters():
    """The three astro-omics and three disparities subtopics each form a
    mutually-linked clique, with no link across the two clusters."""
    descriptions = {
        "genetics_spaceflight_omics": "astro1",
        "single_spaceflight_multiomics": "astro2",
        "systems_spaceflight_omics": "astro3",
        "cardio_health_disparities": "disp1",
        "onco_outcome_differences": "disp2",
        "neuro_access_gaps": "disp3",
    }
    vectors = {
        "astro1": _A1, "astro2": _A2, "astro3": _A3,
        "disp1": _D1, "disp2": _D2, "disp3": _D3,
    }
    nc = find_near_clones(descriptions, 0.8, embed=_fake_embed(vectors))

    astro = {
        "genetics_spaceflight_omics",
        "single_spaceflight_multiomics",
        "systems_spaceflight_omics",
    }
    disparities = {
        "cardio_health_disparities",
        "onco_outcome_differences",
        "neuro_access_gaps",
    }
    for sid in astro:
        assert nc.adjacency[sid] == astro - {sid}
    for sid in disparities:
        assert nc.adjacency[sid] == disparities - {sid}
    # 6 within-cluster pairs reported; every cross-cluster pair is far below
    # the print floor (0.8 - 0.10 = 0.70) and absent.
    assert len(nc.ranked_pairs) == 6
    assert nc.threshold == 0.8


# ---------------------------------------------------------------------------
# 2. adjacency is non-transitive
# ---------------------------------------------------------------------------


def test_adjacency_is_non_transitive():
    """A near-clone CHAIN does not collapse: cos(A,B) and cos(B,C) clear the
    threshold but cos(A,C) does not, so A is not linked to C."""
    # Unit vectors at 0deg, 30deg, 60deg: cos(A,B)=cos(B,C)=cos30~0.866>=0.8;
    # cos(A,C)=cos60=0.5<0.8.
    a = [1.0, 0.0]
    b = [math.cos(math.radians(30)), math.sin(math.radians(30))]
    c = [math.cos(math.radians(60)), math.sin(math.radians(60))]
    descriptions = {"a": "ta", "b": "tb", "c": "tc"}
    nc = find_near_clones(
        descriptions, 0.8, embed=_fake_embed({"ta": a, "tb": b, "tc": c})
    )
    assert nc.adjacency == {"a": {"b"}, "b": {"a", "c"}, "c": {"b"}}


# ---------------------------------------------------------------------------
# 3. blank description
# ---------------------------------------------------------------------------


def test_blank_description_gets_empty_adjacency_entry():
    """A whitespace-only description is not embedded; the subtopic keeps an
    empty adjacency entry and never appears in a ranked pair."""
    descriptions = {
        "x": "real text one",
        "y": "real text two",
        "blank": "   ",
    }
    # x and y are near-clones; blank is whitespace-only.
    vectors = {"real text one": [1.0, 0.0], "real text two": [0.99, 0.141]}
    nc = find_near_clones(descriptions, 0.8, embed=_fake_embed(vectors))
    assert nc.adjacency["blank"] == set()
    assert nc.adjacency["x"] == {"y"}
    assert nc.adjacency["y"] == {"x"}
    for sid_a, sid_b, _ in nc.ranked_pairs:
        assert "blank" not in (sid_a, sid_b)


# ---------------------------------------------------------------------------
# 4. threshold boundary is inclusive
# ---------------------------------------------------------------------------


def test_threshold_boundary_is_inclusive():
    """cosine == threshold counts as a near-clone (the gate uses >=)."""
    v1 = [1.0, 0.0]
    v2 = [0.8, 0.6]
    # Use the module's own _cosine so the threshold lands on the exact float
    # the scan computes — exact-equality on a derived float is otherwise not
    # reliably testable.
    exact = _cosine(v1, v2)
    descriptions = {"p": "tp", "q": "tq"}
    embed = _fake_embed({"tp": v1, "tq": v2})

    nc_at = find_near_clones(descriptions, exact, embed=embed)
    assert nc_at.adjacency == {"p": {"q"}, "q": {"p"}}

    nc_above = find_near_clones(descriptions, exact + 1e-9, embed=embed)
    assert nc_above.adjacency == {"p": set(), "q": set()}


# ---------------------------------------------------------------------------
# 5. ranked_pairs ordering + floor
# ---------------------------------------------------------------------------


def test_ranked_pairs_sorted_descending_and_floored():
    """ranked_pairs is sorted by cosine descending and excludes any pair
    below threshold - RANKED_PAIR_FLOOR_MARGIN."""
    a = [1.0, 0.0, 0.0]
    b = [0.98, 0.20, 0.0]   # cos(a,b) ~ 0.98
    c = [0.93, 0.0, 0.37]   # cos(a,c) ~ 0.93, cos(b,c) ~ 0.91
    d = [0.0, 0.0, 1.0]     # cos(*,d): a~0, b~0, c~0.37 — all below the floor
    descriptions = {"a": "ta", "b": "tb", "c": "tc", "d": "td"}
    vectors = {"ta": a, "tb": b, "tc": c, "td": d}
    nc = find_near_clones(descriptions, 0.8, embed=_fake_embed(vectors))

    cosines = [cos for _, _, cos in nc.ranked_pairs]
    assert cosines == sorted(cosines, reverse=True)
    floor = 0.8 - RANKED_PAIR_FLOOR_MARGIN
    assert all(cos >= floor for cos in cosines)
    # The far pairs involving d sit below the floor and are absent.
    reported = {frozenset((x, y)) for x, y, _ in nc.ranked_pairs}
    assert frozenset(("a", "d")) not in reported
    assert frozenset(("c", "d")) not in reported
    # a, b, c are mutually near-clones; d is linked to nothing.
    assert nc.adjacency["d"] == set()


# ---------------------------------------------------------------------------
# 6. embed contract violation
# ---------------------------------------------------------------------------


def test_embed_count_mismatch_raises():
    """find_near_clones rejects an embed callable that returns the wrong
    number of vectors."""

    def bad_embed(texts):
        return [[1.0, 0.0]]  # one vector for two input texts

    with pytest.raises(ValueError, match="one vector per text"):
        find_near_clones({"a": "ta", "b": "tb"}, 0.8, embed=bad_embed)


# ---------------------------------------------------------------------------
# 7. degenerate inputs
# ---------------------------------------------------------------------------


def test_single_description_is_an_empty_scan_without_calling_embed():
    """One description -> no pair to compare; embed is never called."""

    def embed_must_not_be_called(texts):
        raise AssertionError("embed should not be called for a single sid")

    nc = find_near_clones(
        {"solo": "text"}, 0.8, embed=embed_must_not_be_called
    )
    assert nc.adjacency == {"solo": set()}
    assert nc.ranked_pairs == []
    assert nc.threshold == 0.8


def test_empty_descriptions_returns_empty_scan():
    """No descriptions -> empty adjacency, empty ranked_pairs."""
    nc = find_near_clones({}, 0.8, embed=_fake_embed({}))
    assert nc.adjacency == {}
    assert nc.ranked_pairs == []
    assert isinstance(nc, NearClones)

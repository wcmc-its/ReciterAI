"""
Unit tests for the cross-topic subtopic dedup decision engine (issue #164).

Every test injects a fake ``embed`` callable returning canned unit vectors, so
no AWS/Bedrock call is made. Identical vectors -> cosine 1.0; orthogonal
vectors -> cosine 0.0. The article-overlap signal uses real PMID sets through
the reused D-23 metric (`compute_pairwise_overlap`).

Coverage:
  1. Symmetric pair, high article overlap + low cosine -> MERGE (spaceflight case).
  2. Symmetric pair, low overlap + high cosine -> MERGE (same wording, diff papers).
  3. Asymmetric containment (50 ⊂ 400-style) -> FLAG parent/child, never merge.
  4. Within-topic pair -> ignored (cross-topic only, D-05).
  5. Transitive merge a~b~c across three topics -> one group, canonical by weight.
  6. No-signal pair -> empty plan.
  7. load_dedup_thresholds reads config keys and falls back to defaults.
"""
from __future__ import annotations

from pipeline_hierarchy.subtopic_dedup import (
    DEFAULT_ARTICLE_OVERLAP_MIN,
    DEFAULT_COSINE_MIN,
    Subtopic,
    decide_dedup,
    load_dedup_thresholds,
    overlap_adjacency,
    union_adjacency,
)


# Orthogonal unit vectors -> cosine 0; reuse the same vector for cosine 1.0.
_E1 = [1.0, 0.0, 0.0, 0.0]
_E2 = [0.0, 1.0, 0.0, 0.0]
_E3 = [0.0, 0.0, 1.0, 0.0]


def _fake_embed(vectors_by_text):
    """Return an ``embed`` callable mapping each description text to its vector."""

    def embed(texts):
        return [vectors_by_text[t] for t in texts]

    return embed


def test_symmetric_high_overlap_low_cosine_merges():
    """Same papers, different wording -> merge on the article-overlap signal."""
    subs = [
        Subtopic("a", "genetics", "desc a", frozenset(range(1, 11)), 5.0, 10),
        Subtopic(
            "b",
            "systems_biology",
            "desc b",
            frozenset({1, 2, 3, 4, 5, 11, 12, 13, 14, 15}),
            3.0,
            10,
        ),
    ]
    # overlap = |{1..5}| / min(10, 10) = 0.5 >= 0.40; cosine orthogonal -> 0.
    plan = decide_dedup(subs, embed=_fake_embed({"desc a": _E1, "desc b": _E2}))

    assert len(plan.merges) == 1
    group = plan.merges[0]
    assert group.canonical_id == "a"  # higher total_weight (5 > 3)
    assert group.member_ids == ["b"]
    assert sorted(group.topic_ids) == ["genetics", "systems_biology"]
    assert plan.flags == []
    assert plan.pairs_considered == 1
    # Evidence records the article-overlap signal, not cosine.
    assert "article_overlap" in group.evidence[0]["signal"]


def test_symmetric_high_cosine_low_overlap_merges():
    """Different papers, near-identical descriptions -> merge on the cosine signal."""
    subs = [
        Subtopic("a", "t1", "desc a", frozenset({1, 2, 3}), 2.0, 3),
        Subtopic("b", "t2", "desc b", frozenset({4, 5, 6}), 4.0, 3),
    ]
    # overlap 0; identical vectors -> cosine 1.0 >= 0.75.
    plan = decide_dedup(subs, embed=_fake_embed({"desc a": _E1, "desc b": _E1}))

    assert len(plan.merges) == 1
    assert plan.merges[0].canonical_id == "b"  # higher total_weight (4 > 2)
    assert plan.merges[0].member_ids == ["a"]
    assert plan.flags == []
    assert "cosine" in plan.merges[0].evidence[0]["signal"]


def test_asymmetric_containment_flags_not_merges():
    """A small subtopic fully inside a large one is parent/child, never merged."""
    small = Subtopic("small", "t1", "desc s", frozenset(range(1, 11)), 1.0, 10)
    large = Subtopic(
        "large",
        "t2",
        "desc l",
        frozenset(range(1, 11)) | frozenset(range(101, 141)),  # 50 pmids, contains all 10
        9.0,
        50,
    )
    # overlap = 10/min(10,50) = 1.0; ratio = 5x >= 3 -> asymmetric.
    plan = decide_dedup(
        [small, large], embed=_fake_embed({"desc s": _E1, "desc l": _E2})
    )

    assert plan.merges == []  # the granularity is preserved
    assert len(plan.flags) == 1
    flag = plan.flags[0]
    assert flag.parent_id == "large"  # larger membership is the parent
    assert flag.child_id == "small"
    assert flag.parent_topic_id == "t2"
    assert flag.article_overlap == 1.0
    assert flag.size_ratio == 5.0
    assert "containment" in flag.reason


def test_within_topic_pair_is_ignored():
    """Same-parent pairs are the discovery pass's job (D-05) — never considered."""
    subs = [
        Subtopic("a", "same_topic", "desc a", frozenset(range(1, 11)), 5.0, 10),
        Subtopic("b", "same_topic", "desc b", frozenset(range(1, 11)), 3.0, 10),
    ]
    # Identical pmids AND identical vectors would merge if cross-topic — but same topic.
    plan = decide_dedup(subs, embed=_fake_embed({"desc a": _E1, "desc b": _E1}))

    assert plan.merges == []
    assert plan.flags == []
    assert plan.pairs_considered == 0


def test_component_grouping_is_transitive():
    """grouping='component': a~b and b~c (not a~c) collapse into one group."""
    a = Subtopic("a", "t1", "desc a", frozenset(range(1, 11)), 5.0, 10)
    b = Subtopic(
        "b", "t2", "desc b", frozenset({1, 2, 3, 4, 5, 6, 21, 22, 23, 24}), 9.0, 10
    )  # a∩b = {1..6} -> 0.6 ; b∩c = {21..24} -> 0.4 ; a∩c = 0
    c = Subtopic("c", "t3", "desc c", frozenset(range(21, 31)), 2.0, 10)
    plan = decide_dedup(
        [a, b, c],
        grouping="component",
        embed=_fake_embed({"desc a": _E1, "desc b": _E2, "desc c": _E3}),
    )

    assert len(plan.merges) == 1
    group = plan.merges[0]
    assert group.canonical_id == "b"  # weight 9 is highest
    assert group.member_ids == ["a", "c"]
    assert group.topic_ids == ["t1", "t2", "t3"]
    assert plan.pairs_considered == 3  # (a,b), (a,c), (b,c)


def test_clique_grouping_breaks_chains():
    """grouping='clique' (default): a~b~c with NO a~c does NOT chain into one blob.

    This is the fix for the probe's 100-member transitive blob: c rides no
    overlap chain into the {a, b} cluster.
    """
    a = Subtopic("a", "t1", "desc a", frozenset(range(1, 11)), 5.0, 10)
    b = Subtopic(
        "b", "t2", "desc b", frozenset({1, 2, 3, 4, 5, 6, 21, 22, 23, 24}), 9.0, 10
    )
    c = Subtopic("c", "t3", "desc c", frozenset(range(21, 31)), 2.0, 10)
    plan = decide_dedup(
        [a, b, c],
        embed=_fake_embed({"desc a": _E1, "desc b": _E2, "desc c": _E3}),
    )

    # Only the tight {a, b} pair merges; c is not chained in.
    assert len(plan.merges) == 1
    group = plan.merges[0]
    assert set([group.canonical_id, *group.member_ids]) == {"a", "b"}
    assert "c" not in group.member_ids


def test_clique_grouping_merges_full_triangle():
    """grouping='clique': when ALL three pairwise overlap, the 3-clique merges."""
    a = Subtopic("a", "t1", "desc a", frozenset(range(1, 11)), 5.0, 10)
    b = Subtopic(
        "b", "t2", "desc b", frozenset({1, 2, 3, 4, 5, 6, 11, 12, 13, 14}), 9.0, 10
    )  # a∩b=.6
    c = Subtopic(
        "c", "t3", "desc c", frozenset({1, 2, 3, 4, 11, 12, 13, 14, 21, 22}), 2.0, 10
    )  # a∩c=.4 ; b∩c=.8
    plan = decide_dedup(
        [a, b, c],
        embed=_fake_embed({"desc a": _E1, "desc b": _E2, "desc c": _E3}),
    )

    assert len(plan.merges) == 1
    group = plan.merges[0]
    assert group.canonical_id == "b"  # highest weight in the clique
    assert group.member_ids == ["a", "c"]


def test_no_signal_pair_produces_empty_plan():
    subs = [
        Subtopic("a", "t1", "desc a", frozenset({1, 2, 3}), 1.0, 3),
        Subtopic("b", "t2", "desc b", frozenset({4, 5, 6}), 1.0, 3),
    ]
    plan = decide_dedup(subs, embed=_fake_embed({"desc a": _E1, "desc b": _E2}))

    assert plan.merges == []
    assert plan.flags == []
    assert plan.pairs_considered == 1


def test_load_dedup_thresholds_reads_config_and_falls_back():
    full = {
        "hierarchy_dedup_article_overlap_min": 0.55,
        "hierarchy_dedup_cosine_min": 0.8,
        "hierarchy_dedup_containment_ratio": 4.0,
        "hierarchy_dedup_flag_overlap_min": 0.7,
    }
    got = load_dedup_thresholds(full)
    assert got == {
        "article_overlap_min": 0.55,
        "cosine_min": 0.8,
        "containment_ratio": 4.0,
        "flag_overlap_min": 0.7,
    }

    fallback = load_dedup_thresholds({})
    assert fallback["article_overlap_min"] == DEFAULT_ARTICLE_OVERLAP_MIN
    assert fallback["cosine_min"] == DEFAULT_COSINE_MIN


def test_overlap_adjacency_symmetric_edge():
    """Symmetric pair above the overlap floor gets a symmetric edge."""
    pmid_sets = {
        "a": {1, 2, 3, 4, 5, 6, 7, 8, 9, 10},
        "b": {1, 2, 3, 4, 5, 11, 12, 13, 14, 15},  # overlap 5/10 = 0.5
        "c": {100, 101, 102},  # disjoint
    }
    adj = overlap_adjacency(pmid_sets, article_overlap_min=0.4, containment_ratio=3.0)
    assert adj["a"] == {"b"}
    assert adj["b"] == {"a"}
    assert adj["c"] == set()  # every sid present, isolated nodes empty


def test_overlap_adjacency_skips_containment():
    """A small subtopic inside a large one (ratio >= 3) is NOT an edge."""
    pmid_sets = {
        "small": set(range(1, 11)),  # 10
        "large": set(range(1, 11)) | set(range(101, 141)),  # 50, contains small
    }
    # overlap = 10/10 = 1.0 but ratio = 5x -> exempt.
    adj = overlap_adjacency(pmid_sets, article_overlap_min=0.4, containment_ratio=3.0)
    assert adj["small"] == set()
    assert adj["large"] == set()


def test_overlap_adjacency_below_floor_no_edge():
    pmid_sets = {"a": {1, 2, 3, 4, 5}, "b": {5, 6, 7, 8, 9}}  # overlap 1/5 = 0.2
    adj = overlap_adjacency(pmid_sets, article_overlap_min=0.4, containment_ratio=3.0)
    assert adj["a"] == set()
    assert adj["b"] == set()


def test_union_adjacency_merges_edge_sets():
    cosine = {"a": {"b"}, "b": {"a"}, "c": set()}
    overlap = {"a": {"c"}, "c": {"a"}, "d": {"e"}, "e": {"d"}}
    out = union_adjacency(cosine, overlap)
    assert out["a"] == {"b", "c"}
    assert out["c"] == {"a"}
    assert out["d"] == {"e"}
    # inputs untouched
    assert cosine["a"] == {"b"}


def test_probe_loader_reads_augmented_files(tmp_path):
    """build_subtopics_from_augmented maps augmented JSON -> Subtopic records."""
    import json as _json

    from cli.probe_subtopic_overlap import build_subtopics_from_augmented

    (tmp_path / "hierarchy_augmented_t1.json").write_text(
        _json.dumps(
            {
                "topic_id": "t1",
                "subtopics": [
                    {
                        "id": "t1_alpha",
                        "display_name": "Alpha Theme",
                        "short_description": "alpha desc",
                        "seed_pmids": [1, 2, 3],
                        "total_weight": 4.5,
                        "activity_count": 7,
                    }
                ],
            }
        )
    )
    subs = build_subtopics_from_augmented(tmp_path)
    assert len(subs) == 1
    s = subs[0]
    assert s.id == "t1_alpha"
    assert s.topic_id == "t1"
    assert s.description == "alpha desc"
    assert s.pmids == frozenset({1, 2, 3})
    assert s.total_weight == 4.5
    assert s.activity_count == 7
    assert s.label == "Alpha Theme"

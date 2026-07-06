"""Self-check for the eval-harness math (no network). Run: pytest tests/test_grant_eval.py"""
import math

from pipeline_grants.grant_eval import (
    backtest_ranks,
    dcg,
    ndcg_at_k,
    precision_at_k,
    awardees_for,
)


def test_ndcg_perfect_order_is_1():
    rels = [3, 2, 1, 0]
    assert ndcg_at_k(rels, 4) == 1.0


def test_ndcg_penalizes_bad_order():
    # a leaked off-topic pub at #1 (the Boyraz/Arifuzzaman case) must drop nDCG
    good = ndcg_at_k([3, 2, 1], 3)
    bad = ndcg_at_k([0, 2, 3], 3)
    assert bad < good == 1.0
    # IDCG normalizes by the sorted SAME multiset: ideal of [0,2,3] is [3,2,0]
    exp = dcg([0, 2, 3]) / dcg([3, 2, 0])
    assert math.isclose(bad, exp)


def test_ndcg_all_zero_is_zero():
    assert ndcg_at_k([0, 0, 0], 3) == 0.0


def test_precision_at_k_threshold():
    # fits >= 2 count as relevant
    assert precision_at_k([3, 2, 1, 0], 4) == 0.5
    assert precision_at_k([3, 3], 5) == 1.0
    assert precision_at_k([], 5) == 0.0


def test_backtest_finds_awardees_and_ranks():
    pool = ["a", "b", "c", "d", "e"]
    r = backtest_ranks(pool, {"a", "d", "z"})  # z not in pool
    assert r["awardees"] == 3
    assert r["in_pool"] == 2
    assert r["ranks"] == [1, 4]
    # top-of-pool percentile is highest
    assert r["percentiles"][0] > r["percentiles"][1]


def test_awardees_for_prefers_explicit_field():
    dump = {"grant": "x", "awardees": ["p1", "p2"], "solicitation_title": "X"}
    assert awardees_for(dump, {"x": {"other"}}) == {"p1", "p2"}


def test_awardees_for_title_containment_join():
    dump = {"grant": "worldquant", "solicitation_title": "WorldQuant Foundation Research Scholar Award"}
    db = {"worldquant foundation research scholar award": {"qiz4006"}}
    assert awardees_for(dump, db) == {"qiz4006"}


if __name__ == "__main__":
    import sys
    import subprocess

    sys.exit(subprocess.call(["pytest", "-q", __file__]))

"""Tests for the brick B reconcile stage (#191): deterministic-first match-or-mint.

Stage 1 (overlap) is exercised offline; Stages 2 (embedding) and 3 (LLM) run with
injected fakes so the whole suite stays AWS-free. Also covers conflict-free
assignment, the defer->mint path, snapshot loading, config validation, the
content-keyed verdict cache (proving ids are excluded from the key), and store
delegation through match_or_mint.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from pipeline_hierarchy.subtopic_id_store import (
    META_SK,
    SUBTOPIC_ID_PK_PREFIX,
    SUBTOPIC_SLUG_PK_PREFIX,
    MintContext,
    SubtopicDefer,
    SubtopicIdMinter,
    SubtopicIdStore,
    SubtopicMatch,
    reconcile_durable_ids,
)
from pipeline_hierarchy.subtopic_reconcile import (
    ReconcileThresholds,
    SubtopicReconciler,
    load_id_store_snapshot,
)

T = ReconcileThresholds(
    auto_match_min=0.50, ambiguous_min=0.20, centroid_cosine_min=0.75, llm_arbiter_enabled=True
)


def _snap(**rows):
    """rows: durable_id -> (topic_id, set_of_pmids, label)."""
    return {
        did: {
            "slug_id": f"slug_{did}",
            "topic_id": topic,
            "seed_pmids": set(pmids),
            "label_at_mint": label,
            "status": "active",
        }
        for did, (topic, pmids, label) in rows.items()
    }


def _mem(**clusters):
    """clusters: slug -> (topic_id, list_of_pmids)."""
    return {"subtopics": {s: {"topic_id": t, "seed_pmids": p} for s, (t, p) in clusters.items()}}


def _never_embed(texts):
    raise AssertionError("embedder must not be called")


def _zero_embed(texts):
    return [[0.0, 0.0] for _ in texts]  # cosine 0 -> centroid never clears threshold


def _never_arbiter(**_k):
    raise AssertionError("arbiter must not be called")


# ---------- thresholds config ----------


def test_thresholds_from_real_config_parses():
    t = ReconcileThresholds.from_config()
    assert 0.0 <= t.ambiguous_min <= t.auto_match_min <= 1.0
    assert isinstance(t.llm_arbiter_enabled, bool)


def test_thresholds_missing_key_fails_loud(tmp_path):
    p = tmp_path / "thresholds.json"
    p.write_text(json.dumps({"subtopic_reconcile_overlap_auto_match_min": 0.5}))
    with pytest.raises(ValueError, match="missing required key"):
        ReconcileThresholds.from_config(p)


def test_thresholds_ambiguous_above_auto_fails(tmp_path):
    p = tmp_path / "thresholds.json"
    p.write_text(json.dumps({
        "subtopic_reconcile_overlap_auto_match_min": 0.3,
        "subtopic_reconcile_overlap_ambiguous_min": 0.6,
        "subtopic_reconcile_centroid_cosine_min": 0.75,
        "subtopic_reconcile_llm_arbiter_enabled": True,
    }))
    with pytest.raises(ValueError, match="must be <="):
        ReconcileThresholds.from_config(p)


# ---------- snapshot loader ----------


class _ScanTable:
    def __init__(self, items, page=100):
        self._items = items  # list of dicts
        self._page = page

    def scan(self, **kwargs):
        start = kwargs.get("ExclusiveStartKey", 0)
        chunk = self._items[start : start + self._page]
        resp = {"Items": chunk}
        nxt = start + self._page
        if nxt < len(self._items):
            resp["LastEvaluatedKey"] = nxt
        return resp


def test_load_snapshot_int_coerces_and_paginates():
    items = [
        {"PK": f"{SUBTOPIC_ID_PK_PREFIX}st_a", "SK": META_SK, "durable_id": "st_a",
         "slug_id": "s_a", "topic_id": "t", "seed_pmids": [Decimal(3), Decimal(1)], "label_at_mint": "A"},
        {"PK": f"{SUBTOPIC_ID_PK_PREFIX}st_b", "SK": META_SK, "durable_id": "st_b",
         "slug_id": "s_b", "topic_id": "t", "seed_pmids": [Decimal(9)], "label_at_mint": "B"},
    ]
    snap = load_id_store_snapshot(_ScanTable(items, page=1))  # force pagination
    assert set(snap) == {"st_a", "st_b"}
    assert snap["st_a"]["seed_pmids"] == {1, 3}
    assert all(isinstance(p, int) for p in snap["st_a"]["seed_pmids"])


# ---------- Stage 1 ----------


def test_stage1_auto_match_and_below_floor_mint():
    snap = _snap(st_alpha=("aging", {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}, "Senescence"))
    r = SubtopicReconciler(snap, thresholds=T, embed=_never_embed, arbiter=_never_arbiter)
    r.precompute(
        membership=_mem(cont=("aging", [1, 2, 3, 4, 5, 6, 7, 8]), fresh=("aging", [90, 91, 92])),
        labels={"cont": "Senescence v2", "fresh": "New Area"},
    )
    cont = r.match(slug="cont")
    assert isinstance(cont, SubtopicMatch) and cont.durable_id == "st_alpha" and cont.reason == "overlap"
    assert r.match(slug="fresh") is None  # below floor -> mint


def test_stage1_does_not_match_across_topics():
    snap = _snap(st_alpha=("aging", {1, 2, 3, 4, 5}, "A"))
    r = SubtopicReconciler(snap, thresholds=T, embed=_never_embed, arbiter=_never_arbiter)
    # identical pmids but a DIFFERENT topic -> no candidate -> mint
    r.precompute(membership=_mem(x=("cancer", [1, 2, 3, 4, 5])), labels={"x": "A"})
    assert r.match(slug="x") is None


# ---------- Stage 2 ----------


def test_stage2_centroid_attaches_without_calling_llm():
    snap = _snap(st_alpha=("aging", {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}, "Cellular Senescence"))

    def embed(texts):
        # senescence-ish near-identical; everything else orthogonal
        return [[1.0, 0.0] if "senesc" in t.lower() else [0.0, 1.0] for t in texts]

    r = SubtopicReconciler(snap, thresholds=T, embed=embed, arbiter=_never_arbiter)
    # overlap 2/5 = 0.40 -> ambiguous band
    r.precompute(membership=_mem(x=("aging", [1, 2, 500, 501, 502])), labels={"x": "Senescence pathways"})
    v = r.match(slug="x")
    assert isinstance(v, SubtopicMatch) and v.durable_id == "st_alpha" and v.reason == "centroid"


# ---------- Stage 3 ----------


def test_stage3_llm_attaches_only_on_the_ambiguous_tail():
    snap = _snap(
        st_alpha=("aging", {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}, "Senescence"),
        st_clear=("aging", {200, 201, 202, 203, 204, 205, 206, 207, 208, 209}, "Autophagy"),
    )
    calls = {"n": 0}

    def arbiter(*, new_label, new_seed_pmids, candidates, topic_id):
        calls["n"] += 1
        return {"verdict": "same", "durable_id": candidates[0]["durable_id"]}

    r = SubtopicReconciler(snap, thresholds=T, embed=_zero_embed, arbiter=arbiter)
    r.precompute(
        membership=_mem(
            auto=("aging", [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]),  # overlap 1.0 w/ st_alpha -> Stage 1, no LLM
            amb=("aging", [200, 201, 500, 501, 502]),         # 0.40 w/ st_clear -> centroid fails -> LLM
        ),
        labels={"auto": "x", "amb": "y"},
    )
    assert r.match(slug="auto").reason == "overlap"  # claimed st_alpha
    v = r.match(slug="amb")
    assert isinstance(v, SubtopicMatch) and v.durable_id == "st_clear" and v.reason == "llm"
    assert calls["n"] == 1  # arbiter ran ONLY on the ambiguous tail (auto resolved at Stage 1)


def test_defer_to_mint_when_arbiter_distinct_or_disabled():
    snap = _snap(st_alpha=("aging", {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}, "Senescence"))
    amb = _mem(x=("aging", [1, 2, 500, 501, 502]))  # 0.40 ambiguous

    r1 = SubtopicReconciler(snap, thresholds=T, embed=_zero_embed,
                            arbiter=lambda **k: {"verdict": "distinct", "durable_id": None})
    r1.precompute(membership=amb, labels={"x": "y"})
    assert isinstance(r1.match(slug="x"), SubtopicDefer)

    disabled = ReconcileThresholds(auto_match_min=0.5, ambiguous_min=0.2, centroid_cosine_min=0.75, llm_arbiter_enabled=False)
    r2 = SubtopicReconciler(snap, thresholds=disabled, embed=_zero_embed, arbiter=_never_arbiter)
    r2.precompute(membership=amb, labels={"x": "y"})
    assert isinstance(r2.match(slug="x"), SubtopicDefer)  # arbiter never called when disabled


# ---------- conflict-free assignment ----------


def test_two_clusters_one_prior_highest_attaches_other_mints():
    snap = _snap(st_alpha=("aging", {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}, "Senescence"))
    r = SubtopicReconciler(snap, thresholds=T, embed=_zero_embed,
                           arbiter=lambda **k: {"verdict": "distinct", "durable_id": None})
    r.precompute(
        membership=_mem(
            strong=("aging", [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]),  # overlap 1.0
            weak=("aging", [1, 2, 3, 4, 5, 6, 700, 701, 702, 703]),  # 0.6 -> auto too, but alpha claimed
        ),
        labels={"strong": "s", "weak": "w"},
    )
    assert r.match(slug="strong").durable_id == "st_alpha"
    assert r.match(slug="weak") is None  # alpha already claimed -> no other prior -> mint


def test_assignment_is_order_independent():
    snap = _snap(st_alpha=("aging", {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}, "Senescence"))

    def run(order):
        m = {"subtopics": {}}
        for slug in order:
            if slug == "strong":
                m["subtopics"]["strong"] = {"topic_id": "aging", "seed_pmids": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]}
            else:
                m["subtopics"]["weak"] = {"topic_id": "aging", "seed_pmids": [1, 2, 3, 4, 5, 6, 700, 701, 702, 703]}
        r = SubtopicReconciler(snap, thresholds=T, embed=_zero_embed,
                               arbiter=lambda **k: {"verdict": "distinct", "durable_id": None})
        r.precompute(membership=m, labels={"strong": "s", "weak": "w"})
        return r.match(slug="strong"), r.match(slug="weak")

    a = run(["strong", "weak"])
    b = run(["weak", "strong"])
    assert a[0].durable_id == b[0].durable_id == "st_alpha"  # strong wins regardless of input order
    assert a[1] is None and b[1] is None


def test_intra_run_self_match_is_impossible():
    # Empty prior store: a cluster mints; a second cluster heavily overlapping the
    # FIRST cannot match it (same-run mints are never in the pre-run snapshot).
    r = SubtopicReconciler({}, thresholds=T, embed=_never_embed, arbiter=_never_arbiter)
    r.precompute(
        membership=_mem(first=("aging", [1, 2, 3, 4, 5]), second=("aging", [1, 2, 3, 4, 5])),
        labels={"first": "a", "second": "b"},
    )
    assert r.match(slug="first") is None and r.match(slug="second") is None


# ---------- verdict cache ----------


def test_verdict_cache_is_keyed_on_content_not_id():
    # Two snapshots with IDENTICAL content but DIFFERENT durable ids + a shared
    # cache. The second reconcile must HIT the cache (id excluded from the key);
    # and the chosen-id-not-in-candidates guard prevents the stale id attaching.
    shared_cache: dict = {}
    calls = {"n": 0}

    def arbiter(*, new_label, new_seed_pmids, candidates, topic_id):
        calls["n"] += 1
        return {"verdict": "same", "durable_id": candidates[0]["durable_id"]}

    amb = _mem(x=("aging", [1, 2, 500, 501, 502]))  # 0.40 ambiguous
    content = ("aging", {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}, "Senescence")

    r1 = SubtopicReconciler(_snap(st_alpha=content), thresholds=T, embed=_zero_embed,
                            arbiter=arbiter, verdict_cache=shared_cache)
    r1.precompute(membership=amb, labels={"x": "y"})
    assert r1.match(slug="x").durable_id == "st_alpha" and r1.match(slug="x").reason == "llm"

    r2 = SubtopicReconciler(_snap(st_gamma=content), thresholds=T, embed=_zero_embed,
                            arbiter=arbiter, verdict_cache=shared_cache)
    r2.precompute(membership=amb, labels={"x": "y"})
    assert calls["n"] == 1  # cache HIT despite the different durable id -> content-keyed
    # cached verdict named st_alpha, but r2's only candidate is st_gamma -> guard -> mint
    assert isinstance(r2.match(slug="x"), SubtopicDefer)


# ---------- store delegation (end-to-end via match_or_mint) ----------


class _FakeTable:
    def __init__(self):
        self.items = {}

    def get_item(self, Key):
        k = (Key["PK"], Key["SK"])
        return {"Item": self.items[k]} if k in self.items else {}

    def put_item(self, Item):
        self.items[(Item["PK"], Item["SK"])] = dict(Item)


def test_reconcile_durable_ids_attaches_via_overlap_and_reports_reasons():
    snap = _snap(st_alpha=("aging", {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}, "Senescence"))
    # seed the prior META row so attach has a row to refresh
    table = _FakeTable()
    table.put_item(Item={"PK": f"{SUBTOPIC_ID_PK_PREFIX}st_alpha", "SK": META_SK,
                         "durable_id": "st_alpha", "slug_id": "slug_st_alpha", "topic_id": "aging",
                         "label_at_mint": "Senescence", "created_at": "2026-01-01T00:00:00Z",
                         "first_run_id": "run-0", "status": "active", "seed_pmids": [1, 2, 3]})
    r = SubtopicReconciler(snap, thresholds=T, embed=_never_embed, arbiter=_never_arbiter)
    store = SubtopicIdStore(table, SubtopicIdMinter(), reconciler=r)
    membership = _mem(cont=("aging", [1, 2, 3, 4, 5, 6, 7, 8]), fresh=("aging", [90, 91, 92]))
    hierarchy = {"topics": {"aging": {"subtopics": [{"id": "cont", "label": "Sen v2"}, {"id": "fresh", "label": "New"}]}}}
    summary = reconcile_durable_ids(
        store, membership=membership, hierarchy=hierarchy,
        ctx=MintContext(run_id="run-1", created_at="2026-06-11T00:00:00Z",
                        taxonomy_version="taxonomy_v2", hierarchy_version="v1"),
        reconciler=r,
    )
    assert summary["attached"] == 1 and summary["minted"] == 1
    assert summary["reasons"]["attached_overlap"] == 1 and summary["reasons"]["minted"] == 1
    # 'cont' attached to st_alpha; its META row's mint-once provenance preserved, membership refreshed
    meta = table.items[(f"{SUBTOPIC_ID_PK_PREFIX}st_alpha", META_SK)]
    assert meta["created_at"] == "2026-01-01T00:00:00Z" and meta["first_run_id"] == "run-0"
    assert meta["slug_id"] == "cont" and meta["seed_pmids"] == [1, 2, 3, 4, 5, 6, 7, 8]
    # 'fresh' minted a brand-new durable id
    new_ids = [v for (pk, sk), v in table.items.items() if sk == META_SK and v["slug_id"] == "fresh"]
    assert len(new_ids) == 1 and new_ids[0]["durable_id"] != "st_alpha"


# ---------- review fixes ----------


def test_centroid_runs_against_all_in_band_priors_not_just_top_overlap():
    # A label-true prior ranked LOW by overlap (rank 6) must still reach Stage 2:
    # the candidate cap belongs to the LLM stage only.
    # Decoys are larger than the new {1,2,3,4,5} so min-cardinality = 5: each shares
    # 2 -> overlap 0.40 (ambiguous, rank 1-5). st_true shares 1 -> 0.20 (rank 6).
    _pad = lambda n: set(range(1000 * n, 1000 * n + 8))  # 8 disjoint filler pmids
    snap = _snap(
        st_d1=("aging", {1, 2} | _pad(1), "Decoy one"),
        st_d2=("aging", {1, 3} | _pad(2), "Decoy two"),
        st_d3=("aging", {1, 4} | _pad(3), "Decoy three"),
        st_d4=("aging", {2, 3} | _pad(4), "Decoy four"),
        st_d5=("aging", {2, 4} | _pad(5), "Decoy five"),
        st_true=("aging", {5} | _pad(6), "Senescence"),  # shares {5} -> 1/5 = 0.20
    )

    def embed(texts):  # only the senescence label is near the query
        return [[1.0, 0.0] if "senesc" in t.lower() else [0.0, 1.0] for t in texts]

    r = SubtopicReconciler(snap, thresholds=T, embed=embed, arbiter=_never_arbiter)
    # new {1,2,3,4,5}: decoys overlap 2/5=0.40 (rank 1-5), st_true 1/5=0.20 (rank 6)
    r.precompute(membership=_mem(x=("aging", [1, 2, 3, 4, 5])), labels={"x": "Senescence"})
    v = r.match(slug="x")
    assert isinstance(v, SubtopicMatch) and v.durable_id == "st_true" and v.reason == "centroid"


def test_centroid_records_realized_cosine_not_threshold():
    snap = _snap(st_alpha=("aging", {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}, "Senescence"))

    def embed(texts):  # query [1,0] vs prior [0.8,0.6] -> cosine 0.8 (>= 0.75)
        return [[1.0, 0.0] if "query" in t.lower() else [0.8, 0.6] for t in texts]

    r = SubtopicReconciler(snap, thresholds=T, embed=embed, arbiter=_never_arbiter)
    r.precompute(membership=_mem(x=("aging", [1, 2, 500, 501, 502])), labels={"x": "QUERY label"})
    v = r.match(slug="x")
    assert v.reason == "centroid"
    assert v.score == pytest.approx(0.8, abs=1e-3)  # realized cosine, NOT the 0.75 threshold


def test_split_loser_records_contested_prior_for_brick_c():
    snap = _snap(st_alpha=("aging", {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}, "Senescence"))
    r = SubtopicReconciler(snap, thresholds=T, embed=_zero_embed,
                           arbiter=lambda **k: {"verdict": "distinct", "durable_id": None})
    r.precompute(
        membership=_mem(
            strong=("aging", [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]),  # claims st_alpha
            weak=("aging", [1, 2, 3, 4, 5, 6, 700, 701, 702, 703]),  # 0.6 w/ st_alpha but it's claimed
        ),
        labels={"strong": "s", "weak": "w"},
    )
    assert r.match(slug="strong").durable_id == "st_alpha"
    assert r.match(slug="weak") is None  # mints
    # ...but brick C can read which prior it lost to, without replaying the order:
    assert r.split_parents["weak"] == "st_alpha"


def test_unclaimed_priors_surface_merge_absorbed_ids():
    snap = _snap(
        st_a=("aging", {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}, "A"),
        st_b=("aging", {1, 2, 3, 4, 5, 6, 100, 101, 102, 103}, "B"),  # one cluster will claim st_a, st_b left unclaimed
    )
    r = SubtopicReconciler(snap, thresholds=T, embed=_zero_embed, arbiter=_never_arbiter)
    r.precompute(membership=_mem(merger=("aging", [1, 2, 3, 4, 5, 6, 7, 8, 9, 10])), labels={"merger": "M"})
    assert r.match(slug="merger").durable_id == "st_a"
    assert "st_b" in r.unclaimed_priors  # absorbed/quiet prior brick C+F consume


def test_from_config_rejects_non_boolean_arbiter_flag(tmp_path):
    p = tmp_path / "thresholds.json"
    p.write_text(json.dumps({
        "subtopic_reconcile_overlap_auto_match_min": 0.5,
        "subtopic_reconcile_overlap_ambiguous_min": 0.2,
        "subtopic_reconcile_centroid_cosine_min": 0.75,
        "subtopic_reconcile_llm_arbiter_enabled": "false",  # string, not a JSON bool
    }))
    with pytest.raises(ValueError, match="must be a JSON boolean"):
        ReconcileThresholds.from_config(p)

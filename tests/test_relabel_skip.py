"""Tests for #191 brick E: reconcile skip-logic for the cold-path relabel stage.

Two layers, both AWS-free:
  * ``pipeline_hierarchy/relabel_skip.py`` — the pure skip decision
    (``compute_relabel_skip_map``), config loading, and prior-artifact indexing.
  * ``cli/relabel_subtopics.py`` — the relabel seam (``_apply_skip_reuse`` +
    ``relabel_topic`` consuming a skip_map), with ``_call_relabel`` monkeypatched so
    no Sonnet call ever fires.

The reconciler is exercised through ``compute_relabel_skip_map`` with injected
embed/arbiter (mirrors ``tests/test_subtopic_reconciler.py``).
"""

from __future__ import annotations

import json

import pytest

import cli.relabel_subtopics as relabel_mod
from pipeline_hierarchy.relabel_skip import (
    SKIP_ENABLED_KEY,
    SKIP_OVERLAP_MIN_KEY,
    PriorReuse,
    build_prior_hierarchy_index,
    compute_relabel_skip_map,
    load_skip_config,
)
from pipeline_hierarchy.subtopic_id_store import SubtopicMatch
from pipeline_hierarchy.subtopic_reconcile import ReconcileThresholds, SubtopicReconciler

T = ReconcileThresholds(
    auto_match_min=0.50, ambiguous_min=0.20, centroid_cosine_min=0.75, llm_arbiter_enabled=True
)


def _snap(**rows):
    """rows: durable_id -> (topic_id, set_of_pmids, label_at_mint, slug_id)."""
    return {
        did: {
            "slug_id": slug,
            "topic_id": topic,
            "seed_pmids": set(pmids),
            "label_at_mint": label,
            "status": "active",
        }
        for did, (topic, pmids, label, slug) in rows.items()
    }


def _mem(**clusters):
    """clusters: new_slug -> (topic_id, list_of_pmids)."""
    return {"subtopics": {s: {"topic_id": t, "seed_pmids": p} for s, (t, p) in clusters.items()}}


def _const_embed(texts):
    """All labels map to one vector → cosine 1.0 → centroid clears any threshold."""
    return [[1.0, 0.0] for _ in texts]


def _prior(**slugs):
    """slug -> (display_name, short_description, topic_id)."""
    return {s: {"display_name": dn, "short_description": sd, "topic_id": t} for s, (dn, sd, t) in slugs.items()}


# ---------- load_skip_config ----------


def test_skip_config_real_config_enabled_in_prod():
    # #204 (Shape B): relabel-skip is ENABLED in the shipped config. It was flag-off
    # through the brick-E build (PR #205); flipped on to skip the per-cluster Sonnet
    # relabel for low-drift (overlap >= 0.70) reconcile matches.
    enabled, overlap_min = load_skip_config()
    assert enabled is True
    assert overlap_min == 0.70


def test_skip_config_absent_enabled_key_is_off(tmp_path):
    p = tmp_path / "thresholds.json"
    p.write_text(json.dumps({"something_else": 1}))
    assert load_skip_config(p) == (False, 1.0)


def test_skip_config_enabled_requires_overlap_min(tmp_path):
    p = tmp_path / "thresholds.json"
    p.write_text(json.dumps({SKIP_ENABLED_KEY: True}))
    with pytest.raises(ValueError, match="is missing"):
        load_skip_config(p)


def test_skip_config_overlap_min_out_of_range_fails(tmp_path):
    p = tmp_path / "thresholds.json"
    p.write_text(json.dumps({SKIP_ENABLED_KEY: True, SKIP_OVERLAP_MIN_KEY: 1.4}))
    with pytest.raises(ValueError, match="outside"):
        load_skip_config(p)


def test_skip_config_enabled_valid(tmp_path):
    p = tmp_path / "thresholds.json"
    p.write_text(json.dumps({SKIP_ENABLED_KEY: True, SKIP_OVERLAP_MIN_KEY: 0.7}))
    assert load_skip_config(p) == (True, 0.7)


# ---------- build_prior_hierarchy_index ----------


def test_prior_index_keys_by_slug():
    prior = {
        "topics": {
            "t1": {"subtopics": [{"id": "s_a", "display_name": "A", "short_description": "da"}]},
            "t2": {"subtopics": [{"id": "s_b", "display_name": "B", "short_description": "db"}]},
        }
    }
    idx = build_prior_hierarchy_index(prior)
    assert idx["s_a"] == {"display_name": "A", "short_description": "da", "topic_id": "t1"}
    assert idx["s_b"]["topic_id"] == "t2"


# ---------- compute_relabel_skip_map ----------


def test_high_overlap_match_is_skipped_and_reuses_prior():
    snap = _snap(st_a=("t", {1, 2, 3, 4}, "A", "slug_a"))
    mem = _mem(new_a=("t", [1, 2, 3, 4]))  # overlap 4/4 = 1.0
    prior = _prior(slug_a=("Prior A", "Prior desc A", "t"))
    skip = compute_relabel_skip_map(
        membership=mem, labels={"new_a": "alpha"}, snapshot=snap,
        prior_index=prior, reconcile_thresholds=T, skip_overlap_min=0.70,
    )
    assert "new_a" in skip
    pr = skip["new_a"]
    assert pr == PriorReuse("st_a", "Prior A", "Prior desc A", 1.0)


def test_overlap_below_skip_threshold_is_not_skipped():
    # overlap 0.60: an auto-match (>=0.50) but below the 0.70 skip bar.
    snap = _snap(st_b=("t", {1, 2, 3, 6, 7, 8, 9, 10}, "B", "slug_b"))
    mem = _mem(new_b=("t", [1, 2, 3, 4, 5]))  # |∩|=3, min(5,8)=5 → 0.60
    prior = _prior(slug_b=("Prior B", "Prior desc B", "t"))
    skip = compute_relabel_skip_map(
        membership=mem, labels={"new_b": "beta"}, snapshot=snap,
        prior_index=prior, reconcile_thresholds=T, skip_overlap_min=0.70,
    )
    assert "new_b" not in skip


def test_centroid_match_is_not_skipped():
    # Ambiguous band (0.40 overlap) → resolves via centroid, NOT overlap → never skipped.
    snap = _snap(st_c=("t", {1, 2, 30, 40, 50, 60, 70, 80, 90, 100}, "cancer", "slug_c"))
    mem = _mem(new_c=("t", [1, 2, 3, 4, 5]))  # |∩|=2, min(5,10)=5 → 0.40 (ambiguous)
    prior = _prior(slug_c=("Prior C", "Prior desc C", "t"))
    skip = compute_relabel_skip_map(
        membership=mem, labels={"new_c": "cancer genomics"}, snapshot=snap,
        prior_index=prior, reconcile_thresholds=T, skip_overlap_min=0.30,
        embed=_const_embed,  # force a centroid hit
    )
    assert "new_c" not in skip  # matched, but reason="centroid" → not a skip


def test_prior_value_absent_falls_back_to_relabel():
    snap = _snap(st_d=("t", {1, 2, 3, 4}, "D", "slug_d"))
    mem = _mem(new_d=("t", [1, 2, 3, 4]))
    skip = compute_relabel_skip_map(
        membership=mem, labels={"new_d": "delta"}, snapshot=snap,
        prior_index={}, reconcile_thresholds=T, skip_overlap_min=0.70,  # no prior entry
    )
    assert skip == {}


def test_empty_prior_ui_text_falls_back_to_relabel():
    snap = _snap(st_e=("t", {1, 2, 3, 4}, "E", "slug_e"))
    mem = _mem(new_e=("t", [1, 2, 3, 4]))
    prior = _prior(slug_e=("", "", "t"))  # published but blank UI text
    skip = compute_relabel_skip_map(
        membership=mem, labels={"new_e": "eps"}, snapshot=snap,
        prior_index=prior, reconcile_thresholds=T, skip_overlap_min=0.70,
    )
    assert skip == {}


def test_empty_snapshot_returns_empty():
    skip = compute_relabel_skip_map(
        membership=_mem(new_a=("t", [1, 2])), labels={"new_a": "a"}, snapshot={},
        prior_index=_prior(slug_a=("A", "da", "t")), reconcile_thresholds=T,
        skip_overlap_min=0.70,
    )
    assert skip == {}


def test_skip_verdicts_match_an_independent_reconcile():
    """Determinism (UNCONTESTED case): the slug→durable mapping the skip pre-pass
    produces (skip_min=0) equals the reason=='overlap' auto-matches an independent
    reconciler computes on the same snapshot+membership. The clusters here each own
    their prior uncontested, so the order-invariance gate is a no-op — this covers the
    happy path; test_contested_prior_is_withheld_despite_overlap_match covers the
    divergence the gate exists to block."""
    snap = _snap(
        st_a=("t", {1, 2, 3, 4}, "A", "slug_a"),
        st_b=("t", {10, 11, 12, 13}, "B", "slug_b"),
        st_c=("t", {20, 21, 22, 23}, "C", "slug_c"),
    )
    mem = _mem(
        new_a=("t", [1, 2, 3, 4]),       # 1.0 overlap with st_a
        new_b=("t", [10, 11, 12]),       # 3/3 = 1.0 overlap with st_b
        new_z=("t", [90, 91, 92]),       # no overlap → mints
    )
    prior = _prior(
        slug_a=("PA", "pa", "t"), slug_b=("PB", "pb", "t"), slug_c=("PC", "pc", "t")
    )
    skip = compute_relabel_skip_map(
        membership=mem, labels={k: k for k in mem["subtopics"]}, snapshot=snap,
        prior_index=prior, reconcile_thresholds=T, skip_overlap_min=0.0,
    )

    independent = SubtopicReconciler(
        snap, thresholds=ReconcileThresholds(0.50, 0.20, 0.75, False),
        embed=lambda ts: [[0.0, 0.0] for _ in ts],
        arbiter=lambda **k: {"verdict": "distinct", "durable_id": None},
        verdict_cache={},
    )
    independent.precompute(membership=mem, labels={k: k for k in mem["subtopics"]})
    expected = {
        slug: independent.match(slug=slug).durable_id
        for slug in mem["subtopics"]
        if isinstance(independent.match(slug=slug), SubtopicMatch)
        and independent.match(slug=slug).reason == "overlap"
    }
    assert {slug: pr.durable_id for slug, pr in skip.items()} == expected
    assert set(skip) == {"new_a", "new_b"}  # new_z minted → not skipped


def test_contested_prior_is_withheld_despite_overlap_match():
    """Order-invariance gate (#204 review): a cluster whose bare reconcile verdict is
    reason=='overlap' is NOT skipped when another in-topic cluster could contest the same
    prior. In the LIVE step-10 reconcile (stages 2/3 on) that contesting cluster can claim
    the prior first, leaving step 10 to mint a fresh id for our cluster — so reusing the
    prior's UI text would bind the wrong id's label. This is exactly the divergence the
    'compare the pre-pass to itself' determinism test cannot see."""
    snap = _snap(
        st_p=("t", set(range(21, 41)), "P", "slug_p"),
        st_q=("t", set(range(1, 21)), "Q", "slug_q"),
    )
    mem = _mem(
        new_q=("t", list(range(1, 21))),                              # auto-match Q (1.0), ordered first
        new_x=("t", list(range(1, 20)) + [21, 22, 23, 24, 25, 26]),   # best on Q (0.95); ambiguous on P (0.30)
        new_y=("t", list(range(21, 37)) + [51, 52, 53, 54]),          # auto-match P (0.80)
    )
    prior = _prior(slug_p=("P DN", "P SD", "t"), slug_q=("Q DN", "Q SD", "t"))
    Tt = ReconcileThresholds(0.50, 0.20, 0.75, True)

    # The bare reconcile verdict for new_y IS an overlap auto-match above the skip bar...
    recon = SubtopicReconciler(
        snap, thresholds=ReconcileThresholds(0.50, 0.20, 0.75, False),
        embed=lambda ts: [[0.0, 0.0] for _ in ts],
        arbiter=lambda **k: {"verdict": "distinct", "durable_id": None}, verdict_cache={},
    )
    recon.precompute(membership=mem, labels={k: k for k in mem["subtopics"]})
    vy = recon.match(slug="new_y")
    assert isinstance(vy, SubtopicMatch) and vy.reason == "overlap" and vy.score >= 0.70

    # ...but the gate withholds it because new_x contests prior P (ambiguous-band overlap),
    # and likewise withholds new_q because new_x contests prior Q.
    skip = compute_relabel_skip_map(
        membership=mem, labels={k: k for k in mem["subtopics"]}, snapshot=snap,
        prior_index=prior, reconcile_thresholds=Tt, skip_overlap_min=0.70,
    )
    assert "new_y" not in skip
    assert "new_q" not in skip


# ---------- relabel seam: _apply_skip_reuse + relabel_topic ----------


def test_apply_skip_reuse_prefills_and_partitions():
    subs = [{"id": "s1", "label": "l1"}, {"id": "s2", "label": "l2"}]
    skip_map = {"s1": PriorReuse("st_1", "Mapping signals", "desc one", 0.9)}
    reused, to_relabel = relabel_mod._apply_skip_reuse(subs, skip_map, "t_alpha", "Alpha")
    assert [r["id"] for r in reused] == ["s1"]
    assert [s["id"] for s in to_relabel] == ["s2"]
    assert subs[0]["display_name"] == "Mapping signals"
    assert subs[0]["short_description"] == "desc one"


def test_apply_skip_reuse_drops_parent_prefix_violation():
    subs = [{"id": "s1", "label": "l1"}]
    # "Alpha ..." first word repeats the parent topic label → must NOT be reused.
    skip_map = {"s1": PriorReuse("st_1", "Alpha pathways", "desc", 0.95)}
    reused, to_relabel = relabel_mod._apply_skip_reuse(subs, skip_map, "t_alpha", "Alpha")
    assert reused == []
    assert [s["id"] for s in to_relabel] == ["s1"]
    assert "display_name" not in subs[0]  # not prefilled


def test_apply_skip_reuse_empty_map_relabels_all():
    subs = [{"id": "s1", "label": "l1"}, {"id": "s2", "label": "l2"}]
    reused, to_relabel = relabel_mod._apply_skip_reuse(subs, {}, "t_alpha", "Alpha")
    assert reused == []
    assert len(to_relabel) == 2


def _write_draft(phase_dir, topic_id, sub_ids):
    p = phase_dir / f"hierarchy_draft_{topic_id}.json"
    p.write_text(
        json.dumps(
            {
                "topic_id": topic_id,
                "topic_label": topic_id,
                "subtopics": [{"id": s, "label": s, "seed_pmids": [1]} for s in sub_ids],
            }
        )
    )
    return p


def _tax(topic_id, label="Alpha"):
    return {topic_id: {"label": label, "description": "d"}}


def test_relabel_topic_all_reused_skips_sonnet(tmp_path, monkeypatch):
    monkeypatch.setattr(relabel_mod, "PHASE_DIR", tmp_path)

    def _must_not_call(*a, **k):
        raise AssertionError("Sonnet relabel must not be called when all reused")

    monkeypatch.setattr(relabel_mod, "_call_relabel", _must_not_call)
    _write_draft(tmp_path, "t_alpha", ["s1", "s2"])
    skip_map = {
        "s1": PriorReuse("st_1", "Mapping signals", "desc one", 0.9),
        "s2": PriorReuse("st_2", "Profiling cells", "desc two", 0.85),
    }
    res = relabel_mod.relabel_topic(
        client=None, topic_id="t_alpha", taxonomy=_tax("t_alpha"), skip_map=skip_map
    )
    assert res["reused"] == 2
    assert res["patched"] == 0
    assert sorted(res["reused_durable_ids"]) == ["st_1", "st_2"]
    # Reused values were written to the draft.
    written = json.loads((tmp_path / "hierarchy_draft_t_alpha.json").read_text())
    by_id = {s["id"]: s for s in written["subtopics"]}
    assert by_id["s1"]["display_name"] == "Mapping signals"
    assert by_id["s2"]["short_description"] == "desc two"


def test_relabel_topic_mixed_relabels_only_remainder(tmp_path, monkeypatch):
    monkeypatch.setattr(relabel_mod, "PHASE_DIR", tmp_path)
    seen = []

    def _fake_call(client, topic_id, topic_label, topic_description, subtopics):
        seen.append([s["id"] for s in subtopics])
        return {
            "relabels": [
                {"id": s["id"], "display_name": f"Fresh {s['id']}", "short_description": "x"}
                for s in subtopics
            ]
        }

    monkeypatch.setattr(relabel_mod, "_call_relabel", _fake_call)
    _write_draft(tmp_path, "t_alpha", ["s1", "s2", "s3"])
    skip_map = {
        "s1": PriorReuse("st_1", "Mapping signals", "desc one", 0.9),
        "s2": PriorReuse("st_2", "Profiling cells", "desc two", 0.85),
    }
    res = relabel_mod.relabel_topic(
        client=None, topic_id="t_alpha", taxonomy=_tax("t_alpha"), skip_map=skip_map
    )
    assert seen == [["s3"]]  # only the un-skipped subtopic went to Sonnet
    assert res["reused"] == 2
    assert res["patched"] == 1


def test_relabel_topic_empty_skip_map_relabels_all(tmp_path, monkeypatch):
    monkeypatch.setattr(relabel_mod, "PHASE_DIR", tmp_path)
    seen = []

    def _fake_call(client, topic_id, topic_label, topic_description, subtopics):
        seen.append([s["id"] for s in subtopics])
        return {
            "relabels": [
                {"id": s["id"], "display_name": f"Fresh {s['id']}", "short_description": "x"}
                for s in subtopics
            ]
        }

    monkeypatch.setattr(relabel_mod, "_call_relabel", _fake_call)
    _write_draft(tmp_path, "t_alpha", ["s1", "s2"])
    res = relabel_mod.relabel_topic(
        client=None, topic_id="t_alpha", taxonomy=_tax("t_alpha"), skip_map={}
    )
    assert seen == [["s1", "s2"]]  # full relabel, byte-identical to pre-brick-E
    assert res["reused"] == 0
    assert res["patched"] == 2


def test_relabel_topic_reused_violation_falls_through_to_relabel(tmp_path, monkeypatch):
    monkeypatch.setattr(relabel_mod, "PHASE_DIR", tmp_path)
    seen = []

    def _fake_call(client, topic_id, topic_label, topic_description, subtopics):
        seen.append([s["id"] for s in subtopics])
        return {
            "relabels": [
                {"id": s["id"], "display_name": f"Fresh {s['id']}", "short_description": "x"}
                for s in subtopics
            ]
        }

    monkeypatch.setattr(relabel_mod, "_call_relabel", _fake_call)
    _write_draft(tmp_path, "t_alpha", ["s1"])
    # Reused display_name repeats the parent label "Alpha" → must be relabeled, not reused.
    skip_map = {"s1": PriorReuse("st_1", "Alpha pathways", "desc", 0.99)}
    res = relabel_mod.relabel_topic(
        client=None, topic_id="t_alpha", taxonomy=_tax("t_alpha", "Alpha"), skip_map=skip_map
    )
    assert seen == [["s1"]]
    assert res["reused"] == 0
    assert res["patched"] == 1

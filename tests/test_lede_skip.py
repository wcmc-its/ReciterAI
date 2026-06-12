"""Tests for #191 brick E lede-skip: reuse a prior published lede for the cold
STEP-9 (editorial-lede) stage when a subtopic is unchanged.

Two-gate design (see ``spotlight/lede_skip.py`` docstring):
  * GATE 1 (order-invariant overlap verdict) — the SAME #204-review-hardened
    gate the merged relabel-skip uses, shared via
    ``pipeline_hierarchy.relabel_skip.compute_overlap_reuse_map``. Built at
    startup; carries the prior lede + its grounded PMIDs into the map.
  * GATE 2 (top-3 grounding-PMID set equality) — the load-bearing addition.
    Evaluated in-loop against the current run's papers; a lede is regenerated
    whenever the grounding set drifted, even when identity is stable.

All AWS-free: a FakeTable (DDB Scan) and FakeS3 (single GET) are injected so
nothing touches the network. Mirrors ``tests/test_relabel_skip.py`` fixtures.
"""

from __future__ import annotations

import json

import pytest

from pipeline_hierarchy.relabel_skip import compute_overlap_reuse_map
from pipeline_hierarchy.subtopic_reconcile import ReconcileThresholds
from spotlight.lede_skip import (
    SKIP_LEDE_ENABLED_KEY,
    SKIP_OVERLAP_MIN_KEY,
    PriorLede,
    build_prior_lede_index,
    compute_lede_skip_map,
    current_grounding_pmids,
    lede_reuse_for,
    load_lede_skip_map,
    load_skip_lede_config,
)
from spotlight.types import Author, Paper

T = ReconcileThresholds(
    auto_match_min=0.50, ambiguous_min=0.20, centroid_cosine_min=0.75, llm_arbiter_enabled=True
)


# ---------------------------------------------------------------------------
# Fixture builders (mirror tests/test_relabel_skip.py)
# ---------------------------------------------------------------------------


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


def _prior_lede_index(**slugs):
    """slug -> (lede_text, [grounded_pmids])."""
    return {s: {"lede": lede, "lede_grounded_pmids": list(pmids)} for s, (lede, pmids) in slugs.items()}


def _author(pid: str, position: str) -> Author:
    return Author(person_identifier=pid, display_name="N", position=position)


def _paper(pmid: str, impact: float, *, has_author: bool = True) -> Paper:
    return Paper(
        pmid=pmid,
        title="t",
        journal="j",
        year=2025,
        impact_score=impact,
        impact_justification="ij",
        synopsis="syn",
        first_author=_author(f"fa_{pmid}" if has_author else "", "first"),
        last_author=_author(f"la_{pmid}" if has_author else "", "last"),
    )


# ---------------------------------------------------------------------------
# Fake AWS clients (offline)
# ---------------------------------------------------------------------------


class _FakeTable:
    """Records put/update calls (to assert read-only) and serves a canned Scan."""

    def __init__(self, items=None):
        self._items = items or []
        self.writes = []

    def scan(self, **_kwargs):
        return {"Items": list(self._items)}

    def put_item(self, **kwargs):  # pragma: no cover — should never fire
        self.writes.append(("put_item", kwargs))

    def update_item(self, **kwargs):  # pragma: no cover
        self.writes.append(("update_item", kwargs))


class _FakeS3:
    """Serves spotlight/latest/spotlight.json bytes; records GET keys."""

    def __init__(self, artifact: dict | None = None, raise_on_get: bool = False):
        self._artifact = artifact
        self._raise = raise_on_get
        self.gets = []

    def get_object_bytes(self, key: str) -> bytes:
        self.gets.append(key)
        if self._raise:
            raise RuntimeError("simulated S3 error")
        return json.dumps(self._artifact or {}).encode("utf-8")


def _store_item(durable_id, slug_id, topic_id, pmids):
    """A SUBTOPIC_ID#/META row shape load_id_store_snapshot consumes."""
    return {
        "PK": f"SUBTOPIC_ID#{durable_id}",
        "SK": "META",
        "durable_id": durable_id,
        "slug_id": slug_id,
        "topic_id": topic_id,
        "seed_pmids": [str(p) for p in pmids],
        "label_at_mint": "L",
        "status": "active",
    }


# ---------------------------------------------------------------------------
# load_skip_lede_config
# ---------------------------------------------------------------------------


def test_skip_lede_config_real_config_is_off_by_default():
    enabled, _ = load_skip_lede_config()
    assert enabled is False  # ships flag-off


def test_skip_lede_config_absent_enabled_key_is_off(tmp_path):
    p = tmp_path / "thresholds.json"
    p.write_text(json.dumps({"something_else": 1}))
    assert load_skip_lede_config(p) == (False, 1.0)


def test_skip_lede_config_enabled_requires_overlap_min(tmp_path):
    p = tmp_path / "thresholds.json"
    p.write_text(json.dumps({SKIP_LEDE_ENABLED_KEY: True}))
    with pytest.raises(ValueError, match="missing"):
        load_skip_lede_config(p)


def test_skip_lede_config_overlap_min_out_of_range_fails(tmp_path):
    p = tmp_path / "thresholds.json"
    p.write_text(json.dumps({SKIP_LEDE_ENABLED_KEY: True, SKIP_OVERLAP_MIN_KEY: 1.4}))
    with pytest.raises(ValueError, match="outside"):
        load_skip_lede_config(p)


def test_skip_lede_config_enabled_valid(tmp_path):
    p = tmp_path / "thresholds.json"
    p.write_text(json.dumps({SKIP_LEDE_ENABLED_KEY: True, SKIP_OVERLAP_MIN_KEY: 0.7}))
    assert load_skip_lede_config(p) == (True, 0.7)


# ---------------------------------------------------------------------------
# build_prior_lede_index
# ---------------------------------------------------------------------------


def test_build_prior_lede_index_keys_by_subtopic_id():
    prior = {
        "spotlights": [
            {"subtopic_id": "s_a", "lede": "Lede A", "lede_grounded_pmids": ["1", "2"]},
            {"subtopic_id": "s_b", "lede": "Lede B", "lede_grounded_pmids": ["3", "4", "5"]},
        ]
    }
    idx = build_prior_lede_index(prior)
    assert idx["s_a"] == {"lede": "Lede A", "lede_grounded_pmids": ["1", "2"]}
    assert idx["s_b"]["lede"] == "Lede B"


def test_build_prior_lede_index_drops_empty_lede_or_pmids():
    prior = {
        "spotlights": [
            {"subtopic_id": "s_a", "lede": "", "lede_grounded_pmids": ["1", "2"]},
            {"subtopic_id": "s_b", "lede": "Lede B", "lede_grounded_pmids": []},
            {"subtopic_id": "s_c", "lede": "Lede C", "lede_grounded_pmids": ["7", "8"]},
            {"subtopic_id": "", "lede": "no id", "lede_grounded_pmids": ["9"]},
        ]
    }
    idx = build_prior_lede_index(prior)
    assert set(idx) == {"s_c"}  # blank lede, empty pmids, blank id all dropped


# ---------------------------------------------------------------------------
# compute_overlap_reuse_map (the SHARED gate-1 core both skip layers depend on)
# ---------------------------------------------------------------------------


def test_compute_overlap_reuse_map_returns_durable_prior_slug_tuple():
    """Pin the shared contract: {slug -> (durable_id, prior_slug, overlap_score)}
    for a high-overlap uncontested match; {} for a contested one."""
    snap = _snap(st_a=("t", {1, 2, 3, 4}, "A", "slug_a"))
    mem = _mem(new_a=("t", [1, 2, 3, 4]))  # overlap 1.0
    out = compute_overlap_reuse_map(
        membership=mem, labels={"new_a": "alpha"}, snapshot=snap,
        reconcile_thresholds=T, skip_overlap_min=0.70,
    )
    assert out == {"new_a": ("st_a", "slug_a", 1.0)}

    # Contested: an ambiguous-band sibling withholds the match.
    snap2 = _snap(
        st_p=("t", set(range(21, 41)), "P", "slug_p"),
        st_q=("t", set(range(1, 21)), "Q", "slug_q"),
    )
    mem2 = _mem(
        new_q=("t", list(range(1, 21))),
        new_x=("t", list(range(1, 20)) + [21, 22, 23, 24, 25, 26]),
        new_y=("t", list(range(21, 37)) + [51, 52, 53, 54]),
    )
    Tt = ReconcileThresholds(0.50, 0.20, 0.75, True)
    out2 = compute_overlap_reuse_map(
        membership=mem2, labels={k: k for k in mem2["subtopics"]}, snapshot=snap2,
        reconcile_thresholds=Tt, skip_overlap_min=0.70,
    )
    assert "new_y" not in out2
    assert "new_q" not in out2


# ---------------------------------------------------------------------------
# compute_lede_skip_map (gate 1)
# ---------------------------------------------------------------------------


def test_compute_lede_skip_map_high_overlap_carries_prior_lede():
    snap = _snap(st_a=("t", {1, 2, 3, 4}, "A", "slug_a"))
    mem = _mem(new_a=("t", [1, 2, 3, 4]))  # overlap 1.0
    pli = _prior_lede_index(slug_a=("WCM scholars are mapping prior A.", [101, 102]))
    skip = compute_lede_skip_map(
        membership=mem, labels={"new_a": "alpha"}, snapshot=snap,
        prior_lede_index=pli, reconcile_thresholds=T, skip_overlap_min=0.70,
    )
    assert "new_a" in skip
    pr = skip["new_a"]
    assert isinstance(pr, PriorLede)
    assert pr.durable_id == "st_a"
    assert pr.prior_slug == "slug_a"
    assert pr.lede == "WCM scholars are mapping prior A."
    assert pr.grounded_pmids == frozenset({"101", "102"})  # normalized to str
    assert pr.overlap_score == 1.0


def test_compute_lede_skip_map_below_threshold_not_eligible():
    snap = _snap(st_b=("t", {1, 2, 3, 6, 7, 8, 9, 10}, "B", "slug_b"))
    mem = _mem(new_b=("t", [1, 2, 3, 4, 5]))  # 0.60 < 0.70 bar
    pli = _prior_lede_index(slug_b=("Lede B", [1, 2]))
    skip = compute_lede_skip_map(
        membership=mem, labels={"new_b": "beta"}, snapshot=snap,
        prior_lede_index=pli, reconcile_thresholds=T, skip_overlap_min=0.70,
    )
    assert "new_b" not in skip


def test_compute_lede_skip_map_contested_prior_withheld():
    """The shared order-invariance gate is in force for lede-skip too: a cluster
    contested by an ambiguous-band sibling is withheld (mirrors the relabel-skip
    test_contested_prior_is_withheld_despite_overlap_match)."""
    snap = _snap(
        st_p=("t", set(range(21, 41)), "P", "slug_p"),
        st_q=("t", set(range(1, 21)), "Q", "slug_q"),
    )
    mem = _mem(
        new_q=("t", list(range(1, 21))),
        new_x=("t", list(range(1, 20)) + [21, 22, 23, 24, 25, 26]),
        new_y=("t", list(range(21, 37)) + [51, 52, 53, 54]),
    )
    pli = _prior_lede_index(
        slug_p=("Lede P", [1, 2]), slug_q=("Lede Q", [3, 4])
    )
    Tt = ReconcileThresholds(0.50, 0.20, 0.75, True)
    skip = compute_lede_skip_map(
        membership=mem, labels={k: k for k in mem["subtopics"]}, snapshot=snap,
        prior_lede_index=pli, reconcile_thresholds=Tt, skip_overlap_min=0.70,
    )
    assert "new_y" not in skip
    assert "new_q" not in skip


def test_compute_lede_skip_map_absent_prior_lede_falls_back():
    snap = _snap(st_a=("t", {1, 2, 3, 4}, "A", "slug_a"))
    mem = _mem(new_a=("t", [1, 2, 3, 4]))
    skip = compute_lede_skip_map(
        membership=mem, labels={"new_a": "alpha"}, snapshot=snap,
        prior_lede_index={}, reconcile_thresholds=T, skip_overlap_min=0.70,
    )
    assert skip == {}


def test_compute_lede_skip_map_empty_grounded_pmids_dropped():
    snap = _snap(st_a=("t", {1, 2, 3, 4}, "A", "slug_a"))
    mem = _mem(new_a=("t", [1, 2, 3, 4]))
    # prior entry has a lede but no grounded pmids -> can never satisfy gate 2.
    pli = {"slug_a": {"lede": "Lede A", "lede_grounded_pmids": []}}
    skip = compute_lede_skip_map(
        membership=mem, labels={"new_a": "alpha"}, snapshot=snap,
        prior_lede_index=pli, reconcile_thresholds=T, skip_overlap_min=0.70,
    )
    assert skip == {}


# ---------------------------------------------------------------------------
# current_grounding_pmids (gate 2 helper)
# ---------------------------------------------------------------------------


def test_current_grounding_pmids_matches_filter_and_clamp():
    papers = [
        _paper("10", 90.0),
        _paper("20", 80.0),
        _paper("30", 70.0),
        _paper("40", 60.0),
        _paper("50", 95.0, has_author=False),  # no author -> dropped
    ]
    got = current_grounding_pmids(papers)
    # author-valid, top-3 by impact DESC: 90(10), 80(20), 70(30).
    assert got == frozenset({"10", "20", "30"})


# ---------------------------------------------------------------------------
# lede_reuse_for (gate 2 evaluator)
# ---------------------------------------------------------------------------


def _skip_map_with(subtopic_id, grounded):
    return {
        subtopic_id: PriorLede(
            durable_id="dur", prior_slug="prior", lede="WCM scholars are x.",
            grounded_pmids=frozenset(grounded), overlap_score=1.0,
        )
    }


def test_lede_reuse_for_matching_grounding_returns_prior():
    sm = _skip_map_with("s1", {"a", "b", "c"})
    papers = [_paper("a", 90.0), _paper("b", 80.0), _paper("c", 70.0)]
    pr = lede_reuse_for(sm, "s1", papers)
    assert pr is not None
    assert pr.lede == "WCM scholars are x."


def test_lede_reuse_for_drifted_grounding_returns_none():
    """THE load-bearing test: identity stable (in the map) but grounding moved
    (d swapped for c) -> regenerate."""
    sm = _skip_map_with("s1", {"a", "b", "c"})
    papers = [_paper("a", 90.0), _paper("b", 80.0), _paper("d", 70.0)]
    assert lede_reuse_for(sm, "s1", papers) is None


def test_lede_reuse_for_absent_subtopic_returns_none():
    sm = _skip_map_with("s1", {"a", "b", "c"})
    papers = [_paper("a", 90.0), _paper("b", 80.0), _paper("c", 70.0)]
    assert lede_reuse_for(sm, "OTHER", papers) is None


def test_lede_reuse_for_fewer_than_min_papers_returns_none():
    sm = _skip_map_with("s1", {"a"})
    papers = [_paper("a", 90.0)]  # clamps to 1 < MIN_PAPERS
    assert lede_reuse_for(sm, "s1", papers) is None


# ---------------------------------------------------------------------------
# load_lede_skip_map (I/O shell, fail-soft)
# ---------------------------------------------------------------------------


def test_load_lede_skip_map_flag_off_returns_empty():
    """Against the real config (flag off): {} and the fakes are NEVER touched."""
    table = _FakeTable(items=[_store_item("d1", "slug_a", "t", [1, 2, 3, 4])])
    s3 = _FakeS3(artifact={"spotlights": []})
    out = load_lede_skip_map(table=table, s3_client=s3)
    assert out == {}
    assert s3.gets == []  # no S3 GET when off
    assert table.writes == []


def _enabled_thresholds(tmp_path, overlap_min=0.70):
    p = tmp_path / "thresholds.json"
    p.write_text(json.dumps({SKIP_LEDE_ENABLED_KEY: True, SKIP_OVERLAP_MIN_KEY: overlap_min}))
    return p


def test_load_lede_skip_map_empty_store_returns_empty(tmp_path):
    tp = _enabled_thresholds(tmp_path)
    table = _FakeTable(items=[])  # no store rows
    s3 = _FakeS3(artifact={"spotlights": [{"subtopic_id": "x", "lede": "L", "lede_grounded_pmids": ["1", "2"]}]})
    out = load_lede_skip_map(table=table, s3_client=s3, thresholds_path=tp)
    assert out == {}


def test_load_lede_skip_map_no_prior_spotlight_returns_empty(tmp_path):
    tp = _enabled_thresholds(tmp_path)
    table = _FakeTable(items=[_store_item("d1", "slug_a", "t", [1, 2, 3, 4])])
    s3 = _FakeS3(artifact={"spotlights": []})  # store populated, no prior ledes
    out = load_lede_skip_map(table=table, s3_client=s3, thresholds_path=tp)
    assert out == {}


def test_load_lede_skip_map_s3_error_returns_empty(tmp_path):
    tp = _enabled_thresholds(tmp_path)
    table = _FakeTable(items=[_store_item("d1", "slug_a", "t", [1, 2, 3, 4])])
    s3 = _FakeS3(raise_on_get=True)
    out = load_lede_skip_map(table=table, s3_client=s3, thresholds_path=tp)
    assert out == {}  # NEVER raises


def test_load_lede_skip_map_never_writes(tmp_path):
    tp = _enabled_thresholds(tmp_path)
    table = _FakeTable(items=[_store_item("d1", "slug_a", "t", [1, 2, 3, 4])])
    s3 = _FakeS3(
        artifact={"spotlights": [{"subtopic_id": "slug_a", "lede": "L", "lede_grounded_pmids": ["1", "2"]}]}
    )
    load_lede_skip_map(table=table, s3_client=s3, thresholds_path=tp)
    assert table.writes == []  # read-only invariant

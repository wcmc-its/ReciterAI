"""Unit tests for the MeSH descriptor evidence (HANDOFF-7 section 3): what the join
records per (pub, core), the one-key rule, the inertness claim, and what reaches
DynamoDB. No DB — the engine is faked at the two calls prefilter makes on it.

The point of the whole thing is that ReciterAI has never recorded WHICH descriptor
fired, so `topical_prior` cannot be audited and no per-descriptor lift can be computed.
These tests pin the record, and pin that recording it moved no score.
"""
from types import SimpleNamespace

from pipeline_cores.combine import WEIGHTS, evidence_features, explain, score
from pipeline_cores.dictionary import load_core
from pipeline_cores.models import SignalResult
from pipeline_cores.prefilter import (
    CORE_MESH_TREE_PREFIXES,
    compute_priors,
    core_mesh_tree_descriptors,
    core_mesh_tree_pmids,
)


class _FakeEngine:
    """Just enough sqlalchemy: `with engine.connect() as conn: conn.execute(sql, params)`.

    Records the statement and params so the SQL contract (which columns, which prefix
    binds) is assertable without a database.
    """

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def connect(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params or {}))
        return list(self.rows)


def _row(pmid, ui, label, tree):
    return SimpleNamespace(pmid=pmid, DescriptorUI=ui, Label=label, TreeNumber=tree)


# Core 11 (Microscopy) is the two-prefix core — the case where "which prefix" is a real
# question rather than a constant.
_ROWS = [
    _row(100, "D008854", "Microscopy, Confocal", "E01.370.350.515.512"),
    _row(100, "D008854", "Microscopy, Confocal", "E01.370.225.500.512"),  # 2nd tree no.
    _row(100, "D008853", "Microscopy, Electron", "E01.370.350.515.520"),
    _row(200, "D008853", "Microscopy, Electron", "E01.370.225.500.520"),
]


# --- the record ------------------------------------------------------------
def test_descriptors_are_recorded_per_pub_with_the_prefix_they_hit():
    """The finding this exists to fix: not a boolean and not a blended float, but which
    descriptor UI matched and which of the core's prefixes it sat under."""
    engine = _FakeEngine(_ROWS)
    idx = core_mesh_tree_descriptors(engine, "11", ["100", "200"])

    assert set(idx) == {"100", "200"}
    assert idx["200"] == [("D008853", "Microscopy, Electron", "E01.370.225.500")]
    # Both of core 11's prefixes are attributed, on the same pub and the same descriptor.
    assert sorted({prefix for _ui, _label, prefix in idx["100"]}) == [
        "E01.370.225.500", "E01.370.350.515"]
    # Every entry is self-contained: a UI to compute lift over, a label to check it by,
    # and the branch it hit.
    assert all(ui and label and prefix for ui, label, prefix in idx["100"])


def test_one_descriptor_with_several_tree_numbers_under_one_prefix_is_one_entry():
    """A descriptor sits at many tree positions. Deduped on (ui, label, prefix), so the
    list is a list of CLAIMS rather than a count of tree rows."""
    engine = _FakeEngine([
        _row(100, "D008854", "Microscopy, Confocal", "E01.370.350.515.512"),
        _row(100, "D008854", "Microscopy, Confocal", "E01.370.350.515.999"),
    ])
    assert core_mesh_tree_descriptors(engine, "11", ["100"]) == {
        "100": [("D008854", "Microscopy, Confocal", "E01.370.350.515")]}


def test_a_pub_with_no_matching_descriptors_is_simply_absent():
    """No raise, no empty-string sentinel: the pub is not a key, and run_core turns that
    into an empty list."""
    idx = core_mesh_tree_descriptors(_FakeEngine([]), "2", ["100"])
    assert idx == {}
    assert (idx.get("100") or []) == []


def test_a_core_with_no_mapped_prefix_never_touches_the_engine():
    """6 of 14 cores have no clean technique branch — including core 14, the only core
    on a nightly. They must cost zero, not an empty query."""
    assert "14" not in CORE_MESH_TREE_PREFIXES
    assert core_mesh_tree_descriptors(None, "14", ["100"]) == {}


def test_the_query_selects_the_descriptor_and_binds_every_core_prefix():
    """The SQL contract. The descriptor UI is the column the whole retrospective rests
    on, and each of the core's prefixes has to reach the WHERE clause."""
    engine = _FakeEngine(_ROWS)
    core_mesh_tree_descriptors(engine, "11", ["100"])
    sql, params = engine.calls[0]
    assert "m.DescriptorUI" in sql and "mtn.TreeNumber" in sql
    assert sorted(v for k, v in params.items() if k[1:].isdigit()) == [
        "E01.370.225.500%", "E01.370.350.515%"]
    assert params["pmids"] == [100]


# --- the prior is untouched ------------------------------------------------
def test_the_membership_set_and_therefore_the_prior_is_unchanged():
    """`core_mesh_tree_pmids` is untouched (its own narrower query, batch_screen's hot
    path). Pinned here because the two run the same join and must agree: same pubs in,
    same prior out — the descriptors ride along, they do not move `topical_prior`."""
    pmids = ["100", "200", "300"]
    idx = core_mesh_tree_descriptors(_FakeEngine(_ROWS), "11", pmids)
    assert core_mesh_tree_pmids(_FakeEngine(_ROWS), "11", pmids) == set(idx) == {"100", "200"}
    assert compute_priors(pmids, mesh_pmids=set(idx), author_pmids=set()) == {
        "100": 0.4, "200": 0.4, "300": 0.0}


# --- the one-key rule and the inertness claim ------------------------------
def test_evidence_features_emits_exactly_one_mesh_key():
    """One key however many descriptors matched: a count, a per-prefix key or a
    depth-graded key would all be specificity schemes invented before the lift table
    that would justify one."""
    idx = core_mesh_tree_descriptors(_FakeEngine(_ROWS), "11", ["100"])
    keys = evidence_features(SignalResult(mesh_evidence=idx["100"]))
    assert [k for k in keys if k.startswith("mesh:")] == ["mesh:tree"]
    assert evidence_features(SignalResult(mesh_evidence=[])) == []


def test_the_key_is_visible_in_explain_at_zero():
    """Persisted AND shown — the reviewer-facing half of the ticket. `explain()` is what
    the claim queue renders, so a key it cannot see is a key nobody can audit."""
    idx = core_mesh_tree_descriptors(_FakeEngine(_ROWS), "11", ["100"])
    assert ("mesh:tree", 0.00) in explain(SignalResult(mesh_evidence=idx["100"]))
    assert WEIGHTS["mesh:tree"] == 0.00


def test_the_signal_is_inert():
    """The claim, mechanically checked: at weight 0.00 the score is bit-identical with
    and without the descriptors, for a pair with other evidence and one with none."""
    idx = core_mesh_tree_descriptors(_FakeEngine(_ROWS), "11", ["100"])
    for base in (SignalResult(),
                 SignalResult(ack_matched=True, ack_alias="a", ack_alias_hits=25,
                              ack_institution="home", coauthor_cwids=["abc1001"],
                              llm_score=7, author_affinity=0.42)):
        withmesh = SignalResult(**{**base.__dict__, "mesh_evidence": idx["100"]})
        assert score(withmesh) == score(base)


# --- persistence -----------------------------------------------------------
def test_build_core_item_emits_mesh_evidence_and_owns_it():
    from pipeline_cores.models import CoreUsageRecord
    from pipeline_cores.persist import _OWNED_ATTRS, build_core_item

    idx = core_mesh_tree_descriptors(_FakeEngine(_ROWS), "11", ["100"])
    sig = SignalResult(mesh_evidence=idx["100"])
    item = build_core_item(CoreUsageRecord("100", "11", 0.4, "candidate", sig, "t"))
    entries = item["mesh_evidence"]["L"]
    assert len(entries) == 3
    assert entries[0]["M"]["descriptor_ui"] == {"S": "D008853"}
    assert entries[0]["M"]["descriptor"] == {"S": "Microscopy, Electron"}
    # Every entry is self-contained in DynamoDB too, not just in memory.
    assert all(set(e["M"]) == {"descriptor_ui", "descriptor", "tree_prefix"} for e in entries)
    assert all(e["M"]["tree_prefix"]["S"] in CORE_MESH_TREE_PREFIXES["11"] for e in entries)
    # Outside _OWNED_ATTRS an attribute is never swept into REMOVE, so a descriptor the
    # run no longer supports would survive on the row.
    assert "mesh_evidence" in _OWNED_ATTRS
    # ...and a pair with no descriptor emits nothing at all.
    bare = build_core_item(CoreUsageRecord("300", "11", 0.1, "candidate", SignalResult(), "t"))
    assert "mesh_evidence" not in bare


# --- run_core wiring -------------------------------------------------------
def test_run_core_sets_mesh_evidence(monkeypatch):
    """Wiring, not scoring. Every weight is 0.00, so no likelihood or status assertion
    anywhere can notice this signal never being connected."""
    from pipeline_cores import ingest, run, signals

    core = load_core("11")
    pubs = [{"pmid": "100", "title": "confocal paper", "abstract": ""},
            {"pmid": "300", "title": "no descriptors", "abstract": ""}]
    monkeypatch.setattr(signals, "coauthorship_index", lambda e, c, p, **k: {})
    monkeypatch.setattr(ingest, "fetch_author_bylines", lambda e, p: {})
    monkeypatch.setattr(ingest, "fetch_author_totals", lambda e, c=None: {})

    idx = core_mesh_tree_descriptors(_FakeEngine(_ROWS), "11", ["100"])
    recs = {r.pmid: r for r in run.run_core(core, pubs, bedrock=None, threshold=0.30,
                                            scored_at="t", engine=None, dry_run=True,
                                            mesh_index=idx)}
    assert recs["100"].signals.mesh_evidence == idx["100"]
    # A pub with no MeSH descriptors: an empty list, no raise, no key.
    assert recs["300"].signals.mesh_evidence == []
    assert "mesh:tree" not in evidence_features(recs["300"].signals)


def test_cli_run_hands_the_production_mesh_index_to_the_scorer(monkeypatch):
    """The one line in `main()` that connects the production index to the scorer.

    Every `mesh:tree` weight is 0.00, so deleting `mesh_index=mesh_index` from that call
    moves no likelihood, no status and no evidence key anywhere else — the signal just
    silently stops being recorded and the rest of the suite stays green. Declared but not
    connected is this repo's signature failure; this is the pin for it.
    """
    from unittest.mock import MagicMock

    import utils.db as db
    from pipeline_cores import ingest, prefilter, run, signals

    pubs = [{"pmid": "100", "title": "confocal paper", "abstract": ""},
            {"pmid": "300", "title": "no descriptors", "abstract": ""}]
    index = {"100": [("D008854", "Microscopy, Confocal", "E01.370.350.515")]}

    monkeypatch.setattr(db, "get_engine", lambda: MagicMock(name="engine"))
    monkeypatch.setattr(ingest, "fetch_publications", lambda e, pmids=None, limit=None: pubs)
    monkeypatch.setattr(ingest, "fetch_author_bylines", lambda e, p: {})
    monkeypatch.setattr(ingest, "fetch_author_totals", lambda e, c=None: {})
    monkeypatch.setattr(signals, "coauthorship_index", lambda e, c, p, **k: {})

    # The loader, stubbed where run.py reaches it: no DB, and the pool main() scopes the
    # query to is checkable.
    seen = {}

    def _loader(engine, core_id, pmid_pool):
        seen["core_id"], seen["pool"] = core_id, list(pmid_pool)
        return index

    monkeypatch.setattr(prefilter, "core_mesh_tree_descriptors", _loader)

    # run_core is the REAL one; the spy only keeps what main() handed it and what came
    # back, because a --dry-run main() writes nothing and returns nothing to assert on.
    real_run_core, captured = run.run_core, {}

    def _spy(core, core_pubs, **kwargs):
        captured["kwargs"] = kwargs
        captured["recs"] = real_run_core(core, core_pubs, **kwargs)
        return captured["recs"]

    monkeypatch.setattr(run, "run_core", _spy)

    run.main(["--core", "11", "--dry-run"])

    assert seen == {"core_id": "11", "pool": ["100", "300"]}
    # The connection itself: main()'s index reaches run_core...
    assert captured["kwargs"].get("mesh_index") == index
    # ...and comes out the other side as evidence on the record, not merely as a kwarg.
    recs = {r.pmid: r for r in captured["recs"]}
    assert recs["100"].signals.mesh_evidence == index["100"]
    assert recs["300"].signals.mesh_evidence == []

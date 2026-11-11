"""Tests for pipeline_tools.state — live-reconciled tools state, zero-delta re-emit, storage.

The fixture builds a tiny "live" artifact the way production did: a corpus-shaped payload
from a pre-collapse registry, then ``cli.collapse_alias_forks.collapse_forks`` (the real
post-hoc pass), rendered through the real publish path. Reconciling the pre-collapse
registry to that live set must reproduce it byte for byte. No AWS, no LLM.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from cli.collapse_alias_forks import collapse_forks
from pipeline_tools import state as st
from pipeline_tools.embeddings import EmbeddingCache
from pipeline_tools.publish import S3_PREFIX, _family_record, build_publish_items
from pipeline_tools.registry import FamilyRegistry, ToolRegistry
from pipeline_tools.seed import _build_hierarchy, _enriched_records

MERGES = [{"class": "x", "canonical": "X cells", "variants": ["X cell line", "X"]}]
GENERATED_AT = "2026-06-23T17:56:59Z"


def _no_embed(texts):
    raise AssertionError("embedding seam must not be called")


def _tool(cid, name, pub_ids, cbp, *, tier="A", fam="fam_0001", ctx=None):
    return {
        "canonical_tool_id": cid, "display_name": name, "aliases": [name], "disposition": "method_tool",
        "kind": "organism_or_cells", "supercategory": "animal_cell_models", "member_of_family": fam,
        "salience_tier": tier, "salience_tier_basis": "grounded", "pub_ids": list(pub_ids),
        "pub_count": len(pub_ids), "context_by_pub": dict(cbp), "context_evidence": list(ctx or []),
        "classified_by": "model-x", "classified_by_assumed": False, "flags": [],
        "attributes": {"delivery": None, "provenance": None, "license": None,
                       "consumable": False, "rrid_candidate": False},
    }


def _registry():
    tools = [
        _tool("tool_000001", "X cell line", ["101"], {"101": "We grew X cell line cultures."}, ctx=["c1"]),
        _tool("tool_000009", "X", ["102"], {"102": "X was used."}, ctx=["c2"]),
        _tool("tool_000010", "Y", ["103", "104"], {"103": "Y assay.", "104": "Y again."}),
        # seed-count tool: a pub_count with no pub_ids (as 53 live tools are)
        {**_tool("tool_000011", "Z", [], {}, tier="B"), "pub_count": 4},
    ]
    fams = [{
        "family_id": "fam_0001", "label": "Cell lines", "supercategory": "animal_cell_models",
        "dominant_kind": "organism_or_cells", "status": "active", "display": "feature",
        "definition": "Registry-side definition.", "definition_confidence": 0.9,
        "member_tool_ids": ["tool_000001", "tool_000009", "tool_000010", "tool_000011"],
        "member_display_names": ["X cell line", "X", "Y", "Z"],
        "exemplar_tool_ids": ["tool_000009", "tool_000001"],
    }]
    return tools, fams


def _faculty():
    return {"a1": {"cwid": "a1", "tools": [
        {"canonical_tool_id": "tool_000001", "display_name": "X cell line", "pub_count": 1, "pmids": ["101"]},
        {"canonical_tool_id": "tool_000009", "display_name": "X", "pub_count": 1, "pmids": ["102"]},
        {"canonical_tool_id": "tool_000010", "display_name": "Y", "pub_count": 2, "pmids": ["103", "104"]},
    ], "families": [{"family_id": "fam_0001", "label": "Cell lines", "supercategory": "animal_cell_models",
                     "pmids": ["101", "102", "103", "104"], "pub_count": 4,
                     "exemplar_tool_ids": ["tool_000009", "tool_000001"]}]}}


def _build_live(tmp_path: Path, *, collapse: bool = True) -> Path:
    """Corpus-shaped payload -> (optional) collapse_forks -> curated edits -> real publish render."""
    from pipeline_tools.entities import build_entity_layer

    tools, fams = _registry()
    tr = ToolRegistry(copy.deepcopy(tools), cache=EmbeddingCache(embed=_no_embed))
    fr = FamilyRegistry(copy.deepcopy(fams), cache=EmbeddingCache(embed=_no_embed))
    records = _enriched_records(tr, fr)
    tool_context = {r["canonical_tool_id"]: dict(sorted(r["context_by_pub"].items()))
                    for r in tr.records() if r["context_by_pub"]}
    entities, entity_context, _ = build_entity_layer(records, fr.records(), tool_context, generic_terms=[])
    payload = {
        "schema_version": "tools-a2-v3",
        "provenance": {"run_at_utc": "2026-06-13T22:08:21Z", "raw_mentions": 4},
        "salience_thresholds": {"s_spread_min": 5, "a_pub_floor": 3, "a_spread_floor": 2, "percentile": 0.88},
        "tools": records,
        "families": [_family_record(f) for f in fr.records()],
        "hierarchy": _build_hierarchy(tr, fr),
        "faculty": _faculty(),
        "grant_signal": {"tool_000009": {"appl_ids": ["g1"], "investigator_cwids": ["a1"]}},
        "telemetry": {"canonical_tools": len(records), "grant_signal_tools": 1},
        "exceptions_summary": {},
        "tool_context": tool_context,
        "entities": entities,
        "entity_context": entity_context,
    }
    if collapse:
        collapse_forks(payload, MERGES, generic_terms=[])
    # Curation done on live only (never written back to the registry):
    payload["families"][0]["definition"] = "Live curated definition."
    payload["tool_context"]["tool_000010"]["103"] = "The Y assay was run on every sample."  # #239-style
    items, _ = build_publish_items(payload, prefix=S3_PREFIX, generated_at=GENERATED_AT)
    live_dir = tmp_path / "live"
    live_dir.mkdir()
    for it in items:
        if it.key.startswith(f"{S3_PREFIX}latest/"):
            (live_dir / it.key.rsplit("/", 1)[-1]).write_bytes(it.body)
    return live_dir


def _reconciled(tmp_path, **kw):
    live_dir = _build_live(tmp_path, **kw)
    raw = st.read_live_dir(live_dir)
    tools, fams = _registry()
    state = st.reconcile(tools, fams, ["junk"], st.parse_live(raw), merges=MERGES)
    return state, raw


# --- reconcile + collapse ----------------------------------------------------


def test_zero_delta_reemit_is_byte_identical(tmp_path):
    state, raw = _reconciled(tmp_path)
    items, _ = st.reemit(state)
    cmp = st.compare_to_live(items, raw)
    assert cmp["all_objects_identical"], cmp
    assert cmp["manifest"]["identical"], cmp["manifest"]
    assert all(o["flat_equals_latest"] for o in cmp["objects"].values())
    assert st.check_meta(state, items)


def test_collapse_applied_at_registry_grain(tmp_path):
    state, _ = _reconciled(tmp_path)
    rep = state.reconcile_report["collapse"]
    assert rep["dropped"] == ["tool_000009"]
    assert rep["applied"][0]["keep"] == "tool_000001"
    by_id = {r["canonical_tool_id"]: r for r in state.tool_records}
    assert "tool_000009" not in by_id
    keep = by_id["tool_000001"]
    assert keep["display_name"] == "X cells"
    assert keep["pub_ids"] == ["101", "102"] and keep["pub_count"] == 2   # unioned, not invented
    assert set(keep["aliases"]) >= {"X", "X cell line"}
    assert keep["context_by_pub"] == {"101": "We grew X cell line cultures.", "102": "X was used."}
    fam = state.family_records[0]
    assert fam["member_tool_ids"] == ["tool_000001", "tool_000010", "tool_000011"]
    assert fam["member_display_names"] == ["X cell line", "Y", "Z"]          # parallel list kept aligned
    assert fam["exemplar_tool_ids"] == ["tool_000001"]                       # 000009 -> 000001, dedup
    # the registry's tool records match live with no field-level overlay needed
    assert state.reconcile_report["tool_overlay"]["fields"] == {}


def test_live_curation_wins_over_registry(tmp_path):
    state, _ = _reconciled(tmp_path)
    fam = state.family_records[0]
    assert fam["definition"] == "Live curated definition."
    assert fam["definition_confidence"] is None
    assert state.reconcile_report["family_overlay"]["fields"] == {"definition": 1}
    y = next(r for r in state.tool_records if r["canonical_tool_id"] == "tool_000010")
    assert y["context_by_pub"]["103"] == "The Y assay was run on every sample."
    assert state.reconcile_report["tool_context_vs_registry"]["snippets_replaced"] == 1


def test_live_tool_order_is_preserved(tmp_path):
    # collapse grew tool_000001's pub_count without re-sorting live tools[]
    state, raw = _reconciled(tmp_path)
    live_order = [t["canonical_tool_id"] for t in json.loads(raw["tools.json"])["tools"]]
    canonical = [r["canonical_tool_id"] for r in _enriched_records(state.tool_registry(), state.family_registry())]
    assert live_order != canonical
    assert state.bundle["tool_order"] == live_order


def test_seed_count_tool_keeps_live_count_without_inventing_ids(tmp_path):
    state, _ = _reconciled(tmp_path)
    z = next(r for r in state.tool_records if r["canonical_tool_id"] == "tool_000011")
    assert z["pub_ids"] == [] and z["pub_count"] == 4
    pub = state.reconcile_report["pub_ids"]
    assert pub["seed_count_only"] == ["tool_000011"]
    assert pub["n_count_mismatch"] == 0


def test_cluster_not_collapsed_in_live_is_left_alone(tmp_path):
    state, raw = _reconciled(tmp_path, collapse=False)
    assert state.reconcile_report["collapse"]["applied"] == []
    assert state.reconcile_report["collapse"]["skipped"][0]["ids"] == ["tool_000001", "tool_000009"]
    assert len(state.tool_records) == 4
    items, _ = st.reemit(state)
    assert st.compare_to_live(items, raw)["all_objects_identical"]


def test_live_tool_missing_from_registry_raises(tmp_path):
    live = st.parse_live(st.read_live_dir(_build_live(tmp_path)))
    tools, fams = _registry()
    tools = [t for t in tools if t["canonical_tool_id"] != "tool_000010"]
    with pytest.raises(st.StateError, match="missing from the registry"):
        st.reconcile(tools, fams, [], live, merges=MERGES)


def test_pub_ids_disagreeing_with_live_count_raises(tmp_path):
    live = st.parse_live(st.read_live_dir(_build_live(tmp_path)))
    tools, fams = _registry()
    tools[2]["pub_ids"] = ["103"]  # Y: live pub_count 2
    with pytest.raises(st.StateError, match="disagree with live"):
        st.reconcile(tools, fams, [], live, merges=MERGES)


def test_read_live_dir_rejects_object_not_matching_manifest(tmp_path):
    live_dir = _build_live(tmp_path)
    (live_dir / "faculty.json").write_bytes(b"{}")
    with pytest.raises(st.StateError, match="faculty.json"):
        st.read_live_dir(live_dir)


def test_order_tools_appends_new_ids():
    recs = [{"canonical_tool_id": c} for c in ("t_new", "t2", "t1")]
    assert [r["canonical_tool_id"] for r in st.order_tools(recs, ["t1", "t2"])] == ["t1", "t2", "t_new"]
    assert st.order_tools(recs, None) == recs


# --- storage -----------------------------------------------------------------


def test_save_load_round_trip_and_latest_pointer(tmp_path):
    state, raw = _reconciled(tmp_path)
    store = st.LocalStateStore(tmp_path / "state")
    m = st.save_state(store, state, "v1", created_at="2026-11-11T00:00:00Z")
    assert set(m["files"]) >= {"tool_registry.json", "family_registry.json", "bundle.json", "tool_context.json",
                               "tool_context_meta.json", "entities.json", "entity_context.json",
                               "baseline_manifest.json", "tool_denylist.json"}
    assert st.resolve_latest(store) == "v1"
    loaded = st.load_state(store)
    assert loaded.version == "v1"
    for f in ("tool_records", "family_records", "denylist", "bundle", "tool_context", "tool_context_meta",
              "entities", "entity_context", "baseline_manifest", "reconcile_report"):
        assert getattr(loaded, f) == getattr(state, f), f
    items, _ = st.reemit(loaded)
    assert st.compare_to_live(items, raw)["all_objects_identical"]


def test_versions_are_immutable_and_pointer_moves(tmp_path):
    state, _ = _reconciled(tmp_path)
    store = st.LocalStateStore(tmp_path / "state")
    st.save_state(store, state, "v1", created_at="t1")
    with pytest.raises(st.StateError, match="already exists"):
        st.save_state(store, state, "v1", created_at="t2")
    state.parent_version = "v1"
    st.save_state(store, state, "v2", created_at="t2")
    assert st.resolve_latest(store) == "v2"
    assert st.load_state(store, "v1").version == "v1"           # older version still loadable (rollback)
    st.save_state(store, state, "v3", created_at="t3", set_latest=False)
    assert st.resolve_latest(store) == "v2"
    assert st.load_state(store).parent_version == "v1"


def test_load_rejects_tampered_file(tmp_path):
    state, _ = _reconciled(tmp_path)
    store = st.LocalStateStore(tmp_path / "state")
    st.save_state(store, state, "v1", created_at="t")
    p = tmp_path / "state" / "v1" / "tool_context.json"
    p.write_bytes(p.read_bytes().replace(b"Y again.", b"Y AGAIN."))
    with pytest.raises(st.StateError, match="sha256 mismatch"):
        st.load_state(store)


def test_s3_store_uses_prefix_and_lazy_client():
    calls = []

    class FakeS3:
        def put_object(self, key, body, content_type=None, cache_control=None):
            calls.append(("put", key))

        def get_object_bytes(self, key):
            calls.append(("get", key))
            return b"{}"

        def key_exists(self, key):
            return False

    s = st.S3StateStore(client=FakeS3())
    s.write("v1/bundle.json", b"{}")
    s.read(st.LATEST_POINTER)
    assert calls == [("put", "tools/_state/v1/bundle.json"), ("get", "tools/_state/latest.json")]
    assert st.S3StateStore()._client is None  # nothing built until first use

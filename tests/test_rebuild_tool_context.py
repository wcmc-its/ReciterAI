"""Unit tests for cli.rebuild_tool_context (#238 context-only sidecar rebuild).

Pure logic + a stubbed LLM seam — no AWS/DB. Verifies grouping, the guarded
parse, the upgrade-only apply, the sidecar build, and a small end-to-end run.
(The snippet guards themselves live in tests/test_context_quality.py.)
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal

import pytest

from pipeline_tools.cost_guard import CostCeiling
from utils.bedrock_client import HAIKU_MODEL
from cli.rebuild_tool_context import (
    apply_regen,
    build_sidecar,
    fragment_metrics,
    group_by_pmid,
    parse_regen_response,
    republish_sidecar,
    run_rebuild,
)


@dataclass
class FakeResult:
    text: str
    input_tokens: int = 50
    output_tokens: int = 20
    model: str = HAIKU_MODEL


ABSTRACT = (
    "We applied a bipartite network algorithm to identify subtypes based on baseline "
    "clinical characteristics. Magnetic resonance imaging revealed a scalpel sign."
)


def _records():
    return [
        {"canonical_tool_id": "t1", "display_name": "Bipartite network algorithm",
         "context_by_pub": {"100": "identify subtypes based on baseline clinical characteristics"}},
        {"canonical_tool_id": "t2", "display_name": "Magnetic resonance imaging (MRI)",
         "context_by_pub": {"100": "Magnetic resonance imaging revealed a scalpel sign."}},
        {"canonical_tool_id": "t3", "display_name": "No-context tool", "context_by_pub": {}},
    ]


def test_group_by_pmid_collects_pairs_sorted():
    g = group_by_pmid(_records())
    assert set(g) == {"100"}
    assert g["100"] == [("t1", "Bipartite network algorithm"), ("t2", "Magnetic resonance imaging (MRI)")]


def test_parse_regen_maps_index_to_cid_with_guard():
    names = ["Bipartite network algorithm", "Magnetic resonance imaging (MRI)"]
    cids = ["t1", "t2"]
    text = ('{"1": "We applied a bipartite network algorithm to identify subtypes based on baseline '
            'clinical characteristics.", "2": "INVENTED sentence not in abstract"}')
    out = parse_regen_response(text, names, cids, ABSTRACT)
    assert "t1" in out and out["t1"].startswith("We applied a bipartite")
    assert "t2" not in out  # invented sentence fails the verbatim guard


def test_apply_regen_upgrades_only_and_never_drops():
    recs = _records()
    regen = {"100": {"t1": "We applied a bipartite network algorithm to identify subtypes based on "
                           "baseline clinical characteristics."}}
    stats = apply_regen(recs, regen)
    assert stats["upgraded"] == 1
    # t1 upgraded to the full sentence; t2 (absent from regen) retained verbatim.
    assert recs[0]["context_by_pub"]["100"].startswith("We applied a bipartite")
    assert recs[1]["context_by_pub"]["100"] == "Magnetic resonance imaging revealed a scalpel sign."


def test_build_sidecar_shape_and_sorted():
    recs = _records()
    side = build_sidecar(recs)
    assert side == {
        "t1": {"100": "identify subtypes based on baseline clinical characteristics"},
        "t2": {"100": "Magnetic resonance imaging revealed a scalpel sign."},
    }
    assert "t3" not in side  # no context => omitted


def test_fragment_metrics_flags_mid_clause():
    m = fragment_metrics(build_sidecar(_records()))
    assert m["snippets"] == 2
    assert m["start_lowercase_pct"] == 50.0  # the t1 fragment starts lowercase


def test_run_rebuild_end_to_end_with_stub():
    recs = _records()
    by_pmid = group_by_pmid(recs)
    abstracts = {"100": ABSTRACT}

    def stub(system, user):
        return FakeResult('{"1": "We applied a bipartite network algorithm to identify subtypes based '
                          'on baseline clinical characteristics.", "2": "Magnetic resonance imaging '
                          'revealed a scalpel sign."}')

    ceiling = CostCeiling(cap_usd=Decimal("5"))
    res = run_rebuild(by_pmid, abstracts, call_llm=stub, ceiling=ceiling, checkpoint_path=None, workers=2)
    assert res.n_done == 1 and res.n_failed == 0
    apply_regen(recs, res.regen)
    after = fragment_metrics(build_sidecar(recs))
    assert after["start_lowercase_pct"] == 0.0  # both upgraded to full sentences


# ---------------------------------------------------------------------------
# republish (gated)
# ---------------------------------------------------------------------------

def _payload_and_files(tmp_path, *, context):
    payload = {
        "schema_version": "tools-a2-v2", "provenance": {"run": "x"},
        "tools": [], "families": [], "hierarchy": {}, "faculty": [], "tool_context": context,
    }
    bundle = {k: v for k, v in payload.items() if k != "tool_context"}
    live_tools = tmp_path / "live_tools.json"
    live_tools.write_text(json.dumps(bundle))
    sidecar = tmp_path / "sidecar.json"
    sidecar.write_text(json.dumps(context))
    return live_tools, sidecar


def test_republish_dry_run_emits_full_set_without_upload(tmp_path):
    live_tools, sidecar = _payload_and_files(tmp_path, context={"t1": {"100": "A full sentence."}})
    out = republish_sidecar(str(sidecar), str(live_tools), publish=False)
    assert out["published"] is False
    keys = {r["key"].rsplit("/", 1)[-1] for r in out["report"]}
    assert {"tools.json", "families.json", "faculty.json", "tool_context.json", "manifest.json"} <= keys
    assert all(r["uploaded"] is False for r in out["report"])  # dry-run: nothing written


def test_republish_aborts_if_a_frozen_artifact_would_change(tmp_path):
    live_tools, sidecar = _payload_and_files(tmp_path, context={"t1": {"100": "A full sentence."}})
    # A manifest claiming a DIFFERENT tools.json sha => the freeze guard must abort.
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"objects": {"tools.json": {"sha256": "deadbeef"}}}))
    with pytest.raises(SystemExit):
        republish_sidecar(str(sidecar), str(live_tools), live_manifest_path=str(manifest), publish=False)


def test_republish_never_wipes_the_live_entity_sidecars(tmp_path):
    # The tools.json bundle never carries the #1166 entity layer, so _split_artifacts renders
    # entities.json / entity_context.json EMPTY here; a context republish must NOT publish those
    # empties (that wipes the live layer) — it excludes them and preserves the live manifest entries.
    import hashlib
    from pipeline_tools.publish import S3_PREFIX, _split_artifacts

    live_tools, sidecar = _payload_and_files(tmp_path, context={"t1": {"100": "A full sentence."}})
    payload = {**json.load(open(live_tools)), "tool_context": json.load(open(sidecar))}
    items = _split_artifacts(payload, prefix=S3_PREFIX)
    shas = {it.key.rsplit("/", 1)[-1]: hashlib.sha256(it.body).hexdigest()
            for it in items if it.key.startswith(f"{S3_PREFIX}latest/")}
    live_entities = {"key": "tools/latest/entities.json", "bytes": 999, "sha256": "LIVEENTITIES"}
    live_ctx = {"key": "tools/latest/entity_context.json", "bytes": 42, "sha256": "LIVECTX"}
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "objects": {
            "tools.json": {"sha256": shas["tools.json"]},
            "families.json": {"sha256": shas["families.json"]},
            "faculty.json": {"sha256": shas["faculty.json"]},
            "entities.json": live_entities,
            "entity_context.json": live_ctx,
        },
        "counts": {"entities": 5, "entity_context": 3},
    }))
    out = republish_sidecar(str(sidecar), str(live_tools), live_manifest_path=str(manifest), publish=False)
    upload_leaves = {r["key"].rsplit("/", 1)[-1] for r in out["report"]}
    assert "entities.json" not in upload_leaves and "entity_context.json" not in upload_leaves
    # the live entity objects + counts survive verbatim in the republished manifest
    assert out["manifest"]["objects"]["entities.json"] == live_entities
    assert out["manifest"]["objects"]["entity_context.json"] == live_ctx
    assert out["manifest"]["counts"]["entities"] == 5 and out["manifest"]["counts"]["entity_context"] == 3


def test_republish_publish_requires_a_live_manifest(tmp_path):
    # A real publish without the live manifest can't preserve the entity sidecars -> refuse.
    live_tools, sidecar = _payload_and_files(tmp_path, context={"t1": {"100": "A full sentence."}})
    with pytest.raises(SystemExit):
        republish_sidecar(str(sidecar), str(live_tools), publish=True)

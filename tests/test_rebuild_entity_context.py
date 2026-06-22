"""Unit tests for cli.rebuild_entity_context (#254 re-projection backfill).

Pure logic + the freeze-checked republish path — no AWS. Verifies that
re-projecting the entity layer over an already-aligned tool_context yields clean,
sentence-complete snippets with recomputed spans, preserves parent descriptors,
and that the republish dry-run emits the full object set while the freeze guard
aborts if a sibling artifact would change.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from pipeline_tools.publish import S3_PREFIX, _split_artifacts
from cli.rebuild_entity_context import (
    FROZEN_ARTIFACTS,
    entity_fragment_metrics,
    reproject_entity_context,
    republish_entity_context,
)

TOOLS = [{"canonical_tool_id": "tool_1", "display_name": "HEK293 cells",
          "method_family_id": "fam_cl", "pub_count": 5, "aliases": []}]
FAMILIES = [{"family_id": "fam_cl", "label": "Immortalized cell lines",
             "supercategory": "animal_cell_models", "dominant_kind": "organism_or_cells",
             "status": "active", "member_tool_ids": ["tool_1"]}]
ALIGNED_TC = {"tool_1": {"111": "The F220C opsin was expressed in HEK293 cells."}}
# The pre-#254 live state: a mid-clause fragment for the same (entity, pmid).
STALE_ECTX = {"tool_1": {"111": [{
    "usage_sentence": "they both dimerize in the plasma membrane of HEK293 cells",
    "span": None, "centrality_score": 0.0, "sentence_complete": False, "role": None,
}]}}


# --- pure re-projection ----------------------------------------------------

def test_reproject_produces_clean_snippets_and_recomputes_span():
    entities, ectx = reproject_entity_context(TOOLS, FAMILIES, ALIGNED_TC)
    fact = ectx["tool_1"]["111"][0]
    assert fact["sentence_complete"] is True
    s = fact["usage_sentence"]
    assert s.endswith(".")  # the clean, aligned sentence
    # span recomputed against the clean text (not a stale offset)
    assert s[fact["span"][0]:fact["span"][1]].lower().startswith("hek293")


def test_reproject_preserves_parent_descriptors_without_llm():
    tools = [
        {"canonical_tool_id": "tool_1", "display_name": "3T3-L1 adipocytes",
         "method_family_id": "fam_cl", "pub_count": 4, "aliases": []},
        {"canonical_tool_id": "tool_2", "display_name": "3T3-L1 preadipocytes",
         "method_family_id": "fam_cl", "pub_count": 3, "aliases": []},
    ]
    families = [{"family_id": "fam_cl", "label": "Immortalized cell lines",
                 "supercategory": "animal_cell_models", "dominant_kind": "organism_or_cells",
                 "status": "active", "member_tool_ids": ["tool_1", "tool_2"]}]
    tc = {"tool_1": {"111": "3T3-L1 adipocytes were treated with metformin."}}

    live_entities, _ = reproject_entity_context(tools, families, tc)
    pid = next(e["parent_entity_id"] for e in live_entities if e["parent_entity_id"])
    for e in live_entities:
        if e["parent_entity_id"] == pid:
            e["parent_descriptor"] = "mouse fibroblast line"

    entities, _ = reproject_entity_context(tools, families, tc, live_entities=live_entities)
    nested = [e for e in entities if e["parent_entity_id"] == pid]
    assert nested and all(e["parent_descriptor"] == "mouse fibroblast line" for e in nested)


def test_entity_fragment_metrics_flags_fragments():
    before = entity_fragment_metrics(STALE_ECTX)
    assert before["snippets"] == 1
    assert before["start_lowercase_pct"] == 100.0  # the stale fragment starts lowercase


# --- freeze-checked republish ---------------------------------------------

def _write_live(tmp_path, *, tool_context, entity_context):
    """Write the live latest/* object set + a matching manifest; return the dir."""
    entities, _ = reproject_entity_context(TOOLS, FAMILIES, tool_context)
    payload = {
        "schema_version": "tools-a2-v4", "provenance": {"run": "live"},
        "salience_thresholds": {}, "tools": TOOLS, "families": FAMILIES,
        "hierarchy": {}, "faculty": [], "grant_signal": {}, "telemetry": {},
        "exceptions_summary": {},
        "tool_context": tool_context, "entities": entities, "entity_context": entity_context,
    }
    items = _split_artifacts(payload, prefix=S3_PREFIX)
    latest = {it.key.rsplit("/", 1)[-1]: it for it in items
              if it.key.startswith(f"{S3_PREFIX}latest/")}
    for name in ("tools.json", "families.json", "faculty.json",
                 "tool_context.json", "entities.json", "entity_context.json"):
        (tmp_path / name).write_bytes(latest[name].body)
    manifest = {"objects": {name: {"sha256": hashlib.sha256(it.body).hexdigest()}
                            for name, it in latest.items()}}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    return tmp_path


def test_republish_dry_run_emits_full_set_and_improves_fragments(tmp_path):
    d = _write_live(tmp_path, tool_context=ALIGNED_TC, entity_context=STALE_ECTX)
    out = republish_entity_context(
        str(d / "tools.json"), str(d / "tool_context.json"),
        live_entities_path=str(d / "entities.json"),
        live_entity_context_path=str(d / "entity_context.json"),
        live_manifest_path=str(d / "manifest.json"),
        out_dir=str(tmp_path / "out"), publish=False,
    )
    assert out["published"] is False
    keys = {r["key"].rsplit("/", 1)[-1] for r in out["report"]}
    assert {"tools.json", "families.json", "faculty.json", "tool_context.json",
            "entities.json", "entity_context.json", "manifest.json"} <= keys
    assert all(r["uploaded"] is False for r in out["report"])  # dry-run: nothing written
    # the live fragment is repaired by the re-projection
    assert out["before"]["start_lowercase_pct"] == 100.0
    assert out["after"]["start_lowercase_pct"] == 0.0


def test_republish_aborts_if_a_frozen_sibling_would_change(tmp_path):
    d = _write_live(tmp_path, tool_context=ALIGNED_TC, entity_context=STALE_ECTX)
    # Corrupt the live tools.json sha so the freeze guard sees drift on a frozen sibling.
    manifest = json.loads((d / "manifest.json").read_text())
    manifest["objects"]["tools.json"]["sha256"] = "deadbeef"
    (d / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(SystemExit):
        republish_entity_context(
            str(d / "tools.json"), str(d / "tool_context.json"),
            live_entities_path=str(d / "entities.json"),
            live_manifest_path=str(d / "manifest.json"),
            out_dir=None, publish=False,
        )


def test_entity_context_json_is_not_frozen():
    # The whole point: entity_context.json must be allowed to change.
    assert "entity_context.json" not in FROZEN_ARTIFACTS
    assert "tool_context.json" in FROZEN_ARTIFACTS

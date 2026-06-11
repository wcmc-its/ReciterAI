"""Tests for the additive `durable_id` companion on hierarchy.json (#191 brick D3).

Covers the three contract points for the hierarchy.json surface:

1. Gate OFF ⇒ no `durable_id` key anywhere (byte-identical to today).
2. Gate ON + slug present in the durable_map ⇒ `durable_id` stamped with the
   mapped value, alongside the untouched slug `id`.
3. Gate ON + slug NOT in the map ⇒ `durable_id` OMITTED (key absent, not None).

Plus a unit test for the pure snapshot→{slug: durable} inversion in publish.py
and a schema test that a SubtopicDef carrying `durable_id` validates.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from pipeline_hierarchy.bundler import bundle
from pipeline_hierarchy.generator import SCHEMA_PATH
from pipeline_hierarchy.publish import _invert_snapshot_to_durable_map

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------- fixture helpers (mirror tests/test_hierarchy_bundler.py) ----------


def _write_augmented(dir_path: Path, topic_id: str, subtopics: list[dict]) -> None:
    payload = {
        "topic_id": topic_id,
        "topic_label": topic_id.replace("_", " ").title(),
        "subtopics": subtopics,
    }
    (dir_path / f"hierarchy_augmented_{topic_id}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _minimal_sub(sid: str) -> dict:
    return {
        "id": sid,
        "label": f"Label for {sid}",
        "description": f"Description for {sid}.",
        "display_name": f"{sid.title()} UI",
        "short_description": f"Tagline for {sid}.",
        "total_weight": 12.5,
        "activity_count": 7,
    }


def _make_taxonomy(tmp_path: Path) -> Path:
    p = tmp_path / "taxonomy.json"
    p.write_text(json.dumps({"taxonomy_version": "taxonomy_v_test", "topics": []}))
    return p


def _make_excluded(tmp_path: Path) -> Path:
    p = tmp_path / "excluded.json"
    p.write_text(json.dumps({"excluded_topics": []}))
    return p


def _bundle(tmp_path: Path, **kwargs) -> dict:
    aug_dir = tmp_path / "aug"
    aug_dir.mkdir()
    _write_augmented(
        aug_dir, "topic_a", [_minimal_sub("topic_a_one"), _minimal_sub("topic_a_two")]
    )
    return bundle(
        augmented_dir=aug_dir,
        taxonomy_path=_make_taxonomy(tmp_path),
        excluded_topics_path=_make_excluded(tmp_path),
        **kwargs,
    )


# ---------- gate-off byte-stability (the load-bearing assertion) ----------


def test_gate_off_omits_durable_id_entirely(tmp_path):
    """Gate OFF (default) ⇒ NO subtopic carries a `durable_id` key, even when a
    durable_map is supplied. This is the byte-identical-to-today guarantee."""
    result = _bundle(
        tmp_path,
        durable_map={"topic_a_one": "ST-0001", "topic_a_two": "ST-0002"},
        gate_on=False,
    )
    for sub in result["topics"]["topic_a"]["subtopics"]:
        assert "durable_id" not in sub


def test_default_call_has_no_durable_id(tmp_path):
    """No durable kwargs at all (existing callers) ⇒ byte-identical output."""
    result = _bundle(tmp_path)
    for sub in result["topics"]["topic_a"]["subtopics"]:
        assert "durable_id" not in sub
        # The slug `id` is untouched (additive contract).
        assert sub["id"] in {"topic_a_one", "topic_a_two"}


# ---------- gate-on, slug resolves ----------


def test_gate_on_stamps_durable_id_for_resolved_slug(tmp_path):
    """Gate ON + slug in the map ⇒ `durable_id` present with the mapped value,
    and the slug `id` stays exactly as before."""
    result = _bundle(
        tmp_path,
        durable_map={"topic_a_one": "ST-0001", "topic_a_two": "ST-0002"},
        gate_on=True,
    )
    by_id = {s["id"]: s for s in result["topics"]["topic_a"]["subtopics"]}
    assert by_id["topic_a_one"]["durable_id"] == "ST-0001"
    assert by_id["topic_a_two"]["durable_id"] == "ST-0002"
    # Additive: slug id unchanged.
    assert by_id["topic_a_one"]["id"] == "topic_a_one"


# ---------- gate-on, slug NOT in map (one-run lag) ----------


def test_gate_on_omits_durable_id_for_unresolved_slug(tmp_path):
    """Gate ON but a slug absent from the map ⇒ `durable_id` OMITTED (key absent,
    NOT None). Only the resolved slug gets the companion."""
    result = _bundle(
        tmp_path,
        durable_map={"topic_a_one": "ST-0001"},  # topic_a_two absent
        gate_on=True,
    )
    by_id = {s["id"]: s for s in result["topics"]["topic_a"]["subtopics"]}
    assert by_id["topic_a_one"]["durable_id"] == "ST-0001"
    assert "durable_id" not in by_id["topic_a_two"]


def test_gate_on_empty_map_omits_everywhere(tmp_path):
    """Gate ON with an empty map (store empty / one-run lag) ⇒ no companion at all."""
    result = _bundle(tmp_path, durable_map={}, gate_on=True)
    for sub in result["topics"]["topic_a"]["subtopics"]:
        assert "durable_id" not in sub


# ---------- pure snapshot inversion helper (AWS-free) ----------


def test_invert_snapshot_drops_rows_without_slug_id():
    """A snapshot with two durable rows — one with slug_id, one with slug_id=None —
    inverts to only the slug_id-bearing entry (#191)."""
    snapshot = {
        "ST-0001": {"slug_id": "topic_a_one", "topic_id": "topic_a"},
        "ST-0002": {"slug_id": None, "topic_id": "topic_a"},
    }
    assert _invert_snapshot_to_durable_map(snapshot) == {"topic_a_one": "ST-0001"}


def test_invert_empty_snapshot_is_empty():
    assert _invert_snapshot_to_durable_map({}) == {}


# ---------- schema: durable_id is accepted, additive ----------


def _minimal_subtopic_def(with_durable: bool) -> dict:
    sub = {
        "id": "topic_a_one",
        "label": "Label",
        "description": "Description.",
        "display_name": "Display",
        "short_description": "Tagline.",
        "activity_count": 7,
        "total_weight": 12.5,
    }
    if with_durable:
        sub["durable_id"] = "ST-0001"
    return sub


def _subtopic_def_schema() -> dict:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    # Validate against the SubtopicDef $def directly, carrying $defs for any $ref.
    return {"$defs": schema["$defs"], "$ref": "#/$defs/SubtopicDef"}


def test_subtopic_def_with_durable_id_validates():
    """A SubtopicDef carrying `durable_id` passes the published schema (additive)."""
    jsonschema.validate(
        instance=_minimal_subtopic_def(with_durable=True),
        schema=_subtopic_def_schema(),
    )


def test_subtopic_def_without_durable_id_still_validates():
    """The companion is optional — a SubtopicDef without it is still valid."""
    jsonschema.validate(
        instance=_minimal_subtopic_def(with_durable=False),
        schema=_subtopic_def_schema(),
    )

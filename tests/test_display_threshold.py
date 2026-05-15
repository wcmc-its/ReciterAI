"""Tests for the per-topic `display_threshold` field (#69).

Covers:
- Bundler applies `display_threshold_default` to topics that lack a per-topic value.
- Bundler honors per-topic override from `taxonomy_v2.json`.
- Bundler rejects out-of-range per-topic values.
- thresholds.schema.json rejects out-of-range `display_threshold_default`.
- hierarchy.schema.json rejects out-of-range `TopicEntry.display_threshold`.
- The live `taxonomy_v2.json` carries `display_threshold` on every topic.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from pipeline_hierarchy.bundler import bundle

REPO_ROOT = Path(__file__).resolve().parents[1]
TAXONOMY_PATH = REPO_ROOT / "taxonomy_v2.json"
THRESHOLDS_PATH = REPO_ROOT / "config/thresholds.json"
THRESHOLDS_SCHEMA_PATH = REPO_ROOT / "config/thresholds.schema.json"
HIERARCHY_SCHEMA_PATH = REPO_ROOT / "docs/hierarchy.schema.json"


# ---------- fixture helpers ----------

def _write_augmented(dir_path: Path, topic_id: str) -> None:
    payload = {
        "topic_id": topic_id,
        "topic_label": topic_id.replace("_", " ").title(),
        "subtopics": [
            {
                "id": f"{topic_id}_one",
                "label": "Sub One",
                "description": "Sub one description.",
                "display_name": "Sub One",
                "short_description": "Sub one tagline.",
                "activity_count": 7,
                "total_weight": 12.5,
            }
        ],
    }
    (dir_path / f"hierarchy_augmented_{topic_id}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _write_taxonomy(tmp_path: Path, topics: list[dict]) -> Path:
    p = tmp_path / "taxonomy.json"
    p.write_text(json.dumps({"taxonomy_version": "taxonomy_v_test", "topics": topics}))
    return p


def _write_excluded(tmp_path: Path) -> Path:
    p = tmp_path / "excluded.json"
    p.write_text(json.dumps({"excluded_topics": []}))
    return p


def _write_thresholds(tmp_path: Path, default: float = 0.5) -> Path:
    p = tmp_path / "thresholds.json"
    p.write_text(json.dumps({"display_threshold_default": default}))
    return p


# ---------- bundler behavior ----------

def test_bundler_applies_global_default_when_topic_lacks_override(tmp_path):
    """A topic with no `display_threshold` in taxonomy inherits the global default."""
    aug_dir = tmp_path / "aug"
    aug_dir.mkdir()
    _write_augmented(aug_dir, "topic_a")

    taxonomy_path = _write_taxonomy(
        tmp_path,
        topics=[{"id": "topic_a", "label": "Topic A", "description": "..."}],
    )
    thresholds_path = _write_thresholds(tmp_path, default=0.55)

    result = bundle(
        augmented_dir=aug_dir,
        taxonomy_path=taxonomy_path,
        excluded_topics_path=_write_excluded(tmp_path),
        thresholds_path=thresholds_path,
    )
    assert result["topics"]["topic_a"]["display_threshold"] == 0.55


def test_bundler_honors_per_topic_override(tmp_path):
    """A topic with an explicit `display_threshold` keeps its value, not the default."""
    aug_dir = tmp_path / "aug"
    aug_dir.mkdir()
    _write_augmented(aug_dir, "topic_a")
    _write_augmented(aug_dir, "topic_b")

    taxonomy_path = _write_taxonomy(
        tmp_path,
        topics=[
            {"id": "topic_a", "label": "A", "description": "...", "display_threshold": 0.7},
            {"id": "topic_b", "label": "B", "description": "..."},
        ],
    )
    thresholds_path = _write_thresholds(tmp_path, default=0.5)

    result = bundle(
        augmented_dir=aug_dir,
        taxonomy_path=taxonomy_path,
        excluded_topics_path=_write_excluded(tmp_path),
        thresholds_path=thresholds_path,
    )
    assert result["topics"]["topic_a"]["display_threshold"] == 0.7
    assert result["topics"]["topic_b"]["display_threshold"] == 0.5


def test_bundler_rejects_out_of_range_per_topic_value(tmp_path):
    """`display_threshold` outside [0, 1] on a topic must raise."""
    aug_dir = tmp_path / "aug"
    aug_dir.mkdir()
    _write_augmented(aug_dir, "topic_a")
    taxonomy_path = _write_taxonomy(
        tmp_path,
        topics=[{"id": "topic_a", "label": "A", "description": "...", "display_threshold": 1.5}],
    )
    with pytest.raises(ValueError, match="display_threshold"):
        bundle(
            augmented_dir=aug_dir,
            taxonomy_path=taxonomy_path,
            excluded_topics_path=_write_excluded(tmp_path),
            thresholds_path=_write_thresholds(tmp_path),
        )


def test_bundler_rejects_out_of_range_default(tmp_path):
    """`display_threshold_default` outside [0, 1] must raise."""
    aug_dir = tmp_path / "aug"
    aug_dir.mkdir()
    _write_augmented(aug_dir, "topic_a")
    taxonomy_path = _write_taxonomy(tmp_path, topics=[])
    with pytest.raises(ValueError, match="display_threshold_default"):
        bundle(
            augmented_dir=aug_dir,
            taxonomy_path=taxonomy_path,
            excluded_topics_path=_write_excluded(tmp_path),
            thresholds_path=_write_thresholds(tmp_path, default=-0.1),
        )


# ---------- schema validation ----------

def test_thresholds_schema_rejects_out_of_range_default():
    """thresholds.schema.json must reject `display_threshold_default` outside [0, 1]."""
    cfg = json.loads(THRESHOLDS_PATH.read_text(encoding="utf-8"))
    schema = json.loads(THRESHOLDS_SCHEMA_PATH.read_text(encoding="utf-8"))
    mutated = dict(cfg)
    mutated["display_threshold_default"] = 1.5
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=mutated, schema=schema)


def test_thresholds_config_carries_display_threshold_default():
    """The live config/thresholds.json defines `display_threshold_default`."""
    cfg = json.loads(THRESHOLDS_PATH.read_text(encoding="utf-8"))
    assert "display_threshold_default" in cfg
    v = cfg["display_threshold_default"]
    assert isinstance(v, (int, float))
    assert 0.0 <= float(v) <= 1.0


def test_hierarchy_schema_rejects_out_of_range_topic_display_threshold():
    """docs/hierarchy.schema.json must reject TopicEntry.display_threshold outside [0, 1]."""
    schema = json.loads(HIERARCHY_SCHEMA_PATH.read_text(encoding="utf-8"))
    instance = {
        "version": "subtopic_v1",
        "taxonomy_version": "taxonomy_v2",
        "excluded_topics": [],
        "topics": {
            "topic_a": {
                "subtopics": [],
                "display_threshold": 1.5,
            }
        },
        "see_also": [],
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=instance, schema=schema)


def test_hierarchy_schema_accepts_well_formed_topic_display_threshold():
    """docs/hierarchy.schema.json accepts an in-range TopicEntry.display_threshold."""
    schema = json.loads(HIERARCHY_SCHEMA_PATH.read_text(encoding="utf-8"))
    instance = {
        "version": "subtopic_v1",
        "taxonomy_version": "taxonomy_v2",
        "excluded_topics": [],
        "topics": {
            "topic_a": {
                "subtopics": [],
                "display_threshold": 0.5,
            }
        },
        "see_also": [],
    }
    jsonschema.validate(instance=instance, schema=schema)


# ---------- live taxonomy ----------

def test_every_taxonomy_topic_carries_display_threshold():
    """Every topic in taxonomy_v2.json carries a `display_threshold` in [0, 1]."""
    data = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
    topics = data["topics"]
    assert len(topics) > 0
    for t in topics:
        assert "display_threshold" in t, f"topic {t.get('id')!r} missing display_threshold"
        v = float(t["display_threshold"])
        assert 0.0 <= v <= 1.0, f"topic {t.get('id')!r} display_threshold={v} out of range"

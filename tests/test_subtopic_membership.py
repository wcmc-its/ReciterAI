"""
Tests for the per-subtopic membership sidecar (#191).

`build_membership` recovers the discovery `seed_pmids` that `bundle()` strips out
of hierarchy.json, into a co-located `membership.json` artifact that the durable-ID
reconcile stage and the taxonomy-jitter measurement both require. The publisher
writes it locally and uploads it alongside the hierarchy artifact.

Coverage:
- membership is keyed by subtopic_id and 1:1 with the bundled hierarchy;
- seed_pmids are int-coerced, de-duplicated, and sorted (determinism);
- the artifact is byte-stable across reruns (sort_keys serialization);
- absent/empty seeds map to [], and conflicting seeds across files fail loud;
- the publisher's write_local / upload_to_s3 carry the sidecar correctly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline_hierarchy.bundler import (
    MEMBERSHIP_ARTIFACT_VERSION,
    MEMBERSHIP_KIND,
    build_membership,
    bundle,
)
from pipeline_hierarchy.publish import upload_to_s3, write_local


# ---------- fixture helpers (mirror test_hierarchy_bundler) ----------

def _write_augmented(dir_path: Path, topic_id: str, subtopics: list[dict]) -> None:
    payload = {"topic_id": topic_id, "topic_label": topic_id, "subtopics": subtopics}
    (dir_path / f"hierarchy_augmented_{topic_id}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _sub(sid: str, seed_pmids: list | None = None) -> dict:
    s = {
        "id": sid,
        "label": f"Label for {sid}",
        "description": f"Description for {sid}.",
        "display_name": f"{sid.title()} UI",
        "short_description": f"Tagline for {sid}.",
        "total_weight": 1.0,
        "activity_count": 1,
    }
    if seed_pmids is not None:
        s["seed_pmids"] = seed_pmids
    return s


def _make_taxonomy(tmp_path: Path) -> Path:
    p = tmp_path / "taxonomy.json"
    p.write_text(json.dumps({"taxonomy_version": "taxonomy_v_test", "topics": []}))
    return p


def _make_excluded(tmp_path: Path) -> Path:
    p = tmp_path / "excluded.json"
    p.write_text(json.dumps({"excluded_topics": []}))
    return p


def _bundle(aug_dir: Path, tmp_path: Path) -> dict:
    return bundle(
        augmented_dir=aug_dir,
        taxonomy_path=_make_taxonomy(tmp_path),
        excluded_topics_path=_make_excluded(tmp_path),
    )


# ---------- build_membership ----------

def test_membership_is_keyed_by_subtopic_id_with_topic_and_seeds(tmp_path):
    aug = tmp_path / "aug"
    aug.mkdir()
    _write_augmented(aug, "topic_a", [_sub("topic_a_one", [123, 456]), _sub("topic_a_two", [789])])
    _write_augmented(aug, "topic_b", [_sub("topic_b_one", [42])])
    h = _bundle(aug, tmp_path)

    m = build_membership(h, hierarchy_version="v2026-06-10", augmented_dir=aug)

    assert m["version"] == MEMBERSHIP_ARTIFACT_VERSION
    assert m["membership_kind"] == MEMBERSHIP_KIND
    assert m["taxonomy_version"] == "taxonomy_v_test"
    assert m["hierarchy_version"] == "v2026-06-10"
    assert m["subtopic_count"] == 3
    assert m["subtopics"]["topic_a_one"] == {"topic_id": "topic_a", "seed_pmids": [123, 456]}
    assert m["subtopics"]["topic_a_two"] == {"topic_id": "topic_a", "seed_pmids": [789]}
    assert m["subtopics"]["topic_b_one"] == {"topic_id": "topic_b", "seed_pmids": [42]}


def test_membership_id_set_is_1to1_with_bundled_hierarchy(tmp_path):
    aug = tmp_path / "aug"
    aug.mkdir()
    _write_augmented(aug, "topic_a", [_sub("topic_a_one", [1]), _sub("topic_a_two", [2])])
    _write_augmented(aug, "topic_b", [_sub("topic_b_one", [3])])
    h = _bundle(aug, tmp_path)

    m = build_membership(h, hierarchy_version="v1", augmented_dir=aug)

    bundled_ids = {
        s["id"] for t in h["topics"].values() for s in t["subtopics"]
    }
    assert set(m["subtopics"]) == bundled_ids


def test_seed_pmids_are_int_coerced_deduped_and_sorted(tmp_path):
    aug = tmp_path / "aug"
    aug.mkdir()
    # mixed str/int, duplicates, unsorted
    _write_augmented(aug, "topic_a", [_sub("topic_a_one", ["30", 10, 10, "20"])])
    h = _bundle(aug, tmp_path)

    m = build_membership(h, hierarchy_version="v1", augmented_dir=aug)
    assert m["subtopics"]["topic_a_one"]["seed_pmids"] == [10, 20, 30]


def test_absent_or_empty_seeds_map_to_empty_list(tmp_path):
    aug = tmp_path / "aug"
    aug.mkdir()
    _write_augmented(aug, "topic_a", [_sub("topic_a_one", None), _sub("topic_a_two", [])])
    h = _bundle(aug, tmp_path)

    m = build_membership(h, hierarchy_version="v1", augmented_dir=aug)
    assert m["subtopics"]["topic_a_one"]["seed_pmids"] == []
    assert m["subtopics"]["topic_a_two"]["seed_pmids"] == []


def test_conflicting_seeds_for_same_id_fail_loud(tmp_path):
    aug = tmp_path / "aug"
    aug.mkdir()
    # same subtopic id in two files with different seeds → ambiguous, must raise
    _write_augmented(aug, "topic_a", [_sub("dup_id", [1, 2])])
    _write_augmented(aug, "topic_b", [_sub("dup_id", [3, 4])])

    with pytest.raises(ValueError, match="conflicting seed_pmids"):
        build_membership({"taxonomy_version": "t", "topics": {}}, hierarchy_version="v1", augmented_dir=aug)


def test_membership_serialization_is_byte_stable_across_reruns(tmp_path):
    aug = tmp_path / "aug"
    aug.mkdir()
    _write_augmented(aug, "topic_a", [_sub("topic_a_one", [3, 1, 2])])
    _write_augmented(aug, "topic_b", [_sub("topic_b_one", [9])])
    h = _bundle(aug, tmp_path)

    def serialize():
        m = build_membership(h, hierarchy_version="v1", augmented_dir=aug)
        return json.dumps(m, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")

    assert serialize() == serialize()


# ---------- publisher wiring ----------

class _FakeS3:
    def __init__(self):
        self.calls: list[tuple[str, bytes]] = []

    def put_object(self, key: str, body: bytes, cache_control: str | None = None) -> None:
        self.calls.append((key, body))


def test_upload_to_s3_puts_membership_first_when_provided():
    s3 = _FakeS3()
    upload_to_s3(
        "v2026-06-10", b"h", b"s", {"version": "v2026-06-10"}, b"d",
        s3_client=s3, membership_bytes=b"m",
    )
    keys = [k for k, _ in s3.calls]
    assert keys[0] == "v2026-06-10/membership.json"
    assert "v2026-06-10/hierarchy.json" in keys
    assert "latest/manifest.json" in keys


def test_upload_to_s3_omits_membership_when_none():
    s3 = _FakeS3()
    upload_to_s3("v2026-06-10", b"h", b"s", {"version": "v2026-06-10"}, b"d", s3_client=s3)
    keys = [k for k, _ in s3.calls]
    assert all("membership.json" not in k for k in keys)
    # the canonical 5 keys are still present
    assert "v2026-06-10/hierarchy.json" in keys
    assert "latest/manifest.json" in keys


def test_write_local_writes_membership_when_provided(tmp_path):
    write_local(tmp_path, b"hier", b"sch", {"version": "v1"}, membership=b"members")
    assert (tmp_path / "membership.json").read_bytes() == b"members"
    assert (tmp_path / "hierarchy.json").read_bytes() == b"hier"


def test_write_local_omits_membership_when_none(tmp_path):
    write_local(tmp_path, b"hier", b"sch", {"version": "v1"})
    assert not (tmp_path / "membership.json").exists()
    assert (tmp_path / "hierarchy.json").read_bytes() == b"hier"

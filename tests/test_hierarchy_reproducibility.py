"""Phase 11 D-14 / G-36 prerequisite: reproducibility tests.

hierarchy.json is bit-stable across content-identical reruns because
generated_at is no longer embedded (D-14). Two-pass bundle + build must
produce byte-identical json.dumps output with the same inputs.

Phase 12 G-36 extends coverage to the publish() path: test_publish_*
tests verify that the bytes passed to S3 for hierarchy.json are
bit-stable across two runs, even when the manifest timestamp differs.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from pipeline_hierarchy import publish as _publish_module
from pipeline_hierarchy.bundler import (
    DEFAULT_AUGMENTED_DIR,
    bundle,
)
from pipeline_hierarchy.generator import (
    SOURCE_HIERARCHY,
    build_hierarchy,
    generate,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------- fixture helpers (shared with test_hierarchy_bundler) ----------


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
        "total_weight": 12.5,
        "activity_count": 7,
        "display_name": f"{sid.title()} UI",
        "short_description": f"Tagline for {sid}.",
    }


def _make_taxonomy(tmp_path: Path) -> Path:
    p = tmp_path / "taxonomy.json"
    p.write_text(json.dumps({"taxonomy_version": "taxonomy_v_test", "topics": []}))
    return p


def _make_excluded(tmp_path: Path) -> Path:
    p = tmp_path / "excluded.json"
    p.write_text(json.dumps({"excluded_topics": []}))
    return p


# ---------- reproducibility tests ----------


def test_bundle_is_byte_stable_across_two_passes(tmp_path):
    """D-14 / G-36: Two calls to bundle() with same inputs → byte-identical
    json.dumps output. Since generated_at is removed from the hierarchy dict,
    no timestamp drift can differentiate two passes."""
    aug_dir = tmp_path / "aug"
    aug_dir.mkdir()
    _write_augmented(aug_dir, "topic_a", [_minimal_sub("topic_a_one")])
    _write_augmented(aug_dir, "topic_b", [_minimal_sub("topic_b_one")])

    kwargs = dict(
        augmented_dir=aug_dir,
        taxonomy_path=_make_taxonomy(tmp_path),
        excluded_topics_path=_make_excluded(tmp_path),
    )

    result1 = bundle(**kwargs)
    result2 = bundle(**kwargs)

    serialized1 = json.dumps(result1, sort_keys=True)
    serialized2 = json.dumps(result2, sort_keys=True)
    assert serialized1 == serialized2, (
        "D-14: bundle() output must be byte-stable across two passes with same inputs"
    )


def test_build_hierarchy_is_byte_stable_across_two_passes(tmp_path):
    """D-14 / G-36: Two calls to build_hierarchy() with the same in-memory hierarchy
    produce byte-identical json.dumps output."""
    aug_dir = tmp_path / "aug"
    aug_dir.mkdir()
    _write_augmented(aug_dir, "topic_a", [_minimal_sub("topic_a_one")])
    _write_augmented(aug_dir, "topic_b", [_minimal_sub("topic_b_one")])

    bundled = bundle(
        augmented_dir=aug_dir,
        taxonomy_path=_make_taxonomy(tmp_path),
        excluded_topics_path=_make_excluded(tmp_path),
    )

    # Pass a fixed generated_at to get deterministic manifest for both passes.
    h1 = build_hierarchy(hierarchy=bundled, generated_at="2026-06-01T00:00:00Z")
    h2 = build_hierarchy(hierarchy=bundled, generated_at="2026-06-01T00:00:00Z")

    s1 = json.dumps(h1, sort_keys=True)
    s2 = json.dumps(h2, sort_keys=True)
    assert s1 == s2, (
        "D-14: build_hierarchy() must produce byte-identical output across passes with same inputs"
    )


def test_generate_is_byte_stable_with_pinned_generated_at():
    """D-14 / G-36: generate() with the same generated_at produces byte-identical
    hierarchy bytes. Uses the live source hierarchy."""
    pinned = "2026-06-01T00:00:00Z"

    h_bytes1, _, manifest1 = generate(generated_at=pinned, version="v2026-06-01")
    h_bytes2, _, manifest2 = generate(generated_at=pinned, version="v2026-06-01")

    assert h_bytes1 == h_bytes2, (
        "generate() must produce byte-identical hierarchy bytes with the same generated_at"
    )
    assert manifest1["sha256"] == manifest2["sha256"], (
        "sha256 must match across two passes with the same generated_at"
    )


def test_hierarchy_dict_has_no_generated_at_after_build(tmp_path):
    """D-14: build_hierarchy() must not embed generated_at into the hierarchy dict
    even when a generated_at kwarg is passed for manifest derivation."""
    aug_dir = tmp_path / "aug"
    aug_dir.mkdir()
    _write_augmented(aug_dir, "topic_a", [_minimal_sub("topic_a_one")])

    bundled = bundle(
        augmented_dir=aug_dir,
        taxonomy_path=_make_taxonomy(tmp_path),
        excluded_topics_path=_make_excluded(tmp_path),
    )
    h = build_hierarchy(hierarchy=bundled, generated_at="2026-06-01T00:00:00Z")
    assert "generated_at" not in h, (
        "D-14: hierarchy dict must not contain generated_at after build_hierarchy()"
    )


# ---------- Phase 12 G-36: publish() end-to-end reproducibility ----------


class _S3PutCapture:
    """Minimal S3HierarchyClient stub that captures put_object calls.

    Only the hierarchy-layer surface is needed; all other S3 methods
    (get_object_bytes, key_exists) raise to surface unexpected calls.
    """

    def __init__(self):
        self.calls: list[dict] = []  # {"key": str, "body": bytes, ...}

    def put_object(self, key: str, body: bytes, content_type: str = "application/json",
                   cache_control: str | None = None) -> None:
        self.calls.append({"key": key, "body": body, "cache_control": cache_control})

    def get_object_bytes(self, key: str) -> bytes:
        # Simulate no previous manifest (first publish, O-01 path).
        from botocore.exceptions import ClientError
        raise ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "Not Found"}}, "GetObject"
        )

    def key_exists(self, key: str) -> bool:
        return False


def _make_publish_fixture(tmp_path: Path) -> dict:
    """Build a minimal bundled hierarchy dict using the existing fixture helpers.

    Reuses _write_augmented / _minimal_sub / _make_taxonomy / _make_excluded
    so the fixture is consistent with the rest of this file.
    """
    aug_dir = tmp_path / "aug"
    aug_dir.mkdir()
    _write_augmented(aug_dir, "topic_a", [
        _minimal_sub("topic_a_alpha"),
        _minimal_sub("topic_a_beta"),
    ])
    _write_augmented(aug_dir, "topic_b", [_minimal_sub("topic_b_one")])
    return bundle(
        augmented_dir=aug_dir,
        taxonomy_path=_make_taxonomy(tmp_path),
        excluded_topics_path=_make_excluded(tmp_path),
    )


def _run_publish_with_pinned_time(bundled_dict: dict, generated_at_iso: str) -> _S3PutCapture:
    """Run publish.main() end-to-end using the REAL generate() path.

    Stubs:
      - publish.bundle → returns bundled_dict (pre-built fixture)
      - pipeline_hierarchy.generator.datetime → pinned so generate()'s
        internal datetime.now() returns generated_at_iso, making the manifest
        timestamp deterministic without touching the hierarchy body
      - publish.get_table → MagicMock DynamoDB (no prior complete row → no skip)
      - publish.write_local → no-op (avoids filesystem side-effect)
      - publish.S3HierarchyClient → _S3PutCapture instance

    The real generate() runs, so hierarchy_bytes are the true artifact bytes.
    Returns the _S3PutCapture that recorded all put_object calls.
    """
    from datetime import datetime, timezone

    # Parse generated_at_iso into a fixed datetime object.
    fixed_dt = datetime.fromisoformat(generated_at_iso.replace("Z", "+00:00"))

    # Build a datetime mock that returns fixed_dt from .now() but otherwise
    # delegates to the real datetime class (e.g. fromisoformat, strptime).
    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_dt.replace(tzinfo=tz) if tz else fixed_dt

    s3_stub = _S3PutCapture()
    table_mock = MagicMock()
    table_mock.query.return_value = {"Items": []}  # no prior row → no skip

    with (
        patch.object(_publish_module, "bundle", return_value=bundled_dict),
        patch.object(_publish_module, "get_table", return_value=table_mock),
        patch.object(_publish_module, "write_local", MagicMock()),
        patch.object(_publish_module, "S3HierarchyClient", return_value=s3_stub),
        patch("pipeline_hierarchy.generator.datetime", _FixedDatetime),
    ):
        rc = _publish_module.main(["--version", f"vtest-{generated_at_iso[:10]}"])

    assert rc == _publish_module.EXIT_OK, (
        f"publish.main() returned {rc!r}; expected EXIT_OK"
    )
    return s3_stub


def _extract_put_body(s3_stub: _S3PutCapture, key_suffix: str) -> bytes:
    """Return the body bytes from the first put_object call whose key ends with key_suffix."""
    for c in s3_stub.calls:
        if c["key"].endswith(key_suffix):
            return c["body"]
    keys = [c["key"] for c in s3_stub.calls]
    raise AssertionError(
        f"No put_object call with key ending '{key_suffix}' found. "
        f"Recorded keys: {keys}"
    )


def _recursive_has_key(obj, target_key: str) -> bool:
    """Return True if target_key appears anywhere in a nested dict/list structure."""
    if isinstance(obj, dict):
        if target_key in obj:
            return True
        return any(_recursive_has_key(v, target_key) for v in obj.values())
    if isinstance(obj, list):
        return any(_recursive_has_key(item, target_key) for item in obj)
    return False


def test_publish_byte_identical_across_reruns(tmp_path):
    """G-36 (Phase 12): publish() emits a hierarchy.json that is bit-stable
    across content-identical reruns, even when the manifest timestamp differs.

    Phase 11 D-14/G-29 removed generated_at from hierarchy.json so this
    invariant could hold. This test catches regressions where a future
    change re-introduces a timestamp via the publish path even though
    generate already keeps it out.

    Run 1 uses timestamp "2026-06-01T00:00:00Z"; Run 2 uses a deliberately
    DIFFERENT timestamp "2026-07-01T12:00:00Z". The hierarchy.json bytes
    must be identical (proving D-14 robustness across two distinct wall-clock
    moments); the manifest bytes are intentionally allowed to differ.
    """
    bundled = _make_publish_fixture(tmp_path)

    run1 = _run_publish_with_pinned_time(bundled, "2026-06-01T00:00:00Z")
    run2 = _run_publish_with_pinned_time(bundled, "2026-07-01T12:00:00Z")

    h_bytes_run1 = _extract_put_body(run1, "hierarchy.json")
    h_bytes_run2 = _extract_put_body(run2, "hierarchy.json")

    assert h_bytes_run1 == h_bytes_run2, (
        "G-36 / D-14: publish() must emit byte-identical hierarchy.json across "
        "content-identical reruns regardless of wall-clock timestamp. "
        f"Run 1 len={len(h_bytes_run1)}, Run 2 len={len(h_bytes_run2)}. "
        "Check that generated_at was not re-introduced into the hierarchy body."
    )


def test_publish_hierarchy_has_no_generated_at(tmp_path):
    """G-36 (Phase 12) + G-29 (Phase 11): the hierarchy.json bytes emitted
    by publish() carry no generated_at field anywhere in the tree, including
    nested topic/subtopic dicts.

    A recursive walk checks every dict at every depth so that adding a
    generated_at stamp inside a topic or subtopic also trips this test.
    """
    bundled = _make_publish_fixture(tmp_path)
    s3_stub = _run_publish_with_pinned_time(bundled, "2026-06-01T00:00:00Z")

    h_bytes = _extract_put_body(s3_stub, "hierarchy.json")
    parsed = json.loads(h_bytes)

    assert not _recursive_has_key(parsed, "generated_at"), (
        "G-36 / G-29 / D-14: hierarchy.json emitted by publish() must not contain "
        "a 'generated_at' key at any level of the hierarchy tree. "
        "The generated_at timestamp belongs exclusively in manifest.json (D-14). "
        f"Top-level keys present: {list(parsed.keys())}"
    )


def test_publish_manifest_has_generated_at(tmp_path):
    """Boundary test (Phase 12 G-36): manifest.json IS allowed to carry
    generated_at even though hierarchy.json must not.

    Documents the line between the two artifacts per Phase 11 D-14:
    generated_at moves OUT of hierarchy and INTO manifest — both sides
    of that boundary are asserted here so neither side can regress silently.
    """
    bundled = _make_publish_fixture(tmp_path)
    s3_stub = _run_publish_with_pinned_time(bundled, "2026-06-01T00:00:00Z")

    # Find the versioned manifest (step 4 in the D-11 5-step upload order).
    manifest_bytes = _extract_put_body(s3_stub, "manifest.json")
    manifest = json.loads(manifest_bytes)

    assert "generated_at" in manifest, (
        "D-14 / G-36: manifest.json must carry generated_at. "
        "The timestamp was intentionally moved from hierarchy.json to manifest.json "
        "in Phase 11 D-14. If this assertion fails, manifest stamping regressed. "
        f"manifest keys present: {list(manifest.keys())}"
    )

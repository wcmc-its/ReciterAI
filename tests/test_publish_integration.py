"""Tests for the Phase 9 substrate integration in pipeline_hierarchy.publish.

Mocks DynamoDB (Table) and S3 — no network. End-to-end exercise of the
real DynamoDB is a manual operator step documented in 09-SUMMARY.md.

Phase 11 additions (Tasks 5-8):
- Write order: exact 5-step put_object sequence (D-11)
- Cache-Control on latest/manifest.json only (D-11)
- STAGE#g29_cutover write under --g29-cutover flag (D-16)
- run_id threading into STAGE# complete row (D-13)
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pipeline_hierarchy import publish

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------- compute_publish_input_hash ----------


def test_input_hash_is_stable_across_generated_at_changes():
    """G-29 mitigation: hash MUST NOT change just because re-stamping
    `generated_at` flips the canonical bytes."""
    base = {
        "taxonomy_version": "taxonomy_v2",
        "topics": {"a": {"subtopics": []}},
        "excluded_topics": [],
        "see_also": [],
    }
    a = {**base, "generated_at": "2026-05-12T00:00:00Z"}
    b = {**base, "generated_at": "2026-05-13T15:42:01Z"}
    assert publish.compute_publish_input_hash(a) == publish.compute_publish_input_hash(b)


def test_input_hash_changes_on_real_content_change():
    base = {
        "taxonomy_version": "taxonomy_v2",
        "topics": {"a": {"subtopics": []}},
        "excluded_topics": [],
        "see_also": [],
        "generated_at": "2026-05-12T00:00:00Z",
    }
    other = {**base, "topics": {"a": {"subtopics": [{"id": "a_new"}]}}}
    assert publish.compute_publish_input_hash(base) != publish.compute_publish_input_hash(other)


def test_input_hash_includes_bundler_version():
    """Bumping pipeline_hierarchy.__version__ invalidates skip cache."""
    h = {"taxonomy_version": "taxonomy_v2", "topics": {}, "excluded_topics": [], "see_also": []}
    with patch.object(publish, "pipeline_hierarchy") as mod:
        mod.__version__ = "0.1.0"
        a = publish.compute_publish_input_hash(h)
        mod.__version__ = "0.2.0"
        b = publish.compute_publish_input_hash(h)
    assert a != b


# ---------- end-to-end main() with mocked dependencies ----------


def _minimal_bundled_dict() -> dict:
    """Smallest dict that satisfies the live hierarchy.schema.json.

    Required top-level fields per schema: version, generated_at,
    taxonomy_version, excluded_topics, topics, see_also.

    Topic id/label deliberately use a non-stopword (`microbiome`) so the
    parent_prefix gate's stopword filter doesn't accidentally make
    violation injection a no-op (it would for short topic names like 'a').
    """
    return {
        "version": "subtopic_v1",
        "generated_at": "2026-05-12T00:00:00Z",
        "taxonomy_version": "taxonomy_v2",
        "excluded_topics": [],
        "topics": {
            "microbiome_research": {
                "subtopics": [
                    {
                        "id": "microbiome_research_x",
                        "label": "X",
                        "description": "Long-enough description for the schema.",
                        "display_name": "Clean Subtopic Name",
                        "short_description": "x card",
                        "activity_count": 0,
                        "total_weight": 0.0,
                    }
                ]
            }
        },
        "see_also": [],
    }


def _patch_io(
    *,
    bundled: dict | None = None,
    table: MagicMock | None = None,
    upload_to_s3: MagicMock | None = None,
    s3_get_bytes: MagicMock | None = None,
    write_local: MagicMock | None = None,
):
    """Build a stack of patches the integration tests share."""
    bundled = bundled if bundled is not None else _minimal_bundled_dict()
    table = table if table is not None else MagicMock()
    upload_to_s3 = upload_to_s3 if upload_to_s3 is not None else MagicMock()
    write_local = write_local if write_local is not None else MagicMock()

    fake_s3 = MagicMock()
    if s3_get_bytes is not None:
        fake_s3.get_object_bytes.side_effect = s3_get_bytes
    else:
        # Default: return the bundled hierarchy bytes and the real schema bytes
        # so the post-upload schema_roundtrip gate has something valid to
        # validate. Without this, the mock returns MagicMock instances and the
        # gate sees garbage.
        schema_bytes_default = (REPO_ROOT / "docs/hierarchy.schema.json").read_bytes()
        hierarchy_bytes_default = json.dumps(bundled).encode("utf-8")

        def _default_get(key: str) -> bytes:
            if key.endswith("hierarchy.schema.json"):
                return schema_bytes_default
            return hierarchy_bytes_default

        fake_s3.get_object_bytes.side_effect = _default_get

    fake_manifest = {
        "schema_version": "1.0.0",
        "taxonomy_version": "taxonomy_v2",
        "version": "v2026-05-12",
        "generated_at": "2026-05-12T00:00:00Z",
        "sha256": "deadbeef" * 8,
        "artifact_bytes": 1234,
    }

    return (
        patch.object(publish, "bundle", return_value=bundled),
        patch.object(publish, "get_table", return_value=table),
        patch.object(
            publish,
            "generate",
            return_value=(b"hierarchy-bytes", b"schema-bytes", fake_manifest),
        ),
        patch.object(publish, "upload_to_s3", upload_to_s3),
        patch.object(publish, "write_local", write_local),
        patch.object(publish, "S3HierarchyClient", return_value=fake_s3),
        table,
        upload_to_s3,
        write_local,
    )


def _enter(stack):
    for cm in stack:
        cm.start()


def _exit(stack):
    for cm in stack:
        cm.stop()


# ---------- happy path ----------


def test_happy_path_writes_complete_row_and_uploads():
    """No prior row → gates pass → S3 upload → complete row written."""
    (
        p_bundle, p_table, p_generate, p_upload, p_write_local, p_s3,
        table, upload_mock, write_local_mock,
    ) = _patch_io()
    table.query.return_value = {"Items": []}  # no prior row

    cms = [p_bundle, p_table, p_generate, p_upload, p_write_local, p_s3]
    _enter(cms)
    try:
        rc = publish.main([])
    finally:
        _exit(cms)

    assert rc == publish.EXIT_OK
    upload_mock.assert_called_once()
    # The STAGE# write call.
    puts = [c for c in table.put_item.call_args_list]
    assert len(puts) == 1
    item = puts[0].kwargs["Item"]
    assert item["status"] == "complete"
    assert item["PK"] == "STAGE#publish_hierarchy#GLOBAL"
    assert item["output_pointer"] == "s3://wcmc-reciterai-hierarchy/v2026-05-12/"
    # force_reason is absent or None on a clean happy-path run.
    assert item.get("force_reason") is None


def test_happy_path_dry_run_skips_s3_and_stage_write():
    (
        p_bundle, p_table, p_generate, p_upload, p_write_local, p_s3,
        table, upload_mock, write_local_mock,
    ) = _patch_io()

    cms = [p_bundle, p_table, p_generate, p_upload, p_write_local, p_s3]
    _enter(cms)
    try:
        rc = publish.main(["--dry-run"])
    finally:
        _exit(cms)

    assert rc == publish.EXIT_OK
    upload_mock.assert_not_called()
    # No STAGE# writes under dry-run.
    assert table.put_item.call_count == 0
    # Local write still happens.
    write_local_mock.assert_called_once()


# ---------- skip semantics ----------


def test_skip_on_matching_prior_complete_row():
    """A prior complete row with the same input_hash short-circuits."""
    bundled = _minimal_bundled_dict()
    expected_hash = publish.compute_publish_input_hash(bundled)
    prior_complete = {
        "PK": "STAGE#publish_hierarchy#GLOBAL",
        "SK": "RUN#2026-05-10T12:00:00Z",
        "status": "complete",
        "input_hash": expected_hash,
        "output_pointer": "s3://wcmc-reciterai-hierarchy/v2026-05-10/",
        "started_at": "2026-05-10T12:00:00Z",
    }
    (
        p_bundle, p_table, p_generate, p_upload, p_write_local, p_s3,
        table, upload_mock, write_local_mock,
    ) = _patch_io(bundled=bundled)
    table.query.return_value = {"Items": [prior_complete]}

    cms = [p_bundle, p_table, p_generate, p_upload, p_write_local, p_s3]
    _enter(cms)
    try:
        rc = publish.main([])
    finally:
        _exit(cms)

    assert rc == publish.EXIT_OK
    upload_mock.assert_not_called()
    # Skipped row was written.
    puts = [c.kwargs["Item"] for c in table.put_item.call_args_list]
    assert len(puts) == 1
    assert puts[0]["status"] == "skipped"
    assert "input_hash unchanged since" in puts[0]["skip_reason"]
    assert puts[0]["cost_observed_usd"] == Decimal("0")


def test_no_skip_on_different_input_hash():
    """A prior complete row with a different hash MUST NOT short-circuit."""
    bundled = _minimal_bundled_dict()
    prior_complete = {
        "PK": "STAGE#publish_hierarchy#GLOBAL",
        "SK": "RUN#2026-05-10T12:00:00Z",
        "status": "complete",
        "input_hash": "not_the_current_hash",
        "started_at": "2026-05-10T12:00:00Z",
    }
    (
        p_bundle, p_table, p_generate, p_upload, p_write_local, p_s3,
        table, upload_mock, write_local_mock,
    ) = _patch_io(bundled=bundled)
    table.query.return_value = {"Items": [prior_complete]}

    cms = [p_bundle, p_table, p_generate, p_upload, p_write_local, p_s3]
    _enter(cms)
    try:
        rc = publish.main([])
    finally:
        _exit(cms)

    assert rc == publish.EXIT_OK
    upload_mock.assert_called_once()


# ---------- gate blocking ----------


def test_block_severity_gate_failure_writes_failed_row_and_skips_upload():
    """Inject a parent-prefix violation; gate blocks; failed row written."""
    bundled = _minimal_bundled_dict()
    # Violate parent-prefix: subtopic display_name starts with parent topic word.
    bundled["topics"]["microbiome_research"]["subtopics"][0]["display_name"] = (
        "Microbiome & Cancer Immunotherapy"
    )
    # parent_prefix_gate needs topic labels — patch the loader to inject one.
    (
        p_bundle, p_table, p_generate, p_upload, p_write_local, p_s3,
        table, upload_mock, write_local_mock,
    ) = _patch_io(bundled=bundled)
    table.query.return_value = {"Items": []}

    cms = [
        p_bundle, p_table, p_generate, p_upload, p_write_local, p_s3,
        patch(
            "gates.parent_prefix._load_topic_labels",
            return_value={"microbiome_research": "Microbiome Research"},
        ),
    ]
    _enter(cms)
    try:
        rc = publish.main([])
    finally:
        _exit(cms)

    assert rc == publish.EXIT_GATE_BLOCKED
    upload_mock.assert_not_called()
    puts = [c.kwargs["Item"] for c in table.put_item.call_args_list]
    assert len(puts) == 1
    assert puts[0]["status"] == "failed"
    assert puts[0]["error_code"].startswith("GATE_BLOCK_")


def test_force_with_reason_overrides_gate_block():
    """--force --force-reason '...' allows publish despite gate failure;
    force_reason is recorded into the complete row."""
    bundled = _minimal_bundled_dict()
    bundled["topics"]["microbiome_research"]["subtopics"][0]["display_name"] = (
        "Microbiome & Cancer Immunotherapy"
    )
    (
        p_bundle, p_table, p_generate, p_upload, p_write_local, p_s3,
        table, upload_mock, write_local_mock,
    ) = _patch_io(bundled=bundled)
    table.query.return_value = {"Items": []}

    cms = [
        p_bundle, p_table, p_generate, p_upload, p_write_local, p_s3,
        patch(
            "gates.parent_prefix._load_topic_labels",
            return_value={"microbiome_research": "Microbiome Research"},
        ),
    ]
    _enter(cms)
    try:
        rc = publish.main(["--force", "--force-reason", "emergency rollback"])
    finally:
        _exit(cms)

    assert rc == publish.EXIT_OK
    upload_mock.assert_called_once()
    puts = [c.kwargs["Item"] for c in table.put_item.call_args_list]
    complete_rows = [p for p in puts if p["status"] == "complete"]
    assert len(complete_rows) == 1
    assert complete_rows[0]["force_reason"] == "emergency rollback"


def test_force_without_reason_errors_out_immediately():
    """--force REQUIRES --force-reason; missing reason exits non-zero with
    no bundler, no DynamoDB, no S3."""
    (
        p_bundle, p_table, p_generate, p_upload, p_write_local, p_s3,
        table, upload_mock, write_local_mock,
    ) = _patch_io()

    cms = [p_bundle, p_table, p_generate, p_upload, p_write_local, p_s3]
    _enter(cms)
    try:
        rc = publish.main(["--force"])
    finally:
        _exit(cms)

    assert rc == publish.EXIT_FORCE_WITHOUT_REASON
    upload_mock.assert_not_called()
    assert table.put_item.call_count == 0


# ---------- post-upload gate ----------


def test_post_upload_schema_roundtrip_failure_surfaces_but_does_not_fail_run():
    """schema_roundtrip is warn severity. Failure surfaces in stderr but
    the publish still exits 0 and the complete row is written — bytes
    are already live by then."""
    bundled = _minimal_bundled_dict()
    (
        p_bundle, p_table, p_generate, p_upload, p_write_local, p_s3,
        table, upload_mock, write_local_mock,
    ) = _patch_io(
        bundled=bundled,
        s3_get_bytes=FileNotFoundError("simulated 404 on the round-trip fetch"),
    )
    table.query.return_value = {"Items": []}

    cms = [
        p_bundle, p_table, p_generate, p_upload, p_write_local, p_s3,
        patch(
            "gates.parent_prefix._load_topic_labels",
            return_value={"microbiome_research": "Microbiome Research"},
        ),
    ]
    _enter(cms)
    try:
        rc = publish.main([])
    finally:
        _exit(cms)

    # Warn-severity failure MUST NOT halt.
    assert rc == publish.EXIT_OK
    upload_mock.assert_called_once()
    # Complete row still gets written.
    puts = [c.kwargs["Item"] for c in table.put_item.call_args_list]
    assert any(p["status"] == "complete" for p in puts)


# ---------- Phase 11 Task 2: S3 write order (D-11) ----------


class StubS3PutCapture:
    """Stub S3HierarchyClient subclass that captures put_object calls in order.

    Analog of test_spotlight_rotation_selector.py:309-319 custom stub pattern.
    """

    def __init__(self):
        self.put_calls = []  # list of (key, kwargs) tuples

    def put_object(self, key: str, body: bytes, content_type: str = "application/json",
                   cache_control: str | None = None) -> None:
        self.put_calls.append({"key": key, "body": body, "cache_control": cache_control})

    def get_object_bytes(self, key: str) -> bytes:
        # Simulate no prev manifest (first publish) and empty hierarchy for prev
        from botocore.exceptions import ClientError
        if key == "latest/manifest.json":
            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "Not Found"}}, "GetObject"
            )
        # For schema roundtrip gate — return the real schema bytes
        schema_path = REPO_ROOT / "docs/hierarchy.schema.json"
        if key.endswith("hierarchy.schema.json"):
            return schema_path.read_bytes()
        # Default: return minimal hierarchy JSON for round-trip gate
        bundled = _minimal_bundled_dict()
        return json.dumps(bundled).encode("utf-8")

    def key_exists(self, key: str) -> bool:
        return False


def _patch_io_with_s3_stub(
    *,
    bundled: dict | None = None,
    table: MagicMock | None = None,
    write_local: MagicMock | None = None,
):
    """Patch publish main() but use a real StubS3PutCapture for S3."""
    bundled = bundled if bundled is not None else _minimal_bundled_dict()
    table = table if table is not None else MagicMock()
    write_local = write_local if write_local is not None else MagicMock()

    fake_manifest = {
        "schema_version": "1.0.0",
        "taxonomy_version": "taxonomy_v2",
        "version": "v2026-05-12",
        "generated_at": "2026-05-12T00:00:00Z",
        "sha256": "deadbeef" * 8,
        "artifact_bytes": 1234,
    }

    s3_stub = StubS3PutCapture()

    return (
        patch.object(publish, "bundle", return_value=bundled),
        patch.object(publish, "get_table", return_value=table),
        patch.object(
            publish,
            "generate",
            return_value=(b"hierarchy-bytes", b"schema-bytes", fake_manifest),
        ),
        patch.object(publish, "write_local", write_local),
        patch.object(publish, "S3HierarchyClient", return_value=s3_stub),
        table,
        write_local,
        s3_stub,
        fake_manifest,
    )


def test_s3_write_order_is_exactly_5_steps():
    """D-11: upload_to_s3 must issue exactly 5 PutObjects in the documented order.
    Order: (1) {v}/hierarchy.json, (2) {v}/hierarchy.schema.json,
           (3) {v}/diff.json, (4) {v}/manifest.json, (5) latest/manifest.json."""
    (
        p_bundle, p_table, p_generate, p_write_local, p_s3,
        table, write_local_mock, s3_stub, fake_manifest,
    ) = _patch_io_with_s3_stub()
    table.query.return_value = {"Items": []}

    cms = [p_bundle, p_table, p_generate, p_write_local, p_s3]
    _enter(cms)
    try:
        rc = publish.main([])
    finally:
        _exit(cms)

    assert rc == publish.EXIT_OK
    assert len(s3_stub.put_calls) == 5, (
        f"Expected 5 S3 PutObject calls (D-11), got {len(s3_stub.put_calls)}: "
        f"{[c['key'] for c in s3_stub.put_calls]}"
    )
    keys = [c["key"] for c in s3_stub.put_calls]
    version = fake_manifest["version"]
    assert keys[0] == f"{version}/hierarchy.json", f"Step 1 must be {version}/hierarchy.json"
    assert keys[1] == f"{version}/hierarchy.schema.json", f"Step 2 must be {version}/hierarchy.schema.json"
    assert keys[2] == f"{version}/diff.json", f"Step 3 must be {version}/diff.json"
    assert keys[3] == f"{version}/manifest.json", f"Step 4 must be {version}/manifest.json"
    assert keys[4] == "latest/manifest.json", "Step 5 must be latest/manifest.json"


def test_cache_control_set_only_on_latest_manifest():
    """D-11: Cache-Control: max-age=60, must-revalidate must be on the 5th call
    (latest/manifest.json) ONLY. Other 4 calls must NOT carry CacheControl."""
    (
        p_bundle, p_table, p_generate, p_write_local, p_s3,
        table, write_local_mock, s3_stub, fake_manifest,
    ) = _patch_io_with_s3_stub()
    table.query.return_value = {"Items": []}

    cms = [p_bundle, p_table, p_generate, p_write_local, p_s3]
    _enter(cms)
    try:
        rc = publish.main([])
    finally:
        _exit(cms)

    assert rc == publish.EXIT_OK
    assert len(s3_stub.put_calls) == 5

    # 5th call (latest/manifest.json) must carry Cache-Control
    latest_call = s3_stub.put_calls[4]
    assert latest_call["key"] == "latest/manifest.json"
    assert latest_call["cache_control"] == "max-age=60, must-revalidate", (
        "latest/manifest.json must carry Cache-Control: max-age=60, must-revalidate"
    )

    # Other 4 calls must NOT carry Cache-Control
    for i, call in enumerate(s3_stub.put_calls[:4]):
        assert call["cache_control"] is None, (
            f"Call {i+1} ({call['key']}) must NOT carry cache_control, got {call['cache_control']!r}"
        )


def test_g29_cutover_flag_writes_audit_row():
    """D-16: --g29-cutover flag writes STAGE#g29_cutover#GLOBAL audit row with
    required fields. Without the flag, no such row is written."""
    (
        p_bundle, p_table, p_generate, p_write_local, p_s3,
        table, write_local_mock, s3_stub, fake_manifest,
    ) = _patch_io_with_s3_stub()
    table.query.return_value = {"Items": []}

    cms = [p_bundle, p_table, p_generate, p_write_local, p_s3]
    _enter(cms)
    try:
        rc = publish.main(["--g29-cutover"])
    finally:
        _exit(cms)

    assert rc == publish.EXIT_OK

    # Find the g29_cutover audit row
    all_puts = [c.kwargs["Item"] for c in table.put_item.call_args_list]
    cutover_rows = [p for p in all_puts if "g29_cutover" in p.get("PK", "")]
    assert len(cutover_rows) == 1, (
        f"Expected exactly 1 g29_cutover row, found {len(cutover_rows)}: {cutover_rows}"
    )
    row = cutover_rows[0]
    assert row["PK"] == "STAGE#g29_cutover#GLOBAL"
    assert row["SK"].startswith("RUN#")
    assert "new_publish_sha" in row
    assert "hierarchy_version_at_cutover" in row
    assert "started_at" in row
    assert "completed_at" in row


def test_no_g29_cutover_row_without_flag():
    """D-16: Without --g29-cutover, no g29_cutover STAGE# row is written."""
    (
        p_bundle, p_table, p_generate, p_write_local, p_s3,
        table, write_local_mock, s3_stub, fake_manifest,
    ) = _patch_io_with_s3_stub()
    table.query.return_value = {"Items": []}

    cms = [p_bundle, p_table, p_generate, p_write_local, p_s3]
    _enter(cms)
    try:
        rc = publish.main([])
    finally:
        _exit(cms)

    assert rc == publish.EXIT_OK
    all_puts = [c.kwargs["Item"] for c in table.put_item.call_args_list]
    cutover_rows = [p for p in all_puts if "g29_cutover" in p.get("PK", "")]
    assert len(cutover_rows) == 0, "No g29_cutover row without --g29-cutover flag"


def test_run_id_threaded_into_complete_row():
    """D-13: --run-id abc threads run_id into the STAGE# complete row."""
    (
        p_bundle, p_table, p_generate, p_write_local, p_s3,
        table, write_local_mock, s3_stub, fake_manifest,
    ) = _patch_io_with_s3_stub()
    table.query.return_value = {"Items": []}

    cms = [p_bundle, p_table, p_generate, p_write_local, p_s3]
    _enter(cms)
    try:
        rc = publish.main(["--run-id", "test-run-123"])
    finally:
        _exit(cms)

    assert rc == publish.EXIT_OK
    all_puts = [c.kwargs["Item"] for c in table.put_item.call_args_list]
    complete_rows = [p for p in all_puts if p.get("status") == "complete"]
    assert len(complete_rows) >= 1
    assert complete_rows[0]["run_id"] == "test-run-123", (
        "D-13: run_id must be threaded into the STAGE# complete row"
    )


def test_no_run_id_omits_field_from_complete_row():
    """D-13: Without --run-id, the STAGE# complete row must NOT have a run_id key."""
    (
        p_bundle, p_table, p_generate, p_write_local, p_s3,
        table, write_local_mock, s3_stub, fake_manifest,
    ) = _patch_io_with_s3_stub()
    table.query.return_value = {"Items": []}

    cms = [p_bundle, p_table, p_generate, p_write_local, p_s3]
    _enter(cms)
    try:
        rc = publish.main([])
    finally:
        _exit(cms)

    assert rc == publish.EXIT_OK
    all_puts = [c.kwargs["Item"] for c in table.put_item.call_args_list]
    complete_rows = [p for p in all_puts if p.get("status") == "complete"]
    assert len(complete_rows) >= 1
    assert "run_id" not in complete_rows[0], (
        "D-13: run_id must not appear in STAGE# row when not passed"
    )

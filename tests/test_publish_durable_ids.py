"""End-to-end wiring of brick A's durable-id store into the publish flow (#191).

The store reconcile is step 10 of ``pipeline_hierarchy.publish.main``. These tests
exercise it for real (no autouse stub, unlike ``test_publish_integration.py``) to
lock down the three properties that make brick A safe:

1. on a real publish it runs AFTER the artifact is uploaded and the STAGE#
   complete row is written, and it mints durable ids into the store;
2. under ``--dry-run`` it is unreachable — the table is never even bound, so the
   store does zero I/O (the consumer-safety invariant);
3. a store-write failure does NOT fail the publish — by the time step 10 runs the
   bytes are already live, so it is best-effort.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

from pipeline_hierarchy import publish
from pipeline_hierarchy.subtopic_id_store import (
    SUBTOPIC_ID_PK_PREFIX,
    SUBTOPIC_SLUG_PK_PREFIX,
)
from pipeline_hierarchy.subtopic_ids import is_subtopic_id

REPO_ROOT = Path(__file__).resolve().parents[1]


def _minimal_bundled_dict() -> dict:
    """Smallest dict that satisfies the live hierarchy.schema.json + publish gates
    (mirrors the fixture in test_publish_integration.py)."""
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


def _fake_manifest() -> dict:
    return {
        "schema_version": "1.0.0",
        "taxonomy_version": "taxonomy_v2",
        "version": "v2026-05-12",
        "generated_at": "2026-05-12T00:00:00Z",
        "sha256": "deadbeef" * 8,
        "artifact_bytes": 1234,
    }


class FakeTable:
    """Dict-backed Table resource stand-in. ``get_item`` is used ONLY by the
    durable-id store; ``query`` (skip check) and ``put_item`` (STAGE# + store)
    are used by the publish flow."""

    def __init__(self):
        self.items: dict[tuple[str, str], dict] = {}

    def query(self, **_kwargs):
        return {"Items": []}  # no prior complete row -> no skip

    def get_item(self, Key):
        key = (Key["PK"], Key["SK"])
        return {"Item": self.items[key]} if key in self.items else {}

    def put_item(self, Item):
        self.items[(Item["PK"], Item["SK"])] = dict(Item)

    def keys_with_prefix(self, prefix):
        return [k for k in self.items if k[0].startswith(prefix)]


def _make_s3() -> MagicMock:
    s3 = MagicMock()
    schema_bytes = (REPO_ROOT / "docs/hierarchy.schema.json").read_bytes()

    def _get(key: str) -> bytes:
        if key.endswith("hierarchy.schema.json"):
            return schema_bytes
        if key.endswith("manifest.json"):
            return json.dumps(_fake_manifest()).encode("utf-8")
        return json.dumps(_minimal_bundled_dict()).encode("utf-8")

    s3.get_object_bytes.side_effect = _get
    return s3


@contextmanager
def _patched_real_path(table):
    """Patch the publish I/O so main([]) runs end-to-end to step 10 with `table`."""
    upload = MagicMock()
    with patch.object(publish, "bundle", return_value=_minimal_bundled_dict()), patch.object(
        publish, "generate", return_value=(b"hier", b"schema", _fake_manifest())
    ), patch.object(publish, "get_table", return_value=table), patch.object(
        publish, "upload_to_s3", upload
    ), patch.object(publish, "write_local", MagicMock()), patch.object(
        publish, "S3HierarchyClient", return_value=_make_s3()
    ):
        yield upload


def test_reconcile_runs_after_upload_and_mints_on_real_path():
    table = FakeTable()
    with _patched_real_path(table) as upload:
        rc = publish.main([])

    assert rc == publish.EXIT_OK
    upload.assert_called_once()  # artifact is live before step 10 touches the store

    # write_complete (step 9) ran -> the STAGE# row is present...
    assert len(table.keys_with_prefix("STAGE#")) == 1
    # ...and step 10 minted one durable id + its slug pointer for the lone subtopic.
    id_rows = table.keys_with_prefix(SUBTOPIC_ID_PK_PREFIX)
    ptr_rows = table.keys_with_prefix(SUBTOPIC_SLUG_PK_PREFIX)
    assert len(id_rows) == 1 and len(ptr_rows) == 1
    meta = table.items[id_rows[0]]
    assert meta["slug_id"] == "microbiome_research_x"
    assert is_subtopic_id(meta["durable_id"])


def test_reconcile_runs_strictly_after_upload():
    # Prove ORDER, not just co-occurrence: if step 10 were moved before
    # upload_to_s3 (the regression this invariant forbids), this fails.
    events: list[str] = []
    table = FakeTable()
    orig_put = table.put_item

    def recording_put(Item):
        if Item["PK"].startswith(SUBTOPIC_ID_PK_PREFIX) and "store_write" not in events:
            events.append("store_write")
        return orig_put(Item)

    table.put_item = recording_put
    upload = MagicMock(side_effect=lambda *a, **k: events.append("upload"))
    with patch.object(publish, "bundle", return_value=_minimal_bundled_dict()), patch.object(
        publish, "generate", return_value=(b"hier", b"schema", _fake_manifest())
    ), patch.object(publish, "get_table", return_value=table), patch.object(
        publish, "upload_to_s3", upload
    ), patch.object(publish, "write_local", MagicMock()), patch.object(
        publish, "S3HierarchyClient", return_value=_make_s3()
    ):
        rc = publish.main([])

    assert rc == publish.EXIT_OK
    assert "upload" in events and "store_write" in events
    assert events.index("upload") < events.index("store_write")


def test_reconcile_runs_on_skip_path_so_republish_populates_store():
    # A content-identical republish hits the skip cache (no re-upload) — but the
    # store is a separate side-effect, so step 10 must still run. This is what
    # makes "deploy brick A, run --publish, store is populated" true rather than
    # waiting for the next content-changing cold run.
    bundled = _minimal_bundled_dict()
    expected_hash = publish.compute_publish_input_hash(bundled)
    prior = {
        "PK": "STAGE#publish_hierarchy#GLOBAL",
        "SK": "RUN#2026-05-10T12:00:00Z",
        "status": "complete",
        "input_hash": expected_hash,
        "output_pointer": "s3://wcmc-reciterai-hierarchy/v2026-05-10/",
        "started_at": "2026-05-10T12:00:00Z",
    }
    table = FakeTable()
    table.query = lambda **_k: {"Items": [prior]}  # should_skip -> True

    with patch.object(publish, "bundle", return_value=bundled), patch.object(
        publish, "get_table", return_value=table
    ), patch.object(publish, "write_local", MagicMock()):
        rc = publish.main([])

    assert rc == publish.EXIT_OK
    # skipped STAGE# row written AND the store populated on the skip path:
    assert len(table.keys_with_prefix("STAGE#")) == 1
    assert len(table.keys_with_prefix(SUBTOPIC_ID_PK_PREFIX)) == 1
    assert len(table.keys_with_prefix(SUBTOPIC_SLUG_PK_PREFIX)) == 1


def test_reconcile_is_unreachable_under_dry_run():
    table = FakeTable()
    get_table_mock = MagicMock(return_value=table)
    with patch.object(publish, "bundle", return_value=_minimal_bundled_dict()), patch.object(
        publish, "generate", return_value=(b"hier", b"schema", _fake_manifest())
    ), patch.object(publish, "get_table", get_table_mock), patch.object(
        publish, "write_local", MagicMock()
    ):
        rc = publish.main(["--dry-run"])

    assert rc == publish.EXIT_OK
    get_table_mock.assert_not_called()  # table never bound under --dry-run
    assert table.items == {}  # store (and STAGE#) entirely untouched


def test_store_failure_does_not_fail_the_publish():
    class BoomTable(FakeTable):
        # get_item is only called by the store -> make the reconcile blow up.
        def get_item(self, Key):
            raise RuntimeError("dynamodb unavailable")

    table = BoomTable()
    with _patched_real_path(table) as upload:
        rc = publish.main([])

    assert rc == publish.EXIT_OK  # publish already succeeded; step 10 is best-effort
    upload.assert_called_once()
    assert table.keys_with_prefix("STAGE#")  # the complete row was still written
    assert not table.keys_with_prefix(SUBTOPIC_ID_PK_PREFIX)  # nothing minted

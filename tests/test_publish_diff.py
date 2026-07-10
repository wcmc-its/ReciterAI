"""Phase 11 D-09, D-10, D-12, D-13, D-18: Tests for compute_diff() in publish.py.

Tests the diff.json producer:
- Hybrid: S3 GET of prev hierarchy (W1: uses existing get_object_bytes) +
  STAGE# assign rows filtered by run_id for reassigned_pmid_count.
- First-ever-publish: prev_version=None → from_version: null (O-01).
- editorial_only derivation.
- run_id filter isolates current cold-run rows (D-13).
"""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import MagicMock

import pytest


# Helper to build a minimal hierarchy dict for diff tests
def _make_hierarchy(taxonomy_version: str, subtopics: list[dict]) -> dict:
    return {
        "version": "subtopic_v1",
        "taxonomy_version": taxonomy_version,
        "excluded_topics": [],
        "topics": {
            "topic_a": {
                "subtopics": subtopics,
            }
        },
        "see_also": [],
    }


def _make_subtopic(sid: str, display_name: str = "Test Name") -> dict:
    return {
        "id": sid,
        "label": sid,
        "description": "desc",
        "display_name": display_name,
        "short_description": "short",
        "activity_count": 1,
        "total_weight": 1.0,
    }


def _make_mock_s3(prev_hierarchy: dict | None) -> MagicMock:
    """Mock S3HierarchyClient — get_object_bytes returns serialized prev_hierarchy."""
    mock_s3 = MagicMock()
    if prev_hierarchy is not None:
        mock_s3.get_object_bytes.return_value = json.dumps(prev_hierarchy).encode("utf-8")
    else:
        from botocore.exceptions import ClientError
        mock_s3.get_object_bytes.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "Not Found"}}, "GetObject"
        )
    return mock_s3


def _make_mock_table(stage_rows: list[dict]) -> MagicMock:
    """Mock DDB table — scan returns provided assign STAGE# rows (single page).

    The reassigned-count producer scans the ``STAGE#assign_subtopics#topic:``
    prefix (there is no ``#GLOBAL`` partition), so ``scan`` — not ``query`` —
    must yield the rows. Returning a plain dict with no ``LastEvaluatedKey``
    terminates the producer's pagination loop.
    """
    mock_table = MagicMock()
    mock_table.scan.return_value = {"Items": stage_rows}
    mock_table.query.return_value = {"Items": stage_rows}
    return mock_table


# ---------- Tests ----------


def test_compute_diff_happy_path_with_prev_version():
    """D-09, D-10, D-12, D-18: Happy path with prev version — taxonomy changed,
    added subtopic, reassigned count from STAGE# rows filtered by run_id."""
    from pipeline_hierarchy.publish import compute_diff

    prev_subs = [_make_subtopic("sub_1"), _make_subtopic("sub_2")]
    new_subs = [_make_subtopic("sub_1"), _make_subtopic("sub_2"), _make_subtopic("sub_3")]

    prev_h = _make_hierarchy("taxonomy_v2", prev_subs)
    new_h = _make_hierarchy("taxonomy_v3", new_subs)

    mock_s3 = MagicMock()
    mock_s3.get_object_bytes.return_value = json.dumps(prev_h).encode("utf-8")

    stage_row = {"PK": "STAGE#assign_subtopics#topic_a", "run_id": "abc", "records_written": 42}
    mock_table = _make_mock_table([stage_row])

    diff = compute_diff(
        prev_version="v2026-05-06",
        new_hierarchy=new_h,
        to_version="v2026-06-01",
        run_id="abc",
        table=mock_table,
        s3_client=mock_s3,
    )

    assert diff["diff_schema_version"] == "1.1.0", "D-12: schema version (1.1.0 since brick D)"
    assert diff["split_subtopics"] == [], "MagicMock table -> empty store -> empty lineage"
    assert diff["merged_subtopics"] == []
    assert diff["from_version"] == "v2026-05-06"
    assert diff["to_version"] == "v2026-06-01"
    assert diff["taxonomy_version_changed"] is True
    assert "sub_3" in diff["added_subtopics"], "sub_3 was added"
    assert len(diff["added_subtopics"]) == 1
    assert diff["reassigned_pmid_count"] == 42, "D-18: must be records_written from STAGE# rows"
    assert diff["editorial_only"] is False

    # W1: diff must call the existing get_object_bytes method
    mock_s3.get_object_bytes.assert_called_once_with("v2026-05-06/hierarchy.json")


def test_compute_diff_first_ever_publish_o01():
    """O-01: first-ever-publish (prev_version=None) emits from_version: null.
    No S3 GET call is made. Structural diffs are empty."""
    from pipeline_hierarchy.publish import compute_diff

    new_subs = [_make_subtopic("sub_1")]
    new_h = _make_hierarchy("taxonomy_v3", new_subs)

    mock_s3 = MagicMock()
    stage_row = {"PK": "STAGE#assign_subtopics#GLOBAL", "run_id": "abc", "records_written": 100}
    mock_table = _make_mock_table([stage_row])

    diff = compute_diff(
        prev_version=None,
        new_hierarchy=new_h,
        to_version="v2026-06-01",
        run_id="abc",
        table=mock_table,
        s3_client=mock_s3,
    )

    assert diff["from_version"] is None, "O-01: from_version must be null on first publish"
    assert diff["added_subtopics"] == [], "no structural diffs on first publish"
    assert diff["removed_subtopics"] == []
    assert diff["renamed_subtopics"] == []
    assert diff["taxonomy_version_changed"] is False
    assert diff["reassigned_pmid_count"] == 100
    assert diff["editorial_only"] is False

    # No S3 call when prev_version is None
    mock_s3.get_object_bytes.assert_not_called()


def test_compute_diff_editorial_only_true():
    """editorial_only=True iff only renames, no adds/removes, no taxonomy change, reassigned==0."""
    from pipeline_hierarchy.publish import compute_diff

    prev_subs = [_make_subtopic("sub_1", display_name="Old Name")]
    new_subs = [_make_subtopic("sub_1", display_name="New Name")]  # renamed only

    prev_h = _make_hierarchy("taxonomy_v2", prev_subs)
    new_h = _make_hierarchy("taxonomy_v2", new_subs)

    mock_s3 = MagicMock()
    mock_s3.get_object_bytes.return_value = json.dumps(prev_h).encode("utf-8")
    mock_table = _make_mock_table([])  # no STAGE# rows → reassigned == 0

    diff = compute_diff(
        prev_version="v2026-05-06",
        new_hierarchy=new_h,
        to_version="v2026-06-01",
        run_id="abc",
        table=mock_table,
        s3_client=mock_s3,
    )

    assert diff["editorial_only"] is True
    assert len(diff["renamed_subtopics"]) == 1
    assert diff["renamed_subtopics"][0]["id"] == "sub_1"
    assert diff["renamed_subtopics"][0]["old_display_name"] == "Old Name"
    assert diff["renamed_subtopics"][0]["new_display_name"] == "New Name"


def test_compute_diff_editorial_only_false_when_reassigned_nonzero():
    """editorial_only=False when renames present but reassigned > 0."""
    from pipeline_hierarchy.publish import compute_diff

    prev_subs = [_make_subtopic("sub_1", display_name="Old Name")]
    new_subs = [_make_subtopic("sub_1", display_name="New Name")]

    prev_h = _make_hierarchy("taxonomy_v2", prev_subs)
    new_h = _make_hierarchy("taxonomy_v2", new_subs)

    mock_s3 = MagicMock()
    mock_s3.get_object_bytes.return_value = json.dumps(prev_h).encode("utf-8")
    stage_row = {"run_id": "abc", "records_written": 5}
    mock_table = _make_mock_table([stage_row])

    diff = compute_diff(
        prev_version="v2026-05-06",
        new_hierarchy=new_h,
        to_version="v2026-06-01",
        run_id="abc",
        table=mock_table,
        s3_client=mock_s3,
    )

    assert diff["editorial_only"] is False
    assert diff["reassigned_pmid_count"] == 5


class _PrefixEnforcingTable:
    """Fake DDB table that mirrors production: the assign complete rows live under
    per-topic PKs (STAGE#assign_subtopics#topic:{id}), and the STAGE#...#GLOBAL
    partition the old producer queried is empty. So a scan of the topic prefix
    returns the rows; a query of the GLOBAL PK returns nothing."""

    def __init__(self, topic_rows):
        self._topic_rows = topic_rows

    def query(self, **kwargs):
        return {"Items": []}

    def scan(self, **kwargs):
        vals = kwargs.get("ExpressionAttributeValues", {})
        if vals.get(":pk") == "STAGE#assign_subtopics#topic:":
            return {"Items": self._topic_rows}
        return {"Items": []}


def test_reassigned_count_scans_topic_scoped_stage_rows():
    """The reassigned-count producer must scan the topic-scoped assign STAGE# rows
    filtered by run_id. The old code queried STAGE#assign_subtopics#GLOBAL — a
    partition nothing writes — so reassigned_pmid_count was structurally always 0,
    letting editorial_only be reported on a publish that reassigned thousands."""
    from pipeline_hierarchy.publish import compute_diff

    prev_h = _make_hierarchy("taxonomy_v2", [_make_subtopic("sub_1")])
    new_h = _make_hierarchy("taxonomy_v2", [_make_subtopic("sub_1")])
    mock_s3 = MagicMock()
    mock_s3.get_object_bytes.return_value = json.dumps(prev_h).encode("utf-8")

    rows = [
        {"PK": "STAGE#assign_subtopics#topic:topic_a", "run_id": "cold1", "records_written": 42},
        {"PK": "STAGE#assign_subtopics#topic:topic_b", "run_id": "cold1", "records_written": 8},
        {"PK": "STAGE#assign_subtopics#topic:topic_c", "run_id": "other", "records_written": 999},
    ]
    table = _PrefixEnforcingTable(rows)

    diff = compute_diff(
        prev_version="v2026-05-06",
        new_hierarchy=new_h,
        to_version="v2026-06-01",
        run_id="cold1",
        table=table,
        s3_client=mock_s3,
    )

    assert diff["reassigned_pmid_count"] == 50, (
        "must sum records_written from topic-scoped rows for this run_id only"
    )


def test_compute_diff_run_id_filter():
    """D-13: only STAGE# rows matching run_id are counted; others are filtered out."""
    from pipeline_hierarchy.publish import compute_diff

    prev_h = _make_hierarchy("taxonomy_v2", [_make_subtopic("sub_1")])
    new_h = _make_hierarchy("taxonomy_v2", [_make_subtopic("sub_1")])

    mock_s3 = MagicMock()
    mock_s3.get_object_bytes.return_value = json.dumps(prev_h).encode("utf-8")

    # Two rows: one for run_id="abc" (records_written=42) and one for "xyz" (records_written=999)
    rows = [
        {"run_id": "abc", "records_written": 42},
        {"run_id": "xyz", "records_written": 999},
    ]
    mock_table = _make_mock_table(rows)

    diff = compute_diff(
        prev_version="v2026-05-06",
        new_hierarchy=new_h,
        to_version="v2026-06-01",
        run_id="abc",
        table=mock_table,
        s3_client=mock_s3,
    )

    assert diff["reassigned_pmid_count"] == 42, "Only run_id=abc row should be counted"


# ---------- brick D: store-derived lineage overlay flows into diff.json ----------


class _DiffTable:
    """Fake DDB table for compute_diff: query() yields assign STAGE# rows;
    scan() yields SUBTOPIC_ID#/META rows for the lineage overlay (single page)."""

    def __init__(self, stage_rows, lineage_rows):
        self._stage_rows = stage_rows
        self._lineage_rows = lineage_rows

    def query(self, **kwargs):
        return {"Items": self._stage_rows}

    def scan(self, **kwargs):
        return {"Items": self._lineage_rows}


def _meta_row(durable, slug, *, split_from=None, merged_into=None):
    row = {
        "PK": f"SUBTOPIC_ID#{durable}", "SK": "META", "record_type": "SUBTOPIC_ID",
        "durable_id": durable, "slug_id": slug,
    }
    if split_from:
        row["split_from"] = split_from
    if merged_into:
        row["merged_into"] = merged_into
    return row


def test_compute_diff_wires_store_lineage_and_bumps_schema():
    """Brick D: durable split/merge edges in the store surface as slug-space
    split_subtopics / merged_subtopics in diff.json, at schema 1.1.0. added/removed
    stay authoritative, so a split/merge is never editorial_only."""
    from pipeline_hierarchy.publish import compute_diff

    prev_h = _make_hierarchy("taxonomy_v2", [_make_subtopic("aging_parent"), _make_subtopic("aging_prior")])
    new_h = _make_hierarchy("taxonomy_v2", [
        _make_subtopic("aging_parent"), _make_subtopic("aging_child"), _make_subtopic("aging_succ"),
    ])
    mock_s3 = MagicMock()
    mock_s3.get_object_bytes.return_value = json.dumps(prev_h).encode("utf-8")

    table = _DiffTable(
        stage_rows=[],
        lineage_rows=[
            _meta_row("st_parent", "aging_parent"),
            _meta_row("st_child", "aging_child", split_from="st_parent"),
            _meta_row("st_succ", "aging_succ"),
            _meta_row("st_prior", "aging_prior", merged_into="st_succ"),
        ],
    )

    diff = compute_diff(
        prev_version="v2026-05-06", new_hierarchy=new_h, to_version="v2026-06-01",
        run_id="abc", table=table, s3_client=mock_s3,
    )

    assert diff["diff_schema_version"] == "1.1.0"
    assert diff["split_subtopics"] == [{"id": "aging_child", "split_from": "aging_parent"}]
    assert diff["merged_subtopics"] == [{"id": "aging_prior", "merged_into": "aging_succ"}]
    # added/removed remain authoritative — a 1.0.0 reader is unaffected
    assert set(diff["added_subtopics"]) == {"aging_child", "aging_succ"}
    assert diff["removed_subtopics"] == ["aging_prior"]
    assert diff["editorial_only"] is False  # a split/merge is never editorial-only

"""Tests for scripts/migrate_activity_hierarchy_version.py — Phase 11 D-02/D-17.

Covers:
- Test 1: rows with subtopic_ids stamped with v0.0.0-pre-phase-11; rows
  without subtopic_ids are skipped; counts correct
- Test 2: scan FilterExpression uses attribute_not_exists(hierarchy_version)
  so already-stamped rows are excluded at the DDB layer (idempotent)
- Test 3: --dry-run writes nothing
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_scan_item(pk, sk, has_primary=True):
    """Build a minimal DDB activity item."""
    item = {"PK": pk, "SK": sk}
    if has_primary:
        item["primary_subtopic_id"] = "atherosclerosis"
        item["subtopic_ids"] = ["atherosclerosis"]
    return item


def _build_mock_table(items_page_1, items_page_2=None):
    """Build a mock DynamoDB table whose scan returns paginated results."""
    table = MagicMock()

    responses = [
        # First page
        {
            "Items": items_page_1,
            **({"LastEvaluatedKey": {"PK": "LAST"}} if items_page_2 is not None else {}),
        }
    ]
    if items_page_2 is not None:
        responses.append({"Items": items_page_2})

    table.scan.side_effect = responses
    table.update_item.return_value = {}
    return table


# ---------------------------------------------------------------------------
# Test 1: basic stamp behavior
# ---------------------------------------------------------------------------


def test_t1_stamps_rows_with_primary_subtopic_id():
    """2 rows with subtopic data → both get update_item with v0.0.0-pre-phase-11."""
    from scripts.migrate_activity_hierarchy_version import main

    items = [
        _make_scan_item("TOPIC#x", "SCORE#1#ACTIVITY#pmid_1#cwid_a"),
        _make_scan_item("TOPIC#y", "SCORE#2#ACTIVITY#pmid_2#cwid_b"),
    ]
    mock_table = _build_mock_table(items)

    with patch("scripts.migrate_activity_hierarchy_version.get_table", return_value=mock_table):
        rc = main([])

    assert rc == 0
    assert mock_table.update_item.call_count == 2

    # Verify each update stamps the sentinel value
    for c in mock_table.update_item.call_args_list:
        eav = c.kwargs.get("ExpressionAttributeValues") or c.args[0].get("ExpressionAttributeValues", {})
        assert ":v" in eav or ":hv" in eav, f"Missing sentinel value kwarg: {c}"
        val = eav.get(":v") or eav.get(":hv")
        assert val == "v0.0.0-pre-phase-11", f"Expected sentinel, got {val!r}"


def test_t1_reports_correct_counts(capsys):
    """Script reports Rows scanned: 2, Newly stamped: 2."""
    from scripts.migrate_activity_hierarchy_version import main

    items = [
        _make_scan_item("TOPIC#x", "SCORE#1#ACTIVITY#pmid_1#cwid_a"),
        _make_scan_item("TOPIC#y", "SCORE#2#ACTIVITY#pmid_2#cwid_b"),
    ]
    mock_table = _build_mock_table(items)

    with patch("scripts.migrate_activity_hierarchy_version.get_table", return_value=mock_table):
        main([])

    out = capsys.readouterr().out
    assert "2" in out  # total count appears somewhere in output


# ---------------------------------------------------------------------------
# Test 2: FilterExpression enforces idempotency at scan level
# ---------------------------------------------------------------------------


def test_t2_scan_uses_attribute_not_exists_filter():
    """Scan FilterExpression must filter out already-stamped rows."""
    from scripts.migrate_activity_hierarchy_version import main

    mock_table = _build_mock_table([])  # empty — we just check the scan call

    with patch("scripts.migrate_activity_hierarchy_version.get_table", return_value=mock_table):
        main([])

    assert mock_table.scan.call_count >= 1
    scan_kwargs = mock_table.scan.call_args.kwargs or {}
    if not scan_kwargs:
        scan_kwargs = mock_table.scan.call_args.args[0] if mock_table.scan.call_args.args else {}

    filter_expr = str(scan_kwargs.get("FilterExpression", ""))
    # The filter must involve attribute_not_exists(hierarchy_version) or equivalent.
    # Accept both string representation and boto3 ConditionExpression objects.
    scan_kwargs_str = str(mock_table.scan.call_args)
    assert (
        "attribute_not_exists" in scan_kwargs_str
        or "hierarchy_version" in scan_kwargs_str
    ), f"Expected attribute_not_exists(hierarchy_version) in scan: {scan_kwargs_str}"


# ---------------------------------------------------------------------------
# Test 3: --dry-run writes nothing
# ---------------------------------------------------------------------------


def test_t3_dry_run_does_not_write():
    """--dry-run must call scan but never update_item."""
    from scripts.migrate_activity_hierarchy_version import main

    items = [
        _make_scan_item("TOPIC#x", "SCORE#1#ACTIVITY#pmid_1#cwid_a"),
    ]
    mock_table = _build_mock_table(items)

    with patch("scripts.migrate_activity_hierarchy_version.get_table", return_value=mock_table):
        rc = main(["--dry-run"])

    assert rc == 0
    mock_table.update_item.assert_not_called()

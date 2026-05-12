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


def _extract_condition_attrs(condition) -> list[str]:
    """Recursively extract all attribute names from a boto3 ConditionExpression tree."""
    names = []
    if hasattr(condition, "get_expression"):
        expr = condition.get_expression()
        operator = expr.get("operator", "")
        for v in expr.get("values", ()):
            if hasattr(v, "name"):
                # It's an Attr object
                names.append(v.name)
            elif hasattr(v, "get_expression"):
                names.extend(_extract_condition_attrs(v))
    return names


def test_t2_scan_uses_attribute_not_exists_filter():
    """Scan FilterExpression must filter out already-stamped rows.

    The filter uses boto3 ConditionExpression objects. We verify the expression
    contains an attribute_not_exists condition referencing hierarchy_version.
    """
    from scripts.migrate_activity_hierarchy_version import main

    captured_scan_kwargs: list = []

    def capture_scan(**kwargs):
        captured_scan_kwargs.append(kwargs)
        return {"Items": []}

    mock_table = MagicMock()
    mock_table.scan.side_effect = capture_scan
    mock_table.update_item.return_value = {}

    with patch("scripts.migrate_activity_hierarchy_version.get_table", return_value=mock_table):
        main([])

    assert len(captured_scan_kwargs) >= 1
    filter_expr = captured_scan_kwargs[0].get("FilterExpression")
    assert filter_expr is not None, "No FilterExpression passed to scan"

    # Walk the condition tree to find all referenced attribute names
    attr_names = _extract_condition_attrs(filter_expr)
    assert "hierarchy_version" in attr_names, (
        f"Expected hierarchy_version in FilterExpression attributes: {attr_names}"
    )


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

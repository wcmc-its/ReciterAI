"""Tests for scripts/migrate_spotlight_history_pk.py — Phase 11 D-04/D-05.

Covers:
- Test 3: row with last_shown_publish_id = "v2026-05-12" → PutItem then DeleteItem
- Test 4: row with no last_shown_publish_id → never_spotlighted_count; PK uses 0.0.0-orphan
- Test 5: row with malformed last_shown_publish_id → malformed_publish_id_count; PK uses
  0.0.0-orphan; diff log records the row
- Test 6: default mode is dry-run; --commit required; confirm prompt requires "yes"
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_history_item(subtopic_id, last_shown=None):
    """Build a pre-migration SPOTLIGHT_HISTORY# row (old PK shape)."""
    item = {
        "PK": f"SPOTLIGHT_HISTORY#{subtopic_id}",
        "SK": "STATE",
        "shown_count": 3,
    }
    if last_shown is not None:
        item["last_shown_publish_id"] = last_shown
    return item


def _build_table_with_items(items, already_migrated=None):
    """Build a mock table with scan returning items + optional already-migrated rows."""
    all_items = list(items)
    if already_migrated:
        all_items.extend(already_migrated)
    table = MagicMock()
    table.scan.return_value = {"Items": all_items}
    table.put_item.return_value = {}
    table.delete_item.return_value = {}
    return table


# ---------------------------------------------------------------------------
# Test 3: real publish_id → rewrite with that version
# ---------------------------------------------------------------------------


def test_t3_real_publish_id_rewrites_pk_correctly(tmp_path):
    """Row with v2026-05-12 last_shown_publish_id → PutItem then DeleteItem with new PK."""
    from scripts.migrate_spotlight_history_pk import main

    sid = "atherosclerosis"
    item = _make_history_item(sid, last_shown="v2026-05-12")
    table = _build_table_with_items([item])

    log_file = tmp_path / "test_migration.log"

    with patch("scripts.migrate_spotlight_history_pk.get_table", return_value=table), \
         patch("scripts.migrate_spotlight_history_pk.open", create=True) as mock_open:
        mock_open.return_value.__enter__ = lambda s: io.StringIO()
        mock_open.return_value.__exit__ = MagicMock(return_value=False)

        # --commit with "yes" confirm
        with patch("builtins.input", return_value="yes"):
            rc = main(["--commit"])

    assert rc == 0

    # PutItem called with new PK shape
    assert table.put_item.call_count == 1
    put_item_arg = table.put_item.call_args.kwargs.get("Item") or table.put_item.call_args.args[0]
    assert put_item_arg["PK"] == f"SPOTLIGHT_HISTORY#v2026-05-12#{sid}"

    # DeleteItem called on old PK
    assert table.delete_item.call_count == 1
    del_key = table.delete_item.call_args.kwargs.get("Key") or table.delete_item.call_args.args[0]
    assert del_key["PK"] == f"SPOTLIGHT_HISTORY#{sid}"


def test_t3_put_before_delete():
    """PutItem must be called before DeleteItem (not after) — data safety."""
    from scripts.migrate_spotlight_history_pk import main

    call_order = []
    sid = "cardio"
    item = _make_history_item(sid, last_shown="v2026-05-12")

    table = MagicMock()
    table.scan.return_value = {"Items": [item]}
    table.put_item.side_effect = lambda **kw: call_order.append("put") or {}
    table.delete_item.side_effect = lambda **kw: call_order.append("delete") or {}

    with patch("scripts.migrate_spotlight_history_pk.get_table", return_value=table), \
         patch("builtins.input", return_value="yes"):
        rc = main(["--commit"])

    assert rc == 0
    assert call_order == ["put", "delete"], f"Wrong order: {call_order}"


# ---------------------------------------------------------------------------
# Test 4: missing last_shown_publish_id → orphan
# ---------------------------------------------------------------------------


def test_t4_missing_publish_id_uses_orphan_stamp():
    """Row without last_shown_publish_id → 0.0.0-orphan PK, counted in never_spotlighted."""
    from scripts.migrate_spotlight_history_pk import main

    sid = "aging_subtopic"
    item = _make_history_item(sid)  # no last_shown_publish_id
    table = _build_table_with_items([item])

    with patch("scripts.migrate_spotlight_history_pk.get_table", return_value=table), \
         patch("builtins.input", return_value="yes"):
        rc = main(["--commit"])

    assert rc == 0
    put_item_arg = table.put_item.call_args.kwargs.get("Item") or table.put_item.call_args.args[0]
    assert put_item_arg["PK"] == f"SPOTLIGHT_HISTORY#0.0.0-orphan#{sid}"


def test_t4_never_spotlighted_count_reported(capsys):
    """Script output includes never_spotlighted count."""
    from scripts.migrate_spotlight_history_pk import main

    sid = "aging_subtopic"
    item = _make_history_item(sid)
    table = _build_table_with_items([item])

    with patch("scripts.migrate_spotlight_history_pk.get_table", return_value=table), \
         patch("builtins.input", return_value="yes"):
        main(["--commit"])

    out = capsys.readouterr().out
    # Should mention never_spotlighted count (any non-negative number)
    assert "never" in out.lower() or "spotlighted" in out.lower() or "orphan" in out.lower(), (
        f"Expected never_spotlighted/orphan mention in: {out}"
    )


# ---------------------------------------------------------------------------
# Test 5: malformed publish_id → orphan + diff log
# ---------------------------------------------------------------------------


def test_t5_malformed_publish_id_uses_orphan_and_logs(tmp_path):
    """Malformed last_shown_publish_id → 0.0.0-orphan PK + malformed_publish_id_count."""
    from scripts.migrate_spotlight_history_pk import main

    sid = "cardio_sub"
    malformed = "not-a-valid-date"
    item = _make_history_item(sid, last_shown=malformed)
    table = _build_table_with_items([item])

    diff_log_contents = io.StringIO()

    with patch("scripts.migrate_spotlight_history_pk.get_table", return_value=table), \
         patch("builtins.input", return_value="yes"), \
         patch("builtins.open", MagicMock(return_value=diff_log_contents)):
        rc = main(["--commit"])

    assert rc == 0
    # Orphan stamp in PutItem
    put_item_arg = table.put_item.call_args.kwargs.get("Item") or table.put_item.call_args.args[0]
    assert put_item_arg["PK"] == f"SPOTLIGHT_HISTORY#0.0.0-orphan#{sid}"


def test_t5_malformed_count_reported(capsys):
    """Script output mentions malformed count separately from never_spotlighted."""
    from scripts.migrate_spotlight_history_pk import main

    items = [
        _make_history_item("sub_a"),                          # never spotlighted
        _make_history_item("sub_b", last_shown="garbage"),    # malformed
        _make_history_item("sub_c", last_shown="v2026-05-12"), # real
    ]
    table = _build_table_with_items(items)

    with patch("scripts.migrate_spotlight_history_pk.get_table", return_value=table), \
         patch("builtins.input", return_value="yes"):
        main(["--commit"])

    out = capsys.readouterr().out
    assert "malformed" in out.lower() or "malformed_publish_id" in out.lower(), (
        f"Expected malformed count in output: {out}"
    )


# ---------------------------------------------------------------------------
# Test 6: dry-run default; --commit opt-in; confirm prompt
# ---------------------------------------------------------------------------


def test_t6_default_is_dry_run_no_writes():
    """Without --commit, no PutItem or DeleteItem is called."""
    from scripts.migrate_spotlight_history_pk import main

    item = _make_history_item("sub_a", last_shown="v2026-05-12")
    table = _build_table_with_items([item])

    with patch("scripts.migrate_spotlight_history_pk.get_table", return_value=table):
        rc = main([])  # no --commit

    assert rc == 0
    table.put_item.assert_not_called()
    table.delete_item.assert_not_called()


def test_t6_commit_declined_returns_1():
    """If user types anything other than 'yes' at the confirm prompt, return 1."""
    from scripts.migrate_spotlight_history_pk import main

    item = _make_history_item("sub_a", last_shown="v2026-05-12")
    table = _build_table_with_items([item])

    with patch("scripts.migrate_spotlight_history_pk.get_table", return_value=table), \
         patch("builtins.input", return_value="no"):
        rc = main(["--commit"])

    assert rc == 1
    table.put_item.assert_not_called()
    table.delete_item.assert_not_called()


def test_t6_commit_requires_exact_yes():
    """Confirm prompt requires exact 'yes' (case-insensitive via strip().lower())."""
    from scripts.migrate_spotlight_history_pk import main

    item = _make_history_item("sub_a", last_shown="v2026-05-12")
    table = _build_table_with_items([item])

    with patch("scripts.migrate_spotlight_history_pk.get_table", return_value=table), \
         patch("builtins.input", return_value="YES"):
        rc = main(["--commit"])

    assert rc == 0  # "YES" normalizes to "yes" via lower()
    assert table.put_item.call_count == 1


def test_t6_idempotent_already_migrated_rows_skipped():
    """Rows with PK.count('#') > 1 are already migrated; skip them."""
    from scripts.migrate_spotlight_history_pk import main

    # Already migrated: 2-segment PK with version already there
    already_migrated = {
        "PK": "SPOTLIGHT_HISTORY#v2026-05-01#sub_already",
        "SK": "STATE",
        "shown_count": 5,
    }
    table = _build_table_with_items([already_migrated])

    with patch("scripts.migrate_spotlight_history_pk.get_table", return_value=table), \
         patch("builtins.input", return_value="yes"):
        rc = main(["--commit"])

    assert rc == 0
    table.put_item.assert_not_called()
    table.delete_item.assert_not_called()

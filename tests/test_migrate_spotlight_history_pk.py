"""Tests for the spotlight-history slug->durable re-key migration (#191 brick D).

Covers:
- pure classification: migrate / orphan / already-migrated / malformed buckets;
- the disjoint-namespace guard and the target-PK collision refusal (fail loud);
- dry-run writes nothing;
- the two-phase write: every put flushes before any delete, attrs + provenance ride
  onto the durable row, and the slug row is removed;
- idempotent re-run: durable-keyed rows are recognised and skipped;
- orphans are left untouched;
- best-effort cutover-row count back-write (and the no-row path).
"""
from __future__ import annotations

import pytest

from scripts.migrate_spotlight_history_pk import (
    build_durable_item,
    classify_history_rows,
    migrate_history,
    update_cutover_counts,
)


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #


class _FakeBatchWriter:
    """Buffers like boto3's batch_writer: nothing is applied until __exit__ flushes,
    so Phase A's puts are all durable before Phase B's deletes begin."""

    def __init__(self, table):
        self._table = table
        self._buf: list[tuple[str, dict]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        for kind, payload in self._buf:
            self._table._apply(kind, payload)
        return False

    def put_item(self, Item):
        self._buf.append(("put", dict(Item)))

    def delete_item(self, Key):
        self._buf.append(("delete", dict(Key)))


class FakeTable:
    """Dict-backed Table resource stand-in: scan (paginating, filter-agnostic),
    batch_writer (buffered), query + update_item (for the cutover back-write)."""

    def __init__(self, items=(), page=100):
        self.items: dict[tuple[str, str], dict] = {
            (i["PK"], i["SK"]): dict(i) for i in items
        }
        self._page = page
        self.ops: list[tuple[str, str]] = []  # ordered (kind, PK) — two-phase assertions

    # --- scan ---
    def scan(self, **kwargs):
        rows = list(self.items.values())
        start = kwargs.get("ExclusiveStartKey", 0)
        chunk = [dict(r) for r in rows[start : start + self._page]]
        resp = {"Items": chunk}
        nxt = start + self._page
        if nxt < len(rows):
            resp["LastEvaluatedKey"] = nxt
        return resp

    # --- writes ---
    def batch_writer(self):
        return _FakeBatchWriter(self)

    def _apply(self, kind, payload):
        if kind == "put":
            self.items[(payload["PK"], payload["SK"])] = payload
            self.ops.append(("put", payload["PK"]))
        else:
            self.items.pop((payload["PK"], payload["SK"]), None)
            self.ops.append(("delete", payload["PK"]))

    # --- cutover row helpers ---
    def query(self, **kwargs):
        pk = kwargs["ExpressionAttributeValues"][":pk"]
        rows = [v for (p, _sk), v in self.items.items() if p == pk]
        rows.sort(key=lambda r: r["SK"], reverse=not kwargs.get("ScanIndexForward", True))
        return {"Items": rows}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues, **kw):
        item = self.items[(Key["PK"], Key["SK"])]
        item["migrated_rotation_count"] = ExpressionAttributeValues[":m"]
        item["orphan_count"] = ExpressionAttributeValues[":o"]


def _hist(ident, shown=3, last="2026-06-01T00:00:00Z", pid="v2026-06-01"):
    return {
        "PK": f"SPOTLIGHT_HISTORY#{ident}",
        "SK": "STATE",
        "shown_count": shown,
        "last_shown_at": last,
        "last_shown_publish_id": pid,
    }


_FIXED_CLOCK = lambda: "2026-06-11T12:00:00Z"  # noqa: E731 — test clock injection


# --------------------------------------------------------------------------- #
# pure classification
# --------------------------------------------------------------------------- #


def test_classify_buckets_migrate_orphan_already_and_malformed():
    slug_to_durable = {"aging_one": "st_one", "aging_two": "st_two"}
    items = [
        _hist("aging_one"),          # -> migrate
        _hist("aging_two"),          # -> migrate
        _hist("ghost_slug"),         # -> orphan (no durable)
        _hist("st_one"),             # -> already durable-keyed (skip)
        # a leftover versioned key: the fold has not run -> malformed, never re-keyed
        {"PK": "SPOTLIGHT_HISTORY#v2026-06-01#aging_one", "SK": "STATE"},
        {"PK": "SUBTOPIC_SLUG#aging_one", "SK": "PTR"},    # -> non-STATE: ignored entirely
    ]
    c = classify_history_rows(items, slug_to_durable)
    assert {p.slug for p in c.to_migrate} == {"aging_one", "aging_two"}
    assert {p.target_pk for p in c.to_migrate} == {
        "SPOTLIGHT_HISTORY#st_one",
        "SPOTLIGHT_HISTORY#st_two",
    }
    assert c.orphans == ["ghost_slug"]
    assert c.already_migrated == 1
    assert c.malformed == ["SPOTLIGHT_HISTORY#v2026-06-01#aging_one"]
    assert c.collisions == {}


def test_classify_raises_on_namespace_overlap():
    # a slug literally equal to a durable id makes the migrate-vs-skip call ambiguous
    with pytest.raises(ValueError, match="disjoint"):
        classify_history_rows([], {"st_one": "st_one"})


def test_classify_detects_target_collision():
    # two distinct slugs resolving to the same durable in the same version
    slug_to_durable = {"slug_a": "st_dup", "slug_b": "st_dup"}
    items = [_hist("slug_a"), _hist("slug_b")]
    c = classify_history_rows(items, slug_to_durable)
    assert "SPOTLIGHT_HISTORY#st_dup" in c.collisions
    assert len(c.collisions["SPOTLIGHT_HISTORY#st_dup"]) == 2


def test_build_durable_item_preserves_attrs_and_stamps_provenance():
    [plan] = classify_history_rows(
        [_hist("aging_one", shown=5, last="2026-05-01T00:00:00Z")],
        {"aging_one": "st_one"},
    ).to_migrate
    out = build_durable_item(plan, migrated_at="2026-06-11T12:00:00Z")
    assert out["PK"] == "SPOTLIGHT_HISTORY#st_one"
    assert out["SK"] == "STATE"
    assert out["shown_count"] == 5
    assert out["last_shown_at"] == "2026-05-01T00:00:00Z"
    assert out["migrated_from_slug"] == "aging_one"
    assert out["migrated_at"] == "2026-06-11T12:00:00Z"


# --------------------------------------------------------------------------- #
# migrate_history (I/O orchestration)
# --------------------------------------------------------------------------- #


def test_dry_run_writes_nothing():
    table = FakeTable([_hist("aging_one")], page=1)
    summary = migrate_history(
        table, slug_to_durable={"aging_one": "st_one"}, dry_run=True, now_fn=_FIXED_CLOCK
    )
    assert summary["migrated_rotation_count"] == 1
    assert table.ops == []  # nothing written
    assert ("SPOTLIGHT_HISTORY#aging_one", "STATE") in table.items  # slug row untouched


def test_two_phase_puts_all_flush_before_any_delete():
    items = [_hist("aging_one"), _hist("aging_two")]
    table = FakeTable(items, page=1)  # tiny page to exercise scan pagination too
    summary = migrate_history(
        table,
        slug_to_durable={"aging_one": "st_one", "aging_two": "st_two"},
        dry_run=False,
        now_fn=_FIXED_CLOCK,
    )
    assert summary["migrated_rotation_count"] == 2
    kinds = [k for k, _pk in table.ops]
    # every put precedes every delete — the crash-safety invariant
    assert kinds == ["put", "put", "delete", "delete"]
    # durable rows present with carried attrs + provenance; slug rows gone
    durable = table.items[("SPOTLIGHT_HISTORY#st_one", "STATE")]
    assert durable["shown_count"] == 3 and durable["migrated_from_slug"] == "aging_one"
    assert ("SPOTLIGHT_HISTORY#aging_one", "STATE") not in table.items
    assert ("SPOTLIGHT_HISTORY#aging_two", "STATE") not in table.items


def test_idempotent_rerun_skips_already_durable_rows():
    table = FakeTable([_hist("aging_one")], page=10)
    mapping = {"aging_one": "st_one"}
    migrate_history(table, slug_to_durable=mapping, dry_run=False, now_fn=_FIXED_CLOCK)
    table.ops.clear()
    # second pass: the row is now durable-keyed -> recognised, skipped, no writes
    summary2 = migrate_history(table, slug_to_durable=mapping, dry_run=False, now_fn=_FIXED_CLOCK)
    assert summary2["migrated_rotation_count"] == 0
    assert summary2["already_migrated"] == 1
    assert table.ops == []


def test_orphan_rows_left_untouched():
    table = FakeTable([_hist("ghost_slug")], page=10)
    summary = migrate_history(
        table, slug_to_durable={"aging_one": "st_one"}, dry_run=False, now_fn=_FIXED_CLOCK
    )
    assert summary["orphan_count"] == 1
    assert summary["migrated_rotation_count"] == 0
    assert table.ops == []
    assert ("SPOTLIGHT_HISTORY#ghost_slug", "STATE") in table.items  # not deleted


def test_collision_aborts_real_migration():
    table = FakeTable([_hist("slug_a"), _hist("slug_b")], page=10)
    with pytest.raises(ValueError, match="collision"):
        migrate_history(
            table,
            slug_to_durable={"slug_a": "st_dup", "slug_b": "st_dup"},
            dry_run=False,
            now_fn=_FIXED_CLOCK,
        )
    assert table.ops == []  # refused before any write


# --------------------------------------------------------------------------- #
# cutover-row back-write
# --------------------------------------------------------------------------- #


def _cutover_row(started_at, migrated=0, orphan=0):
    return {
        "PK": "STAGE#hierarchy_version_cutover#GLOBAL",
        "SK": f"RUN#{started_at}",
        "stage": "hierarchy_version_cutover",
        "scope": "GLOBAL",
        "status": "complete",
        "input_hash": "h",
        "migrated_rotation_count": migrated,
        "orphan_count": orphan,
    }


def test_update_cutover_counts_writes_latest_row():
    table = FakeTable(
        [_cutover_row("2026-06-01T00:00:00Z"), _cutover_row("2026-06-10T00:00:00Z")]
    )
    sk = update_cutover_counts(table, migrated=7, orphan=2)
    assert sk == "RUN#2026-06-10T00:00:00Z"  # latest by SK
    latest = table.items[("STAGE#hierarchy_version_cutover#GLOBAL", "RUN#2026-06-10T00:00:00Z")]
    assert latest["migrated_rotation_count"] == 7 and latest["orphan_count"] == 2
    # the older row is untouched
    older = table.items[("STAGE#hierarchy_version_cutover#GLOBAL", "RUN#2026-06-01T00:00:00Z")]
    assert older["migrated_rotation_count"] == 0


def test_update_cutover_counts_no_row_returns_none():
    assert update_cutover_counts(FakeTable([]), migrated=3, orphan=0) is None

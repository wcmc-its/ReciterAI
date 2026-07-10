"""Tests for the one-shot spotlight-history version fold.

The bug being repaired: history was keyed by publish date, so each publish wrote
a fresh partition and every row read `shown_count == 1` no matter how often the
subtopic was featured. The fold must recover the true count and the most recent
`last_shown_at`, since those two drive the rotation decay.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from scripts.fold_spotlight_history_versions import (
    build_folded_item,
    classify_history_rows,
    fold_history,
    parse_history_pk,
)


def _row(pk: str, count: int = 1, last: str | None = None, pid: str | None = None) -> dict:
    item: dict = {"PK": pk, "SK": "STATE", "shown_count": count}
    if last:
        item["last_shown_at"] = last
    if pid:
        item["last_shown_publish_id"] = pid
    return item


# ---------- PK parsing ----------


def test_parse_versioned_and_unversioned_and_malformed():
    assert parse_history_pk("SPOTLIGHT_HISTORY#v2026-06-13#aging_one") == ("v2026-06-13", "aging_one")
    assert parse_history_pk("SPOTLIGHT_HISTORY#aging_one") == (None, "aging_one")
    assert parse_history_pk("SPOTLIGHT_HISTORY#st_ab12") == (None, "st_ab12")
    assert parse_history_pk("TOPIC#cardio") is None
    assert parse_history_pk("SPOTLIGHT_HISTORY#") is None


def test_a_subtopic_id_is_never_mistaken_for_a_version():
    # Only the v{YYYY-MM-DD} shape counts as a version segment.
    assert parse_history_pk("SPOTLIGHT_HISTORY#vaccine_hesitancy") == (None, "vaccine_hesitancy")


# ---------- the fold ----------


def test_repeat_featured_subtopic_recovers_summed_count_and_newest_stamp():
    rows = [
        _row("SPOTLIGHT_HISTORY#v2026-05-07#sig", 1, "2026-05-07T10:00:00Z", "v2026-05-07"),
        _row("SPOTLIGHT_HISTORY#v2026-06-15#sig", 1, "2026-06-15T10:00:00Z", "v2026-06-15"),
        _row("SPOTLIGHT_HISTORY#v2026-06-10#sig", 1, "2026-06-10T10:00:00Z", "v2026-06-10"),
    ]
    c = classify_history_rows(rows)

    assert len(c.plans) == 1
    plan = c.plans[0]
    assert plan.subtopic_id == "sig"
    assert plan.target_pk == "SPOTLIGHT_HISTORY#sig"
    assert plan.shown_count == 3  # the count the buggy writer could never accumulate
    assert plan.last_shown_at == "2026-06-15T10:00:00Z"  # MAX, not last-scanned
    assert plan.last_shown_publish_id == "v2026-06-15"
    assert len(plan.source_pks) == 3


def test_an_already_folded_row_is_left_alone():
    c = classify_history_rows([_row("SPOTLIGHT_HISTORY#sig", 4, "2026-06-15T10:00:00Z")])
    assert c.plans == []
    assert c.already_folded == 1


def test_rerun_folds_an_unversioned_row_together_with_a_late_versioned_sibling():
    """Idempotent convergence: a partial prior run leaves both shapes present."""
    rows = [
        _row("SPOTLIGHT_HISTORY#sig", 2, "2026-06-10T10:00:00Z", "v2026-06-10"),
        _row("SPOTLIGHT_HISTORY#v2026-06-15#sig", 1, "2026-06-15T10:00:00Z", "v2026-06-15"),
    ]
    c = classify_history_rows(rows)

    assert len(c.plans) == 1
    plan = c.plans[0]
    assert plan.shown_count == 3
    assert plan.last_shown_at == "2026-06-15T10:00:00Z"
    # only the versioned row is deleted; the folded target is overwritten in place
    assert plan.source_pks == ["SPOTLIGHT_HISTORY#v2026-06-15#sig"]


def test_row_without_last_shown_at_still_contributes_to_the_count():
    rows = [
        _row("SPOTLIGHT_HISTORY#v2026-05-07#sig", 1),
        _row("SPOTLIGHT_HISTORY#v2026-06-15#sig", 1, "2026-06-15T10:00:00Z", "v2026-06-15"),
    ]
    plan = classify_history_rows(rows).plans[0]
    assert plan.shown_count == 2
    assert plan.last_shown_at == "2026-06-15T10:00:00Z"


def test_malformed_pk_is_reported_not_guessed():
    c = classify_history_rows([_row("SPOTLIGHT_HISTORY#", 1)])
    assert c.malformed == ["SPOTLIGHT_HISTORY#"]
    assert c.plans == []


def test_non_state_rows_are_ignored():
    c = classify_history_rows([{"PK": "SPOTLIGHT_HISTORY#v2026-06-15#sig", "SK": "AUDIT"}])
    assert c.plans == [] and c.malformed == []


def test_folded_item_carries_provenance():
    plan = classify_history_rows(
        [_row("SPOTLIGHT_HISTORY#v2026-06-15#sig", 1, "2026-06-15T10:00:00Z", "v2026-06-15")]
    ).plans[0]
    item = build_folded_item(plan)
    assert item["PK"] == "SPOTLIGHT_HISTORY#sig"
    assert item["SK"] == "STATE"
    assert item["shown_count"] == 1
    assert item["folded_from_versions"] == 1


# ---------- write ordering ----------


def test_dry_run_writes_nothing():
    table = MagicMock()
    c = classify_history_rows([_row("SPOTLIGHT_HISTORY#v2026-06-15#sig", 1, "2026-06-15T10:00:00Z")])

    out = fold_history(table, c, dry_run=True)

    table.batch_writer.assert_not_called()
    assert out["folded"] == 0 and out["dry_run"] is True


def test_every_put_precedes_every_delete():
    """Crash between phases must never leave a deleted source with no successor."""
    calls: list[str] = []
    writer = MagicMock()
    writer.put_item.side_effect = lambda **_kw: calls.append("put")
    writer.delete_item.side_effect = lambda **_kw: calls.append("delete")

    table = MagicMock()
    table.batch_writer.return_value.__enter__.return_value = writer

    c = classify_history_rows([
        _row("SPOTLIGHT_HISTORY#v2026-05-07#a", 1, "2026-05-07T10:00:00Z"),
        _row("SPOTLIGHT_HISTORY#v2026-06-15#a", 1, "2026-06-15T10:00:00Z"),
        _row("SPOTLIGHT_HISTORY#v2026-06-15#b", 1, "2026-06-15T10:00:00Z"),
    ])
    out = fold_history(table, c, dry_run=False)

    assert out == {"folded": 2, "sources_deleted": 3, "dry_run": False}
    assert calls == ["put", "put", "delete", "delete", "delete"]

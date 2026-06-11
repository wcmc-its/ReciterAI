#!/usr/bin/env python3
"""One-shot cutover: re-key spotlight rotation history from slug ids to durable ids (#191 brick D).

``spotlight/history_writer.py`` writes rotation state under
``SPOTLIGHT_HISTORY#{hierarchy_version}#{subtopic_id}`` where ``subtopic_id`` is the
*slug* id of the published subtopic. Before durable ids (#191), an annual recompute
rotated those slugs, so the only way to keep history from going stale was to truncate
it wholesale (``backfill_spotlight.py --reset-history``) and lose every subtopic's
shown/last-shown state. The durable-id store (bricks A–C) ends that: a subtopic keeps
one opaque id across rebuilds, so rotation history *can* survive a relabel — once it is
keyed by the durable id instead of the volatile slug.

This script is that one-time re-key. For every ``SPOTLIGHT_HISTORY#{ver}#{slug}`` row it
resolves ``slug -> durable_id`` and writes ``SPOTLIGHT_HISTORY#{ver}#{durable}`` carrying
the same state attributes, then deletes the old slug-keyed row. After the cutover, an
annual recompute no longer needs ``--reset-history`` at all (that flag is now tombstoned),
because the durable key is stable. The operator runs this once during the
hierarchy-version cutover window.

Slug -> durable source — ``load_slug_pointer_map`` (the live ``SUBTOPIC_SLUG#`` pointer
rows), *not* the published ``aliases.json``. The pointer map is keyed by slug, so it still
resolves a slug a later reconcile relabeled (the original mint wrote that slug's pointer;
``_attach`` never rewrites it). ``aliases.json``/``build_alias_map`` projects the store by
each durable's *current* slug and would orphan a relabeled historical slug. For re-keying
historical rows, keying by slug is the correct lookup; the pointer rows are the same store
the alias map is built from, so the durable ids agree.

Safety / correctness:
- **Two-phase, crash-safe.** Phase A puts every durable row (``batch_writer`` flushes at
  block exit, so all puts are durable); Phase B then deletes the superseded slug rows.
  An interruption between phases leaves *both* rows — never a slug deleted with no durable
  successor — and a re-run reconciles it. Data is never lost to a partial run.
- **Idempotent / resumable.** Re-running is safe: rows already keyed by a durable id are
  recognised (their third PK component is a known durable id) and skipped; only remaining
  slug-keyed rows are migrated. No checkpoint file — the table state *is* the checkpoint.
- **Orphan-safe.** A slug with no durable id in the store (e.g. its version's reconcile
  hasn't populated the store yet) is left **untouched** and counted, never deleted. Run the
  durable reconcile first, then re-run to pick orphans up.
- **Fail-loud on collision.** If two distinct slugs would re-key onto the same durable PK
  (impossible within one published version — slug->durable is 1:1 there — so it implies a
  corrupt store or cross-version clash), the script refuses rather than silently overwrite
  one row's ``shown_count``.
- **Disjoint-namespace guard.** Slugs match ``^[a-z0-9_]+$`` and durable ids
  ``^st_[a-z0-9]+$``; the script asserts the slug-key and durable-id sets never intersect,
  so the already-migrated-vs-migrate decision is unambiguous.

Audit: on a real run that migrated rows, the migrated/orphan counts are back-written onto
the latest ``STAGE#hierarchy_version_cutover#GLOBAL`` row (the 0/0 placeholder
``pipeline_cold/run.py`` writes at cold-run end). Best-effort — a missing audit row never
fails a completed data migration. The machine-readable JSON printed to stdout is the
authoritative per-run record.

Usage:
  scripts/migrate_spotlight_history_pk.py --dry-run     # scan + classify, write nothing
  scripts/migrate_spotlight_history_pk.py               # migrate + back-write cutover counts
  scripts/migrate_spotlight_history_pk.py --no-cutover-update

Env: RECITERAI_TABLE (default "reciterai"), AWS_REGION (default "us-east-1").
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# Repo root on sys.path so ``pipeline_hierarchy.*`` / ``utils.*`` resolve whether the
# script is run directly or via ``PYTHONPATH=<repo-root> -m`` (operator-script convention).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from pipeline_hierarchy.subtopic_id_store import load_slug_pointer_map  # noqa: E402

_log = logging.getLogger("migrate_spotlight_history_pk")

TABLE = os.environ.get("RECITERAI_TABLE", "reciterai")
REGION = os.environ.get("AWS_REGION", "us-east-1")

HISTORY_PK_PREFIX = "SPOTLIGHT_HISTORY#"
HISTORY_SK = "STATE"
CUTOVER_STAGE = "hierarchy_version_cutover"
CUTOVER_SCOPE = "GLOBAL"


@dataclass(frozen=True)
class RekeyPlan:
    """One slug-keyed history row that resolves to a durable id."""

    version: str
    slug: str
    durable_id: str
    source_pk: str
    target_pk: str
    item: dict  # the source row verbatim — its state attrs ride onto the durable row


@dataclass
class Classification:
    """Bucketed result of one classification pass (pure; see classify_history_rows)."""

    to_migrate: list[RekeyPlan] = field(default_factory=list)
    orphans: list[tuple[str, str]] = field(default_factory=list)  # (version, slug)
    already_migrated: int = 0
    malformed: list[str] = field(default_factory=list)  # raw PKs that did not parse
    collisions: dict[str, list[str]] = field(default_factory=dict)  # target_pk -> [source_pk]


def _parse_history_pk(pk: str) -> Optional[tuple[str, str]]:
    """``SPOTLIGHT_HISTORY#{version}#{ident}`` -> ``(version, ident)`` or ``None``.

    ``maxsplit=2``: neither the version (``v2026-..`` dates) nor the third component
    (slug ``^[a-z0-9_]+$`` or durable ``^st_[a-z0-9]+$``) contains ``#``, so a
    well-formed key yields exactly three parts and the third is returned intact.
    """
    parts = pk.split("#", 2)
    if len(parts) != 3 or parts[0] != "SPOTLIGHT_HISTORY" or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]


def classify_history_rows(
    history_items: list[dict], slug_to_durable: dict[str, str]
) -> Classification:
    """Pure: bucket each ``SPOTLIGHT_HISTORY#`` row for the re-key. No I/O.

    ``durable_set`` is the pointer map's *values*: every minted durable id has a slug
    pointer (``_mint`` writes META + pointer; ``_attach`` rewrites neither id), so the
    values are the complete durable-id universe — enough to recognise already-migrated,
    durable-keyed rows on a re-run. The disjoint-namespace assert (slug keys vs durable
    values) fails loud if a single id ever lands in both, which would make the
    already-migrated-vs-migrate decision ambiguous.
    """
    durable_set = set(slug_to_durable.values())
    overlap = set(slug_to_durable) & durable_set
    if overlap:
        raise ValueError(
            "slug ids and durable ids overlap (namespaces must stay disjoint): "
            f"{sorted(overlap)[:10]}"
        )

    c = Classification()
    target_sources: dict[str, list[str]] = {}
    for item in history_items:
        if item.get("SK") != HISTORY_SK:
            continue  # only STATE rows — forward-safe vs any future SK on this PK
        pk = item.get("PK", "")
        parsed = _parse_history_pk(pk)
        if parsed is None:
            c.malformed.append(pk)
            continue
        version, ident = parsed
        if ident in durable_set:
            c.already_migrated += 1  # already durable-keyed: idempotent re-run skip
            continue
        durable = slug_to_durable.get(ident)
        if durable is None:
            c.orphans.append((version, ident))  # unmapped slug: leave untouched
            continue
        target_pk = f"{HISTORY_PK_PREFIX}{version}#{durable}"
        c.to_migrate.append(
            RekeyPlan(
                version=version,
                slug=ident,
                durable_id=durable,
                source_pk=pk,
                target_pk=target_pk,
                item=item,
            )
        )
        target_sources.setdefault(target_pk, []).append(pk)

    c.collisions = {t: srcs for t, srcs in target_sources.items() if len(srcs) > 1}
    return c


def _scan_history_rows(table: Any) -> list[dict]:
    """Paginated Scan of every ``SPOTLIGHT_HISTORY#`` row (load_id_store_snapshot idiom)."""
    from boto3.dynamodb.conditions import Attr

    items: list[dict] = []
    scan_kwargs = {"FilterExpression": Attr("PK").begins_with(HISTORY_PK_PREFIX)}
    while True:
        resp = table.scan(**scan_kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return items


def build_durable_item(plan: RekeyPlan, *, migrated_at: str) -> dict:
    """The durable-keyed row: source attrs verbatim, PK swapped, provenance stamped."""
    new_item = dict(plan.item)
    new_item["PK"] = plan.target_pk
    new_item["SK"] = HISTORY_SK
    new_item["migrated_from_slug"] = plan.slug
    new_item["migrated_at"] = migrated_at
    return new_item


def migrate_history(
    table: Any,
    *,
    slug_to_durable: dict[str, str],
    dry_run: bool,
    now_fn: Callable[[], str],
) -> dict[str, Any]:
    """Scan, classify, and (unless ``dry_run``) two-phase re-key. Returns a summary dict."""
    history = _scan_history_rows(table)
    c = classify_history_rows(history, slug_to_durable)

    if c.collisions:
        raise ValueError(
            f"target-PK collisions ({len(c.collisions)}); refusing to migrate "
            f"(distinct slugs map to one durable PK): "
            f"{dict(list(c.collisions.items())[:5])}"
        )

    summary: dict[str, Any] = {
        "event": "spotlight_history_rekey",
        "dry_run": dry_run,
        "history_rows_scanned": len(history),
        "migrated_rotation_count": len(c.to_migrate),
        "orphan_count": len(c.orphans),
        "already_migrated": c.already_migrated,
        "malformed_skipped": len(c.malformed),
        "distinct_versions_migrated": sorted({p.version for p in c.to_migrate}),
        "orphan_sample": [f"{v}#{s}" for v, s in c.orphans[:10]],
    }
    if c.malformed:
        summary["malformed_sample"] = c.malformed[:10]

    if dry_run or not c.to_migrate:
        return summary

    migrated_at = now_fn()
    # Phase A — put every durable row. batch_writer flushes buffered items at block
    # exit, so ALL puts are durable before Phase B deletes anything.
    with table.batch_writer() as bw:
        for p in c.to_migrate:
            bw.put_item(Item=build_durable_item(p, migrated_at=migrated_at))
    # Phase B — delete the now-superseded slug rows.
    with table.batch_writer() as bw:
        for p in c.to_migrate:
            bw.delete_item(Key={"PK": p.source_pk, "SK": HISTORY_SK})
    return summary


def update_cutover_counts(table: Any, *, migrated: int, orphan: int) -> Optional[str]:
    """Best-effort back-write of migrated/orphan counts onto the latest cutover row.

    Targets the most recent ``STAGE#hierarchy_version_cutover#GLOBAL`` row — the 0/0
    placeholder ``pipeline_cold/run.py`` writes at cold-run end. ``SET`` (not ``ADD``):
    the count reflects the run that did the migrating, and the caller gates this on
    ``migrated > 0`` so a no-op re-run never clobbers a recorded count with 0. Returns the
    SK updated, or ``None`` (no row / failure). Never raises — a missing audit row must not
    fail a completed data migration; stdout JSON is the authoritative per-run record.
    """
    from utils.stage_records import find_latest_complete

    try:
        row = find_latest_complete(table, stage=CUTOVER_STAGE, scope=CUTOVER_SCOPE)
        if row is None:
            _log.warning("no %s cutover row found; counts not back-written", CUTOVER_STAGE)
            return None
        table.update_item(
            Key={"PK": row["PK"], "SK": row["SK"]},
            UpdateExpression="SET migrated_rotation_count = :m, orphan_count = :o",
            ExpressionAttributeValues={":m": migrated, ":o": orphan},
        )
        return row["SK"]
    except Exception as exc:  # noqa: BLE001 — best-effort audit; never fail the migration
        _log.warning("cutover-row count back-write failed (migration unaffected): %s", exc)
        return None


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="scan + classify + report; write nothing"
    )
    ap.add_argument(
        "--no-cutover-update",
        action="store_true",
        help="skip back-writing migrated/orphan counts onto the cutover STAGE# row",
    )
    ap.add_argument("--verbose", action="store_true", help="DEBUG-level logging")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    import boto3
    from utils.iso_clock import now_iso

    table = boto3.resource("dynamodb", region_name=REGION).Table(TABLE)

    slug_to_durable = load_slug_pointer_map(table)
    _log.info(
        "loaded %d slug->durable pointers from %s (%s)", len(slug_to_durable), TABLE, REGION
    )

    summary = migrate_history(
        table, slug_to_durable=slug_to_durable, dry_run=args.dry_run, now_fn=now_iso
    )
    summary["table"] = TABLE
    summary["region"] = REGION

    if (
        not args.dry_run
        and summary["migrated_rotation_count"]
        and not args.no_cutover_update
    ):
        summary["cutover_row_updated"] = update_cutover_counts(
            table,
            migrated=summary["migrated_rotation_count"],
            orphan=summary["orphan_count"],
        )

    if summary["orphan_count"]:
        _log.warning(
            "%d orphan history rows left untouched (slug has no durable id — run the "
            "durable reconcile for those versions, then re-run): %s",
            summary["orphan_count"],
            summary["orphan_sample"],
        )

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

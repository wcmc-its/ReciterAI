#!/usr/bin/env python3
"""One-shot fold: collapse per-publish spotlight history partitions into one row per subtopic.

``spotlight/history_writer.py`` used to write rotation state under
``SPOTLIGHT_HISTORY#{hierarchy_version}#{subtopic_id}``, where the "version" was
``v{date.today()}`` — the *publish date*. Every publish therefore wrote into a
fresh partition, and ``rotation_selector.fetch_history`` read a partition that
was empty by construction: ``last_shown_at`` came back ``None`` for every
subtopic, the SPOT-03 exponential decay multiplier stayed at its cold-start
1.0, and the same top-pool subtopics were re-featured every publish. The
fingerprint in the live table is that *every* row reads ``shown_count == 1``
no matter how many publishes featured that subtopic.

The writer/reader now key on ``SPOTLIGHT_HISTORY#{subtopic_id}`` (durable
subtopic ids, #191, make the version segment both unnecessary and harmful).
This script folds the historical date partitions onto that key so the fixed
rotation starts from real history rather than a cold start:

    shown_count           -> SUM over the folded rows (the true feature count)
    last_shown_at         -> MAX (most recent publish wins; drives the decay)
    last_shown_publish_id -> the id belonging to that MAX row

Run this BEFORE ``scripts/migrate_spotlight_history_pk.py`` (the slug->durable
re-key). Folding first guarantees exactly one row per subtopic id, which is
what that script's 1:1 collision guard assumes.

Safety / correctness:
- **Two-phase, crash-safe.** Phase A puts every folded row; Phase B then deletes
  the superseded versioned rows. An interruption between phases leaves both the
  folded row and its sources — never a source deleted with no successor — and a
  re-run reconciles it.
- **Idempotent.** An already-folded (unversioned) row is recognised and carried
  into the fold as just another input, so re-running converges. Re-running with
  no versioned rows left is a no-op.
- **Fail-loud on malformed PKs.** Anything that is not
  ``SPOTLIGHT_HISTORY#{version}#{ident}`` or ``SPOTLIGHT_HISTORY#{ident}`` is
  counted and reported, never guessed at.

Usage:
  scripts/fold_spotlight_history_versions.py --dry-run   # scan + plan, write nothing
  scripts/fold_spotlight_history_versions.py             # fold, then delete sources

Env: RECITERAI_TABLE (default "reciterai"), AWS_REGION (default "us-east-1").
"""
from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

HISTORY_PK_PREFIX = "SPOTLIGHT_HISTORY#"
HISTORY_SK = "STATE"

# A publish/hierarchy version segment: "v2026-06-13". A subtopic id never
# starts with this shape (slugs are ^[a-z0-9_]+$, durables ^st_[a-z0-9]+$),
# so the two PK shapes are unambiguous.
_VERSION_RE = re.compile(r"^v\d{4}-\d{2}-\d{2}$")


@dataclass
class FoldPlan:
    subtopic_id: str
    target_pk: str
    shown_count: int
    last_shown_at: Optional[str]
    last_shown_publish_id: Optional[str]
    source_pks: list[str]  # versioned rows this fold supersedes (may be empty)


@dataclass
class Classification:
    plans: list[FoldPlan] = field(default_factory=list)
    malformed: list[str] = field(default_factory=list)
    already_folded: int = 0  # unversioned rows with no versioned siblings


def parse_history_pk(pk: str) -> Optional[tuple[Optional[str], str]]:
    """``SPOTLIGHT_HISTORY#[{version}#]{ident}`` -> ``(version|None, ident)``.

    Returns ``None`` for anything that does not parse. The version segment is
    recognised by shape, so a subtopic id is never mistaken for a version.
    """
    if not pk.startswith(HISTORY_PK_PREFIX):
        return None
    rest = pk[len(HISTORY_PK_PREFIX):]
    if not rest:
        return None
    head, sep, tail = rest.partition("#")
    if sep and _VERSION_RE.match(head):
        return (head, tail) if tail else None
    return None, rest


def _to_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def classify_history_rows(history_items: list[dict]) -> Classification:
    """Pure: group ``SPOTLIGHT_HISTORY#`` rows by subtopic id and plan the fold. No I/O."""
    c = Classification()
    by_ident: dict[str, list[tuple[Optional[str], dict]]] = {}

    for item in history_items:
        if item.get("SK") != HISTORY_SK:
            continue  # only STATE rows
        pk = item.get("PK", "")
        parsed = parse_history_pk(pk)
        if parsed is None:
            c.malformed.append(pk)
            continue
        version, ident = parsed
        by_ident.setdefault(ident, []).append((version, item))

    for ident, rows in sorted(by_ident.items()):
        versioned = [(v, it) for v, it in rows if v is not None]
        if not versioned:
            c.already_folded += 1
            continue

        shown_count = sum(_to_int(it.get("shown_count")) for _v, it in rows)
        # MAX by last_shown_at; rows without one can still contribute to the count.
        stamped = [it for _v, it in rows if it.get("last_shown_at")]
        newest = max(stamped, key=lambda it: str(it["last_shown_at"])) if stamped else None

        c.plans.append(
            FoldPlan(
                subtopic_id=ident,
                target_pk=f"{HISTORY_PK_PREFIX}{ident}",
                shown_count=shown_count,
                last_shown_at=str(newest["last_shown_at"]) if newest else None,
                last_shown_publish_id=(
                    str(newest.get("last_shown_publish_id")) if newest and newest.get("last_shown_publish_id") else None
                ),
                source_pks=[it.get("PK", "") for _v, it in versioned],
            )
        )
    return c


def build_folded_item(plan: FoldPlan) -> dict:
    """The row written to the unversioned PK."""
    item: dict[str, Any] = {
        "PK": plan.target_pk,
        "SK": HISTORY_SK,
        "shown_count": plan.shown_count,
        "folded_from_versions": len(plan.source_pks),
    }
    if plan.last_shown_at:
        item["last_shown_at"] = plan.last_shown_at
    if plan.last_shown_publish_id:
        item["last_shown_publish_id"] = plan.last_shown_publish_id
    return item


def _scan_history_rows(table: Any) -> list[dict]:
    from boto3.dynamodb.conditions import Attr

    items: list[dict] = []
    kwargs: dict[str, Any] = {"FilterExpression": Attr("PK").begins_with(HISTORY_PK_PREFIX)}
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        last = resp.get("LastEvaluatedKey")
        if not last:
            return items
        kwargs["ExclusiveStartKey"] = last


def fold_history(table: Any, classification: Classification, *, dry_run: bool) -> dict:
    """Two-phase write: put every folded row, flush, then delete the sources."""
    plans = classification.plans
    if dry_run or not plans:
        return {
            "folded": 0 if dry_run else len(plans),
            "sources_deleted": 0,
            "dry_run": dry_run,
        }

    # Phase A — put folded rows. batch_writer flushes at block exit.
    with table.batch_writer() as bw:
        for plan in plans:
            bw.put_item(Item=build_folded_item(plan))

    # Phase B — only now remove the superseded versioned rows.
    deleted = 0
    with table.batch_writer() as bw:
        for plan in plans:
            for pk in plan.source_pks:
                bw.delete_item(Key={"PK": pk, "SK": HISTORY_SK})
                deleted += 1

    return {"folded": len(plans), "sources_deleted": deleted, "dry_run": False}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="scan + classify, write nothing")
    args = ap.parse_args()

    import boto3

    table_name = os.environ.get("RECITERAI_TABLE", "reciterai")
    region = os.environ.get("AWS_REGION", "us-east-1")
    table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    rows = _scan_history_rows(table)
    c = classify_history_rows(rows)
    result = fold_history(table, c, dry_run=args.dry_run)

    summary = {
        **result,
        "table": table_name,
        "rows_scanned": len(rows),
        "subtopics_to_fold": len(c.plans),
        "already_folded": c.already_folded,
        "malformed": c.malformed,
        "recovered_repeat_features": sum(1 for p in c.plans if p.shown_count > 1),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 1 if c.malformed else 0


if __name__ == "__main__":
    raise SystemExit(main())

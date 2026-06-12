#!/usr/bin/env python3
"""Shadow-validate durable_id propagation across the 3 SPS-consumer surfaces (#191 brick D3, §7).

Brick D3 stamps the durable subtopic id additively onto three artifacts behind the
``propagate_durable_ids`` gate: ``hierarchy.json`` (``subtopics[].durable_id``),
the ``TOPIC#`` activity rows (``primary_subtopic_durable_id``), and ``spotlight.json``
(``spotlights[].durable_id`` / ``pool_snapshot[].durable_id``). The SPS deep-link join
keys on ``TOPIC#.primary_subtopic_id``, so the *same slug must resolve to the same
durable id on every surface* or the join breaks.

There is no single process that sees more than one surface (the cold run walks 7
subprocesses), so this consistency cannot be an in-process assertion — it has to be a
post-run, cross-artifact check. This is that check (plan §7): for every slug that carries
a durable companion on one or more surfaces, compare the durable id across the surfaces
and against the durable-ID store (the source of truth), and flag any disagreement.

It is **read-only** and degrades gracefully: while the durable-ID store is empty (no
``--publish`` has run the reconcile yet) or the gate is off, no surface carries a durable
companion, so there is simply nothing to reconcile — the tool reports the inert state and
exits 0. Run it during shadow validation, after flipping ``propagate_durable_ids`` on and
running a publish, BEFORE flipping the prod flag.

Checks performed
- cross-surface agreement: a slug present on >=2 surfaces must map to one durable id;
- store fidelity: every stamped durable must equal the store's {slug -> durable} (the
  per-producer invariant, post-hoc — a surface only ever stamps a durable it inverted
  from the store, so a disagreement or an absent-from-store stamp is an anomaly);
- intra-artifact conflicts: the same slug mapping to two durables *within* one artifact;
- coverage: how many slugs carry a durable companion per surface (one-run-lag visibility).

Exit code: 0 when consistent (or nothing to reconcile), 1 when any mismatch/conflict.

Usage:
  scripts/validate_durable_id_propagation.py                       # fetch latest from S3 + live store + TOPIC# sample
  scripts/validate_durable_id_propagation.py --hierarchy out/hierarchy/v.../hierarchy.json \
      --spotlight out/spotlight-2026-06-11.json                    # validate local artifacts
  scripts/validate_durable_id_propagation.py --no-store --topic-sample 0   # cross-artifact only (hierarchy vs spotlight)

Env: RECITERAI_TABLE (default "reciterai"), AWS_REGION (default "us-east-1").
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

# Repo root on sys.path so ``pipeline_hierarchy.*`` / ``utils.*`` resolve whether the
# script is run directly or via ``PYTHONPATH=<repo-root> -m`` (operator-script convention).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_log = logging.getLogger("validate_durable_id_propagation")

TABLE = os.environ.get("RECITERAI_TABLE", "reciterai")
REGION = os.environ.get("AWS_REGION", "us-east-1")

SURFACE_HIERARCHY = "hierarchy"
SURFACE_SPOTLIGHT = "spotlight"
SURFACE_TOPIC = "topic"


# --------------------------------------------------------------------------- #
# Pure extractors — parse each artifact/shape into {slug -> durable_id}.
# Only slugs that carry a *truthy* durable companion are included; a slug with
# no durable yet (one-run lag) simply never appears (it is not a null entry).
# --------------------------------------------------------------------------- #


def extract_hierarchy_durable_map(hierarchy: dict) -> dict[str, str]:
    """{subtopic slug id -> durable_id} from a hierarchy.json dict."""
    out: dict[str, str] = {}
    for topic in (hierarchy.get("topics") or {}).values():
        for sub in topic.get("subtopics", []) or []:
            sid = sub.get("id")
            durable = sub.get("durable_id")
            if sid and durable:
                out[sid] = durable
    return out


def extract_spotlight_durable_map(spotlight: dict) -> tuple[dict[str, str], list[dict]]:
    """{subtopic_id -> durable_id} from spotlight.json, plus intra-artifact conflicts.

    Both ``spotlights[]`` and ``pool_snapshot[]`` carry the companion; build_artifact
    stamps both from the same map, so a slug appearing in both with *different* durables
    would be an internal inconsistency and is reported.
    """
    out: dict[str, str] = {}
    conflicts: list[dict] = []
    for section in ("spotlights", "pool_snapshot"):
        for entry in spotlight.get(section, []) or []:
            sid = entry.get("subtopic_id")
            durable = entry.get("durable_id")
            if not (sid and durable):
                continue
            if sid in out and out[sid] != durable:
                conflicts.append({"subtopic_id": sid, "first": out[sid], "second": durable})
            else:
                out[sid] = durable
    return out, conflicts


def extract_topic_durable_map(rows: Iterable[dict]) -> tuple[dict[str, str], list[dict]]:
    """{primary_subtopic_id -> primary_subtopic_durable_id} from TOPIC# activity rows.

    Many rows share a primary slug (one row per author); they must all carry the same
    durable. A divergence across rows is reported as a conflict.
    """
    out: dict[str, str] = {}
    conflicts: list[dict] = []
    for row in rows:
        sid = row.get("primary_subtopic_id")
        durable = row.get("primary_subtopic_durable_id")
        if not (sid and durable):
            continue
        if sid in out and out[sid] != durable:
            conflicts.append({"primary_subtopic_id": sid, "first": out[sid], "second": durable})
        else:
            out[sid] = durable
    return out, conflicts


# --------------------------------------------------------------------------- #
# Pure reconcile core.
# --------------------------------------------------------------------------- #


@dataclass
class ReconciliationReport:
    surfaces_present: list[str]
    coverage: dict[str, int]
    slugs_checked: int
    consistent: int
    mismatches: list[dict]
    intra_artifact_conflicts: dict[str, list]
    store_compared: bool
    store_size: int

    @property
    def ok(self) -> bool:
        return not self.mismatches and not any(self.intra_artifact_conflicts.values())

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "surfaces_present": self.surfaces_present,
            "coverage": self.coverage,
            "slugs_checked": self.slugs_checked,
            "consistent": self.consistent,
            "mismatch_count": len(self.mismatches),
            "mismatches": self.mismatches,
            "intra_artifact_conflicts": {
                k: v for k, v in self.intra_artifact_conflicts.items() if v
            },
            "store_compared": self.store_compared,
            "store_size": self.store_size,
        }


def reconcile_durable_ids(
    surface_maps: dict[str, dict[str, str]],
    store_map: Optional[dict[str, str]] = None,
    *,
    intra_conflicts: Optional[dict[str, list]] = None,
) -> ReconciliationReport:
    """Compare durable ids across surfaces (and the store, if given).

    Args:
        surface_maps: {surface_name -> {slug -> durable_id}}. Only slugs that carry a
            durable companion are present.
        store_map: {slug -> durable_id} inverted from the durable-ID store snapshot — the
            source of truth. ``None`` skips the store-fidelity check (cross-artifact only).
        intra_conflicts: {surface_name -> [conflict, ...]} from the extractors, surfaced
            verbatim in the report (any non-empty list makes the run not-ok).

    A slug is a mismatch when the surfaces disagree, when a stamped durable disagrees with
    the store, or when a stamped slug is absent from a non-empty store (a fidelity gap —
    a surface only ever stamps a durable it inverted from the store).
    """
    coverage = {surface: len(m) for surface, m in surface_maps.items()}
    surfaces_present = [surface for surface, m in surface_maps.items() if m]

    all_slugs: set[str] = set()
    for m in surface_maps.values():
        all_slugs |= set(m)

    mismatches: list[dict] = []
    consistent = 0
    store_nonempty = bool(store_map)

    for slug in sorted(all_slugs):
        by_surface = {
            surface: m[slug] for surface, m in surface_maps.items() if slug in m
        }
        values = set(by_surface.values())
        reasons: list[str] = []

        if len(values) > 1:
            reasons.append("cross_surface_disagreement")

        store_durable = store_map.get(slug) if store_map is not None else None
        if store_map is not None:
            if store_durable is not None and any(v != store_durable for v in values):
                reasons.append("store_disagrees")
            elif store_durable is None and store_nonempty:
                reasons.append("absent_from_store")

        if reasons:
            entry: dict[str, Any] = {
                "slug": slug,
                "by_surface": by_surface,
                "reasons": reasons,
            }
            if store_map is not None:
                entry["store"] = store_durable
            mismatches.append(entry)
        else:
            consistent += 1

    return ReconciliationReport(
        surfaces_present=surfaces_present,
        coverage=coverage,
        slugs_checked=len(all_slugs),
        consistent=consistent,
        mismatches=mismatches,
        intra_artifact_conflicts=intra_conflicts or {},
        store_compared=store_map is not None,
        store_size=len(store_map) if store_map is not None else 0,
    )


# --------------------------------------------------------------------------- #
# I/O shell — load the real artifacts / store / TOPIC# sample.
# --------------------------------------------------------------------------- #


def _load_json_file(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_hierarchy_from_s3(s3_client: Any = None) -> dict:
    """Fetch the latest published hierarchy.json from the hierarchy bucket."""
    from utils.s3_client import HIERARCHY_BUCKET, S3HierarchyClient

    s3 = s3_client or S3HierarchyClient(bucket=HIERARCHY_BUCKET)
    manifest = json.loads(s3.get_object_bytes("latest/manifest.json"))
    version = manifest["version"]
    return json.loads(s3.get_object_bytes(f"{version}/hierarchy.json"))


def load_spotlight_from_s3(s3_client: Any = None) -> dict:
    """Fetch the latest published spotlight.json from the artifacts bucket."""
    from utils.s3_client import ARTIFACTS_BUCKET, S3HierarchyClient

    s3 = s3_client or S3HierarchyClient(bucket=ARTIFACTS_BUCKET)
    return json.loads(s3.get_object_bytes("spotlight/latest/spotlight.json"))


def load_store_map(table: Any) -> dict[str, str]:
    """Invert the durable-ID store snapshot to {slug -> durable_id} (the source of truth)."""
    from pipeline_hierarchy.subtopic_reconcile import load_id_store_snapshot

    snapshot = load_id_store_snapshot(table)
    return {
        row["slug_id"]: durable
        for durable, row in snapshot.items()
        if row.get("slug_id")
    }


def _load_topic_ids() -> list[str]:
    tax = _load_json_file(os.path.join(_REPO_ROOT, "taxonomy_v2.json"))
    return [t["id"] for t in tax.get("topics", []) if t.get("id")]


def scan_topic_durable_rows(
    table: Any, topic_ids: list[str], *, limit_per_topic: int
) -> list[dict]:
    """Sample TOPIC# activity rows' (primary_subtopic_id, primary_subtopic_durable_id).

    Bounded per partition: all rows under a topic that share a primary slug carry the same
    durable, so a small per-topic cap surfaces any disagreement without a full-table scan.
    """
    from boto3.dynamodb.conditions import Key

    rows: list[dict] = []
    for tid in topic_ids:
        resp = table.query(
            KeyConditionExpression=Key("PK").eq(f"TOPIC#{tid}")
            & Key("SK").begins_with("SCORE#"),
            ProjectionExpression="primary_subtopic_id, primary_subtopic_durable_id",
            Limit=limit_per_topic,
        )
        rows.extend(resp.get("Items", []))
    return rows


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--hierarchy",
        help="local hierarchy.json to validate (default: fetch latest from S3)",
    )
    ap.add_argument(
        "--spotlight",
        help="local spotlight.json to validate (default: fetch latest from S3)",
    )
    ap.add_argument(
        "--topic-sample",
        type=int,
        default=200,
        help="max TOPIC# rows to sample per topic partition (0 = skip the TOPIC# surface)",
    )
    ap.add_argument(
        "--no-store",
        action="store_true",
        help="skip the durable-ID store fidelity comparison (cross-artifact only)",
    )
    ap.add_argument("--verbose", action="store_true", help="DEBUG-level logging")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s"
    )

    hierarchy = (
        _load_json_file(args.hierarchy) if args.hierarchy else load_hierarchy_from_s3()
    )
    spotlight = (
        _load_json_file(args.spotlight) if args.spotlight else load_spotlight_from_s3()
    )

    h_map = extract_hierarchy_durable_map(hierarchy)
    s_map, s_conflicts = extract_spotlight_durable_map(spotlight)

    surface_maps: dict[str, dict[str, str]] = {
        SURFACE_HIERARCHY: h_map,
        SURFACE_SPOTLIGHT: s_map,
    }
    intra: dict[str, list] = {SURFACE_SPOTLIGHT: s_conflicts}
    store_map: Optional[dict[str, str]] = None

    need_table = args.topic_sample > 0 or not args.no_store
    if need_table:
        import boto3

        table = boto3.resource("dynamodb", region_name=REGION).Table(TABLE)
        if args.topic_sample > 0:
            rows = scan_topic_durable_rows(
                table, _load_topic_ids(), limit_per_topic=args.topic_sample
            )
            t_map, t_conflicts = extract_topic_durable_map(rows)
            surface_maps[SURFACE_TOPIC] = t_map
            intra[SURFACE_TOPIC] = t_conflicts
        if not args.no_store:
            store_map = load_store_map(table)

    report = reconcile_durable_ids(surface_maps, store_map, intra_conflicts=intra)
    print(
        json.dumps(
            {"event": "durable_id_shadow_reconciliation", "table": TABLE, **report.to_dict()},
            indent=2,
            default=str,
        )
    )
    if report.ok and report.slugs_checked == 0:
        _log.info(
            "No durable companions on any surface yet — nothing to reconcile "
            "(expected while the durable-ID store is empty or the gate is off)."
        )
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Collapse the #252 curated surface-form forks in the LIVE published artifact (operator override).

The #252 alias map (``config/method_alias_map.json``) is FORWARD prevention: at the
next corpus cold-run, re-extracted surface-form variants of one entity fold onto a
single canonical at ``ToolRegistry`` match-time. But the *already-published* artifact
still carries the historical forks (e.g. three "HMC-1" records). This CLI applies the
same merges POST-HOC to the published artifact set so the operator does not have to
wait for the (infrequent, expensive) cold-run.

There is no tool-level merge primitive in ``ToolRegistry`` (forks only ever collapse at
extraction match-time, which re-mints ids and would orphan every faculty score). So this
is a deliberate, surgical reconcile that preserves the DURABLE id of the kept record and
re-points every consumer of the dropped ids:

  tools[]                              keep accretes (display->canonical, aliases/context union,
                                       pub_count = |union of faculty pmids|); drops removed
  families[].member_tool_ids           drops removed (keep already a member)
  families[].exemplar_tool_ids         drop -> keep (dedup)
  hierarchy[sc][].{n_members,...}       affected family rows recomputed with the exact
                                       seed._build_hierarchy formula (other families untouched)
  faculty[cwid].tools[]                drop rows merged into the keep row (pmids unioned)
  faculty[cwid].families[].exemplar_tool_ids   drop -> keep (dedup)
  tool_context[cid]                    drop maps merged into keep (longer snippet per pmid wins)
  entities / entity_context            RE-PROJECTED from the mutated tools+tool_context
  tool_context_meta / manifest         re-derived by publish_artifacts

Keep = the LOWEST canonical_tool_id in the cluster (earliest minted = most durable, the id
faculty scores are most likely already keyed on). Cluster membership is the EXACT normalized
variant set from the alias map (zero fuzzy risk), so the keep-separate guard pairs can never
be pulled in. Dry-run by default; ``--publish`` writes to S3. Pure core is I/O-free + asserted.
"""

from __future__ import annotations

import argparse
import json
import logging
import os

from pipeline_tools import vocab
from pipeline_tools.entities import apply_parent_descriptors, build_entity_layer, load_generic_terms
from pipeline_tools.registry import load_method_alias_map, norm_name

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure logic (no I/O — asserted in __main__)
# ---------------------------------------------------------------------------


def _faculty_pmids_for(faculty: dict, cid: str) -> set[str]:
    """All pmids any faculty attributes to tool ``cid`` (the artifact's only pmid source)."""
    out: set[str] = set()
    for rec in faculty.values():
        for tr in rec.get("tools", []):
            if tr.get("canonical_tool_id") == cid:
                out.update(str(p) for p in (tr.get("pmids") or []))
    return out


def discover_clusters(tools: list[dict], merges: list[dict]) -> list[dict]:
    """Map each alias-map merge class to its live cluster (exact normalized variant match).

    Returns ``[{class, canonical, keep, drops, ids}]``. ``keep`` is the lowest
    canonical_tool_id in the cluster; ``drops`` the rest. Classes with <2 live
    records are skipped (nothing to collapse).
    """
    by_id = {t["canonical_tool_id"]: t for t in tools}
    clusters: list[dict] = []
    for m in merges:
        variants = {norm_name(v) for v in m.get("variants", []) if norm_name(v)}
        if not variants:
            continue
        hit_ids = sorted(
            cid for cid, t in by_id.items()
            if ({norm_name(t.get("display_name") or "")} | {norm_name(a) for a in t.get("aliases") or []})
            & variants
        )
        if len(hit_ids) < 2:
            continue
        clusters.append({
            "class": m.get("class"),
            "canonical": (m.get("canonical") or "").strip(),
            "keep": hit_ids[0],
            "drops": hit_ids[1:],
            "ids": hit_ids,
        })
    return clusters


def _recompute_family_row(fam: dict, by_id: dict[str, dict]) -> dict:
    """The seed._build_hierarchy row formula for one family over the mutated members."""
    members = [by_id[mid] for mid in fam["member_tool_ids"] if mid in by_id]
    non_c = sorted(
        (m for m in members if m.get("salience_tier") != vocab.DEMOTED_TIER),
        key=lambda m: -(int(m.get("pub_count") or 0)),
    )
    return {
        "n_members": len(members),
        "n_members_non_c": len(non_c),
        "pub_count": sum(int(m.get("pub_count") or 0) for m in non_c),
        "exemplars": [m["display_name"] for m in non_c[:3]],
    }


def _repoint_id_list(ids: list, drop_to_keep: dict[str, str]) -> list:
    """Replace dropped ids with their keep id, preserving order + deduping."""
    out: list = []
    for i in ids:
        r = drop_to_keep.get(i, i)
        if r not in out:
            out.append(r)
    return out


def collapse_forks(payload: dict, merges: list[dict], *, generic_terms: list[str] | None = None) -> dict:
    """Apply the alias-map merges to a published ``payload`` in place. Returns a report.

    ``payload`` is the tools.json bundle (tools/families/hierarchy/faculty/...) plus the
    split-out sidecars re-attached: ``tool_context`` / ``entities`` / ``entity_context``.
    """
    tools = payload["tools"]
    families = payload["families"]
    faculty = payload["faculty"]
    tool_context = payload["tool_context"]
    live_entities = payload.get("entities") or []

    clusters = discover_clusters(tools, merges)
    if not clusters:
        return {"clusters": [], "dropped": 0}

    by_id = {t["canonical_tool_id"]: t for t in tools}
    drop_to_keep: dict[str, str] = {}
    canon_display: dict[str, str] = {}
    report_rows = []
    for c in clusters:
        keep, drops = c["keep"], c["drops"]
        keep_rec = by_id[keep]
        canon = c["canonical"] or keep_rec["display_name"]
        canon_display[keep] = canon

        # pub_count = |union of faculty pmids across the whole cluster|. Assert each
        # record's institution pub_count is fully faculty-visible (remainder 0), else
        # the union under-counts hidden non-faculty pubs -> fail loud, operator decides.
        union_pmids: set[str] = set()
        for cid in c["ids"]:
            fp = _faculty_pmids_for(faculty, cid)
            rem = int(by_id[cid].get("pub_count") or 0) - len(fp)
            if rem > 0:
                raise SystemExit(
                    f"ABORT class {c['class']!r}: {cid} has pub_count {by_id[cid]['pub_count']} "
                    f"but only {len(fp)} faculty-visible pmids ({rem} hidden) — cannot recompute "
                    "a deduped merged count from the artifact; collapse this class via cold-run."
                )
            union_pmids |= fp

        # accrete onto keep
        alias_union, ctx_union = set(keep_rec.get("aliases") or []), list(keep_rec.get("context_evidence") or [])
        for d in drops:
            dr = by_id[d]
            alias_union |= set(dr.get("aliases") or []) | {dr.get("display_name")}
            for ce in dr.get("context_evidence") or []:
                if ce not in ctx_union:
                    ctx_union.append(ce)
            drop_to_keep[d] = keep
        alias_union.add(keep_rec["display_name"])
        keep_rec["display_name"] = canon
        keep_rec["aliases"] = sorted(a for a in alias_union if a and a.strip())
        keep_rec["context_evidence"] = ctx_union
        keep_rec["pub_count"] = len(union_pmids)
        report_rows.append({**{k: c[k] for k in ("class", "canonical", "keep", "drops")},
                            "merged_pub_count": len(union_pmids)})

    drops_all = set(drop_to_keep)

    # tools[] — drop the merged-away records
    payload["tools"] = [t for t in tools if t["canonical_tool_id"] not in drops_all]
    surviving = {t["canonical_tool_id"] for t in payload["tools"]}

    # families[] — remove drops from members, repoint exemplars, recompute affected hierarchy rows
    by_id_new = {t["canonical_tool_id"]: t for t in payload["tools"]}
    affected_fams: set[str] = set()
    for fam in families:
        before = list(fam.get("member_tool_ids") or [])
        kept = [m for m in before if m not in drops_all]
        if kept != before:
            affected_fams.add(fam["family_id"])
        fam["member_tool_ids"] = kept
        fam["exemplar_tool_ids"] = [
            e for e in _repoint_id_list(list(fam.get("exemplar_tool_ids") or []), drop_to_keep)
            if e in surviving
        ]
    fam_by_id = {f["family_id"]: f for f in families}
    for sc, rows in (payload.get("hierarchy") or {}).items():
        for row in rows:
            if row.get("family_id") in affected_fams:
                row.update(_recompute_family_row(fam_by_id[row["family_id"]], by_id_new))

    # faculty — merge drop tool rows into the keep row; repoint per-family exemplar ids
    for rec in faculty.values():
        merged_tools: dict[str, dict] = {}
        for tr in rec.get("tools", []):
            cid = drop_to_keep.get(tr["canonical_tool_id"], tr["canonical_tool_id"])
            row = merged_tools.get(cid)
            pmids = {str(p) for p in (tr.get("pmids") or [])}
            if row is None:
                merged_tools[cid] = {
                    "canonical_tool_id": cid,
                    "display_name": canon_display.get(cid, tr.get("display_name")),
                    "pmids": pmids,
                }
            else:
                row["pmids"] |= pmids
        rec["tools"] = [
            {"canonical_tool_id": cid, "display_name": r["display_name"],
             "pub_count": len(r["pmids"]), "pmids": sorted(r["pmids"])}
            for cid, r in merged_tools.items()
        ]
        for fr in rec.get("families", []):
            fr["exemplar_tool_ids"] = [
                e for e in _repoint_id_list(list(fr.get("exemplar_tool_ids") or []), drop_to_keep)
                if e in surviving
            ]

    # tool_context — fold drop maps into keep (longer snippet per pmid wins)
    for d, keep in drop_to_keep.items():
        src = tool_context.pop(d, None)
        if not src:
            continue
        dst = tool_context.setdefault(keep, {})
        for pmid, snip in src.items():
            if snip and (pmid not in dst or len(snip) > len(dst[pmid])):
                dst[pmid] = snip

    # grant_signal — per-tool grant evidence (cid -> {appl_ids, investigator_cwids}),
    # carried in the tools.json bundle and keyed by canonical_tool_id. Fold drop->keep
    # (union both id lists) so the surviving tool keeps the merged forks' grant attribution
    # and no orphan key survives. Empty/absent map is a no-op.
    grant_signal = payload.get("grant_signal")
    gs_folded = 0
    if isinstance(grant_signal, dict):
        for d, keep in drop_to_keep.items():
            src = grant_signal.pop(d, None)
            if not src:
                continue
            gs_folded += 1
            dst = grant_signal.setdefault(keep, {"appl_ids": [], "investigator_cwids": []})
            for f in ("appl_ids", "investigator_cwids"):
                dst[f] = sorted(set(dst.get(f) or []) | set(src.get(f) or []))

    # entities / entity_context — re-project over the mutated tools + tool_context
    entities, entity_context, _parents = build_entity_layer(
        payload["tools"], payload["families"], tool_context, generic_terms=generic_terms,
    )
    if live_entities:
        descriptors = {
            e["parent_entity_id"]: e["parent_descriptor"]
            for e in live_entities
            if e.get("parent_entity_id") and e.get("parent_descriptor")
        }
        if descriptors:
            apply_parent_descriptors(entities, descriptors)
    payload["entities"] = entities
    payload["entity_context"] = entity_context

    # ---- referential-integrity assertions (fail loud before any publish) ----
    blob_ids = set(drops_all)
    leaks = []
    for t in payload["tools"]:
        if t["canonical_tool_id"] in blob_ids:
            leaks.append(("tools", t["canonical_tool_id"]))
    for f in families:
        for i in (f.get("member_tool_ids") or []) + (f.get("exemplar_tool_ids") or []):
            if i in blob_ids:
                leaks.append(("families", f["family_id"], i))
    for cwid, rec in faculty.items():
        for tr in rec.get("tools", []):
            if tr["canonical_tool_id"] in blob_ids:
                leaks.append(("faculty.tools", cwid, tr["canonical_tool_id"]))
        for fr in rec.get("families", []):
            for i in fr.get("exemplar_tool_ids") or []:
                if i in blob_ids:
                    leaks.append(("faculty.families", cwid, i))
    for cid in tool_context:
        if cid in blob_ids:
            leaks.append(("tool_context", cid))
    for cid in (payload.get("grant_signal") or {}):
        if cid in blob_ids:
            leaks.append(("grant_signal", cid))
    for e in entities:
        if e.get("canonical_tool_id") in blob_ids:
            leaks.append(("entities", e.get("canonical_tool_id")))
    if leaks:
        raise SystemExit(f"ABORT: dropped ids still referenced after collapse: {leaks[:20]}")

    # Refresh the bundle-level counts the collapse directly invalidates (the rest of
    # telemetry is extraction-time instrumentation the collapse does not touch).
    tel = payload.get("telemetry")
    if isinstance(tel, dict):
        if "canonical_tools" in tel:
            tel["canonical_tools"] = len(payload["tools"])
        if "grant_signal_tools" in tel and isinstance(payload.get("grant_signal"), dict):
            tel["grant_signal_tools"] = len(payload["grant_signal"])

    return {
        "clusters": report_rows,
        "dropped": len(drops_all),
        "tools_before": len(tools),
        "tools_after": len(payload["tools"]),
        "affected_families": sorted(affected_fams),
        "entities_after": len(entities),
        "grant_signal_folded": gs_folded,
    }


# ---------------------------------------------------------------------------
# Republish (dry-run by default)
# ---------------------------------------------------------------------------


def republish(live_dir: str, *, out_dir: str | None, publish: bool, alias_map_path: str | None = None) -> dict:
    from pipeline_tools.publish import publish_artifacts

    def L(name):
        return json.load(open(os.path.join(live_dir, name)))

    bundle = L("tools.json")                 # tools/families/hierarchy/faculty/... bundle
    payload = dict(bundle)
    payload["tool_context"] = L("tool_context.json").get("tool_context", {})
    payload["entities"] = L("entities.json").get("entities", [])
    payload["entity_context"] = L("entity_context.json").get("entity_context", {})

    merges, _keep_sep = load_method_alias_map(alias_map_path) if alias_map_path else load_method_alias_map()
    report = collapse_forks(payload, merges, generic_terms=load_generic_terms())
    logger.info("collapse: %s", json.dumps(report))

    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        json.dump(payload["entities"], open(os.path.join(out_dir, "entities.collapsed.json"), "w"),
                  ensure_ascii=False, indent=0)

    pub = publish_artifacts(payload, dry_run=not publish)
    logger.info("%s: %d objects", "PUBLISHED" if publish else "DRY-RUN (no upload)", len(pub))
    return {"report": report, "publish": pub, "published": publish}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Collapse #252 surface-form forks in the live artifact (post-hoc).")
    ap.add_argument("--live-dir", required=True, help="dir with tools/families/faculty/tool_context/entities/entity_context.json")
    ap.add_argument("--alias-map", help="path to method_alias_map.json (default: repo config)")
    ap.add_argument("--out-dir", default="out/tools/a2", help="where to write collapsed entities for review")
    ap.add_argument("--publish", action="store_true", help="ACTUALLY upload to S3 (default: dry-run)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    republish(args.live_dir, out_dir=args.out_dir, publish=args.publish, alias_map_path=args.alias_map)
    return 0


# --- self-check (ponytail: money/data path leaves one runnable check) ------
def _selfcheck() -> None:
    merges = [{"class": "x", "canonical": "X cells", "variants": ["X cell line", "X"]}]
    payload = {
        "tools": [
            {"canonical_tool_id": "tool_000001", "display_name": "X cell line", "aliases": ["X cell line"],
             "pub_count": 2, "salience_tier": "A", "context_evidence": ["ctx1"], "supercategory": "s"},
            {"canonical_tool_id": "tool_000009", "display_name": "X", "aliases": ["X"],
             "pub_count": 1, "salience_tier": "A", "context_evidence": ["ctx2"], "supercategory": "s"},
            {"canonical_tool_id": "tool_000010", "display_name": "Y", "aliases": ["Y"],
             "pub_count": 1, "salience_tier": "A", "context_evidence": [], "supercategory": "s"},
        ],
        "families": [{"family_id": "fam_0001", "label": "F", "supercategory": "s", "status": "active",
                      "member_tool_ids": ["tool_000001", "tool_000009", "tool_000010"],
                      "exemplar_tool_ids": ["tool_000009", "tool_000001"]}],
        "hierarchy": {"s": [{"family_id": "fam_0001", "label": "F", "status": "active",
                             "n_members": 3, "n_members_non_c": 3, "pub_count": 4,
                             "exemplars": ["X cell line", "X", "Y"]}]},
        "faculty": {
            "a1": {"cwid": "a1", "tools": [
                {"canonical_tool_id": "tool_000001", "display_name": "X cell line", "pub_count": 2, "pmids": ["p1", "p2"]},
                {"canonical_tool_id": "tool_000009", "display_name": "X", "pub_count": 1, "pmids": ["p3"]}],
                   "families": [{"family_id": "fam_0001", "label": "F", "supercategory": "s",
                                 "pmids": ["p1", "p2", "p3"], "pub_count": 3,
                                 "exemplar_tool_ids": ["tool_000009", "tool_000001"]}]},
        },
        "tool_context": {"tool_000001": {"p1": "short"}, "tool_000009": {"p1": "a longer snippet", "p3": "s3"}},
        "grant_signal": {"tool_000001": {"appl_ids": ["a1"], "investigator_cwids": ["c1"]},
                         "tool_000009": {"appl_ids": ["a2"], "investigator_cwids": ["c1", "c2"]}},
        "telemetry": {"canonical_tools": 3, "grant_signal_tools": 2},
        "entities": [],
    }
    rep = collapse_forks(payload, merges, generic_terms=[])
    assert rep["dropped"] == 1 and rep["tools_after"] == 2, rep
    keep = next(t for t in payload["tools"] if t["canonical_tool_id"] == "tool_000001")
    assert keep["display_name"] == "X cells", keep
    assert keep["pub_count"] == 3, keep["pub_count"]                       # |{p1,p2,p3}|
    assert "X" in keep["aliases"] and "X cell line" in keep["aliases"]
    assert payload["families"][0]["member_tool_ids"] == ["tool_000001", "tool_000010"]
    assert payload["families"][0]["exemplar_tool_ids"] == ["tool_000001"]  # 000009->000001 dedup
    h = payload["hierarchy"]["s"][0]
    assert h["n_members"] == 2 and h["pub_count"] == 4, h                  # X(3)+Y(1); -1 member
    fac = payload["faculty"]["a1"]
    assert len(fac["tools"]) == 1 and fac["tools"][0]["pub_count"] == 3    # p1,p2,p3 unioned
    assert fac["families"][0]["exemplar_tool_ids"] == ["tool_000001"]
    assert payload["tool_context"]["tool_000001"]["p1"] == "a longer snippet"  # longer wins
    assert payload["tool_context"]["tool_000001"]["p3"] == "s3"
    assert "tool_000009" not in payload["tool_context"]
    # grant_signal folded onto keep (union), drop key gone, no orphan
    gs = payload["grant_signal"]
    assert "tool_000009" not in gs, gs
    assert gs["tool_000001"]["appl_ids"] == ["a1", "a2"], gs
    assert gs["tool_000001"]["investigator_cwids"] == ["c1", "c2"], gs
    assert rep["grant_signal_folded"] == 1, rep
    # telemetry counts refreshed
    assert payload["telemetry"]["canonical_tools"] == 2, payload["telemetry"]
    assert payload["telemetry"]["grant_signal_tools"] == 1, payload["telemetry"]
    print("collapse_alias_forks selfcheck OK")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--selfcheck":
        _selfcheck()
    else:
        raise SystemExit(main())

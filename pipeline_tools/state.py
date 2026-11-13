"""Durable tools state, reconciled to the LIVE artifact (scheduled-refresh plan, Phases 0-1).

The live ``s3://wcmc-reciterai-artifacts/tools/latest/`` objects are the curated truth.
They are NOT one corpus build: after the 2026-06-13 corpus run, operator passes changed
them in place (sentence-aligned ``tool_context`` #238/#239, the entity sidecar #1166, and
the #252 alias-fork collapse, ``cli/collapse_alias_forks.py``), and none of those wrote
back to the local registries. Re-running the corpus build from the registries regresses
that curation (ReciterAI#308).

``ToolsState`` is the state a scheduled "maintain" run starts from:

  (a) ``tool_records`` / ``family_records`` / ``denylist``: the registries reconciled to
      live. The alias-fork collapses are applied at registry grain (``pub_ids`` unioned,
      drops removed, family members repointed), every published tool/family field is
      overlaid from live, and ``context_by_pub`` is replaced by the live sentence-aligned
      ``tool_context``. ``pub_ids`` are the registry's own, cross-checked against live
      ``faculty`` pmids and ``tool_context`` pmids (see :func:`reconcile`'s report).
  (b) the live fields the registries cannot regenerate, verbatim: the ``tools.json``
      bundle's other fields (``bundle``: provenance, salience thresholds, hierarchy,
      faculty, grant_signal, telemetry, exceptions summary) and the sidecars
      (``tool_context``, ``tool_context_meta``, ``entities``, ``entity_context``).
  (c) ``baseline_manifest``: the live manifest the state was reconciled against.

:func:`reemit` renders the publish set from a state through the real publish path
(``publish.build_publish_items``); with no new PMIDs it must equal live byte for byte.

Storage layout (``S3StateStore`` at ``s3://wcmc-reciterai-artifacts/tools/_state/``, the
default in ``maintain.py``; ``LocalStateStore`` for a local dir via ``--state-dir``)::

    <root>/latest.json                    {"version": ..., "state_manifest_sha256": ...}
    <root>/<version>/state_manifest.json  schema, version, parent, per-file sha256 + bytes,
                                          counts (written LAST; a version without it is
                                          incomplete and never loaded)
    <root>/<version>/tool_registry.json   {"tools": [...]}   ToolRegistry.save shape
    <root>/<version>/tool_denylist.json   {"aliases": [...]}
    <root>/<version>/family_registry.json {"families": [...]}
    <root>/<version>/bundle.json          {"key_order": [...], "tool_order": [...], "fields": {...}}
    <root>/<version>/tool_context.json    {cid: {pmid: snippet}}
    <root>/<version>/tool_context_meta.json
    <root>/<version>/entities.json        [...]
    <root>/<version>/entity_context.json  {eid: {pmid: [...]}}
    <root>/<version>/baseline_manifest.json
    <root>/<version>/reconcile_report.json (optional)
    <root>/<version>/incorporated_pmids.json (optional) {"pmids": [...]} every PMID / grant key
                                          whose mentions this state already carries; the
                                          maintain run's "new PMIDs" are checkpoint - this set

Versions are immutable (a save never overwrites one); a bad run is rolled back by moving
the ``latest.json`` pointer. No I/O happens at import; the S3 store builds its client lazily.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from pipeline_tools.publish import (
    S3_PREFIX,
    PublishItem,
    _family_record,
    build_publish_items,
)

logger = logging.getLogger(__name__)

STATE_SCHEMA_VERSION = "tools-state-v1"
STATE_S3_PREFIX = "tools/_state/"
LATEST_POINTER = "latest.json"
# A state version saved (set_latest=False) BEFORE its artifact upload; cleared once the
# upload lands and ``latest.json`` moves. Lets the next run adopt that version when the
# upload landed but the pointer flip did not (maintain._adopt_pending).
PENDING_POINTER = "pending.json"
STATE_MANIFEST = "state_manifest.json"

# The publish set (manifest.json aside), in publish order.
ARTIFACT_NAMES = (
    "tools.json", "families.json", "faculty.json", "tool_context.json",
    "tool_context_meta.json", "entities.json", "entity_context.json",
)
# tools.json bundle fields regenerated from the registries; every other bundle field is
# carried verbatim in ``ToolsState.bundle``.
REGISTRY_FIELDS = ("tools", "families")

# Published enriched-record field -> registry field (seed._enriched_records projection).
# ``pub_count`` and ``method_family_label`` are handled separately (derived).
_TOOL_OVERLAY = {
    "display_name": "display_name",
    "disposition": "disposition",
    "kind": "kind",
    "supercategory": "supercategory",
    "method_family_id": "member_of_family",
    "salience_tier": "salience_tier",
    "salience_tier_basis": "salience_tier_basis",
    "attributes": "attributes",
    "aliases": "aliases",
    "context_evidence": "context_evidence",
    "classified_by": "classified_by",
    "classified_by_assumed": "classified_by_assumed",
}
# Published family fields (publish._family_record) carried on the registry record.
_FAMILY_OVERLAY = (
    "label", "supercategory", "dominant_kind", "status", "display", "definition",
    "member_tool_ids", "exemplar_tool_ids",
)


class StateError(RuntimeError):
    """The state cannot be reconciled, saved or loaded without losing or inventing data."""


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _compact(obj, *, sort_keys: bool = False) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=sort_keys).encode("utf-8")


def _no_embed(texts):  # the state path never embeds; fail loud if anything tries
    raise StateError("pipeline_tools.state must not call the embedding seam")


# ---------------------------------------------------------------------------
# The state
# ---------------------------------------------------------------------------


@dataclass
class ToolsState:
    tool_records: list[dict]                 # serialized registry records, sorted by id
    family_records: list[dict]               # registry records, sorted by id
    denylist: list[str]
    bundle: dict                             # {"key_order", "tool_order", "fields"}
    tool_context: dict
    tool_context_meta: dict
    entities: list
    entity_context: dict
    baseline_manifest: dict
    reconcile_report: dict | None = None
    # PMIDs (and ``grant:<appl_id>`` keys) whose mentions are already folded into this
    # state. None on a state built before pipeline_tools.maintain existed.
    incorporated_pmids: list[str] | None = None
    version: str | None = None
    parent_version: str | None = None
    extra: dict = field(default_factory=dict)

    def published_tool_records(self) -> list[dict]:
        """Registry records minus the ones held for review (never published until promoted)."""
        return [r for r in self.tool_records if not r.get("review_hold")]

    def tool_registry(self, *, include_held: bool = True):
        from pipeline_tools.embeddings import EmbeddingCache
        from pipeline_tools.registry import ToolRegistry

        recs = self.tool_records if include_held else self.published_tool_records()
        return ToolRegistry(copy.deepcopy(recs), list(self.denylist),
                            cache=EmbeddingCache(embed=_no_embed))

    def family_registry(self):
        from pipeline_tools.embeddings import EmbeddingCache
        from pipeline_tools.registry import FamilyRegistry

        return FamilyRegistry(copy.deepcopy(self.family_records), cache=EmbeddingCache(embed=_no_embed))

    def counts(self) -> dict:
        f = self.bundle["fields"]
        return {
            "tools": len(self.tool_records),
            "families": len(self.family_records),
            "faculty": len(f.get("faculty") or {}),
            "tool_context": len(self.tool_context),
            "tool_context_meta": len(self.tool_context_meta),
            "entities": len(self.entities),
            "entity_context": len(self.entity_context),
            "denylist": len(self.denylist),
            "held_for_review": len(self.tool_records) - len(self.published_tool_records()),
            "incorporated_pmids": None if self.incorporated_pmids is None else len(self.incorporated_pmids),
        }


# ---------------------------------------------------------------------------
# Live artifact input
# ---------------------------------------------------------------------------


def read_live_dir(live_dir: str | Path) -> dict[str, bytes]:
    """The 7 objects + manifest.json as bytes, each verified against the manifest sha256."""
    live_dir = Path(live_dir)
    out = {"manifest.json": (live_dir / "manifest.json").read_bytes()}
    manifest = json.loads(out["manifest.json"])
    for name in ARTIFACT_NAMES:
        body = (live_dir / name).read_bytes()
        want = manifest["objects"][name]["sha256"]
        if _sha(body) != want:
            raise StateError(f"live {name} sha256 {_sha(body)[:12]} != manifest {want[:12]}")
        out[name] = body
    return out


def parse_live(live: dict[str, bytes]) -> dict:
    """Parsed live objects: bundle (tools.json), sidecar inner objects, manifest."""
    J = lambda n: json.loads(live[n])
    return {
        "bundle": J("tools.json"),
        "tool_context": J("tool_context.json")["tool_context"],
        "tool_context_meta": J("tool_context_meta.json")["tool_context_meta"],
        "entities": J("entities.json")["entities"],
        "entity_context": J("entity_context.json")["entity_context"],
        "faculty_doc": J("faculty.json"),
        "families_doc": J("families.json"),
        "manifest": J("manifest.json"),
    }


# ---------------------------------------------------------------------------
# Reconcile (pure)
# ---------------------------------------------------------------------------


def collapse_registry_forks(
    tool_records: list[dict], family_records: list[dict], merges: list[dict], *, live_ids: set[str],
) -> dict:
    """Apply the #252 alias-fork collapses at REGISTRY grain, in place. Returns a report.

    Clusters are discovered exactly as ``cli/collapse_alias_forks`` discovers them on the
    published artifact (same normalized-variant match, keep = lowest id). Only clusters
    live actually collapsed are applied (every drop absent from live, the keep present);
    a cluster live never collapsed is left alone and reported (live is the truth). A
    partially-collapsed cluster raises.

    Mirrors ``collapse_forks``' tools/families branches, with ``pub_ids`` (which the
    registry has and the artifact lacks) unioned across the cluster.
    """
    from cli.collapse_alias_forks import _repoint_id_list, discover_clusters

    clusters = discover_clusters(tool_records, merges)
    by_id = {r["canonical_tool_id"]: r for r in tool_records}
    applied, skipped = [], []
    drop_to_keep: dict[str, str] = {}
    for c in clusters:
        keep, drops = c["keep"], c["drops"]
        gone = [d for d in drops if d not in live_ids]
        if not gone:
            skipped.append({"class": c["class"], "ids": c["ids"], "why": "not collapsed in live"})
            continue
        if len(gone) != len(drops) or keep not in live_ids:
            raise StateError(f"alias class {c['class']!r} is partially collapsed in live: ids={c['ids']}")
        kr = by_id[keep]
        aliases = set(kr.get("aliases") or []) | {kr.get("display_name")}
        ctx = list(kr.get("context_evidence") or [])
        pub_ids = set(kr.get("pub_ids") or [])
        cbp = dict(kr.get("context_by_pub") or {})
        for d in drops:
            dr = by_id[d]
            aliases |= set(dr.get("aliases") or []) | {dr.get("display_name")}
            for ce in dr.get("context_evidence") or []:
                if ce not in ctx:
                    ctx.append(ce)
            pub_ids |= set(dr.get("pub_ids") or [])
            for pmid, snip in (dr.get("context_by_pub") or {}).items():
                if snip and (pmid not in cbp or len(snip) > len(cbp[pmid])):
                    cbp[pmid] = snip
            drop_to_keep[d] = keep
        kr["display_name"] = c["canonical"] or kr["display_name"]
        kr["aliases"] = sorted(a for a in aliases if a and a.strip())
        kr["context_evidence"] = ctx
        kr["pub_ids"] = sorted(pub_ids)
        kr["pub_count"] = len(pub_ids) if pub_ids else int(kr.get("pub_count") or 0)
        kr["context_by_pub"] = {p: cbp[p] for p in sorted(cbp)}
        applied.append({"class": c["class"], "keep": keep, "drops": drops, "pub_count": kr["pub_count"]})

    tool_records[:] = [r for r in tool_records if r["canonical_tool_id"] not in drop_to_keep]
    surviving = {r["canonical_tool_id"] for r in tool_records}
    for fam in family_records:
        mids = list(fam.get("member_tool_ids") or [])
        names = list(fam.get("member_display_names") or [])
        parallel = len(names) == len(mids)
        keep_idx = [i for i, m in enumerate(mids) if m not in drop_to_keep]
        fam["member_tool_ids"] = [mids[i] for i in keep_idx]
        if parallel:
            fam["member_display_names"] = [names[i] for i in keep_idx]
        fam["exemplar_tool_ids"] = [
            e for e in _repoint_id_list(list(fam.get("exemplar_tool_ids") or []), drop_to_keep)
            if e in surviving
        ]
    return {"applied": applied, "skipped": skipped, "dropped": sorted(drop_to_keep)}


def _enriched_of(rec: dict, fam_label: str | None) -> dict:
    """One record's seed._enriched_records projection (for field-level comparison)."""
    from pipeline_tools.registry import ToolRegistry

    ser = ToolRegistry._serialize(rec)
    return {
        "canonical_tool_id": ser["canonical_tool_id"],
        "display_name": ser["display_name"],
        "disposition": ser["disposition"],
        "kind": ser.get("kind"),
        "supercategory": ser.get("supercategory"),
        "method_family_id": rec.get("member_of_family"),
        "method_family_label": fam_label,
        "salience_tier": ser.get("salience_tier"),
        "salience_tier_basis": ser.get("salience_tier_basis"),
        "attributes": ser.get("attributes"),
        "aliases": ser.get("aliases", []),
        "pub_count": ser["pub_count"],
        "context_evidence": ser.get("context_evidence", []),
        "classified_by": ser.get("classified_by"),
        "classified_by_assumed": ser.get("classified_by_assumed", False),
    }


def _faculty_pmids_by_tool(faculty: dict) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for rec in faculty.values():
        for tr in rec.get("tools", []):
            out.setdefault(tr["canonical_tool_id"], set()).update(str(p) for p in tr.get("pmids") or [])
    return out


def reconcile(
    tool_records: list[dict],
    family_records: list[dict],
    denylist: list[str],
    live: dict,
    *,
    merges: list[dict],
    max_examples: int = 20,
) -> ToolsState:
    """Build a ``ToolsState`` from the local registries + the parsed live artifact.

    ``live`` is :func:`parse_live` output. Inputs are not mutated. Live wins on every
    published field; each correction is counted in ``state.reconcile_report``. Raises
    ``StateError`` when live has a tool or family the registry cannot supply (no way to
    reconstruct ``pub_ids`` without inventing them).
    """
    from pipeline_tools.registry import _normalize_tool_record

    tools = [_normalize_tool_record(copy.deepcopy(r)) for r in tool_records]
    fams = copy.deepcopy(family_records)
    bundle = live["bundle"]
    live_tools = {t["canonical_tool_id"]: t for t in bundle["tools"]}
    live_fams = {f["family_id"]: f for f in bundle["families"]}
    report: dict = {"inputs": {"registry_tools": len(tools), "registry_families": len(fams),
                               "live_tools": len(live_tools), "live_families": len(live_fams)}}

    # 1. alias-fork collapses (the only structural change live has over the registry)
    report["collapse"] = collapse_registry_forks(tools, fams, merges, live_ids=set(live_tools))

    # 2. set reconciliation: registry-only records are dropped (live never published
    #    them); live-only records cannot be reconstructed.
    reg_ids = {r["canonical_tool_id"] for r in tools}
    live_only = sorted(set(live_tools) - reg_ids)
    if live_only:
        raise StateError(f"{len(live_only)} live tools missing from the registry: {live_only[:10]}")
    reg_only = sorted(reg_ids - set(live_tools))
    tools = [r for r in tools if r["canonical_tool_id"] in live_tools]
    report["registry_only_tools_dropped"] = reg_only

    fam_ids = {f["family_id"] for f in fams}
    live_only_f = sorted(set(live_fams) - fam_ids)
    if live_only_f:
        raise StateError(f"{len(live_only_f)} live families missing from the registry: {live_only_f[:10]}")
    report["registry_only_families_dropped"] = sorted(fam_ids - set(live_fams))
    fams = [f for f in fams if f["family_id"] in live_fams]

    # 3. family overlay (labels, display tiers, definitions, membership)
    fam_fix: dict[str, int] = {}
    fam_examples: list = []
    for f in fams:
        lf = live_fams[f["family_id"]]
        for k in _FAMILY_OVERLAY:
            if f.get(k) != lf.get(k):
                fam_fix[k] = fam_fix.get(k, 0) + 1
                if len(fam_examples) < max_examples:
                    fam_examples.append({"family_id": f["family_id"], "field": k})
                f[k] = copy.deepcopy(lf.get(k))
                if k == "definition":
                    # the registry's confidence scored ITS text, not live's
                    f["definition_confidence"] = None
        names = f.get("member_display_names") or []
        if len(names) != len(f["member_tool_ids"]):
            fam_fix["member_display_names"] = fam_fix.get("member_display_names", 0) + 1
            f["member_display_names"] = [live_tools[m]["display_name"] for m in f["member_tool_ids"]]
    report["family_overlay"] = {"fields": fam_fix, "examples": fam_examples}

    # 4. tool overlay + context + pub_ids cross-check
    tool_context = live["tool_context"]
    fac_pmids = _faculty_pmids_by_tool(bundle["faculty"])
    fam_label = {fid: f["label"] for fid, f in live_fams.items()}
    tool_fix: dict[str, int] = {}
    tool_examples: list = []
    pub = {"tools_with_pub_ids": 0, "count_mismatch": [], "seed_count_only": [],
           "faculty_pmids_not_in_pub_ids": [], "context_pmids_not_in_pub_ids": [],
           "seed_count_known_pmids_short": []}
    ctx = {"registry_pairs": 0, "live_pairs": 0, "registry_pairs_absent_from_live": 0,
           "live_pairs_absent_from_registry": 0, "snippets_replaced": 0}
    for r in tools:
        cid = r["canonical_tool_id"]
        lt = live_tools[cid]
        mine = _enriched_of(r, fam_label.get(r.get("member_of_family")))
        for pub_field, reg_field in _TOOL_OVERLAY.items():
            if mine.get(pub_field) != lt.get(pub_field):
                tool_fix[pub_field] = tool_fix.get(pub_field, 0) + 1
                if len(tool_examples) < max_examples:
                    tool_examples.append({"canonical_tool_id": cid, "field": pub_field})
                r[reg_field] = copy.deepcopy(lt.get(pub_field))

        # context_by_pub := live sentence-aligned tool_context (#239) for this tool
        live_cbp = {str(p): s for p, s in (tool_context.get(cid) or {}).items() if s}
        reg_cbp = r.get("context_by_pub") or {}
        ctx["registry_pairs"] += len(reg_cbp)
        ctx["live_pairs"] += len(live_cbp)
        ctx["registry_pairs_absent_from_live"] += len(set(reg_cbp) - set(live_cbp))
        ctx["live_pairs_absent_from_registry"] += len(set(live_cbp) - set(reg_cbp))
        ctx["snippets_replaced"] += sum(1 for p in live_cbp if p in reg_cbp and reg_cbp[p] != live_cbp[p])
        r["context_by_pub"] = {p: live_cbp[p] for p in sorted(live_cbp)}

        # pub_ids: the registry's own; never invented. Cross-check against live.
        ids = set(r.get("pub_ids") or [])
        known = fac_pmids.get(cid, set()) | set(live_cbp)
        if ids:
            pub["tools_with_pub_ids"] += 1
            if len(ids) != lt["pub_count"]:
                pub["count_mismatch"].append({"canonical_tool_id": cid, "pub_ids": len(ids),
                                              "live_pub_count": lt["pub_count"]})
            if fac_pmids.get(cid, set()) - ids:
                pub["faculty_pmids_not_in_pub_ids"].append(cid)
            if set(live_cbp) - ids:
                pub["context_pmids_not_in_pub_ids"].append(cid)
        elif lt["pub_count"]:
            # seed-carried count with no pub ids: keep the stored count (live truth)
            pub["seed_count_only"].append(cid)
            if len(known) < lt["pub_count"]:
                pub["seed_count_known_pmids_short"].append(
                    {"canonical_tool_id": cid, "live_pub_count": lt["pub_count"], "known_pmids": len(known)})
        if not ids and int(r.get("pub_count") or 0) != lt["pub_count"]:
            tool_fix["pub_count(seed)"] = tool_fix.get("pub_count(seed)", 0) + 1
            r["pub_count"] = lt["pub_count"]
    report["tool_overlay"] = {"fields": tool_fix, "examples": tool_examples}
    report["tool_context_vs_registry"] = ctx
    for k in ("count_mismatch", "faculty_pmids_not_in_pub_ids", "context_pmids_not_in_pub_ids",
              "seed_count_only", "seed_count_known_pmids_short"):
        pub[f"n_{k}"] = len(pub[k])
    report["pub_ids"] = pub
    if pub["count_mismatch"]:
        raise StateError(f"{len(pub['count_mismatch'])} tools whose registry pub_ids disagree with live "
                         f"pub_count: {pub['count_mismatch'][:5]}")

    tools.sort(key=lambda r: r["canonical_tool_id"])
    fams.sort(key=lambda f: f["family_id"])
    from pipeline_tools.registry import ToolRegistry

    key_order = list(bundle.keys())
    fields = {k: copy.deepcopy(v) for k, v in bundle.items() if k not in REGISTRY_FIELDS}
    # Live tools[] is NOT in the canonical (tier, -pub_count, id) sort: collapse_forks
    # removed the drops in place and never re-sorted after the keeps' pub_count grew.
    # Carry the published order so a zero-delta re-emit is byte-identical.
    tool_order = [t["canonical_tool_id"] for t in bundle["tools"]]
    return ToolsState(
        tool_records=[ToolRegistry._serialize(r) for r in tools],
        family_records=fams,
        denylist=sorted(denylist),
        bundle={"key_order": key_order, "tool_order": tool_order, "fields": fields},
        tool_context=copy.deepcopy(live["tool_context"]),
        tool_context_meta=copy.deepcopy(live["tool_context_meta"]),
        entities=copy.deepcopy(live["entities"]),
        entity_context=copy.deepcopy(live["entity_context"]),
        baseline_manifest=copy.deepcopy(live["manifest"]),
        reconcile_report=report,
    )


# ---------------------------------------------------------------------------
# Re-emit (zero-delta) + identity check
# ---------------------------------------------------------------------------


def order_tools(records: list[dict], tool_order: list[str] | None) -> list[dict]:
    """Published order: ids in ``tool_order`` keep their stored position; any others
    (new tools) follow in the canonical enriched-record sort they arrive in."""
    if not tool_order:
        return records
    pos = {cid: i for i, cid in enumerate(tool_order)}
    known = sorted((r for r in records if r["canonical_tool_id"] in pos), key=lambda r: pos[r["canonical_tool_id"]])
    return known + [r for r in records if r["canonical_tool_id"] not in pos]


def build_payload(state: ToolsState) -> dict:
    """The publish payload: registry-derived ``tools``/``families`` + verbatim fields,
    in the live bundle's key order, plus the split-out sidecars."""
    from pipeline_tools.seed import _enriched_records

    tr, fr = state.tool_registry(include_held=False), state.family_registry()
    derived = {
        "tools": order_tools(_enriched_records(tr, fr), state.bundle.get("tool_order")),
        "families": [_family_record(f) for f in fr.records()],
    }
    fields = state.bundle["fields"]
    payload = {k: (derived[k] if k in derived else fields[k]) for k in state.bundle["key_order"]}
    payload["tool_context"] = state.tool_context
    # Carried, not re-derived: an existing (tool, pmid) signal is frozen once published
    # (a later alias on the tool must not rescore it); maintain scores only new pairs.
    payload["tool_context_meta"] = state.tool_context_meta
    payload["entities"] = state.entities
    payload["entity_context"] = state.entity_context
    return payload


def reemit(
    state: ToolsState, *, prefix: str = S3_PREFIX, generated_at: str | None = None,
) -> tuple[list[PublishItem], dict]:
    """What publish would write for this state. Zero-delta: ``generated_at`` defaults to
    the baseline manifest's, so a no-new-PMID re-emit does not move ``version``."""
    payload = build_payload(state)
    return build_publish_items(
        payload, prefix=prefix, generated_at=generated_at or state.baseline_manifest.get("generated_at"),
    )


def compare_to_live(items: list[PublishItem], live: dict[str, bytes], *, prefix: str = S3_PREFIX) -> dict:
    """Per-object sha256 identity vs live (``latest/`` copies) + flat/latest parity + an
    exact key-level diff of the manifest (semantic) when its bytes differ."""
    latest = {it.key.rsplit("/", 1)[-1]: it for it in items if it.key.startswith(f"{prefix}latest/")}
    flat = {it.key.rsplit("/", 1)[-1]: it for it in items
            if it.key.startswith(prefix) and "/" not in it.key[len(prefix):]}
    out: dict = {"objects": {}, "all_objects_identical": True}
    for name in ARTIFACT_NAMES:
        mine, theirs = latest[name].body, live[name]
        same = mine == theirs
        out["objects"][name] = {"identical": same, "sha256": _sha(mine), "live_sha256": _sha(theirs),
                                "bytes": len(mine), "live_bytes": len(theirs),
                                "flat_equals_latest": flat[name].body == mine}
        if not same:
            out["all_objects_identical"] = False
            out["objects"][name]["semantic_equal"] = json.loads(mine) == json.loads(theirs)
    m_mine, m_live = latest["manifest.json"].body, live["manifest.json"]
    man = {"identical": m_mine == m_live, "sha256": _sha(m_mine), "live_sha256": _sha(m_live)}
    if m_mine != m_live:
        a, b = json.loads(m_mine), json.loads(m_live)
        man["added_keys"] = {k: a[k] for k in a if k not in b}
        man["removed_keys"] = {k: b[k] for k in b if k not in a}
        man["changed_keys"] = {k: {"reemit": a[k], "live": b[k]} for k in a if k in b and a[k] != b[k]}
        man["key_order_equal"] = [k for k in a if k in b] == [k for k in b if k in a]
    out["manifest"] = man
    return out


def check_meta(state: ToolsState, items: list[PublishItem], *, prefix: str = S3_PREFIX) -> bool:
    """The re-emitted (derived) tool_context_meta equals the stored live one."""
    body = next(it.body for it in items if it.key == f"{prefix}latest/tool_context_meta.json")
    return json.loads(body)["tool_context_meta"] == state.tool_context_meta


# ---------------------------------------------------------------------------
# Storage (versioned dir + latest pointer)
# ---------------------------------------------------------------------------


class LocalStateStore:
    """``<root>/<key>`` on the local filesystem."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def read(self, key: str) -> bytes:
        return (self.root / key).read_bytes()

    def write(self, key: str, body: bytes) -> None:
        p = self.root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)

    def exists(self, key: str) -> bool:
        return (self.root / key).exists()


class S3StateStore:
    """``s3://wcmc-reciterai-artifacts/<prefix><key>``; client built lazily on first use."""

    def __init__(self, prefix: str = STATE_S3_PREFIX, client=None):
        self.prefix = prefix
        self._client = client

    def _c(self):
        if self._client is None:
            from utils.s3_client import ARTIFACTS_BUCKET, S3HierarchyClient
            self._client = S3HierarchyClient(bucket=ARTIFACTS_BUCKET)
        return self._client

    def read(self, key: str) -> bytes:
        return self._c().get_object_bytes(self.prefix + key)

    def write(self, key: str, body: bytes) -> None:
        self._c().put_object(self.prefix + key, body, content_type="application/json")

    def exists(self, key: str) -> bool:
        return self._c().key_exists(self.prefix + key)


def _state_files(state: ToolsState) -> dict[str, bytes]:
    files = {
        "tool_registry.json": json.dumps({"tools": state.tool_records}, indent=2, ensure_ascii=False).encode("utf-8"),
        "tool_denylist.json": json.dumps({"aliases": state.denylist}, indent=2, ensure_ascii=False).encode("utf-8"),
        "family_registry.json": json.dumps({"families": state.family_records}, indent=2,
                                           ensure_ascii=False).encode("utf-8"),
        "bundle.json": _compact(state.bundle),
        "tool_context.json": _compact(state.tool_context, sort_keys=True),
        "tool_context_meta.json": _compact(state.tool_context_meta, sort_keys=True),
        "entities.json": _compact(state.entities),
        "entity_context.json": _compact(state.entity_context, sort_keys=True),
        "baseline_manifest.json": json.dumps(state.baseline_manifest, indent=2, ensure_ascii=False).encode("utf-8"),
    }
    if state.incorporated_pmids is not None:
        files["incorporated_pmids.json"] = _compact({"pmids": sorted(set(state.incorporated_pmids))})
    if state.reconcile_report is not None:
        files["reconcile_report.json"] = json.dumps(state.reconcile_report, indent=2,
                                                    ensure_ascii=False).encode("utf-8")
    return files


def save_state(store, state: ToolsState, version: str, *, created_at: str,
               set_latest: bool = True, notes: dict | None = None) -> dict:
    """Write ``state`` as immutable ``<version>/`` (state_manifest LAST), then the pointer."""
    if not version or "/" in version:
        raise StateError(f"bad state version {version!r}")
    if store.exists(f"{version}/{STATE_MANIFEST}"):
        raise StateError(f"state version {version!r} already exists (versions are immutable)")
    files = _state_files(state)
    for name, body in files.items():
        store.write(f"{version}/{name}", body)
    manifest = {
        "state_schema_version": STATE_SCHEMA_VERSION,
        "version": version,
        "parent_version": state.parent_version,
        "created_at": created_at,
        "baseline_generated_at": state.baseline_manifest.get("generated_at"),
        "counts": state.counts(),
        "files": {n: {"sha256": _sha(b), "bytes": len(b)} for n, b in files.items()},
        "notes": notes or {},
    }
    mbytes = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
    store.write(f"{version}/{STATE_MANIFEST}", mbytes)
    if set_latest:
        store.write(LATEST_POINTER, json.dumps(
            {"version": version, "state_manifest_sha256": _sha(mbytes), "updated_at": created_at},
            indent=2).encode("utf-8"))
    state.version = version
    return manifest


def set_latest(store, version: str, *, updated_at: str) -> None:
    """Point ``latest.json`` at an already-saved ``version`` (its state_manifest must exist)."""
    mbytes = store.read(f"{version}/{STATE_MANIFEST}")
    store.write(LATEST_POINTER, json.dumps(
        {"version": version, "state_manifest_sha256": _sha(mbytes), "updated_at": updated_at},
        indent=2).encode("utf-8"))


def write_pending(store, version: str | None, *, parent_version: str | None, created_at: str) -> None:
    """Record (or, with ``version=None``, clear) the version whose upload is in flight."""
    store.write(PENDING_POINTER, json.dumps(
        {"version": version, "parent_version": parent_version, "created_at": created_at},
        indent=2).encode("utf-8"))


def read_pending(store) -> dict | None:
    if not store.exists(PENDING_POINTER):
        return None
    p = json.loads(store.read(PENDING_POINTER))
    return p if p.get("version") else None


def resolve_latest(store) -> str:
    return json.loads(store.read(LATEST_POINTER))["version"]


def load_state(store, version: str | None = None) -> ToolsState:
    """Load ``version`` (default: the ``latest.json`` pointer), verifying every file's sha256."""
    if version is None:
        ptr = json.loads(store.read(LATEST_POINTER))
        version = ptr["version"]
        mbytes = store.read(f"{version}/{STATE_MANIFEST}")
        if ptr.get("state_manifest_sha256") and _sha(mbytes) != ptr["state_manifest_sha256"]:
            raise StateError(f"latest pointer sha does not match {version}/{STATE_MANIFEST}")
    else:
        mbytes = store.read(f"{version}/{STATE_MANIFEST}")
    manifest = json.loads(mbytes)
    if manifest.get("state_schema_version") != STATE_SCHEMA_VERSION:
        raise StateError(f"unsupported state schema {manifest.get('state_schema_version')!r}")
    raw: dict[str, bytes] = {}
    for name, meta in manifest["files"].items():
        body = store.read(f"{version}/{name}")
        if _sha(body) != meta["sha256"]:
            raise StateError(f"{version}/{name} sha256 mismatch (corrupt or tampered state)")
        raw[name] = body
    J = lambda n: json.loads(raw[n])
    return ToolsState(
        tool_records=J("tool_registry.json")["tools"],
        family_records=J("family_registry.json")["families"],
        denylist=J("tool_denylist.json")["aliases"],
        bundle=J("bundle.json"),
        tool_context=J("tool_context.json"),
        tool_context_meta=J("tool_context_meta.json"),
        entities=J("entities.json"),
        entity_context=J("entity_context.json"),
        baseline_manifest=J("baseline_manifest.json"),
        reconcile_report=J("reconcile_report.json") if "reconcile_report.json" in raw else None,
        incorporated_pmids=J("incorporated_pmids.json")["pmids"] if "incorporated_pmids.json" in raw else None,
        version=version,
        parent_version=manifest.get("parent_version"),
    )

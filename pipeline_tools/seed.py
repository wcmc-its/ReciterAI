"""Seed-batch orchestrator — wires §0.5/§1-§6/§5/§7 over the 230-tool seed.

Flow (one pass over the seed `tools_to_canonicalize.json` -> raw_name /
tool_category / pub_count):

  1. CLASSIFY every mention — disposition gate (§0.5) FIRST, then kind /
     supercategory / attributes (§1-§6) for method_tool records.
  2. IDENTITY — match-or-mint each non-excluded mention into the canonical-tool
     registry (§8), most prominent (highest pub_count) first so the dominant
     surface form mints the canonical record; excluded mentions go on the
     denylist and are never minted.
  3. SALIENCE — seed-mode tiering (§5) over the method_tool canonical records.
  4. FAMILY — match-or-mint each method_tool into the method-family registry
     (§7), same-supercategory gate with the cross-supercategory guard.
  5. EMIT the three §9 outputs: enriched canonical-tool records, the compact
     3-level review hierarchy, and a BOUNDED exceptions queue — plus day-one
     telemetry (mint-vs-attach, exceptions size, per-supercategory family growth).

The LLM and embeddings are injected (``call_json``, ``embed``), so the whole
pipeline is unit-tested with stubs and never touches AWS/OpenAI. The live seed
smoke supplies the Bedrock-backed seams (``cli.build_tool_taxonomy``).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from pipeline_tools import salience as salience_mod
from pipeline_tools import vocab
from pipeline_tools.classify import classify_mentions
from pipeline_tools.embeddings import EmbeddingCache, EmbedFn
from pipeline_tools.registry import (
    DEFAULT_FAMILY_MATCH_COSINE,
    DEFAULT_TOOL_MATCH_COSINE,
    FamilyRegistry,
    ToolRegistry,
    norm_name,
)

logger = logging.getLogger(__name__)

# Exception-queue row types (§9c). "awaiting_a2" is NOT a row here — it covers
# every seed method_tool and is reported as a bounded COUNT in telemetry instead,
# so the human-review queue stays actionable.
EXC_UNCLASSIFIED = "unclassified"               # LLM error / omitted — needs a re-run
EXC_INVALID_CLASSIFICATION = "invalid_classification"
EXC_LOW_CONFIDENCE = "low_confidence_supercategory"
EXC_MINTED_FAMILY = "minted_family"             # provisional family — review + relabel (§7.2)
EXC_CROSS_SUPERCATEGORY = "cross_supercategory_family_match"
EXC_MERGE_SUPERCAT_DISAGREE = "merge_supercategory_disagreement"
EXC_INFRASTRUCTURE = "infrastructure_spot_audit"


@dataclass
class SeedResult:
    tool_registry: ToolRegistry
    family_registry: FamilyRegistry
    records: list[dict] = field(default_factory=list)      # §9a enriched canonical-tool records
    hierarchy: dict = field(default_factory=dict)          # §9c compact 3-level review tree
    exceptions: list[dict] = field(default_factory=list)   # §9c bounded queue
    telemetry: dict = field(default_factory=dict)


def run_seed(
    mentions: list[dict],
    *,
    call_json,
    embed: EmbedFn | None = None,
    force_c_terms: list[str] | None = None,
    tool_match_cosine: float = DEFAULT_TOOL_MATCH_COSINE,
    family_match_cosine: float = DEFAULT_FAMILY_MATCH_COSINE,
    batch_size: int = 50,
) -> SeedResult:
    """Run the full seed pipeline and return the registries + §9 outputs + telemetry."""
    force_c_terms = force_c_terms if force_c_terms is not None else salience_mod.load_force_c_terms()
    cache = EmbeddingCache(embed=embed)
    tools = ToolRegistry(cache=cache, match_cosine=tool_match_cosine)
    families = FamilyRegistry(cache=cache, match_cosine=family_match_cosine)

    meta_by_name = {norm_name(m.get("raw_name", "")): m for m in mentions if m.get("raw_name")}
    exceptions: list[dict] = []

    # 1. classify -----------------------------------------------------------
    classified = classify_mentions(mentions, call_json=call_json, batch_size=batch_size)

    # 2. identity (match-or-mint), most prominent first --------------------
    def _pub_count(rec: dict) -> int:
        return int((meta_by_name.get(norm_name(rec["raw_name"])) or {}).get("pub_count") or 0)

    counts = {"minted": 0, "attached": 0, "denied": 0, "unclassified": 0}
    for c in sorted(classified, key=lambda r: -_pub_count(r)):
        raw = c["raw_name"]
        meta = meta_by_name.get(norm_name(raw), {})
        pub_count = int(meta.get("pub_count") or 0)

        if c["disposition"] is None:  # llm_error / missing
            counts["unclassified"] += 1
            exceptions.append({"type": EXC_UNCLASSIFIED, "raw_name": raw, "flags": c.get("flags", [])})
            continue
        if c["disposition"] == "excluded":
            tools.deny(raw)
            counts["denied"] += 1
            continue

        rec, action = tools.match_or_mint(
            raw_name=raw, display_name=raw, disposition=c["disposition"], pub_count=pub_count,
            context=(meta.get("context") or None),
        )
        if action == "minted":
            counts["minted"] += 1
            tools.update_classification(
                rec["canonical_tool_id"],
                disposition=c["disposition"], kind=c["kind"],
                supercategory=c["supercategory"], attributes=c["attributes"],
            )
            rec["flags"] = list(c.get("flags", []))
            _collect_classification_exceptions(exceptions, rec, c)
        else:  # attached — canonical keeps its minting classification; flag real disagreements
            counts["attached"] += 1
            if (c["disposition"] == vocab.CAPABILITY_DISPOSITION
                    and rec.get("supercategory") and c.get("supercategory")
                    and rec["supercategory"] != c["supercategory"]):
                exceptions.append({
                    "type": EXC_MERGE_SUPERCAT_DISAGREE, "raw_name": raw,
                    "canonical_tool_id": rec["canonical_tool_id"],
                    "canonical_supercategory": rec["supercategory"],
                    "mention_supercategory": c["supercategory"],
                })

    method_tools = [r for r in tools.records() if r["disposition"] == vocab.CAPABILITY_DISPOSITION]

    # 3. salience (seed mode) ----------------------------------------------
    salience_mod.apply_seed_salience(method_tools, force_c_terms=force_c_terms)

    # 4. family match-or-mint ----------------------------------------------
    fam_counts = {"minted": 0, "attached": 0, "flagged": 0}
    for rec in sorted(method_tools, key=lambda r: -int(r.get("pub_count") or 0)):
        supercat = rec.get("supercategory") or vocab.OTHER_SUPERCATEGORY
        fam, action, cross = families.match_or_mint(
            tool_id=rec["canonical_tool_id"], tool_text=rec["display_name"],
            supercategory=supercat, dominant_kind=rec.get("kind"),
        )
        tools.update_classification(rec["canonical_tool_id"], member_of_family=fam["family_id"])
        rec["member_of_family"] = fam["family_id"]
        fam_counts[action] += 1
        # 'minted' and 'flagged' both create a brand-new provisional family ->
        # review + relabel (§7.2); 'flagged' additionally carries a cross match.
        if action in ("minted", "flagged"):
            exceptions.append({"type": EXC_MINTED_FAMILY, "family_id": fam["family_id"],
                               "label": fam["label"], "supercategory": supercat})
        if cross is not None:
            exceptions.append({
                "type": EXC_CROSS_SUPERCATEGORY, "canonical_tool_id": rec["canonical_tool_id"],
                "tool": rec["display_name"], "tool_supercategory": supercat,
                "matched_family_id": cross.key, "score": round(cross.score, 4),
            })

    # 5. §9 outputs + telemetry --------------------------------------------
    result = SeedResult(tool_registry=tools, family_registry=families)
    result.records = _enriched_records(tools, families)
    result.hierarchy = _build_hierarchy(tools, families)
    result.exceptions = exceptions
    result.telemetry = _telemetry(tools, families, method_tools, counts, fam_counts, exceptions)
    logger.info("seed run: %d mentions -> %d canonical tools (%d minted, %d attached, %d denied, %d unclassified); "
                "%d families; %d exceptions",
                len(mentions), len(tools), counts["minted"], counts["attached"], counts["denied"],
                counts["unclassified"], len(families), len(exceptions))
    return result


# ---------------------------------------------------------------------------
# §9 output builders
# ---------------------------------------------------------------------------

def _collect_classification_exceptions(exceptions: list[dict], rec: dict, c: dict) -> None:
    flags = c.get("flags", [])
    from pipeline_tools import classify as _cl
    if _cl.FLAG_LOW_CONFIDENCE in flags:
        exceptions.append({"type": EXC_LOW_CONFIDENCE, "canonical_tool_id": rec["canonical_tool_id"],
                           "tool": rec["display_name"], "supercategory": rec.get("supercategory")})
    if _cl.FLAG_INVALID_SUPERCATEGORY in flags or _cl.FLAG_INVALID_KIND in flags or _cl.FLAG_INVALID_DISPOSITION in flags:
        exceptions.append({"type": EXC_INVALID_CLASSIFICATION, "canonical_tool_id": rec["canonical_tool_id"],
                           "tool": rec["display_name"], "flags": [f for f in flags if f.startswith("invalid")]})
    if _cl.FLAG_INFRASTRUCTURE in flags:
        exceptions.append({"type": EXC_INFRASTRUCTURE, "canonical_tool_id": rec["canonical_tool_id"],
                           "tool": rec["display_name"]})


def _enriched_records(tools: ToolRegistry, families: FamilyRegistry) -> list[dict]:
    """§9a — enriched canonical-tool records (registry deltas), sorted for review."""
    out = []
    for rec in tools.records():
        ser = tools._serialize(rec)
        fam_id = rec.get("member_of_family")
        fam = families.get(fam_id) if fam_id else None
        out.append({
            "canonical_tool_id": ser["canonical_tool_id"],
            "display_name": ser["display_name"],
            "disposition": ser["disposition"],
            "kind": ser.get("kind"),
            "supercategory": ser.get("supercategory"),
            "method_family_id": fam_id,
            "method_family_label": fam["label"] if fam else None,
            "salience_tier": ser.get("salience_tier"),
            "salience_tier_basis": ser.get("salience_tier_basis"),
            "attributes": ser.get("attributes"),
            "aliases": ser.get("aliases", []),
            "pub_count": ser["pub_count"],
            "context_evidence": ser.get("context_evidence", []),
        })
    order = {"S": 0, "A": 1, "B": 2, "C": 3, None: 4}
    out.sort(key=lambda r: (order.get(r["salience_tier"], 4), -(r["pub_count"] or 0), r["canonical_tool_id"]))
    return out


def _build_hierarchy(tools: ToolRegistry, families: FamilyRegistry) -> dict:
    """§9c compact 3-level tree: supercategory -> families -> exemplars + count.

    Exemplars are the family's top member tools by institutional pub_count,
    excluding salience C (a C member is never an exemplar, §5). A family whose
    only members are C-tier surfaces with an empty exemplar list and a 0 non-C
    count — consistent with the C-reconciliation rule (§8).
    """
    by_id = {r["canonical_tool_id"]: r for r in tools.records()}
    tree: dict[str, list[dict]] = {}
    for fam in families.records():
        members = [by_id[mid] for mid in fam["member_tool_ids"] if mid in by_id]
        non_c = [m for m in members if m.get("salience_tier") != vocab.DEMOTED_TIER]
        non_c.sort(key=lambda m: -(int(m.get("pub_count") or 0)))
        tree.setdefault(fam["supercategory"], []).append({
            "family_id": fam["family_id"],
            "label": fam["label"],
            "status": fam["status"],
            "n_members": len(members),
            "n_members_non_c": len(non_c),
            "pub_count": sum(int(m.get("pub_count") or 0) for m in non_c),
            "exemplars": [m["display_name"] for m in non_c[:3]],
        })
    # Order supercategories by the frozen spec order; families by pub_count desc.
    ordered = {}
    for s in vocab.SUPERCATEGORIES:
        fams = tree.get(s["id"])
        if fams:
            fams.sort(key=lambda f: (-f["pub_count"], f["label"]))
            ordered[s["id"]] = fams
    return ordered


def _telemetry(tools, families, method_tools, counts, fam_counts, exceptions) -> dict:
    """Day-one instrumentation: fragmentation, queue size, family growth, distributions."""
    minted = counts["minted"]
    attached = counts["attached"]
    # 'minted' and 'flagged' both create a new family; only 'attached' reuses one.
    fam_minted = fam_counts.get("minted", 0) + fam_counts.get("flagged", 0)
    fam_attached = fam_counts.get("attached", 0)
    per_supercat_families: dict[str, int] = {}
    for fam in families.records():
        per_supercat_families[fam["supercategory"]] = per_supercat_families.get(fam["supercategory"], 0) + 1

    def _dist(field_name: str) -> dict:
        d: dict[str, int] = {}
        for r in tools.records():
            key = r.get(field_name) or "∅"
            d[key] = d.get(key, 0) + 1
        return dict(sorted(d.items(), key=lambda kv: (-kv[1], kv[0])))

    exc_by_type: dict[str, int] = {}
    for e in exceptions:
        exc_by_type[e["type"]] = exc_by_type.get(e["type"], 0) + 1

    return {
        "mentions": sum(counts.values()),
        "canonical_tools": len(tools),
        "tool_minted": minted,
        "tool_attached": attached,
        "tool_denied": counts["denied"],
        "tool_unclassified": counts["unclassified"],
        # fragmentation early-warning: mint-vs-attach ratio (>>1 == under-deduping).
        "mint_vs_attach_ratio": round(minted / attached, 3) if attached else None,
        "families": len(families),
        "family_minted": fam_minted,
        "family_attached": fam_attached,
        "family_flagged_cross_supercat": fam_counts.get("flagged", 0),
        "per_supercategory_family_count": dict(sorted(per_supercat_families.items())),
        "salience_distribution": _dist("salience_tier"),
        "disposition_distribution": _dist("disposition"),
        "supercategory_distribution": _dist("supercategory"),
        "awaiting_a2_regrounding": len(method_tools),  # bounded count, not 230 queue rows
        "exceptions_total": len(exceptions),
        "exceptions_by_type": exc_by_type,
    }


# ---------------------------------------------------------------------------
# Persistence — the registries + the three §9 review artifacts (D-07 gate:
# written for HUMAN REVIEW before anything feeds a downstream stage).
# ---------------------------------------------------------------------------

def write_outputs(result: SeedResult, out_dir: Path) -> list[Path]:
    """Write the two persistent registries + the three §9 review artifacts.

    Files (all under ``out_dir``):
      - tool_registry.json / tool_denylist.json  — §8 persistent registry + denylist
      - family_registry.json                     — §7 persistent registry
      - tool_taxonomy_seed.json                  — §9a enriched canonical-tool records
      - tool_hierarchy_seed.json                 — §9c compact 3-level review tree
      - tool_exceptions_seed.json                — §9c bounded exceptions queue
      - tool_telemetry_seed.json                 — day-one instrumentation
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    result.tool_registry.save(out_dir / "tool_registry.json", out_dir / "tool_denylist.json")
    result.family_registry.save(out_dir / "family_registry.json")

    def _dump(name: str, payload) -> Path:
        path = out_dir / name
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    paths = [
        out_dir / "tool_registry.json",
        out_dir / "tool_denylist.json",
        out_dir / "family_registry.json",
        _dump("tool_taxonomy_seed.json", {"tools": result.records}),
        _dump("tool_hierarchy_seed.json", result.hierarchy),
        _dump("tool_exceptions_seed.json", result.exceptions),
        _dump("tool_telemetry_seed.json", result.telemetry),
    ]
    return paths

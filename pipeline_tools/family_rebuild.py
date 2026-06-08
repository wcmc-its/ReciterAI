"""Family formation by capability class (docs/tool-classifier-spec.md §7 / §7.2).

Replaces the greedy embedding match-or-mint + per-family relabel, which produced
~singleton families at A2 scale (89%): embedding similarity of tool NAMES can't
group same-class-different-name tools (nivolumab vs pembrolizumab are both
anti-PD-1 but their names don't embed alike), and a per-singleton relabel names
the specific instance, not the class.

This forms families the way the spec intends — by shared CAPABILITY CLASS:
  1. **Broaden** each method_tool's name to its §7.2 capability class via the LLM
     (the semantic knowledge a name embedding lacks), batched per supercategory.
  2. **Consolidate** the class strings within a supercategory by embedding NN —
     unlike tool names, class DESCRIPTIONS ("anti-PD-1 immunotherapy") embed well,
     so cross-batch wording variants merge.
  3. **Group** tools by (supercategory, canonical class) into one family each.

Cheaper and faster than the path it replaces (no greedy pass, no per-singleton
relabel), and at the right altitude for a browsable Methods lens.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

from pipeline_tools import vocab
from pipeline_tools.embeddings import EmbeddingCache
from pipeline_tools.ids import IdMinter
from pipeline_tools.registry import FamilyRegistry, norm_name

logger = logging.getLogger(__name__)

BROADEN_MAX_WORKERS = 8
DEFAULT_BROADEN_BATCH = 100
# Class DESCRIPTIONS (not tool names) embed well, so a high threshold safely
# merges only true wording variants ("kinase inhibitors" / "kinase inhibitor
# therapeutics"), never distinct classes.
DEFAULT_CONSOLIDATE_COSINE = 0.92


# ---------------------------------------------------------------------------
# 1. Broaden tool names -> capability class (LLM, per supercategory, parallel)
# ---------------------------------------------------------------------------


def _broaden_batch(names: list[str], supercategory: str, *, call_json) -> dict[str, str]:
    """Assign a broad class to each name; on failure keep the name (its own class)."""
    from prompts.tool_family_broaden import BROADEN_SYSTEM_PROMPT, build_broaden_user_message

    try:
        resp = call_json(BROADEN_SYSTEM_PROMPT, build_broaden_user_message(names, supercategory))
        items = resp.get("assignments", []) if isinstance(resp, dict) else []
    except Exception as exc:  # noqa: BLE001 — partial-failure tolerant
        logger.warning("broaden (%s, %d names) failed (%s); keeping specific names", supercategory, len(names), exc)
        return {n: n for n in names}
    by_name = {norm_name(a.get("label", "")): (a.get("family_class") or "").strip()
               for a in items if a.get("label")}
    return {n: (by_name.get(norm_name(n)) or n) for n in names}


def broaden_tool_classes(
    method_tools: list[dict], *, call_json, batch_size: int = DEFAULT_BROADEN_BATCH,
    max_workers: int = BROADEN_MAX_WORKERS,
) -> dict[str, str]:
    """Return ``{canonical_tool_id: broad_class}`` (batched per supercategory, concurrent)."""
    by_sc: dict[str, list[dict]] = defaultdict(list)
    for t in method_tools:
        by_sc[t.get("supercategory") or vocab.OTHER_SUPERCATEGORY].append(t)

    batches: list[tuple[str, list[dict]]] = []
    for sc, tools in by_sc.items():
        for i in range(0, len(tools), batch_size):
            batches.append((sc, tools[i:i + batch_size]))

    out: dict[str, str] = {}
    workers = max(1, min(max_workers, len(batches)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_broaden_batch, [t["display_name"] for t in tools], sc, call_json=call_json):
                (sc, tools) for sc, tools in batches}
        done = 0
        for fut in as_completed(futs):
            sc, tools = futs[fut]
            name_to_class = fut.result()
            for t in tools:
                out[t["canonical_tool_id"]] = name_to_class.get(t["display_name"], t["display_name"])
            done += 1
            if done % 10 == 0:
                logger.info("broaden: %d/%d batches", done, len(batches))
    logger.info("broaden: %d tools -> classes over %d batch(es)", len(out), len(batches))
    return out


# ---------------------------------------------------------------------------
# 2. Consolidate class strings within a supercategory (embedding NN)
# ---------------------------------------------------------------------------


def consolidate_classes(
    class_of_tool: dict[str, str], method_tools: list[dict], *,
    embed_cache: EmbeddingCache, cosine: float = DEFAULT_CONSOLIDATE_COSINE,
) -> dict[str, str]:
    """Merge near-duplicate class strings within each supercategory.

    Returns ``{canonical_tool_id: canonical_class}``. The canonical form of a merge
    cluster is its most frequent class string (the dominant phrasing), tie-broken
    lexicographically — so the browsable label is the common one.
    """
    sc_of = {t["canonical_tool_id"]: (t.get("supercategory") or vocab.OTHER_SUPERCATEGORY)
             for t in method_tools}
    # distinct classes + their frequency, per supercategory
    by_sc_classes: dict[str, Counter] = defaultdict(Counter)
    for tid, cls in class_of_tool.items():
        by_sc_classes[sc_of.get(tid, vocab.OTHER_SUPERCATEGORY)][cls] += 1

    canonical_of: dict[tuple[str, str], str] = {}
    for sc, freq in by_sc_classes.items():
        classes = sorted(freq)
        if len(classes) <= 1:
            for c in classes:
                canonical_of[(sc, c)] = c
            continue
        matrix = np.asarray(embed_cache.get_many(classes), dtype=np.float64)
        matrix = matrix / np.clip(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-9, None)
        sims = matrix @ matrix.T
        # union-find over the >= cosine graph
        parent = list(range(len(classes)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i in range(len(classes)):
            for j in range(i):
                if sims[i, j] >= cosine:
                    parent[find(i)] = find(j)
        clusters: dict[int, list[int]] = defaultdict(list)
        for i in range(len(classes)):
            clusters[find(i)].append(i)
        for members in clusters.values():
            # canonical = most frequent class in the cluster (tie -> lexicographic)
            canon = max((classes[i] for i in members), key=lambda c: (freq[c], -len(c), c))
            for i in members:
                canonical_of[(sc, classes[i])] = canon

    return {tid: canonical_of.get((sc_of.get(tid, vocab.OTHER_SUPERCATEGORY), cls), cls)
            for tid, cls in class_of_tool.items()}


# ---------------------------------------------------------------------------
# 3. Group tools by canonical class into families
# ---------------------------------------------------------------------------


def form_families(
    method_tools: list[dict], *, call_json, embed_cache: EmbeddingCache,
    batch_size: int = DEFAULT_BROADEN_BATCH, consolidate_cosine: float = DEFAULT_CONSOLIDATE_COSINE,
) -> tuple[FamilyRegistry, dict[str, str]]:
    """Form capability-class families. Returns ``(FamilyRegistry, {tool_id: family_id})``.

    The caller repoints each tool's ``member_of_family`` with the returned mapping.
    Family ids are minted fresh (the granular A2 families were never published, so
    the D-06 carry-forward does not apply here).
    """
    raw = broaden_tool_classes(method_tools, call_json=call_json, batch_size=batch_size)
    canonical = consolidate_classes(raw, method_tools, embed_cache=embed_cache, cosine=consolidate_cosine)

    by_class: dict[tuple[str, str], list[dict]] = defaultdict(list)
    tool_by_id = {t["canonical_tool_id"]: t for t in method_tools}
    for tid, cls in canonical.items():
        sc = tool_by_id[tid].get("supercategory") or vocab.OTHER_SUPERCATEGORY
        by_class[(sc, cls)].append(tool_by_id[tid])

    minter = IdMinter.for_families([])
    fam_records: list[dict] = []
    tool_to_family: dict[str, str] = {}
    for (sc, cls), tools in sorted(by_class.items(), key=lambda kv: (kv[0][0], -len(kv[1]), kv[0][1])):
        fid = minter.mint()
        kinds = [t.get("kind") for t in tools if t.get("kind")]
        dominant = Counter(kinds).most_common(1)[0][0] if kinds else None
        ranked = sorted(tools, key=lambda t: -len(t.get("pub_ids") or []))
        fam_records.append({
            "family_id": fid,
            "label": cls,
            "supercategory": sc,
            "dominant_kind": dominant,
            "member_tool_ids": [t["canonical_tool_id"] for t in ranked],
            "exemplar_tool_ids": [t["canonical_tool_id"] for t in ranked[:3]],
            "status": "active",
            "member_display_names": [t["display_name"] for t in ranked],
        })
        for t in tools:
            tool_to_family[t["canonical_tool_id"]] = fid

    registry = FamilyRegistry(fam_records, cache=embed_cache)
    singletons = sum(1 for f in fam_records if len(f["member_tool_ids"]) == 1)
    logger.info("form_families: %d method_tools -> %d families (%d singletons, %d%%)",
                len(method_tools), len(fam_records), singletons,
                100 * singletons // max(len(fam_records), 1))
    return registry, tool_to_family

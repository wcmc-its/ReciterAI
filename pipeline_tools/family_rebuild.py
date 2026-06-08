"""Family formation by capability class (docs/tool-classifier-spec.md §7 / §7.2).

Replaces the greedy embedding match-or-mint + per-family relabel, which produced
~singleton families at A2 scale (89%): embedding similarity of tool NAMES can't
group same-class-different-name tools (nivolumab vs pembrolizumab are both
anti-PD-1 but their names don't embed alike), and a per-singleton relabel names
the specific instance, not the class.

This forms families the way the spec intends — by shared CAPABILITY CLASS:
  1. **Broaden** each method_tool's name to its §7.2 capability class via the LLM
     (the semantic knowledge a name embedding lacks), batched per supercategory.
     Cached to disk, so the family stage is cheaply re-runnable.
  2. **Reconcile** the class strings within a supercategory via the LLM
     (``reconcile_classes``): the 190 independent broaden batches share no
     vocabulary, so they mint near-unique labels for the SAME capability
     ("transgenic mouse models" / "genetically engineered mouse models"). Deciding
     two labels name the same capability is a semantic judgment — and a fixed
     embedding cosine cannot make it, because the wording variants that should
     merge and the sibling classes that must not ("anti-PD-1" vs "anti-PD-L1")
     differ by the same one-token distance. So the LLM adjudicates, match-or-mint,
     over an accreted per-supercategory canonical vocabulary.
  3. **Group** tools by (supercategory, canonical class) into one family each.

``consolidate_classes`` (embedding NN) is the older, cheaper step-2 — kept as a
no-LLM fallback; the corpus path uses ``reconcile_classes``.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np

from pipeline_tools import vocab
from pipeline_tools.embeddings import EmbeddingCache
from pipeline_tools.ids import IdMinter
from pipeline_tools.registry import FamilyRegistry, norm_name

logger = logging.getLogger(__name__)

BROADEN_MAX_WORKERS = 8
DEFAULT_BROADEN_BATCH = 100
BROADEN_CACHE_NAME = "broaden_cache.json"
# Class DESCRIPTIONS (not tool names) embed well, so a high threshold safely
# merges only true wording variants ("kinase inhibitors" / "kinase inhibitor
# therapeutics"), never distinct classes.
DEFAULT_CONSOLIDATE_COSINE = 0.92
RECONCILE_MAX_WORKERS = 8
# Bigger than the broaden batch: reconciliation accretes the canonical vocabulary
# sequentially WITHIN a supercategory (so cross-batch variants reconcile), and a
# larger batch means the model sees more of a supercategory's labels at once.
DEFAULT_RECONCILE_BATCH = 120
RECONCILE_CACHE_NAME = "reconcile_cache.json"

# A leading camelCase scientific term (iPSC, mRNA, cDNA, mTOR, scRNA-seq, p53):
# a 1-3 char lowercase prefix immediately followed by an uppercase letter OR a
# digit. NO ordinary word matches this (mass, anti-PD-1, in vitro do not), so it
# safely marks the labels whose lower-case opener is meaningful and must be kept.
_STYLED_LEAD = re.compile(r"^[a-z]{1,3}[A-Z0-9]")


def sentence_case_label(label: str) -> str:
    """Sentence-case a family label for display: capitalize the first letter,
    leave everything else as authored (so interior acronyms — NMR, FISH, PD-1 —
    survive), and DON'T touch a label that opens with a camelCase scientific term
    (``iPSC-derived…`` must not become ``IPSC-derived…``). Deterministic."""
    s = label.strip()
    if not s or _STYLED_LEAD.match(s):
        return s
    for i, ch in enumerate(s):
        if ch.isalpha():
            return s[:i] + ch.upper() + s[i + 1:]
        if ch.isdigit():  # leading-number style ("3D …", "5-HT …") — first letter already cased
            return s
    return s


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


def broaden_with_cache(
    method_tools: list[dict], *, call_json, batch_size: int = DEFAULT_BROADEN_BATCH,
    checkpoint_dir=None,
) -> dict[str, str]:
    """``broaden_tool_classes`` keyed by ``canonical_tool_id``, cached to disk.

    Broadening is ~190 LLM calls at A2 scale; the tool set is stable across re-runs
    (tool canonicalization is deterministic), so the cache lets the family stage
    re-run for $0 while only the cheaper reconcile pass is tuned. Delete the cache
    file to force a fresh broaden.
    """
    cache_path = Path(checkpoint_dir) / BROADEN_CACHE_NAME if checkpoint_dir else None
    cached: dict[str, str] = {}
    if cache_path and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            logger.info("broaden cache: %d/%d tools cached at %s", len(cached), len(method_tools), cache_path)
        except (OSError, ValueError) as exc:  # corrupt cache -> re-broaden, don't crash
            logger.warning("broaden cache unreadable (%s); re-broadening", exc)
            cached = {}

    todo = [t for t in method_tools if t["canonical_tool_id"] not in cached]
    if todo:
        fresh = broaden_tool_classes(todo, call_json=call_json, batch_size=batch_size)
        cached.update(fresh)
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")
    return {t["canonical_tool_id"]: cached.get(t["canonical_tool_id"], t["display_name"]) for t in method_tools}


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
# 2'. Reconcile class strings within a supercategory (LLM adjudication)
# ---------------------------------------------------------------------------


def _reconcile_supercategory(
    labels_by_freq: list[str], supercategory: str, *, call_json, batch_size: int,
) -> dict[str, str]:
    """Accretion match-or-mint over ONE supercategory's distinct labels (LLM arbiter).

    ``labels_by_freq`` is the distinct class labels, most-frequent first (so the
    dominant phrasing seeds the canonical vocabulary). Each batch sees the vocabulary
    accreted so far, so a variant in batch N reconciles to a canonical minted in
    batch 1. Returns ``{label: canonical}``; on a batch failure those labels keep
    themselves (their own canonical) rather than dropping.
    """
    from prompts.tool_family_reconcile import RECONCILE_SYSTEM_PROMPT, build_reconcile_user_message

    canonical_vocab: list[str] = []
    seen: set[str] = set()
    label_to_canon: dict[str, str] = {}

    def _adopt(canon: str) -> str:
        if canon not in seen:
            canonical_vocab.append(canon)
            seen.add(canon)
        return canon

    for i in range(0, len(labels_by_freq), batch_size):
        chunk = labels_by_freq[i:i + batch_size]
        try:
            resp = call_json(RECONCILE_SYSTEM_PROMPT,
                             build_reconcile_user_message(chunk, canonical_vocab, supercategory))
            items = resp.get("assignments", []) if isinstance(resp, dict) else []
        except Exception as exc:  # noqa: BLE001 — partial-failure tolerant
            logger.warning("reconcile (%s, batch %d, %d labels) failed (%s); keeping labels as-is",
                           supercategory, i // batch_size, len(chunk), exc)
            for lab in chunk:
                label_to_canon[lab] = _adopt(lab)
            continue
        by_label = {norm_name(a.get("label", "")): (a.get("canonical") or "").strip()
                    for a in items if a.get("label")}
        for lab in chunk:
            label_to_canon[lab] = _adopt(by_label.get(norm_name(lab)) or lab)
    return label_to_canon


def reconcile_classes(
    class_of_tool: dict[str, str], method_tools: list[dict], *,
    call_json, batch_size: int = DEFAULT_RECONCILE_BATCH, max_workers: int = RECONCILE_MAX_WORKERS,
) -> dict[str, str]:
    """LLM-adjudicated canonicalization of broadened class labels, per supercategory.

    Returns ``{canonical_tool_id: canonical_class}``. The LLM decides same-vs-distinct
    capability (the judgment a fixed embedding threshold cannot make — wording variants
    and sibling classes differ by the same one-token distance); an accreted
    per-supercategory vocabulary keeps cross-batch variants consistent. Supercategories
    run concurrently; the accretion within each is sequential by design.
    """
    sc_of = {t["canonical_tool_id"]: (t.get("supercategory") or vocab.OTHER_SUPERCATEGORY)
             for t in method_tools}
    by_sc_freq: dict[str, Counter] = defaultdict(Counter)
    for tid, cls in class_of_tool.items():
        by_sc_freq[sc_of.get(tid, vocab.OTHER_SUPERCATEGORY)][cls] += 1

    items = list(by_sc_freq.items())
    label_maps: dict[str, dict[str, str]] = {}
    workers = max(1, min(max_workers, len(items)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_reconcile_supercategory, [c for c, _ in freq.most_common()], sc,
                            call_json=call_json, batch_size=batch_size): sc
                for sc, freq in items}
        for fut in as_completed(futs):
            label_maps[futs[fut]] = fut.result()

    n_in = sum(len(freq) for _, freq in items)
    n_out = len({(sc, c) for sc, m in label_maps.items() for c in m.values()})
    logger.info("reconcile: %d distinct labels -> %d canonical classes across %d supercategor(ies)",
                n_in, n_out, len(items))
    return {tid: label_maps.get(sc_of.get(tid, vocab.OTHER_SUPERCATEGORY), {}).get(cls, cls)
            for tid, cls in class_of_tool.items()}


def reconcile_with_cache(
    class_of_tool: dict[str, str], method_tools: list[dict], *,
    call_json, batch_size: int = DEFAULT_RECONCILE_BATCH, checkpoint_dir=None,
) -> dict[str, str]:
    """``reconcile_classes`` keyed by ``canonical_tool_id``, cached to disk (all-or-nothing).

    Reconciliation is an LLM pass, so its output is non-deterministic across runs.
    Caching the ``{tool_id: canonical_class}`` mapping lets a later run — to re-case
    or re-publish — reproduce the EXACT reviewed family set for $0 (the accretion is
    not re-run, so it cannot reshuffle). Cached only when it covers every
    method_tool; delete the cache file to force a fresh reconcile.
    """
    cache_path = Path(checkpoint_dir) / RECONCILE_CACHE_NAME if checkpoint_dir else None
    ids = {t["canonical_tool_id"] for t in method_tools}
    if cache_path and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if ids <= set(cached):
                logger.info("reconcile cache: %d tools cached at %s ($0, deterministic)", len(ids), cache_path)
                return {tid: cached[tid] for tid in ids}
            logger.info("reconcile cache covers %d/%d tools; re-reconciling", len(ids & set(cached)), len(ids))
        except (OSError, ValueError) as exc:  # corrupt cache -> re-reconcile, don't crash
            logger.warning("reconcile cache unreadable (%s); re-reconciling", exc)

    canonical = reconcile_classes(class_of_tool, method_tools, call_json=call_json, batch_size=batch_size)
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(canonical, ensure_ascii=False), encoding="utf-8")
    return canonical


# ---------------------------------------------------------------------------
# 3. Group tools by canonical class into families
# ---------------------------------------------------------------------------


def form_families(
    method_tools: list[dict], *, call_json, embed_cache: EmbeddingCache,
    batch_size: int = DEFAULT_BROADEN_BATCH, reconcile_batch_size: int = DEFAULT_RECONCILE_BATCH,
    checkpoint_dir=None,
) -> tuple[FamilyRegistry, dict[str, str]]:
    """Form capability-class families. Returns ``(FamilyRegistry, {tool_id: family_id})``.

    Broaden each tool's name to a capability class (cached to ``checkpoint_dir``),
    then reconcile the per-supercategory class vocabulary via the LLM so cross-batch
    wording variants collapse while distinct capabilities stay separate (also cached,
    so a re-run reproduces the exact set for $0), then group. Family labels are
    sentence-cased for display (``sentence_case_label``).

    The caller repoints each tool's ``member_of_family`` with the returned mapping.
    Family ids are minted fresh (the granular A2 families were never published, so
    the D-06 carry-forward does not apply here). ``embed_cache`` is retained for the
    minted ``FamilyRegistry`` (downstream NN matching), not for class consolidation.
    """
    raw = broaden_with_cache(method_tools, call_json=call_json, batch_size=batch_size,
                             checkpoint_dir=checkpoint_dir)
    canonical = reconcile_with_cache(raw, method_tools, call_json=call_json,
                                     batch_size=reconcile_batch_size, checkpoint_dir=checkpoint_dir)

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
            "label": sentence_case_label(cls),
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

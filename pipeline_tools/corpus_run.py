"""Corpus-mode orchestrator — the A2 analog of ``pipeline_tools.seed`` (Steps C-F).

The seed ran the classify -> §8 tool registry -> §5 salience -> §7 family pipeline
over 230 curated names with aggregate ``pub_count`` and provisional salience. A2
runs the SAME pipeline over corpus-extracted mentions, but with the four things
the seed structurally could not do (spec §1 / handoff):

  C. **Real identity with real pub_ids.** Each mention carries its PMID, so
     ``ToolRegistry.match_or_mint`` accretes ``pub_ids`` and ``pub_count =
     |pub_ids|`` is correct per-publication. The seed registries are LOADED first
     so every canonical id carries forward (D-06) — A2 accretes onto the seed,
     never restarts it.
  D. **Grounded salience.** Cross-faculty spread now exists (the per-faculty
     join), so §5 regrounds: ``basis=grounded``, S becomes assignable, thresholds
     calibrate against the real distribution (``pipeline_tools.salience``).
  E. **Family relabel + dedup.** Families have real members, so the §7.2 kind-aware
     relabel and the §7 exact-label dedup sweep run (``pipeline_tools.relabel``).
  F. **Faculty rollup.** Per-(scholar,tool) and C-reconciled per-(scholar,family)
     counts (``pipeline_tools.rollup``).

Signal scope (docs/tools-producer-model.md §grants): publication AND grant
abstracts feed identity / salience / families, but grants are kept OUT of the
publication pub-filter — grant occurrences contribute NO ``pub_ids`` (so they
never inflate ``pub_count`` or the faculty rollup) and instead accrue a SEPARATE
grant signal (distinct ``appl_id``s + investigator CWIDs) per canonical tool,
surfaced for salience context and review.

The LLM (``call_json``) and embeddings (``embed``) are injected, so the whole
orchestrator unit-tests with stubs and never touches AWS; ``cli.build_tool_taxonomy_corpus``
supplies the live Bedrock-backed seams.
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from pipeline_tools import salience as salience_mod
from pipeline_tools import sanity as sanity_mod
from pipeline_tools import vocab
from pipeline_tools.classify import classify_batch, classify_mentions
from pipeline_tools.registry import (
    DEFAULT_FAMILY_MATCH_COSINE,
    DEFAULT_TOOL_MATCH_COSINE,
    FamilyRegistry,
    ToolRegistry,
    norm_name,
)
from pipeline_tools.rollup import build_faculty_rollup
from pipeline_tools.seed import (
    EXC_CROSS_SUPERCATEGORY,
    EXC_MERGE_SUPERCAT_DISAGREE,
    EXC_ROUTING_SANITY,
    EXC_UNCLASSIFIED,
    _build_hierarchy,
    _collect_classification_exceptions,
    _enriched_records,
)
from utils.bedrock_client import SONNET_MODEL

logger = logging.getLogger(__name__)

GRANT_PMID_PREFIX = "grant:"


def stamp_assumed_classified_by(tool_registry: ToolRegistry, *, assumed_model: str) -> tuple[int, int]:
    """Backfill ``classified_by`` provenance + an ``assumed`` flag (returns (n_assumed, n_real)).

    Fresh classifications carry the real per-form model (stamped by the classify seam,
    persisted in the classify cache). Records whose classification came from a cache entry
    written before per-form stamping carry none — stamp the assumed aggregate model and set
    ``classified_by_assumed=True`` so an assumed value is honestly distinguishable from real
    provenance. Records with a real model are marked ``assumed=False``. Deterministic +
    reproducible on every re-run; as the cache accretes real models, the assumed set shrinks.
    """
    n_assumed = n_real = 0
    for rec in tool_registry.records():
        if rec.get("classified_by"):
            rec["classified_by_assumed"] = False
            n_real += 1
        else:
            rec["classified_by"] = assumed_model
            rec["classified_by_assumed"] = True
            n_assumed += 1
    return n_assumed, n_real


@dataclass
class CorpusResult:
    tool_registry: ToolRegistry
    family_registry: FamilyRegistry
    records: list[dict] = field(default_factory=list)        # §9a enriched canonical-tool records
    hierarchy: dict = field(default_factory=dict)            # §9c compact 3-level review tree
    exceptions: list[dict] = field(default_factory=list)     # §9c bounded queue
    faculty_rollup: dict = field(default_factory=dict)       # Step F: cwid -> {tools, families}
    grant_signal: dict = field(default_factory=dict)         # cid -> {appl_ids, investigator_cwids}
    tool_context: dict = field(default_factory=dict)         # #193 sidecar source: cid -> {pmid: snippet}
    entities: list[dict] = field(default_factory=list)       # #1166 entity DIMENSION (entities.json)
    entity_context: dict = field(default_factory=dict)       # #1166 (pub x entity) FACTS (entity_context.json)
    entity_define_deltas: list[dict] = field(default_factory=list)  # #1166 render-only parent descriptors
    relabel_deltas: list[dict] = field(default_factory=list)
    merge_deltas: list[dict] = field(default_factory=list)
    override_deltas: list[dict] = field(default_factory=list)  # D-07 fix batch (v6→v7)
    consolidation_deltas: list[dict] = field(default_factory=list)  # 820 consolidation (v7'→v8)
    adhoc_deltas: list[dict] = field(default_factory=list)  # accreting co-assignment dupe batch
    define_deltas: list[dict] = field(default_factory=list)  # #879 render-only family definitions
    thresholds: object | None = None                         # salience.GroundedThresholds
    telemetry: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Surface-form grouping — one classification per normalized name, occurrences kept
# ---------------------------------------------------------------------------


@dataclass
class _UniqueMention:
    raw_name: str            # representative display form (longest, preserves acronym+expansion)
    norm_key: str
    tool_category: str | None
    context: str | None
    pub_count: int           # distinct PUBLICATION pmids (prominence hint for classify + mint order)
    occurrences: list[dict]  # [{pmid, source_kind, cwid, author_role}] exploded over authors
    context_by_pmid: dict[str, str] = field(default_factory=dict)  # #193 per-pub usage snippet (pmid -> longest)


def _explode_occurrences(mention: dict) -> list[dict]:
    """One occurrence row per (mention, faculty author); no authors -> one anonymous row."""
    pmid = str(mention.get("pmid") or "")
    source_kind = mention.get("source_kind") or "publication"
    authors = mention.get("authors") or []
    if not authors:
        return [{"pmid": pmid, "source_kind": source_kind, "cwid": None, "author_role": None}]
    rows = []
    for a in authors:
        rows.append({
            "pmid": pmid,
            "source_kind": source_kind,
            "cwid": a.get("cwid"),
            "author_role": a.get("author_role"),
        })
    return rows


def group_mentions(mentions: list[dict]) -> list[_UniqueMention]:
    """Group raw mentions by normalized surface form; keep every occurrence.

    One classification + one match-or-mint per normalized name (cost + a single
    canonical disposition per form), with the per-paper / per-faculty occurrences
    retained so identity accretion, spread, and the rollup can explode back out.
    The registry still merges *different* surface forms (MRI ↔ magnetic resonance
    imaging) into one canonical id downstream; this only collapses exact repeats.
    """
    groups: dict[str, dict] = {}
    for m in mentions:
        raw = (m.get("raw_name") or "").strip()
        key = norm_name(raw)
        if not key:
            continue
        g = groups.get(key)
        if g is None:
            g = {
                "names": defaultdict(int),
                "categories": defaultdict(int),
                "context": None,
                "occurrences": [],
                "pub_pmids": set(),
                "context_by_pmid": {},  # #193 per-publication context (pmid -> longest snippet)
            }
            groups[key] = g
        g["names"][raw] += 1
        cat = m.get("tool_category")
        if cat:
            g["categories"][cat] += 1
        ctx = (m.get("context") or "").strip()
        if ctx and (g["context"] is None or len(ctx) > len(g["context"])):
            g["context"] = ctx
        occ = _explode_occurrences(m)
        g["occurrences"].extend(occ)
        if (m.get("source_kind") or "publication") == "publication":
            pmid = str(m.get("pmid") or "")
            if pmid:
                g["pub_pmids"].add(pmid)
                # #193: keep the longest snippet per pmid (grants excluded — they
                # never join the faculty rollup, which is publication-pmid keyed).
                if ctx and len(ctx) > len(g["context_by_pmid"].get(pmid, "")):
                    g["context_by_pmid"][pmid] = ctx

    out: list[_UniqueMention] = []
    for key, g in groups.items():
        # Representative display: most frequent raw form, tie -> longest (keeps "X (ABC)").
        raw_name = max(g["names"].items(), key=lambda kv: (kv[1], len(kv[0])))[0]
        category = max(g["categories"].items(), key=lambda kv: kv[1])[0] if g["categories"] else None
        out.append(_UniqueMention(
            raw_name=raw_name,
            norm_key=key,
            tool_category=category,
            context=g["context"],
            pub_count=len(g["pub_pmids"]),
            occurrences=g["occurrences"],
            context_by_pmid=g["context_by_pmid"],
        ))
    return out


def _pub_ids_of(occurrences: list[dict]) -> list[str]:
    """Distinct PUBLICATION pmids (grants excluded from the pub-filter)."""
    return sorted({
        str(o["pmid"]) for o in occurrences
        if o.get("source_kind") != "grant" and o.get("pmid")
    })


# ---------------------------------------------------------------------------
# Classify checkpoint — resumable, keyed by normalized name (run-completeness
# independent, so a partial run's cache is always valid to resume from). Protects
# the expensive Sonnet classify pass; the cheap downstream stages re-run.
# ---------------------------------------------------------------------------

CLASSIFY_CHECKPOINT_NAME = "classify_cache.jsonl"
_CLASSIFY_FLUSH_EVERY = 10  # persist the checkpoint every N completed batches
# Classify batches are independent LLM calls; many content-filter on Bedrock and
# fall back to OpenAI (~20s each), so a sequential loop is the wall-clock floor.
# Run them concurrently (BedrockClient retries throttles itself; its default pool
# fits this). Kept modest to stay under Sonnet/OpenAI rate limits.
CLASSIFY_MAX_WORKERS = 8


def _load_classify_cache(path: Path) -> dict[str, dict]:
    cache: dict[str, dict] = {}
    if not path.exists():
        return cache
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue  # tolerate a torn final line (hard kill mid-write)
        key = norm_name(rec.get("raw_name", ""))
        if key:
            cache[key] = rec
    return cache


def _append_classify_cache(path: Path, records: list[dict]) -> None:
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fh.flush()


def _classify_resumable(
    classify_inputs: list[dict], *, call_json, batch_size: int, checkpoint_path: Path | None,
    max_workers: int = CLASSIFY_MAX_WORKERS,
) -> dict[str, dict]:
    """Classify each input once, concurrently, resuming from a JSONL cache.

    Batches run over a thread pool (independent LLM calls; the slow content-filter
    -> OpenAI fallbacks overlap instead of serializing). Returns
    ``{norm_name(raw_name): classification}``. With a checkpoint path, the cache is
    flushed every ``_CLASSIFY_FLUSH_EVERY`` completed batches, so a crash
    re-classifies only the still-missing forms.
    """
    cached = _load_classify_cache(checkpoint_path) if checkpoint_path else {}
    todo = [m for m in classify_inputs if norm_name(m.get("raw_name", "")) not in cached]
    if checkpoint_path and cached:
        logger.info("classify checkpoint: %d cached, %d to classify", len(cached), len(todo))
    by_name = dict(cached)
    if not todo:
        return by_name

    batches = [todo[i:i + batch_size] for i in range(0, len(todo), batch_size)]
    workers = max(1, min(max_workers, len(batches)))
    done = completed = 0
    flush_buffer: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(classify_batch, b, call_json=call_json, label=str(i + 1))
                   for i, b in enumerate(batches)]
        for fut in as_completed(futures):
            recs = fut.result()  # classify_batch never raises (partial-failure tolerant)
            for c in recs:
                by_name[norm_name(c.get("raw_name", ""))] = c
            done += len(recs)
            completed += 1
            if checkpoint_path:
                flush_buffer.extend(recs)
                if completed % _CLASSIFY_FLUSH_EVERY == 0:
                    _append_classify_cache(checkpoint_path, flush_buffer)
                    flush_buffer = []
                    logger.info("classify: %d/%d forms done (%d/%d batches)",
                                done, len(todo), completed, len(batches))
        if checkpoint_path and flush_buffer:
            _append_classify_cache(checkpoint_path, flush_buffer)
    logger.info("classify: %d form(s) over %d batch(es), %d workers", done, len(batches), workers)
    return by_name


def _appl_id(pmid: str) -> str:
    return pmid[len(GRANT_PMID_PREFIX):] if pmid.startswith(GRANT_PMID_PREFIX) else pmid


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_corpus(
    mentions: list[dict],
    *,
    call_json,
    tool_registry: ToolRegistry,
    family_registry: FamilyRegistry,
    force_c_terms: list[str] | None = None,
    batch_size: int = 50,
    relabel_batch_size: int = 40,
    family_batch_size: int = 100,
    define_batch_size: int = 40,
    relabel: bool = True,
    define: bool = True,
    apply_family_overrides: bool = False,
    apply_consolidation: bool = False,
    apply_adhoc_dedup: bool = False,
    label_uniqueness: bool = True,
    checkpoint_dir=None,
) -> CorpusResult:
    """Run the A2 corpus pipeline over ``mentions`` and return registries + outputs.

    ``tool_registry`` / ``family_registry`` are the LOADED seed registries (the
    caller loads them — each with its embedding cache — so seed canonical ids
    carry forward, D-06, and registry NN matching is available). ``call_json`` is
    the shared classify/relabel seam. Set ``relabel=False`` to skip the §7.2 LLM
    relabel pass (a cheaper structural-only run for development).

    ``checkpoint_dir`` (a Path) makes the expensive classify pass resumable: a
    crash re-classifies only the still-missing forms. The cheap downstream stages
    (match/salience/family/relabel) re-run deterministically from the cache.
    """
    force_c_terms = force_c_terms if force_c_terms is not None else salience_mod.load_force_c_terms()

    uniques = group_mentions(mentions)
    logger.info("corpus run: %d raw mention(s) -> %d unique surface form(s)", len(mentions), len(uniques))

    # Pre-warm the embedding cache in ONE batched (parallel) embed of every form
    # text + the loaded registry's candidate texts. Without this, the match loop
    # embeds one text at a time (serialising on Titan latency); warmed, every
    # match call is pure in-memory matrix math.
    cache = getattr(tool_registry, "_cache", None)
    if cache is not None:
        prewarm = [u.raw_name for u in uniques]
        for r in tool_registry.records():
            prewarm.append(r["display_name"])
            prewarm.extend(r.get("aliases", []))
        cache.prewarm(prewarm)
        logger.info("pre-warmed embedding cache with %d unique text(s)", len(set(prewarm)))

    exceptions: list[dict] = []

    # 1. CLASSIFY each unique surface form (one disposition per form). ----------
    classify_inputs = [
        {"raw_name": u.raw_name, "tool_category": u.tool_category, "context": u.context, "pub_count": u.pub_count}
        for u in uniques
    ]
    ckpt_path = (Path(checkpoint_dir) / CLASSIFY_CHECKPOINT_NAME) if checkpoint_dir else None
    cls_by_name = _classify_resumable(
        classify_inputs, call_json=call_json, batch_size=batch_size, checkpoint_path=ckpt_path,
    )

    # 2. IDENTITY (match-or-mint), most prominent first. ------------------------
    counts = {"minted": 0, "attached": 0, "denied": 0, "unclassified": 0}
    occ_by_canonical: dict[str, list[dict]] = defaultdict(list)
    grant_signal: dict[str, dict] = defaultdict(lambda: {"appl_ids": set(), "investigator_cwids": set()})
    # Per-canonical supercategory VOTES across member forms (cid -> {supercat: pub-weight}).
    # The tool's supercategory is a deterministic majority of these (set in step 2b),
    # NOT whichever form happened to mint it — minter-wins is unstable across re-runs
    # (a changed form set reshuffles the minter and silently moves the tool's bucket,
    # which fragments the per-supercategory family layer downstream).
    sc_votes: dict[str, Counter] = defaultdict(Counter)

    for u in sorted(uniques, key=lambda x: -x.pub_count):
        c = cls_by_name.get(u.norm_key)
        if c is None or c["disposition"] is None:  # llm error / omitted
            counts["unclassified"] += 1
            exceptions.append({"type": EXC_UNCLASSIFIED, "raw_name": u.raw_name,
                               "flags": (c or {}).get("flags", [])})
            continue
        if c["disposition"] == "excluded":
            tool_registry.deny(u.raw_name)
            counts["denied"] += 1
            continue

        pub_ids = _pub_ids_of(u.occurrences)
        rec, action = tool_registry.match_or_mint(
            raw_name=u.raw_name, display_name=u.raw_name, disposition=c["disposition"],
            pub_ids=pub_ids, context=(u.context or None),
            context_by_pub=(u.context_by_pmid or None),
        )
        cid = rec["canonical_tool_id"]
        if action == "minted":
            counts["minted"] += 1
            tool_registry.update_classification(
                cid, disposition=c["disposition"], kind=c["kind"],
                supercategory=c["supercategory"], attributes=c["attributes"],
            )
            rec["flags"] = list(c.get("flags", []))
            if c.get("model"):
                rec["classified_by"] = c["model"]  # per-inference provenance
            _collect_classification_exceptions(exceptions, rec, c)
        else:
            counts["attached"] += 1
        if c["disposition"] == vocab.CAPABILITY_DISPOSITION and c.get("supercategory"):
            sc_votes[cid][c["supercategory"]] += max(len(pub_ids), 1)

        # Occurrence + grant-signal accretion (per canonical, across surface forms).
        for o in u.occurrences:
            occ_by_canonical[cid].append({**o, "canonical_tool_id": cid})
            if o.get("source_kind") == "grant":
                grant_signal[cid]["appl_ids"].add(_appl_id(str(o.get("pmid") or "")))
                if o.get("cwid"):
                    grant_signal[cid]["investigator_cwids"].add(o["cwid"])

    # 2b. Resolve each tool's supercategory by DETERMINISTIC weighted majority of its
    #     member forms (tiebreak: heavier pub-weight, then earlier/most-specific bucket
    #     in the frozen order so a tie never lands in `other`). Stable across re-runs.
    _sc_order = {s["id"]: i for i, s in enumerate(vocab.SUPERCATEGORIES)}
    for cid, votes in sc_votes.items():
        winner = max(votes.items(), key=lambda kv: (kv[1], -_sc_order.get(kv[0], len(_sc_order))))[0]
        tool_registry.update_classification(cid, supercategory=winner)
        if len(votes) > 1:  # member forms disagreed on the bucket — surface for review
            exceptions.append({
                "type": EXC_MERGE_SUPERCAT_DISAGREE, "canonical_tool_id": cid,
                "resolved_supercategory": winner,
                "votes": dict(sorted(votes.items(), key=lambda kv: -kv[1])),
            })

    # 2c. classified_by provenance — real per-form model where the classify seam stamped
    #     it; assumed aggregate model (Sonnet) + flag for cache entries that predate
    #     per-form stamping. Captures real provenance going forward, assumes it retroactively.
    n_assumed, n_real = stamp_assumed_classified_by(tool_registry, assumed_model=SONNET_MODEL)
    logger.info("classified_by: %d real (per-form), %d assumed (%s, flagged)", n_real, n_assumed, SONNET_MODEL)

    method_tools = [r for r in tool_registry.records() if r["disposition"] == vocab.CAPABILITY_DISPOSITION]

    # 3. GROUNDED salience (§5) — spread from real publication authorship. -------
    pub_spread: dict[str, set[str]] = defaultdict(set)
    for cid, occs in occ_by_canonical.items():
        for o in occs:
            if o.get("source_kind") != "grant" and o.get("cwid"):
                pub_spread[cid].add(o["cwid"])

    def _signal_of(rec: dict) -> dict:
        cid = rec["canonical_tool_id"]
        pub_count = len(rec.get("pub_ids") or [])
        spread = len(pub_spread.get(cid, ()))
        gsig = grant_signal.get(cid)
        grant_present = bool(gsig and (gsig["appl_ids"] or gsig["investigator_cwids"]))
        return {
            "pub_count": pub_count,
            "spread": spread,
            "grant_spread": len(gsig["investigator_cwids"]) if gsig else 0,
            "rrid_candidate": bool((rec.get("attributes") or {}).get("rrid_candidate")),
            "has_a2_signal": pub_count > 0 or grant_present,
        }

    _, thresholds = salience_mod.apply_grounded_salience(
        method_tools, signal_of=_signal_of, force_c_terms=force_c_terms,
    )

    # 4. FAMILY formation by capability CLASS (§7/§7.2). The LLM assigns each tool
    #    its broad class (the semantic knowledge a name embedding lacks), then the
    #    class strings merge within a supercategory. Replaces the greedy
    #    name-embedding match + per-singleton relabel, which could not group
    #    same-class/different-name tools (nivolumab vs pembrolizumab) and produced
    #    ~89% singleton families at A2 scale.
    from pipeline_tools.embeddings import EmbeddingCache
    cache = getattr(tool_registry, "_cache", None) or EmbeddingCache()
    relabel_deltas: list[dict] = []
    merge_deltas: list[dict] = []
    override_deltas: list[dict] = []
    consolidation_deltas: list[dict] = []
    adhoc_deltas: list[dict] = []
    label_uniqueness_deltas: list[dict] = []
    define_deltas: list[dict] = []
    if relabel:
        from pipeline_tools.family_rebuild import form_families
        from pipeline_tools.relabel import cross_supercategory_label_forks, dedup_families
        family_registry, tool_to_family = form_families(
            method_tools, call_json=call_json, embed_cache=cache, batch_size=family_batch_size,
            checkpoint_dir=checkpoint_dir,
        )
        # §7 dedup sweep — merge any within-bucket exact-label duplicate (reconcile
        # should leave none, but this is the durable safety net), then repoint moved tools.
        merge_deltas = dedup_families(family_registry)
        for d in merge_deltas:
            for moved in d["moved_tool_ids"]:
                tool_to_family[moved] = d["keep_id"]
        for tid, fid in tool_to_family.items():
            tool_registry.update_classification(tid, member_of_family=fid)
        for rec in method_tools:
            rec["member_of_family"] = tool_to_family.get(rec["canonical_tool_id"])
        # D-07 fix batch (v6→v7) — deterministic post-formation reroute/relabel/merge of
        # a closed, reviewed family set, BEFORE the guard re-checks (forks 17→~9). It
        # repoints supercategory/member_of_family in place on the shared records.
        # A2-corpus-specific (the table names concrete v6 family ids), so it is gated:
        # other run_corpus callers leave it off and are unaffected. On the A2 re-run it
        # validates fail-loud against the live ids (drift detection), per §3.8.
        if apply_family_overrides:
            from pipeline_tools.family_overrides import apply_overrides
            override_deltas = apply_overrides(family_registry, tool_registry)
        # 820-family consolidation batch (v7'→v8) — the human-reviewed within-supercategory
        # merges + relabels + per-family display tiers from method-family-consolidation.
        # Runs AFTER the D-07 batch (its ids reference the post-v7' set) and BEFORE the
        # guard, reusing the same fail-loud apply core. Gated like the overrides.
        if apply_consolidation:
            from pipeline_tools.family_consolidation import apply_consolidation as _apply_consolidation
            consolidation_deltas = _apply_consolidation(family_registry, tool_registry)
        # Ad-hoc dedup batch — the accreting, review-driven layer sourced from the scholar
        # co-assignment dupe scan (cli/find_family_dupes.py). Runs AFTER v820 (its drop_ids
        # reference post-v820 survivors) and BEFORE the guard, reusing the same fail-loud
        # apply core; carries reroutes so cross-supercategory near-dupes are expressible.
        if apply_adhoc_dedup:
            from pipeline_tools.family_adhoc_dedup import apply_adhoc_dedup as _apply_adhoc
            adhoc_deltas = _apply_adhoc(family_registry, tool_registry)
        # §7 cross-supercategory guard — flag (don't merge) labels forked across buckets.
        forks = cross_supercategory_label_forks(family_registry)
        for fk in forks:
            exceptions.append({"type": EXC_CROSS_SUPERCATEGORY, **fk})
        # #215 label-uniqueness invariant — deterministic cross-supercategory disambiguation
        # + hard gate. Runs AFTER the ad-hoc dedup (config merges/relabels take precedence by
        # construction) and the cross-SC guard (whose fork audit above we keep intact), and
        # BEFORE the faculty rollup / artifact emit. Appends a supercategory-derived qualifier
        # so genuine keep-separate forks (AAV reagent vs therapeutic) never publish two
        # identical chips; a residual same-supercategory collision (a missed merge) fails loud.
        if label_uniqueness:
            from pipeline_tools.family_label_uniqueness import enforce_label_uniqueness
            label_uniqueness_deltas = enforce_label_uniqueness(family_registry)
    else:
        family_registry = FamilyRegistry([], cache=cache)  # tools-only dev run (no LLM)
        forks = []
    fam_counts = {"minted": len(family_registry), "attached": 0, "flagged": len(forks)}

    # #879 — generate the render-only family DEFINITION after labels are stable (the
    # define prompt grounds on the final label + members). Gated on `relabel` (the else
    # branch forms no labels) and the `define` toggle. Partial-failure tolerant, so a
    # define hiccup leaves definition=null and never aborts the corpus run.
    if relabel and define:
        from pipeline_tools.define_families import define_families
        define_deltas = define_families(
            family_registry, call_json=call_json, batch_size=define_batch_size,
        )

    # 4b. routing-sanity net — deterministic likely-misroute flags (flag-only).
    for rec in method_tools:
        sflags = sanity_mod.routing_sanity_flags(rec)
        if sflags:
            exceptions.append({
                "type": EXC_ROUTING_SANITY, "canonical_tool_id": rec["canonical_tool_id"],
                "tool": rec["display_name"], "supercategory": rec.get("supercategory"),
                "kind": rec.get("kind"), "checks": [f["check"] for f in sflags],
                "reasons": [f["reason"] for f in sflags],
            })

    # 5. (families are formed by capability class in stage 4 — no separate relabel
    #    or dedup sweep; the broaden+consolidate pass produces the final labels.)

    # 6. FACULTY ROLLUP (Step F). -----------------------------------------------
    tool_index = {
        r["canonical_tool_id"]: {
            "display_name": r["display_name"], "disposition": r["disposition"],
            "salience_tier": r.get("salience_tier"), "member_of_family": r.get("member_of_family"),
            "supercategory": r.get("supercategory"),
        }
        for r in tool_registry.records()
    }
    family_index = {f["family_id"]: {"label": f.get("label"), "supercategory": f.get("supercategory")}
                    for f in family_registry.records()}
    all_occurrences = [o for occs in occ_by_canonical.values() for o in occs]
    faculty_rollup = build_faculty_rollup(all_occurrences, tool_index=tool_index, family_index=family_index)

    # 7. §9 outputs + telemetry. ------------------------------------------------
    result = CorpusResult(tool_registry=tool_registry, family_registry=family_registry)
    result.records = _enriched_records(tool_registry, family_registry)
    # #193: per-publication usage context, keyed by canonical_tool_id, accreted on
    # the registry across surface forms. Kept OUT of the enriched records (so
    # tools.json stays lean) and published as a separate tool_context.json sidecar.
    result.tool_context = {
        r["canonical_tool_id"]: {p: cbp[p] for p in sorted(cbp)}
        for r in tool_registry.records()
        if (cbp := r.get("context_by_pub"))
    }
    # #1166 — specific-entity (cell-line) layer for Methods Surface B. A pure
    # projection over the assembled tool records + tool_context (the entity grain
    # == the tool grain for cell lines; see pipeline_tools.entities). The
    # render-only parent DESCRIPTOR is filled by an LLM define-pass (gated on
    # `define`, like the #879 family definitions), partial-failure tolerant.
    from pipeline_tools.entities import (
        apply_parent_descriptors,
        build_entity_layer,
        define_entity_parents,
    )
    result.entities, result.entity_context, _entity_parents = build_entity_layer(
        result.records, family_registry.records(), result.tool_context,
    )
    if define and result.entities and _entity_parents:
        _descriptors = define_entity_parents(
            _entity_parents, call_json=call_json, batch_size=define_batch_size,
        )
        apply_parent_descriptors(result.entities, _descriptors)
        result.entity_define_deltas = [
            {"parent_entity_id": pid, "descriptor": d} for pid, d in sorted(_descriptors.items())
        ]

    result.hierarchy = _build_hierarchy(tool_registry, family_registry)
    result.exceptions = exceptions
    result.faculty_rollup = faculty_rollup
    result.grant_signal = {
        cid: {"appl_ids": sorted(s["appl_ids"]), "investigator_cwids": sorted(s["investigator_cwids"])}
        for cid, s in grant_signal.items() if s["appl_ids"] or s["investigator_cwids"]
    }
    result.relabel_deltas = relabel_deltas
    result.merge_deltas = merge_deltas
    result.override_deltas = override_deltas
    result.consolidation_deltas = consolidation_deltas
    result.adhoc_deltas = adhoc_deltas
    result.label_uniqueness_deltas = label_uniqueness_deltas
    result.define_deltas = define_deltas
    result.thresholds = thresholds
    result.telemetry = _telemetry(
        tool_registry, family_registry, method_tools, counts, fam_counts,
        exceptions, faculty_rollup, result.grant_signal, thresholds, _signal_of,
    )
    logger.info(
        "corpus run: %d unique -> %d canonical tools (%d minted, %d attached, %d denied, %d unclassified); "
        "%d families; %d exceptions; %d faculty",
        len(uniques), len(tool_registry), counts["minted"], counts["attached"], counts["denied"],
        counts["unclassified"], len(family_registry), len(exceptions), len(faculty_rollup),
    )
    return result


def _telemetry(
    tools, families, method_tools, counts, fam_counts, exceptions,
    faculty_rollup, grant_signal, thresholds, signal_of,
) -> dict:
    minted, attached = counts["minted"], counts["attached"]

    def _dist(field_name: str) -> dict:
        d: dict[str, int] = {}
        for r in tools.records():
            d[r.get(field_name) or "∅"] = d.get(r.get(field_name) or "∅", 0) + 1
        return dict(sorted(d.items(), key=lambda kv: (-kv[1], kv[0])))

    exc_by_type: dict[str, int] = {}
    for e in exceptions:
        exc_by_type[e["type"]] = exc_by_type.get(e["type"], 0) + 1

    grounded = [r for r in method_tools if signal_of(r)["has_a2_signal"]]
    spreads = sorted((signal_of(r)["spread"] for r in grounded), reverse=True)
    return {
        "unique_forms": sum(counts.values()),
        "canonical_tools": len(tools),
        "tool_minted": minted,
        "tool_attached": attached,
        "tool_denied": counts["denied"],
        "tool_unclassified": counts["unclassified"],
        "mint_vs_attach_ratio": round(minted / attached, 3) if attached else None,
        "families": len(families),
        "family_minted": fam_counts.get("minted", 0) + fam_counts.get("flagged", 0),
        "family_attached": fam_counts.get("attached", 0),
        "family_flagged_cross_supercat": fam_counts.get("flagged", 0),
        "method_tools_unfamilied": sum(1 for r in method_tools if not r.get("member_of_family")),
        "salience_distribution": _dist("salience_tier"),
        "salience_basis_distribution": _dist("salience_tier_basis"),
        "disposition_distribution": _dist("disposition"),
        "supercategory_distribution": _dist("supercategory"),
        "classified_by_distribution": _dist("classified_by"),
        "classified_by_assumed_count": sum(1 for r in tools.records() if r.get("classified_by_assumed")),
        "method_tools": len(method_tools),
        "method_tools_grounded": len(grounded),
        "s_spread_cutoff": getattr(thresholds, "s_spread_min", None),
        "max_spread": spreads[0] if spreads else 0,
        "grant_signal_tools": len(grant_signal),
        "faculty_count": len(faculty_rollup),
        "exceptions_total": len(exceptions),
        "exceptions_by_type": exc_by_type,
    }


# ---------------------------------------------------------------------------
# Persistence — accreted registries + the §9 review artifacts + rollup + payload.
# ---------------------------------------------------------------------------


def write_outputs(result: CorpusResult, out_dir: Path, *, payload: dict | None = None) -> list[Path]:
    """Write the accreted registries + A2 review artifacts to ``out_dir``.

    Files: the two persistent registries (tool/family) + denylist (the accreted
    durable state, D-06), the §9a/§9c review artifacts, the faculty rollup, the
    separate grant signal, telemetry, and — when supplied — the assembled
    ``tools.json`` publish payload. These are for HUMAN REVIEW before publish
    (D-07); nothing here uploads to S3/DDB.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    result.tool_registry.save(out_dir / "tool_registry.json", out_dir / "tool_denylist.json")
    result.family_registry.save(out_dir / "family_registry.json")

    def _dump(name: str, obj) -> Path:
        path = out_dir / name
        path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    paths = [
        out_dir / "tool_registry.json",
        out_dir / "tool_denylist.json",
        out_dir / "family_registry.json",
        _dump("tool_taxonomy_corpus.json", {"tools": result.records}),
        _dump("tool_hierarchy_corpus.json", result.hierarchy),
        _dump("tool_exceptions_corpus.json", result.exceptions),
        _dump("tool_faculty_rollup.json", result.faculty_rollup),
        _dump("tool_grant_signal.json", result.grant_signal),
        _dump("tool_relabel_deltas.json", {"relabel": result.relabel_deltas, "merge": result.merge_deltas,
                                            "overrides": result.override_deltas,
                                            "consolidation": result.consolidation_deltas}),
        _dump("tool_telemetry_corpus.json", result.telemetry),
    ]
    if payload is not None:
        paths.append(_dump("tools.json", payload))
    return paths

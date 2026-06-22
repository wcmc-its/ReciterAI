"""#1166 — specific-entity (cell-line) resolution stage for Methods Surface B.

Surface B on the SPS Method-family page (``/methods/[supercategory]/[family]``)
discovers the SPECIFIC named entities a method family resolves to — e.g. the
``Immortalized cell lines`` family resolves to ``3T3-L1 adipocytes`` (11 papers),
``HEK293T cells`` (5), etc. — with a per-(publication x entity) usage sentence on
each matching paper and a parent-nested directory ("the two 3T3-L1 forms under
one parent"). None of that grain exists in the tools artifact today; this stage
derives it.

Key real-data finding that shapes this stage (the collapse-hazard probe, plan §1):
the tool registry already mints each specific cell line as its OWN canonical tool
record (``3T3-L1 adipocytes`` = ``tool_000718`` and ``3T3-L1 preadipocytes`` =
``tool_001070`` are DISTINCT, not collapsed). So for cell-line families the entity
grain == the tool grain, and this stage is a PROJECTION over the assembled
artifact, NOT a corpus re-extraction:

  * ``normalized_entity_id`` = the member tool's ``canonical_tool_id`` (the alias
    set already does synonym/casing normalization).
  * ``usage_count`` = the tool's institution-wide ``pub_count`` (authoritative
    ranking number — NOT a count of sentence rows, which is a subset).
  * per-(pub x entity) ``usage_sentence`` = the existing per-(tool, pmid) snippet
    from ``tool_context`` (cid -> {pmid: snippet}); ``matched_span`` +
    ``centrality_score`` are computed here (``context_quality``).
  * ``parent_entity_id`` + nesting = the one genuinely-new thing — grouped
    deterministically by the cell-line core token (``3T3-L1`` for both forms; a
    separate ``NIH 3T3`` line is NOT merged in). The human-readable parent
    DESCRIPTOR ("mouse fibroblast line") is filled by :func:`define_entity_parents`
    (the LLM define-pass, plan decision D-desc), left ``None`` when no LLM is wired
    (SPS then shows "· N forms" without prose).

What this stage deliberately does NOT do (deferred to #1166-B, which needs a
widened extract prompt + a corpus re-run): ``entity_role``/``form`` variants, and
true multi-sentence-per-(pub x entity) multiplicity. Here every (entity, pmid)
carries at most one sentence (the existing best snippet), but the artifact shape
is already a LIST per (entity, pmid) so #1166-B is additive.

Output is two artifact objects, mirroring the entity DIMENSION vs the per-pub
FACTS so SPS can load them into two tables (the strip/directory rank the
dimension by ``usage_count``; the filtered article snippets join the facts):

  entities.json       -> list[EntityRecord]            (one per specific entity)
  entity_context.json -> {entity_id: {pmid: [Usage]}}  (per-(pub x entity) facts)
"""

from __future__ import annotations

import hashlib
import logging
import re

from pipeline_tools.context_quality import (
    MAX_SENTENCE_CHARS,
    MIN_SNIPPET_CHARS,
    centrality_score,
    compute_matched_span,
    is_sentence_complete,
    is_single_sentence,
    salient_name_forms,
)

logger = logging.getLogger(__name__)

# The families this stage projects. Surface B v1 is cell lines; the predicate is
# kept narrow + explicit so the entity layer never silently spans unrelated axes,
# but it keys on the FROZEN ``dominant_kind`` (not a brittle label match) plus a
# "cell line" label guard, so new cell-line families are picked up automatically.
CELL_KIND = "organism_or_cells"
_CELL_LINE_LABEL = re.compile(r"cell line", re.IGNORECASE)

# A parent group needs at least this many child forms to be worth nesting (a lone
# entity stays top-level — nesting a single row is just noise, spec §5.6).
MIN_PARENT_FORMS = 2

# A token is a distinctive cell-line designator if it carries a digit (3T3-L1,
# HEK293T, MS1). The "core" of an entity name is its leading run up to and
# including the LAST such token; trailing tokens (adipocytes / cells / line) are
# the differentiation/form. Pure-alpha names (e.g. "human hepatocyte cell line")
# have no digit token -> no core -> never grouped (conservative; v1 keeps them flat).
_HAS_DIGIT = re.compile(r"\d")


def is_cell_line_family(family: dict) -> bool:
    """The default scope predicate: an active cell-line family."""
    if family.get("status") not in (None, "active"):
        return False
    if family.get("dominant_kind") != CELL_KIND:
        return False
    return bool(_CELL_LINE_LABEL.search(family.get("label") or ""))


def _tokens(name: str) -> list[str]:
    return [t for t in re.split(r"\s+", (name or "").strip()) if t]


def parent_core(display_name: str) -> str | None:
    """The cell-line core of a name (the line identifier), or None if not nestable.

    ``"3T3-L1 adipocytes"`` -> ``"3T3-L1"``; ``"NIH 3T3 cells"`` -> ``"NIH 3T3"``;
    ``"MS1 VEGF angiosarcoma cells"`` -> ``"MS1"``; ``"human hepatocyte cell line"``
    -> None (no digit token). The core MUST be a proper prefix of the name (there
    must be trailing differentiation/form tokens) for the entity to nest — a name
    that IS its core is top-level.
    """
    toks = _tokens(display_name)
    if len(toks) < 2:
        return None
    last_digit_idx = -1
    for i, t in enumerate(toks):
        if _HAS_DIGIT.search(t):
            last_digit_idx = i
    if last_digit_idx < 0 or last_digit_idx == len(toks) - 1:
        # No digit token, or the digit token is the final token (no trailing form
        # to differentiate) -> not nestable.
        return None
    core = " ".join(toks[: last_digit_idx + 1])
    return core if len(core) >= 3 else None


def _parent_id(supercategory: str, family_label: str, core_key: str) -> str:
    """Durable opaque parent id, content-derived from (family, core).

    Unlike ``canonical_tool_id`` (a monotonic mint that must survive re-cluster
    because faculty scores key on it), a parent has NO durable downstream score
    keyed on it and SPS rebuilds the whole entity table on each ETL, so a
    content hash gives the strongest property for free: the SAME core in the SAME
    family yields the SAME id across runs, with no minter/insertion-order state.
    """
    h = hashlib.sha1(f"{supercategory}|{family_label}|{core_key}".encode("utf-8")).hexdigest()
    return f"ent_{h[:12]}"


def _usable_sentence(text: str) -> bool:
    """A snippet fit to display as a usage sentence (length + single-sentence).

    Mirrors the tool-context junk guard's intent without re-checking verbatimness
    (the snippet already came from the verified ``tool_context`` artifact).
    """
    if not text or not isinstance(text, str):
        return False
    s = text.strip()
    if not (MIN_SNIPPET_CHARS <= len(s) <= MAX_SENTENCE_CHARS):
        return False
    return is_single_sentence(s)


def build_entity_layer(
    tools: list[dict],
    families: list[dict],
    tool_context: dict,
    *,
    scope=is_cell_line_family,
) -> tuple[list[dict], dict, list[dict]]:
    """Project the assembled tools artifact into the entity dimension + facts.

    Returns ``(entities, entity_context, parents)`` where:
      * ``entities``       — list of entity-dimension records (entities.json).
      * ``entity_context`` — ``{entity_id: {pmid: [usage]}}`` facts (entity_context.json),
                             sorted for byte-stable republish.
      * ``parents``        — ``[{parent_entity_id, parent_label, supercategory,
                             family_label, member_display_names, form_count}]`` for the
                             descriptor define-pass (:func:`define_entity_parents`).

    Pure + deterministic (no I/O, no LLM, no clock/RNG) so it runs standalone over a
    published ``tools.json`` and is unit-testable byte-for-byte.
    """
    tools_by_id = {t.get("canonical_tool_id"): t for t in tools if t.get("canonical_tool_id")}
    entities: list[dict] = []
    entity_context: dict[str, dict[str, list[dict]]] = {}
    parents: list[dict] = []

    for fam in sorted(families, key=lambda f: (f.get("supercategory") or "", f.get("label") or "")):
        if not scope(fam):
            continue
        supercategory = fam.get("supercategory") or ""
        family_label = fam.get("label") or ""
        members = [
            tools_by_id[mid]
            for mid in (fam.get("member_tool_ids") or [])
            if mid in tools_by_id and (tools_by_id[mid].get("display_name") or "").strip()
        ]
        if not members:
            continue

        # --- parent grouping (deterministic; the one net-new structure) ---
        core_by_member: dict[str, str | None] = {}
        core_members: dict[str, list[dict]] = {}
        for t in members:
            core = parent_core(t.get("display_name") or "")
            core_by_member[t["canonical_tool_id"]] = core
            if core is not None:
                core_members.setdefault(core.lower(), []).append(t)

        parent_id_by_member: dict[str, str] = {}
        parent_label_by_member: dict[str, str] = {}
        for core_key, grp in core_members.items():
            if len(grp) < MIN_PARENT_FORMS:
                continue
            # Parent label = the core in its original casing (from the first member).
            label_tokens = parent_core(grp[0]["display_name"]) or grp[0]["display_name"]
            pid = _parent_id(supercategory, family_label, core_key)
            for t in grp:
                parent_id_by_member[t["canonical_tool_id"]] = pid
                parent_label_by_member[t["canonical_tool_id"]] = label_tokens
            parents.append({
                "parent_entity_id": pid,
                "parent_label": label_tokens,
                "supercategory": supercategory,
                "family_label": family_label,
                "member_display_names": [t.get("display_name") for t in grp],
                "form_count": len(grp),
            })

        # --- entity dimension + per-(pub x entity) facts ---
        for t in members:
            eid = t["canonical_tool_id"]
            display = t.get("display_name") or ""
            # Salient forms = the display name + registry aliases + the bare line
            # CORE ("3T3-L1" for "3T3-L1 adipocytes"), so a sentence that names the
            # line by its bare designator ("...regulates 3T3-L1 adipogenesis...")
            # still resolves a span + centrality instead of degrading to null.
            extra_forms = list(t.get("aliases") or [])
            core = parent_core(display)
            if core:
                extra_forms.append(core)
            forms = salient_name_forms(display, extra_forms)
            ctx = tool_context.get(eid) or {}

            usages: dict[str, list[dict]] = {}
            for pmid, sentence in ctx.items():
                if not _usable_sentence(sentence):
                    continue
                span = compute_matched_span(sentence, forms)
                usages[str(pmid)] = [{
                    "usage_sentence": sentence.strip(),
                    "span": [span[0], span[1]] if span else None,
                    "centrality_score": centrality_score(sentence, forms),
                    # #254: does this snippet read as a whole sentence vs a mid-clause
                    # fragment? Additive hint so SPS shows its leading/trailing ellipsis
                    # only on the residual fragments; the durable text fix is the
                    # re-projection backfill (cli/rebuild_entity_context) over the
                    # #239 sentence-aligned tool_context this snippet is sourced from.
                    "sentence_complete": is_sentence_complete(sentence),
                    "role": None,  # #1166-B
                }]
            if usages:
                entity_context[eid] = {pmid: usages[pmid] for pmid in sorted(usages)}

            entities.append({
                "normalized_entity_id": eid,
                "entity_label": display,
                "canonical_tool_id": eid,
                "supercategory": supercategory,
                "family_label": family_label,
                "parent_entity_id": parent_id_by_member.get(eid),
                "parent_label": parent_label_by_member.get(eid),  # the line core, e.g. "3T3-L1"
                "parent_descriptor": None,  # filled by define_entity_parents
                "entity_role": None,        # #1166-B
                # Authoritative ranking number: institution-wide distinct pubs. NOT
                # len(usages) — sentences are only kept where a usable snippet exists.
                "usage_count": int(t.get("pub_count") or 0),
                "evidenced": bool(usages),  # spec §7 is_evidenced -> clickable affordance
            })

    # Sort entities for a stable artifact: family, then usage_count desc, then id.
    entities.sort(key=lambda e: (e["supercategory"], e["family_label"], -e["usage_count"], e["normalized_entity_id"]))
    parents.sort(key=lambda p: (p["supercategory"], p["family_label"], p["parent_entity_id"]))
    return entities, dict(sorted(entity_context.items())), parents


# ---------------------------------------------------------------------------
# Descriptor define-pass (plan decision D-desc — ReciterAI emits the parent
# descriptor). A pure-text LLM pass over the parent groups, mirroring
# ``define_families`` exactly (injected ``call_json`` seam, batch + one bounded
# re-prompt, partial-failure tolerant, render-only output). Left unrun when no
# ``call_json`` is wired: every ``parent_descriptor`` simply stays None.
# ---------------------------------------------------------------------------

DEFINE_BATCH = 40
_DESC_MAX_WORDS = 12
_DESC_MIN_CHARS = 6
_BANNED_DESC = (
    "cell line", "cell lines",  # the descriptor adds info BEYOND "cell line"
    "cutting-edge", "state-of-the-art", "world-class", "gold standard", "powerful",
)


def _validate_descriptor(text: str) -> tuple[bool, str]:
    t = (text or "").strip()
    if len(t) < _DESC_MIN_CHARS:
        return False, "too short / empty"
    if len(t.split()) > _DESC_MAX_WORDS:
        return False, f"too long (> {_DESC_MAX_WORDS} words)"
    low = t.lower()
    for phrase in _BANNED_DESC:
        if phrase in low:
            return False, f"banned phrase: {phrase!r}"
    return True, ""


def define_entity_parents(parents: list[dict], *, call_json, batch_size: int = DEFINE_BATCH) -> dict[str, str]:
    """Generate a short descriptor ("mouse fibroblast line") for each parent group.

    Returns ``{parent_entity_id: descriptor}`` for the parents successfully defined
    (a parent that fails validation twice is simply omitted -> descriptor stays None).
    Apply the result with :func:`apply_parent_descriptors`. Never raises: an LLM
    error leaves the affected batch undescribed (the run never aborts).
    """
    from prompts.entity_parent_define import (
        ENTITY_PARENT_DEFINE_PROMPT,
        build_entity_parent_message,
    )

    out: dict[str, str] = {}
    failures: dict[str, str] = {}

    def _apply(batch: list[dict], prior: dict | None) -> None:
        try:
            resp = call_json(ENTITY_PARENT_DEFINE_PROMPT, build_entity_parent_message(batch, prior_failures=prior))
            entries = resp.get("parents", []) if isinstance(resp, dict) else []
        except Exception as exc:  # noqa: BLE001 — partial-failure tolerance by design
            logger.warning("entity-parent define batch failed (%s); %d parent(s) undescribed", exc, len(batch))
            for p in batch:
                failures.setdefault(p["parent_entity_id"], "llm call failed")
            return
        by_id = {e.get("parent_entity_id"): e for e in entries if isinstance(e, dict) and e.get("parent_entity_id")}
        for p in batch:
            pid = p["parent_entity_id"]
            desc = ((by_id.get(pid) or {}).get("descriptor") or "").strip()
            ok, reason = _validate_descriptor(desc)
            if not desc or not ok:
                failures[pid] = reason or "no descriptor returned"
                continue
            failures.pop(pid, None)
            out[pid] = desc

    batches = [parents[i:i + batch_size] for i in range(0, len(parents), batch_size)]
    for batch in batches:
        _apply(batch, None)

    retry = [p for p in parents if p["parent_entity_id"] in failures]
    if retry:
        for i in range(0, len(retry), batch_size):
            batch = retry[i:i + batch_size]
            prior = {p["parent_entity_id"]: failures[p["parent_entity_id"]] for p in batch}
            _apply(batch, prior)

    logger.info("entity-parent define: %d/%d parents described", len(out), len(parents))
    return out


def apply_parent_descriptors(entities: list[dict], descriptors: dict[str, str]) -> None:
    """Attach ``{parent_entity_id: descriptor}`` onto each entity's ``parent_descriptor`` in place."""
    for e in entities:
        pid = e.get("parent_entity_id")
        if pid and pid in descriptors:
            e["parent_descriptor"] = descriptors[pid]

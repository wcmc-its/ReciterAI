"""#1166 — specific-entity resolution stage for Methods Surface B.

Surface B on the SPS Method-family page (``/methods/[supercategory]/[family]``)
discovers the SPECIFIC named entities a method family resolves to — e.g. the
``Immortalized cell lines`` family resolves to ``3T3-L1 adipocytes`` (11 papers),
``HEK293T cells`` (5), etc. — with a per-(publication x entity) usage sentence on
each matching paper and a parent-nested directory ("the two 3T3-L1 forms under
one parent"). None of that grain exists in the tools artifact today; this stage
derives it.

The default scope (:func:`is_projectable_family`) spans every specific-entity
``kind`` — datasets (named cohorts), software, instruments, reagents, organisms,
models — and EXCLUDES the generic-dominated ``method``/``assay`` kinds (see
:data:`PROJECTED_KINDS`). The parent-nesting below is gated to cell-line families
(its digit-token core is a cell-line designator); every other kind projects FLAT
(no parent), which is correct for v1. Pass ``scope=is_cell_line_family`` for the
original cell-line-only projection.

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
import json
import logging
import re
from pathlib import Path

from pipeline_tools.context_quality import (
    MAX_SENTENCE_CHARS,
    MIN_SNIPPET_CHARS,
    centrality_score,
    compute_matched_span,
    informativeness_score,
    is_sentence_complete,
    is_single_sentence,
    mention_class,
    salient_name_forms,
)
from pipeline_tools.registry import norm_name
from pipeline_tools.vocab import KIND_SET

logger = logging.getLogger(__name__)

# #252 generics blocklist — curated generic CATEGORY names ("macrophage cell line")
# that are not a specific entity. Loaded by the caller (corpus_run / the backfill)
# and passed into build_entity_layer; absent file or unset param -> nothing flagged.
DEFAULT_GENERICS_PATH = Path(__file__).resolve().parent.parent / "config" / "method_generics_blocklist.json"

# The families this stage projects. The DEFAULT scope is now ``is_projectable_family``
# (all specific-entity kinds — see PROJECTED_KINDS below); ``is_cell_line_family`` is the
# original cell-line-only predicate, kept as the explicit opt-in for reproducible
# cell-line runs (pass ``scope=is_cell_line_family``). Both key on the FROZEN
# ``dominant_kind`` enum, never a brittle label match — except the cell-line predicate
# adds a "cell line" label guard so it stays inside the cell-line axis.
CELL_KIND = "organism_or_cells"
_CELL_LINE_LABEL = re.compile(r"cell line", re.IGNORECASE)

# Kinds whose families resolve to SPECIFIC named entities, projected into the entity
# layer by default. We deliberately EXCLUDE ``method`` and ``assay``: those families are
# dominated by generic procedure / study-design / statistics names ("logistic regression",
# "PCR", "ELISA") that the curated generics blocklist cannot yet suppress at scale (the
# #252 WS-B vocabulary work), so projecting them would flood the SPS rail with non-specific
# clickable rows. The remaining kinds resolve to specific entities: datasets -> named
# cohorts (SEER/NCDB), software -> REDCap/R, instruments -> named platforms, reagents ->
# specific antibodies/plasmids, organisms -> strains, models -> named artifacts. An
# allowlist (NOT ``not in {method, assay}``) so any unvetted future kind fails closed.
_EXCLUDED_KINDS = frozenset({"method", "assay"})
PROJECTED_KINDS = KIND_SET - _EXCLUDED_KINDS

# A parent group needs at least this many child forms to be worth nesting (a lone
# entity stays top-level — nesting a single row is just noise, spec §5.6).
MIN_PARENT_FORMS = 2

# A token is a distinctive cell-line designator if it carries a digit (3T3-L1,
# HEK293T, MS1). The "core" of an entity name is its leading run up to and
# including the LAST such token; trailing tokens (adipocytes / cells / line) are
# the differentiation/form. Pure-alpha names (e.g. "human hepatocyte cell line")
# have no digit token -> no core -> never grouped (conservative; v1 keeps them flat).
_HAS_DIGIT = re.compile(r"\d")


def load_generic_terms(path: Path = DEFAULT_GENERICS_PATH) -> list[str]:
    """The #252 generics blocklist (fail-open: missing file -> none). Mirrors salience.load_force_c_terms."""
    if not path.exists():
        logger.warning("generics blocklist not found at %s; flagging none is_generic.", path)
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("generic_terms", []))


def matches_generic(display_name: str, terms: list[str]) -> bool:
    """True if the entity NAME is a generic category, not a specific entity (#252).

    A curated generic phrase ("macrophage cell line", "fibroblast cells") appears as
    a whole word AND the name carries no distinctive line designator (a digit-bearing
    token like 3T3 / HEK293 / HMC-1), so a SPECIFIC digit-coded line is never flagged
    even when its name contains a generic head noun ("3T3 fibroblast cells" stays
    specific). Whole-word match on norm_name, mirroring salience.matches_force_c. This
    is a FLAG, never a drop — SPS owns the display decision via family_entity.is_generic.
    """
    if not terms or _HAS_DIGIT.search(display_name or ""):
        return False
    hay = f" {norm_name(display_name)} "
    return any((needle := norm_name(t)) and f" {needle} " in hay for t in terms)


def is_projectable_family(family: dict) -> bool:
    """The default scope: an active family whose ``dominant_kind`` resolves to specific
    named entities (every kind except the generic-dominated method/assay — see
    :data:`PROJECTED_KINDS`).

    Supersedes :func:`is_cell_line_family` as the default; pass
    ``scope=is_cell_line_family`` to reproduce the original cell-line-only projection.
    """
    if family.get("status") not in (None, "active"):
        return False
    return family.get("dominant_kind") in PROJECTED_KINDS


def is_cell_line_family(family: dict) -> bool:
    """The original cell-line-only scope predicate (opt-in via ``scope=``)."""
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


# #1166-B — max usage sentences kept per (entity, pmid). The live top-1 snippet
# (from tool_context) plus any augmentation candidates (cli/extract_entity_augment)
# are deduped, ranked by informativeness then centrality, and capped here. A
# default-empty augment ⇒ the list stays length 1 ⇒ byte-identical to the original
# single-sentence projection (so the no-augment re-projection / unit tests are
# unaffected).
MAX_USAGE_PER_PAIR = 3


def _norm_sentence(s: str) -> str:
    """Whitespace/case-fold key for de-duping near-identical usage sentences."""
    return " ".join((s or "").lower().split())


def _usage_fact(sentence: str, forms) -> dict:
    """One entity_context usage fact from a sentence + the entity's name forms.

    Scores are computed on the sentence as-passed and the text stored ``.strip()``-ed
    — byte-identical to the original inline construction, so the live top-1 snippet
    projects unchanged.
    """
    span = compute_matched_span(sentence, forms)
    return {
        "usage_sentence": sentence.strip(),
        "span": [span[0], span[1]] if span else None,
        "centrality_score": centrality_score(sentence, forms),
        # #253: specific experimental use vs generic background mention.
        # informativeness_score drives sentence selection (then centrality);
        # mention_class drives the SPS badge ("How it was used" vs "Where it appears").
        "informativeness_score": informativeness_score(sentence, forms),
        "mention_class": mention_class(sentence, forms),
        # #254: whole sentence vs mid-clause fragment (SPS ellipsis hint).
        "sentence_complete": is_sentence_complete(sentence),
        "role": None,  # #1166-B (entity_role / form variant — deferred)
    }


def build_entity_layer(
    tools: list[dict],
    families: list[dict],
    tool_context: dict,
    *,
    scope=is_projectable_family,
    generic_terms: list[str] | None = None,
    context_augment: dict[str, list[str]] | None = None,
    max_per_pair: int = MAX_USAGE_PER_PAIR,
) -> tuple[list[dict], dict, list[dict]]:
    """Project the assembled tools artifact into the entity dimension + facts.

    Returns ``(entities, entity_context, parents)`` where:
      * ``entities``       — list of entity-dimension records (entities.json).
      * ``entity_context`` — ``{entity_id: {pmid: [usage]}}`` facts (entity_context.json),
                             sorted for byte-stable republish.
      * ``parents``        — ``[{parent_entity_id, parent_label, supercategory,
                             family_label, member_display_names, form_count}]`` for the
                             descriptor define-pass (:func:`define_entity_parents`).

    ``max_per_pair`` caps the kept sentences per (entity, pmid) — defaults to
    ``MAX_USAGE_PER_PAIR``. Pass 1 to publish the single best sentence while the SPS
    UI still renders one per pair (raise once a multi-snippet feed lands).

    ``context_augment`` (#1166-B) is an optional ``{pmid: [extra sentence, …]}`` map
    of additional verbatim tool-naming sentences (from a multi-sentence re-extraction,
    cli/extract_entity_augment). For each existing (entity, pmid) pair, augmentation
    sentences that NAME this entity (a resolvable span) are merged with the live top-1
    snippet, deduped, ranked by informativeness then centrality, and capped at
    ``MAX_USAGE_PER_PAIR``. It NEVER adds new (entity, pmid) pairs (depth, not coverage)
    and is purely additive: omit it and the projection is byte-identical to before.

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
        # #252 0-count suppression: never emit a phantom whose in-scope usage_count
        # (institution-wide pub_count) is 0 — the registry minted it but no in-corpus
        # publication uses it (MDCK/MEF/NIH-3T3 phantoms). Dropped BEFORE parent
        # grouping so a phantom never inflates a parent's form_count either. Pure +
        # deterministic; lowers manifest.counts.entities (content, not schema).
        members = [t for t in members if int(t.get("pub_count") or 0) > 0]
        if not members:
            continue

        # --- parent grouping (deterministic; the one net-new structure) ---
        # CELL-LINE-ONLY: parent_core's digit-token heuristic is a cell-line designator
        # ("3T3-L1") and is meaningless for other kinds — it would mis-nest "AAV9 vector"
        # + "AAV9 capsid" under "AAV9", and feed those groups to the cell-line-specific
        # define-pass (define_entity_parents). So non-cell-line families project FLAT (no
        # parent), gated on the same predicate as the original cell-line scope.
        parent_id_by_member: dict[str, str] = {}
        parent_label_by_member: dict[str, str] = {}
        if is_cell_line_family(fam):
            core_members: dict[str, list[dict]] = {}
            for t in members:
                core = parent_core(t.get("display_name") or "")
                if core is not None:
                    core_members.setdefault(core.lower(), []).append(t)
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
            aug = context_augment or {}

            usages: dict[str, list[dict]] = {}
            for pmid, sentence in ctx.items():
                if not _usable_sentence(sentence):
                    continue
                # Candidate sentences for this (entity, pmid): the live top-1 snippet
                # plus any augmentation sentence (#1166-B) for the SAME pmid that NAMES
                # this entity (a resolvable span ⇒ the entity's form appears) and is not
                # a near-duplicate. Ranked by informativeness then centrality, capped at
                # MAX_USAGE_PER_PAIR. Empty augment ⇒ a single fact, byte-identical to before.
                seen = {_norm_sentence(sentence)}
                facts = [_usage_fact(sentence, forms)]
                for extra in aug.get(str(pmid), ()):
                    if not _usable_sentence(extra):
                        continue
                    key = _norm_sentence(extra)
                    if key in seen:
                        continue
                    if compute_matched_span(extra, forms) is None:
                        continue  # entity not named in this augmentation sentence
                    seen.add(key)
                    facts.append(_usage_fact(extra, forms))
                if len(facts) > 1:
                    facts.sort(
                        key=lambda f: (
                            -(f["informativeness_score"] or 0.0),
                            -(f["centrality_score"] or 0.0),
                        )
                    )
                usages[str(pmid)] = facts[:max_per_pair]
            if usages:
                entity_context[eid] = {pmid: usages[pmid] for pmid in sorted(usages)}

            entities.append({
                "normalized_entity_id": eid,
                "entity_label": display,
                "canonical_tool_id": eid,
                "supercategory": supercategory,
                "family_label": family_label,
                # #260: the family's dominant_kind (the frozen `kind` enum, copied
                # straight from the source family — no new computation) so SPS picks
                # the per-family rail noun (Instruments / Reagents / Methods / …)
                # instead of a hard-coded "Cell lines". null when the family lacks one.
                "dominant_kind": fam.get("dominant_kind"),
                "parent_entity_id": parent_id_by_member.get(eid),
                "parent_label": parent_label_by_member.get(eid),  # the line core, e.g. "3T3-L1"
                "parent_descriptor": None,  # filled by define_entity_parents
                "entity_role": None,        # #1166-B
                # Authoritative ranking number: institution-wide distinct pubs. NOT
                # len(usages) — sentences are only kept where a usable snippet exists.
                "usage_count": int(t.get("pub_count") or 0),
                "evidenced": bool(usages),  # spec §7 is_evidenced -> clickable affordance
                # #252: a generic CATEGORY name, not a specific entity (SPS reads it
                # as family_entity.is_generic). Additive flag, never a drop.
                "is_generic": matches_generic(display, generic_terms or []),
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

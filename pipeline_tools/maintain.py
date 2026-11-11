"""Incremental "maintain" run for the Methods & Tools artifact (scheduled-refresh plan, Phases 2-4).

``python -m pipeline_tools.maintain`` starts from the durable state reconciled to the LIVE
artifact (``pipeline_tools.state``), folds in the publications extracted since, and
republishes only if every guard passes. It never re-runs the corpus build: existing tool,
family, context and entity records are copied from the state, and new data is ADDED.

One run:

  1. Load the state (``tools/_state/latest.json`` or ``--state-dir``) and the live artifact
     (``tools/latest/`` or ``--live-dir``). Live must be exactly what the state last
     published (object sha256s), else fail closed: someone else published since.
  2. Extract mentions for corpus rows not yet in the extraction checkpoint (S3 by default),
     under a REQUIRED per-run hard cap (``--hard-cap-usd``, default $25; this run's spend
     only, not the checkpoint's history).
  3. New PMIDs = checkpoint PMIDs the state has not incorporated. Zero -> ``skipped``.
  4. Forms the registry already resolves by name/alias/acronym attach with no LLM call.
     Only the rest are classified (Sonnet; LLM-error placeholders are never cached, the
     #308 item-3 fix in ``corpus_run._classify_resumable``), then ``match`` (embedding NN)
     or ``mint``. Existing ids never move. A PMID with any form left unresolved (LLM
     error, classify budget exhausted) is NOT marked incorporated, so the next run retries
     it; re-processing a PMID is idempotent.
  5. A new method tool joins an EXISTING family by the registry's non-minting family match.
     No family match -> held for review (kept in the state registry with ``review_hold``,
     never published, listed in ``tools/_review/<run>.json`` + a Teams card). Families are
     never minted or re-formed (``form_families`` re-mints ids, #308 item 4, still open).
  6. New pub_ids, sentence-aligned context (the #239 extraction-time guard, re-checked here
     with the verbatim guard when the abstract is at hand) and entity_context rows are
     added for NEW PMIDs only. Existing (tool, pmid) snippets, their meta signals and
     entity rows are copied untouched.
  7. Salience: existing tiers are FROZEN. Recalibrating over the merged corpus moves the S
     cutoff (a spread percentile) and every tool's spread, so it can re-tier existing
     tools; instead only new tools are tiered, with the published thresholds
     (``salience_thresholds``). The faculty rollup is updated incrementally (new pmids
     appended to the touched rows), never recomputed, for the same reason.
  8. Guards (all fail-closed, :func:`run_guards`), then publish (``--publish``; dry-run by
     default): versioned copies under ``tools/versions/<run>/`` first, then the flat and
     ``latest/`` objects, ``latest/manifest.json`` last. The manifest gains
     ``run_started_at``. A new state version (+ ``latest.json`` pointer) is written only
     after the publish succeeds.
  9. Ledger: a ``STAGE#tools_maintain#GLOBAL`` row (complete | skipped | failed), shaped
     like the onboarding detector's, written on publish runs (a dry run writes nothing).

Carried verbatim on purpose (Phase 0 findings): ``hierarchy`` (the live one is a 06-13
review tree that the current builder no longer reproduces), ``telemetry``,
``exceptions_summary``, ``salience_thresholds`` and the bodies' ``schema_version``.

All LLM / embedding / S3 / DynamoDB / Teams seams are injected (:class:`Seams`), so the
whole run unit-tests offline. Nothing touches AWS at import.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import logging
import sys
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from pipeline_tools import state as st
from pipeline_tools import vocab
from pipeline_tools.publish import (
    S3_PREFIX,
    PublishItem,
    _build_tool_context_meta,
    _family_record,
    build_publish_items,
)
from pipeline_tools.registry import FamilyRegistry, ToolRegistry, norm_name

logger = logging.getLogger(__name__)

STAGE = "tools_maintain"
SCOPE = "GLOBAL"
STAGE_PK = f"STAGE#{STAGE}#{SCOPE}"
RECORD_TYPE = "TOOLS_MAINTAIN_RUN"

REVIEW_PREFIX = f"{S3_PREFIX}_review/"
VERSIONS_PREFIX = f"{S3_PREFIX}versions/"

DEFAULT_HARD_CAP_USD = Decimal(25)
# Extraction may use this share of the hard cap; the rest is reserved for classifying the
# forms it finds (classify spend is estimated, the call_json seam reports no usage).
EXTRACT_CAP_SHARE = Decimal("0.9")
# Classify cost model: one Sonnet call per batch of 50 forms, ~8k tokens in and out.
CLASSIFY_BATCH_SIZE = 50
CLASSIFY_BATCH_INPUT_TOKENS = 8000
CLASSIFY_BATCH_OUTPUT_TOKENS = 8000

HOLD_NO_FAMILY = "no_family_match"
HOLD_CROSS_SUPERCATEGORY = "cross_supercategory_match"

# Bundle fields carried verbatim; a maintain run must leave them byte-identical.
FROZEN_BUNDLE_FIELDS = ("schema_version", "salience_thresholds", "hierarchy", "telemetry", "exceptions_summary")
# Published tool fields a maintain run may change on an EXISTING tool (append-only, checked).
_TOOL_MUTABLE = ("pub_count", "aliases")
# Entity fields a maintain run may change on an EXISTING entity (checked).
_ENTITY_MUTABLE = ("usage_count", "evidenced")


class MaintainError(RuntimeError):
    """The run cannot proceed without risking the live artifact (fail closed)."""


class GuardError(MaintainError):
    """One or more publish guards failed; nothing was published."""

    def __init__(self, failures: list[str]):
        self.failures = failures
        head = "; ".join(failures[:5])
        super().__init__(f"{len(failures)} publish guard failure(s): {head}"
                         f"{' ...' if len(failures) > 5 else ''}")


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _now_iso() -> str:
    from utils.iso_clock import now_iso

    return now_iso()


def _run_id(started_at: str) -> str:
    return started_at.replace(":", "")


# ---------------------------------------------------------------------------
# Live artifact input (local dir or S3 latest/)
# ---------------------------------------------------------------------------


def read_live_s3(client, *, prefix: str = S3_PREFIX) -> dict[str, bytes]:
    """``tools/latest/`` (7 objects + manifest) via a client with ``get_object_bytes``,
    each object verified against the manifest's sha256 (same contract as read_live_dir)."""
    out = {"manifest.json": client.get_object_bytes(f"{prefix}latest/manifest.json")}
    manifest = json.loads(out["manifest.json"])
    for name in st.ARTIFACT_NAMES:
        body = client.get_object_bytes(f"{prefix}latest/{name}")
        want = manifest["objects"][name]["sha256"]
        if _sha(body) != want:
            raise MaintainError(f"live {name} sha256 {_sha(body)[:12]} != manifest {want[:12]}")
        out[name] = body
    return out


def check_state_matches_live(state: st.ToolsState, live_raw: dict[str, bytes]) -> None:
    """Fail closed unless live is byte-for-byte what this state last published.

    Compares the 7 object sha256s of the live manifest to the state's baseline manifest
    (the manifest itself may differ: Phase 0's re-emit adds ``run_at_utc``).
    """
    live_m = json.loads(live_raw["manifest.json"])
    base = state.baseline_manifest.get("objects", {})
    drift = [n for n in st.ARTIFACT_NAMES
             if (live_m["objects"].get(n) or {}).get("sha256") != (base.get(n) or {}).get("sha256")]
    if drift:
        raise MaintainError(
            f"live tools/latest/ no longer matches state {state.version!r} for {drift}: something "
            "else published since. Reconcile a new state from live (cli/tools_state_reconcile.py) "
            "before running maintain.")


# ---------------------------------------------------------------------------
# Snippet guard for NEW context pairs (#238/#239 contract)
# ---------------------------------------------------------------------------


def accept_new_snippet(snippet: str | None, tool_name: str, abstract: str | None) -> bool:
    """A new (tool, pmid) usage snippet is kept only if it reads as one whole sentence.

    With the abstract at hand this is the full ``accept_snippet`` verbatim guard (the
    #239 extraction-time check, re-applied: a checkpoint line may predate #239). Either
    way the snippet must be sentence-complete, so a new pair can never raise the
    fragment metrics the publish guard compares.
    """
    from pipeline_tools.context_quality import (
        MAX_SENTENCE_CHARS,
        MIN_SNIPPET_CHARS,
        accept_snippet,
        is_sentence_complete,
        is_single_sentence,
    )

    s = (snippet or "").strip()
    if not s or not is_sentence_complete(s):
        return False
    if abstract:
        return accept_snippet(s, abstract, tool_name)
    return MIN_SNIPPET_CHARS <= len(s) <= MAX_SENTENCE_CHARS and is_single_sentence(s)


# ---------------------------------------------------------------------------
# The delta (pure: no I/O beyond an optional local classify cache)
# ---------------------------------------------------------------------------


@dataclass
class Delta:
    new_pmids: list[str] = field(default_factory=list)
    new_mentions: int = 0
    unique_forms: int = 0
    surface_resolved: int = 0
    embedding_resolved: int = 0
    classified_forms: int = 0
    classify_est_usd: Decimal = Decimal(0)
    denied_forms: int = 0
    deferred_forms: list[dict] = field(default_factory=list)   # [{raw_name, reason, pmids}]
    deferred_pmids: set[str] = field(default_factory=set)
    minted: list[str] = field(default_factory=list)            # every id minted this run
    published_new: list[str] = field(default_factory=list)     # minted AND published
    held: list[dict] = field(default_factory=list)             # review records
    family_attach: dict[str, str] = field(default_factory=dict)  # new cid -> fid
    tool_pmids_added: dict[str, set[str]] = field(default_factory=dict)  # published cid -> new pub pmids
    aliases_added: int = 0
    context_pairs_added: int = 0
    context_rejected: int = 0
    faculty_rows_touched: int = 0
    faculty_new_cwids: list[str] = field(default_factory=list)
    grant_signal_touched: int = 0
    entities_updated: int = 0
    entities_added: list[str] = field(default_factory=list)
    entity_rows_added: int = 0
    relabels: list[dict] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "new_pmids": len(self.new_pmids),
            "new_mentions": self.new_mentions,
            "unique_forms": self.unique_forms,
            "surface_resolved_forms": self.surface_resolved,
            "embedding_resolved_forms": self.embedding_resolved,
            "classified_forms": self.classified_forms,
            "classify_est_usd": str(self.classify_est_usd),
            "denied_forms": self.denied_forms,
            "deferred_forms": len(self.deferred_forms),
            "deferred_pmids": len(self.deferred_pmids),
            "minted": len(self.minted),
            "new_tools_published": len(self.published_new),
            "held_for_review": len(self.held),
            "existing_tools_gaining_pubs": sum(1 for c, p in self.tool_pmids_added.items()
                                               if p and c not in set(self.published_new)),
            "pub_ids_added": sum(len(p) for p in self.tool_pmids_added.values()),
            "aliases_added": self.aliases_added,
            "context_pairs_added": self.context_pairs_added,
            "context_snippets_rejected": self.context_rejected,
            "faculty_rows_touched": self.faculty_rows_touched,
            "faculty_new_cwids": len(self.faculty_new_cwids),
            "grant_signal_touched": self.grant_signal_touched,
            "entities_updated": self.entities_updated,
            "entities_added": len(self.entities_added),
            "entity_rows_added": self.entity_rows_added,
            "relabels": len(self.relabels),
        }


def classify_cost_per_form() -> Decimal:
    """Estimated Sonnet spend per classified form (one 50-form batch call, amortized)."""
    from utils.bedrock_client import MODEL_IDS_BY_STAGE, SONNET_MODEL
    from utils.llm_cost import cost_for

    model = MODEL_IDS_BY_STAGE.get("tool_classify", SONNET_MODEL)
    per_batch = cost_for(model, CLASSIFY_BATCH_INPUT_TOKENS, CLASSIFY_BATCH_OUTPUT_TOKENS)
    return (per_batch / CLASSIFY_BATCH_SIZE).quantize(Decimal("0.000001"))


def _thresholds_of(bundle_fields: dict):
    from pipeline_tools.salience import GroundedThresholds

    th = bundle_fields.get("salience_thresholds") or {}
    return GroundedThresholds(
        s_spread_min=th.get("s_spread_min"), a_pub_floor=int(th.get("a_pub_floor") or 3),
        a_spread_floor=int(th.get("a_spread_floor") or 2), percentile=float(th.get("percentile") or 0.88),
    )


def build_delta(
    state: st.ToolsState,
    new_mentions: list[dict],
    new_pmids: list[str],
    *,
    call_json,
    embed,
    classify_budget_usd: Decimal,
    abstracts: dict[str, str] | None = None,
    alias_merges: list[dict] | None = None,
    keep_separate: list[list[str]] | None = None,
    force_c_terms: list[str] | None = None,
    generic_terms: list[str] | None = None,
    classify_cache_path: Path | None = None,
    relabel: bool = False,
    run_id: str = "",
    per_form_usd: Decimal | None = None,
) -> tuple[st.ToolsState, Delta]:
    """Fold ``new_mentions`` (from ``new_pmids``) into a COPY of ``state``. Returns the new
    state and what changed. The input state is not mutated."""
    from pipeline_tools.corpus_run import (
        _classify_resumable,
        _pub_ids_of,
        group_mentions,
    )
    from pipeline_tools.embeddings import EmbeddingCache

    abstracts = abstracts or {}
    s2 = copy.deepcopy(state)
    s2.parent_version = state.version
    s2.version = None
    s2.reconcile_report = None
    d = Delta(new_pmids=sorted(set(new_pmids)), new_mentions=len(new_mentions))

    cache = EmbeddingCache(embed=embed)
    reg = ToolRegistry(s2.tool_records, s2.denylist, alias_merges=alias_merges or [],
                       keep_separate=keep_separate or [], cache=cache)
    fam_reg = FamilyRegistry(s2.family_records, cache=cache)
    held_before = {r["canonical_tool_id"] for r in s2.tool_records if r.get("review_hold")}

    uniques = sorted(group_mentions(new_mentions), key=lambda u: (-u.pub_count, u.norm_key))
    d.unique_forms = len(uniques)

    def _defer(u, reason: str) -> None:
        pm = sorted({str(o["pmid"]) for o in u.occurrences if o.get("pmid")})
        d.deferred_forms.append({"raw_name": u.raw_name, "reason": reason, "pmids": pm})
        d.deferred_pmids.update(pm)

    # 1. resolve by name/alias first (no LLM); the rest goes to the classifier.
    resolved: dict[str, str] = {}
    to_classify = []
    for u in uniques:
        if reg.is_denied(u.raw_name):
            d.denied_forms += 1
            continue
        hit = reg.match_surface(u.raw_name)
        if hit is not None:
            resolved[u.norm_key] = hit.key
            d.surface_resolved += 1
        else:
            to_classify.append(u)

    # 2. classify within the remaining budget (most prominent first); overflow is deferred.
    per_form = per_form_usd if per_form_usd is not None else classify_cost_per_form()
    if call_json is None:
        affordable = 0
    else:
        affordable = len(to_classify) if per_form <= 0 else int(classify_budget_usd / per_form)
    if affordable < len(to_classify):
        logger.warning("classify budget $%s covers %d of %d form(s); the rest are deferred to the next run",
                       classify_budget_usd, affordable, len(to_classify))
    batch = to_classify[:max(affordable, 0)]
    for u in to_classify[len(batch):]:
        _defer(u, "classify_budget")
    cls_by_name: dict[str, dict] = {}
    if batch:
        cls_by_name = _classify_resumable(
            [{"raw_name": u.raw_name, "tool_category": u.tool_category, "context": u.context,
              "pub_count": u.pub_count} for u in batch],
            call_json=call_json, batch_size=CLASSIFY_BATCH_SIZE, checkpoint_path=classify_cache_path,
        )
        d.classified_forms = len(batch)
        d.classify_est_usd = (per_form * len(batch)).quantize(Decimal("0.01"))

    minted_cls: dict[str, dict] = {}
    for u in batch:
        c = cls_by_name.get(u.norm_key)
        if c is None or c.get("disposition") is None:
            _defer(u, "unclassified")           # LLM error / omitted: retried next run
            continue
        if c["disposition"] == "excluded":
            reg.deny(u.raw_name)
            d.denied_forms += 1
            continue
        hit = reg.match(u.raw_name)              # surface again (a form minted earlier this run) + embedding NN
        if hit is not None:
            resolved[u.norm_key] = hit.key
            d.embedding_resolved += 1
            continue
        rec = reg.mint(display_name=reg._canonical_display(u.raw_name, u.raw_name), raw_name=u.raw_name,
                       disposition=c["disposition"])
        cid = rec["canonical_tool_id"]
        reg.update_classification(cid, disposition=c["disposition"], kind=c.get("kind"),
                                  supercategory=c.get("supercategory"), attributes=c.get("attributes"))
        rec["flags"] = list(c.get("flags", []))
        if c.get("model"):
            rec["classified_by"], rec["classified_by_assumed"] = c["model"], False
        else:
            from utils.bedrock_client import SONNET_MODEL
            rec["classified_by"], rec["classified_by_assumed"] = SONNET_MODEL, True
        minted_cls[cid] = c
        d.minted.append(cid)
        resolved[u.norm_key] = cid

    # 3. accretion: pub_ids, aliases, per-pub context (new pmids only), occurrences.
    tool_context = s2.tool_context
    occ_by_cid: dict[str, list[dict]] = defaultdict(list)
    new_ctx: dict[str, dict[str, str]] = defaultdict(dict)
    minted = set(d.minted)
    for u in uniques:
        cid = resolved.get(u.norm_key)
        if cid is None:
            continue
        rec = reg.get(cid)
        known_ctx = set(tool_context.get(cid) or {}) | set(rec.get("context_by_pub") or {})
        pubs = _pub_ids_of(u.occurrences)
        added = set(pubs) - set(rec.get("pub_ids") or [])
        if added and not rec.get("pub_ids") and int(rec.get("pub_count") or 0) > 0:
            rec["pub_count_base"] = int(rec["pub_count"])   # seed-count tool: keep its count
        cbp = {}
        for pmid, snip in (u.context_by_pmid or {}).items():
            if pmid in known_ctx or pmid not in added:
                continue
            if accept_new_snippet(snip, u.raw_name, abstracts.get(pmid)):
                cbp[pmid] = snip.strip()
            else:
                d.context_rejected += 1
        n_alias = len(rec.get("aliases") or [])
        reg.attach(cid, raw_name=u.raw_name, pub_ids=pubs,
                   context=(u.context if cid in minted else None),   # existing tools' evidence is frozen
                   context_by_pub=cbp or None)
        d.aliases_added += (len(rec.get("aliases") or []) - n_alias) if cid not in minted else 0
        if added:
            d.tool_pmids_added.setdefault(cid, set()).update(added)
        for pmid in cbp:
            new_ctx[cid][pmid] = rec["context_by_pub"][pmid]
        occ_by_cid[cid].extend(u.occurrences)

    # 4. new tools: existing family or held for review (families are never minted).
    from pipeline_tools import salience as salience_mod
    force_c_terms = force_c_terms if force_c_terms is not None else salience_mod.load_force_c_terms()
    thresholds = _thresholds_of(s2.bundle["fields"])
    for cid in d.minted:
        rec = reg.get(cid)
        if rec["disposition"] == vocab.CAPABILITY_DISPOSITION:
            spread = len({o["cwid"] for o in occ_by_cid[cid]
                          if o.get("source_kind") != "grant" and o.get("cwid")})
            tier = salience_mod.assign_grounded_salience(
                display_name=rec["display_name"], pub_count=len(rec.get("pub_ids") or []), spread=spread,
                rrid_candidate=bool((rec.get("attributes") or {}).get("rrid_candidate")),
                thresholds=thresholds, force_c_terms=force_c_terms)
            reg.update_classification(cid, salience_tier=tier["salience_tier"],
                                      salience_tier_basis=tier["salience_tier_basis"])
            rec["flags"] = [f for f in rec.get("flags", []) if f not in tier["clear_flags"]]
            hit, cross = fam_reg.match_family(rec["display_name"], rec.get("supercategory"))
            if hit is None:
                reason = HOLD_CROSS_SUPERCATEGORY if cross is not None else HOLD_NO_FAMILY
                rec["review_hold"] = {
                    "reason": reason, "held_at_run": run_id,
                    "cross_match": ({"family_id": cross.key, "score": round(cross.score, 4),
                                     "matched_text": cross.matched_text} if cross is not None else None),
                }
                continue
            fam_reg.add_member(hit.key, cid, rec["display_name"])
            reg.update_classification(cid, member_of_family=hit.key)
            d.family_attach[cid] = hit.key
        d.published_new.append(cid)

    held_now = {r["canonical_tool_id"] for r in reg.records() if r.get("review_hold")}
    for cid in sorted(held_now):
        rec = reg.get(cid)
        prev = {tuple(x) for x in rec["review_hold"].get("occurrences") or []}
        cur = {(str(o.get("pmid")), o.get("cwid") or "", o.get("source_kind") or "publication")
               for o in occ_by_cid.get(cid, [])}
        # (pmid, cwid, source_kind) kept on the held record so a later promotion can
        # backfill the faculty rollup without re-extracting.
        rec["review_hold"]["occurrences"] = [list(t) for t in sorted(prev | cur)]
        if cid in minted or cur:
            d.held.append(_review_record(rec, cid in held_before))
    # held tools keep their accreted pmids in the registry only (never published)
    for cid in held_now:
        d.tool_pmids_added.pop(cid, None)
        new_ctx.pop(cid, None)

    # 5. context sidecar + meta: new pairs only.
    for cid, pairs in new_ctx.items():
        if not pairs:
            continue
        rec = reg.get(cid)
        row = tool_context.setdefault(cid, {})
        for pmid, snip in pairs.items():
            row[pmid] = snip
            d.context_pairs_added += 1
        tool_context[cid] = {p: row[p] for p in sorted(row)}
        meta = _build_tool_context_meta({cid: pairs}, [{"canonical_tool_id": cid, "display_name": rec["display_name"],
                                                       "aliases": rec.get("aliases") or []}])
        if meta.get(cid):
            mrow = s2.tool_context_meta.setdefault(cid, {})
            mrow.update(meta[cid])
            s2.tool_context_meta[cid] = {p: mrow[p] for p in sorted(mrow)}

    # 6. faculty rollup + grant signal, incrementally (published tools only).
    published = {r["canonical_tool_id"] for r in reg.records() if not r.get("review_hold")}
    fields = s2.bundle["fields"]
    _update_faculty(fields.setdefault("faculty", {}), occ_by_cid, reg, fam_reg, published, d)
    _update_grant_signal(fields.setdefault("grant_signal", {}), occ_by_cid, published, d)

    # 7. optional relabel of families that gained members (deliberate operator action).
    if relabel and d.family_attach:
        _relabel_touched(fam_reg, set(d.family_attach.values()), call_json=call_json, delta=d)

    # 8. entity layer: update / append for touched tools; new pmid rows only.
    _update_entities(s2, reg, fam_reg, new_ctx, published, d, generic_terms=generic_terms)
    if d.relabels:
        _cascade_relabels(s2, reg, d.relabels)

    # 9. write back.
    s2.tool_records = [ToolRegistry._serialize(r) for r in reg.records()]
    s2.family_records = fam_reg.records()
    s2.denylist = reg.denylist
    done = set(d.new_pmids) - d.deferred_pmids
    s2.incorporated_pmids = sorted(set(s2.incorporated_pmids or []) | done)
    return s2, d


def _review_record(rec: dict, previously_held: bool) -> dict:
    return {
        "canonical_tool_id": rec["canonical_tool_id"],
        "display_name": rec["display_name"],
        "aliases": rec.get("aliases", []),
        "disposition": rec.get("disposition"),
        "kind": rec.get("kind"),
        "supercategory": rec.get("supercategory"),
        "salience_tier": rec.get("salience_tier"),
        "reason": rec["review_hold"].get("reason"),
        "cross_match": rec["review_hold"].get("cross_match"),
        "held_at_run": rec["review_hold"].get("held_at_run"),
        "previously_held": previously_held,
        "pub_ids": sorted(rec.get("pub_ids") or []),
        "context_by_pub": dict(sorted((rec.get("context_by_pub") or {}).items())),
        "context_evidence": rec.get("context_evidence", []),
        "occurrences": rec["review_hold"].get("occurrences", []),
    }


def _update_faculty(faculty: dict, occ_by_cid, reg: ToolRegistry, fam_reg: FamilyRegistry,
                    published: set[str], d: Delta) -> None:
    """Append new publication pmids to the per-(scholar, tool/family) rows they touch.

    Mirrors ``rollup.build_faculty_rollup``'s rules (method tools only, publication
    occurrences only, C-tier members never count toward a family row) but only ADDS:
    untouched scholars and rows are left exactly as live has them.
    """
    adds: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for cid, occs in occ_by_cid.items():
        rec = reg.get(cid)
        if cid not in published or rec.get("disposition") != vocab.CAPABILITY_DISPOSITION:
            continue
        for o in occs:
            pmid, cwid = str(o.get("pmid") or ""), o.get("cwid")
            if (o.get("source_kind") or "publication") != "publication" or not pmid or not cwid:
                continue
            adds[cwid][cid].add(pmid)
    for cwid in sorted(adds):
        row = faculty.get(cwid)
        if row is None:
            row = faculty[cwid] = {"cwid": cwid, "tools": [], "families": []}
            d.faculty_new_cwids.append(cwid)
        tools_by = {t["canonical_tool_id"]: t for t in row["tools"]}
        fam_by = {f["family_id"]: f for f in row["families"]}
        touched_fams: set[str] = set()
        for cid, pmids in adds[cwid].items():
            rec = reg.get(cid)
            tr = tools_by.get(cid)
            if tr is None:
                tr = {"canonical_tool_id": cid, "display_name": rec["display_name"], "pub_count": 0, "pmids": []}
                row["tools"].append(tr)
                tools_by[cid] = tr
            new = pmids - set(tr["pmids"])
            if new:
                tr["pmids"] = sorted(set(tr["pmids"]) | new)
                tr["pub_count"] = len(tr["pmids"])
                d.faculty_rows_touched += 1
            fid = rec.get("member_of_family")
            if fid and rec.get("salience_tier") != vocab.DEMOTED_TIER and new:
                fam = fam_reg.get(fid) or {}
                fr = fam_by.get(fid)
                if fr is None:
                    fr = {"family_id": fid, "label": fam.get("label"), "supercategory": fam.get("supercategory"),
                          "pub_count": 0, "pmids": [], "exemplar_tool_ids": []}
                    row["families"].append(fr)
                    fam_by[fid] = fr
                fr["pmids"] = sorted(set(fr["pmids"]) | new)
                fr["pub_count"] = len(fr["pmids"])
                touched_fams.add(fid)
                d.faculty_rows_touched += 1
        for fid in touched_fams:   # per-profile exemplars: this scholar's own top non-C members
            members = [(t["canonical_tool_id"], t["pub_count"]) for t in row["tools"]
                       if (reg.get(t["canonical_tool_id"]) or {}).get("member_of_family") == fid
                       and (reg.get(t["canonical_tool_id"]) or {}).get("salience_tier") != vocab.DEMOTED_TIER]
            members.sort(key=lambda x: (-x[1], x[0]))
            fam_by[fid]["exemplar_tool_ids"] = [c for c, _ in members[:3]]
        row["tools"].sort(key=lambda r: (-r["pub_count"], r["canonical_tool_id"]))
        row["families"].sort(key=lambda r: (-r["pub_count"], r["family_id"]))


def _update_grant_signal(grant_signal: dict, occ_by_cid, published: set[str], d: Delta) -> None:
    from pipeline_tools.corpus_run import _appl_id

    for cid in sorted(occ_by_cid):
        if cid not in published:
            continue
        appl = {_appl_id(str(o.get("pmid") or "")) for o in occ_by_cid[cid] if o.get("source_kind") == "grant"}
        cwids = {o["cwid"] for o in occ_by_cid[cid] if o.get("source_kind") == "grant" and o.get("cwid")}
        appl.discard("")
        if not appl and not cwids:
            continue
        cur = grant_signal.get(cid) or {"appl_ids": [], "investigator_cwids": []}
        new = {"appl_ids": sorted(set(cur["appl_ids"]) | appl),
               "investigator_cwids": sorted(set(cur["investigator_cwids"]) | cwids)}
        if new != cur:
            grant_signal[cid] = new
            d.grant_signal_touched += 1


def _update_entities(s2: st.ToolsState, reg: ToolRegistry, fam_reg: FamilyRegistry, new_ctx: dict,
                     published: set[str], d: Delta, *, generic_terms: list[str] | None) -> None:
    """Entity rows for NEW pmids only; never re-projects existing rows (ReciterAI#308).

    The live entity_context has multi-usage (entity, pmid) rows the current
    ``build_entity_layer`` does not reproduce (Phase 0), so existing rows are left alone.
    A touched existing entity gets its derived ``usage_count`` (== the tool's pub_count)
    and ``evidenced`` updated; a newly projectable tool gets a new entity record, reusing an
    existing parent group of its family when its cell-line core matches one.
    """
    from pipeline_tools.entities import (
        build_entity_layer,
        is_cell_line_family,
        load_generic_terms,
        parent_core,
    )

    if generic_terms is None:
        generic_terms = load_generic_terms()
    ent_by_id = {e["normalized_entity_id"]: e for e in s2.entities}
    touched = (set(d.tool_pmids_added) | set(d.family_attach)) & published
    for cid in sorted(touched):
        rec = reg.get(cid)
        fam = fam_reg.get(rec.get("member_of_family") or "")
        if not fam:
            continue
        ser = ToolRegistry._serialize(rec)
        tool_pub = {"canonical_tool_id": cid, "display_name": ser["display_name"],
                    "aliases": ser.get("aliases", []), "pub_count": ser["pub_count"]}
        fam_pub = {**_family_record(fam), "member_tool_ids": [cid]}
        ents, ectx, _ = build_entity_layer([tool_pub], [fam_pub], {cid: dict(new_ctx.get(cid) or {})},
                                           generic_terms=generic_terms)
        if not ents:
            continue   # not a projectable family, or still 0-count
        rows = ectx.get(cid) or {}
        cur_rows = s2.entity_context.setdefault(cid, {})
        for pmid, usage in rows.items():
            if pmid not in cur_rows:
                cur_rows[pmid] = usage
                d.entity_rows_added += 1
        s2.entity_context[cid] = {p: cur_rows[p] for p in sorted(cur_rows)}
        if not s2.entity_context[cid]:
            del s2.entity_context[cid]
        e = ent_by_id.get(cid)
        if e is not None:
            e["usage_count"] = ser["pub_count"]
            e["evidenced"] = bool(e.get("evidenced") or cid in s2.entity_context)
            d.entities_updated += 1
            continue
        e = ents[0]
        e["parent_entity_id"] = e["parent_label"] = e["parent_descriptor"] = None
        if is_cell_line_family(fam_pub):
            core = (parent_core(e["entity_label"]) or "").lower()
            for other in s2.entities:
                if (other.get("parent_entity_id") and other.get("supercategory") == e["supercategory"]
                        and other.get("family_label") == e["family_label"]
                        and (other.get("parent_label") or "").lower() == core):
                    e["parent_entity_id"] = other["parent_entity_id"]
                    e["parent_label"] = other["parent_label"]
                    e["parent_descriptor"] = other.get("parent_descriptor")
                    break
        e["evidenced"] = cid in s2.entity_context
        s2.entities.append(e)
        ent_by_id[cid] = e
        d.entities_added.append(cid)


def _relabel_touched(fam_reg: FamilyRegistry, fids: set[str], *, call_json, delta: Delta) -> None:
    from pipeline_tools.relabel import relabel_families

    sub = FamilyRegistry([copy.deepcopy(fam_reg.get(f)) for f in sorted(fids)])
    for dl in relabel_families(sub, call_json=call_json):
        fam = fam_reg.get(dl["family_id"])
        if dl.get("new_label") and dl["new_label"] != fam.get("label"):
            delta.relabels.append({"family_id": dl["family_id"], "old_label": fam.get("label"),
                                   "new_label": dl["new_label"]})
            fam_reg.relabel(dl["family_id"], dl["new_label"])


def _cascade_relabels(s2: st.ToolsState, reg: ToolRegistry, relabels: list[dict]) -> None:
    """Carry a family relabel into the derived copies of the label (faculty rows, entities)."""
    new_label = {r["family_id"]: r["new_label"] for r in relabels}
    for row in s2.bundle["fields"].get("faculty", {}).values():
        for fr in row.get("families", []):
            if fr["family_id"] in new_label:
                fr["label"] = new_label[fr["family_id"]]
    for e in s2.entities:
        fid = (reg.get(e.get("canonical_tool_id") or "") or {}).get("member_of_family")
        if fid in new_label:
            e["family_label"] = new_label[fid]


# ---------------------------------------------------------------------------
# Render + guards
# ---------------------------------------------------------------------------


def render(state: st.ToolsState, *, generated_at: str, run_started_at: str | None,
           provenance_update: dict | None = None) -> tuple[dict, list[PublishItem], dict]:
    """``(payload, items, manifest)`` for a state (not mutated); the published tool order is
    ``[t["canonical_tool_id"] for t in payload["tools"]]``."""
    work = state
    if provenance_update:
        work = copy.copy(state)
        work.bundle = {**state.bundle, "fields": {**state.bundle["fields"],
                                                   "provenance": provenance_update}}
    payload = st.build_payload(work)
    items, manifest = build_publish_items(payload, generated_at=generated_at, run_started_at=run_started_at)
    return payload, items, manifest


def _latest_bodies(items: list[PublishItem]) -> dict[str, bytes]:
    return {it.key.rsplit("/", 1)[-1]: it.body for it in items if it.key.startswith(f"{S3_PREFIX}latest/")}


def _parse_objects(bodies: dict[str, bytes]) -> dict:
    J = lambda n: json.loads(bodies[n])
    return {
        "bundle": J("tools.json"),
        "families_doc": J("families.json"),
        "faculty_doc": J("faculty.json"),
        "tool_context": J("tool_context.json")["tool_context"],
        "tool_context_meta": J("tool_context_meta.json")["tool_context_meta"],
        "entities": J("entities.json")["entities"],
        "entity_context": J("entity_context.json")["entity_context"],
    }


def run_guards(live_raw: dict[str, bytes], items: list[PublishItem], delta: Delta) -> list[str]:
    """Every fail-closed publish check (Phase 3). Returns the failures ([] = pass)."""
    from cli.rebuild_entity_context import entity_fragment_metrics
    from cli.rebuild_tool_context import fragment_metrics

    f: list[str] = []
    new_b = _latest_bodies(items)
    new_m = json.loads(new_b["manifest.json"])
    live_m = json.loads(live_raw["manifest.json"])
    old, new = _parse_objects(live_raw), _parse_objects(new_b)
    new_pmids = set(delta.new_pmids)
    published_new = set(delta.published_new)
    relabeled = {r["family_id"] for r in delta.relabels}

    # G1 no object shrinks (record count, all 7). Not bytes: a legitimate flip such as
    # evidenced false -> true is one byte shorter; record-level loss is caught below.
    for name in st.ARTIFACT_NAMES:
        key = name[:-5]
        if new_m["counts"].get(key, 0) < live_m["counts"].get(key, 0):
            f.append(f"shrink: {name} count {live_m['counts'].get(key)} -> {new_m['counts'].get(key)}")

    ob, nb = old["bundle"], new["bundle"]
    for k in FROZEN_BUNDLE_FIELDS:
        if ob.get(k) != nb.get(k):
            f.append(f"bundle field {k!r} changed")
    for doc, name in ((new["families_doc"], "families.json"), (new["faculty_doc"], "faculty.json")):
        if doc.get("schema_version") != ob.get("schema_version"):
            f.append(f"{name} schema_version changed")
    if new["families_doc"].get("hierarchy") != ob.get("hierarchy"):
        f.append("families.json hierarchy changed")

    # G2 tools: every live tool present, frozen fields identical, append-only growth
    nt = {t["canonical_tool_id"]: t for t in nb["tools"]}
    for t in ob["tools"]:
        cid = t["canonical_tool_id"]
        n = nt.get(cid)
        if n is None:
            f.append(f"tool {cid} missing")
            continue
        for k in set(t) | set(n):
            if k in _TOOL_MUTABLE:
                continue
            if k == "method_family_label" and t.get("method_family_id") in relabeled:
                continue
            if t.get(k) != n.get(k):
                f.append(f"tool {cid} field {k!r} changed")
        if not set(t.get("aliases") or []) <= set(n.get("aliases") or []):
            f.append(f"tool {cid} lost aliases")
        want = int(t.get("pub_count") or 0) + len(delta.tool_pmids_added.get(cid, ()))
        if int(n.get("pub_count") or 0) != want:
            f.append(f"tool {cid} pub_count {t.get('pub_count')} -> {n.get('pub_count')} (expected {want})")
    for cid in set(nt) - {t["canonical_tool_id"] for t in ob["tools"]}:
        if cid not in published_new:
            f.append(f"tool {cid} published but not minted+published by this run")

    # G3 families: id set stable, fields frozen, membership append-only with new tools
    of = {x["family_id"]: x for x in ob["families"]}
    nf = {x["family_id"]: x for x in nb["families"]}
    if set(of) != set(nf):
        f.append(f"family_id set changed: -{sorted(set(of) - set(nf))[:5]} +{sorted(set(nf) - set(of))[:5]}")
    for fid, x in of.items():
        y = nf.get(fid)
        if y is None:
            continue
        for k in set(x) | set(y):
            if k == "member_tool_ids":
                continue
            if k in ("label", "status") and fid in relabeled:
                continue
            if x.get(k) != y.get(k):
                f.append(f"family {fid} field {k!r} changed")
        om, nm = x.get("member_tool_ids") or [], y.get("member_tool_ids") or []
        if nm[:len(om)] != om or not set(nm[len(om):]) <= published_new:
            f.append(f"family {fid} membership changed beyond appending new tools")
    if nb["families"] != new["families_doc"]["families"]:
        f.append("families.json disagrees with tools.json families")

    # G4 tool_context + meta: existing pairs identical, new pairs only for new pmids
    for which in ("tool_context", "tool_context_meta"):
        o, n = old[which], new[which]
        for cid, row in o.items():
            nrow = n.get(cid) or {}
            for pmid, v in row.items():
                if nrow.get(pmid) != v:
                    f.append(f"{which} ({cid}, {pmid}) changed or removed")
        for cid, nrow in n.items():
            extra = set(nrow) - set(o.get(cid) or {})
            if extra - new_pmids:
                f.append(f"{which} {cid} gained pairs for non-new pmids {sorted(extra - new_pmids)[:3]}")
    if set(new["tool_context"]) - set(nt):
        f.append("tool_context has ids not in tools.json")

    # G5 fragment metrics do not regress (tool + entity grain)
    for label, before, after in (
        ("tool_context", fragment_metrics(old["tool_context"]), fragment_metrics(new["tool_context"])),
        ("entity_context", entity_fragment_metrics(old["entity_context"]),
         entity_fragment_metrics(new["entity_context"])),
    ):
        for k in ("start_lowercase_pct", "no_terminal_punct_pct"):
            if after[k] > before[k]:
                f.append(f"{label} fragment metric {k} regressed {before[k]} -> {after[k]}")

    # G6 faculty: every row kept, pmids append-only with new pmids, counts consistent
    ofa, nfa = ob["faculty"], nb["faculty"]
    if new["faculty_doc"]["faculty"] != nfa:
        f.append("faculty.json disagrees with tools.json faculty")
    for cwid, row in ofa.items():
        nrow = nfa.get(cwid)
        if nrow is None:
            f.append(f"faculty {cwid} missing")
            continue
        for part, key in (("tools", "canonical_tool_id"), ("families", "family_id")):
            nby = {r[key]: r for r in nrow.get(part, [])}
            for r in row.get(part, []):
                m = nby.get(r[key])
                if m is None:
                    f.append(f"faculty {cwid} {part} row {r[key]} missing")
                    continue
                for k in ("display_name", "label", "supercategory"):
                    if k in r and r.get(k) != m.get(k) and not (k == "label" and r[key] in relabeled):
                        f.append(f"faculty {cwid} {part} row {r[key]} {k} changed")
                op, np_ = set(r.get("pmids") or []), set(m.get("pmids") or [])
                if not op <= np_ or (np_ - op) - new_pmids:
                    f.append(f"faculty {cwid} {part} row {r[key]} pmids not append-only-new")
    for cwid, nrow in nfa.items():
        for part in ("tools", "families"):
            for r in nrow.get(part, []):
                if r.get("pub_count") != len(r.get("pmids") or []):
                    f.append(f"faculty {cwid} {part} row pub_count != len(pmids)")

    # G7 entities + entity_context
    ne = {e["normalized_entity_id"]: e for e in new["entities"]}
    for e in old["entities"]:
        eid = e["normalized_entity_id"]
        n = ne.get(eid)
        if n is None:
            f.append(f"entity {eid} missing")
            continue
        for k in set(e) | set(n):
            if k in _ENTITY_MUTABLE or (k == "family_label" and (nt.get(eid) or {}).get("method_family_id") in relabeled):
                continue
            if e.get(k) != n.get(k):
                f.append(f"entity {eid} field {k!r} changed")
        if int(n.get("usage_count") or 0) < int(e.get("usage_count") or 0):
            f.append(f"entity {eid} usage_count decreased")
        if e.get("evidenced") and not n.get("evidenced"):
            f.append(f"entity {eid} lost evidenced")
    for eid, row in old["entity_context"].items():
        nrow = new["entity_context"].get(eid) or {}
        for pmid, v in row.items():
            if nrow.get(pmid) != v:
                f.append(f"entity_context ({eid}, {pmid}) changed or removed")
    for eid, nrow in new["entity_context"].items():
        extra = set(nrow) - set(old["entity_context"].get(eid) or {})
        if extra - new_pmids:
            f.append(f"entity_context {eid} gained rows for non-new pmids")

    # G8 grant signal: append-only
    for cid, gs in ob.get("grant_signal", {}).items():
        ngs = nb.get("grant_signal", {}).get(cid)
        if ngs is None or not all(set(gs[k]) <= set(ngs.get(k) or []) for k in gs):
            f.append(f"grant_signal {cid} not append-only")

    # G9 label uniqueness after a relabel (the #215 invariant, checked not re-enforced)
    if relabeled:
        seen: dict[str, str] = {}
        for x in nb["families"]:
            key = norm_name(x.get("label") or "")
            if key in seen:
                f.append(f"family label collision after relabel: {x['family_id']} vs {seen[key]}")
            seen[key] = x["family_id"]
    return f


def is_zero_delta(live_raw: dict[str, bytes], items: list[PublishItem]) -> bool:
    """True when every one of the 7 objects is byte-identical to live."""
    bodies = _latest_bodies(items)
    return all(bodies[n] == live_raw[n] for n in st.ARTIFACT_NAMES)


def diff_report(live_raw: dict[str, bytes], items: list[PublishItem], delta: Delta, *,
                max_rows: int = 200) -> dict:
    """Human-review diff: per-object counts, and named per-record changes (capped)."""
    new_b = _latest_bodies(items)
    new_m = json.loads(new_b["manifest.json"])
    live_m = json.loads(live_raw["manifest.json"])
    objs = {}
    for n in st.ARTIFACT_NAMES:
        k = n[:-5]
        objs[n] = {"count": [live_m["counts"].get(k), new_m["counts"].get(k)],
                   "bytes": [live_m["objects"][n]["bytes"], new_m["objects"][n]["bytes"]],
                   "changed": new_m["objects"][n]["sha256"] != live_m["objects"][n]["sha256"]}
    ot = {t["canonical_tool_id"]: t for t in json.loads(live_raw["tools.json"])["tools"]}
    nb = json.loads(new_b["tools.json"])
    nt = {t["canonical_tool_id"]: t for t in nb["tools"]}
    fam_label = {f["family_id"]: f.get("label") for f in nb["families"]}
    changed = []
    for cid, t in ot.items():
        n = nt.get(cid) or {}
        if n != t:
            changed.append({"canonical_tool_id": cid, "display_name": t["display_name"],
                            "pub_count": [t.get("pub_count"), n.get("pub_count")],
                            "aliases_added": sorted(set(n.get("aliases") or []) - set(t.get("aliases") or []))})
    new_ents = {e["normalized_entity_id"]: e for e in json.loads(new_b["entities.json"])["entities"]}
    return {"objects": objs, "delta": delta.summary(),
            "existing_tools_changed": changed[:max_rows],
            "new_tools": [{"canonical_tool_id": c, "display_name": nt[c]["display_name"],
                           "disposition": nt[c]["disposition"], "kind": nt[c].get("kind"),
                           "supercategory": nt[c].get("supercategory"), "salience_tier": nt[c].get("salience_tier"),
                           "family_id": delta.family_attach.get(c),
                           "family_label": fam_label.get(delta.family_attach.get(c)), "pub_count": nt[c]["pub_count"]}
                          for c in delta.published_new if c in nt],
            "entities_added": [{k: new_ents[c].get(k) for k in ("normalized_entity_id", "entity_label", "family_label",
                                                                  "parent_label", "parent_descriptor", "usage_count")}
                               for c in delta.entities_added if c in new_ents],
            "held": [{k: h[k] for k in ("canonical_tool_id", "display_name", "supercategory", "reason")}
                     for h in delta.held],
            "deferred_forms": delta.deferred_forms[:50],
            "relabels": delta.relabels}


# ---------------------------------------------------------------------------
# Publish + ledger
# ---------------------------------------------------------------------------


def upload(items: list[PublishItem], *, s3, run_id: str) -> list[str]:
    """Versioned copies first (``tools/versions/<run>/``), then the publish set in
    ``build_publish_items`` order (``latest/manifest.json`` last, so a consumer polling the
    manifest never sees it ahead of its objects)."""
    keys = []
    for it in items:
        if it.key.startswith(f"{S3_PREFIX}latest/"):
            vkey = f"{VERSIONS_PREFIX}{run_id}/{it.key.rsplit('/', 1)[-1]}"
            s3.put_object(vkey, it.body, content_type="application/json", cache_control=None)
            keys.append(vkey)
    for it in items:
        s3.put_object(it.key, it.body, content_type="application/json", cache_control=it.cache_control)
        keys.append(it.key)
    return keys


def stage_row(*, status: str, started_at: str, run_id: str, counts: dict, cost_usd: Decimal,
              extra: dict | None = None) -> dict:
    """The ``STAGE#tools_maintain#GLOBAL`` row, shaped like the onboarding detector's."""
    from utils.iso_clock import duration_ms
    from utils.stage_records import compute_input_hash

    row = {
        "PK": STAGE_PK,
        "SK": f"RUN#{started_at}",
        "record_type": RECORD_TYPE,
        "stage": STAGE,
        "scope": SCOPE,
        "status": status,
        "run_id": run_id,
        "input_hash": compute_input_hash(STAGE, {"new_pmids": counts.get("new_pmid_list", [])}),
        "started_at": started_at,
        "completed_at": _now_iso(),
        "duration_ms": int(duration_ms(started_at)),
        "cost_observed_usd": Decimal(str(cost_usd)).quantize(Decimal("0.0001")),
        "new_pmid_count": int(counts.get("new_pmids", 0)),
        "new_mention_count": int(counts.get("new_mentions", 0)),
        "new_tool_count": int(counts.get("new_tools", 0)),
        "held_for_review_count": int(counts.get("held_for_review", 0)),
        "deferred_pmid_count": int(counts.get("deferred_pmids", 0)),
    }
    for k, v in (extra or {}).items():
        if v is not None:
            row[k] = v
    return row


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


@dataclass
class Seams:
    """Everything with a side effect. Defaults are built lazily by :func:`main`."""

    state_store: Any
    read_live: Callable[[], dict[str, bytes]]
    load_corpus: Callable[[], list[dict]]
    open_checkpoint: Callable[[], Any]
    call_llm: Callable | None = None          # extractor (None: no extraction this run)
    call_json: Callable | None = None         # classify / relabel
    embed: Callable | None = None             # registry NN
    s3: Any = None                            # artifact bucket client (publish only)
    ledger_table: Any = None                  # DynamoDB table (publish only)
    alert: Callable | None = None             # (severity, title, message, context) -> bool


@dataclass
class Config:
    publish: bool = False
    relabel: bool = False
    hard_cap_usd: Decimal = DEFAULT_HARD_CAP_USD
    per_pmid_usd: Decimal | None = None
    seed_incorporated_from_checkpoint: bool = False
    allow_cold_checkpoint: bool = False
    preflight_only: bool = False
    out_dir: Path | None = None
    classify_cache: Path | None = None
    alias_map: bool = True
    force_c_terms: list[str] | None = None
    generic_terms: list[str] | None = None


@dataclass
class RunResult:
    status: str                     # complete | skipped | failed | dry_run | preflight
    started_at: str
    report: dict = field(default_factory=dict)
    state_version: str | None = None
    published: bool = False


def _write_local(out_dir: Path | None, name: str, obj) -> None:
    if out_dir is None:
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def run(cfg: Config, seams: Seams, *, started_at: str | None = None) -> RunResult:
    """One maintain run. Writes a ``failed`` ledger row + ERROR alert on a crash (publish
    mode), then re-raises."""
    started_at = started_at or _now_iso()
    ctx: dict = {"counts": {}, "cost": Decimal(0)}
    try:
        return _run(cfg, seams, started_at, ctx)
    except Exception as exc:
        failures = getattr(exc, "failures", None)
        if cfg.publish and seams.ledger_table is not None:
            try:
                seams.ledger_table.put_item(Item=stage_row(
                    status="failed", started_at=started_at, run_id=_run_id(started_at),
                    counts=ctx["counts"], cost_usd=ctx["cost"],
                    extra={"error_code": type(exc).__name__, "error_message": str(exc)[:1000],
                           "failure_details": {"guard_failures": failures[:50]} if failures else None}))
            except Exception as inner:  # noqa: BLE001 — never mask the original error
                logger.error("tools maintain: could not write the failed STAGE# row: %s", inner)
        if seams.alert is not None and cfg.publish:
            seams.alert("ERROR", "Tools maintain run failed (nothing published)", str(exc)[:1500],
                        {"run": _run_id(started_at), "guard_failures": len(failures or [])})
        raise


def _run(cfg: Config, seams: Seams, started_at: str, ctx: dict) -> RunResult:
    from pipeline_tools import cost_guard
    from pipeline_tools.cost_guard import CostCeiling
    from pipeline_tools.extract import run_extraction

    run_id = _run_id(started_at)
    state = st.load_state(seams.state_store)
    live_raw = seams.read_live()
    check_state_matches_live(state, live_raw)
    logger.info("tools maintain %s: state %s (%s), live %s", run_id, state.version, state.counts(),
                json.loads(live_raw["manifest.json"]).get("version"))

    checkpoint = seams.open_checkpoint()
    if len(checkpoint) == 0 and not cfg.allow_cold_checkpoint:
        raise MaintainError("extraction checkpoint is empty/absent: a cold checkpoint would re-extract the whole "
                            "corpus. Upload it first, or pass --allow-cold-checkpoint deliberately.")
    incorporated = state.incorporated_pmids
    if incorporated is None:
        if not cfg.seed_incorporated_from_checkpoint:
            raise MaintainError(f"state {state.version!r} has no incorporated_pmids; the first run must pass "
                                "--seed-incorporated-from-checkpoint (checked against the corpus run's raw "
                                "mention count)")
        want = (state.bundle["fields"].get("provenance") or {}).get("raw_mentions")
        have = len(checkpoint.all_mentions())
        if want is None or int(want) != have:
            raise MaintainError(f"cannot seed incorporated_pmids: checkpoint holds {have} mentions, the published "
                                f"corpus run used {want}. The checkpoint changed since that run; reconcile by hand.")
        incorporated = sorted(checkpoint.done_pmids())
        state.incorporated_pmids = list(incorporated)
        logger.info("seeded incorporated_pmids from the checkpoint: %d PMID(s), %d mentions", len(incorporated), have)
    incorporated_set = set(incorporated)

    # Extraction: only corpus rows the checkpoint has not done; THIS run's spend is capped.
    corpus = seams.load_corpus() if seams.call_llm is not None or cfg.preflight_only else []
    abstracts = {str(r.get("pmid")): (r.get("abstractVarchar") or r.get("abstract") or "") for r in corpus}
    todo = [r for r in corpus if not checkpoint.is_done(str(r.get("pmid", "")))]
    per_pmid = cfg.per_pmid_usd or cost_guard.DEFAULT_PER_PMID_USD
    est = cost_guard.estimate_extraction_cost(len(todo), per_pmid)
    pending = sorted(checkpoint.done_pmids() - incorporated_set)
    preflight = {"corpus_rows": len(corpus), "to_extract": len(todo), "extract_estimate_usd": str(est),
                 "hard_cap_usd": str(cfg.hard_cap_usd), "checkpoint_pmids": len(checkpoint),
                 "incorporated_pmids": len(incorporated_set), "pending_unincorporated": len(pending)}
    logger.info("preflight: %s", preflight)
    if est > cfg.hard_cap_usd:
        logger.warning("extraction estimate $%s exceeds the hard cap $%s: this run stops at the cap and the "
                       "rest resumes next run", est, cfg.hard_cap_usd)
    if cfg.preflight_only:
        _write_local(cfg.out_dir, "preflight.json", preflight)
        return RunResult(status="preflight", started_at=started_at, report={"preflight": preflight})

    extract_cap = (cfg.hard_cap_usd * EXTRACT_CAP_SHARE).quantize(Decimal("0.01"))
    ceiling = CostCeiling(cap_usd=extract_cap, max_calls=max(len(todo), 1) * cost_guard.MAX_CALLS_PER_PMID)
    halted = False
    extract_tel: dict = {}
    if todo and seams.call_llm is not None:
        with checkpoint:
            res = run_extraction(todo, call_llm=seams.call_llm, checkpoint=checkpoint, ceiling=ceiling)
        halted = res.halted is not None
        extract_tel = res.telemetry
    ctx["cost"] = ceiling.observed_usd

    new_pmids = sorted(checkpoint.done_pmids() - incorporated_set)
    new_mentions = checkpoint.mentions_for(new_pmids)
    ctx["counts"] = {"new_pmids": len(new_pmids), "new_mentions": len(new_mentions), "new_pmid_list": new_pmids}
    report: dict = {"run_id": run_id, "state_version": state.version, "preflight": preflight,
                    "extraction": extract_tel, "halted_on_cost_cap": halted}

    if not new_pmids:
        logger.info("tools maintain: zero new PMIDs - nothing to publish")
        report["skip_reason"] = "no_new_pmids"
        _write_local(cfg.out_dir, "maintain_report.json", report)
        if cfg.publish and seams.ledger_table is not None:
            seams.ledger_table.put_item(Item=stage_row(
                status="skipped", started_at=started_at, run_id=run_id, counts=ctx["counts"], cost_usd=ctx["cost"],
                extra={"skip_reason": "no_new_pmids", "halted_on_cost_cap": halted,
                       "state_version": state.version}))
        return RunResult(status="skipped", started_at=started_at, report=report, state_version=state.version)

    # Delta (classify budget = whatever the hard cap has left after extraction).
    merges, keep_separate = ([], [])
    if cfg.alias_map:
        from pipeline_tools.registry import load_method_alias_map
        merges, keep_separate = load_method_alias_map()
    classify_budget = max(cfg.hard_cap_usd - ceiling.observed_usd, Decimal(0))
    new_state, delta = build_delta(
        state, new_mentions, new_pmids, call_json=seams.call_json, embed=seams.embed,
        classify_budget_usd=classify_budget, abstracts=abstracts, alias_merges=merges,
        keep_separate=keep_separate, force_c_terms=cfg.force_c_terms, generic_terms=cfg.generic_terms,
        classify_cache_path=cfg.classify_cache, relabel=cfg.relabel, run_id=run_id,
    )
    cost = ceiling.observed_usd + delta.classify_est_usd
    ctx["cost"] = cost
    ctx["counts"].update({"new_tools": len(delta.published_new), "held_for_review": len(delta.held),
                          "deferred_pmids": len(delta.deferred_pmids)})

    # Render. Zero artifact delta (e.g. every new form was denied or held) -> no republish.
    base_prov = state.bundle["fields"].get("provenance") or {}
    _, items0, _ = render(new_state, generated_at=state.baseline_manifest.get("generated_at"),
                          run_started_at=None)
    zero = is_zero_delta(live_raw, items0)
    prov = {**base_prov, "run_at_utc": started_at,
            "base_run_at_utc": base_prov.get("base_run_at_utc") or base_prov.get("run_at_utc"),
            "maintain": {"run_started_at": started_at, "parent_state_version": state.version,
                         "new_pmids": len(new_pmids), "new_tools": len(delta.published_new),
                         "held_for_review": len(delta.held)}}
    generated_at = _now_iso()
    payload, items, manifest = render(new_state, generated_at=generated_at, run_started_at=started_at,
                                      provenance_update=None if zero else prov)
    new_state.bundle["tool_order"] = [t["canonical_tool_id"] for t in payload["tools"]]
    if not zero:
        new_state.bundle["fields"]["provenance"] = prov
    failures = [] if zero else run_guards(live_raw, items, delta)
    report.update({"diff": diff_report(live_raw, items0 if zero else items, delta), "zero_artifact_delta": zero,
                   "guard_failures": failures, "cost_usd": str(cost), "manifest_version": manifest["version"]})
    review = {"run_id": run_id, "run_started_at": started_at, "parent_state_version": state.version,
              "held": delta.held}
    _write_local(cfg.out_dir, "maintain_report.json", report)
    _write_local(cfg.out_dir, "review.json", review)
    if failures:
        raise GuardError(failures)

    if not cfg.publish:
        logger.info("DRY-RUN: guards pass; %s", delta.summary())
        return RunResult(status="dry_run", started_at=started_at, report=report)

    # Publish (unless no artifact delta), then review queue, then the new state, then the ledger.
    published = False
    if not zero:
        upload(items, s3=seams.s3, run_id=run_id)
        published = True
        new_state.baseline_manifest = manifest
    if delta.held:
        seams.s3.put_object(f"{REVIEW_PREFIX}{run_id}.json",
                            json.dumps(review, indent=2, ensure_ascii=False).encode("utf-8"),
                            content_type="application/json", cache_control=None)
    st.save_state(seams.state_store, new_state, run_id, created_at=_now_iso(),
                  notes={"delta": delta.summary(), "published": published, "run_started_at": started_at})
    seams.ledger_table.put_item(Item=stage_row(
        status="complete", started_at=started_at, run_id=run_id, counts=ctx["counts"], cost_usd=cost,
        extra={"published": published, "halted_on_cost_cap": halted, "state_version": run_id,
               "output_pointer": f"s3://wcmc-reciterai-artifacts/{S3_PREFIX}latest/manifest.json" if published
               else None, "artifact_version": manifest["version"] if published else None}))
    if delta.held and seams.alert is not None:
        seams.alert("INFO", f"Tools maintain: {len(delta.held)} new tool(s) held for review",
                    "New methods/tools matched no existing family and were not published. Review and "
                    "promote or deny them.",
                    {"review": f"s3://wcmc-reciterai-artifacts/{REVIEW_PREFIX}{run_id}.json",
                     "new_pmids": len(new_pmids), "new_tools_published": len(delta.published_new)})
    return RunResult(status="complete", started_at=started_at, report=report, state_version=run_id,
                     published=published)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_seams(args) -> Seams:
    from pipeline_tools import checkpoint as checkpoint_mod
    from pipeline_tools.checkpoint import ExtractionCheckpoint

    s3_box: dict = {}

    def s3():
        if "c" not in s3_box:
            from utils.s3_client import ARTIFACTS_BUCKET, S3HierarchyClient
            s3_box["c"] = S3HierarchyClient(bucket=ARTIFACTS_BUCKET)
        return s3_box["c"]

    store = st.LocalStateStore(args.state_dir) if args.state_dir else st.S3StateStore()
    read_live = (lambda: st.read_live_dir(args.live_dir)) if args.live_dir else (lambda: read_live_s3(s3()))

    def load_corpus():
        if args.input:
            data = json.loads(Path(args.input).read_text(encoding="utf-8"))
            rows = data if isinstance(data, list) else data.get("corpus", data.get("rows", []))
            return [r for r in rows if str(r.get("pmid", "")).strip()]
        from pipeline_tools.corpus import fetch_extraction_corpus, fetch_grant_corpus
        from utils.db import get_engine

        engine = get_engine()
        rows: list[dict] = []
        if args.source in ("pubs", "both"):
            rows.extend(fetch_extraction_corpus(engine))
        if args.source in ("grants", "both"):
            rows.extend(fetch_grant_corpus(engine))
        return rows

    def open_checkpoint():
        if args.checkpoint:
            return ExtractionCheckpoint.load(Path(args.checkpoint))
        return ExtractionCheckpoint.load(store=checkpoint_mod.make_s3_store(args.checkpoint_s3_key))

    call_llm = call_json = embed = None
    if not args.preflight_only:
        from pipeline_tools.classify import make_classifier_call_json
        from pipeline_tools.embeddings import titan_embed
        from pipeline_tools.extract import make_extractor_call_llm

        call_llm = None if args.no_extract else make_extractor_call_llm()
        call_json = make_classifier_call_json()
        embed = titan_embed
    ledger = None
    if args.publish:
        from utils.dynamodb_helpers import TABLE_NAME, get_table
        ledger = get_table(TABLE_NAME)
    from pipeline_enrichment.alerting import alert

    return Seams(state_store=store, read_live=read_live, load_corpus=load_corpus, open_checkpoint=open_checkpoint,
                 call_llm=call_llm, call_json=call_json, embed=embed, s3=s3() if args.publish else None,
                 ledger_table=ledger, alert=alert)


def main(argv: list[str] | None = None) -> int:
    from pipeline_tools import checkpoint as checkpoint_mod

    ap = argparse.ArgumentParser(description="Incremental maintain run for the tools artifact (dry-run by default).")
    ap.add_argument("--publish", action="store_true", help="upload + write state + ledger (default: dry-run)")
    ap.add_argument("--relabel", action="store_true",
                    help="opt-in: LLM-relabel families that gained members (a deliberate operator action)")
    ap.add_argument("--hard-cap-usd", type=Decimal, default=DEFAULT_HARD_CAP_USD,
                    help=f"this run's total LLM spend cap (default ${DEFAULT_HARD_CAP_USD}); raise for a catch-up run")
    ap.add_argument("--per-pmid-usd", type=Decimal, default=None, help="extraction cost model override")
    ap.add_argument("--source", choices=("pubs", "grants", "both"), default="both")
    ap.add_argument("--input", help="JSON corpus export instead of --from-db (offline)")
    ap.add_argument("--state-dir", help="local state root (default: s3 tools/_state/)")
    ap.add_argument("--live-dir", help="local copy of tools/latest/ (default: read S3)")
    ap.add_argument("--checkpoint", help="local extraction checkpoint JSONL (default: the S3 checkpoint)")
    ap.add_argument("--checkpoint-s3-key", default=checkpoint_mod.DEFAULT_S3_KEY)
    ap.add_argument("--no-extract", action="store_true", help="fold in already-checkpointed PMIDs only")
    ap.add_argument("--preflight-only", action="store_true", help="estimate and exit; no LLM, no writes")
    ap.add_argument("--seed-incorporated-from-checkpoint", action="store_true",
                    help="first run only: treat every checkpoint PMID as already in the state")
    ap.add_argument("--allow-cold-checkpoint", action="store_true")
    ap.add_argument("--classify-cache", help="local classify cache JSONL (shared by a dry run and its publish run)")
    ap.add_argument("--out-dir", default="out/tools_maintain", help="local report + review copies")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    cfg = Config(publish=args.publish, relabel=args.relabel, hard_cap_usd=args.hard_cap_usd,
                 per_pmid_usd=args.per_pmid_usd, seed_incorporated_from_checkpoint=args.seed_incorporated_from_checkpoint,
                 allow_cold_checkpoint=args.allow_cold_checkpoint, preflight_only=args.preflight_only,
                 out_dir=Path(args.out_dir) if args.out_dir else None,
                 classify_cache=Path(args.classify_cache) if args.classify_cache else None)
    try:
        res = run(cfg, _build_seams(args))
    except GuardError as e:
        logger.error("%s", e)
        return 4
    except MaintainError as e:
        logger.error("%s", e)
        return 2
    print(json.dumps({"status": res.status, "published": res.published, "state_version": res.state_version,
                      "delta": (res.report.get("diff") or {}).get("delta"),
                      "guard_failures": res.report.get("guard_failures")}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    raise SystemExit(main())

"""Family label-uniqueness invariant (#215) — deterministic cross-supercategory
disambiguation + a hard publish gate so a duplicate display label can never reach
the artifact.

Runs in ``run_corpus`` AFTER the accreting ad-hoc dedup (:mod:`pipeline_tools.family_adhoc_dedup`)
and the cross-supercategory guard (:func:`pipeline_tools.relabel.cross_supercategory_label_forks`),
and BEFORE the faculty rollup / artifact emit. It generalizes the per-pair, reactive
``config/family_adhoc_dedup.json`` adjudication into a structural guarantee:

  1. **Invariant + hard gate.** No two *active* families may share a normalized display
     label (:func:`pipeline_tools.registry.norm_name`). A residual collision is auto-resolved
     (step 2) or, if it cannot be, **fails the build** — the identical-label section of
     ``family_dupe_scan`` becomes a publish PRECONDITION, not an advisory list.

  2. **Deterministic cross-supercategory disambiguation.** When >=2 active families share
     a label and differ by supercategory (the fork the cross-SC guard names as genuinely
     irreducible — e.g. AAV-as-reagent vs AAV-as-therapeutic), append a supercategory-derived
     qualifier so the labels are distinct and self-explanatory:
     "AAV gene-therapy vectors (research reagents)" vs "AAV gene-therapy vectors (therapeutics)".

Precedence (config merge/relabel -> auto-disambiguate -> gate) holds **by construction**,
not by re-checking here:

  - config **merges** already collapsed the pair upstream, so it never reaches this layer;
  - config **bespoke relabels** already made the labels differ, so there is no collision left
    to resolve (a curator label beats the auto-qualifier simply because it ran first);
  - what reaches here is exactly the residue: genuine keep-separate cross-SC forks config left
    identically-labelled (the AAV bug — a keep-separate decision that forgot its disambiguating
    relabel) + any NEW collision a future corpus run introduces.

What this layer must NOT decide (kept in ``config/family_adhoc_dedup.json``): genuine **merges**
(two families that should collapse) and **same-supercategory** duplicates — those cannot be
disambiguated by a supercategory qualifier (both sides get the same noun), so a residual
same-SC collision is a real bug and **fails the build** (the §7 ``dedup_families`` sweep should
have merged it). Fail-loud, no LLM, ``$0`` to re-run.
"""

from __future__ import annotations

import logging

from pipeline_tools import vocab
from pipeline_tools.registry import norm_name

logger = logging.getLogger(__name__)

# Status that renders as a chip (the relabel pass promotes a confident family to "active";
# a low-confidence one stays "provisional" and is not yet a stable published chip).
ACTIVE_STATUS = "active"

# supercategory id -> short, self-explanatory qualifier noun for the parenthetical suffix.
# Covers every closed supercategory (validated at import); a new supercategory without a
# qualifier fails loud here rather than silently shipping a bare-label collision.
SUPERCATEGORY_QUALIFIER: dict[str, str] = {
    "imaging_image_analysis":                "imaging",
    "microscopy_histology":                  "microscopy",
    "genomics_sequencing":                   "genomics",
    "mass_spec_proteomics":                  "mass spectrometry",
    "computational_statistical":             "computational",
    "clinical_instruments_assays":           "clinical",
    "animal_cell_models":                    "models",
    "molecular_biochem_reagents":            "research reagents",
    "therapeutics_interventions":            "therapeutics",
    "datasets_cohorts":                      "datasets",
    "software_informatics":                  "software",
    "structural_biophysical":                "structural biophysics",
    "functional_metabolic_cellular_assays":  "metabolic",
    "other":                                 "other",
}

# Fail loud at import if the qualifier map drifts from the frozen supercategory vocab —
# a new supercategory must get a qualifier (else its forks would publish bare collisions),
# and an unknown key is a typo that would never match.
_missing_q = vocab.SUPERCATEGORY_IDS - set(SUPERCATEGORY_QUALIFIER)
if _missing_q:
    raise ValueError(
        f"family_label_uniqueness: supercategory/categories missing a disambiguation "
        f"qualifier: {sorted(_missing_q)}")
_unknown_q = set(SUPERCATEGORY_QUALIFIER) - vocab.SUPERCATEGORY_IDS
if _unknown_q:
    raise ValueError(
        f"family_label_uniqueness: qualifier map has key(s) not in the supercategory "
        f"vocab: {sorted(_unknown_q)}")


def _is_active(fam: dict) -> bool:
    return fam.get("status") == ACTIVE_STATUS


def _qualified(label: str, supercategory: str) -> str:
    """``label`` + a supercategory-derived parenthetical; idempotent (never double-qualifies)."""
    suffix = f" ({SUPERCATEGORY_QUALIFIER[supercategory]})"
    base = (label or "").rstrip()
    return base if base.endswith(suffix) else f"{base}{suffix}"


def enforce_label_uniqueness(family_registry, *, active_only: bool = True) -> list[dict]:
    """Disambiguate cross-supercategory label collisions; gate on any that cannot be.

    Mutates the registry in place (appends a supercategory qualifier to each colliding
    family's ``label`` via ``FamilyRegistry.relabel``) and returns audit deltas
    ``[{op:"disambiguate", family_id, old_label, new_label, supercategory, qualifier}]``.

    Fail-loud and ATOMIC — every problem is detected in a first pass and raises BEFORE any
    family is mutated, so a gated build never leaves a half-disambiguated registry:
      - a colliding family whose supercategory has no qualifier noun (vocab drift) raises;
      - a residual collision the qualifier cannot split — >=2 families sharing the SAME label
        in the SAME supercategory (a missed merge) — raises, naming the families;
      - a planned qualifier that would newly collide with a label OUTSIDE its group (defense
        in depth) raises.

    ``active_only`` (default True) scopes the invariant to chip-renderable families
    (``status == "active"``); a provisional family on a placeholder label is still in the
    review queue and is not yet a published chip.
    """
    fams = [f for f in family_registry.records() if (not active_only or _is_active(f))]

    groups: dict[str, list[dict]] = {}
    for f in fams:
        key = norm_name(f.get("label") or "")
        if key:
            groups.setdefault(key, []).append(f)

    # --- Pass 1: plan every relabel + detect every problem BEFORE mutating anything. -------
    plan: list[tuple[dict, str]] = []          # (family, new_label) to apply in pass 2
    unresolved: list[dict] = []                # same-SC collisions the qualifier cannot split
    for _key, group in sorted(groups.items()):
        if len(group) < 2:
            continue
        bad_sc = sorted({f.get("supercategory") for f in group
                         if f.get("supercategory") not in SUPERCATEGORY_QUALIFIER})
        if bad_sc:
            raise ValueError(
                f"family_label_uniqueness: family/families in label group «{group[0].get('label')}» "
                f"carry a supercategory with no qualifier noun: {bad_sc}")

        proposals = [(f, _qualified(f.get("label"), f["supercategory"])) for f in group]

        # A residual collision among the PROPOSED labels means the qualifier could not split
        # them — i.e. >=2 families share label AND supercategory (same noun): a missed merge,
        # not a disambiguation. Record it for the gate; never plan a half-resolved group.
        proposed_by_norm: dict[str, list[str]] = {}
        for f, new_label in proposals:
            proposed_by_norm.setdefault(norm_name(new_label), []).append(f["family_id"])
        residual = {k: ids for k, ids in proposed_by_norm.items() if len(ids) > 1}
        if residual:
            for _k, ids in sorted(residual.items()):
                clash = next(f for f in group if f["family_id"] == ids[0])
                unresolved.append({
                    "label": group[0].get("label"),
                    "family_ids": sorted(ids),
                    "supercategory": clash.get("supercategory"),
                })
            continue

        plan.extend((f, nl) for f, nl in proposals if nl != f.get("label"))

    if unresolved:
        detail = "; ".join(
            f"«{u['label']}» {u['family_ids']} (supercategory={u['supercategory']})"
            for u in unresolved)
        raise ValueError(
            f"family_label_uniqueness: {len(unresolved)} label collision(s) the cross-supercategory "
            f"qualifier cannot disambiguate (same supercategory — a missed merge): resolve via a merge "
            f"or bespoke relabel in config/family_adhoc_dedup.json. {detail}")

    # Defense in depth — verify the PLANNED label set is globally unique over the active
    # families (catches a qualifier that would newly collide with a label outside its group),
    # still BEFORE any mutation.
    planned = {f["family_id"]: nl for f, nl in plan}
    final: dict[str, list[str]] = {}
    for f in fams:
        key = norm_name(planned.get(f["family_id"], f.get("label")) or "")
        if key:
            final.setdefault(key, []).append(f["family_id"])
    dupes = {k: sorted(ids) for k, ids in final.items() if len(ids) > 1}
    if dupes:
        raise ValueError(
            f"family_label_uniqueness: post-disambiguation invariant would be violated — {len(dupes)} "
            f"normalized label(s) still shared by active families: "
            + "; ".join(f"{k}={ids}" for k, ids in sorted(dupes.items())))

    # --- Pass 2: apply (reached only when the whole plan is problem-free). -----------------
    deltas: list[dict] = []
    for f, new_label in plan:
        old = f.get("label")
        family_registry.relabel(f["family_id"], new_label)
        deltas.append({
            "op": "disambiguate",
            "family_id": f["family_id"],
            "old_label": old,
            "new_label": new_label,
            "supercategory": f["supercategory"],
            "qualifier": SUPERCATEGORY_QUALIFIER[f["supercategory"]],
        })

    logger.info(
        "family_label_uniqueness: %d family/families disambiguated across %d colliding label group(s)",
        len(deltas), len({d["old_label"] for d in deltas}))
    return deltas

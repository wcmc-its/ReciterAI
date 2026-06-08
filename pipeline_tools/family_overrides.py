"""D-07 review fix batch — deterministic post-formation family overrides (v6 → v7).

A closed, ENUMERATED correction layer applied AFTER ``form_families``/``dedup_families``
and BEFORE the cross-supercategory guard. No LLM, no reclassify, no reconcile re-run: it
mutates the in-memory ``FamilyRegistry`` and repoints member tools, so a $0 corpus re-run
reproduces the v6 family set and then applies this fixed diff. Sourced from
``out/tools/a2/_review/D-07-REVIEW.md`` (the 19 high-severity findings + the 8 actionable
cross-supercategory forks); see ``D-07-FIX-PLAN.md`` for the row-by-row provenance.

SCOPE NOTE — these are PER-FAMILY corrections, a deliberate one-time exception to the
"add only class-level rules, never per-tool ones" principle (the root-cause classify-prompt
fixes are a separate, logged v7 task). The table is meant to stay frozen, not to grow.

Order is load-bearing: **REROUTE → RELABEL → MERGE**. Merge runs last so every drop/keep
pair shares a supercategory once the reroutes have landed (asserted, fail-loud). Every
referenced ``family_id`` is verified present before any mutation.
"""

from __future__ import annotations

import logging

from pipeline_tools import vocab
from pipeline_tools.registry import FamilyRegistry, ToolRegistry

logger = logging.getLogger(__name__)

# (family_id, to_supercategory, why) — move the family + repoint its member tools' bucket.
REROUTES: tuple[tuple[str, str, str], ...] = (
    ("fam_0639", "animal_cell_models",           "viral infection MODELS, not bench reagents (#7 has direct siblings)"),
    ("fam_0152", "molecular_biochem_reagents",   "bench patch-clamp; #6 def excludes it (then merge → fam_0598)"),
    ("fam_0149", "imaging_image_analysis",        "echocardiography-derived cardiac function → #1 (echo rule)"),
    ("fam_0566", "molecular_biochem_reagents",   "flow/FACS = suspension cytometry, not microscopy (then merge → fam_0586)"),
    ("fam_0573", "therapeutics_interventions",   "operating microscope = surgical/procedural device"),
    ("fam_0371", "molecular_biochem_reagents",   "physical screening compound libraries, not a dataset"),
    ("fam_0373", "structural_biophysical",        "crystallography structure resources, not a dataset"),
    ("fam_0701", "molecular_biochem_reagents",   "EV isolation = bench molecular sample-prep (then merge → fam_0650)"),
    ("fam_0392", "molecular_biochem_reagents",   "viability/apoptosis is non-metabolic bench assay; fork #10 (then merge → fam_0604)"),
    ("fam_0114", "therapeutics_interventions",   "biopsy is procedural; fork #8 dominant home (then merge → fam_0769)"),
    ("fam_0442", "clinical_instruments_assays",  "specimen-collection clinical arm; fork #9 (then merge → fam_0116)"),
    ("fam_0705", "molecular_biochem_reagents",   "biospecimen-processing bench arm; fork #12 (then merge → fam_0616)"),
    ("fam_0319", "genomics_sequencing",           "comparative genomics belongs with #3; fork #16 (then merge → fam_0438)"),
    ("fam_0682", "animal_cell_models",            "in-vivo rodent behavior paradigms; fork #4 verifier (then merge → fam_0008)"),
)

# (family_id, new_label, why) — change the display label in place (no move).
RELABELS: tuple[tuple[str, str, str], ...] = (
    ("fam_0227", "Survey & outcome-measurement methods",
     "not PRO instruments — trial endpoints + survey scales; fork #0 frees the PRO label for #6 fam_0094"),
    ("fam_0614", "Bench molecular & biochemical measurement assays",
     "bench arm of the diagnostic fork; #6 fam_0107 keeps the clinical-diagnostic label; fork #6"),
    ("fam_0221", "Experimental study models (in vivo / in vitro)",
     "honest relabel of a setting/umbrella; member dispersal to #8/#7 deferred to v7"),
    ("fam_0745", "Biophysical binding and stability assays",
     "honest relabel of a vague umbrella; member dispersal deferred to v7"),
)

# (drop_id, keep_id, why) — keep_id is the OLDER survivor (never re-mints, D-06).
# Runs AFTER reroutes, so each pair is same-supercategory by the time it executes.
MERGES: tuple[tuple[str, str, str], ...] = (
    ("fam_0760", "fam_0751", "cryo-EM 'method' == 'instrumentation' duplicate (Pattern A, #12)"),
    ("fam_0747", "fam_0750", "X-ray crystallography 'method' == 'instrumentation' duplicate (#12)"),
    ("fam_0746", "fam_0743", "NMR 'spectroscopy' == 'instrumentation' duplicate (#12)"),
    ("fam_0532", "fam_0522", "untargeted metabolomics ⊂ MS-based metabolomics (#4)"),
    ("fam_0502", "fam_0454", "RECIST/BI-RADS = radiological scoring, mislabeled 'clinical outcome measures' (#1)"),
    ("fam_0846", "fam_0884", "members are electrical STIMULATION, mislabeled 'recording assays' (#9)"),
    ("fam_0026", "fam_0006", "transgenic rodent == transgenic mouse (parasite outliers' move deferred to v7) (#7)"),
    ("fam_0152", "fam_0598", "patch-clamp into bench electrophysiology, reroute-induced (#8)"),
    ("fam_0566", "fam_0586", "flow cytometry into flow cytometry assays, reroute-induced (#8)"),
    ("fam_0701", "fam_0650", "EV isolation duplicate the fork-guard missed (suffix differs) (#8)"),
    ("fam_0392", "fam_0604", "viability/apoptosis duplicate in #8; fork #10"),
    ("fam_0114", "fam_0769", "biopsy duplicate, consolidate to #9; fork #8"),
    ("fam_0442", "fam_0116", "specimen-collection duplicate in #6; fork #9"),
    ("fam_0705", "fam_0616", "biospecimen-processing duplicate in #8; fork #12"),
    ("fam_0319", "fam_0438", "comparative-genomics duplicate in #3; fork #16"),
    ("fam_0682", "fam_0008", "into Behavioral animal models; fork #4 verifier correction (#7)"),
)


def _validate(family_registry: FamilyRegistry, reroutes, relabels, merges) -> None:
    """Fail loud on a stale id or an invalid reroute target BEFORE any mutation."""
    referenced = (
        {fid for fid, _, _ in reroutes}
        | {fid for fid, _, _ in relabels}
        | {d for d, _, _ in merges}
        | {k for _, k, _ in merges}
    )
    missing = sorted(f for f in referenced if family_registry.get(f) is None)
    if missing:
        raise ValueError(f"family_overrides references unknown family_id(s): {missing}")
    bad_targets = sorted({sc for _, sc, _ in reroutes if not vocab.is_valid_supercategory(sc)})
    if bad_targets:
        raise ValueError(f"family_overrides reroute target(s) not valid supercategories: {bad_targets}")


def _apply_tables(family_registry, tool_registry, reroutes, relabels, merges) -> list[dict]:
    """Apply (reroute → relabel → merge) tables; returns audit deltas. Parametric core."""
    _validate(family_registry, reroutes, relabels, merges)
    deltas: list[dict] = []

    # 1. REROUTE — family bucket + every member tool's bucket.
    for fid, to_sc, why in reroutes:
        fam = family_registry.get(fid)
        old = fam.get("supercategory")
        fam["supercategory"] = to_sc
        for tid in fam.get("member_tool_ids", []):
            if tool_registry.get(tid) is not None:
                tool_registry.update_classification(tid, supercategory=to_sc)
        deltas.append({"op": "reroute", "family_id": fid, "from": old, "to": to_sc, "why": why})

    # 2. RELABEL — display label only (no move, no status change).
    for fid, new_label, why in relabels:
        fam = family_registry.get(fid)
        old_label = fam.get("label")
        family_registry.relabel(fid, new_label)
        deltas.append({"op": "relabel", "family_id": fid, "old_label": old_label,
                       "new_label": new_label, "why": why})

    # 3. MERGE — drop → keep (older survivor); repoint moved tools' member_of_family.
    for drop_id, keep_id, why in merges:
        keep = family_registry.get(keep_id)
        drop = family_registry.get(drop_id)
        if keep.get("supercategory") != drop.get("supercategory"):
            raise ValueError(
                f"override merge {drop_id} → {keep_id} crosses supercategory "
                f"({drop.get('supercategory')} != {keep.get('supercategory')}); "
                "a reroute must land the drop in the keep's bucket first")
        moved = family_registry.merge_into(keep_id=keep_id, drop_id=drop_id)
        for tid in moved:
            tool_registry.update_classification(tid, member_of_family=keep_id)
        deltas.append({"op": "merge", "drop_id": drop_id, "keep_id": keep_id,
                       "moved_tool_ids": len(moved), "why": why})

    logger.info("family_overrides: %d reroute, %d relabel, %d merge (families -%d)",
                len(reroutes), len(relabels), len(merges), len(merges))
    return deltas


def apply_overrides(
    family_registry: FamilyRegistry, tool_registry: ToolRegistry,
) -> list[dict]:
    """Apply the frozen D-07 reroute/relabel/merge diff. Returns audit deltas.

    Mutates ``family_registry`` (family ``supercategory``/``label``, merges) and
    ``tool_registry`` (each affected tool's ``supercategory`` and, for merged drops,
    ``member_of_family``). ``method_tools`` in the caller share these record objects,
    so they follow automatically. Raises on a stale id, a bad target supercategory, or a
    merge whose pair is still cross-supercategory (a missing/misordered reroute) — and
    on a second application (the merged drop ids are gone), which is the intended
    fail-loud, not silent re-run.
    """
    return _apply_tables(family_registry, tool_registry, REROUTES, RELABELS, MERGES)

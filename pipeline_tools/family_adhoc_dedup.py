"""Ad-hoc family de-duplication layer — the accreting, review-driven dedup batch.

The third and *openly growing* family-correction layer, applied AFTER the frozen
D-07 overrides (:mod:`pipeline_tools.family_overrides`) and the reviewed 820
consolidation (:mod:`pipeline_tools.family_consolidation`). Where those two are
closed reviewed snapshots, this one accretes: each batch is sourced from the
scholar-co-assignment dupe scan (``cli/find_family_dupes.py`` →
``out/tools/a2/_review/family-dupe-candidates.md``), adjudicated by a human, and
appended to ``config/family_adhoc_dedup.json``.

Why a separate layer and not more rows in v820:
  - keeps the two reviewed batches immutable/auditable;
  - the v820 consolidation is **within-supercategory only** (it passes ``reroutes=()``),
    so it cannot express the highest-value catches — the cross-supercategory
    near-dupes the within-supercategory reconcile is structurally blind to. This
    layer carries ``reroutes``, so a cross-SC pair (reroute the loser into the
    survivor's bucket, then merge) is expressible;
  - it runs *after* v820's display-tiering, and the only structural change a merge
    makes is to *remove* the dropped family record (whose ``display`` tier goes
    with it) — so no tier bookkeeping is needed here, as long as every drop_id
    references a post-v820 survivor (fail-loud-validated by ``_apply_tables``).

Reuses the D-07 ``_apply_tables`` core: order is load-bearing **reroute → relabel →
merge**, every id validated present before any mutation, every merge asserted
same-supercategory (a missing/misordered reroute fails loud). ``$0`` to re-run.

See ``Projects/ReciterAI - Planning/family-dedup-coassignment-process-PLAN.md``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pipeline_tools.family_consolidation import _VALID_TIERS
from pipeline_tools.family_overrides import _apply_tables

logger = logging.getLogger(__name__)

_DATA_PATH = Path(__file__).resolve().parent.parent / "config" / "family_adhoc_dedup.json"


def _load(data_path: Path = _DATA_PATH) -> tuple[tuple, tuple, tuple, dict]:
    """Load + shape the ad-hoc diff into the (reroutes, relabels, merges, tiers) the apply uses."""
    data = json.loads(Path(data_path).read_text(encoding="utf-8"))
    reroutes = tuple((fid, to_sc, why) for fid, to_sc, why in data.get("reroutes", []))
    relabels = tuple((fid, new_label, why) for fid, new_label, why in data.get("relabels", []))
    merges = tuple(
        (drop_id, keep_id, f"{why} (conf:{conf})")
        for drop_id, keep_id, conf, why in data.get("merges", [])
    )
    tiers = dict(data.get("tiers", {}))
    return reroutes, relabels, merges, tiers


def apply_adhoc_dedup(family_registry, tool_registry, *, data_path: Path = _DATA_PATH) -> list[dict]:
    """Apply the accreting ad-hoc dedup diff (reroute → relabel → merge → tier). Returns audit deltas.

    The optional ``tiers`` block sets ``display`` on SURVIVING families AFTER the merges
    (e.g. restoring a merge survivor that absorbed a ``feature`` family) — the reviewed
    v820 tiers stay immutable; a tier targeting a merge drop (now gone) fails loud.

    Mutates both registries in place (family ``supercategory``/``label``/``display``, merges) and
    repoints moved tools' ``member_of_family``/``supercategory``; the caller's
    ``method_tools`` share the record objects, so they follow. Fail-loud: a stale id,
    a bad reroute-target supercategory, or a merge still crossing supercategories all
    raise BEFORE a half-applied set can reach the publish payload — and a second
    application raises (the merged drop ids are gone), the intended fail-loud, not a
    silent re-run.

    Defensive post-check: if the registry was display-tiered upstream (v820), every
    surviving family must still carry a ``display`` tier — a merge only removes the
    drop, so this holds unless a future edit breaks the invariant.
    """
    reroutes, relabels, merges, tiers = _load(data_path)
    deltas = _apply_tables(family_registry, tool_registry, reroutes, relabels, merges)

    # Post-merge display-tier corrections on SURVIVORS only (v820 tiers stay immutable).
    bad = sorted({t for t in tiers.values() if t not in _VALID_TIERS})
    if bad:
        raise ValueError(f"family_adhoc_dedup: invalid display tier value(s): {bad}")
    for fid, tier in tiers.items():
        fam = family_registry.get(fid)
        if fam is None:
            raise ValueError(
                f"family_adhoc_dedup: tier override targets non-surviving family {fid} "
                "(a merge drop is not a valid tier target)")
        old = fam.get("display")
        fam["display"] = tier
        deltas.append({"op": "tier", "family_id": fid, "from": old, "to": tier})

    survivors = family_registry.records()
    if survivors and any("display" in f for f in survivors):
        missing = [f["family_id"] for f in survivors if "display" not in f]
        if missing:
            raise ValueError(
                f"family_adhoc_dedup: {len(missing)} surviving family(ies) lost their display "
                f"tier (would publish a half-tiered set): {missing[:10]}")

    logger.info("family_adhoc_dedup: %d reroute, %d relabel, %d merge, %d tier (families -%d)",
                len(reroutes), len(relabels), len(merges), len(tiers), len(merges))
    return deltas

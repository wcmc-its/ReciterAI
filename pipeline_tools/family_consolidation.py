"""820-family consolidation batch — within-supercategory merges + relabels + display tiers.

Applied AFTER the D-07 frozen override batch (:mod:`pipeline_tools.family_overrides`),
on the post-v7' (872-family) registry, BEFORE the cross-supercategory guard re-checks.
Data-driven from ``config/family_consolidation_v820.json`` — the human-reviewed diff
extracted from the ``method-family-consolidation`` review of the published
``tools-a2-v1`` taxonomy. Nothing here re-derives the proposals; it applies the reviewed
diff with the same fail-loud machinery as the D-07 batch.

  - **merges**: 52 within-supercategory absorbs → 820 surviving families. ``keep_id`` is
    the proposal's largest-member survivor (durable id, never re-minted, D-06).
  - **relabels**: survivor display-label changes.
  - **tiers**: a ``display`` ∈ {feature, standard, suppressed} on every surviving family,
    surfaced in the published artifact for the SPS Methods lens.

Gated in ``run_corpus`` (``apply_consolidation=``) exactly like the overrides, so other
callers are unaffected. Order is load-bearing: it runs after the D-07 reroute/relabel/merge
(its ids reference the post-v7' set) and reuses that module's ``_apply_tables`` core, which
fail-loud-validates every id and asserts each merge is same-supercategory.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pipeline_tools.family_overrides import _apply_tables

logger = logging.getLogger(__name__)

_DATA_PATH = Path(__file__).resolve().parent.parent / "config" / "family_consolidation_v820.json"
_VALID_TIERS = {"feature", "standard", "suppressed"}


def _load() -> tuple[tuple, tuple, dict]:
    """Load + shape the consolidation diff into the (relabels, merges, tiers) the apply uses."""
    data = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    relabels = tuple(
        (fid, new_label, f"consolidation relabel (conf:{conf})")
        for fid, new_label, conf in data["relabels"]
    )
    merges = tuple(
        (absorb_id, keep_id, f"consolidation merge: {why} (conf:{conf})")
        for absorb_id, keep_id, conf, why in data["merges"]
    )
    tiers = dict(data["tiers"])
    return relabels, merges, tiers


def _apply(family_registry, tool_registry, relabels, merges, tiers) -> list[dict]:
    """Parametric core: apply (relabel → merge) then set display tiers. Fail-loud."""
    # reroutes=() — every consolidation merge is within-supercategory (validated upstream).
    deltas = _apply_tables(family_registry, tool_registry, (), relabels, merges)

    bad = sorted({t for t in tiers.values() if t not in _VALID_TIERS})
    if bad:
        raise ValueError(f"family_consolidation: invalid display tier value(s): {bad}")
    survivors = {f["family_id"] for f in family_registry.records()}
    tier_ids = set(tiers)
    missing = sorted(survivors - tier_ids)
    if missing:
        raise ValueError(
            f"family_consolidation: {len(missing)} surviving family(ies) have no display "
            f"tier (would publish a half-tiered set): {missing[:10]}")
    stale = sorted(tier_ids - survivors)
    if stale:
        raise ValueError(
            f"family_consolidation: {len(stale)} tier(s) reference non-surviving "
            f"family(ies): {stale[:10]}")

    for fid in survivors:
        family_registry.get(fid)["display"] = tiers[fid]

    counts = {tier: sum(1 for v in tiers.values() if v == tier) for tier in _VALID_TIERS}
    deltas.append({"op": "tiers", "families_tiered": len(survivors), **counts})
    logger.info(
        "family_consolidation: %d relabel, %d merge, %d tiered → %d families (%s)",
        len(relabels), len(merges), len(survivors), len(survivors), counts)
    return deltas


def apply_consolidation(family_registry, tool_registry) -> list[dict]:
    """Apply the bundled 820 consolidation (relabel → merge) then set per-family tiers.

    Mutates both registries in place and repoints merged tools' ``member_of_family``
    (the caller's ``method_tools`` share the record objects, so they follow). Fail-loud:
    a stale id (caught by ``_apply_tables``), an invalid tier value, a surviving family
    left without a tier, or a tier pointing at a non-surviving family all raise BEFORE
    a half-consolidated set can reach the publish payload. Returns audit deltas.
    """
    relabels, merges, tiers = _load()
    return _apply(family_registry, tool_registry, relabels, merges, tiers)

"""Family relabel (§7.2) + dedup sweep (§7) over the family registry.

Two A2-time family-maintenance passes the seed could not run (it had no real
members):

  - **Relabel (§7.2).** A minted family borrows its first member's raw name as a
    placeholder. This pass replaces it with one controlled, kind-aware label that
    names the capability (task / agent class / drug-or-therapy modality /
    technique) and stays in "how-space", and promotes a confidently relabeled
    family ``provisional -> active``. LLM-backed via the injected ``call_json``
    seam (Bedrock Sonnet -> OpenAI gpt-5.x), partial-failure tolerant.

  - **Dedup sweep (§7).** Two provisional singletons can be the same family if
    they minted below the match threshold and then relabeled identically. This
    pass merges exact-label duplicates **within one supercategory** onto the
    OLDER ``family_id`` (D-06: never mint on merge, or every faculty ``tool_score``
    keyed on the dropped id is orphaned). It is deliberately the high-precision
    case only — cross-supercategory near-duplicates are NOT auto-merged here; the
    §7-step-2 cross-supercategory flags already route those to the bounded
    exceptions queue for human review.
"""

from __future__ import annotations

import logging

from pipeline_tools.registry import FamilyRegistry, norm_name

logger = logging.getLogger(__name__)

DEFAULT_RELABEL_BATCH = 40
RELABEL_LOW_CONFIDENCE_FLAG = "family_relabel_low_confidence"


def relabel_families(
    families: FamilyRegistry,
    *,
    call_json,
    batch_size: int = DEFAULT_RELABEL_BATCH,
) -> list[dict]:
    """Assign §7.2 controlled labels to every family; promote confident ones to active.

    Returns relabel deltas ``[{family_id, old_label, new_label, status,
    confidence}]`` (one per family actually relabeled). A confident relabel
    promotes the family to ``active``; a low-confidence one keeps it
    ``provisional`` so it stays in the review queue. Partial-failure tolerant: a
    batch whose LLM call raises leaves its families on their placeholder labels
    (still provisional) and the sweep continues.
    """
    from prompts.tool_family_relabel import RELABEL_SYSTEM_PROMPT, build_relabel_user_message

    records = families.records()
    deltas: list[dict] = []
    batches = [records[i:i + batch_size] for i in range(0, len(records), batch_size)]
    for bi, batch in enumerate(batches, 1):
        try:
            resp = call_json(RELABEL_SYSTEM_PROMPT, build_relabel_user_message(batch))
            entries = resp.get("families", []) if isinstance(resp, dict) else []
        except Exception as exc:  # noqa: BLE001 — partial-failure tolerance by design
            logger.warning("relabel batch %d/%d failed (%s); %d family label(s) left as placeholder",
                           bi, len(batches), exc, len(batch))
            continue
        model = resp.get("_model") if isinstance(resp, dict) else None  # provenance
        by_id = {e.get("family_id"): e for e in entries if e.get("family_id")}
        for fam in batch:
            entry = by_id.get(fam["family_id"])
            new_label = (entry or {}).get("label")
            if not entry or not (new_label or "").strip():
                continue
            new_label = new_label.strip()
            confidence = "low" if str(entry.get("confidence", "")).lower() == "low" else "high"
            status = "active" if confidence == "high" else "provisional"
            old_label = fam.get("label")
            families.relabel(fam["family_id"], new_label, status=status)
            deltas.append({
                "family_id": fam["family_id"],
                "old_label": old_label,
                "new_label": new_label,
                "status": status,
                "confidence": confidence,
                "model": model,
            })
    logger.info("relabel: %d/%d families relabeled (%d low-confidence)",
                len(deltas), len(records), sum(1 for d in deltas if d["confidence"] == "low"))
    return deltas


def dedup_families(families: FamilyRegistry) -> list[dict]:
    """§7 dedup sweep — merge exact-label same-supercategory duplicates (D-06).

    Groups families by ``(supercategory, normalized label)``; any group with more
    than one family is a fork. The lowest (oldest) ``family_id`` survives; the
    rest merge into it via ``FamilyRegistry.merge_into`` (members accrete, id is
    never reminted). Returns merge deltas ``[{keep_id, drop_id, label,
    supercategory, moved_tool_ids}]`` — the caller repoints every moved tool's
    ``member_of_family`` to ``keep_id``.
    """
    groups: dict[tuple[str, str], list[str]] = {}
    for fam in families.records():
        label_key = norm_name(fam.get("label") or "")
        if not label_key:
            continue
        groups.setdefault((fam.get("supercategory") or "", label_key), []).append(fam["family_id"])

    deltas: list[dict] = []
    for (supercat, _label_key), fids in groups.items():
        if len(fids) < 2:
            continue
        keep_id = min(fids)  # zero-padded ids sort oldest-first
        survivor = families.get(keep_id)
        for drop_id in sorted(fids):
            if drop_id == keep_id:
                continue
            moved = families.merge_into(keep_id=keep_id, drop_id=drop_id)
            deltas.append({
                "keep_id": keep_id,
                "drop_id": drop_id,
                "label": survivor.get("label") if survivor else None,
                "supercategory": supercat,
                "moved_tool_ids": moved,
            })
    if deltas:
        logger.info("family dedup: merged %d forked family/families onto %d survivor(s)",
                    len(deltas), len({d['keep_id'] for d in deltas}))
    return deltas

"""Faculty rollup — per-(scholar, tool) and per-(scholar, family) counts (§7.1 / §8).

The profile surfaces need TWO grounded counts the institutional registry does not
carry, and the spec pins how each is computed:

  - **per-(scholar, tool) pub_count** (§7.1): the scholar's own lead/senior pubs
    touching a given canonical tool. Drives the per-profile exemplar ranking — a
    scholar's signature tool must rank by *their* usage, not institutional
    salience, or a low-spread signature tool gets buried on its own profile.

  - **profile family-row count** (§8, with the v3 C-reconciliation): the scholar's
    lead/senior pubs touching any **non-C-tier** member of the family. A pub whose
    only family hit is a C-tier member does NOT count (C is "excluded from the
    counts that drive ranking", §5) — so a family surfaces on a profile only when
    the scholar touched a B-or-better member.

Both are PUBLICATION-scoped: grant-sourced occurrences (``source_kind="grant"``)
feed identity/salience/families but stay out of the publication pub-filter, so
they never enter this rollup (docs/tools-producer-model.md §grants). Pure — no DB,
no LLM — so it unit-tests on synthetic occurrences; the orchestrator supplies the
real ones.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from pipeline_tools import vocab

logger = logging.getLogger(__name__)

DEFAULT_EXEMPLARS_PER_FAMILY = 3


def build_faculty_rollup(
    occurrences: list[dict],
    *,
    tool_index: dict[str, dict],
    family_index: dict[str, dict],
    exemplars_per_family: int = DEFAULT_EXEMPLARS_PER_FAMILY,
) -> dict[str, dict]:
    """Roll occurrences up per faculty into tool + family rows.

    Args:
        occurrences: exploded rows ``{canonical_tool_id, pmid, cwid, author_role,
            source_kind}`` — one per (tool mention, faculty author). Only
            ``source_kind="publication"`` rows on a ``method_tool`` canonical id
            contribute.
        tool_index: ``canonical_tool_id -> {display_name, disposition,
            salience_tier, member_of_family, supercategory}``.
        family_index: ``family_id -> {label, supercategory}``.

    Returns:
        ``{cwid: {"cwid", "tools": [...], "families": [...]}}`` where each tool row
        is ``{canonical_tool_id, display_name, pub_count, pmids}`` (that scholar's
        distinct pmids on the tool) and each family row is ``{family_id, label,
        supercategory, pub_count, pmids, exemplar_tool_ids}`` with the C-reconciled
        count and per-profile exemplars ranked by the scholar's own per-tool
        pub_count. ``pmids`` is the exact distinct set ``pub_count`` counts, so
        ``len(pmids) == pub_count`` on every row (#175); consumers map a tool or
        family back to the scholar's contributing publications without a join.
    """
    # 1. per (cwid, canonical_tool_id) -> distinct pmids (publication, method_tool only).
    tool_pmids: dict[tuple[str, str], set[str]] = defaultdict(set)
    for occ in occurrences:
        if (occ.get("source_kind") or "publication") != "publication":
            continue
        cid = occ.get("canonical_tool_id")
        cwid = occ.get("cwid")
        pmid = str(occ.get("pmid") or "")
        if not cid or not cwid or not pmid:
            continue
        tool = tool_index.get(cid)
        if not tool or tool.get("disposition") != vocab.CAPABILITY_DISPOSITION:
            continue
        tool_pmids[(cwid, cid)].add(pmid)

    by_cwid: dict[str, dict[str, set[str]]] = defaultdict(dict)
    for (cwid, cid), pmids in tool_pmids.items():
        by_cwid[cwid][cid] = pmids

    rollup: dict[str, dict] = {}
    for cwid, cidmap in by_cwid.items():
        tool_rows = sorted(
            (
                {
                    "canonical_tool_id": cid,
                    "display_name": (tool_index.get(cid) or {}).get("display_name"),
                    "pub_count": len(pmids),
                    # The distinct pmid SET pub_count counts — emitted, not just its
                    # cardinality, so a consumer can map tool → the scholar's pubs.
                    # Invariant: len(pmids) == pub_count (#175).
                    "pmids": sorted(pmids),
                }
                for cid, pmids in cidmap.items()
            ),
            key=lambda r: (-r["pub_count"], r["canonical_tool_id"]),
        )

        # Family rows with C-reconciliation: only NON-C members contribute pmids.
        fam_pmids: dict[str, set[str]] = defaultdict(set)
        fam_members: dict[str, list[tuple[str, int]]] = defaultdict(list)
        for cid, pmids in cidmap.items():
            tool = tool_index.get(cid) or {}
            fid = tool.get("member_of_family")
            if not fid or tool.get("salience_tier") == vocab.DEMOTED_TIER:
                continue
            fam_pmids[fid] |= pmids
            fam_members[fid].append((cid, len(pmids)))

        fam_rows = []
        for fid, pmids in fam_pmids.items():
            fam = family_index.get(fid) or {}
            members_sorted = sorted(fam_members[fid], key=lambda x: (-x[1], x[0]))
            fam_rows.append({
                "family_id": fid,
                "label": fam.get("label"),
                "supercategory": fam.get("supercategory"),
                "pub_count": len(pmids),
                # The C-reconciled distinct pmid set (union over non-C members) that
                # pub_count counts — emitted for family → scholar-pubs mapping.
                # Invariant: len(pmids) == pub_count (#175).
                "pmids": sorted(pmids),
                "exemplar_tool_ids": [cid for cid, _ in members_sorted[:exemplars_per_family]],
            })
        fam_rows.sort(key=lambda r: (-r["pub_count"], r["family_id"]))

        rollup[cwid] = {"cwid": cwid, "tools": tool_rows, "families": fam_rows}

    logger.info(
        "faculty rollup: %d scholar(s), %d (scholar,tool) pair(s), %d (scholar,family) row(s)",
        len(rollup), sum(len(v["tools"]) for v in rollup.values()),
        sum(len(v["families"]) for v in rollup.values()),
    )
    return rollup

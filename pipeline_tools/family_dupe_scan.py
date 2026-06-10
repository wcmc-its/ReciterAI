"""Surface near-duplicate method families via scholar co-assignment.

The corpus-wide reconcile pass (``family_rebuild.reconcile_classes``) dedups
families *within a supercategory* and is deliberately biased to keep-separate.
That leaves two residue classes it cannot fix by construction:

  1. **Within-supercategory keep-separate residue** — same-capability families it
     saw and chose to keep apart (e.g. "Psychometric rating scales" vs
     "Psychiatric and psychological rating scales", both ``clinical_instruments_assays``).
  2. **Cross-supercategory near-dupes** — it never compares two families in
     different supercategories, and the cross-supercategory guard only flags
     *byte-identical* labels (not near-dupes). So "Survey and questionnaire
     instruments" forked across ``clinical_instruments_assays`` / ``computational_statistical``
     is invisible to the pipeline.

This scan is the only signal that sees both: when a *single scholar's* profile
page carries two near-duplicate family chips, that pair is demonstrably hurting a
real page. Co-assignment across many scholars ranks which residue is worth a
(global) fix.

Design (validated on the live 820-family registry — see
``Projects/ReciterAI - Planning/family-dedup-coassignment-process-PLAN.md``):

  - Co-assignment is a **prioritizer, not a detector** — raw co-assigned pairs at
    K=2 number ~8.8k; a cheap label-similarity gate collapses them to ~19.
  - **Member-tool overlap under-detects** (≤0.20 Jaccard even for true dupes —
    the whole reason two families exist is that they hold *different* tools that
    should share a class), so it is a weak OR-signal, never the gate.
  - The output is a **candidate list for human/LLM adjudication**, never an
    auto-merge: the gated shortlist deliberately mixes real dupes with genuine
    siblings (MS proteomics ≠ metabolomics; bulk ≠ single-cell RNA-seq), so the
    final arbiter is semantic, exactly as §3.2 of the architecture concluded.

Read-only and AWS-free: it reads the published ``family_registry.json`` +
``tool_faculty_rollup.json`` and emits a review artifact. Nothing here mutates a
registry — fixes are encoded by hand into ``config/family_adhoc_dedup.json`` after
review (:mod:`pipeline_tools.family_adhoc_dedup`).
"""

from __future__ import annotations

import json
import logging
import re
from itertools import combinations
from pathlib import Path

logger = logging.getLogger(__name__)

# Label tokens that carry no discriminating capability meaning — dropped before the
# Jaccard so "Survey and questionnaire INSTRUMENTS" vs "… METHODS" don't read as
# different on the boilerplate suffix alone, and a shared boilerplate token doesn't
# manufacture similarity.
_STOPWORDS = frozenset(
    "a an and or of the to for in with by using based on into from "
    "method methods study studies analysis analyses model models "
    "instrument instruments scale scales assay assays design designs "
    "measure measures measurement test tests tool tools system systems "
    "technique techniques approach approaches data".split()
)
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9\-]*")


def _tokens(text: str) -> set[str]:
    """Discriminating lowercase tokens of a label/name (stopwords + <3-char dropped)."""
    return {t for t in _TOKEN_RE.findall((text or "").lower()) if t not in _STOPWORDS and len(t) > 2}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _load_families(registry_path: Path) -> dict[str, dict]:
    data = json.loads(Path(registry_path).read_text(encoding="utf-8"))
    fams = data.get("families", data) if isinstance(data, dict) else data
    return {f["family_id"]: f for f in fams}


def scan_family_dupes(
    registry_path: Path,
    rollup_path: Path,
    *,
    k: int = 2,
    min_coscholars: int = 3,
    label_jaccard_min: float = 0.34,
    member_jaccard_min: float = 0.18,
    exemplars: int = 6,
    top_scholars: int = 8,
) -> dict:
    """Return near-duplicate family candidates ranked by scholar co-assignment.

    ``k`` — a family must cover >= k of a scholar's pubs to count for that scholar
    (filters incidental single-pub mentions; K=2 is the calibrated floor).
    ``min_coscholars`` — a pair must collide on >= this many scholars to enter the
    similarity-gated list (a pair worth a *global* fix recurs across scholars).
    A pair passes the gate if label Jaccard >= ``label_jaccard_min`` OR member-name
    Jaccard >= ``member_jaccard_min``.

    Returns ``{"identical_label_pairs": [...], "candidates": [...], "stats": {...}}``.
    ``identical_label_pairs`` is the deterministic sub-detector (every byte-identical
    label across >=2 families), emitted unconditionally with co-scholar support.
    """
    families = _load_families(registry_path)
    rollup = json.loads(Path(rollup_path).read_text(encoding="utf-8"))

    label_tok = {fid: _tokens(f.get("label")) for fid, f in families.items()}
    member_tok: dict[str, set[str]] = {}
    for fid, f in families.items():
        tk: set[str] = set()
        for name in (f.get("member_display_names") or [])[:200]:
            tk |= _tokens(name)
        member_tok[fid] = tk

    # 1. Co-assignment aggregation across scholars (the prioritizer). -----------
    pair_coscholars: dict[tuple[str, str], int] = {}
    pair_strength: dict[tuple[str, str], int] = {}          # Σ min(countA,countB)
    pair_scholars: dict[tuple[str, str], list[tuple[str, int]]] = {}  # (cwid, min_count)
    pair_codecount: dict[tuple[str, str], int] = {}         # co-assignment among *all* counted, any k>=1 (for id-label)
    n_scholars = 0
    for cwid, rec in rollup.items():
        n_scholars += 1
        counts = {f["family_id"]: f.get("pub_count", 0)
                  for f in (rec.get("families") or []) if f.get("family_id") in families}
        kept = {fid: c for fid, c in counts.items() if c >= k}
        for a, b in combinations(sorted(kept), 2):
            key = (a, b)
            m = min(kept[a], kept[b])
            pair_coscholars[key] = pair_coscholars.get(key, 0) + 1
            pair_strength[key] = pair_strength.get(key, 0) + m
            pair_scholars.setdefault(key, []).append((cwid, m))
        # k=1 co-occurrence for the identical-label support count
        seen = sorted(counts)
        for a, b in combinations(seen, 2):
            pair_codecount[(a, b)] = pair_codecount.get((a, b), 0) + 1

    def _row(a: str, b: str, *, co: int, strength: int) -> dict:
        fa, fb = families[a], families[b]
        scholars = sorted(pair_scholars.get((a, b), []), key=lambda x: -x[1])[:top_scholars]
        return {
            "family_a": a, "family_b": b,
            "label_a": fa.get("label"), "label_b": fb.get("label"),
            "supercategory_a": fa.get("supercategory"), "supercategory_b": fb.get("supercategory"),
            "same_supercategory": fa.get("supercategory") == fb.get("supercategory"),
            "co_scholars": co,
            "strength": strength,
            "label_jaccard": round(_jaccard(label_tok[a], label_tok[b]), 2),
            "member_jaccard": round(_jaccard(member_tok[a], member_tok[b]), 2),
            "members_a": len(fa.get("member_tool_ids") or []),
            "members_b": len(fb.get("member_tool_ids") or []),
            "exemplars_a": (fa.get("member_display_names") or [])[:exemplars],
            "exemplars_b": (fb.get("member_display_names") or [])[:exemplars],
            "top_scholars": [{"cwid": c, "min_count": m} for c, m in scholars],
        }

    # 2. Deterministic sub-detector — byte-identical labels (cross- or same-SC). -
    by_label: dict[str, list[str]] = {}
    for fid, f in families.items():
        by_label.setdefault((f.get("label") or "").strip().lower(), []).append(fid)
    identical = []
    for lab, ids in by_label.items():
        if len(ids) < 2:
            continue
        for a, b in combinations(sorted(ids), 2):
            co = pair_coscholars.get((a, b), 0)
            identical.append(_row(a, b, co=co, strength=pair_strength.get((a, b), 0)))
    identical.sort(key=lambda r: -r["co_scholars"])

    # 3. Similarity-gated candidate list (co-assignment recurrence). ------------
    candidates = []
    for (a, b), co in pair_coscholars.items():
        if co < min_coscholars:
            continue
        lj = _jaccard(label_tok[a], label_tok[b])
        mj = _jaccard(member_tok[a], member_tok[b])
        if lj < label_jaccard_min and mj < member_jaccard_min:
            continue
        candidates.append(_row(a, b, co=co, strength=pair_strength[(a, b)]))
    candidates.sort(key=lambda r: (-r["co_scholars"], -r["strength"]))

    stats = {
        "scholars": n_scholars,
        "families": len(families),
        "k": k,
        "min_coscholars": min_coscholars,
        "label_jaccard_min": label_jaccard_min,
        "member_jaccard_min": member_jaccard_min,
        "co_assigned_pairs": len(pair_coscholars),
        "identical_label_pairs": len(identical),
        "gated_candidates": len(candidates),
    }
    logger.info("family dupe scan: %d co-assigned pairs (k=%d) -> %d gated candidates, %d identical-label",
                len(pair_coscholars), k, len(candidates), len(identical))
    return {"identical_label_pairs": identical, "candidates": candidates, "stats": stats}


def render_markdown(result: dict) -> str:
    """Human review surface — one table for identical-label dupes, one for gated candidates."""
    s = result["stats"]
    out: list[str] = []
    out.append("# Method-family near-duplicate candidates (scholar co-assignment scan)\n")
    out.append(
        f"_{s['scholars']} scholars · {s['families']} families · k={s['k']} · "
        f"co-scholar floor {s['min_coscholars']} · {s['co_assigned_pairs']} co-assigned pairs → "
        f"**{s['gated_candidates']} gated candidates** + {s['identical_label_pairs']} identical-label._\n"
    )
    out.append(
        "> Co-assignment **prioritizes**, it does not **detect**. The list below mixes real dupes "
        "with genuine siblings (e.g. proteomics ≠ metabolomics, bulk ≠ single-cell RNA-seq) — adjudicate "
        "each as **merge / keep-separate / relabel-to-disambiguate**; never auto-merge. Encode accepted "
        "fixes into `config/family_adhoc_dedup.json`.\n"
    )

    def _table(rows: list[dict]) -> None:
        out.append("| co-sch | str | labJ | memJ | SC | pair |")
        out.append("|--:|--:|--:|--:|:--|:--|")
        for r in rows:
            sc = "same" if r["same_supercategory"] else f"✗ {r['supercategory_a']} / {r['supercategory_b']}"
            out.append(
                f"| {r['co_scholars']} | {r['strength']} | {r['label_jaccard']} | {r['member_jaccard']} | {sc} | "
                f"`{r['family_a']}` «{r['label_a']}» ({r['members_a']}) ⇄ "
                f"`{r['family_b']}` «{r['label_b']}» ({r['members_b']}) |"
            )
        out.append("")

    out.append("## Identical-label pairs (deterministic — all cross-supercategory by construction)\n")
    _table(result["identical_label_pairs"])
    out.append("## Similarity-gated co-assignment candidates\n")
    _table(result["candidates"])

    out.append("## Evidence (top candidates)\n")
    for r in result["candidates"][:12]:
        out.append(f"### `{r['family_a']}` «{r['label_a']}» ⇄ `{r['family_b']}` «{r['label_b']}»")
        out.append(f"- co-scholars: **{r['co_scholars']}**, strength {r['strength']}, "
                   f"{'same SC' if r['same_supercategory'] else 'cross-SC: ' + r['supercategory_a'] + ' / ' + r['supercategory_b']}")
        out.append(f"- A members (n={r['members_a']}): {', '.join(r['exemplars_a'])}")
        out.append(f"- B members (n={r['members_b']}): {', '.join(r['exemplars_b'])}")
        out.append(f"- top co-assigned scholars: {', '.join(f'{x['cwid']}({x['min_count']})' for x in r['top_scholars'])}")
        out.append("")
    return "\n".join(out)

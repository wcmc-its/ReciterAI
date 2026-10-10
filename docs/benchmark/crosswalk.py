"""One row per reference entry across every source, and how the research-area map represents it.

Reads the frame outputs written by frame_a.py and frames_bc.py; writes
outputs/research-areas-reference-crosswalk-<SNAPSHOT_DATE>.csv.
Run after both: python3 docs/benchmark/crosswalk.py
"""

import csv
import re
from pathlib import Path

HERE = Path(__file__).parent
IN, OUT = HERE / "inputs", HERE / "outputs"
# Date the inputs were frozen (pulled from SPS prod and the reference sources). Bump it when they are refreshed.
SNAPSHOT_DATE = "2026-10-10"

HOW = {
    "exact": "Exact match",
    "area_broader": "Contained in a broader research area",
    "area_narrower": "Covered by narrower research areas",
    "overlap": "Partial overlap",
    "none": "Not represented",
    "covered": "Home unit of a research area (lift >= 2, >= 25 papers)",
    "low_volume": "Aligned research area, but under 25 papers",
    "not_covered": "No research area concentrated here",
    "no_papers": "No scored papers from this unit",
    "out_of_scope": "Out of scope (operational, not a research domain)",
}
SHORT = {"exact": "exact match", "area_broader": "contains it", "area_narrower": "part of it", "overlap": "partial overlap"}
COLS = ["source", "entry_id", "entry", "detail", "how_represented", "match_basis", "represented_by"]


def read(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def plain(areas):
    """'Label [area_broader]; ...' -> 'Label (contains it); ...'"""
    return re.sub(r"\[(\w+)\]", lambda m: f"({SHORT[m.group(1)]})", areas)


def pair_basis(k):
    """ref_id -> 'agreed / adjudicated / gap_review' summary of how its pairs were decided."""
    out = {}
    for p in read(OUT / f"frame_{k}_pairs.csv"):
        out.setdefault(p["ref"], set()).add(p["source"])
    return {r: " + ".join(sorted(s)) for r, s in out.items()}


def main():
    rows = []
    for r in read(OUT / "frame_a_reverse.csv"):
        rows.append({
            "source": "WCM " + r["level"], "entry_id": r["unit"], "entry": r["unit_name"], "detail": "",
            "how_represented": HOW[r["status"]], "match_basis": "publication data",
            "represented_by": r["areas"],
        })

    care = {r["id"]: r for r in read(IN / "care_plan_items.csv")}
    basis = pair_basis("B")
    for r in read(OUT / "frame_B_reverse.csv"):
        c = care[r["ref"]]
        rows.append({"source": "CARE Strategic Plan", "entry_id": r["ref"], "entry": r["name"],
                     "detail": f'{c["pillar"]}: {c["section"]}', "how_represented": HOW[r["best"]],
                     "match_basis": basis.get(r["ref"], "both mappers: none"), "represented_by": plain(r["areas"])})
    for c in care.values():
        if c["in_scope"] == "no":
            rows.append({"source": "CARE Strategic Plan", "entry_id": c["id"], "entry": c["item"],
                         "detail": f'{c["pillar"]}: {c["section"]}', "how_represented": HOW["out_of_scope"],
                         "match_basis": c["note"], "represented_by": ""})

    for k, src, detail in (("C1", "NIH Institute/Center", None), ("C2", "NIH RCDC category", "fy2025_amount_musd")):
        basis = pair_basis(k)
        for r in read(OUT / f"frame_{k}_reverse.csv"):
            rows.append({"source": src, "entry_id": r["ref"], "entry": r["name"],
                         "detail": f'FY2025 ${r[detail]}M' if detail else "",
                         "how_represented": HOW[r["best"]],
                         "match_basis": basis.get(r["ref"], "both mappers + gap review: none"),
                         "represented_by": plain(r["areas"])})

    path = OUT / f"research-areas-reference-crosswalk-{SNAPSHOT_DATE}.csv"
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, COLS)
        w.writeheader()
        w.writerows(rows)
    print(len(rows), "rows ->", path.name)


if __name__ == "__main__":
    main()

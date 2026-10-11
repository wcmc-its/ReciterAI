"""Export the benchmark as one JSON snapshot for the Scholars site (SPS /about/research-areas).

Reads the frame outputs and inputs; no new judgement or computation beyond reshaping and counting.
Writes outputs/benchmark-<SNAPSHOT_DATE>.json, outputs/benchmark-latest.json,
outputs/research-areas-reference-crosswalk-latest.csv and upserts outputs/benchmark-index.json.
Run after crosswalk.py: python3 docs/benchmark/export_json.py
Enums stay machine values (display labels live in the page; crosswalk.HOW / SHORT are the reference).
No per-person fields: everything here is area- or unit-level.
"""

import json
import shutil
from collections import Counter

from crosswalk import IN, OUT, SNAPSHOT_DATE, read
from frame_a import LIFT, MIN_N

SCHEMA_VERSION = 1  # bump on any breaking change to the shape below
TAXONOMY_VERSION = "taxonomy_v2"
CHANGE_NOTE = "First published benchmark, redone from scratch against the live taxonomy."  # what moved since the previous snapshot (R4)
REPO = "https://github.com/wcmc-its/ReciterAI"
METHODS_URL = f"{REPO}/blob/main/docs/benchmark/README.md"
# Who filled in outputs/spot_check_sample.csv (D7). method is "human" or "automated"; the page says which.
SPOT_CHECK_METHOD = "automated"
SPOT_CHECK_REVIEWED_BY = "Independent automated check (Claude, separate run from the mappers, adjudicator and gap reviewer)"
SPOT_CHECK_REVIEWED_ON = "2026-10-11"
FRAMES = ("B", "C1", "C2")


def num(s):
    return float(s) if "." in s else int(s)


def build():
    summary = json.loads((OUT / "summary.json").read_text())
    fwd_a = read(OUT / "frame_a_forward.csv")
    rev_a = read(OUT / "frame_a_reverse.csv")
    fwd = {k: {r["area"]: r["best"] for r in read(OUT / f"frame_{k}_forward.csv")} for k in FRAMES}
    divs = {r["code"]: r["dept_code"] for r in read(IN / "wcm_divisions.csv")}

    areas = [{"id": r["area"], "label": r["label"], "papers": int(r["papers"]), "unit_status": r["status"],
              "largest_dept": r["largest_dept"], "best": {k: fwd[k][r["area"]] for k in FRAMES}} for r in fwd_a]
    units = [{"id": r["unit"], "level": r["level"], "name": r["unit_name"],
              "parent": divs.get(r["unit"]) if r["level"] == "division" else None, "status": r["status"]}
             for r in rev_a]
    unit_cells = [{"area": r["area"], "unit": r["unit"], "n": int(r["n"]), "share": num(r["share"]),
                   "lift": num(r["lift"])} for r in read(OUT / "frame_a_area_unit.csv")]

    refs = []
    care = {r["id"]: r for r in read(IN / "care_plan_items.csv")}
    best_b = {r["ref"]: r["best"] for r in read(OUT / "frame_B_reverse.csv")}
    for c in care.values():
        refs.append({"frame": "B", "id": c["id"], "name": c["item"], "in_scope": c["in_scope"] == "yes",
                     "best": best_b.get(c["id"], "out_of_scope"), "note": c["note"] or None,
                     "detail": {"pillar": c["pillar"], "section": c["section"]}})
    for r in read(OUT / "frame_C1_reverse.csv"):
        refs.append({"frame": "C1", "id": r["ref"], "name": r["name"], "in_scope": True, "best": r["best"],
                     "detail": {}})
    for r in read(OUT / "frame_C2_reverse.csv"):
        refs.append({"frame": "C2", "id": r["ref"], "name": r["name"], "in_scope": True, "best": r["best"],
                     "detail": {"fy2025_musd": num(r["fy2025_amount_musd"])}})

    pairs = [{"frame": k, "area": p["area"], "ref": p["ref"], "relation": p["relation"], "source": p["source"],
              "rationale": p["rationale"]} for k in FRAMES for p in read(OUT / f"frame_{k}_pairs.csv")]

    spot = read(OUT / "spot_check_sample.csv")
    done = [r for r in spot if r["reviewer_agrees"].strip()]
    agree = sum(r["reviewer_agrees"].strip().lower() in ("y", "yes", "1", "true") for r in done)
    complete = len(done) == len(spot)
    usd = {r["id"]: r["detail"]["fy2025_musd"] for r in refs if r["frame"] == "C2"}
    matched = {p["ref"] for p in pairs if p["frame"] == "C2"}

    a = Counter(r["status"] for r in fwd_a)
    u = Counter((r["level"], r["status"]) for r in rev_a)
    lv = Counter(r["level"] for r in rev_a)
    s = summary
    return {
        "schema_version": SCHEMA_VERSION,
        "snapshot_date": SNAPSHOT_DATE,
        "taxonomy_version": TAXONOMY_VERSION,
        "methods_url": METHODS_URL,
        "crosswalk_url": f"{REPO}/blob/main/docs/benchmark/outputs/research-areas-reference-crosswalk-{SNAPSHOT_DATE}.csv",
        "outputs_url": f"{REPO}/tree/main/docs/benchmark/outputs",
        "corpus": {"description": "Academic articles 2020+, WCM faculty first or last author",
                   "home_threshold": {"lift": LIFT, "min_papers": MIN_N}},
        "headline": {
            "A": {"areas_with_home": a["home"], "areas_low_volume": a["low_volume"], "areas_total": len(fwd_a),
                  "departments_covered": u["department", "covered"], "departments_total": lv["department"],
                  "divisions_covered": u["division", "covered"], "divisions_total": lv["division"]},
            "B": {"items_matched": s["B"]["refs_matched"], "items_in_scope": s["B"]["reference_entries"],
                  "areas_matched": s["B"]["areas_matched"]},
            "C1": {"ics_matched": s["C1"]["refs_matched"], "ics_total": s["C1"]["reference_entries"],
                   "areas_matched": s["C1"]["areas_matched"]},
            "C2": {"categories_matched": s["C2"]["refs_matched"], "categories_total": s["C2"]["reference_entries"],
                   "areas_matched": s["C2"]["areas_matched"], "areas_exact": s["C2"]["areas_by_best"]["exact"],
                   "dollars_matched_pct": round(100 * sum(usd[r] for r in matched) / sum(usd.values()), 1)},
        },
        "agreement": {k: {f: s[k][f] for f in ("positive_agree", "kappa_any", "kappa_relation", "adjudicated",
                                                 "gap_review_added", "mapper1_pairs", "mapper2_pairs", "final_pairs")}
                      for k in FRAMES},
        "spot_check": {"sample": len(spot), "agree": agree if complete else None,
                       "rate": round(agree / len(spot), 3) if complete else None,
                       "method": SPOT_CHECK_METHOD if complete else None,
                       "reviewed_by": SPOT_CHECK_REVIEWED_BY if complete else None,
                       "reviewed_on": SPOT_CHECK_REVIEWED_ON if complete else None,
                       "disagreements": [{"ref_name": r["ref_name"], "note": r["reviewer_note"]} for r in done
                                         if r["reviewer_agrees"].strip().lower() not in ("y", "yes", "1", "true")]},
        "areas": areas, "units": units, "references": refs, "unit_cells": unit_cells, "pairs": pairs,
    }


def check(d):
    """D11: headline numbers must recompute from the row-level data."""
    h, cells, pairs = d["headline"], d["unit_cells"], d["pairs"]
    home = [c for c in cells if c["lift"] >= LIFT and c["n"] >= MIN_N]
    assert h["A"]["areas_with_home"] == len({c["area"] for c in home}), "A areas_with_home"
    level = {x["id"]: x["level"] for x in d["units"]}
    covered = Counter(level[u] for u in {c["unit"] for c in home})
    assert h["A"]["departments_covered"] == covered["department"], "A departments_covered"
    assert h["A"]["divisions_covered"] == covered["division"], "A divisions_covered"
    ref_key = {"B": "items_matched", "C1": "ics_matched", "C2": "categories_matched"}
    for k in FRAMES:
        mine = [p for p in pairs if p["frame"] == k]
        assert h[k]["areas_matched"] == len({p["area"] for p in mine}), f"{k} areas_matched"
        assert h[k][ref_key[k]] == len({p["ref"] for p in mine}), f"{k} refs matched"
    assert h["C2"]["areas_exact"] == sum(a["best"]["C2"] == "exact" for a in d["areas"]), "C2 areas_exact"
    assert not any(k in json.dumps(d) for k in ('"cwid"', '"people"', '"person"')), "per-person field"


def main():
    d = build()
    check(d)
    name = f"benchmark-{SNAPSHOT_DATE}.json"
    (OUT / name).write_text(json.dumps(d, separators=(",", ":")) + "\n")
    shutil.copyfile(OUT / name, OUT / "benchmark-latest.json")
    shutil.copyfile(OUT / f"research-areas-reference-crosswalk-{SNAPSHOT_DATE}.csv",
                    OUT / "research-areas-reference-crosswalk-latest.csv")
    idx_path = OUT / "benchmark-index.json"
    idx = json.loads(idx_path.read_text())["snapshots"] if idx_path.exists() else []
    idx = [e for e in idx if e["date"] != SNAPSHOT_DATE] + [
        {"date": SNAPSHOT_DATE, "taxonomy_version": TAXONOMY_VERSION, "file": name, "note": CHANGE_NOTE}]
    idx_path.write_text(json.dumps({"snapshots": sorted(idx, key=lambda e: e["date"], reverse=True)}, indent=1) + "\n")
    print(name, f'{(OUT / name).stat().st_size // 1024} KB', len(d["pairs"]), "pairs", len(d["unit_cells"]), "cells")


if __name__ == "__main__":
    main()

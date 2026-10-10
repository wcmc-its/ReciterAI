"""Frames B, C1, C2: research areas vs CARE plan items, NIH ICs, NIH RCDC categories.

Reads outputs/mapping_raw.json (the two blind mappings + adjudication, see README.md),
writes outputs/frame_<k>_{pairs,forward,reverse}.csv and outputs/summary.json.

Final pairs = pairs both mappers gave the same relation + adjudicated non-"none" pairs
+ gap-review finds (outputs/gap_review.json), each labelled by source.
Agreement is reported three ways because the area x reference grid is sparse (most cells
are "none", which inflates plain kappa):
  kappa_any       Cohen's kappa on related / not related, over the full grid
  kappa_relation  Cohen's kappa on the 5 classes (4 relations + none), over the full grid
  positive_agree  2|A∩B| / (|A|+|B|) on related pairs: the sparse-grid-honest number
Run: python3 docs/benchmark/frames_bc.py
"""

import csv
import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
IN, OUT = HERE / "inputs", HERE / "outputs"
REFS = {"B": "care_plan_items.csv", "C1": "nih_institutes_centers.csv", "C2": "nih_rcdc_categories.csv"}
NAMES = {"B": "CARE Strategic Plan 2026-2029 (scientific items)", "C1": "NIH Institutes & Centers (grant-funding)",
         "C2": "NIH RCDC spending categories (FY2025)"}
# Strongest-first, used to pick an area's or entry's headline relation.
RANK = ["exact", "area_broader", "area_narrower", "overlap"]


def read(name):
    with open(IN / name, newline="") as f:
        return list(csv.DictReader(f))


def kappa(a, b):
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in ca) / n / n
    return round((po - pe) / (1 - pe), 3) if pe < 1 else 1.0


PAIR_COLS = ["area", "area_label", "ref", "ref_name", "relation", "source", "rationale"]


def write(name, rows, cols=None):
    with open(OUT / name, "w", newline="") as f:
        w = csv.DictWriter(f, cols or list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def frame(k, raw, areas, gap):
    refs = {r["id"]: r for r in read(REFS[k]) if r.get("in_scope", "yes") == "yes"}
    ok = lambda p: p["area_id"] in areas and p["ref_id"] in refs
    m1 = {(p["area_id"], p["ref_id"]): p for p in raw["m1"] if ok(p)}
    m2 = {(p["area_id"], p["ref_id"]): p for p in raw["m2"] if ok(p)}
    dropped = len(raw["m1"]) + len(raw["m2"]) - len(m1) - len(m2)

    grid = [(a, r) for a in areas for r in refs]
    g1 = [m1[c]["relation"] if c in m1 else "none" for c in grid]
    g2 = [m2[c]["relation"] if c in m2 else "none" for c in grid]
    both = set(m1) & set(m2)

    final = {}
    for c in both:
        if m1[c]["relation"] == m2[c]["relation"]:
            final[c] = {**m1[c], "source": "agreed", "rationale": m1[c]["rationale"]}
    adj = {(d["area_id"], d["ref_id"]): d for d in raw["decisions"]}
    for c, d in adj.items():
        if c in final or not ok(d):
            continue
        if d["relation"] != "none":
            final[c] = {**d, "source": "adjudicated"}
    # Gap review: pairs BOTH mappers left unmatched never reach adjudication, so a third
    # reviewer re-checked every still-unmatched area and entry. Its finds are labelled.
    gap_added = 0
    for d in gap:
        c = (d["area_id"], d["ref_id"])
        if ok(d) and c not in final:
            final[c] = {**d, "source": "gap_review"}
            gap_added += 1
    unresolved = [c for c in set(m1) | set(m2)
                  if c not in final and c not in adj
                  and (m1.get(c, {}).get("relation") != m2.get(c, {}).get("relation"))]

    pairs = [{"area": a, "area_label": areas[a], "ref": r, "ref_name": refs[r].get("name") or refs[r]["item"],
              "relation": p["relation"], "source": p["source"], "rationale": p["rationale"]}
             for (a, r), p in sorted(final.items())]
    write(f"frame_{k}_pairs.csv", pairs, PAIR_COLS)

    def best(rows):
        return min(rows, key=lambda p: RANK.index(p["relation"]))["relation"] if rows else "none"

    fwd = []
    for a, label in sorted(areas.items(), key=lambda x: x[1]):
        mine = [p for p in pairs if p["area"] == a]
        fwd.append({"area": a, "label": label, "best": best(mine),
                    "matches": "; ".join(f'{p["ref_name"]} [{p["relation"]}]' for p in mine)})
    write(f"frame_{k}_forward.csv", fwd)

    rev = []
    for r, ref in refs.items():
        mine = [p for p in pairs if p["ref"] == r]
        rev.append({"ref": r, "name": ref.get("name") or ref["item"], "best": best(mine),
                    **({"fy2025_amount_musd": ref["fy2025_amount_musd"]} if k == "C2" else {}),
                    "areas": "; ".join(f'{p["area_label"]} [{p["relation"]}]' for p in mine)})
    write(f"frame_{k}_reverse.csv", rev)

    s = {
        "frame": NAMES[k], "areas": len(areas), "reference_entries": len(refs),
        "mapper1_pairs": len(m1), "mapper2_pairs": len(m2), "invalid_ids_dropped": dropped,
        "kappa_any": kappa([x != "none" for x in g1], [x != "none" for x in g2]),
        "kappa_relation": kappa(g1, g2),
        "positive_agree": round(2 * len(both) / (len(m1) + len(m2)), 3) if m1 or m2 else None,
        "disagreements": len(raw["disagree"]), "adjudicated": len(adj), "unresolved": len(unresolved),
        "gap_review_added": gap_added,
        "final_pairs": len(pairs), "final_by_source": dict(Counter(p["source"] for p in pairs)),
        "areas_matched": sum(f["best"] != "none" for f in fwd),
        "areas_by_best": dict(Counter(f["best"] for f in fwd)),
        "refs_matched": sum(r["best"] != "none" for r in rev),
        "refs_by_best": dict(Counter(r["best"] for r in rev)),
        "mapper_slices": raw["considered"],
    }
    if k == "C2":
        tot = sum(float(r["fy2025_amount_musd"]) for r in rev)
        cov = sum(float(r["fy2025_amount_musd"]) for r in rev if r["best"] != "none")
        s["category_dollars_matched_pct"] = round(100 * cov / tot, 1)
    return s


def main():
    areas = {r["id"]: r["label"] for r in read("research_areas.csv")}
    raw = {f["frame"]: f for f in json.load(open(OUT / "mapping_raw.json"))}
    gp = OUT / "gap_review.json"
    gap = {g["frame"]: g["decisions"] for g in json.load(open(gp))} if gp.exists() else {}
    summary = {k: frame(k, raw[k], areas, gap.get(k, [])) for k in REFS}
    json.dump(summary, open(OUT / "summary.json", "w"), indent=2)
    for k, s in summary.items():
        print(k, {x: v for x, v in s.items() if x != "mapper_slices"})


if __name__ == "__main__":
    main()
